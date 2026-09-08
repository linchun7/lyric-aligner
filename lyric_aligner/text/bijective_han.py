"""One-to-one Han spelling equivalence for lexical comparison, never display.

General t2s conversion merges meanings (e.g. 發/髮). Require a unique reverse
entry and reject every competing forward entry, including multi-value entries.
No language label or timing authority is inferred from this representation fold.
"""
from __future__ import annotations

from collections import defaultdict
from functools import lru_cache
import hashlib
from pathlib import Path
import unicodedata

POLICY_ID = "bijective-han-opencc-1.3.1-v1"
_SOURCES = {
    "TSCharacters.txt": "ad870b4feeb494cfa7b3b05242bd79af574b22f6b2bdeb89a1633e4b50ed0a3c",
    "STCharacters.txt": "9cedfb8bf13a220087103d9a96d9f56050c341c24a809cbce5c85c9045456557",
}


def _read_dictionary(text: str) -> dict[str, tuple[str, ...]]:
    rows = {}
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        key, values = line.split("\t")
        if key in rows:
            raise ValueError("duplicate Han dictionary key")
        rows[key] = tuple(values.split())
    return rows


def bijective_pairs(forward, backward) -> dict[str, str]:
    incoming = defaultdict(set)
    for key, values in forward.items():
        for value in values:
            incoming[value].add(key)
    pairs = {}
    for key, values in forward.items():
        if len(key) != 1 or len(values) != 1 or len(values[0]) != 1:
            continue
        value = values[0]
        if (key == value or backward.get(value) != (key,)
                or incoming[value] != {key}
                or forward.get(value, (value,)) != (value,)
                or unicodedata.normalize("NFKC", key) != key
                or unicodedata.normalize("NFKC", value) != value):
            continue
        pairs[key] = value
    return pairs


@lru_cache(maxsize=1)
def _translation_table():
    dictionaries = []
    for name, expected in _SOURCES.items():
        # Universal newline decoding permits a normal Windows Git checkout.
        text = (Path(__file__).parent / "data" / "opencc" / name).read_text(encoding="utf-8")
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != expected:
            raise ValueError("Han dictionary differs from frozen comparison policy")
        dictionaries.append(_read_dictionary(text))
    return str.maketrans(bijective_pairs(*dictionaries))


def fold_unambiguous_han(text: str) -> str:
    return text.translate(_translation_table())
