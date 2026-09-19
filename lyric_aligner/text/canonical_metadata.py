"""Auditable preparation for explicitly identified performer prefixes.

This module creates a new canonical line-LRC input.  It never edits a bound
canonical file in place, guesses performer roles, accepts replacement lyrics, or
changes lyric timestamps.  Prefix interpretation must be supplied by an
external, hash-bound role map.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Mapping

POLICY_ID = "explicit-canonical-performer-prefix-preparation-2026-09-10-v2"
LINE = re.compile(r"^(\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\])(.*)$")


def _valid_prefix(value: object) -> bool:
    if not isinstance(value, str) or not value or len(value) > 20:
        return False
    core = value[:-1] if value.endswith((":", "：")) else value
    if not core:
        return False
    return all(char == "." or unicodedata.category(char).startswith("L") for char in core)


def _unmarked_boundary_is_safe(suffix: str) -> bool:
    """Allow an unmarked role only before a non-ASCII letter script boundary.

    This covers Han/Kana/Hangul and other Unicode letter scripts while refusing
    ambiguous ASCII word prefixes such as ``H`` in ``Hoh`` or ``Ella`` in
    ``Ella's``.  A colon-marked prefix does not use this rule.
    """
    if not suffix:
        return False
    first = suffix[0]
    return not first.isascii() and unicodedata.category(first).startswith("L")


def prepare_canonical_metadata(source: Path, mapping: Mapping[str, Any]) -> tuple[str, dict]:
    if set(mapping) != {"source_sha256", "prefixes", "evidence"}:
        raise ValueError("role map must contain only source_sha256, prefixes and evidence")
    source_bytes = source.read_bytes()
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    if mapping["source_sha256"] != source_sha:
        raise ValueError("role map source SHA mismatch")

    prefixes = mapping["prefixes"]
    if (
        not isinstance(prefixes, list)
        or not prefixes
        or any(not _valid_prefix(prefix) for prefix in prefixes)
        or len(prefixes) != len(set(prefixes))
    ):
        raise ValueError("role prefixes must be distinct bounded Unicode-letter labels")
    if not isinstance(mapping["evidence"], str) or not mapping["evidence"].strip():
        raise ValueError("role interpretation requires a source-evidence note")

    raw = source_bytes.decode("utf-8-sig")
    old_times = [match[1] for line in raw.splitlines() if (match := LINE.match(line.lstrip()))]
    if not old_times:
        raise ValueError("performer prefix preparation requires line-LRC timestamp rows")

    result: list[str] = []
    changes: list[dict[str, object]] = []
    package_rows: list[int] = []
    for position, line in enumerate(raw.splitlines(), 1):
        stripped = line.lstrip()
        leading = line[: len(line) - len(stripped)]
        if stripped.startswith("[awlrc:"):
            # The source retains its opaque package.  A prepared line-LRC must
            # not also carry a stale encoded copy of the pre-preparation text.
            package_rows.append(position)
            continue
        if re.match(r"^\[\d+,\d+\]", stripped):
            raise ValueError("performer prefix preparation currently requires line LRC")
        match = LINE.match(stripped)
        if match:
            timestamp, body = match.groups()
            if re.search(r"<\d+[:,]\d+", body):
                raise ValueError("performer prefix preparation cannot rewrite timed tokens")
            for prefix in sorted(prefixes, key=len, reverse=True):
                if not body.startswith(prefix):
                    continue
                suffix = body[len(prefix) :]
                allowed = bool(suffix.strip()) if prefix.endswith((":", "：")) else _unmarked_boundary_is_safe(suffix)
                if not allowed:
                    continue
                changes.append(
                    {
                        "line": position,
                        "timestamp": timestamp,
                        "removed_prefix": prefix,
                        "before": body,
                        "after": suffix,
                    }
                )
                line = leading + timestamp + suffix
                break
        result.append(line)

    prepared = "\n".join(result) + ("\n" if raw.endswith(("\n", "\r")) else "")
    new_times = [match[1] for line in prepared.splitlines() if (match := LINE.match(line.lstrip()))]
    if old_times != new_times:
        raise AssertionError("canonical preparation changed timestamps")

    mapping_payload = json.dumps(
        dict(mapping), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return prepared, {
        "policy_id": POLICY_ID,
        "source_sha256": source_sha,
        "role_map_sha256": hashlib.sha256(mapping_payload).hexdigest(),
        "output_sha256": hashlib.sha256(prepared.encode("utf-8")).hexdigest(),
        "timestamps_immutable": True,
        "removed_prefix_count": len(changes),
        "changes": changes,
        "opaque_package_rows_omitted": package_rows,
        "evidence": mapping["evidence"],
        "timing_authority_granted": False,
    }
