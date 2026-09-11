import json
import tempfile
import unittest
from pathlib import Path

from lyric_aligner.review.model_text_adjudication import normalize


class ModelTextAdjudicationTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize("Don't stop!"), "dontstop")

    def test_proposal_protocol_has_no_freeform_text(self):
        self.assertNotIn("text", {"action", "canonical_span", "cue_ordinals"})


if __name__ == "__main__":
    unittest.main()
