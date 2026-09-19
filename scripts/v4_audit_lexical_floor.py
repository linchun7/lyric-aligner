#!/usr/bin/env python3
"""Audit a production subtitle against resolved canonical occurrence character streams."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.qa.lexical_floor import audit_resolved_lexical_floor


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical-evaluation-audit", required=True, type=Path)
    parser.add_argument("--final-srt", required=True, type=Path)
    parser.add_argument("--final-audit", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    try:
        inputs = {
            args.canonical_evaluation_audit.resolve(),
            args.final_srt.resolve(),
            args.final_audit.resolve(),
        }
        if args.out.resolve() in inputs:
            raise ValueError("lexical-floor audit output must not overwrite an input")
        result = audit_resolved_lexical_floor(
            canonical_evaluation_audit=args.canonical_evaluation_audit,
            final_srt=args.final_srt,
            final_audit=args.final_audit,
        )
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, AssertionError) as exc:
        parser.error(str(exc))
    print(json.dumps({
        "status": result["status"],
        "final_cue_count": result["final_cue_count"],
        "occurrence_count": result["occurrence_count"],
        "canonical_character_count": result["canonical_character_count"],
        "covered_character_count": result["covered_character_count"],
        "lexical_error_count": result["lexical_error_count"],
        "coverage_gap_count": result["coverage_gap_count"],
        "coverage_overlap_count": result["coverage_overlap_count"],
        "unowned_lexical_cue_count": result["unowned_lexical_cue_count"],
    }, ensure_ascii=False))
    return 0 if result["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
