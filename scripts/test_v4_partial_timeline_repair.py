from __future__ import annotations

import unittest

from lyric_aligner.srt import Cue
from lyric_aligner.timeline.partial_repair import (
    CueTrust,
    PartialTimelineRepairError,
    TimingCandidate,
    choose_task_mode,
    plan_partial_timeline_repair,
)


class PartialTimelineRepairTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cues = [
            Cue(1, 1000, 2000, "第一句"),
            Cue(2, 2200, 3200, "第二句"),
            Cue(3, 3500, 4500, "第三句"),
        ]

    def test_all_trusted_routes_to_preserve_and_locks_every_timing(self):
        trust = [
            CueTrust(0, "trusted", "verified"),
            CueTrust(1, "trusted", "verified"),
            CueTrust(2, "trusted", "verified"),
        ]
        plan = plan_partial_timeline_repair(self.cues, trust, [])
        self.assertEqual(plan["task_mode"], "preserve")
        self.assertEqual(plan["status"], "preserve_ready")
        self.assertEqual(plan["action_counts"]["preserve"], 3)
        for cue, decision in zip(self.cues, plan["decisions"]):
            self.assertEqual(decision["action"], "preserve")
            self.assertEqual(decision["original_start_ms"], cue.start_ms)
            self.assertEqual(decision["original_end_ms"], cue.end_ms)
            self.assertIsNone(decision["candidate_start_ms"])
            self.assertIsNone(decision["candidate_end_ms"])

    def test_trusted_untrusted_mix_routes_to_hybrid(self):
        trust = [
            CueTrust(0, "trusted", "good editor timing"),
            CueTrust(1, "untrusted", "known bad cue"),
            CueTrust(2, "trusted", "good editor timing"),
        ]
        self.assertEqual(choose_task_mode(self.cues, trust), "hybrid")

    def test_unknown_trust_routes_to_rebuild_and_review(self):
        trust = [CueTrust(0, "trusted", "verified")]
        plan = plan_partial_timeline_repair(self.cues, trust, [])
        self.assertEqual(plan["task_mode"], "rebuild")
        self.assertEqual(plan["status"], "review_required")
        self.assertEqual(plan["action_counts"]["review"], 2)

    def test_untrusted_cue_can_receive_affine_source_projection_proposal(self):
        trust = [
            CueTrust(0, "trusted", "verified"),
            CueTrust(1, "untrusted", "timing mismatch"),
            CueTrust(2, "trusted", "verified"),
        ]
        candidates = [
            TimingCandidate(
                1,
                2300,
                3300,
                "projected",
                "AFFINE",
                confidence=0.97,
            )
        ]
        plan = plan_partial_timeline_repair(self.cues, trust, candidates)
        decision = plan["decisions"][1]
        self.assertEqual(decision["action"], "propose_repair")
        self.assertEqual(decision["candidate_start_ms"], 2300)
        self.assertEqual(decision["candidate_end_ms"], 3300)
        self.assertEqual(decision["shift_start_ms"], 100)
        self.assertEqual(decision["shift_end_ms"], 100)
        self.assertFalse(plan["publish_ready"])
        self.assertTrue(plan["proposal_only"])

    def test_piecewise_rate_is_valid_continuous_mapping_not_a_cut(self):
        trust = [
            CueTrust(0, "trusted", "verified"),
            CueTrust(1, "untrusted", "timing mismatch"),
            CueTrust(2, "trusted", "verified"),
        ]
        candidates = [
            TimingCandidate(1, 2400, 3300, "projected", "PIECEWISE_RATE")
        ]
        plan = plan_partial_timeline_repair(self.cues, trust, candidates)
        self.assertEqual(plan["decisions"][1]["action"], "propose_repair")
        self.assertIn("rate change is not a cut", plan["rate_change_policy"])

    def test_candidate_cannot_cross_locked_left_neighbor(self):
        trust = [
            CueTrust(0, "trusted", "verified"),
            CueTrust(1, "untrusted", "bad"),
            CueTrust(2, "trusted", "verified"),
        ]
        candidates = [TimingCandidate(1, 1900, 3000, "projected", "AFFINE")]
        plan = plan_partial_timeline_repair(self.cues, trust, candidates)
        self.assertEqual(plan["status"], "blocked")
        self.assertEqual(plan["decisions"][1]["action"], "block")
        self.assertEqual(
            plan["decisions"][1]["reason"],
            "candidate_crosses_locked_left_cue",
        )

    def test_candidate_cannot_cross_locked_right_neighbor(self):
        trust = [
            CueTrust(0, "trusted", "verified"),
            CueTrust(1, "untrusted", "bad"),
            CueTrust(2, "trusted", "verified"),
        ]
        candidates = [TimingCandidate(1, 2400, 3600, "projected", "AFFINE")]
        plan = plan_partial_timeline_repair(self.cues, trust, candidates)
        self.assertEqual(plan["decisions"][1]["action"], "block")
        self.assertEqual(
            plan["decisions"][1]["reason"],
            "candidate_crosses_locked_right_cue",
        )

    def test_cut_aware_unprojectable_interval_fails_closed(self):
        trust = [
            CueTrust(0, "trusted", "verified"),
            CueTrust(1, "untrusted", "bad"),
            CueTrust(2, "trusted", "verified"),
        ]
        candidates = [
            TimingCandidate(
                1,
                None,
                None,
                "unprojectable",
                "CUT_AWARE",
                projection_reason="source_interval_crosses_confirmed_cut",
            )
        ]
        plan = plan_partial_timeline_repair(self.cues, trust, candidates)
        self.assertEqual(plan["status"], "blocked")
        self.assertEqual(plan["decisions"][1]["action"], "block")
        self.assertEqual(
            plan["decisions"][1]["reason"],
            "source_interval_crosses_confirmed_cut",
        )

    def test_editor_or_asr_timing_cannot_be_candidate_authority(self):
        with self.assertRaises(PartialTimelineRepairError):
            TimingCandidate(
                1,
                2300,
                3200,
                "projected",
                "AFFINE",
                source="editor",
            )

    def test_invalid_mapping_kind_is_rejected(self):
        with self.assertRaises(PartialTimelineRepairError):
            TimingCandidate(1, 2300, 3200, "projected", "BPM_LOCKED")

    def test_untrusted_without_projection_requires_review(self):
        trust = [
            CueTrust(0, "trusted", "verified"),
            CueTrust(1, "untrusted", "bad"),
            CueTrust(2, "trusted", "verified"),
        ]
        plan = plan_partial_timeline_repair(self.cues, trust, [])
        self.assertEqual(plan["decisions"][1]["action"], "review")
        self.assertEqual(
            plan["decisions"][1]["reason"],
            "untrusted_cue_has_no_source_to_mix_candidate",
        )

    def test_duplicate_trust_and_candidate_positions_are_rejected(self):
        with self.assertRaises(PartialTimelineRepairError):
            plan_partial_timeline_repair(
                self.cues,
                [
                    CueTrust(1, "trusted", "a"),
                    CueTrust(1, "untrusted", "b"),
                ],
                [],
            )
        with self.assertRaises(PartialTimelineRepairError):
            plan_partial_timeline_repair(
                self.cues,
                [CueTrust(1, "untrusted", "bad")],
                [
                    TimingCandidate(1, 2300, 3000, "projected", "AFFINE"),
                    TimingCandidate(1, 2400, 3100, "projected", "AFFINE"),
                ],
            )

    def test_adjacent_untrusted_candidates_must_not_overlap_each_other(self):
        cues = [
            Cue(1, 1000, 1800, "第一句"),
            Cue(2, 1900, 2700, "第二句"),
            Cue(3, 2800, 3600, "第三句"),
            Cue(4, 3700, 4500, "第四句"),
        ]
        trust = [
            CueTrust(0, "trusted", "verified"),
            CueTrust(1, "untrusted", "bad"),
            CueTrust(2, "untrusted", "bad"),
            CueTrust(3, "trusted", "verified"),
        ]
        candidates = [
            TimingCandidate(1, 1900, 3000, "projected", "AFFINE"),
            TimingCandidate(2, 2900, 3600, "projected", "AFFINE"),
        ]
        plan = plan_partial_timeline_repair(cues, trust, candidates)
        self.assertEqual(plan["status"], "blocked")
        self.assertEqual(plan["decisions"][1]["action"], "block")
        self.assertEqual(plan["decisions"][2]["action"], "block")
        self.assertEqual(
            plan["decisions"][1]["reason"],
            "candidate_overlaps_another_repair_candidate",
        )


if __name__ == "__main__":
    unittest.main()
