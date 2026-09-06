import tempfile
import unittest
from pathlib import Path

import numpy as np

from lyric_aligner.audio import mandarin_lyricalignment_observer as mla


class MandarinLyricAlignmentObserverTests(unittest.TestCase):
    def test_normalize_trusted_text_preserves_lyrics_but_drops_spacing_punctuation(self):
        self.assertEqual(mla.normalize_trusted_mandarin_text("你 好，世界！"), "你好世界")
        with self.assertRaisesRegex(mla.MandarinLyricAlignmentObserverError, "no alignable"):
            mla.normalize_trusted_mandarin_text("，！ …")

    def test_vocab_loader_preserves_real_u2028_entry_and_id_offsets(self):
        entries = [f"v{index}" for index in range(mla.CTC_CLASS_COUNT)]
        entries[343] = "\u2028"
        entries[344] = "你"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "vocab.txt"
            path.write_text("\n".join(entries) + "\n", encoding="utf-8", newline="\n")
            loaded = mla.load_frozen_bert_vocabulary(path)
        self.assertEqual(len(loaded), mla.CTC_CLASS_COUNT)
        self.assertEqual(loaded[343], "\u2028")
        self.assertEqual(loaded[344], "你")

    def test_exact_vocab_to_pronunciation_class_mapping_and_unknown_fail_closed(self):
        vocabulary = [f"v{index}" for index in range(mla.CTC_CLASS_COUNT)]
        pronunciation = ["bad"] * mla.CTC_CLASS_COUNT
        vocabulary[100] = "你"
        vocabulary[101] = "我"
        pronunciation[100] = "ni"
        pronunciation[101] = "wo"
        mapped = mla.map_text_to_pronunciation_classes(
            "你我",
            vocabulary,
            pronunciation,
            {"ni": 12, "wo": 13},
        )
        self.assertEqual(mapped.bert_vocab_ids, (100, 101))
        self.assertEqual(mapped.pronunciations, ("ni", "wo"))
        self.assertEqual(mapped.target_class_ids, (12, 13))
        with self.assertRaisesRegex(mla.MandarinLyricAlignmentObserverError, "unavailable"):
            mla.map_text_to_pronunciation_classes(
                "你他", vocabulary, pronunciation, {"ni": 12, "wo": 13}
            )

    def test_official_ctc_transform_matches_silence_voiced_formula(self):
        raw = np.zeros((2, mla.RAW_OUTPUT_DIM), dtype=np.float64)
        transformed = mla.official_ctc_log_probabilities(raw)
        probabilities = np.exp(transformed)
        self.assertEqual(transformed.shape, (2, mla.CTC_CLASS_COUNT))
        self.assertTrue(np.allclose(probabilities.sum(axis=1), 1.0, atol=1e-12))
        self.assertTrue(np.allclose(probabilities[:, 0], 0.5, atol=1e-12))
        expected_label = 0.5 / float(mla.CTC_CLASS_COUNT - 1)
        self.assertAlmostEqual(float(probabilities[0, 1]), expected_label, places=14)
        self.assertAlmostEqual(float(probabilities[0, -1]), expected_label, places=14)

        raw[0, -1] = 8.0
        raw[1, -1] = -8.0
        probabilities = np.exp(mla.official_ctc_log_probabilities(raw))
        self.assertGreater(probabilities[0, 0], 0.999)
        self.assertLess(probabilities[1, 0], 0.001)

    def test_official_ctc_transform_rejects_wrong_shape_and_nonfinite(self):
        with self.assertRaisesRegex(mla.MandarinLyricAlignmentObserverError, "21129"):
            mla.official_ctc_log_probabilities(np.zeros((2, 10)))
        raw = np.zeros((1, mla.RAW_OUTPUT_DIM), dtype=np.float64)
        raw[0, 5] = np.nan
        with self.assertRaisesRegex(mla.MandarinLyricAlignmentObserverError, "non-finite"):
            mla.official_ctc_log_probabilities(raw)

    def _emission(self, *, automatic_mutation_allowed=False):
        probabilities = np.full((7, mla.CTC_CLASS_COUNT), 1e-12, dtype=np.float64)
        desired = [0, 12, 12, 0, 13, 13, 0]
        for frame, class_id in enumerate(desired):
            probabilities[frame, class_id] = 0.99
            probabilities[frame] /= probabilities[frame].sum()
        return mla.MandarinLyricAlignmentEmission(
            version=mla.OBSERVER_VERSION,
            authority=mla.OBSERVER_AUTHORITY,
            model_id=mla.MODEL_ID,
            model_revision="m" * 64,
            source_revision=mla.SOURCE_REVISION,
            sampling_rate=mla.SAMPLING_RATE,
            frame_ms=mla.FRAME_MS,
            blank_id=mla.BLANK_ID,
            canonical_text="你我",
            alignment_text="你我",
            bert_vocab_ids=(100, 101),
            pronunciations=("ni", "wo"),
            target_class_ids=(12, 13),
            frame_scores=np.log(probabilities),
            audio_duration_ms=140.0,
            automatic_mutation_allowed=automatic_mutation_allowed,
        )

    def test_build_outer_evidence_keeps_observer_only_nbest_contract(self):
        emission = self._emission()
        evidence = mla.build_outer_evidence(
            emission,
            window_start_ms=1000,
            case_id="case-a",
            observer_revision="r" * 64,
        )
        self.assertEqual(evidence.start.observer_id, "mandarin_lyricalignment_asru2023_ctc")
        self.assertEqual(evidence.start.correlation_group, mla.CORRELATION_GROUP)
        self.assertEqual(evidence.start.authority, mla.OBSERVER_AUTHORITY)
        self.assertTrue(evidence.start.hypotheses)
        self.assertTrue(evidence.end.hypotheses)
        self.assertFalse(evidence.start.automatic_mutation_allowed)
        self.assertFalse(evidence.end.automatic_mutation_allowed)
        serialized = evidence.to_dict()
        self.assertNotIn("frame_scores", serialized["emission"])
        self.assertEqual(serialized["emission"]["frame_count"], 7)
        self.assertEqual(serialized["emission"]["class_count"], mla.CTC_CLASS_COUNT)

    def test_direct_mutation_authority_is_rejected(self):
        with self.assertRaisesRegex(mla.MandarinLyricAlignmentObserverError, "cannot directly authorize"):
            self._emission(automatic_mutation_allowed=True).validate()


if __name__ == "__main__":
    unittest.main()
