import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from lyric_aligner.audio.contextual_mapping import (
    CONTEXTUAL_MAPPING_CONTEXT_SECONDS,
    CONTEXTUAL_MAPPING_SAMPLE_RATE,
    CONTEXTUAL_MAPPING_TARGET_SECONDS,
    ContextualMappingConfig,
    FeatureWindow,
    generate_contextual_mapping_candidates,
)
from lyric_aligner.audio.independent_fine import OnsetFeatureBundle


def _bundle(frame_count=5000, *, seed=17):
    rng = np.random.default_rng(seed)
    return OnsetFeatureBundle(
        sr=CONTEXTUAL_MAPPING_SAMPLE_RATE,
        hop_length=256,
        duration_seconds=frame_count * 256 / CONTEXTUAL_MAPPING_SAMPLE_RATE,
        onset=rng.random((1, frame_count), dtype=np.float32),
        multiband_flux=rng.random((8, frame_count), dtype=np.float32),
    )


def _sha(value):
    return hashlib.sha256(value).hexdigest()


class ContextualMappingTests(unittest.TestCase):
    def setUp(self):
        self.source = Path("mock-source.wav")
        self.mix = Path("mock-mix.wav")
        self.source_sha = "a" * 64
        self.mix_sha = "b" * 64
        self.occurrence = {
            "occurrence_id": "occ-1",
            "mix_start": 0.0,
            "mix_end": 90.0,
        }
        self.affine = {
            "mode": "AFFINE",
            "intercept": 3.0,
            "base_slope": 1.1,
            "breakpoints": [],
            "slope_deltas": [],
        }

    def _loader(self, calls):
        def loader(request):
            calls.append(request)
            count = 5000 if request.role == "source" else 4000
            bundle = _bundle(count, seed=1 if request.role == "source" else 2)
            return FeatureWindow(
                bundle=bundle,
                absolute_start=request.start_seconds,
                absolute_end=request.start_seconds + bundle.duration_seconds,
            )

        return loader

    @patch("lyric_aligner.audio.contextual_mapping.retrieve_contextual_onset_window")
    def test_absolute_candidate_and_bounded_feature_scope(self, observer):
        calls = []
        observer.return_value = {
            "reason": "joint_local_context_score",
            "ambiguous": False,
            "margin": 0.2,
            "top1": {
                "source_start": 10.0,
                "source_end": 34.0,
                "source_center": 22.0,
                "estimated_slope": 1.0,
                "fused_score": 0.91,
            },
            "top2": None,
            "candidates": [
                {
                    "source_start": 10.0,
                    "source_end": 34.0,
                    "source_center": 22.0,
                    "estimated_slope": 1.0,
                    "fused_score": 0.91,
                }
            ],
        }
        result = generate_contextual_mapping_candidates(
            source_audio=self.source,
            mix_audio=self.mix,
            source_audio_sha256=self.source_sha,
            mix_audio_sha256=self.mix_sha,
            occurrence=self.occurrence,
            effective_timewarp=self.affine,
            mix_center=40.0,
            feature_loader=self._loader(calls),
        )
        self.assertEqual(result["status"], "available")
        self.assertFalse(result["boundary_authority"])
        self.assertFalse(result["automatic_mutation_allowed"])
        self.assertAlmostEqual(result["mapping_provenance"]["nominal_source_center"], 47.0)
        # The mock observer returns local source coordinates.  The source crop
        # origin must be restored before evidence is emitted.
        self.assertAlmostEqual(result["top1"]["source_center"], 22.0 + calls[1].start_seconds)
        self.assertAlmostEqual(result["top1"]["mix_center"], 40.0)
        self.assertEqual(result["target_window"]["duration_seconds"], 24.0)
        mix_call = next(call for call in calls if call.role == "mix")
        self.assertAlmostEqual(mix_call.start_seconds, 26.0)
        self.assertAlmostEqual(mix_call.end_seconds, 54.0)
        self.assertTrue(result["feature_scope"]["full_mix_features_not_computed"])
        self.assertEqual(result["mapping_provenance"]["slope_grid"], [1.09, 1.095, 1.1, 1.105, 1.11])

    def test_defaults_match_validated_contextual_configuration(self):
        config = ContextualMappingConfig()
        self.assertEqual(config.sample_rate, 22050)
        self.assertEqual(config.target_seconds, 24.0)
        self.assertEqual(config.context_seconds, 2.0)
        self.assertEqual(config.source_search_radius_seconds, 3.0)
        self.assertEqual(config.slope_offsets, (-.01, -.005, 0.0, .005, .01))

    @patch("lyric_aligner.audio.contextual_mapping.retrieve_contextual_onset_window")
    def test_primary_interval_occurrence_alias_is_enforced(self, observer):
        observer.return_value = {
            "reason": "joint_local_context_score",
            "ambiguous": False,
            "margin": 0.2,
            "top1": {"source_start": 10.0, "source_end": 34.0, "source_center": 22.0, "estimated_slope": 1.0, "fused_score": 0.91},
            "top2": None,
            "candidates": [],
        }
        result = generate_contextual_mapping_candidates(
            source_audio=self.source,
            mix_audio=self.mix,
            source_audio_sha256=self.source_sha,
            mix_audio_sha256=self.mix_sha,
            occurrence={"occurrence_id": "interval", "primary_interval": [0.0, 30.0]},
            effective_timewarp=self.affine,
            mix_center=24.0,
            feature_loader=self._loader([]),
        )
        self.assertEqual(result["status"], "inapplicable")
        self.assertEqual(result["reason"], "target_window_outside_occurrence_end")

    def test_known_affine_transform_is_recovered_with_mock_features(self):
        # A compact synthetic case keeps the test fast while exercising the
        # real contextual observer.  The production default remains a 24 sec
        # window; this test only scales the known transform down to 4 sec.
        frame_seconds = 256 / 22050
        rng = np.random.default_rng(921)
        mix_global = rng.random((8, 9000), dtype=np.float32)
        source_global = (rng.random((8, 12000), dtype=np.float32) * 0.02).astype(np.float32)
        for mix_frame in range(1000, 4000):
            mix_time = mix_frame * frame_seconds
            source_frame = round((3.0 + 1.1 * mix_time) / frame_seconds)
            if 0 <= source_frame < source_global.shape[1]:
                source_global[:, source_frame] = mix_global[:, mix_frame]

        def loader(request):
            values = mix_global if request.role == "mix" else source_global
            first = max(0, round(request.start_seconds / frame_seconds))
            last = min(values.shape[1], round(request.end_seconds / frame_seconds) + 1)
            crop = values[:, first:last]
            return OnsetFeatureBundle(
                sr=22050,
                hop_length=256,
                duration_seconds=(last - first) * frame_seconds,
                onset=crop[:1],
                multiband_flux=crop,
            )

        result = generate_contextual_mapping_candidates(
            source_audio=self.source,
            mix_audio=self.mix,
            source_audio_sha256=self.source_sha,
            mix_audio_sha256=self.mix_sha,
            occurrence={"occurrence_id": "synthetic", "mix_start": 0.0, "mix_end": 90.0},
            effective_timewarp={
                "mode": "AFFINE",
                "intercept": 3.0,
                "base_slope": 1.1,
                "breakpoints": [],
                "slope_deltas": [],
            },
            mix_center=30.0,
            feature_loader=loader,
            config=ContextualMappingConfig(
                target_seconds=4.0,
                context_seconds=1.0,
                source_search_radius_seconds=1.0,
            ),
        )
        self.assertEqual(result["status"], "available")
        self.assertFalse(result["ambiguous"])
        self.assertAlmostEqual(result["top1"]["nominal_source_center"], 36.0, places=2)
        self.assertAlmostEqual(result["top1"]["source_center"], 36.0, delta=0.25)
        self.assertAlmostEqual(result["top1"]["estimated_slope"], 1.1, delta=0.011)

    def test_rate_breakpoint_is_fail_closed(self):
        mapping = {
            "mode": "PIECEWISE_RATE",
            "intercept": 0.0,
            "base_slope": 1.0,
            "breakpoints": [40.0],
            "slope_deltas": [0.1],
        }
        result = generate_contextual_mapping_candidates(
            source_audio=self.source,
            mix_audio=self.mix,
            source_audio_sha256=self.source_sha,
            mix_audio_sha256=self.mix_sha,
            occurrence=self.occurrence,
            effective_timewarp=mapping,
            mix_center=40.0,
            feature_loader=self._loader([]),
        )
        self.assertEqual(result["status"], "inapplicable")
        self.assertEqual(result["reason"], "target_window_crosses_rate_breakpoint")

    def test_cut_segment_is_not_bridged(self):
        mapping = {
            "kind": "CUT_AWARE",
            "segments": [
                {
                    "index": 0,
                    "mix_start": 0.0,
                    "mix_end": 35.0,
                    "source_start": 0.0,
                    "source_end": 35.0,
                    "mapping": {"mode": "AFFINE", "intercept": 0.0, "base_slope": 1.0, "breakpoints": [], "slope_deltas": []},
                },
                {
                    "index": 1,
                    "mix_start": 35.0,
                    "mix_end": 90.0,
                    "source_start": 50.0,
                    "source_end": 105.0,
                    "mapping": {"mode": "AFFINE", "intercept": 15.0, "base_slope": 1.0, "breakpoints": [], "slope_deltas": []},
                },
            ],
            "cuts": [{"source_gap_start": 35.0, "source_gap_end": 50.0}],
        }
        result = generate_contextual_mapping_candidates(
            source_audio=self.source,
            mix_audio=self.mix,
            source_audio_sha256=self.source_sha,
            mix_audio_sha256=self.mix_sha,
            occurrence=self.occurrence,
            effective_timewarp=mapping,
            mix_center=35.0,
            feature_loader=self._loader([]),
        )
        self.assertEqual(result["status"], "inapplicable")
        self.assertEqual(result["reason"], "target_window_crosses_cut_or_segment")

    def test_reference_retime_is_explicitly_unsupported(self):
        result = generate_contextual_mapping_candidates(
            source_audio=self.source,
            mix_audio=self.mix,
            source_audio_sha256=self.source_sha,
            mix_audio_sha256=self.mix_sha,
            occurrence=self.occurrence,
            effective_timewarp={"mode": "REFERENCE_RETIME", "mapping": self.affine},
            mix_center=40.0,
            feature_loader=self._loader([]),
        )
        self.assertEqual(result["status"], "inapplicable")
        self.assertEqual(result["reason"], "unsupported_reference_retime_mapping")

    @patch("lyric_aligner.audio.contextual_mapping.retrieve_contextual_onset_window")
    @patch("lyric_aligner.audio.contextual_mapping._default_feature_loader")
    def test_cache_is_bound_to_input_and_crop_scope(self, loader, observer):
        observer.return_value = {
            "reason": "joint_local_context_score",
            "ambiguous": True,
            "margin": 0.0,
            "top1": None,
            "top2": None,
            "candidates": [],
        }
        def load(request):
            bundle = _bundle(5000)
            return FeatureWindow(bundle, request.start_seconds, request.start_seconds + bundle.duration_seconds)
        loader.side_effect = load
        with tempfile.TemporaryDirectory() as directory:
            first = generate_contextual_mapping_candidates(
                source_audio=self.source,
                mix_audio=self.mix,
                source_audio_sha256=self.source_sha,
                mix_audio_sha256=self.mix_sha,
                occurrence=self.occurrence,
                effective_timewarp=self.affine,
                mix_center=40.0,
                cache_dir=directory,
            )
            self.assertEqual(first["status"], "available")
            loader.reset_mock()
            second = generate_contextual_mapping_candidates(
                source_audio=self.source,
                mix_audio=self.mix,
                source_audio_sha256=self.source_sha,
                mix_audio_sha256=self.mix_sha,
                occurrence=self.occurrence,
                effective_timewarp=self.affine,
                mix_center=40.0,
                cache_dir=directory,
            )
            self.assertEqual(second["status"], "available")
            self.assertEqual(loader.call_count, 0)
            self.assertNotEqual(first["feature_scope"]["mix"]["cache_key"], "")


if __name__ == "__main__":
    unittest.main()
