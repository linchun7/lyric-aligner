#!/usr/bin/env python3
"""Apply explicit human-confirmed text-ownership corrections after V4 timing materialization.

This stage is intentionally narrow. It never changes timing, cue count, LRC ownership,
segmentation authority, or any timing-authority field. A replacement is accepted only
when the source row is exactly identified, its current text matches the frozen repair
spec, and the replacement compactly equals the row's already-bound canonical segments.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.contracts.artifacts import sha256_file
from lyric_aligner.srt import parse_srt_strict
from scripts.task_contract import load_task_manifest, verify_manifest_inputs

SCHEMA_VERSION = "post-materialization-human-correction-1.0"
SPEC_SCHEMA_VERSION = "post-materialization-human-correction-spec-1.0"
SOURCE_MATERIALIZATION_SCHEMA = "calibrated-alignment-materialization-1.0"


def sha256_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_json(path: Path, *, label: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def compact_text(value: str) -> str:
    return "".join(ch for ch in str(value) if not ch.isspace())


def read_report(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    if not fields or not rows:
        raise ValueError("source report must be a non-empty CSV")
    return fields, rows


def validate_source_binding(source_srt: Path, source_report: Path, rows: Sequence[Mapping[str, str]]) -> None:
    cues = parse_srt_strict(source_srt)
    if len(cues) != len(rows):
        raise ValueError("source SRT/report row count mismatch")
    for position, (cue, row) in enumerate(zip(cues, rows), start=1):
        if int(row["start_ms"]) != cue.start_ms or int(row["end_ms"]) != cue.end_ms:
            raise ValueError(f"source SRT/report timing mismatch at row {position}")
        if str(row.get("text") or "") != cue.text:
            raise ValueError(f"source SRT/report text mismatch at row {position}")


def verify_source_materialization(
    artifact: Mapping[str, Any],
    *,
    source_srt: Path,
    source_report: Path,
    task_fingerprint: str,
) -> str:
    if artifact.get("schema_version") != SOURCE_MATERIALIZATION_SCHEMA or artifact.get("mode") != "internal":
        raise ValueError("source materialization artifact is not V4 internal materialization")
    if str(artifact.get("task_fingerprint_sha256") or "") != task_fingerprint:
        raise ValueError("source materialization belongs to another task")
    if str(artifact.get("output_srt_sha256") or "") != sha256_file(source_srt):
        raise ValueError("source materialization SRT SHA mismatch")
    if str(artifact.get("output_report_sha256") or "") != sha256_file(source_report):
        raise ValueError("source materialization report SHA mismatch")
    claimed = str(artifact.get("artifact_sha256") or "")
    payload = dict(artifact)
    payload.pop("artifact_sha256", None)
    if len(claimed) != 64 or sha256_json(payload) != claimed:
        raise ValueError("source materialization artifact self-SHA mismatch")
    return claimed


def apply_repairs(
    *,
    rows: list[dict[str, str]],
    repairs: Sequence[Mapping[str, Any]],
) -> list[str]:
    applied_ids: list[str] = []
    seen_cues: set[str] = set()
    for item in repairs:
        if not isinstance(item, Mapping) or str(item.get("kind") or "") != "text_ownership":
            raise ValueError("every correction must be a text_ownership object")
        cue = str(item.get("original_cue") or "").strip()
        if not cue.isdigit() or cue in seen_cues:
            raise ValueError("correction original_cue must be unique numeric text")
        seen_cues.add(cue)
        matches = [row for row in rows if str(row.get("original_cue") or "").strip() == cue]
        if len(matches) != 1:
            raise ValueError(f"correction cue {cue} must resolve to exactly one materialized row")
        row = matches[0]
        expected_text = str(item.get("expected_text") or "")
        replacement_text = str(item.get("replacement_text") or "")
        expected_lrc_indices = str(item.get("expected_lrc_indices") or "")
        reason = str(item.get("reason") or "").strip()
        if not replacement_text.strip() or not reason:
            raise ValueError(f"correction cue {cue} is incomplete")
        if str(row.get("text") or "") != expected_text:
            raise ValueError(f"correction cue {cue} source text changed")
        if str(row.get("lrc_indices") or "") != expected_lrc_indices:
            raise ValueError(f"correction cue {cue} LRC identity changed")
        raw_segments = str(row.get("canonical_segments_json") or "").strip()
        try:
            segments = json.loads(raw_segments)
        except json.JSONDecodeError as exc:
            raise ValueError(f"correction cue {cue} canonical segments are invalid") from exc
        if not isinstance(segments, list) or not segments:
            raise ValueError(f"correction cue {cue} has no canonical segments")
        canonical = " ".join(str(segment.get("text") or "") for segment in segments if isinstance(segment, Mapping))
        if compact_text(replacement_text) != compact_text(canonical):
            raise ValueError(f"correction cue {cue} replacement does not equal bound canonical segments")
        original_start = row["start_ms"]
        original_end = row["end_ms"]
        row["text"] = replacement_text
        row["evidence"] = str(row.get("evidence") or "") + "+post_materialization_human_text_ownership_repair"
        row["post_materialization_correction"] = "human_confirmed_text_ownership"
        row["post_materialization_correction_reason"] = reason
        if row["start_ms"] != original_start or row["end_ms"] != original_end:
            raise AssertionError("text ownership correction changed timing")
        applied_ids.append(cue)
    return applied_ids


def format_ms(value: int) -> str:
    hour, remain = divmod(value, 3_600_000)
    minute, remain = divmod(remain, 60_000)
    second, millis = divmod(remain, 1000)
    return f"{hour:02d}:{minute:02d}:{second:02d},{millis:03d}"


def write_outputs(out_dir: Path, fields: Sequence[str], rows: Sequence[Mapping[str, str]]) -> tuple[Path, Path]:
    out_report = out_dir / "corrected.csv"
    out_srt = out_dir / "corrected.srt"
    final_fields = list(dict.fromkeys([*fields, *(key for row in rows for key in row)]))
    with out_report.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=final_fields, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)
    blocks = [
        f"{index}\n{format_ms(int(row['start_ms']))} --> {format_ms(int(row['end_ms']))}\n{row['text']}"
        for index, row in enumerate(rows, start=1)
    ]
    out_srt.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    return out_srt, out_report


def run(
    *,
    task_manifest_path: Path,
    source_srt: Path,
    source_report: Path,
    source_materialization_artifact_path: Path,
    repair_spec_path: Path,
    out_dir: Path,
) -> dict[str, Any]:
    destination = out_dir.resolve()
    staging = destination.with_name(destination.name + ".staging")
    if destination.exists() or staging.exists():
        raise FileExistsError("post-materialization correction output/staging path must be new")

    task = load_task_manifest(task_manifest_path)
    task_issues = verify_manifest_inputs(task_manifest_path, task)
    if task_issues:
        raise ValueError("task manifest validation failed: " + "; ".join(task_issues))
    fingerprint = str(task["task_fingerprint_sha256"])
    source_artifact = load_json(source_materialization_artifact_path, label="source materialization artifact")
    source_artifact_sha = verify_source_materialization(
        source_artifact,
        source_srt=source_srt,
        source_report=source_report,
        task_fingerprint=fingerprint,
    )
    spec = load_json(repair_spec_path, label="repair spec")
    if spec.get("schema_version") != SPEC_SCHEMA_VERSION:
        raise ValueError("repair spec schema mismatch")
    if str(spec.get("task_fingerprint_sha256") or "") != fingerprint:
        raise ValueError("repair spec belongs to another task")
    if str(spec.get("source_materialization_artifact_sha256") or "") != source_artifact_sha:
        raise ValueError("repair spec belongs to another materialization artifact")
    repairs = spec.get("corrections")
    if not isinstance(repairs, list) or not repairs:
        raise ValueError("repair spec corrections must be a non-empty list")

    fields, rows = read_report(source_report)
    validate_source_binding(source_srt, source_report, rows)
    original_intervals = [(row["start_ms"], row["end_ms"]) for row in rows]
    original_count = len(rows)
    applied = apply_repairs(rows=rows, repairs=repairs)
    if len(rows) != original_count or [(row["start_ms"], row["end_ms"]) for row in rows] != original_intervals:
        raise AssertionError("post-materialization correction changed timing topology")

    staging.parent.mkdir(parents=True, exist_ok=True)
    staging.mkdir(parents=False, exist_ok=False)
    try:
        out_srt, out_report = write_outputs(staging, fields, rows)
        validate_source_binding(out_srt, out_report, rows)
        artifact: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "task_fingerprint_sha256": fingerprint,
            "source_materialization_artifact_sha256": source_artifact_sha,
            "source_srt_sha256": sha256_file(source_srt),
            "source_report_sha256": sha256_file(source_report),
            "repair_spec_sha256": sha256_file(repair_spec_path),
            "output_srt_sha256": sha256_file(out_srt),
            "output_report_sha256": sha256_file(out_report),
            "input_row_count": original_count,
            "output_row_count": len(rows),
            "correction_count": len(applied),
            "corrected_original_cues": applied,
            "timing_changed_count": 0,
            "subtitle_timing_mutation_performed": False,
        }
        artifact["artifact_sha256"] = sha256_json(artifact)
        (staging / "correction.artifact.json").write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        staging.replace(destination)
        return artifact
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--source-srt", required=True, type=Path)
    parser.add_argument("--source-report", required=True, type=Path)
    parser.add_argument("--source-materialization-artifact", required=True, type=Path)
    parser.add_argument("--repair-spec", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()
    artifact = run(
        task_manifest_path=args.task_manifest,
        source_srt=args.source_srt,
        source_report=args.source_report,
        source_materialization_artifact_path=args.source_materialization_artifact,
        repair_spec_path=args.repair_spec,
        out_dir=args.out_dir,
    )
    print(json.dumps({
        "out_dir": str(args.out_dir.resolve()),
        "correction_count": artifact["correction_count"],
        "timing_changed_count": artifact["timing_changed_count"],
        "artifact_sha256": artifact["artifact_sha256"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
