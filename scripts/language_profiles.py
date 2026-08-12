#!/usr/bin/env python3
"""Language-aware normalization and confidence policy for lyric evidence."""

from __future__ import annotations

import re
import unicodedata
from typing import Any


SUPPORTED_LANGUAGES = {"en", "zh", "ko", "ja", "mixed"}

LANGUAGE_THRESHOLDS = {
    "en": {"auto_score": 0.88, "review_score": 0.64, "min_coverage": 0.68},
    "zh": {"auto_score": 0.90, "review_score": 0.66, "min_coverage": 0.72},
    "ko": {"auto_score": 0.86, "review_score": 0.60, "min_coverage": 0.64},
    "ja": {"auto_score": 0.92, "review_score": 0.68, "min_coverage": 0.74},
    "mixed": {"auto_score": 0.94, "review_score": 0.72, "min_coverage": 0.78},
}


def language_code(value: str) -> str:
    normalized = value.strip().lower()
    aliases = {"cn": "zh", "jp": "ja", "kr": "ko", "multi": "mixed"}
    normalized = aliases.get(normalized, normalized)
    if normalized not in SUPPORTED_LANGUAGES:
        raise ValueError(
            f"unsupported language {value!r}; expected one of "
            + ", ".join(sorted(SUPPORTED_LANGUAGES))
        )
    return normalized


def _base_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = value.replace("’", "'").replace("‘", "'")
    return re.sub(r"\s+", " ", value).strip()


def _katakana_to_hiragana(value: str) -> str:
    output: list[str] = []
    for character in value:
        codepoint = ord(character)
        if 0x30A1 <= codepoint <= 0x30F6:
            output.append(chr(codepoint - 0x60))
        else:
            output.append(character)
    return "".join(output)


def pronunciation_for_evidence(language: str, text: str) -> tuple[str, bool]:
    """Return pronunciation evidence and whether a full reading layer exists."""

    language = language_code(language)
    normalized = _base_text(text)
    if language == "zh":
        try:
            from pypinyin import Style, lazy_pinyin
        except ImportError:
            return "", False
        return " ".join(lazy_pinyin(normalized, style=Style.NORMAL)), True
    if language == "ja":
        try:
            from pykakasi import kakasi
        except ImportError:
            return "", False
        readings = kakasi().convert(normalized)
        return " ".join(str(item.get("hira") or item.get("orig") or "") for item in readings), True
    return normalized, True


def normalize_for_evidence(
    language: str,
    text: str,
    *,
    pronunciation: bool = False,
) -> str:
    language = language_code(language)
    value = _base_text(text)
    if pronunciation:
        reading, available = pronunciation_for_evidence(language, value)
        if available:
            value = reading
    if language == "ja":
        value = _katakana_to_hiragana(value)
    if language == "en":
        value = re.sub(r"(?<=\w)-(?=\w)", "", value)
    return "".join(character for character in value if character.isalnum())


def boundary_units_for_language(language: str, text: str) -> list[str]:
    language = language_code(language)
    value = _base_text(text)
    if language == "en":
        return re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)?", value)
    if language in {"zh", "ja"}:
        return [character for character in value if character.isalnum()]
    if language == "ko":
        return re.findall(r"[가-힣]+|[a-z0-9]+", value)
    return re.findall(r"[가-힣]+|[一-鿿ぁ-ゖァ-ヺ]+|[a-z0-9]+", value)


def thresholds(language: str) -> dict[str, float]:
    return dict(LANGUAGE_THRESHOLDS[language_code(language)])


def evidence_capability(language: str, text: str) -> dict[str, Any]:
    """Describe whether pronunciation evidence may support high confidence."""

    language = language_code(language)
    reading, available = pronunciation_for_evidence(language, text)
    contains_kanji = bool(re.search(r"[一-鿿]", text))
    pronunciation_required = language == "ja" and contains_kanji
    return {
        "language": language,
        "pronunciation_available": available,
        "pronunciation_required_for_high_confidence": pronunciation_required,
        "high_confidence_allowed": not pronunciation_required or available,
        "pronunciation": reading if available else "",
    }
