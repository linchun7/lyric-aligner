"""Low-level lyric text normalization shared across v4 domains."""

from __future__ import annotations

import re

# Consumer LRC files commonly timestamp credits just like lyric lines. Keep the
# list explicit so ordinary lyric prose is not removed merely because it appears
# early in a file. Both ASCII and full-width Chinese colons are accepted.
META_RE = re.compile(
    r"^(?:"
    r"\[?by\s*[:：]|"
    r"(?:作词|作曲|编曲|词|曲|制作人|人声采样|版权|发行|混音|母带|企划|出品人|"
    r"监制|和声编写|和声|录音师|录音棚|音频编辑|统筹|宣传发行|出品方|出品|"
    r"吉他|贝斯|鼓|配唱制作人|制作助理|录音|合唱录音棚|Atmos\s*混音|"
    r"曲\s*OP|词\s*OP|OP|SP|商务合作)\s*[:：]"
    r")",
    re.IGNORECASE,
)

# Singer/role labels such as ``Felix Bennett：`` or ``h3R3:`` may also carry a
# timestamp. Restrict the generic form to Latin/digit names so Chinese lyric
# sentences ending in a colon are not broadly classified as metadata.
ROLE_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._&+/\-]{0,40}\s*[:：]$")


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def is_metadata_text(value: str) -> bool:
    normalized = clean_text(value)
    return bool(META_RE.match(normalized) or ROLE_LABEL_RE.match(normalized))