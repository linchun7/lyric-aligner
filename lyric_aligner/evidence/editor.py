"""Language-aware, non-authoritative editor subtitle evidence.

The editor/Jianying SRT is a sensor, never canonical text or primary timing
truth.  This module produces *shadow-only* evidence against an already-built
canonical Source-to-Mix timeline.  It intentionally does not mutate timeline
rows and does not declare any candidate safe for automatic application.

Bootstrap ranking weights are only used to order evidence candidates for review
and calibration.  They are explicitly marked uncalibrated and must not be
interpreted as production confidence thresholds.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from typing import Any, Iterable

from lyric_aligner.srt import Cue
from lyric_aligner.text.language_spans import (
    LanguageSpan,
    editor_mode_for_span,
    language_spans,
)


EDITOR_EVIDENCE_SCHEMA_VERSION = "1.0"
EDITOR_SHADOW_POLICY_ID = "editor-shadow-bootstrap-2026-08-18-v1"


class EditorEvidenceError(ValueError):
    """Raised when editor evidence cannot be computed deterministically."""


@dataclass(frozen=True)
class EditorTrustProfile:
    language: str
    mode: str
    text_weight: float
    timing_weight: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Shadow ranking only.  No threshold in this table authorizes timing mutation.
_TRUST: dict[str, EditorTrustProfile] = {
    "en": EditorTrustProfile("en", "direct_text", 0.85, 0.75),
    "zh": EditorTrustProfile("zh", "direct_text", 0.75, 0.70),
    "yue": EditorTrustProfile("yue", "timing_hint", 0.00, 0.25),
    "ko": EditorTrustProfile("ko", "phonetic_hint", 0.10, 0.30),
    "ja": EditorTrustProfile("ja", "phonetic_hint", 0.10, 0.30),
    "generic": EditorTrustProfile("generic", "timing_hint", 0.00, 0.20),
    "unknown": EditorTrustProfile("unknown", "timing_hint", 0.00, 0.20),
    "und-han": EditorTrustProfile("und-han", "timing_hint", 0.00, 0.20),
}


_HANGUL_INITIAL = (
    "g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss", "",
    "j", "jj", "ch", "k", "t", "p", "h",
)
_HANGUL_MEDIAL = (
    "a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae",
    "oe", "yo", "u", "wo", "we", "wi", "yu", "eu", "ui", "i",
)
_HANGUL_FINAL = (
    "", "k", "k", "ks", "n", "nj", "nh", "t", "l", "lk", "lm", "lb",
    "ls", "lt", "lp", "lh", "m", "p", "ps", "t", "t", "ng", "t", "t",
    "k", "t", "p", "h",
)

# Conservative kana romanization.  Han/Kanji deliberately has no built-in
# reading because guessing Japanese readings would create false evidence.
_KANA = {
    "あ":"a","い":"i","う":"u","え":"e","お":"o",
    "か":"ka","き":"ki","く":"ku","け":"ke","こ":"ko",
    "さ":"sa","し":"shi","す":"su","せ":"se","そ":"so",
    "た":"ta","ち":"chi","つ":"tsu","て":"te","と":"to",
    "な":"na","に":"ni","ぬ":"nu","ね":"ne","の":"no",
    "は":"ha","ひ":"hi","ふ":"fu","へ":"he","ほ":"ho",
    "ま":"ma","み":"mi","む":"mu","め":"me","も":"mo",
    "や":"ya","ゆ":"yu","よ":"yo",
    "ら":"ra","り":"ri","る":"ru","れ":"re","ろ":"ro",
    "わ":"wa","を":"o","ん":"n",
    "が":"ga","ぎ":"gi","ぐ":"gu","げ":"ge","ご":"go",
    "ざ":"za","じ":"ji","ず":"zu","ぜ":"ze","ぞ":"zo",
    "だ":"da","ぢ":"ji","づ":"zu","で":"de","ど":"do",
    "ば":"ba","び":"bi","ぶ":"bu","べ":"be","ぼ":"bo",
    "ぱ":"pa","ぴ":"pi","ぷ":"pu","ぺ":"pe","ぽ":"po",
    "ゔ":"vu",
    "ぁ":"a","ぃ":"i","ぅ":"u","ぇ":"e","ぉ":"o",
    "ゃ":"ya","ゅ":"yu","ょ":"yo",
}
_KANA_DIGRAPH = {
    "きゃ":"kya","きゅ":"kyu","きょ":"kyo",
    "しゃ":"sha","しゅ":"shu","しょ":"sho",
    "ちゃ":"cha","ちゅ":"chu","ちょ":"cho",
    "にゃ":"nya","にゅ":"nyu","にょ":"nyo",
    "ひゃ":"hya","ひゅ":"hyu","ひょ":"hyo",
    "みゃ":"mya","みゅ":"myu","みょ":"myo",
    "りゃ":"rya","りゅ":"ryu","りょ":"ryo",
    "ぎゃ":"gya","ぎゅ":"gyu","ぎょ":"gyo",
    "じゃ":"ja","じゅ":"ju","じょ":"jo",
    "びゃ":"bya","びゅ":"byu","びょ":"byo",
    "ぴゃ":"pya","ぴゅ":"pyu","ぴょ":"pyo",
}
_HAN_RE = re.compile(r"[一-鿿]")


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _base_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    value = value.replace("’", "'").replace("‘", "'")
    return "".join(character for character in value if character.isalnum())


def _katakana_to_hiragana(value: str) -> str:
    output: list[str] = []
    for character in value:
        codepoint = ord(character)
        if 0x30A1 <= codepoint <= 0x30F6:
            output.append(chr(codepoint - 0x60))
        else:
            output.append(character)
    return "".join(output)


def _romanize_hangul(value: str) -> str:
    output: list[str] = []
    for character in unicodedata.normalize("NFKC", value).casefold():
        code = ord(character)
        if 0xAC00 <= code <= 0xD7A3:
            offset = code - 0xAC00
            initial = offset // 588
            medial = (offset % 588) // 28
            final = offset % 28
            output.append(
                _HANGUL_INITIAL[initial]
                + _HANGUL_MEDIAL[medial]
                + _HANGUL_FINAL[final]
            )
        elif character.isalnum():
            output.append(character)
    return "".join(output)


def _romanize_kana(value: str) -> tuple[str, bool]:
    normalized = _katakana_to_hiragana(unicodedata.normalize("NFKC", value).casefold())
    if _HAN_RE.search(normalized):
        return "", False
    output: list[str] = []
    index = 0
    geminate = False
    while index < len(normalized):
        character = normalized[index]
        if character == "っ":
            geminate = True
            index += 1
            continue
        pair = normalized[index:index + 2]
        if pair in _KANA_DIGRAPH:
            syllable = _KANA_DIGRAPH[pair]
            index += 2
        elif character in _KANA:
            syllable = _KANA[character]
            index += 1
        elif character == "ー":
            index += 1
            continue
        elif character.isalnum():
            syllable = character
            index += 1
        else:
            index += 1
            continue
        if geminate and syllable:
            first = syllable[0]
            if first not in "aeioun":
                syllable = first + syllable
            geminate = False
        output.append(syllable)
    return "".join(output), True


def phonetic_form(language: str, value: str) -> tuple[str, bool, str]:
    """Return conservative phonetic form; never invent Cantonese/Kanji readings."""

    if language == "ko":
        return _romanize_hangul(value), True, "builtin_hangul_romanization"
    if language == "ja":
        reading, available = _romanize_kana(value)
        return reading, available, (
            "builtin_kana_romanization" if available else "kanji_reading_unavailable"
        )
    return "", False, "phonetic_backend_not_configured"


def _lcs_length(left: str, right: str) -> int:
    if not left or not right:
        return 0
    if len(right) > len(left):
        left, right = right, left
    previous = [0] * (len(right) + 1)
    for left_char in left:
        current = [0]
        for index, right_char in enumerate(right, start=1):
            if left_char == right_char:
                current.append(previous[index - 1] + 1)
            else:
                current.append(max(previous[index], current[-1]))
        previous = current
    return previous[-1]


def text_support_score(canonical: str, observed: str) -> float:
    """Containment-aware sequence score in [0,1] for one expected span."""

    left = _base_text(canonical)
    right = _base_text(observed)
    if not left or not right:
        return 0.0
    ratio = SequenceMatcher(None, left, right, autojunk=False).ratio()
    lcs = _lcs_length(left, right)
    coverage = lcs / len(left)
    precision = lcs / len(right)
    f1 = 0.0 if coverage + precision == 0 else 2 * coverage * precision / (coverage + precision)
    return max(ratio, 0.70 * coverage + 0.30 * f1)


def phonetic_support_score(language: str, canonical: str, observed: str) -> tuple[float | None, str]:
    canonical_form, available, backend = phonetic_form(language, canonical)
    if not available or not canonical_form:
        return None, backend
    observed_form, observed_available, _ = phonetic_form(language, observed)
    # Latin phonetic editor output contains no target script; in that case the
    # base normalized Latin itself is the intended observed pronunciation.
    if not observed_available or not observed_form:
        observed_form = _base_text(observed)
    if not observed_form:
        return None, backend
    return text_support_score(canonical_form, observed_form), backend


def trust_profile(language: str, mode: str) -> EditorTrustProfile:
    profile = _TRUST.get(language, _TRUST["generic"])
    if profile.mode == mode:
        return profile
    # LanguageSpan owns the mode.  If a future span mode differs from the
    # bootstrap table, keep conservative weights instead of escalating trust.
    if mode == "direct_text":
        return EditorTrustProfile(language, mode, min(profile.text_weight, 0.50), profile.timing_weight)
    if mode == "phonetic_hint":
        return EditorTrustProfile(language, mode, min(profile.text_weight, 0.10), profile.timing_weight)
    return EditorTrustProfile(language, mode, 0.0, profile.timing_weight)


def span_policy(text: str, *, track_language: str) -> list[dict[str, Any]]:
    spans = language_spans(text, track_language=track_language)
    output: list[dict[str, Any]] = []
    for span in spans:
        mode = editor_mode_for_span(span)
        profile = trust_profile(span.language, mode)
        output.append(
            {
                "start": span.start,
                "end": span.end,
                "language": span.language,
                "script": span.script,
                "mode": mode,
                "text_weight": profile.text_weight,
                "timing_weight": profile.timing_weight,
                "text_sha256": _sha(span.text),
            }
        )
    return output


def _interval_distance(left_start: int, left_end: int, right_start: int, right_end: int) -> int:
    if left_end < right_start:
        return right_start - left_end
    if right_end < left_start:
        return left_start - right_end
    return 0


def timing_support_score(
    line_start: int,
    line_end: int,
    cue_start: int,
    cue_end: int,
    *,
    search_radius_ms: int,
) -> float:
    if search_radius_ms <= 0:
        raise EditorEvidenceError("search_radius_ms must be positive")
    overlap = max(0, min(line_end, cue_end) - max(line_start, cue_start))
    union = max(line_end, cue_end) - min(line_start, cue_start)
    iou = overlap / union if union > 0 else 0.0
    line_center = (line_start + line_end) / 2.0
    cue_center = (cue_start + cue_end) / 2.0
    center_distance = abs(line_center - cue_center)
    proximity = max(0.0, 1.0 - center_distance / search_radius_ms)
    return min(1.0, max(iou, 0.80 * proximity))


def _span_scores(
    canonical_text: str,
    editor_text: str,
    *,
    track_language: str,
) -> tuple[list[dict[str, Any]], float | None, float | None, float | None, float, float]:
    spans: list[LanguageSpan] = language_spans(canonical_text, track_language=track_language)
    records: list[dict[str, Any]] = []
    direct_weighted = 0.0
    direct_weight = 0.0
    phonetic_weighted = 0.0
    phonetic_weight = 0.0
    text_weighted = 0.0
    text_weight_total = 0.0
    timing_weighted = 0.0
    length_total = 0.0

    for span in spans:
        length = float(max(1, sum(character.isalnum() for character in span.text)))
        mode = editor_mode_for_span(span)
        profile = trust_profile(span.language, mode)
        score: float | None = None
        backend = "none"
        if mode == "direct_text":
            score = text_support_score(span.text, editor_text)
            direct_weighted += score * length
            direct_weight += length
            backend = "normalized_sequence"
        elif mode == "phonetic_hint":
            score, backend = phonetic_support_score(span.language, span.text, editor_text)
            if score is not None:
                phonetic_weighted += score * length
                phonetic_weight += length

        if score is not None and profile.text_weight > 0:
            text_weighted += score * profile.text_weight * length
            text_weight_total += profile.text_weight * length
        timing_weighted += profile.timing_weight * length
        length_total += length
        records.append(
            {
                "start": span.start,
                "end": span.end,
                "language": span.language,
                "script": span.script,
                "mode": mode,
                "text_sha256": _sha(span.text),
                "support_score": None if score is None else round(score, 6),
                "support_backend": backend,
                "text_weight": profile.text_weight,
                "timing_weight": profile.timing_weight,
            }
        )

    direct = direct_weighted / direct_weight if direct_weight else None
    phonetic = phonetic_weighted / phonetic_weight if phonetic_weight else None
    text_support = text_weighted / text_weight_total if text_weight_total else None
    effective_text_weight = text_weight_total / length_total if length_total else 0.0
    effective_timing_weight = timing_weighted / length_total if length_total else 0.0
    return (
        records,
        direct,
        phonetic,
        text_support,
        effective_text_weight,
        effective_timing_weight,
    )


def candidate_evidence(
    line: dict[str, Any],
    cue: Cue,
    *,
    track_language: str,
    search_radius_ms: int,
) -> dict[str, Any] | None:
    try:
        line_start = int(line["mix_start_ms"])
    except (KeyError, TypeError, ValueError) as exc:
        raise EditorEvidenceError("canonical line has invalid mix_start_ms") from exc
    raw_end = line.get("mix_end_ms")
    try:
        line_end = line_start + 1 if raw_end is None else int(raw_end)
    except (TypeError, ValueError) as exc:
        raise EditorEvidenceError("canonical line has invalid mix_end_ms") from exc
    if line_end <= line_start:
        line_end = line_start + 1
    if _interval_distance(line_start, line_end, cue.start_ms, cue.end_ms) > search_radius_ms:
        return None

    canonical_text = str(line.get("text") or "")
    (
        spans,
        direct_score,
        phonetic_score,
        text_score,
        effective_text_weight,
        effective_timing_weight,
    ) = _span_scores(canonical_text, cue.text, track_language=track_language)
    timing_score = timing_support_score(
        line_start,
        line_end,
        cue.start_ms,
        cue.end_ms,
        search_radius_ms=search_radius_ms,
    )
    weighted = timing_score * effective_timing_weight
    denominator = effective_timing_weight
    if text_score is not None and effective_text_weight > 0:
        weighted += text_score * effective_text_weight
        denominator += effective_text_weight
    rank_score = weighted / denominator if denominator > 0 else timing_score

    return {
        "editor_cue_number": cue.number,
        "editor_start_ms": cue.start_ms,
        "editor_end_ms": cue.end_ms,
        "editor_text_sha256": _sha(cue.text),
        "timing_support_score": round(timing_score, 6),
        "direct_text_support_score": None if direct_score is None else round(direct_score, 6),
        "phonetic_support_score": None if phonetic_score is None else round(phonetic_score, 6),
        "text_support_score": None if text_score is None else round(text_score, 6),
        "effective_text_weight": round(effective_text_weight, 6),
        "effective_timing_weight": round(effective_timing_weight, 6),
        "rank_score_uncalibrated": round(rank_score, 6),
        "suggested_onset_delta_ms": cue.start_ms - line_start,
        "suggested_offset_delta_ms": cue.end_ms - line_end,
        "spans": spans,
    }


def evidence_for_line(
    line: dict[str, Any],
    editor_cues: Iterable[Cue],
    *,
    track_language: str,
    search_radius_ms: int = 2500,
    max_candidates: int = 3,
) -> dict[str, Any]:
    if max_candidates < 1:
        raise EditorEvidenceError("max_candidates must be >= 1")
    candidates = [
        candidate
        for cue in editor_cues
        if (candidate := candidate_evidence(
            line,
            cue,
            track_language=track_language,
            search_radius_ms=search_radius_ms,
        ))
        is not None
    ]
    candidates.sort(
        key=lambda row: (
            float(row["rank_score_uncalibrated"]),
            float(row["timing_support_score"]),
            -int(row["editor_cue_number"]),
        ),
        reverse=True,
    )
    selected = candidates[:max_candidates]
    best = selected[0] if selected else None
    second = selected[1] if len(selected) > 1 else None
    margin = None
    if best is not None:
        margin = float(best["rank_score_uncalibrated"]) - (
            float(second["rank_score_uncalibrated"]) if second is not None else 0.0
        )
    canonical_text = str(line.get("text") or "")
    return {
        "canonical_line_index": int(line.get("canonical_line_index", -1)),
        "canonical_text_sha256": _sha(canonical_text),
        "canonical_mix_start_ms": int(line.get("mix_start_ms", 0)),
        "canonical_mix_end_ms": line.get("mix_end_ms"),
        "track_language": track_language,
        "span_policy": span_policy(canonical_text, track_language=track_language),
        "candidate_count_in_search_window": len(candidates),
        "candidates": selected,
        "best_candidate_margin_uncalibrated": None if margin is None else round(margin, 6),
        "best_editor_cue_number": None if best is None else best["editor_cue_number"],
        "suggested_onset_delta_ms": None if best is None else best["suggested_onset_delta_ms"],
        "suggested_offset_delta_ms": None if best is None else best["suggested_offset_delta_ms"],
        "shadow_only": True,
        "automatic_timing_change_allowed": False,
    }


def _timeline_result(payload: dict[str, Any]) -> dict[str, Any]:
    result = payload.get("result")
    if not isinstance(result, dict):
        raise EditorEvidenceError("timeline payload has no result")
    if not isinstance(result.get("lines"), list):
        raise EditorEvidenceError("timeline result has no line list")
    return result


def build_editor_evidence(
    timeline_payloads: Iterable[dict[str, Any]],
    editor_cues: Iterable[Cue],
    *,
    search_radius_ms: int = 2500,
    max_candidates: int = 3,
) -> dict[str, Any]:
    cues = list(editor_cues)
    occurrences: list[dict[str, Any]] = []
    total_lines = 0
    matched_lines = 0
    direct_lines = 0
    phonetic_lines = 0
    timing_only_lines = 0

    for payload in timeline_payloads:
        result = _timeline_result(payload)
        occurrence_id = str(result.get("occurrence_id") or payload.get("occurrence_id") or "")
        track_id = str(result.get("track_id") or payload.get("track_id") or "")
        if not occurrence_id or not track_id:
            raise EditorEvidenceError("timeline is missing occurrence/track identity")
        language = str(result.get("language_profile") or "auto")
        lines: list[dict[str, Any]] = []
        for line in result["lines"]:
            row = evidence_for_line(
                line,
                cues,
                track_language=language,
                search_radius_ms=search_radius_ms,
                max_candidates=max_candidates,
            )
            lines.append(row)
            total_lines += 1
            if row["best_editor_cue_number"] is not None:
                matched_lines += 1
            modes = {span["mode"] for span in row["span_policy"]}
            if "direct_text" in modes:
                direct_lines += 1
            elif "phonetic_hint" in modes:
                phonetic_lines += 1
            else:
                timing_only_lines += 1
        occurrences.append(
            {
                "occurrence_id": occurrence_id,
                "track_id": track_id,
                "ordinal": int(result.get("ordinal", -1)),
                "language_profile": language,
                "canonical_selection_sha256": str(result.get("canonical_selection_sha256") or ""),
                "line_count": len(lines),
                "lines": lines,
            }
        )

    occurrences.sort(key=lambda row: (row["ordinal"], row["occurrence_id"]))
    return {
        "schema_version": EDITOR_EVIDENCE_SCHEMA_VERSION,
        "mode": "shadow_only",
        "policy_id": EDITOR_SHADOW_POLICY_ID,
        "policy_calibrated": False,
        "search_radius_ms": search_radius_ms,
        "max_candidates_per_line": max_candidates,
        "editor_cue_count": len(cues),
        "summary": {
            "canonical_line_count": total_lines,
            "lines_with_editor_candidate": matched_lines,
            "direct_text_policy_line_count": direct_lines,
            "phonetic_hint_policy_line_count": phonetic_lines,
            "timing_only_policy_line_count": timing_only_lines,
        },
        "occurrences": occurrences,
        "authority": {
            "canonical_text": "canonical_lyrics_only",
            "primary_timing": "source_to_mix_only",
            "editor": "non_authoritative_shadow_evidence",
            "automatic_timing_change_allowed": False,
        },
    }
