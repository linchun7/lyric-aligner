"""Deterministic local ASR/forced-alignment job planning.

The planner turns already-materialized uncertainty into bounded local evidence
jobs.  It never executes a model and never changes canonical text/timing.  Raw
lyric text is excluded from the plan; jobs bind line indices/hashes/windows.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Iterable


ALIGNMENT_PLAN_SCHEMA_VERSION = "1.0"
ALIGNMENT_PLANNER_POLICY_ID = "local-evidence-planner-2026-08-18-v1"


class AlignmentPlanningError(ValueError):
    """Raised when a safe deterministic local evidence plan cannot be built."""


@dataclass(frozen=True)
class AlignmentPlannerConfig:
    mix_context_ms: int = 1500
    source_context_ms: int = 1000
    editor_boundary_disagreement_ms: int = 500
    editor_ambiguous_margin_max: float = 0.08
    include_editor_missing: bool = False
    max_jobs: int = 200

    def validate(self) -> None:
        if self.mix_context_ms < 0 or self.source_context_ms < 0:
            raise AlignmentPlanningError("planner context values must be >= 0")
        if self.editor_boundary_disagreement_ms < 0:
            raise AlignmentPlanningError(
                "editor_boundary_disagreement_ms must be >= 0"
            )
        if not 0.0 <= float(self.editor_ambiguous_margin_max) <= 1.0:
            raise AlignmentPlanningError(
                "editor_ambiguous_margin_max must be within [0,1]"
            )
        if self.max_jobs < 1:
            raise AlignmentPlanningError("max_jobs must be >= 1")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sha(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _text_sha(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _finite_interval(
    start: Any,
    end: Any,
    *,
    context_ms: int,
    label: str,
) -> list[int] | None:
    if start is None:
        return None
    try:
        start_ms = int(round(float(start)))
    except (TypeError, ValueError) as exc:
        raise AlignmentPlanningError(f"{label} start is invalid") from exc
    if end is None:
        end_ms = start_ms + 1
    else:
        try:
            end_ms = int(round(float(end)))
        except (TypeError, ValueError) as exc:
            raise AlignmentPlanningError(f"{label} end is invalid") from exc
    if end_ms <= start_ms:
        end_ms = start_ms + 1
    return [max(0, start_ms - context_ms), max(1, end_ms + context_ms)]


def _timeline_result(payload: dict[str, Any]) -> dict[str, Any]:
    result = payload.get("result")
    if not isinstance(result, dict):
        raise AlignmentPlanningError("timeline payload has no result")
    if not isinstance(result.get("lines"), list):
        raise AlignmentPlanningError("timeline result has no lines")
    return result


def _line_index(
    timeline_payloads: Iterable[dict[str, Any]],
) -> tuple[dict[tuple[str, int], dict[str, Any]], dict[str, dict[str, Any]]]:
    lines: dict[tuple[str, int], dict[str, Any]] = {}
    occurrences: dict[str, dict[str, Any]] = {}
    for payload in timeline_payloads:
        result = _timeline_result(payload)
        occurrence_id = str(result.get("occurrence_id") or payload.get("occurrence_id") or "")
        track_id = str(result.get("track_id") or payload.get("track_id") or "")
        if not occurrence_id or not track_id:
            raise AlignmentPlanningError("timeline is missing occurrence/track identity")
        if occurrence_id in occurrences:
            raise AlignmentPlanningError(f"duplicate timeline occurrence {occurrence_id}")
        occurrence = {
            "occurrence_id": occurrence_id,
            "track_id": track_id,
            "ordinal": int(result.get("ordinal", -1)),
            "language_profile": str(result.get("language_profile") or "auto"),
            "canonical_selection_sha256": str(
                result.get("canonical_selection_sha256") or ""
            ),
        }
        occurrences[occurrence_id] = occurrence
        for line in result["lines"]:
            if not isinstance(line, dict):
                raise AlignmentPlanningError("timeline line must be an object")
            try:
                canonical_index = int(line["canonical_line_index"])
            except (KeyError, TypeError, ValueError) as exc:
                raise AlignmentPlanningError(
                    f"timeline {occurrence_id} has invalid canonical line index"
                ) from exc
            key = (occurrence_id, canonical_index)
            if key in lines:
                raise AlignmentPlanningError(
                    f"duplicate canonical line identity {occurrence_id}/{canonical_index}"
                )
            lines[key] = {
                **line,
                **occurrence,
                "canonical_text_sha256": _text_sha(str(line.get("text") or "")),
            }
    if not occurrences:
        raise AlignmentPlanningError("planner requires at least one canonical timeline")
    return lines, occurrences


def _issue_occurrences(issue: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("occurrence_id", "left_occurrence_id", "right_occurrence_id"):
        value = str(issue.get(key) or "").strip()
        if value and value not in values:
            values.append(value)
    raw = issue.get("occurrences")
    if isinstance(raw, list):
        for item in raw:
            value = str(item or "").strip()
            if value and value not in values:
                values.append(value)
    return values


def _issue_mix_interval(issue: dict[str, Any], config: AlignmentPlannerConfig) -> list[int] | None:
    interval = issue.get("confirmed_interval")
    if isinstance(interval, list) and len(interval) == 2:
        return _finite_interval(
            float(interval[0]) * 1000.0,
            float(interval[1]) * 1000.0,
            context_ms=config.mix_context_ms,
            label="review issue interval",
        )
    for left_key, right_key, seconds in (
        ("interval_start", "interval_end", True),
        ("mix_before", "mix_after", True),
        ("mix_start_ms", "mix_end_ms", False),
    ):
        if left_key in issue:
            left = issue.get(left_key)
            right = issue.get(right_key)
            if seconds:
                left = None if left is None else float(left) * 1000.0
                right = None if right is None else float(right) * 1000.0
            return _finite_interval(
                left,
                right,
                context_ms=config.mix_context_ms,
                label="review issue interval",
            )
    return None


def _requested_capabilities(line: dict[str, Any] | None) -> list[str]:
    capabilities = ["mix_asr", "word_timestamps"]
    if line is not None and line.get("source_start_ms") is not None:
        capabilities.append("source_forced_alignment")
    return capabilities


def _job_key(
    *,
    occurrence_id: str,
    canonical_line_index: int | None,
    mix_window_ms: list[int] | None,
) -> tuple[Any, ...]:
    return (
        occurrence_id,
        canonical_line_index,
        None if mix_window_ms is None else tuple(mix_window_ms),
    )


def _append_reason(
    jobs: dict[tuple[Any, ...], dict[str, Any]],
    *,
    occurrence: dict[str, Any],
    line: dict[str, Any] | None,
    reason: str,
    priority: str,
    mix_window_ms: list[int] | None,
    source_window_ms: list[int] | None,
    evidence: dict[str, Any] | None = None,
) -> None:
    line_index = None if line is None else int(line["canonical_line_index"])
    key = _job_key(
        occurrence_id=occurrence["occurrence_id"],
        canonical_line_index=line_index,
        mix_window_ms=mix_window_ms,
    )
    row = jobs.get(key)
    if row is None:
        row = {
            "occurrence_id": occurrence["occurrence_id"],
            "track_id": occurrence["track_id"],
            "ordinal": occurrence["ordinal"],
            "language_profile": occurrence["language_profile"],
            "canonical_selection_sha256": occurrence["canonical_selection_sha256"],
            "canonical_line_index": line_index,
            "canonical_text_sha256": None
            if line is None
            else line["canonical_text_sha256"],
            "mix_window_ms": mix_window_ms,
            "source_window_ms": source_window_ms,
            "requested_capabilities": _requested_capabilities(line),
            "reasons": [],
            "reason_evidence": [],
            "priority": priority,
            "execution_state": "planned_not_executed",
        }
        jobs[key] = row
    if reason not in row["reasons"]:
        row["reasons"].append(reason)
    if evidence:
        row["reason_evidence"].append({"reason": reason, **evidence})
    if priority == "high":
        row["priority"] = "high"


def _jobs_from_run_issues(
    run: dict[str, Any],
    *,
    occurrences: dict[str, dict[str, Any]],
    jobs: dict[tuple[Any, ...], dict[str, Any]],
    config: AlignmentPlannerConfig,
) -> None:
    issues = run.get("issues") or []
    if not isinstance(issues, list):
        raise AlignmentPlanningError("run issues must be a list")
    for issue in issues:
        if not isinstance(issue, dict):
            raise AlignmentPlanningError("run issue must be an object")
        occurrence_ids = _issue_occurrences(issue)
        if not occurrence_ids:
            continue
        mix_window = _issue_mix_interval(issue, config)
        reason = "run_issue:" + str(issue.get("code") or issue.get("kind") or "unknown")
        evidence = {
            "issue_id": str(issue.get("issue_id") or ""),
            "candidate_id": str(issue.get("candidate_id") or ""),
            "status": str(issue.get("status") or ""),
        }
        for occurrence_id in occurrence_ids:
            occurrence = occurrences.get(occurrence_id)
            if occurrence is None:
                continue
            _append_reason(
                jobs,
                occurrence=occurrence,
                line=None,
                reason=reason,
                priority="high",
                mix_window_ms=mix_window,
                source_window_ms=None,
                evidence=evidence,
            )


def _jobs_from_editor(
    editor_evidence: dict[str, Any],
    *,
    lines: dict[tuple[str, int], dict[str, Any]],
    occurrences: dict[str, dict[str, Any]],
    jobs: dict[tuple[Any, ...], dict[str, Any]],
    config: AlignmentPlannerConfig,
) -> None:
    if editor_evidence.get("mode") != "shadow_only":
        raise AlignmentPlanningError("editor evidence must be shadow_only")
    if editor_evidence.get("authority", {}).get("automatic_timing_change_allowed") is not False:
        raise AlignmentPlanningError("editor evidence unexpectedly permits timing changes")
    occurrence_rows = editor_evidence.get("occurrences")
    if not isinstance(occurrence_rows, list):
        raise AlignmentPlanningError("editor evidence occurrences must be a list")
    for occurrence_row in occurrence_rows:
        occurrence_id = str(occurrence_row.get("occurrence_id") or "")
        occurrence = occurrences.get(occurrence_id)
        if occurrence is None:
            raise AlignmentPlanningError(
                f"editor evidence occurrence {occurrence_id} has no canonical timeline"
            )
        rows = occurrence_row.get("lines")
        if not isinstance(rows, list):
            raise AlignmentPlanningError("editor evidence lines must be a list")
        for editor_line in rows:
            try:
                line_index = int(editor_line["canonical_line_index"])
            except (KeyError, TypeError, ValueError) as exc:
                raise AlignmentPlanningError(
                    "editor evidence has invalid canonical_line_index"
                ) from exc
            line = lines.get((occurrence_id, line_index))
            if line is None:
                raise AlignmentPlanningError(
                    f"editor evidence line {occurrence_id}/{line_index} is not canonical"
                )
            if editor_line.get("canonical_text_sha256") != line["canonical_text_sha256"]:
                raise AlignmentPlanningError("editor/canonical line text identity mismatch")

            mix_window = _finite_interval(
                line.get("mix_start_ms"),
                line.get("mix_end_ms"),
                context_ms=config.mix_context_ms,
                label="canonical mix line",
            )
            source_window = _finite_interval(
                line.get("source_start_ms"),
                line.get("source_end_ms"),
                context_ms=config.source_context_ms,
                label="canonical source line",
            )
            best = editor_line.get("best_editor_cue_number")
            onset = editor_line.get("suggested_onset_delta_ms")
            offset = editor_line.get("suggested_offset_delta_ms")
            margin = editor_line.get("best_candidate_margin_uncalibrated")

            if best is None:
                if config.include_editor_missing:
                    _append_reason(
                        jobs,
                        occurrence=occurrence,
                        line=line,
                        reason="editor_no_candidate",
                        priority="medium",
                        mix_window_ms=mix_window,
                        source_window_ms=source_window,
                    )
                continue

            disagreement = False
            values: list[int] = []
            for value in (onset, offset):
                if value is None:
                    continue
                try:
                    values.append(abs(int(value)))
                except (TypeError, ValueError) as exc:
                    raise AlignmentPlanningError("editor delta is invalid") from exc
            if values and max(values) >= config.editor_boundary_disagreement_ms:
                disagreement = True
                _append_reason(
                    jobs,
                    occurrence=occurrence,
                    line=line,
                    reason="editor_boundary_disagreement",
                    priority="high",
                    mix_window_ms=mix_window,
                    source_window_ms=source_window,
                    evidence={
                        "editor_cue_number": int(best),
                        "max_abs_delta_ms": max(values),
                    },
                )
            if margin is not None:
                try:
                    margin_value = float(margin)
                except (TypeError, ValueError) as exc:
                    raise AlignmentPlanningError("editor candidate margin is invalid") from exc
                if margin_value <= config.editor_ambiguous_margin_max:
                    _append_reason(
                        jobs,
                        occurrence=occurrence,
                        line=line,
                        reason="editor_candidate_ambiguous",
                        priority="medium" if not disagreement else "high",
                        mix_window_ms=mix_window,
                        source_window_ms=source_window,
                        evidence={
                            "editor_cue_number": int(best),
                            "margin_uncalibrated": margin_value,
                        },
                    )


def build_alignment_plan(
    *,
    run: dict[str, Any],
    timeline_payloads: Iterable[dict[str, Any]],
    editor_evidence: dict[str, Any] | None = None,
    config: AlignmentPlannerConfig | None = None,
) -> dict[str, Any]:
    """Build bounded local evidence jobs without executing any backend."""

    config = config or AlignmentPlannerConfig()
    config.validate()
    lines, occurrences = _line_index(timeline_payloads)
    jobs: dict[tuple[Any, ...], dict[str, Any]] = {}
    _jobs_from_run_issues(
        run,
        occurrences=occurrences,
        jobs=jobs,
        config=config,
    )
    if editor_evidence is not None:
        _jobs_from_editor(
            editor_evidence,
            lines=lines,
            occurrences=occurrences,
            jobs=jobs,
            config=config,
        )

    rows = list(jobs.values())
    priority_order = {"high": 0, "medium": 1, "low": 2}
    rows.sort(
        key=lambda row: (
            priority_order.get(row["priority"], 9),
            row["ordinal"],
            row["canonical_line_index"]
            if row["canonical_line_index"] is not None
            else -1,
            row["mix_window_ms"] or [0, 0],
            row["occurrence_id"],
        )
    )
    truncated = len(rows) > config.max_jobs
    rows = rows[: config.max_jobs]
    for row in rows:
        row["reasons"].sort()
        row["requested_capabilities"] = sorted(set(row["requested_capabilities"]))
        row["job_id"] = _sha(
            {
                "occurrence_id": row["occurrence_id"],
                "canonical_line_index": row["canonical_line_index"],
                "mix_window_ms": row["mix_window_ms"],
                "source_window_ms": row["source_window_ms"],
                "requested_capabilities": row["requested_capabilities"],
                "reasons": row["reasons"],
                "canonical_text_sha256": row["canonical_text_sha256"],
            }
        )

    reason_counts: dict[str, int] = {}
    capability_counts: dict[str, int] = {}
    for row in rows:
        for reason in row["reasons"]:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        for capability in row["requested_capabilities"]:
            capability_counts[capability] = capability_counts.get(capability, 0) + 1

    return {
        "schema_version": ALIGNMENT_PLAN_SCHEMA_VERSION,
        "policy_id": ALIGNMENT_PLANNER_POLICY_ID,
        "mode": "plan_only",
        "backend_execution_performed": False,
        "canonical_text_authority": "canonical_lyrics_only",
        "primary_timing_authority": "source_to_mix_only",
        "config": config.to_dict(),
        "summary": {
            "occurrence_count": len(occurrences),
            "canonical_line_count": len(lines),
            "job_count": len(rows),
            "plan_truncated": truncated,
            "reason_counts": dict(sorted(reason_counts.items())),
            "capability_counts": dict(sorted(capability_counts.items())),
        },
        "jobs": rows,
    }
