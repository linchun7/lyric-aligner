from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY_ROOT / "scripts" / "v4_doctor.py"


class DoctorPartialRepairCliTests(unittest.TestCase):
    def test_help_exposes_partial_repair_inputs_and_requirements(self):
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            cwd=REPOSITORY_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--partial-trust-lock", completed.stdout)
        self.assertIn("--partial-trust-decisions", completed.stdout)
        self.assertIn("--partial-trust-decisions-artifact", completed.stdout)
        self.assertIn("partial_repair:proposal_inputs", completed.stdout)

    def test_partial_lineage_requirement_fails_without_formal_inputs(self):
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--no-backend-check",
                "--require",
                "partial_repair:lineage",
            ],
            cwd=REPOSITORY_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 2, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertFalse(report["requirements"]["passed"])
        self.assertFalse(
            report["requirements"]["results"]["partial_repair:lineage"]
        )
        self.assertEqual(
            report["partial_timeline_repair"]["status"],
            "not_requested",
        )
        self.assertFalse(
            report["partial_timeline_repair"][
                "automatic_timing_change_allowed"
            ]
        )


if __name__ == "__main__":
    unittest.main()
