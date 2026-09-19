import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np
import soundfile as sf

from lyric_aligner import __version__
from lyric_aligner.assets.resolver import resolve_assets
from lyric_aligner.audio.cuts import discontinuity_candidate_id
from lyric_aligner.config import DEFAULT_V4_PROFILE
from lyric_aligner.contracts.artifacts import build_artifact_manifest
from lyric_aligner.pipeline.context import build_pipeline_context
from lyric_aligner.srt import parse_srt_strict
from task_contract import build_task_manifest, write_json_atomic


ROOT = Path(__file__).resolve().parents[1]
SR = 16000


def run_command(command):
    return subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def artifact_file(
    root: Path,
    *,
    payload_path: Path,
    stage: str,
    role: str,
    fingerprint: str,
    upstreams=(),
    config=None,
):
    artifact = build_artifact_manifest(
        task_fingerprint_sha256=fingerprint,
        stage=stage,
        algorithm_version=__version__,
        outputs=((role, payload_path),),
        normalized_config=config or {},
        upstream_artifact_ids=tuple(upstreams),
    )
    path = root / f"{payload_path.name}.{stage}.artifact.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")
    return path, artifact


def chord_source(duration=12.0):
    samples = int(round(duration * SR))
    output = np.zeros(samples, dtype=np.float32)
    block = int(0.5 * SR)
    chords = [
        (220.0, 277.18, 329.63),
        (246.94, 311.13, 369.99),
        (261.63, 329.63, 392.00),
        (293.66, 369.99, 440.00),
        (329.63, 415.30, 493.88),
        (349.23, 440.00, 523.25),
        (392.00, 493.88, 587.33),
        (440.00, 554.37, 659.25),
        (466.16, 587.33, 698.46),
        (523.25, 659.25, 783.99),
        (587.33, 739.99, 880.00),
        (622.25, 783.99, 932.33),
    ]
    for index in range((samples + block - 1) // block):
        start = index * block
        end = min(samples, start + block)
        count = end - start
        t = np.arange(count, dtype=np.float64) / SR
        frequencies = chords[index % len(chords)]
        wave = sum(
            np.sin(2.0 * np.pi * frequency * t + index * 0.173)
            for frequency in frequencies
        ) / len(frequencies)
        output[start:end] = (0.65 * wave).astype(np.float32)
    return output


def path_point(mix, source):
    return {
        "mix_center": mix,
        "source_center": source,
        "estimated_slope": 1.0,
        "fused_score": 0.96,
        "feature_scores": {"chroma": 0.96, "mfcc": 0.94},
    }


class V4CutRebuildEndToEndTests(unittest.TestCase):
    def test_confirmed_cut_rebuild_omits_gap_lyrics_and_renders_retained_qrc(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task_root = root / "private" / "cut-task"
            input_dir = task_root / "input"
            qa_dir = task_root / "qa"
            lyrics_dir = input_dir / "lyrics"
            source_dir = input_dir / "source-audio"
            for directory in (qa_dir, lyrics_dir, source_dir):
                directory.mkdir(parents=True, exist_ok=True)

            source_srt = input_dir / "source.srt"
            source_srt.write_text(
                "1\n00:00:00,000 --> 00:00:01,000\nplaceholder\n",
                encoding="utf-8",
            )
            source = chord_source()
            source_audio = source_dir / "Artist - Cut Song.wav"
            sf.write(source_audio, source, SR)
            mix = np.concatenate([source[: 5 * SR], source[8 * SR :]])
            mix_audio = input_dir / "mix.wav"
            sf.write(mix_audio, mix, SR)
            song_list = input_dir / "songs.txt"
            song_list.write_text("00:00 Artist - Cut Song\n", encoding="utf-8")

            # QRC-style token timings give explicit token durations, so a confirmed
            # source gap can safely omit complete removed tokens without guessing.
            lyric = lyrics_dir / "Artist - Cut Song.lrc"
            lyric.write_text(
                "[1000,1000]before (1000,500)cut(1500,500)\n"
                "[5500,1000]removed (5500,500)words(6000,500)\n"
                "[8200,1000]after (8200,500)cut(8700,500)\n"
                "[10000,1000]final (10000,500)line(10500,500)\n",
                encoding="utf-8",
            )

            manifest = build_task_manifest(
                root,
                "cut-task",
                source_srt=source_srt,
                audio=mix_audio,
                song_list=song_list,
                lyrics_dir=lyrics_dir,
                source_audio_dir=source_dir,
            )
            manifest_path = qa_dir / "task_manifest.json"
            write_json_atomic(manifest_path, manifest)
            fingerprint = manifest["task_fingerprint_sha256"]

            profile = replace(
                DEFAULT_V4_PROFILE,
                profile_version="synthetic-cut-e2e-a7",
                cut_boundary=replace(
                    DEFAULT_V4_PROFILE.cut_boundary,
                    context_seconds=0.70,
                    source_radius_seconds=0.60,
                    min_side_score=0.45,
                    min_side_margin=0.0,
                    min_boundary_margin=0.01,
                ),
            )
            assets = resolve_assets(
                song_list=song_list,
                lyrics_dir=lyrics_dir,
                source_audio_dir=source_dir,
                min_score=profile.asset_resolver.min_score,
                min_margin=profile.asset_resolver.min_margin,
            )
            assets.update(
                {
                    "algorithm_version": __version__,
                    "task_fingerprint_sha256": fingerprint,
                    "calibration_profile_version": profile.profile_version,
                    "calibration_profile_id": profile.profile_id,
                    "calibration_profile": profile.to_dict(),
                    "calibration_overrides": {},
                }
            )
            assets_path = root / "track_assets.json"
            assets_path.write_text(json.dumps(assets), encoding="utf-8")
            assets_artifact_path, assets_artifact = artifact_file(
                root,
                payload_path=assets_path,
                stage="asset_resolution",
                role="track_assets",
                fingerprint=fingerprint,
                config={
                    "calibration_profile_version": profile.profile_version,
                    "calibration_profile_id": profile.profile_id,
                    "calibration_overrides": {},
                },
            )
            context = build_pipeline_context(
                expected_task_fingerprint=fingerprint,
                track_assets_payload=assets,
                asset_artifact=assets_artifact,
                verify_asset_files=True,
            )
            [binding] = context.bindings

            discontinuity = {
                "mix_before": 4.5,
                "mix_after": 5.5,
                "source_before": 4.5,
                "source_after": 8.5,
                "observed_source_jump": 4.0,
                "expected_continuous_advance": 1.0,
                "excess_source_jump": 3.0,
                "allowed_continuous_advance": 2.0,
                "reason": "synthetic forward source jump",
            }
            candidate_id = discontinuity_candidate_id(
                binding.occurrence_id, discontinuity
            )
            alignment_path = [
                path_point(0.5, 0.5),
                path_point(1.5, 1.5),
                path_point(2.5, 2.5),
                path_point(3.5, 3.5),
                path_point(4.5, 4.5),
                path_point(5.5, 8.5),
                path_point(6.5, 9.5),
                path_point(7.5, 10.5),
                path_point(8.5, 11.5),
            ]
            coarse = {
                "schema_version": "1.1",
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "calibration_profile_version": profile.profile_version,
                "calibration_profile_id": profile.profile_id,
                "occurrence_id": binding.occurrence_id,
                "track_id": binding.track_id,
                "canonical_selection_sha256": binding.canonical_selection_sha256,
                "upstream_asset_artifact_id": assets_artifact["artifact_id"],
                "result": {
                    "path": alignment_path,
                    "timewarp": {
                        "blocked": True,
                        "selection": "MIDDLE_DISCONTINUITY_REVIEW_REQUIRED",
                        "discontinuities": [discontinuity],
                    },
                },
            }
            coarse_path = root / "primary.coarse.json"
            coarse_path.write_text(json.dumps(coarse), encoding="utf-8")
            coarse_artifact_path, coarse_artifact = artifact_file(
                root,
                payload_path=coarse_path,
                stage="coarse_audio_alignment",
                role="coarse_alignment",
                fingerprint=fingerprint,
                upstreams=(assets_artifact["artifact_id"],),
                config={
                    "calibration_profile_version": profile.profile_version,
                    "calibration_profile_id": profile.profile_id,
                    "asset_artifact_id": assets_artifact["artifact_id"],
                },
            )

            issue_id = "confirmed-cut-issue"
            reviewed_run = {
                "schema_version": "1.2",
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "calibration_profile_version": profile.profile_version,
                "calibration_profile_id": profile.profile_id,
                "status": "review_required",
                "legacy_fallback_used": False,
                "occurrences": [
                    {
                        "occurrence_id": binding.occurrence_id,
                        "ordinal": binding.ordinal,
                        "primary_interval": [0.0, 9.0],
                        "timewarp_selection": "MIDDLE_DISCONTINUITY_REVIEW_REQUIRED",
                        "fine_applied": False,
                        "mapping_source": "coarse",
                        "mapping_blocked": True,
                        "discontinuity_candidate_count": 1,
                        "coarse_path": str(coarse_path),
                        "coarse_artifact_path": str(coarse_artifact_path),
                        "fine_path": None,
                        "fine_artifact_path": None,
                        "timeline_line_count": 0,
                        "timeline_path": None,
                        "timeline_artifact_path": None,
                        "timeline_stage": None,
                    }
                ],
                "transitions": [],
                "issues": [
                    {
                        "kind": "timewarp_discontinuity",
                        "code": "source_position_discontinuity",
                        "candidate_id": candidate_id,
                        "issue_id": issue_id,
                        "occurrence_id": binding.occurrence_id,
                        "status": "confirmed",
                        "selection": "MIDDLE_DISCONTINUITY_REVIEW_REQUIRED",
                        "reason": discontinuity["reason"],
                        "decision_action": "confirmed_cut",
                        "requires_timeline_rebuild": True,
                        "confirmed_discontinuity": {
                            "mix_before": 4.5,
                            "mix_after": 5.5,
                            "source_before": 4.5,
                            "source_after": 8.5,
                        },
                        **discontinuity,
                    }
                ],
                "review_resolution": {
                    "schema_version": "1.2",
                    "base_run_artifact_id": "base-production-artifact",
                    "remaining_issue_count": 1,
                },
            }
            reviewed_path = root / "reviewed_run.json"
            reviewed_path.write_text(json.dumps(reviewed_run), encoding="utf-8")
            review_artifact_path, review_artifact = artifact_file(
                root,
                payload_path=reviewed_path,
                stage="review_resolution",
                role="v4_reviewed_run",
                fingerprint=fingerprint,
                upstreams=(
                    "base-production-artifact",
                    assets_artifact["artifact_id"],
                    coarse_artifact["artifact_id"],
                ),
                config={
                    "calibration_profile_version": profile.profile_version,
                    "calibration_profile_id": profile.profile_id,
                    "base_run_artifact_id": "base-production-artifact",
                    "legacy_fallback": False,
                },
            )

            cut_dir = root / "cut-rebuild"
            cut_run = root / "cut_rebuilt_run.json"
            cut_artifact = root / "cut_rebuilt_run.artifact.json"
            rebuilt = run_command(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "v4_rebuild_cut.py"),
                    "--task-manifest",
                    str(manifest_path),
                    "--run",
                    str(reviewed_path),
                    "--run-artifact",
                    str(review_artifact_path),
                    "--track-assets",
                    str(assets_path),
                    "--asset-artifact",
                    str(assets_artifact_path),
                    "--out-dir",
                    str(cut_dir),
                    "--out",
                    str(cut_run),
                    "--artifact-out",
                    str(cut_artifact),
                ]
            )
            self.assertEqual(rebuilt.returncode, 0, msg=rebuilt.stderr)
            rebuilt_payload = json.loads(cut_run.read_text(encoding="utf-8"))
            self.assertEqual(rebuilt_payload["status"], "ready_for_render")
            self.assertEqual(rebuilt_payload["issues"], [])
            [occurrence] = rebuilt_payload["occurrences"]
            self.assertTrue(occurrence["cut_rebuilt"])
            self.assertEqual(occurrence["timeline_stage"], "cut_timeline_rebuild")

            final_srt = root / "FINAL.srt"
            final_csv = root / "FINAL.csv"
            final_qa = root / "FINAL.qa.json"
            final_artifact = root / "FINAL.render.artifact.json"
            rendered = run_command(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "v4_render.py"),
                    "--task-manifest",
                    str(manifest_path),
                    "--run",
                    str(cut_run),
                    "--run-artifact",
                    str(cut_artifact),
                    "--track-assets",
                    str(assets_path),
                    "--asset-artifact",
                    str(assets_artifact_path),
                    "--final-srt",
                    str(final_srt),
                    "--report",
                    str(final_csv),
                    "--qa-json",
                    str(final_qa),
                    "--artifact-out",
                    str(final_artifact),
                ]
            )
            self.assertEqual(rendered.returncode, 0, msg=rendered.stderr)
            texts = [cue.text for cue in parse_srt_strict(final_srt)]
            self.assertTrue(any("before" in text for text in texts))
            self.assertTrue(any("after" in text for text in texts))
            self.assertTrue(any("final" in text for text in texts))
            self.assertFalse(any("removed" in text for text in texts))
            qa = json.loads(final_qa.read_text(encoding="utf-8"))
            self.assertFalse(qa["publish_ready"])
            self.assertEqual(
                qa["segmentation_authority"], "canonical_line_evaluation_only"
            )
            self.assertEqual(
                qa["release_blocked_reason"], "editor_cue_reconciliation_required"
            )
            self.assertEqual(qa["source_run_stage"], "cut_rebuild")
            self.assertEqual(qa["rebuilt_cut_occurrence_count"], 1)


if __name__ == "__main__":
    unittest.main()
