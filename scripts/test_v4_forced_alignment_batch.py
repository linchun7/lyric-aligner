import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from lyric_aligner.alignment.forced_batch import (
    FORCED_ALIGNMENT_BATCH_PROTOCOL_VERSION,
    execute_external_forced_alignment_batch,
)
from lyric_aligner.alignment.forced_executor import (
    ExternalForcedAlignmentConfig,
    ForcedAlignmentExecutionError,
)
from lyric_aligner.assets.bindings import CanonicalOriginal, ResolvedAssetBinding


class V4ForcedAlignmentBatchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.wav"
        self.source.write_bytes(b"batch-source-audio")
        source_sha = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.texts = {"job-1": "hello world", "job-2": "second line"}
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
            source_audio_sha256=source_sha,
            canonical_lyric_path=str(self.root / "song.lrc"),
            canonical_lyric_sha256="b" * 64,
            canonical_selection_sha256="c" * 64,
            canonical_originals=(
                CanonicalOriginal(timestamp_ms=1000, alternative_index=0, text="hello world"),
                CanonicalOriginal(timestamp_ms=3000, alternative_index=0, text="second line"),
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
                    "canonical_text_sha256": hashlib.sha256(b"hello world").hexdigest(),
                    "source_window_ms": [500, 2500],
                    "mix_window_ms": [3000, 5000],
                    "requested_capabilities": ["source_forced_alignment"],
                },
                {
                    "job_id": "job-2",
                    "occurrence_id": "occ-1",
                    "track_id": "track-1",
                    "ordinal": 1,
                    "language_profile": "en",
                    "canonical_line_index": 1,
                    "canonical_text_sha256": hashlib.sha256(b"second line").hexdigest(),
                    "source_window_ms": [2500, 5000],
                    "mix_window_ms": [5000, 7500],
                    "requested_capabilities": ["source_forced_alignment"],
                },
            ],
        }
        self.config = ExternalForcedAlignmentConfig(
            command=sys.executable,
            backend_id="fake-batch-aligner",
            backend_version="1.0",
            model_id="fake-model",
            model_revision="rev-a",
            timeout_seconds=10,
        )

    def tearDown(self):
        self.temporary.cleanup()

    def good_runner(self, argv, **kwargs):
        self.calls = getattr(self, "calls", 0) + 1
        request_path = Path(argv[argv.index("--batch-request") + 1])
        response_path = Path(argv[argv.index("--batch-response") + 1])
        request = json.loads(request_path.read_text(encoding="utf-8"))
        self.last_request = request
        rows = []
        for index, job in enumerate(request["jobs"]):
            start = job["source_window_ms"][0] + 100
            end = min(job["source_window_ms"][1] - 100, start + 1000)
            rows.append(
                {
                    "job_id": job["job_id"],
                    "status": "aligned",
                    "source_window_ms": job["source_window_ms"],
                    "line_source_start_ms": start,
                    "line_source_end_ms": end,
                    "line_confidence": 0.9 - index * 0.05,
                    "spans": [],
                }
            )
        response = {
            "protocol_version": FORCED_ALIGNMENT_BATCH_PROTOCOL_VERSION,
            "backend_id": request["backend_id"],
            "backend_version": request["backend_version"],
            "model_id": request["model_id"],
            "model_revision": request["model_revision"],
            "status": "aligned_batch",
            "jobs": rows,
        }
        response_path.write_text(json.dumps(response), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def test_two_jobs_invoke_one_process_and_preserve_formal_privacy(self):
        self.calls = 0
        result = execute_external_forced_alignment_batch(
            plan=self.plan,
            bindings=[self.binding],
            canonical_text_by_job_id=self.texts,
            config=self.config,
            runner=self.good_runner,
        )
        self.assertEqual(self.calls, 1)
        self.assertEqual(result["execution_mode"], "batch_subprocess")
        self.assertEqual(result["batch_protocol_version"], "1.1")
        self.assertEqual(result["command_invocation_count"], 1)
        self.assertEqual(result["job_count"], 2)
        self.assertEqual(len(self.last_request["jobs"]), 2)
        self.assertEqual(
            [row["canonical_text"] for row in self.last_request["jobs"]],
            ["hello world", "second line"],
        )
        serialized = json.dumps(result)
        self.assertNotIn("hello world", serialized)
        self.assertNotIn("second line", serialized)

    def test_explicit_empty_selection_never_resolves_or_invokes_command(self):
        config = ExternalForcedAlignmentConfig(
            command="definitely-not-a-real-batch-aligner-command",
            backend_id="fake",
            backend_version="1",
            model_id="model",
            model_revision="rev",
        )
        result = execute_external_forced_alignment_batch(
            plan=self.plan,
            bindings=[self.binding],
            canonical_text_by_job_id={},
            config=config,
            selected_job_ids=[],
            runner=lambda *args, **kwargs: self.fail("runner must not execute"),
        )
        self.assertFalse(result["command_invoked"])
        self.assertEqual(result["command_invocation_count"], 0)
        self.assertEqual(result["job_count"], 0)

    def test_selected_subset_still_invokes_only_one_process(self):
        self.calls = 0
        result = execute_external_forced_alignment_batch(
            plan=self.plan,
            bindings=[self.binding],
            canonical_text_by_job_id=self.texts,
            config=self.config,
            selected_job_ids=["job-2"],
            runner=self.good_runner,
        )
        self.assertEqual(self.calls, 1)
        self.assertEqual(result["job_count"], 1)
        self.assertEqual(result["jobs"][0]["job_id"], "job-2")
        self.assertEqual([row["job_id"] for row in self.last_request["jobs"]], ["job-2"])

    def test_missing_batch_response_job_fails_closed(self):
        def runner(argv, **kwargs):
            request_path = Path(argv[argv.index("--batch-request") + 1])
            response_path = Path(argv[argv.index("--batch-response") + 1])
            request = json.loads(request_path.read_text(encoding="utf-8"))
            response_path.write_text(
                json.dumps(
                    {
                        "protocol_version": "1.1",
                        "backend_id": request["backend_id"],
                        "backend_version": request["backend_version"],
                        "model_id": request["model_id"],
                        "model_revision": request["model_revision"],
                        "status": "aligned_batch",
                        "jobs": [
                            {
                                "job_id": "job-1",
                                "status": "aligned",
                                "source_window_ms": [500, 2500],
                                "line_source_start_ms": 600,
                                "line_source_end_ms": 1600,
                                "spans": [],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with self.assertRaisesRegex(
            ForcedAlignmentExecutionError, "do not exactly match request"
        ):
            execute_external_forced_alignment_batch(
                plan=self.plan,
                bindings=[self.binding],
                canonical_text_by_job_id=self.texts,
                config=self.config,
                runner=runner,
            )

    def test_duplicate_batch_response_job_id_fails_closed(self):
        def runner(argv, **kwargs):
            request_path = Path(argv[argv.index("--batch-request") + 1])
            response_path = Path(argv[argv.index("--batch-response") + 1])
            request = json.loads(request_path.read_text(encoding="utf-8"))
            row = {
                "job_id": "job-1",
                "status": "aligned",
                "source_window_ms": [500, 2500],
                "line_source_start_ms": 600,
                "line_source_end_ms": 1600,
                "spans": [],
            }
            response_path.write_text(
                json.dumps(
                    {
                        "protocol_version": "1.1",
                        "backend_id": request["backend_id"],
                        "backend_version": request["backend_version"],
                        "model_id": request["model_id"],
                        "model_revision": request["model_revision"],
                        "status": "aligned_batch",
                        "jobs": [row, row],
                    }
                ),
                encoding="utf-8",
            )
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with self.assertRaisesRegex(
            ForcedAlignmentExecutionError, "unique/non-empty"
        ):
            execute_external_forced_alignment_batch(
                plan=self.plan,
                bindings=[self.binding],
                canonical_text_by_job_id=self.texts,
                config=self.config,
                runner=runner,
            )


if __name__ == "__main__":
    unittest.main()
