import unittest

from lyric_aligner.audio.timewarp import AlignmentAnchor, select_timewarp


def anchors(points, *, features=True):
    scores = {"waveform": 0.9, "chroma": 0.9} if features else {}
    return [
        AlignmentAnchor(float(mix), float(source), feature_scores=dict(scores))
        for mix, source in points
    ]


class V4TimeWarpTests(unittest.TestCase):
    def test_fixed_rate_stays_on_affine_fast_path(self):
        rows = anchors([(x, 5.0 + 1.12 * x) for x in range(0, 31, 3)])
        result = select_timewarp(rows, bpm_prior=1.10)
        self.assertEqual(result["selection"], "AFFINE_ACCEPTED")
        self.assertFalse(result["escalated"])
        self.assertAlmostEqual(result["mapping"]["base_slope"], 1.12, delta=0.02)

    def test_soft_bpm_prior_does_not_lock_wrong_slope(self):
        rows = anchors([(x, 2.0 + 1.30 * x) for x in range(0, 31, 3)])
        result = select_timewarp(rows, bpm_prior=1.05)
        self.assertAlmostEqual(result["mapping"]["base_slope"], 1.30, delta=0.04)
        self.assertGreater(result["mapping"]["diagnostics"]["bpm_prior_delta"], 0.15)

    def test_three_local_rates_upgrade_to_continuous_piecewise_without_cut(self):
        points = []
        source = 0.0
        previous = 0.0
        for mix in range(0, 31, 2):
            if mix == 0:
                source = 0.0
            else:
                midpoint = (previous + mix) / 2
                rate = 1.08 if midpoint < 10 else (1.17 if midpoint < 20 else 1.43)
                source += (mix - previous) * rate
            points.append((mix, source))
            previous = mix
        result = select_timewarp(anchors(points), min_piecewise_improvement=0.15)
        self.assertEqual(result["selection"], "PIECEWISE_RATE_ACCEPTED")
        self.assertTrue(result["escalated"])
        self.assertEqual(result["discontinuities"], [])
        mapping = result["mapping"]
        self.assertGreaterEqual(len(mapping["breakpoints"]), 1)
        self.assertEqual(len(mapping["breakpoints"]), len(mapping["slope_deltas"]))
        segment_slopes = [mapping["base_slope"]]
        for delta in mapping["slope_deltas"]:
            segment_slopes.append(segment_slopes[-1] + delta)
        self.assertGreaterEqual(len(segment_slopes), 2)
        self.assertTrue(all(slope > 0 for slope in segment_slopes))

    def test_abrupt_rate_change_is_not_cut(self):
        points = [
            (0, 0),
            (2, 2.16),
            (4, 4.32),
            (6, 6.48),
            (8, 9.34),
            (10, 12.20),
            (12, 15.06),
            (14, 17.92),
        ]
        result = select_timewarp(anchors(points), min_piecewise_improvement=0.10)
        self.assertEqual(result["discontinuities"], [])
        self.assertNotIn("DISCONTINUITY", result["selection"])

    def test_undeclared_middle_jump_blocks_instead_of_becoming_rate_change(self):
        rows = anchors(
            [(0, 0), (3, 3.3), (6, 6.6), (9, 17.9), (12, 21.2), (15, 24.5)]
        )
        result = select_timewarp(rows, middle_cut="false")
        self.assertEqual(result["selection"], "AFFINE_WITH_DISCONTINUITY_REVIEW")
        self.assertTrue(result["blocked"])
        self.assertEqual(
            result["discontinuities"][0]["type"],
            "unexpected_middle_discontinuity",
        )

    def test_declared_middle_cut_only_creates_review_candidate_never_confirms(self):
        rows = anchors(
            [(0, 0), (3, 3.3), (6, 6.6), (9, 17.9), (12, 21.2), (15, 24.5)]
        )
        result = select_timewarp(rows, middle_cut="true")
        self.assertTrue(result["blocked"])
        self.assertEqual(result["discontinuities"][0]["status"], "review")
        self.assertEqual(
            result["discontinuities"][0]["type"],
            "declared_middle_cut_candidate",
        )

    def test_leading_retrieval_outliers_do_not_create_false_drift_block(self):
        points = []
        for index in range(46):
            mix = float(index * 3)
            source = 7.0 + 1.10 * mix
            if index == 0:
                source += 5.0
            elif index == 1:
                source += 2.5
            points.append((mix, source))

        result = select_timewarp(anchors(points))

        self.assertEqual(result["selection"], "AFFINE_ACCEPTED")
        diagnostics = result["mapping"]["diagnostics"]
        self.assertEqual(diagnostics["inlier_count"], 44)
        self.assertLess(diagnostics["drift_span"], 0.30)

    def test_piecewise_requires_independent_feature_support(self):
        points = [
            (0, 0),
            (3, 3.0),
            (6, 6.0),
            (9, 9.0),
            (12, 15.0),
            (15, 21.0),
            (18, 27.0),
            (21, 33.0),
        ]
        result = select_timewarp(
            anchors(points, features=False),
            min_piecewise_improvement=0.05,
            minimum_feature_families=2,
        )
        self.assertNotEqual(result["selection"], "PIECEWISE_RATE_ACCEPTED")
        self.assertTrue(result["blocked"])


if __name__ == "__main__":
    unittest.main()
