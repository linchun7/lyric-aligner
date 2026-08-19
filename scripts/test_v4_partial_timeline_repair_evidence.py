from __future__ import annotations

import unittest

from lyric_aligner.srt import Cue
from lyric_aligner.timeline.partial_repair import (
    PartialTimelineRepairError,
    plan_partial_timeline_repair,
)
from lyric_aligner.timeline.partial_repair_evidence import (
    ExplicitCueTrust,
    bridge_fusion_to_partial_repair,
)


def fusion_line(
    *,
    occurrence: str,
    line_index: int,
    cue_number: int | None,
    boundary: tuple[int, int],
    shadow_level: str = "HIGH",
) -> dict:
    families = [
        {
            "family": "source_timeline",
            "available": True,
            "authoritative_for_primary_timing": True,
            "boundary_ms": list(boundary),
        }
    ]
    if cue_number is not None:
        families.append(
            {
                "family": "editor",
                "available": True,
                "authoritative_for_primary_timing": False,
                "boundary_ms": list(boundary),
                "cue_number": cue_number,
            }
        )
    return {
        "occurrence_id": occurrence,
        "track_id": "track-a",
        "ordinal": 0,
        "canonical_line_index": line_index,
        "canonical_text_sha256": f"hash-{line_index}",
        "source_timeline_boundary_ms": list(boundary),
        "shadow_level": shadow_level,
        "shadow_level_calibrated": False,
        "auxiliary_boundary_family_count": 1 if cue_number else 0,
        "families": families,
        "release_gate_eligible": False,
        "automatic_timing_change_allowed": False,
    }


def fusion_payload(lines: list[dict]) -> dict:
    return {
        "schema_version": "1.1",
        "policy_id": "evidence-fusion-shadow-test",
        "mode": "shadow_only",
        "policy_calibrated": False,
        "release_gate_eligible": False,
        "automatic_timing_change_allowed": False,
        "lines": lines,
        "authority": {
            "canonical_text": "canonical_lyrics_only",
            "primary_timing": "source_to_mix_only",
            "editor": "auxiliary_shadow_family",
            "asr": "auxiliary_shadow_family",
            "forced_alignment": "auxiliary_shadow_family_mix_time",
        },
    }


class PartialTimelineRepairEvidenceBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cues = [
            Cue(1, 1000, 2000, "第一句"),
            Cue(2, 2200, 3200, "第二句"),
            Cue(3, 3500, 4500, "第三句"),
        ]

    def test_high_shadow_never_auto_promotes_unknown_cue_to_trusted(self):
        trust, candidates, report = bridge_fusion_to_partial_repair(
            cues=self.cues,
            fusion=fusion_payload(
                [fusion_line(occurrence="occ-1", line_index=0, cue_number=1, boundary=(1000, 2000))]
            ),
            mapping_kind_by_occurrence={"occ-1": "AFFINE"},
            explicit_trust=[],
        )
        self.assertEqual(trust, [])
        self.assertEqual(candidates, [])
        self.assertEqual(report["bindings"][0]["explicit_trust_status"], "unknown")
        self.assertFalse(report["publish_ready"])

    def test_conflict_is_diagnostic_and_does_not_invent_untrusted_status(self):
        trust, candidates, report = bridge_fusion_to_partial_repair(
            cues=self.cues,
            fusion=fusion_payload(
                [
                    fusion_line(
                        occurrence="occ-1",
                        line_index=0,
                        cue_number=1,
                        boundary=(1000, 2000),
                        shadow_level="CONFLICT",
                    )
                ]
            ),
            mapping_kind_by_occurrence={"occ-1": "AFFINE"},
            explicit_trust=[],
        )
        self.assertEqual(trust, [])
        self.assertEqual(candidates, [])
        self.assertTrue(report["bindings"][0]["fusion_conflict"])
        self.assertEqual(report["bindings"][0]["explicit_trust_status"], "unknown")

    def test_explicit_human_untrusted_gets_source_timeline_candidate(self):
        trust, candidates, report = bridge_fusion_to_partial_repair(
            cues=self.cues,
            fusion=fusion_payload(
                [fusion_line(occurrence="occ-1", line_index=1, cue_number=2, boundary=(2400, 3300))]
            ),
            mapping_kind_by_occurrence={"occ-1": "PIECEWISE_RATE"},
            explicit_trust=[
                ExplicitCueTrust(1, "trusted", "checked"),
                ExplicitCueTrust(2, "untrusted", "late onset"),
                ExplicitCueTrust(3, "trusted", "checked"),
            ],
        )
        self.assertEqual([row.status for row in trust], ["trusted", "untrusted", "trusted"])
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate.position, 1)
        self.assertEqual(candidate.start_ms, 2400)
        self.assertEqual(candidate.end_ms, 3300)
        self.assertEqual(candidate.mapping_kind, "PIECEWISE_RATE")
        self.assertEqual(candidate.source, "source_to_mix")
        self.assertEqual(report["bindings"][1]["candidate_status"], "projected")

        plan = plan_partial_timeline_repair(self.cues, trust, candidates)
        self.assertEqual(plan["task_mode"], "hybrid")
        self.assertEqual(plan["decisions"][1]["action"], "propose_repair")
        self.assertFalse(plan["publish_ready"])

    def test_trusted_cue_does_not_get_candidate_even_if_p9_has_boundary(self):
        trust, candidates, report = bridge_fusion_to_partial_repair(
            cues=self.cues,
            fusion=fusion_payload(
                [fusion_line(occurrence="occ-1", line_index=0, cue_number=1, boundary=(1500, 2400))]
            ),
            mapping_kind_by_occurrence={"occ-1": "AFFINE"},
            explicit_trust=[ExplicitCueTrust(1, "trusted", "manual lock")],
        )
        self.assertEqual(len(trust), 1)
        self.assertEqual(candidates, [])
        self.assertEqual(report["bindings"][0]["candidate_status"], "not_requested")

    def test_multiple_canonical_lines_for_one_editor_cue_are_ambiguous(self):
        trust, candidates, report = bridge_fusion_to_partial_repair(
            cues=self.cues,
            fusion=fusion_payload(
                [
                    fusion_line(occurrence="occ-1", line_index=0, cue_number=2, boundary=(2100, 2600)),
                    fusion_line(occurrence="occ-1", line_index=1, cue_number=2, boundary=(2600, 3200)),
                ]
            ),
            mapping_kind_by_occurrence={"occ-1": "AFFINE"},
            explicit_trust=[ExplicitCueTrust(2, "untrusted", "bad segmentation")],
        )
        self.assertEqual(candidates, [])
        self.assertEqual(report["bindings"][1]["candidate_status"], "ambiguous")
        self.assertEqual(
            report["bindings"][1]["candidate_reason"],
            "multiple_canonical_lines_bound_to_same_editor_cue",
        )

    def test_missing_mapping_kind_does_not_guess_bpm_or_affine(self):
        _, candidates, report = bridge_fusion_to_partial_repair(
            cues=self.cues,
            fusion=fusion_payload(
                [fusion_line(occurrence="occ-1", line_index=1, cue_number=2, boundary=(2300, 3200))]
            ),
            mapping_kind_by_occurrence={},
            explicit_trust=[ExplicitCueTrust(2, "untrusted", "bad")],
        )
        self.assertEqual(candidates, [])
        self.assertEqual(report["bindings"][1]["candidate_status"], "unavailable")
        self.assertEqual(
            report["bindings"][1]["candidate_reason"],
            "missing_or_unsupported_occurrence_mapping_kind",
        )

    def test_cut_aware_without_confirmed_cut_identity_is_unavailable(self):
        _, candidates, report = bridge_fusion_to_partial_repair(
            cues=self.cues,
            fusion=fusion_payload(
                [fusion_line(occurrence="occ-1", line_index=1, cue_number=2, boundary=(2300, 3200))]
            ),
            mapping_kind_by_occurrence={"occ-1": "CUT_AWARE"},
            explicit_trust=[ExplicitCueTrust(2, "untrusted", "bad")],
        )
        self.assertEqual(candidates, [])
        self.assertEqual(report["bindings"][1]["candidate_status"], "unavailable")
        self.assertEqual(
            report["bindings"][1]["candidate_reason"],
            "cut_aware_mapping_requires_confirmed_cut_identity",
        )
        self.assertEqual(report["confirmed_cut_occurrence_count"], 0)

    def test_cut_aware_with_confirmed_cut_identity_can_emit_candidate(self):
        _, candidates, report = bridge_fusion_to_partial_repair(
            cues=self.cues,
            fusion=fusion_payload(
                [fusion_line(occurrence="occ-1", line_index=1, cue_number=2, boundary=(2300, 3200))]
            ),
            mapping_kind_by_occurrence={"occ-1": "CUT_AWARE"},
            explicit_trust=[ExplicitCueTrust(2, "untrusted", "bad")],
            confirmed_cut_occurrence_ids=["occ-1"],
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].mapping_kind, "CUT_AWARE")
        self.assertEqual(report["bindings"][1]["candidate_status"], "projected")
        self.assertEqual(report["confirmed_cut_occurrence_count"], 1)

    def test_open_end_one_ms_fusion_boundary_is_never_repair_candidate(self):
        _, candidates, report = bridge_fusion_to_partial_repair(
            cues=self.cues,
            fusion=fusion_payload(
                [fusion_line(occurrence="occ-1", line_index=1, cue_number=2, boundary=(5000, 5001))]
            ),
            mapping_kind_by_occurrence={"occ-1": "AFFINE"},
            explicit_trust=[ExplicitCueTrust(2, "untrusted", "bad")],
        )
        self.assertEqual(candidates, [])
        self.assertEqual(report["bindings"][1]["candidate_status"], "unavailable")
        self.assertEqual(
            report["bindings"][1]["candidate_reason"],
            "open_end_source_timeline_boundary_is_not_repairable",
        )

    def test_calibrated_policy_is_allowed_as_explicit_source_but_not_p9_high(self):
        trust, candidates, _ = bridge_fusion_to_partial_repair(
            cues=self.cues,
            fusion=fusion_payload(
                [fusion_line(occurrence="occ-1", line_index=1, cue_number=2, boundary=(2350, 3250))]
            ),
            mapping_kind_by_occurrence={"occ-1": "AFFINE"},
            explicit_trust=[
                ExplicitCueTrust(
                    2,
                    "untrusted",
                    "blind-locked policy result",
                    source="calibrated_policy",
                )
            ],
        )
        self.assertEqual(trust[0].status, "untrusted")
        self.assertEqual(len(candidates), 1)

    def test_wrong_fusion_authority_or_auto_mutation_claim_fails_closed(self):
        payload = fusion_payload([])
        payload["automatic_timing_change_allowed"] = True
        with self.assertRaises(PartialTimelineRepairError):
            bridge_fusion_to_partial_repair(
                cues=self.cues,
                fusion=payload,
                mapping_kind_by_occurrence={},
                explicit_trust=[],
            )

        payload = fusion_payload([])
        payload["authority"]["primary_timing"] = "editor"
        with self.assertRaises(PartialTimelineRepairError):
            bridge_fusion_to_partial_repair(
                cues=self.cues,
                fusion=payload,
                mapping_kind_by_occurrence={},
                explicit_trust=[],
            )

    def test_unknown_editor_cue_number_is_ignored_not_bound_to_wrong_cue(self):
        _, candidates, report = bridge_fusion_to_partial_repair(
            cues=self.cues,
            fusion=fusion_payload(
                [fusion_line(occurrence="occ-1", line_index=0, cue_number=99, boundary=(1000, 2000))]
            ),
            mapping_kind_by_occurrence={"occ-1": "AFFINE"},
            explicit_trust=[],
        )
        self.assertEqual(candidates, [])
        self.assertEqual(report["ignored_unbound_fusion_line_count"], 1)

    def test_duplicate_explicit_trust_rejected(self):
        with self.assertRaises(PartialTimelineRepairError):
            bridge_fusion_to_partial_repair(
                cues=self.cues,
                fusion=fusion_payload([]),
                mapping_kind_by_occurrence={},
                explicit_trust=[
                    ExplicitCueTrust(1, "trusted", "a"),
                    ExplicitCueTrust(1, "untrusted", "b"),
                ],
            )


if __name__ == "__main__":
    unittest.main()
