"""Low-level lyric text normalization shared across v4 domains."""

from __future__ import annotations

import re

META_RE = re.compile(
    r"^(?:\[?by:|作词|作曲|编曲|词\s*:|曲\s*:|制作人|人声采样|版权|发行|混音|母带|企划|出品人|op\s*:|sp\s*:)",
    re.IGNORECASE,
)


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def is_metadata_text(value: str) -> bool:
    return bool(META_RE.match(clean_text(value)))
