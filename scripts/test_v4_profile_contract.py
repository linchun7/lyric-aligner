import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from lyric_aligner import __version__
from lyric_aligner.assets.resolver import resolve_assets
from lyric_aligner.config import CalibrationProfileError, DEFAULT_V4_PROFILE, load_profile, profile_from_dict, write_profile
from lyric_aligner.contracts.artifacts import build_artifact_manifest
from lyric_aligner.pipeline.context import PipelineContextError, build_pipeline_context
from v4_validate_release import _load_upstream_artifacts


class V4ProfileContractTests(unittest.TestCase):
    def test_profile_file_round_trip_preserves_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            write_profile(path)
            loaded = load_profile(path)
            self.assertEqual(loaded.profile_id, DEFAULT_V4_PROFILE.profile_id)
            self.assertEqual(loaded.to_dict(), DEFAULT_V4_PROFILE.to_dict())

    def test_profile_identity_changes_when_calibration_changes(self):
        changed = replace(DEFAULT_V4_PROFILE, fine=replace(DEFAULT_V4_PROFILE.fine, min_margin=0.02))
        self.assertNotEqual(changed.profile_id, DEFAULT_V4_PROFILE.profile_id)

    def test_profile_rejects_unknown_or_missing_fields(self):
        payload = DEFAULT_V4_PROFILE.to_dict()
        payload["fine"]["unknown"] = 1
        with self.assertRaises(CalibrationProfileError):
            profile_from_dict(payload)
        payload = DEFAULT_V4_PROFILE.to_dict()
        del payload["transition"]
        with self.assertRaises(CalibrationProfileError):
            profile_from_dict(payload)

    def test_pipeline_context_reconstructs_embedded_custom_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lyrics = root / "lyrics"
            source = root / "source"
            lyrics.mkdir()
            source.mkdir()
            songs = root / "songs.txt"
            songs.write_text("00:00 Artist - Signal\n", encoding="utf-8")
            (lyrics / "Artist - Signal.lrc").write_text("[00:01.00]line\n", encoding="utf-8")
            (source / "Artist - Signal.wav").write_bytes(b"source")
            custom = replace(
                DEFAULT_V4_PROFILE,
                profile_version="test-custom",
                asset_resolver=replace(DEFAULT_V4_PROFILE.asset_resolver, min_margin=0.09),
            )
            payload = resolve_assets(
                song_list=songs,
                lyrics_dir=lyrics,
                source_audio_dir=source,
                min_score=custom.asset_resolver.min_score,
                min_margin=custom.asset_resolver.min_margin,
            )
            payload.update({
                "algorithm_version": __version__,
                "task_fingerprint_sha256": "a" * 64,
                "calibration_profile_version": custom.profile_version,
                "calibration_profile_id": custom.profile_id,
                "calibration_profile": custom.to_dict(),
            })
            assets_path = root / "track_assets.json"
            assets_path.write_text(json.dumps(payload), encoding="utf-8")
            artifact = build_artifact_manifest(
                task_fingerprint_sha256="a" * 64,
                stage="asset_resolution",
                algorithm_version=__version__,
                outputs=(("track_assets", assets_path),),
                normalized_config={
                    "calibration_profile_version": custom.profile_version,
                    "calibration_profile_id": custom.profile_id,
                    "calibration_overrides": {},
                },
            )
            context = build_pipeline_context(
                expected_task_fingerprint="a" * 64,
                track_assets_payload=payload,
                asset_artifact=artifact,
            )
            self.assertEqual(context.profile.profile_id, custom.profile_id)
            with self.assertRaises(PipelineContextError):
                build_pipeline_context(
                    expected_task_fingerprint="a" * 64,
                    track_assets_payload=payload,
                    asset_artifact=artifact,
                    profile=DEFAULT_V4_PROFILE,
                )

    def _artifact(self, root: Path, *, name: str, algorithm_version: str, overrides=None) -> Path:
        output = root / f"{name}.json"
        output.write_text("{}", encoding="utf-8")
        artifact = build_artifact_manifest(
            task_fingerprint_sha256="b" * 64,
            stage=name,
            algorithm_version=algorithm_version,
            outputs=((name, output),),
            normalized_config={
                "calibration_profile_version": DEFAULT_V4_PROFILE.profile_version,
                "calibration_profile_id": DEFAULT_V4_PROFILE.profile_id,
                "calibration_overrides": overrides or {},
            },
        )
        path = root / f"{name}.artifact.json"
        path.write_text(json.dumps(artifact), encoding="utf-8")
        return path

    def test_release_blocks_unprofiled_cli_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self._artifact(
                root,
                name="fine_audio_alignment",
                algorithm_version=__version__,
                overrides={"min_margin": 0.02},
            )
            with self.assertRaisesRegex(ValueError, "calibration CLI overrides"):
                _load_upstream_artifacts([path], fingerprint="b" * 64)

    def test_release_blocks_mixed_v4_algorithm_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left = self._artifact(root, name="asset_resolution", algorithm_version="4.0.0a1")
            right = self._artifact(root, name="coarse_audio_alignment", algorithm_version="4.0.0a2")
            with self.assertRaisesRegex(ValueError, "different v4 algorithm_version"):
                _load_upstream_artifacts([left, right], fingerprint="b" * 64)


if __name__ == "__main__":
    unittest.main()
