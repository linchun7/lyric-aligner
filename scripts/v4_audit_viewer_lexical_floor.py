#!/usr/bin/env python3
"""Audit actual viewer subtitle text against canonical truth plus explicit presentation transforms."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.qa.viewer_lexical_floor import audit_viewer_lexical_floor


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--final-srt", required=True, type=Path)
    parser.add_argument("--final-audit", required=True, type=Path)
    parser.add_argument("--canonical-truth-overlay", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    try:
        inputs = {args.final_srt.resolve(), args.final_audit.resolve()}
        if args.canonical_truth_overlay is not None:
            inputs.add(args.canonical_truth_overlay.resolve())
        if args.out.resolve() in inputs:
            raise ValueError("viewer lexical audit output must not overwrite an input")
        result = audit_viewer_lexical_floor(
            final_srt=args.final_srt,
            final_audit=args.final_audit,
            canonical_truth_overlay=args.canonical_truth_overlay,
        )
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, AssertionError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "status": result["status"],
                "final_cue_count": result["final_cue_count"],
                "presentation_only_change_count": result["presentation_only_change_count"],
                "sensitive_mask_change_count": result["sensitive_mask_change_count"],
                "authorized_lexical_rebuttal_count": result["authorized_lexical_rebuttal_count"],
                "unauthorized_lexical_change_count": result["unauthorized_lexical_change_count"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if result["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
