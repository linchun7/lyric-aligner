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

from lyric_aligner.text_repair import (
    DEFAULT_AUTO_THRESHOLD,
    PRODUCTION_MIN_AUTO_THRESHOLD,
    write_repair_outputs,
)


def _validate_path_ownership(args: argparse.Namespace) -> None:
    input_paths = {
        args.source_srt.resolve(),
        *(path.resolve() for path in args.canonical_lrc),
    }
    output_paths = [args.out.resolve()]
    if args.report is not None:
        output_paths.append(args.report.resolve())
    if len(set(output_paths)) != len(output_paths):
        raise ValueError("text-only repair output and report paths must be different")
    if any(path in input_paths for path in output_paths):
        raise ValueError(
            "text-only repair output/report must not overwrite source SRT or canonical lyrics"
        )


def _validate_production_threshold(value: float) -> None:
    if value < PRODUCTION_MIN_AUTO_THRESHOLD:
        raise ValueError(
            "production auto-threshold must be at least "
            f"{PRODUCTION_MIN_AUTO_THRESHOLD:.2f}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-srt", required=True, type=Path)
    parser.add_argument(
        "--canonical-lrc",
        required=True,
        action="append",
        type=Path,
        help="Canonical LRC/TXT/QRC file; repeat in song order for multi-song subtitles.",
    )
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--auto-threshold",
        type=float,
        default=DEFAULT_AUTO_THRESHOLD,
        help=(
            "Automatic text-repair similarity threshold; production values below "
            f"{PRODUCTION_MIN_AUTO_THRESHOLD:.2f} are rejected."
        ),
    )
    args = parser.parse_args()

    try:
        _validate_path_ownership(args)
        _validate_production_threshold(args.auto_threshold)
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

    summary_keys = (
        "mode",
        "status",
        "coverage_status",
        "cue_count",
        "canonical_line_count",
        "replacement_count",
        "unchanged_count",
        "cue_review_count",
        "unmatched_canonical_count",
        "coverage_warning_count",
        "review_count",
        "timeline_unchanged",
        "cue_count_unchanged",
        "span_match_count",
        "segmentation_span_count",
        "edit_counts",
        "formatting_policy",
        "output_srt_sha256",
    )
    print(json.dumps({key: report[key] for key in summary_keys}, ensure_ascii=False))
    return 0 if report["status"] == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())