"""Smart v1.2.6 production reporting and canonical-role hardening.

The alignment engine remains the frozen v1.2.5 implementation.  v1.2.6 runs
after the shared canonical parser has removed conservative CJK/mixed singer
labels and adds product-facing timing semantics which distinguish an actual
timing hypothesis from a cue that simply lacks independent validation.
"""

from __future__ import annotations

from typing import Mapping, MutableMapping, Sequence

from lyric_aligner.text_repair import (
    DEFAULT_AUTO_THRESHOLD,
    CanonicalLine as RepairCanonicalLine,
    parse_srt_text,
    render_repaired_srt,
)
from lyric_aligner.timeline.anchor_repair import TimedCanonicalOccurrence
from lyric_aligner.timeline.final_text_recovery import recover_final_text_reviews
from lyric_aligner.timeline.smart_policy_v125 import (
    _match_decisions,
    _materialization_counts,
    _review_mapping_counts,
    _review_reason_counts,
    _text_payload,
    smart_repair_srt_text_v125,
)

SMART_POLICY_ID = "smart-validation-policy-2026-08-22-v1.2.6"
SMART_TIMING_ACTIONABLE_SHIFT_MS = 900


def add_timing_product_semantics(report: MutableMapping[str, object]) -> None:
    """Add non-breaking product semantics over the legacy review counters."""

    preserved = int(report.get("timing_validated_preserve_count", 0) or 0)
    repaired = int(report.get("timing_repair_count", 0) or 0)
    suspected = int(report.get("timing_review_with_proposal_count", 0) or 0)
    unvalidated = int(report.get("timing_review_without_proposal_count", 0) or 0)
    unresolved = int(report.get("timing_review_count", suspected + unvalidated) or 0)
    if unresolved != suspected + unvalidated:
        raise AssertionError("Smart timing review semantic counts do not reconcile")
    report["timing_validated_count"] = preserved + repaired
    report["timing_suspected_count"] = suspected
    report["timing_unvalidated_count"] = unvalidated
    report["timing_review_count_semantics"] = (
        "legacy_unresolved_total_not_manual_review_queue"
    )
    decisions = report.get("timing_decisions")
    if isinstance(decisions, list):
        actionable = 0
        within_tolerance = 0
        for row in decisions:
            if not isinstance(row, Mapping) or row.get("action") != "review":
                continue
            proposed = row.get("proposed_start_ms")
            old = row.get("old_start_ms")
            if proposed is None or old is None:
                continue
            if abs(int(proposed) - int(old)) >= SMART_TIMING_ACTIONABLE_SHIFT_MS:
                actionable += 1
            else:
                within_tolerance += 1
        if actionable + within_tolerance != suspected:
            raise AssertionError("Smart suspected timing counts do not reconcile")
        report["timing_suspected_actionable_count"] = actionable
        report["timing_suspected_within_display_tolerance_count"] = within_tolerance
        report["manual_timing_review_candidate_count"] = actionable
        report["timing_product_state"] = (
            "suspected_and_unvalidated"
            if suspected and unvalidated
            else "suspected"
            if suspected
            else "unvalidated"
            if unvalidated
            else "validated"
        )


def smart_repair_srt_text_v126(
    source_text: str,
    timed_canonical: Sequence[TimedCanonicalOccurrence],
    repair_canonical: Sequence[RepairCanonicalLine],
    *,
    auto_threshold: float = DEFAULT_AUTO_THRESHOLD,
    rate_prior_by_source: Mapping[int, float] | None = None,
    rate_prior_metadata_by_source: Mapping[int, Mapping[str, object]] | None = None,
    _segmentation_internal_boundary_guard: bool = False,
) -> tuple[str, dict[str, object]]:
    rendered_v125, base_report = smart_repair_srt_text_v125(
        source_text,
        timed_canonical,
        repair_canonical,
        auto_threshold=auto_threshold,
        rate_prior_by_source=rate_prior_by_source,
        rate_prior_metadata_by_source=rate_prior_metadata_by_source,
        _segmentation_internal_boundary_guard=_segmentation_internal_boundary_guard,
    )
    parts, cues = parse_srt_text(rendered_v125)
    text_decisions = _match_decisions(base_report["text_decisions"])
    replacements, text_decisions, recovery = recover_final_text_reviews(
        cues,
        timed_canonical,
        text_decisions,
        allow_cross_script_vocalization=False,
    )
    rendered = (
        render_repaired_srt(parts, cues, replacements)
        if replacements
        else rendered_v125
    )

    report = dict(base_report)
    report["policy_id"] = SMART_POLICY_ID
    report["text_isomorphic_recovery_count"] = recovery.isomorphic_recovery_count
    report["text_suffix_ownership_recovery_count"] = (
        recovery.suffix_ownership_recovery_count
    )
    report["text_decisions"] = _text_payload(text_decisions)
    text_review_count = sum(item.action == "review" for item in text_decisions)
    mapped, unmapped = _review_mapping_counts(text_decisions)
    replacement_count = sum(item.action == "replace" for item in text_decisions)
    materialized, semantic = _materialization_counts(source_text, rendered)
    report["text_replacement_count"] = replacement_count
    report["text_decision_replacement_count"] = replacement_count
    report["text_materialized_change_count"] = materialized
    report["text_semantic_change_count"] = semantic
    report["text_review_count"] = text_review_count
    report["text_mapped_review_count"] = mapped
    report["text_unmapped_review_count"] = unmapped
    report["text_review_reason_counts"] = _review_reason_counts(text_decisions)
    report["text_status"] = "review_required" if text_review_count else "ready"
    report["pro_text_escalation_required"] = bool(text_review_count)
    timing_review_count = int(report.get("timing_review_count", 0) or 0)
    report["pro_escalation_required"] = bool(text_review_count or timing_review_count)
    report["status"] = (
        "review_required" if text_review_count or timing_review_count else "ready"
    )
    add_timing_product_semantics(report)
    manual_timing_count = int(
        report.get("manual_timing_review_candidate_count", 0) or 0
    )
    report["manual_review_required"] = bool(text_review_count or manual_timing_count)
    report["evidence_incomplete"] = bool(report.get("timing_unvalidated_count", 0))
    report["product_status"] = (
        "review_required"
        if report["manual_review_required"]
        else "evidence_incomplete"
        if report["evidence_incomplete"]
        else "ready"
    )
    return rendered, report


__all__ = (
    "SMART_POLICY_ID",
    "SMART_TIMING_ACTIONABLE_SHIFT_MS",
    "add_timing_product_semantics",
    "smart_repair_srt_text_v126",
)
