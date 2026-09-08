import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v4_execute_asr_evidence as cli


class RetryPathSafetyTests(unittest.TestCase):
    def test_both_outputs_cannot_overwrite_retry_model_directory(self):
        for flag in ("--out", "--artifact-out"):
            with self.subTest(flag=flag), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                model = root / "retry-model"
                model.mkdir()
                weights = model / "config.json"
                weights.write_text("model-config", encoding="utf-8")
                argv = ["cli", "--task-manifest", str(root/"task.json"), "--plan", str(root/"plan.json"),
                        "--plan-artifact", str(root/"plan.artifact.json"), "--run", str(root/"run.json"),
                        "--run-artifact", str(root/"run.artifact.json"), "--model-id", "primary-model",
                        "--retry-model-id", str(model), "--out", str(root/"result.json"),
                        "--artifact-out", str(root/"result.artifact.json")]
                argv[argv.index(flag)+1] = str(weights)
                payload = dict(task_fingerprint_sha256="task", algorithm_version=cli.__version__,
                    backend_execution_performed=False, source_run_artifact_id="run",
                    stage="production_orchestration", artifact_id="run", upstream_artifact_ids=["run"])
                task = dict(task_fingerprint_sha256="task", inputs={"audio": {"sha256": "a"*64}})
                with (mock.patch.object(sys, "argv", argv),
                      mock.patch.object(cli, "load_task_manifest", return_value=task),
                      mock.patch.object(cli, "verify_manifest_inputs", return_value=[]),
                      mock.patch.object(cli, "resolve_manifest_record", return_value=root/"mix.wav"),
                      mock.patch.object(cli, "_load", return_value=payload),
                      mock.patch.object(cli, "_validate_artifact"),
                      mock.patch.object(cli, "_canonical_lookup", return_value=({}, set())),
                      mock.patch.object(cli, "protected_task_input_paths", return_value={}),
                      mock.patch.object(cli, "declared_input_paths", return_value={}),
                      mock.patch.object(cli, "_filter_jobs", side_effect=AssertionError("EXECUTION_REACHED")),
                      contextlib.redirect_stderr(io.StringIO())):
                    with self.assertRaises(SystemExit) as caught:
                        cli.main()
                    self.assertEqual(caught.exception.code, 2)
                self.assertEqual(weights.read_text(encoding="utf-8"), "model-config")
