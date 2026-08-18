#!/usr/bin/env python3
"""Project P7 source forced-alignment evidence into edited-mix time."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner import __version__
from lyric_aligner.alignment.forced_projection import (
    ForcedMixProjectionError,
    project_forced_alignment_to_mix,
)
from lyric_aligner.contracts.artifacts import (
    atomic_write_json,
    build_artifact_manifest,
    validate_artifact_output,
    validate_upstream_artifact,
)
from lyric_aligner.timeline.projector import effective_timewarp
from task_contract import load_task_manifest, verify_manifest_inputs


_RUN_ROLES = {
    "production_orchestration": "v4_production_run",
    "review_resolution": "v4_reviewed_run",
    "overlap_recomposition": "v4_recomposed_run",
    "cut_rebuild": "v4_cut_rebuilt_run",
    "combined_recomposition": "v4_combined_run",
}


def _load(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _artifact_id(artifact: dict, *, label: str) -> str:
    value = str(artifact.get("artifact_id") or "").strip()
    if not value:
        raise ValueError(f"{label} artifact_id is missing")
    return value


def _check_artifact(
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


def _mapping_from_occurrence(
    occurrence: dict,
    *,
    fingerprint: str,
    run_upstreams: set[str],
) -> tuple[dict, set[str], str]:
    occurrence_id = str(occurrence.get("occurrence_id") or "").strip()
    if not occurrence_id:
        raise ValueError("source run occurrence has no occurrence_id")

    cut_path_value = str(occurrence.get("cut_mapping_path") or "").strip()
    cut_artifact_value = str(occurrence.get("cut_mapping_artifact_path") or "").strip()
    if cut_path_value or cut_artifact_value:
        if not cut_path_value or not cut_artifact_value:
            raise ValueError(
                f"occurrence {occurrence_id} has incomplete cut mapping provenance"
            )
        cut_path = Path(cut_path_value)
        cut_artifact_path = Path(cut_artifact_value)
        cut_payload = _load(cut_path)
        cut_artifact = _load(cut_artifact_path)
        _check_artifact(
            cut_artifact,
            fingerprint=fingerprint,
            stage="cut_timewarp_rebuild",
            role="cut_aware_timewarp",
            output=cut_path,
        )
        cut_id = _artifact_id(cut_artifact, label="cut-aware mapping")
        if cut_id not in run_upstreams:
            raise ValueError(
                f"cut-aware mapping for occurrence {occurrence_id} is not source-run upstream"
            )
        if str(cut_payload.get("occurrence_id") or "") != occurrence_id:
            raise ValueError("cut-aware mapping occurrence identity mismatch")
        mapping = cut_payload.get("result")
        if not isinstance(mapping, dict) or mapping.get("kind") != "CUT_AWARE":
            raise ValueError("cut-aware mapping payload has no CUT_AWARE result")
        return mapping, {cut_id}, "cut_aware"

    coarse_value = str(occurrence.get("coarse_path") or "").strip()
    coarse_artifact_value = str(occurrence.get("coarse_artifact_path") or "").strip()
    if not coarse_value or not coarse_artifact_value:
        raise ValueError(
            f"occurrence {occurrence_id} has no continuous Source-to-Mix provenance"
        )
    coarse_path = Path(coarse_value)
    coarse_artifact_path = Path(coarse_artifact_value)
    coarse = _load(coarse_path)
    coarse_artifact = _load(coarse_artifact_path)
    _check_artifact(
        coarse_artifact,
        fingerprint=fingerprint,
        stage="coarse_audio_alignment",
        role="coarse_alignment",
        output=coarse_path,
    )
    coarse_id = _artifact_id(coarse_artifact, label="coarse alignment")
    if coarse_id not in run_upstreams:
        raise ValueError(
            f"coarse alignment for occurrence {occurrence_id} is not source-run upstream"
        )
    if str(coarse.get("occurrence_id") or "") != occurrence_id:
        raise ValueError("coarse alignment occurrence identity mismatch")

    fine = None
    mapping_ids = {coarse_id}
    fine_value = str(occurrence.get("fine_path") or "").strip()
    fine_artifact_value = str(occurrence.get("fine_artifact_path") or "").strip()
    if fine_value or fine_artifact_value:
        if not fine_value or not fine_artifact_value:
            raise ValueError(
                f"occurrence {occurrence_id} has incomplete fine alignment provenance"
            )
        fine_path = Path(fine_value)
        fine_artifact_path = Path(fine_artifact_value)
        fine = _load(fine_path)
        fine_artifact = _load(fine_artifact_path)
        _check_artifact(
            fine_artifact,
            fingerprint=fingerprint,
            stage="fine_audio_alignment",
            role="fine_alignment",
            output=fine_path,
        )
        fine_id = _artifact_id(fine_artifact, label="fine alignment")
        if fine_id not in run_upstreams:
            raise ValueError(
                f"fine alignment for occurrence {occurrence_id} is not source-run upstream"
            )
        if str(fine.get("occurrence_id") or "") != occurrence_id:
            raise ValueError("fine alignment occurrence identity mismatch")
        mapping_ids.add(fine_id)

    mapping, blocked, source = effective_timewarp(coarse, fine)
    if blocked:
        raise ValueError(
            f"effective Source-to-Mix mapping is blocked for occurrence {occurrence_id}"
        )
    return mapping, mapping_ids, source


def _needed_occurrence_ids(forced: dict) -> set[str]:
    jobs = forced.get("jobs")
    if not isinstance(jobs, list):
        raise ValueError("forced-alignment evidence jobs must be a list")
    needed: set[str] = set()
    for job in jobs:
        if not isinstance(job, dict):
            raise ValueError("forced-alignment evidence job must be an object")
        occurrence_id = str(job.get("occurrence_id") or "").strip()
        if not occurrence_id:
            raise ValueError("forced-alignment evidence job has no occurrence_id")
        needed.add(occurrence_id)
    return needed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--forced-evidence", required=True, type=Path)
    parser.add_argument("--forced-evidence-artifact", required=True, type=Path)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--run-artifact", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--artifact-out", required=True, type=Path)
    parser.add_argument("--git-commit", default="")
    args = parser.parse_args()

    try:
        task = load_task_manifest(args.task_manifest)
        input_issues = verify_manifest_inputs(args.task_manifest, task)
        if input_issues:
            raise ValueError("task manifest validation failed: " + "; ".join(input_issues))
        fingerprint = str(task["task_fingerprint_sha256"])

        run = _load(args.run)
        run_artifact = _load(args.run_artifact)
        run_stage = str(run_artifact.get("stage") or "")
        run_role = _RUN_ROLES.get(run_stage)
        if run_role is None:
            raise ValueError("unsupported source run stage")
        _check_artifact(
            run_artifact,
            fingerprint=fingerprint,
            stage=run_stage,
            role=run_role,
            output=args.run,
        )
        run_artifact_id = _artifact_id(run_artifact, label="source run")
        if run.get("task_fingerprint_sha256") != fingerprint:
            raise ValueError("source run belongs to another task")
        if run.get("algorithm_version") != __version__:
            raise ValueError("source run algorithm version mismatch")
        run_upstreams = {
            str(value) for value in run_artifact.get("upstream_artifact_ids", [])
        }

        forced = _load(args.forced_evidence)
        forced_artifact = _load(args.forced_evidence_artifact)
        _check_artifact(
            forced_artifact,
            fingerprint=fingerprint,
            stage="source_forced_alignment_evidence",
            role="forced_alignment_evidence",
            output=args.forced_evidence,
        )
        forced_artifact_id = _artifact_id(
            forced_artifact, label="forced-alignment evidence"
        )
        if forced.get("task_fingerprint_sha256") != fingerprint:
            raise ValueError("forced-alignment evidence belongs to another task")
        if forced.get("algorithm_version") != __version__:
            raise ValueError("forced-alignment evidence algorithm version mismatch")
        if forced.get("source_run_artifact_id") != run_artifact_id:
            raise ValueError("forced-alignment evidence belongs to another source run")
        forced_upstreams = {
            str(value) for value in forced_artifact.get("upstream_artifact_ids", [])
        }
        if run_artifact_id not in forced_upstreams:
            raise ValueError("forced-alignment artifact does not bind source run")

        occurrences = run.get("occurrences")
        if not isinstance(occurrences, list):
            raise ValueError("source run occurrences must be a list")
        occurrence_by_id: dict[str, dict] = {}
        for occurrence in occurrences:
            if not isinstance(occurrence, dict):
                raise ValueError("source run occurrence must be an object")
            occurrence_id = str(occurrence.get("occurrence_id") or "").strip()
            if not occurrence_id or occurrence_id in occurrence_by_id:
                raise ValueError(
                    "source run occurrence IDs must be unique/non-empty"
                )
            occurrence_by_id[occurrence_id] = occurrence

        needed_occurrence_ids = _needed_occurrence_ids(forced)
        missing_occurrences = sorted(
            needed_occurrence_ids - set(occurrence_by_id)
        )
        if missing_occurrences:
            raise ValueError(
                "forced-alignment evidence references occurrence absent from source run: "
                + ", ".join(missing_occurrences)
            )

        mappings: dict[str, dict] = {}
        mapping_sources: dict[str, str] = {}
        mapping_artifact_ids: set[str] = set()
        for occurrence_id in sorted(needed_occurrence_ids):
            mapping, ids, source = _mapping_from_occurrence(
                occurrence_by_id[occurrence_id],
                fingerprint=fingerprint,
                run_upstreams=run_upstreams,
            )
            mappings[occurrence_id] = mapping
            mapping_sources[occurrence_id] = source
            mapping_artifact_ids.update(ids)

        projected = project_forced_alignment_to_mix(
            forced_evidence=forced,
            mappings_by_occurrence=mappings,
        )
        projected.update(
            {
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "source_run_artifact_id": run_artifact_id,
                "source_forced_alignment_artifact_id": forced_artifact_id,
                "mapping_sources_by_occurrence": dict(sorted(mapping_sources.items())),
            }
        )
        atomic_write_json(args.out, projected)

        artifact = build_artifact_manifest(
            task_fingerprint_sha256=fingerprint,
            stage="forced_alignment_mix_projection",
            algorithm_version=__version__,
            outputs=(("forced_alignment_mix_evidence", args.out),),
            normalized_config={
                "source_run_artifact_id": run_artifact_id,
                "source_forced_alignment_artifact_id": forced_artifact_id,
                "mapping_sources_by_occurrence": dict(sorted(mapping_sources.items())),
                "cut_aware_cross_gap_policy": "unprojectable_not_bridged",
                "mapping_scope": "forced_evidence_occurrences_only",
            },
            producer={"git_commit": args.git_commit} if args.git_commit else {},
            upstream_artifact_ids=tuple(
                sorted(
                    {
                        run_artifact_id,
                        forced_artifact_id,
                        *mapping_artifact_ids,
                    }
                )
            ),
            evidence={
                "mode": "forced_alignment_mix_projection",
                "projected_line_count": projected["projected_line_count"],
                "unprojectable_line_count": projected["unprojectable_line_count"],
                "projected_span_count": projected["projected_span_count"],
                "unprojectable_span_count": projected["unprojectable_span_count"],
                "primary_timing_authority_unchanged": True,
                "forced_alignment_authority": "auxiliary_acoustic_evidence_only",
                "mapping_scope": "forced_evidence_occurrences_only",
            },
        )
        atomic_write_json(args.artifact_out, artifact)
    except (
        OSError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        ForcedMixProjectionError,
    ) as exc:
        parser.error(str(exc))

    print(
        json.dumps(
            {
                "status": "forced_alignment_mix_projection",
                "jobs": projected["job_count"],
                "projected_lines": projected["projected_line_count"],
                "unprojectable_lines": projected["unprojectable_line_count"],
                "artifact_id": artifact["artifact_id"],
                "out": str(args.out),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
