"""Final low-authority text recovery for ownership-preserving Smart reviews."""

from __future__ import annotations

from dataclasses import dataclass, replace
from difflib import SequenceMatcher
import re
from typing import Sequence

from lyric_aligner.text_repair import MatchDecision, SubtitleCue, _normalize_for_match
from lyric_aligner.timeline.anchor_repair import TimedCanonicalOccurrence


@dataclass(frozen=True)
class FinalTextRecoverySummary:
    isomorphic_recovery_count: int = 0
    suffix_ownership_recovery_count: int = 0
    cross_script_vocalization_recovery_count: int = 0


_CJK_VOCALIZATION_CHARS = frozenset("哈嘿嗨咿咦呀啊喔哦噢吼唷呵哎唉诶欸呦哟")
_LATIN_VOCALIZATION_CHARS = frozenset("aehiouly")


def _is_cjk_vocalization(text: str) -> bool:
    normalized = _normalize_for_match(text)
    return (
        len(normalized) >= 4
        and all("\u3400" <= char <= "\u9fff" for char in normalized)
        and set(normalized) <= _CJK_VOCALIZATION_CHARS
    )


def _is_latin_vocalization(text: str) -> bool:
    normalized = _normalize_for_match(text)
    display = text.strip()
    return (
        len(normalized) >= 4
        and bool(re.fullmatch(r"[a-z]+", normalized))
        and set(normalized) <= _LATIN_VOCALIZATION_CHARS
        and ("-" in display or len(display.split()) >= 2)
    )


def _is_cross_script_vocalization(left: str, right: str) -> bool:
    return (
        _is_cjk_vocalization(left) and _is_latin_vocalization(right)
    ) or (
        _is_latin_vocalization(left) and _is_cjk_vocalization(right)
    )


def _edit_distance_at_most_one(left: str, right: str) -> bool:
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right)) <= 1
    short, long = (left, right) if len(left) < len(right) else (right, left)
    i = j = differences = 0
    while i < len(short) and j < len(long):
        if short[i] == long[j]:
            i += 1
            j += 1
            continue
        differences += 1
        if differences > 1:
            return False
        j += 1
    return True


def _display_suffix(text: str, normalized_prefix_length: int) -> str:
    consumed = 0
    for index, char in enumerate(text):
        consumed += len(_normalize_for_match(char))
        if consumed >= normalized_prefix_length:
            return text[index + 1 :].lstrip()
    return ""


def recover_final_text_reviews(
    cues: Sequence[SubtitleCue],
    canonical: Sequence[TimedCanonicalOccurrence],
    decisions: Sequence[MatchDecision],
    *,
    allow_cross_script_vocalization: bool = True,
) -> tuple[dict[int, str], list[MatchDecision], FinalTextRecoverySummary]:
    """Recover only text that cannot change established editor ownership."""

    by_ordinal = {row.ordinal: row for row in canonical}
    updated = list(decisions)
    replacements: dict[int, str] = {}
    isomorphic = 0
    suffix = 0
    cross_script_vocalization = 0
    decision_by_cue = {row.cue_ordinal: row for row in decisions}

    for index, decision in enumerate(decisions):
        span = decision.canonical_span
        if (
            decision.action != "review"
            or span is None
            or int(span[1]) - int(span[0]) != 1
        ):
            continue
        occurrence = by_ordinal.get(int(span[0]))
        if occurrence is None:
            continue
        cue = cues[decision.cue_ordinal]
        source = cue.normalized
        target = occurrence.normalized
        if not source or not target:
            continue

        # A narrow cross-script vocalization case can have near-zero lexical
        # similarity even though sequence identity is already fixed by the
        # immediately preceding resolved canonical occurrence.  This changes
        # text only, keeps the existing one-cue/one-occurrence ownership, and
        # deliberately does not grant timing authority.
        previous_decision = decision_by_cue.get(cue.ordinal - 1)
        previous_span = (
            previous_decision.canonical_span
            if previous_decision is not None
            else None
        )
        previous_occurrence = (
            by_ordinal.get(int(previous_span[1]) - 1)
            if previous_span is not None and int(previous_span[1]) > int(previous_span[0])
            else None
        )
        if (
            allow_cross_script_vocalization
            and previous_decision is not None
            and previous_decision.action != "review"
            and previous_span is not None
            and int(previous_span[1]) == occurrence.ordinal
            and previous_occurrence is not None
            and previous_occurrence.source_ordinal == occurrence.source_ordinal
            and _is_cross_script_vocalization(cue.text, occurrence.text)
        ):
            replacements[cue.ordinal] = occurrence.text
            updated[index] = replace(
                decision,
                action="replace",
                reason="preceding_canonical_anchor_confirms_cross_script_vocalization",
                score=max(float(decision.score), 0.88),
            )
            cross_script_vocalization += 1
            continue

        # Same-length one-character corrections cannot move a token or phrase
        # across cue boundaries and are safe even when a neighbouring gap made
        # the broader Text Repair span fail closed.
        if (
            len(source) == len(target)
            and len(source) >= 6
            and _edit_distance_at_most_one(source, target)
        ):
            replacements[cue.ordinal] = occurrence.text
            updated[index] = replace(
                decision,
                action="replace",
                reason="isomorphic_single_cue_canonical_correction",
                score=max(float(decision.score), 0.92),
            )
            isomorphic += 1
            continue

        if cue.ordinal <= 0:
            continue
        previous = cues[cue.ordinal - 1].normalized
        best_prefix = 0
        for prefix_length in range(4, max(4, len(target) - 3) + 1):
            if previous.endswith(target[:prefix_length]):
                best_prefix = prefix_length
        if best_prefix < 4 or best_prefix >= len(target) - 2:
            continue
        target_suffix = target[best_prefix:]
        if (
            len(target_suffix) < 4
            or source == target_suffix
            or not _edit_distance_at_most_one(source, target_suffix)
            or SequenceMatcher(None, source, target_suffix).ratio() < 0.84
        ):
            continue
        display_suffix = _display_suffix(occurrence.text, best_prefix)
        if not display_suffix or _normalize_for_match(display_suffix) != target_suffix:
            continue
        replacements[cue.ordinal] = display_suffix
        updated[index] = replace(
            decision,
            action="replace",
            reason="editor_suffix_proves_canonical_prefix_ownership",
            score=max(float(decision.score), 0.90),
        )
        suffix += 1

    return replacements, updated, FinalTextRecoverySummary(
        isomorphic,
        suffix,
        cross_script_vocalization,
    )


__all__ = (
    "FinalTextRecoverySummary",
    "recover_final_text_reviews",
)
