"""Canonical lyric parsing with explicit same-timestamp original selection.

This is the v4 lyric truth layer. Downstream stages must not pick the first
same-timestamp LRC row or re-run language heuristics. Asset resolution decides
the original once; this parser consumes that decision while preserving
Enhanced-LRC/QRC token timing when available.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from lyric_aligner.io.text import read_task_text
from lyric_aligner.text.normalization import META_RE, clean_text

LRC_RE = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\](.*)")
ENHANCED_TOKEN_RE = re.compile(r"<(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?>")
QRC_LINE_RE = re.compile(r"^\[(\d+),(\d+)\](.*)$")
QRC_TOKEN_RE = re.compile(r"(.+?)\((\d+),(\d+)\)")


class CanonicalLyricError(ValueError):
    """Raised when canonical lyric truth cannot be reconstructed safely."""


@dataclass(frozen=True)
class CanonicalToken:
    text: str
    start_ms: int
    end_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CanonicalLine:
    index: int
    time_ms: int
    text: str
    tokens: tuple[CanonicalToken, ...] = ()
    timing_format: str = "line_lrc"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["tokens"] = [item.to_dict() for item in self.tokens]
        return payload


def _fraction_ms(value: str | None) -> int:
    value = value or "0"
    if len(value) == 1:
        return int(value) * 100
    if len(value) == 2:
        return int(value) * 10
    return int(value[:3])


def timestamp_ms(minute: str, second: str, fraction: str | None) -> int:
    return (int(minute) * 60 + int(second)) * 1000 + _fraction_ms(fraction)


def _enhanced(body: str) -> tuple[str, tuple[CanonicalToken, ...]]:
    markers = list(ENHANCED_TOKEN_RE.finditer(body))
    if not markers:
        return clean_text(body), ()
    pieces: list[str] = []
    tokens: list[CanonicalToken] = []
    if markers[0].start() > 0:
        pieces.append(body[: markers[0].start()])
    for position, marker in enumerate(markers):
        end = markers[position + 1].start() if position + 1 < len(markers) else len(body)
        raw_text = body[marker.end() : end]
        pieces.append(raw_text)
        text = clean_text(raw_text)
        if not text:
            continue
        start = timestamp_ms(*marker.groups())
        next_start = (
            timestamp_ms(*markers[position + 1].groups())
            if position + 1 < len(markers)
            else None
        )
        tokens.append(CanonicalToken(text, start, next_start))
    return clean_text("".join(pieces)), tuple(tokens)


def _qrc(
    line_start_ms: int,
    line_duration_ms: int,
    body: str,
) -> tuple[str, tuple[CanonicalToken, ...]]:
    matches = list(QRC_TOKEN_RE.finditer(body))
    if not matches:
        return clean_text(re.sub(r"\(\d+,\d+\)", "", body)), ()
    raw_starts = [int(match.group(2)) for match in matches]
    relative = bool(
        raw_starts
        and min(raw_starts) < max(0, line_start_ms - 500)
        and max(raw_starts) <= line_duration_ms + 500
    )
    pieces: list[str] = []
    tokens: list[CanonicalToken] = []
    previous_start = -1
    for match in matches:
        raw_text = match.group(1)
        text = clean_text(raw_text)
        if not text:
            continue
        raw_start = int(match.group(2))
        duration = int(match.group(3))
        start = line_start_ms + raw_start if relative else raw_start
        if start < previous_start:
            raise CanonicalLyricError("QRC token timestamps move backward")
        previous_start = start
        pieces.append(raw_text)
        tokens.append(
            CanonicalToken(text, start, start + duration if duration > 0 else None)
        )
    return clean_text("".join(pieces)), tuple(tokens)


def _select_index(
    start: int,
    candidates: list,
    *,
    original_index_by_timestamp: dict[int, int],
    lexical_predicate,
) -> int:
    selected_index = original_index_by_timestamp.get(start)
    if selected_index is None:
        lexical = [index for index, item in enumerate(candidates) if lexical_predicate(item)]
        if len(lexical) != 1:
            raise CanonicalLyricError(
                f"canonical original is ambiguous at {start}ms; consume TrackAsset selection"
            )
        selected_index = lexical[0]
    if selected_index < 0 or selected_index >= len(candidates):
        raise CanonicalLyricError(
            f"canonical alternative index {selected_index} is out of range at {start}ms"
        )
    return selected_index


def parse_canonical_lyrics(
    path: Path,
    *,
    original_index_by_timestamp: dict[int, int] | None = None,
) -> list[CanonicalLine]:
    """Parse canonical lines without ever guessing among alternatives.

    ``original_index_by_timestamp`` uses the exact alternative index emitted by
    ``track_assets.json``. When a timestamp has multiple lexical alternatives
    and no explicit selection is supplied, parsing fails closed.
    """

    original_index_by_timestamp = original_index_by_timestamp or {}
    lrc_groups: dict[int, list[tuple[str, str, tuple[CanonicalToken, ...], str]]] = {}
    qrc_groups: dict[int, list[tuple[str, tuple[CanonicalToken, ...]]]] = {}

    for line_number, raw in enumerate(read_task_text(path).splitlines(), start=1):
        stripped = raw.strip()
        if not stripped:
            continue
        qrc_match = QRC_LINE_RE.match(stripped)
        if qrc_match:
            start = int(qrc_match.group(1))
            duration = int(qrc_match.group(2))
            text, tokens = _qrc(start, duration, qrc_match.group(3))
            if text:
                qrc_groups.setdefault(start, []).append((text, tokens))
            continue

        match = LRC_RE.match(stripped)
        if not match:
            continue
        minute, second, fraction, body = match.groups()
        try:
            start = timestamp_ms(minute, second, fraction)
        except ValueError as exc:
            raise CanonicalLyricError(
                f"invalid LRC timestamp at line {line_number}"
            ) from exc
        text, tokens = _enhanced(body.strip())
        if not text:
            continue
        lrc_groups.setdefault(start, []).append(
            (body.strip(), text, tokens, "enhanced_lrc" if tokens else "line_lrc")
        )

    result: list[CanonicalLine] = []
    timestamps = sorted(set(lrc_groups) | set(qrc_groups))
    for start in timestamps:
        if start in qrc_groups:
            candidates = qrc_groups[start]
            selected_index = _select_index(
                start,
                candidates,
                original_index_by_timestamp=original_index_by_timestamp,
                lexical_predicate=lambda item: bool(item[0]) and not META_RE.match(item[0]),
            )
            text, tokens = candidates[selected_index]
            if not text or META_RE.match(text):
                raise CanonicalLyricError(
                    f"canonical selection points to metadata/blank QRC text at {start}ms"
                )
            result.append(
                CanonicalLine(len(result), start, text, tokens, "qrc_word_timing")
            )
            continue

        alternatives = lrc_groups[start]
        selected_index = _select_index(
            start,
            alternatives,
            original_index_by_timestamp=original_index_by_timestamp,
            lexical_predicate=lambda item: bool(item[1]) and not META_RE.match(item[1]),
        )
        _, text, tokens, timing_format = alternatives[selected_index]
        if not text or META_RE.match(text):
            raise CanonicalLyricError(
                f"canonical selection points to metadata/blank text at {start}ms"
            )
        result.append(
            CanonicalLine(
                index=len(result),
                time_ms=start,
                text=text,
                tokens=tokens,
                timing_format=timing_format,
            )
        )

    if not result:
        raise CanonicalLyricError(f"no canonical lyric lines in {path}")
    return result
