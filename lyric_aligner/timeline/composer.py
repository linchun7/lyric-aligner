"""Compose v4 canonical occurrence timelines into final subtitle cues.

The composer only accepts already projected, unblocked canonical timelines and
never invents lyric text. Cross-track cue overlap is allowed only when every
actual pairwise intersection is fully contained by a materialized confirmed-
overlap region for that exact TrackOccurrence pair.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any, Iterable

from lyric_aligner.config import RenderConfig
from lyric_aligner.timeline.overlap import ConfirmedOverlapRegion


class TimelineComposeError(ValueError):
    """Raised when projected timelines cannot be rendered without guessing."""


@dataclass(frozen=True)
class FinalCue:
    number: int
    start_ms: int
    end_ms: int
    text: str
    occurrence_id: str
    track_id: str
    ordinal: int
    canonical_line_index: int
    timing_format: str
    end_basis: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _timeline_parts(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    result = payload.get("result")
    if not isinstance(result, dict):
        raise TimelineComposeError("canonical timeline payload has no result")

    coverage = result.get("projection_coverage")
    if coverage is not None:
        if not isinstance(coverage, dict):
            raise TimelineComposeError("canonical timeline has invalid projection_coverage")
        omitted_line_count = coverage.get("authority_omitted_line_count", 0)
        if type(omitted_line_count) is not int:
            raise TimelineComposeError(
                "canonical timeline has invalid authority_omitted_line_count"
            )
        if omitted_line_count < 0:
            raise TimelineComposeError(
                "canonical timeline has negative authority_omitted_line_count"
            )
        if omitted_line_count:
            raise TimelineComposeError(
                "canonical timeline projection coverage is incomplete; omitted canonical "
                "lines require remap/rebuild before rendering"
            )

    window = result.get("window")
    if not isinstance(window, dict):
        raise TimelineComposeError("canonical timeline has no finite occurrence window")
    try:
        start_ms = int(window["start_ms"])
        end_ms = int(window["end_ms"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TimelineComposeError("canonical timeline has invalid occurrence window") from exc
    if start_ms < 0 or end_ms <= start_ms:
        raise TimelineComposeError("canonical timeline occurrence window is invalid")
    return result, {"start_ms": start_ms, "end_ms": end_ms}


def _line_end(
    line: dict[str, Any],
    *,
    start_ms: int,
    next_start_ms: int | None,
    window_end_ms: int,
    config: RenderConfig,
) -> int:
    raw_end = line.get("mix_end_ms")
    timing_format = str(line.get("timing_format") or "")
    if raw_end is None:
        end_ms = start_ms + config.open_line_duration_ms
    else:
        try:
            end_ms = int(raw_end)
        except (TypeError, ValueError) as exc:
            raise TimelineComposeError("canonical line has invalid mix_end_ms") from exc
        if timing_format in {"enhanced_lrc", "qrc_word_timing"}:
            end_ms += config.word_timing_tail_ms

    end_ms = min(end_ms, start_ms + config.maximum_line_duration_ms, window_end_ms)
    if next_start_ms is not None:
        end_ms = min(end_ms, next_start_ms)
    return end_ms


def _repair_short_cues(
    cues: list[FinalCue],
    *,
    window_start_ms: int,
    window_end_ms: int,
    config: RenderConfig,
) -> list[FinalCue]:
    """Raise isolated projected fragments to the minimum readable duration.

    The repair never reorders text and never creates overlap. It first consumes
    real silence around the cue. If that is insufficient, it redistributes the
    adjacent cue boundaries while keeping every donor cue at or above the same
    minimum duration. Cases without enough temporal capacity remain blocked.
    """

    repaired = list(cues)
    minimum = int(config.minimum_cue_duration_ms)
    for index in range(len(repaired)):
        cue = repaired[index]
        duration = cue.end_ms - cue.start_ms
        if duration >= minimum:
            continue

        needed = minimum - duration
        previous = repaired[index - 1] if index > 0 else None
        following = repaired[index + 1] if index + 1 < len(repaired) else None

        left_limit = previous.end_ms if previous is not None else window_start_ms
        right_limit = following.start_ms if following is not None else window_end_ms
        left_gap = max(0, cue.start_ms - left_limit)
        right_gap = max(0, right_limit - cue.end_ms)

        use_right_gap = min(needed, right_gap)
        cue = replace(cue, end_ms=cue.end_ms + use_right_gap)
        needed -= use_right_gap
        use_left_gap = min(needed, left_gap)
        cue = replace(cue, start_ms=cue.start_ms - use_left_gap)
        needed -= use_left_gap

        if needed:
            previous_capacity = (
                max(0, previous.end_ms - previous.start_ms - minimum)
                if previous is not None
                else 0
            )
            following_capacity = (
                max(0, following.end_ms - following.start_ms - minimum)
                if following is not None
                else 0
            )
            if previous_capacity + following_capacity < needed:
                raise TimelineComposeError(
                    f"canonical cue too short in {cue.occurrence_id}: "
                    f"line={cue.canonical_line_index}, duration={duration}ms; "
                    "adjacent cues cannot safely donate enough time"
                )

            take_previous = min(previous_capacity, needed // 2)
            take_following = min(following_capacity, needed - take_previous)
            remaining = needed - take_previous - take_following
            if remaining:
                extra_previous = min(previous_capacity - take_previous, remaining)
                take_previous += extra_previous
                remaining -= extra_previous
            if remaining:
                take_following += remaining

            if take_previous and previous is not None:
                new_boundary = cue.start_ms - take_previous
                repaired[index - 1] = replace(
                    previous,
                    end_ms=new_boundary,
                    end_basis="minimum_duration_neighbor_adjustment",
                )
                cue = replace(cue, start_ms=new_boundary)
            if take_following and following is not None:
                new_boundary = cue.end_ms + take_following
                repaired[index + 1] = replace(following, start_ms=new_boundary)
                cue = replace(cue, end_ms=new_boundary)

        repaired[index] = replace(cue, end_basis="minimum_duration_rebalanced")

    for left, right in zip(repaired, repaired[1:]):
        if left.end_ms > right.start_ms:
            raise TimelineComposeError(
                f"minimum-duration repair created overlap in {left.occurrence_id}"
            )
        if left.end_ms - left.start_ms < minimum:
            raise TimelineComposeError(
                f"minimum-duration repair left a short cue in {left.occurrence_id}"
            )
    if repaired and repaired[-1].end_ms - repaired[-1].start_ms < minimum:
        raise TimelineComposeError(
            f"minimum-duration repair left a short cue in {repaired[-1].occurrence_id}"
        )
    return repaired


def _compose_one(payload: dict[str, Any], config: RenderConfig) -> list[FinalCue]:
    result, window = _timeline_parts(payload)
    occurrence_id = str(result.get("occurrence_id") or payload.get("occurrence_id") or "")
    track_id = str(result.get("track_id") or payload.get("track_id") or "")
    if not occurrence_id or not track_id:
        raise TimelineComposeError("canonical timeline is missing occurrence/track identity")
    try:
        ordinal = int(result["ordinal"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TimelineComposeError("canonical timeline is missing ordinal") from exc

    rows = result.get("lines")
    if not isinstance(rows, list):
        raise TimelineComposeError("canonical timeline lines must be a list")
    ordered = sorted(
        rows,
        key=lambda item: (
            int(item.get("mix_start_ms", -1)),
            int(item.get("canonical_line_index", -1)),
        ),
    )

    provisional: list[FinalCue] = []
    for position, line in enumerate(ordered):
        text = str(line.get("text") or "").strip()
        if not text:
            raise TimelineComposeError(f"blank canonical text in {occurrence_id}")
        try:
            projected_start = int(line["mix_start_ms"])
            line_index = int(line["canonical_line_index"])
        except (KeyError, TypeError, ValueError) as exc:
            raise TimelineComposeError(
                f"invalid projected line coordinates in {occurrence_id}"
            ) from exc

        start_ms = max(projected_start, window["start_ms"])
        if start_ms >= window["end_ms"]:
            continue

        next_start_ms: int | None = None
        if position + 1 < len(ordered):
            try:
                next_projected = int(ordered[position + 1]["mix_start_ms"])
            except (KeyError, TypeError, ValueError) as exc:
                raise TimelineComposeError(
                    f"invalid next-line coordinate in {occurrence_id}"
                ) from exc
            next_start_ms = max(next_projected, window["start_ms"])

        end_ms = _line_end(
            line,
            start_ms=start_ms,
            next_start_ms=next_start_ms,
            window_end_ms=window["end_ms"],
            config=config,
        )
        if (
            end_ms - start_ms < config.minimum_cue_duration_ms
            and projected_start < window["start_ms"]
        ):
            # The lyric starts outside this occurrence's authoritative window.
            # Without confirmed-overlap recomposition, showing only the tiny
            # clipped tail is less faithful than omitting the fragment.
            continue

        provisional.append(
            FinalCue(
                number=0,
                start_ms=start_ms,
                end_ms=end_ms,
                text=text,
                occurrence_id=occurrence_id,
                track_id=track_id,
                ordinal=ordinal,
                canonical_line_index=line_index,
                timing_format=str(line.get("timing_format") or "line_lrc"),
                end_basis=str(line.get("end_basis") or "unknown"),
            )
        )

    provisional = _repair_short_cues(
        provisional,
        window_start_ms=window["start_ms"],
        window_end_ms=window["end_ms"],
        config=config,
    )
    for left, right in zip(provisional, provisional[1:]):
        if right.start_ms < left.end_ms:
            raise TimelineComposeError(
                f"same-occurrence canonical cues overlap in {occurrence_id}: "
                f"line {left.canonical_line_index} -> {right.canonical_line_index}"
            )
    return provisional


def _regions(
    values: Iterable[ConfirmedOverlapRegion | dict[str, Any]],
) -> tuple[ConfirmedOverlapRegion, ...]:
    output: list[ConfirmedOverlapRegion] = []
    for value in values:
        if isinstance(value, ConfirmedOverlapRegion):
            output.append(value)
            continue
        if not isinstance(value, dict):
            raise TimelineComposeError("confirmed overlap region must be an object")
        try:
            output.append(
                ConfirmedOverlapRegion(
                    candidate_id=str(value["candidate_id"]),
                    left_occurrence_id=str(value["left_occurrence_id"]),
                    right_occurrence_id=str(value["right_occurrence_id"]),
                    start_ms=int(value["start_ms"]),
                    end_ms=int(value["end_ms"]),
                    issue_id=str(value.get("issue_id") or ""),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise TimelineComposeError("confirmed overlap region is malformed") from exc
    return tuple(output)


def _cross_track_overlap_is_confirmed(
    left: FinalCue,
    right: FinalCue,
    regions: tuple[ConfirmedOverlapRegion, ...],
) -> bool:
    overlap_start = max(left.start_ms, right.start_ms)
    overlap_end = min(left.end_ms, right.end_ms)
    if overlap_end <= overlap_start:
        return True
    pair = {left.occurrence_id, right.occurrence_id}
    for region in regions:
        if pair != {region.left_occurrence_id, region.right_occurrence_id}:
            continue
        if overlap_start >= region.start_ms and overlap_end <= region.end_ms:
            return True
    return False


def compose_canonical_timelines(
    timeline_payloads: Iterable[dict[str, Any]],
    *,
    config: RenderConfig,
    confirmed_overlap_regions: Iterable[ConfirmedOverlapRegion | dict[str, Any]] = (),
) -> list[FinalCue]:
    """Compose occurrence timelines into a deterministic final cue stream."""

    cues: list[FinalCue] = []
    seen_occurrences: set[str] = set()
    for payload in timeline_payloads:
        result = payload.get("result") or {}
        occurrence_id = str(result.get("occurrence_id") or payload.get("occurrence_id") or "")
        if not occurrence_id:
            raise TimelineComposeError("timeline payload has no occurrence_id")
        if occurrence_id in seen_occurrences:
            raise TimelineComposeError(f"duplicate occurrence timeline: {occurrence_id}")
        seen_occurrences.add(occurrence_id)
        cues.extend(_compose_one(payload, config))

    if not cues:
        raise TimelineComposeError("no canonical cues available for final rendering")

    regions = _regions(confirmed_overlap_regions)
    ordered = sorted(
        cues,
        key=lambda cue: (
            cue.start_ms,
            cue.ordinal,
            cue.canonical_line_index,
            cue.end_ms,
        ),
    )
    for index, left in enumerate(ordered):
        for right in ordered[index + 1 :]:
            if right.start_ms >= left.end_ms:
                break
            if right.occurrence_id == left.occurrence_id:
                continue
            if not _cross_track_overlap_is_confirmed(left, right, regions):
                raise TimelineComposeError(
                    "cross-track cue overlap is outside confirmed-overlap evidence: "
                    f"{left.occurrence_id} / {right.occurrence_id}"
                )

    return [
        FinalCue(
            number=index,
            start_ms=cue.start_ms,
            end_ms=cue.end_ms,
            text=cue.text,
            occurrence_id=cue.occurrence_id,
            track_id=cue.track_id,
            ordinal=cue.ordinal,
            canonical_line_index=cue.canonical_line_index,
            timing_format=cue.timing_format,
            end_basis=cue.end_basis,
        )
        for index, cue in enumerate(ordered, start=1)
    ]
