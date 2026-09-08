import unittest
import sys
import csv
import json
import tempfile
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scripts.redo_karaoke_pipeline import Cue, unverified_timing_mutation_candidates
from scripts.verified_boundary_receipt import verified_qa_edges
from scripts.v4_materialize_calibrated_alignment import sha256_json
from lyric_aligner.contracts.artifacts import sha256_file


class ConfirmedEdgeQATests(unittest.TestCase):
    def test_only_confirmed_changed_edge_is_accepted(self):
        source = [Cue(1, 1000, 2000, 'hello')]
        row = dict(kind='existing', original_cue='1', start_ms=1100, end_ms=2000,
                   text='hello', track='track', human_confirmed_start_record='untrusted-field')
        self.assertEqual(len(unverified_timing_mutation_candidates([row], source)), 1)
        self.assertEqual(unverified_timing_mutation_candidates([row], source,
            verified_boundary_edges={(1, 'start'):1100}), [])
        row['end_ms'] = 2100
        self.assertEqual(len(unverified_timing_mutation_candidates([row], source,
            verified_boundary_edges={(1, 'start'):1100})), 1)

    def test_stale_value_or_wrong_position_does_not_authorize(self):
        source = [Cue(1, 1000, 2000, 'hello')]
        row = dict(kind='existing', original_cue='1', start_ms=1100, end_ms=2000, text='hello', track='track')
        for edges in ({(1, 'start'):1099}, {(2, 'start'):1100}, {(1, 'end'):1100}):
            with self.subTest(edges=edges):
                self.assertEqual(len(unverified_timing_mutation_candidates([row], source, verified_boundary_edges=edges)), 1)

    def test_new_interval_requires_both_edges(self):
        row = dict(kind='inserted', original_cue='', start_ms=1100, end_ms=2000, text='hello', track='track')
        self.assertEqual(len(unverified_timing_mutation_candidates([row], [], verified_boundary_edges={(1, 'start'):1100})), 1)
        self.assertEqual(unverified_timing_mutation_candidates([row], [],
            verified_boundary_edges={(1, 'start'):1100, (1, 'end'):2000}), [])


class ReceiptReadbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.report, self.srt, self.receipt = (self.root/p for p in ('final.csv','final.srt','receipt.json'))
        self.row = dict(start_ms=1100,end_ms=2000,text='hello')
        with self.report.open('w',newline='',encoding='utf-8') as f:
            w=csv.DictWriter(f,fieldnames=list(self.row));w.writeheader();w.writerow(self.row)
        self.srt.write_text('1\n00:00:01,100 --> 00:00:02,000\nhello\n',encoding='utf-8')
        files={}
        for role in ('manifest','lock','gold','report','srt'):
            p=self.root/(role+'.input');p.write_text(role,encoding='utf-8')
            files[role]={'path':p.name,'sha256':sha256_file(p)}
        self.decisions=[dict(output_position=1,boundary_kind='start',reason='exact_audio_and_lexical_target',confirmed_ms=1100,uncertainty_ms=50)]
        self.artifact=dict(schema_version='human-boundary-reuse-materialization-1.1',task_fingerprint_sha256='a'*64,
            source_srt_sha256='b'*64,final_audio_sha256='c'*64,output_report_sha256=sha256_file(self.report),
            output_srt_sha256=sha256_file(self.srt),input_files=files,selection_lock_sha256='d'*64,
            human_gold_artifact_sha256='e'*64,decisions=self.decisions,decisions_sha256=sha256_json(self.decisions))
        # Isolate receipt readback from the separately tested gold/core replay;
        # real H180 integration exercises the complete prepare_reuse validator.
        self.replay=({'task_fingerprint_sha256':'a'*64,'inputs':{'audio':{'sha256':'c'*64}}},
                     {'lock_sha256':'d'*64},{'artifact_sha256':'e'*64},None,[],[],[self.row],self.decisions)
        self.write_receipt()

    def write_receipt(self):
        self.artifact.pop('artifact_sha256',None)
        self.artifact['artifact_sha256']=sha256_json(self.artifact)
        self.receipt.write_text(json.dumps(self.artifact),encoding='utf-8')

    def verify(self):
        with patch('scripts.verified_boundary_receipt.prepare_reuse',return_value=self.replay):
            return verified_qa_edges(artifact_path=self.receipt,report_path=self.report,srt_path=self.srt,
                task_fingerprint='a'*64,source_srt_sha256='b'*64,final_audio_sha256='c'*64)

    def test_exact_srt_returns_only_confirmed_edge(self):
        self.assertEqual(self.verify()[0],{(1,'start'):1100})

    def test_rehashed_receipt_cannot_hide_different_srt(self):
        self.srt.write_text('1\n00:00:01,101 --> 00:00:02,000\nhello\n',encoding='utf-8')
        self.artifact['output_srt_sha256']=sha256_file(self.srt)
        self.write_receipt()
        with self.assertRaisesRegex(ValueError,'final SRT differs'):
            self.verify()

    def test_changed_replay_input_is_rejected(self):
        (self.root/'gold.input').write_text('tampered',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'input changed'):
            self.verify()


if __name__ == '__main__':
    unittest.main()
