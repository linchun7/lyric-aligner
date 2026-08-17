import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lyric_aligner import __version__
from lyric_aligner.contracts.artifacts import build_artifact_manifest
from task_contract import build_task_manifest, write_json_atomic


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "v4_fuse_evidence.py"


def run_command(*args: str):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def write_artifact(
    root: Path,
    *,
    payload_path: Path,
    stage: str,
    role: str,
    fingerprint: str,
    upstreams=(),
):
    artifact = build_artifact_manifest(
        task_fingerprint_sha256=fingerprint,
        stage=stage,
        algorithm_version=__version__,
        outputs=((role, payload_path),),
        upstream_artifact_ids=tuple(upstreams),
    )
    path = root / f"{payload_path.name}.{stage}.artifact.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")
    return path, artifact


class V4EvidenceFusionEndToEndTests(unittest.TestCase):
    def test_editor_and_asr_artifacts_fuse_without_timeline_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task_root = root / "private" / "fusion-task"
            input_dir = task_root / "input"
            qa_dir = task_root / "qa"
            lyrics_dir = input_dir / "lyrics"
            for directory in (qa_dir, lyrics_dir):
                directory.mkdir(parents=True, exist_ok=True)

            source_srt = input_dir / "source.srt"
            source_srt.write_text(
                "1\n00:00:05,100 --> 00:00:06,300\neditor private\n",
                encoding="utf-8",
            )
            audio = input_dir / "mix.wav"
            audio.write_bytes(b"synthetic-mix")
            song_list = input_dir / "songs.txt"
            song_list.write_text("00:00 Artist - Song\n", encoding="utf-8")
            (lyrics_dir / "Artist - Song.lrc").write_text(
                "[00:01.00]canonical private\n", encoding="utf-8"
            )
            manifest = build_task_manifest(
                root,
                "fusion-task",
                source_srt=source_srt,
                audio=audio,
                song_list=song_list,
                lyrics_dir=lyrics_dir,
            )
            manifest_path = qa_dir / "task_manifest.json"
            write_json_atomic(manifest_path, manifest)
            fingerprint = manifest["task_fingerprint_sha256"]

            canonical_text = "canonical private"
            import hashlib
            canonical_sha = hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()
            timeline = {
                "schema_version": "1.0",
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "occurrence_id": "occ-1",
                "track_id": "track-1",
                "result": {
                    "occurrence_id": "occ-1",
                    "track_id": "track-1",
                    "ordinal": 1,
                    "language_profile": "en",
                    "canonical_selection_sha256": "a" * 64,
                    "window": {"start_ms": 0, "end_ms": 10000},
                    "line_count": 1,
                    "lines": [
                        {
                            "canonical_line_index": 0,
                            "text": canonical_text,
                            "source_start_ms": 1000,
                            "source_end_ms": 2200,
                            "mix_start_ms": 5000,
                            "mix_end_ms": 6200,
                        }
                    ],
                },
            }
            timeline_path = root / "timeline.json"
            timeline_path.write_text(json.dumps(timeline), encoding="utf-8")
            timeline_artifact_path, timeline_artifact = write_artifact(
                root,
                payload_path=timeline_path,
                stage="canonical_timeline_projection",
                role="canonical_timeline",
                fingerprint=fingerprint,
            )

            run_payload = {
                "schema_version": "1.0",
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "status": "ready_for_render",
                "legacy_fallback_used": False,
                "issues": [],
                "occurrences": [
                    {
                        "occurrence_id": "occ-1",
                        "track_id": "track-1",
                        "ordinal": 1,
                        "timeline_path": str(timeline_path),
                        "timeline_artifact_path": str(timeline_artifact_path),
                    }
                ],
            }
            run_path = root / "run.json"
            run_path.write_text(json.dumps(run_payload), encoding="utf-8")
            run_artifact_path, run_artifact = write_artifact(
                root,
                payload_path=run_path,
                stage="production_orchestration",
                role="v4_production_run",
                fingerprint=fingerprint,
                upstreams=(timeline_artifact["artifact_id"],),
            )

            editor_payload = {
                "schema_version": "1.0",
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "mode": "shadow_only",
                "authority": {"automatic_timing_change_allowed": False},
                "source_run_artifact_id": run_artifact["artifact_id"],
                "occurrences": [
                    {
                        "occurrence_id": "occ-1",
                        "lines": [
                            {
                                "canonical_line_index": 0,
                                "canonical_text_sha256": canonical_sha,
                                "best_editor_cue_number": 1,
                                "suggested_onset_delta_ms": 100,
                                "suggested_offset_delta_ms": 100,
                                "best_candidate_margin_uncalibrated": 0.2,
                                "candidates": [{"timing_support_score": 0.9}],
                            }
                        ],
                    }
                ],
            }
            editor_path = root / "editor.json"
            editor_path.write_text(json.dumps(editor_payload), encoding="utf-8")
            editor_artifact_path, editor_artifact = write_artifact(
                root,
                payload_path=editor_path,
                stage="editor_evidence_shadow",
                role="editor_evidence",
                fingerprint=fingerprint,
                upstreams=(run_artifact["artifact_id"], timeline_artifact["artifact_id"]),
            )

            asr_payload = {
                "schema_version": "1.0",
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "backend": "faster_whisper",
                "source_run_artifact_id": run_artifact["artifact_id"],
                "jobs": [
                    {
                        "job_id": "job-1",
                        "occurrence_id": "occ-1",
                        "canonical_line_index": 0,
                        "canonical_text_support_score": 0.9,
                        "language_probability": 0.95,
                        "segments": [{"start_ms": 5120, "end_ms": 6310}],
                    }
                ],
            }
            asr_path = root / "asr.json"
            asr_path.write_text(json.dumps(asr_payload), encoding="utf-8")
            asr_artifact_path, asr_artifact = write_artifact(
                root,
                payload_path=asr_path,
                stage="asr_evidence_local",
                role="asr_evidence",
                fingerprint=fingerprint,
                upstreams=(run_artifact["artifact_id"], timeline_artifact["artifact_id"]),
            )

            output = root / "fusion.json"
            output_artifact = root / "fusion.artifact.json"
            result = run_command(
                "--task-manifest", str(manifest_path),
                "--run", str(run_path),
                "--run-artifact", str(run_artifact_path),
                "--editor-evidence", str(editor_path),
                "--editor-evidence-artifact", str(editor_artifact_path),
                "--asr-evidence", str(asr_path),
                "--asr-evidence-artifact", str(asr_artifact_path),
                "--out", str(output),
                "--artifact-out", str(output_artifact),
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            fusion = json.loads(output.read_text(encoding="utf-8"))
            artifact = json.loads(output_artifact.read_text(encoding="utf-8"))
            self.assertEqual(fusion["lines"][0]["shadow_level"], "HIGH")
            self.assertFalse(fusion["release_gate_eligible"])
            self.assertFalse(fusion["automatic_timing_change_allowed"])
            serialized = json.dumps(fusion, ensure_ascii=False)
            self.assertNotIn("canonical private", serialized)
            self.assertNotIn("editor private", serialized)
            self.assertEqual(artifact["stage"], "evidence_fusion_shadow")
            for expected in (
                run_artifact["artifact_id"],
                timeline_artifact["artifact_id"],
                editor_artifact["artifact_id"],
                asr_artifact["artifact_id"],
            ):
                self.assertIn(expected, artifact["upstream_artifact_ids"])

            # Auxiliary evidence from another run must fail before fusion.
            asr_payload["source_run_artifact_id"] = "another-run"
            asr_path.write_text(json.dumps(asr_payload), encoding="utf-8")
            bad = run_command(
                "--task-manifest", str(manifest_path),
                "--run", str(run_path),
                "--run-artifact", str(run_artifact_path),
                "--asr-evidence", str(asr_path),
                "--asr-evidence-artifact", str(asr_artifact_path),
                "--out", str(root / "bad.json"),
                "--artifact-out", str(root / "bad.artifact.json"),
            )
            self.assertNotEqual(bad.returncode, 0)


if __name__ == "__main__":
    unittest.main()
