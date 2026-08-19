"""Reason-aware Smart -> Pro v1.1 planning policy.

The base selective planner remains the privacy-safe identity bridge.  This
policy makes Pro cheaper and more targeted: choose evidence by failure reason,
merge nearby mix windows into decode regions, adapt source windows from timed
canonical structure, and add shadow neighbour-source competitors at song
boundaries without granting them timing mutation authority.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Mapping, Sequence

from lyric_aligner.alignment.selective_repair import (
    SelectiveRepairConfig,
    build_selective_repair_plan,
)
from lyric_aligner.text.language_spans import asr_language_hint_for_text
from lyric_aligner.text_repair import SubtitleCue
from lyric_aligner.timeline.anchor_repair import TimedCanonicalOccurrence

PRO_V11_POLICY_ID = "smart-to-pro-reason-aware-2026-08-19-v1"


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
    return rate if 0.5 <= rate <= 2.0 else None


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


def _adaptive_source_window(
    occurrence: TimedCanonicalOccurrence,
    source_rows: Sequence[TimedCanonicalOccurrence],
) -> list[int]:
    """Use token timing or the next lyric onset before falling back to a fixed window."""

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
    return [left, max(left + 2500, right)]


def _reason_flags(job: Mapping[str, Any]) -> tuple[bool, bool]:
    reasons = [str(value) for value in job.get("reasons") or []]
    timing = any(value.startswith("smart_timing_review:") for value in reasons)
    text = any(value.startswith("smart_text_review:") for value in reasons)
    return timing, text


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
    if not jobs:
        return
    ordered = sorted(
        jobs,
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
        "source_window_ms": _adaptive_source_window(alternative, alternative_rows),
        "editor_cue_start_ms": primary.get("editor_cue_start_ms"),
        "editor_cue_end_ms": primary.get("editor_cue_end_ms"),
        "expected_source_time_ms": alternative.anchor_time_ms,
        "rate_prior": _ready_rate(models, alternative.source_ordinal),
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
    """Build the reason-aware Pro v1.1 plan."""

    language_by_source = language_by_source or {}
    config = config or SelectiveRepairConfig()
    if region_merge_gap_ms < 0:
        raise ValueError("region_merge_gap_ms must be >= 0")

    base = build_selective_repair_plan(
        smart_report=smart_report,
        cues=cues,
        canonical=canonical,
        language_by_source=language_by_source,
        config=config,
    )
    plan = deepcopy(base)
    by_ordinal = {item.ordinal: item for item in canonical}
    by_source = _canonical_by_source(canonical)
    models = _model_index(smart_report)

    primary_jobs: list[dict[str, Any]] = []
    competitors: list[dict[str, Any]] = []
    for job in plan.get("jobs", []):
        if not isinstance(job, dict):
            continue
        timing_review, text_review = _reason_flags(job)
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

        job["source_window_ms"] = _adaptive_source_window(
            occurrence,
            by_source[occurrence.source_ordinal],
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
            )
            if competitor is not None:
                competitors.append(competitor)

    jobs = [*primary_jobs, *competitors]
    _assign_regions(jobs, merge_gap_ms=region_merge_gap_ms)

    regions: dict[str, list[int]] = {}
    for job in jobs:
        region_id = str(job["region_id"])
        region = [int(value) for value in job["region_mix_window_ms"]]
        regions[region_id] = region
    merged_ms = sum(end - start for start, end in regions.values())
    unmerged_ms = sum(
        int(job["mix_window_ms"][1]) - int(job["mix_window_ms"][0])
        for job in primary_jobs
    )
    capability_counts: dict[str, int] = {}
    route_counts: dict[str, int] = {}
    for job in primary_jobs:
        route = str(job.get("evidence_route") or "unknown")
        route_counts[route] = route_counts.get(route, 0) + 1
        for capability in job.get("requested_capabilities") or []:
            capability_counts[capability] = capability_counts.get(capability, 0) + 1

    plan["schema_version"] = "1.1"
    plan["policy_id"] = PRO_V11_POLICY_ID
    plan["jobs"] = jobs
    plan["summary"] = {
        **dict(plan.get("summary") or {}),
        "primary_job_count": len(primary_jobs),
        "boundary_competitor_job_count": len(competitors),
        "region_count": len(regions),
        "planned_mix_audio_ms_unmerged": unmerged_ms,
        "planned_mix_audio_ms_merged": merged_ms,
        "region_merge_saved_ms": max(0, unmerged_ms - merged_ms),
        "evidence_route_counts": dict(sorted(route_counts.items())),
        "capability_counts": dict(sorted(capability_counts.items())),
    }
    return plan
