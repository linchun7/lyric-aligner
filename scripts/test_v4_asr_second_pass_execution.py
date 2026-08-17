import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from lyric_aligner.alignment.asr_executor import FasterWhisperExecutionConfig
from lyric_aligner.alignment.asr_second_pass import (
    AsrSecondPassExecutionError,
    execute_second_pass_and_compose,
)


class FakeModel:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append((audio, kwargs))
        word = SimpleNamespace(
            start=3.10,
            end=3.50,
            word=" upgraded",
            probability=0.96,
        )
        segment = SimpleNamespace(
            start=3.05,
            end=3.90,
            text="upgraded line",
            avg_logprob=-0.1,
            no_speech_prob=0.01,
            compression_ratio=1.0,
            words=[word],
        )
        info = SimpleNamespace(language="en", language_probability=0.99)
        return iter([segment]), info


class V4AsrSecondPassExecutionTests(unittest.TestCase):
    def alignment_plan(self):
        return {
            "mode": "plan_only",
            "backend_execution_performed": False,
            "jobs": [
                {
                    "job_id": "job-good",
                    "occurrence_id": "occ-1",
                    "track_id": "track-1",
                    "ordinal": 1,
                    "priority": "medium",
                    "canonical_line_index": 0,
                    "language_profile": "en",
                    "mix_window_ms": [1000, 2500],
                    "source_window_ms": [5000, 6500],
                    "canonical_text_sha256": "a" * 64,
                    "requested_capabilities": ["mix_asr", "word_timestamps"],
                },
                {
                    "job_id": "job-weak",
                    "occurrence_id": "occ-1",
                    "track_id": "track-1",
                    "ordinal": 1,
                    "priority": "high",
                    "canonical_line_index": 1,
                    "language_profile": "en",
                    "mix_window_ms": [3000, 4500],
                    "source_window_ms": [7000, 8500],
                    "canonical_text_sha256": "b" * 64,
                    "requested_capabilities": ["mix_asr", "word_timestamps"],
                },
            ],
        }

    def first_pass(self):
        return {
            "backend": "faster_whisper",
            "config": {"model_id": "fast-model"},
            "jobs": [
                {
                    "job_id": "job-good",
                    "occurrence_id": "occ-1",
                    "canonical_line_index": 0,
                    "mix_window_ms": [1000, 2500],
                    "canonical_text_support_score": 0.95,
                    "segments": [{"start_ms": 1050, "end_ms": 1900}],
                },
                {
                    "job_id": "job-weak",
                    "occurrence_id": "occ-1",
                    "canonical_line_index": 1,
                    "mix_window_ms": [3000, 4500],
                    "canonical_text_support_score": 0.30,
                    "segments": [{"start_ms": 3050, "end_ms": 3900}],
                },
            ],
        }

    def second_plan(self, *, selected=True):
        jobs = []
        if selected:
            original = self.alignment_plan()["jobs"][1]
            jobs = [
                {
                    "job_id": original["job_id"],
                    "occurrence_id": original["occurrence_id"],
                    "track_id": original["track_id"],
                    "ordinal": original["ordinal"],
                    "canonical_line_index": original["canonical_line_index"],
                    "language_profile": original["language_profile"],
                    "mix_window_ms": original["mix_window_ms"],
                    "source_window_ms": original["source_window_ms"],
                    "canonical_text_sha256": original["canonical_text_sha256"],
                    "requested_capabilities": ["mix_asr", "word_timestamps"],
                    "second_pass_reasons": ["low_canonical_text_support"],
                }
            ]
        return {
            "mode": "second_pass_plan_only",
            "policy_calibrated": False,
            "backend_execution_performed": False,
            "scope_policy": "reuse_exact_first_pass_local_windows",
            "first_pass_model_id": "fast-model",
            "second_pass_model_id": "accuracy-model",
            "selected_job_ids": [row["job_id"] for row in jobs],
            "jobs": jobs,
        }

    def test_selected_job_is_replaced_and_good_first_pass_job_retained(self):
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "mix.wav"
            audio.write_bytes(b"fake")
            fake = FakeModel()
            factory_calls = []

            def factory(model_id, *, device, compute_type):
                factory_calls.append((model_id, device, compute_type))
                return fake

            result = execute_second_pass_and_compose(
                audio_path=audio,
                alignment_plan=self.alignment_plan(),
                second_pass_plan=self.second_plan(),
                first_pass_evidence=self.first_pass(),
                canonical_text_by_job_id={"job-weak": "upgraded line"},
                config=FasterWhisperExecutionConfig(model_id="accuracy-model"),
                model_factory=factory,
            )
            self.assertEqual(factory_calls, [("accuracy-model", "cpu", "int8")])
            self.assertEqual(len(fake.calls), 1)
            self.assertEqual(fake.calls[0][1]["clip_timestamps"], [3.0, 4.5])
            self.assertEqual(result["job_count"], 2)
            by_id = {row["job_id"]: row for row in result["jobs"]}
            self.assertEqual(by_id["job-good"]["evidence_pass"], "first")
            self.assertEqual(by_id["job-good"]["evidence_model_id"], "fast-model")
            self.assertEqual(by_id["job-weak"]["evidence_pass"], "second")
            self.assertEqual(by_id["job-weak"]["evidence_model_id"], "accuracy-model")
            self.assertGreater(by_id["job-weak"]["canonical_text_support_score"], 0.99)
            self.assertEqual(result["first_pass_retained_job_count"], 1)
            self.assertEqual(result["second_pass_executed_job_count"], 1)
            self.assertEqual(result["scope_policy"], "reuse_exact_first_pass_local_windows")
            self.assertNotIn("upgraded line", json.dumps(result))

    def test_empty_second_pass_selection_executes_zero_and_never_loads_model(self):
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "mix.wav"
            audio.write_bytes(b"fake")
            factory_calls = []
            result = execute_second_pass_and_compose(
                audio_path=audio,
                alignment_plan=self.alignment_plan(),
                second_pass_plan=self.second_plan(selected=False),
                first_pass_evidence=self.first_pass(),
                canonical_text_by_job_id={},
                config=FasterWhisperExecutionConfig(model_id="accuracy-model"),
                model_factory=lambda *args, **kwargs: factory_calls.append(1),
            )
            self.assertEqual(factory_calls, [])
            self.assertFalse(result["model_loaded_second_pass"])
            self.assertEqual(result["second_pass_selected_job_count"], 0)
            self.assertEqual(result["second_pass_executed_job_count"], 0)
            self.assertEqual(result["first_pass_retained_job_count"], 2)
            self.assertEqual(
                [row["evidence_pass"] for row in result["jobs"]],
                ["first", "first"],
            )

    def test_changed_second_pass_window_fails_closed(self):
        plan = self.second_plan()
        plan["jobs"][0]["mix_window_ms"] = [0, 99999]
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "mix.wav"
            audio.write_bytes(b"fake")
            with self.assertRaisesRegex(
                AsrSecondPassExecutionError, "changed original mix_window_ms"
            ):
                execute_second_pass_and_compose(
                    audio_path=audio,
                    alignment_plan=self.alignment_plan(),
                    second_pass_plan=plan,
                    first_pass_evidence=self.first_pass(),
                    canonical_text_by_job_id={},
                    config=FasterWhisperExecutionConfig(model_id="accuracy-model"),
                    model_factory=lambda *args, **kwargs: FakeModel(),
                )

    def test_executor_model_must_match_second_pass_plan(self):
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "mix.wav"
            audio.write_bytes(b"fake")
            with self.assertRaisesRegex(
                AsrSecondPassExecutionError, "does not match second-pass plan"
            ):
                execute_second_pass_and_compose(
                    audio_path=audio,
                    alignment_plan=self.alignment_plan(),
                    second_pass_plan=self.second_plan(),
                    first_pass_evidence=self.first_pass(),
                    canonical_text_by_job_id={},
                    config=FasterWhisperExecutionConfig(model_id="wrong-model"),
                    model_factory=lambda *args, **kwargs: FakeModel(),
                )

    def test_selected_job_ids_must_match_second_plan_jobs(self):
        plan = self.second_plan()
        plan["selected_job_ids"] = []
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "mix.wav"
            audio.write_bytes(b"fake")
            with self.assertRaisesRegex(
                AsrSecondPassExecutionError, "selected_job_ids do not match"
            ):
                execute_second_pass_and_compose(
                    audio_path=audio,
                    alignment_plan=self.alignment_plan(),
                    second_pass_plan=plan,
                    first_pass_evidence=self.first_pass(),
                    canonical_text_by_job_id={},
                    config=FasterWhisperExecutionConfig(model_id="accuracy-model"),
                    model_factory=lambda *args, **kwargs: FakeModel(),
                )


if __name__ == "__main__":
    unittest.main()
