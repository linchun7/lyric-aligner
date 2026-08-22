import unittest

from lyric_aligner.srt import Cue, cue_id, text_sha256
from lyric_aligner.timeline.editor_cue_reconcile import (
    CanonicalCueEvidence,
    EditorCueReconciliationError,
    canonical_evidence_from_audit,
    evaluate_editor_cue_reconciliation,
)


def canonical(
    position: int,
    start_ms: int,
    end_ms: int,
    text: str,
    *,
    occurrence_id: str = "occ-1",
    track_id: str = "track-1",
    ordinal: int = 1,
    line_index: int | None = None,
) -> CanonicalCueEvidence:
    cue = Cue(position, start_ms, end_ms, text)
    return CanonicalCueEvidence(
        position=position,
        cue_number=position,
        start_ms=start_ms,
        end_ms=end_ms,
        text=text,
        occurrence_id=occurrence_id,
        track_id=track_id,
        ordinal=ordinal,
        canonical_line_index=position - 1 if line_index is None else line_index,
        timing_format="line_lrc",
        end_basis="synthetic",
        cue_id=cue_id(position, cue),
        text_sha256=text_sha256(text),
    )


class EditorCueReconciliationTests(unittest.TestCase):
    def test_unique_containment_resolves_without_granting_authority(self):
        editor = [
            Cue(10, 0, 3000, "editor alpha"),
            Cue(20, 3000, 6000, "editor beta"),
        ]
        result = evaluate_editor_cue_reconciliation(
            editor,
            [
                canonical(1, 500, 2500, "canonical alpha"),
                canonical(2, 3500, 5500, "canonical beta"),
            ],
        )
        self.assertEqual(result["status_counts"]["resolved"], 2)
        self.assertEqual(result["canonical_unassigned_count"], 0)
        self.assertTrue(result["full_topology_candidate"])
        self.assertFalse(result["production_authority_granted"])
        self.assertEqual(
            result["segmentation_authority"],
            "editor_reconciliation_evaluation_only",
        )
        self.assertTrue(result["editor_cues"][0]["text_changed"])

    def test_multiple_nonoverlapping_canonical_cues_may_share_one_editor_cue(self):
        result = evaluate_editor_cue_reconciliation(
            [Cue(1, 0, 6000, "combined editor cue")],
            [
                canonical(1, 500, 2000, "first canonical line"),
                canonical(2, 2500, 5000, "second canonical line"),
            ],
        )
        row = result["editor_cues"][0]
        self.assertEqual(row["status"], "resolved")
        self.assertEqual(row["canonical_cue_count"], 2)
        self.assertEqual(len(row["canonical_refs"]), 2)
        self.assertTrue(result["full_topology_candidate"])

    def test_canonical_crossing_editor_boundary_stays_review(self):
        result = evaluate_editor_cue_reconciliation(
            [
                Cue(1, 0, 3000, "left"),
                Cue(2, 3000, 6000, "right"),
            ],
            [canonical(1, 2000, 4000, "crossing")],
        )
        self.assertEqual(result["status_counts"]["still_review"], 2)
        self.assertEqual(result["canonical_unassigned_count"], 1)
        self.assertEqual(
            result["canonical_unassigned"][0]["reason"],
            "canonical_interval_crosses_editor_boundary",
        )
        self.assertFalse(result["full_topology_candidate"])

    def test_canonical_inside_two_overlapping_editor_cues_is_ambiguous(self):
        result = evaluate_editor_cue_reconciliation(
            [
                Cue(1, 0, 5000, "layer one"),
                Cue(2, 1000, 6000, "layer two"),
            ],
            [canonical(1, 2000, 3000, "ambiguous")],
        )
        self.assertEqual(result["status_counts"]["still_review"], 2)
        self.assertEqual(
            result["canonical_unassigned"][0]["reason"],
            "ambiguous_overlapping_editor_ownership",
        )

    def test_overlapping_canonical_material_inside_one_editor_cue_stays_review(self):
        result = evaluate_editor_cue_reconciliation(
            [Cue(1, 0, 6000, "one editor cue")],
            [
                canonical(1, 1000, 4000, "layer A", occurrence_id="occ-a"),
                canonical(2, 2500, 5000, "layer B", occurrence_id="occ-b", ordinal=2),
            ],
        )
        row = result["editor_cues"][0]
        self.assertEqual(row["status"], "still_review")
        self.assertIn("canonical_overlap_inside_editor_cue", row["reason"])
        self.assertEqual(result["canonical_assigned_count"], 2)
        self.assertFalse(result["full_topology_candidate"])

    def test_editor_cue_without_canonical_evidence_is_not_evaluable(self):
        result = evaluate_editor_cue_reconciliation(
            [
                Cue(1, 0, 3000, "covered"),
                Cue(2, 7000, 9000, "editor only"),
            ],
            [canonical(1, 500, 2500, "covered canonical")],
        )
        self.assertEqual(result["status_counts"]["resolved"], 1)
        self.assertEqual(result["status_counts"]["not_evaluable"], 1)
        self.assertFalse(result["full_topology_candidate"])

    def test_nonmonotonic_editor_file_order_blocks_full_candidate_only(self):
        result = evaluate_editor_cue_reconciliation(
            [
                Cue(1, 4000, 6000, "later first"),
                Cue(2, 0, 3000, "earlier second"),
            ],
            [
                canonical(1, 4500, 5500, "later"),
                canonical(2, 500, 2500, "earlier"),
            ],
        )
        self.assertEqual(result["status_counts"]["resolved"], 2)
        self.assertFalse(result["editor_file_order_monotonic"])
        self.assertIn("editor_file_order_nonmonotonic", result["global_issues"])
        self.assertFalse(result["full_topology_candidate"])

    def test_audit_builder_requires_exact_render_identity(self):
        cue = Cue(1, 1000, 2000, "generic canonical")
        row = {
            "position": "1",
            "cue_number": "1",
            "start_ms": "1000",
            "end_ms": "2000",
            "text": cue.text,
            "occurrence_id": "occ-1",
            "track_id": "track-1",
            "ordinal": "1",
            "canonical_line_index": "0",
            "timing_format": "line_lrc",
            "end_basis": "synthetic",
            "cue_id": cue_id(1, cue),
            "text_sha256": text_sha256(cue.text),
        }
        built = canonical_evidence_from_audit([cue], [row])
        self.assertEqual(built[0].occurrence_id, "occ-1")

        row["cue_id"] = "wrong"
        with self.assertRaisesRegex(EditorCueReconciliationError, "invalid cue_id"):
            canonical_evidence_from_audit([cue], [row])

    def test_empty_inputs_fail_closed(self):
        with self.assertRaisesRegex(EditorCueReconciliationError, "editor SRT has no cues"):
            evaluate_editor_cue_reconciliation([], [canonical(1, 0, 1000, "line")])
        with self.assertRaisesRegex(
            EditorCueReconciliationError,
            "canonical evaluation render has no cues",
        ):
            evaluate_editor_cue_reconciliation([Cue(1, 0, 1000, "line")], [])


if __name__ == "__main__":
    unittest.main()
