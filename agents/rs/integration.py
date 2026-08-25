"""Diagnosis-to-safety contract for recommendation packets.

The builder produces data only. It does not render executable arguments or call
tools; the future remediation policy remains responsible for rendering, policy
checks, approval, and execution.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from agents.rs.features import build_content_query
from agents.rs.recommender import HybridRecommender, default_risk_penalty, rank_candidates
from agents.rs.schemas import (
    InteractionRow,
    Runbook,
    SchemaError,
    validate_catalogue,
    validate_interactions,
)
from agents.tool_policy import ROLE_ALLOWED_TOOLS

_TEMPLATE_RE = re.compile(r"\{\{([a-z][a-z0-9_]*)(?::(int|str))?(?::([A-Za-z0-9_.-]+))?\}\}")
_DEFAULT_RISK_PENALTIES = {
    "content": 0.0,
    "collaborative": 0.0,
    "success": 0.0,
    "risk": default_risk_penalty,
}


@dataclass(frozen=True)
class RecommendationToggle:
    enabled: bool
    reason: str = "enabled"

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise SchemaError("RS toggle enabled must be boolean")
        if not self.reason.strip():
            raise SchemaError("RS toggle reason cannot be blank")


class RecommendationPacketBuilder:
    def __init__(
        self,
        catalogue: list[Runbook],
        recommender: HybridRecommender,
        *,
        k: int = 5,
        available_tools: frozenset[str] | None = None,
    ) -> None:
        tools = available_tools or ROLE_ALLOWED_TOOLS["remediation"]
        validate_catalogue(catalogue, frozenset(tools))
        if k <= 0:
            raise SchemaError("k must be positive")
        self.catalogue = list(catalogue)
        self.catalogue_by_id = {item.action_id: item for item in catalogue}
        self.recommender = recommender
        self.k = k
        self.available_tools = frozenset(tools)

    def recommend_packet(
        self,
        context: Any,
        *,
        toggle: RecommendationToggle | None = None,
        template_values: Mapping[str, Any] | None = None,
        risk_penalty_overrides: Mapping[str, float] | None = None,
    ) -> dict[str, Any]:
        toggle = toggle or RecommendationToggle(enabled=True)
        base_packet: dict[str, Any] = {
            "contract_version": "rs-v0",
            "incident_key": getattr(context, "incident_key", ""),
            "profile": {
                "service": getattr(context, "service", ""),
                "fault_types": list(getattr(context, "fault_types", ())),
            },
            "toggle": {"enabled": toggle.enabled, "reason": toggle.reason},
            "top_k_requested": self.k if toggle.enabled else 0,
            "candidates": [],
            "next_stage": "safety_approval_and_grpo_policy",
        }
        if not toggle.enabled:
            base_packet["disabled_reason"] = toggle.reason
            return base_packet
        if not self.recommender.fitted_split_rows:
            raise SchemaError("recommend before calling fit on legal train/calibration rows")
        query = build_content_query(context)
        safe_candidates = []
        for runbook in self.catalogue:
            reasons = self._safety_exclusions(runbook, context, template_values or {})
            if not reasons:
                safe_candidates.append(runbook)
        penalties = {
            runbook.action_id: risk_penalty_overrides.get(
                runbook.action_id, default_risk_penalty(runbook)
            )
            if risk_penalty_overrides else default_risk_penalty(runbook)
            for runbook in safe_candidates
        }
        raw_scores = self.recommender.score(query, safe_candidates, risk_penalty_by_action=penalties)
        ranked = rank_candidates(raw_scores, min(self.k, len(safe_candidates)))
        component_models = {
            "content": self.recommender.content_model.score(query, safe_candidates),
            "collaborative": self.recommender.collaborative_model.score(query, safe_candidates),
            "success": self.recommender.success_model.score(query, safe_candidates),
        }
        candidates: list[dict[str, Any]] = []
        for rank, (action_id, score) in enumerate(ranked, start=1):
            runbook = self.catalogue_by_id[action_id]
            penalty = penalties[action_id]
            components = {
                name: model[action_id]
                for name, model in component_models.items()
            }
            candidates.append({
                "rank": rank,
                "action_id": action_id,
                "tool_name": runbook.tool_name,
                "score": score,
                "component_scores": components,
                "risk_penalty": penalty,
                "mutating": runbook.mutating,
                "risk": runbook.risk,
                "stage": runbook.stage,
                "approval_required_before_execution": runbook.mutating,
                "parameter_template": runbook.parameter_template,
            })
        base_packet["candidate_pool_size"] = len(safe_candidates)
        base_packet["weights"] = dict(self.recommender.weights)
        base_packet["candidates"] = candidates
        return base_packet

    def feedback_row(
        self,
        packet: Mapping[str, Any],
        action_id: str,
        *,
        selected: bool,
        relevance: float,
        outcome: str,
        split: str,
        recorded_at_unix: float | None = None,
    ) -> InteractionRow:
        """Create an ablation row after a human/system decision is known."""
        if packet.get("contract_version") != "rs-v0":
            raise SchemaError("unknown recommendation packet contract")
        candidate = next((
            item for item in packet.get("candidates", [])
            if item.get("action_id") == action_id
        ), None)
        if candidate is None:
            raise SchemaError(f"action was not in packet: {action_id}")
        if selected != (outcome != "not_selected"):
            raise SchemaError("selected/outcome combination is inconsistent")
        row = InteractionRow(
            incident_key=str(packet["incident_key"]),
            action_id=action_id,
            service=str(packet.get("profile", {}).get("service", "")),
            fault_types=tuple(packet.get("profile", {}).get("fault_types", ())),
            outcome=outcome,
            relevance=relevance,
            selected=selected,
            split=split,
            eligible_for_fit=split in {"train", "calibration"},
            recorded_at_unix=recorded_at_unix,
            source_run="rs_feedback",
        )
        validate_interactions([row])
        return row

    def _safety_exclusions(
        self,
        runbook: Runbook,
        context: Any,
        template_values: Mapping[str, Any],
    ) -> list[str]:
        reasons: list[str] = []
        if runbook.tool_name not in self.available_tools:
            reasons.append("tool_unavailable")
        if runbook.mutating:
            if not bool(getattr(context, "approval_granted", False)):
                reasons.append("mutation_approval_missing")
            if int(getattr(context, "mutation_budget_remaining", 0)) <= 0:
                reasons.append("mutation_budget_exhausted")
        required = self._required_inputs(runbook)
        missing = [
            name for name in required
            if name not in template_values and not self._known_context_input(name, context)
        ]
        if missing:
            reasons.append("missing_prerequisite:" + ",".join(sorted(missing)))
        return reasons

    @staticmethod
    def _required_inputs(runbook: Runbook) -> list[str]:
        required: set[str] = set()
        def visit(value: Any) -> None:
            if isinstance(value, str):
                for name, _kind, default in _TEMPLATE_RE.findall(value):
                    if default == "":
                        required.add(name)
            elif isinstance(value, dict):
                for child in value.values():
                    visit(child)
            elif isinstance(value, (list, tuple)):
                for child in value:
                    visit(child)
        visit(runbook.parameter_template)
        return sorted(required)

    @staticmethod
    def _known_context_input(name: str, context: Any) -> bool:
        boolean_inputs = {
            "active_chaos_experiment": bool(getattr(context, "active_chaos_experiment", False)),
            "deployment_recently_changed": bool(getattr(context, "deployment_recently_changed", False)),
            "mitigation_in_progress": True,
        }
        derived_inputs = {
            "workload_kind": "deployment",
            "severity": str(getattr(context, "severity", "")),
            "recommendation_summary": "Top-K recommendation pending approval",
        }
        if name in boolean_inputs:
            return boolean_inputs[name]
        if name in derived_inputs:
            return bool(derived_inputs[name])
        return False
