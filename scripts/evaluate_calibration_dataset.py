#!/usr/bin/env python3
"""Evaluate exactly one private dataset split with immutable ground-truth binding."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from evaluate_dataset import aggregate, case_metrics
from lyric_aligner.evaluation.protocol import (
    EvaluationProtocolError,
    augment_evaluation,
    load_dataset_manifest,
)


def evaluate_selected_split(
    dataset_path: Path,
    *,
    split: str,
    candidate_id: str,
    require_source_groups: bool,
) -> dict:
    dataset = load_dataset_manifest(dataset_path)
    cases = [
        case
        for case in dataset["cases"]
        if str(case.get("split") or "") == split
    ]
    if not cases:
        raise EvaluationProtocolError(
            f"dataset contains no cases for split {split!r}"
        )

    rows = [case_metrics(case, dataset_path.parent) for case in cases]
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[f"language:{row['language']}"].append(row)

    base_result = {
        "schema_version": "2.0",
        "dataset": str(dataset.get("dataset") or dataset_path.stem),
        "overall": aggregate(rows),
        "groups": {
            name: aggregate(group_rows)
            for name, group_rows in sorted(grouped.items())
        },
        "cases": [
            {
                "id": row["id"],
                "split": row["split"],
                "language": row["language"],
                "reference_cues": row["reference_cues"],
                "predicted_cues": row["predicted_cues"],
                "review_candidate_count": row["review_candidate_count"],
                "publish_ready": row["publish_ready"],
            }
            for row in rows
        ],
    }
    return augment_evaluation(
        base_result,
        dataset_path=dataset_path,
        dataset_payload=dataset,
        selected_split=split,
        candidate_id=candidate_id,
        require_source_groups=require_source_groups,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument(
        "--split",
        required=True,
        choices=("train", "calibration", "blind_test"),
    )
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument(
        "--allow-legacy-without-source-group",
        action="store_true",
        help=(
            "Compatibility only. Calibration/blind-test should normally use "
            "dataset schema 1.1 with opaque source_group isolation."
        ),
    )
    args = parser.parse_args()

    try:
        result = evaluate_selected_split(
            args.dataset,
            split=args.split,
            candidate_id=args.candidate_id,
            require_source_groups=not args.allow_legacy_without_source_group,
        )
    except (OSError, ValueError, json.JSONDecodeError, EvaluationProtocolError) as exc:
        parser.error(str(exc))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "candidate_id": result["candidate_id"],
                "split": result["evaluated_split"],
                "dataset_ground_truth_sha256": result["dataset_identity"][
                    "dataset_ground_truth_sha256"
                ],
                "cases": result["dataset_identity"]["case_count"],
                "out": str(args.out),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
