"""Task-scoped, replayable review decisions for v4 production issues.

Transition evidence is reviewed at candidate granularity. A human may clear one
false-positive interval without implicitly clearing other intervals on the same
A→B boundary. Confirmed overlap remains blocked until a dedicated recomposition
stage materializes new dual-track timeline evidence.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from lyric_aligner.contracts.artifacts import canonical_json_sha256


REVIEW_DECISION_SCHEMA_VERSION = "1.1"
_TRANSITION_KINDS = {"transition", "transition_overlap", "transition_ambiguity"}


class ReviewDecisionError(ValueError):
    """Raised when a review template/decision cannot be safely replayed."""


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ReviewDecisionError(f"review issue is missing {key}")
    return value


def _candidate_interval(issue: dict[str, Any]) -> tuple[float, float] | None:
    if "interval_start" not in issue and "interval_end" not in issue:
        return None
    try:
        start = float(issue["interval_start"])
        end = float(issue["interval_end"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ReviewDecisionError("transition review issue has invalid candidate interval") from exc
    if start < 0 or end <= start:
        raise ReviewDecisionError("transition review issue candidate interval is invalid")
    return start, end


def _issue_identity(issue: dict[str, Any], *, task_fingerprint_sha256: str) -> dict[str, Any]:
    kind = _required_text(issue, "kind")
    if kind in _TRANSITION_KINDS:
        identity = {
            "schema_version": REVIEW_DECISION_SCHEMA_VERSION,
            "task_fingerprint_sha256": task_fingerprint_sha256,
            "kind": kind,
            "code": str(issue.get("code") or "transition_overlap_or_ambiguity"),
            "left_occurrence_id": _required_text(issue, "left_occurrence_id"),
            "right_occurrence_id": _required_text(issue, "right_occurrence_id"),
        }
        candidate_id = str(issue.get("candidate_id") or "").strip()
        if kind != "transition" and not candidate_id:
            raise ReviewDecisionError("candidate-level transition issue is missing candidate_id")
        if candidate_id:
            identity["candidate_id"] = candidate_id
            _candidate_interval(issue)
        return identity
    if kind == "timewarp":
        return {
            "schema_version": REVIEW_DECISION_SCHEMA_VERSION,
            "task_fingerprint_sha256": task_fingerprint_sha256,
            "kind": kind,
            "code": str(issue.get("code") or "effective_mapping_blocked"),
            "occurrence_id": _required_text(issue, "occurrence_id"),
        }
    raise ReviewDecisionError(f"unsupported review issue kind {kind!r}")


def normalize_review_issue(
    issue: dict[str, Any], *, task_fingerprint_sha256: str
) -> dict[str, Any]:
    """Attach a deterministic task-scoped issue_id without hashing display text."""

    identity = _issue_identity(issue, task_fingerprint_sha256=task_fingerprint_sha256)
    normalized = deepcopy(issue)
    normalized.setdefault("code", identity["code"])
    normalized["issue_id"] = canonical_json_sha256(identity)
    return normalized


def allowed_actions(issue: dict[str, Any]) -> tuple[str, ...]:
    kind = _required_text(issue, "kind")
    if kind in _TRANSITION_KINDS:
        return ("resolved_clear", "confirmed_overlap")
    if kind == "timewarp":
        return ("confirmed_requires_rebuild",)
    raise ReviewDecisionError(f"unsupported review issue kind {kind!r}")


def build_review_template(
    run_payload: dict[str, Any], *, base_run_artifact_id: str
) -> dict[str, Any]:
    fingerprint = _required_text(run_payload, "task_fingerprint_sha256")
    if run_payload.get("status") != "review_required":
        raise ReviewDecisionError("review template requires a review_required production run")
    if run_payload.get("legacy_fallback_used") is not False:
        raise ReviewDecisionError("review template refuses a run that used legacy fallback")
    issues = run_payload.get("issues")
    if not isinstance(issues, list) or not issues:
        raise ReviewDecisionError("review_required production run contains no review issues")

    normalized = [
        normalize_review_issue(issue, task_fingerprint_sha256=fingerprint) for issue in issues
    ]
    ids = [str(issue["issue_id"]) for issue in normalized]
    if len(ids) != len(set(ids)):
        raise ReviewDecisionError("production run contains duplicate logical review issue identities")

    return {
        "schema_version": REVIEW_DECISION_SCHEMA_VERSION,
        "algorithm_version": str(run_payload.get("algorithm_version") or ""),
        "task_fingerprint_sha256": fingerprint,
        "base_run_artifact_id": str(base_run_artifact_id),
        "review_items": [
            {
                "issue_id": issue["issue_id"],
                "issue": issue,
                "allowed_actions": list(allowed_actions(issue)),
                "decision": None,
            }
            for issue in normalized
        ],
    }


def _validate_template_header(
    run_payload: dict[str, Any],
    template: dict[str, Any],
    *,
    base_run_artifact_id: str,
) -> tuple[str, list[dict[str, Any]]]:
    if template.get("schema_version") != REVIEW_DECISION_SCHEMA_VERSION:
        raise ReviewDecisionError("review decision schema_version mismatch")
    fingerprint = _required_text(run_payload, "task_fingerprint_sha256")
    if template.get("task_fingerprint_sha256") != fingerprint:
        raise ReviewDecisionError("review decisions belong to another task")
    if template.get("algorithm_version") != run_payload.get("algorithm_version"):
        raise ReviewDecisionError("review decisions algorithm version mismatch")
    if template.get("base_run_artifact_id") != base_run_artifact_id:
        raise ReviewDecisionError("review decisions belong to another production run artifact")
    items = template.get("review_items")
    if not isinstance(items, list) or not items:
        raise ReviewDecisionError("review decision file has no review_items")
    return fingerprint, items


def _decision_action(item: dict[str, Any]) -> tuple[str | None, str]:
    decision = item.get("decision")
    if decision is None:
        return None, ""
    if not isinstance(decision, dict):
        raise ReviewDecisionError("review item decision must be null or an object")
    action = str(decision.get("action") or "").strip()
    rationale = str(decision.get("rationale") or "").strip()
    if not action:
        raise ReviewDecisionError("review decision action must be non-empty")
    if not rationale:
        raise ReviewDecisionError("review decision rationale must be non-empty")
    return action, rationale


def _annotate_transition(
    transitions: list[dict[str, Any]],
    issue: dict[str, Any],
    *,
    action: str,
    rationale: str,
) -> None:
    left = issue.get("left_occurrence_id")
    right = issue.get("right_occurrence_id")
    matches = [
        row
        for row in transitions
        if row.get("left_occurrence_id") == left and row.get("right_occurrence_id") == right
    ]
    if len(matches) != 1:
        raise ReviewDecisionError("transition review issue does not map to exactly one transition summary")
    resolution = {
        "issue_id": issue["issue_id"],
        "candidate_id": str(issue.get("candidate_id") or ""),
        "kind": issue["kind"],
        "action": action,
        "rationale": rationale,
        "effective_blocked": action != "resolved_clear",
    }
    interval = _candidate_interval(issue)
    if interval is not None:
        resolution["interval_start"] = interval[0]
        resolution["interval_end"] = interval[1]
    matches[0].setdefault("review_resolutions", []).append(resolution)


def apply_review_template(
    run_payload: dict[str, Any],
    template: dict[str, Any],
    *,
    base_run_artifact_id: str,
) -> dict[str, Any]:
    """Replay decisions and derive a new immutable reviewed-run payload."""

    if run_payload.get("status") != "review_required":
        raise ReviewDecisionError("review decisions require a review_required production run")
    if run_payload.get("legacy_fallback_used") is not False:
        raise ReviewDecisionError("review decisions refuse a run that used legacy fallback")

    fingerprint, items = _validate_template_header(
        run_payload,
        template,
        base_run_artifact_id=base_run_artifact_id,
    )
    base_issues = run_payload.get("issues")
    if not isinstance(base_issues, list) or not base_issues:
        raise ReviewDecisionError("production run contains no review issues")
    normalized_issues = [
        normalize_review_issue(issue, task_fingerprint_sha256=fingerprint)
        for issue in base_issues
    ]
    by_id = {str(issue["issue_id"]): issue for issue in normalized_issues}
    if len(by_id) != len(normalized_issues):
        raise ReviewDecisionError("production run contains duplicate logical review issue identities")

    seen: set[str] = set()
    active: dict[str, dict[str, Any]] = {key: deepcopy(value) for key, value in by_id.items()}
    resolved: list[dict[str, Any]] = []
    applied: list[dict[str, Any]] = []
    transitions = deepcopy(run_payload.get("transitions", []))
    if not isinstance(transitions, list):
        raise ReviewDecisionError("production run transitions must be a list")

    for item in items:
        if not isinstance(item, dict):
            raise ReviewDecisionError("review_items entries must be objects")
        issue_id = str(item.get("issue_id") or "").strip()
        if not issue_id:
            raise ReviewDecisionError("review item is missing issue_id")
        if issue_id in seen:
            raise ReviewDecisionError(f"duplicate review item for issue_id {issue_id}")
        seen.add(issue_id)
        current = by_id.get(issue_id)
        if current is None:
            raise ReviewDecisionError(f"review item references unknown issue_id {issue_id}")

        snapshot = item.get("issue")
        if not isinstance(snapshot, dict):
            raise ReviewDecisionError("review item issue snapshot must be an object")
        normalized_snapshot = normalize_review_issue(snapshot, task_fingerprint_sha256=fingerprint)
        if normalized_snapshot != current:
            raise ReviewDecisionError(f"review item snapshot no longer matches issue {issue_id}")
        expected_actions = list(allowed_actions(current))
        if item.get("allowed_actions") != expected_actions:
            raise ReviewDecisionError(f"allowed_actions mismatch for issue {issue_id}")

        action, rationale = _decision_action(item)
        if action is None:
            continue
        if action not in expected_actions:
            raise ReviewDecisionError(
                f"action {action!r} is not allowed for {current.get('kind')} issue {issue_id}"
            )

        record = {
            "issue_id": issue_id,
            "kind": current["kind"],
            "candidate_id": str(current.get("candidate_id") or ""),
            "action": action,
            "rationale": rationale,
        }
        interval = _candidate_interval(current)
        if interval is not None:
            record["interval_start"] = interval[0]
            record["interval_end"] = interval[1]
        applied.append(record)

        if current["kind"] in _TRANSITION_KINDS:
            _annotate_transition(
                transitions,
                current,
                action=action,
                rationale=rationale,
            )
            if action == "resolved_clear":
                resolved.append({**record, "effect": "clear_review_block"})
                del active[issue_id]
            elif action == "confirmed_overlap":
                updated = {
                    **active[issue_id],
                    "status": "confirmed",
                    "decision_action": action,
                    "requires_recomposition": True,
                }
                if interval is not None:
                    updated["confirmed_interval"] = [interval[0], interval[1]]
                active[issue_id] = updated
        elif current["kind"] == "timewarp":
            active[issue_id] = {
                **active[issue_id],
                "status": "confirmed",
                "decision_action": action,
                "requires_timeline_rebuild": True,
            }

    missing_from_template = sorted(set(by_id) - seen)
    if missing_from_template:
        raise ReviewDecisionError(
            "review decision file does not contain every base issue: "
            + ", ".join(missing_from_template)
        )

    remaining = [active[key] for key in by_id if key in active]
    status = "review_required" if remaining else "ready_for_render"
    reviewed = deepcopy(run_payload)
    reviewed["schema_version"] = str(run_payload.get("schema_version") or "1.0")
    reviewed["status"] = status
    reviewed["issues"] = remaining
    reviewed["transitions"] = transitions
    reviewed["review_resolution"] = {
        "schema_version": REVIEW_DECISION_SCHEMA_VERSION,
        "base_run_artifact_id": base_run_artifact_id,
        "decision_count": len(applied),
        "resolved_issue_count": len(resolved),
        "remaining_issue_count": len(remaining),
        "applied_decisions": applied,
        "resolved_issues": resolved,
        "remaining_issue_ids": [issue["issue_id"] for issue in remaining],
    }
    return reviewed
