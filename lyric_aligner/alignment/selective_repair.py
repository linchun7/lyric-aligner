"""Bridge Smart no-audio review decisions into bounded Pro evidence jobs.

The planner consumes a Smart report and the same canonical occurrences/SRT. It
never reads audio and never changes subtitle timing. Its only job is to spend
acoustic compute on unresolved local windows instead of rescanning the program.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from lyric_aligner.text.language_spans import asr_language_hint_for_text
from lyric_aligner.text_repair import SubtitleCue
from lyric_aligner.timeline.anchor_repair import TimedCanonicalOccurrence, _cue_times


SELECTIVE_REPAIR_PLAN_SCHEMA_VERSION = "1.0"
SELECTIVE_REPAIR_POLICY_ID = "smart-to-pro-local-evidence-2026-08-19-v1"


class SelectiveRepairPlanningError(ValueError):
    """Raised when a Smart report cannot safely become a local Pro plan."""


@dataclass(frozen=True)
class SelectiveRepairConfig:
    mix_context_ms: int = 2500
    source_context_before_ms: int = 3500
    source_context_after_ms: int = 5000
    min_mix_window_ms: int = 4500
    max_jobs: int = 100

    def validate(self) -> None:
        for label, value in (
            ("mix_context_ms", self.mix_context_ms),
            ("source_context_before_ms", self.source_context_before_ms),
            ("source_context_after_ms", self.source_context_after_ms),
            ("min_mix_window_ms", self.min_mix_window_ms),
        ):
            if value < 0:
                raise SelectiveRepairPlanningError(f"{label} must be >= 0")
        if self.max_jobs < 1:
            raise SelectiveRepairPlanningError("max_jobs must be >= 1")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sha(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _text_sha(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _bounded_mix_window(
    cue: SubtitleCue,
    *,
    context_ms: int,
    minimum_ms: int,
) -> list[int]:
    start, end = _cue_times(cue)
    left = max(0, start - context_ms)
    right = end + context_ms
    if right - left < minimum_ms:
        deficit = minimum_ms - (right - left)
        grow_left = deficit // 2
        grow_right = deficit - grow_left
        left = max(0, left - grow_left)
        right += grow_right
        if left == 0 and right - left < minimum_ms:
            right = minimum_ms
    return [left, right]


def _source_window(
    occurrence: TimedCanonicalOccurrence,
    *,
    before_ms: int,
    after_ms: int,
) -> list[int]:
    starts = [occurrence.anchor_time_ms]
    ends = [occurrence.anchor_time_ms]
    if occurrence.tokens:
        starts.extend(token.start_ms for token in occurrence.tokens)
        ends.extend(token.end_ms for token in occurrence.tokens)
    return [
        max(0, min(starts) - before_ms),
        max(starts[0] + 1, max(ends) + after_ms),
    ]


def _single_canonical_ordinal(decision: Mapping[str, Any] | None) -> int | None:
    if decision is None:
        return None
    direct = decision.get("canonical_ordinal")
    if direct is not None:
        try:
            return int(direct)
        except (TypeError, ValueError):
            return None
    span = decision.get("canonical_span")
    if isinstance(span, list) and len(span) == 2:
        try:
            start, end = int(span[0]), int(span[1])
        except (TypeError, ValueError):
            return None
        if end - start == 1:
            return start
    return None


def _model_index(smart_report: Mapping[str, Any]) -> dict[int, Mapping[str, Any]]:
    rows = smart_report.get("models")
    if not isinstance(rows, list):
        return {}
    result: dict[int, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        try:
            source_ordinal = int(row["source_ordinal"])
        except (KeyError, TypeError, ValueError):
            continue
        result[source_ordinal] = row
    return result


def _safe_rate(model: Mapping[str, Any] | None) -> float | None:
    if model is None:
        return None
    try:
        rate = float(model.get("rate"))
    except (TypeError, ValueError):
        return None
    return rate if 0.5 <= rate <= 2.0 else None


def build_selective_repair_plan(
    *,
    smart_report: Mapping[str, Any],
    cues: Sequence[SubtitleCue],
    canonical: Sequence[TimedCanonicalOccurrence],
    language_by_source: Mapping[int, str] | None = None,
    config: SelectiveRepairConfig | None = None,
) -> dict[str, Any]:
    """Create bounded Pro jobs only for Smart-unresolved cues.

    Raw canonical text is never included in the returned plan. The caller can
    keep an in-memory job->canonical lookup for backend execution.
    """

    config = config or SelectiveRepairConfig()
    config.validate()
    language_by_source = language_by_source or {}

    if smart_report.get("mode") != "smart_anchor_timeline_repair_no_audio":
        raise SelectiveRepairPlanningError("input is not a Smart no-audio report")
    if smart_report.get("audio_read") is not False:
        raise SelectiveRepairPlanningError("Smart report unexpectedly reports audio reads")

    timing_rows = smart_report.get("timing_decisions")
    text_rows = smart_report.get("text_decisions")
    if not isinstance(timing_rows, list) or not isinstance(text_rows, list):
        raise SelectiveRepairPlanningError("Smart report is missing decision lists")

    timing_by_cue = {
        int(row["cue_ordinal"]): row
        for row in timing_rows
        if isinstance(row, Mapping) and row.get("cue_ordinal") is not None
    }
    text_by_cue = {
        int(row["cue_ordinal"]): row
        for row in text_rows
        if isinstance(row, Mapping) and row.get("cue_ordinal") is not None
    }
    canonical_by_ordinal = {row.ordinal: row for row in canonical}
    models = _model_index(smart_report)

    jobs: list[dict[str, Any]] = []
    escalation_without_canonical = 0
    for cue in cues:
        timing = timing_by_cue.get(cue.ordinal)
        text = text_by_cue.get(cue.ordinal)
        timing_review = timing is not None and timing.get("action") == "review"
        text_review = text is not None and text.get("action") == "review"
        if not timing_review and not text_review:
            continue

        reasons: list[str] = []
        if timing_review:
            reasons.append("smart_timing_review:" + str(timing.get("reason") or "unknown"))
        if text_review:
            reasons.append("smart_text_review:" + str(text.get("reason") or "unknown"))

        canonical_ordinal = _single_canonical_ordinal(timing)
        if canonical_ordinal is None:
            canonical_ordinal = _single_canonical_ordinal(text)
        occurrence = canonical_by_ordinal.get(canonical_ordinal) if canonical_ordinal is not None else None

        mix_window = _bounded_mix_window(
            cue,
            context_ms=config.mix_context_ms,
            minimum_ms=config.min_mix_window_ms,
        )
        start_ms, end_ms = _cue_times(cue)
        priority = "high" if timing_review else "medium"

        if occurrence is None:
            escalation_without_canonical += 1
            source_ordinal = None
            source_window = None
            language_profile = "auto"
            asr_hint = None
            canonical_sha = None
            capabilities = ["mix_asr", "word_timestamps"]
            occurrence_id = "smart-unmapped"
            source = None
            expected_source_time = None
            rate = None
        else:
            source_ordinal = occurrence.source_ordinal
            source_window = _source_window(
                occurrence,
                before_ms=config.source_context_before_ms,
                after_ms=config.source_context_after_ms,
            )
            language_profile = str(language_by_source.get(source_ordinal, "auto") or "auto")
            asr_hint = asr_language_hint_for_text(
                occurrence.text,
                track_language=language_profile,
            )
            canonical_sha = _text_sha(occurrence.text)
            capabilities = [
                "mix_asr",
                "word_timestamps",
                "source_local_acoustic_match",
                "source_forced_alignment",
            ]
            occurrence_id = f"smart-source-{source_ordinal:03d}"
            source = occurrence.source
            expected_source_time = occurrence.anchor_time_ms
            rate = _safe_rate(models.get(source_ordinal))

        identity = {
            "cue_ordinal": cue.ordinal,
            "canonical_line_index": canonical_ordinal,
            "source_ordinal": source_ordinal,
            "mix_window_ms": mix_window,
            "source_window_ms": source_window,
            "reasons": sorted(reasons),
            "canonical_text_sha256": canonical_sha,
        }
        jobs.append(
            {
                "job_id": _sha(identity),
                "occurrence_id": occurrence_id,
                "track_id": source or occurrence_id,
                "ordinal": source_ordinal if source_ordinal is not None else -1,
                "source_ordinal": source_ordinal,
                "source": source,
                "cue_ordinal": cue.ordinal,
                "canonical_line_index": canonical_ordinal,
                "canonical_text_sha256": canonical_sha,
                "language_profile": language_profile,
                "asr_language_hint": asr_hint or "auto",
                "mix_window_ms": mix_window,
                "source_window_ms": source_window,
                "editor_cue_start_ms": start_ms,
                "editor_cue_end_ms": end_ms,
                "expected_source_time_ms": expected_source_time,
                "rate_prior": rate,
                "requested_capabilities": capabilities,
                "reasons": sorted(reasons),
                "priority": priority,
                "execution_state": "planned_not_executed",
            }
        )

    priority_order = {"high": 0, "medium": 1, "low": 2}
    jobs.sort(
        key=lambda row: (
            priority_order.get(str(row["priority"]), 9),
            int(row["cue_ordinal"]),
            str(row["job_id"]),
        )
    )
    truncated = len(jobs) > config.max_jobs
    jobs = jobs[: config.max_jobs]

    capability_counts: dict[str, int] = {}
    reason_counts: dict[str, int] = {}
    language_hint_counts: dict[str, int] = {}
    planned_mix_ms = 0
    for job in jobs:
        planned_mix_ms += int(job["mix_window_ms"][1]) - int(job["mix_window_ms"][0])
        language = str(job.get("asr_language_hint") or "auto")
        language_hint_counts[language] = language_hint_counts.get(language, 0) + 1
        for capability in job["requested_capabilities"]:
            capability_counts[capability] = capability_counts.get(capability, 0) + 1
        for reason in job["reasons"]:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

    return {
        "schema_version": SELECTIVE_REPAIR_PLAN_SCHEMA_VERSION,
        "policy_id": SELECTIVE_REPAIR_POLICY_ID,
        "mode": "plan_only",
        "product_mode": "pro_selective_audio_repair",
        "backend_execution_performed": False,
        "source": "smart_anchor_timeline_repair_no_audio",
        "canonical_text_authority": "canonical_lyrics_only",
        "editor_timing_authority": "strong_but_rebuttable_prior",
        "config": config.to_dict(),
        "summary": {
            "smart_cue_count": len(cues),
            "job_count": len(jobs),
            "plan_truncated": truncated,
            "planned_mix_audio_ms_unmerged": planned_mix_ms,
            "unmapped_review_count": escalation_without_canonical,
            "reason_counts": dict(sorted(reason_counts.items())),
            "capability_counts": dict(sorted(capability_counts.items())),
            "asr_language_hint_counts": dict(sorted(language_hint_counts.items())),
        },
        "jobs": jobs,
    }


def canonical_text_by_job_id(
    plan: Mapping[str, Any],
    canonical: Sequence[TimedCanonicalOccurrence],
) -> dict[str, str]:
    """Build the private in-memory lookup required by ASR/forced executors."""

    canonical_by_ordinal = {row.ordinal: row for row in canonical}
    output: dict[str, str] = {}
    jobs = plan.get("jobs")
    if not isinstance(jobs, list):
        raise SelectiveRepairPlanningError("plan jobs must be a list")
    for job in jobs:
        if not isinstance(job, Mapping):
            continue
        canonical_ordinal = job.get("canonical_line_index")
        if canonical_ordinal is None:
            continue
        occurrence = canonical_by_ordinal.get(int(canonical_ordinal))
        if occurrence is None:
            raise SelectiveRepairPlanningError("plan references missing canonical occurrence")
        expected = str(job.get("canonical_text_sha256") or "")
        if not expected or expected != _text_sha(occurrence.text):
            raise SelectiveRepairPlanningError("plan/canonical text identity mismatch")
        output[str(job["job_id"])] = occurrence.text
    return output
