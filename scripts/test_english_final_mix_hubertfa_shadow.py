from __future__ import annotations

import hashlib
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from lyric_aligner.alignment.english_final_mix_hubertfa_shadow import (
    ENGLISH_FINAL_MIX_HUBERTFA_CORRELATION_GROUP,
    ENGLISH_FINAL_MIX_HUBERTFA_MAX_ADJACENT_CONTEXT_GAP_MS,
    ENGLISH_FINAL_MIX_HUBERTFA_OBSERVER_ID,
    ENGLISH_FINAL_MIX_HUBERTFA_SHADOW_POLICY_ID,
    EnglishFinalMixHuBERTFAShadowError,
    bind_adapter_response,
    build_adapter_request,
    build_shadow_plan,
    evaluate_shadow,
    sha256_file,
    sha256_json,
)
from lyric_aligner.audio.forced_alignment import CONTEXTUAL_SEGMENT_INTERVAL_POLICY_ID


def text_sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class EnglishFinalMixHuBERTFAShadowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.audio = self.root / "mix.wav"
        self.audio.write_bytes(b"synthetic-final-mix")
        self.lyric = self.root / "track.lrc"
        # Deliberately keep raw-LRC metadata outside the resolved canonical selection.
        self.lyric.write_text(
            "[ar:Example]\n"
            "[00:01.00]alpha beta\n"
            "[00:02.00]bravo charlie\n"
            "[00:03.00]delta echo\n",
            encoding="utf-8",
        )
        self.canonical_selection = [
            {"timestamp_ms": 1000, "alternative_index": 0, "text": "alpha beta"},
            {"timestamp_ms": 2000, "alternative_index": 0, "text": "bravo charlie"},
            {"timestamp_ms": 3000, "alternative_index": 0, "text": "delta echo"},
        ]
        self.occurrence_id = "occ_test"
        self.track_id = "track_test"
        self.selection = {
            "schema_version": "targeted-asr-selection-lock-1.0",
            "policy_id": "targeted-semantic-failed-expansion-v1",
            "task_fingerprint_sha256": "a" * 64,
            "selected_job_count": 1,
            "selection": {
                "4": [
                    {
                        "job_id": "job-1",
                        "ordinal": 4,
                        "occurrence_id": self.occurrence_id,
                        "canonical_line_index": 1,
                        "canonical_text_sha256": text_sha("bravo charlie"),
                    }
                ]
            },
        }
        self.assets = {
            "assets": [
                {
                    "track_id": self.track_id,
                    "canonical_lyric_path": str(self.lyric),
                    "canonical_lyric_sha256": sha256_file(self.lyric),
                    "canonical_selection_sha256": sha256_json(self.canonical_selection),
                }
            ],
            "occurrences": [
                {
                    "ordinal": 4,
                    "occurrence_id": self.occurrence_id,
                    "track_id": self.track_id,
                }
            ],
            "resolution": [
                {
                    "track_id": self.track_id,
                    "canonical_selection": deepcopy(self.canonical_selection),
                }
            ],
        }
        self.audit = [
            {
                "occurrence_id": self.occurrence_id,
                "canonical_line_indices": "[0]",
                "start_ms": "1000",
                "end_ms": "1800",
            },
            {
                "occurrence_id": self.occurrence_id,
                "canonical_line_indices": "[1]",
                "start_ms": "2000",
                "end_ms": "2800",
            },
            {
                "occurrence_id": self.occurrence_id,
                "canonical_line_indices": "[2]",
                "start_ms": "3000",
                "end_ms": "4000",
            },
        ]
        self.runtime_files: dict[str, Path] = {}
        for name in (
            "model",
            "model_config",
            "model_version",
            "model_vocab",
            "dictionary",
            "adapter",
            "existing_mandarin_adapter_dependency",
            "vendor_onnx",
            "vendor_device_utils",
        ):
            path = self.root / f"{name}.bin"
            path.write_bytes((name + "-fixture").encode("utf-8"))
            self.runtime_files[name] = path

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def build(self, *, audit=None, selection=None, assets=None):
        return build_shadow_plan(
            selection_lock=selection or self.selection,
            selection_lock_sha256="c" * 64,
            track_assets=assets or self.assets,
            track_assets_sha256="d" * 64,
            final_audit_rows=self.audit if audit is None else audit,
            final_audit_sha256="e" * 64,
            final_audio_path=self.audio,
            final_audio_sha256=sha256_file(self.audio),
            mix_duration_ms=10000,
        )

    @staticmethod
    def binding(path: Path) -> dict[str, str]:
        return {"path": str(path.resolve()), "sha256": sha256_file(path)}

    def response_for(self, plan: dict, *, aligned: bool = True, onset: int = 2300) -> tuple[dict, dict]:
        request = build_adapter_request(plan)
        record = plan["records"][0]
        if aligned:
            row = {
                "record_id": record["record_id"],
                "status": "aligned",
                "predicted_onset_ms": onset,
                "predicted_interval_ms": [onset, onset + 300],
                "reason": "",
                "aligned_words_sha256": "f" * 64,
                "lexical_units_sha256": record["lexical_units_sha256"],
                "alignment_method_id": CONTEXTUAL_SEGMENT_INTERVAL_POLICY_ID,
                "mix_window_ms": record["mix_window_ms"],
            }
        else:
            row = {
                "record_id": record["record_id"],
                "status": "unaligned",
                "predicted_onset_ms": None,
                "predicted_interval_ms": None,
                "reason": "synthetic_unaligned",
                "lexical_units_sha256": record["lexical_units_sha256"],
                "alignment_method_id": CONTEXTUAL_SEGMENT_INTERVAL_POLICY_ID,
                "mix_window_ms": record["mix_window_ms"],
            }
        response = {
            "protocol_version": request["protocol_version"],
            "policy_id": request["policy_id"],
            "observer_id": request["observer_id"],
            "correlation_group": request["correlation_group"],
            "authority": "shadow_only_uncalibrated",
            "automatic_timing_change_allowed": False,
            "plan_sha256": request["plan_sha256"],
            "request_sha256": request["request_sha256"],
            "task_fingerprint_sha256": request["task_fingerprint_sha256"],
            "final_audio_sha256": request["final_audio_sha256"],
            "window_policy_id": request["window_policy_id"],
            "alignment_method_id": request["alignment_method_id"],
            "context_continuity_max_gap_ms": request["context_continuity_max_gap_ms"],
            "record_count": 1,
            "model": self.binding(self.runtime_files["model"]),
            "model_config": self.binding(self.runtime_files["model_config"]),
            "model_version": self.binding(self.runtime_files["model_version"]),
            "model_vocab": self.binding(self.runtime_files["model_vocab"]),
            "dictionary": self.binding(self.runtime_files["dictionary"]),
            "adapter": self.binding(self.runtime_files["adapter"]),
            "existing_mandarin_adapter_dependency": self.binding(
                self.runtime_files["existing_mandarin_adapter_dependency"]
            ),
            "vendor_files": {
                str(self.runtime_files["vendor_onnx"].resolve()): sha256_file(
                    self.runtime_files["vendor_onnx"]
                ),
                str(self.runtime_files["vendor_device_utils"].resolve()): sha256_file(
                    self.runtime_files["vendor_device_utils"]
                ),
            },
            "provider_runtime": {
                "python_executable": str(Path(__import__("sys").executable).resolve()),
                "python_version": "fixture",
                "actual_providers": ["CPUExecutionProvider"],
                "libraries": {
                    "onnxruntime": "fixture",
                    "librosa": "fixture",
                    "soundfile": "fixture",
                },
                "vendor_device_utils": self.binding(self.runtime_files["vendor_device_utils"]),
            },
            "records": [row],
        }
        return request, response

    def test_plan_uses_resolved_canonical_selection_and_contextual_interval(self) -> None:
        plan = self.build()
        self.assertEqual(plan["policy_id"], ENGLISH_FINAL_MIX_HUBERTFA_SHADOW_POLICY_ID)
        self.assertEqual(plan["observer_id"], ENGLISH_FINAL_MIX_HUBERTFA_OBSERVER_ID)
        self.assertEqual(plan["correlation_group"], ENGLISH_FINAL_MIX_HUBERTFA_CORRELATION_GROUP)
        self.assertEqual(plan["selected_target_count"], 1)
        self.assertEqual(plan["prepared_record_count"], 1)
        self.assertEqual(plan["rejected_target_count"], 0)
        record = plan["records"][0]
        self.assertEqual(record["context_canonical_line_indices"], [0, 1, 2])
        self.assertEqual(record["target_segment_index"], 1)
        self.assertEqual(record["alignment_method_id"], CONTEXTUAL_SEGMENT_INTERVAL_POLICY_ID)
        self.assertEqual(
            record["context_continuity_max_gap_ms"],
            ENGLISH_FINAL_MIX_HUBERTFA_MAX_ADJACENT_CONTEXT_GAP_MS,
        )
        self.assertEqual(record["adjacent_context_gaps_ms"], [200, 200])
        self.assertEqual(record["routing_final_interval_ms"], [2000, 2800])
        self.assertEqual(record["context_final_interval_ms"], [1000, 4000])
        self.assertEqual(record["mix_window_ms"], [0, 5500])
        self.assertFalse(record["routing_timing_is_truth"])

    def test_first_line_is_rejected_without_bilateral_context(self) -> None:
        selection = deepcopy(self.selection)
        selection["selection"]["4"][0]["canonical_line_index"] = 0
        selection["selection"]["4"][0]["canonical_text_sha256"] = text_sha("alpha beta")
        plan = self.build(selection=selection)
        self.assertEqual(plan["prepared_record_count"], 0)
        self.assertEqual(plan["rejected_target_count"], 1)
        self.assertEqual(plan["targets"][0]["reason"], "insufficient_bilateral_canonical_context")

    def test_context_gap_larger_than_existing_route_pad_is_rejected(self) -> None:
        audit = deepcopy(self.audit)
        audit[1]["start_ms"] = "4000"
        audit[1]["end_ms"] = "4800"
        audit[2]["start_ms"] = "5000"
        audit[2]["end_ms"] = "6000"
        plan = self.build(audit=audit)
        self.assertEqual(plan["prepared_record_count"], 0)
        self.assertEqual(plan["rejected_target_count"], 1)
        self.assertEqual(plan["targets"][0]["reason"], "context_temporal_discontinuity")
        self.assertEqual(plan["targets"][0]["adjacent_context_gaps_ms"], [2200, 200])
        self.assertEqual(
            plan["targets"][0]["max_allowed_adjacent_context_gap_ms"],
            ENGLISH_FINAL_MIX_HUBERTFA_MAX_ADJACENT_CONTEXT_GAP_MS,
        )

    def test_missing_context_owner_rejects_without_shrinking_denominator(self) -> None:
        plan = self.build(audit=self.audit[:2])
        self.assertEqual(plan["selected_target_count"], 1)
        self.assertEqual(plan["prepared_record_count"], 0)
        self.assertEqual(plan["rejected_target_count"], 1)
        self.assertIn("missing_final_ownership", plan["targets"][0]["reason"])

    def test_scalar_canonical_line_index_fallback_requires_exact_text_sha(self) -> None:
        audit = []
        for index, row in enumerate(self.audit):
            text = self.canonical_selection[index]["text"]
            audit.append(
                {
                    **row,
                    "canonical_line_indices": "",
                    "canonical_line_index": str(index),
                    "text_sha256": text_sha(text),
                }
            )
        plan = self.build(audit=audit)
        self.assertEqual(plan["prepared_record_count"], 1)
        self.assertEqual(plan["records"][0]["context_canonical_line_indices"], [0, 1, 2])

        audit[0]["text_sha256"] = "0" * 64
        rejected = self.build(audit=audit)
        self.assertEqual(rejected["prepared_record_count"], 0)
        self.assertEqual(
            rejected["targets"][0]["reason"],
            "missing_final_ownership_for_context_line:0",
        )

    def test_resolved_canonical_selection_sha_mismatch_fails_closed(self) -> None:
        assets = deepcopy(self.assets)
        assets["resolution"][0]["canonical_selection"][1]["text"] = "wrong resolved text"
        with self.assertRaisesRegex(EnglishFinalMixHuBERTFAShadowError, "canonical selection identity mismatch"):
            self.build(assets=assets)

    def test_selected_canonical_text_sha_mismatch_fails_closed(self) -> None:
        selection = deepcopy(self.selection)
        selection["selection"]["4"][0]["canonical_text_sha256"] = "f" * 64
        with self.assertRaisesRegex(EnglishFinalMixHuBERTFAShadowError, "canonical text SHA mismatch"):
            self.build(selection=selection)

    def test_unexpected_selection_policy_fails_closed(self) -> None:
        selection = deepcopy(self.selection)
        selection["policy_id"] = "post-hoc-selection"
        with self.assertRaisesRegex(EnglishFinalMixHuBERTFAShadowError, "unexpected targeted selection policy"):
            self.build(selection=selection)

    def test_request_rejects_tampered_plan(self) -> None:
        plan = self.build()
        plan["records"][0]["mix_window_ms"][0] += 1
        with self.assertRaisesRegex(EnglishFinalMixHuBERTFAShadowError, "plan SHA mismatch"):
            build_adapter_request(plan)

    def test_adapter_response_binds_exact_identity_runtime_and_interval(self) -> None:
        plan = self.build()
        request, response = self.response_for(plan, onset=2300)
        bound = bind_adapter_response(plan, request, response)
        self.assertEqual(bound["records"][0]["predicted_interval_ms"], [2300, 2600])
        self.assertEqual(len(bound["observer_runtime_bundle_sha256"]), 64)

    def test_adapter_response_rejects_tampered_task_identity(self) -> None:
        plan = self.build()
        request, response = self.response_for(plan)
        response["task_fingerprint_sha256"] = "0" * 64
        with self.assertRaisesRegex(EnglishFinalMixHuBERTFAShadowError, "task_fingerprint_sha256 mismatch"):
            bind_adapter_response(plan, request, response)

    def test_adapter_response_rejects_interval_outside_exact_window(self) -> None:
        plan = self.build()
        request, response = self.response_for(plan)
        response["records"][0]["predicted_onset_ms"] = -1
        response["records"][0]["predicted_interval_ms"] = [-1, 100]
        with self.assertRaisesRegex(EnglishFinalMixHuBERTFAShadowError, "outside the exact mix window"):
            bind_adapter_response(plan, request, response)

    def test_evaluation_preserves_rejected_targets_in_denominator(self) -> None:
        selection = deepcopy(self.selection)
        selection["selected_job_count"] = 2
        first = deepcopy(selection["selection"]["4"][0])
        first["job_id"] = "job-edge"
        first["canonical_line_index"] = 0
        first["canonical_text_sha256"] = text_sha("alpha beta")
        selection["selection"]["4"].insert(0, first)
        plan = self.build(selection=selection)
        self.assertEqual(plan["selected_target_count"], 2)
        self.assertEqual(plan["prepared_record_count"], 1)
        request, response = self.response_for(plan, onset=2300)
        bound = bind_adapter_response(plan, request, response)
        evaluation = evaluate_shadow(plan, bound)
        self.assertEqual(evaluation["selected_target_count"], 2)
        self.assertEqual(evaluation["rejected_before_adapter_count"], 1)
        self.assertEqual(evaluation["aligned_record_count"], 1)
        self.assertEqual(evaluation["tracks"][0]["selected_count"], 2)
        self.assertEqual(evaluation["tracks"][0]["aligned_fraction_of_selected"], 0.5)
        self.assertEqual(evaluation["rows"][1]["absolute_disagreement_vs_current_final_ms"], 300)


if __name__ == "__main__":
    unittest.main()
