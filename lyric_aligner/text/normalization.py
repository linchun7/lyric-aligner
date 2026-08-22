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

# Singer/role labels may carry their own timestamps.  A bare CJK name cannot be
# distinguished safely from short lexical lyrics by a surname table, so it is
# retained unless an explicit role marker or multi-person separator supplies
# stronger metadata evidence.
ROLE_LABEL_RE = re.compile(
    r"^(?:"
    r"(?:合|齐唱|主唱|男|女|男声|女声|独白|和声|说唱|Rap|Chorus)|"
    r"(?:[A-Za-z0-9][A-Za-z0-9 ._&+/\-（）()\u3400-\u9fff]{0,60}|"
    r"[\u3400-\u9fffA-Za-z0-9._+\-]{1,20}(?:[/、&+][\u3400-\u9fffA-Za-z0-9._+\-]{1,20})+)"
    r"(?:\s*[（(](?:Rap|说唱|主唱|和声|合唱|独白)[）)])?|"
    r"[\u3400-\u9fff]{1,20}\s*[（(](?:Rap|说唱|主唱|和声|合唱|独白)[）)]"
    r")\s*[:：]$",
    re.IGNORECASE,
)


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def is_metadata_text(value: str) -> bool:
    normalized = clean_text(value)
    return bool(META_RE.match(normalized) or ROLE_LABEL_RE.match(normalized))


def is_title_like_intro(start_ms: int, text: str) -> bool:
    """Recognize the common early ``artist - title`` consumer-LRC row."""

    return int(start_ms) <= 1000 and " - " in clean_text(text)
