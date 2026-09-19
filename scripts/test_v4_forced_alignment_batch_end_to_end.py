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


class V4ForcedAlignmentBatchEndToEndTests(unittest.TestCase):
    def test_cli_batch_executes_two_jobs_with_one_real_subprocess(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task_root = root / "private" / "forced-batch-task"
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
            texts = ["hello world", "second line"]
            lyric = lyrics_dir / "Artist - Song.lrc"
            lyric.write_text(
                f"[00:01.00]{texts[0]}\n[00:03.00]{texts[1]}\n",
                encoding="utf-8",
            )
            source_audio = source_dir / "Artist - Song.wav"
            source_audio.write_bytes(b"synthetic-source-audio")

            manifest = build_task_manifest(
                root,
                "forced-batch-task",
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
            shas = [hashlib.sha256(text.encode("utf-8")).hexdigest() for text in texts]

            timeline = {
                "schema_version": "1.0",
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "result": {
                    "occurrence_id": occurrence_id,
                    "track_id": track_id,
                    "ordinal": 1,
                    "language_profile": "en",
                    "canonical_selection_sha256": asset["canonical_selection_sha256"],
                    "lines": [
                        {
                            "canonical_line_index": 0,
                            "text": texts[0],
                            "source_start_ms": 1000,
                            "source_end_ms": 2200,
                            "mix_start_ms": 3000,
                            "mix_end_ms": 4200,
                        },
                        {
                            "canonical_line_index": 1,
                            "text": texts[1],
                            "source_start_ms": 3000,
                            "source_end_ms": 4400,
                            "mix_start_ms": 5000,
                            "mix_end_ms": 6400,
                        },
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

            jobs = []
            for index, (source_window, mix_window) in enumerate(
                [([500, 2500], [2500, 4700]), ([2500, 4800], [4500, 6800])]
            ):
                jobs.append(
                    {
                        "job_id": f"forced-job-{index + 1}",
                        "occurrence_id": occurrence_id,
                        "track_id": track_id,
                        "ordinal": 1,
                        "priority": "high",
                        "canonical_line_index": index,
                        "language_profile": "en",
                        "mix_window_ms": mix_window,
                        "source_window_ms": source_window,
                        "canonical_text_sha256": shas[index],
                        "requested_capabilities": ["source_forced_alignment"],
                        "reasons": ["test_batch"],
                    }
                )
            plan_payload = {
                "schema_version": "1.0",
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "mode": "plan_only",
                "backend_execution_performed": False,
                "source_run_artifact_id": run_artifact["artifact_id"],
                "jobs": jobs,
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

            invocation_file = root / "invocations.txt"
            fake_aligner = root / "fake_batch_aligner.py"
            fake_aligner.write_text(
                f"""
import argparse, json
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--batch-request', required=True)
p.add_argument('--batch-response', required=True)
a = p.parse_args()
request = json.loads(Path(a.batch_request).read_text(encoding='utf-8'))
marker = Path({str(invocation_file)!r})
previous = marker.read_text(encoding='utf-8') if marker.exists() else ''
marker.write_text(previous + '1', encoding='utf-8')
rows = []
for job in request['jobs']:
    start, end = job['source_window_ms']
    rows.append({{
        'job_id': job['job_id'],
        'status': 'aligned',
        'source_window_ms': job['source_window_ms'],
        'line_source_start_ms': start + 100,
        'line_source_end_ms': end - 100,
        'line_confidence': 0.92,
        'spans': [],
    }})
response = {{
    'protocol_version': request['protocol_version'],
    'backend_id': request['backend_id'],
    'backend_version': request['backend_version'],
    'model_id': request['model_id'],
    'model_revision': request['model_revision'],
    'status': 'aligned_batch',
    'jobs': rows,
}}
Path(a.batch_response).write_text(json.dumps(response), encoding='utf-8')
""".strip()
                + "\n",
                encoding="utf-8",
            )
            command = f'"{sys.executable}" "{fake_aligner}"'
            output = root / "forced-batch.json"
            output_artifact = root / "forced-batch.artifact.json"
            result = run_command(
                "--task-manifest", str(manifest_path),
                "--plan", str(plan_path),
                "--plan-artifact", str(plan_artifact_path),
                "--track-assets", str(assets_path),
                "--track-assets-artifact", str(assets_artifact_path),
                "--run", str(run_path),
                "--run-artifact", str(run_artifact_path),
                "--external-command", command,
                "--backend-id", "fake-batch-aligner",
                "--backend-version", "1.0",
                "--model-id", "fake-model",
                "--model-revision", "fake-rev",
                "--execution-mode", "batch",
                "--out", str(output),
                "--artifact-out", str(output_artifact),
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            payload = json.loads(output.read_text(encoding="utf-8"))
            artifact = json.loads(output_artifact.read_text(encoding="utf-8"))
            stdout = json.loads(result.stdout)

            self.assertEqual(invocation_file.read_text(encoding="utf-8"), "1")
            self.assertTrue(payload["command_invoked"])
            self.assertEqual(payload["execution_mode"], "batch_subprocess")
            self.assertEqual(payload["batch_protocol_version"], "1.1")
            self.assertEqual(payload["command_invocation_count"], 1)
            self.assertEqual(payload["job_count"], 2)
            self.assertEqual(
                [row["job_id"] for row in payload["jobs"]],
                ["forced-job-1", "forced-job-2"],
            )
            self.assertEqual(stdout["protocol_version"], "1.1")
            self.assertEqual(stdout["execution_mode"], "batch_subprocess")
            self.assertEqual(stdout["command_invocation_count"], 1)
            self.assertEqual(artifact["normalized_config"]["protocol_version"], "1.1")
            self.assertEqual(
                artifact["normalized_config"]["requested_execution_mode"], "batch"
            )
            self.assertEqual(
                artifact["normalized_config"]["execution_mode"], "batch_subprocess"
            )
            self.assertEqual(artifact["evidence"]["command_invocation_count"], 1)
            self.assertEqual(artifact["evidence"]["job_count"], 2)
            serialized = json.dumps(payload)
            self.assertNotIn(texts[0], serialized)
            self.assertNotIn(texts[1], serialized)
            self.assertNotIn(command, json.dumps(artifact))
            for expected in (
                plan_artifact["artifact_id"],
                assets_artifact["artifact_id"],
                run_artifact["artifact_id"],
                timeline_artifact["artifact_id"],
            ):
                self.assertIn(expected, artifact["upstream_artifact_ids"])


if __name__ == "__main__":
    unittest.main()
