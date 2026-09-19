"""Deterministic final-mix window policies for full-sequence lyric alignment."""

from __future__ import annotations

from typing import Sequence


FULL_SEQUENCE_ALIGNMENT_WINDOW_POLICY_ID = "editor_cue_plus_1500ms_clamped_to_mix_v1"
FULL_SEQUENCE_ALIGNMENT_CONTEXT_MS = 1500


class AlignmentWindowPolicyError(ValueError):
    pass


def full_sequence_alignment_window_ms(
    *,
    cue_start_ms: int,
    cue_end_ms: int,
    mix_duration_ms: int,
) -> list[int]:
    try:
        start = int(cue_start_ms)
        end = int(cue_end_ms)
        duration = int(mix_duration_ms)
    except (TypeError, ValueError) as exc:
        raise AlignmentWindowPolicyError("alignment window arguments must be integers") from exc
    if start < 0 or end <= start:
        raise AlignmentWindowPolicyError("owning cue interval is invalid")
    if duration <= 0 or start >= duration:
        raise AlignmentWindowPolicyError("owning cue lies outside final mix")
    clamped_end = min(end, duration)
    if clamped_end <= start:
        raise AlignmentWindowPolicyError("owning cue has no final-mix coverage")
    window = [
        max(0, start - FULL_SEQUENCE_ALIGNMENT_CONTEXT_MS),
        min(duration, clamped_end + FULL_SEQUENCE_ALIGNMENT_CONTEXT_MS),
    ]
    if window[1] <= window[0]:
        raise AlignmentWindowPolicyError("derived full-sequence alignment window is empty")
    return window


def window_matches_policy(
    window_ms: Sequence[int],
    *,
    cue_start_ms: int,
    cue_end_ms: int,
    mix_duration_ms: int,
) -> bool:
    try:
        actual = [int(window_ms[0]), int(window_ms[1])]
    except (IndexError, TypeError, ValueError):
        return False
    return actual == full_sequence_alignment_window_ms(
        cue_start_ms=cue_start_ms,
        cue_end_ms=cue_end_ms,
        mix_duration_ms=mix_duration_ms,
    )
