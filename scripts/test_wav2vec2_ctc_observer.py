import unittest

import numpy as np

from lyric_aligner.audio.wav2vec2_ctc_observer import (
    WAV2VEC2_CTC_OBSERVER_AUTHORITY,
    WAV2VEC2_CTC_OBSERVER_VERSION,
    XINGYU_XLSR53_CORRELATION_GROUP,
    Wav2Vec2CTCEmission,
    Wav2Vec2CTCObserverError,
    build_outer_evidence,
    map_text_to_ctc_classes,
    normalize_trusted_ctc_text,
)


class Wav2Vec2CTCObserverTests(unittest.TestCase):
    def test_normalize_preserves_lexical_characters_only(self):
        self.assertEqual(normalize_trusted_ctc_text("Ａ你，好! B 2♪"), "a你好b2")

    def test_normalize_empty_fails_closed(self):
        with self.assertRaisesRegex(Wav2Vec2CTCObserverError, "no alignable"):
            normalize_trusted_ctc_text(" ，。♪ ")

    def test_exact_vocabulary_mapping_and_unknown_fail_closed(self):
        vocab = {"你": 1, "好": 2, "a": 3, "<pad>": 0}
        self.assertEqual(
            map_text_to_ctc_classes("你好a", vocab, blank_id=0, reserved_ids={0}),
            (1, 2, 3),
        )
        with self.assertRaisesRegex(Wav2Vec2CTCObserverError, "unavailable"):
            map_text_to_ctc_classes("你呀", vocab, blank_id=0, reserved_ids={0})

    def test_reserved_or_blank_class_fails_closed(self):
        with self.assertRaisesRegex(Wav2Vec2CTCObserverError, "unavailable"):
            map_text_to_ctc_classes("你", {"你": 0}, blank_id=0)
        with self.assertRaisesRegex(Wav2Vec2CTCObserverError, "unavailable"):
            map_text_to_ctc_classes("你", {"你": 4}, blank_id=0, reserved_ids={4})

    def _fixture_emission(self) -> Wav2Vec2CTCEmission:
        scores = np.array(
            [
                [8.0, 0.0, 0.0],
                [7.0, 1.0, 0.0],
                [0.0, 8.0, 0.0],
                [0.0, 8.0, 0.0],
                [7.0, 1.0, 0.0],
                [7.0, 0.0, 1.0],
                [0.0, 0.0, 8.0],
                [0.0, 0.0, 8.0],
                [8.0, 0.0, 0.0],
            ],
            dtype=np.float64,
        )
        return Wav2Vec2CTCEmission(
            version=WAV2VEC2_CTC_OBSERVER_VERSION,
            authority=WAV2VEC2_CTC_OBSERVER_AUTHORITY,
            model_id="fixture-model",
            model_revision="fixture-revision",
            sampling_rate=16000,
            frame_ms=20.0,
            blank_id=0,
            canonical_text="你好",
            alignment_text="你好",
            target_class_ids=(1, 2),
            frame_scores=scores,
            audio_duration_ms=180.0,
        )

    def test_outer_evidence_preserves_posterior_and_identity(self):
        evidence = build_outer_evidence(
            self._fixture_emission(),
            window_start_ms=1000,
            case_id="fixture-case",
            observer_id="xingyu-w2v2-fixture",
            observer_revision="fixture-revision",
            max_hypotheses=3,
        )
        self.assertEqual(evidence.viterbi.boundary_start_ms, 40)
        self.assertEqual(evidence.viterbi.boundary_end_ms, 160)
        self.assertEqual(evidence.start.boundary_id, "fixture-case:start")
        self.assertEqual(evidence.end.boundary_id, "fixture-case:end")
        self.assertEqual(evidence.start.hypotheses[0].boundary_ms, 1040)
        self.assertEqual(evidence.end.hypotheses[0].boundary_ms, 1160)
        self.assertEqual(evidence.start.correlation_group, XINGYU_XLSR53_CORRELATION_GROUP)
        self.assertFalse(evidence.start.automatic_mutation_allowed)
        self.assertAlmostEqual(
            sum(row.probability for row in evidence.start.hypotheses)
            + evidence.start.residual_probability,
            1.0,
            places=6,
        )

    def test_serialized_evidence_omits_large_scores_by_default(self):
        payload = build_outer_evidence(
            self._fixture_emission(),
            window_start_ms=0,
            case_id="fixture-case",
            observer_id="fixture-observer",
            observer_revision="fixture-revision",
        ).to_dict()
        self.assertNotIn("frame_scores", payload["emission"])
        self.assertEqual(payload["emission"]["frame_count"], 9)
        self.assertEqual(payload["emission"]["class_count"], 3)
        self.assertEqual(payload["emission"]["target_class_ids"], [1, 2])

    def test_emission_rejects_direct_mutation_authority(self):
        base = self._fixture_emission()
        invalid = Wav2Vec2CTCEmission(
            **{
                **base.__dict__,
                "automatic_mutation_allowed": True,
            }
        )
        with self.assertRaisesRegex(Wav2Vec2CTCObserverError, "cannot directly authorize"):
            invalid.validate()


if __name__ == "__main__":
    unittest.main()
