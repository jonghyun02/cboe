# -*- coding: utf-8 -*-
"""Korean caption phrases (문구) for sound classes and music moods.

Sound classes: ``data/sfx_classes.tsv`` (built by tools/build_phrases.py, hand-checked):
설명 문구 ``desc`` ('자동차 경적 소리'), 의성어 ``ono`` ('빵빵'), policy ``category``
(event / ambient / minor / vocal / speech / music / background).
- 기본 문구 ``[desc]`` → ``[자동차 경적 소리]``
- 의성어 문구 ``[ono, desc]`` → ``[빵빵, 자동차 경적 소리]``
- 짧은 문구 ``[ono]`` → ``[빵빵]`` (끼워 넣기에서 자리가 모자랄 때)

Music moods (10-13 메모 3.1, 넷플릭스 II.7: 객관적인 표현, '행복한' 대신 '경쾌한').
"""
from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path

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


def phrases(cls: str) -> dict:
    """{'long': '[천둥 소리]', 'ono': '[우르릉, 천둥 소리]' | None, 'short': '[우르릉]' | long}."""
    r = info(cls)
    desc, ono = r["desc"] or cls, r["ono"]
    long = f"[{desc}]"
    with_ono = f"[{ono}, {desc}]" if ono and ono not in desc else None
    short = f"[{ono}]" if ono else long
    return {"long": long, "ono": with_ono, "short": short}


def mood_phrase(mood_key: str | None, change: str | None = None, instrument: str | None = None) -> str:
    parts = []
    if change:
        parts.append(change)
    if mood_key:
        parts.append(MOODS[mood_key])
    if instrument:
        parts.append(INSTRUMENTS.get(instrument, instrument))
    else:
        parts.append("음악")
    return "♬ " + " ".join(parts)
