import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from lyric_aligner.alignment.asr_executor import (
    AsrExecutionError,
    FasterWhisperExecutionConfig,
    execute_faster_whisper_jobs,
)


class FakeModel:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append((audio, kwargs))
        word = SimpleNamespace(
            start=1.10,
            end=1.50,
            word=" hello",
            probability=0.91,
        )
        segment = SimpleNamespace(
            start=1.05,
            end=1.90,
            text="hello world",
            avg_logprob=-0.2,
            no_speech_prob=0.01,
            compression_ratio=1.1,
            words=[word],
        )
        info = SimpleNamespace(language="en", language_probability=0.97)
        return iter([segment]), info


class V4AsrExecutorTests(unittest.TestCase):
    def plan(self):
        return {
            "mode": "plan_only",
            "backend_execution_performed": False,
            "jobs": [
                {
                    "job_id": "job-1",
                    "occurrence_id": "occ-1",
                    "canonical_line_index": 0,
                    "language_profile": "en",
                    "mix_window_ms": [1000, 2500],
                    "requested_capabilities": ["mix_asr", "word_timestamps"],
                },
                {
                    "job_id": "job-2",
                    "occurrence_id": "occ-1",
                    "canonical_line_index": 1,
                    "language_profile": "en",
                    "mix_window_ms": [3000, 4000],
                    "requested_capabilities": ["source_forced_alignment"],
                },
            ],
        }

    def test_executor_only_runs_mix_asr_jobs_and_uses_clip_timestamps(self):
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "mix.wav"
            audio.write_bytes(b"not-decoded-by-fake-model")
            fake = FakeModel()
            factory_calls = []

            def factory(model_id, *, device, compute_type):
                factory_calls.append((model_id, device, compute_type))
                return fake

            result = execute_faster_whisper_jobs(
                audio_path=audio,
                plan=self.plan(),
                canonical_text_by_job_id={"job-1": "hello world"},
                config=FasterWhisperExecutionConfig(model_id="test-model"),
                model_factory=factory,
            )
            self.assertEqual(factory_calls, [("test-model", "cpu", "int8")])
            self.assertEqual(len(fake.calls), 1)
            _, kwargs = fake.calls[0]
            self.assertEqual(kwargs["clip_timestamps"], [1.0, 2.5])
            self.assertTrue(kwargs["word_timestamps"])
            self.assertFalse(kwargs["condition_on_previous_text"])
            self.assertFalse(kwargs["vad_filter"])
            self.assertEqual(kwargs["language"], "en")
            self.assertEqual(result["job_count"], 1)
            job = result["jobs"][0]
            self.assertGreater(job["canonical_text_support_score"], 0.99)
            self.assertEqual(job["detected_language"], "en")
            self.assertEqual(job["segments"][0]["words"][0]["probability"], 0.91)
            serialized = json.dumps(result)
            self.assertNotIn("hello world", serialized)
            self.assertNotIn('" hello"', serialized)

    def test_private_text_requires_explicit_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "mix.wav"
            audio.write_bytes(b"fake")
            fake = FakeModel()
            result = execute_faster_whisper_jobs(
                audio_path=audio,
                plan=self.plan(),
                canonical_text_by_job_id={"job-1": "hello world"},
                config=FasterWhisperExecutionConfig(
                    model_id="test-model", include_private_text=True
                ),
                model_factory=lambda *args, **kwargs: fake,
            )
            self.assertEqual(result["jobs"][0]["observed_text"], "hello world")
            self.assertEqual(
                result["jobs"][0]["segments"][0]["words"][0]["text"],
                " hello",
            )

    def test_unknown_or_cantonese_profile_leaves_language_detection_open(self):
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "mix.wav"
            audio.write_bytes(b"fake")
            plan = self.plan()
            plan["jobs"][0]["language_profile"] = "yue"
            fake = FakeModel()
            execute_faster_whisper_jobs(
                audio_path=audio,
                plan=plan,
                canonical_text_by_job_id={},
                config=FasterWhisperExecutionConfig(model_id="test-model"),
                model_factory=lambda *args, **kwargs: fake,
            )
            self.assertIsNone(fake.calls[0][1]["language"])

    def test_no_asr_jobs_does_not_load_model(self):
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "mix.wav"
            audio.write_bytes(b"fake")
            plan = self.plan()
            plan["jobs"] = [plan["jobs"][1]]
            calls = []
            result = execute_faster_whisper_jobs(
                audio_path=audio,
                plan=plan,
                canonical_text_by_job_id={},
                config=FasterWhisperExecutionConfig(model_id="test-model"),
                model_factory=lambda *args, **kwargs: calls.append(1),
            )
            self.assertEqual(calls, [])
            self.assertFalse(result["model_loaded"])
            self.assertEqual(result["job_count"], 0)

    def test_invalid_plan_or_missing_audio_fails_explicitly(self):
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "missing.wav"
            with self.assertRaisesRegex(AsrExecutionError, "mix audio does not exist"):
                execute_faster_whisper_jobs(
                    audio_path=audio,
                    plan=self.plan(),
                    canonical_text_by_job_id={},
                    config=FasterWhisperExecutionConfig(model_id="test-model"),
                    model_factory=lambda *args, **kwargs: FakeModel(),
                )


if __name__ == "__main__":
    unittest.main()
