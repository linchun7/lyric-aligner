#!/usr/bin/env python3
"""Evaluate private lyric-alignment cases without emitting lyric text."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from language_profiles import boundary_units_for_language, language_code
from redo_karaoke_pipeline import Cue, normalized_text, parse_srt


DATASET_SCHEMA_VERSION = "1.0"


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


def pair_cues(reference: list[Cue], predicted: list[Cue]) -> list[tuple[Cue, Cue]]:
    """Pair cues monotonically using timing overlap, then nearest midpoint."""

    pairs: list[tuple[Cue, Cue]] = []
    start_index = 0
    for truth in reference:
        candidates: list[tuple[float, int, Cue]] = []
        truth_midpoint = (truth.start_ms + truth.end_ms) / 2.0
        for index in range(start_index, len(predicted)):
            candidate = predicted[index]
            if candidate.start_ms > truth.end_ms + 2500:
                break
            overlap = max(
                0,
                min(truth.end_ms, candidate.end_ms)
                - max(truth.start_ms, candidate.start_ms),
            )
            candidate_midpoint = (candidate.start_ms + candidate.end_ms) / 2.0
            distance = abs(candidate_midpoint - truth_midpoint)
            if overlap > 0 or distance <= 2000:
                candidates.append((-float(overlap), distance, candidate))
        if not candidates:
            continue
        _, _, selected = min(candidates)
        pairs.append((truth, selected))
        start_index = predicted.index(selected, start_index) + 1
    return pairs


def token_counts(language: str, cues: list[Cue]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for cue in cues:
        counts.update(boundary_units_for_language(language, cue.text))
    return counts


def case_metrics(case: dict[str, Any], base: Path) -> dict[str, Any]:
    case_id = str(case.get("id", "")).strip()
    if not case_id:
        raise ValueError("every dataset case requires a non-empty id")
    split = str(case.get("split", "")).strip()
    if split not in {"train", "calibration", "blind_test"}:
        raise ValueError(f"case {case_id} has invalid split {split!r}")
    language = language_code(str(case.get("language", "")))
    reference_path = resolve_local(base, str(case["reference_srt"]))
    predicted_path = resolve_local(base, str(case["predicted_srt"]))
    reference = parse_srt(reference_path)
    predicted = parse_srt(predicted_path)
    pairs = pair_cues(reference, predicted)

    exact = sum(
        normalized_text(left.text) == normalized_text(right.text)
        for left, right in pairs
    )
    boundary_errors = [
        float(abs(left.start_ms - right.start_ms))
        for left, right in pairs
    ] + [
        float(abs(left.end_ms - right.end_ms))
        for left, right in pairs
    ]
    reference_units = token_counts(language, reference)
    predicted_units = token_counts(language, predicted)
    true_positive = sum((reference_units & predicted_units).values())
    predicted_count = sum(predicted_units.values())
    reference_count = sum(reference_units.values())
    precision = safe_divide(true_positive, predicted_count)
    recall = safe_divide(true_positive, reference_count)
    f1 = safe_divide(2 * precision * recall, precision + recall)

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

    expected_cuts = {str(value) for value in case.get("expected_cut_ids", [])}
    predicted_cuts = {str(value) for value in case.get("predicted_cut_ids", [])}
    cut_true_positive = len(expected_cuts & predicted_cuts)
    return {
        "id": case_id,
        "split": split,
        "language": language,
        "reference_cues": len(reference),
        "predicted_cues": len(predicted),
        "paired_cues": len(pairs),
        "exact_text_matches": exact,
        "unit_true_positive": true_positive,
        "unit_predicted": predicted_count,
        "unit_reference": reference_count,
        "unit_precision": precision,
        "unit_recall": recall,
        "unit_f1": f1,
        "boundary_error_sum_ms": sum(boundary_errors),
        "boundary_error_count": len(boundary_errors),
        "boundary_errors_ms": boundary_errors,
        "audio_duration_seconds": duration_seconds,
        "runtime_seconds": runtime_seconds,
        "review_candidate_count": review_count,
        "publish_ready": publish_ready,
        "cut_true_positive": cut_true_positive,
        "cut_predicted": len(predicted_cuts),
        "cut_expected": len(expected_cuts),
    }


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    unit_true_positive = sum(row["unit_true_positive"] for row in rows)
    unit_predicted = sum(row["unit_predicted"] for row in rows)
    unit_reference = sum(row["unit_reference"] for row in rows)
    precision = safe_divide(unit_true_positive, unit_predicted)
    recall = safe_divide(unit_true_positive, unit_reference)
    f1 = safe_divide(2 * precision * recall, precision + recall)
    errors = [value for row in rows for value in row["boundary_errors_ms"]]
    total_duration = sum(row["audio_duration_seconds"] for row in rows)
    total_runtime = sum(row["runtime_seconds"] for row in rows)
    review_count = sum(row["review_candidate_count"] for row in rows)
    cut_true_positive = sum(row["cut_true_positive"] for row in rows)
    cut_predicted = sum(row["cut_predicted"] for row in rows)
    cut_expected = sum(row["cut_expected"] for row in rows)
    paired = sum(row["paired_cues"] for row in rows)
    exact = sum(row["exact_text_matches"] for row in rows)
    return {
        "case_count": len(rows),
        "unit_precision": round(precision, 6),
        "unit_recall": round(recall, 6),
        "unit_f1": round(f1, 6),
        "cue_text_exact_match_rate": round(safe_divide(exact, paired), 6),
        "boundary_mae_ms": round(statistics.fmean(errors), 3) if errors else 0.0,
        "boundary_p95_ms": round(percentile(errors, 0.95), 3),
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
    }


def evaluate_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict) or payload.get("schema_version") != DATASET_SCHEMA_VERSION:
        raise ValueError(f"dataset schema_version must be {DATASET_SCHEMA_VERSION}")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("dataset manifest must contain a non-empty cases list")
    rows = [case_metrics(case, path.parent) for case in cases]
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[f"split:{row['split']}"] .append(row)
        groups[f"language:{row['language']}"] .append(row)
    return {
        "schema_version": DATASET_SCHEMA_VERSION,
        "dataset": str(payload.get("dataset", "private-dataset")),
        "overall": aggregate(rows),
        "groups": {key: aggregate(value) for key, value in sorted(groups.items())},
        "cases": [
            {
                "id": row["id"],
                "split": row["split"],
                "language": row["language"],
                "unit_f1": round(row["unit_f1"], 6),
                "cue_text_exact_match_rate": round(
                    safe_divide(row["exact_text_matches"], row["paired_cues"]), 6
                ),
                "boundary_mae_ms": round(
                    safe_divide(
                        row["boundary_error_sum_ms"], row["boundary_error_count"]
                    ),
                    3,
                ),
                "review_candidate_count": row["review_candidate_count"],
                "publish_ready": row["publish_ready"],
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
