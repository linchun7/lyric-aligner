"""Smart v1.1 production policy layered on Anchor Timeline Repair v1.

The strict A-anchor affine model remains the only automatic timing-mutation
authority. Smart may additionally use bounded local-bilateral and robust exact-
consensus affine evidence for TEXT recovery only. Those recovery models never
promote their recovered cues into primary timing anchors.
"""

from __future__ import annotations

import math
from dataclasses import asdict, replace
from typing import Mapping, Sequence

from lyric_aligner.text_repair import (
    DEFAULT_AUTO_THRESHOLD,
    CanonicalLine as RepairCanonicalLine,
    SubtitleCue,
    build_repair_plan_v2,
    parse_srt_text,
    render_repaired_srt,
)
from lyric_aligner.timeline.anchor_repair import (
    TimedCanonicalOccurrence,
    TimingDecision,
    _cue_times,
    apply_timing_decisions,
    build_anchor_timing_plan,
)
from lyric_aligner.timeline.text_recovery import recover_text_reviews_from_timing
from lyric_aligner.timeline.text_recovery_consensus import (
    recover_text_reviews_from_consensus,
)

SMART_SCHEMA_VERSION = "smart-1.1"
SMART_POLICY_ID = "smart-validation-policy-2026-08-19-v1.1.4"
_BPM_COMPATIBILITY_TOLERANCE = 0.03

_RECOVERY_REASONS = {
    "timing_model_confirms_canonical_sequence",
    "timing_model_confirms_song_edge_canonical",
    "local_bilateral_span_preserves_editor_segmentation",
    "local_bilateral_timing_confirms_canonical_sequence",
    "exact_consensus_timing_confirms_mapped_canonical",
}


def _creates_new_overlap(
    cues: Sequence[SubtitleCue],
    decision: TimingDecision,
) -> bool:
    """Return True when one proposed repair creates a previously absent overlap."""

    if decision.proposed_start_ms is None or decision.proposed_end_ms is None:
        return False
    index = decision.cue_ordinal
    old_start, old_end = _cue_times(cues[index])
    new_start = int(decision.proposed_start_ms)
    new_end = int(decision.proposed_end_ms)

    if index > 0:
        _, prev_end = _cue_times(cues[index - 1])
        if prev_end <= old_start and prev_end > new_start:
            return True
    if index + 1 < len(cues):
        next_start, _ = _cue_times(cues[index + 1])
        if old_end <= next_start and new_end > next_start:
            return True
    return False


def _harden_timing_decisions(
    cues: Sequence[SubtitleCue],
    decisions: Sequence[TimingDecision],
) -> list[TimingDecision]:
    hardened: list[TimingDecision] = []
    for item in decisions:
        decision = item
        if decision.action == "repair" and _creates_new_overlap(cues, decision):
            decision = replace(
                decision,
                action="review",
                reason="proposed_shift_creates_new_overlap",
                evidence=tuple((*decision.evidence, "no_new_overlap_guard")),
            )
        elif decision.action == "preserve" and decision.reason in {
            "timing_model_not_ready",
            "no_unique_timed_canonical_mapping",
        }:
            decision = replace(
                decision,
                action="review",
                reason=f"unresolved_{decision.reason}",
                evidence=tuple((*decision.evidence, "pro_escalation_required")),
            )
        elif decision.action == "preserve" and decision.anchor_grade == "C":
            decision = replace(
                decision,
                action="review",
                reason="non_A_identity_not_validated",
                evidence=tuple((*decision.evidence, "pro_escalation_required")),
            )
        elif (
            decision.action == "preserve"
            and decision.anchor_grade == "B"
            and decision.model_status == "ready"
            and decision.residual_ms is not None
        ):
            decision = replace(
                decision,
                evidence=tuple((*decision.evidence, "B_confirmed_by_A_model")),
            )
        hardened.append(decision)
    return hardened


def _overlap_ms(left_end: int, right_start: int) -> int:
    return max(0, int(left_end) - int(right_start))


def _harden_combined_timeline(
    cues: Sequence[SubtitleCue],
    decisions: Sequence[TimingDecision],
) -> list[TimingDecision]:
    """Downgrade repairs when the final combined proposal worsens any overlap."""

    by_cue = {item.cue_ordinal: item for item in decisions}
    proposed: list[tuple[int, int]] = []
    original: list[tuple[int, int]] = []
    for cue in cues:
        old = _cue_times(cue)
        original.append(old)
        decision = by_cue.get(cue.ordinal)
        if (
            decision is not None
            and decision.action == "repair"
            and decision.proposed_start_ms is not None
            and decision.proposed_end_ms is not None
        ):
            proposed.append(
                (int(decision.proposed_start_ms), int(decision.proposed_end_ms))
            )
        else:
            proposed.append(old)

    unsafe_repairs: set[int] = set()
    for index in range(len(cues) - 1):
        old_overlap = _overlap_ms(original[index][1], original[index + 1][0])
        new_overlap = _overlap_ms(proposed[index][1], proposed[index + 1][0])
        if new_overlap <= old_overlap:
            continue
        for cue_ordinal in (index, index + 1):
            decision = by_cue.get(cue_ordinal)
            if decision is not None and decision.action == "repair":
                unsafe_repairs.add(cue_ordinal)

    output: list[TimingDecision] = []
    for decision in decisions:
        if decision.cue_ordinal in unsafe_repairs:
            decision = replace(
                decision,
                action="review",
                reason="proposed_shift_increases_overlap",
                evidence=tuple((*decision.evidence, "combined_overlap_guard")),
            )
        output.append(decision)
    return output


def _hard_rate_priors(
    rate_prior_by_source: Mapping[int, float] | None,
    metadata: Mapping[int, Mapping[str, object]] | None,
) -> dict[int, float]:
    """Keep exact/legacy priors hard; treat explicitly BPM-derived priors as soft."""

    values = rate_prior_by_source or {}
    meta = metadata or {}
    output: dict[int, float] = {}
    for source_ordinal, value in values.items():
        provenance = str(meta.get(source_ordinal, {}).get("provenance") or "")
        if provenance == "bpm_derived":
            continue
        output[source_ordinal] = float(value)
    return output


def _bpm_prior_compatibility(
    models,
    metadata: Mapping[int, Mapping[str, object]] | None,
) -> dict[int, tuple[float, bool]]:
    meta = metadata or {}
    result: dict[int, tuple[float, bool]] = {}
    for model in models:
        prior = meta.get(model.source_ordinal)
        if not prior or str(prior.get("provenance") or "") != "bpm_derived":
            continue
        try:
            value = float(prior.get("value"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value) or value <= 0 or not math.isfinite(float(model.rate)):
            continue
        relative = abs(float(model.rate) - value) / value
        result[model.source_ordinal] = (
            relative,
            relative <= _BPM_COMPATIBILITY_TOLERANCE,
        )
    return result


def _harden_bpm_conflicts(
    decisions: Sequence[TimingDecision],
    compatibility: Mapping[int, tuple[float, bool]],
) -> list[TimingDecision]:
    """A soft BPM conflict blocks automatic mutation but does not invalidate preserves."""

    output: list[TimingDecision] = []
    for decision in decisions:
        item = (
            compatibility.get(decision.source_ordinal)
            if decision.source_ordinal is not None
            else None
        )
        if decision.action == "repair" and item is not None and not item[1]:
            decision = replace(
                decision,
                action="review",
                reason="bpm_prior_conflict",
                evidence=tuple((*decision.evidence, "soft_bpm_prior_conflict")),
            )
        output.append(decision)
    return output


def _model_payload(
    models,
    rate_prior_metadata_by_source: Mapping[int, Mapping[str, object]] | None,
    bpm_compatibility: Mapping[int, tuple[float, bool]],
) -> list[dict[str, object]]:
    metadata = rate_prior_metadata_by_source or {}
    rows: list[dict[str, object]] = []
    for model in models:
        row = asdict(model)
        prior = metadata.get(model.source_ordinal)
        prior_provenance = (
            str(prior.get("provenance") or "unknown") if prior else "none"
        )
        row["rate_prior_provenance"] = prior_provenance
        row["rate_prior_value"] = prior.get("value") if prior else None
        if model.rate_source == "robust_anchor_estimate":
            row["rate_provenance"] = "anchor_estimated"
        elif prior_provenance == "exact_daw":
            row["rate_provenance"] = "exact_daw"
        else:
            row["rate_provenance"] = model.rate_source or "none"
        compatibility = bpm_compatibility.get(model.source_ordinal)
        if compatibility is not None:
            row["bpm_prior_relative_error"] = round(float(compatibility[0]), 6)
            row["bpm_prior_compatible"] = bool(compatibility[1])
        else:
            row["bpm_prior_relative_error"] = None
            row["bpm_prior_compatible"] = None
        rows.append(row)
    return rows


def _text_payload(
    text_decisions,
    *,
    for_timing: bool = False,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in text_decisions:
        score = float(item.score)
        if for_timing and item.reason in _RECOVERY_REASONS:
            # Recovered text may be canonical-correct, but it must never become
            # a primary A/B timing anchor merely because text was recovered.
            score = min(score, 0.919)
        rows.append(
            {
                "cue_ordinal": item.cue_ordinal,
                "canonical_ordinal": item.canonical_ordinal,
                "cue_span": list(item.cue_span) if item.cue_span else None,
                "canonical_span": (
                    list(item.canonical_span) if item.canonical_span else None
                ),
                "score": score,
                "action": item.action,
                "reason": item.reason,
            }
        )
    return rows


def _recovery_model_pool(strict_models, consensus_models):
    """Return models usable for TEXT recovery, preferring strict A models."""

    by_source = {item.source_ordinal: item for item in consensus_models}
    for item in strict_models:
        if item.status == "ready":
            by_source[item.source_ordinal] = item
    return [by_source[key] for key in sorted(by_source)]


def smart_repair_srt_text_v11(
    source_text: str,
    timed_canonical: Sequence[TimedCanonicalOccurrence],
    repair_canonical: Sequence[RepairCanonicalLine],
    *,
    auto_threshold: float = DEFAULT_AUTO_THRESHOLD,
    rate_prior_by_source: Mapping[int, float] | None = None,
    rate_prior_metadata_by_source: Mapping[int, Mapping[str, object]] | None = None,
) -> tuple[str, dict[str, object]]:
    """Run Standard text repair followed by Smart v1.1.4 no-audio policy."""

    parts, cues = parse_srt_text(source_text)
    replacements, text_decisions, operations = build_repair_plan_v2(
        cues,
        repair_canonical,
        auto_threshold=auto_threshold,
    )
    text_review_count_before_recovery = sum(
        item.action == "review" for item in text_decisions
    )

    hard_priors = _hard_rate_priors(
        rate_prior_by_source,
        rate_prior_metadata_by_source,
    )

    # This is the only model family that may later authorize timing mutation.
    initial_timing, initial_models = build_anchor_timing_plan(
        cues,
        timed_canonical,
        _text_payload(text_decisions, for_timing=True),
        rate_prior_by_source=hard_priors,
    )
    del initial_timing

    # Text-only v1.1.4 stage. Local two-boundary interpolation and robust exact
    # consensus may recover canonical text without weakening the strict A model.
    (
        consensus_replacements,
        text_decisions,
        consensus_models,
        consensus,
    ) = recover_text_reviews_from_consensus(
        cues,
        timed_canonical,
        text_decisions,
    )
    replacements.update(consensus_replacements)

    # Existing bounded bilateral/edge recovery may consume either a strict A
    # model or, if unavailable, a high-quality exact-consensus TEXT model.
    recovery_models = _recovery_model_pool(initial_models, consensus_models)
    strict_replacements, text_decisions, recovery = recover_text_reviews_from_timing(
        cues,
        timed_canonical,
        text_decisions,
        recovery_models,
    )
    replacements.update(strict_replacements)
    text_repaired = render_repaired_srt(parts, cues, replacements)

    # Recovered cues are score-capped in the timing-only payload, guaranteeing
    # that text recovery cannot promote itself into A/B primary timing evidence.
    raw_timing, models = build_anchor_timing_plan(
        cues,
        timed_canonical,
        _text_payload(text_decisions, for_timing=True),
        rate_prior_by_source=hard_priors,
    )
    bpm_compatibility = _bpm_prior_compatibility(
        models,
        rate_prior_metadata_by_source,
    )
    timing = _harden_timing_decisions(cues, raw_timing)
    timing = _harden_bpm_conflicts(timing, bpm_compatibility)
    timing = _harden_combined_timeline(cues, timing)
    rendered = apply_timing_decisions(text_repaired, timing)

    unresolved_count = sum(item.action == "review" for item in timing)
    text_review_count = sum(item.action == "review" for item in text_decisions)
    consensus_recovery_count = (
        consensus.local_bilateral_cue_count
        + consensus.consensus_timing_cue_count
    )
    total_recovery_count = consensus_recovery_count + recovery.resolved_cue_count
    total_recovery_blocks = (
        consensus.local_bilateral_block_count + recovery.resolved_block_count
    )

    report: dict[str, object] = {
        "schema_version": SMART_SCHEMA_VERSION,
        "policy_id": SMART_POLICY_ID,
        "mode": "smart_anchor_timeline_repair_no_audio",
        "audio_read": False,
        "cue_count": len(cues),
        "canonical_line_count": len(timed_canonical),
        "word_timed_canonical_count": sum(
            item.has_word_timing for item in timed_canonical
        ),
        "text_replacement_count": sum(
            item.action == "replace" for item in text_decisions
        ),
        "text_review_count_before_timing_recovery": text_review_count_before_recovery,
        "text_timing_recovery_count": total_recovery_count,
        "text_timing_recovery_block_count": total_recovery_blocks,
        "text_local_bilateral_recovery_count": consensus.local_bilateral_cue_count,
        "text_local_bilateral_recovery_block_count": (
            consensus.local_bilateral_block_count
        ),
        "text_local_segmentation_preserve_count": (
            consensus.local_segmentation_preserve_count
        ),
        "text_local_timing_partition_count": consensus.local_timing_partition_count,
        "text_consensus_timing_recovery_count": consensus.consensus_timing_cue_count,
        "text_consensus_model_count": consensus.consensus_model_count,
        "text_strict_timing_recovery_count": recovery.resolved_cue_count,
        "text_edge_timing_recovery_count": recovery.resolved_edge_cue_count,
        "text_edge_timing_recovery_block_count": recovery.resolved_edge_block_count,
        "text_review_count": text_review_count,
        "timing_repair_count": sum(item.action == "repair" for item in timing),
        "timing_review_count": unresolved_count,
        "timing_validated_preserve_count": sum(
            item.action == "preserve" for item in timing
        ),
        "pro_escalation_required": bool(unresolved_count or text_review_count),
        "status": (
            "review_required" if (unresolved_count or text_review_count) else "ready"
        ),
        "models": _model_payload(
            models,
            rate_prior_metadata_by_source,
            bpm_compatibility,
        ),
        "timing_decisions": [asdict(item) for item in timing],
        "text_decisions": _text_payload(text_decisions),
        "alignment_span_count": sum(item.kind == "match" for item in operations),
    }
    return rendered, report
