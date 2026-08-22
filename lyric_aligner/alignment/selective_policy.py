"""Reason-aware Smart -> Pro v1.2 planning policy.

The base selective planner remains the privacy-safe identity bridge. This
policy keeps Pro cheap and targeted while hardening v1.1 production behavior:
validate the exact Smart policy, tolerate open-ended Enhanced LRC tokens,
ensure acoustic source windows are long enough for the planned slope search,
and merge only jobs that actually request acoustic evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from dataclasses import replace
from typing import Any, Mapping, Sequence

from lyric_aligner.alignment.local_acoustic_match import LocalAcousticMatchConfig
from lyric_aligner.alignment.selective_repair import (
    SelectiveRepairConfig,
    SelectiveRepairPlanningError,
    build_selective_repair_plan,
)
from lyric_aligner.text.language_spans import asr_language_hint_for_text
from lyric_aligner.text_repair import SubtitleCue
from lyric_aligner.timeline.anchor_repair import TimedCanonicalOccurrence
from lyric_aligner.timeline.smart_current import (
    SMART_POLICY_ID,
    SMART_SCHEMA_VERSION,
    SMART_TIMING_ACTIONABLE_SHIFT_MS,
)

PRO_POLICY_ID = "smart-to-pro-reason-aware-2026-08-22-v1.2.5"
# Backward-compatible import name retained for existing plan consumers.
PRO_V11_POLICY_ID = PRO_POLICY_ID


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


def _ready_rate(models: Mapping[int, Mapping[str, Any]], source_ordinal: int) -> float | None:
    row = models.get(source_ordinal)
    if row is None or str(row.get("status") or "") != "ready":
        return None
    try:
        rate = float(row.get("rate"))
    except (TypeError, ValueError):
        return None
    return rate if math.isfinite(rate) and 0.5 <= rate <= 2.0 else None


def _model_index(smart_report: Mapping[str, Any]) -> dict[int, Mapping[str, Any]]:
    output: dict[int, Mapping[str, Any]] = {}
    rows = smart_report.get("models")
    if not isinstance(rows, list):
        return output
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        try:
            output[int(row["source_ordinal"])] = row
        except (KeyError, TypeError, ValueError):
            continue
    return output


def _canonical_by_source(
    canonical: Sequence[TimedCanonicalOccurrence],
) -> dict[int, list[TimedCanonicalOccurrence]]:
    output: dict[int, list[TimedCanonicalOccurrence]] = {}
    for item in canonical:
        output.setdefault(item.source_ordinal, []).append(item)
    return output


def _safe_canonical_for_base_planner(
    canonical: Sequence[TimedCanonicalOccurrence],
) -> list[TimedCanonicalOccurrence]:
    """Normalize open-ended Enhanced-LRC tokens for the legacy v1 window helper.

    Enhanced LRC legitimately leaves the final token end unknown. The v1.1
    adaptive window uses the real token structure later; this compatibility
    view only prevents the base planner from attempting max(..., None).
    """

    safe: list[TimedCanonicalOccurrence] = []
    for occurrence in canonical:
        if not occurrence.tokens or all(token.end_ms is not None for token in occurrence.tokens):
            safe.append(occurrence)
            continue
        tokens = tuple(
            token if token.end_ms is not None else replace(token, end_ms=token.start_ms)
            for token in occurrence.tokens
        )
        safe.append(replace(occurrence, tokens=tokens))
    return safe


def _minimum_acoustic_source_span_ms(
    mix_window_ms: Sequence[int],
    *,
    rate_prior: float | None,
    acoustic_config: LocalAcousticMatchConfig,
) -> int:
    mix_duration_ms = max(1, int(mix_window_ms[1]) - int(mix_window_ms[0]))
    if rate_prior is None:
        max_slope = acoustic_config.no_prior_max_slope
    else:
        max_slope = min(2.2, rate_prior + acoustic_config.slope_radius)
    return int(math.ceil(mix_duration_ms * max_slope)) + 750


def _adaptive_source_window(
    occurrence: TimedCanonicalOccurrence,
    source_rows: Sequence[TimedCanonicalOccurrence],
    *,
    mix_window_ms: Sequence[int],
    rate_prior: float | None,
    acoustic_config: LocalAcousticMatchConfig,
) -> list[int]:
    """Use lyric timing while guaranteeing enough span for local retrieval."""

    left = max(0, occurrence.anchor_time_ms - 2500)
    position = next(
        (index for index, item in enumerate(source_rows) if item.ordinal == occurrence.ordinal),
        None,
    )
    next_onset = None
    if position is not None and position + 1 < len(source_rows):
        next_onset = source_rows[position + 1].anchor_time_ms

    if occurrence.tokens:
        token_ends = [
            token.end_ms if token.end_ms is not None else token.start_ms
            for token in occurrence.tokens
        ]
        right = max(occurrence.anchor_time_ms + 3000, max(token_ends) + 1500)
    elif next_onset is not None and next_onset > occurrence.anchor_time_ms:
        lyric_span = next_onset - occurrence.anchor_time_ms
        right = occurrence.anchor_time_ms + min(max(lyric_span + 750, 4500), 15000)
    else:
        right = occurrence.anchor_time_ms + 6000

    right = max(left + 2500, right)
    minimum = _minimum_acoustic_source_span_ms(
        mix_window_ms,
        rate_prior=rate_prior,
        acoustic_config=acoustic_config,
    )
    current = right - left
    if current < minimum:
        deficit = minimum - current
        grow_left = min(left, deficit // 2)
        left -= grow_left
        right += deficit - grow_left
    return [left, right]


def _reason_flags(job: Mapping[str, Any]) -> tuple[bool, bool]:
    reasons = [str(value) for value in job.get("reasons") or []]
    timing = any(value.startswith("smart_timing_review:") for value in reasons)
    text = any(value.startswith("smart_text_review:") for value in reasons)
    return timing, text


def _timing_review_index(smart_report: Mapping[str, Any]) -> dict[int, Mapping[str, Any]]:
    rows = smart_report.get("timing_decisions")
    if not isinstance(rows, list):
        return {}
    output: dict[int, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or row.get("cue_ordinal") is None:
            continue
        try:
            output[int(row["cue_ordinal"])] = row
        except (TypeError, ValueError):
            continue
    return output


def _high_value_timing_cues(smart_report: Mapping[str, Any]) -> set[int]:
    """Return Smart's budget-priority subset without changing manual-review scope."""

    rows = smart_report.get("timing_high_value_pro_candidate_positions")
    if not isinstance(rows, list):
        return set()
    output: set[int] = set()
    for row in rows:
        if not isinstance(row, Mapping) or row.get("cue_ordinal") is None:
            continue
        try:
            output.add(int(row["cue_ordinal"]))
        except (TypeError, ValueError):
            continue
    return output


def _has_concrete_timing_proposal(row: Mapping[str, Any] | None) -> bool:
    return bool(
        row is not None
        and row.get("action") == "review"
        and (
            row.get("proposed_start_ms") is not None
            or row.get("proposed_end_ms") is not None
        )
    )


def _selection_tier(
    *,
    timing_review: bool,
    text_review: bool,
    timing_has_proposal: bool,
    timing_proposal_abs_shift_ms: int,
) -> tuple[int, str]:
    actionable = (
        timing_has_proposal
        and timing_proposal_abs_shift_ms >= SMART_TIMING_ACTIONABLE_SHIFT_MS
    )
    if text_review and actionable:
        return 0, "text_review_with_actionable_timing_suspicion"
    if timing_review and actionable:
        return 1, "actionable_timing_suspicion"
    if text_review:
        return 2, "text_review"
    if timing_review and timing_has_proposal:
        return 3, "timing_suspicion_within_display_tolerance"
    return 4, "timing_unvalidated"


def _timing_proposal_abs_shift_ms(
    row: Mapping[str, Any] | None,
    *,
    editor_start_ms: Any,
) -> int:
    """Return a stable value signal for concrete Smart start proposals."""

    if not _has_concrete_timing_proposal(row):
        return 0
    proposed = row.get("proposed_start_ms") if row is not None else None
    old = row.get("old_start_ms") if row is not None else None
    if old is None:
        old = editor_start_ms
    if proposed is None or old is None:
        return 0
    try:
        return abs(int(proposed) - int(old))
    except (TypeError, ValueError):
        return 0


def _strong_timing_model(row: Mapping[str, Any] | None) -> bool:
    if row is None or row.get("status") != "ready":
        return False
    try:
        median = row.get("median_abs_residual_ms")
        return (
            int(row.get("inlier_count", 0) or 0) >= 6
            and float(row.get("inlier_fraction", 0.0) or 0.0) >= 0.80
            and float(median if median is not None else 10_000.0) <= 250.0
        )
    except (TypeError, ValueError):
        return False


def _needs_forced_alignment(
    job: Mapping[str, Any],
    occurrence: TimedCanonicalOccurrence,
    *,
    timing_review: bool,
    text_review: bool,
) -> bool:
    if occurrence.has_word_timing:
        return False
    reasons = " ".join(str(value) for value in job.get("reasons") or [])
    identity_doubt = any(
        token in reasons
        for token in (
            "non_A_identity",
            "no_unique_timed_canonical_mapping",
            "smart_text_review",
        )
    )
    return text_review or (timing_review and identity_doubt)


def _assign_regions(jobs: list[dict[str, Any]], *, merge_gap_ms: int) -> None:
    """Merge only acoustic jobs; ASR-only jobs must not widen acoustic decode."""

    acoustic = [
        job
        for job in jobs
        if "source_local_acoustic_match" in (job.get("requested_capabilities") or [])
    ]
    non_acoustic = [job for job in jobs if job not in acoustic]

    for job in non_acoustic:
        window = [int(value) for value in job["mix_window_ms"]]
        job["region_id"] = f"pro-local-{str(job['job_id'])[:12]}"
        job["region_mix_window_ms"] = window

    if not acoustic:
        return
    ordered = sorted(
        acoustic,
        key=lambda row: (
            int(row["mix_window_ms"][0]),
            int(row["mix_window_ms"][1]),
            str(row["job_id"]),
        ),
    )
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_end = -1
    for job in ordered:
        start, end = [int(value) for value in job["mix_window_ms"]]
        if not current or start > current_end + merge_gap_ms:
            if current:
                groups.append(current)
            current = [job]
            current_end = end
        else:
            current.append(job)
            current_end = max(current_end, end)
    if current:
        groups.append(current)

    for index, group in enumerate(groups):
        region_start = min(int(row["mix_window_ms"][0]) for row in group)
        region_end = max(int(row["mix_window_ms"][1]) for row in group)
        region_id = f"pro-region-{index:04d}-{_sha([region_start, region_end])[:10]}"
        for job in group:
            job["region_id"] = region_id
            job["region_mix_window_ms"] = [region_start, region_end]


def _boundary_competitor(
    *,
    primary: Mapping[str, Any],
    occurrence: TimedCanonicalOccurrence,
    by_source: Mapping[int, Sequence[TimedCanonicalOccurrence]],
    models: Mapping[int, Mapping[str, Any]],
    language_by_source: Mapping[int, str],
    acoustic_config: LocalAcousticMatchConfig,
) -> dict[str, Any] | None:
    source_rows = list(by_source.get(occurrence.source_ordinal, ()))
    if not source_rows:
        return None
    position = next(
        (index for index, item in enumerate(source_rows) if item.ordinal == occurrence.ordinal),
        None,
    )
    if position is None:
        return None

    alternative: TimedCanonicalOccurrence | None = None
    role = ""
    if position <= 1 and occurrence.source_ordinal - 1 in by_source:
        previous = list(by_source[occurrence.source_ordinal - 1])
        if previous:
            alternative = previous[-1]
            role = "previous_source"
    elif position >= max(0, len(source_rows) - 2) and occurrence.source_ordinal + 1 in by_source:
        following = list(by_source[occurrence.source_ordinal + 1])
        if following:
            alternative = following[0]
            role = "next_source"
    if alternative is None:
        return None

    alternative_rows = list(by_source[alternative.source_ordinal])
    language_profile = str(
        language_by_source.get(alternative.source_ordinal, "auto") or "auto"
    )
    rate = _ready_rate(models, alternative.source_ordinal)
    identity = {
        "boundary_competitor_for": primary["job_id"],
        "source_ordinal": alternative.source_ordinal,
        "canonical_line_index": alternative.ordinal,
        "role": role,
    }
    return {
        "job_id": _sha(identity),
        "occurrence_id": f"smart-source-{alternative.source_ordinal:03d}",
        "track_id": alternative.source,
        "ordinal": alternative.source_ordinal,
        "source_ordinal": alternative.source_ordinal,
        "source": alternative.source,
        "cue_ordinal": primary.get("cue_ordinal"),
        "canonical_line_index": alternative.ordinal,
        "canonical_text_sha256": _text_sha(alternative.text),
        "language_profile": language_profile,
        "asr_language_hint": asr_language_hint_for_text(
            alternative.text,
            track_language=language_profile,
        )
        or "auto",
        "mix_window_ms": list(primary["mix_window_ms"]),
        "source_window_ms": _adaptive_source_window(
            alternative,
            alternative_rows,
            mix_window_ms=primary["mix_window_ms"],
            rate_prior=rate,
            acoustic_config=acoustic_config,
        ),
        "editor_cue_start_ms": primary.get("editor_cue_start_ms"),
        "editor_cue_end_ms": primary.get("editor_cue_end_ms"),
        "expected_source_time_ms": alternative.anchor_time_ms,
        "rate_prior": rate,
        "requested_capabilities": ["source_local_acoustic_match"],
        "reasons": ["song_boundary_dual_source_competitor"],
        "priority": "high",
        "execution_state": "planned_not_executed",
        "shadow_evidence_only": True,
        "boundary_competitor_for_job_id": primary["job_id"],
        "boundary_role": role,
    }


def build_selective_repair_plan_v11(
    *,
    smart_report: Mapping[str, Any],
    cues: Sequence[SubtitleCue],
    canonical: Sequence[TimedCanonicalOccurrence],
    language_by_source: Mapping[int, str] | None = None,
    config: SelectiveRepairConfig | None = None,
    region_merge_gap_ms: int = 750,
) -> dict[str, Any]:
    """Build the reason-aware Pro v1.2 plan from the current Smart policy only."""

    language_by_source = language_by_source or {}
    config = config or SelectiveRepairConfig()
    config.validate()
    acoustic_config = LocalAcousticMatchConfig()
    acoustic_config.validate()
    if region_merge_gap_ms < 0:
        raise ValueError("region_merge_gap_ms must be >= 0")
    if smart_report.get("schema_version") != SMART_SCHEMA_VERSION:
        raise SelectiveRepairPlanningError(
            f"Pro v1.2 requires Smart schema {SMART_SCHEMA_VERSION}; rerun Smart"
        )
    if smart_report.get("policy_id") != SMART_POLICY_ID:
        raise SelectiveRepairPlanningError(
            "Pro v1.2 requires the current Smart production policy; rerun Smart"
        )

    planning_config = replace(config, max_jobs=max(config.max_jobs, len(cues)))
    base = build_selective_repair_plan(
        smart_report=smart_report,
        cues=cues,
        canonical=_safe_canonical_for_base_planner(canonical),
        language_by_source=language_by_source,
        config=planning_config,
    )
    plan = deepcopy(base)
    # The base planner sees an expanded budget only to collect the complete
    # unresolved pool. The public plan config must report the caller-requested
    # primary budget that is actually applied below.
    plan["config"] = config.to_dict()
    by_ordinal = {item.ordinal: item for item in canonical}
    by_source = _canonical_by_source(canonical)
    models = _model_index(smart_report)
    timing_by_cue = _timing_review_index(smart_report)
    high_value_timing_cues = _high_value_timing_cues(smart_report)

    primary_jobs: list[dict[str, Any]] = []
    candidate_competitors: list[dict[str, Any]] = []
    for job in plan.get("jobs", []):
        if not isinstance(job, dict):
            continue
        timing_review, text_review = _reason_flags(job)
        cue_ordinal = int(job["cue_ordinal"])
        timing_row = timing_by_cue.get(cue_ordinal)
        timing_has_proposal = _has_concrete_timing_proposal(timing_row)
        proposal_abs_shift_ms = _timing_proposal_abs_shift_ms(
            timing_row,
            editor_start_ms=job.get("editor_cue_start_ms"),
        )
        selection_rank, selection_tier = _selection_tier(
            timing_review=timing_review,
            text_review=text_review,
            timing_has_proposal=timing_has_proposal,
            timing_proposal_abs_shift_ms=proposal_abs_shift_ms,
        )
        job["selection_tier"] = selection_tier
        job["smart_timing_high_value_pro_candidate"] = (
            cue_ordinal in high_value_timing_cues
        )
        job["timing_has_concrete_proposal"] = timing_has_proposal
        job["timing_proposal_abs_shift_ms"] = proposal_abs_shift_ms
        source_raw = job.get("source_ordinal")
        try:
            model_row = models.get(int(source_raw)) if source_raw is not None else None
        except (TypeError, ValueError):
            model_row = None
        strong_model = _strong_timing_model(model_row)
        job["timing_model_evidence_tier"] = (
            "strong" if strong_model else "weak_or_unknown"
        )
        job["priority"] = (
            "high" if selection_rank <= 1 else "medium" if selection_rank == 2 else "low"
        )
        job["_selection_rank"] = selection_rank
        job["_selection_high_value_rank"] = (
            0 if cue_ordinal in high_value_timing_cues else 1
        )
        job["_selection_model_rank"] = 0 if strong_model else 1
        canonical_ordinal = job.get("canonical_line_index")
        occurrence = (
            by_ordinal.get(int(canonical_ordinal))
            if canonical_ordinal is not None
            else None
        )
        if occurrence is None:
            job["requested_capabilities"] = ["mix_asr", "word_timestamps"]
            job["evidence_route"] = "unmapped_asr_only"
            primary_jobs.append(job)
            continue

        rate = _ready_rate(models, occurrence.source_ordinal)
        job["rate_prior"] = rate
        job["source_window_ms"] = _adaptive_source_window(
            occurrence,
            by_source[occurrence.source_ordinal],
            mix_window_ms=job["mix_window_ms"],
            rate_prior=rate,
            acoustic_config=acoustic_config,
        )
        capabilities: list[str] = []
        if timing_review:
            capabilities.append("source_local_acoustic_match")
        if text_review:
            capabilities.extend(("mix_asr", "word_timestamps"))
        if _needs_forced_alignment(
            job,
            occurrence,
            timing_review=timing_review,
            text_review=text_review,
        ):
            capabilities.append("source_forced_alignment")
        if not capabilities:
            capabilities = ["source_local_acoustic_match"]
        job["requested_capabilities"] = list(dict.fromkeys(capabilities))
        job["evidence_route"] = (
            "timing_plus_text"
            if timing_review and text_review
            else "timing_acoustic_first"
            if timing_review
            else "text_asr_first"
        )
        primary_jobs.append(job)

        if timing_review and "source_local_acoustic_match" in capabilities:
            competitor = _boundary_competitor(
                primary=job,
                occurrence=occurrence,
                by_source=by_source,
                models=models,
                language_by_source=language_by_source,
                acoustic_config=acoustic_config,
            )
            if competitor is not None:
                candidate_competitors.append(competitor)

    primary_candidate_count = len(primary_jobs)
    primary_jobs.sort(
        key=lambda row: (
            int(row.get("_selection_high_value_rank", 1)),
            int(row.get("_selection_rank", 9)),
            int(row.get("_selection_model_rank", 1)),
            -int(row.get("timing_proposal_abs_shift_ms", 0)),
            int(row["cue_ordinal"]),
            str(row["job_id"]),
        )
    )
    primary_truncated = primary_candidate_count > config.max_jobs
    primary_jobs = primary_jobs[: config.max_jobs]
    selected_primary_ids = {str(job["job_id"]) for job in primary_jobs}
    for job in primary_jobs:
        job.pop("_selection_rank", None)
        job.pop("_selection_high_value_rank", None)
        job.pop("_selection_model_rank", None)

    eligible_competitors = [
        job
        for job in candidate_competitors
        if str(job.get("boundary_competitor_for_job_id") or "") in selected_primary_ids
    ]
    competitors = eligible_competitors
    omitted_competitors = 0
    jobs = [*primary_jobs, *competitors]
    _assign_regions(jobs, merge_gap_ms=region_merge_gap_ms)

    all_regions: dict[str, list[int]] = {}
    acoustic_regions: dict[str, list[int]] = {}
    for job in jobs:
        region_id = str(job["region_id"])
        region = [int(value) for value in job["region_mix_window_ms"]]
        all_regions[region_id] = region
        if "source_local_acoustic_match" in (job.get("requested_capabilities") or []):
            acoustic_regions[region_id] = region

    unmerged_ms = sum(
        int(job["mix_window_ms"][1]) - int(job["mix_window_ms"][0])
        for job in primary_jobs
    )
    acoustic_unmerged_ms = sum(
        int(job["mix_window_ms"][1]) - int(job["mix_window_ms"][0])
        for job in jobs
        if "source_local_acoustic_match" in (job.get("requested_capabilities") or [])
    )
    acoustic_merged_ms = sum(end - start for start, end in acoustic_regions.values())
    non_acoustic_primary_ms = sum(
        int(job["mix_window_ms"][1]) - int(job["mix_window_ms"][0])
        for job in primary_jobs
        if "source_local_acoustic_match" not in (job.get("requested_capabilities") or [])
    )
    effective_merged_ms = acoustic_merged_ms + non_acoustic_primary_ms

    capability_counts: dict[str, int] = {}
    route_counts: dict[str, int] = {}
    reason_counts: dict[str, int] = {}
    language_hint_counts: dict[str, int] = {}
    selection_tier_counts: dict[str, int] = {}
    for job in primary_jobs:
        route = str(job.get("evidence_route") or "unknown")
        route_counts[route] = route_counts.get(route, 0) + 1
        tier = str(job.get("selection_tier") or "unknown")
        selection_tier_counts[tier] = selection_tier_counts.get(tier, 0) + 1
        language = str(job.get("asr_language_hint") or "auto")
        language_hint_counts[language] = language_hint_counts.get(language, 0) + 1
        for capability in job.get("requested_capabilities") or []:
            capability_counts[capability] = capability_counts.get(capability, 0) + 1
        for reason in job.get("reasons") or []:
            value = str(reason)
            reason_counts[value] = reason_counts.get(value, 0) + 1

    inherited_summary = dict(plan.get("summary") or {})
    plan["schema_version"] = "1.1"
    plan["policy_id"] = PRO_POLICY_ID
    plan["jobs"] = jobs
    plan["summary"] = {
        **inherited_summary,
        "job_count": len(jobs),
        "primary_job_count": len(primary_jobs),
        "primary_candidate_job_count": primary_candidate_count,
        "primary_deferred_due_to_max_jobs": max(0, primary_candidate_count - len(primary_jobs)),
        "boundary_competitor_job_count": len(competitors),
        "boundary_competitor_omitted_due_to_max_jobs": omitted_competitors,
        "max_jobs_applies_to": "primary_jobs_only_shadow_competitors_additive",
        "selection_policy": (
            "smart_high_value_then_actionable_text_model_shift_then_display_tolerance_then_unvalidated"
        ),
        "smart_high_value_candidate_count": len(high_value_timing_cues),
        "smart_high_value_selected_count": sum(
            bool(job.get("smart_timing_high_value_pro_candidate"))
            for job in primary_jobs
        ),
        "selection_tier_counts": dict(sorted(selection_tier_counts.items())),
        "plan_truncated": bool(primary_truncated),
        "reason_counts": dict(sorted(reason_counts.items())),
        "asr_language_hint_counts": dict(sorted(language_hint_counts.items())),
        "region_count": len(all_regions),
        "acoustic_region_count": len(acoustic_regions),
        "planned_mix_audio_ms_unmerged": unmerged_ms,
        "planned_mix_audio_ms_merged": effective_merged_ms,
        "region_merge_saved_ms": max(0, unmerged_ms - effective_merged_ms),
        "planned_acoustic_mix_audio_ms_unmerged": acoustic_unmerged_ms,
        "planned_acoustic_mix_audio_ms_merged": acoustic_merged_ms,
        "acoustic_region_merge_saved_ms": max(0, acoustic_unmerged_ms - acoustic_merged_ms),
        "evidence_route_counts": dict(sorted(route_counts.items())),
        "capability_counts": dict(sorted(capability_counts.items())),
    }
    return plan
