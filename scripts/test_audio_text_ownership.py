import unittest

from lyric_aligner.timeline.audio_text_ownership import (
    AUDIO_TEXT_OWNERSHIP_AUTHORITY,
    AudioTextOwnershipError,
    OwnershipCue,
    OwnershipPolicy,
    TimedCanonicalUnit,
    solve_audio_text_ownership,
)


def unit(unit_id, text, start, end, confidence=0.95):
    return TimedCanonicalUnit(
        unit_id=unit_id,
        text=text,
        start_ms=start,
        end_ms=end,
        confidence=confidence,
    )


def cue(cue_id, start, end):
    return OwnershipCue(cue_id=cue_id, start_ms=start, end_ms=end)


class AudioTextOwnershipTests(unittest.TestCase):
    def test_clear_two_cue_partition_is_lossless_and_ordered(self):
        units = [
            unit("u1", "A", 100, 200),
            unit("u2", "B", 350, 450),
            unit("u3", "C", 1200, 1300),
            unit("u4", "D", 1500, 1600),
        ]
        result = solve_audio_text_ownership(
            units=units,
            cues=[cue("c1", 0, 900), cue("c2", 1000, 1900)],
        )
        self.assertTrue(result.feasible)
        self.assertEqual(
            [assignment.unit_ids for assignment in result.assignments],
            [("u1", "u2"), ("u3", "u4")],
        )
        flattened = [
            item
            for assignment in result.assignments
            for item in assignment.unit_ids
        ]
        self.assertEqual(flattened, ["u1", "u2", "u3", "u4"])
        self.assertTrue(result.automatic_eligible)
        self.assertFalse(result.timing_mutation_performed)
        self.assertEqual(result.authority, AUDIO_TEXT_OWNERSHIP_AUTHORITY)

    def test_audio_timing_can_repartition_wrong_editor_text_without_changing_timing(self):
        units = [
            unit("u1", "回", 100, 180),
            unit("u2", "忆", 260, 340),
            unit("u3", "飞", 1150, 1230),
            unit("u4", "过", 1370, 1450),
            unit("u5", "线", 1580, 1660),
        ]
        result = solve_audio_text_ownership(
            units=units,
            cues=[cue("left", 0, 900), cue("right", 1000, 1800)],
        )
        self.assertEqual(result.assignments[0].unit_ids, ("u1", "u2"))
        self.assertEqual(result.assignments[1].unit_ids, ("u3", "u4", "u5"))
        self.assertTrue(result.automatic_eligible)
        self.assertFalse(result.timing_mutation_performed)

    def test_three_cues_preserve_contiguous_canonical_order(self):
        units = [
            unit("u1", "a", 100, 150),
            unit("u2", "b", 300, 350),
            unit("u3", "c", 800, 850),
            unit("u4", "d", 1000, 1050),
            unit("u5", "e", 1500, 1550),
            unit("u6", "f", 1700, 1750),
        ]
        result = solve_audio_text_ownership(
            units=units,
            cues=[cue("c1", 0, 600), cue("c2", 700, 1200), cue("c3", 1300, 1900)],
        )
        self.assertEqual(
            [assignment.unit_ids for assignment in result.assignments],
            [("u1", "u2"), ("u3", "u4"), ("u5", "u6")],
        )

    def test_ambiguous_partition_margin_blocks_automatic_eligibility(self):
        # u2/u3 sit symmetrically around the gap, making two plausible split points.
        units = [
            unit("u1", "a", 100, 200),
            unit("u2", "b", 900, 1000),
            unit("u3", "c", 1000, 1100),
            unit("u4", "d", 1800, 1900),
        ]
        result = solve_audio_text_ownership(
            units=units,
            cues=[cue("c1", 0, 950), cue("c2", 1050, 2000)],
            policy=OwnershipPolicy(minimum_partition_margin_ms=200.0),
        )
        self.assertTrue(result.feasible)
        self.assertIsNotNone(result.partition_margin_ms)
        self.assertLess(result.partition_margin_ms, 200.0)
        self.assertFalse(result.automatic_eligible)
        self.assertEqual(result.reason, "best_and_second_partition_are_too_close")

    def test_low_unit_confidence_blocks_auto_even_with_clear_geometry(self):
        units = [
            unit("u1", "a", 100, 200),
            unit("u2", "b", 300, 400, confidence=0.40),
            unit("u3", "c", 1200, 1300),
            unit("u4", "d", 1500, 1600),
        ]
        result = solve_audio_text_ownership(
            units=units,
            cues=[cue("c1", 0, 800), cue("c2", 1000, 1800)],
        )
        self.assertFalse(result.automatic_eligible)
        self.assertEqual(result.reason, "unit_timing_confidence_below_policy")

    def test_low_confidence_unit_has_less_partition_leverage(self):
        confident = [
            unit("u1", "a", 100, 200),
            unit("u2", "b", 1550, 1650, confidence=1.0),
            unit("u3", "c", 1700, 1800),
        ]
        uncertain = [
            confident[0],
            unit("u2", "b", 1550, 1650, confidence=0.10),
            confident[2],
        ]
        cues = [cue("c1", 0, 1200), cue("c2", 1300, 2000)]
        high = solve_audio_text_ownership(
            units=confident,
            cues=cues,
            policy=OwnershipPolicy(minimum_unit_confidence=0.0),
        )
        low = solve_audio_text_ownership(
            units=uncertain,
            cues=cues,
            policy=OwnershipPolicy(minimum_unit_confidence=0.0),
        )
        self.assertLessEqual(low.total_timing_cost_ms, high.total_timing_cost_ms)

    def test_overlapping_cues_require_structural_ownership_handler(self):
        with self.assertRaises(AudioTextOwnershipError):
            solve_audio_text_ownership(
                units=[unit("u1", "a", 100, 200), unit("u2", "b", 500, 600)],
                cues=[cue("c1", 0, 550), cue("c2", 500, 900)],
            )

    def test_not_enough_units_for_nonempty_cues_fails_closed(self):
        with self.assertRaises(AudioTextOwnershipError):
            solve_audio_text_ownership(
                units=[unit("u1", "a", 100, 200)],
                cues=[cue("c1", 0, 300), cue("c2", 400, 700)],
            )

    def test_high_mean_distance_blocks_automatic_eligibility(self):
        units = [
            unit("u1", "a", 3000, 3100),
            unit("u2", "b", 3300, 3400),
        ]
        result = solve_audio_text_ownership(
            units=units,
            cues=[cue("c1", 0, 500), cue("c2", 600, 1100)],
            policy=OwnershipPolicy(
                maximum_mean_distance_ms=250.0,
                minimum_partition_margin_ms=0.0,
            ),
        )
        self.assertFalse(result.automatic_eligible)
        self.assertEqual(result.reason, "best_partition_timing_cost_exceeds_policy")

    def test_duplicate_unit_identity_fails_closed(self):
        with self.assertRaises(AudioTextOwnershipError):
            solve_audio_text_ownership(
                units=[unit("dup", "a", 100, 200), unit("dup", "b", 400, 500)],
                cues=[cue("c1", 0, 300), cue("c2", 350, 700)],
            )


if __name__ == "__main__":
    unittest.main()
