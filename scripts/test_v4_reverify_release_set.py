import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.v4_reverify_release_set as reverify_module
from scripts.v4_reverify_release_set import invalidation_references, raw_unmasked_fword_count


class ReleaseSetReverificationTests(unittest.TestCase):
    def test_unmasked_fword_detection_does_not_flag_masked_form(self):
        self.assertEqual(raw_unmasked_fword_count("keep f* masked"), 0)
        self.assertEqual(raw_unmasked_fword_count("this has fuck and fucking"), 2)

    def test_tracked_invalidation_binds_exact_task_and_srt_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            references = root / "references"
            references.mkdir()
            payload = {
                "task_fingerprint_sha256": "a" * 64,
                "final_srt_sha256": "b" * 64,
                "release_publish_ready": False,
            }
            path = references / "release-invalidation.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with patch.object(reverify_module, "REPOSITORY_ROOT", root):
                self.assertEqual(
                    invalidation_references(fingerprint="a" * 64, final_sha="b" * 64),
                    ["references/release-invalidation.json"],
                )
                self.assertEqual(
                    invalidation_references(fingerprint="a" * 64, final_sha="c" * 64),
                    [],
                )


if __name__ == "__main__":
    unittest.main()
