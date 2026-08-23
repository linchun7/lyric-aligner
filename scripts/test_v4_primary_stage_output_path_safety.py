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
from lyric_aligner.io.stage_writer_path_safety import validate_primary_stage_writer_from_argv


class V4PrimaryStageOutputPathSafetyTests(unittest.TestCase):
    def _fixture(self, temporary: str) -> dict[str, Path]:
        repository = Path(temporary) / "fixture-repository"
        task_root = repository / "private" / "fixture"
        input_root = task_root / "input"
        lyrics_dir = input_root / "lyrics"
        source_dir = input_root / "source"
        lyrics_dir.mkdir(parents=True)
        source_dir.mkdir(parents=True)

        source_srt = input_root / "source.srt"
        mix_audio = input_root / "mix.wav"
        song_list = input_root / "songs.txt"
        source_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8")
        mix_audio.write_bytes(b"fixture-mix")
        song_list.write_text("song\n", encoding="utf-8")
        (lyrics_dir / "song.lrc").write_text("[00:00.00]hello\n", encoding="utf-8")
        (source_dir / "song.wav").write_bytes(b"fixture-source")

        manifest = task_contract.build_task_manifest(
            repository,
            "fixture",
            source_srt=source_srt,
            audio=mix_audio,
            song_list=song_list,
            lyrics_dir=lyrics_dir,
            source_audio_dir=source_dir,
        )
        manifest_path = task_root / "qa" / "task_manifest.json"
        manifest_path.parent.mkdir(parents=True)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.assertEqual(task_contract.verify_manifest_inputs(manifest_path, manifest), [])

        upstream = Path(temporary) / "upstream"
        upstream.mkdir()
        paths = {
            "track_assets": upstream / "track_assets.json",
            "asset_artifact": upstream / "track_assets.artifact.json",
            "coarse": upstream / "coarse.json",
            "coarse_artifact": upstream / "coarse.artifact.json",
            "right_coarse": upstream / "right.coarse.json",
            "right_artifact": upstream / "right.coarse.artifact.json",
        }
        for path in paths.values():
            path.write_text("{}\n", encoding="utf-8")

        return {
            "repository": repository,
            "task_root": task_root,
            "lyrics_dir": lyrics_dir,
            "source_dir": source_dir,
            "song_list": song_list,
            "mix_audio": mix_audio,
            "manifest": manifest_path,
            **paths,
        }

    def _run(self, script: str, args: list[str]) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [sys.executable, str(SCRIPTS / script), *args],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def _base_args(self, fixture: dict[str, Path]) -> dict[str, list[str]]:
        manifest = str(fixture["manifest"])
        return {
            "v4_resolve_assets.py": [
                "--task-manifest", manifest,
                "--song-list", str(fixture["song_list"]),
                "--lyrics-dir", str(fixture["lyrics_dir"]),
                "--source-dir", str(fixture["source_dir"]),
            ],
            "v4_coarse_align.py": [
                "--task-manifest", manifest,
                "--mix-audio", str(fixture["mix_audio"]),
                "--track-assets", str(fixture["track_assets"]),
                "--asset-artifact", str(fixture["asset_artifact"]),
            ],
            "v4_fine_align.py": [
                "--task-manifest", manifest,
                "--mix-audio", str(fixture["mix_audio"]),
                "--track-assets", str(fixture["track_assets"]),
                "--asset-artifact", str(fixture["asset_artifact"]),
                "--coarse", str(fixture["coarse"]),
                "--coarse-artifact", str(fixture["coarse_artifact"]),
            ],
            "v4_probe_transition.py": [
                "--task-manifest", manifest,
                "--track-assets", str(fixture["track_assets"]),
                "--asset-artifact", str(fixture["asset_artifact"]),
                "--left-coarse", str(fixture["coarse"]),
                "--left-artifact", str(fixture["coarse_artifact"]),
                "--right-coarse", str(fixture["right_coarse"]),
                "--right-artifact", str(fixture["right_artifact"]),
            ],
        }

    def test_all_primary_stage_entrypoints_reject_output_inside_task_input_before_writing(self):
        for script in (
            "v4_resolve_assets.py",
            "v4_coarse_align.py",
            "v4_fine_align.py",
            "v4_probe_transition.py",
        ):
            with self.subTest(script=script), tempfile.TemporaryDirectory() as temporary:
                fixture = self._fixture(temporary)
                unsafe_out = fixture["lyrics_dir"] / f"{script}.json"
                artifact_out = Path(temporary) / "safe" / f"{script}.artifact.json"
                result = self._run(
                    script,
                    [
                        *self._base_args(fixture)[script],
                        "--out", str(unsafe_out),
                        "--artifact-out", str(artifact_out),
                    ],
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("inside input directory", result.stderr)
                self.assertFalse(unsafe_out.exists())
                self.assertFalse(artifact_out.exists())

    def test_coarse_cache_tree_cannot_enter_or_contain_task_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._fixture(temporary)
            safe_out = Path(temporary) / "safe" / "coarse.json"
            safe_artifact = Path(temporary) / "safe" / "coarse.artifact.json"
            base = self._base_args(fixture)["v4_coarse_align.py"]

            with self.assertRaisesRegex(ValueError, "inside input directory"):
                validate_primary_stage_writer_from_argv(
                    [
                        *base,
                        "--feature-cache-dir", str(fixture["lyrics_dir"] / "cache"),
                        "--out", str(safe_out),
                        "--artifact-out", str(safe_artifact),
                    ],
                    stage="coarse_align",
                )

            with self.assertRaisesRegex(ValueError, "materialization tree contains input"):
                validate_primary_stage_writer_from_argv(
                    [
                        *base,
                        "--feature-cache-dir", str(fixture["task_root"]),
                        "--out", str(safe_out),
                        "--artifact-out", str(safe_artifact),
                    ],
                    stage="coarse_align",
                )

    def test_coarse_default_cache_tree_is_checked(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._fixture(temporary)
            run_root = Path(temporary) / "run"
            track_assets = run_root / "cache" / "features" / "track_assets.json"
            track_assets.parent.mkdir(parents=True)
            track_assets.write_text("{}\n", encoding="utf-8")
            out = run_root / "primary" / "coarse.json"
            artifact_out = Path(temporary) / "safe" / "coarse.artifact.json"

            with self.assertRaisesRegex(
                ValueError,
                "materialization tree contains input cli_track_assets",
            ):
                validate_primary_stage_writer_from_argv(
                    [
                        "--task-manifest", str(fixture["manifest"]),
                        "--mix-audio", str(fixture["mix_audio"]),
                        "--track-assets", str(track_assets),
                        "--asset-artifact", str(fixture["asset_artifact"]),
                        "--out", str(out),
                        "--artifact-out", str(artifact_out),
                    ],
                    stage="coarse_align",
                )

    def test_recursive_declared_lineage_paths_are_protected(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._fixture(temporary)
            declared = Path(temporary) / "declared" / "protected.json"
            declared.parent.mkdir(parents=True)
            fixture["track_assets"].write_text(
                json.dumps({"nested": {"source_path": str(declared)}}) + "\n",
                encoding="utf-8",
            )
            artifact_out = Path(temporary) / "safe" / "coarse.artifact.json"

            with self.assertRaisesRegex(
                ValueError,
                "collides with input cli_track_assets.nested.source_path",
            ):
                validate_primary_stage_writer_from_argv(
                    [
                        *self._base_args(fixture)["v4_coarse_align.py"],
                        "--out", str(declared),
                        "--artifact-out", str(artifact_out),
                    ],
                    stage="coarse_align",
                )

    def test_primary_stage_outputs_must_be_pairwise_distinct(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._fixture(temporary)
            shared = Path(temporary) / "safe" / "shared.json"
            with self.assertRaisesRegex(ValueError, "share the same path"):
                validate_primary_stage_writer_from_argv(
                    [
                        *self._base_args(fixture)["v4_resolve_assets.py"],
                        "--out", str(shared),
                        "--artifact-out", str(shared),
                    ],
                    stage="resolve_assets",
                )


if __name__ == "__main__":
    unittest.main()
