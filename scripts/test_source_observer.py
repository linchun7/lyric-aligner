import hashlib
import importlib.metadata
import json
import tempfile
import threading
import unittest
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from lyric_aligner.alignment.source_observer import (
    FLOAT_DECODE_POLICY,
    SourceObservationConfig,
    json_sha,
    observe_source,
)


class _FakeModel:
    def __init__(self, segments, info=None):
        self.segments = segments
        self.info = info or SimpleNamespace(language="en")
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append((audio, kwargs))
        return iter(self.segments), self.info


class SourceObserverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.audio = self.root / "source.wav"
        self.audio.write_bytes(b"fixed source bytes")
        self.audio_sha = hashlib.sha256(self.audio.read_bytes()).hexdigest()
        self.model_dir = self.root / "model"
        self.model_dir.mkdir()
        (self.model_dir / "model.bin").write_bytes(b"fixed local model")
        self.cache_dir = self.root / "cache"
        self.config = SourceObservationConfig(model_path=str(self.model_dir), language="en")
        self.runtime = {"faster-whisper": "test", "ctranslate2": "test", "av": "test"}

    def tearDown(self):
        self.temp.cleanup()

    def _observe(self, *, model=None, audio=None, config=None, audio_sha=None):
        return observe_source(
            audio_path=self.audio,
            audio_sha256=audio_sha or self.audio_sha,
            config=config or self.config,
            cache_dir=self.cache_dir,
            model_factory=(lambda _config: model) if model is not None else None,
            audio_loader=(lambda _path: audio) if audio is not None else None,
            runtime_identity=self.runtime,
        )

    def test_source_time_basis_and_content_bound_cache(self):
        segments = [
            SimpleNamespace(
                words=[
                    SimpleNamespace(start=0.125, end=0.375, word=" first", probability=0.8),
                    SimpleNamespace(start=1.25, end=1.5, word="second", probability=0.7),
                ]
            )
        ]
        model = _FakeModel(segments)
        first = self._observe(model=model, audio=[0.0] * 32_000)
        self.assertEqual(first["status"], "observed")
        self.assertFalse(first["cache_hit"])
        self.assertEqual(first["audio_basis"], "source")
        self.assertEqual(first["search_domain"], {"start_ms": 0, "end_ms": 2000, "domain_id": "whole_source"})
        self.assertEqual(
            first["words"],
            [
                {"start_ms": 125, "end_ms": 375, "text": " first", "probability": 0.8, "window_id": "whole_source"},
                {"start_ms": 1250, "end_ms": 1500, "text": "second", "probability": 0.7, "window_id": "whole_source"},
            ],
        )
        self.assertEqual(model.calls[0][1]["language"], "en")
        self.assertNotIn("multilingual", model.calls[0][1])
        self.assertFalse(model.calls[0][1]["vad_filter"])
        self.assertFalse(model.calls[0][1]["condition_on_previous_text"])
        self.assertNotIn("multilingual", first["identity"]["config"])

        cached = self._observe(
            model=_FakeModel([]),
            audio=[0.0] * 1,
        )
        self.assertTrue(cached["cache_hit"])
        self.assertEqual(cached["words"], first["words"])
        self.assertEqual(cached["artifact_sha256"], first["artifact_sha256"])

    def test_float_policy_separates_cache_and_records_decode(self):
        legacy = self._observe(model=_FakeModel([]), audio=[0.] * 160)
        config = replace(self.config, decode_policy=FLOAT_DECODE_POLICY)
        with patch('lyric_aligner.audio.float_decode.decode_float_audio', return_value=([0.] * 160, {'applied_gain': .5})) as decoder:
            result = self._observe(model=_FakeModel([]), config=config)
        decoder.assert_called_once_with(self.audio.resolve())
        self.assertNotEqual(legacy['cache_key_sha256'], result['cache_key_sha256'])
        self.assertEqual(result['schema_version'], 'source-observer-1.1')
        self.assertEqual(result['decode_diagnostics'], {'applied_gain': .5})
        self.assertNotIn('decode_policy', legacy['identity']['config'])
        self.assertNotIn('decode_diagnostics', legacy)
        self.assertEqual(legacy['schema_version'], 'source-observer-1.0')

    def test_unknown_decode_policy_rejected(self):
        with self.assertRaisesRegex(ValueError, 'decode policy'):
            self._observe(config=replace(self.config, decode_policy='unknown'))

    def test_multilingual_detection_is_explicit_and_separate_from_legacy_cache(self):
        legacy_model = _FakeModel([SimpleNamespace(words=[])])
        legacy = self._observe(model=legacy_model, audio=[0.0] * 16_000)

        multilingual_config = replace(self.config, language=None, multilingual=True)
        multilingual_model = _FakeModel(
            [SimpleNamespace(words=[])], info=SimpleNamespace(language="ja")
        )
        multilingual = self._observe(
            model=multilingual_model,
            config=multilingual_config,
            audio=[0.0] * 16_000,
        )

        self.assertNotEqual(legacy["cache_key_sha256"], multilingual["cache_key_sha256"])
        self.assertEqual(multilingual["schema_version"], "source-observer-1.2")
        self.assertTrue(multilingual["identity"]["config"]["multilingual"])
        self.assertEqual(multilingual["detected_language"], "ja")
        self.assertEqual(multilingual["detected_language_scope"], "initial_detection_only")
        self.assertEqual(multilingual_model.calls[0][1]["language"], None)
        self.assertTrue(multilingual_model.calls[0][1]["multilingual"])
        self.assertEqual(
            multilingual_model.calls[0][1]["word_timestamps"],
            legacy_model.calls[0][1]["word_timestamps"],
        )

    def test_multilingual_requires_implicit_language_detection(self):
        with self.assertRaisesRegex(ValueError, "language=None"):
            self._observe(config=replace(self.config, multilingual=True))

    def test_multilingual_flag_requires_a_real_bool(self):
        for invalid in (1, 0, "true", None):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "multilingual must be a bool"):
                    self._observe(config=replace(self.config, multilingual=invalid))

    def test_float_policy_rejects_custom_loader_even_on_cache_hit(self):
        config = replace(self.config, decode_policy=FLOAT_DECODE_POLICY)
        with patch('lyric_aligner.audio.float_decode.decode_float_audio', return_value=([0.] * 160, {})):
            self._observe(model=_FakeModel([]), config=config)
        with self.assertRaisesRegex(ValueError, 'custom audio loader'):
            self._observe(model=_FakeModel([]), config=config, audio=[1.] * 160)

    def test_zero_duration_word_is_preserved_as_observed_text_without_fake_duration(self):
        model = _FakeModel(
            [
                SimpleNamespace(
                    words=[SimpleNamespace(start=0.5001, end=0.5004, word="brief", probability=0.5)]
                )
            ]
        )
        result = self._observe(model=model, audio=[0.0] * 16_000)
        self.assertEqual(result["words"][0]["start_ms"], 500)
        self.assertEqual(result["words"][0]["end_ms"], 500)
        self.assertEqual(result["words"][0]["text"], "brief")

    def test_missing_local_model_and_optional_backend_are_recoverable_unavailable(self):
        unavailable = observe_source(
            audio_path=self.audio,
            audio_sha256=self.audio_sha,
            config=SourceObservationConfig(model_path=str(self.root / "missing")),
            cache_dir=self.cache_dir,
        )
        self.assertEqual(unavailable["status"], "unavailable")
        self.assertEqual(unavailable["reason"], "local_model_unavailable")
        self.assertEqual(unavailable["words"], [])

        with patch(
            "lyric_aligner.alignment.source_observer._runtime_identity",
            side_effect=importlib.metadata.PackageNotFoundError,
        ):
            unavailable_backend = observe_source(
                audio_path=self.audio,
                audio_sha256=self.audio_sha,
                config=self.config,
                cache_dir=self.cache_dir,
            )
        self.assertEqual(unavailable_backend["status"], "unavailable")
        self.assertEqual(unavailable_backend["reason"], "optional_backend_unavailable")

    def test_stale_or_tampered_cache_is_rejected_before_decode(self):
        model = _FakeModel([SimpleNamespace(words=[])])
        result = self._observe(model=model, audio=[0.0] * 16_000)
        cache_path = self.cache_dir / f"{result['cache_key_sha256']}.source.json"
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        cached["source_audio_sha256"] = "0" * 64
        cache_path.write_text(json.dumps(cached), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "cache identity/hash mismatch"):
            self._observe(model=_FakeModel([]), audio=[0.0] * 16_000)

    def test_stale_identity_hash_cannot_be_reused_under_its_old_key(self):
        model = _FakeModel([SimpleNamespace(words=[])])
        result = self._observe(model=model, audio=[0.0] * 16_000)
        cache_path = self.cache_dir / f"{result['cache_key_sha256']}.source.json"
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        cached["identity"]["config"]["beam_size"] = 99
        cached["artifact_sha256"] = json_sha({key: value for key, value in cached.items() if key != "artifact_sha256"})
        cache_path.write_text(json.dumps(cached), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "cache identity/hash mismatch"):
            self._observe(model=_FakeModel([]), audio=[0.0] * 16_000)

    def test_corrupt_cache_is_rejected_as_cache_error(self):
        model = _FakeModel([SimpleNamespace(words=[])])
        result = self._observe(model=model, audio=[0.0] * 16_000)
        cache_path = self.cache_dir / f"{result['cache_key_sha256']}.source.json"
        cache_path.write_text("[]", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "cache must be a JSON object"):
            self._observe(model=_FakeModel([]), audio=[0.0] * 16_000)

    def test_audio_hash_is_verified_before_model_or_cache_lookup(self):
        with self.assertRaisesRegex(ValueError, "source audio SHA mismatch"):
            self._observe(model=_FakeModel([]), audio=[0.0] * 16_000, audio_sha="f" * 64)

    def test_concurrent_publication_never_exposes_partial_cache_or_overwrites_winner(self):
        """Two identical jobs publish one complete immutable cache entry."""

        barrier = threading.Barrier(2)
        class BarrierModel(_FakeModel):
            def transcribe(self, audio, **kwargs):
                barrier.wait(timeout=10)
                return super().transcribe(audio, **kwargs)

        def observe_concurrently(label):
            segments = [
                SimpleNamespace(
                    words=[SimpleNamespace(start=0.25, end=0.75, word=f" {label}", probability=0.9)]
                )
            ]
            return self._observe(
                model=BarrierModel(segments),
                audio=[0.0] * 16_000,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(observe_concurrently, ("first", "second")))

        self.assertEqual(sorted(result["cache_hit"] for result in results), [False, True])
        self.assertEqual(
            {result["artifact_sha256"] for result in results},
            {results[0]["artifact_sha256"]},
        )
        self.assertEqual({result["words"][0]["text"] for result in results}, {results[0]["words"][0]["text"]})
        cache_files = list(self.cache_dir.glob("*.source.json"))
        self.assertEqual(len(cache_files), 1)
        published = json.loads(cache_files[0].read_text(encoding="utf-8"))
        self.assertEqual(published["artifact_sha256"], results[0]["artifact_sha256"])
        self.assertFalse(list(self.cache_dir.glob(".*.tmp")))

        reread = self._observe(model=_FakeModel([]), audio=[0.0] * 1)
        self.assertTrue(reread["cache_hit"])
        self.assertEqual(reread["artifact_sha256"], published["artifact_sha256"])


if __name__ == "__main__":
    unittest.main()
