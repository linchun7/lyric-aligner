#!/usr/bin/env python3
"""Build a Partial Timeline Repair trust lock from strict calibration/blind outputs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.contracts.artifacts import atomic_write_json
from lyric_aligner.evaluation.strict_workflow import StrictEvaluationError
from lyric_aligner.timeline.partial_repair import PartialTimelineRepairError
from lyric_aligner.timeline.partial_repair_trust import (
    build_calibrated_trust_policy_lock,
)


_INPUT_ARGUMENTS = (
    "selection",
    "calibration_baseline",
    "calibration_candidate",
    "calibration_policy",
    "blind_gate",
    "blind_baseline",
    "blind_candidate",
    "blind_policy",
)


def _ensure_output_is_distinct(args: argparse.Namespace) -> None:
    output = args.out.resolve()
    for name in _INPUT_ARGUMENTS:
        source = getattr(args, name).resolve()
        if output == source:
            raise PartialTimelineRepairError(
                "trust lock output must not overwrite an input file: "
                f"{getattr(args, name).name}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--calibration-baseline", required=True, type=Path)
    parser.add_argument("--calibration-candidate", required=True, type=Path)
    parser.add_argument("--calibration-policy", required=True, type=Path)
    parser.add_argument("--blind-gate", required=True, type=Path)
    parser.add_argument("--blind-baseline", required=True, type=Path)
    parser.add_argument("--blind-candidate", required=True, type=Path)
    parser.add_argument("--blind-policy", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    try:
        _ensure_output_is_distinct(args)
        payload = build_calibrated_trust_policy_lock(
            selection_path=args.selection,
            calibration_baseline_path=args.calibration_baseline,
            calibration_candidate_path=args.calibration_candidate,
            calibration_policy_path=args.calibration_policy,
            blind_gate_path=args.blind_gate,
            blind_baseline_path=args.blind_baseline,
            blind_candidate_path=args.blind_candidate,
            blind_policy_path=args.blind_policy,
        )
        atomic_write_json(args.out, payload)
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        StrictEvaluationError,
        PartialTimelineRepairError,
    ) as exc:
        parser.error(str(exc))

    print(
        json.dumps(
            {
                "candidate_id": payload["candidate_id"],
                "candidate_revision": payload["candidate_revision"],
                "eligible_language_scopes": payload["eligible_language_scopes"],
                "cue_trust_generation_allowed": payload[
                    "cue_trust_generation_allowed"
                ],
                "trust_policy_lock_sha256": payload[
                    "trust_policy_lock_sha256"
                ],
                "out_file": args.out.name,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
