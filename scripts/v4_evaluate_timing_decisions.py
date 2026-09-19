#!/usr/bin/env python3
"""Evaluate old-final -> hybrid timing decisions on frozen human boundary truth."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.evaluation.decision_validation import evaluate_timing_decisions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--manual-tolerance-ms", type=int, default=100)
    parser.add_argument("--decision-margin-ms", type=int, default=100)
    args = parser.parse_args()
    try:
        if args.input.resolve() == args.out.resolve():
            raise ValueError("evaluation output must not overwrite its input")
        payload = json.loads(args.input.read_text(encoding="utf-8-sig"))
        records = payload.get("records") if isinstance(payload, dict) else None
        if not isinstance(records, list):
            raise ValueError("input must contain a records list")
        result = evaluate_timing_decisions(
            records,
            manual_tolerance_ms=args.manual_tolerance_ms,
            decision_margin_ms=args.decision_margin_ms,
        )
        result["dataset_id"] = payload.get("dataset_id")
        result["policy_id"] = payload.get("policy_id")
        result["input_sha256"] = __import__("hashlib").sha256(args.input.read_bytes()).hexdigest()
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result["scopes"]["overall"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
