from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import task_contract
from lyric_aligner.io.run_output_path_safety import validate_run_output_tree_from_argv


class V4RunOutputTreeSafetyTests(unittest.TestCase):
    def _task_fixture(self, temporary: str) -> tuple[Path, Path, Path]:
        repository = Path(temporary) / "fixture-repository"
        task_root = repository / "private" / "fixture"
        input_root = task_root / "input"
        lyrics_dir = input_root / "lyrics"
        lyrics_dir.mkdir(parents=True)

        source_srt = input_root / "source.srt"
        audio = input_root / "mix.wav"
        song_list = input_root / "songs.txt"
        source_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8")
        audio.write_bytes(b"fixture-audio")
        song_list.write_text("song\n", encoding="utf-8")
        (lyrics_dir / "song.lrc").write_text("[00:00.00]hello\n", encoding="utf-8")

        manifest = task_contract.build_task_manifest(
            repository,
            "fixture",
            source_srt=source_srt,
            audio=audio,
            song_list=song_list,
            lyrics_dir=lyrics_dir,
        )
        manifest_path = task_root / "qa" / "task_manifest.json"
        manifest_path.parent.mkdir(parents=True)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.assertEqual(task_contract.verify_manifest_inputs(manifest_path, manifest), [])
        return manifest_path, task_root, lyrics_dir

    def _run_cli(self, script_name: str, manifest_path: Path, out_dir: Path) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / script_name),
                "--task-manifest",
                str(manifest_path),
                "--out-dir",
                str(out_dir),
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_all_public_run_entrypoints_reject_output_inside_task_input_before_writing(self):
        for script_name in ("v4_run.py", "v4_run_optimized.py", "v4_run_legacy.py"):
            with self.subTest(script_name=script_name), tempfile.TemporaryDirectory() as temporary:
                manifest_path, _task_root, lyrics_dir = self._task_fixture(temporary)
                unsafe_out = lyrics_dir / "generated-output"
                result = self._run_cli(script_name, manifest_path, unsafe_out)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("inside input directory", result.stderr)
                self.assertFalse(unsafe_out.exists())
                self.assertFalse((unsafe_out / ".v4-run.lock").exists())

    def test_output_tree_cannot_contain_task_manifest_or_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest_path, task_root, _lyrics_dir = self._task_fixture(temporary)
            with self.assertRaisesRegex(ValueError, "materialization tree contains input"):
                validate_run_output_tree_from_argv(
                    [
                        "--task-manifest",
                        str(manifest_path),
                        "--out-dir",
                        str(task_root),
                    ]
                )

    def test_explicit_cli_config_is_protected_from_output_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest_path, _task_root, _lyrics_dir = self._task_fixture(temporary)
            config_dir = Path(temporary) / "external-config"
            config_dir.mkdir()
            profile = config_dir / "profile.json"
            profile.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "materialization tree contains input cli_profile"):
                validate_run_output_tree_from_argv(
                    [
                        "--task-manifest",
                        str(manifest_path),
                        "--out-dir",
                        str(config_dir),
                        "--profile",
                        str(profile),
                    ]
                )


if __name__ == "__main__":
    unittest.main()
