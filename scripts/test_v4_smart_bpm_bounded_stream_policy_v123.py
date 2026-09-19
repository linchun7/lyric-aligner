from __future__ import annotations

import unittest

from lyric_aligner.timeline.bpm_sequence_reconcile import BpmTextRecoverySummary
from lyric_aligner.timeline.smart_policy import SMART_POLICY_ID, SMART_SCHEMA_VERSION


class SmartBpmBoundedStreamPolicyV123Tests(unittest.TestCase):
    def test_policy_id_is_v123(self) -> None:
        self.assertEqual(
            SMART_POLICY_ID,
            "smart-validation-policy-2026-08-21-v1.2.4",
        )

    def test_v123_keeps_backward_compatible_smart_schema(self) -> None:
        self.assertEqual(SMART_SCHEMA_VERSION, "smart-1.1")

    def test_bounded_stream_summary_is_explicit(self) -> None:
        summary = BpmTextRecoverySummary()
        self.assertEqual(summary.bounded_stream_cue_count, 0)
        self.assertEqual(summary.bounded_stream_region_count, 0)
        self.assertEqual(summary.bounded_stream_unmapped_cue_count, 0)


if __name__ == "__main__":
    unittest.main()
