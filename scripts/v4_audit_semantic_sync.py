#!/usr/bin/env python3
"""Fail-closed Max semantic timing QA using independent audio evidence.

Editor/Jianying SRT remains an auxiliary witness only. Release-grade timing must
also agree with audio-semantic evidence already projected into mix time through
the v4 evidence-fusion path. Projected forced alignment is the preferred strong
family; ASR-only evidence may be used only together with a lexically reliable
editor witness that independently agrees with projection and final timing.
"""

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
from lyric_aligner.qa.final_integrity import read_audit_rows, validate_srt_report_binding
from lyric_aligner.qa.semantic_sync import (
    audit_independent_audio_sync,
    audit_semantic_sync,
    parse_song_windows,
)
from lyric_aligner.srt import Cue, parse_srt_strict
from task_contract import (
    load_task_manifest,
    resolve_manifest_record,
    sha256,
    verify_manifest_inputs,
)


_SEMANTIC_SYNC_SCHEMA = "semantic-sync-qa-1.1"
_SEMANTIC_SYNC_MODE = "independent_audio_semantic_release_gate"


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


def _projection_cues(run: dict, *, fingerprint: str) -> list[Cue]:
    rows = run.get("occurrences")
    if not isinstance(rows, list) or not rows:
        raise ValueError("run has no occurrences")
    ordered = sorted(rows, key=lambda row: int(row.get("ordinal", 0)))
    cues: list[Cue] = []
    next_number = 1
    for occurrence in ordered:
        timeline = _load_json(_timeline_path(occurrence.get("timeline_path")))
        if timeline.get("task_fingerprint_sha256") != fingerprint:
            raise ValueError("timeline belongs to another task")
        if str(timeline.get("algorithm_version") or "") != __version__:
            raise ValueError("timeline algorithm_version differs from current runtime")
        result = timeline.get("result")
        lines = result.get("lines") if isinstance(result, dict) else None
        if not isinstance(lines, list):
            raise ValueError("timeline has no canonical lines")
        for line in lines:
            if not isinstance(line, dict):
                raise ValueError("timeline canonical line is invalid")
            start_ms = int(line["mix_start_ms"])
            raw_end = line.get("mix_end_ms")
            end_ms = int(raw_end) if raw_end is not None else start_ms + 1
            text = str(line.get("text") or "").strip()
            if end_ms <= start_ms or not text:
                continue
            cues.append(Cue(next_number, start_ms, end_ms, text))
            next_number += 1
    cues.sort(key=lambda cue: (cue.start_ms, cue.end_ms, cue.number))
    return [
        Cue(index + 1, cue.start_ms, cue.end_ms, cue.text)
        for index, cue in enumerate(cues)
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--fusion", required=True, type=Path)
    parser.add_argument("--final-srt", required=True, type=Path)
    parser.add_argument("--final-report", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    try:
        task = load_task_manifest(args.task_manifest)
        issues = verify_manifest_inputs(args.task_manifest, task)
        if issues:
            raise ValueError("task manifest validation failed: " + "; ".join(issues))
        fingerprint = str(task["task_fingerprint_sha256"])
        inputs = task["inputs"]
        source_srt = resolve_manifest_record(args.task_manifest, inputs["source_srt"])
        audio = resolve_manifest_record(args.task_manifest, inputs["audio"])
        song_list = resolve_manifest_record(args.task_manifest, inputs["song_list"])

        run = _load_json(args.run)
        if run.get("task_fingerprint_sha256") != fingerprint:
            raise ValueError("run belongs to another task")
        if str(run.get("algorithm_version") or "") != __version__:
            raise ValueError("run algorithm_version differs from current runtime")

        fusion = _load_json(args.fusion)
        if fusion.get("task_fingerprint_sha256") != fingerprint:
            raise ValueError("evidence fusion belongs to another task")
        if str(fusion.get("algorithm_version") or "") != __version__:
            raise ValueError("evidence fusion algorithm_version differs from current runtime")
        if fusion.get("mode") != "shadow_only":
            raise ValueError("evidence fusion must be shadow_only")

        protected = protected_task_input_paths(
            manifest_path=args.task_manifest,
            manifest=task,
            repository_root=REPOSITORY_ROOT,
        )
        protected.update(
            {
                "run": args.run,
                "fusion": args.fusion,
                "final_srt": args.final_srt,
                "final_report": args.final_report,
            }
        )
        protected.update(declared_input_paths({"run": run}))
        validate_separate_artifact_paths(
            inputs=protected,
            outputs={"semantic_sync_qa": args.out},
        )

        plan = run.get("plan")
        if not isinstance(plan, dict):
            raise ValueError("run has no production plan")
        content_end = plan.get("content_end", plan.get("mix_duration"))
        if not isinstance(content_end, (int, float)) or isinstance(content_end, bool):
            raise ValueError("run has invalid content_end/mix_duration")
        content_end_ms = int(round(float(content_end) * 1000.0))

        validate_srt_report_binding(
            args.final_srt,
            args.final_report,
            expected_task_fingerprint=fingerprint,
        )
        final_rows = read_audit_rows(args.final_report)
        source_cues = parse_srt_strict(source_srt)
        projection_cues = _projection_cues(run, fingerprint=fingerprint)
        final_cues = parse_srt_strict(args.final_srt)
        windows = parse_song_windows(song_list, content_end_ms=content_end_ms)

        editor_projection = audit_semantic_sync(source_cues, projection_cues, windows)
        editor_final = audit_semantic_sync(source_cues, final_cues, windows)
        audio_sync = audit_independent_audio_sync(
            fusion,
            final_rows,
            editor_projection_audit=editor_projection,
            editor_final_audit=editor_final,
        )
        projection = audio_sync["projection_sync"]
        final = audio_sync["final_sync"]
        passed = bool(audio_sync["passed"])
        payload = {
            "schema_version": _SEMANTIC_SYNC_SCHEMA,
            "algorithm_version": __version__,
            "task_fingerprint_sha256": fingerprint,
            "mode": _SEMANTIC_SYNC_MODE,
            "passed": passed,
            "bindings": {
                "source_srt_sha256": sha256(source_srt),
                "audio_sha256": sha256(audio),
                "song_list_sha256": sha256(song_list),
                "run_sha256": sha256(args.run),
                "fusion_sha256": sha256(args.fusion),
                "final_srt_sha256": sha256(args.final_srt),
                "final_report_sha256": sha256(args.final_report),
            },
            "editor_witness": {
                "authority": "auxiliary_only",
                "projection_sync": editor_projection,
                "final_sync": editor_final,
            },
            "audio_evidence_policy": audio_sync["policy"],
            "diagnostics_policy": audio_sync["diagnostics_policy"],
            "audio_evidence_tracks": audio_sync["tracks"],
            "projection_sync": projection,
            "final_sync": final,
        }
        atomic_write_json(args.out, payload)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    print(
        json.dumps(
            {
                "passed": payload["passed"],
                "projection_failed_tracks": projection["failed_track_count"],
                "final_failed_tracks": final["failed_track_count"],
                "audio_evidence_policy": payload["audio_evidence_policy"],
                "output": str(args.out),
            },
            ensure_ascii=False,
        )
    )
    return 0 if payload["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
