import importlib.util
import tempfile
import unittest
from pathlib import Path

from lyric_aligner.audio.float_decode import decode_float_audio


@unittest.skipUnless(all(importlib.util.find_spec(x) for x in ('av', 'numpy', 'soundfile')), 'optional audio dependencies')
class FloatDecodeTests(unittest.TestCase):
    def decode(self, samples, rate=16000):
        import soundfile as sf
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.wav'
            sf.write(path, samples, rate, subtype='FLOAT')
            return decode_float_audio(path)

    def test_overrange_preserves_ratios_and_clock(self):
        import numpy as np
        samples = np.tile(np.array([0., .5, 1., 2., -.5, -2.], dtype=np.float32), 100)
        audio, info = self.decode(samples)
        np.testing.assert_array_equal(audio, samples / 2)
        self.assertEqual(info['sample_count'], len(samples))
        self.assertEqual(info['samples_outside_unit_range_before_gain'], 200)
        self.assertEqual(info['applied_gain'], .5)

    def test_in_range_and_silence_are_unchanged(self):
        import numpy as np
        for samples in (np.zeros(1600, dtype=np.float32), np.linspace(-.8, .8, 1600, dtype=np.float32)):
            audio, info = self.decode(samples)
            np.testing.assert_array_equal(audio, samples)
            self.assertEqual(info['applied_gain'], 1.)

    def test_resampler_flush_keeps_full_second(self):
        import numpy as np
        audio, info = self.decode(np.zeros(44100, dtype=np.float32), 44100)
        self.assertEqual(len(audio), 16000)

    def test_nonfinite_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'finite'):
            self.decode([0., float('nan'), 0.])


if __name__ == '__main__':
    unittest.main()
