#!/usr/bin/env python3
"""Validate a complete candidate-blind review response and materialize hash-bound human gold."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.evaluation.timing_decision_review import validate_review_response


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--response", required=True, type=Path)
    parser.add_argument("--partition", required=True, choices=["development", "calibration", "blind", "holdout", "regression"])
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    try:
        inputs = {args.manifest.resolve(), args.response.resolve()}
        if args.out.resolve() in inputs:
            raise ValueError("gold output must not overwrite manifest/response")
        manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
        response = json.loads(args.response.read_text(encoding="utf-8-sig"))
        if not isinstance(manifest, dict) or not isinstance(response, dict):
            raise ValueError("manifest and response must each contain one JSON object")
        gold = validate_review_response(manifest, response, partition=args.partition)
        gold.update(
            {
                "manifest_file_sha256": _sha256(args.manifest),
                "response_file_sha256": _sha256(args.response),
                "review_method": "candidate_blind_audio_boundary_review",
            }
        )
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(gold, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "partition": gold["partition"],
                "record_count": len(gold["records"]),
                "selection_lock_sha256": gold["selection_lock_sha256"],
                "review_manifest_sha256": gold["review_manifest_sha256"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
