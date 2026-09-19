"""Conservative editor cue-ownership preservation for Smart text output.

Canonical lyrics own text and order, but line-LRC boundaries do not own SRT
segmentation.  When Smart reconciliation has moved or duplicated a short,
clearly recognized boundary phrase across neighbouring Jianying cues, this
guard may restore the editor-proven ownership.  It never changes cue count or
timing and never invents text.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Mapping, Sequence

from lyric_aligner.text_repair import (
    MatchDecision,
    SubtitleCue,
    _normalize_for_match,
    _pair_score,
)

_SEQUENCE_REASON_PREFIX = "sequence_projection_confirms_"
_MAX_BOUNDARY_FRAGMENT_CHARS = 6
_MIN_NORMALIZED_FRAGMENT_CHARS = 2
_MIN_SCORE_IMPROVEMENT = 0.05


def _sequence_related(decision: MatchDecision | None) -> bool:
    return (
        decision is not None
        and decision.reason.startswith(_SEQUENCE_REASON_PREFIX)
    )


def _sequence_pair(left: MatchDecision | None, right: MatchDecision | None) -> bool:
    return (
        left is not None
        and right is not None
        and (_sequence_related(left) or _sequence_related(right))
    )


def _materialized_upstream_change(
    cue: SubtitleCue,
    decision: MatchDecision | None,
    current_text: str,
) -> bool:
    """Return True only when the decision itself materialized this changed text."""

    if decision is None or decision.action != "replace" or not decision.output_text:
        return False
    current = _normalize_for_match(current_text)
    return (
        current != cue.normalized
        and _normalize_for_match(str(decision.output_text)) == current
    )


def _duplicate_drop_pair(
    left_cue: SubtitleCue,
    right_cue: SubtitleCue,
    left_decision: MatchDecision | None,
    right_decision: MatchDecision | None,
    current_left: str,
    current_right: str,
) -> bool:
    """Allow duplicate cleanup only when an upstream mutation can explain it.

    Sequence reconciliation remains eligible as before.  Text Repair and other
    existing Smart text layers may also introduce a short duplicate at an editor
    boundary; in that case at least one side must prove that its current changed
    text is exactly the output materialized by its own ``replace`` decision.
    This prevents the final guard from becoming a free-standing baseline editor.
    """

    if _sequence_pair(left_decision, right_decision):
        return True
    return _materialized_upstream_change(
        left_cue, left_decision, current_left
    ) or _materialized_upstream_change(
        right_cue, right_decision, current_right
    )


def _score(source: str, candidate: str) -> float:
    return _pair_score(_normalize_for_match(source), _normalize_for_match(candidate))


def _fragment_ok(fragment: str) -> bool:
    normalized = _normalize_for_match(fragment)
    return len(normalized) >= _MIN_NORMALIZED_FRAGMENT_CHARS


def _best_forward_restore(
    original_left: str,
    original_right: str,
    current_left: str,
    current_right: str,
) -> tuple[str, str, float] | None:
    best: tuple[str, str, float] | None = None
    source = original_left.strip()
    right = current_right.strip()
    for count in range(2, min(_MAX_BOUNDARY_FRAGMENT_CHARS, len(source), len(right)) + 1):
        fragment = source[-count:]
        if not _fragment_ok(fragment):
            continue
        if not right.startswith(fragment):
            continue
        if current_left.rstrip().endswith(fragment):
            continue
        new_left = current_left.rstrip() + fragment
        new_right = right[count:].lstrip()
        if not new_right:
            continue
        before = (_score(original_left, current_left) + _score(original_right, current_right)) / 2
        after = (_score(original_left, new_left) + _score(original_right, new_right)) / 2
        gain = after - before
        if gain >= _MIN_SCORE_IMPROVEMENT and (best is None or gain > best[2]):
            best = (new_left, new_right, gain)
    return best


def _best_backward_restore(
    original_left: str,
    original_right: str,
    current_left: str,
    current_right: str,
) -> tuple[str, str, float] | None:
    best: tuple[str, str, float] | None = None
    source = original_right.strip()
    left = current_left.strip()
    for count in range(2, min(_MAX_BOUNDARY_FRAGMENT_CHARS, len(source), len(left)) + 1):
        fragment = source[:count]
        if not _fragment_ok(fragment):
            continue
        if not left.endswith(fragment):
            continue
        if current_right.lstrip().startswith(fragment):
            continue
        new_left = left[:-count].rstrip()
        new_right = fragment + current_right.lstrip()
        if not new_left:
            continue
        before = (_score(original_left, current_left) + _score(original_right, current_right)) / 2
        after = (_score(original_left, new_left) + _score(original_right, new_right)) / 2
        gain = after - before
        if gain >= _MIN_SCORE_IMPROVEMENT and (best is None or gain > best[2]):
            best = (new_left, new_right, gain)
    return best


def _best_right_duplicate_drop(
    original_left: str,
    original_right: str,
    current_left: str,
    current_right: str,
) -> tuple[str, str, float] | None:
    best: tuple[str, str, float] | None = None
    source = original_left.strip()
    left = current_left.strip()
    right = current_right.strip()
    for count in range(2, min(_MAX_BOUNDARY_FRAGMENT_CHARS, len(source), len(left), len(right)) + 1):
        fragment = source[-count:]
        if not _fragment_ok(fragment):
            continue
        if not left.endswith(fragment) or not right.startswith(fragment):
            continue
        if original_right.strip().startswith(fragment):
            continue
        new_right = right[count:].lstrip()
        if not new_right:
            continue
        before = (_score(original_left, current_left) + _score(original_right, current_right)) / 2
        after = (_score(original_left, current_left) + _score(original_right, new_right)) / 2
        gain = after - before
        if gain >= _MIN_SCORE_IMPROVEMENT and (best is None or gain > best[2]):
            best = (current_left, new_right, gain)
    return best


def _best_left_duplicate_drop(
    original_left: str,
    original_right: str,
    current_left: str,
    current_right: str,
) -> tuple[str, str, float] | None:
    best: tuple[str, str, float] | None = None
    source = original_right.strip()
    left = current_left.strip()
    right = current_right.strip()
    for count in range(2, min(_MAX_BOUNDARY_FRAGMENT_CHARS, len(source), len(left), len(right)) + 1):
        fragment = source[:count]
        if not _fragment_ok(fragment):
            continue
        if not left.endswith(fragment) or not right.startswith(fragment):
            continue
        if original_left.strip().endswith(fragment):
            continue
        new_left = left[:-count].rstrip()
        if not new_left:
            continue
        before = (_score(original_left, current_left) + _score(original_right, current_right)) / 2
        after = (_score(original_left, new_left) + _score(original_right, current_right)) / 2
        gain = after - before
        if gain >= _MIN_SCORE_IMPROVEMENT and (best is None or gain > best[2]):
            best = (new_left, current_right, gain)
    return best


def _guarded_decision(
    cue: SubtitleCue,
    decision: MatchDecision,
    text: str,
) -> MatchDecision:
    return replace(
        decision,
        canonical_ordinal=None,
        score=min(float(decision.score), 0.90),
        action="unchanged" if cue.normalized == _normalize_for_match(text) else "replace",
        reason="editor_boundary_ownership_restored",
        cue_span=(cue.ordinal, cue.ordinal + 1),
        canonical_span=None,
        canonical_text=text,
        output_text=text,
        edit_operations=(),
    )


def restore_editor_cue_ownership(
    cues: Sequence[SubtitleCue],
    decisions: Sequence[MatchDecision],
    replacements: Mapping[int, str],
) -> tuple[dict[int, str], list[MatchDecision], int]:
    """Repartition already-selected text across existing cue boundaries only.

    Ordinary boundary restoration remains restricted to Sequence reconciliation
    and preserves the adjacent pair's combined lyric stream exactly. A narrow
    duplicate-drop path may remove one 2-6 character copy only when the same
    fragment is present on both sides, the original editor recognition assigns
    it to one side, and either Sequence reconciliation or a materialized upstream
    ``replace`` decision can explain the duplicate. Cue count and timing never
    move.
    """

    output = {item.cue_ordinal: item for item in decisions}
    changed: dict[int, str] = {}
    working = {
        cue.ordinal: str(replacements.get(cue.ordinal, cue.text))
        for cue in cues
    }
    count = 0

    for index in range(len(cues) - 1):
        left_cue = cues[index]
        right_cue = cues[index + 1]
        left_decision = output.get(left_cue.ordinal)
        right_decision = output.get(right_cue.ordinal)
        if left_decision is None or right_decision is None:
            continue

        current_left = working[left_cue.ordinal]
        current_right = working[right_cue.ordinal]
        candidates: list[tuple[str, str, float, str]] = []
        if _duplicate_drop_pair(
            left_cue,
            right_cue,
            left_decision,
            right_decision,
            current_left,
            current_right,
        ):
            for item in (
                _best_right_duplicate_drop(left_cue.text, right_cue.text, current_left, current_right),
                _best_left_duplicate_drop(left_cue.text, right_cue.text, current_left, current_right),
            ):
                if item is not None:
                    candidates.append((*item, "duplicate_drop"))
        if _sequence_pair(left_decision, right_decision):
            for item in (
                _best_forward_restore(left_cue.text, right_cue.text, current_left, current_right),
                _best_backward_restore(left_cue.text, right_cue.text, current_left, current_right),
            ):
                if item is not None:
                    candidates.append((*item, "boundary_move"))
        if not candidates:
            continue

        new_left, new_right, _, kind = max(candidates, key=lambda item: item[2])
        old_combined = _normalize_for_match(current_left + current_right)
        new_combined = _normalize_for_match(new_left + new_right)
        if kind == "boundary_move" and new_combined != old_combined:
            continue
        if kind == "duplicate_drop":
            removed = len(old_combined) - len(new_combined)
            if not (_MIN_NORMALIZED_FRAGMENT_CHARS <= removed <= _MAX_BOUNDARY_FRAGMENT_CHARS):
                continue

        working[left_cue.ordinal] = new_left
        working[right_cue.ordinal] = new_right
        changed[left_cue.ordinal] = new_left
        changed[right_cue.ordinal] = new_right
        output[left_cue.ordinal] = _guarded_decision(left_cue, left_decision, new_left)
        output[right_cue.ordinal] = _guarded_decision(right_cue, right_decision, new_right)
        count += 1

    ordered = [output.get(item.cue_ordinal, item) for item in decisions]
    return changed, ordered, count
