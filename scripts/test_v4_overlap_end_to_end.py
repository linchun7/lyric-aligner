import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lyric_aligner import __version__
from lyric_aligner.assets.resolver import resolve_assets
from lyric_aligner.audio.transition import transition_candidate_id
from lyric_aligner.config import DEFAULT_V4_PROFILE
from lyric_aligner.contracts.artifacts import build_artifact_manifest
from lyric_aligner.pipeline.context import build_pipeline_context
from lyric_aligner.srt import parse_srt_strict
from lyric_aligner.timeline.projector import ProjectionWindow, project_binding_timeline
from task_contract import build_task_manifest, write_json_atomic


ROOT = Path(__file__).resolve().parents[1]


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


class V4OverlapEndToEndTests(unittest.TestCase):
    def test_confirmed_candidate_recomposes_two_canonical_streams_and_renders_overlap(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task_root = root / "private" / "overlap-task"
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
            mix_audio = input_dir / "mix.wav"
            mix_audio.write_bytes(b"synthetic-overlap-mix")
            song_list = input_dir / "songs.txt"
            song_list.write_text(
                "00:00 Artist A - Left Song\n00:10 Artist B - Right Song\n",
                encoding="utf-8",
            )
            left_lrc = lyrics_dir / "Artist A - Left Song.lrc"
            left_lrc.write_text(
                "[00:08.00]left before\n"
                "[00:10.20]left overlap\n"
                "[00:12.00]left after\n",
                encoding="utf-8",
            )
            right_lrc = lyrics_dir / "Artist B - Right Song.lrc"
            right_lrc.write_text(
                "[00:00.00]right overlap\n"
                "[00:02.50]right later\n",
                encoding="utf-8",
            )
            (source_dir / "Artist A - Left Song.wav").write_bytes(b"left-source")
            (source_dir / "Artist B - Right Song.wav").write_bytes(b"right-source")

            manifest = build_task_manifest(
                root,
                "overlap-task",
                source_srt=source_srt,
                audio=mix_audio,
                song_list=song_list,
                lyrics_dir=lyrics_dir,
                source_audio_dir=source_dir,
            )
            manifest_path = qa_dir / "task_manifest.json"
            write_json_atomic(manifest_path, manifest)
            fingerprint = manifest["task_fingerprint_sha256"]

            assets = resolve_assets(
                song_list=song_list,
                lyrics_dir=lyrics_dir,
                source_audio_dir=source_dir,
            )
            assets.update(
                {
                    "algorithm_version": __version__,
                    "task_fingerprint_sha256": fingerprint,
                    "calibration_profile_version": DEFAULT_V4_PROFILE.profile_version,
                    "calibration_profile_id": DEFAULT_V4_PROFILE.profile_id,
                    "calibration_profile": DEFAULT_V4_PROFILE.to_dict(),
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
                    "calibration_profile_version": DEFAULT_V4_PROFILE.profile_version,
                    "calibration_profile_id": DEFAULT_V4_PROFILE.profile_id,
                    "calibration_overrides": {},
                },
            )
            context = build_pipeline_context(
                expected_task_fingerprint=fingerprint,
                track_assets_payload=assets,
                asset_artifact=assets_artifact,
                verify_asset_files=True,
            )
            ordered = sorted(context.bindings, key=lambda row: row.ordinal)
            left_binding, right_binding = ordered

            left_mapping = {
                "intercept": 0.0,
                "base_slope": 1.0,
                "breakpoints": [],
                "slope_deltas": [],
            }
            right_mapping = {
                "intercept": -9.5,
                "base_slope": 1.0,
                "breakpoints": [],
                "slope_deltas": [],
            }

            timeline_dir = root / "timelines"
            timeline_dir.mkdir()
            occurrence_rows = []
            primary_artifacts = []
            for binding, mapping, window in (
                (left_binding, left_mapping, ProjectionWindow(0, 10000)),
                (right_binding, right_mapping, ProjectionWindow(10000, 20000)),
            ):
                result = project_binding_timeline(binding, mapping, window=window)
                timeline_payload = {
                    "schema_version": "1.0",
                    "algorithm_version": __version__,
                    "task_fingerprint_sha256": fingerprint,
                    "calibration_profile_version": DEFAULT_V4_PROFILE.profile_version,
                    "calibration_profile_id": DEFAULT_V4_PROFILE.profile_id,
                    "occurrence_id": binding.occurrence_id,
                    "track_id": binding.track_id,
                    "mapping_source": "synthetic-primary",
                    "result": result,
                }
                timeline_path = timeline_dir / f"{binding.ordinal}.timeline.json"
                timeline_path.write_text(json.dumps(timeline_payload), encoding="utf-8")
                timeline_artifact_path, timeline_artifact = artifact_file(
                    root,
                    payload_path=timeline_path,
                    stage="canonical_timeline_projection",
                    role="canonical_timeline",
                    fingerprint=fingerprint,
                    upstreams=(assets_artifact["artifact_id"],),
                    config={
                        "calibration_profile_version": DEFAULT_V4_PROFILE.profile_version,
                        "calibration_profile_id": DEFAULT_V4_PROFILE.profile_id,
                        "asset_artifact_id": assets_artifact["artifact_id"],
                    },
                )
                primary_artifacts.append(timeline_artifact["artifact_id"])
                occurrence_rows.append(
                    {
                        "occurrence_id": binding.occurrence_id,
                        "ordinal": binding.ordinal,
                        "primary_interval": [0.0, 10.0]
                        if binding.ordinal == 1
                        else [10.0, 20.0],
                        "mapping_blocked": False,
                        "timeline_line_count": result["line_count"],
                        "timeline_path": str(timeline_path),
                        "timeline_artifact_path": str(timeline_artifact_path),
                        "timeline_stage": "canonical_timeline_projection",
                    }
                )

            transition_dir = root / "transition"
            transition_dir.mkdir()
            coarse_entries = {}
            for side, binding, mapping in (
                ("left", left_binding, left_mapping),
                ("right", right_binding, right_mapping),
            ):
                coarse_payload = {
                    "schema_version": "1.1",
                    "algorithm_version": __version__,
                    "task_fingerprint_sha256": fingerprint,
                    "calibration_profile_version": DEFAULT_V4_PROFILE.profile_version,
                    "calibration_profile_id": DEFAULT_V4_PROFILE.profile_id,
                    "occurrence_id": binding.occurrence_id,
                    "track_id": binding.track_id,
                    "canonical_selection_sha256": binding.canonical_selection_sha256,
                    "upstream_asset_artifact_id": assets_artifact["artifact_id"],
                    "result": {
                        "windows": [{"ambiguous": False}],
                        "timewarp": {
                            "blocked": False,
                            "selection": "AFFINE_ACCEPTED",
                            "mapping": mapping,
                        },
                    },
                }
                coarse_path = transition_dir / f"{side}.coarse.json"
                coarse_path.write_text(json.dumps(coarse_payload), encoding="utf-8")
                coarse_artifact_path, coarse_artifact = artifact_file(
                    root,
                    payload_path=coarse_path,
                    stage="coarse_audio_alignment",
                    role="coarse_alignment",
                    fingerprint=fingerprint,
                    upstreams=(assets_artifact["artifact_id"],),
                    config={
                        "calibration_profile_version": DEFAULT_V4_PROFILE.profile_version,
                        "calibration_profile_id": DEFAULT_V4_PROFILE.profile_id,
                        "asset_artifact_id": assets_artifact["artifact_id"],
                    },
                )
                coarse_entries[side] = (
                    coarse_path,
                    coarse_artifact_path,
                    coarse_artifact,
                )

            start, end = 9.0, 11.5
            candidate_id = transition_candidate_id(
                "cross_track_overlap_candidate",
                left_binding.occurrence_id,
                right_binding.occurrence_id,
                start,
                end,
            )
            transition_payload = {
                "schema_version": "1.1",
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "calibration_profile_version": DEFAULT_V4_PROFILE.profile_version,
                "calibration_profile_id": DEFAULT_V4_PROFILE.profile_id,
                "left_occurrence_id": left_binding.occurrence_id,
                "right_occurrence_id": right_binding.occurrence_id,
                "left_track_id": left_binding.track_id,
                "right_track_id": right_binding.track_id,
                "result": {
                    "left_occurrence_id": left_binding.occurrence_id,
                    "right_occurrence_id": right_binding.occurrence_id,
                    "overlap_candidates": [
                        {
                            "candidate_id": candidate_id,
                            "type": "cross_track_overlap_candidate",
                            "status": "review",
                            "start": start,
                            "end": end,
                            "occurrences": [
                                left_binding.occurrence_id,
                                right_binding.occurrence_id,
                            ],
                            "reason": "synthetic confirmed overlap",
                            "left_score": 0.95,
                            "right_score": 0.94,
                        }
                    ],
                    "uncertain_intervals": [],
                    "blocked": True,
                    "status": "review_required",
                },
            }
            transition_path = transition_dir / "transition.json"
            transition_path.write_text(json.dumps(transition_payload), encoding="utf-8")
            transition_artifact_path, transition_artifact = artifact_file(
                root,
                payload_path=transition_path,
                stage="transition_probe",
                role="transition_probe",
                fingerprint=fingerprint,
                upstreams=(
                    assets_artifact["artifact_id"],
                    coarse_entries["left"][2]["artifact_id"],
                    coarse_entries["right"][2]["artifact_id"],
                ),
                config={
                    "calibration_profile_version": DEFAULT_V4_PROFILE.profile_version,
                    "calibration_profile_id": DEFAULT_V4_PROFILE.profile_id,
                    "asset_artifact_id": assets_artifact["artifact_id"],
                },
            )

            issue_id = "confirmed-issue-1"
            reviewed_run = {
                "schema_version": "1.1",
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "calibration_profile_version": DEFAULT_V4_PROFILE.profile_version,
                "calibration_profile_id": DEFAULT_V4_PROFILE.profile_id,
                "status": "review_required",
                "legacy_fallback_used": False,
                "occurrences": occurrence_rows,
                "transitions": [
                    {
                        "left_occurrence_id": left_binding.occurrence_id,
                        "right_occurrence_id": right_binding.occurrence_id,
                        "blocked": True,
                        "overlap_candidate_count": 1,
                        "uncertain_interval_count": 0,
                        "left_coarse_path": str(coarse_entries["left"][0]),
                        "left_coarse_artifact_path": str(coarse_entries["left"][1]),
                        "right_coarse_path": str(coarse_entries["right"][0]),
                        "right_coarse_artifact_path": str(coarse_entries["right"][1]),
                        "transition_path": str(transition_path),
                        "transition_artifact_path": str(transition_artifact_path),
                    }
                ],
                "issues": [
                    {
                        "kind": "transition_overlap",
                        "code": "cross_track_overlap_candidate",
                        "candidate_id": candidate_id,
                        "issue_id": issue_id,
                        "left_occurrence_id": left_binding.occurrence_id,
                        "right_occurrence_id": right_binding.occurrence_id,
                        "interval_start": start,
                        "interval_end": end,
                        "confirmed_interval": [start, end],
                        "status": "confirmed",
                        "decision_action": "confirmed_overlap",
                        "requires_recomposition": True,
                    }
                ],
                "review_resolution": {
                    "schema_version": "1.1",
                    "base_run_artifact_id": "base-production-artifact",
                    "remaining_issue_count": 1,
                },
            }
            reviewed_path = root / "reviewed_run.json"
            reviewed_path.write_text(json.dumps(reviewed_run), encoding="utf-8")
            inherited = {
                assets_artifact["artifact_id"],
                *primary_artifacts,
                coarse_entries["left"][2]["artifact_id"],
                coarse_entries["right"][2]["artifact_id"],
                transition_artifact["artifact_id"],
                "base-production-artifact",
            }
            review_artifact_path, review_artifact = artifact_file(
                root,
                payload_path=reviewed_path,
                stage="review_resolution",
                role="v4_reviewed_run",
                fingerprint=fingerprint,
                upstreams=tuple(inherited),
                config={
                    "calibration_profile_version": DEFAULT_V4_PROFILE.profile_version,
                    "calibration_profile_id": DEFAULT_V4_PROFILE.profile_id,
                    "base_run_artifact_id": "base-production-artifact",
                    "legacy_fallback": False,
                },
            )

            recompose_dir = root / "recompose"
            recomposed_path = root / "recomposed_run.json"
            recomposed_artifact_path = root / "recomposed_run.artifact.json"
            recompose_result = run_command(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "v4_recompose_overlap.py"),
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
                    str(recompose_dir),
                    "--out",
                    str(recomposed_path),
                    "--artifact-out",
                    str(recomposed_artifact_path),
                ]
            )
            self.assertEqual(recompose_result.returncode, 0, msg=recompose_result.stderr)
            recomposed = json.loads(recomposed_path.read_text(encoding="utf-8"))
            self.assertEqual(recomposed["status"], "ready_for_render")
            self.assertEqual(recomposed["issues"], [])
            self.assertEqual(len(recomposed["confirmed_overlap_regions"]), 1)
            self.assertTrue(all(row.get("overlap_recomposed") for row in recomposed["occurrences"]))

            final_srt = root / "FINAL.srt"
            final_csv = root / "FINAL.csv"
            final_qa = root / "FINAL.qa.json"
            final_artifact = root / "FINAL.render.artifact.json"
            render_result = run_command(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "v4_render.py"),
                    "--task-manifest",
                    str(manifest_path),
                    "--run",
                    str(recomposed_path),
                    "--run-artifact",
                    str(recomposed_artifact_path),
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
            self.assertEqual(render_result.returncode, 0, msg=render_result.stderr)
            cues = parse_srt_strict(final_srt)
            self.assertIn("left overlap", [cue.text for cue in cues])
            self.assertIn("right overlap", [cue.text for cue in cues])

            overlapping_cross_track = False
            audit_rows = final_csv.read_text(encoding="utf-8-sig").splitlines()
            self.assertGreater(len(audit_rows), 2)
            for i, left in enumerate(cues):
                for right in cues[i + 1 :]:
                    if right.start_ms < left.end_ms and left.start_ms < right.end_ms:
                        overlapping_cross_track = True
                        break
                if overlapping_cross_track:
                    break
            self.assertTrue(overlapping_cross_track)
            qa = json.loads(final_qa.read_text(encoding="utf-8"))
            self.assertTrue(qa["publish_ready"])
            self.assertEqual(qa["source_run_stage"], "overlap_recomposition")
            self.assertEqual(qa["confirmed_overlap_region_count"], 1)


if __name__ == "__main__":
    unittest.main()
