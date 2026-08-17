import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "v4_dataset_readiness.py"


def run(*args: str):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


class V4DatasetReadinessCliTests(unittest.TestCase):
    def test_scaffold_check_and_clone_do_not_invent_readiness(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-dataset"
            created = run(
                "scaffold",
                "--out-dir",
                str(root),
                "--dataset",
                "opaque-set",
                "--dataset-revision",
                "r1",
                "--candidate-id",
                "baseline",
                "--calibration-cases",
                "1",
                "--blind-cases",
                "1",
            )
            self.assertEqual(created.returncode, 0, msg=created.stderr)
            self.assertFalse((root / "reference" / "calibration-0001.srt").exists())
            self.assertFalse(
                (root / "predictions" / "baseline" / "calibration-0001.srt").exists()
            )

            manifest = root / "baseline.dataset.json"
            metadata = run(
                "check",
                "--dataset",
                str(manifest),
                "--require",
                "metadata",
            )
            self.assertEqual(metadata.returncode, 0, msg=metadata.stderr)
            references = run(
                "check",
                "--dataset",
                str(manifest),
                "--split",
                "calibration",
                "--require",
                "references",
            )
            self.assertEqual(references.returncode, 2, msg=references.stderr)

            candidate = root / "candidate-a.dataset.json"
            cloned = run(
                "clone-candidate",
                "--source",
                str(manifest),
                "--candidate-id",
                "candidate-a",
                "--out",
                str(candidate),
            )
            self.assertEqual(cloned.returncode, 0, msg=cloned.stderr)
            payload = json.loads(candidate.read_text(encoding="utf-8"))
            self.assertTrue(
                all(
                    "predictions/candidate-a/" in row["predicted_srt"]
                    for row in payload["cases"]
                )
            )
            self.assertTrue(
                all(
                    "predictions/candidate-a/" in row["qa_json"]
                    for row in payload["cases"]
                )
            )

    def test_scaffold_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-dataset"
            args = (
                "scaffold",
                "--out-dir",
                str(root),
                "--dataset",
                "opaque-set",
                "--dataset-revision",
                "r1",
                "--calibration-cases",
                "1",
                "--blind-cases",
                "1",
            )
            self.assertEqual(run(*args).returncode, 0)
            second = run(*args)
            self.assertNotEqual(second.returncode, 0)
            self.assertIn("refusing to overwrite", second.stderr)


if __name__ == "__main__":
    unittest.main()
