#!/usr/bin/env python3
"""Materialize calibrated outer or internal alignment decisions into a new report/SRT.

This command is a thin production adapter over the existing fail-closed core materializers.
It rebinds the exact task manifest, source SRT, final-mix SHA, input report SHA, plan,
evidence and decisions before any subtitle row is written.  Outputs are created as one
new directory transaction and are never written in place.
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

from lyric_aligner.srt import parse_srt_strict
from lyric_aligner.timeline.boundary_refinement import apply_boundary_decisions_to_rows
from lyric_aligner.timeline.internal_segmentation import apply_internal_segmentation_to_rows
from scripts.v4_adjudicate_calibrated_alignment import ADJUDICATION_BUNDLE_SCHEMA_VERSION
from scripts.v4_plan_boundary_refinement import resolve_manifest_file, sha256_file


SCHEMA_VERSION = "calibrated-alignment-materialization-1.0"


def sha256_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is unreadable JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def load_report(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise ValueError("input report has no header")
            rows = [dict(row) for row in reader]
    except OSError as exc:
        raise ValueError("input report is unreadable") from exc
    if not rows:
        raise ValueError("input report is empty")
    return list(reader.fieldnames), rows


def _format_ms(value: int) -> str:
    if value < 0:
        raise ValueError("SRT timestamp must be nonnegative")
    hours, remainder = divmod(value, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _validated_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    previous_start = -1
    for position, raw in enumerate(rows, start=1):
        if not isinstance(raw, Mapping):
            raise ValueError(f"materialized row {position} is not an object")
        row = dict(raw)
        try:
            start = int(row.get("start_ms"))
            end = int(row.get("end_ms"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"materialized row {position} has invalid timing") from exc
        text = str(row.get("text") or "").strip()
        if start < 0 or end <= start:
            raise ValueError(f"materialized row {position} has a non-positive interval")
        if start < previous_start:
            raise ValueError("materialized row order moved backward in time")
        if not text:
            raise ValueError(f"materialized row {position} has blank text")
        row["start_ms"] = start
        row["end_ms"] = end
        row["text"] = text
        previous_start = start
        output.append(row)
    return output


def _write_csv(path: Path, original_fields: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> None:
    fields = list(dict.fromkeys([*original_fields, *(key for row in rows for key in row)]))
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def _write_srt(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    blocks: list[str] = []
    for index, row in enumerate(rows, start=1):
        blocks.append(
            f"{index}\n{_format_ms(int(row['start_ms']))} --> {_format_ms(int(row['end_ms']))}\n{str(row['text'])}"
        )
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def materialize_from_paths(
    *,
    mode: str,
    task_manifest_path: Path,
    report_path: Path,
    plan_path: Path,
    evidence_path: Path,
    decisions_path: Path,
    bundle_path: Path,
    out_dir: Path,
) -> dict[str, Any]:
    if mode not in {"outer", "internal"}:
        raise ValueError("mode must be outer or internal")
    destination = out_dir.resolve()
    staging = destination.with_name(destination.name + ".staging")
    if destination.exists() or staging.exists():
        raise FileExistsError("materialization output/staging directory must be new")

    manifest = load_json(task_manifest_path, label="task manifest")
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

    current_report_sha = sha256_file(report_path.resolve())
    original_fields, report_rows = load_report(report_path.resolve())
    plan = load_json(plan_path, label="alignment plan")
    evidence = load_json(evidence_path, label="alignment evidence")
    decisions = load_json(decisions_path, label="alignment decisions")
    bundle = load_json(bundle_path, label="adjudication bundle")

    if bundle.get("schema_version") != ADJUDICATION_BUNDLE_SCHEMA_VERSION:
        raise ValueError("unsupported adjudication bundle schema")
    if str(bundle.get("mode") or "") != mode:
        raise ValueError("adjudication bundle mode does not match materialization mode")
    if bool(bundle.get("subtitle_mutation_performed")):
        raise ValueError("adjudication bundle unexpectedly claims prior subtitle mutation")
    claimed_bundle_sha = str(bundle.get("bundle_sha256") or "")
    unsigned_bundle = dict(bundle)
    unsigned_bundle.pop("bundle_sha256", None)
    if len(claimed_bundle_sha) != 64 or claimed_bundle_sha != sha256_json(unsigned_bundle):
        raise ValueError("adjudication bundle SHA is invalid")
    plan_sha = sha256_json(plan)
    evidence_sha = sha256_json(evidence)
    decisions_sha = sha256_json(decisions)
    if str(bundle.get("plan_sha256") or "") != plan_sha:
        raise ValueError("adjudication bundle belongs to another plan")
    if str(bundle.get("evidence_sha256") or "") != evidence_sha:
        raise ValueError("adjudication bundle belongs to another evidence artifact")
    if str(bundle.get("decisions_sha256") or "") != decisions_sha:
        raise ValueError("adjudication bundle belongs to another decision artifact")
    if len(str(bundle.get("calibration_suite_sha256") or "")) != 64:
        raise ValueError("adjudication bundle calibration-suite identity is invalid")
    selective_sha = str(bundle.get("selective_consensus_calibration_sha256") or "").strip()
    if selective_sha and len(selective_sha) != 64:
        raise ValueError("adjudication bundle selective-consensus calibration identity is invalid")
    if mode == "outer" and selective_sha:
        raise ValueError("outer adjudication bundle must not carry selective internal consensus authority")
    if selective_sha:
        embedded = evidence.get("selective_consensus_calibration_artifact")
        if not isinstance(embedded, Mapping) or str(embedded.get("artifact_sha256") or "") != selective_sha:
            raise ValueError("adjudication bundle selective-consensus SHA differs from bound evidence")
    elif evidence.get("selective_consensus_calibration_artifact") is not None:
        raise ValueError("selective-consensus evidence is not bound by the adjudication bundle")
    raw_structural = bundle.get("structural_confirmations", [])
    if not isinstance(raw_structural, list) or any(not isinstance(value, str) or not value for value in raw_structural):
        raise ValueError("adjudication bundle structural confirmations are invalid")
    structural_confirmations = tuple(sorted(set(raw_structural)))
    if list(structural_confirmations) != raw_structural:
        raise ValueError("adjudication bundle structural confirmations are not canonical")
    if mode == "internal" and structural_confirmations:
        raise ValueError("internal adjudication bundle must not carry structural confirmations")

    if str(plan.get("task_fingerprint_sha256") or "") != fingerprint:
        raise ValueError("alignment plan belongs to another task manifest")
    if str(plan.get("source_srt_sha256") or "") != str(source_record.get("sha256") or ""):
        raise ValueError("alignment plan belongs to another source SRT")
    if str(plan.get("final_audio_sha256") or "") != str(audio_record.get("sha256") or ""):
        raise ValueError("alignment plan belongs to another final mix")
    if str(bundle.get("final_audio_sha256") or "") != str(audio_record.get("sha256") or ""):
        raise ValueError("adjudication bundle belongs to another final mix")
    if str(plan.get("report_sha256") or "") != current_report_sha:
        raise ValueError("alignment plan belongs to another input report")

    if mode == "outer":
        source_cues = [
            {
                "number": cue.number,
                "start_ms": cue.start_ms,
                "end_ms": cue.end_ms,
                "text": cue.text,
            }
            for cue in parse_srt_strict(source_path)
        ]
        materialized = apply_boundary_decisions_to_rows(
            report_rows=report_rows,
            source_cues=source_cues,
            plan=plan,
            evidence=evidence,
            decisions=decisions,
            structural_confirmations=structural_confirmations,
        )
    else:
        materialized = apply_internal_segmentation_to_rows(
            report_rows=report_rows,
            plan=plan,
            evidence=evidence,
            decisions=decisions,
        )
    rows = _validated_rows(materialized)

    staging.parent.mkdir(parents=True, exist_ok=True)
    staging.mkdir(parents=False, exist_ok=False)
    try:
        out_report = staging / "materialized.csv"
        out_srt = staging / "materialized.srt"
        out_artifact = staging / "materialization.artifact.json"
        _write_csv(out_report, original_fields, rows)
        _write_srt(out_srt, rows)
        parsed = parse_srt_strict(out_srt)
        if len(parsed) != len(rows):
            raise ValueError("materialized SRT row count mismatch")

        artifact: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "mode": mode,
            "task_fingerprint_sha256": fingerprint,
            "source_srt_sha256": str(source_record["sha256"]),
            "final_audio_sha256": str(audio_record["sha256"]),
            "input_report_sha256": current_report_sha,
            "plan_sha256": plan_sha,
            "evidence_sha256": evidence_sha,
            "decisions_sha256": decisions_sha,
            "adjudication_bundle_sha256": claimed_bundle_sha,
            "calibration_suite_sha256": str(bundle["calibration_suite_sha256"]),
            "structural_confirmations": list(structural_confirmations),
            "input_row_count": len(report_rows),
            "output_row_count": len(rows),
            "automatic_mutation_count": int(
                (decisions.get("summary") or {}).get(
                    "automatic_mutation_count",
                    (decisions.get("summary") or {}).get("automatic_split_boundary_count", 0),
                )
                or 0
            ),
            "audio_verified_internal_split_row_count": sum(
                str(row.get("status") or "") == "audio_verified_internal_split" for row in rows
            ),
            "output_report_sha256": sha256_file(out_report),
            "output_srt_sha256": sha256_file(out_srt),
            "materialization_authority": (
                "fresh_recomputed_calibrated_outer_adjudication"
                if mode == "outer"
                else "fresh_recomputed_calibrated_internal_adjudication"
            ),
        }
        artifact["artifact_sha256"] = sha256_json(artifact)
        out_artifact.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        staging.replace(destination)
        return artifact
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("outer", "internal"), required=True)
    parser.add_argument("--task-manifest", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    artifact = materialize_from_paths(
        mode=args.mode,
        task_manifest_path=args.task_manifest,
        report_path=args.report,
        plan_path=args.plan,
        evidence_path=args.evidence,
        decisions_path=args.decisions,
        bundle_path=args.bundle,
        out_dir=args.out_dir,
    )
    print(
        json.dumps(
            {
                "out_dir": str(args.out_dir.resolve()),
                "mode": args.mode,
                "input_row_count": artifact["input_row_count"],
                "output_row_count": artifact["output_row_count"],
                "automatic_mutation_count": artifact["automatic_mutation_count"],
                "artifact_sha256": artifact["artifact_sha256"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
