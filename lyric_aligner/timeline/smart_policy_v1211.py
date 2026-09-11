"""Current Smart occurrence-identity and final lexical-floor binding.

v1.2.11 keeps v1.2.10 timing authority unchanged.  It hardens exact text
occurrence identity and re-evaluates canonical ownership after all lower Smart
wrappers have produced their final display segmentation.
"""
from __future__ import annotations

from collections import Counter
from typing import Mapping, Sequence

from lyric_aligner.text_repair import (
    MatchDecision,
    TEXT_ANCHOR_POLICY_ID,
    TEXT_FLOOR_POLICY_ID,
    build_trusted_lexical_floor_report,
    parse_srt_text,
)
from lyric_aligner.timeline.anchor_repair import TimedCanonicalOccurrence
from lyric_aligner.text_repair import CanonicalLine as RepairCanonicalLine
from lyric_aligner.timeline.smart_policy_v1210 import smart_repair_srt_text_v1210
from lyric_aligner.timeline.smart_policy_v128 import add_timing_review_product_semantics

SMART_POLICY_ID = "smart-validation-policy-2026-09-10-v1.2.11"


def _decision(row: Mapping[str, object]) -> MatchDecision:
    cue_span = row.get("cue_span")
    canonical_span = row.get("canonical_span")
    return MatchDecision(
        cue_ordinal=int(row["cue_ordinal"]),
        canonical_ordinal=(None if row.get("canonical_ordinal") is None else int(row["canonical_ordinal"])),
        score=float(row.get("score") or 0.0),
        action=str(row.get("action") or "review"),
        reason=str(row.get("reason") or "unknown"),
        cue_span=(tuple(int(v) for v in cue_span) if isinstance(cue_span, (list, tuple)) and len(cue_span) == 2 else None),
        canonical_span=(tuple(int(v) for v in canonical_span) if isinstance(canonical_span, (list, tuple)) and len(canonical_span) == 2 else None),
    )


def _row_cues(row: Mapping[str, object]) -> set[int]:
    span = row.get("cue_span")
    if isinstance(span, (list, tuple)) and len(span) == 2:
        try:
            start, end = int(span[0]), int(span[1])
        except (TypeError, ValueError):
            pass
        else:
            if 0 <= start < end:
                return set(range(start, end))
    return {int(row["cue_ordinal"])}


def _has_valid_canonical_identity(row: Mapping[str, object]) -> bool:
    span = row.get("canonical_span")
    if not isinstance(span, (list, tuple)) or len(span) != 2:
        return False
    try:
        start, end = int(span[0]), int(span[1])
    except (TypeError, ValueError):
        return False
    return 0 <= start < end


def _floor(cues, rows, canonical, *, timeline_mutation_count: int) -> dict[str, object]:
    return build_trusted_lexical_floor_report(
        cues,
        [_decision(row) for row in rows],
        canonical,
        policy_id=TEXT_FLOOR_POLICY_ID,
        timeline_mutation_count=timeline_mutation_count,
    )


def smart_repair_srt_text_v1211(
    source_text: str,
    timed_canonical: Sequence[TimedCanonicalOccurrence],
    repair_canonical: Sequence[RepairCanonicalLine],
    **kwargs,
):
    rendered, base_report = smart_repair_srt_text_v1210(
        source_text, timed_canonical, repair_canonical, **kwargs
    )
    report = dict(base_report)
    _, cues = parse_srt_text(rendered)
    rows = [dict(row) for row in report.get("text_decisions", [])]

    # Restoring an editor/display boundary can discard canonical ownership.
    # Boundary restoration is not evidence that the remaining text belongs to a
    # particular canonical occurrence, so keep those cues in review.
    rows = [
        {
            **row,
            "action": "review",
            "reason": "final_canonical_ownership_unproven",
        }
        if str(row.get("action")) != "review" and not _has_valid_canonical_identity(row)
        else row
        for row in rows
    ]

    initial_floor = _floor(
        cues,
        rows,
        repair_canonical,
        timeline_mutation_count=int(report.get("timing_repair_count", 0) or 0),
    )
    quarantined_cues: set[int] = set()
    if initial_floor["status"] == "failed":
        for diagnostic in initial_floor.get("diagnostics", []):
            span = diagnostic.get("cue_span") if isinstance(diagnostic, Mapping) else None
            if isinstance(span, list) and len(span) == 2:
                quarantined_cues.update(range(int(span[0]), int(span[1])))
        if quarantined_cues:
            rows = [
                {
                    **row,
                    "action": "review",
                    "reason": "final_lexical_floor_failed",
                }
                if _row_cues(row).intersection(quarantined_cues)
                else row
                for row in rows
            ]

    # Recompute from the actual final decision set after quarantine.  If the
    # failing ownership is now isolated, the floor should become review_required
    # rather than retaining a stale failed result from the pre-quarantine state.
    floor = _floor(
        cues,
        rows,
        repair_canonical,
        timeline_mutation_count=int(report.get("timing_repair_count", 0) or 0),
    )
    reviews = [row for row in rows if row.get("action") == "review"]
    mapped_reviews = sum(_has_valid_canonical_identity(row) for row in reviews)
    replacements = sum(row.get("action") == "replace" for row in rows)

    report["text_decisions"] = rows
    report.update(
        policy_id=SMART_POLICY_ID,
        text_anchor_policy_id=TEXT_ANCHOR_POLICY_ID,
        text_lexical_floor=floor,
        text_lexical_floor_status=floor["status"],
        text_trusted_region_lexical_error_count=floor["trusted_region_lexical_error_count"],
        text_trusted_region_word_boundary_error_count=floor["trusted_region_word_boundary_error_count"],
        text_review_count=len(reviews),
        text_mapped_review_count=mapped_reviews,
        text_unmapped_review_count=len(reviews) - mapped_reviews,
        text_review_reason_counts=dict(Counter(str(row.get("reason") or "unknown") for row in reviews)),
        text_replacement_count=replacements,
        text_decision_replacement_count=replacements,
        text_lexical_floor_quarantined_cue_count=len(quarantined_cues),
    )
    if initial_floor["status"] == "failed":
        report["text_lexical_floor_prequarantine"] = initial_floor

    add_timing_review_product_semantics(report)
    if floor["status"] != "complete":
        report.update(
            status="review_required",
            product_status="review_required",
            text_status="review_required",
            manual_review_required=True,
            pro_text_escalation_required=True,
            pro_escalation_required=True,
        )
    return rendered, report


__all__ = ("SMART_POLICY_ID", "smart_repair_srt_text_v1211")
