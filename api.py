from __future__ import annotations

import json
import os
import copy
import threading
import uuid
from urllib.request import urlopen
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from warranty_agent.data_sources import CsvDataSource
from warranty_agent.policy import PolicyRepository
from warranty_agent.repositories import ClaimRepository
from warranty_agent.service import WarrantyCaseService

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
DB_PATH = Path(os.getenv("DATABASE_PATH", str(ROOT / "warranty_agent.db"))).resolve()
DATA_DIR = Path(os.getenv("DATA_DIR", str(ROOT / "data"))).resolve()
POLICY_PATH = Path(os.getenv("POLICY_PATH", str(ROOT / "policy.json"))).resolve()


def _json_field(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None


def serialize_claim(row: dict) -> dict:
    item = dict(row)
    for field in (
        "interpretation_json",
        "warranty_json",
        "evidence_json",
        "support_json",
        "action_json",
        "proposal_json",
    ):
        value = _json_field(item.pop(field, None))
        item[field.removesuffix("_json")] = value
    return item


class ProcessRequest(BaseModel):
    provider: str = Field(
        default_factory=lambda: os.getenv("LLM_PROVIDER", "inhouse"),
        pattern="^(groq|openai|ollama|inhouse)$",
    )
    model: str | None = None


class ReviewRequest(ProcessRequest):
    approved: bool
    reviewer: str = Field(min_length=1, max_length=120)
    note: str = Field(min_length=1, max_length=2000)

    @field_validator("reviewer", "note")
    @classmethod
    def require_non_whitespace(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("This field cannot be blank.")
        return cleaned


class BatchRequest(ProcessRequest):
    claim_ids: list[str] | None = None


class ReviewerNoteRequest(BaseModel):
    reviewer: str = Field(min_length=1, max_length=120)
    note: str = Field(min_length=1, max_length=2000)

    @field_validator("reviewer", "note")
    @classmethod
    def require_non_whitespace(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("This field cannot be blank.")
        return cleaned


class OverrideRequest(ReviewerNoteRequest):
    decision: str = Field(pattern="^(deny|review)$")


class ReverseActionRequest(ReviewerNoteRequest):
    pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    data = CsvDataSource(DATA_DIR)
    repository = ClaimRepository(DB_PATH)
    policy_repository = PolicyRepository(POLICY_PATH)
    app.state.repository = repository
    app.state.policy_repository = policy_repository
    service = WarrantyCaseService(data, repository, policy_repository, DB_PATH)
    service.import_seed_claims()
    repository.recover_stale_processing()
    app.state.service = service
    yield


app = FastAPI(title="NovaHearth Warranty API", version="1.0.0", lifespan=lifespan)
BATCH_JOBS: dict[str, dict] = {}
BATCH_JOBS_LOCK = threading.Lock()


def get_repo() -> ClaimRepository:
    return app.state.repository


def get_service() -> WarrantyCaseService:
    return app.state.service


def _claim_or_404(claim_id: str) -> dict:
    claim = get_repo().get_claim(claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    return claim


def _workflow_response(claim_id: str, result: dict) -> dict:
    return {"claim": serialize_claim(_claim_or_404(claim_id)), "result": result}


def _decode_details(rows: list[dict]) -> list[dict]:
    for row in rows:
        row["details"] = _json_field(row.pop("details_json", None))
    return rows


def _model_name(provider: str, requested: str | None) -> str:
    if requested and requested.strip():
        return requested.strip()
    from warranty_agent.providers import default_model

    return default_model(provider)


def _safe_processing_error(exc: Exception) -> tuple[int, str]:
    if getattr(exc, "status_code", None) == 429 or type(exc).__name__ == "RateLimitError":
        return (
            503,
            "The selected LLM provider is rate-limited. Wait for its quota reset or choose another provider.",
        )
    return (
        502,
        f"Claim processing failed ({type(exc).__name__}). See the claim audit for the recorded workflow failure.",
    )


def _ollama_model_available(base_url: str, model_name: str) -> bool:
    """Return false quickly when Ollama or its configured model is unavailable."""
    try:
        with urlopen(f"{base_url.rstrip('/')}/api/tags", timeout=0.4) as response:
            tags = json.loads(response.read().decode("utf-8"))
        models = {item.get("name") or item.get("model") for item in tags.get("models", [])}
        return model_name in models
    except Exception:
        return False


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "provider": os.getenv("LLM_PROVIDER", "inhouse")}


@app.get("/api/config")
def config() -> dict:
    policy = app.state.policy_repository.load()
    provider = os.getenv("LLM_PROVIDER", "inhouse").lower()
    from warranty_agent.providers import default_model

    ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").strip()
    ollama_model = default_model("ollama")
    inhouse_url = os.getenv("INHOUSE_LLM_BASE_URL", "").strip()
    inhouse_key = os.getenv("INHOUSE_LLM_API_KEY", "").strip()
    providers = {
        "groq": {
            "model": default_model("groq"),
            "api_key_configured": bool(os.getenv("GROQ_API_KEY", "").strip()),
            "requires_api_key": True,
            "available": bool(os.getenv("GROQ_API_KEY", "").strip()),
        },
        "openai": {
            "model": default_model("openai"),
            "api_key_configured": bool(os.getenv("OPENAI_API_KEY", "").strip()),
            "requires_api_key": True,
            "available": bool(os.getenv("OPENAI_API_KEY", "").strip()),
        },
        "inhouse": {
            "model": default_model("inhouse"),
            "api_key_configured": bool(inhouse_url),
            "requires_api_key": False,
            "available": bool(inhouse_url),
            "api_key_optional": bool(inhouse_key),
            "base_url": inhouse_url,
        },
        "ollama": {
            "model": ollama_model,
            "api_key_configured": True,
            "requires_api_key": False,
            "available": _ollama_model_available(ollama_url, ollama_model),
            "base_url": ollama_url,
        },
    }
    return {
        "provider": provider,
        "model": _model_name(provider, None),
        "api_key_configured": providers[provider]["api_key_configured"],
        "providers": providers,
        "policy": policy.model_dump(),
        "demo_data_reset_enabled": os.getenv("ENABLE_DEMO_DATA_RESET", "false").strip().lower() == "true",
    }


@app.get("/api/claims")
def list_claims(status: str | None = Query(default=None)) -> list[dict]:
    claims = get_repo().list_claims()
    if status and status not in {
        "new",
        "error",
        "processing",
        "assessing",
        "awaiting_review",
        "needs_review",
        "resolved",
        "denied",
    }:
        raise HTTPException(status_code=422, detail="Unknown claim status filter.")
    if status:
        claims = [claim for claim in claims if claim["status"] == status]
    return [serialize_claim(claim) for claim in claims]


@app.get("/api/claims/{claim_id}/reviews")
def claim_reviews(claim_id: str) -> list[dict]:
    _claim_or_404(claim_id)
    return get_repo().list_reviews(claim_id)


@app.get("/api/warranties")
def list_warranties() -> list[dict]:
    """Return the read-only warranty catalog loaded from the supplied CSV."""
    return [dict(record) for record in get_service().data.warranty]


@app.get("/api/data-summary")
def data_summary() -> dict:
    """Describe the loaded source datasets for the audit walkthrough."""
    data = get_service().data
    return {
        "warranty_claims": len(data.claims),
        "product_warranty_records": len(data.warranty),
        "replacement_history": len(data.replacements),
        "source_files": [
            "warranty_claims.csv",
            "product_warranty_records.csv",
            "replacement_history.csv",
        ],
    }


@app.get("/api/claims/{claim_id}")
def claim_detail(claim_id: str) -> dict:
    return serialize_claim(_claim_or_404(claim_id))


@app.get("/api/claims/{claim_id}/events")
def claim_events(claim_id: str) -> list[dict]:
    _claim_or_404(claim_id)
    return _decode_details(get_repo().list_events(claim_id))


@app.get("/api/actions")
def list_actions() -> list[dict]:
    return _decode_details(get_repo().list_actions())


@app.get("/api/claims/{claim_id}/actions")
def claim_actions(claim_id: str) -> list[dict]:
    _claim_or_404(claim_id)
    return _decode_details(get_repo().list_actions(claim_id))


@app.get("/api/usage")
def token_usage(limit: int = Query(default=500, ge=1, le=2000)) -> dict:
    summary = get_repo().summarize_llm_usage()
    summary["recent"] = get_repo().list_llm_usage(limit=limit)
    return summary


@app.get("/api/events")
def recent_events(limit: int = Query(default=150, ge=1, le=500)) -> list[dict]:
    return _decode_details(get_repo().list_events(limit=limit))


@app.post("/api/claims/import")
def import_claims() -> dict:
    inserted = get_service().import_seed_claims()
    return {"inserted": inserted, "total": len(get_repo().list_claims())}


@app.post("/api/claims/{claim_id}/process")
def process_claim(claim_id: str, request: ProcessRequest) -> dict:
    _claim_or_404(claim_id)
    try:
        state = get_service().process_claim(claim_id, request.provider, _model_name(request.provider, request.model))
        return _workflow_response(claim_id, state)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        status_code, message = _safe_processing_error(exc)
        raise HTTPException(status_code=status_code, detail=message) from exc


def _run_batch_job(job_id: str, claim_ids: list[str], provider: str, model: str) -> None:
    with BATCH_JOBS_LOCK:
        job = BATCH_JOBS.get(job_id)
        if not job:
            return
        job["status"] = "running"
    for claim_id in claim_ids:
        with BATCH_JOBS_LOCK:
            job = BATCH_JOBS.get(job_id)
            if not job:
                return
            job["current_claim_id"] = claim_id
        try:
            state = get_service().process_claim(claim_id, provider, model)
            result = {"claim_id": claim_id, "status": state.get("status", "unknown")}
        except Exception as exc:
            _, message = _safe_processing_error(exc)
            result = {"claim_id": claim_id, "error": message}
        with BATCH_JOBS_LOCK:
            job = BATCH_JOBS.get(job_id)
            if not job:
                return
            job["completed"] += 1
            job["results"].append(result)
            if "error" in result:
                job["errors"].append(result)
    with BATCH_JOBS_LOCK:
        job = BATCH_JOBS.get(job_id)
        if job:
            job["current_claim_id"] = None
            job["status"] = "completed"


@app.post("/api/claims/process-batch", status_code=202)
def process_batch(request: BatchRequest, background_tasks: BackgroundTasks) -> dict:
    if request.claim_ids is not None:
        if len(request.claim_ids) != len(set(request.claim_ids)):
            raise HTTPException(status_code=422, detail="claim_ids must not contain duplicates.")
        if len(request.claim_ids) > 100:
            raise HTTPException(status_code=422, detail="Process at most 100 claims in one batch job.")
    claims = get_repo().list_claims()
    queued = [
        claim
        for claim in claims
        if claim["status"] in {"new", "error"} and (request.claim_ids is None or claim["claim_id"] in request.claim_ids)
    ]
    job_id = uuid.uuid4().hex
    claim_ids = [claim["claim_id"] for claim in queued]
    job = {
        "job_id": job_id,
        "status": "queued" if claim_ids else "completed",
        "total": len(claim_ids),
        "completed": 0,
        "current_claim_id": None,
        "processed": 0,
        "results": [],
        "errors": [],
    }
    with BATCH_JOBS_LOCK:
        if any(existing["status"] in {"queued", "running"} for existing in BATCH_JOBS.values()):
            raise HTTPException(status_code=409, detail="A batch job is already queued or running.")
        BATCH_JOBS[job_id] = job
    if claim_ids:
        background_tasks.add_task(
            _run_batch_job,
            job_id,
            claim_ids,
            request.provider,
            _model_name(request.provider, request.model),
        )
    return copy.deepcopy(job)


@app.get("/api/batch-jobs/{job_id}")
def get_batch_job(job_id: str) -> dict:
    with BATCH_JOBS_LOCK:
        job = BATCH_JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Batch job not found.")
        snapshot = copy.deepcopy(job)
    snapshot["processed"] = sum("error" not in result for result in snapshot["results"])
    return snapshot


@app.post("/api/claims/{claim_id}/review")
def review_claim(claim_id: str, request: ReviewRequest) -> dict:
    _claim_or_404(claim_id)
    try:
        state = get_service().review_claim(
            claim_id,
            request.provider,
            _model_name(request.provider, request.model),
            request.approved,
            request.reviewer,
            request.note,
        )
        return _workflow_response(claim_id, state)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        status_code, message = _safe_processing_error(exc)
        raise HTTPException(status_code=status_code, detail=f"Review could not be completed. {message}") from exc


@app.post("/api/claims/{claim_id}/override")
def override_claim(claim_id: str, request: OverrideRequest) -> dict:
    try:
        claim = get_service().override_decision(claim_id, request.reviewer, request.decision, request.note)
        return {"claim": serialize_claim(claim)}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/claims/{claim_id}/reverse")
def reverse_automated_action(claim_id: str, request: ReverseActionRequest) -> dict:
    _claim_or_404(claim_id)
    try:
        claim = get_service().reverse_automated_action(claim_id, request.reviewer, request.note)
        return {"claim": serialize_claim(claim)}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/admin/clear-data")
def clear_saved_data() -> dict:
    """Clear decisions/actions/reviews/checkpoints and reload seed claims."""
    if os.getenv("ENABLE_DEMO_DATA_RESET", "false").strip().lower() != "true":
        raise HTTPException(
            status_code=403,
            detail="Demo data reset is disabled. Set ENABLE_DEMO_DATA_RESET=true for a trusted local demo.",
        )
    inserted = get_repo().clear_saved_data(get_service().data.claims_as_records())
    return {
        "message": "Saved application data cleared and seed claims reloaded.",
        "claims": inserted,
    }


@app.get("/", include_in_schema=False)
def frontend() -> FileResponse:
    built_index = ROOT / "frontend" / "dist" / "index.html"
    return FileResponse(built_index if built_index.is_file() else ROOT / "frontend" / "index.html")


DIST_DIR = ROOT / "frontend" / "dist"
STATIC_DIR = DIST_DIR if (DIST_DIR / "index.html").is_file() else ROOT / "frontend"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
