"""Conservative same-timestamp lyric role classification for canonical preflight."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from lyric_aligner.io.text import read_task_text

LRC_LINE_RE = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\](.*)")
META_RE = re.compile(
    r"^(?:\[?by:|作词|作曲|编曲|词\s*:|曲\s*:|制作人|人声采样|版权|发行|混音|母带|企划|出品人|op\s*:|sp\s*:)",
    re.IGNORECASE,
)
HANGUL_RE = re.compile(r"[가-힣]")
HAN_RE = re.compile(r"[一-鿿]")
KANA_RE = re.compile(r"[ぁ-ゖァ-ヺ]")
LATIN_RE = re.compile(r"[A-Za-z]")


class LyricRoleError(ValueError):
    """Raised when a canonical original cannot be identified conservatively."""


@dataclass(frozen=True)
class LyricAlternative:
    timestamp_ms: int
    text: str
    role: str
    script: str

    def to_dict(self) -> dict:
        return asdict(self)


def _fraction_digits(value: str | None) -> int:
    if not value:
        return 0
    if len(value) == 1:
        return int(value) * 100
    if len(value) == 2:
        return int(value) * 10
    return int(value[:3])


def _timestamp_ms(minute: str, second: str, fraction: str | None) -> int:
    return (int(minute) * 60 + int(second)) * 1000 + _fraction_digits(fraction)


def script_kind(text: str) -> str:
    has_hangul = bool(HANGUL_RE.search(text))
    has_han = bool(HAN_RE.search(text))
    has_kana = bool(KANA_RE.search(text))
    has_latin = bool(LATIN_RE.search(text))
    active = [
        name
        for name, present in (
            ("hangul", has_hangul),
            ("han", has_han),
            ("kana", has_kana),
            ("latin", has_latin),
        )
        if present
    ]
    if not active:
        return "other"
    if len(active) == 1:
        return active[0]
    if set(active) <= {"han", "kana"}:
        return "japanese"
    return "mixed"


def _native_candidate(language: str, text: str) -> bool:
    language = language.casefold()
    if language in {"ko", "kr"}:
        return bool(HANGUL_RE.search(text))
    if language in {"ja", "jp"}:
        return bool(KANA_RE.search(text) or HAN_RE.search(text))
    if language in {"zh", "yue", "zh-yue", "cn"}:
        return bool(HAN_RE.search(text))
    if language == "en":
        return bool(LATIN_RE.search(text)) and not bool(
            HANGUL_RE.search(text) or HAN_RE.search(text) or KANA_RE.search(text)
        )
    return False


def classify_alternatives(
    timestamp_ms: int,
    texts: Iterable[str],
    *,
    language: str,
) -> list[LyricAlternative]:
    cleaned = [text.strip() for text in texts if text.strip()]
    if not cleaned:
        return []

    roles = ["metadata" if META_RE.match(text) else "unknown" for text in cleaned]
    lyric_indexes = [index for index, role in enumerate(roles) if role != "metadata"]

    if len(lyric_indexes) == 1:
        roles[lyric_indexes[0]] = "original"
    elif len(lyric_indexes) > 1:
        native = [
            index
            for index in lyric_indexes
            if _native_candidate(language, cleaned[index])
        ]
        if len(native) == 1:
            roles[native[0]] = "original"
        # Every other alternative intentionally remains unknown. Latin text next
        # to a Korean/Japanese/Chinese original may be a translation OR a
        # romanization; guessing that role would create false confidence.

    return [
        LyricAlternative(timestamp_ms, text, role, script_kind(text))
        for text, role in zip(cleaned, roles)
    ]


def inspect_lyric_roles(path: Path, *, language: str) -> dict:
    groups: dict[int, list[str]] = {}
    for line_number, raw in enumerate(read_task_text(path).splitlines(), start=1):
        stripped = raw.strip()
        if not stripped:
            continue
        match = LRC_LINE_RE.match(stripped)
        if not match:
            continue
        minute, second, fraction, text = match.groups()
        try:
            timestamp = _timestamp_ms(minute, second, fraction)
        except ValueError as exc:
            raise LyricRoleError(f"invalid LRC timestamp at line {line_number}") from exc
        groups.setdefault(timestamp, []).append(text.strip())

    if not groups:
        raise LyricRoleError(f"no timestamped lyric lines in {path}")

    inspected: list[dict] = []
    ambiguous: list[int] = []
    original_count = 0
    for timestamp, texts in sorted(groups.items()):
        alternatives = classify_alternatives(timestamp, texts, language=language)
        originals = [row for row in alternatives if row.role == "original"]
        if len(originals) != 1:
            ambiguous.append(timestamp)
        else:
            original_count += 1
        inspected.append(
            {
                "timestamp_ms": timestamp,
                "alternatives": [row.to_dict() for row in alternatives],
                "canonical_original_count": len(originals),
            }
        )

    if ambiguous:
        preview = ", ".join(str(value) for value in ambiguous[:5])
        raise LyricRoleError(
            "canonical original is ambiguous at same-timestamp lyric group(s) "
            f"{preview}; supply a cleaned canonical LRC or explicit role mapping"
        )

    return {
        "language": language,
        "timestamp_group_count": len(inspected),
        "canonical_original_count": original_count,
        "groups": inspected,
    }
