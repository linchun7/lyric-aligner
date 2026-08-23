import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lyric_aligner.io.materializer_path_safety import validate_materializer_preflight
from lyric_aligner.io.path_safety import PathCollisionError
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


def build_task(root: Path):
    task_root = root / "private" / "materializer-safety"
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
    mix.write_bytes(b"synthetic-materializer-mix")
    songs = input_dir / "songs.txt"
    songs.write_text("00:00 Artist - Song\n", encoding="utf-8")
    (lyrics_dir / "Artist - Song.lrc").write_text(
        "[00:00.00]line\n", encoding="utf-8"
    )
    (source_dir / "Artist - Song.wav").write_bytes(b"synthetic-source")

    manifest = build_task_manifest(
        root,
        "materializer-safety",
        source_srt=source_srt,
        audio=mix,
        song_list=songs,
        lyrics_dir=lyrics_dir,
        source_audio_dir=source_dir,
    )
    manifest_path = qa_dir / "task_manifest.json"
    write_json_atomic(manifest_path, manifest)
    return manifest, manifest_path


class V4MaterializerPathSafetyTests(unittest.TestCase):
    def test_output_tree_cannot_contain_declared_lineage_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, manifest_path = build_task(root)
            tree = root / "materialized"
            lineage = tree / "old" / "coarse.json"
            with self.assertRaisesRegex(PathCollisionError, "contains input"):
                validate_materializer_preflight(
                    manifest_path=manifest_path,
                    manifest=manifest,
                    direct_inputs={"run": root / "reviewed.json"},
                    lineage_payloads={
                        "run": {"occurrences": [{"coarse_path": str(lineage)}]}
                    },
                    output_dir=tree,
                    outputs={"run_out": root / "rebuilt.json"},
                )

    def test_output_tree_cannot_live_inside_task_input_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, manifest_path = build_task(root)
            lyrics_path = root / "private" / "materializer-safety" / "input" / "lyrics"
            with self.assertRaisesRegex(PathCollisionError, "inside input directory"):
                validate_materializer_preflight(
                    manifest_path=manifest_path,
                    manifest=manifest,
                    direct_inputs={"run": root / "reviewed.json"},
                    lineage_payloads={"run": {}},
                    output_dir=lyrics_path / "generated",
                    outputs={"run_out": root / "rebuilt.json"},
                )

    def test_three_public_materializers_fail_before_overwriting_direct_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, manifest_path = build_task(root)
            tree = root / "stage"
            tree.mkdir()
            reviewed = tree / "reviewed.json"
            reviewed.write_text(json.dumps({"occurrences": []}), encoding="utf-8")
            cut = tree / "cut.json"
            cut.write_text(json.dumps({"occurrences": []}), encoding="utf-8")
            overlap = tree / "overlap.json"
            overlap.write_text(json.dumps({"occurrences": []}), encoding="utf-8")
            before = {
                path: path.read_bytes() for path in (reviewed, cut, overlap)
            }

            common = [
                "--task-manifest",
                str(manifest_path),
                "--track-assets",
                str(root / "track_assets.json"),
                "--asset-artifact",
                str(root / "track_assets.artifact.json"),
                "--out-dir",
                str(tree),
                "--out",
                str(root / "out.json"),
                "--artifact-out",
                str(root / "out.artifact.json"),
            ]
            commands = [
                [
                    sys.executable,
                    str(ROOT / "scripts" / "v4_rebuild_cut.py"),
                    "--run",
                    str(reviewed),
                    "--run-artifact",
                    str(root / "review.artifact.json"),
                    *common,
                ],
                [
                    sys.executable,
                    str(ROOT / "scripts" / "v4_recompose_overlap.py"),
                    "--run",
                    str(reviewed),
                    "--run-artifact",
                    str(root / "review.artifact.json"),
                    *common,
                ],
                [
                    sys.executable,
                    str(ROOT / "scripts" / "v4_compose_materializations.py"),
                    "--cut-run",
                    str(cut),
                    "--cut-artifact",
                    str(root / "cut.artifact.json"),
                    "--overlap-run",
                    str(overlap),
                    "--overlap-artifact",
                    str(root / "overlap.artifact.json"),
                    *common,
                ],
            ]
            for command in commands:
                result = run_command(command)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("materialization tree contains input", result.stderr)

            for path, payload in before.items():
                self.assertEqual(path.read_bytes(), payload)
            self.assertFalse((tree / "left.fine.json").exists())

    def test_public_materializer_help_is_preserved(self):
        for name in (
            "v4_rebuild_cut.py",
            "v4_recompose_overlap.py",
            "v4_compose_materializations.py",
        ):
            result = run_command([sys.executable, str(ROOT / "scripts" / name), "--help"])
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertIn("--out-dir", result.stdout)


if __name__ == "__main__":
    unittest.main()
