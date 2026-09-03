from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import task_contract
from lyric_aligner.contracts.run_config import (
    RUN_CONFIG_FILENAME,
    build_run_config,
    expand_run_config_argv,
    load_run_config,
    strip_run_config_control_argv,
    write_run_config_atomic,
)
from lyric_aligner.io.run_output_path_safety import validate_run_output_tree_from_argv


class V4RunConfigTests(unittest.TestCase):
    def _fixture(self, temporary: str) -> tuple[Path, Path, Path]:
        repository = Path(temporary) / "repo"
        input_root = repository / "private" / "fixture" / "input"
        qa_root = repository / "private" / "fixture" / "qa"
        lyrics = input_root / "lyrics"
        source_audio = input_root / "source-audio"
        lyrics.mkdir(parents=True)
        source_audio.mkdir(parents=True)
        qa_root.mkdir(parents=True)
        source_srt = input_root / "source.srt"
        mix = input_root / "mix.wav"
        songs = input_root / "songs.txt"
        source_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8")
        mix.write_bytes(b"mix")
        songs.write_text("00:00 Artist - Song\n", encoding="utf-8")
        (lyrics / "song.lrc").write_text("[00:00.00]hello\n", encoding="utf-8")
        (source_audio / "song.wav").write_bytes(b"source")
        manifest = task_contract.build_task_manifest(
            repository,
            "fixture",
            source_srt=source_srt,
            audio=mix,
            song_list=songs,
            lyrics_dir=lyrics,
            source_audio_dir=source_audio,
        )
        manifest_path = qa_root / "task_manifest.json"
        task_contract.write_json_atomic(manifest_path, manifest)
        language_map = qa_root / "language_map.json"
        language_map.write_text('{"Artist - Song":"en"}\n', encoding="utf-8")
        return repository, manifest_path, language_map

    def test_task_local_config_auto_expands_and_control_flag_is_stripped(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository, manifest_path, language_map = self._fixture(temporary)
            manifest = task_contract.load_task_manifest(manifest_path)
            config = build_run_config(
                repository,
                manifest["task_fingerprint_sha256"],
                language_map=language_map,
            )
            config_path = manifest_path.parent / RUN_CONFIG_FILENAME
            write_run_config_atomic(config_path, config)

            argv = expand_run_config_argv(
                [
                    "--task-manifest",
                    str(manifest_path),
                    "--out-dir",
                    str(repository / "output" / "fixture"),
                ],
                repository_root=repository,
            )
            self.assertIn("--run-config", argv)
            self.assertIn("--language-map", argv)
            language_index = argv.index("--language-map")
            self.assertEqual(Path(argv[language_index + 1]), language_map.resolve())
            stripped = strip_run_config_control_argv(argv)
            self.assertNotIn("--run-config", stripped)
            self.assertIn("--language-map", stripped)

    def test_config_rejects_mutated_semantic_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository, manifest_path, language_map = self._fixture(temporary)
            manifest = task_contract.load_task_manifest(manifest_path)
            config_path = manifest_path.parent / RUN_CONFIG_FILENAME
            write_run_config_atomic(
                config_path,
                build_run_config(
                    repository,
                    manifest["task_fingerprint_sha256"],
                    language_map=language_map,
                ),
            )
            language_map.write_text('{"Artist - Song":"zh"}\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "content differs from recorded value"):
                expand_run_config_argv(
                    ["--task-manifest", str(manifest_path), "--out-dir", str(repository / "out")],
                    repository_root=repository,
                )

    def test_config_rejects_manual_semantic_override_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository, manifest_path, language_map = self._fixture(temporary)
            other = manifest_path.parent / "other_language_map.json"
            other.write_text('{"Artist - Song":"en"}\n', encoding="utf-8")
            manifest = task_contract.load_task_manifest(manifest_path)
            write_run_config_atomic(
                manifest_path.parent / RUN_CONFIG_FILENAME,
                build_run_config(
                    repository,
                    manifest["task_fingerprint_sha256"],
                    language_map=language_map,
                ),
            )
            with self.assertRaisesRegex(ValueError, "differs from task run config"):
                expand_run_config_argv(
                    [
                        "--task-manifest",
                        str(manifest_path),
                        "--out-dir",
                        str(repository / "out"),
                        "--language-map",
                        str(other),
                    ],
                    repository_root=repository,
                )

    def test_run_config_itself_is_protected_from_output_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository, manifest_path, language_map = self._fixture(temporary)
            manifest = task_contract.load_task_manifest(manifest_path)
            config_path = manifest_path.parent / RUN_CONFIG_FILENAME
            write_run_config_atomic(
                config_path,
                build_run_config(
                    repository,
                    manifest["task_fingerprint_sha256"],
                    language_map=language_map,
                ),
            )
            argv = expand_run_config_argv(
                [
                    "--task-manifest",
                    str(manifest_path),
                    "--out-dir",
                    str(manifest_path.parent),
                ],
                repository_root=repository,
            )
            with self.assertRaisesRegex(ValueError, "cli_run_config"):
                validate_run_output_tree_from_argv(argv)

    def test_init_cli_creates_and_requires_intentional_replace(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository, manifest_path, language_map = self._fixture(temporary)
            command = [
                sys.executable,
                str(SCRIPTS / "init_v4_run_config.py"),
                "--task-manifest",
                str(manifest_path),
                "--language-map",
                str(language_map),
            ]
            first = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
            self.assertEqual(first.returncode, 0, first.stderr)
            output = json.loads(first.stdout)
            self.assertTrue(output["changed"])
            config_path = manifest_path.parent / RUN_CONFIG_FILENAME
            self.assertTrue(config_path.is_file())
            payload = load_run_config(
                config_path,
                repository_root=repository,
                expected_task_fingerprint_sha256=task_contract.load_task_manifest(manifest_path)[
                    "task_fingerprint_sha256"
                ],
            )
            self.assertIsNotNone(payload["semantic_inputs"]["language_map"])

            other = manifest_path.parent / "other_language_map.json"
            other.write_text('{"Artist - Song":"zh"}\n', encoding="utf-8")
            changed_command = [
                sys.executable,
                str(SCRIPTS / "init_v4_run_config.py"),
                "--task-manifest",
                str(manifest_path),
                "--language-map",
                str(other),
            ]
            blocked = subprocess.run(
                changed_command, cwd=ROOT, capture_output=True, text=True, check=False
            )
            self.assertEqual(blocked.returncode, 2)
            self.assertIn("--replace", blocked.stderr)
            replaced = subprocess.run(
                [*changed_command, "--replace"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(replaced.returncode, 0, replaced.stderr)
            self.assertTrue(json.loads(replaced.stdout)["changed"])

            other.write_text('{"Artist - Song":"ja"}\n', encoding="utf-8")
            stale = subprocess.run(
                changed_command,
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(stale.returncode, 2)
            self.assertIn("content differs from recorded value", stale.stderr)
            rebound = subprocess.run(
                [*changed_command, "--replace"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(rebound.returncode, 0, rebound.stderr)
            self.assertTrue(json.loads(rebound.stdout)["changed"])
            rebound_payload = load_run_config(
                config_path,
                repository_root=repository,
                expected_task_fingerprint_sha256=task_contract.load_task_manifest(manifest_path)[
                    "task_fingerprint_sha256"
                ],
            )
            self.assertEqual(
                rebound_payload["semantic_inputs"]["language_map"]["sha256"],
                build_run_config(
                    repository,
                    task_contract.load_task_manifest(manifest_path)[
                        "task_fingerprint_sha256"
                    ],
                    language_map=other,
                )["semantic_inputs"]["language_map"]["sha256"],
            )


if __name__ == "__main__":
    unittest.main()
