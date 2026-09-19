#!/usr/bin/env python3
"""Read-only structural/presentation audit for a publish-ready v4 final subtitle."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner import __version__
from lyric_aligner.contracts.artifacts import atomic_write_json
from lyric_aligner.io.materializer_path_safety import declared_input_paths
from lyric_aligner.io.path_safety import validate_separate_artifact_paths
from lyric_aligner.io.task_path_safety import protected_task_input_paths
from lyric_aligner.qa.final_candidate_audit import (
    FinalCandidateAuditError,
    audit_final_candidate,
)
from lyric_aligner.qa.final_integrity import (
    FinalIntegrityError,
    read_audit_rows,
    validate_qa_payload,
    validate_srt_report_binding,
)
from lyric_aligner.srt import parse_srt_strict
from task_contract import load_task_manifest, verify_manifest_inputs


def _load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _timeline_path(value: object) -> Path:
    path = Path(str(value or ""))
    if not str(path):
        raise ValueError("run occurrence is missing timeline_path")
    return path if path.is_absolute() else REPOSITORY_ROOT / path


def _occurrence_windows(run: dict, *, fingerprint: str) -> dict[str, tuple[int, int]]:
    windows: dict[str, tuple[int, int]] = {}
    rows = run.get("occurrences")
    if not isinstance(rows, list) or not rows:
        raise ValueError("run has no occurrences")
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("run occurrence entry is invalid")
        occurrence_id = str(row.get("occurrence_id") or "").strip()
        if not occurrence_id or occurrence_id in windows:
            raise ValueError("run has missing/duplicate occurrence_id")
        timeline = _load_json(_timeline_path(row.get("timeline_path")))
        if timeline.get("task_fingerprint_sha256") != fingerprint:
            raise ValueError(f"timeline task fingerprint mismatch: {occurrence_id}")
        if str(timeline.get("occurrence_id") or "") != occurrence_id:
            raise ValueError(f"timeline occurrence_id mismatch: {occurrence_id}")
        result = timeline.get("result")
        window = result.get("window") if isinstance(result, dict) else None
        if not isinstance(window, dict):
            raise ValueError(f"timeline has no authoritative window: {occurrence_id}")
        try:
            start_ms = int(window["start_ms"])
            end_ms = int(window["end_ms"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"timeline window is invalid: {occurrence_id}") from exc
        if start_ms < 0 or end_ms <= start_ms:
            raise ValueError(f"timeline window is non-positive: {occurrence_id}")
        windows[occurrence_id] = (start_ms, end_ms)
    return windows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--final-srt", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--qa-json", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--long-hold-threshold-ms", type=int, default=6000)
    parser.add_argument("--extreme-hold-threshold-ms", type=int, default=8000)
    args = parser.parse_args()

    try:
        task = load_task_manifest(args.task_manifest)
        task_issues = verify_manifest_inputs(args.task_manifest, task)
        if task_issues:
            raise ValueError("task manifest validation failed: " + "; ".join(task_issues))
        fingerprint = str(task["task_fingerprint_sha256"])
        run = _load_json(args.run)
        if run.get("task_fingerprint_sha256") != fingerprint:
            raise ValueError("run belongs to another task")

        protected = protected_task_input_paths(
            manifest_path=args.task_manifest,
            manifest=task,
            repository_root=REPOSITORY_ROOT,
        )
        protected.update(
            {
                "run": args.run,
                "final_srt": args.final_srt,
                "audit_csv": args.report,
                "qa_json": args.qa_json,
            }
        )
        protected.update(declared_input_paths({"run": run}))
        validate_separate_artifact_paths(inputs=protected, outputs={"audit_output": args.out})

        validate_srt_report_binding(
            args.final_srt,
            args.report,
            expected_task_fingerprint=fingerprint,
        )
        validate_qa_payload(
            args.qa_json,
            expected_task_fingerprint=fingerprint,
            expected_algorithm_version=__version__,
        )

        plan = run.get("plan")
        if not isinstance(plan, dict):
            raise ValueError("run has no production plan")
        content_end = plan.get("content_end", plan.get("mix_duration"))
        if not isinstance(content_end, (int, float)) or isinstance(content_end, bool):
            raise ValueError("run has invalid content_end/mix_duration")
        content_end_ms = int(round(float(content_end) * 1000.0))

        regions = run.get("confirmed_overlap_regions") or []
        if not isinstance(regions, list):
            raise ValueError("run confirmed_overlap_regions must be a list")
        audit = audit_final_candidate(
            parse_srt_strict(args.final_srt),
            read_audit_rows(args.report),
            occurrence_windows=_occurrence_windows(run, fingerprint=fingerprint),
            confirmed_overlap_regions=regions,
            content_end_ms=content_end_ms,
            long_hold_threshold_ms=args.long_hold_threshold_ms,
            extreme_hold_threshold_ms=args.extreme_hold_threshold_ms,
        )
        payload = {
            "algorithm_version": __version__,
            "task_fingerprint_sha256": fingerprint,
            "authority": "diagnostic_only",
            "source_run": str(args.run),
            "result": audit,
        }
        atomic_write_json(args.out, payload)
    except (
        OSError,
        KeyError,
        ValueError,
        json.JSONDecodeError,
        FinalIntegrityError,
        FinalCandidateAuditError,
    ) as exc:
        parser.error(str(exc))

    summary = {
        "passed": audit["passed"],
        "cue_count": audit["cue_count"],
        "error_count": len(audit["errors"]),
        "warning_count": len(audit["warnings"]),
        "duration": audit["duration"],
        "confirmed_overlap_intersections": len(audit["confirmed_overlap_cue_intersections"]),
        "unconfirmed_overlap_intersections": len(audit["unconfirmed_overlap_cue_intersections"]),
        "output": str(args.out),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if audit["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
