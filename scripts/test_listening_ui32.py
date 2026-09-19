from __future__ import annotations
import copy,json,tempfile,unittest,wave
from pathlib import Path
from lyric_aligner.review.listening import ListeningReviewApp,digest,file_ref,evaluate_review
from scripts.v4_listen import open_app
from scripts.v4_human_boundary_anchor_audit_ui import HTML,verify_ui32_core,render_review_extension


class Listening32Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        with wave.open(str(self.root/'clip.wav'),'wb') as w:
            w.setparams((1,2,16000,0,'NONE','not compressed'));w.writeframes(b'\x00\x00'*32000)
        selection={'partition':'calibration','cases':[{'id':'fixture','variant_sources':{'v1':['baseline'],'v2':['candidate']}}]}
        (self.root/'selection.private.json').write_text(json.dumps(selection),encoding='utf-8')
        self.pack={'schema_version':'listening-review-pack-1.0','ui_revision':'3.2','title':'测试','partition':'calibration','source_labels_hidden':True,
            'selection_sha256':file_ref(self.root/'selection.private.json')['sha256'],
            'cases':[{'id':'fixture','language':'ko','duration_ms':2000,'focus_ms':[500,1500],
                'audio':{'path':'clip.wav','sha256':file_ref(self.root/'clip.wav')['sha256']},
                'options':[{'id':'v1','cues':[{'start_ms':500,'end_ms':1500,'text':'wrong'}]},
                           {'id':'v2','cues':[{'start_ms':500,'end_ms':1500,'text':'안녕'}]}]}]}
        self.freeze()

    def freeze(self):
        self.pack.pop('pack_sha256',None);self.pack['pack_sha256']=digest(self.pack)
        (self.root/'review.pack.json').write_text(json.dumps(self.pack,ensure_ascii=False),encoding='utf-8')
        self.app=ListeningReviewApp(self.root)

    def response(self):
        return {'pack_sha256':self.app.identity,'case_id':'fixture','reviewer':'synthetic-test-only','language_proficiency':'fluent','prior_exposure':'unseen','audio_listened':True,
                'ratings':[{'id':'v1','lexical':'wrong','ownership':'correct','timing':'correct'},
                           {'id':'v2','lexical':'correct','ownership':'correct','timing':'correct'}],'notes':'synthetic fixture, never real human Gold'}

    def test_confirmed_core_is_frozen(self):
        verify_ui32_core()
        import hashlib
        self.assertEqual(hashlib.sha256(HTML.encode()).hexdigest(),'956d1a63d737551abbef5cc66386622a12ba3208aa136dc415068b58cf5607b1')

    def test_real_port_cannot_serve_two_review_packs(self):
        from scripts.v4_human_boundary_anchor_audit_ui import _bind_server
        first=_bind_server(self.app,0)
        self.addCleanup(first.server_close)
        with self.assertRaisesRegex(RuntimeError,'refusing silent fallback'):
            _bind_server(self.app,first.server_port)

    def test_default_comparison_reuses_32_and_hides_source_files(self):
        app=open_app(self.root);state=app.state();raw=json.dumps(state)
        self.assertIn('UI 3.2',app.html);self.assertIn('A/B ×2',app.html);self.assertIn('jumpFromInput',app.html)
        self.assertNotIn('variant_sources',raw);self.assertNotIn('selection.private',raw);self.assertNotIn(str(self.root),raw)
        self.assertEqual(state['rows'][0]['options'][1]['cues'][0]['text'],'안녕')
        self.assertFalse(state['candidate_text_blind'])

    def test_unanswered_metrics_are_null_not_zero_accuracy(self):
        m=evaluate_review(self.root)
        self.assertIsNone(m['repair_rate']);self.assertIsNone(m['introduced_error_rate']);self.assertIsNone(m['net_new_correct_regions'])
        self.assertFalse(m['production_authority_granted'])

    def test_raw_saves_are_append_only_and_repeat_is_not_an_extra_vote(self):
        p=self.response();self.app.save(p);self.app.save(p)
        self.assertEqual(len(list((self.root/'responses').glob('*.json'))),2)
        self.assertEqual(evaluate_review(self.root)['fully_assessed_regions'],1)
        self.assertEqual(evaluate_review(self.root)['repaired_regions'],1)

    def test_low_language_confidence_does_not_become_gold(self):
        p=self.response();p['language_proficiency']='limited';self.app.save(p)
        self.assertEqual(self.app.state()['complete'],1)
        m=evaluate_review(self.root);self.assertEqual(m['fully_assessed_regions'],0);self.assertIsNone(m['repair_rate'])

    def test_unheard_incomplete_or_stale_response_is_not_written(self):
        for field,value in [('audio_listened',False),('pack_sha256','0'*64),('language_proficiency',''),('ratings',[])]:
            p=self.response();p[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):self.app.save(p)
        self.assertFalse((self.root/'responses').exists())

    def test_changed_audio_blocks_save(self):
        with (self.root/'clip.wav').open('ab') as f:f.write(b'changed')
        with self.assertRaisesRegex(ValueError,'audio changed'):self.app.save(self.response())

    def test_changed_plan_blocks_old_page_even_after_rehash(self):
        p=self.response();self.pack['cases'][0]['options'][0]['cues'][0]['text']='new option';self.freeze()
        with self.assertRaisesRegex(ValueError,'stale'):self.app.save(p)

    def test_private_source_labels_cannot_be_in_public_options(self):
        self.pack['cases'][0]['options'][0]['source']='safe'
        with self.assertRaisesRegex(ValueError,'private labels'):self.freeze()

    def test_path_escape_rejected_before_write(self):
        self.pack['cases'][0]['audio']['path']='../clip.wav'
        with self.assertRaisesRegex(ValueError,'local pack'):self.freeze()

    def test_blind_seen_response_excluded_without_relabeling_partition(self):
        self.pack['partition']='blind'
        selection=json.loads((self.root/'selection.private.json').read_text());selection['partition']='blind'
        (self.root/'selection.private.json').write_text(json.dumps(selection));self.pack['selection_sha256']=file_ref(self.root/'selection.private.json')['sha256'];self.freeze()
        p=self.response();p['prior_exposure']='seen';self.app.save(p)
        self.assertIsNone(evaluate_review(self.root)['repair_rate'])

    def test_qualified_disagreement_is_excluded(self):
        p=self.response();self.app.save(p);p['reviewer']='another-synthetic-fixture';p['ratings'][1]['lexical']='wrong';self.app.save(p)
        m=evaluate_review(self.root);self.assertEqual(m['fully_assessed_regions'],0);self.assertEqual(m['excluded'][0]['reason'],'qualified_reviewers_disagree')

    def test_correct_baseline_denominator_records_new_errors(self):
        p=self.response();p['ratings'][0]['lexical']='correct';p['ratings'][1]['ownership']='wrong';self.app.save(p)
        m=evaluate_review(self.root);self.assertEqual(m['introduced_error_rate'],1);self.assertIsNone(m['repair_rate']);self.assertEqual(m['net_new_correct_regions'],-1)

    def test_unknown_axis_is_not_imputed_correct(self):
        p=self.response();p['ratings'][1]['timing']='uncertain';self.app.save(p)
        m=evaluate_review(self.root);self.assertEqual(m['fully_assessed_regions'],0);self.assertEqual(m['per_axis']['lexical']['candidate']['assessed'],1)
        self.assertIsNone(m['per_axis']['timing']['candidate']['rate'])

    def test_legacy_safe_balanced_source_labels_normalize_to_generic_roles(self):
        selection={'partition':'calibration','cases':[{'id':'fixture','variant_sources':{'v1':['safe'],'v2':['balanced']}}]}
        (self.root/'selection.private.json').write_text(json.dumps(selection),encoding='utf-8')
        self.pack['selection_sha256']=file_ref(self.root/'selection.private.json')['sha256'];self.freeze()
        self.app.save(self.response());metrics=evaluate_review(self.root)
        self.assertIn('baseline',metrics['per_axis']['lexical']);self.assertIn('candidate',metrics['per_axis']['lexical'])
        self.assertNotIn('balanced',metrics['per_axis']['lexical'])

    def test_private_mapping_cannot_be_changed_after_review(self):
        self.app.save(self.response());(self.root/'selection.private.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'selection changed'):evaluate_review(self.root)

    def test_legacy_html_directory_is_not_default(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d,'index.html').write_text('<h1>legacy</h1>')
            with self.assertRaisesRegex(ValueError,'legacy'):open_app(Path(d))

    def test_anchor_instance_identity_includes_consensus(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'outer').mkdir();(root/'outer/human_audit.csv').touch()
            c=root/'consensus.json';c.write_text('{}')
            with patch('scripts.v4_listen.ui.AnchorAuditApp',side_effect=lambda *a,**k:SimpleNamespace(pack=root,lock={'lock_sha256':'f'*64})):
                first=open_app(root,c).service_identity
                c.write_text('{"changed":true}')
                self.assertNotEqual(first,open_app(root,c).service_identity)

    def test_timing_clip_hash_change_is_rejected(self):
        from scripts.test_timing_decision_review import pack
        from lyric_aligner.evaluation.timing_decision_review import build_review_manifest
        from scripts.v4_listen import TimingReviewApp
        m=build_review_manifest(pack());c=m['cases'][0];clip=self.root/c['clip_file'];clip.parent.mkdir(exist_ok=True)
        clip.write_bytes((self.root/'clip.wav').read_bytes())
        (self.root/'manifest.json').write_text(json.dumps(m))
        receipt={'review_manifest_sha256':m['manifest_sha256'],'clips':[{'file':c['clip_file'],'sha256':file_ref(clip)['sha256']}]}
        (self.root/'materialization.json').write_text(json.dumps(receipt))
        TimingReviewApp(self.root)
        with clip.open('ab') as f:f.write(b'changed')
        with self.assertRaisesRegex(ValueError,'timing clip changed'):TimingReviewApp(self.root)

    def test_timing_blind_uses_same_ui_and_escapes_lyrics(self):
        from scripts.test_timing_decision_review import pack
        from lyric_aligner.evaluation.timing_decision_review import build_review_manifest,render_review_html
        m=build_review_manifest(pack());m['cases'][0]['target_text']='</script><script>alert(1)</script>'
        page=render_review_html(m)
        self.assertIn('UI 3.2',page);self.assertIn('A/B ×2',page)
        self.assertNotIn('</script><script>alert(1)</script>',page)
        self.assertNotIn('old_final_ms',page);self.assertNotIn('hybrid_ms',page)
        self.assertIn('timing-decision-review-response-1.0',page)


if __name__=='__main__':unittest.main()
