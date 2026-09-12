#!/usr/bin/env python3
"""Freeze and evaluate boundary-level timing-promotion decisions in shadow only."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lyric_aligner.contracts.artifacts import atomic_write_json
from lyric_aligner.evaluation.boundary_promotion_shadow import (
    BoundaryPromotionShadowError,
    evaluate_boundary_promotion_shadow,
    freeze_boundary_promotion_selection,
)
from lyric_aligner.io.path_safety import validate_separate_artifact_paths


def _load(path: Path, *, label: str) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _write(path: Path, payload: dict, *, inputs: dict[str, Path]) -> None:
    validate_separate_artifact_paths(inputs=inputs, outputs={"output": path})
    if path.exists():
        raise ValueError("P1 shadow output already exists; use a new path")
    atomic_write_json(path, payload)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    freeze = sub.add_parser("freeze-selection", help="freeze pre-gold machine keep/promote decisions")
    freeze.add_argument("--pack", required=True, type=Path)
    freeze.add_argument("--decisions", required=True, type=Path)
    freeze.add_argument("--out", required=True, type=Path)

    evaluate = sub.add_parser("evaluate", help="evaluate a frozen selection on later blind/holdout truth")
    evaluate.add_argument("--pack", required=True, type=Path)
    evaluate.add_argument("--selection", required=True, type=Path)
    evaluate.add_argument("--review-manifest", required=True, type=Path)
    evaluate.add_argument("--review-response", required=True, type=Path)
    evaluate.add_argument("--gold", required=True, type=Path)
    evaluate.add_argument("--out", required=True, type=Path)

    args = parser.parse_args()
    try:
        if args.command == "freeze-selection":
            pack = _load(args.pack, label="timing decision pack")
            decisions = _load(args.decisions, label="machine decisions")
            result = freeze_boundary_promotion_selection(pack, decisions)
            _write(
                args.out,
                result,
                inputs={"pack": args.pack, "decisions": args.decisions},
            )
            summary = {
                "command": args.command,
                "case_count": result["case_count"],
                "promoted_case_count": result["promoted_case_count"],
                "selection_payload_sha256": result["selection_payload_sha256"],
                "production_authority_granted": result["production_authority_granted"],
            }
        else:
            pack = _load(args.pack, label="timing decision pack")
            selection = _load(args.selection, label="boundary promotion selection")
            review_manifest = _load(args.review_manifest, label="timing decision review manifest")
            review_response = _load(args.review_response, label="timing decision review response")
            gold = _load(args.gold, label="timing decision gold")
            result = evaluate_boundary_promotion_shadow(
                pack,
                selection,
                gold,
                review_manifest=review_manifest,
                review_response=review_response,
            )
            _write(
                args.out,
                result,
                inputs={
                    "pack": args.pack,
                    "selection": args.selection,
                    "review_manifest": args.review_manifest,
                    "review_response": args.review_response,
                    "gold": args.gold,
                },
            )
            summary = {
                "command": args.command,
                "partition": result["partition"],
                "valid_truth_count": result["valid_truth_count"],
                "promoted_scored_count": result["promoted_scored_count"],
                "review_manifest_sha256": result["review_manifest_sha256"],
                "review_response_payload_sha256": result["review_response_payload_sha256"],
                "shadow_gate_passed": result["shadow_gate_passed"],
                "production_authority_granted": result["production_authority_granted"],
                "evaluation_payload_sha256": result["evaluation_payload_sha256"],
            }
    except (OSError, ValueError, json.JSONDecodeError, BoundaryPromotionShadowError) as exc:
        parser.error(str(exc))
        return 2
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
