import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from task_contract import resolve_repository_path

from scripts import (
    v4_editor_evidence,
    v4_execute_asr_evidence,
    v4_execute_asr_second_pass,
    v4_execute_forced_alignment,
    v4_fuse_evidence,
    v4_plan_alignment,
    v4_project_forced_alignment,
)


class ReferenceRetimeEvidenceStageTests(unittest.TestCase):
    def test_reference_retime_run_role_is_supported_by_mix_evidence_chain(self):
        modules = (
            v4_editor_evidence,
            v4_execute_asr_evidence,
            v4_execute_asr_second_pass,
            v4_fuse_evidence,
            v4_plan_alignment,
        )
        for module in modules:
            with self.subTest(module=module.__name__):
                self.assertEqual(
                    module._RUN_ROLES.get("reference_retime"),
                    "v4_reference_retimed_run",
                )

    def test_reference_retimed_timeline_stage_is_supported_by_mix_timeline_consumers(self):
        modules = (
            v4_editor_evidence,
            v4_execute_asr_evidence,
            v4_execute_asr_second_pass,
            v4_fuse_evidence,
            v4_plan_alignment,
        )
        for module in modules:
            with self.subTest(module=module.__name__):
                self.assertIn("reference_timeline_retime", module._TIMELINE_STAGES)

    def test_repository_relative_run_paths_do_not_depend_on_cwd(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            relative = Path("output") / "reference" / "timeline.json"
            self.assertEqual(
                resolve_repository_path(relative, root),
                (root / relative).resolve(),
            )
            absolute = (root / "absolute.json").resolve()
            self.assertEqual(resolve_repository_path(absolute, root), absolute)
            with self.assertRaisesRegex(ValueError, "escapes repository"):
                resolve_repository_path(Path("..") / "escape.json", root)

    def test_reference_retime_remains_fail_closed_for_source_forced_alignment(self):
        self.assertNotIn("reference_retime", v4_execute_forced_alignment._RUN_ROLES)
        self.assertNotIn("reference_timeline_retime", v4_execute_forced_alignment._TIMELINE_STAGES)
        self.assertNotIn("reference_retime", v4_project_forced_alignment._RUN_ROLES)


if __name__ == "__main__":
    unittest.main()
