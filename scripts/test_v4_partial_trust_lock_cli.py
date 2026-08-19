from __future__ import annotations

import argparse
import importlib.util
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).with_name("v4_build_partial_trust_lock.py")


def load_script_module():
    spec = importlib.util.spec_from_file_location("v4_build_partial_trust_lock_tested", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load trust-lock CLI")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PartialTrustLockCliTests(unittest.TestCase):
    def test_output_cannot_overwrite_any_input_and_error_redacts_directory(self):
        module = load_script_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            selection = root / "selection.json"
            args = argparse.Namespace(
                selection=selection,
                calibration_baseline=root / "cal-base.json",
                calibration_candidate=root / "cal-candidate.json",
                calibration_policy=root / "cal-policy.json",
                blind_gate=root / "blind-gate.json",
                blind_baseline=root / "blind-base.json",
                blind_candidate=root / "blind-candidate.json",
                blind_policy=root / "blind-policy.json",
                out=selection,
            )
            with self.assertRaisesRegex(
                module.PartialTimelineRepairError,
                "must not overwrite an input file: selection.json",
            ) as caught:
                module._ensure_output_is_distinct(args)
            self.assertNotIn(str(root), str(caught.exception))

    def test_success_stdout_reports_basename_not_absolute_output_path(self):
        module = load_script_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "partial-trust.lock.json"
            argv = [
                str(SCRIPT),
                "--selection",
                str(root / "selection.json"),
                "--calibration-baseline",
                str(root / "cal-base.json"),
                "--calibration-candidate",
                str(root / "cal-candidate.json"),
                "--calibration-policy",
                str(root / "cal-policy.json"),
                "--blind-gate",
                str(root / "blind-gate.json"),
                "--blind-baseline",
                str(root / "blind-base.json"),
                "--blind-candidate",
                str(root / "blind-candidate.json"),
                "--blind-policy",
                str(root / "blind-policy.json"),
                "--out",
                str(out),
            ]
            fake_payload = {
                "candidate_id": "candidate",
                "candidate_revision": "r1",
                "eligible_language_scopes": ["language:zh"],
                "cue_trust_generation_allowed": True,
                "trust_policy_lock_sha256": "a" * 64,
            }
            stdout = io.StringIO()
            stderr = io.StringIO()
            with patch.object(sys, "argv", argv), patch.object(
                module,
                "build_calibrated_trust_policy_lock",
                return_value=fake_payload,
            ), redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(module.main(), 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertIn('"out_file": "partial-trust.lock.json"', stdout.getvalue())
            self.assertNotIn(str(root), stdout.getvalue())
            self.assertTrue(out.is_file())


if __name__ == "__main__":
    unittest.main()
