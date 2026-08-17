"""Compose v4 canonical occurrence timelines into final subtitle cues.

The composer is deliberately conservative.  It only accepts already projected,
unblocked canonical timelines and never invents lyric text.  Cross-track overlap
requires an explicit future review-decision path; until then an unexpected cue
overlap is a hard error rather than an implicit merge.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

from lyric_aligner.config import RenderConfig


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
        if end_ms - start_ms < config.minimum_cue_duration_ms:
            raise TimelineComposeError(
                f"canonical cue too short in {occurrence_id}: "
                f"line={line_index}, duration={end_ms - start_ms}ms"
            )

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

    for left, right in zip(provisional, provisional[1:]):
        if right.start_ms < left.end_ms:
            raise TimelineComposeError(
                f"same-occurrence canonical cues overlap in {occurrence_id}: "
                f"line {left.canonical_line_index} -> {right.canonical_line_index}"
            )
    return provisional


def compose_canonical_timelines(
    timeline_payloads: Iterable[dict[str, Any]],
    *,
    config: RenderConfig,
) -> list[FinalCue]:
    """Compose all occurrence timelines into a deterministic final cue stream."""

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

    ordered = sorted(
        cues,
        key=lambda cue: (
            cue.start_ms,
            cue.ordinal,
            cue.canonical_line_index,
            cue.end_ms,
        ),
    )
    for left, right in zip(ordered, ordered[1:]):
        if right.start_ms < left.end_ms and right.occurrence_id != left.occurrence_id:
            raise TimelineComposeError(
                "cross-track cue overlap requires an explicit confirmed-overlap decision: "
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
