#!/usr/bin/env python3
"""Apply task-reviewed canonical character splices after Smart; preserve the floor."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lyric_aligner.review.canonical_splices import apply_reviewed_splices, inspect_splice_context


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--inspect", action="store_true", help="Print full neighbouring subtitles and time gaps without applying edits")
    args = parser.parse_args()
    if args.inspect and (args.review or args.out_dir):
        parser.error("--inspect does not accept --review/--out-dir")
    if not args.inspect and (not args.review or not args.out_dir):
        parser.error("application requires --review and --out-dir")
    try:
        if args.inspect:
            print(json.dumps(inspect_splice_context(args.plan), ensure_ascii=False, indent=2))
            return 0
        result = apply_reviewed_splices(plan_path=args.plan, review_path=args.review, out_dir=args.out_dir)
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps({"final": result["final"], "applied_count": result["applied_count"], "automatic_lexical_authority": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
