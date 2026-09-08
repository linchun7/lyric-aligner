import copy
import itertools
import random
import unittest
from unittest.mock import patch

from lyric_aligner.alignment.source_packets import BOUNDED_TARGET_POLICY, build_source_packet_candidates
from lyric_aligner.alignment.source_sequence import mandatory_chain_vertices, resolve_source_sequence as _resolve


def resolve_source_sequence(items):
    return _resolve([{**p, 'source_observation_sha256': 'c' * 64} for p in items], source_observation_sha256='c' * 64)

def oracle(vertices):
    paths = []
    for size in range(len(vertices) + 1):
        for subset in itertools.combinations(range(len(vertices)), size):
            ordered = sorted(subset, key=lambda i: vertices[i])
            if all(vertices[a][1] <= vertices[b][0] and vertices[a][3] <= vertices[b][2]
                   and vertices[a][5] <= vertices[b][4] for a, b in zip(ordered, ordered[1:])):
                paths.append(set(subset))
    longest = max(map(len, paths))
    return set.intersection(*(p for p in paths if len(p) == longest)), longest


def packets(observed_text=None):
    text = ['alpha beta', 'chorus repeat', 'chorus repeat', 'chorus repeat',
            'chorus repeat', 'chorus repeat', 'chorus repeat', 'omega end']
    canonical = [{'canonical_line_index': i, 'text': t} for i, t in enumerate(text)]
    words = [{'text': word, 'start_ms': i * 200, 'end_ms': (i + 1) * 200}
             for i, word in enumerate(' '.join(observed_text or text).split())]
    return [build_source_packet_candidates(canonical_lines=canonical, observed_words=words,
        target_cue={'cue_id': str(i), 'canonical_character_ranges': [
            {'canonical_line_index': i, 'start_char': 0, 'end_char': len(t)}]},
        source_audio_sha256='a' * 64, lyric_version='b' * 64,
        source_search_domain={'start_ms': 0, 'end_ms': 10000}, model_id='fixture',
        context_radius=1, n_best=100, target_alignment_policy=BOUNDED_TARGET_POLICY)
        for i, t in enumerate(text)]


class SourceSequenceTests(unittest.TestCase):
    def test_all_optimal_matches_exhaustive_oracle(self):
        rng = random.Random(873)
        for _ in range(150):
            vertices = []
            for _ in range(rng.randrange(1, 9)):
                c, s, t = (rng.randrange(6) for _ in range(3))
                vertices.append(((c, 0), (c, 1), s, s + 1, t, t + 1))
            self.assertEqual(mandatory_chain_vertices(vertices), oracle(vertices))

    def test_duplicate_recovered_without_mutating_local_outputs(self):
        original = packets()
        before = copy.deepcopy(original)
        result = resolve_source_sequence(original)
        self.assertEqual(original, before)
        self.assertGreater(len(result['promotions']), 0)
        for promotion in result['promotions']:
            p = next(p for p in original if p['cache_key_sha256'] == promotion['packet_cache_key_sha256'])
            self.assertIsNone(p['selected_candidate_id'])
            self.assertEqual(p['selection_reason'], 'ambiguous_canonical_packet_identity')

    def test_order_independent_and_duplicate_queries_do_not_add_evidence(self):
        original = packets()
        def chosen(items):
            result = resolve_source_sequence(items)
            return {p['packet_cache_key_sha256'] for p in result['promotions']}
        self.assertEqual(resolve_source_sequence(original), resolve_source_sequence(list(reversed(original))))
        self.assertEqual(chosen(original), chosen(list(reversed(original))))
        self.assertEqual(chosen(original), chosen(original + copy.deepcopy(original)))

    def test_missing_repeated_occurrence_remains_ambiguous(self):
        original = packets(['alpha beta'] + ['chorus repeat'] * 5 + ['omega end'])
        self.assertEqual(resolve_source_sequence(original)['promotions'], [])

    def test_incomplete_and_resource_fail_closed(self):
        original = packets()
        original[0]['candidates_truncated'] = True
        self.assertEqual(resolve_source_sequence(original)['status'], 'incomplete_lattice')
        with patch('lyric_aligner.alignment.source_sequence.MAX_VERTICES', 1):
            self.assertEqual(resolve_source_sequence(packets())['status'], 'source_sequence_resource_limit')

    def test_mixed_source_rejected(self):
        original = packets()
        original[0]['cache_identity']['model_id'] = 'another-observation'
        self.assertEqual(resolve_source_sequence(original)['status'], 'mixed_source_identity')

    def test_equivalent_candidate_copy_does_not_remove_promotion(self):
        original = packets()
        previous = resolve_source_sequence(original)
        key = previous['promotions'][0]['packet_cache_key_sha256']
        packet = next(p for p in original if p['cache_key_sha256'] == key)
        packet['candidates'].append(copy.deepcopy(packet['candidates'][0]))
        packet['coverage']['candidate_count_before_truncation'] += 1
        self.assertEqual(previous['promotions'], resolve_source_sequence(original)['promotions'])

    def test_legacy_policy_rejected(self):
        original = packets()
        original[0]['policy_id'] = 'old-exact'
        self.assertEqual(resolve_source_sequence(original)['status'], 'unsupported_packet_policy')

    def test_nonfinite_anchor_rejected(self):
        for value in (True, float('nan'), float('inf')):
            original = packets()
            anchor = next(p for p in original if p['selected_candidate_id'])
            candidate = next(c for c in anchor['candidates'] if c['candidate_id'] == anchor['selected_candidate_id'])
            candidate['source_interval_ms'][0] = value
            self.assertEqual(resolve_source_sequence(original)['status'], 'invalid_existing_anchor')

    def test_conflicting_existing_lock_prevents_promotion(self):
        original = packets()
        # A previously selected first cue with a later source location must
        # constrain new decisions, even if the best global chain discards it.
        anchor = next(p for p in original if p['selected_candidate_id'])
        candidate = next(c for c in anchor['candidates'] if c['candidate_id'] == anchor['selected_candidate_id'])
        candidate['source_interval_ms'] = [9000, 9500]
        self.assertEqual(resolve_source_sequence(original)['promotions'], [])


if __name__ == '__main__':
    unittest.main()
