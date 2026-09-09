import copy
import itertools
import unittest

from lyric_aligner.evaluation.interval_bottleneck import evaluate_interval_bottleneck


class IntervalBottleneckTests(unittest.TestCase):
    def test_independent_best_edges_are_not_a_real_candidate(self):
        rows=[dict(id='a',editor=[100,500],candidates={'start':[200,600],'end':[50,400]})]
        report=evaluate_interval_bottleneck(rows,{'a':[200,400]})
        self.assertEqual(report['pointwise_oracle_mae_ms'],0)
        self.assertEqual(report['legal_interval_oracle_mae_ms'],75)
        self.assertEqual(report['legal_mixed_edge_oracle_mae_ms'],0)

    def test_unannotated_neighbor_cannot_move_to_enable_oracle(self):
        rows=[dict(id='a',editor=[100,200],candidates={'new':[100,400]}),
              dict(id='b',editor=[300,500],candidates={'new':[400,600]})]
        report=evaluate_interval_bottleneck(rows,{'a':[100,400]})
        self.assertEqual(report['pointwise_oracle_mae_ms'],0)
        self.assertEqual(report['legal_interval_oracle_mae_ms'],100)
        self.assertEqual(report['legal_mixed_edge_oracle_mae_ms'],100)
        self.assertEqual(report['legal_oracle_changed_cues'],0)

    def test_dynamic_program_can_choose_joint_changes(self):
        rows=[dict(id='a',editor=[100,200],candidates={'new':[100,400]}),
              dict(id='b',editor=[300,500],candidates={'new':[400,600]})]
        report=evaluate_interval_bottleneck(rows,{'a':[100,400],'b':[400,600]})
        self.assertEqual(report['legal_interval_oracle_mae_ms'],0)
        self.assertEqual(report['legal_oracle_changed_cues'],2)

    def test_missing_and_invalid_candidates_stay_in_denominator(self):
        rows=[dict(id='a',editor=[100,200],candidates={'new':[100,200]}),
              dict(id='b',editor=[300,500],candidates={'new':[500,400]}),
              dict(id='c',editor=[600,700],candidates={'new':None})]
        report=evaluate_interval_bottleneck(rows,{r['id']:r['editor'] for r in rows})
        self.assertEqual(report['candidate_coverage']['new']['fraction'],1/3)
        self.assertEqual(len(report['invalid_candidates']),1)

    def test_missing_ownership_and_no_gold_never_claim_complete_accuracy(self):
        rows=[dict(id='a',editor=[100,200])]
        report=evaluate_interval_bottleneck(rows,{'missing':[300,400]})
        self.assertEqual(report['status'],'incomplete_ownership')
        self.assertEqual(report['unrepresented_gold_ids'],['missing'])
        self.assertIsNone(report['legal_interval_oracle_mae_ms'])
        self.assertEqual(evaluate_interval_bottleneck(rows,{})['status'],'no_gold')

    def test_ties_preserve_editor_and_inputs(self):
        rows=[dict(id='a',editor=[100,200],candidates={'new':[120,220]})]
        original=copy.deepcopy(rows)
        report=evaluate_interval_bottleneck(rows,{'a':[110,210]})
        self.assertEqual(report['legal_oracle_changed_cues'],0)
        self.assertEqual(rows,original)

    def test_sequence_oracle_matches_exhaustive_paths(self):
        rows=[dict(id='a',editor=[100,200],candidates={'x':[100,350],'y':[80,170]}),
              dict(id='b',editor=[250,400],candidates={'x':[350,440],'y':[180,390]}),
              dict(id='c',editor=[450,550],candidates={'x':[420,510],'y':[390,500]})]
        truth={'a':[80,300],'b':[300,430],'c':[430,510]}
        best=None
        for path in itertools.product(*[[r['editor'],*r['candidates'].values()] for r in rows]):
            if any(a[1]>b[0] for a,b in zip(path,path[1:])):
                continue
            error=sum(abs(a-b) for r,iv in zip(rows,path) for a,b in zip(iv,truth[r['id']]))
            cost=(error,sum(iv!=r['editor'] for iv,r in zip(path,rows)))
            best=cost if best is None else min(best,cost)
        report=evaluate_interval_bottleneck(rows,truth)
        self.assertEqual((report['legal_interval_oracle_mae_ms']*6,report['legal_oracle_changed_cues']),best)

    def test_invalid_editor_sequence_is_not_silently_sorted_or_clipped(self):
        for rows in ([],[dict(id='a',editor=[True,200])],
            [dict(id='a',editor=[100,300]),dict(id='b',editor=[200,400])],
            [dict(id='a',editor=[100,200]),dict(id='a',editor=[300,400])]):
            with self.subTest(rows=rows),self.assertRaises(ValueError):
                evaluate_interval_bottleneck(rows,{})
