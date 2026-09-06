import hashlib
import json
import tempfile
import types
import unittest
from pathlib import Path

from lyric_aligner.evaluation.human_gold_prediction import (
    ExternalHumanGoldPredictionConfig,
    HumanGoldPredictionError,
    _validate_response_record,
    execute_human_gold_prediction_scope,
)
from scripts.test_support_production_calibration import human_gold_artifact
from scripts.v4_build_production_boundary_calibration import validate_prediction_artifact


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class HumanGoldPredictionTests(unittest.TestCase):
    def cases(self, purpose: str):
        output = []
        for index in range(30):
            case_id = hashlib.sha256(f"{purpose}-{index}".encode()).hexdigest()
            if purpose == "outer":
                segments = [{"text": "hello world"}]
                internal_index = None
                start_ms = 10000 + index * 1000
                end_ms = start_ms + 700
            else:
                segments = [{"text": "hello"}, {"text": "world"}]
                internal_index = 1
                start_ms = 50000 + index * 1000 - 200
                end_ms = 50000 + index * 1000 + 400
            output.append(
                {
                    "case_id": case_id,
                    "segments": segments,
                    "internal_boundary_index": internal_index,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "clip_start_ms": max(0, start_ms - 500),
                    "clip_end_ms": end_ms + 500,
                }
            )
        return output

    def config(self):
        return ExternalHumanGoldPredictionConfig(
            command="python fake_adapter.py",
            family="final_mix_singing_alignment",
            correlation_group="fake-independent-model",
            backend_id="fake_backend",
            backend_version="1",
            model_id="fake-model",
            model_revision="a" * 64,
            language="en",
            timeout_seconds=30,
        )

    def fake_runner(self, *, unaligned_first=False, outside_owner_first=False, inspect_requests=None):
        def run(argv, **kwargs):
            request_path = Path(argv[argv.index("--request") + 1])
            response_path = Path(argv[argv.index("--response") + 1])
            request = json.loads(request_path.read_text(encoding="utf-8"))
            if inspect_requests is not None:
                inspect_requests.append(request)
            response_records = []
            for index, row in enumerate(request["records"]):
                if unaligned_first and index == 0:
                    response_records.append(
                        {
                            "record_id": row["record_id"],
                            "boundary_kind": row["boundary_kind"],
                            "lexical_units_sha256": row["lexical_units_sha256"],
                            "mix_window_ms": row["mix_window_ms"],
                            "status": "unaligned",
                            "predicted_ms": None,
                            "reason": "explicit lexical mismatch",
                        }
                    )
                    continue
                if row["boundary_kind"] == "start":
                    predicted = row["row_interval_ms"][0] + 20
                elif row["boundary_kind"] == "end":
                    predicted = row["row_interval_ms"][1] - 20
                elif outside_owner_first and index == 0:
                    predicted = row["row_interval_ms"][1] + 20
                else:
                    predicted = (row["row_interval_ms"][0] + row["row_interval_ms"][1]) // 2
                response_records.append(
                    {
                        "record_id": row["record_id"],
                        "boundary_kind": row["boundary_kind"],
                        "lexical_units_sha256": row["lexical_units_sha256"],
                        "mix_window_ms": row["mix_window_ms"],
                        "status": "aligned",
                        "predicted_ms": predicted,
                        "evidence_sha256": "e" * 64,
                    }
                )
            response = {
                "protocol_version": request["protocol_version"],
                "backend_id": request["backend_id"],
                "backend_version": request["backend_version"],
                "model_id": request["model_id"],
                "model_revision": request["model_revision"],
                "backend_profile_id": request["backend_profile_id"],
                "window_policy_id": request["window_policy_id"],
                "family": request["family"],
                "correlation_group": request["correlation_group"],
                "audio_basis": request["audio_basis"],
                "language": request["language"],
                "lexical_id": request["lexical_id"],
                "lexical_revision": request["lexical_revision"],
                "final_audio_sha256": request["final_audio_sha256"],
                "record_count": len(response_records),
                "records": response_records,
            }
            response_path.write_text(json.dumps(response), encoding="utf-8")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        return run

    def test_prediction_requires_completed_human_gold_and_never_sends_gold_timing(self):
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "final.wav"
            audio.write_bytes(b"fake-final-audio")
            gold = human_gold_artifact(
                language_scope="en",
                final_audio_sha256=sha256_file(audio),
            )
            seen = []
            artifact = execute_human_gold_prediction_scope(
                human_gold_artifact=gold,
                locked_cases=self.cases("outer"),
                final_audio_path=audio,
                boundary_kind="start",
                config=self.config(),
                runner=self.fake_runner(inspect_requests=seen),
            )
            self.assertEqual(len(seen), 1)
            self.assertTrue(not any("gold" in key.casefold() for key in seen[0]))
            self.assertTrue(
                all(not any("gold" in key.casefold() for key in row) for row in seen[0]["records"])
            )
            self.assertEqual(artifact["record_count"], 30)
            self.assertTrue(all(row["predicted_ms"] is not None for row in artifact["records"]))
            verified = validate_prediction_artifact(artifact, human_gold_artifact=gold)
            self.assertEqual(verified["scope"]["boundary_kind"], "start")
            self.assertEqual(verified["scope"]["lexical_id"], "lyric-alignment-lexical")

    def test_explicit_unaligned_case_becomes_null_and_is_not_dropped(self):
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "final.wav"
            audio.write_bytes(b"fake-final-audio")
            gold = human_gold_artifact(
                language_scope="en",
                final_audio_sha256=sha256_file(audio),
            )
            artifact = execute_human_gold_prediction_scope(
                human_gold_artifact=gold,
                locked_cases=self.cases("internal"),
                final_audio_path=audio,
                boundary_kind="internal",
                config=self.config(),
                runner=self.fake_runner(unaligned_first=True),
            )
            self.assertEqual(len(artifact["records"]), 30)
            self.assertIsNone(artifact["records"][0]["predicted_ms"])
            validate_prediction_artifact(artifact, human_gold_artifact=gold)

    def test_internal_prediction_outside_editor_owner_is_retained_inside_locked_window(self):
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "final.wav"
            audio.write_bytes(b"fake-final-audio")
            gold = human_gold_artifact(
                language_scope="en",
                final_audio_sha256=sha256_file(audio),
            )
            artifact = execute_human_gold_prediction_scope(
                human_gold_artifact=gold,
                locked_cases=self.cases("internal"),
                final_audio_path=audio,
                boundary_kind="internal",
                config=self.config(),
                runner=self.fake_runner(outside_owner_first=True),
            )
            self.assertEqual(len(artifact["records"]), 30)
            self.assertEqual(artifact["records"][0]["predicted_ms"], 50420)
            self.assertTrue(all(row["predicted_ms"] is not None for row in artifact["records"][1:]))
            validate_prediction_artifact(artifact, human_gold_artifact=gold)

    def test_outer_edge_clamped_aligned_response_is_demoted_to_missing(self):
        request = {
            "record_id": "a" * 64 + ":start",
            "boundary_kind": "start",
            "lexical_units_sha256": "b" * 64,
            "mix_window_ms": [9000, 12200],
            "row_interval_ms": [10000, 10700],
        }
        payload = {
            "record_id": request["record_id"],
            "boundary_kind": "start",
            "lexical_units_sha256": request["lexical_units_sha256"],
            "mix_window_ms": request["mix_window_ms"],
            "status": "aligned",
            "predicted_ms": 9020,
            "evidence_sha256": "e" * 64,
        }
        self.assertIsNone(_validate_response_record(payload, request=request))
        payload["predicted_ms"] = 10020
        self.assertEqual(_validate_response_record(payload, request=request), 10020)

    def test_audio_changed_after_gold_aborts_whole_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "final.wav"
            audio.write_bytes(b"first")
            gold = human_gold_artifact(
                language_scope="en",
                final_audio_sha256=sha256_file(audio),
            )
            audio.write_bytes(b"changed")
            with self.assertRaises(HumanGoldPredictionError):
                execute_human_gold_prediction_scope(
                    human_gold_artifact=gold,
                    locked_cases=self.cases("outer"),
                    final_audio_path=audio,
                    boundary_kind="end",
                    config=self.config(),
                    runner=self.fake_runner(),
                )

    def test_process_failure_is_not_misreported_as_missing_prediction(self):
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "final.wav"
            audio.write_bytes(b"fake-final-audio")
            gold = human_gold_artifact(
                language_scope="en",
                final_audio_sha256=sha256_file(audio),
            )

            def failing_runner(argv, **kwargs):
                return types.SimpleNamespace(returncode=9, stdout="", stderr="model crashed")

            with self.assertRaises(HumanGoldPredictionError):
                execute_human_gold_prediction_scope(
                    human_gold_artifact=gold,
                    locked_cases=self.cases("outer"),
                    final_audio_path=audio,
                    boundary_kind="start",
                    config=self.config(),
                    runner=failing_runner,
                )


if __name__ == "__main__":
    unittest.main()
