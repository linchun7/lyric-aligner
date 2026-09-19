import json
import tempfile
import unittest
from pathlib import Path

from editor_evidence import editor_evidence_profile
from language_profiles import boundary_units_for_language, language_code, thresholds
from validate_multilingual_asr import load_jobs


class V4LanguageFoundationTests(unittest.TestCase):
    def test_cantonese_is_distinct_from_mandarin(self):
        self.assertEqual(language_code("cantonese"), "yue")
        self.assertEqual(boundary_units_for_language("yue", "廣東歌"), list("廣東歌"))
        self.assertNotEqual(thresholds("yue"), thresholds("zh"))

    def test_non_editor_languages_cannot_directly_claim_canonical_text(self):
        self.assertTrue(editor_evidence_profile("en").allow_direct_canonical_match)
        self.assertTrue(editor_evidence_profile("zh").allow_direct_canonical_match)
        for language in ("yue", "ko", "ja", "mixed", "auto", "generic"):
            profile = editor_evidence_profile(language)
            self.assertFalse(profile.allow_direct_canonical_match)
            self.assertLess(profile.text_weight, 0.20)

    def test_korean_and_japanese_keep_phonetic_hint_mode(self):
        self.assertEqual(editor_evidence_profile("ko").mode, "phonetic_hint")
        self.assertEqual(editor_evidence_profile("ja").mode, "phonetic_hint")
        self.assertTrue(editor_evidence_profile("ko").allow_phonetic_hint)
        self.assertTrue(editor_evidence_profile("ja").allow_phonetic_hint)

    def test_auto_language_uses_asr_detection(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = Path(directory) / "jobs.json"
            jobs.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "jobs": [
                            {
                                "id": "a",
                                "track": "track",
                                "start": 0,
                                "end": 3,
                                "language": "auto",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            loaded = load_jobs(jobs)
            self.assertEqual(loaded[0]["language"], "auto")
            self.assertEqual(loaded[0]["language_mode"], "detect")


if __name__ == "__main__":
    unittest.main()
