import unittest

from lyric_aligner.text.bijective_han import bijective_pairs, fold_unambiguous_han
from lyric_aligner.alignment.asr_executor import _normalize, _text_support


class BijectiveHanTests(unittest.TestCase):
    def test_spelling_variants_match_without_rewriting_display_normalization(self):
        self.assertEqual(fold_unambiguous_han("愛與裝飾"), "爱与装饰")
        self.assertEqual(_normalize("愛與裝飾"), "愛與裝飾")
        self.assertEqual(_text_support("爱与装饰", "愛與裝飾"), 1.)

    def test_ambiguous_semantic_merges_are_not_equated(self):
        for canonical, observed in (("发", "發"), ("发", "髮"), ("干", "乾"),
                                    ("干", "幹"), ("后", "後"), ("台", "臺")):
            with self.subTest(observed=observed):
                self.assertEqual(fold_unambiguous_han(observed), observed)
                self.assertEqual(_text_support(canonical, observed), 0.)

    def test_competing_forward_entries_and_conversion_chains_are_rejected(self):
        self.assertEqual(bijective_pairs({"A": ("a",), "B": ("a", "b")},
                                        {"a": ("A",)}), {})
        self.assertEqual(bijective_pairs({"A": ("a",)}, {"a": ("A", "B")}), {})
        self.assertEqual(bijective_pairs({"A": ("a",), "a": ("b",)},
                                        {"a": ("A",)}), {})

    def test_non_han_scripts_and_offsets_stay_unchanged(self):
        text = "BABY Beyoncé 사랑 사랑 カタカナ かな 123 ＡＢＣ"
        self.assertEqual(fold_unambiguous_han(text), text)
        folded = fold_unambiguous_han("愛 BABY 裝飾")
        self.assertEqual(len(folded), len("愛 BABY 裝飾"))
        self.assertEqual(fold_unambiguous_han(folded), folded)


if __name__ == "__main__":
    unittest.main()
