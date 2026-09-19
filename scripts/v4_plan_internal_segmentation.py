#!/usr/bin/env python3
"""Build an immutable internal-segmentation plan from a bound recovery report."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.contracts.artifacts import atomic_write_json
from lyric_aligner.srt import parse_srt_strict
from lyric_aligner.timeline.internal_segmentation import (
    DEFAULT_INTERNAL_CONTEXT_MS,
    build_internal_segmentation_plan,
)
from scripts.v4_plan_boundary_refinement import resolve_manifest_file, sha256_file


def build_plan_from_paths(
    *,
    task_manifest_path: Path,
    report_path: Path,
    context_ms: int = DEFAULT_INTERNAL_CONTEXT_MS,
) -> dict[str, Any]:
    manifest = json.loads(task_manifest_path.read_text(encoding="utf-8-sig"))
    if not isinstance(manifest, dict):
        raise ValueError("task manifest must be an object")
    inputs = manifest.get("inputs")
    if not isinstance(inputs, dict):
        raise ValueError("task manifest has no inputs object")
    fingerprint = str(manifest.get("task_fingerprint_sha256") or "")
    if len(fingerprint) != 64:
        raise ValueError("task manifest fingerprint is invalid")

    source_record = inputs.get("source_srt")
    audio_record = inputs.get("audio")
    source_path = resolve_manifest_file(source_record, label="source_srt")
    resolve_manifest_file(audio_record, label="audio")

    resolved_report = report_path.resolve()
    if not resolved_report.is_file():
        raise FileNotFoundError(f"internal segmentation report does not exist: {resolved_report}")
    report_hash = sha256_file(resolved_report)
    with resolved_report.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError("internal segmentation report is empty")

    cues = parse_srt_strict(source_path)
    source_cues = [
        {
            "number": cue.number,
            "start_ms": cue.start_ms,
            "end_ms": cue.end_ms,
            "text": cue.text,
        }
        for cue in cues
    ]
    return build_internal_segmentation_plan(
        task_fingerprint_sha256=fingerprint,
        source_srt_sha256=str(source_record["sha256"]),
        final_audio_sha256=str(audio_record["sha256"]),
        report_sha256=report_hash,
        source_cues=source_cues,
        report_rows=rows,
        context_ms=context_ms,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-manifest", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--context-ms", type=int, default=DEFAULT_INTERNAL_CONTEXT_MS)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    if args.out.exists():
        raise FileExistsError("internal segmentation plan output must be a new path")
    plan = build_plan_from_paths(
        task_manifest_path=args.task_manifest,
        report_path=args.report,
        context_ms=args.context_ms,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(args.out, plan)
    print(json.dumps({"out": str(args.out), **plan["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
