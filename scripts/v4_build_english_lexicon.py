"""Build a separately bound HuBERTFA English dictionary without touching base.

Example:
  python scripts/v4_build_english_lexicon.py \
    --base-dictionary private/_models/hubertfa_sidecar/model/ds_cmudict-07b.txt \
    --vocab private/_models/hubertfa_sidecar/model/vocab.json \
    --cmudict private/_models/hubertfa_sidecar/oov_frontend_probe/cmusphinx_cmudict/cmudict.dict \
    --output-dictionary private/_models/hubertfa_sidecar/derived/en_cmudict_bound_v1.txt \
    --output-manifest private/_models/hubertfa_sidecar/derived/en_cmudict_bound_v1.manifest.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lyric_aligner.text.english_lexicon import build_english_lexicon


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a reproducible complete HFA English dictionary.")
    parser.add_argument("--base-dictionary", type=Path, required=True,
                        help="Bound HFA tab-separated dictionary; it is never modified.")
    parser.add_argument("--vocab", type=Path, required=True, help="Bound HuBERTFA vocab.json.")
    parser.add_argument("--cmudict", type=Path,
                        help="Optional pinned CMUdict text file; only exact lexical entries are considered.")
    parser.add_argument("--output-dictionary", type=Path, required=True, help="New full tab-separated dictionary.")
    parser.add_argument("--output-manifest", type=Path, required=True, help="Provenance JSON for the generated dictionary.")
    args = parser.parse_args()
    manifest = build_english_lexicon(base_dictionary_path=args.base_dictionary, vocab_path=args.vocab,
                                     cmudict_path=args.cmudict, output_dictionary_path=args.output_dictionary,
                                     output_manifest_path=args.output_manifest)
    print(json.dumps({"output_dictionary": manifest["output_dictionary"], "stats": manifest["stats"],
                      "artifact_sha256": manifest["artifact_sha256"]}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
