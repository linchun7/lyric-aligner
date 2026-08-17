import tempfile
import unittest
from pathlib import Path

from lyric_aligner.assets.bindings import AssetBindingError, bindings_from_payload
from lyric_aligner.assets.resolver import resolve_assets


class V4AssetBindingTests(unittest.TestCase):
    def fixture(self):
        directory = tempfile.TemporaryDirectory()
        root = Path(directory.name)
        lyrics = root / "lyrics"
        source = root / "source"
        lyrics.mkdir()
        source.mkdir()
        song_list = root / "songs.txt"
        song_list.write_text("00:00 Artist - Signal\n", encoding="utf-8")
        lyric = lyrics / "Artist - Signal.lrc"
        lyric.write_text(
            "[00:01.00]第一候选\n[00:01.00]第二候选\n"
            "[00:03.00]唯一一句\n",
            encoding="utf-8",
        )
        audio = source / "Artist - Signal.wav"
        audio.write_bytes(b"synthetic-source-audio")
        return directory, song_list, lyrics, source, lyric, audio

    def resolve(self, *, original_index: int):
        directory, song_list, lyrics, source, lyric, audio = self.fixture()
        payload = resolve_assets(
            song_list=song_list,
            lyrics_dir=lyrics,
            source_audio_dir=source,
            language_by_track={"Signal": "zh"},
            lyric_role_overrides_by_track={
                "Signal": {1000: original_index}
            },
        )
        return directory, payload, lyric, audio

    def test_role_override_changes_track_asset_identity(self):
        left_dir, left, _, _ = self.resolve(original_index=0)
        right_dir, right, _, _ = self.resolve(original_index=1)
        self.addCleanup(left_dir.cleanup)
        self.addCleanup(right_dir.cleanup)

        self.assertNotEqual(left["assets"][0]["track_id"], right["assets"][0]["track_id"])
        self.assertNotEqual(left["assets"][0]["version_id"], right["assets"][0]["version_id"])
        self.assertNotEqual(
            left["assets"][0]["canonical_selection_sha256"],
            right["assets"][0]["canonical_selection_sha256"],
        )

    def test_binding_is_single_source_for_audio_lyric_and_original_choice(self):
        directory, payload, lyric, audio = self.resolve(original_index=1)
        self.addCleanup(directory.cleanup)

        [binding] = bindings_from_payload(payload, verify_files=True)

        self.assertEqual(Path(binding.source_audio_path), audio.resolve())
        self.assertEqual(Path(binding.canonical_lyric_path), lyric.resolve())
        self.assertEqual(binding.original_index_by_timestamp[1000], 1)
        self.assertEqual(binding.canonical_originals[0].text, "第二候选")

    def test_tampered_canonical_selection_is_rejected(self):
        directory, payload, _, _ = self.resolve(original_index=1)
        self.addCleanup(directory.cleanup)
        payload["resolution"][0]["canonical_selection"][0]["text"] = "被篡改"

        with self.assertRaisesRegex(AssetBindingError, "selection hash mismatch"):
            bindings_from_payload(payload)

    def test_materialized_file_hash_drift_is_rejected(self):
        directory, payload, _, audio = self.resolve(original_index=1)
        self.addCleanup(directory.cleanup)
        audio.write_bytes(b"changed-after-resolution")

        with self.assertRaisesRegex(AssetBindingError, "source audio changed"):
            bindings_from_payload(payload, verify_files=True)


if __name__ == "__main__":
    unittest.main()
