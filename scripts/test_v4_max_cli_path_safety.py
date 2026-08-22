import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lyric_aligner import __version__
from lyric_aligner.contracts.artifacts import build_artifact_manifest
from lyric_aligner.io.path_safety import PathCollisionError, validate_separate_artifact_paths
from lyric_aligner.io.task_path_safety import protected_task_input_paths
from task_contract import build_task_manifest, write_json_atomic
from v4_validate_release import _load_upstream_artifacts


ROOT = Path(__file__).resolve().parents[1]


def run_command(command):
    return subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def build_task(root: Path):
    task_root = root / "private" / "max-cli-safety"
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
    mix = input_dir / "mix.wav"
    mix.write_bytes(b"synthetic-max-cli-safety-mix")
    song_list = input_dir / "songs.txt"
    song_list.write_text("00:00 Artist - Song\n", encoding="utf-8")
    lyric = lyrics_dir / "Artist - Song.lrc"
    lyric.write_text("[00:00.00]line\n", encoding="utf-8")
    source_audio = source_dir / "Artist - Song.wav"
    source_audio.write_bytes(b"synthetic-source")

    manifest = build_task_manifest(
        root,
        "max-cli-safety",
        source_srt=source_srt,
        audio=mix,
        song_list=song_list,
        lyrics_dir=lyrics_dir,
        source_audio_dir=source_dir,
    )
    manifest_path = qa_dir / "task_manifest.json"
    write_json_atomic(manifest_path, manifest)
    return manifest, manifest_path, source_srt, lyric


def build_review_run(root: Path, manifest: dict):
    run = {
        "schema_version": "1.0",
        "algorithm_version": __version__,
        "task_fingerprint_sha256": manifest["task_fingerprint_sha256"],
        "calibration_profile_version": "profile-test",
        "calibration_profile_id": "p" * 64,
        "status": "review_required",
        "legacy_fallback_used": False,
        "plan": {},
        "occurrences": [{"occurrence_id": "occ-1"}],
        "transitions": [
            {
                "left_occurrence_id": "occ-1",
                "right_occurrence_id": "occ-2",
                "blocked": True,
            }
        ],
        "issues": [
            {
                "kind": "transition",
                "left_occurrence_id": "occ-1",
                "right_occurrence_id": "occ-2",
                "status": "review",
                "reason": "synthetic review candidate",
                "overlap_candidate_count": 1,
            }
        ],
    }
    run_path = root / "v4_run.json"
    run_path.write_text(json.dumps(run), encoding="utf-8")
    artifact = build_artifact_manifest(
        task_fingerprint_sha256=manifest["task_fingerprint_sha256"],
        stage="production_orchestration",
        algorithm_version=__version__,
        outputs=(("v4_production_run", run_path),),
        normalized_config={
            "calibration_profile_version": "profile-test",
            "calibration_profile_id": "p" * 64,
            "legacy_fallback": False,
        },
        upstream_artifact_ids=("asset-artifact", "timeline-artifact"),
    )
    artifact_path = root / "v4_run.artifact.json"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    return run_path, artifact_path


class V4MaxCLIPathSafetyTests(unittest.TestCase):
    def test_task_path_expansion_protects_directory_members(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, manifest_path, _, lyric = build_task(root)
            protected = protected_task_input_paths(
                manifest_path=manifest_path,
                manifest=manifest,
                repository_root=root,
            )
            self.assertIn(lyric.resolve(), {path.resolve() for path in protected.values()})
            with self.assertRaisesRegex(PathCollisionError, "collides with input"):
                validate_separate_artifact_paths(
                    inputs=protected,
                    outputs={"unsafe": lyric},
                )

    def test_new_output_inside_task_input_directory_is_blocked(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, manifest_path, _, lyric = build_task(root)
            protected = protected_task_input_paths(
                manifest_path=manifest_path,
                manifest=manifest,
            )
            lyrics_dir = lyric.parent
            output = lyrics_dir / "new-artifact.json"
            self.assertFalse(output.exists())
            with self.assertRaisesRegex(PathCollisionError, "is inside input directory"):
                validate_separate_artifact_paths(
                    inputs=protected,
                    outputs={"unsafe": output},
                )
            self.assertFalse(output.exists())

    def test_normal_output_outside_task_inputs_remains_allowed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, manifest_path, _, _ = build_task(root)
            protected = protected_task_input_paths(
                manifest_path=manifest_path,
                manifest=manifest,
            )
            validate_separate_artifact_paths(
                inputs=protected,
                outputs={"safe": root / "output" / "artifact.json"},
            )

    def test_review_template_refuses_to_overwrite_production_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, manifest_path, _, _ = build_task(root)
            run_path, artifact_path = build_review_run(root, manifest)
            before = run_path.read_bytes()
            result = run_command(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "v4_review.py"),
                    "template",
                    "--task-manifest",
                    str(manifest_path),
                    "--run",
                    str(run_path),
                    "--run-artifact",
                    str(artifact_path),
                    "--out",
                    str(run_path),
                ]
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("collides with input production_run", result.stderr)
            self.assertEqual(run_path.read_bytes(), before)

    def test_review_apply_refuses_to_overwrite_decisions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, manifest_path, _, _ = build_task(root)
            run_path, artifact_path = build_review_run(root, manifest)
            decisions = root / "decisions.json"
            template = run_command(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "v4_review.py"),
                    "template",
                    "--task-manifest",
                    str(manifest_path),
                    "--run",
                    str(run_path),
                    "--run-artifact",
                    str(artifact_path),
                    "--out",
                    str(decisions),
                ]
            )
            self.assertEqual(template.returncode, 0, msg=template.stderr)
            payload = json.loads(decisions.read_text(encoding="utf-8"))
            payload["review_items"][0]["decision"] = {
                "action": "resolved_clear",
                "rationale": "Synthetic path-safety review decision.",
            }
            decisions.write_text(json.dumps(payload), encoding="utf-8")
            before = decisions.read_bytes()
            result = run_command(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "v4_review.py"),
                    "apply",
                    "--task-manifest",
                    str(manifest_path),
                    "--run",
                    str(run_path),
                    "--run-artifact",
                    str(artifact_path),
                    "--decisions",
                    str(decisions),
                    "--out",
                    str(decisions),
                    "--artifact-out",
                    str(root / "review.artifact.json"),
                ]
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("collides with input review_decisions", result.stderr)
            self.assertEqual(decisions.read_bytes(), before)

    def test_release_manifest_refuses_to_overwrite_task_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, manifest_path, source_srt, _ = build_task(root)
            before = source_srt.read_bytes()
            result = run_command(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "v4_validate_release.py"),
                    "--task-manifest",
                    str(manifest_path),
                    "--final-srt",
                    str(root / "unused.srt"),
                    "--report",
                    str(root / "unused.csv"),
                    "--qa-json",
                    str(root / "unused.qa.json"),
                    "--algorithm-version",
                    "3.9",
                    "--out-manifest",
                    str(source_srt),
                ]
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("collides with input task_source_srt", result.stderr)
            self.assertEqual(source_srt.read_bytes(), before)

    def test_release_rejects_non_object_upstream_config_cleanly(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact_path = root / "bad.artifact.json"
            payload = build_artifact_manifest(
                task_fingerprint_sha256="f" * 64,
                stage="asset_resolution",
                algorithm_version=__version__,
                outputs=(),
            )
            unsigned = {key: value for key, value in payload.items() if key != "artifact_id"}
            unsigned["normalized_config"] = []
            from lyric_aligner.contracts.artifacts import canonical_json_sha256

            malformed = {**unsigned, "artifact_id": canonical_json_sha256(unsigned)}
            artifact_path.write_text(json.dumps(malformed), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid normalized_config"):
                _load_upstream_artifacts(
                    [artifact_path],
                    fingerprint="f" * 64,
                )


if __name__ == "__main__":
    unittest.main()
