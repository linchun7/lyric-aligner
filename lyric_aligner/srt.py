"""Strict SRT parsing and canonical cue identity for production validation."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

TIME_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})[,.](\d{3})$")


class SRTParseError(ValueError):
    """Raised when production SRT input contains a malformed block."""


@dataclass(frozen=True)
class Cue:
    number: int
    start_ms: int
    end_ms: int
    text: str


def parse_time(value: str) -> int:
    match = TIME_RE.fullmatch(value.strip())
    if not match:
        raise SRTParseError(f"invalid SRT time {value!r}")
    hour, minute, second, millis = map(int, match.groups())
    if minute >= 60 or second >= 60:
        raise SRTParseError(f"invalid SRT clock value {value!r}")
    return ((hour * 60 + minute) * 60 + second) * 1000 + millis


def _normalize_newlines(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def normalized_text(value: str) -> str:
    """Normalize only representation details; do not change lyric semantics."""

    value = unicodedata.normalize("NFKC", _normalize_newlines(value))
    return "\n".join(line.rstrip() for line in value.strip().split("\n"))


def text_sha256(value: str) -> str:
    return hashlib.sha256(normalized_text(value).encode("utf-8")).hexdigest()


def cue_id(position: int, cue: Cue) -> str:
    payload = {
        "position": position,
        "number": cue.number,
        "start_ms": cue.start_ms,
        "end_ms": cue.end_ms,
        "text_sha256": text_sha256(cue.text),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def parse_srt_strict(path: Path) -> list[Cue]:
    """Parse SRT fail-closed.

    Unlike the legacy diagnostic parser, malformed non-empty blocks are not
    silently discarded. This prevents broken input from masquerading as a
    naturally missing lyric line.
    """

    raw = path.read_text(encoding="utf-8-sig")
    text = _normalize_newlines(raw).strip()
    if not text:
        raise SRTParseError(f"empty SRT: {path}")

    cues: list[Cue] = []
    seen_numbers: set[int] = set()
    blocks = re.split(r"\n\s*\n", text)
    for block_index, block in enumerate(blocks, start=1):
        lines = block.split("\n")
        if len(lines) < 3:
            raise SRTParseError(
                f"malformed SRT block {block_index}: expected number, timing and text"
            )
        try:
            number = int(lines[0].strip())
        except ValueError as exc:
            raise SRTParseError(
                f"malformed SRT block {block_index}: invalid cue number {lines[0]!r}"
            ) from exc
        if number <= 0:
            raise SRTParseError(f"malformed SRT block {block_index}: cue number must be positive")
        if number in seen_numbers:
            raise SRTParseError(f"duplicate SRT cue number {number} at block {block_index}")
        seen_numbers.add(number)

        if "-->" not in lines[1]:
            raise SRTParseError(f"malformed SRT block {block_index}: missing '-->' timing separator")
        start_raw, end_raw = (part.strip() for part in lines[1].split("-->", 1))
        start_ms = parse_time(start_raw)
        end_ms = parse_time(end_raw)
        if end_ms <= start_ms:
            raise SRTParseError(
                f"malformed SRT block {block_index}: end must be after start"
            )
        cue_text = "\n".join(lines[2:]).strip()
        if not cue_text:
            raise SRTParseError(f"malformed SRT block {block_index}: blank cue text")
        cues.append(Cue(number, start_ms, end_ms, cue_text))

    return cues


def timeline_end_ms(cues: list[Cue]) -> int:
    if not cues:
        raise ValueError("timeline_end_ms requires at least one cue")
    return max(cue.end_ms for cue in cues)
