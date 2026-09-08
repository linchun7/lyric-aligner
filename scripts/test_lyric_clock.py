import unittest

from lyric_aligner.timeline.lyric_clock import fit_lyric_clock


class LyricClockTests(unittest.TestCase):
    def test_recovers_rate_and_offset_despite_one_wrong_observation(self):
        rows = [(t, round(1.03*t+800)) for t in (10000,30000,60000,90000,120000,150000)]
        rows[2] = (60000, 140000)
        clock = fit_lyric_clock(rows)
        self.assertAlmostEqual(clock.rate, 1.03)
        self.assertAlmostEqual(clock.offset_ms, 800)
        self.assertEqual(clock.map_ms(110000),114100)
        self.assertFalse(clock.is_extrapolation(110000))
        self.assertTrue(clock.is_extrapolation(160000))

    def test_does_not_turn_duplicate_repeat_into_independent_observation(self):
        with self.assertRaisesRegex(ValueError,'duplicate'):
            fit_lyric_clock([(0,10),(20000,20010),(20000,20010),(40000,40010)])

    def test_rejects_unusable_coordinates_and_backward_clock(self):
        for rows in ([(0,0)]*3, [(True,0),(10000,10000),(20000,20000),(40000,40000)],
                     [(0,40000),(10000,30000),(20000,20000),(40000,0)],
                     [(0,0),(1,1),(2,2),(3,3)]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                fit_lyric_clock(rows)

    def test_negative_mapped_timestamp_is_not_clamped(self):
        clock = fit_lyric_clock([(10000,9000),(30000,29000),(50000,49000),(70000,69000)])
        with self.assertRaises(ValueError):
            clock.map_ms(0)


if __name__ == '__main__':
    unittest.main()
