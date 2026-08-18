#!/usr/bin/env python3
"""Fuse source/editor/ASR/forced evidence into uncalibrated shadow support states."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner import __version__
from lyric_aligner.contracts.artifacts import (
    atomic_write_json,
    build_artifact_manifest,
    validate_artifact_output,
    validate_upstream_artifact,
)
from lyric_aligner.evidence.fusion import (
    FUSION_POLICY_ID,
    EvidenceFusionConfig,
    EvidenceFusionError,
    build_evidence_fusion,
)
from task_contract import load_task_manifest, verify_manifest_inputs


_RUN_ROLES = {
    "production_orchestration": "v4_production_run",
    "review_resolution": "v4_reviewed_run",
    "overlap_recomposition": "v4_recomposed_run",
    "cut_rebuild": "v4_cut_rebuilt_run",
    "combined_recomposition": "v4_combined_run",
}
_TIMELINE_STAGES = {
    "canonical_timeline_projection",
    "overlap_timeline_recomposition",
    "cut_timeline_rebuild",
    "combined_timeline_recomposition",
}


def _load(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _validate_artifact(
    artifact: dict,
    *,
    fingerprint: str,
    stage: str,
    role: str,
    output: Path,
) -> None:
    issues = validate_upstream_artifact(
        artifact,
        expected_task_fingerprint=fingerprint,
        expected_algorithm_version=__version__,
        expected_stage=stage,
    )
    issues.extend(validate_artifact_output(artifact, role=role, path=output))
    if issues:
        raise ValueError(f"invalid {stage} artifact: " + "; ".join(issues))


def _load_timelines(
    run: dict,
    run_artifact: dict,
    *,
    fingerprint: str,
) -> tuple[list[dict], list[str]]:
    occurrences = run.get("occurrences")
    if not isinstance(occurrences, list) or not occurrences:
        raise ValueError("source run has no occurrences")
    run_upstreams = {
        str(value) for value in run_artifact.get("upstream_artifact_ids", [])
    }
    timelines: list[dict] = []
    ids: list[str] = []
    for occurrence in occurrences:
        if not isinstance(occurrence, dict):
            raise ValueError("source run occurrence must be an object")
        occurrence_id = str(occurrence.get("occurrence_id") or "")
        timeline_value = str(occurrence.get("timeline_path") or "").strip()
        artifact_value = str(occurrence.get("timeline_artifact_path") or "").strip()
        if not occurrence_id or not timeline_value or not artifact_value:
            continue
        timeline_path = Path(timeline_value)
        artifact_path = Path(artifact_value)
        timeline = _load(timeline_path)
        timeline_artifact = _load(artifact_path)
        stage = str(timeline_artifact.get("stage") or "")
        if stage not in _TIMELINE_STAGES:
            raise ValueError(f"unsupported canonical timeline stage {stage!r}")
        _validate_artifact(
            timeline_artifact,
            fingerprint=fingerprint,
            stage=stage,
            role="canonical_timeline",
            output=timeline_path,
        )
        artifact_id = str(timeline_artifact.get("artifact_id") or "")
        if artifact_id not in run_upstreams:
            raise ValueError("canonical timeline is not upstream of source run")
        result = timeline.get("result")
        if not isinstance(result, dict):
            raise ValueError("canonical timeline has no result")
        if str(result.get("occurrence_id") or "") != occurrence_id:
            raise ValueError("run/timeline occurrence identity mismatch")
        timelines.append(timeline)
        ids.append(artifact_id)
    if not timelines:
        raise ValueError("no canonical timeline available for fusion")
    return timelines, ids


def _load_aux(
    payload_path: Path | None,
    artifact_path: Path | None,
    *,
    fingerprint: str,
    stage: str,
    role: str,
    source_run_artifact_id: str,
) -> tuple[dict | None, str | None]:
    if payload_path is None and artifact_path is None:
        return None, None
    if payload_path is None or artifact_path is None:
        raise ValueError(f"{stage} payload/artifact must be supplied together")
    payload = _load(payload_path)
    artifact = _load(artifact_path)
    _validate_artifact(
        artifact,
        fingerprint=fingerprint,
        stage=stage,
        role=role,
        output=payload_path,
    )
    if payload.get("source_run_artifact_id") != source_run_artifact_id:
        raise ValueError(f"{stage} belongs to another source run")
    if source_run_artifact_id not in {
        str(value) for value in artifact.get("upstream_artifact_ids", [])
    }:
        raise ValueError(f"{stage} artifact does not bind source run")
    return payload, str(artifact["artifact_id"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--run-artifact", required=True, type=Path)
    parser.add_argument("--editor-evidence", type=Path)
    parser.add_argument("--editor-evidence-artifact", type=Path)
    parser.add_argument("--asr-evidence", type=Path)
    parser.add_argument("--asr-evidence-artifact", type=Path)
    parser.add_argument("--forced-mix-evidence", type=Path)
    parser.add_argument("--forced-mix-evidence-artifact", type=Path)
    parser.add_argument("--conflict-boundary-ms", type=int, default=500)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--artifact-out", required=True, type=Path)
    parser.add_argument("--git-commit", default="")
    args = parser.parse_args()

    try:
        task = load_task_manifest(args.task_manifest)
        issues = verify_manifest_inputs(args.task_manifest, task)
        if issues:
            raise ValueError("task manifest validation failed: " + "; ".join(issues))
        fingerprint = str(task["task_fingerprint_sha256"])

        run = _load(args.run)
        run_artifact = _load(args.run_artifact)
        run_stage = str(run_artifact.get("stage") or "")
        run_role = _RUN_ROLES.get(run_stage)
        if run_role is None:
            raise ValueError("unsupported source run stage")
        _validate_artifact(
            run_artifact,
            fingerprint=fingerprint,
            stage=run_stage,
            role=run_role,
            output=args.run,
        )
        if run.get("task_fingerprint_sha256") != fingerprint:
            raise ValueError("source run belongs to another task")
        if run.get("algorithm_version") != __version__:
            raise ValueError("source run algorithm version mismatch")
        run_artifact_id = str(run_artifact["artifact_id"])
        timelines, timeline_ids = _load_timelines(
            run, run_artifact, fingerprint=fingerprint
        )

        editor, editor_artifact_id = _load_aux(
            args.editor_evidence,
            args.editor_evidence_artifact,
            fingerprint=fingerprint,
            stage="editor_evidence_shadow",
            role="editor_evidence",
            source_run_artifact_id=run_artifact_id,
        )
        asr, asr_artifact_id = _load_aux(
            args.asr_evidence,
            args.asr_evidence_artifact,
            fingerprint=fingerprint,
            stage="asr_evidence_local",
            role="asr_evidence",
            source_run_artifact_id=run_artifact_id,
        )
        forced_mix, forced_mix_artifact_id = _load_aux(
            args.forced_mix_evidence,
            args.forced_mix_evidence_artifact,
            fingerprint=fingerprint,
            stage="forced_alignment_mix_projection",
            role="forced_alignment_mix_evidence",
            source_run_artifact_id=run_artifact_id,
        )
        config = EvidenceFusionConfig(
            conflict_boundary_ms=args.conflict_boundary_ms
        )
        fusion = build_evidence_fusion(
            timeline_payloads=timelines,
            editor_evidence=editor,
            asr_evidence=asr,
            forced_mix_evidence=forced_mix,
            config=config,
        )
        fusion.update(
            {
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "source_run_stage": run_stage,
                "source_run_artifact_id": run_artifact_id,
                "source_editor_evidence_artifact_id": editor_artifact_id,
                "source_asr_evidence_artifact_id": asr_artifact_id,
                "source_forced_mix_evidence_artifact_id": forced_mix_artifact_id,
            }
        )
        atomic_write_json(args.out, fusion)

        upstreams = {run_artifact_id, *timeline_ids}
        if editor_artifact_id:
            upstreams.add(editor_artifact_id)
        if asr_artifact_id:
            upstreams.add(asr_artifact_id)
        if forced_mix_artifact_id:
            upstreams.add(forced_mix_artifact_id)
        artifact = build_artifact_manifest(
            task_fingerprint_sha256=fingerprint,
            stage="evidence_fusion_shadow",
            algorithm_version=__version__,
            outputs=(("evidence_fusion", args.out),),
            normalized_config={
                "policy_id": FUSION_POLICY_ID,
                "policy_calibrated": False,
                "conflict_boundary_ms": args.conflict_boundary_ms,
                "source_run_artifact_id": run_artifact_id,
                "editor_evidence_artifact_id": editor_artifact_id,
                "asr_evidence_artifact_id": asr_artifact_id,
                "forced_mix_evidence_artifact_id": forced_mix_artifact_id,
                "conflict_policy": "any_auxiliary_pair_over_threshold_blocks",
            },
            producer={"git_commit": args.git_commit} if args.git_commit else {},
            upstream_artifact_ids=tuple(sorted(upstreams)),
            evidence={
                "mode": "shadow_only",
                "policy_calibrated": False,
                "release_gate_eligible": False,
                "automatic_timing_change_allowed": False,
                "shadow_level_counts": fusion["summary"]["shadow_level_counts"],
                "forced_alignment_line_counts": fusion["summary"][
                    "forced_alignment_line_counts"
                ],
            },
        )
        atomic_write_json(args.artifact_out, artifact)
    except (
        OSError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        EvidenceFusionError,
    ) as exc:
        parser.error(str(exc))

    print(
        json.dumps(
            {
                "status": "shadow_only",
                "policy_calibrated": False,
                "release_gate_eligible": False,
                "automatic_timing_change_allowed": False,
                "lines": fusion["summary"]["canonical_line_count"],
                "levels": fusion["summary"]["shadow_level_counts"],
                "forced_alignment": fusion["summary"]["forced_alignment_line_counts"],
                "artifact_id": artifact["artifact_id"],
                "out": str(args.out),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
