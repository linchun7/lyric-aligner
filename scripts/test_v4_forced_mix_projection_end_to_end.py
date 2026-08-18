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
SCRIPT = ROOT / "scripts" / "v4_project_forced_alignment.py"


def run_command(*args: str):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def write_artifact(root, payload, stage, role, fingerprint, upstreams=()):
    value = build_artifact_manifest(
        task_fingerprint_sha256=fingerprint,
        stage=stage,
        algorithm_version=__version__,
        outputs=((role, payload),),
        upstream_artifact_ids=tuple(upstreams),
    )
    path = root / f"{payload.name}.{stage}.artifact.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path, value


class V4ForcedMixProjectionEndToEndTests(unittest.TestCase):
    def test_continuous_mapping_artifact_projects_source_boundaries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task_root = root / "private" / "forced-projection-task"
            input_dir = task_root / "input"
            qa_dir = task_root / "qa"
            lyrics_dir = input_dir / "lyrics"
            qa_dir.mkdir(parents=True)
            lyrics_dir.mkdir(parents=True)
            source_srt = input_dir / "source.srt"
            source_srt.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\nplaceholder\n",
                encoding="utf-8",
            )
            audio = input_dir / "mix.wav"
            audio.write_bytes(b"mix")
            songs = input_dir / "songs.txt"
            songs.write_text("00:00 Artist - Song\n", encoding="utf-8")
            (lyrics_dir / "Artist - Song.lrc").write_text(
                "[00:01.00]private lyric\n", encoding="utf-8"
            )
            manifest = build_task_manifest(
                root,
                "forced-projection-task",
                source_srt=source_srt,
                audio=audio,
                song_list=songs,
                lyrics_dir=lyrics_dir,
            )
            manifest_path = qa_dir / "task_manifest.json"
            write_json_atomic(manifest_path, manifest)
            fingerprint = manifest["task_fingerprint_sha256"]

            coarse = {
                "schema_version": "1.1",
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "occurrence_id": "occ-1",
                "track_id": "track-1",
                "result": {
                    "timewarp": {
                        "selection": "AFFINE",
                        "blocked": False,
                        "mapping": {
                            "intercept": 0.0,
                            "base_slope": 2.0,
                            "breakpoints": [],
                            "slope_deltas": [],
                        },
                    }
                },
            }
            coarse_path = root / "coarse.json"
            coarse_path.write_text(json.dumps(coarse), encoding="utf-8")
            coarse_artifact_path, coarse_artifact = write_artifact(
                root,
                coarse_path,
                "coarse_audio_alignment",
                "coarse_alignment",
                fingerprint,
            )

            run = {
                "schema_version": "1.0",
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "status": "ready",
                "occurrences": [
                    {
                        "occurrence_id": "occ-1",
                        "ordinal": 1,
                        "coarse_path": str(coarse_path),
                        "coarse_artifact_path": str(coarse_artifact_path),
                        "fine_path": None,
                        "fine_artifact_path": None,
                    },
                    {
                        "occurrence_id": "occ-unrelated-blocked",
                        "ordinal": 2,
                        "status": "blocked",
                        "coarse_path": None,
                        "coarse_artifact_path": None,
                        "fine_path": None,
                        "fine_artifact_path": None,
                    },
                ],
                "issues": [],
            }
            run_path = root / "run.json"
            run_path.write_text(json.dumps(run), encoding="utf-8")
            run_artifact_path, run_artifact = write_artifact(
                root,
                run_path,
                "production_orchestration",
                "v4_production_run",
                fingerprint,
                upstreams=(coarse_artifact["artifact_id"],),
            )

            forced = {
                "schema_version": "1.0",
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "backend": "external_forced_aligner",
                "source_run_artifact_id": run_artifact["artifact_id"],
                "jobs": [
                    {
                        "job_id": "job-1",
                        "occurrence_id": "occ-1",
                        "track_id": "track-1",
                        "ordinal": 1,
                        "canonical_line_index": 0,
                        "canonical_text_sha256": "a" * 64,
                        "source_audio_sha256": "b" * 64,
                        "source_window_ms": [0, 3000],
                        "line_source_start_ms": 1000,
                        "line_source_end_ms": 2000,
                        "line_confidence": 0.9,
                        "backend_id": "fake",
                        "backend_version": "1",
                        "model_id": "model",
                        "model_revision": "rev",
                        "spans": [],
                    }
                ],
            }
            forced_path = root / "forced.json"
            forced_path.write_text(json.dumps(forced), encoding="utf-8")
            forced_artifact_path, forced_artifact = write_artifact(
                root,
                forced_path,
                "source_forced_alignment_evidence",
                "forced_alignment_evidence",
                fingerprint,
                upstreams=(run_artifact["artifact_id"],),
            )

            output = root / "projected.json"
            output_artifact = root / "projected.artifact.json"
            result = run_command(
                "--task-manifest", str(manifest_path),
                "--forced-evidence", str(forced_path),
                "--forced-evidence-artifact", str(forced_artifact_path),
                "--run", str(run_path),
                "--run-artifact", str(run_artifact_path),
                "--out", str(output),
                "--artifact-out", str(output_artifact),
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            payload = json.loads(output.read_text(encoding="utf-8"))
            artifact = json.loads(output_artifact.read_text(encoding="utf-8"))
            self.assertEqual(payload["mode"], "forced_alignment_mix_projection")
            self.assertEqual(payload["jobs"][0]["mix_start_ms"], 500)
            self.assertEqual(payload["jobs"][0]["mix_end_ms"], 1000)
            self.assertEqual(
                payload["mapping_sources_by_occurrence"], {"occ-1": "coarse"}
            )
            self.assertEqual(
                artifact["normalized_config"]["mapping_scope"],
                "forced_evidence_occurrences_only",
            )
            self.assertEqual(artifact["stage"], "forced_alignment_mix_projection")
            self.assertIn(
                forced_artifact["artifact_id"], artifact["upstream_artifact_ids"]
            )
            self.assertIn(
                coarse_artifact["artifact_id"], artifact["upstream_artifact_ids"]
            )
            self.assertIn(
                run_artifact["artifact_id"], artifact["upstream_artifact_ids"]
            )
            self.assertNotIn("private lyric", json.dumps(payload))

            foreign_coarse = dict(coarse)
            foreign_coarse_path = root / "foreign-coarse.json"
            foreign_coarse_path.write_text(json.dumps(foreign_coarse), encoding="utf-8")
            foreign_artifact_path, _ = write_artifact(
                root,
                foreign_coarse_path,
                "coarse_audio_alignment",
                "coarse_alignment",
                fingerprint,
            )
            run["occurrences"][0]["coarse_path"] = str(foreign_coarse_path)
            run["occurrences"][0]["coarse_artifact_path"] = str(
                foreign_artifact_path
            )
            run_path.write_text(json.dumps(run), encoding="utf-8")
            bad = run_command(
                "--task-manifest", str(manifest_path),
                "--forced-evidence", str(forced_path),
                "--forced-evidence-artifact", str(forced_artifact_path),
                "--run", str(run_path),
                "--run-artifact", str(run_artifact_path),
                "--out", str(root / "bad.json"),
                "--artifact-out", str(root / "bad.artifact.json"),
            )
            self.assertNotEqual(bad.returncode, 0)


if __name__ == "__main__":
    unittest.main()
