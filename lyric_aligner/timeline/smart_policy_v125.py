"""Smart v1.2.5 production wrapper over the frozen v1.2.4 policy.

v1.2.5 intentionally leaves the v1.2.4 timing plan frozen.  It runs the
previous policy to completion, then applies the separately validated A-bounded
text-only tier to the materialized v1.2.4 SRT using that already-final timing
evidence.  Timing decisions are never rebuilt after A-bounded recovery.
"""

from __future__ import annotations

from collections import Counter
from typing import Mapping, Sequence

from lyric_aligner.text_repair import (
    DEFAULT_AUTO_THRESHOLD,
    CanonicalLine as RepairCanonicalLine,
    MatchDecision,
    parse_srt_text,
    render_repaired_srt,
)
from lyric_aligner.timeline.a_bounded_reconcile import (
    recover_mapped_reviews_from_a_bounded,
)
from lyric_aligner.timeline.anchor_repair import (
    TimedCanonicalOccurrence,
    TimingDecision,
)
from lyric_aligner.timeline.smart_policy import (
    smart_repair_srt_text_v11 as smart_repair_srt_text_v124,
)

SMART_POLICY_ID = "smart-validation-policy-2026-08-21-v1.2.5"


def _match_decisions(payload: Sequence[Mapping[str, object]]) -> list[MatchDecision]:
    output: list[MatchDecision] = []
    for item in payload:
        cue_span = item.get("cue_span")
        canonical_span = item.get("canonical_span")
        output.append(
            MatchDecision(
                cue_ordinal=int(item["cue_ordinal"]),
                canonical_ordinal=(
                    int(item["canonical_ordinal"])
                    if item.get("canonical_ordinal") is not None
                    else None
                ),
                score=float(item.get("score", 0.0)),
                action=str(item.get("action", "review")),
                reason=str(item.get("reason", "review_required")),
                cue_span=(
                    (int(cue_span[0]), int(cue_span[1]))
                    if cue_span is not None
                    else None
                ),
                canonical_span=(
                    (int(canonical_span[0]), int(canonical_span[1]))
                    if canonical_span is not None
                    else None
                ),
            )
        )
    return output


def _timing_decisions(payload: Sequence[Mapping[str, object]]) -> list[TimingDecision]:
    output: list[TimingDecision] = []
    for item in payload:
        output.append(
            TimingDecision(
                cue_ordinal=int(item["cue_ordinal"]),
                source_ordinal=(
                    int(item["source_ordinal"])
                    if item.get("source_ordinal") is not None
                    else None
                ),
                canonical_ordinal=(
                    int(item["canonical_ordinal"])
                    if item.get("canonical_ordinal") is not None
                    else None
                ),
                anchor_grade=str(item.get("anchor_grade", "C")),
                action=str(item.get("action", "review")),
                reason=str(item.get("reason", "review_required")),
                old_start_ms=int(item["old_start_ms"]),
                old_end_ms=int(item["old_end_ms"]),
                proposed_start_ms=(
                    int(item["proposed_start_ms"])
                    if item.get("proposed_start_ms") is not None
                    else None
                ),
                proposed_end_ms=(
                    int(item["proposed_end_ms"])
                    if item.get("proposed_end_ms") is not None
                    else None
                ),
                residual_ms=(
                    float(item["residual_ms"])
                    if item.get("residual_ms") is not None
                    else None
                ),
                model_status=(
                    str(item["model_status"])
                    if item.get("model_status") is not None
                    else None
                ),
                evidence=tuple(str(value) for value in item.get("evidence", ())),
            )
        )
    return output


def _text_payload(decisions: Sequence[MatchDecision]) -> list[dict[str, object]]:
    return [
        {
            "cue_ordinal": item.cue_ordinal,
            "canonical_ordinal": item.canonical_ordinal,
            "cue_span": list(item.cue_span) if item.cue_span else None,
            "canonical_span": list(item.canonical_span) if item.canonical_span else None,
            "score": item.score,
            "action": item.action,
            "reason": item.reason,
        }
        for item in decisions
    ]


def _review_reason_counts(decisions: Sequence[MatchDecision]) -> dict[str, int]:
    counts = Counter(item.reason for item in decisions if item.action == "review")
    return dict(sorted(counts.items()))


def _review_mapping_counts(decisions: Sequence[MatchDecision]) -> tuple[int, int]:
    mapped = 0
    unmapped = 0
    for item in decisions:
        if item.action != "review":
            continue
        span = item.canonical_span
        if span is None or int(span[0]) == int(span[1]):
            unmapped += 1
        else:
            mapped += 1
    return mapped, unmapped


def _materialization_counts(source_text: str, rendered: str) -> tuple[int, int]:
    _, source_cues = parse_srt_text(source_text)
    _, output_cues = parse_srt_text(rendered)
    if len(source_cues) != len(output_cues):
        raise AssertionError("A-bounded recovery changed cue count")
    exact = sum(
        source.text != output.text
        for source, output in zip(source_cues, output_cues)
    )
    semantic = sum(
        source.normalized != output.normalized
        for source, output in zip(source_cues, output_cues)
    )
    return exact, semantic


def smart_repair_srt_text_v125(
    source_text: str,
    timed_canonical: Sequence[TimedCanonicalOccurrence],
    repair_canonical: Sequence[RepairCanonicalLine],
    *,
    auto_threshold: float = DEFAULT_AUTO_THRESHOLD,
    rate_prior_by_source: Mapping[int, float] | None = None,
    rate_prior_metadata_by_source: Mapping[int, Mapping[str, object]] | None = None,
) -> tuple[str, dict[str, object]]:
    """Run frozen Smart v1.2.4, then A-bounded text-only recovery."""

    rendered_v124, base_report = smart_repair_srt_text_v124(
        source_text,
        timed_canonical,
        repair_canonical,
        auto_threshold=auto_threshold,
        rate_prior_by_source=rate_prior_by_source,
        rate_prior_metadata_by_source=rate_prior_metadata_by_source,
    )

    parts, cues = parse_srt_text(rendered_v124)
    text_decisions = _match_decisions(base_report["text_decisions"])
    timing = _timing_decisions(base_report["timing_decisions"])
    replacements, text_decisions, recovery = recover_mapped_reviews_from_a_bounded(
        cues,
        timed_canonical,
        text_decisions,
        timing,
    )
    rendered = (
        render_repaired_srt(parts, cues, replacements)
        if replacements
        else rendered_v124
    )

    # This tier is post-timing by construction: final timing decisions from
    # v1.2.4 are copied unchanged and are never rebuilt from recovered text.
    _, final_cues = parse_srt_text(rendered)
    if [(cue.number, cue.timing) for cue in final_cues] != [
        (cue.number, cue.timing) for cue in cues
    ]:
        raise AssertionError("A-bounded recovery changed numbering or timing")

    report = dict(base_report)
    report["policy_id"] = SMART_POLICY_ID
    report["text_a_bounded_recovery_count"] = recovery.resolved_review_cue_count
    report["text_a_bounded_region_count"] = recovery.resolved_region_count
    report["text_a_bounded_materialized_change_count"] = (
        recovery.materialized_text_change_count
    )
    report["text_decisions"] = _text_payload(text_decisions)

    text_review_count = sum(item.action == "review" for item in text_decisions)
    mapped_review_count, unmapped_review_count = _review_mapping_counts(text_decisions)
    replacement_count = sum(item.action == "replace" for item in text_decisions)
    materialized_count, semantic_count = _materialization_counts(source_text, rendered)

    report["text_replacement_count"] = replacement_count
    report["text_decision_replacement_count"] = replacement_count
    report["text_materialized_change_count"] = materialized_count
    report["text_semantic_change_count"] = semantic_count
    report["text_review_count"] = text_review_count
    report["text_mapped_review_count"] = mapped_review_count
    report["text_unmapped_review_count"] = unmapped_review_count
    report["text_review_reason_counts"] = _review_reason_counts(text_decisions)
    report["text_status"] = "review_required" if text_review_count else "ready"
    report["pro_text_escalation_required"] = bool(text_review_count)

    timing_review_count = int(report.get("timing_review_count", 0))
    report["pro_escalation_required"] = bool(text_review_count or timing_review_count)
    report["status"] = (
        "review_required" if (text_review_count or timing_review_count) else "ready"
    )
    return rendered, report
