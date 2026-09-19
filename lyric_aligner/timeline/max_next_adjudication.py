"""Unified Max Next boundary-selection semantics.

The existing precise boundary authority remains tier A.  When it cannot authorize
a mutation, a human-gold expected-loss comparison may select a tier B calibrated
best estimate or tier C automatic rescue.  Semantic/structural ambiguity is tier D
and stays out of automatic mutation.

This module deliberately does not materialize SRT.  It produces a versioned,
auditable selection recommendation so one future production materializer can own
all writes and revalidate exact provenance/global constraints in one place.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from lyric_aligner.timeline.boundary_risk import (
    BOUNDARY_RISK_POLICY_VERSION,
    BoundaryErrorProfile,
    BoundaryLocalSupport,
    BoundaryRiskDecision,
    BoundaryRiskPolicy,
    adjudicate_calibrated_fallback,
)


MAX_NEXT_ADJUDICATION_VERSION = "1.1"
MAX_NEXT_ADJUDICATION_AUTHORITY = "selection_recommendation_only_no_srt_mutation"

PRECISE_AUTO_ACTIONS = frozenset({"auto_fine_refine", "auto_safe_refine"})


class MaxNextAdjudicationError(ValueError):
    pass


@dataclass(frozen=True)
class MaxNextBoundarySelection:
    version: str
    authority: str
    tier: str
    action: str
    selected_ms: int
    editor_ms: int
    candidate_ms: int | None
    automatic_selection_recommended: bool
    production_mutation_allowed: bool
    expected_improvement_ms: float | None
    estimated_candidate_p90_error_ms: float | None
    precise_action: str
    risk_action: str | None
    risk_policy_version: str | None
    semantic_ambiguity: bool
    structural_ambiguity: bool
    global_constraints_satisfied: bool
    reason: str
    local_support_id: str | None = None
    local_support_p90_error_ms: float | None = None
    local_support_authoritative: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _precise_fields(precise_decision: Mapping[str, Any] | Any) -> tuple[str, int, int]:
    if isinstance(precise_decision, Mapping):
        action = str(precise_decision.get("action") or "").strip()
        selected_raw = precise_decision.get("selected_ms")
        editor_raw = precise_decision.get("editor_ms")
    else:
        action = str(getattr(precise_decision, "action", "") or "").strip()
        selected_raw = getattr(precise_decision, "selected_ms", None)
        editor_raw = getattr(precise_decision, "editor_ms", None)
    if not action:
        raise MaxNextAdjudicationError("precise decision action is missing")
    try:
        selected_ms = int(selected_raw)
        editor_ms = int(editor_raw)
    except (TypeError, ValueError) as exc:
        raise MaxNextAdjudicationError("precise decision timing is invalid") from exc
    if selected_ms < 0 or editor_ms < 0:
        raise MaxNextAdjudicationError("precise decision timing must be nonnegative")
    return action, selected_ms, editor_ms


def adjudicate_max_next_boundary(
    *,
    precise_decision: Mapping[str, Any] | Any,
    fallback_candidate_ms: int | None = None,
    editor_profile: BoundaryErrorProfile | None = None,
    candidate_profile: BoundaryErrorProfile | None = None,
    local_support: BoundaryLocalSupport | None = None,
    semantic_ambiguity: bool = False,
    structural_ambiguity: bool = False,
    global_constraints_satisfied: bool = True,
    risk_policy: BoundaryRiskPolicy | None = None,
) -> MaxNextBoundarySelection:
    """Select the best current boundary strategy without writing subtitle timing."""

    precise_action, precise_selected, editor_ms = _precise_fields(precise_decision)
    semantic_ambiguity = bool(semantic_ambiguity)
    structural_ambiguity = bool(structural_ambiguity)
    global_constraints_satisfied = bool(global_constraints_satisfied)

    if precise_action in PRECISE_AUTO_ACTIONS:
        if semantic_ambiguity or structural_ambiguity or not global_constraints_satisfied:
            # The global/semantic layer is a stricter downstream veto.  A local
            # precise boundary must never punch through a structural contradiction.
            return MaxNextBoundarySelection(
                version=MAX_NEXT_ADJUDICATION_VERSION,
                authority=MAX_NEXT_ADJUDICATION_AUTHORITY,
                tier="D_structural_ambiguity",
                action="structural_review",
                selected_ms=editor_ms,
                editor_ms=editor_ms,
                candidate_ms=precise_selected,
                automatic_selection_recommended=False,
                production_mutation_allowed=False,
                expected_improvement_ms=None,
                estimated_candidate_p90_error_ms=None,
                precise_action=precise_action,
                risk_action=None,
                risk_policy_version=None,
                semantic_ambiguity=semantic_ambiguity,
                structural_ambiguity=structural_ambiguity,
                global_constraints_satisfied=global_constraints_satisfied,
                reason="downstream semantic/structural/global constraint vetoes local precise mutation",
            )
        return MaxNextBoundarySelection(
            version=MAX_NEXT_ADJUDICATION_VERSION,
            authority=MAX_NEXT_ADJUDICATION_AUTHORITY,
            tier="A_verified_precise",
            action="select_precise",
            selected_ms=precise_selected,
            editor_ms=editor_ms,
            candidate_ms=precise_selected,
            automatic_selection_recommended=True,
            production_mutation_allowed=False,
            expected_improvement_ms=None,
            estimated_candidate_p90_error_ms=None,
            precise_action=precise_action,
            risk_action=None,
            risk_policy_version=None,
            semantic_ambiguity=False,
            structural_ambiguity=False,
            global_constraints_satisfied=True,
            reason="existing independently calibrated precise boundary authority is preferred",
        )

    if semantic_ambiguity or structural_ambiguity or not global_constraints_satisfied:
        return MaxNextBoundarySelection(
            version=MAX_NEXT_ADJUDICATION_VERSION,
            authority=MAX_NEXT_ADJUDICATION_AUTHORITY,
            tier="D_structural_ambiguity",
            action="structural_review",
            selected_ms=editor_ms,
            editor_ms=editor_ms,
            candidate_ms=None if fallback_candidate_ms is None else int(fallback_candidate_ms),
            automatic_selection_recommended=False,
            production_mutation_allowed=False,
            expected_improvement_ms=None,
            estimated_candidate_p90_error_ms=None,
            precise_action=precise_action,
            risk_action=None,
            risk_policy_version=None,
            semantic_ambiguity=semantic_ambiguity,
            structural_ambiguity=structural_ambiguity,
            global_constraints_satisfied=global_constraints_satisfied,
            reason="semantic/structural ambiguity is not reducible to a timing-error tradeoff",
        )

    if fallback_candidate_ms is None or editor_profile is None or candidate_profile is None:
        return MaxNextBoundarySelection(
            version=MAX_NEXT_ADJUDICATION_VERSION,
            authority=MAX_NEXT_ADJUDICATION_AUTHORITY,
            tier="E_keep_editor",
            action="keep_editor",
            selected_ms=editor_ms,
            editor_ms=editor_ms,
            candidate_ms=None,
            automatic_selection_recommended=False,
            production_mutation_allowed=False,
            expected_improvement_ms=None,
            estimated_candidate_p90_error_ms=None,
            precise_action=precise_action,
            risk_action=None,
            risk_policy_version=None,
            semantic_ambiguity=False,
            structural_ambiguity=False,
            global_constraints_satisfied=True,
            reason="no authoritative expected-loss fallback profile/candidate is available",
        )

    try:
        candidate_ms = int(fallback_candidate_ms)
    except (TypeError, ValueError) as exc:
        raise MaxNextAdjudicationError("fallback_candidate_ms is invalid") from exc
    if candidate_ms < 0:
        raise MaxNextAdjudicationError("fallback_candidate_ms must be nonnegative")
    risk: BoundaryRiskDecision = adjudicate_calibrated_fallback(
        editor_ms=editor_ms,
        candidate_ms=candidate_ms,
        editor_profile=editor_profile,
        candidate_profile=candidate_profile,
        local_support=local_support,
        semantic_ambiguity=False,
        structural_ambiguity=False,
        global_constraints_satisfied=True,
        policy=risk_policy,
    )
    if risk.action == "auto_calibrated_better":
        tier = "B_calibrated_better"
        action = "select_calibrated_better"
        auto = True
    elif risk.action == "auto_rescue":
        tier = "C_automatic_rescue"
        action = "select_automatic_rescue"
        auto = True
    elif risk.action == "structural_review":
        tier = "D_structural_ambiguity"
        action = "structural_review"
        auto = False
    else:
        tier = "E_keep_editor"
        action = "keep_editor"
        auto = False

    return MaxNextBoundarySelection(
        version=MAX_NEXT_ADJUDICATION_VERSION,
        authority=MAX_NEXT_ADJUDICATION_AUTHORITY,
        tier=tier,
        action=action,
        selected_ms=int(risk.selected_ms),
        editor_ms=editor_ms,
        candidate_ms=candidate_ms,
        automatic_selection_recommended=auto,
        production_mutation_allowed=False,
        expected_improvement_ms=float(risk.expected_improvement_ms),
        estimated_candidate_p90_error_ms=float(risk.estimated_candidate_p90_error_ms),
        precise_action=precise_action,
        risk_action=risk.action,
        risk_policy_version=BOUNDARY_RISK_POLICY_VERSION,
        semantic_ambiguity=False,
        structural_ambiguity=False,
        global_constraints_satisfied=True,
        reason=risk.reason,
        local_support_id=risk.local_support_id,
        local_support_p90_error_ms=risk.local_support_p90_error_ms,
        local_support_authoritative=risk.local_support_authoritative,
    )


__all__ = [
    "MAX_NEXT_ADJUDICATION_VERSION",
    "MAX_NEXT_ADJUDICATION_AUTHORITY",
    "MaxNextAdjudicationError",
    "MaxNextBoundarySelection",
    "adjudicate_max_next_boundary",
]
