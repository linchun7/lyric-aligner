import csv
import json
import tempfile
import unittest
import argparse
from unittest.mock import patch
from types import SimpleNamespace
from pathlib import Path

from scripts.task_contract import build_task_manifest
from scripts.v4_upgrade_subtitles import run_job, validate_pair
from lyric_aligner.contracts.artifacts import canonical_json_sha256, sha256_file


class UpgradeSubtitlesTests(unittest.TestCase):
    def setUp(self):
        repository = Path(__file__).resolve().parents[1]
        (repository/'private').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=repository/'private', prefix='test_upgrade_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.srt = self.root/'source.srt'
        self.srt.write_bytes(b'1\r\n00:00:01,000 --> 00:00:02,000\r\nhello\r\n')
        audio = self.root/'audio.wav'
        audio.write_bytes(b'test-audio')
        songs = self.root/'songs.txt'
        songs.write_text('song', encoding='utf-8')
        lyrics = self.root/'lyrics'
        lyrics.mkdir()
        (lyrics/'song.lrc').write_text('[00:01.00]hello', encoding='utf-8')
        manifest = build_task_manifest(repository, 'fixture', source_srt=self.srt,
                                      audio=audio, song_list=songs, lyrics_dir=lyrics)
        (self.root/'qa').mkdir()
        self.manifest_path = self.root/'qa/task_manifest.json'
        self.manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        self.report = self.root/'report.csv'
        self.row = dict(start_ms=1000, end_ms=2000, text='hello',
                        task_fingerprint_sha256=manifest['task_fingerprint_sha256'])
        self.write_report()
        self.job = self.root/'job.json'
        self.job.write_text(json.dumps(dict(schema_version='subtitle-upgrade-job-1.0',
            task_manifest='qa/task_manifest.json', report='report.csv', srt='source.srt')), encoding='utf-8')

    def write_report(self):
        with self.report.open('w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=list(self.row))
            w.writeheader()
            w.writerow(self.row)

    def test_missing_evidence_keeps_exact_bytes_without_human_task(self):
        result = run_job(self.job, self.root/'result')
        self.assertTrue(result['srt_byte_identical'])
        self.assertEqual(result['start_changed_count'], 0)
        self.assertEqual(result['new_human_annotations'], 0)
        self.assertEqual(result['quality_status'], 'no_human_truth_accuracy_unknown')
        self.assertEqual((self.root/'result/final.srt').read_bytes(), self.srt.read_bytes())
        with self.assertRaises(FileExistsError):
            run_job(self.job, self.root/'result')

    def test_unknown_top_level_human_confirmation_typo_is_rejected(self):
        # A misspelled ``human_confirmations`` key used to fall through to the
        # no-evidence path and produce an apparently successful byte-preserving
        # result, even though the requested evidence was never consumed.
        job = json.loads(self.job.read_text(encoding='utf-8'))
        job['human_confirmation'] = {'gold': 'missing.json', 'selection_lock': 'missing.json'}
        path = self.root/'unknown-human-confirmation.json'
        path.write_text(json.dumps(job), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'unknown upgrade job fields: human_confirmation'):
            run_job(path, self.root/'unknown-human-confirmation-result')

    def test_unknown_editor_preservation_key_is_rejected_before_materialization(self):
        # ``canonical_region`` is optional: a typo currently turns a requested
        # local preservation into a whole-occurrence request via config.get().
        # The strict config gate must reject it before any bound input is read.
        job = json.loads(self.job.read_text(encoding='utf-8'))
        job['editor_preservation'] = {'canonical_regoin': [0, 1]}
        path = self.root/'unknown-editor-region.json'
        path.write_text(json.dumps(job), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'editor_preservation has unknown fields: canonical_regoin'):
            run_job(path, self.root/'unknown-editor-region-result')

    def test_replaying_existing_gap_reviews_does_not_count_new_annotations(self):
        job = json.loads(self.job.read_text(encoding='utf-8'))
        job['gap_review'] = {}
        for key in ('lock', 'review', 'prior_receipt'):
            p = self.root / (key + '.json')
            p.write_text('{}', encoding='utf-8')
            job['gap_review'][key] = p.name
        self.job.write_text(json.dumps(job), encoding='utf-8')
        def apply_existing(**kwargs):
            dest = kwargs['output_dir']; dest.mkdir()
            (dest/'corrected.csv').write_bytes(self.report.read_bytes())
            (dest/'corrected.srt').write_bytes(self.srt.read_bytes())
            return {'confirmed_record_count': 3}
        display = dict(artifact_sha256='a'*64, output_srt_sha256='b'*64,
                       output_cue_count=1, groups=[])
        with patch('scripts.v4_apply_gap_review.prepare', return_value=(None, {}, None, None, None, None)), \
             patch('scripts.v4_apply_gap_review.materialize', side_effect=apply_existing), \
             patch('scripts.v4_group_vocalization_display.materialize', return_value=display), \
             patch('scripts.v4_group_vocalization_display.verify'):
            result = run_job(self.job, self.root/'result')
        self.assertEqual(result['new_human_annotations'], 0)
        self.assertEqual(result['applied_existing_gap_review_records'], 3)
        self.assertTrue(result['srt_byte_identical'])

    def test_wrong_task_rejected_before_output(self):
        self.row['task_fingerprint_sha256'] = 'b'*64
        self.write_report()
        with self.assertRaisesRegex(ValueError, 'another task'):
            run_job(self.job, self.root/'result')
        self.assertFalse((self.root/'result.staging').exists())

    def test_mismatched_srt_and_changed_source_rejected(self):
        self.row['end_ms'] = 2100
        self.write_report()
        with self.assertRaisesRegex(ValueError, 'report/SRT mismatch'):
            run_job(self.job, self.root/'result')
        self.srt.write_text('different', encoding='utf-8')
        with self.assertRaises(ValueError):
            run_job(self.job, self.root/'result')

    def test_display_audit_uses_actual_display_columns(self):
        self.row.update(end_ms=9000, display_start_ms=1000, display_end_ms=2000,
                        display_text='hello', display_policy_id='fixture')
        self.write_report()
        validate_pair(self.report, self.srt)

    def test_input_directory_cannot_be_used_as_destination(self):
        with self.assertRaises((ValueError, FileExistsError)):
            run_job(self.job, self.root/'lyrics')
        self.assertTrue((self.root/'lyrics/song.lrc').exists())

    def test_qa_output_cannot_overwrite_receipt(self):
        import sys
        sys.path.insert(0,str(Path(__file__).resolve().parent))
        from scripts.redo_karaoke_pipeline import validate_confirmation_qa_paths
        receipt=self.root/'receipt.json'
        receipt.write_text('{"input_files": {}}',encoding='utf-8')
        manifest=json.loads(self.manifest_path.read_text(encoding='utf-8'))
        for field in ('out','out_review','release_manifest'):
            args=argparse.Namespace(task_manifest=self.manifest_path,boundary_confirmations=receipt,
                out=self.root/'qa.json',out_review=self.root/'review.csv',release_manifest=self.root/'release.json')
            setattr(args,field,receipt)
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_confirmation_qa_paths(args,manifest)
        self.assertEqual(receipt.read_text(encoding='utf-8'),'{"input_files": {}}')

    def test_successful_qa_can_release_and_paths_survive_promotion(self):
        job=json.loads(self.job.read_text(encoding='utf-8'))
        job['qa']={}
        for role in ('audio_alignment','manual_overrides','regression_cases'):
            p=self.root/(role+'.json');p.write_text('{}',encoding='utf-8')
            job['qa'][role]=p.name
        self.job.write_text(json.dumps(job),encoding='utf-8')
        def successful_qa(command, **kwargs):
            output=Path(command[command.index('--out')+1])
            qa=dict(task_fingerprint_sha256=self.row['task_fingerprint_sha256'],algorithm_version='fixture',
                passed=True,structurally_valid=True,fully_reviewed=True,publish_ready=True,release_status='ready',
                review_candidate_count=0,unverified_timing_mutation_count=0,verified_human_boundary_edge_count=0)
            output.write_text(json.dumps(qa),encoding='utf-8')
            Path(command[command.index('--out-review')+1]).write_text('category\n',encoding='utf-8')
            return SimpleNamespace(returncode=0,stdout='',stderr='')
        with patch('scripts.v4_upgrade_subtitles.subprocess.run',side_effect=successful_qa):
            result=run_job(self.job,self.root/'result')
        self.assertTrue(result['publish_ready'])
        release=json.loads((self.root/'result/qa_RELEASE_ARTIFACT.json').read_text(encoding='utf-8'))
        self.assertEqual(release['artifact_id'],canonical_json_sha256({k:v for k,v in release.items() if k!='artifact_id'}))
        for record in release['outputs']:
            self.assertTrue(Path(record['path']).is_file())
            self.assertEqual(sha256_file(Path(record['path'])),record['sha256'])


if __name__ == '__main__':
    unittest.main()
