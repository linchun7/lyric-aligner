from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

from lyric_aligner.contracts.verification_session import (
    clear_verified_input_session,
    create_verified_input_session,
)
from task_contract import build_task_manifest, write_json_atomic


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "v4_run_optimized_test",
    ROOT / "scripts" / "v4_run_optimized.py",
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load scripts/v4_run_optimized.py")
OPTIMIZER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OPTIMIZER)


class OptimizerSafetyTests(unittest.TestCase):
    def tearDown(self):
        clear_verified_input_session()

    def _task(self, root: Path):
        task = root / "private" / "safety_test"
        input_dir = task / "input"
        lyrics = input_dir / "lyrics"
        sources = input_dir / "source-audio"
        qa = task / "qa"
        for directory in (lyrics, sources, qa):
            directory.mkdir(parents=True, exist_ok=True)
        source_srt = input_dir / "source.srt"
        audio = input_dir / "mix.wav"
        song_list = input_dir / "songs.txt"
        lyric = lyrics / "song.lrc"
        source_audio = sources / "song.wav"
        for path, content in (
            (source_srt, "1\n00:00:00,000 --> 00:00:01,000\nx\n"),
            (audio, "audio"),
            (song_list, "00:00 artist - song\n"),
            (lyric, "[00:00.00]x\n"),
            (source_audio, "source"),
        ):
            path.write_text(content, encoding="utf-8")
        manifest = build_task_manifest(
            root,
            "safety_test",
            source_srt=source_srt,
            audio=audio,
            song_list=song_list,
            lyrics_dir=lyrics,
            source_audio_dir=sources,
        )
        manifest_path = qa / "task_manifest.json"
        write_json_atomic(manifest_path, manifest)
        return manifest_path, manifest, audio, lyrics

    def test_stat_snapshot_detects_file_change_and_directory_membership_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path, manifest, audio, lyrics = self._task(root)
            before = OPTIMIZER.manifest_stat_snapshot(manifest_path, manifest)

            stat = audio.stat()
            os.utime(audio, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
            self.assertNotEqual(
                OPTIMIZER.manifest_stat_snapshot(manifest_path, manifest),
                before,
            )

            os.utime(audio, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            before = OPTIMIZER.manifest_stat_snapshot(manifest_path, manifest)
            (lyrics / "unexpected.lrc").write_text("x", encoding="utf-8")
            self.assertNotEqual(
                OPTIMIZER.manifest_stat_snapshot(manifest_path, manifest),
                before,
            )

    def test_session_payload_does_not_persist_raw_absolute_input_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path, manifest, audio, _ = self._task(root)
            session_path = root / "output" / "session.json"
            create_verified_input_session(
                manifest_path=manifest_path,
                manifest=manifest,
                repository_root=root,
                session_path=session_path,
            )
            serialized = session_path.read_text(encoding="utf-8")
            payload = json.loads(serialized)

            self.assertEqual(payload["schema_version"], "1.1")
            self.assertNotIn(str(root.resolve()), serialized)
            self.assertNotIn(str(audio.resolve()), serialized)
            self.assertNotIn("manifest_path", payload)
            self.assertIn("manifest_path_identity_sha256", payload)


if __name__ == "__main__":
    unittest.main()
