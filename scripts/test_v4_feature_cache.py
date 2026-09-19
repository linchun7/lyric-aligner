from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from lyric_aligner.audio.feature_cache import (
    FeatureCacheSpec,
    cache_path,
    load_feature_bundle,
    save_feature_bundle,
)
from lyric_aligner.audio.features import FeatureBundle


class V4FeatureCacheTests(unittest.TestCase):
    def _bundle(self, *, sr: int = 11025, hop_length: int = 1024) -> FeatureBundle:
        chroma = np.linspace(0.1, 0.9, 12 * 8, dtype=np.float32).reshape(12, 8)
        mfcc = np.linspace(-0.5, 0.5, 12 * 8, dtype=np.float32).reshape(12, 8)
        return FeatureBundle(
            sr=sr,
            hop_length=hop_length,
            duration_seconds=8 * hop_length / sr,
            chroma=chroma,
            mfcc=mfcc,
        )

    def _spec(self, *, digest: str = "a" * 64, sr: int = 11025, hop_length: int = 1024):
        return FeatureCacheSpec(
            audio_sha256=digest,
            sr=sr,
            hop_length=hop_length,
        )

    def test_roundtrip_preserves_numeric_feature_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = self._spec()
            bundle = self._bundle()
            path = save_feature_bundle(root, spec, bundle)
            self.assertEqual(path, cache_path(root, spec))
            loaded = load_feature_bundle(root, spec)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.sr, bundle.sr)
            self.assertEqual(loaded.hop_length, bundle.hop_length)
            self.assertAlmostEqual(loaded.duration_seconds, bundle.duration_seconds)
            np.testing.assert_array_equal(loaded.chroma, bundle.chroma)
            np.testing.assert_array_equal(loaded.mfcc, bundle.mfcc)

    def test_cache_key_changes_with_audio_or_feature_config(self):
        base = self._spec()
        self.assertNotEqual(base.key, self._spec(digest="b" * 64).key)
        self.assertNotEqual(base.key, self._spec(sr=8000).key)
        self.assertNotEqual(base.key, self._spec(hop_length=512).key)

    def test_corrupt_cache_is_treated_as_miss(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = self._spec()
            path = cache_path(root, spec)
            path.write_bytes(b"not-an-npz")
            self.assertIsNone(load_feature_bundle(root, spec))

    def test_wrong_spec_never_reuses_another_cache_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = self._spec()
            save_feature_bundle(root, spec, self._bundle())
            self.assertIsNone(load_feature_bundle(root, self._spec(digest="b" * 64)))

    def test_invalid_bundle_is_rejected_before_write(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = self._bundle()
            bad = FeatureBundle(
                sr=bundle.sr,
                hop_length=bundle.hop_length,
                duration_seconds=bundle.duration_seconds,
                chroma=np.asarray([[np.nan, 0.0]], dtype=np.float32),
                mfcc=np.asarray([[0.0, 0.0]], dtype=np.float32),
            )
            with self.assertRaises(ValueError):
                save_feature_bundle(Path(directory), self._spec(), bad)


if __name__ == "__main__":
    unittest.main()
