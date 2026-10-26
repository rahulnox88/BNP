from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from .data_sources import CsvDataSource
from .policy import PolicyRepository
from .providers import get_provider
from .repositories import ClaimRepository
from .workflow import WarrantyWorkflowFactory

logger = logging.getLogger(__name__)


class WarrantyCaseService:
    """Application service coordinating CSV sources, policy, graph, and persistence."""

    def __init__(
        self,
        data: CsvDataSource,
        repository: ClaimRepository,
        policy_repository: PolicyRepository,
        db_path: Path,
    ):
        self.data = data
        self.repository = repository
        self.policy_repository = policy_repository
        self.db_path = db_path

    def import_seed_claims(self) -> int:
        return self.repository.import_claims(self.data.claims_as_records())

    def process_claim(self, claim_id: str, provider_name: str, model_name: str) -> dict:
        claim = self.repository.get_claim(claim_id)
        if not claim:
            raise ValueError(f"Claim '{claim_id}' was not found.")
        if claim["status"] not in {"new", "error"}:
            raise ValueError(
                "Only new or failed claims can be processed. Use the review controls for pending proposals."
            )
        thread_id = self.repository.begin_run(claim_id)
        initial_state = {
            "claim": {
                k: claim[k]
                for k in (
                    "claim_id",
                    "customer_name",
                    "product_ref",
                    "claim_date",
                    "claim_text",
                )
            },
        }
        return self._invoke(claim_id, thread_id, provider_name, model_name, initial_state)

    def review_claim(
        self,
        claim_id: str,
        provider_name: str,
        model_name: str,
        approved: bool,
        reviewer: str,
        note: str,
    ) -> dict:
        claim = self.repository.get_claim(claim_id)
        if not claim or not claim.get("graph_thread_id"):
            raise ValueError("This claim has no resumable review workflow.")
        if claim["status"] != "awaiting_review":
            raise ValueError("This claim is not waiting for a reviewer decision.")
        if approved and self.repository.has_action(claim_id):
            raise ValueError("An action is already recorded for this claim.")
        thread_id = claim["graph_thread_id"]
        reviewer = reviewer.strip() or "reviewer"
        note = note.strip() or ("Approved by human reviewer." if approved else "Denied by human reviewer.")
        payload = {"approved": approved, "reviewer": reviewer, "note": note}
        return self._invoke(claim_id, thread_id, provider_name, model_name, Command(resume=payload))

    def override_decision(self, claim_id: str, reviewer: str, decision: str, note: str) -> dict:
        claim = self.repository.get_claim(claim_id)
        if not claim:
            raise ValueError(f"Claim '{claim_id}' was not found.")
        if claim["status"] in {"new", "processing", "error", "awaiting_review"}:
            raise ValueError("Assess the claim before recording a human override.")
        if self.repository.list_actions(claim_id):
            raise ValueError(
                "A claim with a recorded action cannot be denied or reassigned through a decision override."
            )
        if decision == "review" and claim["status"] == "needs_review":
            raise ValueError("The claim is already in human review.")
        if decision == "deny" and claim["status"] == "denied":
            raise ValueError("The claim is already denied.")
        self.repository.record_override(claim_id, reviewer, decision, note)
        return self.repository.get_claim(claim_id)

    def reverse_automated_action(self, claim_id: str, reviewer: str, note: str) -> dict:
        return self.repository.reverse_automated_action(
            claim_id,
            reviewer.strip() or "reviewer",
            note.strip(),
        )

    def _invoke(
        self,
        claim_id: str,
        thread_id: str,
        provider_name: str,
        model_name: str,
        input_state: Any,
    ) -> dict:
        policy = self.policy_repository.load()
        config = {"configurable": {"thread_id": thread_id}}
        try:
            provider = get_provider(provider_name)
            factory = WarrantyWorkflowFactory(provider, model_name, self.data, policy, self.repository)
            with SqliteSaver.from_conn_string(str(self.db_path)) as checkpointer:
                graph = factory.build(checkpointer=checkpointer)
                graph.invoke(input_state, config)
                final_state = dict(graph.get_state(config).values)
        except Exception as exc:
            logger.exception("Claim workflow failed for claim_id=%s", claim_id)
            rate_limited = getattr(exc, "status_code", None) == 429 or type(exc).__name__ == "RateLimitError"
            reason = (
                "The selected LLM provider rate-limited this request. Wait for its quota reset or select another provider."
                if rate_limited
                else f"Claim processing failed ({type(exc).__name__}). Check the server log for details."
            )
            if rate_limited:
                self.repository.record_event(
                    claim_id,
                    "provider_rate_limited",
                    "system",
                    {"provider": provider_name, "model": model_name},
                )
            self.repository.update_claim(
                claim_id,
                {"status": "error", "reason": reason},
                event_type="workflow_error",
                actor="system",
            )
            raise
        self._persist_state(claim_id, thread_id, final_state, policy.policy_version)
        return final_state

    def _persist_state(self, claim_id: str, thread_id: str, state: dict, policy_version: str) -> None:
        interpretation = state.get("interpretation", {})
        warranty = state.get("warranty")
        evidence = state.get("evidence")
        support = state.get("support")
        action = state.get("action")
        reviewer = state.get("reviewer")
        reviewer_note = state.get("reviewer_note")
        values = {
            "intent": interpretation.get("intent"),
            "issue_summary": interpretation.get("issue_summary"),
            "requested_product": interpretation.get("target_product"),
            "interpretation_json": self._dump(interpretation),
            "evidence_json": self._dump(evidence),
            "warranty_json": self._dump(warranty),
            "support_json": self._dump(support),
            "decision": state.get("decision"),
            "reason": state.get("decision_reason"),
            "action_json": self._dump(action),
            "proposal_json": self._dump(state.get("proposed_action")),
            "status": state.get("status", "error"),
            "graph_thread_id": thread_id,
            "policy_version": policy_version,
        }
        human = state.get("human_decision")
        if human:
            values["reviewer"] = reviewer or human.get("reviewer")
            values["reviewer_note"] = reviewer_note or human.get("note")
            values["decision"] = state.get("decision")
            values["status"] = state.get("status", "needs_review")
            # Successful action tools already persist an approving review
            # together with the action. Only denials need a separate write.
            if not human.get("approved"):
                self.repository.record_review_decision(
                    claim_id,
                    reviewer or human.get("reviewer", "human"),
                    "deny",
                    reviewer_note or human.get("note", ""),
                )
        self.repository.update_claim(
            claim_id,
            values,
            event_type=None if human else "agent_decision",
            actor="agent",
        )

    @staticmethod
    def _dump(value: Any) -> str | None:
        return json.dumps(value, ensure_ascii=False, default=str) if value is not None else None
