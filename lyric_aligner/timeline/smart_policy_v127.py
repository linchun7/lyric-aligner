"""Smart v1.2.7 cross-script vocalization text recovery.

The frozen v1.2.6 result remains the timing authority.  This wrapper performs
one additional text-only pass after timing is final: a mapped one-cue canonical
vocalization may cross writing systems only when the immediately preceding
resolved canonical occurrence proves exact same-source sequence ownership.
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
)
from lyric_aligner.timeline.smart_policy_v126 import (
    SMART_TIMING_ACTIONABLE_SHIFT_MS,
    smart_repair_srt_text_v126,
)

SMART_POLICY_ID = "smart-validation-policy-2026-08-22-v1.2.7"


def _strong_local_model(row: Mapping[str, object] | None) -> bool:
    if row is None or row.get("status") != "ready":
        return False
    try:
        return (
            int(row.get("inlier_count", 0) or 0) >= 6
            and float(row.get("inlier_fraction", 0.0) or 0.0) >= 0.80
            and float(
                row.get("median_abs_residual_ms")
                if row.get("median_abs_residual_ms") is not None
                else 10_000.0
            ) <= 250.0
        )
    except (TypeError, ValueError):
        return False


def add_timing_confidence_semantics(report: MutableMapping[str, object]) -> None:
    """Rank Smart hypotheses without calling no-audio evidence vocal onset."""

    models = report.get("models")
    model_by_source = {
        int(row["source_ordinal"]): row
        for row in models
        if isinstance(models, list)
        and isinstance(row, Mapping)
        and row.get("source_ordinal") is not None
    } if isinstance(models, list) else {}
    text_rows = report.get("text_decisions")
    text_by_cue = {
        int(row["cue_ordinal"]): row
        for row in text_rows
        if isinstance(text_rows, list)
        and isinstance(row, Mapping)
        and row.get("cue_ordinal") is not None
    } if isinstance(text_rows, list) else {}
    timing_rows = report.get("timing_decisions")
    strong = weak = text_unresolved = text_resolved = text_identity_special = high_value = 0
    positions: list[dict[str, object]] = []
    if isinstance(timing_rows, list):
        for row in timing_rows:
            if not isinstance(row, Mapping) or row.get("action") != "review":
                continue
            old = row.get("old_start_ms")
            proposed = row.get("proposed_start_ms")
            if old is None or proposed is None:
                continue
            try:
                shift = abs(int(proposed) - int(old))
            except (TypeError, ValueError):
                continue
            if shift < SMART_TIMING_ACTIONABLE_SHIFT_MS:
                continue
            cue = int(row.get("cue_ordinal", -1))
            source = row.get("source_ordinal")
            model = None
            try:
                model = model_by_source.get(int(source)) if source is not None else None
            except (TypeError, ValueError):
                model = None
            model_strong = _strong_local_model(model)
            if model_strong:
                strong += 1
            else:
                weak += 1
            text_row = text_by_cue.get(cue, {})
            text_review = text_row.get("action") == "review"
            cross_script_recovered = (
                text_row.get("reason")
                == "preceding_canonical_anchor_confirms_cross_script_vocalization"
            )
            if text_review:
                text_unresolved += 1
            else:
                text_resolved += 1
            if text_review or cross_script_recovered:
                text_identity_special += 1
            if model_strong and (text_review or cross_script_recovered):
                high_value += 1
                positions.append(
                    {
                        "cue_ordinal": cue,
                        "editor_cue_start_ms": int(old),
                        "smart_shift_abs_ms": shift,
                    }
                )

    report["timing_actionable_strong_model_count"] = strong
    report["timing_actionable_weak_or_unknown_model_count"] = weak
    report["timing_actionable_text_unresolved_count"] = text_unresolved
    report["timing_actionable_resolved_text_count"] = text_resolved
    report["timing_actionable_text_identity_special_count"] = text_identity_special
    report["timing_high_value_pro_candidate_count"] = high_value
    report["timing_high_value_pro_candidate_positions"] = positions
    report["pro_timing_evidence_candidate_count"] = strong + weak
    report["manual_timing_review_candidate_count"] = high_value
    report["timing_confidence_semantics"] = (
        "smart_no_audio_hypothesis_priority_not_vocal_onset_probability"
    )


def smart_repair_srt_text_v127(
    source_text: str,
    timed_canonical: Sequence[TimedCanonicalOccurrence],
    repair_canonical: Sequence[RepairCanonicalLine],
    *,
    auto_threshold: float = DEFAULT_AUTO_THRESHOLD,
    rate_prior_by_source: Mapping[int, float] | None = None,
    rate_prior_metadata_by_source: Mapping[int, Mapping[str, object]] | None = None,
    _segmentation_internal_boundary_guard: bool = False,
) -> tuple[str, dict[str, object]]:
    rendered_v126, base_report = smart_repair_srt_text_v126(
        source_text,
        timed_canonical,
        repair_canonical,
        auto_threshold=auto_threshold,
        rate_prior_by_source=rate_prior_by_source,
        rate_prior_metadata_by_source=rate_prior_metadata_by_source,
        _segmentation_internal_boundary_guard=_segmentation_internal_boundary_guard,
    )
    parts, cues = parse_srt_text(rendered_v126)
    text_decisions = _match_decisions(base_report["text_decisions"])
    replacements, text_decisions, recovery = recover_final_text_reviews(
        cues,
        timed_canonical,
        text_decisions,
        allow_cross_script_vocalization=True,
    )
    rendered = (
        render_repaired_srt(parts, cues, replacements)
        if replacements
        else rendered_v126
    )

    report = dict(base_report)
    report["policy_id"] = SMART_POLICY_ID
    report["text_cross_script_vocalization_recovery_count"] = (
        recovery.cross_script_vocalization_recovery_count
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
    add_timing_confidence_semantics(report)
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
    "add_timing_confidence_semantics",
    "smart_repair_srt_text_v127",
)
