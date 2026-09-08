import copy
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from scripts.v4_apply_gap_review import apply_reviews
from scripts.v4_materialize_calibrated_alignment import sha256_json
from scripts.verified_boundary_receipt import replay_gap_receipt
from lyric_aligner.contracts.artifacts import sha256_file


class GapReviewTests(unittest.TestCase):
    def fixture(self):
        rows = [dict(track='track',lrc_indices=str(i),text='line '+str(i),start_ms=s,end_ms=e)
                for i,(s,e) in enumerate([(1000,2000),(2100,3000),(3000,4000)])]
        cases = [dict(id='gap'+str(i),track=r['track'],lrc_indices=r['lrc_indices'],text=r['text'],
                      reference_start_ms=r['start_ms'],reference_end_ms=r['end_ms'],
                      clip_start_ms=0,clip_end_ms=5000,clip_sha256=str(i)*64) for i,r in enumerate(rows[:2])]
        lock = dict(schema_version='gap-boundary-review-pack-1.0',task_fingerprint_sha256='a'*64,
                    final_audio_sha256='b'*64,cases=cases)
        lock['lock_sha256']=sha256_json(lock)
        review = dict(schema_version='human-gap-boundary-review-1.0',selection_lock_sha256=lock['lock_sha256'],
                      task_fingerprint_sha256='a'*64,final_audio_sha256='b'*64,records=[
                          dict(id=c['id'],track=c['track'],lrc_indices=c['lrc_indices'],text=c['text'],
                               clip_sha256=c['clip_sha256'],human_confirmed=True,presence='present',
                               start_ms=c['reference_start_ms']+100,end_ms=c['reference_end_ms']+200) for c in cases])
        return rows,lock,review

    def test_joint_neighbors_apply_without_extending_into_untouched_cue(self):
        rows,lock,review=self.fixture(); before=copy.deepcopy(rows)
        after,decisions=apply_reviews(rows,lock,review)
        self.assertEqual([(r['start_ms'],r['end_ms']) for r in after],[(1100,2200),(2200,3000),(3000,4000)])
        self.assertEqual(decisions[-1]['reason'],'geometry_conflict')
        self.assertEqual(rows,before)
        self.assertFalse(any('boundary_authority' in r for r in after))

    def test_unchecked_review_notes_never_grant_edge_authority(self):
        rows,lock,review=self.fixture()
        for r in review['records']:
            r.update(human_confirmed=False,note='everything confirmed; approve all boundaries')
        after,decisions=apply_reviews(rows,lock,review)
        self.assertEqual(after,rows)
        self.assertTrue(all(d['reason']=='pending_human_confirmation' for d in decisions))

    def test_absent_review_is_not_automatic_deletion(self):
        rows,lock,review=self.fixture()
        for r in review['records']:r['presence']='absent'
        after,decisions=apply_reviews(rows,lock,review)
        self.assertEqual(after,rows)
        self.assertTrue(all(d['reason']=='presence_requires_structural_resolution' for d in decisions))

    def test_wrong_bindings_duplicates_and_lexical_changes_are_rejected(self):
        rows,lock,review=self.fixture()
        for mutation in ('audio','duplicate','text','bool','range'):
            with self.subTest(mutation=mutation):
                value=copy.deepcopy(review)
                if mutation=='audio':value['final_audio_sha256']='c'*64
                if mutation=='duplicate':value['records'].append(value['records'][0])
                if mutation=='text':value['records'][0]['text']='another line'
                if mutation=='bool':value['records'][0]['human_confirmed']='true'
                if mutation=='range':value['records'][0]['end_ms']=6000
                with self.assertRaises(ValueError):apply_reviews(rows,lock,value)

    def test_changed_baseline_and_tampered_lock_rejected(self):
        rows,lock,review=self.fixture()
        rows[0]['start_ms']+=1
        with self.assertRaises(ValueError):apply_reviews(rows,lock,review)
        rows,lock,review=self.fixture();lock['cases'][0]['text']='changed'
        with self.assertRaises(ValueError):apply_reviews(rows,lock,review)

    def test_unchanged_confirmed_edge_remains_exact_authority(self):
        rows,lock,review=self.fixture()
        for r in review['records']:
            r['start_ms']=rows[int(r['lrc_indices'])]['start_ms']
            r['end_ms']=rows[int(r['lrc_indices'])]['end_ms']
        after,decisions=apply_reviews(rows,lock,review)
        self.assertEqual(after,rows)
        self.assertTrue(all(d['reason']=='exact_human_confirmed_edge' for d in decisions))


class GapReceiptReplayTests(unittest.TestCase):
    def test_rehashed_scope_or_missing_dependency_cannot_bypass_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); receipt=root/'receipt.json'
            direct={k:root/(k+'.input') for k in ('manifest','lock','review','report','srt','prior_receipt','clip_1')}
            for k,p in direct.items():p.write_text(k,encoding='utf-8')
            manifest=dict(task_fingerprint_sha256='a'*64,inputs={'audio':{'sha256':'b'*64},'source_srt':{'sha256':'c'*64}})
            artifact=dict(task_fingerprint_sha256='a'*64,final_audio_sha256='b'*64,source_srt_sha256='c'*64,
                          input_files={k:dict(path=p.name,sha256=sha256_file(p)) for k,p in direct.items()},decisions=[])
            def seal(a):
                a['artifact_sha256']=sha256_json({k:v for k,v in a.items() if k!='artifact_sha256'})
                return a
            with patch('scripts.v4_apply_gap_review.prepare',return_value=(manifest,direct,[],[],[],{(1,'start'):1000})):
                self.assertEqual(replay_gap_receipt(receipt,seal(artifact))[2],{(1,'start'):1000})
                missing=copy.deepcopy(artifact);del missing['input_files']['clip_1']
                with self.assertRaisesRegex(ValueError,'dependency inventory'):
                    replay_gap_receipt(receipt,seal(missing))
                foreign=copy.deepcopy(artifact)
                for k in ('task_fingerprint_sha256','final_audio_sha256','source_srt_sha256'):foreign[k]='0'*64
                with self.assertRaisesRegex(ValueError,'identity mismatch'):
                    replay_gap_receipt(receipt,seal(foreign))


if __name__=='__main__':
    unittest.main()
