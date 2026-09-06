import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import v4_select_independent_fine_holdout_pairs as selector


class IndependentFineHoldoutSelectionTests(unittest.TestCase):
    def _policy(self):
        return {
            "schema_version": "independent-fine-local-support-policy-1.0",
            "holdout_status": "not_run_at_policy_freeze",
            "production_authoritative": False,
            "automatic_mutation_allowed": False,
            "artifact_sha256": "f" * 64,
        }

    def test_source_title_dictionary_preserves_title_digits_and_parses_suffix_bpm(self):
        parsed = selector._parse_adjusted_stem(
            "BoA - NO.1103-110 -  - 输出 - Stereo Out",
            ["BoA - NO.1", "BoA - NO."],
        )
        self.assertEqual(parsed, ("BoA - NO.1", 103.0, 110.0))

    def test_selects_unused_sorted_pairs_and_excludes_gee_and_more_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_dir = root / "private/source"
            adjusted_dir = root / "private/adjusted"
            source_dir.mkdir(parents=True)
            adjusted_dir.mkdir(parents=True)
            names = [
                ("C Song", "100"),
                ("A Song", "101"),
                ("B Song", "102"),
                ("D Song", "103"),
                ("E Song", "104"),
                ("F Song", "105"),
            ]
            source_shas = {}
            for title, bpm in names:
                source = source_dir / f"{title}.flac"
                source.write_bytes(("source-" + title).encode())
                source_shas[title] = selector.sha256_file(source)
                adjusted = adjusted_dir / f"{title}{bpm}-110 -  - 输出 - Stereo Out.wav"
                adjusted.write_bytes(("mix-" + title).encode())
            (source_dir / "Gee.flac").write_bytes(b"gee")
            (adjusted_dir / "Gee100-110 -  - 输出 - Stereo Out.wav").write_bytes(b"gee-mix")
            (source_dir / "More Song.flac").write_bytes(b"more")
            (adjusted_dir / "More Song100-110 更 -  - 输出 - Stereo Out.wav").write_bytes(b"more-mix")

            calibration = {
                "manifest_sha256": "c" * 64,
                "records": [{"source_sha256": source_shas["A Song"]}],
            }
            with (
                mock.patch.object(selector, "REPOSITORY_ROOT", root),
                mock.patch.object(selector, "load_manifest", return_value=calibration),
                mock.patch.object(selector, "_load_hashed", return_value=self._policy()),
            ):
                artifact = selector.select_pairs(
                    source_dir=source_dir,
                    adjusted_dir=adjusted_dir,
                    calibration_manifest_path=root / "calibration.json",
                    frozen_policy_path=root / "policy.json",
                    pair_count=4,
                )
            self.assertEqual(artifact["partition"], "holdout")
            self.assertEqual([row["title"] for row in artifact["records"]], ["B Song", "C Song", "D Song", "E Song"])
            self.assertFalse(artifact["independent_fine_predictions_consulted"])
            self.assertEqual(artifact["frozen_policy_sha256"], "f" * 64)

    def test_requires_pre_holdout_frozen_policy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_dir = root / "source"
            adjusted_dir = root / "adjusted"
            source_dir.mkdir()
            adjusted_dir.mkdir()
            policy = self._policy()
            policy["holdout_status"] = "already_run"
            with (
                mock.patch.object(selector, "load_manifest", return_value={"manifest_sha256": "c" * 64, "records": []}),
                mock.patch.object(selector, "_load_hashed", return_value=policy),
            ):
                with self.assertRaisesRegex(selector.IndependentFineHoldoutSelectionError, "pre-holdout state"):
                    selector.select_pairs(
                        source_dir=source_dir,
                        adjusted_dir=adjusted_dir,
                        calibration_manifest_path=root / "calibration.json",
                        frozen_policy_path=root / "policy.json",
                        pair_count=4,
                    )


if __name__ == "__main__":
    unittest.main()
