#!/usr/bin/env python3
"""Evaluate a pre-gold locked timing-decision pack after complete human truth is supplied."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.evaluation.decision_validation import (
    SCHEMA_VERSION as DECISION_VALIDATION_SCHEMA_VERSION,
    evaluate_timing_decisions,
)
from lyric_aligner.evaluation.timing_decision_pack import merge_timing_decision_gold


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", required=True, type=Path)
    parser.add_argument("--gold", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--manual-tolerance-ms", type=int, default=100)
    parser.add_argument("--decision-margin-ms", type=int, default=100)
    args = parser.parse_args()
    try:
        inputs = {args.pack.resolve(), args.gold.resolve()}
        if args.out.resolve() in inputs:
            raise ValueError("evaluation output must not overwrite pack/gold")
        pack = json.loads(args.pack.read_text(encoding="utf-8-sig"))
        gold = json.loads(args.gold.read_text(encoding="utf-8-sig"))
        if not isinstance(pack, dict) or not isinstance(gold, dict):
            raise ValueError("pack and gold must each contain one JSON object")
        records = merge_timing_decision_gold(pack, gold)
        if records:
            result = evaluate_timing_decisions(
                records,
                manual_tolerance_ms=args.manual_tolerance_ms,
                decision_margin_ms=args.decision_margin_ms,
            )
        else:
            result = {
                "schema_version": DECISION_VALIDATION_SCHEMA_VERSION,
                "purpose": "offline_selector_validation_never_production_authority",
                "manual_tolerance_ms": args.manual_tolerance_ms,
                "decision_margin_ms": args.decision_margin_ms,
                "record_count": 0,
                "track_count": 0,
                "scopes": {},
                "records": [],
                "no_scorable_gold": True,
            }
        invalid_records = gold.get("invalid_records", [])
        if not isinstance(invalid_records, list):
            raise ValueError("gold invalid_records must be a list")
        result.update(
            {
                "selection_lock_sha256": pack["selection_lock_sha256"],
                "pack_sha256": _sha256(args.pack),
                "gold_sha256": _sha256(args.gold),
                "gold_partition": gold["partition"],
                "selected_population_count": int(pack["selected_case_count"]),
                "valid_gold_count": len(records),
                "invalid_gold_count": len(invalid_records),
                "invalid_gold_records": invalid_records,
            }
        )
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            result["scopes"].get(
                "overall",
                {
                    "record_count": result["record_count"],
                    "no_scorable_gold": result.get("no_scorable_gold", False),
                    "invalid_gold_count": result["invalid_gold_count"],
                },
            ),
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
