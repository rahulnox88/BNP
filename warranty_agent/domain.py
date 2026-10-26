from __future__ import annotations

from typing import Literal, TypedDict

from pydantic import BaseModel, Field


Intent = Literal["refund", "credit", "replacement", "unclear"]
IssueCategory = Literal[
    "manufacturing_defect",
    "shipping_damage_covered",
    "accidental_damage",
    "normal_wear",
    "unknown",
]


class ClaimUnderstanding(BaseModel):
    intent: Intent
    product_mentioned: str = Field(
        default="",
        description="Exact product name or description the customer mentions; empty if none",
    )
    issue_summary: str = Field(description="Short factual summary of the reported issue")
    issue_category: IssueCategory
    target_product: str = Field(description="Replacement product requested, or empty when none was named")
    mentions_repeat_replacement: bool
    needs_human_review: bool


class ClaimState(TypedDict, total=False):
    claim: dict
    interpretation: dict
    warranty: dict | None
    replacement_count: int
    warranty_customer_match: bool
    evidence: dict
    support: dict
    proposed_action: dict | None
    decision: str
    decision_reason: str
    status: str
    needs_human: bool
    human_decision: dict
    action: dict | None
    reviewer: str
    reviewer_note: str


class WarrantyPolicy(BaseModel):
    auto_approval_limit_usd: float = Field(default=100.0, ge=0.0, allow_inf_nan=False)
    refunds_require_human_approval: bool = False
    credits_require_human_approval: bool = False
    replacements_require_human_approval: bool = False
    supported_warranty_categories: list[str] = [
        "manufacturing_defect",
        "known_component_recall",
        "shipping_damage_covered",
    ]
    policy_version: str = "1.5.0"
