"""Final deterministic adjudication for adjacent-source transition reviews.

The resolver layers three independent, bounded proofs over the existing lexical
resolver:

1. clean local source-audio handoff (left-only -> right-only, no same-window overlap),
2. lyric-free editor gap with a right-canonical onset that agrees with the next editor cue,
3. confirmed vocal overlap only when same-window dual-source retrieval lands inside
   closed canonical vocal intervals on both source clocks.

It never infers an overlap from a musical crossfade alone and never mutates subtitle
text/timing directly; it only emits the official review-decision contract.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from lyric_aligner.review.transition_lyric_resolution import authorize_transition_lyrics
from lyric_aligner.text_repair import SubtitleCue, _normalize_for_match

SCHEMA_VERSION = "transition-final-resolution-1.0"
POLICY_ID = "lexical-local-audio-editor-gap-vocal-overlap-1.0"


class TransitionFinalResolutionError(ValueError):
    pass


@dataclass(frozen=True)
class Policy:
    boundary_tolerance_seconds: float = 1.5
    local_sequence_pre_left_min: int = 2
    local_sequence_post_right_min: int = 1
    editor_boundary_overrun_ms: int = 250
    editor_min_right_gap_ms: int = 1000
    editor_right_timeline_match_ms: int = 1500
    editor_left_positional_window_seconds: float = 6.0
    overlap_min_windows: int = 2
    overlap_max_center_step_seconds: float = 3.5
    overlap_min_projected_seconds: float = 0.20


def _text(value: Any) -> str:
    return str(value or "").strip()


def _cue_bounds_ms(cue: SubtitleCue) -> tuple[int, int]:
    left, right = [part.strip() for part in cue.timing.split("-->", 1)]

    def parse(value: str) -> int:
        hh, mm, rest = value.replace(".", ",").split(":")
        ss, milli = rest.split(",")
        return (int(hh) * 3600 + int(mm) * 60 + int(ss)) * 1000 + int(milli)

    return parse(left), parse(right)


def _paired(positional_row: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = positional_row.get("paired_evidence")
    if not isinstance(rows, list) or not rows:
        return []
    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        try:
            center = float(row["mix_center"])
        except (KeyError, TypeError, ValueError):
            continue
        result.append(
            {
                "mix_center": center,
                "left_strong": bool(row.get("left_strong")),
                "right_strong": bool(row.get("right_strong")),
                "overlap_like": bool(row.get("overlap_like")),
            }
        )
    result.sort(key=lambda row: row["mix_center"])
    return result


def _local_audio_sequence_clear(
    positional_row: Mapping[str, Any], *, boundary: float, policy: Policy
) -> tuple[bool, dict[str, Any]]:
    paired = _paired(positional_row)
    if not paired or any(row["overlap_like"] for row in paired):
        return False, {"reason": "local_audio_overlap_or_missing"}
    pre_limit = boundary - policy.boundary_tolerance_seconds
    post_limit = boundary + policy.boundary_tolerance_seconds
    pre_left = [
        row for row in paired
        if row["mix_center"] <= pre_limit and row["left_strong"] and not row["right_strong"]
    ]
    # The center window may already be wholly owned by the right source.  Count it
    # as post-right when no left support remains at/after the nominal boundary.
    post_right = [
        row for row in paired
        if row["mix_center"] >= boundary and row["right_strong"] and not row["left_strong"]
    ]
    right_before = any(
        row["mix_center"] <= pre_limit and row["right_strong"] for row in paired
    )
    left_after = any(
        row["mix_center"] >= post_limit and row["left_strong"] for row in paired
    )
    clear = bool(
        len(pre_left) >= policy.local_sequence_pre_left_min
        and len(post_right) >= policy.local_sequence_post_right_min
        and not right_before
        and not left_after
    )
    return clear, {
        "reason": "clean_local_source_handoff" if clear else "local_source_handoff_insufficient",
        "pre_left_only_centers": [row["mix_center"] for row in pre_left],
        "post_right_only_centers": [row["mix_center"] for row in post_right],
        "right_before": right_before,
        "left_after": left_after,
    }


def _window_by_side(
    positional_row: Mapping[str, Any], *, center: float, side: str
) -> Mapping[str, Any] | None:
    rows = positional_row.get("windows")
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, Mapping) or row.get("side") != side:
            continue
        try:
            row_center = float(row.get("global_mix_center"))
        except (TypeError, ValueError):
            continue
        if abs(row_center - center) <= 1e-6:
            return row
    return None


def _timeline_lines(timeline: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    result = timeline.get("result")
    if not isinstance(result, Mapping):
        return []
    lines = result.get("lines")
    return [row for row in lines if isinstance(row, Mapping)] if isinstance(lines, list) else []


def _source_line_at(
    timeline: Mapping[str, Any], source_center_seconds: float
) -> Mapping[str, Any] | None:
    point_ms = source_center_seconds * 1000.0
    matches: list[Mapping[str, Any]] = []
    for row in _timeline_lines(timeline):
        start = row.get("source_start_ms")
        end = row.get("source_end_ms")
        if isinstance(start, bool) or isinstance(end, bool):
            continue
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            continue
        if float(start) <= point_ms <= float(end) and _normalize_for_match(_text(row.get("text"))):
            matches.append(row)
    if len(matches) != 1:
        return None
    return matches[0]


def _projected_mix_interval(row: Mapping[str, Any]) -> tuple[float, float] | None:
    start = row.get("mix_start_ms")
    end = row.get("mix_end_ms")
    if isinstance(start, bool) or isinstance(end, bool):
        return None
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        return None
    if float(end) <= float(start):
        return None
    return float(start) / 1000.0, float(end) / 1000.0


def _canonical_vocal_overlap(
    positional_row: Mapping[str, Any],
    *,
    left_timeline: Mapping[str, Any],
    right_timeline: Mapping[str, Any],
    issue_start: float,
    issue_end: float,
    policy: Policy,
) -> tuple[tuple[float, float] | None, dict[str, Any]]:
    paired = [row for row in _paired(positional_row) if row["overlap_like"]]
    if len(paired) < policy.overlap_min_windows:
        return None, {"reason": "insufficient_same_window_dual_source_support"}
    centers = [row["mix_center"] for row in paired]
    if any(
        right - left > policy.overlap_max_center_step_seconds
        for left, right in zip(centers, centers[1:])
    ):
        return None, {"reason": "dual_source_windows_not_contiguous", "centers": centers}

    interval_parts: list[tuple[float, float]] = []
    line_witnesses: list[dict[str, Any]] = []
    for center in centers:
        left_window = _window_by_side(positional_row, center=center, side="left")
        right_window = _window_by_side(positional_row, center=center, side="right")
        if not left_window or not right_window or not left_window.get("strong") or not right_window.get("strong"):
            return None, {"reason": "paired_overlap_window_missing_strong_side", "center": center}
        left_local = left_window.get("local")
        right_local = right_window.get("local")
        if not isinstance(left_local, Mapping) or not isinstance(right_local, Mapping):
            return None, {"reason": "paired_overlap_window_missing_local_retrieval", "center": center}
        left_top = left_local.get("top1")
        right_top = right_local.get("top1")
        if not isinstance(left_top, Mapping) or not isinstance(right_top, Mapping):
            return None, {"reason": "paired_overlap_window_missing_top1", "center": center}
        try:
            left_source = float(left_top["source_center"])
            right_source = float(right_top["source_center"])
        except (KeyError, TypeError, ValueError):
            return None, {"reason": "paired_overlap_window_source_center_invalid", "center": center}
        left_line = _source_line_at(left_timeline, left_source)
        right_line = _source_line_at(right_timeline, right_source)
        if left_line is None or right_line is None:
            return None, {"reason": "dual_source_support_not_inside_unique_canonical_vocal", "center": center}
        left_mix = _projected_mix_interval(left_line)
        right_mix = _projected_mix_interval(right_line)
        if left_mix is None or right_mix is None:
            return None, {"reason": "canonical_vocal_interval_open_or_invalid", "center": center}
        start = max(left_mix[0], right_mix[0], issue_start)
        end = min(left_mix[1], right_mix[1], issue_end)
        if end - start < policy.overlap_min_projected_seconds:
            return None, {"reason": "canonical_vocal_intersection_too_short", "center": center}
        # The directly matched window center must actually lie inside (or within one
        # half-window tolerance of) the projected vocal intersection.
        if not (start - policy.boundary_tolerance_seconds <= center <= end + policy.boundary_tolerance_seconds):
            return None, {"reason": "dual_source_center_outside_vocal_intersection", "center": center}
        interval_parts.append((start, end))
        line_witnesses.append(
            {
                "mix_center": center,
                "left_source_center": left_source,
                "right_source_center": right_source,
                "left_canonical_line_index": left_line.get("canonical_line_index"),
                "right_canonical_line_index": right_line.get("canonical_line_index"),
                "left_text_sha256": left_line.get("text") and hashlib.sha256(_text(left_line.get("text")).encode("utf-8")).hexdigest(),
                "right_text_sha256": right_line.get("text") and hashlib.sha256(_text(right_line.get("text")).encode("utf-8")).hexdigest(),
                "projected_intersection": [start, end],
            }
        )
    ordered_parts = sorted(interval_parts)
    for (_, left_end), (right_start, _) in zip(ordered_parts, ordered_parts[1:]):
        if right_start > left_end + policy.boundary_tolerance_seconds:
            return None, {
                "reason": "canonical_vocal_intersections_not_contiguous",
                "interval_parts": [[start, end] for start, end in ordered_parts],
            }
    merged_start = min(start for start, _ in ordered_parts)
    merged_end = max(end for _, end in ordered_parts)
    if merged_end <= merged_start:
        return None, {"reason": "merged_vocal_overlap_invalid"}
    return (merged_start, merged_end), {
        "reason": "direct_dual_source_support_inside_both_canonical_vocal_intervals",
        "centers": centers,
        "line_witnesses": line_witnesses,
    }


def _right_first_line(timeline: Mapping[str, Any]) -> Mapping[str, Any] | None:
    candidates = [
        row for row in _timeline_lines(timeline)
        if isinstance(row.get("mix_start_ms"), (int, float))
        and not isinstance(row.get("mix_start_ms"), bool)
        and _normalize_for_match(_text(row.get("text")))
    ]
    return min(candidates, key=lambda row: float(row["mix_start_ms"])) if candidates else None


def _editor_gap_clear(
    *,
    editor_cues: Sequence[SubtitleCue],
    boundary: float,
    positional_row: Mapping[str, Any],
    lexical_row: Mapping[str, Any],
    right_timeline: Mapping[str, Any],
    policy: Policy,
) -> tuple[bool, dict[str, Any]]:
    paired = _paired(positional_row)
    if not paired or any(row["overlap_like"] for row in paired):
        return False, {"reason": "editor_gap_has_or_lacks_positional_overlap_context"}
    boundary_ms = int(round(boundary * 1000.0))
    cue_rows = [(*_cue_bounds_ms(cue), cue) for cue in editor_cues]
    before = [row for row in cue_rows if row[0] <= boundary_ms]
    after = [row for row in cue_rows if row[0] > boundary_ms]
    if not before or not after:
        return False, {"reason": "editor_gap_missing_neighbor_cue"}
    prev_start, prev_end, prev_cue = max(before, key=lambda row: row[0])
    next_start, next_end, next_cue = min(after, key=lambda row: row[0])
    if prev_end > boundary_ms + policy.editor_boundary_overrun_ms:
        return False, {"reason": "editor_cue_crosses_boundary_too_far", "previous_end_ms": prev_end}
    if next_start < boundary_ms + policy.editor_min_right_gap_ms:
        return False, {"reason": "editor_right_gap_too_short", "next_start_ms": next_start}

    identity = lexical_row.get("identity_support")
    order = lexical_row.get("order_support")
    left_identity = bool(
        isinstance(identity, Mapping)
        and int(identity.get("left_unique_cue_count") or 0) >= 1
        and isinstance(order, Mapping)
        and isinstance(order.get("last_left_editor_end_ms"), int)
        and int(order["last_left_editor_end_ms"]) <= boundary_ms + policy.editor_boundary_overrun_ms
    )
    left_positional = any(
        row["left_strong"]
        and not row["right_strong"]
        and boundary - policy.editor_left_positional_window_seconds <= row["mix_center"] <= boundary + policy.boundary_tolerance_seconds
        for row in paired
    )
    if not (left_identity or left_positional):
        return False, {"reason": "editor_gap_left_occurrence_not_supported"}

    first_right = _right_first_line(right_timeline)
    if first_right is None:
        return False, {"reason": "editor_gap_right_timeline_has_no_first_line"}
    first_right_ms = int(round(float(first_right["mix_start_ms"])))
    if abs(first_right_ms - next_start) > policy.editor_right_timeline_match_ms:
        return False, {
            "reason": "editor_gap_right_timeline_editor_onset_disagree",
            "right_timeline_start_ms": first_right_ms,
            "next_editor_start_ms": next_start,
        }
    canonical_norm = _normalize_for_match(_text(first_right.get("text")))
    editor_norm = _normalize_for_match(next_cue.text)
    if not canonical_norm or not editor_norm or not (
        canonical_norm in editor_norm or editor_norm in canonical_norm
    ):
        return False, {"reason": "editor_gap_right_timeline_editor_text_disagree"}

    right_before = any(
        row["right_strong"] and row["mix_center"] <= boundary - policy.boundary_tolerance_seconds
        for row in paired
    )
    left_after = any(
        row["left_strong"] and row["mix_center"] >= boundary + policy.boundary_tolerance_seconds
        for row in paired
    )
    if right_before or left_after:
        return False, {"reason": "editor_gap_positional_cross_support"}
    return True, {
        "reason": "lyric_free_editor_gap_with_right_canonical_onset_witness",
        "previous_editor": [prev_start, prev_end, prev_cue.ordinal],
        "next_editor": [next_start, next_end, next_cue.ordinal],
        "right_timeline_start_ms": first_right_ms,
        "left_identity_support": left_identity,
        "left_positional_support": left_positional,
    }


def authorize_final_transitions(
    *,
    run: Mapping[str, Any],
    base_run_artifact_id: str,
    run_sha256: str,
    source_srt_sha256: str,
    lexical: Mapping[str, Any],
    positional: Mapping[str, Any],
    timelines: Mapping[str, Mapping[str, Any]],
    editor_cues: Sequence[SubtitleCue],
    policy: Policy | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    policy = policy or Policy()
    decisions, base_report = authorize_transition_lyrics(
        run=run,
        base_run_artifact_id=base_run_artifact_id,
        run_sha256=run_sha256,
        source_srt_sha256=source_srt_sha256,
        lexical=lexical,
        positional=positional,
    )
    transitions = run.get("transitions")
    if not isinstance(transitions, list):
        raise TransitionFinalResolutionError("base run transitions are invalid")
    lexical_rows = lexical.get("transitions")
    positional_rows = positional.get("evidence")
    if not isinstance(lexical_rows, list) or not isinstance(positional_rows, list):
        raise TransitionFinalResolutionError("evidence populations are invalid")
    lexical_by_pair = {
        (_text(row.get("left_occurrence_id")), _text(row.get("right_occurrence_id"))): row
        for row in lexical_rows if isinstance(row, Mapping)
    }
    positional_by_index = {
        int(row["transition_index"]): row
        for row in positional_rows
        if isinstance(row, Mapping) and isinstance(row.get("transition_index"), int)
    }
    issue_by_pair = {
        (_text(item["issue"].get("left_occurrence_id")), _text(item["issue"].get("right_occurrence_id"))): item
        for item in decisions.get("review_items", [])
        if isinstance(item, Mapping) and isinstance(item.get("issue"), Mapping)
    }
    evidence_rows: list[dict[str, Any]] = []
    for index, transition in enumerate(transitions, start=1):
        if not isinstance(transition, Mapping):
            raise TransitionFinalResolutionError("transition row is invalid")
        left_id = _text(transition.get("left_occurrence_id"))
        right_id = _text(transition.get("right_occurrence_id"))
        pair = (left_id, right_id)
        item = issue_by_pair.get(pair)
        lexical_row = lexical_by_pair.get(pair)
        positional_row = positional_by_index.get(index)
        left_timeline = timelines.get(left_id)
        right_timeline = timelines.get(right_id)
        if not item or lexical_row is None or positional_row is None or left_timeline is None or right_timeline is None:
            raise TransitionFinalResolutionError(f"transition {index} is missing exact bound evidence")
        existing = item.get("decision")
        row: dict[str, Any] = {
            "transition_index": index,
            "left_occurrence_id": left_id,
            "right_occurrence_id": right_id,
            "nominal_boundary": float(transition["nominal_boundary"]),
            "resolution": "unresolved",
            "rule": None,
            "detail": {},
            "timing_mutation_performed": False,
        }
        if isinstance(existing, Mapping) and existing.get("action") == "resolved_clear":
            row.update({"resolution": "resolved_clear", "rule": "lexical_sequence_clear", "detail": {"inherited_from": base_report["policy_id"]}})
            evidence_rows.append(row)
            continue

        issue = item["issue"]
        issue_start = float(issue["interval_start"])
        issue_end = float(issue["interval_end"])
        vocal_interval, vocal_detail = _canonical_vocal_overlap(
            positional_row,
            left_timeline=left_timeline,
            right_timeline=right_timeline,
            issue_start=issue_start,
            issue_end=issue_end,
            policy=policy,
        )
        if vocal_interval is not None:
            item["decision"] = {
                "action": "confirmed_overlap",
                "rationale": (
                    "Two adjacent final-mix windows independently match both source tracks, and each matched "
                    "source position falls inside a closed canonical vocal interval on its own source clock. "
                    "The projected vocal intervals overlap inside the reviewed transition candidate, so this is "
                    "a vocal overlap requiring recomposition rather than a merely instrumental crossfade."
                ),
                "confirmed_interval": [vocal_interval[0], vocal_interval[1]],
            }
            row.update({"resolution": "confirmed_overlap", "rule": "direct_dual_source_canonical_vocal_overlap", "detail": vocal_detail, "confirmed_interval": [vocal_interval[0], vocal_interval[1]]})
            evidence_rows.append(row)
            continue

        local_clear, local_detail = _local_audio_sequence_clear(
            positional_row, boundary=float(transition["nominal_boundary"]), policy=policy
        )
        if local_clear:
            item["decision"] = {
                "action": "resolved_clear",
                "rationale": (
                    "Mapping-constrained final-mix retrieval shows a clean adjacent-source handoff: at least two "
                    "pre-boundary windows support only the left source and post/boundary windows support only the "
                    "right source, with no same-window dual-source support or cross-support."
                ),
            }
            row.update({"resolution": "resolved_clear", "rule": "clean_local_source_handoff", "detail": local_detail})
            evidence_rows.append(row)
            continue

        gap_clear, gap_detail = _editor_gap_clear(
            editor_cues=editor_cues,
            boundary=float(transition["nominal_boundary"]),
            positional_row=positional_row,
            lexical_row=lexical_row,
            right_timeline=right_timeline,
            policy=policy,
        )
        if gap_clear:
            item["decision"] = {
                "action": "resolved_clear",
                "rationale": (
                    "The final-mix editor stream contains no competing lyric across this boundary: the previous cue "
                    "ends at/before the boundary tolerance, the next cue begins after a real lyric-free gap, its text "
                    "and onset agree with the right occurrence's first canonical line, and positional evidence has no "
                    "same-window overlap/cross-support."
                ),
            }
            row.update({"resolution": "resolved_clear", "rule": "lyric_free_editor_gap", "detail": gap_detail})
        else:
            row["detail"] = {
                "vocal_overlap": vocal_detail,
                "local_audio": local_detail,
                "editor_gap": gap_detail,
            }
        evidence_rows.append(row)

    resolved_clear = sum(row["resolution"] == "resolved_clear" for row in evidence_rows)
    confirmed_overlap = sum(row["resolution"] == "confirmed_overlap" for row in evidence_rows)
    unresolved = len(evidence_rows) - resolved_clear - confirmed_overlap
    report = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "policy": asdict(policy),
        "task_fingerprint_sha256": _text(run.get("task_fingerprint_sha256")),
        "base_run_artifact_id": base_run_artifact_id,
        "run_sha256": run_sha256,
        "source_srt_sha256": source_srt_sha256,
        "transition_count": len(evidence_rows),
        "resolved_clear_count": resolved_clear,
        "confirmed_overlap_count": confirmed_overlap,
        "remaining_unresolved_count": unresolved,
        "decisions": evidence_rows,
        "authority": "official_review_decisions_only_no_direct_timeline_mutation",
        "timing_mutation_performed": False,
        "text_mutation_performed": False,
    }
    return decisions, report


__all__ = (
    "POLICY_ID",
    "SCHEMA_VERSION",
    "Policy",
    "TransitionFinalResolutionError",
    "authorize_final_transitions",
)
