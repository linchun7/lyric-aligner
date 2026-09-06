#!/usr/bin/env python3
"""Build an immutable, audio-not-yet-read boundary-refinement plan."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.contracts.artifacts import atomic_write_json
from lyric_aligner.srt import parse_srt_strict
from lyric_aligner.timeline.boundary_refinement import (
    BoundaryRefinementConfig,
    build_boundary_refinement_plan,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_manifest_file(record: dict, *, label: str) -> Path:
    if not isinstance(record, dict) or record.get("kind") != "file":
        raise ValueError(f"manifest {label} must be a file record")
    raw = str(record.get("path") or "").strip()
    expected = str(record.get("sha256") or "").strip()
    if not raw or len(expected) != 64:
        raise ValueError(f"manifest {label} record is incomplete")
    path = Path(raw)
    if not path.is_absolute():
        path = REPOSITORY_ROOT / path
    path = path.resolve()
    actual = sha256_file(path)
    if actual != expected:
        raise ValueError(f"manifest {label} SHA mismatch: expected {expected}, got {actual}")
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--local-context-ms", type=int, default=900)
    parser.add_argument("--multi-evidence-context-ms", type=int, default=1800)
    parser.add_argument("--heavy-context-ms", type=int, default=3500)
    parser.add_argument("--structural-context-ms", type=int, default=8000)
    args = parser.parse_args()

    manifest = json.loads(args.task_manifest.read_text(encoding="utf-8"))
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

    report_path = args.report.resolve()
    report_hash = sha256_file(report_path)
    with report_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError("boundary refinement report is empty")

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
    config = BoundaryRefinementConfig(
        local_context_ms=args.local_context_ms,
        multi_evidence_context_ms=args.multi_evidence_context_ms,
        heavy_context_ms=args.heavy_context_ms,
        structural_context_ms=args.structural_context_ms,
    )
    plan = build_boundary_refinement_plan(
        task_fingerprint_sha256=fingerprint,
        source_srt_sha256=str(source_record["sha256"]),
        final_audio_sha256=str(audio_record["sha256"]),
        report_sha256=report_hash,
        source_cues=source_cues,
        report_rows=rows,
        config=config,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(args.out, plan)
    print(json.dumps({"out": str(args.out), **plan["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
