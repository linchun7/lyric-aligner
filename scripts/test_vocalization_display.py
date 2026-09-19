import copy
import unittest

from lyric_aligner.timeline.vocalization_display import group_display_rows


class VocalizationDisplayTests(unittest.TestCase):
    def fixture(self):
        rows=[dict(track='track',lrc_indices=str(i),text='Na na na',start_ms=1000+i*2000,end_ms=2800+i*2000) for i in range(4)]
        records=[dict(rows[0],human_confirmed=True,presence='present',start_ms=1000,end_ms=8800)]
        return rows,records

    def test_lossless_group_inherits_only_original_outer_times(self):
        rows,records=self.fixture();before=copy.deepcopy(rows)
        after,groups=group_display_rows(rows,records)
        self.assertEqual(len(after),1)
        self.assertEqual(after[0],dict(start_ms=1000,end_ms=8800,text='\n'.join(r['text'] for r in rows)))
        self.assertEqual(after[0]['text'].casefold().split(),['na']*12)
        self.assertEqual(groups[0]['canonical_indices'],['0','1','2','3'])
        self.assertEqual(rows,before)

    def test_no_confirmation_or_uncertain_presence_does_not_fill_gaps(self):
        for key,value in [('human_confirmed',False),('presence','uncertain')]:
            rows,records=self.fixture();records[0][key]=value
            after,groups=group_display_rows(rows,records)
            self.assertEqual(len(after),4);self.assertEqual(groups,[])

    def test_lexical_repetitions_are_not_vocalization(self):
        rows,records=self.fixture()
        for r in rows+records:r['text']='please come back'
        self.assertEqual(group_display_rows(rows,records)[1],[])

    def test_next_real_lyric_is_not_swallowed(self):
        rows,records=self.fixture()
        rows.append(dict(track='track',lrc_indices='4',text='next lyric',start_ms=9000,end_ms=10000))
        after,groups=group_display_rows(rows,records)
        self.assertEqual(len(after),2);self.assertEqual(after[-1]['text'],'next lyric')

    def test_missing_canonical_line_breaks_group(self):
        rows,records=self.fixture();rows[1]['lrc_indices']='8'
        _,groups=group_display_rows(rows,records)
        self.assertFalse(any(1 in g['member_positions'] and 2 in g['member_positions'] for g in groups))

    def test_foreign_track_and_unreviewed_silence_are_not_joined(self):
        rows,records=self.fixture();rows[1]['track']='other'
        self.assertFalse(any(1 in g['member_positions'] for g in group_display_rows(rows,records)[1]))
        rows,records=self.fixture();records[0]['end_ms']=2800
        self.assertEqual(group_display_rows(rows,records)[1],[])

    def test_filling_gap_cannot_cover_unrelated_simultaneous_cue(self):
        rows,records=self.fixture()
        rows.append(dict(track='other',lrc_indices='0',text='other',start_ms=2850,end_ms=2950))
        _,groups=group_display_rows(rows,records)
        self.assertFalse(any(1 in g['member_positions'] for g in groups))

    def test_chinese_vocalization_preserves_each_character(self):
        rows,records=self.fixture()
        for r in rows+records:r['text']='啊啊'
        after,_=group_display_rows(rows,records)
        self.assertEqual(after[0]['text'].replace('\n',''),'啊'*8)


if __name__ == '__main__':
    unittest.main()
