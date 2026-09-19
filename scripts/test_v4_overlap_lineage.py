import json
import tempfile
import unittest
from pathlib import Path

from lyric_aligner import __version__
from lyric_aligner.contracts.artifacts import build_artifact_manifest
from v4_recompose_overlap import _effective_boundary_mapping


FINGERPRINT = "7" * 64
ASSET_ID = "asset-artifact-id"
OCCURRENCE_ID = "occ-left"
TRACK_ID = "track-left"
CANONICAL_SHA = "c" * 64


class V4OverlapLineageTests(unittest.TestCase):
    def fixture(self, root: Path):
        coarse = {
            "schema_version": "1.1",
            "algorithm_version": __version__,
            "task_fingerprint_sha256": FINGERPRINT,
            "occurrence_id": OCCURRENCE_ID,
            "track_id": TRACK_ID,
            "canonical_selection_sha256": CANONICAL_SHA,
            "upstream_asset_artifact_id": ASSET_ID,
            "result": {
                "windows": [{"ambiguous": False}],
                "timewarp": {
                    "blocked": False,
                    "selection": "AFFINE_ACCEPTED",
                    "mapping": {
                        "intercept": 0.0,
                        "base_slope": 1.0,
                        "breakpoints": [],
                        "slope_deltas": [],
                    },
                },
            },
        }
        coarse_path = root / "left.coarse.json"
        coarse_path.write_text(json.dumps(coarse), encoding="utf-8")
        artifact = build_artifact_manifest(
            task_fingerprint_sha256=FINGERPRINT,
            stage="coarse_audio_alignment",
            algorithm_version=__version__,
            outputs=(("coarse_alignment", coarse_path),),
            upstream_artifact_ids=(ASSET_ID,),
        )
        artifact_path = root / "left.coarse.artifact.json"
        artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
        return coarse_path, artifact_path, artifact

    def call(self, root: Path, coarse_path: Path, artifact_path: Path, **overrides):
        values = {
            "expected_occurrence_id": OCCURRENCE_ID,
            "expected_track_id": TRACK_ID,
            "expected_canonical_selection_sha256": CANONICAL_SHA,
            "expected_asset_artifact_id": ASSET_ID,
        }
        values.update(overrides)
        return _effective_boundary_mapping(
            task_manifest=root / "unused-task.json",
            mix_audio=root / "unused-mix.wav",
            track_assets=root / "unused-assets.json",
            asset_artifact=root / "unused-assets.artifact.json",
            coarse_path=coarse_path,
            coarse_artifact_path=artifact_path,
            out_dir=root,
            side="left",
            git_commit="",
            fingerprint=FINGERPRINT,
            required_upstreams={json.loads(artifact_path.read_text())["artifact_id"]},
            **values,
        )

    def test_exact_boundary_identity_is_accepted_without_fine(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coarse_path, artifact_path, _ = self.fixture(root)
            mapping, upstreams, source = self.call(root, coarse_path, artifact_path)
            self.assertEqual(mapping["base_slope"], 1.0)
            self.assertEqual(len(upstreams), 1)
            self.assertEqual(source, "coarse")

    def test_swapped_occurrence_boundary_coarse_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coarse_path, artifact_path, _ = self.fixture(root)
            with self.assertRaisesRegex(ValueError, "occurrence identity mismatch"):
                self.call(
                    root,
                    coarse_path,
                    artifact_path,
                    expected_occurrence_id="occ-right",
                )

    def test_wrong_track_or_canonical_selection_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coarse_path, artifact_path, _ = self.fixture(root)
            with self.assertRaisesRegex(ValueError, "track identity mismatch"):
                self.call(
                    root,
                    coarse_path,
                    artifact_path,
                    expected_track_id="track-right",
                )
            with self.assertRaisesRegex(ValueError, "canonical selection mismatch"):
                self.call(
                    root,
                    coarse_path,
                    artifact_path,
                    expected_canonical_selection_sha256="d" * 64,
                )

    def test_coarse_artifact_must_derive_from_exact_asset_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coarse_path, artifact_path, _ = self.fixture(root)
            with self.assertRaisesRegex(ValueError, "asset identity mismatch"):
                self.call(
                    root,
                    coarse_path,
                    artifact_path,
                    expected_asset_artifact_id="other-asset",
                )


if __name__ == "__main__":
    unittest.main()
