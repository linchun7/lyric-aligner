import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from lyric_aligner.audio.waveform_alignment import refine_waveform_window
from lyric_aligner.audio.fine_alignment import refine_coarse_mapping


class WaveformAlignmentTests(unittest.TestCase):
    sr = 8000

    def setUp(self):
        self.rng = np.random.default_rng(710)
        self.source = self.rng.normal(0, .1, 20*self.sr)

    def align(self, mix, **kwargs):
        options = dict(sr=self.sr, mix_start=0, mix_end=6, source_center=8.2, slope=1, radius=1.0)
        options.update(kwargs)
        return refine_waveform_window(mix, self.source, **options)

    def test_known_crop_gain_polarity_and_noise_have_submillisecond_mapping(self):
        mix = -.7*self.source[5*self.sr:11*self.sr] + .01*self.rng.normal(size=6*self.sr) + .2
        result = self.align(mix)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result['source_center'], 8, delta=.001)
        self.assertAlmostEqual(result['slope'], 1, delta=.0001)

    def test_absolute_buffer_coordinates_preserve_mapping(self):
        mix = self.source[5*self.sr:11*self.sr]
        result = self.align(mix, mix_start=3000, mix_end=3006, mix_audio_start=3000)
        self.assertAlmostEqual(result['source_center'], 8, delta=.001)

    def test_silence_unrelated_audio_and_changed_speed_abstain(self):
        for mix in [np.zeros(6*self.sr), self.rng.normal(size=6*self.sr)]:
            self.assertIsNone(self.align(mix))
        self.assertIsNone(self.align(self.source[:6*self.sr], slope=1.2))

    def test_edit_between_patches_does_not_get_smoothed_over(self):
        mix = np.r_[self.source[5*self.sr:8*self.sr], self.source[round(8.08*self.sr):round(11.08*self.sr)]]
        self.assertIsNone(self.align(mix))

    def test_periodic_ambiguity_is_rejected(self):
        block = self.rng.normal(0, .1, self.sr//2)
        self.source = np.tile(block, 40)
        self.assertIsNone(self.align(self.source[5*self.sr:11*self.sr]))

    def test_support_does_not_certify_unsampled_gaps(self):
        mix = self.source[5*self.sr:11*self.sr].copy()
        mix[round(1.9*self.sr):round(2.3*self.sr)] = self.source[:round(.4*self.sr)]
        result = self.align(mix)
        self.assertIsNotNone(result)
        self.assertTrue(all(p['mix_end'] <= 1.9 or p['mix_start'] >= 2.3 for p in result['support_intervals']))

    def test_nonfinite_and_short_inputs_do_not_produce_evidence(self):
        mix = self.source[5*self.sr:11*self.sr].copy()
        mix[:] = np.nan
        self.assertIsNone(self.align(mix))
        self.assertIsNone(self.align(mix[:self.sr]))
        with self.assertRaises(ValueError):
            self.align(mix, source_center=float('inf'))

    def test_experimental_registration_cannot_relabel_old_feature_evidence(self):
        self.source = self.rng.normal(0, .1, 30*self.sr)
        mix = self.source[5*self.sr:25*self.sr]
        centers = [3., 9., 15.]
        windows = [dict(mix_start=c-2,mix_end=c+2,mix_center=c) for c in centers]
        points = [dict(mix_center=c,source_center=c+5+offset,estimated_slope=1.,fused_score=.9)
                  for c,offset in zip(centers,[.35,-.35,.35])]
        def run(enabled):
            candidates = [SimpleNamespace(source_center=p['source_center'],estimated_slope=1.,
                fused_score=.9,chroma_score=.9,mfcc_score=.9,feature_agreement=2) for p in points]
            retrievals = [SimpleNamespace(top1=c,margin=.001,ambiguous=True) for c in candidates]
            with patch('lyric_aligner.audio.fine_alignment.extract_harmonic_features', return_value=SimpleNamespace(duration_seconds=30)), \
                 patch('lyric_aligner.audio.fine_alignment.retrieve_coarse_window', side_effect=retrievals):
                return refine_coarse_mapping(mix,self.source,dict(result=dict(windows=windows,path=points)),
                                             sr=self.sr,force=True,waveform_refinement=enabled)
        old,new = run(False),run(True)
        self.assertTrue(old['timewarp']['blocked'])
        self.assertEqual(new['timewarp'],old['timewarp'],'unvalidated registration must not clear feature-only review')
        self.assertEqual(new['path'],old['path'],'feature scores must stay bound to their measured coordinates')
        self.assertEqual(len(new['waveform_candidates']),3)
        self.assertEqual(old['waveform_candidates'],[])


if __name__ == '__main__':
    unittest.main()
