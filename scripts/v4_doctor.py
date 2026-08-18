#!/usr/bin/env python3
"""Inspect v4 task/data/evidence/backend readiness without changing artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.doctor import DoctorError, build_doctor_report


_REQUIRE_CHOICES = (
    "task",
    "run",
    "editor",
    "alignment_plan",
    "asr",
    "forced_source",
    "forced_mix",
    "fusion",
    "runtime_snapshot",
    "lineage",
    "artifact:run",
    "artifact:editor",
    "artifact:alignment_plan",
    "artifact:asr",
    "artifact:forced_source",
    "artifact:forced_mix",
    "artifact:fusion",
    "dataset:metadata",
    "dataset:references",
    "dataset:predictions",
    "dataset:evaluation",
    "backend:mix_asr",
    "backend:word_timestamps",
    "backend:ctc_alignment",
    "backend:source_forced_alignment",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-manifest", type=Path)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--dataset-split", choices=("train", "calibration", "blind_test"))
    parser.add_argument("--run", type=Path)
    parser.add_argument("--run-artifact", type=Path)
    parser.add_argument("--editor-evidence", type=Path)
    parser.add_argument("--editor-evidence-artifact", type=Path)
    parser.add_argument("--alignment-plan", type=Path)
    parser.add_argument("--alignment-plan-artifact", type=Path)
    parser.add_argument("--asr-evidence", type=Path)
    parser.add_argument("--asr-evidence-artifact", type=Path)
    parser.add_argument("--forced-evidence", type=Path)
    parser.add_argument("--forced-evidence-artifact", type=Path)
    parser.add_argument("--forced-mix-evidence", type=Path)
    parser.add_argument("--forced-mix-evidence-artifact", type=Path)
    parser.add_argument("--fusion", type=Path)
    parser.add_argument("--fusion-artifact", type=Path)
    parser.add_argument("--runtime-snapshot", type=Path)
    parser.add_argument("--no-backend-check", action="store_true")
    parser.add_argument("--faster-whisper-model-id")
    parser.add_argument("--whisperx-model-id")
    parser.add_argument("--whisperx-align-model-id")
    parser.add_argument("--external-forced-aligner-command")
    parser.add_argument("--require", action="append", choices=_REQUIRE_CHOICES, default=[])
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    try:
        report = build_doctor_report(
            task_manifest=args.task_manifest,
            dataset=args.dataset,
            dataset_split=args.dataset_split,
            run=args.run,
            run_artifact=args.run_artifact,
            editor_evidence=args.editor_evidence,
            editor_evidence_artifact=args.editor_evidence_artifact,
            alignment_plan=args.alignment_plan,
            alignment_plan_artifact=args.alignment_plan_artifact,
            asr_evidence=args.asr_evidence,
            asr_evidence_artifact=args.asr_evidence_artifact,
            forced_evidence=args.forced_evidence,
            forced_evidence_artifact=args.forced_evidence_artifact,
            forced_mix_evidence=args.forced_mix_evidence,
            forced_mix_evidence_artifact=args.forced_mix_evidence_artifact,
            fusion=args.fusion,
            fusion_artifact=args.fusion_artifact,
            runtime_snapshot=args.runtime_snapshot,
            inspect_backend_status=not args.no_backend_check,
            faster_whisper_model_id=args.faster_whisper_model_id,
            whisperx_model_id=args.whisperx_model_id,
            whisperx_align_model_id=args.whisperx_align_model_id,
            external_forced_aligner_command=args.external_forced_aligner_command,
            requirements=args.require,
        )
    except (OSError, ValueError, KeyError, json.JSONDecodeError, DoctorError) as exc:
        parser.error(str(exc))

    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if report["requirements"]["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
