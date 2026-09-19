from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lyric_aligner.alignment.selective_fusion import PRO_PRODUCT_VERSION
from lyric_aligner.assets.bindings import bindings_from_payload
from lyric_aligner.assets.resolver import resolve_assets
from lyric_aligner.timeline.anchor_repair import TimedCanonicalOccurrence
from v4_pro_selective import _forced_bindings


class ProVersionBindingTests(unittest.TestCase):
    def test_new_forced_binding_version_and_legacy_asset_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_audio = root / "source.wav"
            canonical_lyric = root / "song.lrc"
            source_audio.write_bytes(b"synthetic-source-audio")
            canonical_lyric.write_text("[00:01.00]line\n", encoding="utf-8")

            [binding] = _forced_bindings(
                timed=[
                    TimedCanonicalOccurrence(
                        ordinal=0,
                        source="song.lrc",
                        source_ordinal=0,
                        time_ms=1000,
                        text="line",
                        normalized="line",
                    )
                ],
                source_names=["song.lrc"],
                canonical_lyrics=[canonical_lyric],
                source_paths={0: source_audio},
                languages={},
            )
            self.assertEqual(
                binding.version_id,
                f"smart-pro-v{PRO_PRODUCT_VERSION}",
            )
            self.assertNotEqual(binding.version_id, "smart-pro-v1.2.6")

            lyrics_dir = root / "lyrics"
            source_dir = root / "source-audio"
            lyrics_dir.mkdir()
            source_dir.mkdir()
            song_list = root / "songs.txt"
            song_list.write_text("00:00 Artist - Song\n", encoding="utf-8")
            lyric = lyrics_dir / "Artist - Song.lrc"
            lyric.write_text("[00:01.00]line\n", encoding="utf-8")
            (source_dir / "Artist - Song.wav").write_bytes(b"legacy-source")
            legacy_payload = resolve_assets(
                song_list=song_list,
                lyrics_dir=lyrics_dir,
                source_audio_dir=source_dir,
            )
            legacy_payload["assets"][0]["version_id"] = "smart-pro-v1.2.6"

            [legacy_binding] = bindings_from_payload(
                legacy_payload,
                verify_files=True,
            )
            self.assertEqual(legacy_binding.version_id, "smart-pro-v1.2.6")


if __name__ == "__main__":
    unittest.main()
