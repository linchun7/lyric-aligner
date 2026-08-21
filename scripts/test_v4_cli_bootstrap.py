import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def bootstrap_env() -> dict[str, str]:
    """Keep OS process prerequisites while proving CLIs do not need PYTHONPATH."""

    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONNOUSERSITE"] = "1"
    return env


class V4CLIBootstrapTests(unittest.TestCase):
    def test_v4_clis_import_from_repository_root_without_pythonpath(self):
        for script in (
            "scripts/v4_profile.py",
            "scripts/v4_validate_release.py",
            "scripts/v4_resolve_assets.py",
            "scripts/v4_coarse_align.py",
            "scripts/v4_probe_transition.py",
            "scripts/v4_fine_align.py",
            "scripts/v4_run.py",
            "scripts/v4_review.py",
            "scripts/v4_recompose_overlap.py",
            "scripts/v4_rebuild_cut.py",
            "scripts/v4_compose_materializations.py",
            "scripts/v4_render.py",
            "scripts/v4_smart_repair.py",
            "scripts/v4_pro_selective.py",
        ):
            completed = subprocess.run(
                [sys.executable, script, "--help"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                env=bootstrap_env(),
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
