from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from pathlib import Path

from lyric_aligner.text.english_lexicon import (
    EnglishLexiconBuildError, build_english_lexicon, sha256_file, verify_derived_english_lexicon,
)


class EnglishLexiconTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / "base.txt"
        # No terminal newline confirms the output keeps every bound byte and
        # adds exactly one separator before generated keys.
        self.base.write_bytes(b"known\tk n ow n\ngettin'\tg eh t ih n\nbaseconflict\tb ey s")
        self.vocab = self.root / "vocab.json"
        self.vocab.write_text(json.dumps({"vocab": {
            "en/k": 1, "en/n": 2, "en/ow": 3, "en/g": 4, "en/eh": 5, "en/t": 6,
            "en/ih": 7, "en/b": 8, "en/ey": 9, "en/d": 10, "en/aa": 11,
            "en/s": 12, "en/er": 13, "en/l": 14, "en/ah": 15, "en/aw": 16,
        }}), encoding="utf-8")
        self.cmu = self.root / "cmudict.dict"
        self.output = self.root / "derived.txt"
        self.manifest = self.root / "derived.manifest.json"

    def _build(self, *, output=None, manifest=None):
        return build_english_lexicon(base_dictionary_path=self.base, vocab_path=self.vocab,
                                     cmudict_path=self.cmu, output_dictionary_path=output or self.output,
                                     output_manifest_path=manifest or self.manifest)

    def test_preserves_base_bytes_aliases_and_exact_external_entries_deterministically(self):
        self.cmu.write_text("\n".join([
            ";;; comment", "external EH1 K S T ER0 N AH0 L", "external(2) EH0 K S T ER1 N AH2 L",
            "known D AA1 G", "baseconflict D AA1 G", "'bout B AW1 T", "badphone ZZ1",
        ]) + "\n", encoding="utf-8")
        first = self._build()
        output = self.output.read_bytes()
        self.assertTrue(output.startswith(self.base.read_bytes() + b"\n"))
        rows = dict(line.split("\t", 1) for line in output.decode("utf-8").splitlines())
        self.assertEqual(rows["known"], "k n ow n")
        self.assertEqual(rows["baseconflict"], "b ey s")
        self.assertEqual(rows["gettin"], "g eh t ih n")
        self.assertEqual(rows["external"], "eh k s t er n ah l")
        self.assertNotIn("bout", rows)  # Word-initial apostrophe is not an alias rule.
        self.assertNotIn("badphone", rows)
        self.assertEqual(first["stats"]["terminal_apostrophe_alias_added_count"], 1)
        self.assertEqual(first["stats"]["cmudict_added_count"], 1)
        self.assertEqual(first["stats"]["cmudict_phone_outside_vocab_rejected_count"], 1)
        self.assertEqual(first["output_dictionary"]["sha256"], sha256_file(self.output))
        self.assertEqual(first["artifact_sha256"], json.loads(self.manifest.read_text(encoding="utf-8"))["artifact_sha256"])
        original = self.output.read_bytes()
        with self.assertRaisesRegex(EnglishLexiconBuildError, "paths must be new"):
            self._build()
        alternate_output, alternate_manifest = self.root / "derived-again.txt", self.root / "derived-again.json"
        second = self._build(output=alternate_output, manifest=alternate_manifest)
        self.assertEqual(alternate_output.read_bytes(), original)
        self.assertEqual(second["output_dictionary"]["sha256"], first["output_dictionary"]["sha256"])
        self.assertEqual(second["added_entries"], first["added_entries"])

    def test_ambiguous_external_variants_and_alias_external_conflict_do_not_create_new_keys(self):
        self.cmu.write_text("\n".join([
            "ambig B EY1 S", "ambig(2) D AA1 G",
            # The base only has gettin' (an allowed alias), but the independent
            # direct entry disagrees, so neither source gets to win by order.
            "gettin G EH1 T IH0 N D",
        ]) + "\n", encoding="utf-8")
        result = self._build()
        rows = dict(line.split("\t", 1) for line in self.output.read_text(encoding="utf-8").splitlines())
        self.assertNotIn("ambig", rows)
        self.assertNotIn("gettin", rows)
        self.assertEqual(result["stats"]["cmudict_ambiguous_pronunciation_rejected_count"], 1)
        self.assertEqual(result["stats"]["terminal_apostrophe_external_conflict_rejected_count"], 1)
        self.assertEqual(result["stats"]["cmudict_alias_authoritative_skipped_count"], 1)

    def test_rejects_output_that_would_mutate_the_bound_dictionary(self):
        self.cmu.write_text("external EH1 K S T ER0 N AH0 L\n", encoding="utf-8")
        with self.assertRaisesRegex(EnglishLexiconBuildError, "bound input"):
            build_english_lexicon(base_dictionary_path=self.base, vocab_path=self.vocab, cmudict_path=self.cmu,
                                  output_dictionary_path=self.base, output_manifest_path=self.manifest)

    def test_refuses_existing_or_any_input_path_for_generated_artifacts(self):
        self.cmu.write_text("external EH1 K S T ER0 N AH0 L\n", encoding="utf-8")
        self.output.write_text("historical output\n", encoding="utf-8")
        with self.assertRaisesRegex(EnglishLexiconBuildError, "paths must be new"):
            self._build()
        with self.assertRaisesRegex(EnglishLexiconBuildError, "bound input"):
            build_english_lexicon(base_dictionary_path=self.base, vocab_path=self.vocab, cmudict_path=self.cmu,
                                  output_dictionary_path=self.root / "new.txt", output_manifest_path=self.vocab)

    def test_verifier_requires_manifest_lineage_and_exact_deterministic_rebuild(self):
        self.cmu.write_text("external EH1 K S T ER0 N AH0 L\n", encoding="utf-8")
        built = self._build()
        verified = verify_derived_english_lexicon(dictionary_path=self.output, manifest_path=self.manifest,
                                                   base_dictionary_path=self.base, vocab_path=self.vocab)
        self.assertEqual(verified["artifact_sha256"], built["artifact_sha256"])
        tampered = json.loads(self.manifest.read_text(encoding="utf-8"))
        tampered["stats"]["cmudict_added_count"] = 0
        self.manifest.write_text(json.dumps(tampered), encoding="utf-8")
        with self.assertRaisesRegex(EnglishLexiconBuildError, "self hash"):
            verify_derived_english_lexicon(dictionary_path=self.output, manifest_path=self.manifest,
                                            base_dictionary_path=self.base, vocab_path=self.vocab)
        # A coherent forged manifest must still fail the recipe replay; the
        # self-hash is only tamper evidence, not the derivation proof.
        tampered["artifact_sha256"] = hashlib.sha256(json.dumps(
            {key: value for key, value in tampered.items() if key != "artifact_sha256"},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")).hexdigest()
        self.manifest.write_text(json.dumps(tampered), encoding="utf-8")
        with self.assertRaisesRegex(EnglishLexiconBuildError, "deterministic rebuild provenance mismatch"):
            verify_derived_english_lexicon(dictionary_path=self.output, manifest_path=self.manifest,
                                            base_dictionary_path=self.base, vocab_path=self.vocab)


if __name__ == "__main__":
    unittest.main()
