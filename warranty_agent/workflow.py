from __future__ import annotations

from typing import Any, Callable

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy, interrupt

from .action_tools import SimulatedActionTools
from .agents import ClaimIntakeAgent, ResolutionPolicyAgent, WarrantyAssessmentAgent
from .data_sources import CsvDataSource
from .domain import ClaimState, WarrantyPolicy
from .providers import ChatModelProvider
from .repositories import ClaimRepository


def _retry_non_rate_limit_errors(error: Exception) -> bool:
    """Avoid repeating calls that the provider has already rejected for quota."""
    return getattr(error, "status_code", None) != 429 and type(error).__name__ != "RateLimitError"


class WarrantyWorkflowFactory:
    """Builds an explicit, inspectable multi-agent LangGraph per processing run."""

    def __init__(
        self,
        provider: ChatModelProvider,
        model_name: str,
        data: CsvDataSource,
        policy: WarrantyPolicy,
        repository: ClaimRepository,
    ):
        self.intake_agent = ClaimIntakeAgent(provider, model_name, repository)
        self.assessment_agent = WarrantyAssessmentAgent(provider, model_name, data, repository)
        self.resolution_agent = ResolutionPolicyAgent(policy)
        self.action_tools = SimulatedActionTools(repository)
        self.policy = policy

    def build(self, checkpointer=None):
        graph = StateGraph(ClaimState)
        retry_policy = RetryPolicy(
            max_attempts=2,
            initial_interval=2,
            max_interval=8,
            backoff_factor=2,
            jitter=True,
            retry_on=_retry_non_rate_limit_errors,
        )
        graph.add_node("intake_agent", self._intake_node, retry_policy=retry_policy)
        graph.add_node(
            "warranty_assessment_agent",
            self._assessment_node,
            retry_policy=retry_policy,
        )
        graph.add_node("resolution_policy_agent", self._resolution_node)
        graph.add_node("human_approval_gate", self._approval_node)
        graph.add_node("execute_action_tool", self._action_node)
        graph.add_node("finalize_denial", self._denial_node)
        graph.add_edge(START, "intake_agent")
        graph.add_edge("intake_agent", "warranty_assessment_agent")
        graph.add_edge("warranty_assessment_agent", "resolution_policy_agent")
        graph.add_conditional_edges(
            "resolution_policy_agent",
            self._needs_human,
            {
                "human_approval_gate": "human_approval_gate",
                "execute_action_tool": "execute_action_tool",
            },
        )
        graph.add_conditional_edges(
            "human_approval_gate",
            self._review_route,
            {
                "execute_action_tool": "execute_action_tool",
                "finalize_denial": "finalize_denial",
            },
        )
        graph.add_edge("execute_action_tool", END)
        graph.add_edge("finalize_denial", END)
        return graph.compile(checkpointer=checkpointer)

    def _intake_node(self, state: ClaimState) -> dict:
        return self._tracked_step(
            state,
            "claim_intake_agent",
            lambda: {
                "interpretation": self.intake_agent.run(state["claim"]),
                "status": "assessing",
            },
        )

    def _assessment_node(self, state: ClaimState) -> dict:
        return self._tracked_step(
            state,
            "warranty_assessment_agent",
            lambda: self.assessment_agent.run(state["claim"], state["interpretation"], self.policy),
        )

    def _resolution_node(self, state: ClaimState) -> dict:
        result = self._tracked_step(state, "resolution_policy_agent", lambda: self.resolution_agent.run(state))
        if result.get("needs_human"):
            claim_id = state["claim"]["claim_id"]
            self.action_tools.repository.record_event(
                claim_id,
                "human_review_requested",
                "human_approval_gate",
                {
                    "reason": result.get("decision_reason", ""),
                    "proposal": result.get("proposed_action"),
                },
            )
        return result

    def _tracked_step(self, state: ClaimState, step: str, operation: Callable[[], dict]) -> dict:
        claim_id = state["claim"]["claim_id"]
        actor = f"agent:{step}"
        self.action_tools.repository.record_event(claim_id, "agent_step_started", actor, {"step": step})
        try:
            output = operation()
        except Exception as exc:
            self.action_tools.repository.record_event(
                claim_id,
                "agent_step_failed",
                actor,
                {"step": step, "error_type": type(exc).__name__},
            )
            raise
        self.action_tools.repository.record_event(
            claim_id,
            "agent_step_completed",
            actor,
            {"step": step, "outputs": sorted(output.keys())},
        )
        return output

    @staticmethod
    def _needs_human(state: ClaimState) -> str:
        return "human_approval_gate" if state.get("needs_human") else "execute_action_tool"

    @staticmethod
    def _approval_node(state: ClaimState) -> dict:
        value = {
            "claim_id": state["claim"]["claim_id"],
            "reason": state.get("decision_reason", "Human approval required."),
            "proposal": state.get("proposed_action"),
            "support": state.get("support"),
            "policy_version": state.get("policy_version", ""),
        }
        decision = interrupt(value, response_schema=dict)
        return {
            "human_decision": decision,
            "reviewer": decision.get("reviewer"),
            "reviewer_note": decision.get("note"),
        }

    @staticmethod
    def _review_route(state: ClaimState) -> str:
        answer = state.get("human_decision", {})
        if answer.get("approved") and state.get("proposed_action"):
            return "execute_action_tool"
        return "finalize_denial"

    def _action_node(self, state: ClaimState) -> dict:
        return self._tracked_step(state, "execute_action_tool", lambda: self._execute_action(state))

    def _execute_action(self, state: ClaimState) -> dict:
        proposed = state.get("proposed_action") or {}
        action_type = proposed.get("action_type")
        human_decision = state.get("human_decision") or {}
        params: dict[str, Any] = {
            "claim_id": state["claim"]["claim_id"],
            "product_ref": proposed.get("product_ref", state["claim"]["product_ref"]),
            "product_name": proposed.get("product_name", ""),
        }
        if human_decision.get("approved"):
            params.update(
                {
                    "requested_by": "human",
                    "reviewer": human_decision.get("reviewer", "human"),
                    "reviewer_note": human_decision.get("note", ""),
                    "idempotency_key": f"{state['claim']['claim_id']}:{action_type}:{proposed.get('product_ref', state['claim']['product_ref'])}",
                }
            )
        if action_type == "replacement":
            action = self.action_tools.schedule_replacement.invoke(params)
        elif action_type in {"refund", "credit"}:
            tool = self.action_tools.issue_refund if action_type == "refund" else self.action_tools.issue_credit
            action = tool.invoke(
                {
                    **params,
                    "amount": proposed.get("amount", 0.0),
                    "currency": proposed.get("currency", "USD"),
                }
            )
        else:
            raise ValueError("No safe resolution action was proposed for execution.")
        return {
            "action": action,
            "status": "resolved",
            "decision": "human_approved_action"
            if human_decision.get("approved")
            else state.get("decision", "auto_resolved"),
            "decision_reason": human_decision.get("note") or state.get("decision_reason", ""),
            "human_decision": human_decision,
        }

    @staticmethod
    def _denial_node(state: ClaimState) -> dict:
        note = state.get("human_decision", {}).get("note", "Human reviewer denied the claim.")
        return {
            "status": "denied",
            "decision": "human_denied",
            "decision_reason": note,
            "human_decision": state.get("human_decision", {}),
            "reviewer": state.get("reviewer"),
            "reviewer_note": state.get("reviewer_note"),
        }
