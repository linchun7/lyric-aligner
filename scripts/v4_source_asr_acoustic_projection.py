#!/usr/bin/env python3
"""Build and execute shadow source-ASR -> final-mix acoustic onset evidence."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.alignment.local_acoustic_match import LocalAcousticMatchConfig
from lyric_aligner.alignment.local_acoustic_v11 import execute_region_source_match_jobs
from lyric_aligner.alignment.source_asr_acoustic_projection import (
    bind_source_asr_acoustic_results,
    build_source_asr_acoustic_plan,
    evaluate_source_asr_acoustic_shadow,
)
from lyric_aligner.alignment.source_observer import SourceObservationConfig, observe_source
from lyric_aligner.contracts.artifacts import atomic_write_json, sha256_file
from lyric_aligner.io.path_safety import validate_separate_artifact_paths, validate_artifact_output_tree
from lyric_aligner.qa.final_integrity import read_audit_rows
from task_contract import load_task_manifest, resolve_manifest_record, verify_manifest_inputs


def _load(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _timeline_path(value: object) -> Path:
    path = Path(str(value or ""))
    if not str(path):
        raise ValueError("run occurrence lacks timeline_path")
    return path if path.is_absolute() else REPOSITORY_ROOT / path


def _validate_cache_artifact_separation(cache_dir: Path, artifact_outputs: dict[str, Path]) -> None:
    validate_artifact_output_tree(inputs=artifact_outputs, output_dir=cache_dir)
    cache = cache_dir.resolve()
    for path in artifact_outputs.values():
        if cache.is_relative_to(path.resolve()):
            raise ValueError("artifact path must not contain the cache directory")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--alignment-plan", required=True, type=Path)
    parser.add_argument("--selection-lock", required=True, type=Path)
    parser.add_argument("--track-assets", required=True, type=Path)
    parser.add_argument("--fusion", required=True, type=Path)
    parser.add_argument("--final-report", required=True, type=Path)
    parser.add_argument("--model-path", required=True, type=Path)
    parser.add_argument("--cache-dir", required=True, type=Path)
    parser.add_argument("--observations-out", required=True, type=Path)
    parser.add_argument("--plan-out", required=True, type=Path)
    parser.add_argument("--acoustic-raw-out", required=True, type=Path)
    parser.add_argument("--evidence-out", required=True, type=Path)
    parser.add_argument("--evaluation-out", required=True, type=Path)
    parser.add_argument("--source-language", default=None)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--beam-size", type=int, default=5)
    args = parser.parse_args()

    try:
        task = load_task_manifest(args.task_manifest)
        issues = verify_manifest_inputs(args.task_manifest, task)
        if issues:
            raise ValueError("task manifest validation failed: " + "; ".join(issues))
        fingerprint = str(task["task_fingerprint_sha256"])
        mix_audio = resolve_manifest_record(args.task_manifest, task["inputs"]["audio"])
        mix_sha = sha256_file(mix_audio)

        run = _load(args.run)
        alignment_plan = _load(args.alignment_plan)
        selection_lock = _load(args.selection_lock)
        track_assets = _load(args.track_assets)
        fusion = _load(args.fusion)
        final_rows = read_audit_rows(args.final_report)
        if run.get("task_fingerprint_sha256") != fingerprint:
            raise ValueError("run belongs to another task")
        if alignment_plan.get("task_fingerprint_sha256") != fingerprint:
            raise ValueError("alignment plan belongs to another task")
        if selection_lock.get("task_fingerprint_sha256") != fingerprint:
            raise ValueError("selection lock belongs to another task")
        if fusion.get("task_fingerprint_sha256") != fingerprint:
            raise ValueError("fusion belongs to another task")

        artifact_outputs = {
            "observations_out": args.observations_out,
            "plan_out": args.plan_out,
            "acoustic_raw_out": args.acoustic_raw_out,
            "evidence_out": args.evidence_out,
            "evaluation_out": args.evaluation_out,
        }
        for path in artifact_outputs.values():
            if path.exists():
                raise ValueError(f"output already exists; use a fresh path: {path}")
        if args.cache_dir.exists() and not args.cache_dir.is_dir():
            raise ValueError(f"cache path exists but is not a directory: {args.cache_dir}")
        outputs = {"cache_dir": args.cache_dir, **artifact_outputs}
        _validate_cache_artifact_separation(args.cache_dir, artifact_outputs)
        validate_separate_artifact_paths(
            inputs={
                "task_manifest": args.task_manifest,
                "run": args.run,
                "alignment_plan": args.alignment_plan,
                "selection_lock": args.selection_lock,
                "track_assets": args.track_assets,
                "fusion": args.fusion,
                "final_report": args.final_report,
                "model_path": args.model_path,
                "mix_audio": mix_audio,
            },
            outputs=outputs,
        )
        if not args.model_path.is_dir() or not (args.model_path / "model.bin").is_file():
            raise ValueError("model path must be an existing local faster-whisper snapshot with model.bin")

        selected_ordinals = [int(value) for value in selection_lock.get("selected_ordinals", [])]
        assets = track_assets.get("assets")
        occurrences = track_assets.get("occurrences")
        if not isinstance(assets, list) or not isinstance(occurrences, list):
            raise ValueError("track assets payload is invalid")
        occurrence_by_ordinal = {
            int(row["ordinal"]): row
            for row in occurrences
            if isinstance(row, dict) and row.get("ordinal") is not None
        }
        asset_by_track = {
            str(row.get("track_id") or ""): row for row in assets if isinstance(row, dict)
        }

        config = SourceObservationConfig(
            model_path=str(args.model_path.resolve()),
            device=args.device,
            compute_type=args.compute_type,
            beam_size=args.beam_size,
            language=(None if not str(args.source_language or "").strip() else str(args.source_language).strip()),
        )
        observations: dict[int, dict] = {}
        source_paths: dict[int, Path] = {}
        observation_rows: list[dict] = []
        for ordinal in selected_ordinals:
            occurrence = occurrence_by_ordinal.get(ordinal)
            if occurrence is None:
                raise ValueError(f"selected ordinal {ordinal} has no occurrence asset binding")
            track_id = str(occurrence.get("track_id") or "")
            asset = asset_by_track.get(track_id)
            if asset is None:
                raise ValueError(f"selected ordinal {ordinal} has no track asset")
            source_path = Path(str(asset.get("source_audio_path") or ""))
            if not source_path.is_absolute():
                source_path = REPOSITORY_ROOT / source_path
            source_sha = str(asset.get("source_audio_sha256") or "")
            if not source_path.is_file() or sha256_file(source_path) != source_sha:
                raise ValueError(f"selected ordinal {ordinal} source audio binding mismatch")
            observed = observe_source(
                audio_path=source_path,
                audio_sha256=source_sha,
                config=config,
                cache_dir=args.cache_dir,
            )
            if observed.get("status") != "observed":
                raise ValueError(
                    f"selected ordinal {ordinal} source observation unavailable: {observed.get('reason')}"
                )
            observations[ordinal] = observed
            source_paths[ordinal - 1] = source_path
            observation_rows.append(
                {
                    "ordinal": ordinal,
                    "occurrence_id": str(occurrence.get("occurrence_id") or ""),
                    "track_id": track_id,
                    "source_audio_path": str(source_path),
                    "source_audio_sha256": source_sha,
                    "cache_key_sha256": observed.get("cache_key_sha256"),
                    "artifact_sha256": observed.get("artifact_sha256"),
                    "cache_hit": observed.get("cache_hit"),
                    "search_domain": observed.get("search_domain"),
                    "detected_language": observed.get("detected_language"),
                    "word_count": len(observed.get("words") or []),
                    "observation": observed,
                }
            )

        timelines = {
            int(row["ordinal"]): _load(_timeline_path(row.get("timeline_path")))
            for row in run.get("occurrences", [])
            if isinstance(row, dict) and row.get("ordinal") is not None
            and int(row["ordinal"]) in selected_ordinals
        }
        source_plan = build_source_asr_acoustic_plan(
            task_fingerprint_sha256=fingerprint,
            selection_lock=selection_lock,
            alignment_plan=alignment_plan,
            track_assets=track_assets,
            timelines_by_ordinal=timelines,
            source_observations_by_ordinal=observations,
            mix_audio_sha256=mix_sha,
        )
        observations_payload = {
            "schema_version": "source-observation-batch-1.0",
            "task_fingerprint_sha256": fingerprint,
            "selection_lock_sha256": sha256_file(args.selection_lock),
            "track_assets_sha256": sha256_file(args.track_assets),
            "model_path": str(args.model_path.resolve()),
            "source_observation_config": asdict(config),
            "automatic_timing_change_allowed": False,
            "tracks": observation_rows,
        }
        atomic_write_json(args.observations_out, observations_payload)
        source_plan["bindings"] = {
            "selection_lock_sha256": sha256_file(args.selection_lock),
            "alignment_plan_sha256": sha256_file(args.alignment_plan),
            "track_assets_sha256": sha256_file(args.track_assets),
            "run_sha256": sha256_file(args.run),
            "mix_audio_sha256": mix_sha,
            "observations_sha256": sha256_file(args.observations_out),
        }
        atomic_write_json(args.plan_out, source_plan)

        acoustic = execute_region_source_match_jobs(
            mix_audio_path=mix_audio,
            plan=source_plan,
            source_audio_by_source_ordinal=source_paths,
            config=LocalAcousticMatchConfig(),
        )
        acoustic["task_fingerprint_sha256"] = fingerprint
        acoustic["source_plan_sha256"] = sha256_file(args.plan_out)
        acoustic["mix_audio_sha256"] = mix_sha
        acoustic["automatic_timing_change_allowed"] = False
        atomic_write_json(args.acoustic_raw_out, acoustic)

        evidence = bind_source_asr_acoustic_results(plan=source_plan, acoustic_result=acoustic)
        evidence["bindings"] = {
            "source_plan_sha256": sha256_file(args.plan_out),
            "source_observations_sha256": sha256_file(args.observations_out),
            "acoustic_raw_sha256": sha256_file(args.acoustic_raw_out),
            "selection_lock_sha256": sha256_file(args.selection_lock),
            "alignment_plan_sha256": sha256_file(args.alignment_plan),
            "track_assets_sha256": sha256_file(args.track_assets),
            "run_sha256": sha256_file(args.run),
            "mix_audio_sha256": mix_sha,
        }
        atomic_write_json(args.evidence_out, evidence)

        evaluation = evaluate_source_asr_acoustic_shadow(
            evidence=evidence,
            fusion=fusion,
            final_rows=final_rows,
        )
        evaluation["task_fingerprint_sha256"] = fingerprint
        evaluation["bindings"] = {
            "evidence_sha256": sha256_file(args.evidence_out),
            "fusion_sha256": sha256_file(args.fusion),
            "final_report_sha256": sha256_file(args.final_report),
            "selection_lock_sha256": sha256_file(args.selection_lock),
            "run_sha256": sha256_file(args.run),
            "mix_audio_sha256": mix_sha,
        }
        atomic_write_json(args.evaluation_out, evaluation)
    except (OSError, KeyError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    print(
        json.dumps(
            {
                "source_observation_track_count": len(observation_rows),
                "frozen_selection_job_count": source_plan["frozen_selection_job_count"],
                "accepted_source_onset_count": source_plan["accepted_source_onset_count"],
                "projected_job_count": evidence["job_count"],
                "eligible_projection_count": evidence["eligible_job_count"],
                "evidence_out": str(args.evidence_out),
                "evaluation_eligible_job_count": evaluation["eligible_job_count"],
                "evaluation_out": str(args.evaluation_out),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
