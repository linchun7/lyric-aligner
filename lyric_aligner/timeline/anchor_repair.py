"""No-audio anchor timeline repair for mostly-correct editor subtitles.

Smart mode uses canonical lyric identity plus timed LRC structure to identify
isolated Jianying timing outliers. It intentionally does not read audio.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from typing import Mapping, Sequence

from lyric_aligner.text.canonical_lyrics import (
    CanonicalLyricError,
    CanonicalToken,
    parse_canonical_lyrics,
)
from lyric_aligner.text_repair import (
    DEFAULT_AUTO_THRESHOLD,
    CanonicalLine as RepairCanonicalLine,
    SubtitleCue,
    _normalize_for_match,
    build_repair_plan_v2,
    parse_srt_text,
    render_repaired_srt,
)


@dataclass(frozen=True)
class TimedCanonicalOccurrence:
    ordinal: int
    source: str
    source_ordinal: int
    time_ms: int
    text: str
    normalized: str
    tokens: tuple[CanonicalToken, ...] = ()
    timing_format: str = "line_lrc"

    @property
    def anchor_time_ms(self) -> int:
        """Prefer the first word/token onset when it is plausibly line-local."""
        if self.tokens:
            first = self.tokens[0].start_ms
            if self.time_ms - 250 <= first <= self.time_ms + 2500:
                return first
        return self.time_ms

    @property
    def has_word_timing(self) -> bool:
        return bool(self.tokens)


@dataclass(frozen=True)
class AnchorObservation:
    cue_ordinal: int
    canonical_ordinal: int
    source_ordinal: int
    source: str
    grade: str
    mix_start_ms: int
    source_time_ms: int
    score: float
    has_word_timing: bool


@dataclass(frozen=True)
class SongTimingModel:
    source_ordinal: int
    source: str
    rate: float
    offset_ms: float
    rate_source: str
    anchor_count: int
    inlier_count: int
    median_abs_residual_ms: float
    inlier_fraction: float
    status: str
    word_timing_anchor_count: int

    def source_to_mix_ms(self, source_ms: int) -> int:
        return int(round((source_ms - self.offset_ms) / self.rate))


@dataclass(frozen=True)
class TimingDecision:
    cue_ordinal: int
    source_ordinal: int | None
    canonical_ordinal: int | None
    anchor_grade: str
    action: str
    reason: str
    old_start_ms: int
    old_end_ms: int
    proposed_start_ms: int | None
    proposed_end_ms: int | None
    residual_ms: float | None
    model_status: str | None
    evidence: tuple[str, ...] = ()


def parse_timed_canonical_files(
    paths: Sequence[Path],
) -> tuple[list[TimedCanonicalOccurrence], list[RepairCanonicalLine]]:
    """Parse timed canonical lyrics for Smart mode.

    Smart mode intentionally requires timed canonical lyrics. Standard mode
    remains the fallback for untimed TXT/LRC inputs.
    """

    timed: list[TimedCanonicalOccurrence] = []
    repair: list[RepairCanonicalLine] = []
    for source_ordinal, path in enumerate(paths):
        lines = parse_canonical_lyrics(path)
        for line in lines:
            normalized = _normalize_for_match(line.text)
            if not normalized:
                continue
            ordinal = len(timed)
            timed.append(
                TimedCanonicalOccurrence(
                    ordinal=ordinal,
                    source=path.name,
                    source_ordinal=source_ordinal,
                    time_ms=line.time_ms,
                    text=line.text,
                    normalized=normalized,
                    tokens=line.tokens,
                    timing_format=line.timing_format,
                )
            )
            repair.append(
                RepairCanonicalLine(
                    ordinal=ordinal,
                    source=path.name,
                    text=line.text,
                    normalized=normalized,
                    source_ordinal=source_ordinal,
                )
            )
    if not timed:
        raise CanonicalLyricError("Smart mode requires timestamped canonical lyrics")
    return timed, repair


def _clock_ms(value: str) -> int:
    value = value.replace(".", ",")
    hour, minute, rest = value.split(":")
    second, millis = rest.split(",")
    return (((int(hour) * 60) + int(minute)) * 60 + int(second)) * 1000 + int(millis)


def _cue_times(cue: SubtitleCue) -> tuple[int, int]:
    left, right = cue.timing.split("-->", 1)
    start = _clock_ms(left.strip())
    end_token = right.strip().split()[0]
    return start, _clock_ms(end_token)


def _format_clock(ms: int) -> str:
    ms = max(0, int(ms))
    hour, rem = divmod(ms, 3_600_000)
    minute, rem = divmod(rem, 60_000)
    second, millis = divmod(rem, 1000)
    return f"{hour:02d}:{minute:02d}:{second:02d},{millis:03d}"


def _replace_timing_line(line: str, start_ms: int, end_ms: int) -> str:
    left, right = line.split("-->", 1)
    left_ws = left[: len(left) - len(left.lstrip())]
    around_left = left[len(left.rstrip()) :]
    right_stripped = right.lstrip()
    right_leading = right[: len(right) - len(right_stripped)]
    end_token = right_stripped.split(maxsplit=1)
    suffix = f" {end_token[1]}" if len(end_token) == 2 else ""
    return (
        f"{left_ws}{_format_clock(start_ms)}{around_left}-->"
        f"{right_leading}{_format_clock(end_ms)}{suffix}"
    )


def _decision_grade(
    decision: Mapping[str, object],
    cue: SubtitleCue,
    occurrence: TimedCanonicalOccurrence,
    *,
    canonical_count: int,
    cue_count: int,
) -> str:
    cue_span = decision.get("cue_span")
    canonical_span = decision.get("canonical_span")
    if cue_span is None or canonical_span is None:
        return "C"
    if list(cue_span) != [cue.ordinal, cue.ordinal + 1]:
        return "C"
    if list(canonical_span) != [occurrence.ordinal, occurrence.ordinal + 1]:
        return "C"
    score = float(decision.get("score", 0.0))
    action = str(decision.get("action", "review"))
    if action == "review":
        return "C"
    exact = cue.normalized == occurrence.normalized
    if (
        exact
        and score >= 0.995
        and canonical_count == 1
        and cue_count == 1
        and action == "unchanged"
    ):
        return "A"
    if score >= 0.92 and canonical_count == 1 and action in {"unchanged", "replace"}:
        return "B"
    return "C"


def _build_observations(
    cues: Sequence[SubtitleCue],
    canonical: Sequence[TimedCanonicalOccurrence],
    decisions: Sequence[Mapping[str, object]],
) -> tuple[list[AnchorObservation], dict[int, AnchorObservation]]:
    canonical_by_ordinal = {line.ordinal: line for line in canonical}
    decision_by_cue = {int(item["cue_ordinal"]): item for item in decisions}

    canonical_counts: dict[int, Counter[str]] = defaultdict(Counter)
    for line in canonical:
        canonical_counts[line.source_ordinal][line.normalized] += 1

    cue_source: dict[int, int] = {}
    for item in decisions:
        span = item.get("canonical_span")
        if not span or len(span) != 2 or int(span[1]) - int(span[0]) != 1:
            continue
        occurrence = canonical_by_ordinal.get(int(span[0]))
        if occurrence is not None:
            cue_source[int(item["cue_ordinal"])] = occurrence.source_ordinal

    cue_counts: dict[int, Counter[str]] = defaultdict(Counter)
    for cue in cues:
        source_ordinal = cue_source.get(cue.ordinal)
        if source_ordinal is not None:
            cue_counts[source_ordinal][cue.normalized] += 1

    observations: list[AnchorObservation] = []
    by_cue: dict[int, AnchorObservation] = {}
    for cue in cues:
        decision = decision_by_cue.get(cue.ordinal)
        if decision is None:
            continue
        span = decision.get("canonical_span")
        if not span or len(span) != 2 or int(span[1]) - int(span[0]) != 1:
            continue
        occurrence = canonical_by_ordinal.get(int(span[0]))
        if occurrence is None:
            continue
        grade = _decision_grade(
            decision,
            cue,
            occurrence,
            canonical_count=canonical_counts[occurrence.source_ordinal][occurrence.normalized],
            cue_count=cue_counts[occurrence.source_ordinal][cue.normalized],
        )
        start_ms, _ = _cue_times(cue)
        observation = AnchorObservation(
            cue_ordinal=cue.ordinal,
            canonical_ordinal=occurrence.ordinal,
            source_ordinal=occurrence.source_ordinal,
            source=occurrence.source,
            grade=grade,
            mix_start_ms=start_ms,
            source_time_ms=occurrence.anchor_time_ms,
            score=float(decision.get("score", 0.0)),
            has_word_timing=occurrence.has_word_timing,
        )
        observations.append(observation)
        by_cue[cue.ordinal] = observation
    return observations, by_cue


def _pairwise_rate(anchors: Sequence[AnchorObservation]) -> float | None:
    slopes: list[float] = []
    for index, left in enumerate(anchors):
        for right in anchors[index + 1 :]:
            mix_delta = right.mix_start_ms - left.mix_start_ms
            source_delta = right.source_time_ms - left.source_time_ms
            if mix_delta < 3000 or source_delta <= 0:
                continue
            slope = source_delta / mix_delta
            if 0.5 <= slope <= 2.0:
                slopes.append(slope)
    return float(median(slopes)) if slopes else None


def _fit_model(
    anchors: Sequence[AnchorObservation],
    *,
    rate_prior: float | None,
    source_ordinal: int,
    source: str,
    min_anchors: int = 4,
    inlier_threshold_ms: int = 750,
) -> SongTimingModel:
    if len(anchors) < min_anchors:
        return SongTimingModel(
            source_ordinal,
            source,
            1.0,
            0.0,
            "none",
            len(anchors),
            0,
            float("inf"),
            0.0,
            "insufficient_anchors",
            0,
        )

    estimated = _pairwise_rate(anchors)
    if rate_prior is not None:
        if not 0.5 <= rate_prior <= 2.0:
            return SongTimingModel(
                source_ordinal,
                source,
                rate_prior,
                0.0,
                "invalid_prior",
                len(anchors),
                0,
                float("inf"),
                0.0,
                "invalid_rate_prior",
                0,
            )
        rate = rate_prior
        rate_source = "rate_prior"
    elif estimated is not None:
        rate = estimated
        rate_source = "robust_anchor_estimate"
    else:
        return SongTimingModel(
            source_ordinal,
            source,
            1.0,
            0.0,
            "none",
            len(anchors),
            0,
            float("inf"),
            0.0,
            "insufficient_rate_evidence",
            0,
        )

    offset = float(
        median([item.source_time_ms - rate * item.mix_start_ms for item in anchors])
    )
    residuals = [
        item.source_time_ms - (offset + rate * item.mix_start_ms)
        for item in anchors
    ]
    inliers = [
        item
        for item, residual in zip(anchors, residuals)
        if abs(residual) <= inlier_threshold_ms
    ]

    if rate_prior is None and len(inliers) >= min_anchors:
        refined = _pairwise_rate(inliers)
        if refined is not None:
            rate = refined
            offset = float(
                median(
                    [item.source_time_ms - rate * item.mix_start_ms for item in inliers]
                )
            )
            residuals = [
                item.source_time_ms - (offset + rate * item.mix_start_ms)
                for item in anchors
            ]
            inliers = [
                item
                for item, residual in zip(anchors, residuals)
                if abs(residual) <= inlier_threshold_ms
            ]

    med = float(median([abs(value) for value in residuals]))
    fraction = len(inliers) / len(anchors)
    status = "ready"
    if med > 450 or fraction < 0.70:
        status = "unstable"
    if rate_prior is not None and estimated is not None:
        relative = abs(estimated - rate_prior) / rate_prior
        if relative > 0.03:
            status = "rate_prior_conflict"

    return SongTimingModel(
        source_ordinal=source_ordinal,
        source=source,
        rate=rate,
        offset_ms=offset,
        rate_source=rate_source,
        anchor_count=len(anchors),
        inlier_count=len(inliers),
        median_abs_residual_ms=med,
        inlier_fraction=fraction,
        status=status,
        word_timing_anchor_count=sum(item.has_word_timing for item in anchors),
    )


def _support_counts(
    candidate: AnchorObservation,
    anchors: Sequence[AnchorObservation],
) -> tuple[int, int]:
    left = sum(
        item.canonical_ordinal < candidate.canonical_ordinal
        and item.cue_ordinal < candidate.cue_ordinal
        for item in anchors
    )
    right = sum(
        item.canonical_ordinal > candidate.canonical_ordinal
        and item.cue_ordinal > candidate.cue_ordinal
        for item in anchors
    )
    return left, right


def _structurally_safe_shift(
    cues: Sequence[SubtitleCue],
    cue_ordinal: int,
    new_start: int,
    new_end: int,
) -> bool:
    if new_start < 0 or new_end <= new_start:
        return False
    if cue_ordinal > 0:
        prev_start, _ = _cue_times(cues[cue_ordinal - 1])
        if new_start <= prev_start:
            return False
    if cue_ordinal + 1 < len(cues):
        next_start, next_end = _cue_times(cues[cue_ordinal + 1])
        if new_start >= next_start or new_end >= next_end:
            return False
    return True


def build_anchor_timing_plan(
    cues: Sequence[SubtitleCue],
    canonical: Sequence[TimedCanonicalOccurrence],
    text_decisions: Sequence[Mapping[str, object]],
    *,
    rate_prior_by_source: Mapping[int, float] | None = None,
    preserve_tolerance_ms: int = 350,
    repair_threshold_ms: int = 900,
    max_auto_shift_ms: int = 8000,
) -> tuple[list[TimingDecision], list[SongTimingModel]]:
    """Build conservative no-audio timing repairs.

    Only A-grade 1:1 exact anchors are eligible for automatic timing changes in
    v1. B/C mappings can be diagnosed but are never auto-written.
    """

    rate_prior_by_source = rate_prior_by_source or {}
    observations, by_cue = _build_observations(cues, canonical, text_decisions)
    by_source: dict[int, list[AnchorObservation]] = defaultdict(list)
    source_names: dict[int, str] = {}
    for item in observations:
        by_source[item.source_ordinal].append(item)
        source_names[item.source_ordinal] = item.source

    models: dict[int, SongTimingModel] = {}
    for source_ordinal, items in by_source.items():
        a_anchors = [item for item in items if item.grade == "A"]
        models[source_ordinal] = _fit_model(
            a_anchors,
            rate_prior=rate_prior_by_source.get(source_ordinal),
            source_ordinal=source_ordinal,
            source=source_names[source_ordinal],
        )

    decisions: list[TimingDecision] = []
    for cue in cues:
        old_start, old_end = _cue_times(cue)
        observation = by_cue.get(cue.ordinal)
        if observation is None:
            decisions.append(
                TimingDecision(
                    cue.ordinal,
                    None,
                    None,
                    "C",
                    "preserve",
                    "no_unique_timed_canonical_mapping",
                    old_start,
                    old_end,
                    None,
                    None,
                    None,
                    None,
                )
            )
            continue
        model = models.get(observation.source_ordinal)
        if model is None or model.status != "ready":
            decisions.append(
                TimingDecision(
                    cue.ordinal,
                    observation.source_ordinal,
                    observation.canonical_ordinal,
                    observation.grade,
                    "preserve",
                    "timing_model_not_ready",
                    old_start,
                    old_end,
                    None,
                    None,
                    None,
                    model.status if model else "missing",
                )
            )
            continue

        a_anchors = [
            item
            for item in by_source[observation.source_ordinal]
            if item.grade == "A" and item.cue_ordinal != cue.ordinal
        ]
        loo_model = _fit_model(
            a_anchors,
            rate_prior=rate_prior_by_source.get(observation.source_ordinal),
            source_ordinal=observation.source_ordinal,
            source=observation.source,
        )
        judge_model = (
            loo_model
            if observation.grade == "A" and loo_model.status == "ready"
            else model
        )

        proposed_start = judge_model.source_to_mix_ms(observation.source_time_ms)
        residual = float(old_start - proposed_start)
        if abs(residual) <= preserve_tolerance_ms:
            decisions.append(
                TimingDecision(
                    cue.ordinal,
                    observation.source_ordinal,
                    observation.canonical_ordinal,
                    observation.grade,
                    "preserve",
                    "timing_matches_anchor_model",
                    old_start,
                    old_end,
                    None,
                    None,
                    residual,
                    judge_model.status,
                    ("canonical_identity", "affine_model"),
                )
            )
            continue
        if abs(residual) < repair_threshold_ms:
            decisions.append(
                TimingDecision(
                    cue.ordinal,
                    observation.source_ordinal,
                    observation.canonical_ordinal,
                    observation.grade,
                    "preserve",
                    "timing_deviation_below_repair_threshold",
                    old_start,
                    old_end,
                    proposed_start,
                    proposed_start + (old_end - old_start),
                    residual,
                    judge_model.status,
                    ("canonical_identity", "affine_model"),
                )
            )
            continue

        candidate_anchors = [
            item
            for item in by_source[observation.source_ordinal]
            if item.grade == "A" and item.cue_ordinal != cue.ordinal
        ]
        left_support, right_support = _support_counts(observation, candidate_anchors)
        rate_prior = rate_prior_by_source.get(observation.source_ordinal)
        bilateral = left_support >= 2 and right_support >= 2
        edge_supported = (
            rate_prior is not None
            and (
                (left_support >= 3 and right_support == 0)
                or (right_support >= 3 and left_support == 0)
            )
        )

        evidence = ["canonical_identity", "affine_model", "leave_one_out"]
        if bilateral:
            evidence.append("bilateral_anchor_support")
        elif edge_supported:
            evidence.extend(("one_sided_anchor_support", "rate_prior"))
        if observation.has_word_timing:
            evidence.append("word_timing")

        new_start = proposed_start
        new_end = proposed_start + (old_end - old_start)
        if observation.grade != "A":
            action = "review"
            reason = "non_A_identity_not_auto_repairable"
        elif judge_model.status != "ready":
            action = "review"
            reason = "independent_model_not_ready"
        elif not (bilateral or edge_supported):
            action = "review"
            reason = "insufficient_independent_neighbor_support"
        elif abs(residual) > max_auto_shift_ms:
            action = "review"
            reason = "shift_exceeds_auto_limit"
        elif not _structurally_safe_shift(cues, cue.ordinal, new_start, new_end):
            action = "review"
            reason = "proposed_shift_breaks_timeline_structure"
        else:
            action = "repair"
            reason = "multi_evidence_timing_outlier"

        decisions.append(
            TimingDecision(
                cue.ordinal,
                observation.source_ordinal,
                observation.canonical_ordinal,
                observation.grade,
                action,
                reason,
                old_start,
                old_end,
                new_start,
                new_end,
                residual,
                judge_model.status,
                tuple(evidence),
            )
        )

    return decisions, [models[key] for key in sorted(models)]


def apply_timing_decisions(
    srt_text: str,
    decisions: Sequence[TimingDecision],
) -> str:
    parts, cues = parse_srt_text(srt_text)
    by_cue = {
        item.cue_ordinal: item
        for item in decisions
        if item.action == "repair"
        and item.proposed_start_ms is not None
        and item.proposed_end_ms is not None
    }
    output = list(parts)
    for cue in cues:
        decision = by_cue.get(cue.ordinal)
        if decision is None:
            continue
        block = parts[cue.raw_block_index]
        line_ending = "\r\n" if "\r\n" in block else "\n"
        trailing = line_ending if block.endswith(line_ending) else ""
        rows = block.splitlines()
        rows[1] = _replace_timing_line(
            rows[1],
            int(decision.proposed_start_ms),
            int(decision.proposed_end_ms),
        )
        output[cue.raw_block_index] = line_ending.join(rows) + trailing
    return "".join(output)


def smart_repair_srt_text(
    source_text: str,
    timed_canonical: Sequence[TimedCanonicalOccurrence],
    repair_canonical: Sequence[RepairCanonicalLine],
    *,
    auto_threshold: float = DEFAULT_AUTO_THRESHOLD,
    rate_prior_by_source: Mapping[int, float] | None = None,
) -> tuple[str, dict[str, object]]:
    """Run Standard text repair followed by Smart no-audio timing repair."""

    parts, cues = parse_srt_text(source_text)
    replacements, text_decisions, operations = build_repair_plan_v2(
        cues,
        repair_canonical,
        auto_threshold=auto_threshold,
    )
    text_repaired = render_repaired_srt(parts, cues, replacements)
    text_decision_payload = [
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
    timing_decisions, models = build_anchor_timing_plan(
        cues,
        timed_canonical,
        text_decision_payload,
        rate_prior_by_source=rate_prior_by_source,
    )
    rendered = apply_timing_decisions(text_repaired, timing_decisions)

    report: dict[str, object] = {
        "schema_version": "smart-1.0",
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
        "text_review_count": sum(item.action == "review" for item in text_decisions),
        "timing_repair_count": sum(item.action == "repair" for item in timing_decisions),
        "timing_review_count": sum(item.action == "review" for item in timing_decisions),
        "status": (
            "review_required"
            if any(item.action == "review" for item in timing_decisions)
            or any(item.action == "review" for item in text_decisions)
            else "ready"
        ),
        "models": [asdict(item) for item in models],
        "timing_decisions": [asdict(item) for item in timing_decisions],
        "text_decisions": text_decision_payload,
        "alignment_span_count": sum(item.kind == "match" for item in operations),
    }
    return rendered, report
