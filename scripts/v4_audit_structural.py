#!/usr/bin/env python3
"""Read-only structural evidence audit for a v4 task."""

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
from lyric_aligner.io.path_safety import validate_separate_artifact_paths
from lyric_aligner.io.task_path_safety import protected_task_input_paths
from lyric_aligner.qa.structural_evidence import (
    StructuralEvidenceAuditError,
    audit_structural_evidence,
    editor_source_map_bound_artifact,
    file_sha256,
)
from task_contract import (
    load_task_manifest,
    resolve_manifest_record,
    verify_manifest_inputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--editor-source-map", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    try:
        task = load_task_manifest(args.task_manifest)
        issues = verify_manifest_inputs(args.task_manifest, task)
        if issues:
            raise ValueError("task manifest validation failed: " + "; ".join(issues))
        fingerprint = str(task["task_fingerprint_sha256"])
        inputs = task.get("inputs")
        if not isinstance(inputs, dict):
            raise ValueError("task manifest inputs must be an object")
        source_record = inputs.get("source_srt")
        audio_record = inputs.get("audio")
        if not isinstance(source_record, dict) or not isinstance(audio_record, dict):
            raise ValueError("task manifest requires source_srt and audio file inputs")
        if source_record.get("kind") != "file" or audio_record.get("kind") != "file":
            raise ValueError("task source_srt/audio inputs must be files")
        editor_srt = resolve_manifest_record(args.task_manifest, source_record)
        audio_path = resolve_manifest_record(args.task_manifest, audio_record)

        protected = protected_task_input_paths(
            manifest_path=args.task_manifest,
            manifest=task,
            repository_root=REPOSITORY_ROOT,
        )
        if args.editor_source_map is not None:
            protected["editor_source_map"] = args.editor_source_map
            protected["editor_source_mapping_artifact"] = editor_source_map_bound_artifact(
                args.editor_source_map,
                repository_root=REPOSITORY_ROOT,
            )
        validate_separate_artifact_paths(
            inputs=protected,
            outputs={"structural_audit_output": args.out},
        )

        result = audit_structural_evidence(
            editor_srt=editor_srt,
            audio_path=audio_path,
            expected_task_fingerprint=fingerprint,
            repository_root=REPOSITORY_ROOT,
            editor_source_map=args.editor_source_map,
        )
        payload = {
            "schema_version": "v4-structural-evidence-audit-1.0",
            "algorithm_version": __version__,
            "task_fingerprint_sha256": fingerprint,
            "authority": "diagnostic_only",
            "source_bindings": {
                "task_manifest": str(args.task_manifest),
                "task_manifest_sha256": file_sha256(args.task_manifest),
                "editor_srt": str(editor_srt),
                "editor_srt_sha256": file_sha256(editor_srt),
                "audio": str(audio_path),
                "audio_sha256": file_sha256(audio_path),
                "editor_source_map": str(args.editor_source_map) if args.editor_source_map else None,
                "editor_source_map_sha256": (
                    file_sha256(args.editor_source_map) if args.editor_source_map else None
                ),
            },
            "result": result,
        }
        atomic_write_json(args.out, payload)
    except (
        OSError,
        KeyError,
        ValueError,
        json.JSONDecodeError,
        StructuralEvidenceAuditError,
    ) as exc:
        parser.error(str(exc))

    print(
        json.dumps(
            {
                "authority": "diagnostic_only",
                "event_count": result["event_count"],
                "reorder_status": result["reorder"]["status"],
                "reorder_event_count": result["reorder"]["event_count"],
                "detached_tail_event_count": result["detached_tail"]["event_count"],
                "output": str(args.out),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
