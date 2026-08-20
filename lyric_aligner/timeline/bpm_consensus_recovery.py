"""BPM-aided text-only recovery for Smart unresolved lyric runs.

This layer uses a user-supplied BPM-derived rate only as soft corroborating
text evidence. It never grants timing-mutation authority. A source becomes
eligible only when several pre-recovery 1:1 canonical mappings independently
agree on one Source-to-Mix offset under that BPM rate. Within that source, the
layer may repartition a consecutive canonical text stream across consecutive
existing editor cues while preserving already-resolved lower-mode text.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from statistics import median
from typing import Mapping, Sequence

from lyric_aligner.text_repair import (
    MatchDecision,
    SubtitleCue,
    _assign_targets,
    _normalize_for_match,
)
from lyric_aligner.timeline.anchor_repair import TimedCanonicalOccurrence, _cue_times

_EVIDENCE_REASONS = frozenset(
    {
        "canonical_content_matches_source_segmentation",
        "high_confidence_span_preserving_match",
        "low_or_structurally_unsafe_similarity",
        "adjacent_alignment_gap_requires_review",
        "segmentation_would_empty_existing_cue",
    }
)
_MIN_EVIDENCE_SCORE = 0.40
_CLUSTER_TOLERANCE_MS = 1200
_MAX_RUN_CUES = 8
_MIN_RUN_REVIEW_SCORE = 0.40
_SINGLE_RECOVERY_SCORE = 0.84
_SINGLE_RESIDUAL_MS = 600
_SINGLE_MIN_LENGTH_RATIO = 0.80


@dataclass(frozen=True)
class BpmConsensusTextModel:
    source_ordinal: int
    source: str
    rate: float
    offset_ms: float
    evidence_count: int
    inlier_count: int
    median_abs_residual_ms: float
    inlier_fraction: float
    status: str

    def source_to_mix_ms(self, source_ms: int) -> int:
        return int(round((source_ms - self.offset_ms) / self.rate))


@dataclass(frozen=True)
class BpmConsensusRecoverySummary:
    reconciled_cue_count: int = 0
    reconciled_region_count: int = 0
    resolved_review_cue_count: int = 0
    single_cue_count: int = 0


@dataclass(frozen=True)
class _Evidence:
    cue_ordinal: int
    canonical_ordinal: int
    score: float
    offset_ms: float


def _single_span(decision: MatchDecision | None) -> int | None:
    if decision is None or decision.canonical_span is None:
        return None
    start, end = decision.canonical_span
    if end - start != 1:
        return None
    return int(start)


def _soft_bpm_rates(
    rate_prior_by_source: Mapping[int, float] | None,
    metadata: Mapping[int, Mapping[str, object]] | None,
) -> dict[int, float]:
    values = rate_prior_by_source or {}
    meta = metadata or {}
    output: dict[int, float] = {}
    for source_ordinal, value in values.items():
        row = meta.get(source_ordinal) or {}
        if str(row.get("provenance") or "") != "bpm_derived":
            continue
        number = float(value)
        if 0.5 <= number <= 2.0:
            output[int(source_ordinal)] = number
    return output


def build_bpm_consensus_text_models(
    cues: Sequence[SubtitleCue],
    canonical: Sequence[TimedCanonicalOccurrence],
    evidence_decisions: Sequence[MatchDecision],
    *,
    rate_prior_by_source: Mapping[int, float] | None = None,
    rate_prior_metadata_by_source: Mapping[int, Mapping[str, object]] | None = None,
) -> list[BpmConsensusTextModel]:
    """Build text-only offset consensus under a soft BPM-derived rate.

    The BPM rate does not become a hard timeline prior. It is only accepted for
    text recovery when multiple baseline mappings form one dense offset cluster.
    """

    rates = _soft_bpm_rates(rate_prior_by_source, rate_prior_metadata_by_source)
    by_ordinal = {item.ordinal: item for item in canonical}
    sources = {item.source_ordinal: item.source for item in canonical}
    models: list[BpmConsensusTextModel] = []

    for source_ordinal in sorted(sources):
        rate = rates.get(source_ordinal)
        if rate is None:
            models.append(
                BpmConsensusTextModel(
                    source_ordinal,
                    sources[source_ordinal],
                    1.0,
                    0.0,
                    0,
                    0,
                    float("inf"),
                    0.0,
                    "no_bpm_soft_prior",
                )
            )
            continue

        evidence: list[_Evidence] = []
        for cue, decision in zip(cues, evidence_decisions):
            if decision.reason not in _EVIDENCE_REASONS:
                continue
            canonical_ordinal = _single_span(decision)
            if canonical_ordinal is None:
                continue
            occurrence = by_ordinal.get(canonical_ordinal)
            if occurrence is None or occurrence.source_ordinal != source_ordinal:
                continue
            score = float(decision.score)
            if score < _MIN_EVIDENCE_SCORE:
                continue
            mix_start_ms, _ = _cue_times(cue)
            evidence.append(
                _Evidence(
                    cue_ordinal=cue.ordinal,
                    canonical_ordinal=canonical_ordinal,
                    score=score,
                    offset_ms=float(occurrence.anchor_time_ms - rate * mix_start_ms),
                )
            )

        if len(evidence) < 5:
            models.append(
                BpmConsensusTextModel(
                    source_ordinal,
                    sources[source_ordinal],
                    rate,
                    0.0,
                    len(evidence),
                    0,
                    float("inf"),
                    0.0,
                    "insufficient_consensus_evidence",
                )
            )
            continue

        best_group: list[_Evidence] = []
        best_key = (-1, -1.0, -1)
        for seed in evidence:
            group = [
                item
                for item in evidence
                if abs(item.offset_ms - seed.offset_ms) <= _CLUSTER_TOLERANCE_MS
            ]
            key = (
                len(group),
                sum(0.25 + item.score for item in group),
                sum(item.score >= 0.72 for item in group),
            )
            if key > best_key:
                best_key = key
                best_group = group

        offset = float(median(item.offset_ms for item in best_group))
        inliers = [
            item
            for item in evidence
            if abs(item.offset_ms - offset) <= _CLUSTER_TOLERANCE_MS
        ]
        offset = float(median(item.offset_ms for item in inliers))
        inliers = [
            item
            for item in evidence
            if abs(item.offset_ms - offset) <= _CLUSTER_TOLERANCE_MS
        ]
        residuals = [abs(item.offset_ms - offset) for item in inliers]
        med = float(median(residuals)) if residuals else float("inf")
        fraction = len(inliers) / len(evidence)
        mix_starts = [_cue_times(cues[item.cue_ordinal])[0] for item in inliers]
        source_times = [by_ordinal[item.canonical_ordinal].anchor_time_ms for item in inliers]
        mix_span = max(mix_starts) - min(mix_starts) if mix_starts else 0
        source_span = max(source_times) - min(source_times) if source_times else 0
        strong_060 = sum(item.score >= 0.60 for item in inliers)
        strong_072 = sum(item.score >= 0.72 for item in inliers)
        ready = (
            len(inliers) >= 5
            and fraction >= 0.70
            and med <= 450
            and mix_span >= 8000
            and source_span >= 8000
            and strong_060 >= 2
            and strong_072 >= 1
        )
        models.append(
            BpmConsensusTextModel(
                source_ordinal=source_ordinal,
                source=sources[source_ordinal],
                rate=rate,
                offset_ms=offset,
                evidence_count=len(evidence),
                inlier_count=len(inliers),
                median_abs_residual_ms=med,
                inlier_fraction=fraction,
                status="ready" if ready else "unstable_consensus",
            )
        )
    return models


def _model_consistent(
    cue: SubtitleCue,
    occurrence: TimedCanonicalOccurrence,
    model: BpmConsensusTextModel,
    *,
    tolerance_ms: int = _CLUSTER_TOLERANCE_MS,
) -> bool:
    cue_start, _ = _cue_times(cue)
    return abs(cue_start - model.source_to_mix_ms(occurrence.anchor_time_ms)) <= tolerance_ms


def _projected_run_decision(
    cue: SubtitleCue,
    original: MatchDecision,
    *,
    first_canonical_ordinal: int,
    last_canonical_ordinal: int,
    canonical_text: str,
    output_text: str,
    reason: str,
) -> MatchDecision:
    return replace(
        original,
        canonical_ordinal=first_canonical_ordinal,
        score=min(float(original.score), 0.91),
        action="unchanged" if cue.normalized == _normalize_for_match(output_text) else "replace",
        reason=reason,
        cue_span=(cue.ordinal, cue.ordinal + 1),
        canonical_span=(first_canonical_ordinal, last_canonical_ordinal + 1),
        canonical_text=canonical_text,
        output_text=output_text,
        edit_operations=(),
    )


def recover_text_reviews_from_bpm_consensus(
    cues: Sequence[SubtitleCue],
    canonical: Sequence[TimedCanonicalOccurrence],
    decisions: Sequence[MatchDecision],
    *,
    evidence_decisions: Sequence[MatchDecision] | None = None,
    replacements: Mapping[int, str] | None = None,
    rate_prior_by_source: Mapping[int, float] | None = None,
    rate_prior_metadata_by_source: Mapping[int, Mapping[str, object]] | None = None,
    max_run_cues: int = _MAX_RUN_CUES,
) -> tuple[
    dict[int, str],
    list[MatchDecision],
    BpmConsensusRecoverySummary,
    list[BpmConsensusTextModel],
]:
    """Resolve review text using BPM-validated consecutive canonical streams.

    Existing non-review text is immutable. A run is accepted only when the
    baseline canonical ordinals are consecutive, every occurrence agrees with
    the BPM offset consensus, and repartitioning the complete canonical stream
    leaves all already-resolved cues text-identical after normalization.
    """

    evidence_decisions = list(evidence_decisions or decisions)
    models = build_bpm_consensus_text_models(
        cues,
        canonical,
        evidence_decisions,
        rate_prior_by_source=rate_prior_by_source,
        rate_prior_metadata_by_source=rate_prior_metadata_by_source,
    )
    ready_models = {
        item.source_ordinal: item for item in models if item.status == "ready"
    }
    if not ready_models:
        return {}, list(decisions), BpmConsensusRecoverySummary(), models

    canonical_by_ordinal = {item.ordinal: item for item in canonical}
    current_by_cue = {item.cue_ordinal: item for item in decisions}
    evidence_by_cue = {item.cue_ordinal: item for item in evidence_decisions}
    working_text = {
        cue.ordinal: str((replacements or {}).get(cue.ordinal, cue.text)) for cue in cues
    }
    changed: dict[int, str] = {}
    resolved: set[int] = set()
    reconciled: set[int] = set()
    region_count = 0
    single_count = 0

    def run_identity(cue_ordinal: int) -> tuple[int, int] | None:
        evidence = evidence_by_cue.get(cue_ordinal)
        canonical_ordinal = _single_span(evidence)
        if evidence is None or canonical_ordinal is None:
            return None
        occurrence = canonical_by_ordinal.get(canonical_ordinal)
        if occurrence is None:
            return None
        model = ready_models.get(occurrence.source_ordinal)
        if model is None or not _model_consistent(cues[cue_ordinal], occurrence, model):
            return None
        return occurrence.source_ordinal, canonical_ordinal

    index = 0
    while index < len(cues):
        identity = run_identity(index)
        if identity is None:
            index += 1
            continue
        source_ordinal, canonical_ordinal = identity
        run = [index]
        last_canonical = canonical_ordinal
        cursor = index + 1
        while cursor < len(cues) and len(run) < max_run_cues:
            next_identity = run_identity(cursor)
            if (
                next_identity is None
                or next_identity[0] != source_ordinal
                or next_identity[1] != last_canonical + 1
            ):
                break
            run.append(cursor)
            last_canonical = next_identity[1]
            cursor += 1

        current_run = [current_by_cue.get(cue_ordinal) for cue_ordinal in run]
        review_rows = [item for item in current_run if item is not None and item.action == "review"]
        evidence_review_scores = [
            float(evidence_by_cue[cue_ordinal].score)
            for cue_ordinal in run
            if current_by_cue.get(cue_ordinal) is not None
            and current_by_cue[cue_ordinal].action == "review"
            and cue_ordinal in evidence_by_cue
        ]
        if (
            len(run) >= 2
            and review_rows
            and evidence_review_scores
            and max(evidence_review_scores) >= _MIN_RUN_REVIEW_SCORE
        ):
            first_canonical = run_identity(run[0])[1]
            last_canonical = run_identity(run[-1])[1]
            rows = [canonical_by_ordinal[value] for value in range(first_canonical, last_canonical + 1)]
            target_text = "".join(item.text for item in rows)
            source_text = "".join(cues[cue_ordinal].text for cue_ordinal in run)
            source_norm = _normalize_for_match(source_text)
            target_norm = _normalize_for_match(target_text)
            length_ratio = (
                min(len(source_norm), len(target_norm)) / max(len(source_norm), len(target_norm))
                if source_norm and target_norm
                else 0.0
            )
            assigned, _ = _assign_targets(
                [cues[cue_ordinal].text for cue_ordinal in run],
                target_text,
            )
            nonempty = all(_normalize_for_match(value) for value in assigned)
            stream_exact = _normalize_for_match("".join(assigned)) == target_norm
            resolved_unchanged = all(
                current_by_cue[cue_ordinal].action == "review"
                or _normalize_for_match(working_text[cue_ordinal]) == _normalize_for_match(output_text)
                for cue_ordinal, output_text in zip(run, assigned)
                if current_by_cue.get(cue_ordinal) is not None
            )
            if 0.55 <= length_ratio <= 1.80 and nonempty and stream_exact and resolved_unchanged:
                for cue_ordinal, output_text in zip(run, assigned):
                    original = current_by_cue.get(cue_ordinal)
                    if original is None or original.action != "review":
                        continue
                    cue = cues[cue_ordinal]
                    decision = _projected_run_decision(
                        cue,
                        original,
                        first_canonical_ordinal=first_canonical,
                        last_canonical_ordinal=last_canonical,
                        canonical_text=target_text,
                        output_text=output_text,
                        reason="sequence_projection_confirms_bpm_consensus_stream",
                    )
                    current_by_cue[cue_ordinal] = decision
                    changed[cue_ordinal] = output_text
                    working_text[cue_ordinal] = output_text
                    reconciled.add(cue_ordinal)
                    resolved.add(cue_ordinal)
                region_count += 1
        index = max(index + 1, cursor)

    # Resolve isolated high-similarity gap reviews after stream recovery. This is
    # intentionally much stricter than the run path because there is no adjacent
    # ownership context to absorb a boundary difference.
    for cue in cues:
        current = current_by_cue.get(cue.ordinal)
        evidence = evidence_by_cue.get(cue.ordinal)
        if current is None or evidence is None or current.action != "review":
            continue
        canonical_ordinal = _single_span(evidence)
        if canonical_ordinal is None or float(evidence.score) < _SINGLE_RECOVERY_SCORE:
            continue
        occurrence = canonical_by_ordinal.get(canonical_ordinal)
        if occurrence is None:
            continue
        model = ready_models.get(occurrence.source_ordinal)
        if model is None:
            continue
        cue_start, _ = _cue_times(cue)
        residual = abs(cue_start - model.source_to_mix_ms(occurrence.anchor_time_ms))
        if residual > _SINGLE_RESIDUAL_MS:
            continue
        source_norm = cue.normalized
        target_norm = occurrence.normalized
        if not source_norm or not target_norm:
            continue
        length_ratio = min(len(source_norm), len(target_norm)) / max(len(source_norm), len(target_norm))
        if length_ratio < _SINGLE_MIN_LENGTH_RATIO:
            continue
        output_text = occurrence.text
        decision = _projected_run_decision(
            cue,
            current,
            first_canonical_ordinal=occurrence.ordinal,
            last_canonical_ordinal=occurrence.ordinal,
            canonical_text=occurrence.text,
            output_text=output_text,
            reason="sequence_projection_confirms_bpm_consensus_single",
        )
        current_by_cue[cue.ordinal] = decision
        changed[cue.ordinal] = output_text
        working_text[cue.ordinal] = output_text
        reconciled.add(cue.ordinal)
        resolved.add(cue.ordinal)
        single_count += 1

    ordered = [current_by_cue.get(item.cue_ordinal, item) for item in decisions]
    return (
        changed,
        ordered,
        BpmConsensusRecoverySummary(
            reconciled_cue_count=len(reconciled),
            reconciled_region_count=region_count,
            resolved_review_cue_count=len(resolved),
            single_cue_count=single_count,
        ),
        models,
    )
