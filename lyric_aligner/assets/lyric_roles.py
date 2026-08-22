"""Conservative same-timestamp lyric role classification for canonical preflight."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from lyric_aligner.io.text import read_task_text
from lyric_aligner.text.normalization import (
    clean_text,
    contextual_cjk_role_names,
    is_metadata_text,
    is_title_like_intro,
)

LRC_LINE_RE = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\](.*)")
QRC_LINE_RE = re.compile(r"^\[(\d+),(\d+)\](.*)$")
ENHANCED_TOKEN_RE = re.compile(r"<\d{1,3}:\d{2}(?:[.:]\d{1,3})?>")
QRC_TOKEN_TIME_RE = re.compile(r"\(\d+,\d+\)")
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


def _display_text(value: str) -> str:
    """Remove timing markup before role/script classification."""

    value = ENHANCED_TOKEN_RE.sub("", value)
    value = QRC_TOKEN_TIME_RE.sub("", value)
    return clean_text(value)


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
    contextual_role_names: frozenset[str] = frozenset(),
) -> list[LyricAlternative]:
    cleaned = [_display_text(text) for text in texts]
    cleaned = [text for text in cleaned if text]
    if not cleaned:
        return []

    # Keep role preflight aligned with the canonical parser. Consumer lyric
    # files commonly timestamp credits, role labels and an early artist-title
    # row. Those rows are not canonical lyric alternatives and must not force
    # Max to invent an "original" merely because they have timestamps.
    roles = [
        "metadata"
        if is_metadata_text(
            text,
            contextual_role_names=contextual_role_names,
        ) or is_title_like_intro(timestamp_ms, text)
        else "unknown"
        for text in cleaned
    ]
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

    return [
        LyricAlternative(timestamp_ms, text, role, script_kind(text))
        for text, role in zip(cleaned, roles)
    ]


def inspect_lyric_roles(
    path: Path,
    *,
    language: str,
    original_index_overrides: dict[int, int] | None = None,
) -> dict:
    groups: dict[int, list[str]] = {}
    formats: dict[int, set[str]] = {}
    original_index_overrides = original_index_overrides or {}
    for line_number, raw in enumerate(read_task_text(path).splitlines(), start=1):
        stripped = raw.strip()
        if not stripped:
            continue

        qrc_match = QRC_LINE_RE.match(stripped)
        if qrc_match:
            timestamp = int(qrc_match.group(1))
            groups.setdefault(timestamp, []).append(qrc_match.group(3))
            formats.setdefault(timestamp, set()).add("qrc")
            continue

        match = LRC_LINE_RE.match(stripped)
        if not match:
            continue
        minute, second, fraction, text = match.groups()
        try:
            timestamp = _timestamp_ms(minute, second, fraction)
        except ValueError as exc:
            raise LyricRoleError(f"invalid LRC timestamp at line {line_number}") from exc
        groups.setdefault(timestamp, []).append(text)
        formats.setdefault(timestamp, set()).add(
            "enhanced_lrc" if ENHANCED_TOKEN_RE.search(text) else "line_lrc"
        )

    if not groups:
        raise LyricRoleError(f"no timestamped lyric lines in {path}")

    contextual_role_names = contextual_cjk_role_names(
        (timestamp, text)
        for timestamp, texts in groups.items()
        for text in texts
    )
    inspected: list[dict] = []
    ambiguous: list[int] = []
    original_count = 0
    ignored_blank_group_count = 0
    ignored_metadata_group_count = 0
    for timestamp, texts in sorted(groups.items()):
        alternatives = classify_alternatives(
            timestamp,
            texts,
            language=language,
            contextual_role_names=contextual_role_names,
        )
        if not alternatives:
            ignored_blank_group_count += 1
            continue
        override_index = original_index_overrides.get(timestamp)
        if override_index is not None:
            if override_index < 0 or override_index >= len(alternatives):
                raise LyricRoleError(
                    f"original_index override {override_index} is out of range at {timestamp}ms"
                )
            if alternatives[override_index].role == "metadata":
                raise LyricRoleError(
                    f"original_index override {override_index} selects metadata at {timestamp}ms"
                )
            alternatives = [
                LyricAlternative(
                    row.timestamp_ms,
                    row.text,
                    "original"
                    if index == override_index
                    else ("metadata" if row.role == "metadata" else "unknown"),
                    row.script,
                )
                for index, row in enumerate(alternatives)
            ]

        # Metadata-only timestamp groups are valid consumer-LRC decoration, not
        # an ambiguous lyric occurrence. Ignore them exactly as the canonical
        # parser does. A group with two or more genuine lexical alternatives
        # still fails closed below.
        if all(row.role == "metadata" for row in alternatives):
            ignored_metadata_group_count += 1
            continue

        originals = [row for row in alternatives if row.role == "original"]
        if len(originals) != 1:
            ambiguous.append(timestamp)
        else:
            original_count += 1
        inspected.append(
            {
                "timestamp_ms": timestamp,
                "formats": sorted(formats.get(timestamp, set())),
                "alternatives": [row.to_dict() for row in alternatives],
                "canonical_original_count": len(originals),
            }
        )

    if not inspected:
        raise LyricRoleError(f"no lexical timestamped lyric lines in {path}")
    if ambiguous:
        preview = ", ".join(str(value) for value in ambiguous[:5])
        raise LyricRoleError(
            "canonical original is ambiguous at same-timestamp lyric group(s) "
            f"{preview}; supply a cleaned canonical LRC or explicit role mapping"
        )

    # Preserve the legacy timestamp_group_count meaning (all nonblank parsed
    # groups) while exposing the narrower lexical count explicitly.
    return {
        "language": language,
        "timestamp_group_count": len(inspected) + ignored_metadata_group_count,
        "lexical_timestamp_group_count": len(inspected),
        "canonical_original_count": original_count,
        "ignored_blank_group_count": ignored_blank_group_count,
        "ignored_metadata_group_count": ignored_metadata_group_count,
        "groups": inspected,
    }
