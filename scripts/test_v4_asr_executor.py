import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from lyric_aligner.alignment.asr_executor import (
    AsrExecutionError,
    FasterWhisperExecutionConfig,
    execute_faster_whisper_jobs,
    _canonical_word_span,
    _window_word_text,
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
        word2 = SimpleNamespace(start=1.50, end=1.75, word=" world", probability=0.92)
        segment = SimpleNamespace(
            start=1.05,
            end=1.90,
            text="hello world",
            avg_logprob=-0.2,
            no_speech_prob=0.01,
            compression_ratio=1.1,
            words=[word, word2],
        )
        info = SimpleNamespace(language="en", language_probability=0.97)
        return iter([segment]), info


class V4AsrExecutorTests(unittest.TestCase):
    def test_unambiguous_han_variants_preserve_canonical_outer_words(self):
        words=[SimpleNamespace(word=c,start=1+i*.2,end=1.1+i*.2,probability=.9)
               for i,c in enumerate('愛是菜色糖衣包裝')]
        span=_canonical_word_span('爱是彩色糖衣包装',[SimpleNamespace(words=words)])
        self.assertTrue(span['canonical_start_covered'])
        self.assertTrue(span['canonical_end_covered'])
        self.assertGreaterEqual(span['support_score'],.72)
        self.assertEqual(span['start_ms'],1000)

    def test_variant_equivalent_repeated_lyrics_remain_ambiguous(self):
        words=[SimpleNamespace(word=w,start=t,end=t+.2,probability=.9)
               for w,t in [('愛',1),('你',1.3),('爱',4),('你',4.3)]]
        span=_canonical_word_span('爱你',[SimpleNamespace(words=words)])
        self.assertTrue(span['ambiguous'])
        self.assertIsNone(span['start_ms'])
        self.assertFalse(span['canonical_start_covered'])

    def test_zero_duration_interior_keeps_lexical_content_without_inventing_timing(self):
        for text in ('hello middle world', '我们天生就是派对动物', 'hello世界'):
            words = [SimpleNamespace(word=c, start=1+i*.1,
                     end=1+i*.1+(0 if i == 1 else .05), probability=.9)
                     for i,c in enumerate(text.replace(' ', ''))]
            span = _canonical_word_span(text, [SimpleNamespace(words=words)])
            self.assertEqual(span['support_score'], 1.)
            self.assertTrue(span['canonical_start_covered'])
            self.assertTrue(span['canonical_end_covered'])
            self.assertEqual(span['untimed_word_count'], 1)
            self.assertEqual(span['start_ms'], 1000)

    def test_zero_duration_outer_word_leaves_only_that_edge_unknown(self):
        for index in (0, 2):
            words = [SimpleNamespace(word=w, start=1+i,
                     end=1+i+(0 if i == index else .5), probability=.9)
                     for i,w in enumerate(('hello', 'middle', 'world'))]
            span = _canonical_word_span('hello middle world', [SimpleNamespace(words=words)])
            self.assertEqual(span['support_score'], 1.)
            self.assertEqual(span['canonical_start_covered'], index != 0)
            self.assertEqual(span['canonical_end_covered'], index != 2)
            self.assertEqual(span['start_ms'] is None, index == 0)
            self.assertEqual(span['end_ms'] is None, index == 2)

    def test_both_execution_paths_export_independent_canonical_edges(self):
        with tempfile.TemporaryDirectory() as temporary:
            audio=Path(temporary)/'mix.wav';audio.write_bytes(b'fixture')
            for predecoded in (False,True):
                for text,start,end in [('hello world',True,True),('hello world again',True,False),('say hello world',False,True)]:
                    with self.subTest(predecoded=predecoded,text=text):
                        plan=self.plan();plan['jobs']=plan['jobs'][:1];plan['jobs'][0]['mix_window_ms']=[0,4000]
                        result=execute_faster_whisper_jobs(audio_path=audio,plan=plan,
                            canonical_text_by_job_id={'job-1':text},config=FasterWhisperExecutionConfig(model_id='fixture'),
                            model_factory=lambda *args,**kwargs:FakeModel(),
                            audio_loader=(lambda path:[0.0]*64000) if predecoded else None)['jobs'][0]
                        self.assertEqual(result['canonical_start_covered'],start)
                        self.assertEqual(result['canonical_end_covered'],end)
                        self.assertEqual(result['canonical_match_start_ms'] is not None,start)
                        self.assertEqual(result['canonical_match_end_ms'] is not None,end)

    def test_equal_repeated_matches_do_not_claim_first_occurrence(self):
        words = [SimpleNamespace(word=w,start=t,end=t+.2,probability=.9)
                 for w,t in [('for',1.),('someone',1.3),('for',8.),('someone',8.3)]]
        span = _canonical_word_span('for someone',[SimpleNamespace(words=words)],window_ms=(0,10000))
        self.assertTrue(span.get('ambiguous',False))
        self.assertIsNone(span['start_ms'])
        self.assertIsNone(span['end_ms'])
        self.assertEqual([r['start_ms'] for r in span['candidates']],[1000,8000])

    def test_outside_segment_text_cannot_support_job_or_skip_second_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "mix.wav"
            audio.write_bytes(b"fake")
            class OutsideModel:
                def transcribe(self, audio, **kwargs):
                    word = SimpleNamespace(word="hello world", start=0.5, end=0.9, probability=0.99)
                    segment = SimpleNamespace(start=0.5, end=1.1, text="hello world", words=[word])
                    return iter([segment]), SimpleNamespace(language="en", language_probability=1.0)
            for grouped in (False, True):
                plan = self.plan()
                if grouped:
                    plan["jobs"][1].update(mix_window_ms=[1000, 2500], requested_capabilities=["mix_asr"])
                result = execute_faster_whisper_jobs(
                    audio_path=audio, plan=plan,
                    canonical_text_by_job_id={"job-1": "hello world", "job-2": "hello world"},
                    config=FasterWhisperExecutionConfig(model_id="test-model"),
                    model_factory=lambda *args, **kwargs: OutsideModel(),
                    audio_loader=(lambda path: [0.0] * 64000) if grouped else None,
                )
                self.assertIsNone(result["jobs"][0]["canonical_text_support_score"])
                self.assertIsNone(result["jobs"][0]["canonical_match_support_score"])

    def test_invalid_word_is_a_barrier_not_permission_to_join_neighbors(self):
        for start, end in ((float("nan"), 1.0), (1.5, 1.5), (1.5001, 1.5002)):
            with self.subTest(start=start, end=end):
                segments = [SimpleNamespace(words=[
                    SimpleNamespace(word="hello", start=1.0, end=1.4, probability=0.9),
                    SimpleNamespace(word="bad", start=start, end=end, probability=0.9),
                    SimpleNamespace(word="world", start=2.0, end=2.4, probability=0.9),
                ])]
                result = _canonical_word_span("hello world", segments)
                self.assertLess(result["support_score"], 1.0)
                self.assertEqual(_window_word_text(segments, (0, 10000)), "")

    def test_canonical_match_does_not_join_backwards_word_sequence(self):
        segments = [SimpleNamespace(words=[
            SimpleNamespace(word="hello", start=8.0, end=8.4, probability=0.9),
            SimpleNamespace(word="world", start=3.0, end=3.4, probability=0.9),
        ])]
        result = _canonical_word_span("hello world", segments)
        self.assertLess(result["support_score"], 1.0)
        self.assertTrue(result["ambiguous"])
        self.assertIsNone(result["start_ms"])
        self.assertIsNone(result["end_ms"])
        self.assertTrue(all(r["end_ms"] > r["start_ms"] for r in result["candidates"]))
        self.assertEqual(_window_word_text(segments, (0, 10000)), "")

    def test_canonical_match_excludes_words_outside_requested_window(self):
        segments = [SimpleNamespace(words=[
            SimpleNamespace(word="hello", start=0.8, end=1.1, probability=0.9),
            SimpleNamespace(word="world", start=1.2, end=1.5, probability=0.9),
        ])]
        result = _canonical_word_span("hello world", segments, window_ms=(1000, 2000))
        self.assertLess(result["support_score"], 1.0)
        self.assertGreaterEqual(result["start_ms"], 1000)

    def test_overlapping_jobs_have_separate_backend_batches(self):
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "mix.wav"
            audio.write_bytes(b"fake")
            plan = self.plan()
            plan["jobs"][1].update(mix_window_ms=[1500, 3000], requested_capabilities=["mix_asr"])
            fake = FakeModel()
            result = execute_faster_whisper_jobs(
                audio_path=audio, plan=plan,
                canonical_text_by_job_id={"job-1": "hello world", "job-2": "hello world"},
                config=FasterWhisperExecutionConfig(model_id="test-model"),
                model_factory=lambda *args, **kwargs: fake,
                audio_loader=lambda path: [0.0] * 64000,
            )
            self.assertEqual(len(fake.calls), 2)
            self.assertIsNone(result["jobs"][1]["canonical_match_start_ms"])
            self.assertFalse(result["jobs"][1]["canonical_start_covered"])

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

    def test_predecoded_audio_slices_bounded_clip_and_restores_absolute_timestamps(self):
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "mix.wav"
            audio.write_bytes(b"fake")
            fake = FakeModel()
            loader_calls = []

            def loader(path):
                loader_calls.append(path)
                return [0.0] * 64000

            result = execute_faster_whisper_jobs(
                audio_path=audio,
                plan=self.plan(),
                canonical_text_by_job_id={"job-1": "hello world"},
                config=FasterWhisperExecutionConfig(model_id="test-model"),
                model_factory=lambda *args, **kwargs: fake,
                audio_loader=loader,
            )
            self.assertEqual(loader_calls, [audio])
            transcribe_audio, kwargs = fake.calls[0]
            self.assertEqual(len(transcribe_audio), 24000)
            self.assertNotIn("clip_timestamps", kwargs)
            self.assertEqual(result["jobs"][0]["segments"][0]["start_ms"], 2050)
            self.assertEqual(result["jobs"][0]["segments"][0]["words"][0]["start_ms"], 2100)
            self.assertEqual(result["jobs"][0]["canonical_match_start_ms"], 2100)

    def test_grouped_predecoded_audio_uses_one_multi_clip_transcribe_call(self):
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "mix.wav"
            audio.write_bytes(b"fake")
            plan = self.plan()
            plan["jobs"][1]["requested_capabilities"] = ["mix_asr", "word_timestamps"]
            plan["jobs"][1]["mix_window_ms"] = [3000, 4000]

            class GroupFakeModel:
                def __init__(self):
                    self.calls = []

                def transcribe(self, audio_value, **kwargs):
                    self.calls.append((audio_value, kwargs))
                    segments = []
                    for start, end, text in (
                        (1.05, 1.90, "hello world"),
                        (3.10, 3.75, "second line"),
                    ):
                        word = SimpleNamespace(
                            start=start + 0.05,
                            end=min(end, start + 0.35),
                            word=" " + text.split()[0],
                            probability=0.91,
                        )
                        word2 = SimpleNamespace(
                            start=min(end, start + 0.35),
                            end=end,
                            word=" " + text.split()[-1],
                            probability=0.92,
                        )
                        segments.append(
                            SimpleNamespace(
                                start=start,
                                end=end,
                                text=text,
                                avg_logprob=-0.2,
                                no_speech_prob=0.01,
                                compression_ratio=1.1,
                                words=[word, word2],
                            )
                        )
                    return iter(segments), SimpleNamespace(
                        language="en", language_probability=0.97
                    )

            fake = GroupFakeModel()
            result = execute_faster_whisper_jobs(
                audio_path=audio,
                plan=plan,
                canonical_text_by_job_id={
                    "job-1": "hello world",
                    "job-2": "second line",
                },
                config=FasterWhisperExecutionConfig(model_id="test-model"),
                model_factory=lambda *args, **kwargs: fake,
                audio_loader=lambda path: [0.0] * 80000,
            )
            self.assertEqual(len(fake.calls), 1)
            _, kwargs = fake.calls[0]
            self.assertEqual(kwargs["clip_timestamps"], [1.0, 2.5, 3.0, 4.0])
            self.assertEqual(result["job_count"], 2)
            self.assertEqual(result["jobs"][0]["segments"][0]["start_ms"], 1050)
            self.assertEqual(result["jobs"][1]["segments"][0]["start_ms"], 3100)
            self.assertGreater(result["jobs"][0]["canonical_text_support_score"], 0.99)
            self.assertGreater(result["jobs"][1]["canonical_text_support_score"], 0.99)

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

    def test_explicit_supported_job_hint_overrides_local_canonical_language(self):
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "mix.wav"
            audio.write_bytes(b"fake")
            plan = self.plan()
            plan["jobs"][0]["language_profile"] = "zh"
            plan["jobs"][0]["asr_language_hint"] = "en"
            fake = FakeModel()
            execute_faster_whisper_jobs(
                audio_path=audio,
                plan=plan,
                canonical_text_by_job_id={"job-1": "你好世界"},
                config=FasterWhisperExecutionConfig(model_id="test-model"),
                model_factory=lambda *args, **kwargs: fake,
            )
            self.assertEqual(fake.calls[0][1]["language"], "en")

    def test_planner_force_auto_wins_over_cross_language_local_hint(self):
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "mix.wav"
            audio.write_bytes(b"fake")
            plan = self.plan()
            plan["jobs"][0]["language_profile"] = "zh"
            plan["jobs"][0]["asr_language_hint"] = "auto"
            plan["jobs"][0]["asr_force_auto_detect"] = True
            fake = FakeModel()
            execute_faster_whisper_jobs(
                audio_path=audio,
                plan=plan,
                canonical_text_by_job_id={"job-1": "hello world"},
                config=FasterWhisperExecutionConfig(model_id="test-model"),
                model_factory=lambda *args, **kwargs: fake,
            )
            self.assertIsNone(fake.calls[0][1]["language"])

    def test_auto_job_hint_uses_local_chinese_canonical_language(self):
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "mix.wav"
            audio.write_bytes(b"fake")
            plan = self.plan()
            plan["jobs"][0]["language_profile"] = "zh"
            plan["jobs"][0]["asr_language_hint"] = "auto"
            fake = FakeModel()
            execute_faster_whisper_jobs(
                audio_path=audio,
                plan=plan,
                canonical_text_by_job_id={"job-1": "你好世界"},
                config=FasterWhisperExecutionConfig(model_id="test-model"),
                model_factory=lambda *args, **kwargs: fake,
            )
            self.assertEqual(fake.calls[0][1]["language"], "zh")

    def test_empty_job_hint_uses_local_english_canonical_language(self):
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "mix.wav"
            audio.write_bytes(b"fake")
            plan = self.plan()
            plan["jobs"][0]["language_profile"] = "en"
            plan["jobs"][0]["asr_language_hint"] = ""
            fake = FakeModel()
            execute_faster_whisper_jobs(
                audio_path=audio,
                plan=plan,
                canonical_text_by_job_id={"job-1": "hello world"},
                config=FasterWhisperExecutionConfig(model_id="test-model"),
                model_factory=lambda *args, **kwargs: fake,
            )
            self.assertEqual(fake.calls[0][1]["language"], "en")

    def test_auto_job_hint_keeps_mixed_or_unknown_canonical_open(self):
        for canonical, profile in (
            ("你好 hello", "zh"),
            ("你好 hello", "mixed"),
            ("123 !!!", "zh"),
        ):
            with (
                self.subTest(canonical=canonical, profile=profile),
                tempfile.TemporaryDirectory() as temporary,
            ):
                audio = Path(temporary) / "mix.wav"
                audio.write_bytes(b"fake")
                plan = self.plan()
                plan["jobs"][0]["language_profile"] = profile
                plan["jobs"][0]["asr_language_hint"] = "auto"
                fake = FakeModel()
                execute_faster_whisper_jobs(
                    audio_path=audio,
                    plan=plan,
                    canonical_text_by_job_id={"job-1": canonical},
                    config=FasterWhisperExecutionConfig(model_id="test-model"),
                    model_factory=lambda *args, **kwargs: fake,
                )
                self.assertIsNone(fake.calls[0][1]["language"])

    def test_auto_job_hint_without_canonical_falls_back_to_track_profile(self):
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "mix.wav"
            audio.write_bytes(b"fake")
            plan = self.plan()
            plan["jobs"][0]["language_profile"] = "ko"
            plan["jobs"][0]["asr_language_hint"] = "auto"
            fake = FakeModel()
            execute_faster_whisper_jobs(
                audio_path=audio,
                plan=plan,
                canonical_text_by_job_id={},
                config=FasterWhisperExecutionConfig(model_id="test-model"),
                model_factory=lambda *args, **kwargs: fake,
            )
            self.assertEqual(fake.calls[0][1]["language"], "ko")

    def test_explicit_mixed_or_unknown_job_hint_keeps_detection_open(self):
        for job_hint in ("mixed", "unknown"):
            with (
                self.subTest(job_hint=job_hint),
                tempfile.TemporaryDirectory() as temporary,
            ):
                audio = Path(temporary) / "mix.wav"
                audio.write_bytes(b"fake")
                plan = self.plan()
                plan["jobs"][0]["language_profile"] = "zh"
                plan["jobs"][0]["asr_language_hint"] = job_hint
                fake = FakeModel()
                execute_faster_whisper_jobs(
                    audio_path=audio,
                    plan=plan,
                    canonical_text_by_job_id={"job-1": "你好世界"},
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
