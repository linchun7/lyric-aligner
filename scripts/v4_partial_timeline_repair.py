#!/usr/bin/env python3
"""Preview fail-closed timing fixes for explicitly selected SRT cues."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.partial_timeline_repair import (
    PartialTimelineRepairError,
    write_partial_timeline_preview,
)


def _validate_path_ownership(args: argparse.Namespace) -> None:
    inputs = {
        args.source_srt.resolve(),
        args.canonical_lrc.resolve(),
        args.forced_mix_evidence.resolve(),
        args.forced_mix_evidence_artifact.resolve(),
    }
    outputs = [args.report.resolve()]
    if args.preview_out is not None:
        outputs.append(args.preview_out.resolve())
    if len(set(outputs)) != len(outputs):
        raise PartialTimelineRepairError(
            "partial timing report and preview paths must be different"
        )
    if any(path in inputs for path in outputs):
        raise PartialTimelineRepairError(
            "partial timing report/preview must not overwrite an input"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-srt", required=True, type=Path)
    parser.add_argument(
        "--canonical-lrc",
        required=True,
        type=Path,
        help="Canonical lyric for exactly one V4 occurrence; timestamps are text-order metadata only.",
    )
    parser.add_argument(
        "--forced-mix-evidence",
        required=True,
        type=Path,
        help="P8 forced_alignment_mix_projection payload for the current effective run.",
    )
    parser.add_argument(
        "--forced-mix-evidence-artifact",
        required=True,
        type=Path,
        help="Artifact manifest paired with --forced-mix-evidence.",
    )
    parser.add_argument(
        "--occurrence-id",
        required=True,
        help="Expected V4 occurrence ID; selected evidence must belong to it.",
    )
    parser.add_argument(
        "--cue",
        required=True,
        action="append",
        type=int,
        help="Numeric SRT cue number to preview-repair; repeat for multiple cues.",
    )
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument(
        "--preview-out",
        type=Path,
        help="Optional NON-RELEASEABLE preview SRT. Unselected cue timing stays frozen.",
    )
    parser.add_argument("--text-match-threshold", type=float, default=0.86)
    args = parser.parse_args()

    try:
        _validate_path_ownership(args)
        for path, label in (
            (args.source_srt, "source SRT"),
            (args.canonical_lrc, "canonical lyric"),
            (args.forced_mix_evidence, "forced mix evidence"),
            (args.forced_mix_evidence_artifact, "forced mix evidence artifact"),
        ):
            if not path.is_file():
                raise PartialTimelineRepairError(f"{label} does not exist: {path}")
        report = write_partial_timeline_preview(
            args.source_srt,
            args.canonical_lrc,
            args.forced_mix_evidence,
            args.forced_mix_evidence_artifact,
            expected_occurrence_id=args.occurrence_id,
            repair_cue_numbers=args.cue,
            report_path=args.report,
            preview_out=args.preview_out,
            text_match_threshold=args.text_match_threshold,
        )
    except (OSError, PartialTimelineRepairError, ValueError, AssertionError) as exc:
        parser.error(str(exc))

    summary = {
        key: report[key]
        for key in (
            "mode",
            "status",
            "releaseable",
            "automatic_timing_change_allowed",
            "cue_count",
            "selected_cue_count",
            "locked_cue_count",
            "proposed_change_count",
            "selected_unchanged_count",
            "review_count",
            "timing_evidence",
        )
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if report["status"] == "preview_ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
