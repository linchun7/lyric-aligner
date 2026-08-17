import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lyric_aligner import __version__
from lyric_aligner.assets.resolver import resolve_assets
from lyric_aligner.config import DEFAULT_V4_PROFILE
from lyric_aligner.contracts.artifacts import build_artifact_manifest
from lyric_aligner.pipeline.context import build_pipeline_context
from lyric_aligner.srt import parse_srt_strict
from lyric_aligner.timeline.overlap import ConfirmedOverlapRegion
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


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


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
    write_json(path, artifact)
    return path, artifact


def line(index, text, source_start, source_end, mix_start, mix_end, **extra):
    return {
        "canonical_line_index": index,
        "text": text,
        "timing_format": "line_lrc",
        "source_start_ms": source_start,
        "source_end_ms": source_end,
        "mix_start_ms": mix_start,
        "mix_end_ms": mix_end,
        "end_basis": "synthetic_e2e",
        "tokens": [],
        **extra,
    }


class V4CombinedRecompositionEndToEndTests(unittest.TestCase):
    def test_disjoint_cut_and_overlap_materializations_compose_render_and_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task_root = root / "private" / "combined-task"
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
            mix_audio.write_bytes(b"synthetic-combined-mix")
            song_list = input_dir / "songs.txt"
            song_list.write_text(
                "00:00 Artist A - Cut Song\n00:09 Artist B - Overlap Song\n",
                encoding="utf-8",
            )
            (lyrics_dir / "Artist A - Cut Song.lrc").write_text(
                "[00:01.00]A before\n"
                "[00:06.00]A removed\n"
                "[00:09.00]A after\n"
                "[00:12.00]A overlap tail\n",
                encoding="utf-8",
            )
            (lyrics_dir / "Artist B - Overlap Song.lrc").write_text(
                "[00:00.80]B overlap head\n[00:03.00]B later\n",
                encoding="utf-8",
            )
            (source_dir / "Artist A - Cut Song.wav").write_bytes(b"left-source")
            (source_dir / "Artist B - Overlap Song.wav").write_bytes(b"right-source")

            manifest = build_task_manifest(
                root,
                "combined-task",
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
            write_json(assets_path, assets)
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
            first, second = sorted(context.bindings, key=lambda row: row.ordinal)

            review_payload_path = root / "reviewed_run.json"
            review_payload = {
                "schema_version": "1.2",
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "calibration_profile_version": DEFAULT_V4_PROFILE.profile_version,
                "calibration_profile_id": DEFAULT_V4_PROFILE.profile_id,
                "status": "review_required",
                "legacy_fallback_used": False,
                "occurrences": [],
                "transitions": [],
                "issues": [],
                "review_resolution": {
                    "schema_version": "1.2",
                    "base_run_artifact_id": "base-production-artifact",
                    "remaining_issue_count": 2,
                },
            }
            write_json(review_payload_path, review_payload)
            review_artifact_path, review_artifact = artifact_file(
                root,
                payload_path=review_payload_path,
                stage="review_resolution",
                role="v4_reviewed_run",
                fingerprint=fingerprint,
                upstreams=(assets_artifact["artifact_id"], "base-production-artifact"),
                config={
                    "calibration_profile_version": DEFAULT_V4_PROFILE.profile_version,
                    "calibration_profile_id": DEFAULT_V4_PROFILE.profile_id,
                    "base_run_artifact_id": "base-production-artifact",
                    "legacy_fallback": False,
                },
            )
            review_id = review_artifact["artifact_id"]

            cut_mapping_path = root / "cut-map.json"
            write_json(
                cut_mapping_path,
                {
                    "schema_version": "1.0",
                    "algorithm_version": __version__,
                    "task_fingerprint_sha256": fingerprint,
                    "calibration_profile_version": DEFAULT_V4_PROFILE.profile_version,
                    "calibration_profile_id": DEFAULT_V4_PROFILE.profile_id,
                    "occurrence_id": first.occurrence_id,
                    "track_id": first.track_id,
                    "canonical_selection_sha256": first.canonical_selection_sha256,
                    "result": {"kind": "CUT_AWARE"},
                },
            )
            cut_mapping_artifact_path, cut_mapping_artifact = artifact_file(
                root,
                payload_path=cut_mapping_path,
                stage="cut_timewarp_rebuild",
                role="cut_aware_timewarp",
                fingerprint=fingerprint,
                upstreams=(assets_artifact["artifact_id"], review_id),
                config={
                    "calibration_profile_version": DEFAULT_V4_PROFILE.profile_version,
                    "calibration_profile_id": DEFAULT_V4_PROFILE.profile_id,
                    "source_review_artifact_id": review_id,
                },
            )

            region = ConfirmedOverlapRegion(
                candidate_id="overlap-candidate",
                issue_id="overlap-issue",
                left_occurrence_id=first.occurrence_id,
                right_occurrence_id=second.occurrence_id,
                start_ms=9000,
                end_ms=11500,
            )

            cut_result = {
                "occurrence_id": first.occurrence_id,
                "ordinal": first.ordinal,
                "track_id": first.track_id,
                "artist": first.artist,
                "title": first.title,
                "language_profile": first.language_profile,
                "canonical_selection_sha256": first.canonical_selection_sha256,
                "window": {"start_ms": 0, "end_ms": 12000},
                "line_count": 2,
                "cut_aware": True,
                "cuts": [
                    {
                        "candidate_id": "cut-candidate",
                        "issue_id": "cut-issue",
                        "cut_mix_time": 5.0,
                        "source_gap_start": 5.0,
                        "source_gap_end": 8.0,
                    }
                ],
                "omitted_lines": [
                    {
                        "canonical_line_index": 1,
                        "text": "A removed",
                        "reason": "entire_line_interval_removed_by_confirmed_cut",
                    }
                ],
                "projection_issues": [],
                "lines": [
                    line(0, "A before", 1000, 3000, 1000, 3000),
                    line(2, "A after", 9000, 11000, 6000, 8000),
                ],
            }
            cut_timeline_path = root / "a.cut.timeline.json"
            write_json(
                cut_timeline_path,
                {
                    "schema_version": "1.0",
                    "algorithm_version": __version__,
                    "task_fingerprint_sha256": fingerprint,
                    "calibration_profile_version": DEFAULT_V4_PROFILE.profile_version,
                    "calibration_profile_id": DEFAULT_V4_PROFILE.profile_id,
                    "occurrence_id": first.occurrence_id,
                    "track_id": first.track_id,
                    "mapping_source": "cut_aware_rebuild",
                    "cut_mapping_artifact_id": cut_mapping_artifact["artifact_id"],
                    "result": cut_result,
                },
            )
            cut_timeline_artifact_path, cut_timeline_artifact = artifact_file(
                root,
                payload_path=cut_timeline_path,
                stage="cut_timeline_rebuild",
                role="canonical_timeline",
                fingerprint=fingerprint,
                upstreams=(
                    assets_artifact["artifact_id"],
                    review_id,
                    cut_mapping_artifact["artifact_id"],
                ),
                config={
                    "calibration_profile_version": DEFAULT_V4_PROFILE.profile_version,
                    "calibration_profile_id": DEFAULT_V4_PROFILE.profile_id,
                    "cut_mapping_artifact_id": cut_mapping_artifact["artifact_id"],
                    "source_review_artifact_id": review_id,
                },
            )

            second_primary_result = {
                "occurrence_id": second.occurrence_id,
                "ordinal": second.ordinal,
                "track_id": second.track_id,
                "artist": second.artist,
                "title": second.title,
                "language_profile": second.language_profile,
                "canonical_selection_sha256": second.canonical_selection_sha256,
                "window": {"start_ms": 9000, "end_ms": 14000},
                "line_count": 1,
                "lines": [line(0, "B overlap head", 800, 2200, 9800, 11200)],
            }
            second_primary_path = root / "b.primary.timeline.json"
            write_json(
                second_primary_path,
                {
                    "schema_version": "1.0",
                    "algorithm_version": __version__,
                    "task_fingerprint_sha256": fingerprint,
                    "calibration_profile_version": DEFAULT_V4_PROFILE.profile_version,
                    "calibration_profile_id": DEFAULT_V4_PROFILE.profile_id,
                    "occurrence_id": second.occurrence_id,
                    "track_id": second.track_id,
                    "mapping_source": "synthetic-primary",
                    "result": second_primary_result,
                },
            )
            second_primary_artifact_path, second_primary_artifact = artifact_file(
                root,
                payload_path=second_primary_path,
                stage="canonical_timeline_projection",
                role="canonical_timeline",
                fingerprint=fingerprint,
                upstreams=(assets_artifact["artifact_id"],),
                config={
                    "calibration_profile_version": DEFAULT_V4_PROFILE.profile_version,
                    "calibration_profile_id": DEFAULT_V4_PROFILE.profile_id,
                },
            )

            first_overlap_result = {
                "occurrence_id": first.occurrence_id,
                "ordinal": first.ordinal,
                "track_id": first.track_id,
                "artist": first.artist,
                "title": first.title,
                "language_profile": first.language_profile,
                "canonical_selection_sha256": first.canonical_selection_sha256,
                "window": {"start_ms": 0, "end_ms": 11500},
                "line_count": 4,
                "lines": [
                    line(0, "A before", 1000, 3000, 1000, 3000),
                    line(1, "A removed", 6000, 8000, 5000, 7000),
                    line(2, "A after", 9000, 11000, 6000, 8000),
                    line(
                        3,
                        "A overlap tail",
                        12000,
                        14000,
                        9500,
                        10500,
                        overlap_region_id=region.region_id,
                        overlap_candidate_id=region.candidate_id,
                        overlap_clip=True,
                    ),
                ],
                "overlap_recomposition": {
                    "region_ids": [region.region_id],
                    "candidate_ids": [region.candidate_id],
                },
            }
            first_overlap_path = root / "a.overlap.timeline.json"
            write_json(
                first_overlap_path,
                {
                    "schema_version": "1.0",
                    "algorithm_version": __version__,
                    "task_fingerprint_sha256": fingerprint,
                    "calibration_profile_version": DEFAULT_V4_PROFILE.profile_version,
                    "calibration_profile_id": DEFAULT_V4_PROFILE.profile_id,
                    "occurrence_id": first.occurrence_id,
                    "track_id": first.track_id,
                    "mapping_source": "boundary_recomposition",
                    "result": first_overlap_result,
                },
            )
            first_overlap_artifact_path, first_overlap_artifact = artifact_file(
                root,
                payload_path=first_overlap_path,
                stage="overlap_timeline_recomposition",
                role="canonical_timeline",
                fingerprint=fingerprint,
                upstreams=(assets_artifact["artifact_id"], review_id),
                config={
                    "calibration_profile_version": DEFAULT_V4_PROFILE.profile_version,
                    "calibration_profile_id": DEFAULT_V4_PROFILE.profile_id,
                    "source_review_artifact_id": review_id,
                },
            )

            second_overlap_result = dict(second_primary_result)
            second_overlap_result["lines"] = [
                line(
                    0,
                    "B overlap head",
                    800,
                    2200,
                    9800,
                    11200,
                    overlap_region_id=region.region_id,
                    overlap_candidate_id=region.candidate_id,
                    overlap_clip=True,
                )
            ]
            second_overlap_result["overlap_recomposition"] = {
                "region_ids": [region.region_id],
                "candidate_ids": [region.candidate_id],
            }
            second_overlap_path = root / "b.overlap.timeline.json"
            write_json(
                second_overlap_path,
                {
                    "schema_version": "1.0",
                    "algorithm_version": __version__,
                    "task_fingerprint_sha256": fingerprint,
                    "calibration_profile_version": DEFAULT_V4_PROFILE.profile_version,
                    "calibration_profile_id": DEFAULT_V4_PROFILE.profile_id,
                    "occurrence_id": second.occurrence_id,
                    "track_id": second.track_id,
                    "mapping_source": "boundary_recomposition",
                    "result": second_overlap_result,
                },
            )
            second_overlap_artifact_path, second_overlap_artifact = artifact_file(
                root,
                payload_path=second_overlap_path,
                stage="overlap_timeline_recomposition",
                role="canonical_timeline",
                fingerprint=fingerprint,
                upstreams=(assets_artifact["artifact_id"], review_id),
                config={
                    "calibration_profile_version": DEFAULT_V4_PROFILE.profile_version,
                    "calibration_profile_id": DEFAULT_V4_PROFILE.profile_id,
                    "source_review_artifact_id": review_id,
                },
            )

            cut_run = {
                "schema_version": "1.3",
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "calibration_profile_version": DEFAULT_V4_PROFILE.profile_version,
                "calibration_profile_id": DEFAULT_V4_PROFILE.profile_id,
                "status": "review_required",
                "legacy_fallback_used": False,
                "occurrences": [
                    {
                        "occurrence_id": first.occurrence_id,
                        "ordinal": first.ordinal,
                        "mapping_blocked": False,
                        "cut_rebuilt": True,
                        "timeline_path": str(cut_timeline_path),
                        "timeline_artifact_path": str(cut_timeline_artifact_path),
                        "timeline_stage": "cut_timeline_rebuild",
                        "timeline_line_count": cut_result["line_count"],
                    },
                    {
                        "occurrence_id": second.occurrence_id,
                        "ordinal": second.ordinal,
                        "mapping_blocked": False,
                        "timeline_path": str(second_primary_path),
                        "timeline_artifact_path": str(second_primary_artifact_path),
                        "timeline_stage": "canonical_timeline_projection",
                        "timeline_line_count": second_primary_result["line_count"],
                    },
                ],
                "transitions": [],
                "issues": [
                    {
                        "issue_id": "overlap-issue",
                        "kind": "transition_overlap",
                        "candidate_id": region.candidate_id,
                        "status": "confirmed",
                        "decision_action": "confirmed_overlap",
                    }
                ],
                "cut_rebuild": {
                    "source_review_artifact_id": review_id,
                    "processed_issue_ids": ["cut-issue"],
                    "new_mapping_artifact_ids": [cut_mapping_artifact["artifact_id"]],
                    "new_timeline_artifact_ids": [cut_timeline_artifact["artifact_id"]],
                    "rebuilt_occurrence_count": 1,
                    "canonical_fragment_issue_count": 0,
                    "remaining_issue_count": 1,
                },
            }
            cut_run_path = root / "cut_run.json"
            write_json(cut_run_path, cut_run)
            cut_artifact_path, cut_artifact = artifact_file(
                root,
                payload_path=cut_run_path,
                stage="cut_rebuild",
                role="v4_cut_rebuilt_run",
                fingerprint=fingerprint,
                upstreams=(
                    assets_artifact["artifact_id"],
                    review_id,
                    cut_mapping_artifact["artifact_id"],
                    cut_timeline_artifact["artifact_id"],
                    second_primary_artifact["artifact_id"],
                ),
                config={
                    "calibration_profile_version": DEFAULT_V4_PROFILE.profile_version,
                    "calibration_profile_id": DEFAULT_V4_PROFILE.profile_id,
                    "source_review_artifact_id": review_id,
                    "legacy_fallback": False,
                },
            )

            overlap_run = {
                "schema_version": "1.2",
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "calibration_profile_version": DEFAULT_V4_PROFILE.profile_version,
                "calibration_profile_id": DEFAULT_V4_PROFILE.profile_id,
                "status": "review_required",
                "legacy_fallback_used": False,
                "occurrences": [
                    {
                        "occurrence_id": first.occurrence_id,
                        "ordinal": first.ordinal,
                        "mapping_blocked": False,
                        "overlap_recomposed": True,
                        "timeline_path": str(first_overlap_path),
                        "timeline_artifact_path": str(first_overlap_artifact_path),
                        "timeline_stage": "overlap_timeline_recomposition",
                        "timeline_line_count": first_overlap_result["line_count"],
                    },
                    {
                        "occurrence_id": second.occurrence_id,
                        "ordinal": second.ordinal,
                        "mapping_blocked": False,
                        "overlap_recomposed": True,
                        "timeline_path": str(second_overlap_path),
                        "timeline_artifact_path": str(second_overlap_artifact_path),
                        "timeline_stage": "overlap_timeline_recomposition",
                        "timeline_line_count": second_overlap_result["line_count"],
                    },
                ],
                "transitions": [],
                "issues": [
                    {
                        "issue_id": "cut-issue",
                        "kind": "timewarp_discontinuity",
                        "candidate_id": "cut-candidate",
                        "status": "confirmed",
                        "decision_action": "confirmed_cut",
                    }
                ],
                "confirmed_overlap_regions": [region.to_dict()],
                "overlap_recomposition": {
                    "source_review_artifact_id": review_id,
                    "processed_issue_ids": ["overlap-issue"],
                    "new_timeline_artifact_ids": [
                        first_overlap_artifact["artifact_id"],
                        second_overlap_artifact["artifact_id"],
                    ],
                    "region_count": 1,
                    "remaining_issue_count": 1,
                },
            }
            overlap_run_path = root / "overlap_run.json"
            write_json(overlap_run_path, overlap_run)
            overlap_artifact_path, overlap_artifact = artifact_file(
                root,
                payload_path=overlap_run_path,
                stage="overlap_recomposition",
                role="v4_recomposed_run",
                fingerprint=fingerprint,
                upstreams=(
                    assets_artifact["artifact_id"],
                    review_id,
                    first_overlap_artifact["artifact_id"],
                    second_overlap_artifact["artifact_id"],
                ),
                config={
                    "calibration_profile_version": DEFAULT_V4_PROFILE.profile_version,
                    "calibration_profile_id": DEFAULT_V4_PROFILE.profile_id,
                    "source_review_artifact_id": review_id,
                    "legacy_fallback": False,
                },
            )

            combined_dir = root / "combined"
            combined_run_path = root / "combined_run.json"
            combined_artifact_path = root / "combined_run.artifact.json"
            compose_result = run_command(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "v4_compose_materializations.py"),
                    "--task-manifest",
                    str(manifest_path),
                    "--cut-run",
                    str(cut_run_path),
                    "--cut-artifact",
                    str(cut_artifact_path),
                    "--overlap-run",
                    str(overlap_run_path),
                    "--overlap-artifact",
                    str(overlap_artifact_path),
                    "--track-assets",
                    str(assets_path),
                    "--asset-artifact",
                    str(assets_artifact_path),
                    "--out-dir",
                    str(combined_dir),
                    "--out",
                    str(combined_run_path),
                    "--artifact-out",
                    str(combined_artifact_path),
                ]
            )
            self.assertEqual(compose_result.returncode, 0, msg=compose_result.stderr)
            combined = json.loads(combined_run_path.read_text(encoding="utf-8"))
            self.assertEqual(combined["status"], "ready_for_render")
            self.assertEqual(combined["issues"], [])
            self.assertEqual(
                combined["combined_recomposition"]["combined_occurrence_count"],
                1,
            )
            first_combined = next(
                row
                for row in combined["occurrences"]
                if row["occurrence_id"] == first.occurrence_id
            )
            self.assertEqual(
                first_combined["timeline_stage"],
                "combined_timeline_recomposition",
            )

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
                    str(combined_run_path),
                    "--run-artifact",
                    str(combined_artifact_path),
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
            texts = [cue.text for cue in cues]
            self.assertIn("A before", texts)
            self.assertIn("A after", texts)
            self.assertIn("A overlap tail", texts)
            self.assertIn("B overlap head", texts)
            self.assertNotIn("A removed", texts)
            overlap_cues = [
                cue
                for cue in cues
                if cue.text in {"A overlap tail", "B overlap head"}
            ]
            self.assertEqual(len(overlap_cues), 2)
            self.assertLess(
                max(cue.start_ms for cue in overlap_cues),
                min(cue.end_ms for cue in overlap_cues),
            )

            qa = json.loads(final_qa.read_text(encoding="utf-8"))
            self.assertTrue(qa["publish_ready"])
            self.assertEqual(qa["source_run_stage"], "combined_recomposition")
            self.assertEqual(qa["rebuilt_cut_occurrence_count"], 1)
            self.assertEqual(qa["confirmed_overlap_region_count"], 1)
            self.assertEqual(qa["combined_recomposition_occurrence_count"], 1)

            release_path = root / "release.artifact.json"
            release_result = run_command(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "v4_validate_release.py"),
                    "--task-manifest",
                    str(manifest_path),
                    "--final-srt",
                    str(final_srt),
                    "--report",
                    str(final_csv),
                    "--qa-json",
                    str(final_qa),
                    "--algorithm-version",
                    __version__,
                    "--upstream-artifact",
                    str(final_artifact),
                    "--out-manifest",
                    str(release_path),
                ]
            )
            self.assertEqual(release_result.returncode, 0, msg=release_result.stderr)
            release = json.loads(release_path.read_text(encoding="utf-8"))
            self.assertEqual(release["stage"], "release")
            self.assertEqual(release["algorithm_version"], __version__)


if __name__ == "__main__":
    unittest.main()
