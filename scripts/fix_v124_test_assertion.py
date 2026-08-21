from pathlib import Path
p=Path('scripts/test_v4_smart_bpm_bounded_stream_v123.py')
s=p.read_text(encoding='utf-8')
old='''        replacements, _, summary, _ = recover_text_reviews_from_bpm_projection(\n            cues, canonical, decisions, rate_prior_metadata_by_source={0: {"provenance": "bpm_derived", "value": 1.0}}\n        )\n        self.assertEqual(summary.bounded_stream_region_count, 0)\n        self.assertNotIn(1, replacements)\n        self.assertNotIn(2, replacements)\n'''
new='''        replacements, updated, summary, _ = recover_text_reviews_from_bpm_projection(\n            cues, canonical, decisions, rate_prior_metadata_by_source={0: {"provenance": "bpm_derived", "value": 1.0}}\n        )\n        self.assertEqual(summary.bounded_stream_region_count, 0)\n        # Existing mapped 1:1 BPM recovery is intentionally unchanged and may\n        # still resolve these English lines.  The new v1.2.4 guard only blocks\n        # the multi-cue bounded tier.\n        self.assertTrue(all(\n            not item.reason.startswith("sequence_projection_confirms_bpm_bounded_stream")\n            for item in updated[1:3]\n        ))\n'''
if s.count(old)!=1: raise SystemExit('expected assertion block not found exactly once')
p.write_text(s.replace(old,new,1),encoding='utf-8')
