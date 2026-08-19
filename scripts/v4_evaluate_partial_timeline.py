#!/usr/bin/env python3
"""Evaluate Partial Timeline Repair previews against private human timing truth."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.evaluation.partial_timeline import (
    PartialTimelineEvaluationError,
    evaluate_partial_timeline_dataset,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument(
        "--error-threshold-ms",
        type=float,
        default=250.0,
        help="Evaluation tolerance only; does not change preview or release authority.",
    )
    args = parser.parse_args()

    try:
        if not args.dataset.is_file():
            raise PartialTimelineEvaluationError(
                f"dataset does not exist: {args.dataset}"
            )
        if args.dataset.resolve() == args.out.resolve():
            raise PartialTimelineEvaluationError(
                "evaluation output must not overwrite dataset manifest"
            )
        report = evaluate_partial_timeline_dataset(
            args.dataset,
            error_threshold_ms=args.error_threshold_ms,
        )
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except (OSError, PartialTimelineEvaluationError, ValueError) as exc:
        parser.error(str(exc))

    print(
        json.dumps(
            {
                "mode": report["mode"],
                "split": report["split"],
                "releaseable": report["releaseable"],
                "automatic_timing_change_allowed": report[
                    "automatic_timing_change_allowed"
                ],
                "overall": report["overall"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
