"""Smart v1.1 production policy layered on Anchor Timeline Repair v1.

The v1 timing model remains the deterministic no-audio engine.  This module
hardens production semantics around it: inability to validate is escalated,
B-grade identities may be confirmed only by an already-ready A-anchor model,
and an automatic shift may not create a new subtitle overlap.
"""

from __future__ import annotations

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

SMART_SCHEMA_VERSION = "smart-1.1"
SMART_POLICY_ID = "smart-validation-policy-2026-08-19-v1"


def _creates_new_overlap(
    cues: Sequence[SubtitleCue],
    decision: TimingDecision,
) -> bool:
    """Return True only when a proposed repair introduces a new overlap.

    Existing editor overlaps are not treated as errors here; Smart is allowed to
    preserve or reduce them.  The safety invariant is narrower: a cue pair that
    did not overlap before Smart may not newly overlap after an automatic shift.
    """

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
            # Preserve the editor timing physically, but do not report it as
            # validated.  Pro must receive this unresolved cue.
            decision = replace(
                decision,
                action="review",
                reason=f"unresolved_{decision.reason}",
                evidence=tuple((*decision.evidence, "pro_escalation_required")),
            )
        elif decision.action == "preserve" and decision.anchor_grade == "C":
            # C-grade identity is not strong enough to claim timing validation.
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
            # B never builds the model.  It may only be secondarily confirmed
            # by the already-ready A-anchor model.
            decision = replace(
                decision,
                evidence=tuple((*decision.evidence, "B_confirmed_by_A_model")),
            )
        hardened.append(decision)
    return hardened


def _model_payload(
    models,
    rate_prior_metadata_by_source: Mapping[int, Mapping[str, object]] | None,
) -> list[dict[str, object]]:
    metadata = rate_prior_metadata_by_source or {}
    rows: list[dict[str, object]] = []
    for model in models:
        row = asdict(model)
        prior = metadata.get(model.source_ordinal)
        if prior is not None:
            row["rate_provenance"] = str(prior.get("provenance") or "unknown")
            row["rate_prior_value"] = prior.get("value")
        elif model.rate_source == "robust_anchor_estimate":
            row["rate_provenance"] = "anchor_estimated"
            row["rate_prior_value"] = None
        else:
            row["rate_provenance"] = "none"
            row["rate_prior_value"] = None
        rows.append(row)
    return rows


def smart_repair_srt_text_v11(
    source_text: str,
    timed_canonical: Sequence[TimedCanonicalOccurrence],
    repair_canonical: Sequence[RepairCanonicalLine],
    *,
    auto_threshold: float = DEFAULT_AUTO_THRESHOLD,
    rate_prior_by_source: Mapping[int, float] | None = None,
    rate_prior_metadata_by_source: Mapping[int, Mapping[str, object]] | None = None,
) -> tuple[str, dict[str, object]]:
    """Run text repair + Smart timing with v1.1 production semantics."""

    parts, cues = parse_srt_text(source_text)
    replacements, text_decisions, operations = build_repair_plan_v2(
        cues,
        repair_canonical,
        auto_threshold=auto_threshold,
    )
    text_repaired = render_repaired_srt(parts, cues, replacements)
    text_payload = [
        {
            "cue_ordinal": item.cue_ordinal,
            "canonical_ordinal": item.canonical_ordinal,
            "cue_span": list(item.cue_span) if item.cue_span else None,
            "canonical_span": list(item.canonical_span) if item.canonical_span else None,
            "score": item.score,
            "action": item.action,
            "reason": item.reason,
        }
        for item in text_decisions
    ]

    raw_timing, models = build_anchor_timing_plan(
        cues,
        timed_canonical,
        text_payload,
        rate_prior_by_source=rate_prior_by_source,
    )
    timing = _harden_timing_decisions(cues, raw_timing)
    rendered = apply_timing_decisions(text_repaired, timing)

    unresolved_count = sum(item.action == "review" for item in timing)
    text_review_count = sum(item.action == "review" for item in text_decisions)
    report: dict[str, object] = {
        "schema_version": SMART_SCHEMA_VERSION,
        "policy_id": SMART_POLICY_ID,
        "mode": "smart_anchor_timeline_repair_no_audio",
        "audio_read": False,
        "cue_count": len(cues),
        "canonical_line_count": len(timed_canonical),
        "word_timed_canonical_count": sum(item.has_word_timing for item in timed_canonical),
        "text_replacement_count": sum(item.action == "replace" for item in text_decisions),
        "text_review_count": text_review_count,
        "timing_repair_count": sum(item.action == "repair" for item in timing),
        "timing_review_count": unresolved_count,
        "timing_validated_preserve_count": sum(item.action == "preserve" for item in timing),
        "pro_escalation_required": bool(unresolved_count or text_review_count),
        "status": "review_required" if (unresolved_count or text_review_count) else "ready",
        "models": _model_payload(models, rate_prior_metadata_by_source),
        "timing_decisions": [asdict(item) for item in timing],
        "text_decisions": text_payload,
        "alignment_span_count": sum(item.kind == "match" for item in operations),
    }
    return rendered, report
