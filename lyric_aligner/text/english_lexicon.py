"""Reproducibly extend a bound HuBERTFA English dictionary from CMUdict.

The builder is intentionally lexical rather than predictive.  It preserves the
bound dictionary byte-for-byte, adds only tokenizer-proven terminal-apostrophe
aliases and exact CMUdict word entries, and refuses competing pronunciations.
"""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from lyric_aligner.text.alignment_lexical import english_units


ENGLISH_LEXICON_BUILDER_ID = "hfa-english-lexicon-bound-cmudict-v1"
ENGLISH_LEXICON_SCHEMA_VERSION = "hfa-english-lexicon-build-1.0"
ENGLISH_LEXICON_POLICY = {
    "base_dictionary_entries_preserved_byte_for_byte": True,
    "allowed_new_entry_sources": ["bound_dictionary_terminal_apostrophe_alias", "cmudict_exact_token"],
    "external_pronunciation_variants": "stress_normalized_exactly_one_or_reject",
    "neural_g2p": "forbidden",
    "compound_splitting": "forbidden",
    "song_specific_inputs": "forbidden",
}
_STRESS_RE = re.compile(r"\d")
_CMUDICT_VARIANT_RE = re.compile(r"^(.*)\(([1-9][0-9]*)\)$")


class EnglishLexiconBuildError(ValueError):
    """A requested output could not preserve the frozen lexical contract."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _json_sha(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _normal_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold().replace("’", "'").strip()


def _exact_lexical_token(value: str) -> str | None:
    """Return a token only if the project tokenizer preserves the full label."""
    normalized = _normal_text(value)
    try:
        units = english_units(normalized)
    except ValueError:
        return None
    return normalized if units == [normalized] else None


def _terminal_apostrophe_alias(value: str) -> str | None:
    """Allow only ``token'`` -> ``token`` using the project tokenizer."""
    normalized = _normal_text(value)
    if not normalized.endswith("'"):
        return None
    candidate = normalized[:-1]
    if not candidate or _exact_lexical_token(candidate) != candidate:
        return None
    try:
        return candidate if english_units(normalized) == [candidate] else None
    except ValueError:
        return None


def _phones_in_vocab(vocab: Mapping[str, Any]) -> set[str]:
    raw = vocab.get("vocab")
    if not isinstance(raw, Mapping):
        raise EnglishLexiconBuildError("HuBERTFA vocab has no vocab mapping")
    phones = {str(key).split("/", 1)[1] for key in raw if str(key).startswith("en/")}
    if not phones:
        raise EnglishLexiconBuildError("HuBERTFA vocab has no English phones")
    return phones


def _normalize_phones(phones: Iterable[str]) -> tuple[str, ...]:
    result = tuple(_STRESS_RE.sub("", str(phone).strip().casefold()) for phone in phones)
    if not result or any(not phone for phone in result):
        raise EnglishLexiconBuildError("dictionary entry has no usable phones")
    return result


def _parse_base_dictionary(path: Path) -> tuple[bytes, list[tuple[str, tuple[str, ...]]]]:
    raw = path.read_bytes()
    try:
        lines = raw.decode("utf-8-sig").splitlines()
    except UnicodeDecodeError as exc:
        raise EnglishLexiconBuildError("base dictionary is not UTF-8") from exc
    entries: list[tuple[str, tuple[str, ...]]] = []
    for line_number, line in enumerate(lines, 1):
        if not line:
            continue
        if "\t" not in line:
            raise EnglishLexiconBuildError(f"base dictionary line {line_number} has no tab separator")
        key, phones = line.split("\t", 1)
        key = key.strip()
        if not key:
            raise EnglishLexiconBuildError(f"base dictionary line {line_number} has an empty key")
        entries.append((key, _normalize_phones(phones.split())))
    if not entries:
        raise EnglishLexiconBuildError("base dictionary has no entries")
    return raw, entries


def _parse_cmudict(path: Path) -> tuple[dict[str, list[tuple[str, tuple[str, ...]]]], dict[str, int]]:
    """Group direct CMU entries and numbered alternatives by lexical token."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise EnglishLexiconBuildError("CMUdict is not UTF-8") from exc
    entries: dict[str, list[tuple[str, tuple[str, ...]]]] = defaultdict(list)
    stats = {"cmudict_comment_or_blank_line_count": 0, "cmudict_nonlexical_label_count": 0,
             "cmudict_malformed_line_count": 0}
    for line in lines:
        body = line.split("#", 1)[0].strip()
        if not body or body.startswith(";;;"):
            stats["cmudict_comment_or_blank_line_count"] += 1
            continue
        parts = body.split()
        if len(parts) < 2:
            stats["cmudict_malformed_line_count"] += 1
            continue
        raw_label = _normal_text(parts[0])
        variant = _CMUDICT_VARIANT_RE.fullmatch(raw_label)
        lexical_label = variant.group(1) if variant else raw_label
        token = _exact_lexical_token(lexical_label)
        # A numbered CMU alternative is an alternative pronunciation of the
        # same exact lexical token, not a punctuation-normalization rule.
        if token is None:
            stats["cmudict_nonlexical_label_count"] += 1
            continue
        try:
            phones = _normalize_phones(parts[1:])
        except EnglishLexiconBuildError:
            stats["cmudict_malformed_line_count"] += 1
            continue
        entries[token].append((raw_label, phones))
    return dict(entries), stats


def _append_base_bytes(base_bytes: bytes, additions: list[tuple[str, tuple[str, ...]]]) -> bytes:
    if not additions:
        return base_bytes
    separator = b"" if base_bytes.endswith((b"\n", b"\r")) else b"\n"
    encoded = "".join(f"{token}\t{' '.join(phones)}\n" for token, phones in additions).encode("utf-8")
    return base_bytes + separator + encoded


def build_english_lexicon(
    *,
    base_dictionary_path: Path,
    vocab_path: Path,
    output_dictionary_path: Path,
    output_manifest_path: Path,
    cmudict_path: Path | None = None,
) -> dict[str, Any]:
    """Build a complete, independently bound dictionary without mutating base.

    No canonical lyric, song title, model prediction, compound splitting, or
    arbitrary spelling rule is an input to this function.
    """
    base_dictionary_path = Path(base_dictionary_path).resolve()
    vocab_path = Path(vocab_path).resolve()
    output_dictionary_path = Path(output_dictionary_path).resolve()
    output_manifest_path = Path(output_manifest_path).resolve()
    cmudict_path = Path(cmudict_path).resolve() if cmudict_path is not None else None
    if not base_dictionary_path.is_file() or not vocab_path.is_file():
        raise EnglishLexiconBuildError("base dictionary and vocab must be regular files")
    if cmudict_path is not None and not cmudict_path.is_file():
        raise EnglishLexiconBuildError("CMUdict must be a regular file when provided")
    inputs = {base_dictionary_path, vocab_path}
    if cmudict_path is not None:
        inputs.add(cmudict_path)
    if output_dictionary_path in inputs or output_manifest_path in inputs:
        raise EnglishLexiconBuildError("generated output paths must be distinct from every bound input")
    if output_manifest_path == output_dictionary_path:
        raise EnglishLexiconBuildError("manifest path must be distinct from generated dictionary output")
    if output_dictionary_path.exists() or output_manifest_path.exists():
        raise EnglishLexiconBuildError("generated dictionary and manifest paths must be new; existing artifacts are retained")

    base_bytes, base_entries = _parse_base_dictionary(base_dictionary_path)
    try:
        vocab = json.loads(vocab_path.read_text(encoding="utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EnglishLexiconBuildError("HuBERTFA vocab JSON is invalid") from exc
    allowed_phones = _phones_in_vocab(vocab)
    base_unknown = sorted({phone for _key, phones in base_entries for phone in phones if phone not in allowed_phones})
    if base_unknown:
        raise EnglishLexiconBuildError("bound base dictionary contains phones absent from vocab: " + ",".join(base_unknown))

    base_exact_tokens: set[str] = set()
    aliases: dict[str, set[tuple[str, ...]]] = defaultdict(set)
    alias_sources: dict[str, set[str]] = defaultdict(set)
    for key, phones in base_entries:
        exact = _exact_lexical_token(key)
        if exact is not None:
            base_exact_tokens.add(exact)
        alias = _terminal_apostrophe_alias(key)
        if alias is not None:
            aliases[alias].add(phones)
            alias_sources[alias].add(key)

    additions: list[tuple[str, tuple[str, ...]]] = []
    added_entries: list[dict[str, Any]] = []
    stats: dict[str, int] = {
        "base_line_count": len(base_entries),
        "base_exact_lexical_token_count": len(base_exact_tokens),
        "terminal_apostrophe_alias_candidate_count": len(aliases),
        "terminal_apostrophe_alias_added_count": 0,
        "terminal_apostrophe_alias_ambiguous_rejected_count": 0,
        "terminal_apostrophe_external_conflict_rejected_count": 0,
        "cmudict_candidate_token_count": 0,
        "cmudict_added_count": 0,
        "cmudict_ambiguous_pronunciation_rejected_count": 0,
        "cmudict_phone_outside_vocab_rejected_count": 0,
        "cmudict_base_authoritative_skipped_count": 0,
        "cmudict_alias_authoritative_skipped_count": 0,
    }

    cmu_stats: dict[str, int] = {}
    cmudict_entries: dict[str, list[tuple[str, tuple[str, ...]]]] = {}
    if cmudict_path is not None:
        cmudict_entries, cmu_stats = _parse_cmudict(cmudict_path)
        stats["cmudict_candidate_token_count"] = len(cmudict_entries)

    alias_added: set[str] = set()
    alias_or_external_rejected: set[str] = set()
    for token in sorted(aliases):
        if token in base_exact_tokens:
            continue
        variants = aliases[token]
        if len(variants) != 1:
            stats["terminal_apostrophe_alias_ambiguous_rejected_count"] += 1
            alias_or_external_rejected.add(token)
            continue
        external_variants = {phones for _label, phones in cmudict_entries.get(token, [])}
        if external_variants and external_variants != variants:
            # Do not use lexical spelling to silently select a pronunciation
            # between the bound alias and the independent direct source.
            stats["terminal_apostrophe_external_conflict_rejected_count"] += 1
            alias_or_external_rejected.add(token)
            continue
        phones = next(iter(variants))
        additions.append((token, phones))
        alias_added.add(token)
        stats["terminal_apostrophe_alias_added_count"] += 1
        added_entries.append({"token": token, "phones": list(phones),
                              "source_kind": "bound_dictionary_terminal_apostrophe_alias",
                              "source_keys": sorted(alias_sources[token])})

    if cmudict_path is not None:
        for token in sorted(cmudict_entries):
            if token in base_exact_tokens:
                stats["cmudict_base_authoritative_skipped_count"] += 1
                continue
            if token in alias_or_external_rejected:
                stats["cmudict_alias_authoritative_skipped_count"] += 1
                continue
            if token in alias_added:
                stats["cmudict_alias_authoritative_skipped_count"] += 1
                continue
            entries = cmudict_entries[token]
            variants = {phones for _label, phones in entries}
            if len(variants) != 1:
                stats["cmudict_ambiguous_pronunciation_rejected_count"] += 1
                continue
            phones = next(iter(variants))
            if any(phone not in allowed_phones for phone in phones):
                stats["cmudict_phone_outside_vocab_rejected_count"] += 1
                continue
            additions.append((token, phones))
            stats["cmudict_added_count"] += 1
            added_entries.append({"token": token, "phones": list(phones), "source_kind": "cmudict_exact_token",
                                  "source_keys": sorted({label for label, _phones in entries})})

    additions.sort(key=lambda item: item[0])
    added_entries.sort(key=lambda item: item["token"])
    output_bytes = _append_base_bytes(base_bytes, additions)
    output_dictionary_path.parent.mkdir(parents=True, exist_ok=True)
    output_dictionary_path.write_bytes(output_bytes)

    tokenizer_path = Path(__file__).with_name("alignment_lexical.py").resolve()
    manifest = {
        "schema_version": ENGLISH_LEXICON_SCHEMA_VERSION,
        "builder_id": ENGLISH_LEXICON_BUILDER_ID,
        "policy": dict(ENGLISH_LEXICON_POLICY),
        "inputs": {
            "base_dictionary": {"path": str(base_dictionary_path), "sha256": sha256_file(base_dictionary_path)},
            "vocab": {"path": str(vocab_path), "sha256": sha256_file(vocab_path)},
            "cmudict": (None if cmudict_path is None else {"path": str(cmudict_path), "sha256": sha256_file(cmudict_path)}),
            "lexical_tokenizer": {"path": str(tokenizer_path), "sha256": sha256_file(tokenizer_path)},
            "builder_module": {"path": str(Path(__file__).resolve()), "sha256": sha256_file(Path(__file__))},
        },
        "output_dictionary": {"path": str(output_dictionary_path), "sha256": sha256_file(output_dictionary_path)},
        "stats": {**stats, **cmu_stats, "added_entry_count": len(added_entries)},
        "added_entries": added_entries,
    }
    manifest["artifact_sha256"] = _json_sha(manifest)
    output_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    output_manifest_path.write_text(_canonical_json(manifest) + "\n", encoding="utf-8")
    return manifest


def _verify_binding(value: Any, path: Path, *, label: str) -> None:
    if not isinstance(value, Mapping) or set(value) != {"path", "sha256"}:
        raise EnglishLexiconBuildError(label + " binding is invalid")
    if str(path.resolve()) != value["path"] or sha256_file(path) != value["sha256"]:
        raise EnglishLexiconBuildError(label + " binding does not match the current file")


def verify_derived_english_lexicon(
    *,
    dictionary_path: Path,
    manifest_path: Path,
    base_dictionary_path: Path,
    vocab_path: Path,
) -> dict[str, Any]:
    """Verify provenance and deterministically rebuild a derived dictionary.

    This accepts no free-form pronunciation source: every source path and hash
    in the manifest is checked, then the tracked builder recreates the complete
    output in a temporary directory and compares its exact bytes.
    """
    dictionary_path = Path(dictionary_path).resolve()
    manifest_path = Path(manifest_path).resolve()
    base_dictionary_path = Path(base_dictionary_path).resolve()
    vocab_path = Path(vocab_path).resolve()
    if not dictionary_path.is_file() or not manifest_path.is_file():
        raise EnglishLexiconBuildError("derived dictionary and manifest must be regular files")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EnglishLexiconBuildError("derived dictionary manifest JSON is invalid") from exc
    if not isinstance(manifest, Mapping):
        raise EnglishLexiconBuildError("derived dictionary manifest must be an object")
    expected_self = _json_sha({key: value for key, value in manifest.items() if key != "artifact_sha256"})
    if manifest.get("artifact_sha256") != expected_self:
        raise EnglishLexiconBuildError("derived dictionary manifest self hash mismatch")
    if (manifest.get("schema_version") != ENGLISH_LEXICON_SCHEMA_VERSION
            or manifest.get("builder_id") != ENGLISH_LEXICON_BUILDER_ID
            or manifest.get("policy") != ENGLISH_LEXICON_POLICY):
        raise EnglishLexiconBuildError("derived dictionary builder policy identity mismatch")
    inputs = manifest.get("inputs")
    if not isinstance(inputs, Mapping):
        raise EnglishLexiconBuildError("derived dictionary manifest inputs are invalid")
    _verify_binding(inputs.get("base_dictionary"), base_dictionary_path, label="derived base dictionary")
    _verify_binding(inputs.get("vocab"), vocab_path, label="derived vocab")
    _verify_binding(inputs.get("lexical_tokenizer"), Path(__file__).with_name("alignment_lexical.py"),
                    label="derived lexical tokenizer")
    _verify_binding(inputs.get("builder_module"), Path(__file__), label="derived builder module")
    cmu_binding = inputs.get("cmudict")
    if cmu_binding is not None:
        if not isinstance(cmu_binding, Mapping) or set(cmu_binding) != {"path", "sha256"}:
            raise EnglishLexiconBuildError("derived CMUdict binding is invalid")
        cmudict_path = Path(str(cmu_binding["path"])).resolve()
        if not cmudict_path.is_file() or sha256_file(cmudict_path) != cmu_binding["sha256"]:
            raise EnglishLexiconBuildError("derived CMUdict binding does not match the current file")
    else:
        cmudict_path = None
    _verify_binding(manifest.get("output_dictionary"), dictionary_path, label="derived output dictionary")
    stats = manifest.get("stats")
    added_entries = manifest.get("added_entries")
    if not isinstance(stats, Mapping) or not isinstance(added_entries, list):
        raise EnglishLexiconBuildError("derived dictionary manifest build result is invalid")
    with tempfile.TemporaryDirectory(prefix="hfa-english-lexicon-verify-") as temporary:
        root = Path(temporary)
        rebuilt = build_english_lexicon(base_dictionary_path=base_dictionary_path, vocab_path=vocab_path,
                                        cmudict_path=cmudict_path, output_dictionary_path=root / "dictionary.txt",
                                        output_manifest_path=root / "manifest.json")
        if (root / "dictionary.txt").read_bytes() != dictionary_path.read_bytes():
            raise EnglishLexiconBuildError("derived dictionary deterministic rebuild bytes mismatch")
    if rebuilt["stats"] != dict(stats) or rebuilt["added_entries"] != added_entries:
        raise EnglishLexiconBuildError("derived dictionary deterministic rebuild provenance mismatch")
    return dict(manifest)


__all__ = ["ENGLISH_LEXICON_BUILDER_ID", "ENGLISH_LEXICON_SCHEMA_VERSION", "ENGLISH_LEXICON_POLICY",
           "EnglishLexiconBuildError", "build_english_lexicon", "verify_derived_english_lexicon", "sha256_file"]
