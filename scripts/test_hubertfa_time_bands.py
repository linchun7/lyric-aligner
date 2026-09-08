from __future__ import annotations

import hashlib
import json
import unittest

import numpy as np

from lyric_aligner.alignment.hubertfa_time_bands import (
    EXACT_SOURCE_ANCHOR_POLICY_ID,
    HuBERTFATimeBandError,
    make_time_banded_decoder,
    record_word_bands,
    validate_word_bands,
)


class _ReferenceDecoder:
    """Small hermetic copy of the vendor recurrence surface used by the factory."""
    def __init__(self, *, vocab, sample_rate, hop_size):
        self.vocab, self.frame_length = vocab, hop_size / sample_rate

    @staticmethod
    def forward_pass(T, S, prob_log, edge_prob, curr, dp, ph_seq_id, prob3_pad_len=2):
        backtrack = np.full_like(dp, -1, dtype=np.int32)
        edge_log, no_edge = np.log(edge_prob + 1e-6), np.log(1 - edge_prob + 1e-6)
        reset = ph_seq_id == 0
        first = np.empty(S, dtype=np.float32)
        second = np.full(S, -np.inf, dtype=np.float32)
        third = np.full(S, -np.inf, dtype=np.float32)
        indices = np.arange(prob3_pad_len, S)
        prior = np.clip(indices - prob3_pad_len + 1, 0, S - 1)
        jump_allowed = (prior >= S - 1) | (ph_seq_id[prior] == 0)
        for time in range(1, T):
            emission, previous = prob_log[:, time], dp[:, time - 1]
            first[:] = previous + emission + no_edge[time]
            second[1:] = previous[:S - 1] + emission[:S - 1] + edge_log[time] + curr[:S - 1] * (T / S)
            jump = previous[:S - prob3_pad_len] + emission[:S - prob3_pad_len] + edge_log[time] + curr[:S - prob3_pad_len] * (T / S)
            third[indices] = np.where(jump_allowed, jump, -np.inf)
            candidates = np.vstack((first, second, third))
            moves = np.argmax(candidates, axis=0)
            dp[:, time], backtrack[:, time] = candidates[moves, np.arange(S)], moves
            stay = moves == 0
            np.maximum(curr, emission, out=curr, where=stay)
            np.copyto(curr, emission, where=~stay)
            curr[reset] = 0.0
            second[1:], third[indices] = -np.inf, -np.inf
        return dp, backtrack, curr


class HuBERTFATimeBandTests(unittest.TestCase):
    def setUp(self):
        self.vocab = {"vocab": {"SP": 0, "AA": 1, "BB": 2}}
        self.banded_type = make_time_banded_decoder(_ReferenceDecoder)

    def _inputs(self):
        T, S = 6, 3
        probs = np.array([[0.0] * T, [-0.2] * T, [-0.4] * T], dtype=np.float32)
        edges = np.full(T, 0.5, dtype=np.float32)
        ids = np.array([0, 1, 2], dtype=np.int32)
        dp = np.full((S, T), -np.inf, dtype=np.float32)
        dp[0, 0] = probs[0, 0]
        curr = np.full(S, -np.inf, dtype=np.float32)
        curr[0] = probs[0, 0]
        return T, S, probs, edges, curr, dp, ids

    def test_unbounded_mask_matches_vendor_recurrence(self):
        reference = _ReferenceDecoder(vocab=self.vocab, sample_rate=10, hop_size=1)
        banded = self.banded_type(vocab=self.vocab, sample_rate=10, hop_size=1)
        banded.state_bands = np.array([[-np.inf, np.inf]] * 3, dtype=float)
        expected = reference.forward_pass(*self._inputs())
        actual = banded.forward_pass(*self._inputs())
        for left, right in zip(expected, actual):
            self.assertTrue(np.array_equal(left, right), (left, right))

    def test_destination_mask_applies_after_transition(self):
        decoder = self.banded_type(vocab=self.vocab, sample_rate=10, hop_size=1)
        decoder.state_bands = np.array([[-np.inf, np.inf], [0.2, 0.3], [-np.inf, np.inf]], dtype=float)
        dp, _backtrack, _curr = decoder.forward_pass(*self._inputs())
        self.assertFalse(np.isfinite(dp[1, 1]))  # frame time .1 outside destination state band
        self.assertTrue(np.isfinite(dp[1, 2]))   # frame time .2 is allowed

    def test_impossible_terminal_band_fails(self):
        decoder = self.banded_type(vocab=self.vocab, sample_rate=10, hop_size=1)
        decoder.state_bands = np.array([[-np.inf, np.inf], [-np.inf, np.inf], [2.0, 3.0]], dtype=float)
        with self.assertRaisesRegex(HuBERTFATimeBandError, "no_feasible_source_time_banded_path"):
            decoder.forward_pass(*self._inputs())

    def test_signed_bands_preserve_outer_only_target_without_an_own_gate(self):
        record = {
            "source_window_ms": [1000, 7000],
            "segment_lexical_units": [["left"], ["target"], ["right"]],
            "context": {
                "canonical_segment_indices": [10, 20, 30],
                "qualified_source_anchors": [
                    {"canonical_line_index": 10, "packet_cache_key_sha256": "a", "candidate_id": "a",
                     "source_interval_ms": [2000, 2200], "qualification": "selected_full_context"},
                    {"canonical_line_index": 30, "packet_cache_key_sha256": "b", "candidate_id": "b",
                     "source_interval_ms": [5000, 5200], "qualification": "selected_full_context"},
                ],
            },
        }
        bands = record_word_bands(record, ["left", "target", "right"], source_pad_ms=1500)
        self.assertEqual(set(bands), {0, 2})
        validate_word_bands([
            {"text": "left", "start": 0.0, "end": 2.7},
            {"text": "target", "start": 2.7, "end": 4.0},
            {"text": "right", "start": 3.5, "end": 5.7},
        ], ["left", "target", "right"], bands, frame_length=0.01)
        with self.assertRaisesRegex(HuBERTFATimeBandError, "word_time_band_postcheck_failed"):
            validate_word_bands([
                {"text": "left", "start": 0.0, "end": 4.0},
                {"text": "target", "start": 4.0, "end": 4.2},
                {"text": "right", "start": 4.2, "end": 5.7},
            ], ["left", "target", "right"], bands, frame_length=0.01)

    @staticmethod
    def _sha(value):
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                         separators=(",", ":")).encode("utf-8")).hexdigest()

    def _exact_record(self, *, source_window=(1000, 7000), old_anchors=None):
        if old_anchors is None:
            old_anchors = [
                {"canonical_line_index": 10, "packet_cache_key_sha256": "a", "candidate_id": "a",
                 "source_interval_ms": [2000, 2200], "qualification": "selected_full_context"},
                {"canonical_line_index": 30, "packet_cache_key_sha256": "b", "candidate_id": "b",
                 "source_interval_ms": [5000, 5200], "qualification": "selected_full_context"},
            ]
        identity_anchors = [
            {"canonical_line_index": 10, "source_interval_ms": [2000, 2200],
             "proof_ids": ["left-proof"]},
            {"canonical_line_index": 30, "source_interval_ms": [5000, 5200],
             "proof_ids": ["right-proof"]},
        ]
        identity_payload = {
            "canonical_lines_sha256": "c" * 64,
            "observed_words_sha256": "d" * 64,
            "source_observation_sha256": "a" * 64,
            "source_audio_sha256": "b" * 64,
            "source_domain_ms": [0, 8000],
            "policy_constants": {"policy_id": EXACT_SOURCE_ANCHOR_POLICY_ID},
            "qualified_anchors": [
                {"canonical_line_index": item["canonical_line_index"],
                 "source_interval_ms": list(item["source_interval_ms"]),
                 "proof_ids": list(item["proof_ids"])}
                for item in identity_anchors
            ],
        }
        input_identity = dict(identity_payload)
        input_identity["identity_sha256"] = self._sha(identity_payload)
        anchor = {
            "canonical_line_index": 20,
            "source_interval_ms": [3200, 3400],
            "observed_word_indices": [10],
            "lexical_units": ["target"],
            "probabilities": [0.92],
            "legacy_bracket": {
                "left": {"canonical_line_index": 10, "source_interval_ms": [2000, 2200],
                          "proof_ids": ["left-proof"]},
                "right": {"canonical_line_index": 30, "source_interval_ms": [5000, 5200],
                           "proof_ids": ["right-proof"]},
            },
        }
        anchor["anchor_identity_sha256"] = self._sha({
            "input_identity_sha256": input_identity["identity_sha256"],
            "anchor": anchor.copy(),
        })
        return {
            "source_audio_sha256": "b" * 64,
            "source_search_domain_end_ms": 8000,
            "source_window_ms": list(source_window),
            "segment_lexical_units": [["left"], ["target"], ["right"]],
            "context": {
                "canonical_segment_indices": [10, 20, 30],
                "qualified_source_anchors": old_anchors,
                "source_observation_sha256": "a" * 64,
                "exact_source_anchor_policy_id": EXACT_SOURCE_ANCHOR_POLICY_ID,
                "exact_source_anchors": [anchor],
                "exact_source_anchor_input_identity": input_identity,
                "exact_source_anchor_evidence_sha256": "e" * 64,
            },
        }

    def test_exact_anchor_adds_own_band_without_legacy_identity(self):
        record = self._exact_record()
        bands = record_word_bands(record, ["left", "target", "right"], source_pad_ms=1500)
        self.assertEqual(set(bands), {0, 1, 2})
        self.assertEqual(bands[1], (0.7, 3.9))

    def test_exact_anchor_requires_explicit_policy(self):
        record = self._exact_record()
        record["context"].pop("exact_source_anchor_policy_id")
        with self.assertRaisesRegex(HuBERTFATimeBandError, "exact_source_anchor_policy_invalid"):
            record_word_bands(record, ["left", "target", "right"], source_pad_ms=1500)

    def test_exact_anchor_binds_line_lexical_units(self):
        record = self._exact_record()
        record["context"]["exact_source_anchors"][0]["lexical_units"] = ["wrong"]
        with self.assertRaisesRegex(HuBERTFATimeBandError, "exact_source_anchor_lexical_units_mismatch"):
            record_word_bands(record, ["left", "target", "right"], source_pad_ms=1500)

    def test_exact_anchor_identity_is_verified(self):
        record = self._exact_record()
        record["context"]["exact_source_anchors"][0]["anchor_identity_sha256"] = "0" * 64
        with self.assertRaisesRegex(HuBERTFATimeBandError, "exact_source_anchor_identity_mismatch"):
            record_word_bands(record, ["left", "target", "right"], source_pad_ms=1500)

    def test_exact_anchor_rejects_overlap_with_legacy_anchor(self):
        record = self._exact_record()
        anchor = record["context"]["exact_source_anchors"][0]
        anchor["source_interval_ms"] = [2100, 2300]
        anchor["legacy_bracket"]["left"]["source_interval_ms"] = [1800, 1900]
        anchor_payload = dict(anchor)
        anchor_payload.pop("anchor_identity_sha256")
        input_identity = record["context"]["exact_source_anchor_input_identity"]
        input_identity["qualified_anchors"][0]["source_interval_ms"] = [1800, 1900]
        identity_payload = dict(input_identity)
        identity_payload.pop("identity_sha256")
        input_identity["identity_sha256"] = self._sha(identity_payload)
        anchor["anchor_identity_sha256"] = self._sha({
            "input_identity_sha256": input_identity["identity_sha256"],
            "anchor": anchor_payload,
        })
        with self.assertRaisesRegex(HuBERTFATimeBandError, "source_anchor_order_or_overlap_invalid"):
            record_word_bands(record, ["left", "target", "right"], source_pad_ms=1500)

    def test_exact_only_record_allows_bracket_outside_cropped_window(self):
        record = self._exact_record(source_window=(3000, 4500), old_anchors=[])
        bands = record_word_bands(record, ["left", "target", "right"], source_pad_ms=1500)
        self.assertEqual(set(bands), {1})


if __name__ == "__main__":
    unittest.main()
