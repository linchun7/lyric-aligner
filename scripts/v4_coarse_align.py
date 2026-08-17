#!/usr/bin/env python3
"""Run fingerprinted v4 harmonic coarse Source-to-Mix alignment for one occurrence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import librosa

from lyric_aligner import __version__
from lyric_aligner.audio.coarse_mapper import build_coarse_timewarp
from lyric_aligner.config import DEFAULT_V4_PROFILE
from lyric_aligner.contracts.artifacts import (
    atomic_write_json,
    build_artifact_manifest,
    sha256_file,
    validate_artifact_output,
)
from lyric_aligner.pipeline.context import build_pipeline_context
from task_contract import assert_manifest_paths, load_task_manifest, resolve_manifest_record


def _default_interval(bindings, binding, mix_duration: float) -> tuple[float, float]:
    """Return a coarse seed interval, not a final active-track boundary."""

    ordered = sorted(bindings, key=lambda item: item.ordinal)
    position = next(
        index
        for index, item in enumerate(ordered)
        if item.occurrence_id == binding.occurrence_id
    )
    start = float(binding.nominal_start_ms) / 1000.0
    end = (
        float(ordered[position + 1].nominal_start_ms) / 1000.0
        if position + 1 < len(ordered)
        else mix_duration
    )
    return max(0.0, start), min(mix_duration, end)


def main() -> int:
    defaults = DEFAULT_V4_PROFILE.coarse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--mix-audio", required=True, type=Path)
    parser.add_argument("--track-assets", required=True, type=Path)
    parser.add_argument("--asset-artifact", required=True, type=Path)
    parser.add_argument("--occurrence-id", required=True)
    parser.add_argument("--bpm-prior", type=float)
    parser.add_argument("--mix-start", type=float)
    parser.add_argument("--mix-end", type=float)
    parser.add_argument("--sr", type=int, default=defaults.sr)
    parser.add_argument("--hop-length", type=int, default=defaults.hop_length)
    parser.add_argument("--window-seconds", type=float, default=defaults.window_seconds)
    parser.add_argument("--step-seconds", type=float, default=defaults.step_seconds)
    parser.add_argument(
        "--candidate-step-seconds",
        type=float,
        default=defaults.candidate_step_seconds,
    )
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--artifact-out", required=True, type=Path)
    parser.add_argument("--git-commit", default="")
    args = parser.parse_args()

    try:
        task = load_task_manifest(args.task_manifest)
        assert_manifest_paths(args.task_manifest, task, {"audio": args.mix_audio})
        fingerprint = str(task["task_fingerprint_sha256"])

        assets = json.loads(args.track_assets.read_text(encoding="utf-8-sig"))
        asset_artifact = json.loads(
            args.asset_artifact.read_text(encoding="utf-8-sig")
        )
        output_issues = validate_artifact_output(
            asset_artifact,
            role="track_assets",
            path=args.track_assets,
        )
        if output_issues:
            raise ValueError(
                "invalid asset artifact output: " + "; ".join(output_issues)
            )
        context = build_pipeline_context(
            expected_task_fingerprint=fingerprint,
            track_assets_payload=assets,
            asset_artifact=asset_artifact,
            verify_asset_files=True,
        )
        binding = context.binding_by_occurrence_id.get(args.occurrence_id)
        if binding is None:
            raise ValueError(
                f"occurrence_id not found in track assets: {args.occurrence_id}"
            )
        source_path = Path(binding.source_audio_path)

        source_dir_record = task["inputs"].get("source_audio_dir")
        if source_dir_record is not None:
            source_dir = resolve_manifest_record(
                args.task_manifest, source_dir_record
            ).resolve()
            try:
                source_path.resolve().relative_to(source_dir)
            except ValueError as exc:
                raise ValueError(
                    "TrackAsset source audio is outside task source_audio_dir"
                ) from exc

        mix_audio, _ = librosa.load(args.mix_audio, sr=args.sr, mono=True)
        source_audio, _ = librosa.load(source_path, sr=args.sr, mono=True)
        mix_duration = len(mix_audio) / args.sr
        default_start, default_end = _default_interval(
            context.bindings, binding, mix_duration
        )
        mix_start = default_start if args.mix_start is None else args.mix_start
        mix_end = default_end if args.mix_end is None else args.mix_end
        if mix_start < 0 or mix_end > mix_duration or mix_end <= mix_start:
            raise ValueError("invalid occurrence mix interval")

        mapping = build_coarse_timewarp(
            mix_audio,
            source_audio,
            sr=args.sr,
            mix_start=mix_start,
            mix_end=mix_end,
            bpm_prior=args.bpm_prior,
            middle_cut=binding.middle_cut,
            feature_hop_length=args.hop_length,
            window_seconds=args.window_seconds,
            step_seconds=args.step_seconds,
            candidate_step_seconds=args.candidate_step_seconds,
            slope_minimum=defaults.slope_minimum,
            slope_maximum=defaults.slope_maximum,
            slope_step=defaults.slope_step,
            min_score=defaults.min_score,
            min_margin=defaults.min_margin,
        )
        payload = {
            "schema_version": "1.1",
            "algorithm_version": __version__,
            "task_fingerprint_sha256": fingerprint,
            "calibration_profile_version": context.calibration_profile_version,
            "calibration_profile_id": context.calibration_profile_id,
            "occurrence_id": binding.occurrence_id,
            "track_id": binding.track_id,
            "canonical_selection_sha256": binding.canonical_selection_sha256,
            "mix_audio_sha256": sha256_file(args.mix_audio),
            "source_audio_sha256": binding.source_audio_sha256,
            "upstream_asset_artifact_id": context.asset_artifact.artifact_id,
            "result": mapping,
        }
        atomic_write_json(args.out, payload)
        normalized_config = {
            **context.artifact_config(),
            "sr": args.sr,
            "hop_length": args.hop_length,
            "window_seconds": args.window_seconds,
            "step_seconds": args.step_seconds,
            "candidate_step_seconds": args.candidate_step_seconds,
            "slope_minimum": defaults.slope_minimum,
            "slope_maximum": defaults.slope_maximum,
            "slope_step": defaults.slope_step,
            "min_score": defaults.min_score,
            "min_margin": defaults.min_margin,
            "bpm_prior": args.bpm_prior,
            "mix_start": mix_start,
            "mix_end": mix_end,
        }
        artifact = build_artifact_manifest(
            task_fingerprint_sha256=fingerprint,
            stage="coarse_audio_alignment",
            algorithm_version=__version__,
            outputs=(("coarse_alignment", args.out),),
            normalized_config=normalized_config,
            producer={"git_commit": args.git_commit} if args.git_commit else {},
            upstream_artifact_ids=(context.asset_artifact.artifact_id,),
            evidence={
                "occurrence_id": binding.occurrence_id,
                "track_id": binding.track_id,
                "selection": mapping["timewarp"]["selection"],
                "blocked": mapping["timewarp"]["blocked"],
            },
        )
        atomic_write_json(args.artifact_out, artifact)
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    print(
        json.dumps(
            {
                "occurrence_id": binding.occurrence_id,
                "selection": mapping["timewarp"]["selection"],
                "blocked": mapping["timewarp"]["blocked"],
                "calibration_profile_id": context.calibration_profile_id,
                "artifact_id": artifact["artifact_id"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
