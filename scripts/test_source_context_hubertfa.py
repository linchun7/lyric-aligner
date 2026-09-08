from __future__ import annotations

import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from lyric_aligner.alignment.source_context_hubertfa import (
    SourceContextHuBERTFAError,
    SourceContextHuBERTFAConfig,
    SOURCE_CONTEXT_HUBERTFA_ACOUSTIC_POLICY_LEXICAL_ONLY_NO_AP,
    SOURCE_CONTEXT_HUBERTFA_LEXICAL_ONLY_NO_AP_POLICY_ID,
    SOURCE_CONTEXT_HUBERTFA_ANCHORED_BLOCK_LEXICAL_ONLY_NO_AP_POLICY_ID,
    SOURCE_CONTEXT_HUBERTFA_ANCHORED_PATH_LEXICAL_ONLY_NO_AP_POLICY_ID,
    SOURCE_CONTEXT_POLICY_ANCHORED_BLOCK,
    SOURCE_CONTEXT_POLICY_ANCHORED_PATH,
    SOURCE_CONTEXT_POLICY_THREE_LINE,
    SOURCE_CONTEXT_BLOCK_MAX_INTERIOR_LINES,
    SOURCE_CONTEXT_BLOCK_MAX_LEXICAL_UNITS,
    acoustic_policy_contract,
    batch_id_matches_request,
    build_batch_request,
    compact_prepared_occurrence,
    finalize_records,
    json_sha,
    prepare_occurrence,
    prepare_anchored_blocks,
)
from lyric_aligner.contracts.artifacts import sha256_file


class SourceContextHuBERTFATests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.audio = self.root / "source.wav"
        self.audio.write_bytes(b"identity-only-source")
        self.audio_sha = sha256_file(self.audio)
        self.dictionary = self.root / "en.dict"
        self.dictionary.write_text("left\tL EH F T\nmiddle\tM IH D AH L\nright\tR AY T\n", encoding="utf-8")
        self.lines = [
            {"canonical_line_index": 10, "text": "left"},
            {"canonical_line_index": 20, "text": "middle"},
            {"canonical_line_index": 30, "text": "right"},
        ]
        self.source_sha = "a" * 64

    @staticmethod
    def _packet(index, candidate_specs):
        return {
            "cache_key_sha256": f"{index:064x}",
            "selection_reason": "unique_full_context_candidate",
            "candidates_truncated": False,
            "coverage": {"candidate_count_before_truncation": len(candidate_specs)},
            "candidates": [
                {"candidate_id": f"{index}:{ordinal}", "source_interval_ms": interval,
                 "full_context_disambiguated": complete,
                 "context_alignment": {"independent_context_support": True},
                 "source_interval_start_reason": "observed_positive_duration_word",
                 "source_interval_end_reason": "observed_positive_duration_word"}
                for ordinal, (interval, complete) in enumerate(candidate_specs)
            ],
        }

    def _builder(self, overrides=None, called=None):
        overrides = overrides or {}
        def build(**kwargs):
            index = kwargs["target_cue"]["canonical_character_ranges"][0]["canonical_line_index"]
            if called is not None:
                called.append(index)
            return self._packet(index, overrides.get(index, {
                10: [([1000, 1500], False)],
                20: [([2000, 2400], True)],
                30: [([3000, 3500], False)],
            }[index]))
        return build

    def _resolver(self, promotions=None, captured=None):
        def resolve(packets, *, source_observation_sha256):
            if captured is not None:
                captured["count"] = len(packets)
                captured["source_hashes"] = [packet.get("source_observation_sha256") for packet in packets]
            return {"status": "complete", "promotions": promotions or []}
        return resolve

    def _prepare(self, **kwargs):
        language = kwargs.pop("language", "en")
        return prepare_occurrence(
            occurrence_id="occ", source_audio_path=self.audio, source_audio_sha256=self.audio_sha,
            language=language, canonical_lines=self.lines,
            cue_specs=[{"position": 2, "baseline_interval_ms": [2100, 2300],
                        "canonical_character_ranges": [{"canonical_line_index": 20, "start_char": 0, "end_char": 6}]}],
            observed_words=[], source_observation_sha256=self.source_sha,
            source_search_domain={"start_ms": 0, "end_ms": 7000, "domain_id": "whole"},
            lyric_version="lyrics", model_id="observed", dictionary_path=self.dictionary, **kwargs,
        )

    def test_uses_all_original_lines_and_independent_neighbor_packets(self):
        called, captured = [], {}
        prepared = self._prepare(packet_builder=self._builder(called=called), sequence_resolver=self._resolver(captured=captured))
        self.assertEqual(called, [10, 20, 30])
        self.assertEqual(captured, {"count": 3, "source_hashes": [self.source_sha] * 3})
        record = prepared["records"][0]
        self.assertEqual(record["source_window_ms"], [0, 5000])
        context = record["context"]
        self.assertEqual((context["left_index"], context["target_index"], context["right_index"]), (10, 20, 30))
        self.assertEqual(context["line_intervals_ms"], {"10": [1000, 1500], "20": [2000, 2400], "30": [3000, 3500]})

    def test_only_explicit_duplicate_packet_uses_sequence_promotion(self):
        overrides = {10: [([1000, 1500], False)], 20: [([2000, 2400], True), ([4200, 4500], True)],
                     30: [([5000, 5500], False)]}
        promoted_packet = f"{20:064x}"
        def builder(**kwargs):
            packet = self._builder(overrides)(**kwargs)
            if kwargs["target_cue"]["canonical_character_ranges"][0]["canonical_line_index"] == 20:
                packet["selection_reason"] = "ambiguous_canonical_packet_identity"
            return packet
        prepared = self._prepare(packet_builder=builder,
            sequence_resolver=self._resolver([{"packet_cache_key_sha256": promoted_packet, "candidate_id": "20:1"}]))
        self.assertEqual(prepared["records"][0]["source_packet_candidate_id"], "20:1")

    def test_ordinary_ambiguous_target_cannot_consume_promotion(self):
        overrides = {10: [([1000, 1500], False)], 20: [([2000, 2400], True), ([4200, 4500], True)],
                     30: [([5000, 5500], False)]}
        def builder(**kwargs):
            packet = self._builder(overrides)(**kwargs)
            if kwargs["target_cue"]["canonical_character_ranges"][0]["canonical_line_index"] == 20:
                packet["selection_reason"] = "multiple_full_context_candidates"
            return packet
        prepared = self._prepare(packet_builder=builder,
            sequence_resolver=self._resolver([{"packet_cache_key_sha256": f"{20:064x}", "candidate_id": "20:1"}]))
        self.assertEqual(prepared["records"], [])
        self.assertEqual(prepared["ledger"][0]["reasons"], ["source_packet_no_unique_full_context_candidate"])

    def test_three_line_source_order_or_overlap_is_rejected(self):
        overrides = {10: [([1000, 1500], False)], 20: [([1400, 1800], True)],
                     30: [([3000, 3500], False)]}
        prepared = self._prepare(packet_builder=self._builder(overrides), sequence_resolver=self._resolver())
        self.assertEqual(prepared["records"], [])
        self.assertEqual(prepared["ledger"][0]["reasons"], ["three_line_source_order_or_overlap_invalid"])

    def test_non_english_context_is_unavailable_without_using_detected_track_language(self):
        lines = [dict(item) for item in self.lines]
        lines[0]["text"] = "안녕"
        prepared = prepare_occurrence(
            occurrence_id="occ", source_audio_path=self.audio, source_audio_sha256=self.audio_sha,
            language="en", canonical_lines=lines,
            cue_specs=[{"position": 2, "baseline_interval_ms": [2100, 2300],
                        "canonical_character_ranges": [{"canonical_line_index": 20, "start_char": 0, "end_char": 6}]}],
            observed_words=[], source_observation_sha256=self.source_sha,
            source_search_domain={"start_ms": 0, "end_ms": 7000}, lyric_version="lyrics", model_id="observed",
            dictionary_path=self.dictionary, packet_builder=self._builder(), sequence_resolver=self._resolver())
        self.assertEqual(prepared["ledger"][0]["reasons"], ["provider_text_not_verified_en"])

    def test_finalization_rejects_nonfinite_and_cut_crossing_observations(self):
        record = {"record_id": "one", "source_window_ms": [1000, 6000],
                  "segment_lexical_units": [["left"], ["middle"], ["right"]]}
        affine = {"kind": "AFFINE", "intercept": 0.0, "base_slope": 1.0, "breakpoints": [], "slope_deltas": []}
        observed = {"record_id": "one", "status": "aligned", "words": [
            {"text": "left", "start": 0.1, "end": 0.5},
            {"text": "middle", "start": 0.8, "end": 1.2},
            {"text": "right", "start": 1.7, "end": 2.1},
        ]}
        result = finalize_records([record], [observed], effective_mapping=affine)["one"]
        self.assertEqual(result["status"], "observed_complete_interval")
        self.assertEqual(result["source_interval_ms"], [1800, 2200])
        nonfinite = dict(observed, words=[dict(word) for word in observed["words"]])
        nonfinite["words"][1]["end"] = float("nan")
        self.assertEqual(finalize_records([record], [nonfinite], effective_mapping=affine)["one"]["reason"],
                         "adapter_nonfinite_word_time")

    def test_strict_config_frontend_language(self):
        with self.assertRaises(SourceContextHuBERTFAError):
            self._prepare(language="zh", packet_builder=self._builder(), sequence_resolver=self._resolver())

    def test_lexical_only_no_ap_policy_is_explicit_and_changes_prepared_identity(self):
        default = self._prepare(packet_builder=self._builder(), sequence_resolver=self._resolver())
        lexical_only = self._prepare(policy_id=SOURCE_CONTEXT_HUBERTFA_LEXICAL_ONLY_NO_AP_POLICY_ID,
            packet_builder=self._builder(), sequence_resolver=self._resolver())
        self.assertEqual(lexical_only["policy_id"], SOURCE_CONTEXT_HUBERTFA_LEXICAL_ONLY_NO_AP_POLICY_ID)
        self.assertNotEqual(default["records"][0]["record_id"], lexical_only["records"][0]["record_id"])
        self.assertEqual(acoustic_policy_contract(SOURCE_CONTEXT_HUBERTFA_ACOUSTIC_POLICY_LEXICAL_ONLY_NO_AP)["request_fields"], {
            "acoustic_policy": "lexical_only_no_ap", "non_lexical_phonemes": "", "pad_times": 3, "pad_length": 3})

    def test_lexical_only_no_ap_request_binds_vendor_arguments(self):
        config = SourceContextHuBERTFAConfig(
            mode="report-only", acoustic_policy=SOURCE_CONTEXT_HUBERTFA_ACOUSTIC_POLICY_LEXICAL_ONLY_NO_AP,
            adapter_path=self.audio, model_path=self.audio, model_config_path=self.audio,
            model_version_path=self.audio, model_vocab_path=self.audio, dictionary_path=self.audio,
            runtime_path=Path(sys.executable).resolve())
        with patch.object(SourceContextHuBERTFAConfig, "vendor_files", return_value={}):
            request = build_batch_request(config, [])
        self.assertEqual(request["policy_id"], SOURCE_CONTEXT_HUBERTFA_LEXICAL_ONLY_NO_AP_POLICY_ID)
        self.assertEqual({key: request[key] for key in ("acoustic_policy", "non_lexical_phonemes", "pad_times", "pad_length")},
                         {"acoustic_policy": "lexical_only_no_ap", "non_lexical_phonemes": "", "pad_times": 3, "pad_length": 3})
        self.assertTrue(batch_id_matches_request(request))

    def test_job_acoustic_policy_selector_is_finite_and_opt_in(self):
        binding = {"path": str(self.audio), "sha256": "0" * 64}
        block = {"enabled": True, "mode": "report-only", "adapter": binding, "model": binding,
                 "model_config": binding, "model_version": binding, "model_vocab": binding,
                 "dictionary": binding, "runtime": {"path": str(Path(sys.executable).resolve())}}
        parse = lambda value: SourceContextHuBERTFAConfig.from_job(value, resolve_path=Path,
            bind_file=lambda item, _label: Path(item["path"]))
        default = parse(block)
        self.assertEqual(default.acoustic_request_fields(), {})
        lexical = parse({**block, "acoustic_policy": "lexical_only_no_ap"})
        self.assertEqual(lexical.policy_id, SOURCE_CONTEXT_HUBERTFA_LEXICAL_ONLY_NO_AP_POLICY_ID)
        with self.assertRaises(SourceContextHuBERTFAError):
            parse({**block, "acoustic_policy": "invent-a-knob"})

    def test_compact_prepared_occurrence_omits_full_candidate_lattice(self):
        prepared = self._prepare(packet_builder=self._builder(), sequence_resolver=self._resolver())
        compact = compact_prepared_occurrence(prepared)
        self.assertEqual(compact["schema_version"], "source-context-hubertfa-prepared-occurrence-1.1-compact")
        self.assertNotIn("packets", compact)
        self.assertTrue(compact["packet_materialization"]["full_candidates_retained_during_computation"])
        self.assertFalse(compact["packet_materialization"]["full_packets_materialized_in_this_artifact"])
        summaries = compact["packet_materialization"]["compact_packets"]
        self.assertEqual(len(summaries), 3)
        self.assertTrue(all("candidates" not in item for item in summaries))
        self.assertTrue(all("source_packet" not in item for item in compact["ledger"]))
        self.assertEqual(compact["canonical_lines"], self.lines)

    def _prepare_block(self, *, lines=None, packets=None, promotions=None, cue_indices=None,
                       context_policy=SOURCE_CONTEXT_POLICY_ANCHORED_BLOCK):
        lines = lines or [
            {"canonical_line_index": index, "text": "left"}
            for index in (10, 20, 30, 40, 50)
        ]
        cue_indices = cue_indices if cue_indices is not None else [item["canonical_line_index"] for item in lines[1:-1]]
        packet_specs = packets or {
            10: [([1000, 1500], True)],
            20: [([2000, 2400], False)],
            30: [([3000, 3400], False)],
            40: [([4000, 4400], False)],
            50: [([5000, 5500], True)],
        }
        def build(**kwargs):
            index = kwargs["target_cue"]["canonical_character_ranges"][0]["canonical_line_index"]
            packet = self._packet(index, packet_specs[index])
            if index == 10 and promotions:
                packet["selection_reason"] = "ambiguous_canonical_packet_identity"
            return packet
        cue_specs = [{"position": position, "baseline_interval_ms": [2000 + position * 100, 2050 + position * 100],
                      "canonical_character_ranges": [{"canonical_line_index": index, "start_char": 0,
                                                        "end_char": len(next(item["text"] for item in lines if item["canonical_line_index"] == index))}]}
                     for position, index in enumerate(cue_indices, 1)]
        policy_id = (SOURCE_CONTEXT_HUBERTFA_ANCHORED_PATH_LEXICAL_ONLY_NO_AP_POLICY_ID
                     if context_policy == SOURCE_CONTEXT_POLICY_ANCHORED_PATH
                     else SOURCE_CONTEXT_HUBERTFA_ANCHORED_BLOCK_LEXICAL_ONLY_NO_AP_POLICY_ID)
        prepared = prepare_occurrence(
            occurrence_id="block", source_audio_path=self.audio, source_audio_sha256=self.audio_sha,
            language="en", canonical_lines=lines, cue_specs=cue_specs, observed_words=[],
            source_observation_sha256=self.source_sha,
            source_search_domain={"start_ms": 0, "end_ms": 70_000, "domain_id": "whole"},
            lyric_version="lyrics", model_id="observed", dictionary_path=self.dictionary,
            policy_id=policy_id, context_policy=context_policy, packet_builder=build,
            sequence_resolver=self._resolver(promotions))
        return prepared, prepare_anchored_blocks(prepared, dictionary_path=self.dictionary)

    def test_anchored_block_recovers_missing_three_line_targets_and_fans_out_only_interiors(self):
        prepared, blocks = self._prepare_block()
        self.assertEqual(prepared["records"], [])
        self.assertEqual(blocks["missing_three_line_target_count"], 3)
        self.assertEqual(len(blocks["records"]), 1)
        record = blocks["records"][0]
        self.assertEqual(record["context_policy"], SOURCE_CONTEXT_POLICY_ANCHORED_BLOCK)
        self.assertEqual(record["source_window_ms"], [0, 7000])
        self.assertEqual(record["context"]["canonical_segment_indices"], [10, 20, 30, 40, 50])
        self.assertEqual([item["target_segment_index"] for item in record["target_outputs"]], [1, 2, 3])
        self.assertEqual([item["canonical_line_index"] for item in record["target_outputs"]], [20, 30, 40])
        self.assertEqual([item["qualification"] for item in record["context"]["outer_anchor_candidates"]],
                         ["selected_full_context", "selected_full_context"])
        self.assertTrue(all(entry["status"] == "ready_for_hubertfa_anchored_block" for entry in prepared["ledger"]))
        self.assertNotIn(10, [item["canonical_line_index"] for item in record["target_outputs"]])
        self.assertNotIn(50, [item["canonical_line_index"] for item in record["target_outputs"]])

    def test_anchored_block_accepts_only_explicit_duplicate_sequence_anchor_promotion(self):
        packets = {
            10: [([900, 1400], True), ([1000, 1500], True)],
            20: [([2000, 2400], False)], 30: [([3000, 3400], False)],
            40: [([4000, 4400], False)], 50: [([5000, 5500], True)],
        }
        prepared, blocks = self._prepare_block(packets=packets, promotions=[{
            "packet_cache_key_sha256": f"{10:064x}", "candidate_id": "10:1"}])
        self.assertEqual(len(blocks["records"]), 1)
        anchor = blocks["records"][0]["context"]["outer_anchor_candidates"][0]
        self.assertEqual(anchor["candidate_id"], "10:1")
        self.assertEqual(anchor["qualification"], "all_optimal_duplicate_promotion")
        self.assertEqual(prepared["source_sequence"]["promotions"][0]["candidate_id"], "10:1")

    def test_anchored_block_rejects_outer_anchor_overlap_without_widening(self):
        packets = {
            10: [([1000, 2200], True)], 20: [([2300, 2400], False)],
            30: [([3000, 3400], False)], 40: [([4000, 4400], False)],
            50: [([2000, 5500], True)],
        }
        prepared, blocks = self._prepare_block(packets=packets)
        self.assertEqual(blocks["records"], [])
        self.assertTrue(all(entry["anchored_block_rejection"] == "outer_anchor_order_or_overlap_invalid"
                            for entry in prepared["ledger"]))

    def test_anchored_block_does_not_expand_to_farther_anchors_after_nearest_pair_fails(self):
        lines = [{"canonical_line_index": index, "text": "left"} for index in (10, 20, 30, 40, 50)]
        # The immediate anchor pair around 30 overlaps.  Although 10/50 is a
        # valid wider pair, using it would tune context after the failed pair.
        packets = {
            10: [([500, 900], True)], 20: [([2000, 2500], True)],
            30: [([3000, 3400], False)], 40: [([2400, 2700], True)],
            50: [([5000, 5500], True)],
        }
        prepared, blocks = self._prepare_block(lines=lines, packets=packets, cue_indices=[30])
        self.assertEqual(blocks["records"], [])
        self.assertEqual(prepared["ledger"][0]["anchored_block_rejection"],
                         "outer_anchor_order_or_overlap_invalid")

    def test_anchored_block_enforces_fixed_interior_and_lexical_caps(self):
        indices = list(range(10, 10 + SOURCE_CONTEXT_BLOCK_MAX_INTERIOR_LINES + 3))
        lines = [{"canonical_line_index": index, "text": "left"} for index in indices]
        packets = {index: [([1000 + offset * 1000, 1400 + offset * 1000],
                            offset in (0, len(indices) - 1))]
                   for offset, index in enumerate(indices)}
        prepared, blocks = self._prepare_block(lines=lines, packets=packets, cue_indices=indices[1:-1])
        self.assertEqual(blocks["records"], [])
        self.assertTrue(all(entry["anchored_block_rejection"] == "interior_line_cap_exceeded" for entry in prepared["ledger"]))
        words = " ".join(["left"] * (SOURCE_CONTEXT_BLOCK_MAX_LEXICAL_UNITS - 1))
        lexical_lines = [
            {"canonical_line_index": 10, "text": "left"},
            {"canonical_line_index": 20, "text": words},
            {"canonical_line_index": 30, "text": "left"},
        ]
        lexical_packets = {10: [([1000, 1500], True)], 20: [([2000, 2400], False)], 30: [([3000, 3500], True)]}
        prepared, blocks = self._prepare_block(lines=lexical_lines, packets=lexical_packets, cue_indices=[20])
        self.assertEqual(blocks["records"], [])
        self.assertEqual(prepared["ledger"][0]["anchored_block_rejection"], "lexical_unit_cap_exceeded")

    def test_anchored_block_policy_is_explicit_and_legacy_three_line_identity_stays_unchanged(self):
        config = SourceContextHuBERTFAConfig(
            mode="report-only", acoustic_policy=SOURCE_CONTEXT_HUBERTFA_ACOUSTIC_POLICY_LEXICAL_ONLY_NO_AP,
            adapter_path=self.audio, model_path=self.audio, model_config_path=self.audio,
            model_version_path=self.audio, model_vocab_path=self.audio, dictionary_path=self.audio,
            runtime_path=Path(sys.executable).resolve(), context_policy=SOURCE_CONTEXT_POLICY_ANCHORED_BLOCK)
        with patch.object(SourceContextHuBERTFAConfig, "vendor_files", return_value={}):
            request = build_batch_request(config, [])
        self.assertEqual(request["policy_id"], SOURCE_CONTEXT_HUBERTFA_ANCHORED_BLOCK_LEXICAL_ONLY_NO_AP_POLICY_ID)
        self.assertEqual(request["context_policy"], SOURCE_CONTEXT_POLICY_ANCHORED_BLOCK)
        self.assertEqual(request["block_max_interior_lines"], SOURCE_CONTEXT_BLOCK_MAX_INTERIOR_LINES)
        with self.assertRaises(SourceContextHuBERTFAError):
            SourceContextHuBERTFAConfig(
                mode="report-only", acoustic_policy="ap_default", adapter_path=self.audio, model_path=self.audio,
                model_config_path=self.audio, model_version_path=self.audio, model_vocab_path=self.audio,
                dictionary_path=self.audio, runtime_path=Path(sys.executable).resolve(),
                context_policy=SOURCE_CONTEXT_POLICY_ANCHORED_BLOCK).policy_id
        default = SourceContextHuBERTFAConfig(
            mode="report-only", acoustic_policy=SOURCE_CONTEXT_HUBERTFA_ACOUSTIC_POLICY_LEXICAL_ONLY_NO_AP,
            adapter_path=self.audio, model_path=self.audio, model_config_path=self.audio,
            model_version_path=self.audio, model_vocab_path=self.audio, dictionary_path=self.audio,
            runtime_path=Path(sys.executable).resolve(), context_policy=SOURCE_CONTEXT_POLICY_THREE_LINE)
        self.assertEqual(default.policy_id, SOURCE_CONTEXT_HUBERTFA_LEXICAL_ONLY_NO_AP_POLICY_ID)

    def test_anchored_path_binds_all_qualified_block_anchors_and_keeps_outer_only_targets(self):
        packets = {
            10: [([1000, 1500], True)],
            # A two-candidate immediate left neighbor blocks three-line FA for
            # target 30, while target 30 itself is still a qualified anchor.
            20: [([2000, 2400], False), ([2050, 2450], False)],
            30: [([3000, 3400], True)],
            40: [([4000, 4400], False)],
            50: [([5000, 5500], True)],
        }
        prepared, blocks = self._prepare_block(packets=packets, cue_indices=[30],
            context_policy=SOURCE_CONTEXT_POLICY_ANCHORED_PATH)
        self.assertEqual(prepared["records"], [])
        self.assertEqual(len(blocks["records"]), 1)
        record = blocks["records"][0]
        self.assertEqual(record["context_policy"], SOURCE_CONTEXT_POLICY_ANCHORED_PATH)
        self.assertEqual([item["canonical_line_index"] for item in record["context"]["qualified_source_anchors"]],
                         [10, 30, 50])
        self.assertTrue(prepared["ledger"][0]["time_band_own_anchor"])
        self.assertEqual(blocks["anchored_path_target_with_own_band_count"], 1)
        self.assertEqual(blocks["anchored_path_target_outer_only_band_count"], 0)
        self.assertNotEqual(record["record_id"], json_sha({"policy": "old-block"}))

    def test_anchored_path_policy_requires_decoder_binding_and_is_opt_in(self):
        binding = {"path": str(self.audio), "sha256": "0" * 64}
        base = {"enabled": True, "mode": "report-only", "adapter": binding, "model": binding,
                "model_config": binding, "model_version": binding, "model_vocab": binding,
                "dictionary": binding, "runtime": {"path": str(Path(sys.executable).resolve())},
                "acoustic_policy": "lexical_only_no_ap", "context_policy": "anchored-path-v1"}
        parse = lambda value: SourceContextHuBERTFAConfig.from_job(value, resolve_path=Path,
            bind_file=lambda item, _label: Path(item["path"]))
        with self.assertRaises(SourceContextHuBERTFAError):
            parse(base)
        config = parse({**base, "time_band_decoder": binding})
        with patch.object(SourceContextHuBERTFAConfig, "vendor_files", return_value={}):
            request = build_batch_request(config, [])
        self.assertEqual(request["policy_id"], SOURCE_CONTEXT_HUBERTFA_ANCHORED_PATH_LEXICAL_ONLY_NO_AP_POLICY_ID)
        self.assertEqual(request["context_policy"], SOURCE_CONTEXT_POLICY_ANCHORED_PATH)
        self.assertIn("time_band_decoder", request)
        self.assertTrue(batch_id_matches_request(request))
        derived = parse({**base, "time_band_decoder": binding, "dictionary_manifest": binding})
        with patch.object(SourceContextHuBERTFAConfig, "vendor_files", return_value={}):
            derived_request = build_batch_request(derived, [])
        self.assertEqual(derived_request["dictionary_manifest"]["path"], str(self.audio))
        self.assertTrue(batch_id_matches_request(derived_request))
        with self.assertRaisesRegex(SourceContextHuBERTFAError, "dictionary_manifest is only valid"):
            parse({**base, "context_policy": SOURCE_CONTEXT_POLICY_THREE_LINE,
                   "dictionary_manifest": binding})

    def test_batch_id_binds_tokens_and_windows(self):
        request = {"protocol_version": "test", "records": [{"source_window_ms": [10, 20],
            "segment_lexical_units": [["left"], ["middle"], ["right"]]}]}
        request["batch_id"] = json_sha(request)
        self.assertTrue(batch_id_matches_request(request))
        request["records"][0]["source_window_ms"][1] = 21
        self.assertFalse(batch_id_matches_request(request))


if __name__ == "__main__":
    unittest.main()
