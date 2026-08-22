from __future__ import annotations

import unittest
from unittest.mock import patch

from lyric_aligner.timeline import smart_current
from lyric_aligner.timeline.smart_policy_v1210 import (
    SMART_POLICY_ID,
    smart_repair_srt_text_v1210,
)


class SmartPolicyV1210Tests(unittest.TestCase):
    def test_current_binding_records_split_line_timing_contract(self) -> None:
        with patch(
            "lyric_aligner.timeline.smart_policy_v1210.smart_repair_srt_text_v129",
            return_value=("rendered", {"policy_id": "old"}),
        ) as base:
            rendered, report = smart_repair_srt_text_v1210("", [], [])

        self.assertEqual(rendered, "rendered")
        self.assertEqual(
            SMART_POLICY_ID,
            "smart-validation-policy-2026-08-22-v1.2.10",
        )
        self.assertEqual(smart_current.SMART_POLICY_ID, SMART_POLICY_ID)
        self.assertEqual(
            report["segmentation_internal_timing_semantics"],
            "multi_cue_single_line_internal_onset_requires_exact_word_token_boundary",
        )
        self.assertIn("strict_ensemble_context", report["canonical_role_context_semantics"])
        self.assertTrue(
            base.call_args.kwargs["_segmentation_internal_boundary_guard"]
        )


if __name__ == "__main__":
    unittest.main()
