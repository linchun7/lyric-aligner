#!/usr/bin/env python3
"""Safe Final lexical/gap review plus mandatory model semantic-sensitive finalization."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lyric_aligner.review.safe_final_review import build_candidates, verify_gap_review
from lyric_aligner.review.semantic_sensitive import (
    build_semantic_sensitive_pack,
    finalize_semantic_sensitive_review,
)


def _write_json_create(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    scan = commands.add_parser("scan", help="No ASR, no SRT writes; full context and occurrence diagnostics")
    scan.add_argument("--plan", required=True, type=Path)
    scan.add_argument("--final-mix", type=Path, help="Optional hash binding only; does not decode audio")
    scan.add_argument("--out", required=True, type=Path, help="New JSON file; never overwrites an input or prior result")

    verify = commands.add_parser("verify-gaps", help="Validate evidence/geometry; NEVER grants insertion authority")
    verify.add_argument("--pack", required=True, type=Path)
    verify.add_argument("--review", required=True, type=Path)
    verify.add_argument("--out", required=True, type=Path, help="New JSON file; never overwrites an input or prior result")

    sensitive_pack = commands.add_parser(
        "sensitive-pack",
        help="Freeze the complete pre-release SRT for mandatory model semantic-sensitive review",
    )
    sensitive_pack.add_argument("--source-srt", required=True, type=Path)
    sensitive_pack.add_argument("--song-list", required=True, type=Path)
    sensitive_pack.add_argument("--out", required=True, type=Path, help="New model-review pack JSON")

    sensitive_finalize = commands.add_parser(
        "sensitive-finalize",
        help="Materialize only high-confidence model-approved masks; REVIEW fails closed",
    )
    sensitive_finalize.add_argument("--pack", required=True, type=Path)
    sensitive_finalize.add_argument("--review", required=True, type=Path)
    sensitive_finalize.add_argument("--out-srt", required=True, type=Path)
    sensitive_finalize.add_argument("--out-audit", required=True, type=Path)

    args = parser.parse_args()
    try:
        if args.command == "scan":
            result = build_candidates(args.plan, final_mix=args.final_mix)
            _write_json_create(args.out, result)
            message = {
                "out": str(args.out),
                "production_writeback_permitted": False,
            }
        elif args.command == "verify-gaps":
            result = verify_gap_review(args.pack, args.review)
            _write_json_create(args.out, result)
            message = {
                "out": str(args.out),
                "production_writeback_permitted": False,
            }
        elif args.command == "sensitive-pack":
            result = build_semantic_sensitive_pack(
                args.source_srt,
                song_list=args.song_list,
            )
            _write_json_create(args.out, result)
            message = {
                "out": str(args.out),
                "full_scan_required": True,
                "candidate_hint_count": result["candidate_hint_count"],
                "production_writeback_permitted": False,
            }
        else:
            audit = finalize_semantic_sensitive_review(
                args.pack,
                args.review,
                out_srt=args.out_srt,
                out_audit=args.out_audit,
            )
            message = {
                "out_srt": str(args.out_srt),
                "out_audit": str(args.out_audit),
                "reviewer_model": audit["reviewer_model"],
                "masked_cue_count": audit["masked_cue_count"],
                "masked_term_count": audit["masked_term_count"],
                "production_writeback_permitted": True,
                "mutation_scope": "mask_only_display_text",
                "formal_safe_final_ready": True,
            }
    except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(message, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
