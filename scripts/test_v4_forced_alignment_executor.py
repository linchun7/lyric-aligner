import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from lyric_aligner.alignment.backends import inspect_backends
from lyric_aligner.alignment.forced_executor import (
    ExternalForcedAlignmentConfig,
    ForcedAlignmentExecutionError,
    execute_external_forced_alignment_jobs,
)
from lyric_aligner.assets.bindings import CanonicalOriginal, ResolvedAssetBinding


class V4ForcedAlignmentExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.wav"
        self.source.write_bytes(b"synthetic-source-audio")
        self.source_sha = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.canonical = "hello world"
        self.canonical_sha = hashlib.sha256(self.canonical.encode("utf-8")).hexdigest()
        self.binding = ResolvedAssetBinding(
            ordinal=1,
            occurrence_id="occ-1",
            track_id="track-1",
            artist="Artist",
            title="Song",
            version_id="source-v1",
            nominal_start_ms=0,
            middle_cut="false",
            language_profile="en",
            source_audio_path=str(self.source),
            source_audio_sha256=self.source_sha,
            canonical_lyric_path=str(self.root / "song.lrc"),
            canonical_lyric_sha256="b" * 64,
            canonical_selection_sha256="c" * 64,
            canonical_originals=(
                CanonicalOriginal(timestamp_ms=1000, alternative_index=0, text=self.canonical),
            ),
        )
        self.plan = {
            "mode": "plan_only",
            "backend_execution_performed": False,
            "jobs": [
                {
                    "job_id": "job-1",
                    "occurrence_id": "occ-1",
                    "track_id": "track-1",
                    "ordinal": 1,
                    "language_profile": "en",
                    "canonical_line_index": 0,
                    "canonical_text_sha256": self.canonical_sha,
                    "source_window_ms": [500, 2500],
                    "mix_window_ms": [3000, 5000],
                    "requested_capabilities": [
                        "mix_asr",
                        "word_timestamps",
                        "source_forced_alignment",
                    ],
                }
            ],
        }
        self.config = ExternalForcedAlignmentConfig(
            command=sys.executable,
            backend_id="fake-aligner",
            backend_version="1.2.3",
            model_id="fake-model",
            model_revision="rev-abc",
            timeout_seconds=10,
        )

    def tearDown(self):
        self.temporary.cleanup()

    def fake_runner(self, argv, **kwargs):
        request_path = Path(argv[argv.index("--request") + 1])
        response_path = Path(argv[argv.index("--response") + 1])
        request = json.loads(request_path.read_text(encoding="utf-8"))
        self.last_request = request
        response = {
            "protocol_version": "1.0",
            "job_id": request["job_id"],
            "backend_id": request["backend_id"],
            "backend_version": request["backend_version"],
            "model_id": request["model_id"],
            "model_revision": request["model_revision"],
            "status": "aligned",
            "source_window_ms": request["source_window_ms"],
            "line_source_start_ms": 900,
            "line_source_end_ms": 2100,
            "line_confidence": 0.94,
            "spans": [
                {
                    "char_start": 0,
                    "char_end": 5,
                    "source_start_ms": 900,
                    "source_end_ms": 1300,
                    "confidence": 0.95,
                },
                {
                    "char_start": 6,
                    "char_end": 11,
                    "source_start_ms": 1500,
                    "source_end_ms": 2100,
                    "confidence": 0.93,
                },
            ],
        }
        response_path.write_text(json.dumps(response), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def test_executes_exact_source_job_and_output_contains_hashes_not_text(self):
        result = execute_external_forced_alignment_jobs(
            plan=self.plan,
            bindings=[self.binding],
            canonical_text_by_job_id={"job-1": self.canonical},
            config=self.config,
            runner=self.fake_runner,
        )
        self.assertTrue(result["command_invoked"])
        self.assertEqual(result["job_count"], 1)
        row = result["jobs"][0]
        self.assertEqual(row["line_source_start_ms"], 900)
        self.assertEqual(row["line_source_end_ms"], 2100)
        self.assertEqual(row["source_audio_sha256"], self.source_sha)
        self.assertEqual(row["canonical_text_sha256"], self.canonical_sha)
        self.assertEqual(row["span_count"], 2)
        self.assertEqual(row["spans"][0]["char_start"], 0)
        self.assertEqual(row["spans"][0]["char_end"], 5)
        self.assertEqual(
            row["spans"][0]["canonical_fragment_sha256"],
            hashlib.sha256(b"hello").hexdigest(),
        )
        self.assertEqual(self.last_request["canonical_text"], self.canonical)
        self.assertEqual(self.last_request["source_window_ms"], [500, 2500])
        serialized = json.dumps(result)
        self.assertNotIn(self.canonical, serialized)
        self.assertNotIn("hello", serialized)
        self.assertNotIn("world", serialized)

    def test_empty_selected_jobs_never_resolve_or_invoke_command(self):
        config = ExternalForcedAlignmentConfig(
            command="command-that-does-not-exist-for-zero-work",
            backend_id="fake-aligner",
            backend_version="1",
            model_id="fake-model",
            model_revision="rev",
        )
        result = execute_external_forced_alignment_jobs(
            plan=self.plan,
            bindings=[self.binding],
            canonical_text_by_job_id={},
            config=config,
            selected_job_ids=[],
            runner=lambda *args, **kwargs: self.fail("runner must not execute"),
        )
        self.assertFalse(result["command_invoked"])
        self.assertEqual(result["job_count"], 0)

    def test_source_audio_hash_change_fails_before_runner(self):
        self.source.write_bytes(b"tampered")
        with self.assertRaisesRegex(ForcedAlignmentExecutionError, "source audio hash changed"):
            execute_external_forced_alignment_jobs(
                plan=self.plan,
                bindings=[self.binding],
                canonical_text_by_job_id={"job-1": self.canonical},
                config=self.config,
                runner=lambda *args, **kwargs: self.fail("runner must not execute"),
            )

    def test_canonical_identity_mismatch_fails(self):
        with self.assertRaisesRegex(ForcedAlignmentExecutionError, "canonical text identity mismatch"):
            execute_external_forced_alignment_jobs(
                plan=self.plan,
                bindings=[self.binding],
                canonical_text_by_job_id={"job-1": "different"},
                config=self.config,
                runner=self.fake_runner,
            )

    def test_response_model_revision_mismatch_fails(self):
        def runner(argv, **kwargs):
            request_path = Path(argv[argv.index("--request") + 1])
            response_path = Path(argv[argv.index("--response") + 1])
            request = json.loads(request_path.read_text(encoding="utf-8"))
            response_path.write_text(
                json.dumps(
                    {
                        "protocol_version": "1.0",
                        "job_id": request["job_id"],
                        "backend_id": request["backend_id"],
                        "backend_version": request["backend_version"],
                        "model_id": request["model_id"],
                        "model_revision": "WRONG",
                        "status": "aligned",
                        "source_window_ms": request["source_window_ms"],
                        "line_source_start_ms": 900,
                        "line_source_end_ms": 2100,
                    }
                ),
                encoding="utf-8",
            )
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with self.assertRaisesRegex(ForcedAlignmentExecutionError, "model_revision mismatch"):
            execute_external_forced_alignment_jobs(
                plan=self.plan,
                bindings=[self.binding],
                canonical_text_by_job_id={"job-1": self.canonical},
                config=self.config,
                runner=runner,
            )

    def test_out_of_window_span_fails(self):
        def runner(argv, **kwargs):
            request_path = Path(argv[argv.index("--request") + 1])
            response_path = Path(argv[argv.index("--response") + 1])
            request = json.loads(request_path.read_text(encoding="utf-8"))
            response_path.write_text(
                json.dumps(
                    {
                        "protocol_version": "1.0",
                        "job_id": request["job_id"],
                        "backend_id": request["backend_id"],
                        "backend_version": request["backend_version"],
                        "model_id": request["model_id"],
                        "model_revision": request["model_revision"],
                        "status": "aligned",
                        "source_window_ms": request["source_window_ms"],
                        "line_source_start_ms": 900,
                        "line_source_end_ms": 2100,
                        "spans": [
                            {
                                "char_start": 0,
                                "char_end": 5,
                                "source_start_ms": 100,
                                "source_end_ms": 1300,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with self.assertRaisesRegex(ForcedAlignmentExecutionError, "outside source window"):
            execute_external_forced_alignment_jobs(
                plan=self.plan,
                bindings=[self.binding],
                canonical_text_by_job_id={"job-1": self.canonical},
                config=self.config,
                runner=runner,
            )

    def test_backend_registry_handles_command_with_arguments(self):
        command = f'"{sys.executable}" --some-adapter-argument'
        statuses = inspect_backends(external_forced_aligner_command=command)
        forced = next(row for row in statuses if row.backend_id == "external_forced_aligner")
        self.assertTrue(forced.available)
        self.assertTrue(forced.execution_ready)
        self.assertIn("arguments preserved", forced.detail)


if __name__ == "__main__":
    unittest.main()
