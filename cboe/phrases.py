# -*- coding: utf-8 -*-
"""Korean caption phrases (문구) for sound classes and music moods.

Sound classes: ``data/sfx_classes.tsv`` (built by tools/build_phrases.py, hand-checked):
설명 문구 ``desc`` ('천둥 소리'), 의성어 ``ono`` ('우르릉'), policy ``category``
(event / ambient / minor / vocal / speech / music / background).
The notation comes from ``cboe.style`` (넷플릭스 II.9: "Use onomatopoeic wording to create more
intuitive descriptions when possible" = 될 수 있으면 의성어로 적는다):
- 기본 문구 ``[ono]`` → ``[우르릉]``; 의성어가 없는 종류는 ``[desc]`` → ``[재채기]``
- 의성어와 출처 ``[ono - 출처]`` → ``[우르릉 - 천둥]`` (출처를 명사로 댈 수 있는 종류만)
- 설명 문구 ``[desc]`` → ``[천둥 소리]`` (고를 수 있는 선택지; '소리'로 끝맺기는 지침이 피하라는 꼴)
- '[의성어, 설명]' 쉼표 꼴은 자동으로 만들지 않는다 (지침에 없는 꼴).

Music (10-13 메모 3.1, 넷플릭스 II.7: 대괄호, 객관적인 표현, '행복한' 대신 '경쾌한'):
``[경쾌한 음악]``, 분위기를 모르면 ``[음악]``.
"""
from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path

from . import style

DATA = Path(__file__).resolve().parent / "data"

MOODS = {
    "Scary music": "무서운",
    "Exciting music": "신나는",
    "Sad music": "슬픈",
    "Happy music": "경쾌한",
    "Funny music": "익살스러운",
    "Tender music": "부드러운",
    "Angry music": "격렬한",
}
# moods that should not be offered together as one reading (성격이 다른 분위기)
MOOD_GROUP = {"Scary music": "dark", "Angry music": "dark", "Sad music": "dark",
              "Exciting music": "bright", "Happy music": "bright", "Funny music": "bright",
              "Tender music": "calm"}
INSTRUMENTS = {
    "Piano": "피아노 연주", "Guitar": "기타 연주", "Acoustic guitar": "기타 연주", "Electric guitar": "전자 기타 연주",
    "Violin, fiddle": "바이올린 연주", "Cello": "첼로 연주", "Bowed string instrument": "현악기 연주",
    "Orchestra": "오케스트라 연주", "Drum": "드럼 연주", "Drum kit": "드럼 연주", "Organ": "오르간 연주",
    "Harp": "하프 연주", "Flute": "플루트 연주", "Saxophone": "색소폰 연주", "Trumpet": "트럼펫 연주",
    "Synthesizer": "신시사이저 연주", "Music box": "오르골 소리", "Accordion": "아코디언 연주",
    "Bell": "종소리", "Choir": "합창", "Whistling": "휘파람",
}
CHANGES = ("고조되는", "잦아드는")


@lru_cache(maxsize=1)
def table() -> dict[str, dict]:
    with open(DATA / "sfx_classes.tsv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    out = {}
    for r in rows:
        r["min_on_s"] = float(r.get("min_on_s") or 0.2)
        r["merge_gap_s"] = float(r.get("merge_gap_s") or 0.5)
        out[r["class"]] = r
    return out


def info(cls: str) -> dict:
    return table().get(cls, {"class": cls, "desc": "", "ono": "", "family": "", "category": "background",
                             "min_on_s": 0.2, "merge_gap_s": 0.5})


def category(cls: str) -> str:
    return info(cls)["category"]


def ko(cls: str) -> str:
    """Short Korean name for lists ('자동차 경적', '천둥')."""
    d = info(cls)["desc"] or cls
    for suf in (" 소리", "소리"):
        if d.endswith(suf) and len(d) > len(suf):
            return d[: -len(suf)].strip()
    return d


_SAI = {"빗": "비", "노랫": "노래"}          # 사이시옷: 빗소리 = 비 + 소리


@lru_cache(maxsize=1)
def _sound_words() -> frozenset:
    return frozenset(r["ono"] for r in table().values() if r["ono"])


def source(cls: str) -> str | None:
    """What makes the sound, as a noun for '[의성어 - 출처]' ('천둥', '지퍼', '자동차 경적');
    None when the table's description does not name one: '개 짖는 소리' (not a noun),
    '쾅 소리' (the sound word itself)."""
    d, ono = info(cls)["desc"], info(cls)["ono"]
    for suf in (" 소리", "소리"):
        if d.endswith(suf) and len(d) > len(suf):
            d = d[: -len(suf)].strip()
            break
    head, _, last = d.rpartition(" ")
    d = (head + " " if head else "") + _SAI.get(last, last)
    if not d or d[-1] in "는운은던":                  # '개 짖는', '날카로운': an adnominal, not a noun
        return None
    if d in _sound_words() or (ono and ono in d):     # '쾅', '보글보글': the sound word, not its source
        return None
    if " " in ono:                                    # '차량 소음': the table's sound word is a description
        return None
    return d


def phrases(cls: str) -> dict:
    """The captions of a sound class::

        {'long':  '[우르릉]',          # the caption tried first: the onomatopoeia, else the description
         'short': '[우르릉]',          # for a tight place (the same as long now)
         'ono':   '[우르릉 - 천둥]',    # onomatopoeia and source, or None
         'desc':  '[천둥 소리]'}        # the description
    """
    r = info(cls)
    desc = r["desc"] or r["ono"] or cls              # a class the table does not describe: its name
    first = style.sfx_phrase(r["ono"], desc)
    src = source(cls) if r["ono"] else None
    return {"long": first, "short": first, "ono": style.sfx_with_source(r["ono"], src) if src else None,
            "desc": style.bracket(desc)}


def mood_phrase(mood_key: str | None, change: str | None = None, instrument: str | None = None) -> str:
    """'[고조되는 무서운 음악]'; '[음악]' when nothing is known (II.7)."""
    return style.music_phrase(change, MOODS[mood_key] if mood_key else None,
                              INSTRUMENTS.get(instrument, instrument) if instrument else style.MUSIC)
