import json
import tempfile
import unittest
from pathlib import Path

from lyric_aligner import __version__
from lyric_aligner.assets.resolver import resolve_assets
from lyric_aligner.contracts.artifacts import build_artifact_manifest
from lyric_aligner.legacy.bridge import (
    LEGACY_ALGORITHM_VERSION,
    canonical_lines_for_ordinal,
    legacy_bridge_metadata,
    load_bridge_context,
)


class V4LegacyBridgeTests(unittest.TestCase):
    def fixture(self, root: Path):
        lyrics = root / "lyrics"
        audio = root / "audio"
        lyrics.mkdir()
        audio.mkdir()
        songs = root / "songs.txt"
        songs.write_text("00:00 Artist - Signal\n", encoding="utf-8")
        lyric = lyrics / "Artist - Signal.lrc"
        lyric.write_text(
            "[00:01.00]translation\n[00:01.00]canonical\n"
            "[00:03.00]next line\n",
            encoding="utf-8",
        )
        source = audio / "Artist - Signal.wav"
        source.write_bytes(b"source")
        payload = resolve_assets(
            song_list=songs,
            lyrics_dir=lyrics,
            source_audio_dir=audio,
            language_by_track={"Signal": "en"},
            lyric_role_overrides_by_track={"Signal": {1000: 1}},
        )
        payload["algorithm_version"] = __version__
        payload["task_fingerprint_sha256"] = "1" * 64
        track_assets = root / "track_assets.json"
        track_assets.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        artifact = build_artifact_manifest(
            task_fingerprint_sha256="1" * 64,
            stage="asset_resolution",
            algorithm_version=__version__,
            outputs=(("track_assets", track_assets),),
        )
        artifact_path = root / "track_assets.artifact.json"
        artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
        return track_assets, artifact_path, source

    def test_bridge_uses_exact_resolved_source_and_canonical_original(self):
        with tempfile.TemporaryDirectory() as directory:
            track_assets, artifact, source = self.fixture(Path(directory))
            context = load_bridge_context(
                expected_task_fingerprint="1" * 64,
                track_assets_path=track_assets,
                asset_artifact_path=artifact,
            )
            [binding] = context.bindings
            self.assertEqual(Path(binding.source_audio_path), source.resolve())
            lines = canonical_lines_for_ordinal(context, 1)
            self.assertEqual([line.text for line in lines], ["canonical", "next line"])

    def test_bridge_metadata_separates_legacy_and_v4_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            track_assets, artifact, _ = self.fixture(Path(directory))
            context = load_bridge_context(
                expected_task_fingerprint="1" * 64,
                track_assets_path=track_assets,
                asset_artifact_path=artifact,
            )
            metadata = legacy_bridge_metadata(context)
            self.assertEqual(metadata["legacy_algorithm_version"], LEGACY_ALGORITHM_VERSION)
            self.assertEqual(metadata["v4_algorithm_version"], __version__)
            self.assertEqual(metadata["asset_artifact_id"], artifact_id(artifact))


def artifact_id(path: Path) -> str:
    return str(json.loads(path.read_text(encoding="utf-8"))["artifact_id"])


if __name__ == "__main__":
    unittest.main()
