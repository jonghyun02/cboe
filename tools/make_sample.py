# -*- coding: utf-8 -*-
"""Build a small hand-made work folder (work/sample) for UI development.

Uses the real 가즈아 도서관 video and the human SDH dialogue lines of 3:00~4:25,
and the deck's example candidates (3.1 화면: 발걸음·삑삑·무서운 음악·책·천둥·쿵).
Times, scores and evidence are illustrative, not model output.

    .venv\\Scripts\\python.exe tools\\make_sample.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cboe.util import write_json  # noqa: E402

DATA = Path(r"C:\projects\barrier_free\data\sdh3")
VIDEO = DATA / "video_RIt0WP6hHQE.mp4"
SRT = DATA / "sub_RIt0WP6hHQE.ko.srt"
T0, T1 = 180.0, 265.0
OUT = ROOT / "work" / "sample"


def ts(s: str) -> float:
    h, m, rest = s.split(":")
    sec, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000


def cues(path: Path):
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8").strip()):
        rows = block.strip().splitlines()
        if len(rows) < 3 or "-->" not in rows[1]:
            continue
        a, b = [ts(x.strip()) for x in rows[1].split("-->")]
        yield a, b, rows[2:]


def dialogue_lines():
    lines = []
    for a, b, rows in cues(SRT):
        if not (T0 <= a < T1):
            continue
        texts, speaker = [], None
        for r in rows:
            r = r.strip()
            if r.startswith("[") or r.startswith("♬"):
                continue
            m = re.match(r"^\((.+?)\)\s*(.*)$", r)
            if m:
                speaker = m.group(1).split("/")[0].strip()
                r = m.group(2).strip()
            if r:
                texts.append(r)
        if not texts:
            continue
        text = " ".join(texts)
        ws = text.split()
        step = (b - a) / max(len(ws), 1)
        words = [{"w": w, "s": round(a + i * step, 3), "e": round(a + (i + 1) * step, 3)} for i, w in enumerate(ws)]
        lines.append({"id": f"L{len(lines) + 1:04d}", "speaker": speaker, "text": text, "start": a, "end": b,
                      "words": words, "aligned": True, "match": 1.0, "voice": 0 if speaker != "양희" else 1})
    # the human track names the speaker only on some lines; the sample script names everyone
    last = None
    for ln in lines:
        if ln["speaker"]:
            last = ln["speaker"]
        else:
            ln["speaker"] = last or "BJ 옴뫄"
    return lines


def ev(kind, t, text, pol="neutral", src="rule", t1=None):
    e = {"kind": kind, "t": t, "text": text, "source": src, "polarity": pol}
    if t1 is not None:
        e["t1"] = t1
    return e


def main():
    lines = dialogue_lines()
    items = [
        {"id": "S0001", "kind": "sfx", "t0": 221.0, "t1": 222.6, "onset": 221.0,
         "label": {"class": "Walk, footsteps", "ko": "발걸음", "score": 0.71, "path": ["Walk, footsteps"],
                   "top": [{"class": "Walk, footsteps", "ko": "발걸음", "score": 0.71},
                           {"class": "Run", "ko": "뛰는 소리", "score": 0.12}], "competing": [], "unknown": False},
         "phrases": [{"text": "[발걸음 소리]", "short": "[발걸음 소리]", "source": "detected", "note": "검출 종류"}],
         "phrase": "[발걸음 소리]", "short": "[발걸음 소리]",
         "evidence": [ev("화면", 221.6, "걷는 모습이 보임", "exclude", "vlm"),
                      ev("자막", 213.8, "앞 자막에서 이미 알린 소리", "exclude")],
         "rec": "exclude", "reason": "걷는 동작이 보임 · 앞 자막에서 이미 알림", "flags": []},
        {"id": "S0002", "kind": "sfx", "t0": 223.8, "t1": 225.1, "onset": 223.8,
         "label": {"class": "Beep, bleep", "ko": "삑", "score": 0.38, "path": ["Beep, bleep", "Alarm"],
                   "top": [{"class": "Beep, bleep", "ko": "삑", "score": 0.38},
                           {"class": "Alarm", "ko": "경보음", "score": 0.33},
                           {"class": "Machine gun", "ko": "기관총", "score": 0.31}],
                   "competing": [{"class": "Machine gun", "score": 0.31}], "unknown": False},
         "phrases": [{"text": "[삑삑 소리]", "short": "[삑삑]", "source": "detected", "note": "검출 종류"},
                     {"text": "[삑삑, 바코드 찍는 소리]", "short": "[삑삑]", "source": "caption", "note": "소리 설명 모델 제안"}],
         "phrase": "[삑삑 소리]", "short": "[삑삑]",
         "evidence": [ev("소리", 223.8, "종류 후보가 갈림: 삑 0.38 · 경보음 0.33 · 기관총 0.31", "neutral", "sed"),
                      ev("에이전트", 223.8, "어둠 속이라 출처가 안 보임 · 소리 설명: 짧은 전자음 두 번", "include", "agent"),
                      ev("화면", 223.9, "어두운 서가, 빨간 불빛만 보임", "include", "vlm")],
         "rec": "check", "reason": "소리 종류가 불확실 → 에이전트 재확인", "flags": ["label_uncertain"],
         "agent": {"status": "done", "summary": "종류를 '바코드 찍는 소리'로 좁혔지만 확신 낮음", "tools": ["rescan_sound", "describe_sound", "look_frames"]}},
        {"id": "M0001", "kind": "music", "t0": 225.7, "t1": 229.0, "onset": 225.7,
         "label": {"class": "Music", "ko": "음악", "score": 0.8},
         "music": {"moods": [{"key": "Scary music", "ko": "무서운", "score": 0.41},
                             {"key": "Tender music", "ko": "부드러운", "score": 0.12},
                             {"key": "Happy music", "ko": "경쾌한", "score": 0.08}],
                   "change": "고조되는", "instruments": [], "segment": [225.7, 229.0], "event": "start"},
         "phrases": [{"text": "♬ 무서운 음악", "short": "♬ 무서운 음악", "source": "mood", "note": "분위기만"},
                     {"text": "♬ 고조되는 무서운 음악", "short": "♬ 무서운 음악", "source": "mood", "note": "변화 + 분위기"},
                     {"text": "♬ 음악", "short": "♬ 음악", "source": "mood", "note": "분위기 없이"}],
         "phrase": "♬ 무서운 음악", "short": "♬ 무서운 음악",
         "evidence": [ev("소리", 225.7, "음악 시작", "include", "sed"),
                      ev("분위기", 225.7, "후보 1위 '무서운'", "include", "ast"),
                      ev("변화", 227.0, "소리가 점점 커짐", "neutral", "rule")],
         "rec": "include", "reason": "음악 시작 · 분위기 1위 '무서운'", "flags": []},
        {"id": "S0003", "kind": "sfx", "t0": 228.65, "t1": 229.6, "onset": 228.65,
         "label": {"class": "Thump, thud", "ko": "쿵", "score": 0.55, "path": ["Thump, thud", "Generic impact sounds"],
                   "top": [{"class": "Thump, thud", "ko": "쿵", "score": 0.55},
                           {"class": "Machine gun", "ko": "기관총", "score": 0.21}], "competing": [], "unknown": False},
         "phrases": [{"text": "[쿵, 책 떨어지는 소리]", "short": "[쿵]", "source": "caption", "note": "소리 설명 모델 제안"},
                     {"text": "[쿵]", "short": "[쿵]", "source": "detected", "note": "검출 종류"}],
         "phrase": "[쿵, 책 떨어지는 소리]", "short": "[쿵]",
         "evidence": [ev("화면", 228.7, "소리 낸 동작 안 보임", "include", "vlm"),
                      ev("대사", 230.4, "직후 비명", "include", "llm")],
         "rec": "include", "reason": "화면에 출처 없음 · 직후 비명", "flags": []},
        {"id": "S0004", "kind": "sfx", "t0": 230.4, "t1": 232.2, "onset": 230.4,
         "label": {"class": "Thunder", "ko": "천둥", "score": 0.64, "path": ["Thunder", "Thunderstorm"],
                   "top": [{"class": "Thunder", "ko": "천둥", "score": 0.64},
                           {"class": "Train", "ko": "기차", "score": 0.22}], "competing": [], "unknown": False},
         "phrases": [{"text": "[천둥 소리]", "short": "[천둥]", "source": "detected", "note": "검출 종류"}],
         "phrase": "[천둥 소리]", "short": "[천둥]",
         "evidence": [ev("화면", 230.4, "출처 안 보임", "include", "vlm"),
                      ev("대사", 230.4, "같은 순간 비명", "include", "llm")],
         "rec": "include", "reason": "출처 안 보임 · 같은 순간 비명", "flags": []},
        {"id": "S0005", "kind": "sfx", "t0": 232.4, "t1": 232.9, "onset": 232.4,
         "label": {"class": "Thump, thud", "ko": "쿵", "score": 0.47, "path": ["Thump, thud"], "top": [], "competing": [], "unknown": False},
         "phrases": [{"text": "[쿵]", "short": "[쿵]", "source": "detected", "note": "검출 종류"}],
         "phrase": "[쿵]", "short": "[쿵]",
         "evidence": [ev("화면", 232.4, "출처 안 보임", "include", "vlm")],
         "rec": "include", "reason": "출처 안 보임 · 사건(넘어짐)", "flags": []},
    ]
    for ln in lines:
        vis = ln["text"].startswith("으아") or ln["text"] in ("악",)
        face = "visible" if vis or ln["speaker"] == "양희" else ("not_visible" if ln["start"] < 220 else "unclear")
        rec = {"visible": "exclude", "not_visible": "include", "unclear": "check"}[face]
        reason = {"visible": "말하는 얼굴이 보임", "not_visible": "말하는 얼굴 안 보임", "unclear": "얼굴 판정 애매"}[face]
        items.append({
            "id": "P" + ln["id"][1:], "kind": "speaker", "t0": ln["start"], "t1": ln["end"], "onset": ln["start"],
            "label": {"class": "speaker", "ko": ln["speaker"], "score": 0.9},
            "speaker": {"line_id": ln["id"], "name": ln["speaker"], "face": face, "n_faces": 1 if vis else 0,
                        "asd": 0.8 if vis else -0.5, "name_revealed": True},
            "phrases": [], "phrase": ln["text"], "short": ln["text"],
            "evidence": [ev("화면", ln["start"], reason, "exclude" if face == "visible" else "include", "asd"),
                         ev("대본", ln["start"], f"이 대사는 {ln['speaker']}", "neutral", "script")],
            "rec": rec, "reason": reason, "flags": []})
    items.sort(key=lambda it: (it["onset"], it["id"]))
    OUT.mkdir(parents=True, exist_ok=True)
    write_json(OUT / "project.json", {"title": "가즈아 도서관 (예시)", "video": str(VIDEO), "script": None,
                                      "t0": T0, "t1": T1, "fps": 23.976, "duration": 3600.0,
                                      "width": 640, "height": 360, "sample": True})
    write_json(OUT / "dialogue.json", {"lines": lines})
    write_json(OUT / "items.json", {"items": items})
    write_json(OUT / "cuts.json", {"fps": 23.976, "cuts": [184.6, 192.1, 201.3, 209.0, 216.5, 221.2, 228.1,
                                                            230.35, 234.8, 241.5, 251.9, 258.2]})
    write_json(OUT / "script.json", {"lines": [{"id": ln["id"], "speaker": ln["speaker"], "text": ln["text"]}
                                               for ln in lines], "characters": ["BJ 옴뫄", "양희"]})
    print(f"wrote {OUT}: {len(lines)} lines, {len(items)} items")


if __name__ == "__main__":
    main()
