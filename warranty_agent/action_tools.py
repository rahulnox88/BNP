from __future__ import annotations

from langchain_core.tools import tool

from .repositories import ClaimRepository


class SimulatedActionTools:
    """Demo effect adapters. Replace with authenticated, idempotent APIs for production."""

    def __init__(self, repository: ClaimRepository):
        self.repository = repository
        self.schedule_replacement = self._replacement_tool()
        self.issue_refund = self._money_tool("refund")
        self.issue_credit = self._money_tool("credit")

    def _replacement_tool(self):
        repository = self.repository

        @tool("schedule_like_for_like_replacement")
        def schedule_like_for_like_replacement(
            claim_id: str,
            product_ref: str,
            product_name: str,
            requested_by: str = "agent",
            reviewer: str = "",
            reviewer_note: str = "",
            idempotency_key: str = "",
        ) -> dict:
            """Schedule a simulated replacement of exactly the product_ref in the warranty record."""
            return repository.add_action(
                claim_id,
                {
                    "action_type": "replacement",
                    "product_ref": product_ref,
                    "product_name": product_name,
                    "amount": 0.0,
                    "currency": "USD",
                },
                actor=requested_by,
                reviewer=reviewer or None,
                reviewer_note=reviewer_note or None,
                idempotency_key=idempotency_key or None,
            )

        return schedule_like_for_like_replacement

    def _money_tool(self, action_type: str):
        repository = self.repository

        @tool(f"issue_simulated_{action_type}")
        def issue_money(
            claim_id: str,
            product_ref: str,
            product_name: str,
            amount: float,
            currency: str = "USD",
            requested_by: str = "agent",
            reviewer: str = "",
            reviewer_note: str = "",
            idempotency_key: str = "",
        ) -> dict:
            """Record a simulated refund/credit action. This demo adapter does not move money."""
            return repository.add_action(
                claim_id,
                {
                    "action_type": action_type,
                    "product_ref": product_ref,
                    "product_name": product_name,
                    "amount": round(amount, 2),
                    "currency": currency,
                },
                actor=requested_by,
                reviewer=reviewer or None,
                reviewer_note=reviewer_note or None,
                idempotency_key=idempotency_key or None,
            )

        return issue_money
