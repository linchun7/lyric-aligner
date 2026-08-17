import unittest

from lyric_aligner.audio.transition import probe_adjacent_transition, transition_search_interval


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

    def test_simultaneous_strong_evidence_creates_review_only_overlap_candidate(self):
        left = payload("occ-a", [window(94, 100), window(97, 103)])
        right = payload("occ-b", [window(99, 105), window(102, 108)])
        result = probe_adjacent_transition(left, right)
        self.assertTrue(result["blocked"])
        self.assertEqual(result["status"], "review_required")
        self.assertTrue(result["overlap_candidates"])
        candidate = result["overlap_candidates"][0]
        self.assertEqual(candidate["status"], "review")
        self.assertEqual(candidate["occurrences"], ["occ-a", "occ-b"])

    def test_sequential_activity_does_not_invent_overlap(self):
        left = payload("occ-a", [window(90, 96), window(93, 99)])
        right = payload("occ-b", [window(100, 106), window(103, 109)])
        result = probe_adjacent_transition(left, right)
        self.assertFalse(result["blocked"])
        self.assertEqual(result["overlap_candidates"], [])

    def test_ambiguous_repeated_source_peak_blocks_without_claiming_overlap(self):
        left = payload("occ-a", [window(97, 103, ambiguous=True, margin=0.005)])
        right = payload("occ-b", [window(99, 105)])
        result = probe_adjacent_transition(left, right)
        self.assertTrue(result["blocked"])
        self.assertEqual(result["overlap_candidates"], [])
        self.assertTrue(result["uncertain_intervals"])

    def test_weak_second_track_does_not_create_overlap(self):
        left = payload("occ-a", [window(97, 103)])
        right = payload("occ-b", [window(99, 105, score=0.55)])
        result = probe_adjacent_transition(left, right)
        self.assertFalse(result["blocked"])
        self.assertEqual(result["overlap_candidates"], [])


if __name__ == "__main__":
    unittest.main()
