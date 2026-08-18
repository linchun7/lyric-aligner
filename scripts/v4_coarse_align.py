#!/usr/bin/env python3
"""Run fingerprinted v4 harmonic coarse Source-to-Mix alignment for one occurrence."""

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
from lyric_aligner.audio.coarse_mapper import build_coarse_timewarp
from lyric_aligner.audio.feature_cache import FeatureCacheSpec, load_feature_bundle, save_feature_bundle
from lyric_aligner.audio.features import extract_harmonic_features
from lyric_aligner.config import calibration_overrides
from lyric_aligner.contracts.artifacts import atomic_write_json, build_artifact_manifest, validate_artifact_output
from lyric_aligner.pipeline.context import build_pipeline_context
from task_contract import assert_manifest_paths, load_task_manifest, resolve_manifest_record


MIX_DECODE_PADDING_SECONDS = 2.0


def _default_interval(bindings, binding, mix_duration: float) -> tuple[float, float]:
    ordered = sorted(bindings, key=lambda item: item.ordinal)
    position = next(index for index, item in enumerate(ordered) if item.occurrence_id == binding.occurrence_id)
    start = float(binding.nominal_start_ms) / 1000.0
    end = float(ordered[position + 1].nominal_start_ms) / 1000.0 if position + 1 < len(ordered) else mix_duration
    return max(0.0, start), min(mix_duration, end)


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
        raise ValueError("bounded mix decode ended before requested occurrence interval")
    return audio, decode_start


def _default_feature_cache_dir(out_path: Path) -> Path | None:
    resolved = out_path.resolve()
    for parent in resolved.parents:
        if parent.name in {"primary", "transitions"}:
            return parent.parent / "cache" / "features"
    return None


def _source_features(
    source_path: Path,
    *,
    source_sha256: str,
    sr: int,
    hop_length: int,
    cache_dir: Path | None,
):
    spec = FeatureCacheSpec(
        audio_sha256=source_sha256,
        sr=sr,
        hop_length=hop_length,
    )
    if cache_dir is not None:
        cached = load_feature_bundle(cache_dir, spec)
        if cached is not None:
            return cached, "hit"

    source_audio, _ = librosa.load(source_path, sr=sr, mono=True)
    features = extract_harmonic_features(
        source_audio,
        sr=sr,
        hop_length=hop_length,
    )
    cache_status = "disabled"
    if cache_dir is not None:
        try:
            save_feature_bundle(cache_dir, spec, features)
            cache_status = "miss_written"
        except OSError:
            # A disposable performance cache must never become a production
            # blocker. Continue with the freshly computed SHA-bound features.
            cache_status = "miss_write_failed"
    return features, cache_status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--mix-audio", required=True, type=Path)
    parser.add_argument("--track-assets", required=True, type=Path)
    parser.add_argument("--asset-artifact", required=True, type=Path)
    parser.add_argument("--occurrence-id", required=True)
    parser.add_argument("--bpm-prior", type=float)
    parser.add_argument("--mix-start", type=float)
    parser.add_argument("--mix-end", type=float)
    parser.add_argument("--sr", type=int)
    parser.add_argument("--hop-length", type=int)
    parser.add_argument("--window-seconds", type=float)
    parser.add_argument("--step-seconds", type=float)
    parser.add_argument("--candidate-step-seconds", type=float)
    parser.add_argument("--feature-cache-dir", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--artifact-out", required=True, type=Path)
    parser.add_argument("--git-commit", default="")
    args = parser.parse_args()

    try:
        task = load_task_manifest(args.task_manifest)
        assert_manifest_paths(args.task_manifest, task, {"audio": args.mix_audio})
        fingerprint = str(task["task_fingerprint_sha256"])
        assets = json.loads(args.track_assets.read_text(encoding="utf-8-sig"))
        asset_artifact = json.loads(args.asset_artifact.read_text(encoding="utf-8-sig"))
        output_issues = validate_artifact_output(asset_artifact, role="track_assets", path=args.track_assets)
        if output_issues:
            raise ValueError("invalid asset artifact output: " + "; ".join(output_issues))
        context = build_pipeline_context(
            expected_task_fingerprint=fingerprint,
            track_assets_payload=assets,
            asset_artifact=asset_artifact,
            verify_asset_files=True,
        )
        binding = context.binding_by_occurrence_id.get(args.occurrence_id)
        if binding is None:
            raise ValueError(f"occurrence_id not found in track assets: {args.occurrence_id}")
        defaults = context.profile.coarse
        timewarp_defaults = context.profile.timewarp
        sr = defaults.sr if args.sr is None else args.sr
        hop_length = defaults.hop_length if args.hop_length is None else args.hop_length
        window_seconds = defaults.window_seconds if args.window_seconds is None else args.window_seconds
        step_seconds = defaults.step_seconds if args.step_seconds is None else args.step_seconds
        candidate_step_seconds = defaults.candidate_step_seconds if args.candidate_step_seconds is None else args.candidate_step_seconds
        overrides = calibration_overrides(defaults, {
            "sr": sr,
            "hop_length": hop_length,
            "window_seconds": window_seconds,
            "step_seconds": step_seconds,
            "candidate_step_seconds": candidate_step_seconds,
        })
        source_path = Path(binding.source_audio_path)
        source_dir_record = task["inputs"].get("source_audio_dir")
        if source_dir_record is not None:
            source_dir = resolve_manifest_record(args.task_manifest, source_dir_record).resolve()
            try:
                source_path.resolve().relative_to(source_dir)
            except ValueError as exc:
                raise ValueError("TrackAsset source audio is outside task source_audio_dir") from exc

        mix_duration = float(librosa.get_duration(path=str(args.mix_audio)))
        default_start, default_end = _default_interval(context.bindings, binding, mix_duration)
        mix_start = default_start if args.mix_start is None else args.mix_start
        mix_end = default_end if args.mix_end is None else args.mix_end
        if mix_start < 0 or mix_end > mix_duration or mix_end <= mix_start:
            raise ValueError("invalid occurrence mix interval")
        mix_audio, mix_audio_start = _load_bounded_mix(
            args.mix_audio,
            sr=sr,
            mix_start=mix_start,
            mix_end=mix_end,
            full_mix_duration=mix_duration,
        )
        feature_cache_dir = args.feature_cache_dir or _default_feature_cache_dir(args.out)
        source_feature_bundle, cache_status = _source_features(
            source_path,
            source_sha256=binding.source_audio_sha256,
            sr=sr,
            hop_length=hop_length,
            cache_dir=feature_cache_dir,
        )

        mapping = build_coarse_timewarp(
            mix_audio,
            None,
            sr=sr,
            mix_start=mix_start,
            mix_end=mix_end,
            mix_audio_start=mix_audio_start,
            full_mix_duration=mix_duration,
            source_feature_bundle=source_feature_bundle,
            bpm_prior=args.bpm_prior,
            middle_cut=binding.middle_cut,
            feature_hop_length=hop_length,
            window_seconds=window_seconds,
            step_seconds=step_seconds,
            candidate_step_seconds=candidate_step_seconds,
            slope_minimum=defaults.slope_minimum,
            slope_maximum=defaults.slope_maximum,
            slope_step=defaults.slope_step,
            min_score=defaults.min_score,
            min_margin=defaults.min_margin,
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
            "occurrence_id": binding.occurrence_id,
            "track_id": binding.track_id,
            "canonical_selection_sha256": binding.canonical_selection_sha256,
            "mix_audio_sha256": str(task["inputs"]["audio"]["sha256"]),
            "source_audio_sha256": binding.source_audio_sha256,
            "upstream_asset_artifact_id": context.asset_artifact.artifact_id,
            "result": mapping,
        }
        atomic_write_json(args.out, payload)
        normalized_config = {
            **context.artifact_config(),
            "calibration_overrides": overrides,
            "sr": sr,
            "hop_length": hop_length,
            "window_seconds": window_seconds,
            "step_seconds": step_seconds,
            "candidate_step_seconds": candidate_step_seconds,
            "slope_minimum": defaults.slope_minimum,
            "slope_maximum": defaults.slope_maximum,
            "slope_step": defaults.slope_step,
            "min_score": defaults.min_score,
            "min_margin": defaults.min_margin,
            "timewarp": timewarp_defaults.__dict__,
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

    print(json.dumps({
        "occurrence_id": binding.occurrence_id,
        "selection": mapping["timewarp"]["selection"],
        "blocked": mapping["timewarp"]["blocked"],
        "source_feature_cache": cache_status,
        "calibration_profile_id": context.calibration_profile_id,
        "calibration_override": bool(overrides),
        "artifact_id": artifact["artifact_id"],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
