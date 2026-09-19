import unittest
from unittest.mock import Mock
from lyric_aligner.audio.adjacent_transition_adjudication import adjudicate_transition, AdjudicationError
from lyric_aligner.audio.features import RetrievalResult, RetrievalCandidate
from lyric_aligner.timeline.projector import source_time_at_mix

def fake(score=0.9, margin=0.2):
    c=RetrievalCandidate(0,1,0,1,score,score,score,2)
    return RetrievalResult(0,6,3,c,None,(c,),margin,False,0.72,0.012)
def mapping(offset): return {"intercept":offset,"base_slope":1.0,"breakpoints":[],"slope_deltas":[]}
class TestAdjudicator(unittest.TestCase):
    def base(self):
        issue={"kind":"transition_ambiguity","code":"ambiguous_source_occurrence","candidate_id":"i","left_occurrence_id":"a","right_occurrence_id":"b","interval_start":0,"interval_end":12}
        tr={"left_occurrence_id":"a","right_occurrence_id":"b","nominal_boundary":6}
        bundle=Mock(duration_seconds=30, frame_seconds=1.0, frame_count=30, sr=1, hop_length=1)
        return issue,tr,bundle
    def test_clear_requires_expected_crossing_and_ignores_global_competitor(self):
        issue,tr,b=self.base(); calls=[]
        def retrieve(*args,**kw): calls.append(kw["source_search_start"]); return fake()
        result=adjudicate_transition(issue=issue,transition=tr,left_mapping=mapping(0),right_mapping=mapping(-10),mix_features=b,left_source_features=b,right_source_features=b,retrieve=retrieve)
        self.assertEqual(result["recommendation"]["action"],"review")
    def test_simultaneous_is_review(self):
        issue,tr,b=self.base(); result=adjudicate_transition(issue=issue,transition=tr,left_mapping=mapping(0),right_mapping=mapping(0),mix_features=b,left_source_features=b,right_source_features=b,retrieve=lambda *a,**k: fake())
        self.assertEqual(result["recommendation"]["action"],"review"); self.assertTrue(result["activity_state"]["simultaneous_expected_support"])
    def test_mapping_blocked_is_fail_closed_at_boundary(self):
        issue,tr,b=self.base()
        with self.assertRaises(Exception):
            adjudicate_transition(issue=issue,transition=tr,left_mapping={"intercept":0},right_mapping=mapping(0),mix_features=b,left_source_features=b,right_source_features=b)
    def test_issue_pair_mismatch(self):
        issue,tr,b=self.base(); issue["right_occurrence_id"]="wrong"
        with self.assertRaises(AdjudicationError): adjudicate_transition(issue=issue,transition=tr,left_mapping=mapping(0),right_mapping=mapping(0),mix_features=b,left_source_features=b,right_source_features=b)
if __name__=='__main__': unittest.main()
