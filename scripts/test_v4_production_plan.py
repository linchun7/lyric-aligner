import unittest

from lyric_aligner.assets.bindings import CanonicalOriginal, ResolvedAssetBinding
from lyric_aligner.pipeline.production import build_production_plan, readiness_status


def binding(ordinal: int, start_ms: int) -> ResolvedAssetBinding:
    return ResolvedAssetBinding(
        ordinal=ordinal,
        occurrence_id=f"occ-{ordinal}",
        track_id=f"track-{ordinal}",
        artist="Artist",
        title=f"Song {ordinal}",
        version_id=f"version-{ordinal}",
        nominal_start_ms=start_ms,
        middle_cut="false",
        language_profile="auto",
        source_audio_path=f"/tmp/source-{ordinal}.wav",
        source_audio_sha256="a" * 64,
        canonical_lyric_path=f"/tmp/song-{ordinal}.lrc",
        canonical_lyric_sha256="b" * 64,
        canonical_selection_sha256="c" * 64,
        canonical_originals=(CanonicalOriginal(1000, 0, "line"),),
    )


class V4ProductionPlanTests(unittest.TestCase):
    def test_primary_intervals_remain_ordered_but_transition_search_overlaps_boundary(self):
        plan = build_production_plan(
            [binding(1, 0), binding(2, 30000), binding(3, 60000)],
            mix_duration=90.0,
            transition_margin_seconds=10.0,
        )
        self.assertEqual(
            [(row.primary_start, row.primary_end) for row in plan.occurrences],
            [(0.0, 30.0), (30.0, 60.0), (60.0, 90.0)],
        )
        first = plan.transitions[0]
        self.assertEqual(first.nominal_boundary, 30.0)
        self.assertEqual((first.search_start, first.search_end), (20.0, 40.0))
        self.assertLess(first.search_start, plan.occurrences[0].primary_end)
        self.assertGreater(first.search_end, plan.occurrences[1].primary_start)

    def test_transition_margin_clamps_to_mix_edges(self):
        plan = build_production_plan(
            [binding(1, 0), binding(2, 5000)],
            mix_duration=12.0,
            transition_margin_seconds=10.0,
        )
        transition = plan.transitions[0]
        self.assertEqual(transition.search_start, 0.0)
        self.assertEqual(transition.search_end, 12.0)

    def test_readiness_never_falls_back_to_legacy(self):
        self.assertEqual(readiness_status(issues=[]), "ready_for_render")
        self.assertEqual(
            readiness_status(issues=[{"kind": "timewarp", "status": "review"}]),
            "review_required",
        )


if __name__ == "__main__":
    unittest.main()
