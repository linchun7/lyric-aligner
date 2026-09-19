import json
import tempfile
import unittest
from pathlib import Path

from language_profiles import (
    boundary_units_for_language,
    evidence_capability,
    normalize_for_evidence,
    thresholds,
)
from redo_karaoke_pipeline import release_gate
from validate_multilingual_asr import load_jobs


class ReleaseGateTests(unittest.TestCase):
    def test_any_review_candidate_blocks_release(self):
        release = release_gate([], [{"risk": "low"}])

        self.assertTrue(release["structurally_valid"])
        self.assertFalse(release["fully_reviewed"])
        self.assertFalse(release["publish_ready"])
        self.assertEqual(release["release_status"], "blocked")

    def test_release_requires_structure_and_zero_candidates(self):
        self.assertTrue(release_gate([], [])["publish_ready"])
        self.assertFalse(release_gate(["broken"], [])["publish_ready"])


class LanguageProfileTests(unittest.TestCase):
    def test_language_thresholds_are_not_one_global_constant(self):
        values = {language: thresholds(language)["auto_score"] for language in ("en", "zh", "ko", "ja", "mixed")}

        self.assertGreater(len(set(values.values())), 1)
        self.assertGreater(values["mixed"], values["ko"])

    def test_japanese_kanji_requires_reading_layer_for_high_confidence(self):
        capability = evidence_capability("ja", "未来へ")

        if capability["pronunciation_available"]:
            self.assertTrue(capability["high_confidence_allowed"])
        else:
            self.assertFalse(capability["high_confidence_allowed"])

    def test_language_specific_boundary_units(self):
        self.assertEqual(boundary_units_for_language("en", "we're re-born"), ["we're", "re", "born"])
        self.assertEqual(boundary_units_for_language("zh", "向前走"), list("向前走"))
        self.assertEqual(boundary_units_for_language("ko", "다시 go"), ["다시", "go"])
        self.assertEqual(normalize_for_evidence("ja", "カタカナ"), "かたかな")


class MultilingualAsrJobTests(unittest.TestCase):
    def test_schema_one_jobs_support_fixed_and_detect_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jobs.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "jobs": [
                            {
                                "id": "ko-window",
                                "track": "synthetic",
                                "start": 1,
                                "end": 2,
                                "language": "ko",
                            },
                            {
                                "id": "mixed-window",
                                "track": "synthetic",
                                "start": 2,
                                "end": 3,
                                "language": "mixed",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            jobs = load_jobs(path)

            self.assertEqual(jobs[0]["language_mode"], "fixed")
            self.assertEqual(jobs[1]["language_mode"], "detect")

    def test_jobs_require_schema_outside_legacy_wrapper(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jobs.json"
            path.write_text(json.dumps({"jobs": [{"track": "x", "start": 0, "end": 1, "language": "ko"}]}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "schema_version"):
                load_jobs(path)

    def test_same_track_may_have_multiple_windows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jobs.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "jobs": [
                            {"id": "a", "track": "x", "start": 0, "end": 1, "language": "ko"},
                            {"id": "b", "track": "x", "start": 10, "end": 11, "language": "ko"},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            jobs = load_jobs(path)

            self.assertEqual([job["id"] for job in jobs], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
