"""Conservative script/language spans inside one canonical lyric line."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

HANGUL = re.compile(r"[가-힣ㄱ-ㅎㅏ-ㅣ]")
KANA = re.compile(r"[ぁ-ゖァ-ヺー]")
HAN = re.compile(r"[一-鿿]")
LATIN = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿĀ-ž]")
DIGIT = re.compile(r"[0-9]")


@dataclass(frozen=True)
class LanguageSpan:
    start: int
    end: int
    language: str
    script: str
    text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _base_language(value: str) -> str:
    value = value.strip().casefold()
    aliases = {
        "kr": "ko",
        "jp": "ja",
        "cn": "zh",
        "cantonese": "yue",
        "zh-yue": "yue",
    }
    return aliases.get(value, value or "auto")


def _classify_char(character: str, track_language: str) -> tuple[str, str] | None:
    base = _base_language(track_language)
    if HANGUL.fullmatch(character):
        return "ko", "hangul"
    if KANA.fullmatch(character):
        return "ja", "kana"
    if HAN.fullmatch(character):
        if base == "ja":
            return "ja", "han"
        if base == "yue":
            return "yue", "han"
        if base == "zh":
            return "zh", "han"
        return "und-han", "han"
    if LATIN.fullmatch(character):
        # Latin inside a known East-Asian canonical lyric may be an actual
        # English span. For an unknown Latin-script language, never silently
        # rename the entire line to English.
        if base in {"ko", "ja", "zh", "yue", "mixed"}:
            return "en", "latin"
        if base == "en":
            return "en", "latin"
        return (
            base if base not in {"auto", "generic", "unknown"} else "generic"
        ), "latin"
    if DIGIT.fullmatch(character):
        return None
    return None


def language_spans(text: str, *, track_language: str) -> list[LanguageSpan]:
    """Return merged language spans while keeping punctuation attached.

    Whitespace and punctuation never create language evidence on their own.
    Japanese Han and Kana remain one language span even though the script changes.
    """

    if not text:
        return []
    labels: list[tuple[str, str] | None] = [
        _classify_char(character, track_language) for character in text
    ]

    last = None
    for index, label in enumerate(labels):
        if label is not None:
            last = label
        elif last is not None:
            labels[index] = last
    next_label = None
    for index in range(len(labels) - 1, -1, -1):
        label = labels[index]
        if label is not None:
            next_label = label
        elif next_label is not None:
            labels[index] = next_label
    if all(label is None for label in labels):
        return [LanguageSpan(0, len(text), "unknown", "other", text)]

    output: list[LanguageSpan] = []
    start = 0
    current = labels[0] or ("unknown", "other")

    def emit(span_start: int, span_end: int, language: str) -> None:
        scripts = {
            (labels[index] or (language, "other"))[1]
            for index in range(span_start, span_end)
            if (labels[index] or (language, "other"))[0] == language
        }
        script = next(iter(scripts)) if len(scripts) == 1 else "mixed"
        output.append(
            LanguageSpan(span_start, span_end, language, script, text[span_start:span_end])
        )

    for index in range(1, len(text)):
        label = labels[index] or current
        if label[0] != current[0]:
            emit(start, index, current[0])
            start = index
            current = label
    emit(start, len(text), current[0])
    return output


def asr_language_hint_for_text(text: str, *, track_language: str) -> str | None:
    """Return a safe faster-whisper hint for one local canonical line.

    A single supported language across the lexical spans is safe to pin. Mixed
    or uncertain lines deliberately return ``None`` so the ASR backend can
    auto-detect rather than inheriting a possibly-wrong whole-track hint.

    This is intentionally local: a Chinese track containing an all-English rap
    line returns ``en`` for that job, while a Chinese+English code-switch line
    returns ``None``.
    """

    spans = language_spans(text, track_language=track_language)
    languages = {
        span.language
        for span in spans
        if span.language not in {"unknown", "generic", "und-han"}
    }
    supported = languages & {"zh", "en", "ko", "ja"}
    unsupported = languages - {"zh", "en", "ko", "ja"}
    uncertain = any(
        span.language in {"unknown", "generic", "und-han"}
        for span in spans
    )
    if unsupported or uncertain or len(supported) != 1:
        return None
    return next(iter(supported))


def asr_language_hint_for_bounded_context(
    text: str,
    *,
    track_language: str,
) -> str | None:
    """Pin ASR only when local text and known track language agree.

    Pro windows intentionally include timing-search context around one cue.  A
    cross-language local line can therefore contain adjacent track-language
    vocals; backend auto-detection is safer than forcing the local script.
    """

    track = _base_language(track_language)
    if track not in {"zh", "en", "ko", "ja"}:
        return None
    local = asr_language_hint_for_text(text, track_language=track)
    return local if local == track else None


def editor_mode_for_span(span: LanguageSpan) -> str:
    """Return the v4 editor evidence mode for one canonical language span."""

    if span.language in {"zh", "en"}:
        return "direct_text"
    if span.language in {"ko", "ja"}:
        return "phonetic_hint"
    if span.language == "yue":
        return "timing_hint"
    return "timing_hint"
