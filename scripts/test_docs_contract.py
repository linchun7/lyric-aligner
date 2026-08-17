import unittest

from validate_docs_contract import validate_changed_paths


class DocumentationContractTests(unittest.TestCase):
    def test_test_only_change_does_not_require_docs(self):
        result = validate_changed_paths(["scripts/test_v4_timewarp.py"])
        self.assertEqual(result.substantive, ())
        self.assertEqual(result.issues, ())

    def test_core_change_requires_change_record_and_status(self):
        result = validate_changed_paths(["lyric_aligner/audio/timewarp.py"])
        self.assertIn("references/v4-change-record.md", " ".join(result.issues))
        self.assertIn("references/v4-status.md", " ".join(result.issues))

    def test_core_change_passes_with_owned_docs(self):
        result = validate_changed_paths(
            [
                "lyric_aligner/audio/timewarp.py",
                "references/v4-change-record.md",
                "references/v4-status.md",
            ]
        )
        self.assertEqual(result.issues, ())

    def test_cli_change_requires_runtime_documentation(self):
        result = validate_changed_paths(
            [
                "scripts/v4_run.py",
                "references/v4-change-record.md",
                "references/v4-status.md",
            ]
        )
        self.assertTrue(any("CLI/workflow" in issue for issue in result.issues))

    def test_contract_change_requires_contract_documentation(self):
        result = validate_changed_paths(
            [
                "lyric_aligner/contracts/artifacts.py",
                "references/v4-change-record.md",
                "references/v4-status.md",
            ]
        )
        self.assertTrue(any("schema/contract" in issue for issue in result.issues))

    def test_new_timeline_layer_requires_architecture_documentation(self):
        result = validate_changed_paths(
            [
                "lyric_aligner/timeline/projector.py",
                "references/v4-change-record.md",
                "references/v4-status.md",
            ]
        )
        self.assertTrue(any("architecture responsibility" in issue for issue in result.issues))

    def test_full_related_document_set_passes(self):
        result = validate_changed_paths(
            [
                "lyric_aligner/timeline/projector.py",
                "scripts/v4_run.py",
                "references/v4-change-record.md",
                "references/v4-status.md",
                "references/v4-runtime-guide.md",
                "references/v4-implementation.md",
            ]
        )
        self.assertEqual(result.issues, ())


if __name__ == "__main__":
    unittest.main()
