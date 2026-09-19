import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from lyric_aligner import __version__
from lyric_aligner.assets.resolver import resolve_assets
from lyric_aligner.config import DEFAULT_V4_PROFILE
from lyric_aligner.contracts.artifacts import build_artifact_manifest
from lyric_aligner.pipeline.context import PipelineContextError, build_pipeline_context


class V4PipelineContextTests(unittest.TestCase):
    def resolved_fixture(self, root: Path):
        lyrics = root / "lyrics"
        audio = root / "audio"
        lyrics.mkdir()
        audio.mkdir()
        songs = root / "songs.txt"
        songs.write_text("00:00 Artist - Signal\n", encoding="utf-8")
        (lyrics / "Artist - Signal.lrc").write_text(
            "[00:01.00]line\n", encoding="utf-8"
        )
        (audio / "Artist - Signal.wav").write_bytes(b"source")
        payload = resolve_assets(
            song_list=songs,
            lyrics_dir=lyrics,
            source_audio_dir=audio,
        )
        payload.update(
            {
                "algorithm_version": __version__,
                "task_fingerprint_sha256": "a" * 64,
                "calibration_profile_version": DEFAULT_V4_PROFILE.profile_version,
                "calibration_profile_id": DEFAULT_V4_PROFILE.profile_id,
                "calibration_profile": DEFAULT_V4_PROFILE.to_dict(),
                "calibration_overrides": {},
            }
        )
        track_assets = root / "track_assets.json"
        track_assets.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        artifact = build_artifact_manifest(
            task_fingerprint_sha256="a" * 64,
            stage="asset_resolution",
            algorithm_version=__version__,
            outputs=(("track_assets", track_assets),),
            normalized_config={
                "calibration_profile_version": DEFAULT_V4_PROFILE.profile_version,
                "calibration_profile_id": DEFAULT_V4_PROFILE.profile_id,
                "calibration_overrides": {},
            },
        )
        return payload, artifact

    def test_profile_id_changes_when_threshold_changes(self):
        changed = replace(
            DEFAULT_V4_PROFILE,
            asset_resolver=replace(
                DEFAULT_V4_PROFILE.asset_resolver,
                min_margin=0.09,
            ),
        )
        self.assertNotEqual(DEFAULT_V4_PROFILE.profile_id, changed.profile_id)
        self.assertEqual(
            DEFAULT_V4_PROFILE.profile_id,
            DEFAULT_V4_PROFILE.profile_id,
        )

    def test_context_binds_task_profile_artifact_and_occurrence(self):
        with tempfile.TemporaryDirectory() as directory:
            payload, artifact = self.resolved_fixture(Path(directory))
            context = build_pipeline_context(
                expected_task_fingerprint="a" * 64,
                track_assets_payload=payload,
                asset_artifact=artifact,
            )
            [binding] = context.bindings
            self.assertEqual(binding.ordinal, 1)
            self.assertEqual(
                context.binding_by_occurrence_id[binding.occurrence_id], binding
            )
            self.assertEqual(
                context.artifact_config()["calibration_profile_id"],
                DEFAULT_V4_PROFILE.profile_id,
            )
            self.assertEqual(context.profile.to_dict(), DEFAULT_V4_PROFILE.to_dict())

    def test_context_rejects_wrong_task_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            payload, artifact = self.resolved_fixture(Path(directory))
            with self.assertRaises(PipelineContextError):
                build_pipeline_context(
                    expected_task_fingerprint="b" * 64,
                    track_assets_payload=payload,
                    asset_artifact=artifact,
                )

    def test_context_rejects_tampered_embedded_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            payload, artifact = self.resolved_fixture(Path(directory))
            payload["calibration_profile"]["fine"]["min_margin"] = 0.02
            with self.assertRaises(PipelineContextError):
                build_pipeline_context(
                    expected_task_fingerprint="a" * 64,
                    track_assets_payload=payload,
                    asset_artifact=artifact,
                )


if __name__ == "__main__":
    unittest.main()
