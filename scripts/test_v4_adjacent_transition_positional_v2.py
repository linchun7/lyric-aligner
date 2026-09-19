import copy
import unittest
from unittest.mock import Mock
from lyric_aligner.audio.adjacent_transition_positional_v2 import adjudicate_transition, PositionalEvidenceError, _support, Policy
from lyric_aligner.audio.features import RetrievalCandidate, RetrievalResult

def mapping(offset=0): return {"intercept": offset, "base_slope": 1.0, "breakpoints": [], "slope_deltas": []}
def fine(offset=0, status="refined"):
    p=[{"mix_center": 0, "refined_source_center": offset, "fused_score": .9, "margin": .02, "feature_agreement": 2, "ambiguous": False}, {"mix_center": 6, "refined_source_center": 6+offset, "fused_score": .9, "margin": .02, "feature_agreement": 2, "ambiguous": False}, {"mix_center": 12, "refined_source_center": 12+offset, "fused_score": .9, "margin": .02, "feature_agreement": 2, "ambiguous": False}]
    return {"result":{"status":status,"path":p,"timewarp":{"blocked":False,"mapping":mapping(offset)}}}
def result(source=6, score=.9):
    c=RetrievalCandidate(source,source+3,source+1.5,1,score,score,score,2)
    return RetrievalResult(0,3,1.5,c,None,(c,),.2,False,.72,.012)
class TestPositionalV2(unittest.TestCase):
    def base(self):
        issue={"kind":"transition_ambiguity","code":"ambiguous_source_occurrence","left_occurrence_id":"l","right_occurrence_id":"r","interval_start":0,"interval_end":12}
        transition={"left_occurrence_id":"l","right_occurrence_id":"r","nominal_boundary":6}
        b=Mock(duration_seconds=30,sr=1,hop_length=1,frame_seconds=1,frame_count=30)
        return issue,transition,b
    def test_global_centres_and_full_search_range(self):
        issue,tr,b=self.base(); calls=[]
        def retrieve(*a,**kw): calls.append(kw); return result()
        adjudicate_transition(issue=issue,transition=tr,left_fine=fine(),right_fine=fine(),mix_features=b,left_source_features=b,right_source_features=b,retrieve=retrieve)
        self.assertTrue(any(abs(c["mix_start"]+1.5-6)<1e-6 for c in calls))
        self.assertTrue(all(c["source_search_end"]-c["source_search_start"] > 0 for c in calls))
        center_calls = [c for c in calls if abs(c["mix_start"] + 1.5 - 6) < 1e-6]
        self.assertTrue(center_calls)
        # search_end is the right boundary of the full source interval, not an
        # already query-length-adjusted candidate-start ceiling.
        self.assertTrue(all(c["source_search_end"] - c["source_search_start"] >= 7.0 for c in center_calls))
    def test_stale_or_missing_fine_abstains(self):
        issue,tr,b=self.base()
        with self.assertRaises(PositionalEvidenceError): adjudicate_transition(issue=issue,transition=tr,left_fine=fine(status="review_required"),right_fine=fine(),mix_features=b,left_source_features=b,right_source_features=b)
    def test_simultaneous_support_is_not_clear(self):
        issue,tr,b=self.base(); out=adjudicate_transition(issue=issue,transition=tr,left_fine=fine(),right_fine=fine(),mix_features=b,left_source_features=b,right_source_features=b,retrieve=lambda *a,**k: result())
        self.assertNotEqual(out["recommendation"]["action"],"clear_sequential_advisory")
    def test_one_side_near_boundary_is_not_overlap(self):
        issue,tr,b=self.base()
        calls = []
        def retrieve(*a,**kw):
            calls.append(kw)
            return result(source=6 if len(calls) % 2 else 20)
        out=adjudicate_transition(issue=issue,transition=tr,left_fine=fine(),right_fine=fine(),mix_features=b,left_source_features=b,right_source_features=b,retrieve=retrieve)
        self.assertFalse(any(p["overlap_like"] for p in out["paired_evidence"]))
    def test_residual_and_search_edge_reject_strong(self):
        issue,tr,b=self.base()
        out=adjudicate_transition(issue=issue,transition=tr,left_fine=fine(),right_fine=fine(),mix_features=b,left_source_features=b,right_source_features=b,retrieve=lambda *a,**k: result(source=k["source_search_start"]+3))
        self.assertTrue(any("residual_exceeds_policy" in r.get("strong_rejection_reasons",[]) for r in out["windows"]))
    def test_support_uses_selected_point_global_mix(self):
        path = [
            {"mix_center": 1.0, "global_mix_center": 101.0, "source_center": 1.0, "fused_score": .9, "margin": .02, "feature_agreement": 2, "ambiguous": False},
            {"mix_center": 2.0, "global_mix_center": 102.0, "source_center": 2.0, "fused_score": .9, "margin": .02, "feature_agreement": 2, "ambiguous": False},
        ]
        out = _support(path, 101.5, "left", Policy())
        self.assertEqual(out["mix_center"], 1.0)
        self.assertEqual(out["global_mix_center"], 101.0)
        self.assertEqual(out["point"], path[0])
    def test_nonzero_global_offset_drives_pre_post_and_support(self):
        issue, tr, b = self.base()
        offset = 100.0
        left = fine(); right = fine()
        for payload in (left, right):
            for point in payload["result"]["path"]:
                point["global_mix_center"] = point["mix_center"] + offset
        tr["nominal_boundary"] = 106.0
        issue["interval_start"], issue["interval_end"] = 100.0, 112.0
        out = adjudicate_transition(issue=issue, transition=tr, left_fine=left, right_fine=right,
            mix_features=b, left_source_features=b, right_source_features=b,
            mix_feature_global_start_seconds=offset, retrieve=lambda *a, **k: result())
        self.assertTrue(any(row["global_mix_center"] < 106.0 for row in out["windows"]))
        self.assertTrue(any(row["global_mix_center"] > 106.0 for row in out["windows"]))
        self.assertEqual(out["recommendation"]["action"], "unresolved")
    def test_production_fine_centers_are_already_global(self):
        issue, tr, b = self.base()
        offset = 100.0
        left = fine(); right = fine()
        for payload in (left, right):
            for point in payload["result"]["path"]:
                point["mix_center"] += offset
        tr["nominal_boundary"] = 106.0
        issue["interval_start"], issue["interval_end"] = 100.0, 112.0
        out = adjudicate_transition(
            issue=issue, transition=tr, left_fine=left, right_fine=right,
            mix_features=b, left_source_features=b, right_source_features=b,
            mix_feature_global_start_seconds=offset, retrieve=lambda *a, **k: result(),
        )
        self.assertEqual(out["support"][0]["global_mix_center"], 106.0)
        self.assertEqual(out["support"][1]["global_mix_center"], 106.0)
    def test_adjudication_does_not_mutate_fine_payload(self):
        issue, tr, b = self.base()
        left = fine(); right = fine()
        left_before = copy.deepcopy(left)
        right_before = copy.deepcopy(right)
        adjudicate_transition(
            issue=issue, transition=tr, left_fine=left, right_fine=right,
            mix_features=b, left_source_features=b, right_source_features=b,
            retrieve=lambda *a, **k: result(),
        )
        self.assertEqual(left, left_before)
        self.assertEqual(right, right_before)
    def test_clear_requires_support_outside_boundary_tolerance(self):
        issue, tr, b = self.base()
        calls = 0
        policy = Policy(window_offsets=(-1.0, 1.0), boundary_tolerance_seconds=1.5)
        def retrieve(*args, **kwargs):
            nonlocal calls
            calls += 1
            strong = calls in {1, 4}
            source_start = (kwargs["source_search_start"] + kwargs["source_search_end"]) / 2 - 1.5
            return result(source=source_start, score=.9 if strong else .5)
        out = adjudicate_transition(
            issue=issue, transition=tr, left_fine=fine(), right_fine=fine(),
            mix_features=b, left_source_features=b, right_source_features=b,
            retrieve=retrieve, policy=policy,
        )
        self.assertNotEqual(out["recommendation"]["action"], "clear_sequential_advisory")
    def test_source_edge_missing_window_abstains_instead_of_raising(self):
        issue = {"kind":"transition_ambiguity","code":"ambiguous_source_occurrence","left_occurrence_id":"l","right_occurrence_id":"r","interval_start":21,"interval_end":33}
        transition = {"left_occurrence_id":"l","right_occurrence_id":"r","nominal_boundary":27}
        points = [
            {"mix_center":21,"refined_source_center":21,"fused_score":.9,"margin":.02,"feature_agreement":2,"ambiguous":False},
            {"mix_center":27,"refined_source_center":27,"fused_score":.9,"margin":.02,"feature_agreement":2,"ambiguous":False},
            {"mix_center":30,"refined_source_center":30,"fused_score":.9,"margin":.02,"feature_agreement":2,"ambiguous":False},
        ]
        payload = {"result":{"status":"refined","path":points,"timewarp":{"blocked":False,"mapping":mapping(0)}}}
        b = Mock(duration_seconds=30,sr=1,hop_length=1,frame_seconds=1,frame_count=30)
        out = adjudicate_transition(issue=issue,transition=transition,left_fine=payload,right_fine=payload,mix_features=b,left_source_features=b,right_source_features=b,retrieve=lambda *a,**k: result())
        self.assertTrue(any(row.get("error") == "search_range_cannot_contain_query" for row in out["windows"]))
        self.assertFalse(out["timing_mutation_performed"])
if __name__ == "__main__": unittest.main()
