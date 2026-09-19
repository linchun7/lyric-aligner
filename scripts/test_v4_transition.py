import unittest

from lyric_aligner.audio.transition import (
    probe_adjacent_transition,
    transition_candidate_id,
    transition_search_interval,
)


def window(start, end, score=0.9, margin=0.08, agreement=2, ambiguous=False):
    return {
        "mix_start": start,
        "mix_end": end,
        "top1": {
            "fused_score": score,
            "feature_agreement": agreement,
        },
        "margin": margin,
        "ambiguous": ambiguous,
    }


def payload(occurrence_id, windows):
    return {"occurrence_id": occurrence_id, "result": {"windows": windows}}


class V4TransitionTests(unittest.TestCase):
    def test_transition_search_margin_is_not_hard_track_end(self):
        self.assertEqual(
            transition_search_interval(100.0, mix_duration=200.0, margin_seconds=10.0),
            (90.0, 110.0),
        )

    def test_simultaneous_strong_evidence_creates_candidate_with_stable_identity(self):
        left = payload("occ-a", [window(94, 100), window(97, 103)])
        right = payload("occ-b", [window(99, 105), window(102, 108)])
        result = probe_adjacent_transition(left, right)
        self.assertTrue(result["blocked"])
        self.assertEqual(result["status"], "review_required")
        self.assertTrue(result["overlap_candidates"])
        candidate = result["overlap_candidates"][0]
        self.assertEqual(candidate["status"], "review")
        self.assertEqual(candidate["occurrences"], ["occ-a", "occ-b"])
        self.assertEqual(
            candidate["candidate_id"],
            transition_candidate_id(
                "cross_track_overlap_candidate",
                "occ-a",
                "occ-b",
                candidate["start"],
                candidate["end"],
            ),
        )

    def test_candidate_id_is_stable_to_sub_millisecond_float_noise(self):
        left = transition_candidate_id(
            "cross_track_overlap_candidate", "occ-a", "occ-b", 9.0001, 11.0001
        )
        right = transition_candidate_id(
            "cross_track_overlap_candidate", "occ-a", "occ-b", 9.0004, 11.0004
        )
        self.assertEqual(left, right)

    def test_sequential_activity_does_not_invent_overlap(self):
        left = payload("occ-a", [window(90, 96), window(93, 99)])
        right = payload("occ-b", [window(100, 106), window(103, 109)])
        result = probe_adjacent_transition(left, right)
        self.assertFalse(result["blocked"])
        self.assertEqual(result["overlap_candidates"], [])

    def test_ambiguous_repeated_source_peak_blocks_with_candidate_identity(self):
        left = payload("occ-a", [window(97, 103, ambiguous=True, margin=0.005)])
        right = payload("occ-b", [window(99, 105)])
        result = probe_adjacent_transition(left, right)
        self.assertTrue(result["blocked"])
        self.assertEqual(result["overlap_candidates"], [])
        self.assertTrue(result["uncertain_intervals"])
        candidate = result["uncertain_intervals"][0]
        self.assertTrue(candidate["candidate_id"])
        self.assertEqual(
            candidate["candidate_id"],
            transition_candidate_id(
                "transition_ambiguity",
                "occ-a",
                "occ-b",
                candidate["start"],
                candidate["end"],
            ),
        )

    def test_weak_second_track_does_not_create_overlap(self):
        left = payload("occ-a", [window(97, 103)])
        right = payload("occ-b", [window(99, 105, score=0.55)])
        result = probe_adjacent_transition(left, right)
        self.assertFalse(result["blocked"])
        self.assertEqual(result["overlap_candidates"], [])


if __name__ == "__main__":
    unittest.main()
