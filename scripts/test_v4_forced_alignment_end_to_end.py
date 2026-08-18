import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lyric_aligner import __version__
from lyric_aligner.assets.resolver import resolve_assets
from lyric_aligner.contracts.artifacts import build_artifact_manifest
from task_contract import build_task_manifest, write_json_atomic


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "v4_execute_forced_alignment.py"


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
    payload: Path,
    stage: str,
    role: str,
    fingerprint: str,
    upstreams=(),
):
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


class V4ForcedAlignmentEndToEndTests(unittest.TestCase):
    def test_cli_runs_external_json_protocol_and_keeps_canonical_text_private(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task_root = root / "private" / "forced-task"
            input_dir = task_root / "input"
            qa_dir = task_root / "qa"
            lyrics_dir = input_dir / "lyrics"
            source_dir = input_dir / "source"
            for directory in (qa_dir, lyrics_dir, source_dir):
                directory.mkdir(parents=True, exist_ok=True)

            source_srt = input_dir / "source.srt"
            source_srt.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\nplaceholder\n",
                encoding="utf-8",
            )
            mix_audio = input_dir / "mix.wav"
            mix_audio.write_bytes(b"synthetic-mix")
            song_list = input_dir / "songs.txt"
            song_list.write_text("00:00 Artist - Song\n", encoding="utf-8")
            canonical_text = "hello world"
            lyric = lyrics_dir / "Artist - Song.lrc"
            lyric.write_text(f"[00:01.00]{canonical_text}\n", encoding="utf-8")
            source_audio = source_dir / "Artist - Song.wav"
            source_audio.write_bytes(b"synthetic-source-audio")

            manifest = build_task_manifest(
                root,
                "forced-task",
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
                language_by_track={"Song": "en"},
            )
            assets["algorithm_version"] = __version__
            assets["task_fingerprint_sha256"] = fingerprint
            assets_path = root / "track_assets.json"
            assets_path.write_text(json.dumps(assets), encoding="utf-8")
            assets_artifact_path, assets_artifact = write_artifact(
                root,
                assets_path,
                "asset_resolution",
                "track_assets",
                fingerprint,
            )
            occurrence = assets["occurrences"][0]
            asset = assets["assets"][0]
            occurrence_id = occurrence["occurrence_id"]
            track_id = occurrence["track_id"]
            canonical_sha = hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()

            timeline = {
                "schema_version": "1.0",
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "result": {
                    "occurrence_id": occurrence_id,
                    "track_id": track_id,
                    "ordinal": 1,
                    "language_profile": "en",
                    "canonical_selection_sha256": asset[
                        "canonical_selection_sha256"
                    ],
                    "lines": [
                        {
                            "canonical_line_index": 0,
                            "text": canonical_text,
                            "source_start_ms": 1000,
                            "source_end_ms": 2200,
                            "mix_start_ms": 3000,
                            "mix_end_ms": 4200,
                        }
                    ],
                },
            }
            timeline_path = root / "timeline.json"
            timeline_path.write_text(json.dumps(timeline), encoding="utf-8")
            timeline_artifact_path, timeline_artifact = write_artifact(
                root,
                timeline_path,
                "canonical_timeline_projection",
                "canonical_timeline",
                fingerprint,
            )

            run_payload = {
                "schema_version": "1.0",
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "status": "ready",
                "occurrences": [
                    {
                        "occurrence_id": occurrence_id,
                        "ordinal": 1,
                        "track_id": track_id,
                        "timeline_path": str(timeline_path),
                        "timeline_artifact_path": str(timeline_artifact_path),
                    }
                ],
                "issues": [],
            }
            run_path = root / "run.json"
            run_path.write_text(json.dumps(run_payload), encoding="utf-8")
            run_artifact_path, run_artifact = write_artifact(
                root,
                run_path,
                "production_orchestration",
                "v4_production_run",
                fingerprint,
                upstreams=(
                    assets_artifact["artifact_id"],
                    timeline_artifact["artifact_id"],
                ),
            )

            plan_payload = {
                "schema_version": "1.0",
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "mode": "plan_only",
                "backend_execution_performed": False,
                "source_run_artifact_id": run_artifact["artifact_id"],
                "jobs": [
                    {
                        "job_id": "forced-job-1",
                        "occurrence_id": occurrence_id,
                        "track_id": track_id,
                        "ordinal": 1,
                        "priority": "high",
                        "canonical_line_index": 0,
                        "language_profile": "en",
                        "mix_window_ms": [2500, 4700],
                        "source_window_ms": [500, 2500],
                        "canonical_text_sha256": canonical_sha,
                        "requested_capabilities": [
                            "source_forced_alignment"
                        ],
                        "reasons": ["run_issue:fragment_review"],
                    }
                ],
            }
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps(plan_payload), encoding="utf-8")
            plan_artifact_path, plan_artifact = write_artifact(
                root,
                plan_path,
                "alignment_job_planning",
                "alignment_plan",
                fingerprint,
                upstreams=(
                    run_artifact["artifact_id"],
                    timeline_artifact["artifact_id"],
                ),
            )

            fake_aligner = root / "fake_aligner.py"
            fake_aligner.write_text(
                """
import argparse, json
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--request', required=True)
p.add_argument('--response', required=True)
a = p.parse_args()
request = json.loads(Path(a.request).read_text(encoding='utf-8'))
text = request['canonical_text']
start, end = request['source_window_ms']
response = {
    'protocol_version': request['protocol_version'],
    'job_id': request['job_id'],
    'backend_id': request['backend_id'],
    'backend_version': request['backend_version'],
    'model_id': request['model_id'],
    'model_revision': request['model_revision'],
    'status': 'aligned',
    'source_window_ms': request['source_window_ms'],
    'line_source_start_ms': start + 100,
    'line_source_end_ms': end - 100,
    'line_confidence': 0.91,
    'spans': [{
        'char_start': 0,
        'char_end': len(text),
        'source_start_ms': start + 100,
        'source_end_ms': end - 100,
        'confidence': 0.90,
    }],
}
Path(a.response).write_text(json.dumps(response), encoding='utf-8')
""".strip()
                + "\n",
                encoding="utf-8",
            )
            command = f'"{sys.executable}" "{fake_aligner}"'
            output = root / "forced.json"
            output_artifact = root / "forced.artifact.json"
            result = run_command(
                "--task-manifest", str(manifest_path),
                "--plan", str(plan_path),
                "--plan-artifact", str(plan_artifact_path),
                "--track-assets", str(assets_path),
                "--track-assets-artifact", str(assets_artifact_path),
                "--run", str(run_path),
                "--run-artifact", str(run_artifact_path),
                "--external-command", command,
                "--backend-id", "fake-aligner",
                "--backend-version", "1.0",
                "--model-id", "fake-model",
                "--model-revision", "fake-rev",
                "--out", str(output),
                "--artifact-out", str(output_artifact),
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            payload = json.loads(output.read_text(encoding="utf-8"))
            out_artifact = json.loads(output_artifact.read_text(encoding="utf-8"))
            self.assertTrue(payload["command_invoked"])
            self.assertEqual(payload["job_count"], 1)
            row = payload["jobs"][0]
            self.assertEqual(row["line_source_start_ms"], 600)
            self.assertEqual(row["line_source_end_ms"], 2400)
            self.assertEqual(row["canonical_text_sha256"], canonical_sha)
            self.assertEqual(row["source_audio_sha256"], asset["source_audio_sha256"])
            self.assertEqual(row["model_revision"], "fake-rev")
            serialized = json.dumps(payload)
            self.assertNotIn(canonical_text, serialized)
            self.assertNotIn("hello", serialized)
            self.assertEqual(out_artifact["stage"], "source_forced_alignment_evidence")
            self.assertIn(plan_artifact["artifact_id"], out_artifact["upstream_artifact_ids"])
            self.assertIn(assets_artifact["artifact_id"], out_artifact["upstream_artifact_ids"])
            self.assertIn(run_artifact["artifact_id"], out_artifact["upstream_artifact_ids"])
            self.assertIn(timeline_artifact["artifact_id"], out_artifact["upstream_artifact_ids"])
            self.assertNotIn(command, json.dumps(out_artifact))
            self.assertEqual(
                out_artifact["normalized_config"]["command_sha256"],
                hashlib.sha256(command.encode("utf-8")).hexdigest(),
            )

            missing = run_command(
                "--task-manifest", str(manifest_path),
                "--plan", str(plan_path),
                "--plan-artifact", str(plan_artifact_path),
                "--track-assets", str(assets_path),
                "--track-assets-artifact", str(assets_artifact_path),
                "--run", str(run_path),
                "--run-artifact", str(run_artifact_path),
                "--external-command", "definitely-not-a-real-forced-aligner-command",
                "--backend-id", "missing",
                "--backend-version", "1",
                "--model-id", "missing-model",
                "--model-revision", "missing-rev",
                "--out", str(root / "missing.json"),
                "--artifact-out", str(root / "missing.artifact.json"),
            )
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("executable not found", missing.stderr)


if __name__ == "__main__":
    unittest.main()
