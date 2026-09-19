"""Mapping-constrained second-pass adjudication for transition ambiguities.

This module is deliberately separate from :mod:`audio.transition`.  The old
probe answers “what source occurrence wins globally?”; this pass answers only
“does each already accepted adjacent occurrence support its expected position
near this boundary?”.  It never mutates a timeline and never grants overlap
authority.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from lyric_aligner.audio.features import FeatureBundle, RetrievalResult, retrieve_coarse_window
from lyric_aligner.timeline.projector import source_time_at_mix

SCHEMA_VERSION = "adjacent-transition-adjudication-1.0"
POLICY_ID = "adjacent-transition-positional-v1-conservative"


class AdjudicationError(ValueError):
    pass


@dataclass(frozen=True)
class Policy:
    source_radius_seconds: float = 1.25
    sample_step_seconds: float = 3.0
    window_seconds: float = 6.0
    strong_score: float = 0.72
    strong_margin: float = 0.012
    minimum_feature_agreement: int = 2
    minimum_windows_per_side: int = 2
    minimum_support_duration_seconds: float = 1.0
    allowed_boundary_seconds: float = 10.0


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _finite(value: Any) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise AdjudicationError("non-finite numeric value")
    return result


def _mapping_hash(mapping: dict[str, Any]) -> str:
    return _sha(mapping)


def _mapping_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    result = payload.get("result", payload)
    timewarp = result.get("timewarp") if isinstance(result, dict) else None
    mapping = timewarp.get("mapping") if isinstance(timewarp, dict) else None
    if not isinstance(mapping, dict) or bool(timewarp.get("blocked")):
        raise AdjudicationError("mapping is missing or blocked")
    return mapping


def _activity(result: RetrievalResult, policy: Policy) -> bool:
    top = result.top1
    return (top.fused_score >= policy.strong_score and
            result.margin >= policy.strong_margin and
            top.feature_agreement >= policy.minimum_feature_agreement and
            not result.ambiguous)


def _window_starts(start: float, end: float, step: float, width: float) -> list[float]:
    if end - start < width:
        return [max(start, (start + end - width) / 2.0)]
    count = max(2, int(math.floor((end - start - width) / step)) + 1)
    return [start + min(index * step, end - start - width) for index in range(count)]


def adjudicate_transition(
    *,
    issue: dict[str, Any],
    transition: dict[str, Any],
    left_mapping: dict[str, Any],
    right_mapping: dict[str, Any],
    mix_features: FeatureBundle,
    left_source_features: FeatureBundle,
    right_source_features: FeatureBundle,
    retrieve: Callable[..., RetrievalResult] = retrieve_coarse_window,
    policy: Policy = Policy(),
) -> dict[str, Any]:
    left_id = str(transition.get("left_occurrence_id") or "")
    right_id = str(transition.get("right_occurrence_id") or "")
    if issue.get("kind") != "transition_ambiguity" or issue.get("code") != "ambiguous_source_occurrence":
        raise AdjudicationError("unsupported issue identity")
    if (issue.get("left_occurrence_id"), issue.get("right_occurrence_id")) != (left_id, right_id):
        raise AdjudicationError("issue transition pair mismatch")
    nominal = _finite(transition["nominal_boundary"])
    issue_start = _finite(issue["interval_start"])
    issue_end = _finite(issue["interval_end"])
    if issue_end <= issue_start or nominal < issue_start - policy.allowed_boundary_seconds or nominal > issue_end + policy.allowed_boundary_seconds:
        raise AdjudicationError("issue interval/order mismatch")

    mix_start = max(0.0, issue_start)
    mix_end = min(mix_features.duration_seconds, issue_end)
    if mix_end <= mix_start:
        raise AdjudicationError("transition window outside mix")

    rows: list[dict[str, Any]] = []
    for side, mapping, source in (("left", left_mapping, left_source_features), ("right", right_mapping, right_source_features)):
        values = []
        for window_start in _window_starts(mix_start, mix_end, policy.sample_step_seconds, policy.window_seconds):
            window_end = min(mix_end, window_start + policy.window_seconds)
            expected = source_time_at_mix(mapping, (window_start + window_end) / 2.0)
            try:
                result = retrieve(
                    mix_features, source,
                    mix_start=window_start, mix_end=window_end,
                    source_search_start=max(0.0, expected - policy.source_radius_seconds),
                    source_search_end=min(source.duration_seconds, expected + policy.source_radius_seconds),
                    top_k=4, nms_separation_seconds=0.25,
                    min_score=policy.strong_score, min_margin=policy.strong_margin,
                )
            except (ValueError, IndexError):
                values.append({"mix_start": window_start, "mix_end": window_end, "expected_source_center": expected, "search_range": [max(0.0, expected - policy.source_radius_seconds), min(source.duration_seconds, expected + policy.source_radius_seconds)], "local": None, "strong": False, "error": "insufficient_expected_position_support"})
                continue
            values.append({
                "mix_start": window_start, "mix_end": window_end,
                "expected_source_center": expected,
                "search_range": [max(0.0, expected - policy.source_radius_seconds), min(source.duration_seconds, expected + policy.source_radius_seconds)],
                "local": result.to_dict(), "strong": _activity(result, policy),
            })
        rows.append({"side": side, "windows": values, "strong_window_count": sum(bool(v["strong"]) for v in values)})

    left, right = rows
    left_strong = [v for v in left["windows"] if v["strong"]]
    right_strong = [v for v in right["windows"] if v["strong"]]
    simultaneous = any(max(a["mix_start"], b["mix_start"]) < min(a["mix_end"], b["mix_end"]) for a in left_strong for b in right_strong)
    left_before = any(v["strong"] and v["mix_end"] <= nominal for v in left["windows"])
    right_after = any(v["strong"] and v["mix_start"] >= nominal for v in right["windows"])
    left_after = any(v["strong"] and v["mix_start"] >= nominal for v in left["windows"])
    right_before = any(v["strong"] and v["mix_end"] <= nominal for v in right["windows"])
    enough = len(left_strong) >= policy.minimum_windows_per_side and len(right_strong) >= policy.minimum_windows_per_side
    sequential = left_before and right_after and not simultaneous and (not left_after or not right_before)
    action = "resolved_clear" if enough and sequential else "review"
    reason = "expected-position supports a monotonic left-to-right transition" if action == "resolved_clear" else (
        "both expected positions are simultaneously strong; overlap remains review-only" if simultaneous else
        "insufficient or conflicting expected-position evidence")
    confidence = "high" if action == "resolved_clear" else "low"
    return {
        "schema_version": SCHEMA_VERSION, "policy_id": POLICY_ID,
        "issue_id": str(issue.get("candidate_id") or ""),
        "left_occurrence_id": left_id, "right_occurrence_id": right_id,
        "nominal_boundary": nominal, "issue_interval": [issue_start, issue_end],
        "evidence": rows,
        "activity_state": {"left_before": left_before, "right_after": right_after, "left_after": left_after, "right_before": right_before, "simultaneous_expected_support": simultaneous},
        "recommendation": {"action": action, "reason": reason, "confidence": confidence},
        "authority": {"automatic_scope": ["resolved_clear"], "confirmed_overlap_automatic": False},
        "timing_mutation_performed": False,
        "mapping_hashes": {"left": _mapping_hash(left_mapping), "right": _mapping_hash(right_mapping)},
    }


def build_review_recommendation(*, task_fingerprint: str, base_run_artifact_id: str, evidence: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items = []
    for row in evidence:
        rec = row["recommendation"]
        items.append({"issue_id": row["issue_id"], "action": rec["action"] if rec["action"] == "resolved_clear" else None, "recommendation": rec, "review_required": rec["action"] != "resolved_clear"})
    return {"schema_version": SCHEMA_VERSION, "task_fingerprint_sha256": task_fingerprint, "base_run_artifact_id": base_run_artifact_id, "automatic_authority": "resolved_clear_only", "items": items}
