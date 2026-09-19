"""Shadow lexical evidence for adjacent-track transition review.

The production transition probe answers an audio-source question and can remain
ambiguous when a song contains repeated musical material. This module answers a
different, narrower question: does the editor subtitle stream from the final mix
provide unique lexical evidence that lyrics from the left occurrence finish before
lyrics from the right occurrence begin?

This module intentionally grants no automatic review authority. It produces a
task-bound witness that a reviewer can use with the existing replayable review
decision contract. Instrumental crossfades are therefore not confused with lyric
overlap, and an editor transcript is never promoted to canonical text truth.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from lyric_aligner.text_repair import SubtitleCue, _normalize_for_match


SCHEMA_VERSION = "adjacent-transition-editor-lexical-evidence-1.0"
POLICY_ID = "adjacent-transition-editor-lexical-witness-1.0"

_TIMING = re.compile(
    r"^\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
)


class TransitionLexicalEvidenceError(ValueError):
    """Raised when transition evidence cannot be safely bound."""


@dataclass(frozen=True)
class TransitionLexicalPolicy:
    editor_context_ms: int = 25_000
    canonical_context_ms: int = 30_000
    maximum_canonical_witness_distance_ms: int = 45_000
    maximum_canonical_lines_per_side: int = 8
    canonical_boundary_slack_ms: int = 2_500
    boundary_witness_slack_ms: int = 8_000
    maximum_sequential_gap_ms: int = 18_000
    overlap_tolerance_ms: int = 120
    minimum_cue_normalized_chars: int = 6
    minimum_side_normalized_chars: int = 10

    def validate(self) -> None:
        positive = (
            self.editor_context_ms,
            self.canonical_context_ms,
            self.maximum_canonical_witness_distance_ms,
            self.maximum_canonical_lines_per_side,
            self.boundary_witness_slack_ms,
            self.maximum_sequential_gap_ms,
            self.minimum_cue_normalized_chars,
            self.minimum_side_normalized_chars,
        )
        if any(int(value) <= 0 for value in positive):
            raise TransitionLexicalEvidenceError(
                "transition lexical policy values must be positive"
            )
        if self.canonical_boundary_slack_ms < 0 or self.overlap_tolerance_ms < 0:
            raise TransitionLexicalEvidenceError(
                "transition lexical slack/tolerance must be >= 0"
            )


def _text_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _cue_bounds(cue: SubtitleCue) -> tuple[int, int]:
    match = _TIMING.match(cue.timing)
    if match is None:
        raise TransitionLexicalEvidenceError("editor cue has unsupported SRT timing")
    values = [int(value) for value in match.groups()]
    start = (((values[0] * 60 + values[1]) * 60 + values[2]) * 1000) + values[3]
    end = (((values[4] * 60 + values[5]) * 60 + values[6]) * 1000) + values[7]
    if start < 0 or end <= start:
        raise TransitionLexicalEvidenceError("editor cue has invalid timing")
    return start, end


def _timeline_lines(
    timeline: Mapping[str, Any],
    *,
    side: str,
    boundary_ms: int,
    policy: TransitionLexicalPolicy,
) -> list[dict[str, Any]]:
    result = timeline.get("result")
    if not isinstance(result, Mapping):
        raise TransitionLexicalEvidenceError("timeline is missing result")
    raw_lines = result.get("lines")
    if not isinstance(raw_lines, list) or not raw_lines:
        raise TransitionLexicalEvidenceError("timeline has no canonical lines")
    candidates: list[tuple[int, dict[str, Any]]] = []
    for raw in raw_lines:
        if not isinstance(raw, Mapping):
            continue
        start = raw.get("mix_start_ms")
        text = str(raw.get("text") or "")
        if isinstance(start, bool) or not isinstance(start, int) or not text:
            continue
        if side == "left":
            if start > boundary_ms + policy.canonical_boundary_slack_ms:
                continue
            distance = max(0, boundary_ms - start)
        elif side == "right":
            if start < boundary_ms - policy.canonical_boundary_slack_ms:
                continue
            distance = max(0, start - boundary_ms)
        else:
            raise TransitionLexicalEvidenceError("side must be left or right")
        if distance > policy.maximum_canonical_witness_distance_ms:
            continue
        normalized = _normalize_for_match(text)
        if not normalized:
            continue
        candidates.append(
            (
                distance,
                {
                    "canonical_line_index": raw.get("canonical_line_index"),
                    "mix_start_ms": start,
                    "mix_end_ms": raw.get("mix_end_ms"),
                    "normalized": normalized,
                    "normalized_length": len(normalized),
                    "text_sha256": _text_sha(text),
                },
            )
        )
    candidates.sort(
        key=lambda item: (
            0 if item[0] <= policy.canonical_context_ms else 1,
            item[0],
            int(item[1]["mix_start_ms"]),
        )
    )
    selected = [
        row for _, row in candidates[: policy.maximum_canonical_lines_per_side]
    ]
    selected.sort(key=lambda row: int(row["mix_start_ms"]))
    return selected


def _side_stream(lines: Sequence[Mapping[str, Any]]) -> str:
    return "".join(str(line.get("normalized") or "") for line in lines)


def _editor_witnesses(
    editor_cues: Sequence[SubtitleCue],
    *,
    boundary_ms: int,
    left_stream: str,
    right_stream: str,
    policy: TransitionLexicalPolicy,
) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = {
        "left": [],
        "right": [],
        "ambiguous": [],
        "unmatched": [],
    }
    window_start = max(0, boundary_ms - policy.editor_context_ms)
    window_end = boundary_ms + policy.editor_context_ms
    for cue in editor_cues:
        start, end = _cue_bounds(cue)
        if end <= window_start or start >= window_end:
            continue
        key = _normalize_for_match(cue.text)
        if len(key) < policy.minimum_cue_normalized_chars:
            continue
        left = key in left_stream
        right = key in right_stream
        row = {
            "cue_ordinal": cue.ordinal,
            "cue_number": cue.number,
            "start_ms": start,
            "end_ms": end,
            "normalized_length": len(key),
            "text_sha256": _text_sha(cue.text),
        }
        if left and not right:
            output["left"].append(row)
        elif right and not left:
            output["right"].append(row)
        elif left and right:
            output["ambiguous"].append(row)
        else:
            output["unmatched"].append(row)
    for rows in output.values():
        rows.sort(key=lambda row: (int(row["start_ms"]), int(row["cue_ordinal"])))
    return output


def build_transition_lexical_evidence(
    *,
    issue: Mapping[str, Any],
    transition: Mapping[str, Any],
    left_occurrence: Mapping[str, Any],
    right_occurrence: Mapping[str, Any],
    left_timeline: Mapping[str, Any],
    right_timeline: Mapping[str, Any],
    editor_cues: Sequence[SubtitleCue],
    policy: TransitionLexicalPolicy | None = None,
) -> dict[str, Any]:
    """Build one shadow evidence row for an adjacent transition ambiguity."""

    policy = policy or TransitionLexicalPolicy()
    policy.validate()
    if issue.get("kind") != "transition_ambiguity":
        raise TransitionLexicalEvidenceError("only transition_ambiguity is supported")
    if issue.get("code") != "ambiguous_source_occurrence":
        raise TransitionLexicalEvidenceError("unsupported transition ambiguity code")
    left_id = str(transition.get("left_occurrence_id") or "")
    right_id = str(transition.get("right_occurrence_id") or "")
    if not left_id or not right_id or left_id == right_id:
        raise TransitionLexicalEvidenceError("invalid adjacent occurrence pair")
    if (
        str(issue.get("left_occurrence_id") or "") != left_id
        or str(issue.get("right_occurrence_id") or "") != right_id
    ):
        raise TransitionLexicalEvidenceError("issue/transition pair mismatch")
    if str(left_occurrence.get("occurrence_id") or "") != left_id:
        raise TransitionLexicalEvidenceError("left occurrence identity mismatch")
    if str(right_occurrence.get("occurrence_id") or "") != right_id:
        raise TransitionLexicalEvidenceError("right occurrence identity mismatch")
    if bool(left_occurrence.get("mapping_blocked")) or bool(
        right_occurrence.get("mapping_blocked")
    ):
        raise TransitionLexicalEvidenceError(
            "transition lexical witness requires unblocked mappings"
        )
    left_result = left_timeline.get("result")
    right_result = right_timeline.get("result")
    if not isinstance(left_result, Mapping) or str(
        left_result.get("occurrence_id") or ""
    ) != left_id:
        raise TransitionLexicalEvidenceError("left timeline identity mismatch")
    if not isinstance(right_result, Mapping) or str(
        right_result.get("occurrence_id") or ""
    ) != right_id:
        raise TransitionLexicalEvidenceError("right timeline identity mismatch")

    try:
        nominal_seconds = float(transition["nominal_boundary"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TransitionLexicalEvidenceError(
            "transition has invalid nominal boundary"
        ) from exc
    if nominal_seconds < 0:
        raise TransitionLexicalEvidenceError(
            "transition nominal boundary must be non-negative"
        )
    boundary_ms = int(round(nominal_seconds * 1000.0))

    left_lines = _timeline_lines(
        left_timeline, side="left", boundary_ms=boundary_ms, policy=policy
    )
    right_lines = _timeline_lines(
        right_timeline, side="right", boundary_ms=boundary_ms, policy=policy
    )
    left_stream = _side_stream(left_lines)
    right_stream = _side_stream(right_lines)
    witnesses = _editor_witnesses(
        editor_cues,
        boundary_ms=boundary_ms,
        left_stream=left_stream,
        right_stream=right_stream,
        policy=policy,
    )

    left_support = witnesses["left"]
    right_support = witnesses["right"]
    left_chars = sum(int(row["normalized_length"]) for row in left_support)
    right_chars = sum(int(row["normalized_length"]) for row in right_support)
    enough_identity = bool(
        left_support
        and right_support
        and left_chars >= policy.minimum_side_normalized_chars
        and right_chars >= policy.minimum_side_normalized_chars
    )

    last_left_end = max(
        (int(row["end_ms"]) for row in left_support), default=None
    )
    first_right_start = min(
        (int(row["start_ms"]) for row in right_support), default=None
    )
    sequential_gap_ms = (
        None
        if last_left_end is None or first_right_start is None
        else first_right_start - last_left_end
    )
    lexical_order_clear = bool(
        enough_identity
        and sequential_gap_ms is not None
        and sequential_gap_ms >= -policy.overlap_tolerance_ms
        and sequential_gap_ms <= policy.maximum_sequential_gap_ms
        and last_left_end is not None
        and first_right_start is not None
        and last_left_end <= boundary_ms + policy.boundary_witness_slack_ms
        and first_right_start >= boundary_ms - policy.boundary_witness_slack_ms
        and all(
            int(row["end_ms"])
            <= first_right_start + policy.overlap_tolerance_ms
            for row in left_support
        )
        and all(
            int(row["start_ms"])
            >= last_left_end - policy.overlap_tolerance_ms
            for row in right_support
        )
    )

    if lexical_order_clear:
        recommendation = "clear_candidate"
        reason = (
            "unique final-mix editor lexical witnesses support sequential "
            "left-to-right lyric ownership"
        )
    elif not enough_identity:
        recommendation = "review"
        reason = (
            "insufficient unique editor lexical identity on one or both "
            "adjacent occurrences"
        )
    else:
        recommendation = "review"
        reason = (
            "editor lexical witnesses do not prove a non-overlapping "
            "left-to-right lyric order"
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "issue_candidate_id": str(issue.get("candidate_id") or ""),
        "left_occurrence_id": left_id,
        "right_occurrence_id": right_id,
        "nominal_boundary_ms": boundary_ms,
        "policy": asdict(policy),
        "canonical_local": {
            "left_line_count": len(left_lines),
            "right_line_count": len(right_lines),
            "left_normalized_char_count": len(left_stream),
            "right_normalized_char_count": len(right_stream),
            "left_lines": left_lines,
            "right_lines": right_lines,
        },
        "editor_witnesses": witnesses,
        "identity_support": {
            "left_unique_cue_count": len(left_support),
            "right_unique_cue_count": len(right_support),
            "left_unique_normalized_chars": left_chars,
            "right_unique_normalized_chars": right_chars,
            "ambiguous_cue_count": len(witnesses["ambiguous"]),
            "unmatched_cue_count": len(witnesses["unmatched"]),
            "enough_identity": enough_identity,
        },
        "order_support": {
            "last_left_editor_end_ms": last_left_end,
            "first_right_editor_start_ms": first_right_start,
            "sequential_gap_ms": sequential_gap_ms,
            "lexical_order_clear": lexical_order_clear,
        },
        "recommendation": recommendation,
        "recommendation_reason": reason,
        "automatic_authority_granted": False,
        "confirmed_overlap_authority_granted": False,
        "timing_mutation_performed": False,
    }


__all__ = (
    "POLICY_ID",
    "SCHEMA_VERSION",
    "TransitionLexicalEvidenceError",
    "TransitionLexicalPolicy",
    "build_transition_lexical_evidence",
)
