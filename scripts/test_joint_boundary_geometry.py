import itertools
import random
import unittest

from lyric_aligner.timeline.joint_boundary_geometry import select_compatible_edits


class JointBoundaryGeometryTests(unittest.TestCase):
    def test_nested_prior_cue_cannot_gain_nonadjacent_overlap(self):
        rows = [dict(start_ms=0,end_ms=3000), dict(start_ms=1000,end_ms=1500), dict(start_ms=4000,end_ms=5000)]
        edits = [dict(id='third',row_index=2,boundary_kind='start',selected_ms=2500)]
        self.assertEqual(select_compatible_edits(rows,edits), set())

    def test_mutually_enabling_neighbors_are_applied_together(self):
        rows = [{'start_ms': 0, 'end_ms': 100}, {'start_ms': 100, 'end_ms': 200}]
        edits = [dict(id='end', row_index=0, boundary_kind='end', selected_ms=130),
                 dict(id='start', row_index=1, boundary_kind='start', selected_ms=130)]
        self.assertEqual(select_compatible_edits(rows, edits), {'end', 'start'})
        self.assertEqual(select_compatible_edits(rows, edits[::-1]), {'end', 'start'})

    def test_safe_edge_survives_other_edge_conflict(self):
        rows = [{'start_ms': 0, 'end_ms': 100}, {'start_ms': 100, 'end_ms': 200}]
        edits = [dict(id='start', row_index=1, boundary_kind='start', selected_ms=50),
                 dict(id='end', row_index=1, boundary_kind='end', selected_ms=180)]
        self.assertEqual(select_compatible_edits(rows, edits), {'end'})

    def test_matches_exhaustive_solver_on_small_random_timelines(self):
        rng = random.Random(718)
        for _ in range(100):
            rows = [dict(start_ms=i*100+100, end_ms=i*100+100+rng.randint(90,310)) for i in range(3)]
            edits = [dict(id=f'{i}:{kind}', row_index=i, boundary_kind=kind,
                          selected_ms=rows[i][kind+'_ms']+rng.randint(-80, 80))
                     for i in range(3) for kind in ('start', 'end')]
            possible = []
            for bits in itertools.product((0, 1), repeat=len(edits)):
                after = [dict(r) for r in rows]
                accepted = []
                for bit, edit in zip(bits, edits):
                    if bit and edit['selected_ms'] != rows[edit['row_index']][edit['boundary_kind']+'_ms']:
                        after[edit['row_index']][edit['boundary_kind']+'_ms'] = edit['selected_ms']
                        accepted.append(edit)
                if any(r['end_ms'] <= r['start_ms'] for r in after):
                    continue
                if any(after[i]['start_ms'] > after[j]['start_ms'] or
                       max(0, after[i]['end_ms']-after[j]['start_ms']) >
                       max(0, rows[i]['end_ms']-rows[j]['start_ms']) for i in range(3) for j in range(i+1,3)):
                    continue
                possible.append((len(accepted), sum(abs(e['selected_ms']-rows[e['row_index']][e['boundary_kind']+'_ms']) for e in accepted)))
            actual = select_compatible_edits(rows, edits)
            score = (len(actual), sum(abs(e['selected_ms']-rows[e['row_index']][e['boundary_kind']+'_ms']) for e in edits if e['id'] in actual))
            self.assertEqual(score, max(possible))

    def test_invalid_proposal_is_rejected(self):
        rows = [dict(start_ms=0, end_ms=100)]
        for edit in [dict(id='x', row_index=True, boundary_kind='start', selected_ms=10),
                     dict(id='x', row_index=0, boundary_kind='end', selected_ms=-1)]:
            with self.assertRaises(ValueError):
                select_compatible_edits(rows, [edit])


if __name__ == '__main__':
    unittest.main()
