from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import task_contract


class MixContentExtentTaskContractTests(unittest.TestCase):
    def test_optional_extent_file_is_fingerprinted_and_schema_supported(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source_srt = root / "source.srt"
            audio = root / "mix.wav"
            song_list = root / "songs.txt"
            lyrics = root / "lyrics"
            sources = root / "sources"
            extent = root / "extent.json"
            source_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nline\n", encoding="utf-8")
            audio.write_bytes(b"audio")
            song_list.write_text("00:00 Artist - Song\n", encoding="utf-8")
            lyrics.mkdir()
            (lyrics / "Artist - Song.lrc").write_text("[00:00.00]line\n", encoding="utf-8")
            sources.mkdir()
            (sources / "Artist - Song.wav").write_bytes(b"source")
            extent.write_text(
                json.dumps(
                    {
                        "schema_version": "mix-content-extent-1.0",
                        "audio_sha256": "0" * 64,
                        "content_end_seconds": 10.0,
                        "reason": "test",
                    }
                ),
                encoding="utf-8",
            )

            base = task_contract.build_task_manifest(
                root,
                "task",
                source_srt=source_srt,
                audio=audio,
                song_list=song_list,
                lyrics_dir=lyrics,
                source_audio_dir=sources,
            )
            with_extent = task_contract.build_task_manifest(
                root,
                "task",
                source_srt=source_srt,
                audio=audio,
                song_list=song_list,
                lyrics_dir=lyrics,
                source_audio_dir=sources,
                mix_content_extent=extent,
            )
            self.assertEqual(task_contract.validate_task_manifest_schema(with_extent), [])
            self.assertNotIn("mix_content_extent", base["inputs"])
            self.assertEqual(with_extent["inputs"]["mix_content_extent"]["kind"], "file")
            self.assertNotEqual(base["task_fingerprint_sha256"], with_extent["task_fingerprint_sha256"])


if __name__ == "__main__":
    unittest.main()
