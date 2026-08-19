#!/usr/bin/env python3
"""Repair subtitle text from canonical lyrics without reading audio or changing SRT timing."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.text_repair import write_repair_outputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-srt", required=True, type=Path)
    parser.add_argument(
        "--canonical-lrc",
        required=True,
        action="append",
        type=Path,
        help="Canonical LRC/TXT file; repeat in song order for multi-song subtitles.",
    )
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--auto-threshold", type=float, default=0.72)
    args = parser.parse_args()

    try:
        if args.out.resolve() == args.source_srt.resolve():
            raise ValueError("text-only repair refuses to overwrite the source SRT")
        if not args.source_srt.is_file():
            raise ValueError(f"source SRT does not exist: {args.source_srt}")
        missing = [path for path in args.canonical_lrc if not path.is_file()]
        if missing:
            raise ValueError(f"canonical lyric file does not exist: {missing[0]}")
        report = write_repair_outputs(
            args.source_srt,
            args.canonical_lrc,
            args.out,
            report_path=args.report,
            auto_threshold=args.auto_threshold,
        )
    except (OSError, ValueError, AssertionError) as exc:
        parser.error(str(exc))

    summary = {
        key: report[key]
        for key in (
            "mode",
            "status",
            "cue_count",
            "canonical_line_count",
            "replacement_count",
            "unchanged_count",
            "cue_review_count",
            "unmatched_canonical_count",
            "review_count",
            "timeline_unchanged",
            "formatting_policy",
            "output_srt_sha256",
        )
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if report["status"] == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
