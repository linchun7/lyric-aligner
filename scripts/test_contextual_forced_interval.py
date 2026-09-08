import unittest

from lyric_aligner.audio.forced_alignment import (
    AlignmentWord, ForcedAlignmentEvidenceError, alignment_contextual_segment_interval_ms,
)


class ContextualForcedIntervalTests(unittest.TestCase):
    def words(self):
        return [AlignmentWord(0.1, 0.7, 'left'), AlignmentWord(1.0, 1.4, 'same'),
                AlignmentWord(1.4, 2.0, 'word'), AlignmentWord(2.0, 2.7, 'SP'),
                AlignmentWord(2.7, 3.4, 'right')]

    def run_interval(self, words=None, index=1, segments=None):
        return alignment_contextual_segment_interval_ms(self.words() if words is None else words,
            window_start_ms=10000, segment_tokens=segments or [['left'], ['same', 'word'], ['right']],
            target_segment_index=index)

    def test_target_end_is_its_offset_not_next_onset_or_clip_end(self):
        self.assertEqual(self.run_interval(), (11000, 12000))

    def test_unrelated_right_context_duration_does_not_move_target(self):
        words = self.words()
        words[-1] = AlignmentWord(2.7, 20.0, 'right')
        self.assertEqual(self.run_interval(iter(words)), (11000, 12000))

    def test_both_context_sides_required(self):
        for index in (0, 2, -1, True, 1.5):
            with self.assertRaises(ForcedAlignmentEvidenceError):
                self.run_interval(index=index)

    def test_complete_text_not_substring_must_match(self):
        with self.assertRaises(ForcedAlignmentEvidenceError):
            self.run_interval(segments=[['different'], ['same', 'word'], ['right']])

    def test_repeated_target_resolved_by_exact_full_sequence(self):
        words = [AlignmentWord(i, i + 0.5, 'same') for i in range(3)]
        self.assertEqual(self.run_interval(words, segments=[['same'], ['same'], ['same']]), (11000, 11500))

    def test_zero_nonfinite_and_overlapping_target_rejected(self):
        for replacement in (AlignmentWord(1, 1, 'same'), AlignmentWord(float('nan'), 1.4, 'same'),
                            AlignmentWord(0.5, 1.4, 'same')):
            words = self.words(); words[1] = replacement
            with self.assertRaises(ForcedAlignmentEvidenceError):
                self.run_interval(words)

    def test_fractional_or_negative_offset_rejected(self):
        for offset in (True, -1, 0.5):
            with self.assertRaises(ForcedAlignmentEvidenceError):
                alignment_contextual_segment_interval_ms(self.words(), window_start_ms=offset,
                    segment_tokens=[['left'], ['same', 'word'], ['right']], target_segment_index=1)


if __name__ == '__main__':
    unittest.main()
