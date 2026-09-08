import csv,json,shutil,tempfile,unittest
from pathlib import Path
from lyric_aligner import __version__
from lyric_aligner.assets.resolver import resolve_assets
from lyric_aligner.assets.bindings import bindings_from_payload
from lyric_aligner.contracts.artifacts import build_artifact_manifest,validate_artifact_output
from lyric_aligner.srt import Cue,parse_srt_strict
from task_contract import build_task_manifest,write_json_atomic
from v4_preserve_editor_occurrence import _select_region_target_positions,materialize
from v4_shadow_upgrade import _cue_ranges


class EditorMaterializerTests(unittest.TestCase):
    def region_fixture(self, overlapping=False):
        args=self.fixture()
        with args['audit_path'].open(encoding='utf-8') as f:row=next(csv.DictReader(f))
        row['canonical_line_index']='0'
        start,end=(2500,4000) if overlapping else (8000,9000)
        second=dict(row,start_ms=start,end_ms=end,text='unresolved content',canonical_line_index='99')
        # Keep the preexisting SRT sorted; the first candidate moves to 1-3s.
        if overlapping:
            row.update(start_ms=1000,end_ms=2000)
        with args['audit_path'].open('w',encoding='utf-8',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(row));w.writeheader();w.writerows([row,second])
        from v4_materialize_calibrated_alignment import _write_srt
        _write_srt(args['srt_path'],[row,second])
        return args

    def test_region_preserves_unselected_content_in_same_occurrence(self):
        args=self.region_fixture();report=materialize(**args,canonical_region=[0,1])
        cues=parse_srt_strict(args['output_dir']/'final.srt')
        self.assertEqual([(c.start_ms,c.end_ms) for c in cues],[(1000,3000),(8000,9000)])
        self.assertEqual(cues[1].text,'unresolved content')
        self.assertEqual(report['canonical_region'],[0,1])
        with (args['output_dir']/'final.csv').open(encoding='utf-8-sig') as f:row=next(csv.DictReader(f))
        self.assertEqual(json.loads(row['canonical_line_indices']),[0])

    def test_region_cannot_overlap_retained_content_in_same_occurrence(self):
        args=self.region_fixture(overlapping=True)
        with self.assertRaisesRegex(ValueError,'overlaps retained'):
            materialize(**args,canonical_region=[0,1])
        self.assertFalse(args['output_dir'].exists())

    def test_unproven_region_is_rejected(self):
        args=self.region_fixture()
        with self.assertRaisesRegex(ValueError,'unique exact'):
            materialize(**args,canonical_region=[0,2])

    def test_exact_internal_region_excludes_crossing_edge_cue(self):
        cases=(
            ('left',(
                "1\n00:00:01,000 --> 00:00:03,000\nzzzzzzzz\n\n"
                "2\n00:00:04,000 --> 00:00:06,000\nDeeper than I’ve ever known\n"
            ),0),
            ('right',(
                "1\n00:00:04,000 --> 00:00:06,000\nDeeper than I’ve ever known\n\n"
                "2\n00:00:07,000 --> 00:00:09,000\nzzzzzzzz\n"
            ),1),
        )
        for side,source_srt_text,crossing_ordinal in cases:
            with self.subTest(side=side):
                args=self.fixture(window=(2000,8000),source_srt_text=source_srt_text)
                source=args['manifest_path'].parent.parent/'input/source.srt'
                with args['audit_path'].open(encoding='utf-8') as handle:
                    row=next(csv.DictReader(handle))
                row['canonical_line_index']='0'
                with args['audit_path'].open('w',encoding='utf-8',newline='') as handle:
                    writer=csv.DictWriter(handle,fieldnames=list(row));writer.writeheader();writer.writerow(row)
                # Whole-occurrence materialization remains strict.  The exact
                # [0, 1) region owns only the safe 4-6s cue on either side.
                with self.assertRaisesRegex(ValueError,'crosses occurrence boundary'):
                    materialize(**args)
                self.assertFalse(args['output_dir'].exists())
                report=materialize(**args,canonical_region=[0,1])
                cues=parse_srt_strict(args['output_dir']/'final.srt')
                self.assertEqual([(cue.number,cue.start_ms,cue.end_ms,cue.text) for cue in cues],[
                    (1,4000,6000,'Deeper than I’ve ever known'),
                ])
                # The source is immutable and the crossing edge cue never
                # reaches the region materialization output.
                source_cues=parse_srt_strict(source)
                crossing=source_cues[crossing_ordinal]
                self.assertEqual(crossing.text,'zzzzzzzz')
                self.assertTrue(crossing.start_ms<2000 or crossing.end_ms>8000)
                self.assertEqual(report['canonical_region'],[0,1])
                # Smart still received both intersecting lexical cues; region
                # selection cannot bypass its pre-existing timing checks.
                self.assertEqual(report['smart_input_preparation']['lexical_to_source_ordinals'],[0,1])

    def test_region_rejects_when_selected_editor_cue_crosses_occurrence_edge(self):
        args=self.fixture(window=(2000,8000))
        with self.assertRaisesRegex(ValueError,'crosses occurrence boundary'):
            materialize(**args,canonical_region=[0,1])
        self.assertFalse(args['output_dir'].exists())

    def test_region_offsets_use_full_bound_canonical_prefix_for_shadow_consumer(self):
        args=self.fixture(
            canonical_lrc_text=(
                '[00:00.00]alpha\n[00:01.00]bravo\n[00:02.00]chorus\n'
                '[00:03.00]bridge\n[00:04.00]charlie\n[00:05.00]delta\n'
                '[00:06.00]echo\n'
            ),
            timeline_lines=[
                dict(canonical_line_index=3,text='bridge',source_start_ms=3000,source_end_ms=4000,mix_start_ms=3000,mix_end_ms=4000),
                dict(canonical_line_index=4,text='charlie',source_start_ms=4000,source_end_ms=5000,mix_start_ms=4000,mix_end_ms=5000),
                dict(canonical_line_index=5,text='delta',source_start_ms=5000,source_end_ms=6000,mix_start_ms=5000,mix_end_ms=6000),
                dict(canonical_line_index=6,text='echo',source_start_ms=6000,source_end_ms=7000,mix_start_ms=6000,mix_end_ms=7000),
            ],
            source_srt_text=(
                '1\n00:00:01,000 --> 00:00:02,000\nunrelated opening\n\n'
                '2\n00:00:03,000 --> 00:00:05,000\ncharlie delta\n\n'
                '3\n00:00:06,000 --> 00:00:07,000\nunrelated closing\n'
            ),
            baseline_specs=[
                (1000,2000,'bridge',3),
                (3000,4000,'charlie',4),
                (4000,5000,'delta',5),
                (6000,7000,'echo',6),
            ],
        )
        report=materialize(**args,canonical_region=[1,3])
        self.assertEqual(report['canonical_region'],[1,3])
        cues=parse_srt_strict(args['output_dir']/'final.srt')
        self.assertEqual([(cue.start_ms,cue.end_ms,cue.text) for cue in cues],[
            (1000,2000,'bridge'),(3000,5000,'charlie delta'),(6000,7000,'echo'),
        ])
        with (args['output_dir']/'final.csv').open(encoding='utf-8-sig') as handle:
            rows=list(csv.DictReader(handle))
        row=next(row for row in rows if row['text']=='charlie delta')
        # ``canonical_region`` starts at timeline position 1, but the bound
        # canonical stream itself begins at index 0 (alpha).  The editor cue
        # crosses canonical indices 4 and 5, so its persisted offsets must be
        # absolute (alpha+bravo+chorus+bridge = 22), not local [0, 12).
        self.assertEqual((row['canonical_content_start'],row['canonical_content_end']),('22','34'))
        ranges=_cue_ranges(row,next(cue for cue in cues if cue.text=='charlie delta'),{
            0:'alpha',1:'bravo',2:'chorus',3:'bridge',4:'charlie',5:'delta',6:'echo',
        })
        self.assertEqual(ranges,[
            {'canonical_line_index':4,'start_char':0,'end_char':7},
            {'canonical_line_index':5,'start_char':0,'end_char':5},
        ])

        # The first materialization above emits a cross-line cue with a
        # complete absolute character span. Replaying the same region must
        # select that whole cue again and keep the already materialized SRT
        # byte-identical. The separate split-cue test below covers the case
        # where canonical_line_index is empty.
        first_output=args['output_dir']
        replay_args=dict(args,srt_path=first_output/'final.srt',
                         audit_path=first_output/'final.csv',
                         output_dir=first_output.parent/'region_replay')
        materialize(**replay_args,canonical_region=[1,3])
        self.assertEqual(
            (first_output/'final.srt').read_bytes(),
            (replay_args['output_dir']/'final.srt').read_bytes(),
        )
        replay_cues=parse_srt_strict(replay_args['output_dir']/'final.srt')
        self.assertEqual([(cue.start_ms,cue.end_ms,cue.text) for cue in replay_cues],[
            (1000,2000,'bridge'),(3000,5000,'charlie delta'),(6000,7000,'echo'),
        ])

    def test_region_replay_rejects_incomplete_or_partial_ownership(self):
        args=self.fixture(
            canonical_lrc_text=(
                '[00:00.00]alpha\n[00:01.00]bravo\n[00:02.00]chorus\n'
                '[00:03.00]bridge\n[00:04.00]charlie starts\n[00:05.00]delta\n'
                '[00:06.00]echo\n'
            ),
            timeline_lines=[
                dict(canonical_line_index=3,text='bridge',source_start_ms=3000,source_end_ms=4000,mix_start_ms=3000,mix_end_ms=4000),
                dict(canonical_line_index=4,text='charlie starts',source_start_ms=4000,source_end_ms=5000,mix_start_ms=4000,mix_end_ms=5000),
                dict(canonical_line_index=5,text='delta',source_start_ms=5000,source_end_ms=6000,mix_start_ms=5000,mix_end_ms=6000),
                dict(canonical_line_index=6,text='echo',source_start_ms=6000,source_end_ms=7000,mix_start_ms=6000,mix_end_ms=7000),
            ],
            source_srt_text=(
                '1\n00:00:01,000 --> 00:00:02,000\nunrelated opening\n\n'
                '2\n00:00:03,000 --> 00:00:04,000\ncharlie\n\n'
                '3\n00:00:04,000 --> 00:00:05,000\nstarts delta\n\n'
                '4\n00:00:06,000 --> 00:00:07,000\nunrelated closing\n'
            ),
            baseline_specs=[
                (1000,2000,'bridge',3),
                (3000,4000,'charlie',4),
                (4000,5000,'starts delta',5),
                (6000,7000,'echo',6),
            ],
        )
        materialize(**args,canonical_region=[1,3])
        first_output=args['output_dir']
        with (first_output/'final.csv').open(encoding='utf-8-sig',newline='') as handle:
            original_rows=list(csv.DictReader(handle))
        cross_line=next(row for row in original_rows if row['text']=='starts delta')
        self.assertEqual(cross_line['canonical_line_index'],'')
        self.assertEqual(cross_line['canonical_line_indices'],'[4, 5]')
        self.assertEqual(cross_line['canonical_content_start'],'29')
        self.assertEqual(cross_line['canonical_content_end'],'40')

        # A legacy one-line row may coexist with a newer split row. Its
        # single canonical_line_index is sufficient, while the split row must
        # still carry and validate its character span.
        mixed=first_output.parent/'mixed_legacy_replay'
        mixed.mkdir()
        mixed_rows=[dict(row) for row in original_rows]
        legacy=next(row for row in mixed_rows if row['text']=='charlie')
        legacy['canonical_content_start']=''
        legacy['canonical_content_end']=''
        with (mixed/'final.csv').open('w',encoding='utf-8-sig',newline='') as handle:
            writer=csv.DictWriter(handle,fieldnames=list(mixed_rows[0]))
            writer.writeheader();writer.writerows(mixed_rows)
        shutil.copy2(first_output/'final.srt',mixed/'final.srt')
        mixed_args=dict(args,srt_path=mixed/'final.srt',audit_path=mixed/'final.csv',
                        output_dir=mixed/'replay')
        materialize(**mixed_args,canonical_region=[1,3])
        self.assertEqual(
            (first_output/'final.srt').read_bytes(),
            (mixed_args['output_dir']/'final.srt').read_bytes(),
        )

        for label,mutate in (
            ('missing_end',lambda row: row.update(canonical_content_end='')),
            ('partial_span',lambda row: row.update(canonical_content_start='21')),
        ):
            with self.subTest(label=label):
                case=first_output.parent/('damaged_'+label)
                case.mkdir()
                damaged_rows=[dict(row) for row in original_rows]
                damaged=next(row for row in damaged_rows if row['text']=='starts delta')
                mutate(damaged)
                with (case/'final.csv').open('w',encoding='utf-8-sig',newline='') as handle:
                    writer=csv.DictWriter(handle,fieldnames=list(damaged_rows[0]))
                    writer.writeheader();writer.writerows(damaged_rows)
                shutil.copy2(first_output/'final.srt',case/'final.srt')
                replay_args=dict(args,srt_path=case/'final.srt',audit_path=case/'final.csv',
                                 output_dir=case/'replay')
                with self.assertRaisesRegex(ValueError,'canonical ownership'):
                    materialize(**replay_args,canonical_region=[1,3])
                self.assertFalse(replay_args['output_dir'].exists())

    def test_region_replay_rejects_overlapping_declared_content_spans(self):
        # Both rows individually match the repeated text, but the second row
        # reuses the first row's span. Per-row substring equality alone must
        # not turn duplicate ownership into a valid region.
        baseline=[Cue(1,0,100,'ha'),Cue(2,100,200,'ha')]
        rows=[
            dict(canonical_line_index='0',canonical_line_indices='[0]',
                 canonical_content_start='0',canonical_content_end='2'),
            dict(canonical_line_index='1',canonical_line_indices='[1]',
                 canonical_content_start='0',canonical_content_end='2'),
        ]
        selected_lines=[
            dict(canonical_line_index=0,text='ha'),
            dict(canonical_line_index=1,text='ha'),
        ]
        with self.assertRaisesRegex(ValueError,'content spans are not contiguous'):
            _select_region_target_positions(
                baseline=baseline,baseline_rows=rows,candidate_positions=[0,1],
                selected_lines=selected_lines,content_prefix=0,bound_stream='haha')

    def test_offsets_reject_discontinuous_bound_canonical_stream(self):
        args=self.fixture(
            canonical_lrc_text='[00:00.00]alpha\n[00:01.00]bravo\n[00:02.00]charlie\n[00:03.00]delta\n',
            timeline_lines=[
                dict(canonical_line_index=1,text='bravo',source_start_ms=1000,source_end_ms=2000,mix_start_ms=1000,mix_end_ms=2000),
                dict(canonical_line_index=3,text='delta',source_start_ms=3000,source_end_ms=4000,mix_start_ms=3000,mix_end_ms=4000),
            ],
            source_srt_text='1\n00:00:01,000 --> 00:00:03,000\nbravo delta\n',
            baseline_specs=[(1000,3000,'bravo delta',1)],
        )
        with self.assertRaisesRegex(ValueError,'continuous bound interval'):
            materialize(**args)
        self.assertFalse(args['output_dir'].exists())

    def fixture(self, nonlexical=False, window=(0,10000), source_srt_text=None,
                canonical_lrc_text=None,timeline_lines=None,baseline_specs=None):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        root=Path(temporary.name);task=root/'private'/'fixture';inputs=task/'input';lyrics=inputs/'lyrics';sources=inputs/'sources'
        for p in (lyrics,sources,task/'qa'):p.mkdir(parents=True,exist_ok=True)
        source=inputs/'source.srt';source.write_text(source_srt_text or "1\n00:00:01,000 --> 00:00:03,000\ndeep than i've ever known\n",encoding='utf-8')
        if nonlexical:
            extra="129\n00:00:10,000 --> 00:00:11,000\n'\n"
            original=source.read_text(encoding='utf-8')
            source.write_text(extra+'\n'+original if nonlexical=='first' else original+'\n'+extra,encoding='utf-8')
        audio=inputs/'mix.wav';audio.write_bytes(b'fixture audio')
        songs=inputs/'songs.txt';songs.write_text('00:00 Artist - Signal\n',encoding='utf-8')
        (sources/'Artist - Signal.wav').write_bytes(b'fixture audio')
        text='Deeper than I’ve ever known'
        (lyrics/'Artist - Signal.lrc').write_text(canonical_lrc_text or '[00:02.00]'+text+'\n',encoding='utf-8')
        manifest=build_task_manifest(root,'fixture',source_srt=source,
            audio=audio,song_list=songs,lyrics_dir=lyrics,source_audio_dir=sources)
        mp=task/'qa/task_manifest.json';write_json_atomic(mp,manifest);fingerprint=manifest['task_fingerprint_sha256']
        def artifact(payload,name,stage,role,upstreams=()):
            path=root/name;write_json_atomic(path,payload)
            data=build_artifact_manifest(task_fingerprint_sha256=fingerprint,algorithm_version=__version__,
                stage=stage,outputs=((role,path),),upstream_artifact_ids=upstreams)
            ap=root/(name+'.artifact.json');write_json_atomic(ap,data);return path,ap,data
        assets=resolve_assets(song_list=songs,lyrics_dir=lyrics,source_audio_dir=sources)
        assets['task_fingerprint_sha256']=fingerprint
        assetp,assetap,_=artifact(assets,'assets.json','asset_resolution','track_assets')
        binding=bindings_from_payload(assets)[0];oid=binding.occurrence_id
        timeline=dict(algorithm_version=__version__,task_fingerprint_sha256=fingerprint,
            result=dict(occurrence_id=oid,track_id=binding.track_id,ordinal=1,artist='Artist',title='Signal',
                canonical_selection_sha256=binding.canonical_selection_sha256,
                window=dict(start_ms=window[0],end_ms=window[1]),
                lines=timeline_lines or [dict(canonical_line_index=0,text=text,source_start_ms=2000,source_end_ms=4000,mix_start_ms=5000,mix_end_ms=7000)]))
        tp,tap,ta=artifact(timeline,'timeline.json','canonical_timeline_projection','canonical_timeline')
        run=dict(task_fingerprint_sha256=fingerprint,algorithm_version=__version__,occurrences=[dict(occurrence_id=oid,
            timeline_path=str(tp),timeline_artifact_path=str(tap))])
        rp,rap,_=artifact(run,'run.json','production_orchestration','v4_production_run',(ta['artifact_id'],))
        srt=root/'baseline.srt'
        audit=root/'baseline.csv'
        baseline_specs=baseline_specs or [(5000,7000,text,0)]
        with audit.open('w',encoding='utf-8',newline='') as handle:
            rows=[dict(start_ms=start_ms,end_ms=end_ms,text=cue_text,canonical_line_index=canonical_line_index,
                       occurrence_id=oid,task_fingerprint_sha256=fingerprint,boundary_authority='old_authority_must_not_copy')
                  for start_ms,end_ms,cue_text,canonical_line_index in baseline_specs]
            writer=csv.DictWriter(handle,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
        from v4_materialize_calibrated_alignment import _write_srt
        _write_srt(srt,rows)
        return dict(manifest_path=mp,srt_path=srt,audit_path=audit,run_path=rp,run_artifact_path=rap,
            assets_path=assetp,assets_artifact_path=assetap,occurrence_id=oid,output_dir=root/'result')

    def test_nonlexical_editor_cue_does_not_block_other_occurrence(self):
        args=self.fixture(nonlexical=True)
        with args['audit_path'].open(encoding='utf-8') as f: row=next(csv.DictReader(f))
        outside=dict(row,start_ms=10000,end_ms=11000,text="'",occurrence_id='unselected')
        with args['audit_path'].open('w',encoding='utf-8',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=list(row));writer.writeheader();writer.writerows([row,outside])
        from v4_materialize_calibrated_alignment import _write_srt
        _write_srt(args['srt_path'],[row,outside])
        report=materialize(**args)
        retained=parse_srt_strict(args['output_dir']/'final.srt')[1]
        self.assertEqual((retained.start_ms,retained.end_ms,retained.text),(10000,11000,"'"))
        self.assertEqual(parse_srt_strict(args['output_dir']/'final.srt')[0].start_ms,1000)
        self.assertEqual(report['smart_input_preparation']['nonlexical_source_cue_numbers'],[129])
        self.assertEqual(report['smart_input_preparation']['lexical_to_source_ordinals'],[0])
        source=args['manifest_path'].parent.parent/'input/source.srt'
        self.assertEqual(parse_srt_strict(source)[1].text,"'")

    def test_filtered_input_ordinals_map_suffix_decision_to_original_cue(self):
        args=self.fixture(nonlexical='first');report=materialize(**args)
        self.assertEqual(report['smart_input_preparation']['lexical_to_source_ordinals'],[1])
        self.assertEqual(report['text_corrections'][0]['original_cue'],1)
        self.assertEqual(parse_srt_strict(args['output_dir']/'final.srt')[0].text,'Deeper than I’ve ever known')

    def test_real_lineage_restores_editor_and_rebuilds_artifact(self):
        args=self.fixture();report=materialize(**args);out=args['output_dir']
        cue=parse_srt_strict(out/'final.srt')[0]
        self.assertEqual((cue.start_ms,cue.end_ms),(1000,3000))
        self.assertEqual(cue.text,'Deeper than I’ve ever known')
        self.assertEqual(len(report['text_corrections']),1)
        with (out/'final.csv').open(encoding='utf-8-sig') as handle:row=next(csv.DictReader(handle))
        self.assertEqual(row['boundary_authority'],'')
        self.assertEqual(row['original_cue'],'1')
        self.assertEqual(row['canonical_line_index'],'0')
        artifact=json.loads((out/'preservation.artifact.json').read_text(encoding='utf-8'))
        for role,name in [('final_srt','final.srt'),('audit_csv','final.csv'),('preservation_report','preservation.json')]:
            self.assertEqual(validate_artifact_output(artifact,role=role,path=out/name),[])
        self.assertFalse(report['publish_ready'])

    def test_output_cannot_contain_immutable_source(self):
        args=self.fixture();args['output_dir']=args['manifest_path'].parent.parent
        with self.assertRaises((ValueError,FileExistsError)):materialize(**args)

    def test_single_upgrade_entry_executes_preservation(self):
        from v4_upgrade_subtitles import run_job
        args=self.fixture();root=args['output_dir'].parent
        config={key:str(args[key+'_path']) for key in ('run','run_artifact','assets','assets_artifact')}
        config['occurrence_id']=args['occurrence_id']
        job=dict(schema_version='subtitle-upgrade-job-1.0',task_manifest=str(args['manifest_path']),
            srt=str(args['srt_path']),report=str(args['audit_path']),editor_preservation=config)
        jp=root/'job.json';write_json_atomic(jp,job)
        report=run_job(jp,args['output_dir'])
        self.assertFalse(report['publish_ready'])
        self.assertEqual(report['new_human_annotations'],0)
        self.assertEqual(parse_srt_strict(args['output_dir']/'final.srt')[0].start_ms,1000)
        artifact=json.loads((args['output_dir']/'preservation.artifact.json').read_text(encoding='utf-8'))
        self.assertEqual(validate_artifact_output(artifact,role='final_srt',path=args['output_dir']/'final.srt'),[])

    def test_preservation_rejects_incompatible_locked_stage(self):
        from v4_upgrade_subtitles import run_job
        args=self.fixture();jp=args['output_dir'].parent/'job.json'
        write_json_atomic(jp,dict(schema_version='subtitle-upgrade-job-1.0',task_manifest=str(args['manifest_path']),
            srt=str(args['srt_path']),report=str(args['audit_path']),editor_preservation={},gap_review={'lock':'locked'}))
        with self.assertRaisesRegex(ValueError,'own job'):run_job(jp,args['output_dir'])
        self.assertFalse(args['output_dir'].exists())

    def test_changed_bound_lyrics_fail_before_creating_output(self):
        args=self.fixture();lyrics=args['manifest_path'].parent.parent/'input/lyrics/Artist - Signal.lrc'
        lyrics.write_text('[00:02.00]different words entirely\n',encoding='utf-8')
        with self.assertRaises(ValueError):materialize(**args)
        self.assertFalse(args['output_dir'].exists())


if __name__=='__main__':unittest.main()
