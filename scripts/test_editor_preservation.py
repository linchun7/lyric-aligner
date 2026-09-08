import unittest
from dataclasses import replace
from lyric_aligner.srt import Cue
from lyric_aligner.timeline.editor_preservation import canonical_editor_cues,resolve_single_cue_suffix,canonical_editor_regions


class EditorPreservationTests(unittest.TestCase):
    def test_local_regions_leave_missing_middle_unassigned(self):
        cues=[Cue(i,i*1000,(i+1)*1000,t) for i,t in enumerate(['hello world','wrong','last word'])]
        regions=canonical_editor_regions(cues,['hello','world','missing','last word'])
        self.assertEqual(regions,[dict(editor_cue_range=[0,1],canonical_line_range=[0,2]),
                                  dict(editor_cue_range=[2,3],canonical_line_range=[3,4])])

    def test_repeated_region_cannot_choose_first_chorus(self):
        self.assertEqual(canonical_editor_regions([Cue(1,0,1000,'hello world')],
                                                  ['hello world','other','hello world']),[])

    def test_region_must_end_on_both_existing_boundaries(self):
        self.assertEqual(canonical_editor_regions([Cue(1,0,1000,'hello world extra')],
                                                  ['hello world']),[])

    def test_repairs_layout_without_changing_cue_topology_or_time(self):
        source=[Cue(9,1000,2000,'helloWorld'),Cue(10,2100,3200,'again')]
        result,owners=canonical_editor_cues(source,source,['Hello world again!'])
        self.assertEqual(result,[replace(source[0],text='Hello world'),replace(source[1],text='again!')])
        self.assertEqual([r['original_cue'] for r in owners],[9,10])
        self.assertEqual(owners[0]['canonical_line_index'],0)
        self.assertIsNone(owners[1]['canonical_line_index'])
        self.assertEqual(owners[1]['canonical_line_indices'],[0])

    def test_missing_or_reordered_lyrics_cannot_be_allocated_to_editor(self):
        cues=[Cue(1,0,1000,'hello'),Cue(2,1000,2000,'again')]
        for text in ('hello world again','again hello','hello hello again'):
            with self.subTest(text=text),self.assertRaises(ValueError):
                canonical_editor_cues(cues,cues,[text])

    def test_timing_repair_cannot_be_laundered_as_immutable_editor(self):
        source=[Cue(1,100,1000,'hello')]
        with self.assertRaisesRegex(ValueError,'timing'):
            canonical_editor_cues(source,[replace(source[0],start_ms=200)],['hello'])

    def test_single_bound_suffix_requires_other_words_and_same_count(self):
        self.assertEqual(resolve_single_cue_suffix("deep than i've ever known","Deeper than I’ve ever known"),"Deeper than I’ve ever known")
        for a,b in [('I lov you','I love you'),('deep than ever known','deep er than ever known'),
                    ('deep than never known','deeper than ever known'),('deep than ever known','deeply than ever forgotten')]:
            with self.subTest(a=a,b=b):self.assertIsNone(resolve_single_cue_suffix(a,b))


if __name__=='__main__':unittest.main()
