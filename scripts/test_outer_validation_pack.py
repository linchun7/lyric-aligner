import unittest

from scripts.v4_build_outer_validation_pack import select_recordings


class OuterValidationPackTests(unittest.TestCase):
    def pool(self):
        return [{"id":f"{t}:{c}", "recording_group":str(t), "text":"lyric", "start_ms":c*1000, "end_ms":c*1000+500} for t in range(16) for c in range(8)]

    def test_tracks_are_disjoint_and_counts_are_exact(self):
        rows = select_recordings(self.pool())
        self.assertEqual(len(rows),60)
        partitions = {p:{r['recording_group'] for r in rows if r['partition']==p} for p in ('calibration','holdout')}
        self.assertEqual(len(partitions['calibration']),8)
        self.assertEqual(len(partitions['holdout']),4)
        self.assertFalse(partitions['calibration'] & partitions['holdout'])

    def test_determinism_is_independent_of_input_order(self):
        self.assertEqual(select_recordings(self.pool()),select_recordings(list(reversed(self.pool()))))

    def test_model_scores_do_not_affect_selection(self):
        pool=self.pool()
        chosen=[r['id'] for r in select_recordings(pool)]
        for i,r in enumerate(pool): r['model_score']=i
        self.assertEqual(chosen,[r['id'] for r in select_recordings(pool)])

    def test_insufficient_groups_and_duplicate_ids_rejected(self):
        for pool in (self.pool()[:10],self.pool()+[self.pool()[0]]):
            with self.assertRaises(ValueError): select_recordings(pool)


if __name__=='__main__':
    unittest.main()
