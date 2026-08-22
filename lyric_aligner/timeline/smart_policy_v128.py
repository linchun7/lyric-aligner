"""Smart v1.2.8 production-report safety wrapper.

The v1.2.7 text/timing decisions remain unchanged.  This wrapper restores the
product distinction between every actionable timing suspicion and the smaller
high-value Pro evidence subset, preventing a false ``ready`` product state.
"""

from __future__ import annotations

from typing import Mapping, MutableMapping, Sequence

from lyric_aligner.text_repair import (
    DEFAULT_AUTO_THRESHOLD,
    CanonicalLine as RepairCanonicalLine,
)
from lyric_aligner.timeline.anchor_repair import TimedCanonicalOccurrence
from lyric_aligner.timeline.smart_policy_v127 import (
    add_timing_confidence_semantics,
    smart_repair_srt_text_v127,
)

SMART_POLICY_ID = "smart-validation-policy-2026-08-22-v1.2.8"


def add_timing_review_product_semantics(report: MutableMapping[str, object]) -> None:
    """Keep actionable manual review separate from Pro evidence priority."""

    add_timing_confidence_semantics(report)
    actionable = int(report.get("timing_actionable_strong_model_count", 0) or 0) + int(
        report.get("timing_actionable_weak_or_unknown_model_count", 0) or 0
    )
    declared_actionable = int(report.get("timing_suspected_actionable_count", actionable) or 0)
    if actionable != declared_actionable:
        raise AssertionError("Smart actionable timing counts do not reconcile")
    high_value = int(report.get("timing_high_value_pro_candidate_count", 0) or 0)
    if high_value > actionable:
        raise AssertionError("Smart high-value timing subset exceeds actionable queue")
    text_review_count = int(report.get("text_review_count", 0) or 0)
    report["manual_timing_review_candidate_count"] = actionable
    report["manual_timing_review_candidate_semantics"] = (
        "all_explicit_actionable_timing_suspicions"
    )
    report["timing_high_value_pro_candidate_semantics"] = (
        "budget_priority_subset_of_actionable_timing_suspicions"
    )
    report["manual_review_required"] = bool(text_review_count or actionable)
    report["evidence_incomplete"] = bool(report.get("timing_unvalidated_count", 0))
    report["product_status"] = (
        "review_required"
        if report["manual_review_required"]
        else "evidence_incomplete"
        if report["evidence_incomplete"]
        else "ready"
    )


def smart_repair_srt_text_v128(
    source_text: str,
    timed_canonical: Sequence[TimedCanonicalOccurrence],
    repair_canonical: Sequence[RepairCanonicalLine],
    *,
    auto_threshold: float = DEFAULT_AUTO_THRESHOLD,
    rate_prior_by_source: Mapping[int, float] | None = None,
    rate_prior_metadata_by_source: Mapping[int, Mapping[str, object]] | None = None,
    _segmentation_internal_boundary_guard: bool = False,
) -> tuple[str, dict[str, object]]:
    rendered, base_report = smart_repair_srt_text_v127(
        source_text,
        timed_canonical,
        repair_canonical,
        auto_threshold=auto_threshold,
        rate_prior_by_source=rate_prior_by_source,
        rate_prior_metadata_by_source=rate_prior_metadata_by_source,
        _segmentation_internal_boundary_guard=_segmentation_internal_boundary_guard,
    )
    report = dict(base_report)
    report["policy_id"] = SMART_POLICY_ID
    add_timing_review_product_semantics(report)
    return rendered, report


__all__ = (
    "SMART_POLICY_ID",
    "add_timing_review_product_semantics",
    "smart_repair_srt_text_v128",
)
