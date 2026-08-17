import json
import tempfile
import unittest
from pathlib import Path

from redo_karaoke_pipeline import (
    Cue,
    Track,
    apply_audio_edit_reviews,
    apply_global_sequence_repairs,
    apply_interval_overrides,
    audio_edit_candidates,
    audio_edit_review_consistency_issues,
    build_variable_speed_runs,
    assignment_candidates,
    assignment_score,
    auto_resegment_high_confidence_boundaries,
    canonical_event_coverage,
    cross_language_phonetic_rescue,
    cross_track_overlap_candidates,
    cross_track_overlap_review_consistency_issues,
    deduplicate_boundary_vocalizations,
    discard_noncanonical_vocalization_rows,
    evaluate_regression_cases,
    global_sequence_alignment,
    is_generic_vocalization,
    matching_audio_edit_review,
    matching_cross_track_overlap_review,
    normalized_confirmed_overlap_intervals,
    normalized_cross_track_overlap_reviews,
    overlap_pair_is_confirmed,
    parse_lrc,
    preferred_boundary_observation,
    project_source_with_confirmed_cuts,
    projected_track_window_status,
    report_fieldnames,
    set_row_lrc_provenance,
    sha256,
    source_overlap_signatures,
    validate_source_srt_scope,
)
from task_contract import qa_metadata, sha256 as contract_sha256


class GlobalSequenceAlignmentTests(unittest.TestCase):
    def test_enhanced_lrc_word_timing_keeps_one_canonical_line(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lyrics.lrc"
            path.write_text(
                "[00:10.00]<00:10.00>Hello <00:10.50>world\n",
                encoding="utf-8",
            )

            lines = parse_lrc(path)

            self.assertEqual(len(lines), 1)
            self.assertEqual(lines[0].text, "Hello world")
            self.assertEqual([token.text for token in lines[0].tokens], ["Hello", "world"])
            self.assertEqual([token.start_ms for token in lines[0].tokens], [10000, 10500])
            self.assertEqual(lines[0].timing_format, "enhanced_lrc")

    def test_qrc_word_timing_keeps_one_canonical_line(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lyrics.lrc"
            path.write_text(
                "[10000,1200]Hello(0,500) world(500,700)\n",
                encoding="utf-8",
            )

            lines = parse_lrc(path)

            self.assertEqual(len(lines), 1)
            self.assertEqual(lines[0].text, "Hello world")
            self.assertEqual([token.start_ms for token in lines[0].tokens], [10000, 10500])
            self.assertEqual([token.end_ms for token in lines[0].tokens], [10500, 11200])
            self.assertEqual(lines[0].timing_format, "qrc_word_timing")

    def test_plain_lrc_behavior_is_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lyrics.lrc"
            path.write_text("[00:10.25]One complete line\n", encoding="utf-8")

            lines = parse_lrc(path)

            self.assertEqual(len(lines), 1)
            self.assertEqual(lines[0].text, "One complete line")
            self.assertEqual(lines[0].tokens, ())
            self.assertEqual(lines[0].timing_format, "line_lrc")

    def test_variable_speed_run_is_continuous_monotonic_and_used_for_projection(self):
        points = [(0.0, 0.0), (3.0, 3.0), (6.0, 6.3), (9.0, 10.8), (12.0, 15.9)]
        audio_track = {
            "path": [
                {
                    "mix_center": mix,
                    "selected": {"source_center": source, "ncc": 0.9},
                }
                for mix, source in points
            ],
            "edit_candidates": [],
        }
        mapping = {"slope": 1.2, "intercept": 0.0, "method": "affine"}

        runs = build_variable_speed_runs(audio_track, mapping)

        self.assertEqual(len(runs), 1)
        mapping["variable_speed_runs"] = runs
        projected = [
            project_source_with_confirmed_cuts(source, mapping, audio_track)[0]
            for source in (3.0, 6.3, 10.8, 15.9)
        ]
        self.assertEqual(projected, sorted(projected))
        self.assertAlmostEqual(projected[-1], 12.0)

    def test_ambiguous_alternating_waveform_path_does_not_enable_variable_speed(self):
        audio_track = {
            "path": [
                {
                    "mix_center": mix,
                    "selected": {"source_center": source, "ncc": 0.9},
                }
                for mix, source in [(0, 0), (3, 5), (6, 6), (9, 11), (12, 12)]
            ],
            "edit_candidates": [],
        }
        self.assertEqual(
            build_variable_speed_runs(
                audio_track, {"slope": 1.0, "intercept": 0.0}
            ),
            [],
        )

    def test_two_fixed_speed_sections_create_one_continuous_piecewise_run(self):
        points = [(0, 0), (3, 3), (6, 6), (9, 10.5), (12, 15), (15, 19.5)]
        audio_track = {
            "path": [
                {
                    "mix_center": mix,
                    "selected": {"source_center": source, "ncc": 0.92},
                }
                for mix, source in points
            ],
            "edit_candidates": [],
        }

        runs = build_variable_speed_runs(
            audio_track, {"slope": 1.25, "intercept": 0.0}
        )

        self.assertEqual(len(runs), 1)
        self.assertGreaterEqual(len(runs[0]["knots"]), 3)
        self.assertEqual(runs[0]["source_start"], 0.0)
        self.assertEqual(runs[0]["source_end"], 19.5)

    def test_variable_speed_and_confirmed_cut_preserve_both_sides(self):
        points = [
            (0, 0), (3, 3), (6, 6.3), (9, 10.8), (12, 15.9),
            (15, 24), (18, 27), (21, 30.3), (24, 34.8), (27, 39.9),
        ]
        audio_track = {
            "path": [
                {
                    "mix_center": mix,
                    "selected": {"source_center": source, "ncc": 0.91},
                }
                for mix, source in points
            ],
            "edit_candidates": [
                {
                    "type": "forward_source_cut",
                    "status": "confirmed",
                    "mix_time": 13.5,
                    "source_start": 18.0,
                    "source_end": 22.5,
                }
            ],
        }
        mapping = {"slope": 1.2, "intercept": 0.0, "method": "affine"}
        mapping["variable_speed_runs"] = build_variable_speed_runs(audio_track, mapping)

        self.assertEqual(len(mapping["variable_speed_runs"]), 2)
        self.assertIsNone(
            project_source_with_confirmed_cuts(20.0, mapping, audio_track)[0]
        )
        before = project_source_with_confirmed_cuts(10.8, mapping, audio_track)[0]
        after = project_source_with_confirmed_cuts(34.8, mapping, audio_track)[0]
        self.assertAlmostEqual(before, 9.0, delta=0.25)
        self.assertAlmostEqual(after, 24.0, delta=0.25)

    def test_confirmed_two_track_overlap_requires_exact_tracks_and_interval(self):
        intervals = normalized_confirmed_overlap_intervals(
            [
                {
                    "cue": 7,
                    "start_ms": 1000,
                    "end_ms": 3000,
                    "tracks": ["song b", "song a"],
                    "evidence": "reviewed both vocal stems",
                }
            ]
        )
        left = {"start_ms": 1200, "end_ms": 2500, "track": "song a"}
        right = {"start_ms": 1800, "end_ms": 2800, "track": "song b"}

        self.assertTrue(overlap_pair_is_confirmed(left, right, intervals))
        self.assertFalse(
            overlap_pair_is_confirmed(
                left, {**right, "track": "song c"}, intervals
            )
        )

    def test_overlap_review_and_allowed_interval_must_agree(self):
        intervals = normalized_confirmed_overlap_intervals(
            [
                {
                    "cue": 7,
                    "start_ms": 1000,
                    "end_ms": 3000,
                    "tracks": ["song a", "song b"],
                    "evidence": "reviewed simultaneous vocals",
                }
            ]
        )
        confirmed = normalized_cross_track_overlap_reviews(
            [
                {
                    "cue": 7,
                    "tracks": ["song a", "song b"],
                    "status": "confirmed",
                    "evidence": "reviewed simultaneous vocals",
                }
            ]
        )
        rejected = normalized_cross_track_overlap_reviews(
            [
                {
                    "cue": 7,
                    "tracks": ["song a", "song b"],
                    "status": "rejected",
                    "evidence": "sequential handoff",
                }
            ]
        )

        self.assertEqual(
            cross_track_overlap_review_consistency_issues(intervals, confirmed),
            [],
        )
        self.assertEqual(
            len(cross_track_overlap_review_consistency_issues(intervals, rejected)),
            1,
        )
        self.assertEqual(
            len(cross_track_overlap_review_consistency_issues([], confirmed)),
            1,
        )

    def test_cross_track_overlap_candidate_requires_both_lyrics_in_one_transition_cue(self):
        tracks = [
            Track(1, 0, 5000, "artist a", "song a", "a.lrc"),
            Track(2, 5000, 10000, "artist b", "song b", "b.lrc"),
        ]
        cue = Cue(1, 4500, 5500, "alpha ending beta opening")
        events = [
            {
                "track_index": 1,
                "track": "song a",
                "projected_ms": 4800,
                "text": "alpha ending",
            },
            {
                "track_index": 2,
                "track": "song b",
                "projected_ms": 5200,
                "text": "beta opening",
            },
        ]

        candidates = cross_track_overlap_candidates([cue], tracks, events)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["category"], "cross_track_vocal_overlap")
        self.assertEqual(
            cross_track_overlap_candidates(
                [Cue(1, 4500, 5500, "alpha ending")], tracks, events
            ),
            [],
        )

    def test_cross_track_overlap_review_can_confirm_or_reject_exact_candidate(self):
        candidate = {
            "track": "song a + song b",
            "left_cue": 7,
        }
        reviews = normalized_cross_track_overlap_reviews(
            [
                {
                    "cue": 7,
                    "tracks": ["song b", "song a"],
                    "status": "rejected",
                    "evidence": "sequential handoff, no simultaneous vocals",
                }
            ]
        )

        matched = matching_cross_track_overlap_review(candidate, reviews)

        self.assertIsNotNone(matched)
        self.assertEqual(matched["status"], "rejected")
        self.assertIsNone(
            matching_cross_track_overlap_review(
                {**candidate, "left_cue": 8}, reviews
            )
        )

    def test_korean_jianying_latin_phonetics_can_be_rescued_by_asr_and_lrc(self):
        self.assertTrue(
            cross_language_phonetic_rescue(
                "ko",
                "synthetic latin phonetics",
                "가나다 라마바",
                "라마바",
                0.75,
                0.60,
                2700,
                False,
            )
        )

    def test_genuine_canonical_english_is_not_rewritten_as_korean(self):
        self.assertFalse(
            cross_language_phonetic_rescue(
                "ko",
                "we go together",
                "위 고 투게더",
                "we go together",
                0.90,
                0.90,
                300,
                False,
            )
        )

    def test_cross_language_rescue_rejects_weak_or_already_mapped_rows(self):
        self.assertFalse(
            cross_language_phonetic_rescue(
                "ko", "phonetic", "한국어", "한국어", 0.69, 0.90, 200, False
            )
        )
        self.assertFalse(
            cross_language_phonetic_rescue(
                "ko", "phonetic", "한국어", "한국어", 0.90, 0.90, 200, True
            )
        )

    def test_report_fields_include_columns_added_to_later_asr_rows(self):
        rows = [
            {"kind": "existing", "text": "first"},
            {
                "kind": "existing",
                "text": "second",
                "evidence_language": "ko",
                "pronunciation_evidence_available": "true",
            },
        ]

        self.assertEqual(
            report_fieldnames(rows),
            [
                "kind",
                "text",
                "evidence_language",
                "pronunciation_evidence_available",
            ],
        )

    def test_asr_canonical_replacement_overwrites_stale_lrc_index(self):
        row = {"text": "old text", "lrc_indices": "15"}

        set_row_lrc_provenance(row, [{"lrc_index": 14}])

        self.assertEqual(row["lrc_indices"], "14")

    def test_coverage_rejects_stale_index_without_canonical_text(self):
        rows = [
            {
                "track": "song",
                "start_ms": 1000,
                "end_ms": 2000,
                "text": "unrelated previous lyric",
                "lrc_indices": "4",
            }
        ]
        event = {
            "track": "song",
            "lrc_index": 4,
            "projected_ms": 1400,
            "text": "the missing canonical lyric is here",
        }

        result = canonical_event_coverage(rows, event)

        self.assertFalse(result["covered"])
        self.assertTrue(result["linked"])

    def test_coverage_accepts_canonical_text_in_adjacent_split_cell(self):
        rows = [
            {
                "track": "song",
                "start_ms": 1000,
                "end_ms": 2000,
                "text": "canonical lyric first half",
                "lrc_indices": "4",
            },
            {
                "track": "song",
                "start_ms": 2000,
                "end_ms": 3000,
                "text": "second half completes",
                "lrc_indices": "5",
            },
        ]
        event = {
            "track": "song",
            "lrc_index": 4,
            "projected_ms": 1500,
            "text": "canonical lyric first half second half completes",
        }

        result = canonical_event_coverage(rows, event)

        self.assertTrue(result["covered"])

    def test_source_overlap_signatures_find_nonadjacent_overlay_tracks(self):
        cues = [
            Cue(1, 1000, 3000, "overlay a"),
            Cue(2, 4000, 5000, "later"),
            Cue(3, 2000, 3500, "overlay b"),
        ]

        self.assertEqual(
            source_overlap_signatures(cues),
            {(1000, 3000, 2000, 3500)},
        )

    def test_assignment_candidates_sort_out_of_order_overlay_tracks(self):
        cues = [
            Cue(1, 1000, 6333, "spoken introduction"),
            Cue(2, 6433, 7833, "spoken instruction"),
            Cue(3, 7966, 8633, "later spoken instruction"),
            Cue(4, 6600, 8400, "canonical lyric words"),
        ]
        event = {"projected_ms": 6586, "text": "canonical lyric words"}

        candidates = assignment_candidates(cues, event, 1400)
        selected = max(candidates, key=lambda cue: assignment_score(cue, event))

        self.assertEqual([cue.number for cue in candidates], [1, 2, 4, 3])
        self.assertEqual(selected.number, 4)

    def test_original_song_prefix_trim_is_not_treated_as_a_missing_first_lyric(self):
        track = Track(3, 352000, 554000, "artist", "song", "lyrics.lrc")

        included, reason = projected_track_window_status(track, 330125)

        self.assertFalse(included)
        self.assertEqual(reason, "trimmed_before_mix_entry")

        included, reason = projected_track_window_status(track, 352100)

        self.assertTrue(included)
        self.assertIsNone(reason)

    def test_repeated_phrase_uses_global_order(self):
        observations = [
            {"id": 1, "start_ms": 900, "end_ms": 1700, "text": "signal returns"},
            {
                "id": 2,
                "start_ms": 2000,
                "end_ms": 3300,
                "text": "middle sequence stays unique",
            },
            {"id": 3, "start_ms": 4900, "end_ms": 5700, "text": "signal returns"},
        ]
        events = [
            {"lrc_index": 1, "projected_ms": 1000, "text": "signal returns"},
            {
                "lrc_index": 2,
                "projected_ms": 2200,
                "text": "middle sequence stays unique",
            },
            {"lrc_index": 3, "projected_ms": 5000, "text": "signal returns"},
        ]

        result = global_sequence_alignment(observations, events)

        self.assertEqual(
            [row["event_indices"] for row in result["matches"]],
            [[1], [2], [3]],
        )
        self.assertEqual(result["skipped_lyric_indices"], [])

    def test_global_repair_adds_suffix_line_hidden_by_local_match(self):
        cues = [
            Cue(1, 1000, 2200, "alpha opens beta continues"),
            Cue(2, 2200, 4000, "bridge completes gamma follows"),
        ]
        track = Track(1, 0, 5000, "artist", "title", "")
        events = [
            {"track_index": 1, "track": "title", "lrc_index": 1, "projected_ms": 1100, "text": "alpha opens", "mapping_method": "test"},
            {"track_index": 1, "track": "title", "lrc_index": 2, "projected_ms": 1600, "text": "beta continues", "mapping_method": "test"},
            {"track_index": 1, "track": "title", "lrc_index": 3, "projected_ms": 2100, "text": "bridge completes", "mapping_method": "test"},
            {"track_index": 1, "track": "title", "lrc_index": 4, "projected_ms": 3100, "text": "gamma follows", "mapping_method": "test"},
        ]
        cue_events = {1: events[:2], 2: [events[3]]}

        repairs, _ = apply_global_sequence_repairs(
            cues, cue_events, events, [track], set()
        )

        self.assertGreaterEqual(repairs, 1)
        self.assertEqual(
            [row["lrc_index"] for row in cue_events[2]],
            [3, 4],
        )

    def test_global_repair_cannot_drop_unique_existing_coverage(self):
        cues = [Cue(1, 1000, 2600, "alpha beta gamma")]
        track = Track(1, 0, 5000, "artist", "title", "")
        events = [
            {"track_index": 1, "track": "title", "lrc_index": 93, "projected_ms": 1100, "text": "alpha beta gamma", "mapping_method": "test"},
            {"track_index": 1, "track": "title", "lrc_index": 94, "projected_ms": 4200, "text": "beta beta beta", "mapping_method": "test"},
        ]
        cue_events = {1: list(events)}

        repairs, _ = apply_global_sequence_repairs(
            cues, cue_events, events, [track], set()
        )

        self.assertEqual(repairs, 0)
        self.assertEqual(
            [row["lrc_index"] for row in cue_events[1]],
            [93, 94],
        )

    def test_merged_jianying_cell_consumes_consecutive_lyrics(self):
        observations = [
            {
                "id": 10,
                "start_ms": 1000,
                "end_ms": 4000,
                "text": "first sequence second sequence",
            }
        ]
        events = [
            {"lrc_index": 20, "projected_ms": 1200, "text": "first sequence"},
            {"lrc_index": 21, "projected_ms": 2500, "text": "second sequence"},
        ]

        result = global_sequence_alignment(observations, events)

        self.assertEqual(result["matches"][0]["event_indices"], [20, 21])
        self.assertEqual(result["skipped_lyric_indices"], [])

    def test_one_lrc_line_can_continue_across_adjacent_cells(self):
        observations = [
            {
                "id": 1,
                "start_ms": 1000,
                "end_ms": 2200,
                "text": "alpha opens beta continues bridge",
            },
            {
                "id": 2,
                "start_ms": 2200,
                "end_ms": 4000,
                "text": "bridge completes gamma follows",
            },
        ]
        events = [
            {"lrc_index": 49, "projected_ms": 1100, "text": "alpha opens"},
            {"lrc_index": 50, "projected_ms": 1600, "text": "beta continues"},
            {"lrc_index": 51, "projected_ms": 2100, "text": "bridge bridge completes"},
            {"lrc_index": 52, "projected_ms": 3100, "text": "gamma follows"},
        ]

        result = global_sequence_alignment(observations, events)

        self.assertEqual(
            [row["event_indices"] for row in result["matches"]],
            [[49, 50, 51], [51, 52]],
        )
        self.assertEqual(result["skipped_lyric_indices"], [])


class BoundaryEvidenceTests(unittest.TestCase):
    def test_word_asr_can_replace_bad_jianying_observation(self):
        row = {
            "original": "random phonetic noise",
            "text": "가나다라마바사",
            "asr_text": "가나다라마바사",
            "asr_score": "0.93",
        }

        observation, source = preferred_boundary_observation(row)

        self.assertEqual(observation, "가나다라마바사")
        self.assertEqual(source, "word_asr")

    def test_short_english_edge_is_restored_even_when_whole_cue_gain_is_small(self):
        rows = [
            {
                "kind": "existing",
                "original_cue": "1",
                "start_ms": 1000,
                "end_ms": 2000,
                "track": "song",
                "original": "alpha beta gamma delta",
                "text": "Alpha beta gamma",
                "status": "replace_existing",
                "confidence": "high",
                "evidence": "test",
            },
            {
                "kind": "existing",
                "original_cue": "2",
                "start_ms": 2000,
                "end_ms": 4000,
                "track": "song",
                "original": "epsilon zeta eta theta",
                "text": "Delta epsilon zeta eta theta",
                "status": "replace_existing",
                "confidence": "high",
                "evidence": "test",
            },
        ]

        applied = auto_resegment_high_confidence_boundaries(rows)

        self.assertGreaterEqual(applied, 1)
        self.assertEqual(rows[0]["text"], "Alpha beta gamma Delta")
        self.assertEqual(
            rows[1]["text"],
            "epsilon zeta eta theta",
        )

    def test_short_chinese_edge_uses_same_generic_rule(self):
        rows = [
            {
                "kind": "existing",
                "original_cue": "10",
                "start_ms": 1000,
                "end_ms": 2000,
                "track": "中文歌",
                "original": "春夏秋冬",
                "text": "春夏",
                "status": "replace_existing",
                "confidence": "high",
                "evidence": "test",
            },
            {
                "kind": "existing",
                "original_cue": "11",
                "start_ms": 2000,
                "end_ms": 4000,
                "track": "中文歌",
                "original": "风雨雷电",
                "text": "秋冬风雨雷电",
                "status": "replace_existing",
                "confidence": "high",
                "evidence": "test",
            },
        ]

        applied = auto_resegment_high_confidence_boundaries(rows)

        self.assertGreaterEqual(applied, 1)
        self.assertEqual(rows[0]["text"], "春夏秋冬")
        self.assertEqual(rows[1]["text"], "风雨雷电")

    def test_boundary_resegment_moves_single_canonical_uh_without_duplication(self):
        rows = [
            {
                "kind": "existing",
                "original_cue": "20",
                "start_ms": 1000,
                "end_ms": 2000,
                "track": "song",
                "original": "alpha beta uh",
                "text": "Alpha beta",
                "status": "replace_existing",
                "confidence": "high",
                "evidence": "test",
            },
            {
                "kind": "existing",
                "original_cue": "21",
                "start_ms": 2000,
                "end_ms": 4000,
                "track": "song",
                "original": "gamma delta",
                "text": "Uh gamma delta",
                "status": "manual_verified_override",
                "confidence": "high",
                "evidence": "manual_context_review",
            },
        ]

        applied = auto_resegment_high_confidence_boundaries(rows)

        self.assertEqual(applied, 1)
        self.assertEqual(rows[0]["text"], "Alpha beta Uh")
        self.assertEqual(rows[1]["text"], "gamma delta")

    def test_manual_restore_does_not_duplicate_lrc_authored_uh(self):
        rows = [
            {
                "kind": "existing",
                "original_cue": "20",
                "start_ms": 1000,
                "end_ms": 2000,
                "track": "song",
                "text": "Alpha beta Uh",
                "status": "auto_resegmented_boundary",
                "confidence": "high",
                "evidence": "jianying",
                "lrc_indices": "9",
            },
            {
                "kind": "existing",
                "original_cue": "21",
                "start_ms": 2000,
                "end_ms": 4000,
                "track": "song",
                "text": "Uh gamma delta",
                "status": "manual_verified_override",
                "confidence": "high",
                "evidence": "manual_context_review",
                "lrc_indices": "10",
            },
        ]
        events = [
            {"track": "song", "lrc_index": 9, "text": "Alpha beta"},
            {"track": "song", "lrc_index": 10, "text": "Uh gamma delta"},
        ]

        removed = deduplicate_boundary_vocalizations(rows, events)

        self.assertEqual(removed, 1)
        self.assertEqual(rows[0]["text"], "Alpha beta")
        self.assertEqual(rows[1]["text"], "Uh gamma delta")

    def test_source_only_adlib_row_is_discarded(self):
        rows = [
            {
                "original_cue": "20",
                "text": "Uh Oh Yeah",
                "status": "keep_existing",
                "evidence": "jianying_only",
                "lrc_indices": "",
            },
            {
                "original_cue": "21",
                "text": "Uh lexical phrase",
                "status": "replace_existing",
                "evidence": "lrc",
                "lrc_indices": "10",
            },
        ]

        discarded = discard_noncanonical_vocalization_rows(rows)

        self.assertEqual(discarded, 1)
        self.assertEqual([row["original_cue"] for row in rows], ["21"])

    def test_foreign_lyric_with_trailing_oh_is_not_an_adlib_only_row(self):
        self.assertFalse(is_generic_vocalization("가나다 oh"))
        self.assertFalse(is_generic_vocalization("甲乙丙 oh"))
        self.assertTrue(is_generic_vocalization("Uh Oh Yeah"))


class ProjectRegressionTests(unittest.TestCase):
    def test_interval_override_replaces_only_contained_track_rows(self):
        rows = [
            {"track": "song", "start_ms": 1000, "end_ms": 2000, "text": "old 1"},
            {"track": "song", "start_ms": 2000, "end_ms": 3000, "text": "old 2"},
            {"track": "other", "start_ms": 1000, "end_ms": 3000, "text": "overlay"},
        ]

        applied = apply_interval_overrides(
            rows,
            [
                {
                    "track": "song",
                    "start_ms": 1000,
                    "end_ms": 3000,
                    "evidence": "reviewed audio",
                    "parts": [
                        {
                            "start_ms": 1000,
                            "end_ms": 2200,
                            "text": "new 1",
                            "lrc_indices": "4",
                        },
                        {
                            "start_ms": 2200,
                            "end_ms": 3000,
                            "text": "new 2",
                            "lrc_indices": "5",
                        },
                    ],
                }
            ],
        )

        self.assertEqual(applied, 1)
        self.assertEqual([row["text"] for row in rows], ["overlay", "new 1", "new 2"])
        self.assertTrue(all(row.get("confidence") == "high" for row in rows[1:]))

    def test_interval_override_rejects_crossing_existing_row(self):
        rows = [
            {"track": "song", "start_ms": 900, "end_ms": 2100, "text": "crosses"}
        ]
        with self.assertRaisesRegex(ValueError, "crosses an existing row boundary"):
            apply_interval_overrides(
                rows,
                [
                    {
                        "track": "song",
                        "start_ms": 1000,
                        "end_ms": 2000,
                        "evidence": "reviewed audio",
                        "parts": [
                            {"start_ms": 1000, "end_ms": 2000, "text": "new"}
                        ],
                    }
                ],
            )

    def test_interval_override_drops_same_index_rebuild_crossing_boundary(self):
        rows = [
            {
                "track": "song",
                "start_ms": 900,
                "end_ms": 1300,
                "text": "automatic duplicate",
                "lrc_indices": "4",
                "evidence": "automatic",
            },
            {
                "track": "song",
                "start_ms": 1300,
                "end_ms": 2000,
                "text": "next lyric",
                "lrc_indices": "5",
                "evidence": "automatic",
            },
        ]

        apply_interval_overrides(
            rows,
            [
                {
                    "track": "song",
                    "start_ms": 1000,
                    "end_ms": 1600,
                    "evidence": "verified audio",
                    "parts": [
                        {
                            "start_ms": 1000,
                            "end_ms": 1600,
                            "text": "verified lyric",
                            "lrc_indices": "4",
                        }
                    ],
                }
            ],
        )

        self.assertFalse(any(row["text"] == "automatic duplicate" for row in rows))
        cropped = next(row for row in rows if row["text"] == "next lyric")
        self.assertEqual(cropped["start_ms"], 1600)
        self.assertIn("cropped_to_manual_interval_boundary", cropped["evidence"])

    def test_audio_edit_review_requires_full_exact_coordinates(self):
        edit = {
            "mix_time": 10.5,
            "source_start": 20.25,
            "source_end": 24.75,
        }
        reviews = [
            {"track": "song", "status": "rejected"},
            {
                "track": "song",
                "mix_time": 10.5,
                "source_start": 20.25,
                "source_end": 24.75,
                "status": "confirmed",
                "evidence": "manual waveform review",
            },
        ]

        matched = matching_audio_edit_review("song", edit, reviews)

        self.assertIsNotNone(matched)
        self.assertEqual(matched["status"], "confirmed")
        self.assertIsNone(
            matching_audio_edit_review(
                "song", {**edit, "source_end": 24.751}, reviews
            )
        )

    def test_reviewed_cut_is_applied_to_projection_and_rejected_cut_is_not(self):
        base = {
            "algorithm_version": "3.8",
            "task_fingerprint_sha256": "1" * 64,
            "tracks": [
                {
                    "track": {"title": "song"},
                    "bpm_tempo_ratio": 1.0,
                    "path": [
                        {"mix_center": 2.0, "selected": {"source_center": 2.0}},
                        {"mix_center": 8.0, "selected": {"source_center": 12.0}},
                    ],
                    "edit_candidates": [
                        {
                            "type": "forward_source_cut",
                            "status": "review",
                            "mix_time": 5.0,
                            "source_start": 5.0,
                            "source_end": 9.0,
                        }
                    ],
                }
            ],
        }
        coordinates = {
            "track": "song",
            "mix_time": 5.0,
            "source_start": 5.0,
            "source_end": 9.0,
            "evidence": "synthetic review",
        }

        confirmed, summary = apply_audio_edit_reviews(
            base, [{**coordinates, "status": "confirmed"}]
        )
        mapping = {"slope": 1.0, "intercept": 0.0, "method": "test"}
        track = confirmed["tracks"][0]
        self.assertEqual(summary["confirmed_count"], 1)
        self.assertEqual(
            project_source_with_confirmed_cuts(7.0, mapping, track),
            (None, "confirmed_source_cut"),
        )
        projected, method = project_source_with_confirmed_cuts(10.0, mapping, track)
        self.assertAlmostEqual(projected, 6.0)
        self.assertEqual(method, "confirmed_cut_piecewise_audio_mapping")

        rejected, summary = apply_audio_edit_reviews(
            base, [{**coordinates, "status": "rejected"}]
        )
        self.assertEqual(summary["rejected_count"], 1)
        self.assertEqual(
            project_source_with_confirmed_cuts(7.0, mapping, rejected["tracks"][0]),
            (7.0, "test"),
        )

    def test_multiple_confirmed_cuts_project_piecewise_and_remove_both_ranges(self):
        audio_track = {
            "bpm_tempo_ratio": 1.0,
            "path": [
                {"mix_center": 2.0, "selected": {"source_center": 2.0}},
                {"mix_center": 7.0, "selected": {"source_center": 10.0}},
                {"mix_center": 12.0, "selected": {"source_center": 18.0}},
            ],
            "edit_candidates": [
                {
                    "type": "forward_source_cut",
                    "status": "confirmed",
                    "mix_time": 5.0,
                    "source_start": 5.0,
                    "source_end": 8.0,
                },
                {
                    "type": "forward_source_cut",
                    "status": "confirmed",
                    "mix_time": 10.0,
                    "source_start": 13.0,
                    "source_end": 16.0,
                },
            ],
        }
        mapping = {"slope": 1.0, "intercept": 0.0, "method": "test"}

        self.assertIsNone(project_source_with_confirmed_cuts(6.0, mapping, audio_track)[0])
        self.assertIsNone(project_source_with_confirmed_cuts(14.0, mapping, audio_track)[0])
        after, _ = project_source_with_confirmed_cuts(18.0, mapping, audio_track)
        self.assertAlmostEqual(after, 12.0)

    def test_short_cut_requires_reliable_waveform_and_text_support(self):
        segments = [
            {
                "mix_start": 0.0,
                "mix_end": 5.0,
                "slope": 1.0,
                "intercept": 0.0,
                "anchor_count": 4,
                "max_residual": 0.1,
            },
            {
                "mix_start": 5.0,
                "mix_end": 10.0,
                "slope": 1.0,
                "intercept": 1.0,
                "anchor_count": 4,
                "max_residual": 0.1,
            },
        ]

        supported = audio_edit_candidates(
            segments, 1.0, [(4.0, 4.0), (6.0, 7.0)]
        )
        unsupported = audio_edit_candidates(segments, 1.0, [])

        self.assertEqual(len(supported), 1)
        self.assertEqual(supported[0]["status"], "review")
        self.assertEqual(unsupported, [])

    def test_qa_detects_audio_review_not_applied_to_alignment(self):
        payload = {
            "tracks": [
                {
                    "track": {"title": "song"},
                    "edit_candidates": [
                        {
                            "status": "review",
                            "mix_time": 5.0,
                            "source_start": 5.0,
                            "source_end": 9.0,
                        }
                    ],
                }
            ]
        }
        reviews = [
            {
                "track": "song",
                "status": "confirmed",
                "mix_time": 5.0,
                "source_start": 5.0,
                "source_end": 9.0,
                "evidence": "synthetic review",
            }
        ]

        issues = audio_edit_review_consistency_issues(payload, reviews)

        self.assertEqual(len(issues), 1)
        self.assertIn("not applied", issues[0])

    @staticmethod
    def manifest(source: Path, fingerprint: str = "1" * 64) -> dict:
        return {
            "schema_version": "2.0",
            "project": "one mix",
            "task_fingerprint_sha256": fingerprint,
            "inputs": {
                "source_srt": {
                    "kind": "file",
                    "path": "private/one-mix/input/source.srt",
                    "size": source.stat().st_size,
                    "sha256": contract_sha256(source),
                }
            },
        }

    def test_manual_override_scope_accepts_matching_canonical_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.srt"
            source.write_text("1\n00:00:01,000 --> 00:00:02,000\nhello\n", encoding="utf-8")

            actual, issue = validate_source_srt_scope(
                {"source_srt_sha256": sha256(source)},
                source,
                "manual override file",
            )

            self.assertEqual(actual, sha256(source))
            self.assertIsNone(issue)

    def test_manual_override_scope_rejects_missing_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.srt"
            source.write_text("1\n00:00:01,000 --> 00:00:02,000\nhello\n", encoding="utf-8")

            _, issue = validate_source_srt_scope(
                {},
                source,
                "manual override file",
            )

            self.assertIn("no source_srt_sha256 guard", str(issue))

    def test_case_file_is_rejected_for_another_source_srt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.srt"
            source.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\nhello\n",
                encoding="utf-8",
            )
            cases = root / "cases.json"
            manifest = self.manifest(source)
            payload = {**qa_metadata(manifest, "regression_cases"), "cases": []}
            payload["source_srt_sha256"] = "0" * 64
            cases.write_text(json.dumps(payload), encoding="utf-8")

            issues, _ = evaluate_regression_cases([], cases, manifest)

            self.assertTrue(any("source_srt_sha256" in issue for issue in issues))

    def test_exact_project_case_passes_with_matching_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.srt"
            source.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\nwrong\n",
                encoding="utf-8",
            )
            cases = root / "cases.json"
            manifest = self.manifest(source)
            cases.write_text(
                json.dumps(
                    {
                        **qa_metadata(manifest, "regression_cases"),
                        "cases": [
                            {
                                "id": "corrected",
                                "kind": "interval_text",
                                "start_ms": 1000,
                                "end_ms": 2000,
                                "text": "correct",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            issues, summary = evaluate_regression_cases(
                [Cue(1, 1000, 2000, "correct")], cases, manifest
            )

            self.assertEqual(issues, [])
            self.assertEqual(summary["passed"], 1)

    def test_timing_only_case_does_not_require_lyric_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.srt"
            source.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\nunknown language\n",
                encoding="utf-8",
            )
            cases = root / "cases.json"
            manifest = self.manifest(source)
            cases.write_text(
                json.dumps(
                    {
                        **qa_metadata(manifest, "regression_cases"),
                        "cases": [
                            {
                                "id": "confirmed-vocal-boundary",
                                "kind": "interval_timing",
                                "start_ms": 1000,
                                "end_ms": 2000,
                                "tolerance_ms": 0,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            issues, summary = evaluate_regression_cases(
                [Cue(1, 1000, 2000, "different text is acceptable")],
                cases,
                manifest,
            )

            self.assertEqual(issues, [])
            self.assertEqual(summary["passed"], 1)

    def test_continuous_coverage_accepts_touching_cues_and_rejects_gap(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.srt"
            source.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\ntext\n",
                encoding="utf-8",
            )
            cases = root / "cases.json"
            manifest = self.manifest(source)
            cases.write_text(
                json.dumps(
                    {
                        **qa_metadata(manifest, "regression_cases"),
                        "cases": [
                            {
                                "id": "continuous-vocals",
                                "kind": "continuous_coverage",
                                "start_ms": 1000,
                                "end_ms": 3000,
                                "max_gap_ms": 0,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            issues, summary = evaluate_regression_cases(
                [Cue(1, 900, 2000, "first"), Cue(2, 2000, 3100, "second")],
                cases,
                manifest,
            )
            self.assertEqual(issues, [])
            self.assertEqual(summary["passed"], 1)

            issues, _ = evaluate_regression_cases(
                [Cue(1, 900, 1900, "first"), Cue(2, 2000, 3100, "second")],
                cases,
                manifest,
            )
            self.assertEqual(len(issues), 1)


if __name__ == "__main__":
    unittest.main()
