#!/usr/bin/env python3
"""Evaluate private lyric-alignment cases without emitting lyric text."""

from __future__ import annotations

import argparse
import difflib
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.evaluation.structural_scenarios import structural_scenarios
from language_profiles import boundary_units_for_language, language_code
from redo_karaoke_pipeline import Cue, normalized_text, parse_srt


SUPPORTED_DATASET_SCHEMA_VERSIONS = {"1.0", "1.1"}


def safe_divide(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def resolve_local(base: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def evaluation_language(value: str) -> str:
    """Map benchmark-only labels onto production-safe tokenization."""

    if value.strip().lower() == "synthetic":
        return "generic"
    return language_code(value)


def sequence_units(language: str, cues: list[Cue]) -> list[str]:
    units: list[str] = []
    for cue in cues:
        units.extend(boundary_units_for_language(language, cue.text))
    return units


def levenshtein_distance(left: list[str], right: list[str]) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for left_index, left_value in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_value in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_value != right_value),
                )
            )
        previous = current
    return previous[-1]


def lcs_length(left: list[str], right: list[str]) -> int:
    previous = [0] * (len(right) + 1)
    for left_value in left:
        current = [0]
        for index, right_value in enumerate(right, start=1):
            if left_value == right_value:
                current.append(previous[index - 1] + 1)
            else:
                current.append(max(previous[index], current[-1]))
        previous = current
    return previous[-1]


def normalized_lines(cues: list[Cue]) -> list[str]:
    return [normalized_text(cue.text) for cue in cues]


def _group_text(cues: list[Cue]) -> str:
    return " ".join(normalized_text(cue.text) for cue in cues)


def _group_bounds(cues: list[Cue]) -> tuple[int, int]:
    return min(cue.start_ms for cue in cues), max(cue.end_ms for cue in cues)


def _group_match_cost(reference: list[Cue], predicted: list[Cue]) -> float:
    text_score = difflib.SequenceMatcher(
        None,
        _group_text(reference),
        _group_text(predicted),
        autojunk=False,
    ).ratio()
    ref_start, ref_end = _group_bounds(reference)
    pred_start, pred_end = _group_bounds(predicted)
    overlap = max(0, min(ref_end, pred_end) - max(ref_start, pred_start))
    union = max(ref_end, pred_end) - min(ref_start, pred_start)
    timing_score = safe_divide(overlap, union)
    complexity = 0.08 * (len(reference) + len(predicted) - 2)
    return 1.55 * (1.0 - text_score) + 0.35 * (1.0 - timing_score) + complexity


def align_cue_groups(
    reference: list[Cue],
    predicted: list[Cue],
    *,
    max_group: int = 3,
) -> list[tuple[list[Cue], list[Cue]]]:
    """Monotonic DP cue alignment supporting split/merge groups."""

    n, m = len(reference), len(predicted)
    inf = float("inf")
    cost = [[inf] * (m + 1) for _ in range(n + 1)]
    parent: list[list[tuple[int, int, int, int] | None]] = [
        [None] * (m + 1) for _ in range(n + 1)
    ]
    cost[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            base = cost[i][j]
            if not math.isfinite(base):
                continue
            if i < n and base + 1.0 < cost[i + 1][j]:
                cost[i + 1][j] = base + 1.0
                parent[i + 1][j] = (i, j, 1, 0)
            if j < m and base + 1.0 < cost[i][j + 1]:
                cost[i][j + 1] = base + 1.0
                parent[i][j + 1] = (i, j, 0, 1)
            for ref_count in range(1, min(max_group, n - i) + 1):
                for pred_count in range(1, min(max_group, m - j) + 1):
                    candidate = base + _group_match_cost(
                        reference[i : i + ref_count],
                        predicted[j : j + pred_count],
                    )
                    target_i, target_j = i + ref_count, j + pred_count
                    if candidate < cost[target_i][target_j]:
                        cost[target_i][target_j] = candidate
                        parent[target_i][target_j] = (
                            i,
                            j,
                            ref_count,
                            pred_count,
                        )

    pairs: list[tuple[list[Cue], list[Cue]]] = []
    i, j = n, m
    while i or j:
        step = parent[i][j]
        if step is None:
            break
        previous_i, previous_j, ref_count, pred_count = step
        if ref_count and pred_count:
            pairs.append((reference[previous_i:i], predicted[previous_j:j]))
        i, j = previous_i, previous_j
    pairs.reverse()
    return pairs


def _cut_time_ms(value: Any) -> float:
    if isinstance(value, dict):
        for key in ("time_ms", "mix_time_ms"):
            if key in value:
                return float(value[key])
        if "mix_time" in value:
            return float(value["mix_time"]) * 1000.0
    if isinstance(value, (int, float)):
        return float(value)
    raise ValueError(f"cut event requires time_ms/mix_time: {value!r}")


def _match_times(expected: list[float], predicted: list[float], tolerance_ms: float) -> int:
    unused = set(range(len(predicted)))
    matches = 0
    for truth in sorted(expected):
        candidates = [
            (abs(predicted[index] - truth), index)
            for index in unused
            if abs(predicted[index] - truth) <= tolerance_ms
        ]
        if not candidates:
            continue
        _, index = min(candidates)
        unused.remove(index)
        matches += 1
    return matches


def _interval(value: dict[str, Any]) -> tuple[float, float, frozenset[str]]:
    if "start_ms" in value:
        start = float(value["start_ms"])
        end = float(value["end_ms"])
    else:
        start = float(value["start"]) * 1000.0
        end = float(value["end"]) * 1000.0
    if end <= start:
        raise ValueError(f"invalid overlap interval: {value!r}")
    tracks = frozenset(str(track) for track in value.get("tracks", []))
    return start, end, tracks


def _merged_duration(intervals: list[tuple[float, float]]) -> float:
    if not intervals:
        return 0.0
    ordered = sorted(intervals)
    total = 0.0
    start, end = ordered[0]
    for next_start, next_end in ordered[1:]:
        if next_start <= end:
            end = max(end, next_end)
        else:
            total += end - start
            start, end = next_start, next_end
    return total + end - start


def _intersection_duration(
    left: list[tuple[float, float]], right: list[tuple[float, float]]
) -> float:
    left = sorted(left)
    right = sorted(right)
    i = j = 0
    total = 0.0
    while i < len(left) and j < len(right):
        start = max(left[i][0], right[j][0])
        end = min(left[i][1], right[j][1])
        if end > start:
            total += end - start
        if left[i][1] <= right[j][1]:
            i += 1
        else:
            j += 1
    return total


def _interval_iou(left: tuple[float, float], right: tuple[float, float]) -> float:
    intersection = max(0.0, min(left[1], right[1]) - max(left[0], right[0]))
    union = max(left[1], right[1]) - min(left[0], right[0])
    return safe_divide(intersection, union)


def overlap_metrics(case: dict[str, Any]) -> dict[str, Any]:
    expected = [_interval(value) for value in case.get("expected_overlaps", [])]
    predicted = [_interval(value) for value in case.get("predicted_overlaps", [])]
    expected_intervals = [(start, end) for start, end, _ in expected]
    predicted_intervals = [(start, end) for start, end, _ in predicted]
    expected_duration = _merged_duration(expected_intervals)
    predicted_duration = _merged_duration(predicted_intervals)
    intersection = _intersection_duration(expected_intervals, predicted_intervals)
    union = expected_duration + predicted_duration - intersection

    threshold = float(case.get("overlap_match_iou", 0.20))
    unused = set(range(len(predicted)))
    event_matches = 0
    track_matches = 0
    for truth_start, truth_end, truth_tracks in expected:
        candidates = []
        for index in unused:
            pred_start, pred_end, pred_tracks = predicted[index]
            iou = _interval_iou((truth_start, truth_end), (pred_start, pred_end))
            if iou >= threshold:
                candidates.append((iou, index, pred_tracks))
        if not candidates:
            continue
        _, index, pred_tracks = max(candidates)
        unused.remove(index)
        event_matches += 1
        if truth_tracks and truth_tracks == pred_tracks:
            track_matches += 1
    return {
        "overlap_intersection_ms": intersection,
        "overlap_expected_ms": expected_duration,
        "overlap_predicted_ms": predicted_duration,
        "overlap_union_ms": union,
        "overlap_event_true_positive": event_matches,
        "overlap_event_expected": len(expected),
        "overlap_event_predicted": len(predicted),
        "track_attribution_correct": track_matches,
        "track_attribution_compared": event_matches,
    }


def case_metrics(case: dict[str, Any], base: Path) -> dict[str, Any]:
    case_id = str(case.get("id", "")).strip()
    if not case_id:
        raise ValueError("every dataset case requires a non-empty id")
    split = str(case.get("split", "")).strip()
    if split not in {"train", "calibration", "blind_test"}:
        raise ValueError(f"case {case_id} has invalid split {split!r}")
    language = evaluation_language(str(case.get("language", "")))
    scenarios = list(structural_scenarios(case))
    reference = parse_srt(resolve_local(base, str(case["reference_srt"])))
    predicted = parse_srt(resolve_local(base, str(case["predicted_srt"])))

    reference_units = sequence_units(language, reference)
    predicted_units = sequence_units(language, predicted)
    unit_distance = levenshtein_distance(reference_units, predicted_units)
    sequence_true_positive = max(
        0, max(len(reference_units), len(predicted_units)) - unit_distance
    )
    unit_precision = safe_divide(sequence_true_positive, len(predicted_units))
    unit_recall = safe_divide(sequence_true_positive, len(reference_units))
    unit_f1 = safe_divide(
        2 * unit_precision * unit_recall, unit_precision + unit_recall
    )

    reference_lines = normalized_lines(reference)
    predicted_lines = normalized_lines(predicted)
    line_exact_matches = lcs_length(reference_lines, predicted_lines)
    unordered_line_matches = sum(
        (Counter(reference_lines) & Counter(predicted_lines)).values()
    )
    missing_lines = max(0, len(reference_lines) - line_exact_matches)
    extra_lines = max(0, len(predicted_lines) - line_exact_matches)
    wrong_order_lines = max(0, unordered_line_matches - line_exact_matches)

    group_pairs = align_cue_groups(reference, predicted)
    onset_errors: list[float] = []
    offset_errors: list[float] = []
    split_errors = 0
    merge_errors = 0
    for truth_group, predicted_group in group_pairs:
        truth_start, truth_end = _group_bounds(truth_group)
        pred_start, pred_end = _group_bounds(predicted_group)
        onset_errors.append(float(abs(truth_start - pred_start)))
        offset_errors.append(float(abs(truth_end - pred_end)))
        if len(truth_group) == 1 and len(predicted_group) > 1:
            split_errors += 1
        if len(truth_group) > 1 and len(predicted_group) == 1:
            merge_errors += 1

    expected_cuts_new = case.get("expected_cuts")
    predicted_cuts_new = case.get("predicted_cuts")
    if expected_cuts_new is not None or predicted_cuts_new is not None:
        expected_times = [_cut_time_ms(value) for value in (expected_cuts_new or [])]
        predicted_times = [_cut_time_ms(value) for value in (predicted_cuts_new or [])]
        cut_true_positive = _match_times(
            expected_times,
            predicted_times,
            float(case.get("cut_tolerance_ms", 500.0)),
        )
        cut_expected = len(expected_times)
        cut_predicted = len(predicted_times)
        cut_metric_mode = "time_tolerance"
    else:
        expected_ids = {str(value) for value in case.get("expected_cut_ids", [])}
        predicted_ids = {str(value) for value in case.get("predicted_cut_ids", [])}
        cut_true_positive = len(expected_ids & predicted_ids)
        cut_expected = len(expected_ids)
        cut_predicted = len(predicted_ids)
        cut_metric_mode = "legacy_id"

    duration_seconds = float(case.get("audio_duration_seconds") or 0.0)
    runtime_seconds = float(case.get("runtime_seconds") or 0.0)
    review_count = 0
    publish_ready: bool | None = None
    qa_path_value = case.get("qa_json")
    if qa_path_value:
        qa_payload = json.loads(
            resolve_local(base, str(qa_path_value)).read_text(encoding="utf-8-sig")
        )
        review_count = int(qa_payload.get("review_candidate_count", 0))
        publish_ready = bool(qa_payload.get("publish_ready", False))

    overlap = overlap_metrics(case)
    boundary_errors = onset_errors + offset_errors
    return {
        "id": case_id,
        "split": split,
        "language": language,
        "structural_scenarios": scenarios,
        "reference_cues": len(reference),
        "predicted_cues": len(predicted),
        "paired_cues": len(group_pairs),
        "paired_groups": len(group_pairs),
        "line_exact_matches": line_exact_matches,
        "line_reference": len(reference_lines),
        "line_predicted": len(predicted_lines),
        "missing_lines": missing_lines,
        "extra_lines": extra_lines,
        "wrong_order_lines": wrong_order_lines,
        "split_errors": split_errors,
        "merge_errors": merge_errors,
        "unit_edit_distance": unit_distance,
        "unit_predicted": len(predicted_units),
        "unit_reference": len(reference_units),
        "unit_true_positive": sequence_true_positive,
        "unit_precision": unit_precision,
        "unit_recall": unit_recall,
        "unit_f1": unit_f1,
        "onset_errors_ms": onset_errors,
        "offset_errors_ms": offset_errors,
        "boundary_errors_ms": boundary_errors,
        "boundary_error_sum_ms": sum(boundary_errors),
        "boundary_error_count": len(boundary_errors),
        "audio_duration_seconds": duration_seconds,
        "runtime_seconds": runtime_seconds,
        "review_candidate_count": review_count,
        "publish_ready": publish_ready,
        "cut_true_positive": cut_true_positive,
        "cut_predicted": cut_predicted,
        "cut_expected": cut_expected,
        "cut_metric_mode": cut_metric_mode,
        **overlap,
    }


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    unit_edit_distance = sum(row["unit_edit_distance"] for row in rows)
    unit_predicted = sum(row["unit_predicted"] for row in rows)
    unit_reference = sum(row["unit_reference"] for row in rows)
    unit_true_positive = sum(row["unit_true_positive"] for row in rows)
    precision = safe_divide(unit_true_positive, unit_predicted)
    recall = safe_divide(unit_true_positive, unit_reference)
    f1 = safe_divide(2 * precision * recall, precision + recall)

    line_exact = sum(row["line_exact_matches"] for row in rows)
    line_reference = sum(row["line_reference"] for row in rows)
    line_predicted = sum(row["line_predicted"] for row in rows)
    line_precision = safe_divide(line_exact, line_predicted)
    line_recall = safe_divide(line_exact, line_reference)
    line_f1 = safe_divide(
        2 * line_precision * line_recall, line_precision + line_recall
    )

    onset = [value for row in rows for value in row["onset_errors_ms"]]
    offset = [value for row in rows for value in row["offset_errors_ms"]]
    errors = onset + offset
    total_duration = sum(row["audio_duration_seconds"] for row in rows)
    total_runtime = sum(row["runtime_seconds"] for row in rows)
    review_count = sum(row["review_candidate_count"] for row in rows)
    cut_true_positive = sum(row["cut_true_positive"] for row in rows)
    cut_predicted = sum(row["cut_predicted"] for row in rows)
    cut_expected = sum(row["cut_expected"] for row in rows)

    overlap_intersection = sum(row["overlap_intersection_ms"] for row in rows)
    overlap_expected = sum(row["overlap_expected_ms"] for row in rows)
    overlap_predicted = sum(row["overlap_predicted_ms"] for row in rows)
    overlap_union = sum(row["overlap_union_ms"] for row in rows)
    overlap_tp = sum(row["overlap_event_true_positive"] for row in rows)
    overlap_event_expected = sum(row["overlap_event_expected"] for row in rows)
    overlap_event_predicted = sum(row["overlap_event_predicted"] for row in rows)
    track_correct = sum(row["track_attribution_correct"] for row in rows)
    track_compared = sum(row["track_attribution_compared"] for row in rows)

    def error_summary(prefix: str, values: list[float]) -> dict[str, float]:
        return {
            f"{prefix}_mae_ms": round(statistics.fmean(values), 3) if values else 0.0,
            f"{prefix}_p50_ms": round(percentile(values, 0.50), 3),
            f"{prefix}_p90_ms": round(percentile(values, 0.90), 3),
            f"{prefix}_p95_ms": round(percentile(values, 0.95), 3),
            f"{prefix}_within_250ms_rate": round(
                safe_divide(sum(value <= 250 for value in values), len(values)), 6
            ),
            f"{prefix}_within_500ms_rate": round(
                safe_divide(sum(value <= 500 for value in values), len(values)), 6
            ),
        }

    return {
        "case_count": len(rows),
        "unit_precision": round(precision, 6),
        "unit_recall": round(recall, 6),
        "unit_f1": round(f1, 6),
        "sequence_wer": round(safe_divide(unit_edit_distance, unit_reference), 6),
        "line_exact_precision": round(line_precision, 6),
        "line_exact_recall": round(line_recall, 6),
        "line_exact_f1": round(line_f1, 6),
        "cue_text_exact_match_rate": round(line_recall, 6),
        "missing_line_rate": round(
            safe_divide(sum(row["missing_lines"] for row in rows), line_reference), 6
        ),
        "extra_line_rate": round(
            safe_divide(sum(row["extra_lines"] for row in rows), line_predicted), 6
        ),
        "wrong_order_line_count": sum(row["wrong_order_lines"] for row in rows),
        "split_error_count": sum(row["split_errors"] for row in rows),
        "merge_error_count": sum(row["merge_errors"] for row in rows),
        "boundary_mae_ms": round(statistics.fmean(errors), 3) if errors else 0.0,
        "boundary_p95_ms": round(percentile(errors, 0.95), 3),
        **error_summary("onset", onset),
        **error_summary("offset", offset),
        "review_candidates_per_10_audio_minutes": round(
            safe_divide(review_count * 600.0, total_duration), 6
        ),
        "runtime_per_audio_minute": round(
            safe_divide(total_runtime, total_duration / 60.0), 6
        ),
        "publish_ready_rate": round(
            safe_divide(
                sum(row["publish_ready"] is True for row in rows),
                sum(row["publish_ready"] is not None for row in rows),
            ),
            6,
        ),
        "cut_precision": round(safe_divide(cut_true_positive, cut_predicted), 6),
        "cut_recall": round(safe_divide(cut_true_positive, cut_expected), 6),
        "overlap_duration_precision": round(
            safe_divide(overlap_intersection, overlap_predicted), 6
        ),
        "overlap_duration_recall": round(
            safe_divide(overlap_intersection, overlap_expected), 6
        ),
        "overlap_iou": round(safe_divide(overlap_intersection, overlap_union), 6),
        "overlap_event_precision": round(
            safe_divide(overlap_tp, overlap_event_predicted), 6
        ),
        "overlap_event_recall": round(
            safe_divide(overlap_tp, overlap_event_expected), 6
        ),
        "track_attribution_accuracy": round(
            safe_divide(track_correct, track_compared), 6
        ),
    }


def evaluate_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("dataset manifest must be a JSON object")
    schema_version = str(payload.get("schema_version") or "")
    if schema_version not in SUPPORTED_DATASET_SCHEMA_VERSIONS:
        raise ValueError(
            "dataset schema_version must be one of "
            + ", ".join(sorted(SUPPORTED_DATASET_SCHEMA_VERSIONS))
        )
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("dataset manifest must contain a non-empty cases list")
    if schema_version == "1.0" and any(
        isinstance(case, dict) and "structural_scenarios" in case for case in cases
    ):
        raise ValueError("structural_scenarios requires dataset schema_version 1.1")
    rows = [case_metrics(case, path.parent) for case in cases]
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[f"split:{row['split']}"] .append(row)
        groups[f"language:{row['language']}"] .append(row)
        if schema_version == "1.1":
            for scenario in row["structural_scenarios"]:
                groups[f"structural:{scenario}"].append(row)
    return {
        "schema_version": schema_version,
        "dataset": str(payload.get("dataset", "private-dataset")),
        "overall": aggregate(rows),
        "groups": {key: aggregate(value) for key, value in sorted(groups.items())},
        "cases": [
            {
                "id": row["id"],
                "split": row["split"],
                "language": row["language"],
                **(
                    {"structural_scenarios": row["structural_scenarios"]}
                    if schema_version == "1.1"
                    else {}
                ),
                "unit_f1": round(row["unit_f1"], 6),
                "sequence_wer": round(
                    safe_divide(row["unit_edit_distance"], row["unit_reference"]), 6
                ),
                "line_exact_recall": round(
                    safe_divide(row["line_exact_matches"], row["line_reference"]), 6
                ),
                "cue_text_exact_match_rate": round(
                    safe_divide(row["line_exact_matches"], row["line_reference"]), 6
                ),
                "onset_mae_ms": round(
                    statistics.fmean(row["onset_errors_ms"])
                    if row["onset_errors_ms"]
                    else 0.0,
                    3,
                ),
                "offset_mae_ms": round(
                    statistics.fmean(row["offset_errors_ms"])
                    if row["offset_errors_ms"]
                    else 0.0,
                    3,
                ),
                "boundary_mae_ms": round(
                    safe_divide(
                        row["boundary_error_sum_ms"], row["boundary_error_count"]
                    ),
                    3,
                ),
                "split_error_count": row["split_errors"],
                "merge_error_count": row["merge_errors"],
                "wrong_order_line_count": row["wrong_order_lines"],
                "review_candidate_count": row["review_candidate_count"],
                "publish_ready": row["publish_ready"],
                "cut_metric_mode": row["cut_metric_mode"],
            }
            for row in rows
        ],
        "privacy": "aggregate metrics only; lyric text is intentionally omitted",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = evaluate_manifest(args.dataset.resolve())
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result["overall"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
