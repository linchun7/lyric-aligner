import tempfile
import unittest
from pathlib import Path

from lyric_aligner.assets.resolver import AssetResolutionError, resolve_assets


class V4AssetResolverTests(unittest.TestCase):
    def make_dirs(self, root: Path):
        lyrics = root / "lyrics"
        audio = root / "audio"
        lyrics.mkdir()
        audio.mkdir()
        return lyrics, audio

    def test_unrelated_single_assets_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lyrics, audio = self.make_dirs(root)
            songs = root / "songs.txt"
            songs.write_text("00:00 Artist - Target Song\n", encoding="utf-8")
            (lyrics / "completely-unrelated.lrc").write_text("[00:00]x\n", encoding="utf-8")
            (audio / "completely-unrelated.wav").write_bytes(b"not-wave-data")
            with self.assertRaisesRegex(AssetResolutionError, "match too weak"):
                resolve_assets(song_list=songs, lyrics_dir=lyrics, source_audio_dir=audio)

    def test_ambiguous_near_duplicate_assets_fail_on_margin(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lyrics, audio = self.make_dirs(root)
            songs = root / "songs.txt"
            songs.write_text("00:00 Artist - Signal\n", encoding="utf-8")
            (lyrics / "Artist - Signal studio.lrc").write_text("[00:00]x\n", encoding="utf-8")
            (lyrics / "Artist - Signal live.lrc").write_text("[00:00]x\n", encoding="utf-8")
            (audio / "Artist - Signal.wav").write_bytes(b"a")
            with self.assertRaisesRegex(AssetResolutionError, "ambiguous"):
                resolve_assets(song_list=songs, lyrics_dir=lyrics, source_audio_dir=audio)

    def test_repeated_song_reuses_asset_but_gets_distinct_occurrences(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lyrics, audio = self.make_dirs(root)
            songs = root / "songs.txt"
            songs.write_text(
                "00:00 Artist - Signal\n02:00 Artist - Signal\n",
                encoding="utf-8",
            )
            (lyrics / "Artist - Signal.lrc").write_text("[00:00]line\n", encoding="utf-8")
            (audio / "Artist - Signal.wav").write_bytes(b"source-version")
            payload = resolve_assets(
                song_list=songs,
                lyrics_dir=lyrics,
                source_audio_dir=audio,
                middle_cut_by_occurrence={2: "true"},
            )
            self.assertEqual(len(payload["assets"]), 1)
            self.assertEqual(len(payload["occurrences"]), 2)
            self.assertEqual(
                payload["occurrences"][0]["track_id"],
                payload["occurrences"][1]["track_id"],
            )
            self.assertNotEqual(
                payload["occurrences"][0]["occurrence_id"],
                payload["occurrences"][1]["occurrence_id"],
            )
            self.assertEqual(payload["occurrences"][1]["middle_cut"], "true")

    def test_distinct_tracks_cannot_silently_collapse_to_one_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lyrics, audio = self.make_dirs(root)
            songs = root / "songs.txt"
            songs.write_text(
                "00:00 Alpha - Signal\n01:00 Beta - Signal\n",
                encoding="utf-8",
            )
            (lyrics / "Alpha - Signal.lrc").write_text("[00:00]a\n", encoding="utf-8")
            (lyrics / "Beta - Signal.lrc").write_text("[00:00]b\n", encoding="utf-8")
            (audio / "Alpha - Signal.wav").write_bytes(b"alpha")
            (audio / "Beta - Signal.wav").write_bytes(b"beta")
            payload = resolve_assets(song_list=songs, lyrics_dir=lyrics, source_audio_dir=audio)
            self.assertEqual(len(payload["assets"]), 2)
            self.assertEqual(len({item["track_id"] for item in payload["assets"]}), 2)

    def test_same_generic_file_cannot_be_reused_for_distinct_artists(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lyrics, audio = self.make_dirs(root)
            songs = root / "songs.txt"
            songs.write_text(
                "00:00 Alpha - Signal\n01:00 Beta - Signal\n",
                encoding="utf-8",
            )
            (lyrics / "Signal.lrc").write_text("[00:00]x\n", encoding="utf-8")
            (audio / "Signal.wav").write_bytes(b"one-version")
            with self.assertRaises(AssetResolutionError):
                resolve_assets(song_list=songs, lyrics_dir=lyrics, source_audio_dir=audio)


if __name__ == "__main__":
    unittest.main()
