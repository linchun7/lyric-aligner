"""Low-level lyric text normalization shared across v4 domains."""

from __future__ import annotations

import re
from collections.abc import Iterable

# Consumer LRC files commonly timestamp credits just like lyric lines. Keep the
# list explicit so ordinary lyric prose is not removed merely because it appears
# early in a file. Both ASCII and full-width Chinese colons are accepted.
META_RE = re.compile(
    r"^(?:"
    r"\[?by\s*[:：]|"
    r"(?:作词|作曲|编曲|词|曲|制作人|人声采样|版权|发行|混音|母带|企划|出品人|"
    r"监制|和声编写|和声|录音师|录音棚|音频编辑|统筹|宣传发行|出品方|出品|"
    r"吉他|贝斯|鼓|配唱制作人|制作助理|录音|合唱录音棚|Atmos\s*混音|"
    r"曲\s*OP|词\s*OP|OP|SP|商务合作)\s*[:：]|"
    r"(?:lyrics?|words|music|composed?|composition|written|produced?|producer|"
    r"arranged?|arrangement|original\s+publisher|sub-?publisher|publishing|"
    r"all\s+instruments?|piano|keyboards?|synth(?:esizer)?|guitars?|bass|trumpet|"
    r"flugelhorn|trombone|(?:tenor|alto|baritone|bari)(?:\s+(?:and\s+)?(?:tenor|alto|baritone|bari))*\s+sax(?:ophone)?|"
    r"drums?|percussion|programming|background\s+vocals?|backing\s+vocals?|"
    r"vocal(?:s)?\s+(?:arrangement|direction|directed|production)|digital\s+editing|"
    r"record(?:ed|ing)(?:\s+engineers?)?|mix(?:ed|ing|\s+engineer(?:ed)?|\s+assisted)?(?:\s+in\s+dolby\s+atmos)?|"
    r"master(?:ed|ing)|engineer(?:ed|ing)?)"
    r"(?:\s+by)?\s*[:：]|"
    r"(?:mixed|mastered|recorded)\s+at\s+[^\n]*(?:studio|studios|mastering|recording|mix\s+room|sound\s+lab)\b"
    r")",
    re.IGNORECASE,
)

# Bare provider section markers made only from two or more instrument names are
# non-lyric annotations. Requiring the whole line to be instrument names joined
# by a separator avoids removing ordinary lyric prose that merely mentions an
# instrument.
_INSTRUMENT_NAME = (
    r"(?:bass|drums?|percussion|piano|keyboards?|synth(?:esizer)?|guitars?|"
    r"horns?|trumpet|flugelhorn|trombone|sax(?:ophone)?)"
)
INSTRUMENT_SECTION_RE = re.compile(
    rf"^{_INSTRUMENT_NAME}(?:\s*(?:and|&|/|\+)\s*{_INSTRUMENT_NAME})+$",
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

MULTI_ROLE_LABEL_RE = re.compile(
    r"^(?P<cast>[\u3400-\u9fffA-Za-z0-9._+\-]{1,20}"
    r"(?:[/、&+][\u3400-\u9fffA-Za-z0-9._+\-]{1,20})+)"
    r"(?:\s*[（(](?:Rap|说唱|主唱|和声|合唱|独白)[）)])?\s*[:：]$",
    re.IGNORECASE,
)
BARE_CJK_ROLE_LABEL_RE = re.compile(r"^(?P<name>[\u3400-\u9fff]{2,6})\s*[:：]$")
ROLE_MEMBER_SPLIT_RE = re.compile(r"[/、&+]")


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def explicit_multi_role_names(values: Iterable[str]) -> frozenset[str]:
    """Collect CJK cast members proved by explicit multi-person role rows."""

    names: set[str] = set()
    for value in values:
        match = MULTI_ROLE_LABEL_RE.match(clean_text(value))
        if match is None:
            continue
        for member in ROLE_MEMBER_SPLIT_RE.split(match.group("cast")):
            if re.fullmatch(r"[\u3400-\u9fff]{2,6}", member):
                names.add(member)
    return frozenset(names)


def contextual_cjk_role_names(
    entries: Iterable[tuple[int | None, str]],
) -> frozenset[str]:
    """Infer only repeated bare roles inside a strongly proved ensemble grammar.

    Explicit multi-person rows remain the primary proof.  An additional bare
    name is accepted only when at least four distinct bare labels already match
    that cast, the candidate repeats, and every occurrence is immediately
    followed by lexical text within two seconds.
    """

    rows = sorted(
        ((timestamp, clean_text(text)) for timestamp, text in entries if clean_text(text)),
        key=lambda row: (row[0] is None, row[0] if row[0] is not None else 0),
    )
    explicit = explicit_multi_role_names(text for _, text in rows)
    proved_bare_names = {
        match.group("name")
        for _, text in rows
        if (match := BARE_CJK_ROLE_LABEL_RE.match(text)) is not None
        and match.group("name") in explicit
    }
    if len(proved_bare_names) < 4:
        return explicit

    qualifying_counts: dict[str, int] = {}
    total_counts: dict[str, int] = {}
    for index, (timestamp, text) in enumerate(rows):
        match = BARE_CJK_ROLE_LABEL_RE.match(text)
        if match is None or match.group("name") in explicit:
            continue
        name = match.group("name")
        total_counts[name] = total_counts.get(name, 0) + 1
        if timestamp is None or index + 1 >= len(rows):
            continue
        next_timestamp, next_text = rows[index + 1]
        if (
            next_timestamp is not None
            and 0 < next_timestamp - timestamp <= 2000
            and not is_metadata_text(next_text, contextual_role_names=explicit)
            and BARE_CJK_ROLE_LABEL_RE.match(next_text) is None
        ):
            qualifying_counts[name] = qualifying_counts.get(name, 0) + 1

    inferred = {
        name
        for name, count in total_counts.items()
        if count >= 2 and qualifying_counts.get(name, 0) == count
    }
    return frozenset(set(explicit) | inferred)


def is_metadata_text(
    value: str,
    *,
    contextual_role_names: frozenset[str] = frozenset(),
) -> bool:
    normalized = clean_text(value)
    if (
        META_RE.match(normalized)
        or INSTRUMENT_SECTION_RE.match(normalized)
        or ROLE_LABEL_RE.match(normalized)
    ):
        return True
    bare = BARE_CJK_ROLE_LABEL_RE.match(normalized)
    return bool(bare and bare.group("name") in contextual_role_names)


def is_title_like_intro(start_ms: int, text: str) -> bool:
    """Recognize the common early ``artist - title`` consumer-LRC row."""

    return int(start_ms) <= 1000 and " - " in clean_text(text)
