import copy
import unittest

from lyric_aligner.timeline.human_boundary_reuse import apply_confirmed_boundaries
from scripts.test_editor_risk_profile import _fixture


class HumanBoundaryReuseTests(unittest.TestCase):
    def fixture(self, **kwargs):
        lock,gold,sha=_fixture(**kwargs)
        target=min(lock['selection']['populations']['outer'],key=lambda r:r['start_ms'])
        row={'original_cue':target['cue_number'],'track':target['track'],
             'start_ms':target['start_ms'],'end_ms':target['end_ms'],
             'text':target['canonical_text'],'task_fingerprint_sha256':'a'*64,
             'boundary_authority':''}
        args=dict(selection_lock=lock,selection_lock_file_sha256=sha,human_gold=gold,
                  final_audio_sha256=gold['final_audio_sha256'],task_fingerprint='a'*64)
        return row,args

    def test_reuses_exact_confirmations_without_row_wide_authority(self):
        row,args=self.fixture()
        before=copy.deepcopy(row)
        rows,decisions=apply_confirmed_boundaries(report_rows=[row],**args)
        self.assertEqual(rows[0]['start_ms'],10000)
        self.assertEqual(rows[0]['end_ms'],10700)
        self.assertEqual(rows[0]['text'],row['text'])
        self.assertEqual(rows[0]['boundary_authority'],'')
        self.assertEqual(sum(d['action']=='reuse_human_confirmation' for d in decisions),2)
        self.assertEqual(row,before)

    def test_within_tolerance_is_preserved(self):
        row,args=self.fixture(start_offset_ms=30,end_offset_ms=40)
        rows,_=apply_confirmed_boundaries(report_rows=[row],**args)
        self.assertEqual(rows,[row])

    def test_split_only_changes_outer_edges(self):
        row,args=self.fixture()
        a={**row,'text':row['text'][:3],'end_ms':10500}
        b={**row,'text':row['text'][3:],'start_ms':10500}
        rows,_=apply_confirmed_boundaries(report_rows=[a,b],**args)
        self.assertEqual([(r['start_ms'],r['end_ms']) for r in rows],[(10000,10500),(10500,10700)])
        self.assertNotIn('human_confirmed_end_record',rows[0])
        self.assertNotIn('human_confirmed_start_record',rows[1])

    def test_conflicting_start_does_not_block_safe_end(self):
        row,args=self.fixture()
        previous={**row,'original_cue':999,'track':'other','text':'previous','start_ms':9000,'end_ms':10200}
        rows,decisions=apply_confirmed_boundaries(report_rows=[previous,row],**args)
        self.assertEqual(rows[1]['start_ms'],row['start_ms'])
        self.assertEqual(rows[1]['end_ms'],10700)
        self.assertTrue(any(d['reason']=='geometry_conflict' for d in decisions))

    def test_changed_lyrics_do_not_reuse_other_target(self):
        row,args=self.fixture()
        row['text']='different phrase'
        rows,decisions=apply_confirmed_boundaries(report_rows=[row],**args)
        self.assertEqual(rows,[row])
        self.assertTrue(any(d['reason']=='lexical_target_changed' for d in decisions))

    def test_stale_audio_task_and_tampered_gold_rejected(self):
        row,args=self.fixture()
        for key,value in [('final_audio_sha256','0'*64),('task_fingerprint','b'*64)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                apply_confirmed_boundaries(report_rows=[row],**{**args,key:value})
        args=copy.deepcopy(args)
        args['human_gold']['records'][0]['gold_ms']+=1
        with self.assertRaises(ValueError): apply_confirmed_boundaries(report_rows=[row],**args)

    def test_repeat_reuse_is_stable_for_resolved_target(self):
        row,args=self.fixture()
        rows,_=apply_confirmed_boundaries(report_rows=[row],**args)
        again,decisions=apply_confirmed_boundaries(report_rows=rows,**args)
        self.assertEqual(again,rows)
        self.assertFalse(any(d['action']=='reuse_human_confirmation' for d in decisions))


if __name__=='__main__':
    unittest.main()
