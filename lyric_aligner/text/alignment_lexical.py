"""Deterministic lexical-unit preparation for forced-alignment backends."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Mapping, Sequence

ALIGNMENT_LEXICAL_ID = "lyric-alignment-lexical"
ALIGNMENT_LEXICAL_REVISION = "1.0"


class AlignmentLexicalError(ValueError):
    pass


_EN_RE = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)?", re.IGNORECASE)
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def _ascii(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold().replace("’", "'").strip()


def english_units(text: str) -> list[str]:
    value = _ascii(str(text or ""))
    result = [_ascii(match.group(0)) for match in _EN_RE.finditer(value)]
    if not result:
        raise AlignmentLexicalError("English alignment text contains no lexical units")
    return result


def mandarin_pinyin_units(text: str) -> list[str]:
    value = unicodedata.normalize("NFKC", str(text or "")).strip()
    if not value:
        raise AlignmentLexicalError("Mandarin alignment text is empty")
    try:
        from pypinyin import Style, lazy_pinyin
    except ImportError as exc:
        raise AlignmentLexicalError(
            "Mandarin alignment preparation requires requirements-language/pypinyin"
        ) from exc

    output: list[str] = []
    ascii_buffer: list[str] = []

    def flush_ascii() -> None:
        if not ascii_buffer:
            return
        output.extend(english_units("".join(ascii_buffer)))
        ascii_buffer.clear()

    for character in value:
        if _CJK_RE.fullmatch(character):
            flush_ascii()
            readings = lazy_pinyin(character, style=Style.NORMAL, errors=lambda item: [item])
            if len(readings) != 1:
                raise AlignmentLexicalError("Mandarin character did not yield one reading")
            unit = _ascii(readings[0]).replace("u:", "v").replace("ü", "v")
            if not unit or not re.fullmatch(r"[a-z0-9]+", unit):
                raise AlignmentLexicalError("unsupported Mandarin pinyin unit")
            output.append(unit)
        elif character.isascii() and (character.isalnum() or character in {"'", "’"}):
            ascii_buffer.append(character)
        else:
            flush_ascii()
    flush_ascii()
    if not output:
        raise AlignmentLexicalError("Mandarin alignment text contains no lexical units")
    return output


def alignment_units(language: str, text: str) -> list[str]:
    code = str(language or "").strip().casefold()
    if code == "en":
        return english_units(text)
    if code in {"zh", "cmn"}:
        return mandarin_pinyin_units(text)
    raise AlignmentLexicalError(
        f"no deterministic forced-alignment preparation is defined for language {code!r}"
    )


def segment_units_from_canonical_segments(
    segments: Sequence[Mapping[str, Any]],
    *,
    language: str,
) -> list[list[str]]:
    if len(segments) < 2:
        raise AlignmentLexicalError("internal alignment needs at least two canonical segments")
    result: list[list[str]] = []
    for segment in segments:
        if not isinstance(segment, Mapping):
            raise AlignmentLexicalError("canonical segment must be an object")
        text = str(segment.get("text") or "").strip()
        if not text:
            raise AlignmentLexicalError("canonical segment text is empty")
        result.append(alignment_units(language, text))
    return result
