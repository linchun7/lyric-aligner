import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class V4CLIBootstrapTests(unittest.TestCase):
    def test_v4_clis_import_from_repository_root_without_pythonpath(self):
        for script in (
            "scripts/v4_validate_release.py",
            "scripts/v4_resolve_assets.py",
            "scripts/v4_coarse_align.py",
        ):
            completed = subprocess.run(
                [sys.executable, script, "--help"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                env={},
                check=False,
            )
            self.assertEqual(
                completed.returncode,
                0,
                msg=f"{script}: {completed.stderr}",
            )
            self.assertIn("usage:", completed.stdout.casefold())


if __name__ == "__main__":
    unittest.main()
