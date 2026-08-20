"""BPM-validated text-only recovery for Smart review cues.

BPM-derived rate is a soft prior, never timing authority.  This layer upgrades
that prior to text-only evidence only after several independently safe baseline
text identities agree with the fixed-rate affine projection.  It may then
recover already-mapped 1:1 review cues whose projected onset and local structure
are consistent.  Recovered decisions remain below B-grade timing authority.

The implementation deliberately does not repartition whole review blocks and
does not fill editor-only vocalization cues with lexical lyrics.  This keeps the
v1.2.1 ownership/false-auto safety boundary intact while helping repeated-lyric
songs where unique A anchors are scarce.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from statistics import median
from typing import Mapping, Sequence

from lyric_aligner.text_repair import MatchDecision, SubtitleCue, _normalize_for_match
from lyric_aligner.timeline.anchor_repair import TimedCanonicalOccurrence, _cue_times

_BASELINE_SAFE_REASONS = frozenset(
    {
        "canonical_content_matches_source_segmentation",
        "high_confidence_span_preserving_match",
    }
)
_CJK_VOCALIZATION = frozenset("哦噢啊阿耶呀哎哟呃呜喔嘿哈啦嗯哼唔诶哒")
_LATIN_VOCALIZATION = frozenset(
    {"oh", "ooh", "ah", "uh", "huh", "hey", "yeah", "ya", "yo", "woo", "woah", "whoa", "hmm", "mm", "na", "la"}
)
_LATIN_TOKEN_RE = re.compile(r"[A-Za-z]+")


@dataclass(frozen=True)
class BpmTextProjectionModel:
    source_ordinal: int
    source: str
    rate: float
    offset_ms: float
    anchor_count: int
    inlier_count: int
    median_abs_residual_ms: float
    inlier_fraction: float
    pairwise_rate_relative_error: float | None
    status: str

    def source_to_mix_ms(self, source_ms: int) -> int:
        return int(round((source_ms - self.offset_ms) / self.rate))


@dataclass(frozen=True)
class BpmTextRecoverySummary:
    resolved_review_cue_count: int = 0
    vocalization_trim_count: int = 0


@dataclass(frozen=True)
class _Anchor:
    cue_ordinal: int
    canonical_ordinal: int
    source_ordinal: int
    mix_start_ms: int
    source_time_ms: int
    score: float


def _single_span(decision: MatchDecision | None) -> int | None:
    if decision is None or decision.canonical_span is None:
        return None
    start, end = decision.canonical_span
    if int(end) - int(start) != 1:
        return None
    return int(start)


def _pairwise_rate(anchors: Sequence[_Anchor]) -> float | None:
    slopes: list[float] = []
    for index, left in enumerate(anchors):
        for right in anchors[index + 1 :]:
            mix_delta = right.mix_start_ms - left.mix_start_ms
            source_delta = right.source_time_ms - left.source_time_ms
            if mix_delta < 3000 or source_delta <= 0:
                continue
            slope = source_delta / mix_delta
            if 0.5 <= slope <= 2.0:
                slopes.append(float(slope))
    return float(median(slopes)) if slopes else None


def _is_pure_vocalization(value: str) -> bool:
    normalized = _normalize_for_match(value)
    if not normalized:
        return False
    if all(char in _CJK_VOCALIZATION for char in normalized):
        return True
    latin = _LATIN_TOKEN_RE.findall(value.casefold())
    non_latin = "".join(char for char in normalized if not ("a" <= char <= "z"))
    if non_latin:
        return False
    return bool(latin) and all(token in _LATIN_VOCALIZATION for token in latin)


def _strip_cjk_edges(value: str) -> str:
    result = value.strip()
    while result and result[0] in _CJK_VOCALIZATION:
        result = result[1:].lstrip(" ,，。.!！?？、-—")
    while result and result[-1] in _CJK_VOCALIZATION:
        result = result[:-1].rstrip(" ,，。.!！?？、-—")
    return result.strip()


def _strip_latin_edge_tokens(value: str) -> str:
    result = value.strip()
    while True:
        match = re.match(r"^\s*([A-Za-z]+)(?:\s+|[,，。.!！?？、-—]+\s*)", result)
        if match is None or match.group(1).casefold() not in _LATIN_VOCALIZATION:
            break
        result = result[match.end() :].lstrip()
    while True:
        match = re.search(r"(?:\s+|\s*[,，。.!！?？、-—]+\s*)([A-Za-z]+)\s*$", result)
        if match is None or match.group(1).casefold() not in _LATIN_VOCALIZATION:
            break
        result = result[: match.start()].rstrip()
    return result.strip()


def _trim_optional_vocalization_edges(source: str, canonical: str) -> str | None:
    """Return canonical text only when optional edge vocalization explains delta."""

    if _normalize_for_match(source) == _normalize_for_match(canonical):
        return None
    trimmed = _strip_latin_edge_tokens(_strip_cjk_edges(source))
    if not trimmed or trimmed == source.strip():
        return None
    if _normalize_for_match(trimmed) != _normalize_for_match(canonical):
        return None
    return canonical


def _safe_anchor_inventory(
    cues: Sequence[SubtitleCue],
    canonical_by_ordinal: Mapping[int, TimedCanonicalOccurrence],
    decisions: Sequence[MatchDecision],
) -> list[_Anchor]:
    best_by_canonical: dict[int, _Anchor] = {}
    cue_by_ordinal = {cue.ordinal: cue for cue in cues}
    for decision in decisions:
        ordinal = _single_span(decision)
        if (
            ordinal is None
            or decision.action == "review"
            or decision.reason not in _BASELINE_SAFE_REASONS
            or float(decision.score) < 0.72
        ):
            continue
        occurrence = canonical_by_ordinal.get(ordinal)
        cue = cue_by_ordinal.get(decision.cue_ordinal)
        if occurrence is None or cue is None:
            continue
        start_ms, _ = _cue_times(cue)
        anchor = _Anchor(
            cue_ordinal=cue.ordinal,
            canonical_ordinal=ordinal,
            source_ordinal=occurrence.source_ordinal,
            mix_start_ms=start_ms,
            source_time_ms=occurrence.anchor_time_ms,
            score=float(decision.score),
        )
        current = best_by_canonical.get(ordinal)
        if current is None or anchor.score > current.score:
            best_by_canonical[ordinal] = anchor
    return sorted(best_by_canonical.values(), key=lambda item: item.cue_ordinal)


def _bpm_rates(
    metadata: Mapping[int, Mapping[str, object]] | None,
) -> dict[int, float]:
    result: dict[int, float] = {}
    for source_ordinal, item in (metadata or {}).items():
        if str(item.get("provenance") or "") != "bpm_derived":
            continue
        try:
            value = float(item.get("value"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and 0.5 <= value <= 2.0:
            result[int(source_ordinal)] = value
    return result


def _build_models(
    canonical: Sequence[TimedCanonicalOccurrence],
    anchors: Sequence[_Anchor],
    metadata: Mapping[int, Mapping[str, object]] | None,
) -> tuple[list[BpmTextProjectionModel], dict[int, list[_Anchor]]]:
    rates = _bpm_rates(metadata)
    grouped: dict[int, list[_Anchor]] = defaultdict(list)
    for anchor in anchors:
        grouped[anchor.source_ordinal].append(anchor)
    source_names = {item.source_ordinal: item.source for item in canonical}
    models: list[BpmTextProjectionModel] = []
    inliers_by_source: dict[int, list[_Anchor]] = {}

    for source_ordinal, source in sorted(source_names.items()):
        rows = sorted(grouped.get(source_ordinal, []), key=lambda item: item.cue_ordinal)
        rate = rates.get(source_ordinal)
        if rate is None:
            models.append(BpmTextProjectionModel(source_ordinal, source, 1.0, 0.0, len(rows), 0, float("inf"), 0.0, None, "no_bpm_prior"))
            continue
        if len(rows) < 3:
            models.append(BpmTextProjectionModel(source_ordinal, source, rate, 0.0, len(rows), 0, float("inf"), 0.0, None, "insufficient_safe_anchors"))
            continue

        initial_offset = float(median(item.source_time_ms - rate * item.mix_start_ms for item in rows))
        first_inliers = [
            item for item in rows
            if abs(item.source_time_ms - (initial_offset + rate * item.mix_start_ms)) <= 750
        ]
        if len(first_inliers) < 3:
            models.append(BpmTextProjectionModel(source_ordinal, source, rate, initial_offset, len(rows), len(first_inliers), float("inf"), len(first_inliers) / len(rows), None, "insufficient_bpm_inliers"))
            continue

        offset = float(median(item.source_time_ms - rate * item.mix_start_ms for item in first_inliers))
        inliers = [
            item for item in rows
            if abs(item.source_time_ms - (offset + rate * item.mix_start_ms)) <= 750
        ]
        residuals = [abs(item.source_time_ms - (offset + rate * item.mix_start_ms)) for item in inliers]
        fraction = len(inliers) / len(rows)
        med = float(median(residuals)) if residuals else float("inf")
        pairwise = _pairwise_rate(inliers)
        relative = abs(pairwise - rate) / rate if pairwise is not None else None
        ordered = all(
            right.canonical_ordinal > left.canonical_ordinal
            for left, right in zip(inliers, inliers[1:])
        )
        mix_span = inliers[-1].mix_start_ms - inliers[0].mix_start_ms if len(inliers) >= 2 else 0
        source_span = inliers[-1].source_time_ms - inliers[0].source_time_ms if len(inliers) >= 2 else 0
        ready = (
            len(inliers) >= 3
            and fraction >= 0.75
            and med <= 300
            and mix_span >= 8000
            and source_span >= 8000
            and ordered
            and relative is not None
            and relative <= 0.025
        )
        status = "ready" if ready else "bpm_projection_unstable"
        model = BpmTextProjectionModel(
            source_ordinal=source_ordinal,
            source=source,
            rate=rate,
            offset_ms=offset,
            anchor_count=len(rows),
            inlier_count=len(inliers),
            median_abs_residual_ms=med,
            inlier_fraction=fraction,
            pairwise_rate_relative_error=relative,
            status=status,
        )
        models.append(model)
        if ready:
            inliers_by_source[source_ordinal] = inliers
    return models, inliers_by_source


def _adjacent_claims_same(
    cue_ordinal: int,
    canonical_ordinal: int,
    decisions: Mapping[int, MatchDecision],
) -> bool:
    for neighbor in (cue_ordinal - 1, cue_ordinal + 1):
        item = decisions.get(neighbor)
        if item is None or item.canonical_span is None:
            continue
        start, end = item.canonical_span
        if int(start) <= canonical_ordinal < int(end):
            return True
    return False


def _meaningful_overlap(value: str, missing: str) -> bool:
    left = _normalize_for_match(value)
    right = _normalize_for_match(missing)
    if len(left) < 2 or len(right) < 2:
        return False
    limit = min(8, len(left), len(right))
    for count in range(limit, 1, -1):
        if left.startswith(right[:count]) or left.endswith(right[-count:]):
            return True
        if right.startswith(left[:count]) or right.endswith(left[-count:]):
            return True
    return False


def _split_continuation_risk(
    cue: SubtitleCue,
    occurrence: TimedCanonicalOccurrence,
    cues: Sequence[SubtitleCue],
) -> bool:
    source = cue.normalized
    target = occurrence.normalized
    if not source or not target or source == target:
        return False
    if target.startswith(source) and len(source) < len(target) and cue.ordinal + 1 < len(cues):
        if _meaningful_overlap(cues[cue.ordinal + 1].normalized, target[len(source) :]):
            return True
    if target.endswith(source) and len(source) < len(target) and cue.ordinal > 0:
        if _meaningful_overlap(cues[cue.ordinal - 1].normalized, target[: len(target) - len(source)]):
            return True
    return False


def _lexical_rows_by_source(
    canonical: Sequence[TimedCanonicalOccurrence],
) -> dict[int, list[TimedCanonicalOccurrence]]:
    result: dict[int, list[TimedCanonicalOccurrence]] = defaultdict(list)
    for item in canonical:
        if not _is_pure_vocalization(item.text):
            result[item.source_ordinal].append(item)
    return dict(result)


def _bracketed_or_strict_leading_edge(
    cue_ordinal: int,
    canonical_ordinal: int,
    source_ordinal: int,
    inliers: Sequence[_Anchor],
    lexical_rows: Mapping[int, Sequence[TimedCanonicalOccurrence]],
) -> bool:
    left = [item for item in inliers if item.cue_ordinal < cue_ordinal and item.canonical_ordinal < canonical_ordinal]
    right = [item for item in inliers if item.cue_ordinal > cue_ordinal and item.canonical_ordinal > canonical_ordinal]
    if left and right:
        return True
    if not right or left:
        return False
    first = min(inliers, key=lambda item: item.cue_ordinal)
    if cue_ordinal >= first.cue_ordinal or first.cue_ordinal - cue_ordinal > 2:
        return False
    rows = list(lexical_rows.get(source_ordinal, ()))
    positions = {item.ordinal: index for index, item in enumerate(rows)}
    first_pos = positions.get(first.canonical_ordinal)
    candidate_pos = positions.get(canonical_ordinal)
    if first_pos is None or candidate_pos is None:
        return False
    return 1 <= first_pos - candidate_pos <= 2


def _next_lexical_starts_inside_cue(
    occurrence: TimedCanonicalOccurrence,
    cue: SubtitleCue,
    model: BpmTextProjectionModel,
    lexical_rows: Mapping[int, Sequence[TimedCanonicalOccurrence]],
    *,
    safety_ms: int = 300,
) -> bool:
    rows = list(lexical_rows.get(occurrence.source_ordinal, ()))
    positions = {item.ordinal: index for index, item in enumerate(rows)}
    pos = positions.get(occurrence.ordinal)
    if pos is None or pos + 1 >= len(rows):
        return False
    _, cue_end = _cue_times(cue)
    next_start = model.source_to_mix_ms(rows[pos + 1].anchor_time_ms)
    return next_start < cue_end - safety_ms


def _recovered_decision(
    cue: SubtitleCue,
    original: MatchDecision,
    occurrence: TimedCanonicalOccurrence,
    *,
    reason: str,
) -> MatchDecision:
    return replace(
        original,
        canonical_ordinal=occurrence.ordinal,
        score=min(float(original.score), 0.90),
        action="unchanged" if cue.normalized == occurrence.normalized else "replace",
        reason=reason,
        cue_span=(cue.ordinal, cue.ordinal + 1),
        canonical_span=(occurrence.ordinal, occurrence.ordinal + 1),
        canonical_text=occurrence.text,
        output_text=occurrence.text,
        edit_operations=(),
    )


def recover_text_reviews_from_bpm_projection(
    cues: Sequence[SubtitleCue],
    canonical: Sequence[TimedCanonicalOccurrence],
    decisions: Sequence[MatchDecision],
    *,
    rate_prior_metadata_by_source: Mapping[int, Mapping[str, object]] | None = None,
) -> tuple[dict[int, str], list[MatchDecision], BpmTextRecoverySummary, list[BpmTextProjectionModel]]:
    """Recover narrowly provable review text using a validated BPM projection."""

    canonical_by_ordinal = {item.ordinal: item for item in canonical}
    decision_by_cue = {item.cue_ordinal: item for item in decisions}
    anchors = _safe_anchor_inventory(cues, canonical_by_ordinal, decisions)
    models, inliers_by_source = _build_models(
        canonical,
        anchors,
        rate_prior_metadata_by_source,
    )
    ready_models = {item.source_ordinal: item for item in models if item.status == "ready"}
    lexical_rows = _lexical_rows_by_source(canonical)
    output = dict(decision_by_cue)
    replacements: dict[int, str] = {}
    resolved = 0
    trimmed = 0

    for cue in cues:
        original = output.get(cue.ordinal)
        canonical_ordinal = _single_span(original)
        if original is None or original.action != "review" or canonical_ordinal is None:
            continue
        occurrence = canonical_by_ordinal.get(canonical_ordinal)
        if occurrence is None:
            continue
        model = ready_models.get(occurrence.source_ordinal)
        inliers = inliers_by_source.get(occurrence.source_ordinal, ())
        if model is None or not inliers:
            continue
        cue_start, _ = _cue_times(cue)
        start_delta = abs(cue_start - model.source_to_mix_ms(occurrence.anchor_time_ms))
        limit = 220 if float(original.score) < 0.20 else 450
        if start_delta > limit:
            continue
        if _is_pure_vocalization(occurrence.text):
            continue

        trimmed_target = _trim_optional_vocalization_edges(cue.text, occurrence.text)
        if trimmed_target is not None:
            decision = _recovered_decision(
                cue,
                original,
                occurrence,
                reason="bpm_projection_trims_optional_vocalization",
            )
            output[cue.ordinal] = decision
            replacements[cue.ordinal] = trimmed_target
            resolved += 1
            trimmed += 1
            continue

        if _is_pure_vocalization(cue.text):
            continue
        if _adjacent_claims_same(cue.ordinal, canonical_ordinal, output):
            continue
        if _split_continuation_risk(cue, occurrence, cues):
            continue
        if _next_lexical_starts_inside_cue(occurrence, cue, model, lexical_rows):
            continue
        if not _bracketed_or_strict_leading_edge(
            cue.ordinal,
            canonical_ordinal,
            occurrence.source_ordinal,
            inliers,
            lexical_rows,
        ):
            continue

        decision = _recovered_decision(
            cue,
            original,
            occurrence,
            reason="bpm_projection_confirms_mapped_canonical",
        )
        output[cue.ordinal] = decision
        replacements[cue.ordinal] = decision.output_text
        resolved += 1

    ordered = [output.get(item.cue_ordinal, item) for item in decisions]
    return (
        replacements,
        ordered,
        BpmTextRecoverySummary(
            resolved_review_cue_count=resolved,
            vocalization_trim_count=trimmed,
        ),
        models,
    )


def bpm_text_model_payload(models: Sequence[BpmTextProjectionModel]) -> list[dict[str, object]]:
    return [asdict(item) for item in models]
