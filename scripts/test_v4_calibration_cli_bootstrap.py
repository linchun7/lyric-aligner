import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class V4CalibrationCLIBootstrapTests(unittest.TestCase):
    def test_strict_calibration_workflow_imports_without_pythonpath(self):
        completed = subprocess.run(
            [sys.executable, "scripts/v4_calibration_workflow.py", "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            env={},
            check=False,
        )
        self.assertEqual(completed.returncode, 0, msg=completed.stderr)
        self.assertIn("usage:", completed.stdout.casefold())


if __name__ == "__main__":
    unittest.main()
