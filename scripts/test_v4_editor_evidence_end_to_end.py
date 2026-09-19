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
SCRIPT = ROOT / "scripts" / "v4_editor_evidence.py"


def run_command(*args: str):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


class V4EditorEvidenceEndToEndTests(unittest.TestCase):
    def test_fingerprinted_editor_srt_builds_shadow_artifact_and_tamper_blocks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task_root = root / "private" / "editor-task"
            input_dir = task_root / "input"
            qa_dir = task_root / "qa"
            lyrics_dir = input_dir / "lyrics"
            for directory in (qa_dir, lyrics_dir):
                directory.mkdir(parents=True, exist_ok=True)

            source_srt = input_dir / "source.srt"
            source_srt.write_text(
                "1\n00:00:01,050 --> 00:00:02,150\nannyeong\n",
                encoding="utf-8",
            )
            audio = input_dir / "mix.wav"
            audio.write_bytes(b"synthetic-mix")
            song_list = input_dir / "songs.txt"
            song_list.write_text("00:00 Artist - Song\n", encoding="utf-8")
            (lyrics_dir / "Artist - Song.lrc").write_text(
                "[00:01.00]안녕\n", encoding="utf-8"
            )

            manifest = build_task_manifest(
                root,
                "editor-task",
                source_srt=source_srt,
                audio=audio,
                song_list=song_list,
                lyrics_dir=lyrics_dir,
            )
            manifest_path = qa_dir / "task_manifest.json"
            write_json_atomic(manifest_path, manifest)
            fingerprint = manifest["task_fingerprint_sha256"]

            timeline_payload = {
                "schema_version": "1.0",
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "calibration_profile_version": "profile-r1",
                "calibration_profile_id": "a" * 64,
                "occurrence_id": "occ-1",
                "track_id": "track-1",
                "result": {
                    "occurrence_id": "occ-1",
                    "ordinal": 1,
                    "track_id": "track-1",
                    "artist": "Artist",
                    "title": "Song",
                    "language_profile": "ko",
                    "canonical_selection_sha256": "b" * 64,
                    "window": {"start_ms": 0, "end_ms": 5000},
                    "line_count": 1,
                    "lines": [
                        {
                            "canonical_line_index": 0,
                            "text": "안녕",
                            "timing_format": "line_lrc",
                            "source_start_ms": 1000,
                            "source_end_ms": 2200,
                            "mix_start_ms": 1000,
                            "mix_end_ms": 2200,
                            "end_basis": "synthetic",
                            "tokens": [],
                        }
                    ],
                },
            }
            timeline_path = root / "timeline.json"
            timeline_path.write_text(json.dumps(timeline_payload), encoding="utf-8")
            timeline_artifact = build_artifact_manifest(
                task_fingerprint_sha256=fingerprint,
                stage="canonical_timeline_projection",
                algorithm_version=__version__,
                outputs=(("canonical_timeline", timeline_path),),
            )
            timeline_artifact_path = root / "timeline.artifact.json"
            timeline_artifact_path.write_text(
                json.dumps(timeline_artifact), encoding="utf-8"
            )

            run_payload = {
                "schema_version": "1.0",
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "calibration_profile_version": "profile-r1",
                "calibration_profile_id": "a" * 64,
                "status": "ready_for_render",
                "legacy_fallback_used": False,
                "issues": [],
                "occurrences": [
                    {
                        "occurrence_id": "occ-1",
                        "ordinal": 1,
                        "track_id": "track-1",
                        "timeline_path": str(timeline_path),
                        "timeline_artifact_path": str(timeline_artifact_path),
                        "timeline_stage": "canonical_timeline_projection",
                    }
                ],
                "transitions": [],
            }
            run_path = root / "run.json"
            run_path.write_text(json.dumps(run_payload), encoding="utf-8")
            run_artifact = build_artifact_manifest(
                task_fingerprint_sha256=fingerprint,
                stage="production_orchestration",
                algorithm_version=__version__,
                outputs=(("v4_production_run", run_path),),
                upstream_artifact_ids=(timeline_artifact["artifact_id"],),
            )
            run_artifact_path = root / "run.artifact.json"
            run_artifact_path.write_text(json.dumps(run_artifact), encoding="utf-8")

            evidence_path = root / "editor-evidence.json"
            evidence_artifact_path = root / "editor-evidence.artifact.json"
            result = run_command(
                "--task-manifest",
                str(manifest_path),
                "--run",
                str(run_path),
                "--run-artifact",
                str(run_artifact_path),
                "--out",
                str(evidence_path),
                "--artifact-out",
                str(evidence_artifact_path),
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            artifact = json.loads(
                evidence_artifact_path.read_text(encoding="utf-8")
            )
            self.assertEqual(evidence["mode"], "shadow_only")
            self.assertFalse(
                evidence["authority"]["automatic_timing_change_allowed"]
            )
            line = evidence["occurrences"][0]["lines"][0]
            self.assertEqual(line["best_editor_cue_number"], 1)
            self.assertGreater(
                line["candidates"][0]["phonetic_support_score"], 0.85
            )
            serialized = json.dumps(evidence, ensure_ascii=False)
            self.assertNotIn("안녕", serialized)
            self.assertNotIn("annyeong", serialized)
            self.assertEqual(artifact["stage"], "editor_evidence_shadow")
            self.assertIn(
                run_artifact["artifact_id"], artifact["upstream_artifact_ids"]
            )
            self.assertIn(
                timeline_artifact["artifact_id"],
                artifact["upstream_artifact_ids"],
            )

            # Task fingerprint owns source_srt. Editing it after manifest creation
            # must stop evidence generation instead of silently using new text.
            source_srt.write_text(
                "1\n00:00:01,050 --> 00:00:02,150\ntampered\n",
                encoding="utf-8",
            )
            tampered = run_command(
                "--task-manifest",
                str(manifest_path),
                "--run",
                str(run_path),
                "--run-artifact",
                str(run_artifact_path),
                "--out",
                str(root / "tampered.json"),
                "--artifact-out",
                str(root / "tampered.artifact.json"),
            )
            self.assertNotEqual(tampered.returncode, 0)
            self.assertIn("task manifest validation failed", tampered.stderr)


if __name__ == "__main__":
    unittest.main()
