from __future__ import annotations

import unittest

from lyric_aligner.timeline.smart_policy import SMART_POLICY_ID


class SmartBpmBoundedStreamPolicyV123Tests(unittest.TestCase):
    def test_policy_id_is_v123(self) -> None:
        self.assertEqual(
            SMART_POLICY_ID,
            "smart-validation-policy-2026-08-21-v1.2.3",
        )


if __name__ == "__main__":
    unittest.main()
