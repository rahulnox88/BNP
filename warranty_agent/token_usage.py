from __future__ import annotations

from typing import Any

from .repositories import ClaimRepository


def extract_token_usage(
    response: dict[str, Any],
) -> tuple[int | None, int | None, int | None]:
    """Read provider-reported usage from a LangChain structured-output response."""
    raw = response.get("raw")
    metadata = getattr(raw, "usage_metadata", None) or {}
    if metadata:
        prompt = metadata.get("input_tokens")
        completion = metadata.get("output_tokens")
        total = metadata.get("total_tokens")
    else:
        response_metadata = getattr(raw, "response_metadata", None) or {}
        metadata = response_metadata.get("token_usage") or response_metadata.get("usage") or {}
        prompt = metadata.get("prompt_tokens", metadata.get("input_tokens"))
        completion = metadata.get("completion_tokens", metadata.get("output_tokens"))
        total = metadata.get("total_tokens")
    if total is None and prompt is not None and completion is not None:
        total = prompt + completion
    return (
        _nonnegative_int(prompt),
        _nonnegative_int(completion),
        _nonnegative_int(total),
    )


def invoke_structured(
    model,
    messages: list,
    repository: ClaimRepository,
    claim_id: str,
    provider: str,
    model_name: str,
    agent: str,
) -> dict:
    """Invoke a LangChain structured model and persist provider-reported token usage."""
    try:
        response = model.invoke(messages)
    except Exception:
        repository.record_llm_usage(claim_id, provider, model_name, agent, None, None, None)
        raise
    prompt, completion, total = extract_token_usage(response)
    repository.record_llm_usage(claim_id, provider, model_name, agent, prompt, completion, total)
    if response.get("parsing_error"):
        raise ValueError(f"{agent} returned output that did not match the expected schema.")
    parsed = response.get("parsed")
    if parsed is None:
        raise ValueError(f"{agent} returned no structured result.")
    return parsed.model_dump()


def _nonnegative_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None
