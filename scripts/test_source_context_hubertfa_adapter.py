from __future__ import annotations

import json
import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

from lyric_aligner.alignment.source_context_hubertfa import json_sha
from lyric_aligner.contracts.artifacts import sha256_file
from lyric_aligner.text.english_lexicon import build_english_lexicon
from scripts import source_context_hubertfa_adapter as adapter


class SourceContextHuBERTFAAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        model = self.root / "model"
        model.mkdir()
        fixture_files = {
            "model.onnx": b"fixture-model",
            "config.json": b"{}\n",
            "VERSION": b"fixture\n",
            "vocab.json": b"{}\n",
            "ds_cmudict-07b.txt": b"left\tL EH F T\nmiddle\tM IH D AH L\nright\tR AY T\n",
        }
        for name, content in fixture_files.items():
            (model / name).write_bytes(content)
        vendor = self.root / "vendor"
        vendor.mkdir()
        (vendor / "onnx_infer.py").write_text("# hermetic adapter test fixture\n", encoding="utf-8")
        (vendor / "device_utils.py").write_text("# hermetic adapter test fixture\n", encoding="utf-8")
        self.model = model
        self.patches = patch.multiple(adapter,
            MODEL=model / "model.onnx", MODEL_CONFIG=model / "config.json", MODEL_VERSION=model / "VERSION",
            MODEL_VOCAB=model / "vocab.json", EN_DICTIONARY=model / "ds_cmudict-07b.txt",
            VENDOR_ONNX_INFER=vendor / "onnx_infer.py", VENDOR_DEVICE_UTILS=vendor / "device_utils.py")
        self.patches.start()
        self.addCleanup(self.patches.stop)

    def _record(self, audio: Path, *, record_id="one", domain_end_ms=1000):
        return {"record_id": record_id, "language": "en", "source_audio_path": str(audio),
            "source_audio_sha256": sha256_file(audio), "source_window_ms": [100, 900],
            "source_search_domain_end_ms": domain_end_ms,
            "segment_lexical_units": [["left"], ["middle"], ["right"]],
            "lexical_units_sha256": json_sha([["left"], ["middle"], ["right"]])}

    @staticmethod
    def _request(*, lexical_only=False, anchored_block=False, anchored_path=False):
        def binding(path):
            return {"path": str(path), "sha256": "test"}
        policy_id = (adapter.SOURCE_CONTEXT_HUBERTFA_ANCHORED_PATH_LEXICAL_ONLY_NO_AP_POLICY_ID if anchored_path
                     else adapter.SOURCE_CONTEXT_HUBERTFA_ANCHORED_BLOCK_LEXICAL_ONLY_NO_AP_POLICY_ID if anchored_block
                     else adapter.SOURCE_CONTEXT_HUBERTFA_LEXICAL_ONLY_NO_AP_POLICY_ID if lexical_only
                     else adapter.SOURCE_CONTEXT_HUBERTFA_POLICY_ID)
        request = {"protocol_version": adapter.SOURCE_CONTEXT_HUBERTFA_PROTOCOL,
            "policy_id": policy_id, "context_radius": adapter.SOURCE_CONTEXT_RADIUS,
            "n_best": adapter.SOURCE_CONTEXT_N_BEST, "window_pad_ms": adapter.SOURCE_CONTEXT_WINDOW_PAD_MS,
            "model": binding(adapter.MODEL), "model_config": binding(adapter.MODEL_CONFIG),
            "model_version": binding(adapter.MODEL_VERSION), "model_vocab": binding(adapter.MODEL_VOCAB),
            "dictionary": binding(adapter.EN_DICTIONARY),
            "runtime": {"path": str(Path(sys.executable).resolve()), "version": adapter._runtime_version()},
            "cpu_execution": adapter.CPU_EXECUTION, "vendor_files": {}, "record_count": 1,
            "records": [{"record_id": "one", "source_window_ms": [100, 200]}]}
        if lexical_only or anchored_block or anchored_path:
            request.update({"acoustic_policy": "lexical_only_no_ap", "non_lexical_phonemes": "",
                            "pad_times": 3, "pad_length": 3})
        if anchored_block:
            request.update({"context_policy": "anchored-block-v1", "block_max_interior_lines": 12,
                            "block_max_window_ms": 45_000, "block_max_lexical_units": 256})
        if anchored_path:
            request.update({"context_policy": "anchored-path-v1", "block_max_interior_lines": 12,
                            "block_max_window_ms": 45_000, "block_max_lexical_units": 256,
                            "time_banded_decoder_id": adapter.HUBERTFA_TIME_BAND_DECODER_ID,
                            "time_banded_postcheck": adapter.HUBERTFA_TIME_BAND_POSTCHECK_ID,
                            "time_banded_source_pad_ms": adapter.SOURCE_CONTEXT_WINDOW_PAD_MS,
                            "time_band_decoder": binding(adapter.TIME_BAND_DECODER)})
        request["batch_id"] = json_sha(request)
        return request, binding

    def _derived_anchored_path_request(self):
        """Return an actually replayable generic-derived dictionary request."""
        # The adapter normally reads the production model vocabulary.  Make a
        # small valid equivalent fixture so the verifier exercises the same
        # output bytes / manifest replay rather than a mocked success path.
        vocab = {"vocab": {"en/" + phone: index for index, phone in enumerate(
            ("l", "eh", "f", "t", "m", "ih", "d", "ah", "r", "ay", "jh", "iy"), 1)}}
        adapter.MODEL_VOCAB.write_text(json.dumps(vocab, sort_keys=True), encoding="utf-8")
        cmudict = self.root / "bound-cmudict.txt"
        cmudict.write_text("newword D JH IY1\n", encoding="utf-8")
        dictionary = self.root / "derived-dictionary.txt"
        manifest = self.root / "derived-dictionary.manifest.json"
        build_english_lexicon(base_dictionary_path=adapter.EN_DICTIONARY, vocab_path=adapter.MODEL_VOCAB,
                              cmudict_path=cmudict, output_dictionary_path=dictionary,
                              output_manifest_path=manifest)
        binding = adapter._binding
        request = {
            "protocol_version": adapter.SOURCE_CONTEXT_HUBERTFA_PROTOCOL,
            "policy_id": adapter.SOURCE_CONTEXT_HUBERTFA_ANCHORED_PATH_LEXICAL_ONLY_NO_AP_POLICY_ID,
            "context_radius": adapter.SOURCE_CONTEXT_RADIUS,
            "n_best": adapter.SOURCE_CONTEXT_N_BEST,
            "window_pad_ms": adapter.SOURCE_CONTEXT_WINDOW_PAD_MS,
            "model": binding(adapter.MODEL), "model_config": binding(adapter.MODEL_CONFIG),
            "model_version": binding(adapter.MODEL_VERSION), "model_vocab": binding(adapter.MODEL_VOCAB),
            "dictionary": binding(dictionary), "dictionary_manifest": binding(manifest),
            "runtime": {"path": str(Path(sys.executable).resolve()), "version": adapter._runtime_version()},
            "cpu_execution": adapter.CPU_EXECUTION, "vendor_files": {}, "record_count": 1,
            "records": [{"record_id": "one"}],
            "acoustic_policy": "lexical_only_no_ap", "non_lexical_phonemes": "", "pad_times": 3,
            "pad_length": 3, "context_policy": adapter.SOURCE_CONTEXT_POLICY_ANCHORED_PATH,
            "block_max_interior_lines": 12, "block_max_window_ms": 45_000,
            "block_max_lexical_units": 256,
            "time_banded_decoder_id": adapter.HUBERTFA_TIME_BAND_DECODER_ID,
            "time_banded_postcheck": adapter.HUBERTFA_TIME_BAND_POSTCHECK_ID,
            "time_banded_source_pad_ms": adapter.SOURCE_CONTEXT_WINDOW_PAD_MS,
            "time_band_decoder": binding(adapter.TIME_BAND_DECODER),
        }
        request["batch_id"] = json_sha(request)
        return request, dictionary

    def test_derived_dictionary_replays_manifest_and_run_uses_verified_path(self):
        request, dictionary = self._derived_anchored_path_request()
        with patch.object(adapter, "_vendor_files", return_value={}):
            self.assertEqual(adapter._validate_request(request), {
                "non_lexical_phonemes": "", "pad_times": 3, "pad_length": 3})

        class FakeModel:
            dictionary_paths = []
            def __init__(self, onnx_path): self.model = self
            def load_config(self): pass
            def init_decoder(self): pass
            def load_model(self, device): pass
            def get_providers(self): return ["CPUExecutionProvider"]
            def get_dataset(self, *, wav_folder, dictionary_path, **_kwargs):
                type(self).dictionary_paths.append(dictionary_path)
                self.dataset = [(path,) for path in wav_folder.glob("*.wav")]
            def infer(self, **_kwargs):
                path = self.dataset[0][0]
                self.predictions = [(str(path), 1.0, [{"text": "left", "start": 0.1, "end": 0.2}])]

        prepared_paths = []
        def fake_prepare(_record, work, stem, *, dictionary_path, **_kwargs):
            prepared_paths.append(dictionary_path)
            wav = work / (stem + ".wav")
            wav.write_bytes(b"fixture")
            (work / (stem + ".lab")).write_text("left\n", encoding="utf-8")
            return "en", wav
        with patch.object(adapter, "_vendor_files", return_value={}), \
             patch.object(adapter, "_prepare", side_effect=fake_prepare), \
             patch.object(adapter, "_new_cpu4_model", side_effect=lambda: FakeModel(adapter.MODEL)):
            response = adapter.run(request)
        self.assertEqual(prepared_paths, [dictionary])
        self.assertEqual(FakeModel.dictionary_paths, [dictionary])
        self.assertEqual(response["dictionary_manifest"], request["dictionary_manifest"])

    def test_derived_dictionary_is_rejected_without_a_bound_replayable_manifest(self):
        request, dictionary = self._derived_anchored_path_request()
        request.pop("dictionary_manifest")
        request["dictionary"] = adapter._binding(dictionary)
        request["batch_id"] = json_sha({key: value for key, value in request.items() if key != "batch_id"})
        with patch.object(adapter, "_vendor_files", return_value={}):
            with self.assertRaisesRegex(ValueError, "dictionary identity mismatch"):
                adapter._validate_request(request)

    def test_manifest_cannot_relabel_the_original_base_dictionary_as_derived(self):
        request, _dictionary = self._derived_anchored_path_request()
        request["dictionary"] = adapter._binding(adapter.EN_DICTIONARY)
        request["batch_id"] = json_sha({key: value for key, value in request.items() if key != "batch_id"})
        with patch.object(adapter, "_vendor_files", return_value={}):
            with self.assertRaisesRegex(ValueError, "must not reuse the base dictionary path"):
                adapter._validate_request(request)

    def test_invalid_audio_is_unaligned_per_record(self):
        audio = self.root / "invalid.wav"
        audio.write_bytes(b"not-a-wave")
        language, reason = adapter._prepare(self._record(audio), self.root, "invalid")
        self.assertIsNone(language)
        self.assertEqual(reason, "source_audio_decode_failed")

    def test_source_clock_mismatch_is_unaligned_per_record(self):
        audio = self.root / "clock.wav"
        sf.write(audio, np.zeros(16_000, dtype=np.float32), 16_000, subtype="FLOAT")
        language, reason = adapter._prepare(self._record(audio, domain_end_ms=999), self.root, "clock")
        self.assertIsNone(language)
        self.assertEqual(reason, "source_clock_mismatch")

    def test_stale_batch_id_is_rejected_before_any_adapter_inference(self):
        request, binding = self._request()
        request["records"][0]["source_window_ms"][1] = 201
        with patch.object(adapter, "_binding", side_effect=binding), patch.object(adapter, "_vendor_files", return_value={}):
            with self.assertRaisesRegex(ValueError, "batch request identity"):
                adapter._validate_request(request)

    def test_lexical_only_no_ap_contract_is_validated_before_inference(self):
        request, binding = self._request(lexical_only=True)
        with patch.object(adapter, "_binding", side_effect=binding), patch.object(adapter, "_vendor_files", return_value={}):
            self.assertEqual(adapter._validate_request(request), {
                "non_lexical_phonemes": "", "pad_times": 3, "pad_length": 3})
            request["non_lexical_phonemes"] = "AP"
            with self.assertRaisesRegex(ValueError, "policy field mismatch"):
                adapter._validate_request(request)

    def test_anchored_block_contract_binds_caps_and_adapter_accepts_only_interior_targets(self):
        request, binding = self._request(anchored_block=True)
        with patch.object(adapter, "_binding", side_effect=binding), patch.object(adapter, "_vendor_files", return_value={}):
            self.assertEqual(adapter._validate_request(request), {
                "non_lexical_phonemes": "", "pad_times": 3, "pad_length": 3})
            request["block_max_window_ms"] = 44_999
            with self.assertRaisesRegex(ValueError, "block_max_window_ms"):
                adapter._validate_request(request)
        audio = self.root / "block.wav"
        sf.write(audio, np.zeros(16_000, dtype=np.float32), 16_000, subtype="FLOAT")
        record = self._record(audio)
        record.update({"context_policy": "anchored-block-v1", "source_window_ms": [0, 1000],
                       "segment_lexical_units": [["left"], ["middle"], ["middle"], ["right"]],
                       "lexical_units_sha256": json_sha([["left"], ["middle"], ["middle"], ["right"]]),
                       "target_outputs": [{"position": 2, "target_segment_index": 1},
                                          {"position": 3, "target_segment_index": 2}]})
        language, _wav = adapter._prepare(record, self.root, "block",
                                           request_policy_id=adapter.SOURCE_CONTEXT_HUBERTFA_ANCHORED_BLOCK_LEXICAL_ONLY_NO_AP_POLICY_ID)
        self.assertEqual(language, "en")
        record["target_outputs"] = [{"position": 1, "target_segment_index": 0}]
        language, reason = adapter._prepare(record, self.root, "bad-block",
                                             request_policy_id=adapter.SOURCE_CONTEXT_HUBERTFA_ANCHORED_BLOCK_LEXICAL_ONLY_NO_AP_POLICY_ID)
        self.assertIsNone(language)
        self.assertEqual(reason, "anchored_block_target_outputs_invalid")

    def test_prepare_reads_dictionary_once_for_multiword_block_record(self):
        audio = self.root / "multiword.wav"
        sf.write(audio, np.zeros(16_000, dtype=np.float32), 16_000, subtype="FLOAT")
        units = [["left", "left"], ["middle", "middle"], ["right", "right"]]
        record = self._record(audio, domain_end_ms=1000)
        record.update({"source_window_ms": [0, 1000], "segment_lexical_units": units,
                       "lexical_units_sha256": json_sha(units)})
        with patch.object(adapter, "_dictionary_keys", wraps=adapter._dictionary_keys) as read_dictionary:
            language, _wav = adapter._prepare(record, self.root, "multiword")
        self.assertEqual(language, "en")
        self.assertEqual(read_dictionary.call_count, 1)

    def test_prepare_default_dictionary_is_resolved_at_call_time(self):
        audio = self.root / "dynamic-default.wav"
        sf.write(audio, np.zeros(16_000, dtype=np.float32), 16_000, subtype="FLOAT")
        replacement = self.root / "replacement-dictionary.txt"
        replacement.write_text("left\tL EH F T\nmiddle\tM IH D AH L\nright\tR AY T\n", encoding="utf-8")
        # The function default must follow this patched path.  Capturing the
        # module's production private path at definition time breaks isolated
        # test environments and makes adapter fixtures non-hermetic.
        with patch.object(adapter, "EN_DICTIONARY", replacement):
            language, _wav = adapter._prepare(self._record(audio), self.root, "dynamic-default")
        self.assertEqual(language, "en")

    def test_anchored_block_caps_do_not_retroactively_limit_three_line_records(self):
        audio = self.root / "caps.wav"
        sf.write(audio, np.zeros(16_000 * 46, dtype=np.float32), 16_000, subtype="FLOAT")
        legacy = self._record(audio, domain_end_ms=46_000)
        # The new request may retain an old three-line record.  Its pre-existing
        # context has no new block cap, even if the source window is long.
        legacy.update({"context_policy": "three-line-v1", "source_window_ms": [0, 45_001]})
        language, _wav = adapter._prepare(legacy, self.root, "legacy",
                                           request_policy_id=adapter.SOURCE_CONTEXT_HUBERTFA_ANCHORED_BLOCK_LEXICAL_ONLY_NO_AP_POLICY_ID)
        self.assertEqual(language, "en")
        block = dict(legacy, context_policy="anchored-block-v1", source_window_ms=[0, 45_001],
                     target_outputs=[{"position": 2, "target_segment_index": 1}])
        language, reason = adapter._prepare(block, self.root, "overcap",
                                             request_policy_id=adapter.SOURCE_CONTEXT_HUBERTFA_ANCHORED_BLOCK_LEXICAL_ONLY_NO_AP_POLICY_ID)
        self.assertIsNone(language)
        self.assertEqual(reason, "anchored_block_source_window_cap_exceeded")

    def test_anchored_path_contract_binds_decoder_and_validates_qualified_anchor_order(self):
        request, binding = self._request(anchored_path=True)
        with patch.object(adapter, "_binding", side_effect=binding), patch.object(adapter, "_vendor_files", return_value={}):
            self.assertEqual(adapter._validate_request(request), {
                "non_lexical_phonemes": "", "pad_times": 3, "pad_length": 3})
            request["time_banded_decoder_id"] = "wrong"
            with self.assertRaisesRegex(ValueError, "time_banded_decoder_id"):
                adapter._validate_request(request)
        audio = self.root / "path.wav"
        sf.write(audio, np.zeros(16_000, dtype=np.float32), 16_000, subtype="FLOAT")
        record = self._record(audio)
        record.update({"context_policy": "anchored-path-v1", "source_window_ms": [0, 1000],
                       "segment_lexical_units": [["left"], ["middle"], ["right"]],
                       "lexical_units_sha256": json_sha([["left"], ["middle"], ["right"]]),
                       "target_outputs": [{"position": 2, "target_segment_index": 1}],
                       "context": {"time_banded_decoder_id": adapter.HUBERTFA_TIME_BAND_DECODER_ID,
                                   "time_banded_postcheck": adapter.HUBERTFA_TIME_BAND_POSTCHECK_ID,
                                   "time_banded_source_pad_ms": adapter.SOURCE_CONTEXT_WINDOW_PAD_MS,
                                   "canonical_segment_indices": [10, 20, 30],
                                   "qualified_source_anchors": [
                                       {"canonical_line_index": 10, "packet_cache_key_sha256": "a", "candidate_id": "a",
                                        "source_interval_ms": [100, 200], "qualification": "selected_full_context"},
                                       {"canonical_line_index": 30, "packet_cache_key_sha256": "b", "candidate_id": "b",
                                        "source_interval_ms": [700, 800], "qualification": "selected_full_context"},
                                   ]}})
        language, _wav = adapter._prepare(record, self.root, "path",
                                           request_policy_id=adapter.SOURCE_CONTEXT_HUBERTFA_ANCHORED_PATH_LEXICAL_ONLY_NO_AP_POLICY_ID)
        self.assertEqual(language, "en")
        record["context"]["qualified_source_anchors"][1]["source_interval_ms"] = [150, 250]
        language, reason = adapter._prepare(record, self.root, "path-overlap",
                                             request_policy_id=adapter.SOURCE_CONTEXT_HUBERTFA_ANCHORED_PATH_LEXICAL_ONLY_NO_AP_POLICY_ID)
        self.assertIsNone(language)
        self.assertEqual(reason, "qualified_source_anchor_order_or_overlap_invalid")

    def test_interleaved_path_and_legacy_records_restore_the_vendor_decoder(self):
        records = [
            {"record_id": "path-one", "context_policy": "anchored-path-v1"},
            {"record_id": "legacy", "context_policy": "three-line-v1"},
            {"record_id": "path-two", "context_policy": "anchored-path-v1"},
        ]
        request = {"protocol_version": adapter.SOURCE_CONTEXT_HUBERTFA_PROTOCOL,
            "policy_id": adapter.SOURCE_CONTEXT_HUBERTFA_ANCHORED_PATH_LEXICAL_ONLY_NO_AP_POLICY_ID,
            "batch_id": "b" * 64, "model": {}, "model_config": {}, "model_version": {}, "model_vocab": {},
            "dictionary": {}, "runtime": {}, "cpu_execution": adapter.CPU_EXECUTION, "vendor_files": {},
            "time_band_decoder": {}, "record_count": len(records), "records": records}
        class Decoder:
            frame_length = 0.01
        original = Decoder()
        banded = Decoder()
        class FakeModel:
            decoder_seen = []
            def __init__(self, onnx_path): self.model, self.fa_decoder = self, original
            def load_config(self): pass
            def init_decoder(self): pass
            def load_model(self, device): pass
            def get_providers(self): return ["CPUExecutionProvider"]
            def get_dataset(self, *, wav_folder, **_kwargs): self.dataset = [(path, [], ["left"], []) for path in sorted(wav_folder.glob("*.wav"))]
            def infer(self, **_kwargs):
                type(self).decoder_seen.append(self.fa_decoder)
                path = self.dataset[0][0]
                self.predictions = [(str(path), 1.0, [{"text": "left", "start": 0.1, "end": 0.2}])]
        def fake_prepare(_record, work, stem, **_kwargs):
            wav = work / (stem + ".wav")
            wav.write_bytes(b"fake")
            (work / (stem + ".lab")).write_text("left\n", encoding="utf-8")
            return "en", wav
        with patch.object(adapter, "_validate_request", return_value={"non_lexical_phonemes": "", "pad_times": 3, "pad_length": 3}), \
             patch.object(adapter, "_prepare", side_effect=fake_prepare), \
             patch.object(adapter, "_time_banded_decoder_for_record", return_value=(banded, ["left"], {0: (0.0, 1.0)})), \
             patch.object(adapter, "_new_cpu4_model", side_effect=lambda: FakeModel(adapter.MODEL)):
            response = adapter.run(request)
        self.assertEqual([item["status"] for item in response["records"]], ["aligned", "aligned", "aligned"])
        self.assertEqual(FakeModel.decoder_seen, [banded, original, banded])

    def test_over_96_records_resets_vendor_accumulators_without_reloading_session(self):
        records = [{"record_id": f"r{i}"} for i in range(97)]
        request = {"protocol_version": "p", "policy_id": "policy", "batch_id": "b" * 64,
            "model": {}, "model_config": {}, "model_version": {}, "model_vocab": {}, "dictionary": {},
            "runtime": {}, "cpu_execution": adapter.CPU_EXECUTION, "vendor_files": {},
            "record_count": len(records), "records": records}
        class FakeModel:
            load_count = 0
            dataset_sizes = []
            def __init__(self, onnx_path): self.model = self
            def load_config(self): pass
            def init_decoder(self): pass
            def load_model(self, device): type(self).load_count += 1
            def get_providers(self): return ["CPUExecutionProvider"]
            def get_dataset(self, *, wav_folder, **_kwargs):
                self.dataset = [(path,) for path in sorted(wav_folder.glob("*.wav"))]
                type(self).dataset_sizes.append(len(self.dataset))
            def infer(self, **_kwargs):
                self.predictions = [(str(item[0]), 1.0, [{"text": "left", "start": 0.1, "end": 0.2}])
                                    for item in self.dataset]
        def fake_prepare(_record, work, stem, **_kwargs):
            wav = work / (stem + ".wav")
            wav.write_bytes(b"fake")
            (work / (stem + ".lab")).write_text("left\n", encoding="utf-8")
            return "en", wav
        with patch.object(adapter, "_validate_request", return_value={"non_lexical_phonemes": "AP", "pad_times": 3, "pad_length": 3}), patch.object(adapter, "_prepare", side_effect=fake_prepare), \
             patch.object(adapter, "_new_cpu4_model", side_effect=lambda: FakeModel(adapter.MODEL)):
            response = adapter.run(request)
        self.assertEqual(FakeModel.load_count, 1)
        self.assertEqual(FakeModel.dataset_sizes, [96, 1])
        self.assertEqual([item["record_id"] for item in response["records"]], [item["record_id"] for item in records])

    def test_one_vendor_inference_failure_does_not_block_the_other_record(self):
        records = [{"record_id": "bad"}, {"record_id": "good"}]
        request = {"protocol_version": "p", "policy_id": "policy", "batch_id": "b" * 64,
            "model": {}, "model_config": {}, "model_version": {}, "model_vocab": {}, "dictionary": {},
            "runtime": {}, "cpu_execution": adapter.CPU_EXECUTION, "vendor_files": {},
            "record_count": len(records), "records": records}
        class FakeModel:
            load_count = 0
            def __init__(self, onnx_path): self.model = self
            def load_config(self): pass
            def init_decoder(self): pass
            def load_model(self, device): type(self).load_count += 1
            def get_providers(self): return ["CPUExecutionProvider"]
            def get_dataset(self, *, wav_folder, **_kwargs): self.dataset = [(path,) for path in sorted(wav_folder.glob("*.wav"))]
            def infer(self, **_kwargs):
                path = self.dataset[0][0]
                if path.stem == "record_00001":
                    raise Exception("No duplicate groups")
                self.predictions = [(str(path), 1.0, [{"text": "left", "start": 0.1, "end": 0.2}])]
        def fake_prepare(_record, work, stem, **_kwargs):
            wav = work / (stem + ".wav")
            wav.write_bytes(b"fake")
            (work / (stem + ".lab")).write_text("left\n", encoding="utf-8")
            return "en", wav
        with patch.object(adapter, "_validate_request", return_value={"non_lexical_phonemes": "AP", "pad_times": 3, "pad_length": 3}), patch.object(adapter, "_prepare", side_effect=fake_prepare), \
             patch.object(adapter, "_new_cpu4_model", side_effect=lambda: FakeModel(adapter.MODEL)):
            response = adapter.run(request)
        self.assertEqual(FakeModel.load_count, 1)
        self.assertEqual([item["status"] for item in response["records"]], ["unaligned", "aligned"])
        self.assertEqual(response["records"][0]["reason"], "hubertfa_inference_failed:Exception:No duplicate groups")
        self.assertEqual(response["diagnostics"]["inference_failures"][0]["record_id"], "bad")

    def test_lexical_only_no_ap_is_echoed_and_passed_to_vendor(self):
        records = [{"record_id": "one"}]
        request = {"protocol_version": adapter.SOURCE_CONTEXT_HUBERTFA_PROTOCOL,
            "policy_id": adapter.SOURCE_CONTEXT_HUBERTFA_LEXICAL_ONLY_NO_AP_POLICY_ID,
            "batch_id": "b" * 64, "model": {}, "model_config": {}, "model_version": {}, "model_vocab": {},
            "dictionary": {}, "runtime": {}, "cpu_execution": adapter.CPU_EXECUTION, "vendor_files": {},
            "acoustic_policy": "lexical_only_no_ap", "non_lexical_phonemes": "", "pad_times": 3,
            "pad_length": 3, "record_count": 1, "records": records}
        class FakeModel:
            infer_kwargs = []
            def __init__(self, onnx_path): self.model = self
            def load_config(self): pass
            def init_decoder(self): pass
            def load_model(self, device): pass
            def get_providers(self): return ["CPUExecutionProvider"]
            def get_dataset(self, *, wav_folder, **_kwargs): self.dataset = [(path,) for path in wav_folder.glob("*.wav")]
            def infer(self, **kwargs):
                type(self).infer_kwargs.append(kwargs)
                path = self.dataset[0][0]
                self.predictions = [(str(path), 1.0, [{"text": "left", "start": 0.1, "end": 0.2}])]
        def fake_prepare(_record, work, stem, **_kwargs):
            wav = work / (stem + ".wav")
            wav.write_bytes(b"fake")
            (work / (stem + ".lab")).write_text("left\n", encoding="utf-8")
            return "en", wav
        no_ap = {"non_lexical_phonemes": "", "pad_times": 3, "pad_length": 3}
        with patch.object(adapter, "_validate_request", return_value=no_ap), patch.object(adapter, "_prepare", side_effect=fake_prepare), \
             patch.object(adapter, "_new_cpu4_model", side_effect=lambda: FakeModel(adapter.MODEL)):
            response = adapter.run(request)
        self.assertEqual(FakeModel.infer_kwargs, [no_ap])
        self.assertEqual({key: response[key] for key in ("acoustic_policy", "non_lexical_phonemes", "pad_times", "pad_length")},
                         {"acoustic_policy": "lexical_only_no_ap", **no_ap})


if __name__ == "__main__":
    unittest.main()
