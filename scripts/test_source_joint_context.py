from __future__ import annotations

from copy import deepcopy
import tempfile
import unittest
from pathlib import Path

from lyric_aligner.alignment.source_context_hubertfa import (
    SOURCE_CONTEXT_HUBERTFA_ANCHORED_PATH_LEXICAL_ONLY_NO_AP_POLICY_ID,
    SOURCE_CONTEXT_POLICY_ANCHORED_PATH,
)
from lyric_aligner.alignment.hubertfa_time_bands import record_word_bands
from lyric_aligner.alignment.source_joint_context import (
    SOURCE_JOINT_CONTEXT_POLICY_ID,
    prepare_joint_pairs,
)


class SourceJointContextTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def _prepared(self, *, indices=(10, 20, 30, 40), qualified=None, intervals=None,
                  target_positions=((2, 20), (3, 30)), text_by_index=None, domain=(0, 10_000)):
        qualified = set(indices if qualified is None else qualified)
        intervals = intervals or {index: (1000 + ordinal * 1000, 1400 + ordinal * 1000)
                                  for ordinal, index in enumerate(indices)}
        text_by_index = text_by_index or {index: f"word{index}" for index in indices}
        packets = {}
        packet_rows = []
        for index in indices:
            packet = {
                "cache_key_sha256": f"packet-{index}",
                "selection_reason": "unique_full_context_candidate",
                "candidates": [{"candidate_id": f"candidate-{index}",
                                "source_interval_ms": list(intervals[index]),
                                "full_context_disambiguated": index in qualified}],
            }
            packets[index] = packet
            packet_rows.append({"canonical_line_index": index, "packet": packet})
        ledger = []
        for position, target_index in target_positions:
            ledger.append({"position": position, "target_index": target_index,
                           "source_packet": packets[target_index], "status": "unavailable", "reasons": []})
        return {
            "context_policy": SOURCE_CONTEXT_POLICY_ANCHORED_PATH,
            "policy_id": SOURCE_CONTEXT_HUBERTFA_ANCHORED_PATH_LEXICAL_ONLY_NO_AP_POLICY_ID,
            "occurrence_id": "occurrence", "source_observation_sha256": "a" * 64,
            "source_audio_path": "source.wav", "source_audio_sha256": "b" * 64,
            "source_search_domain": {"start_ms": domain[0], "end_ms": domain[1]},
            "canonical_lines": [{"canonical_line_index": index, "text": text_by_index[index]} for index in indices],
            "packets": packet_rows, "source_sequence": {"promotions": []}, "ledger": ledger,
        }

    def _dictionary(self, *words):
        path = self.root / ("dictionary-" + str(len(list(self.root.glob("dictionary-*.txt")))) + ".txt")
        path.write_text("".join(f"{word}\tW ER D\n" for word in words), encoding="utf-8")
        return path

    def test_exact_source_anchor_shortens_window_without_skipping_canonical_text(self):
        from lyric_aligner.alignment.source_joint_context import SOURCE_JOINT_EXACT_CONTEXT_POLICY_ID
        prepared = self._prepared(indices=(10, 20, 30, 40, 50), qualified={10, 50},
            intervals={10: (1000, 1400), 20: (2000, 2400), 30: (3000, 3400),
                       40: (4000, 4500), 50: (9000, 9400)},
            text_by_index={10: "left", 20: "alpha", 30: "bravo",
                           40: "this is a private show", 50: "unlisted"})
        dictionary = self._dictionary("left", "alpha", "bravo", "this", "is", "a", "private", "show")
        kwargs = {"cue_specs": self._cue_specs(prepared), "dictionary_path": dictionary}
        old = prepare_joint_pairs(prepared, [(2, 3)], **kwargs)
        self.assertEqual(old["records"], [])
        observed = [{"text": word, "start_ms": 4000 + i * 100,
                     "end_ms": 4100 + i * 100, "probability": .95}
                    for i, word in enumerate("this is a private show".split())]
        new = prepare_joint_pairs(prepared, [(2, 3)], observed_words=observed, **kwargs)
        self.assertEqual(new["joint_proposal_policy_id"], SOURCE_JOINT_EXACT_CONTEXT_POLICY_ID)
        self.assertEqual(len(new["records"]), 1)
        record = new["records"][0]
        self.assertEqual(record["segment_text"], ["left", "alpha", "bravo", "this is a private show"])
        self.assertEqual([a["canonical_line_index"] for a in record["context"]["qualified_source_anchors"]], [10])
        self.assertEqual([a["canonical_line_index"] for a in record["context"]["exact_source_anchors"]], [40])
        words = [w for segment in record["segment_lexical_units"] for w in segment]
        self.assertTrue(record_word_bands(record, words, source_pad_ms=1500))

    @staticmethod
    def _cue_specs(prepared):
        text_by_index = {item["canonical_line_index"]: item["text"] for item in prepared["canonical_lines"]}
        return [{"position": entry["position"], "canonical_character_ranges": [{
            "canonical_line_index": entry["target_index"], "start_char": 0,
            "end_char": len(text_by_index[entry["target_index"]]),
        }]} for entry in prepared["ledger"]]

    def test_builds_exact_two_output_anchored_path_record_without_mutating_prepared(self):
        prepared = self._prepared()
        before = deepcopy(prepared)
        result = prepare_joint_pairs(prepared, [(2, 3)], cue_specs=self._cue_specs(prepared),
                                     dictionary_path=self._dictionary("word10", "word20", "word30", "word40"))
        self.assertEqual(prepared, before)
        self.assertEqual(result["joint_proposal_policy_id"], SOURCE_JOINT_CONTEXT_POLICY_ID)
        self.assertEqual(result["pair_results"][0]["status"], "ready")
        self.assertEqual(len(result["records"]), 1)
        record = result["records"][0]
        self.assertEqual(record["record_id"], record["proposal_id"])
        self.assertEqual(record["joint_proposal_policy_id"], SOURCE_JOINT_CONTEXT_POLICY_ID)
        self.assertEqual(record["context_policy"], SOURCE_CONTEXT_POLICY_ANCHORED_PATH)
        self.assertEqual([item["position"] for item in record["target_outputs"]], [2, 3])
        self.assertEqual([item["target_segment_index"] for item in record["target_outputs"]], [1, 2])
        self.assertEqual(record["context"]["canonical_segment_indices"], [10, 20, 30, 40])
        self.assertEqual([item["canonical_line_index"] for item in record["context"]["qualified_source_anchors"]],
                         [10, 20, 30, 40])
        words = [unit for segment in record["segment_lexical_units"] for unit in segment]
        self.assertEqual(len(record_word_bands(record, words, source_pad_ms=1500)), len(words))

    def test_rejects_nonadjacent_positions_and_unproven_partial_cue(self):
        prepared = self._prepared(target_positions=((2, 20), (3, 30), (4, 40)))
        dictionary = self._dictionary("word10", "word20", "word30", "word40")
        nonadjacent = prepare_joint_pairs(prepared, [(2, 4)], cue_specs=self._cue_specs(prepared), dictionary_path=dictionary)
        self.assertEqual(nonadjacent["pair_results"][0]["reason"], "cue_positions_not_adjacent")
        partial = deepcopy(prepared)
        partial["ledger"][1].pop("target_index")
        rejected = prepare_joint_pairs(partial, [(2, 3)], cue_specs=self._cue_specs(prepared), dictionary_path=dictionary)
        self.assertEqual(rejected["pair_results"][0]["reason"], "cue_not_proven_complete_single_line")
        forged = deepcopy(prepared)
        raw_specs = self._cue_specs(forged)
        raw_specs[1]["canonical_character_ranges"][0]["end_char"] -= 1
        rejected_partial = prepare_joint_pairs(forged, [(2, 3)], cue_specs=raw_specs, dictionary_path=dictionary)
        self.assertEqual(rejected_partial["pair_results"][0]["reason"], "cue_not_proven_complete_single_line")

    def test_rejects_dictionary_gap_and_qualified_interior_contradiction(self):
        prepared = self._prepared()
        missing = prepare_joint_pairs(prepared, [(2, 3)], cue_specs=self._cue_specs(prepared),
                                      dictionary_path=self._dictionary("word10", "word20", "word40"))
        self.assertEqual(missing["pair_results"][0]["reason"], "dictionary_coverage_missing:word30")
        contradictory = self._prepared(intervals={10: (1000, 1400), 20: (1300, 1600),
                                                   30: (3000, 3400), 40: (4000, 4400)})
        rejected = prepare_joint_pairs(contradictory, [(2, 3)], cue_specs=self._cue_specs(contradictory),
                                       dictionary_path=self._dictionary("word10", "word20", "word30", "word40"))
        self.assertEqual(rejected["pair_results"][0]["reason"],
                         "qualified_interior_anchor_order_or_overlap_invalid")

    def test_rejects_existing_resource_caps_without_widening_anchors(self):
        indices = tuple(range(10, 160, 10))
        prepared = self._prepared(indices=indices, qualified={indices[0], indices[-1]},
                                  target_positions=((2, 20), (3, 30)))
        dictionary = self._dictionary(*(f"word{index}" for index in indices))
        capped = prepare_joint_pairs(prepared, [(2, 3)], cue_specs=self._cue_specs(prepared), dictionary_path=dictionary)
        self.assertEqual(capped["pair_results"][0]["reason"], "interior_line_cap_exceeded")

        window_prepared = self._prepared(qualified={10, 40}, intervals={10: (1000, 1200), 20: (2000, 2200),
                                                                              30: (3000, 3200), 40: (47_000, 48_000)},
                                         domain=(0, 60_000))
        window = prepare_joint_pairs(window_prepared, [(2, 3)], cue_specs=self._cue_specs(window_prepared),
                                     dictionary_path=self._dictionary("word10", "word20", "word30", "word40"))
        self.assertEqual(window["pair_results"][0]["reason"], "source_window_cap_exceeded")

        large_text = " ".join(["word"] * 257)
        lexical_prepared = self._prepared(text_by_index={10: large_text, 20: "word20", 30: "word30", 40: "word40"})
        lexical = prepare_joint_pairs(lexical_prepared, [(2, 3)], cue_specs=self._cue_specs(lexical_prepared),
                                      dictionary_path=self._dictionary("word", "word20", "word30", "word40"))
        self.assertEqual(lexical["pair_results"][0]["reason"], "lexical_unit_cap_exceeded")

    def test_missing_outer_anchor_rejects_the_pair(self):
        prepared = self._prepared(qualified={10, 20, 30})
        result = prepare_joint_pairs(prepared, [(2, 3)], cue_specs=self._cue_specs(prepared),
                                     dictionary_path=self._dictionary("word10", "word20", "word30", "word40"))
        self.assertEqual(result["pair_results"][0]["reason"], "nearest_qualified_outer_anchor_missing")


if __name__ == "__main__":
    unittest.main()
