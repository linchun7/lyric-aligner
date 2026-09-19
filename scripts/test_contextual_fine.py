import unittest
import numpy as np
from lyric_aligner.audio.independent_fine import OnsetFeatureBundle, retrieve_independent_onset_window
from lyric_aligner.audio.contextual_fine import retrieve_contextual_onset_window


def bundle(values):
    return OnsetFeatureBundle(sr=1000,hop_length=100,duration_seconds=values.shape[1]/10,
                              onset=values[:1],multiband_flux=values)


class ContextualFineTests(unittest.TestCase):
    def test_repeated_local_pattern_is_resolved_by_surrounding_audio(self):
        rng=np.random.default_rng(714)
        mix=rng.random((8,60)).astype(np.float32)
        source=rng.random((8,180)).astype(np.float32)*.3
        source[:,20:80]=mix
        source[:,110:130]=mix[:,20:40]
        args=dict(mix_start=2.,mix_end=4.,slopes=[1.],source_search_start=0.,source_search_end=18.,candidate_step_seconds=.1)
        old=retrieve_independent_onset_window(bundle(mix),bundle(source),**args)
        self.assertTrue(old.ambiguous)
        result=retrieve_contextual_onset_window(bundle(mix),bundle(source),**args)
        self.assertAlmostEqual(result['top1']['source_start'],4.)
        self.assertFalse(result['ambiguous'])
        self.assertFalse(result['automatic_mutation_allowed'])

    def test_repeated_context_remains_ambiguous(self):
        rng=np.random.default_rng(815);mix=rng.random((8,60)).astype(np.float32)
        source=np.zeros((8,180),np.float32);source[:,10:70]=mix;source[:,100:160]=mix
        r=retrieve_contextual_onset_window(bundle(mix),bundle(source),mix_start=2.,mix_end=4.,slopes=[1.],
            source_search_start=0.,source_search_end=18.,candidate_step_seconds=.1)
        self.assertTrue(r['ambiguous'])

    def test_context_corrects_a_clean_local_decoy(self):
        rng=np.random.default_rng(717);mix=rng.random((8,60)).astype(np.float32)
        source=rng.random((8,180)).astype(np.float32)*.3
        source[:,20:80]=mix
        source[:,40:60]=np.maximum(0,mix[:,20:40]+rng.normal(0,.25,(8,20)))
        source[:,110:130]=mix[:,20:40]
        args=dict(mix_start=2.,mix_end=4.,slopes=[1.],source_search_start=0.,source_search_end=18.,candidate_step_seconds=.1)
        old=retrieve_independent_onset_window(bundle(mix),bundle(source),**args)
        self.assertAlmostEqual(old.top1.source_start,11.)
        result=retrieve_contextual_onset_window(bundle(mix),bundle(source),**args)
        self.assertAlmostEqual(result['top1']['source_start'],4.)

    def test_no_context_does_not_claim_contextual_evidence(self):
        rng=np.random.default_rng(916);mix=rng.random((8,40)).astype(np.float32)
        source=np.zeros((8,100),np.float32);source[:,20:60]=mix
        r=retrieve_contextual_onset_window(bundle(mix),bundle(source),mix_start=0.,mix_end=4.,slopes=[1.],
            source_search_start=0.,source_search_end=10.)
        self.assertFalse(r['context_available']);self.assertFalse(r['automatic_mutation_allowed'])

    def test_source_without_flanks_returns_unavailable_context(self):
        rng=np.random.default_rng(11);mix=rng.random((8,80)).astype(np.float32)
        r=retrieve_contextual_onset_window(bundle(mix),bundle(mix[:,20:60]),mix_start=2.,mix_end=6.,
            slopes=[1.],source_search_start=0.,source_search_end=4.)
        self.assertFalse(r['context_available']);self.assertTrue(r['ambiguous'])

    def test_coarse_grid_phase_cannot_disambiguate_exact_repetitions(self):
        rng=np.random.default_rng(33);mix=rng.random((8,60)).astype(np.float32)
        source=np.zeros((8,190),np.float32);source[:,10:70]=mix;source[:,103:163]=mix
        r=retrieve_contextual_onset_window(bundle(mix),bundle(source),mix_start=2.,mix_end=4.,slopes=[1.],
            source_search_start=0.,source_search_end=19.,candidate_step_seconds=.4)
        self.assertTrue(r['ambiguous'])

    def test_nonfinite_slopes_cannot_change_a_valid_candidate(self):
        v=np.random.default_rng(2).random((8,100)).astype(np.float32)
        for slopes in ([1.,float('nan')],[1.,float('inf')],[1.,-1.]):
            with self.subTest(slopes=slopes),self.assertRaises(ValueError):
                retrieve_contextual_onset_window(bundle(v),bundle(v),mix_start=3.,mix_end=6.,slopes=slopes,
                    source_search_start=0.,source_search_end=10.)

    def test_nonfinite_features_cannot_produce_a_confident_result(self):
        v=np.random.default_rng(2).random((8,100)).astype(np.float32);v[0,50]=np.nan
        with self.assertRaises(ValueError):
            retrieve_contextual_onset_window(bundle(v),bundle(v),mix_start=3.,mix_end=6.,slopes=[1.],
                source_search_start=0.,source_search_end=10.)


if __name__=='__main__':unittest.main()
