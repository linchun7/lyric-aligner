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

# Singer/role labels may carry their own timestamps.  Consumer LRC files use
# Latin stage names, Chinese personal names, role words, slash-separated casts,
# and optional parenthesized parts such as ``(Rap)``.  Keep the single-CJK-name
# branch surname-bounded so short lyric questions such as ``为什么：`` are not
# broadly discarded merely because they end in a colon.
ROLE_LABEL_RE = re.compile(
    r"^(?:"
    r"[A-Za-z0-9][A-Za-z0-9 ._&+/\-（）()\u3400-\u9fff]{0,60}|"
    r"(?:合|齐唱|主唱|男|女|男声|女声|独白|和声|说唱|Rap|Chorus)|"
    r"(?:欧阳|司马|上官|诸葛|东方|独孤|南宫|夏侯|皇甫|尉迟|公孙|慕容|令狐|长孙|宇文|司徒|司空|端木)[\u3400-\u9fff]{1,3}|"
    r"[赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐费廉岑薛雷贺倪汤滕殷罗毕郝鞍安常乐于时傅皮卞齐康伍余元卜顾孟平黄和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍虞万支柯管卢莫柯房裘缪干解应宗丁宣贲邓郁单杭洪包诸左石崔吉钮龚程嵇邢滑裴陆荣翁荀羊惠甄曲家封芈羌储靡松井段富巫乌焦巴弓牧隗山谷车侯宓蓬全郗班仰秋仲伊宫宁仇栾暴甘钞厉戎祖武符刘景詹束龙叶幸司韶郜黎蓟薄印宿白怀蒲邰从鄂索咸籍赖卓蔺屠蒙池乔阴郁胥能苍双闻莘党翟谭贡劳逄姬申扶堵冉宰郦雍桑桂濮牛寿通边扈燕冀郏浦尚农温别庄晏柴瞿阎充慕连茭习宦艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘匡国文寇广禄阙东殴殃沃利蔚越夔隆师巩厍聂晁勾敖融冷訾辛阚那简饶空曾毋沙乍养鞠须丰巢关蒯相查后荆红游竺权盖益桓公][\u3400-\u9fff]{1,3}|"
    r"[\u3400-\u9fffA-Za-z0-9._+\-]{1,20}(?:[/、&+][\u3400-\u9fffA-Za-z0-9._+\-]{1,20})+"
    r")(?:\s*[（(](?:Rap|说唱|主唱|和声|合唱|独白)[）)])?\s*[:：]$",
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
