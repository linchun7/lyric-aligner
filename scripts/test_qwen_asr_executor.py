import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as Obj
from unittest import mock
from lyric_aligner.alignment import qwen_asr_executor as qwen

import numpy as np

from lyric_aligner.alignment.asr_executor import AsrExecutionError
from lyric_aligner.alignment.qwen_asr_executor import QwenAsrExecutionConfig, execute_qwen_asr_jobs


class QwenExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.audio = self.root/'audio.wav'
        self.audio.write_bytes(b'fixture')
        self.config = QwenAsrExecutionConfig(str(self.root), str(self.root))
        self.plan = dict(mode='plan_only', backend_execution_performed=False, jobs=[
            dict(job_id='j1', occurrence_id='o1', canonical_line_index=0,
                 mix_window_ms=[1000,2000], requested_capabilities=['mix_asr'])])
        self.words = [Obj(text='hello',start_time=.1,end_time=.3),
                      Obj(text='world',start_time=.3,end_time=.5)]
        self.calls = []

    def execute(self, canonical='hello world'):
        def transcribe(**kwargs):
            self.calls.append(kwargs)
            return [Obj(text='hello world',language='English',time_stamps=self.words)]
        return execute_qwen_asr_jobs(audio_path=self.audio,plan=self.plan,
            canonical_text_by_job_id={'j1':canonical},config=self.config,
            model_factory=lambda config:Obj(transcribe=transcribe),
            audio_loader=lambda path:np.arange(48000,dtype=np.float32))

    def test_unicode_windows_tokenizer_is_mirrored_without_changing_installed_files(self):
        source = self.root/'中文环境'/'nagisa'
        source.mkdir(parents=True)
        (source/'__init__.py').write_bytes(b"# installed tokenizer")
        (source/'model.bin').write_bytes(b"native model bytes")
        original = {p.name:p.read_bytes() for p in source.iterdir()}
        with (mock.patch.object(qwen.sys, "platform", "win32"),
              mock.patch.object(qwen.sys, "path", list(qwen.sys.path)),
              mock.patch.object(qwen, "_TOKENIZER_COMPAT", None),
              mock.patch.object(qwen.importlib.util, "find_spec", return_value=Obj(origin=str(source/'__init__.py')))):
            metadata = qwen._prepare_windows_tokenizer()
            temporary, _ = qwen._TOKENIZER_COMPAT
            try:
                mirror = Path(temporary.name)/'nagisa'
                self.assertTrue(str(mirror).isascii())
                self.assertEqual({p.name:p.read_bytes() for p in mirror.iterdir()}, original)
                self.assertEqual(metadata['file_count'], 2)
                self.assertEqual(len(metadata['installed_package_sha256']), 64)
                self.assertEqual(qwen._prepare_windows_tokenizer(), metadata)
                self.assertEqual(qwen.sys.path[0], temporary.name)
            finally:
                temporary.cleanup()
        self.assertEqual({p.name:p.read_bytes() for p in source.iterdir()}, original)

    def test_ascii_tokenizer_installation_needs_no_mirror(self):
        with (mock.patch.object(qwen.sys, "platform", "win32"),
              mock.patch.object(qwen, "_TOKENIZER_COMPAT", None),
              mock.patch.object(qwen.importlib.util, "find_spec", return_value=Obj(origin="C:/runtime/nagisa/__init__.py")),
              mock.patch.object(qwen.tempfile, "TemporaryDirectory") as temporary):
            self.assertIsNone(qwen._prepare_windows_tokenizer())
            temporary.assert_not_called()

    def test_clip_origin_and_missing_probabilities_are_truthful(self):
        result=self.execute();row=result['jobs'][0]
        np.testing.assert_array_equal(self.calls[0]['audio'][0],np.arange(16000,32000,dtype=np.float32))
        self.assertEqual((row['canonical_match_start_ms'],row['canonical_match_end_ms']),(1100,1500))
        self.assertEqual(row['backend'],'qwen3_asr')
        self.assertIsNone(row['language_probability'])
        self.assertIsNone(row['canonical_match_mean_word_probability'])
        self.assertEqual(row['segments'],[])
        self.assertEqual(result['evidence_family_count'],1)
        self.assertNotIn('observed_text',row)
        self.assertNotIn('text',row['aligned_words'][0])
        self.assertEqual(self.calls[0]['context'],'')
        self.assertNotIn('text',self.calls[0])

    def test_partial_phrase_does_not_claim_canonical_onset(self):
        row=self.execute('oh hello world')['jobs'][0]
        self.assertGreater(row['canonical_match_support_score'],.8)
        self.assertFalse(row['canonical_start_covered'])
        self.assertIsNone(row['canonical_match_start_ms'])
        self.assertIsNotNone(row['observed_match_span'])

    def test_zero_duration_prefix_and_nonfinite_words_do_not_create_onsets(self):
        for bad in (.1,float('nan')):
            with self.subTest(bad=bad):
                self.words[0].end_time=bad
                row=self.execute()['jobs'][0]
                self.assertIsNone(row['canonical_match_start_ms'])
                json.dumps(row,allow_nan=False)

    def test_partial_suffix_does_not_claim_canonical_end(self):
        row=self.execute('hello world again')['jobs'][0]
        self.assertTrue(row['canonical_start_covered'])
        self.assertFalse(row['canonical_end_covered'])
        self.assertEqual(row['canonical_match_start_ms'],1100)
        self.assertIsNone(row['canonical_match_end_ms'])

    def test_overlapping_jobs_execute_separate_local_clips(self):
        self.plan['jobs'].append({**self.plan['jobs'][0],'job_id':'j2','mix_window_ms':[1500,2500]})
        result=self.execute()
        self.assertEqual(result['job_count'],2)
        self.assertEqual(len(self.calls),2)
        self.assertEqual(self.calls[1]['audio'][0][0],24000)

    def test_invalid_or_duplicate_windows_fail_before_model(self):
        for window in ([1000,4000],[-1,500],[1000,1000]):
            self.plan['jobs'][0]['mix_window_ms']=window
            with self.subTest(window=window),self.assertRaises(AsrExecutionError):self.execute()
        self.assertEqual(self.calls,[])
        self.plan['jobs'][0]['mix_window_ms']=[1000,2000]
        self.plan['jobs'].append(dict(self.plan['jobs'][0]))
        with self.assertRaisesRegex(AsrExecutionError,'duplicate'):self.execute()

    def test_empty_plan_does_not_decode_or_load_models(self):
        self.plan['jobs']=[]
        result=execute_qwen_asr_jobs(audio_path=self.audio,plan=self.plan,canonical_text_by_job_id={},
            config=self.config,model_factory=lambda c:self.fail('loaded model'),
            audio_loader=lambda p:self.fail('decoded audio'))
        self.assertFalse(result['model_loaded'])

    def test_mixed_language_does_not_force_track_language(self):
        self.plan['jobs'][0]['language_profile']='zh'
        self.execute('你好 hello')
        self.assertIsNone(self.calls[0]['language'])

    def test_remote_model_id_cannot_trigger_download(self):
        self.config=QwenAsrExecutionConfig('Qwen/not-a-local-directory',str(self.root))
        with self.assertRaisesRegex(AsrExecutionError,'local model directory'):self.execute()


if __name__=='__main__':unittest.main()
