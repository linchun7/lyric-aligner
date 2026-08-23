#!/usr/bin/env python3
"""Selectively refine one fingerprinted v4 coarse Source-to-Mix alignment."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import librosa

from lyric_aligner import __version__
from lyric_aligner.audio.fine_alignment import refine_coarse_mapping, should_run_fine_alignment
from lyric_aligner.config import calibration_overrides
from lyric_aligner.contracts.artifacts import atomic_write_json, build_artifact_manifest, validate_artifact_output, validate_upstream_artifact
from lyric_aligner.pipeline.context import build_pipeline_context
from task_contract import assert_manifest_paths, load_task_manifest, resolve_manifest_record


MIX_DECODE_PADDING_SECONDS = 2.0


def _load(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _validate_stage(path: Path, artifact_path: Path, *, fingerprint: str, role: str, stage: str):
    payload = _load(path)
    if payload.get("task_fingerprint_sha256") != fingerprint:
        raise ValueError(f"{role} task fingerprint mismatch")
    if payload.get("algorithm_version") != __version__:
        raise ValueError(f"{role} algorithm version mismatch")
    artifact = _load(artifact_path)
    issues = validate_upstream_artifact(
        artifact,
        expected_task_fingerprint=fingerprint,
        expected_algorithm_version=__version__,
        expected_stage=stage,
    )
    issues.extend(validate_artifact_output(artifact, role=role, path=path))
    if issues:
        raise ValueError(f"invalid {role} artifact: " + "; ".join(issues))
    return payload, artifact


def _mix_window_bounds(coarse: dict) -> tuple[float, float]:
    windows = coarse.get("result", {}).get("windows", [])
    if not isinstance(windows, list) or not windows:
        raise ValueError("fine alignment requires coarse retrieval windows")
    try:
        start = min(float(window["mix_start"]) for window in windows)
        end = max(float(window["mix_end"]) for window in windows)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("coarse retrieval windows have invalid mix bounds") from exc
    if start < 0 or end <= start:
        raise ValueError("coarse retrieval windows have invalid mix interval")
    return start, end


def _load_bounded_mix(
    path: Path,
    *,
    sr: int,
    mix_start: float,
    mix_end: float,
    full_mix_duration: float,
) -> tuple[object, float]:
    decode_start = max(0.0, mix_start - MIX_DECODE_PADDING_SECONDS)
    decode_end = min(full_mix_duration, mix_end + MIX_DECODE_PADDING_SECONDS)
    audio, _ = librosa.load(
        path,
        sr=sr,
        mono=True,
        offset=decode_start,
        duration=max(0.0, decode_end - decode_start),
    )
    required_samples = int(math.ceil((mix_end - decode_start) * sr))
    if len(audio) + 1 < required_samples:
        raise ValueError("bounded mix decode ended before requested fine interval")
    return audio, decode_start


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--mix-audio", required=True, type=Path)
    parser.add_argument("--track-assets", required=True, type=Path)
    parser.add_argument("--asset-artifact", required=True, type=Path)
    parser.add_argument("--coarse", required=True, type=Path)
    parser.add_argument("--coarse-artifact", required=True, type=Path)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--sr", type=int)
    parser.add_argument("--hop-length", type=int)
    parser.add_argument("--source-radius-seconds", type=float)
    parser.add_argument("--slope-radius", type=float)
    parser.add_argument("--slope-step", type=float)
    parser.add_argument("--candidate-step-seconds", type=float)
    parser.add_argument("--min-score", type=float, help="Experimental override; release is blocked until moved into the asset profile.")
    parser.add_argument("--min-margin", type=float, help="Experimental override; release is blocked until moved into the asset profile.")
    parser.add_argument("--bpm-prior", type=float)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--artifact-out", required=True, type=Path)
    parser.add_argument("--git-commit", default="")
    args = parser.parse_args()

    try:
        task = load_task_manifest(args.task_manifest)
        assert_manifest_paths(args.task_manifest, task, {"audio": args.mix_audio})
        fingerprint = str(task["task_fingerprint_sha256"])
        track_assets, asset_artifact = _validate_stage(
            args.track_assets, args.asset_artifact, fingerprint=fingerprint, role="track_assets", stage="asset_resolution"
        )
        context = build_pipeline_context(
            expected_task_fingerprint=fingerprint,
            track_assets_payload=track_assets,
            asset_artifact=asset_artifact,
            verify_asset_files=True,
        )
        defaults = context.profile.fine
        timewarp_defaults = context.profile.timewarp
        sr = defaults.sr if args.sr is None else args.sr
        hop_length = defaults.hop_length if args.hop_length is None else args.hop_length
        source_radius_seconds = defaults.source_radius_seconds if args.source_radius_seconds is None else args.source_radius_seconds
        slope_radius = defaults.slope_radius if args.slope_radius is None else args.slope_radius
        slope_step = defaults.slope_step if args.slope_step is None else args.slope_step
        candidate_step_seconds = defaults.candidate_step_seconds if args.candidate_step_seconds is None else args.candidate_step_seconds
        min_score = defaults.min_score if args.min_score is None else args.min_score
        min_margin = defaults.min_margin if args.min_margin is None else args.min_margin
        overrides = calibration_overrides(defaults, {
            "sr": sr,
            "hop_length": hop_length,
            "source_radius_seconds": source_radius_seconds,
            "slope_radius": slope_radius,
            "slope_step": slope_step,
            "candidate_step_seconds": candidate_step_seconds,
            "min_score": min_score,
            "min_margin": min_margin,
        })
        coarse, coarse_artifact = _validate_stage(
            args.coarse, args.coarse_artifact, fingerprint=fingerprint, role="coarse_alignment", stage="coarse_audio_alignment"
        )
        if str(coarse.get("upstream_asset_artifact_id")) != context.asset_artifact.artifact_id:
            raise ValueError("coarse alignment came from a different asset artifact")
        if str(coarse.get("calibration_profile_id")) != context.calibration_profile_id:
            raise ValueError("coarse alignment came from a different calibration profile")
        occurrence_id = str(coarse["occurrence_id"])
        binding = context.binding_by_occurrence_id.get(occurrence_id)
        if binding is None:
            raise ValueError("coarse occurrence missing from track_assets")
        if str(coarse.get("track_id")) != binding.track_id:
            raise ValueError("coarse track_id differs from resolved TrackAsset")
        if str(coarse.get("canonical_selection_sha256")) != binding.canonical_selection_sha256:
            raise ValueError("coarse canonical selection differs from TrackAsset")
        source_path = Path(binding.source_audio_path)
        source_dir_record = task["inputs"].get("source_audio_dir")
        if source_dir_record is not None:
            source_dir = resolve_manifest_record(args.task_manifest, source_dir_record).resolve()
            try:
                source_path.resolve().relative_to(source_dir)
            except ValueError as exc:
                raise ValueError("TrackAsset source audio is outside task source_audio_dir") from exc

        needs_fine = should_run_fine_alignment(coarse, force=args.force)
        mix_duration = float(librosa.get_duration(path=str(args.mix_audio)))
        if needs_fine:
            mix_start, mix_end = _mix_window_bounds(coarse)
            if mix_end > mix_duration + 1e-6:
                raise ValueError("coarse retrieval windows exceed mix duration")
            mix_audio, mix_audio_start = _load_bounded_mix(
                args.mix_audio,
                sr=sr,
                mix_start=mix_start,
                mix_end=mix_end,
                full_mix_duration=mix_duration,
            )
            source_audio, _ = librosa.load(source_path, sr=sr, mono=True)
        else:
            mix_audio = []
            source_audio = []
            mix_audio_start = 0.0

        fine = refine_coarse_mapping(
            mix_audio,
            source_audio,
            coarse,
            sr=sr,
            mix_audio_start=mix_audio_start,
            full_mix_duration=mix_duration,
            force=args.force,
            hop_length=hop_length,
            source_radius_seconds=source_radius_seconds,
            slope_radius=slope_radius,
            slope_step=slope_step,
            candidate_step_seconds=candidate_step_seconds,
            min_score=min_score,
            min_margin=min_margin,
            bpm_prior=args.bpm_prior,
            middle_cut=binding.middle_cut,
            bpm_prior_strength=timewarp_defaults.bpm_prior_strength,
            max_continuous_rate=timewarp_defaults.max_continuous_rate,
            min_excess_source_jump=timewarp_defaults.min_excess_source_jump,
            min_piecewise_improvement=timewarp_defaults.min_piecewise_improvement,
            minimum_feature_families=timewarp_defaults.minimum_feature_families,
            drift_threshold=timewarp_defaults.drift_threshold,
            residual_threshold=timewarp_defaults.residual_threshold,
            complexity_penalty=timewarp_defaults.complexity_penalty,
        )
        payload = {
            "schema_version": "1.1",
            "algorithm_version": __version__,
            "task_fingerprint_sha256": fingerprint,
            "calibration_profile_version": context.calibration_profile_version,
            "calibration_profile_id": context.calibration_profile_id,
            "calibration_overrides": overrides,
            "occurrence_id": occurrence_id,
            "track_id": binding.track_id,
            "canonical_selection_sha256": binding.canonical_selection_sha256,
            "mix_audio_sha256": str(task["inputs"]["audio"]["sha256"]),
            "source_audio_sha256": binding.source_audio_sha256,
            "upstream_asset_artifact_id": context.asset_artifact.artifact_id,
            "upstream_coarse_artifact_id": coarse_artifact["artifact_id"],
            "result": fine,
        }
        atomic_write_json(args.out, payload)
        artifact = build_artifact_manifest(
            task_fingerprint_sha256=fingerprint,
            stage="fine_audio_alignment",
            algorithm_version=__version__,
            outputs=(("fine_alignment", args.out),),
            normalized_config={
                **context.artifact_config(),
                "calibration_overrides": overrides,
                "force": args.force,
                "sr": sr,
                "hop_length": hop_length,
                "source_radius_seconds": source_radius_seconds,
                "slope_radius": slope_radius,
                "slope_step": slope_step,
                "candidate_step_seconds": candidate_step_seconds,
                "min_score": min_score,
                "min_margin": min_margin,
                "timewarp": timewarp_defaults.__dict__,
                "bpm_prior": args.bpm_prior,
            },
            producer={"git_commit": args.git_commit} if args.git_commit else {},
            upstream_artifact_ids=(context.asset_artifact.artifact_id, str(coarse_artifact["artifact_id"])),
            evidence={
                "occurrence_id": occurrence_id,
                "track_id": binding.track_id,
                "applied": fine["applied"],
                "status": fine["status"],
                "blocked": bool(fine.get("timewarp", {}).get("blocked", False)),
            },
        )
        atomic_write_json(args.artifact_out, artifact)
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    print(json.dumps({
        "occurrence_id": occurrence_id,
        "applied": fine["applied"],
        "status": fine["status"],
        "calibration_profile_id": context.calibration_profile_id,
        "calibration_override": bool(overrides),
        "artifact_id": artifact["artifact_id"],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
