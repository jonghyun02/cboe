# -*- coding: utf-8 -*-
"""단계별 결과 보기: 어느 단계에서 틀어졌는지 찾을 때 쓴다 (파일만 읽고, 아무것도 다시 계산하지 않는다).

    .venv\\Scripts\\python.exe tools\\inspect_stages.py work\\<작업>                  단계마다 요약
    .venv\\Scripts\\python.exe tools\\inspect_stages.py work\\<작업> --at 2:33        그 시각 ±5초, 모든 단계 결과를 순서대로
    .venv\\Scripts\\python.exe tools\\inspect_stages.py work\\<작업> --at 2:33 --win 10 --clips
                                                                              + 원본·대사·음악·효과음 트랙을 잘라 wav로 저장
    .venv\\Scripts\\python.exe tools\\inspect_stages.py work\\<작업> --item S0017     후보 하나를 단계마다 따라가기
    .venv\\Scripts\\python.exe tools\\inspect_stages.py work\\<작업> --stage sfx      한 단계 결과 전부 (한 줄씩)

시각은 153.1 / 2:33 / 2:33.5 / 0:02:33 모두 된다. 출력이 길면 `> 결과.txt` 로 저장해서 본다.
'추천(에이전트 전)'은 recommend.build 를 에이전트 결과 없이 다시 불러 보여 준다 (규칙만, 파일은 안 씀).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

STAGES = ["ingest", "script", "separate", "cuts", "sed", "asr", "align", "cues", "sfx", "music", "speaker", "screen",
          "dialog_ev", "recommend", "agent", "placement", "export"]
NO_TIME = ("ingest", "script", "export")        # nothing of theirs sits at a time (--at leaves them out)

# stage -> (덱, 코드 (파일, 찾을 줄), 결과 파일)
INFO = {
    "ingest": ("영상 소리 뽑기", [("cboe/stages/ingest.py", "def run(")], "project.json, audio/mix_48k.wav·mix_16k.wav"),
    "script": ("대본 읽기 (촬영 대본 PDF·HWP, 줄 대본, 자막)",
               [("cboe/stages/script.py", "def run("), ("cboe/script.py", "def read("),
                ("cboe/screenplay.py", "def classify_plain(")],
               "script.json (version 2: 장면·지문·대사 줄·괄호 지시)"),
    "separate": ("5.2 음원 분리 (Bandit v2)", [("cboe/stages/separate.py", "def run(")],
                 "audio/{speech,music,effects}_48k·16k.wav  (트랙 소리 세기 env.npz 는 sed 단계가 씀)"),
    "cuts": ("5.5 장면 나누기 (TransNetV2)", [("cboe/stages/cuts.py", "def run("), ("cboe/stages/cuts.py", "THRESHOLD =")],
             "cuts.json"),
    "sed": ("5.3·5.4 소리 점수 (PretrainedSED 447종, 40ms)", [("cboe/stages/sed.py", "def run(")],
            "sed_effects.npz · sed_mix.npz · sed_music.npz, env.npz"),
    "asr": ("5.2 받아쓰기 (Whisper large-v3)", [("cboe/stages/asr.py", "def run("), ("cboe/stages/asr.py", "VAD =")],
            "asr.json"),
    "align": ("5.2 대본 줄 정렬", [("cboe/stages/align.py", "def run("), ("cboe/stages/align.py", "def align_lines("),
                                ("cboe/stages/align.py", "def _place_unaligned(")], "dialogue.json"),
    "cues": ("대본 단서 (지문에 적힌 소리·음악·침묵, 말하는 사람의 소리)",
             [("cboe/stages/cues.py", "def run("), ("cboe/cues.py", "def extract("), ("cboe/cues.py", "def match_sounds(")],
             "cues.json (실행마다 다시 만듦; 추천은 같은 단서를 다시 계산해 씀)"),
    "sfx": ("5.3 효과음 후보", [("cboe/stages/sfx.py", "def run("), ("cboe/stages/sfx.py", "T_ON, T_OFF =")],
            "sfx.json"),
    "music": ("5.4 음악 후보 (SED 'Music' + AST)", [("cboe/stages/music.py", "def run(")], "music.json"),
    "speaker": ("5.6 화자 근거 (YuNet + LR-ASD)", [("cboe/stages/speaker.py", "def run("),
                                               ("cboe/stages/speaker.py", "speaking = [t for t in info")],
                "speaker.json"),
    "screen": ("5.5 화면 근거 (Claude, 프레임 3장)", [("cboe/stages/screen.py", "def run("),
                                                ("cboe/stages/screen.py", "SYSTEM = (")], "screen.json"),
    "dialog_ev": ("대사 근거 (Claude, 글)", [("cboe/stages/dialog_ev.py", "def run("),
                                        ("cboe/stages/dialog_ev.py", "SYSTEM = (")], "dialog_ev.json"),
    "recommend": ("5.7 추천 (규칙)", [("cboe/stages/recommend.py", "def build("),
                                  ("cboe/stages/recommend.py", "def decide_sfx("),
                                  ("cboe/stages/recommend.py", "def decide_music("),
                                  ("cboe/stages/recommend.py", "def decide_speaker(")],
                  "items.json (agent 단계가 같은 파일을 덮어씀)"),
    "agent": ("5.8 재확인 에이전트", [("cboe/stages/agent.py", "def run("), ("cboe/stages/agent.py", "def priority("),
                                  ("cboe/agent_tools.py", "class Tools")],
              "agent_results.json, agent/<id>.json (질문·도구 기록), agent_done.json, items.json"),
    "placement": ("5.9 초안 배치 (규칙)", [("cboe/placement.py", "def place("), ("cboe/placement.py", "DEFAULTS ="),
                                      ("cboe/state.py", "def recompute(")],
                  "draft.json (review.json 의 작가 결정까지 반영된 결과)"),
    "export": ("내보내기", [("cboe/export.py", "def final_check(")], "export/*.srt · *.vtt"),
}

VIS_KO = {"visible": "얼굴 보임", "not_visible": "얼굴 안 보임", "unclear": "애매"}
TIMING_KO = {"asr": "받아쓰기와 맞춤", "voice": "못 맞춤→목소리 구간에 배치", "even": "못 맞춤→고르게 나눔",
             "script": "자막 시각 그대로", None: "-"}
STATUS_KO = {"ok": "맞음", "differs": "들린 말이 다름", "unheard": "못 맞춤", "absent": "장면이 영상에 없음",
             "out_of_range": "처리 구간 밖"}


# ---------------------------------------------------------------- helpers
def loc(rel: str, needle: str) -> str:
    f = ROOT / rel
    try:
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if needle in line:
                return f"{rel}:{i}"
    except OSError:
        pass
    return rel


def parse_t(s: str) -> float:
    parts = [float(x) for x in s.split(":")]
    t = 0.0
    for x in parts:
        t = t * 60 + x
    return t


def ft(t) -> str:
    if t is None:
        return "-"
    t = float(t)
    m, s = divmod(t, 60)
    h, m = divmod(int(m), 60)
    return (f"{h}:{m:02d}:{s:04.1f}" if h else f"{m}:{s:04.1f}")


def cut(s, n=90) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[:n - 1] + "…"


def overlap(s, e, a, b) -> bool:
    """Whether [s, e] meets [a, b]; a row without a time (a script line not found in the film) never does."""
    return s is not None and e is not None and e >= a and s <= b


class Ctx:
    def __init__(self, work: Path):
        self.w = work
        self._c = {}

    def j(self, name, default=None):
        if name not in self._c:
            f = self.w / name
            self._c[name] = json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
        v = self._c[name]
        return default if v is None else v

    def npz(self, name):
        key = "npz:" + name
        if key not in self._c:
            f = self.w / name
            self._c[key] = dict(np.load(f, allow_pickle=False)) if f.exists() else None
        return self._c[key]

    @property
    def meta(self):
        return self.j("project.json", {})

    def sfx(self):
        return self.j("sfx.json", {}).get("candidates", [])

    def items(self):
        return self.j("items.json", {}).get("items", [])

    def pre_agent(self):
        """recommend.build without agent results: what the rules alone said."""
        if "pre" not in self._c:
            try:
                from cboe.state import Project
                from cboe.stages import recommend
                self._c["pre"] = {it["id"]: it for it in recommend.build(Project(self.w), None)}
            except Exception as e:      # missing inputs: show the error instead of stopping
                self._c["pre"] = {"_error": str(e)}
        return self._c["pre"]

    def ko(self, cls):
        try:
            from cboe import phrases
            return phrases.ko(cls)
        except Exception:
            return cls

    def name(self, cls, ko=None):
        """The Korean name of a class for a list; one the table does not describe is only '소리'
        in Korean, so its English name goes with it ('소리(Human sounds)')."""
        ko = self.ko(cls) if ko is None else ko
        return f"{ko}({cls})" if ko == _plain_sound() and cls else ko


def _plain_sound() -> str:
    try:
        from cboe import phrases
        return phrases.SOUND
    except Exception:
        return "소리"


def head(name: str):
    deck, codes, out = INFO[name]
    print()
    print(f"■ {name} — {deck}")
    print("  코드: " + "  ".join(loc(f, n) for f, n in codes))
    print("  결과: " + out)


def missing(c: Ctx, *files) -> bool:
    gone = [f for f in files if not (c.w / f).exists()]
    if gone:
        print("  (결과 없음: " + ", ".join(gone) + " — 이 단계가 아직 안 돌았음)")
    return bool(gone)


# ---------------------------------------------------------------- stages
def st_ingest(c: Ctx, a, b, full):
    m = c.meta
    print(f"  제목 {m.get('title')} | 영상 {m.get('video')}")
    print(f"  처리 구간 {ft(m.get('t0'))}~{ft(m.get('t1'))} | {m.get('fps')}fps {m.get('width')}x{m.get('height')} "
          f"| 프로필 {m.get('profile') or 'adult'}")
    adir = c.w / "audio"
    print("  소리: " + (", ".join(f for f in ("mix_48k.wav", "mix_16k.wav") if (adir / f).exists()) or "없음"))


def _line_marks(ln: dict) -> str:
    """What the script says about a line beside its words: language, singing, source, manner, sounds."""
    marks = [ln["language"]] if ln.get("language") else []
    marks += ["노래"] if ln.get("sung") else []
    marks += [ln["source"]] if ln.get("source") else []
    marks += [f"말투 {', '.join(ln['delivery'])}"] if ln.get("delivery") else []
    marks += [f"소리 {', '.join(ln['vocal'])}"] if ln.get("vocal") else []
    marks += [f"{d['by']}의 소리 {d['text']}" for d in ln.get("directions") or [] if d.get("by")]
    return f" ({'; '.join(marks)})" if marks else ""


def st_script(c: Ctx, a, b, full):
    m = c.meta
    sc = c.j("script.json")
    print(f"  대본 파일 {m.get('script') or '없음'}")
    if sc is None:
        missing(c, "script.json")
        return
    lines, elements = sc.get("lines", []), sc.get("elements", [])
    try:
        from cboe import script as script_mod
        sm = script_mod.summary(sc)
    except Exception as e:      # a reading the summary does not know: show what is there instead of stopping
        sm = {"warnings": [f"요약 실패: {e}"]}
    src = sc.get("source") if isinstance(sc.get("source"), dict) else {}
    print(f"  형식 version {sc.get('version', 1)} · {sc.get('format') or ('timed' if sc.get('timed') else 'lines')}"
          + (f" ({sc['layout']})" if sc.get("layout") else "") + (f" · sha1 {src['sha1'][:10]}" if src.get("sha1") else ""))
    print(f"  장면 {len(sc.get('scenes') or [])} · 대사 줄 {len(lines)} (화자 없음 {sum(1 for ln in lines if not ln.get('speaker'))}) "
          f"· 인물 {len(sc.get('characters') or [])} · 요소 " +
          (", ".join(f"{k} {v}" for k, v in Counter(e.get("type") for e in elements).items()) or "없음"))
    print(f"  외국어 {dict(Counter(ln['language'] for ln in lines if ln.get('language')))} · 노래 "
          f"{sum(1 for ln in lines if ln.get('sung'))} · 출처 {dict(Counter(ln['source'] for ln in lines if ln.get('source')))}"
          f" · 말투 {sum(len(ln.get('delivery') or []) for ln in lines)} · 화자 소리 {sum(len(ln.get('vocal') or []) for ln in lines)}"
          f" · 남의 소리 {sum(1 for ln in lines for d in ln.get('directions') or [] if d.get('by'))}")
    for w in sm.get("warnings") or []:
        print(f"  ⚠ {w}")
    if sm.get("error"):
        print(f"  ✗ {sm['error']}")
    print(f"  인물: {', '.join(sc.get('characters') or [])}")
    for ln in lines[:None if full else 3]:
        print(f"    {ln['id']} {ln.get('scene_id') or '-'} [{ln.get('speaker') or '-'}] {cut(ln['text'], 80)}{_line_marks(ln)}")


def st_separate(c: Ctx, a, b, full):
    adir = c.w / "audio"
    files = sorted(x.name for x in adir.glob("*.wav")) if adir.exists() else []
    if a is None:
        print("  트랙 파일: " + (", ".join(files) or "없음"))
    env = c.npz("env.npz")
    if env is None:
        missing(c, "env.npz")
        return
    hop, t0 = float(env["hop"]), float(env["t0"])
    if a is None:
        for k in ("speech", "music", "effects"):
            x = env[k]
            print(f"  {k:8s} 중앙값 {np.median(x):6.1f}dB | -40dB 넘는 시간 {100 * np.mean(x > -40):4.1f}%")
        print("  → 귀로 확인: --at <시각> --clips 로 같은 구간을 트랙마다 잘라 들어 본다")
        return
    print("  0.5초마다 트랙 소리 세기 최대값 (dB, # 하나 = 5dB, -60dB 아래는 비움)")
    print(f"  {'시각':>8s}  {'대사':<14s}{'음악':<14s}{'효과음':<14s}")
    t = a
    while t < b:
        i0, i1 = int((t - t0) / hop), int((t + 0.5 - t0) / hop)
        row = []
        for k in ("speech", "music", "effects"):
            seg = env[k][max(i0, 0):max(i1, 1)]
            v = float(seg.max()) if len(seg) else -120.0
            row.append(f"{v:5.0f} {'#' * max(0, int((v + 60) / 5)):<8s}")
        print(f"  {ft(t):>8s}  " + "".join(row))
        t += 0.5


def st_cuts(c: Ctx, a, b, full):
    d = c.j("cuts.json")
    if d is None:
        missing(c, "cuts.json")
        return
    cuts = d.get("cuts", [])
    if a is None:
        lens = np.diff([s for s, _ in d.get("shots", [])] + [d["shots"][-1][1]]) if d.get("shots") else []
        print(f"  장면 전환 {len(cuts)}개 | 장면 길이 중앙값 {np.median(lens) if len(lens) else 0:.1f}초 | 기준 {d.get('threshold')}")
        if full:
            print("  " + ", ".join(ft(x) for x in cuts))
        return
    inside = [x for x in cuts if a <= x <= b]
    print("  전환 시각: " + (", ".join(ft(x) for x in inside) or "없음 (한 장면)"))


def _top(P, names, i0, i1, n, c: Ctx, thr=0.05):
    seg = P[max(i0, 0):max(i1, i0 + 1)]
    if not len(seg):
        return "-"
    mx = seg.max(axis=0)
    idx = np.argsort(-mx)[:n]
    return ", ".join(f"{c.ko(names[k])}({names[k]}) {mx[k]:.2f}" for k in idx if mx[k] >= thr) or "-"


def st_sed(c: Ctx, a, b, full):
    tracks = {"effects": "효과음 트랙", "mix": "원본", "music": "음악 트랙"}
    if missing(c, *[f"sed_{k}.npz" for k in tracks]):
        return
    for k, ko in tracks.items():
        d = c.npz(f"sed_{k}.npz")
        P, names, fps, t0 = d["probs"].astype(np.float32), [str(x) for x in d["classes"]], int(d["fps"]), float(d["t0"])
        if a is None:
            on = (P >= 0.3).sum(axis=0) / fps
            idx = np.argsort(-on)[:8]
            print(f"  {ko}: 0.3 넘은 시간 상위 — " + ", ".join(f"{c.name(names[i])} {on[i]:.0f}초" for i in idx))
            continue
        i0, i1 = int((a - t0) * fps), int((b - t0) * fps)
        print(f"  {ko} (구간 최대 상위 6): {_top(P, names, i0, i1, 6, c)}")
        if k == "effects":
            print("    1초마다 효과음 트랙 상위 3:")
            t = a
            while t < b:
                j0 = int((t - t0) * fps)
                print(f"    {ft(t):>8s}  {_top(P, names, j0, j0 + fps, 3, c, 0.1)}")
                t += 1.0


def st_asr(c: Ctx, a, b, full):
    d = c.j("asr.json")
    if d is None:
        missing(c, "asr.json")
        return
    words, vad = d.get("words", []), d.get("vad", [])
    if a is None:
        print(f"  언어 {d.get('language')} | 모델 {d.get('model')} | 입력 {d.get('input')} | 낱말 {len(words)}개 | "
              f"목소리 구간 {len(vad)}개 {sum(e - s for s, e in vad):.0f}초")
        return
    print("  목소리 구간(VAD): " + (", ".join(f"{ft(s)}~{ft(e)}" for s, e in vad if overlap(s, e, a, b)) or "없음"))
    ws = [w for w in words if overlap(w["s"], w["e"], a, b)]
    print(f"  받아쓴 낱말 {len(ws)}개:")
    line, out = "", []
    for w in ws:
        piece = f"{w['w']}({w['s']:.1f}) "
        if len(line) + len(piece) > 100:
            out.append(line)
            line = ""
        line += piece
    out.append(line)
    for ln in out:
        if ln:
            print("    " + ln)


def st_align(c: Ctx, a, b, full):
    d = c.j("dialogue.json")
    if d is None:
        missing(c, "dialogue.json")
        return
    lines = d.get("lines", [])
    if a is None:
        tm = Counter(ln.get("timing") for ln in lines)
        mt = [ln.get("match") or 0 for ln in lines]
        print(f"  방식 {d.get('mode')} | 줄 {len(lines)} | " + ", ".join(f"{TIMING_KO.get(k, k)} {v}" for k, v in tm.items()))
        print(f"  글자 일치(match): 0.8 이상 {sum(m >= 0.8 for m in mt)}, 0.5~0.8 {sum(0.5 <= m < 0.8 for m in mt)}, "
              f"0.5 미만 {sum(m < 0.5 for m in mt)}")
        st = Counter(ln.get("status") or "ok" for ln in lines)      # a dialogue.json without status: all 'ok'
        print("  줄 상태: " + ", ".join(f"{STATUS_KO.get(k, k)} {v}" for k, v in st.items())
              + f" | 시각 없음 {sum(1 for ln in lines if ln.get('start') is None)}"
              + f" | 대본에 없는 들린 말 {len(d.get('extras') or [])}")
        bad = [ln for ln in lines if ln.get("timing") != "asr" and ln.get("timing") != "script"]
        if bad:
            print("  받아쓰기와 못 맞춘 줄 (시각이 추정값):")
            for ln in bad[:None if full else 8]:
                print(f"    {ln['id']} {ft(ln.get('start'))}~{ft(ln.get('end'))} [{TIMING_KO.get(ln.get('timing'))}] "
                      f"[{ln.get('speaker') or '-'}] {cut(ln['text'], 60)}")
        if not full:
            return
        a, b = -1, 1e12
    every = a == -1 and b == 1e12           # the whole stage: the lines without a time too
    for ln in lines:
        if overlap(ln.get("start"), ln.get("end"), a, b) or (every and ln.get("start") is None):
            words = ln.get("words") or []          # a line without a time carries "words": null
            anch = sum(1 for w in words if w.get("anchored"))
            print(f"  {ln['id']} {ft(ln.get('start'))}~{ft(ln.get('end'))} [{ln.get('speaker') or '-'}] "
                  f"{STATUS_KO.get(ln.get('status') or 'ok', ln.get('status'))} · "
                  f"{TIMING_KO.get(ln.get('timing'), ln.get('timing'))} · match {ln.get('match')} · "
                  f"맞춘 낱말 {anch}/{len(words)}")
            print(f"      {cut(ln['text'], 100)}")


CUE_KO = {"sfx": "소리", "music": "음악", "silence": "침묵", "vocal": "말하는 사람의 소리"}


def _cue_row(q: dict) -> None:
    w = q.get("window")
    where = f"{ft(w[0])}~{ft(w[1])} ({'·'.join(q.get('window_by') or []) or '-'})" if w else "시각 범위 없음"
    who = f" [{q['speaker']}]" if q.get("speaker") else ""
    print(f"  {q['id']} {CUE_KO.get(q.get('type'), q.get('type'))}{who} {q.get('scene_id') or '-'} {where} "
          f"→ {q.get('phrase') or '-'}")
    print(f"      {cut(q.get('text'), 90)}")


def st_cues(c: Ctx, a, b, full):
    d = c.j("cues.json")
    if d is None:
        missing(c, "cues.json")
        return
    cues = d.get("cues") or []
    if a is None:
        kinds = Counter(q.get("type") for q in cues)
        windowed = [q for q in cues if q.get("window")]
        try:
            from cboe import cues as cue_mod
            wide_s = cue_mod.WIDE_S
        except Exception:
            wide_s = 30.0
        wide = sum(1 for q in windowed if q["window"][1] - q["window"][0] > wide_s)
        by = Counter(k for q in windowed for k in q.get("window_by") or [])
        print(f"  단서 {len(cues)} · " + (", ".join(f"{CUE_KO.get(k, k)} {v}" for k, v in kinds.items()) or "없음")
              + f" | 시각 범위 있음 {len(windowed)} (범위 끝을 정한 것: 대사 시각 {by.get('line', 0)}, "
              f"장면 시각 {by.get('scene', 0)}; {wide_s:.0f}초보다 넓음 {wide})")
        items = c.items()
        if items:
            hit = [it for it in items if it.get("cue_id") and it.get("origin") != "script"]
            new = [it for it in items if it.get("origin") == "script"]
            print(f"  추천(items.json)에서: 검출 후보에 붙음 {len(hit)} (이름을 대본에서 받음 "
                  f"{sum(1 for it in hit if it.get('named_by') == 'script')}) · 대본만으로 만든 확인 필요 후보 {len(new)}")
        if not full:
            return
    for q in cues:
        w = q.get("window")
        if a is None or (w and overlap(w[0], w[1], a, b)):
            _cue_row(q)


def _sfx_row(x, c: Ctx):
    lab = x["label"]
    st = "목록 밖" if lab.get("unknown") else ("확실" if lab.get("confident") else "불확실")
    comp = (" · 경쟁 " + ", ".join(f"{k['ko']} {k['score']:.2f}" for k in lab.get("competing", [])[:2])) if lab.get("competing") else ""
    top = ", ".join(f"{c.name(k.get('class'), k['ko'])} {k['score']:.2f}" for k in lab.get("top", [])[:3])
    rep = f" · 반복 {x['repeats']}" if x.get("repeats") else ""
    print(f"  {x['id']} {ft(x['onset'])}~{ft(x['t1'])} [{x.get('category')}] {lab['ko']}({lab['class']}) {lab['score']:.2f} "
          f"{st}{comp}{rep}")
    print(f"      찾은 방법 {'+'.join(x.get('onset_by', []))} · 갑자기 {x.get('level_jump_db')}dB 커짐 · 세기 {x.get('level_db')}dB"
          f" · 원본 점수 {lab.get('mix_score')} | 상위: {top} | 문구 {x.get('phrase')}")


def st_sfx(c: Ctx, a, b, full):
    if missing(c, "sfx.json"):
        return
    cands = c.sfx()
    if a is None:
        print(f"  후보 {len(cands)} | 종류별 " + ", ".join(f"{k} {v}" for k, v in Counter(x['category'] for x in cands).items()))
        print(f"  목록 밖 {sum(x['label'].get('unknown', False) for x in cands)} · 종류 불확실 "
              f"{sum(not x['label'].get('confident') for x in cands)} · 경쟁 종류 있음 {sum(bool(x['label'].get('competing')) for x in cands)}")
        print("  많이 나온 종류: " + ", ".join(f"{k} {v}" for k, v in Counter(
            c.name(x['label'].get('class'), x['label']['ko']) for x in cands).most_common(10)))
        print("  찾은 방법: " + ", ".join(f"{'+'.join(k)} {v}" for k, v in Counter(tuple(x.get('onset_by', [])) for x in cands).items()))
        if not full:
            return
        a, b = -1, 1e12
    for x in cands:
        if overlap(x["onset"], x["t1"], a, b):
            _sfx_row(x, c)


def _music_row(x):
    moods = ", ".join(f"{m['ko']} {m['score']:.2f}" for m in x.get("moods", [])[:3])
    inst = ", ".join(f"{m['ko']} {m['score']:.2f}" for m in x.get("instruments", [])[:2])
    seg = x.get("segment") or [None, None]
    print(f"  {x['kind']:11s} {ft(x['onset'])} (구간 {ft(seg[0])}~{ft(seg[1])}) 음악 점수 {x.get('music_score')}"
          f" · 분위기 세기 {x.get('mood_strength')} · 변화 {x.get('change') or '-'}")
    if x["kind"] == "stop":                 # the music breaks off: how far its level fell
        print(f"      음악이 멈춤 · 소리 {x.get('drop_db')}dB 떨어짐")
    else:
        print(f"      분위기: {moods} | 악기: {inst}")


def st_music(c: Ctx, a, b, full):
    d = c.j("music.json")
    if d is None:
        missing(c, "music.json")
        return
    segs, items = d.get("segments", []), d.get("items", [])
    if a is None:
        print(f"  음악 구간 {len(segs)}개 {sum(e - s for s, e in segs):.0f}초 | 후보 " +
              ", ".join(f"{k} {v}" for k, v in Counter(x['kind'] for x in items).items()))
        print("  분위기 1위: " + ", ".join(f"{k} {v}" for k, v in Counter((x.get('moods') or [{}])[0].get('ko') for x in items).most_common()))
        if not full:
            return
        a, b = -1, 1e12
    print("  음악 구간: " + (", ".join(f"{ft(s)}~{ft(e)}" for s, e in segs if overlap(s, e, a, b)) or "없음"))
    for x in items:
        if a <= x["onset"] <= b:
            _music_row(x)


def st_speaker(c: Ctx, a, b, full):
    d = c.j("speaker.json")
    if d is None:
        missing(c, "speaker.json")
        return
    rows = d.get("lines", [])
    dl = {ln["id"]: ln for ln in c.j("dialogue.json", {}).get("lines", [])}
    if a is None:
        print("  " + ", ".join(f"{VIS_KO.get(k, k)} {v}" for k, v in Counter(r['face'] for r in rows).items())
              + f" | 대본이 화면 밖이라 한 줄 {sum(1 for r in rows if r.get('by') == 'script')}"
              + f" | 시각 없어 건너뛴 대사 줄 {sum(1 for ln in dl.values() if ln.get('start') is None)}")
        if not full:
            return
        a, b = -1, 1e12
    for r in rows:
        ln = dl.get(r["line_id"], {})
        win = r.get("window") or [None, None]
        s = ln.get("start") if ln.get("start") is not None else win[0]
        e = ln.get("end") if ln.get("end") is not None else win[1]
        if overlap(s, e, a, b):
            best = r.get("best") or {}
            bs = (f" · 가장 큰 얼굴 말한 프레임 {best.get('speaking_frames')}/{best.get('frames')} 최고점 {best.get('max_score')}"
                  if best else "")
            by = f" · 대본에 적힌 출처 {r.get('source')} (모델 없이)" if r.get("by") == "script" else ""
            print(f"  {r['line_id']} [{ln.get('speaker') or '-'}] 본 구간 {ft(win[0])}~{ft(win[1])} → "
                  f"{VIS_KO.get(r['face'], r['face'])} · 얼굴 {r.get('n_faces')}개{bs}{by} · 이름 이미 불림 {r.get('name_revealed')}")


def _ids_in(c: Ctx, a, b, kinds=("sfx", "music")):
    out = []
    if "sfx" in kinds:
        out += [(x["id"], x["onset"]) for x in c.sfx() if overlap(x["onset"], x["t1"], a, b)]
    if "music" in kinds:
        out += [(x["id"], x["onset"]) for x in c.items() if x["kind"] == "music" and a <= x["onset"] <= b]
    return out


def st_screen(c: Ctx, a, b, full):
    d = c.j("screen.json")
    if d is None:
        missing(c, "screen.json")
        return
    items = d.get("items", {})
    if a is None:
        print(f"  LLM {d.get('llm')} · 오류 {d.get('errors')} · 끝남 {d.get('complete')} | 소리 낸 동작이 " +
              ", ".join(f"{k} {v}" for k, v in Counter(x.get('visible') for x in items.values()).items()))
        if not full:
            return
        a, b = -1, 1e12
    for i, t in _ids_in(c, a, b, ("sfx",)):
        x = items.get(i)
        if not x:
            print(f"  {i} {ft(t)} (화면 근거 없음)")
            continue
        print(f"  {i} {ft(t)} → {x.get('visible')} | 본 프레임 {', '.join(ft(f) for f in x.get('frames', []))} | "
              f"{cut(x.get('observation'), 70)}" + (f" | 반응: {cut(x['reaction'], 40)}" if x.get("reaction") else "") +
              (f" | 출처 추정: {x['likely_source']}" if x.get("likely_source") else ""))


def st_dialog_ev(c: Ctx, a, b, full):
    d = c.j("dialog_ev.json")
    if d is None:
        missing(c, "dialog_ev.json")
        return
    items = d.get("items", {})
    if a is None:
        print(f"  LLM {d.get('llm')} · 오류 {d.get('errors')} | 관계 " +
              ", ".join(f"{k} {v}" for k, v in Counter(x.get('relation') for x in items.values()).items()) +
              "  (reaction=대사가 반응, mention=대사가 언급, already=대사에 이미 적힘)")
        if not full:
            return
        a, b = -1, 1e12
    for i, t in _ids_in(c, a, b):
        x = items.get(i)
        if not x:
            print(f"  {i} {ft(t)} (대사 근거 없음)")
            continue
        print(f"  {i} {ft(t)} → {x.get('relation')} {x.get('line_id') or ''} | {cut(x.get('evidence'), 70)}"
              + (f" | 줄거리 단서: {cut(x['plot_hint'], 50)}" if x.get("plot_hint") else "") + f" ({x.get('source')})")


def _rec_counts(items):
    cnt = Counter((it["kind"], it["rec"]) for it in items)
    return " | ".join(f"{k}: " + ", ".join(f"{r} {cnt[(k, r)]}" for r in ("include", "exclude", "check") if cnt[(k, r)])
                      for k in ("sfx", "music", "speaker"))


def st_recommend(c: Ctx, a, b, full):
    if missing(c, "items.json"):
        return
    pre = c.pre_agent()
    if "_error" in pre:
        print(f"  (에이전트 전 추천을 다시 못 만듦: {pre['_error']})")
        pre = {}
    if a is None:
        print("  에이전트 전(규칙만): " + _rec_counts(pre.values()))
        print(f"  에이전트로 보낼 후보(agent_reasons 있음): {sum(1 for it in pre.values() if it.get('agent_reasons'))}")
        print("  확인 필요 이유: " + ", ".join(f"{k} {v}" for k, v in Counter(r for it in pre.values() if it['rec'] == 'check'
                                                                     for r in (it.get('agent_reasons') or ['기타'])).most_common()))
        if not full:
            return
        a, b = -1, 1e12
    for it in sorted(pre.values(), key=lambda x: x["onset"]):
        if it["kind"] == "speaker":
            ok = overlap(it["t0"], it["t1"], a, b)
        else:
            ok = overlap(it["onset"], it.get("t1", it["onset"]), a, b)
        if ok:
            ev = " / ".join(f"{e['kind']}{'+' if e['polarity'] == 'include' else '-' if e['polarity'] == 'exclude' else ''}:"
                            f"{cut(e['text'], 30)}" for e in it.get("evidence", []))
            print(f"  {it['id']} {ft(it['onset'])} {it['kind']:7s} → {it['rec']:7s} {cut(it['reason'], 50)} | 문구 {cut(it.get('phrase'), 30)}")
            if it["kind"] != "speaker":
                print(f"      근거(+넣는 쪽 -빼는 쪽): {cut(ev, 160)}")


def st_agent(c: Ctx, a, b, full):
    d = c.j("agent_results.json")
    if d is None:
        missing(c, "agent_results.json")
        return
    res = d.get("items", {})
    done = c.j("agent_done.json", {})
    final = {it["id"]: it for it in c.items()}
    pre = c.pre_agent()
    if a is None:
        print(f"  다시 본 후보 {len(res)} · 남은 대기 {len(done.get('left', []))} · 실패 {len(done.get('failed', []))} | "
              f"도구 호출 {d.get('tool_calls')}")
        ch = Counter(f"{pre.get(i, {}).get('rec')}→{final.get(i, {}).get('rec')}" for i in res)
        print("  추천 변화(에이전트 전→후): " + ", ".join(f"{k} {v}" for k, v in ch.most_common()))
        if not full:
            return
        a, b = -1, 1e12
    for i, x in sorted(res.items(), key=lambda kv: final.get(kv[0], {}).get("onset", 0)):
        it = final.get(i, {})
        if not overlap(it.get("onset", -1), it.get("t1", it.get("onset", -1)), a, b):
            continue
        print(f"  {i} {ft(it.get('onset'))} {x.get('status')} · 도구 {','.join(x.get('tools') or [])} · {x.get('seconds')}초 | "
              f"추천 {pre.get(i, {}).get('rec')} → {it.get('rec')} (에이전트 답 {x.get('recommendation')})")
        print(f"      보임 {x.get('visible')} · 줄거리 {x.get('plot')} · 종류 맞음 {x.get('label_ok')} | {cut(x.get('summary'), 120)}")
        print(f"      기록: {c.w / 'agent' / (i + '.json')}")


def st_placement(c: Ctx, a, b, full):
    d = c.j("draft.json")
    if d is None:
        missing(c, "draft.json")
        return
    cells, nr = d.get("cells", []), d.get("no_room", [])
    rv = c.j("review.json", {}).get("items", {})
    if a is None:
        print(f"  {d.get('summary')} | 작가 결정 {sum(1 for v in rv.values() if v.get('decision'))}개 반영")
        print("  규칙 어긋남: " + (", ".join(f"{k} {v}" for k, v in Counter(v for x in cells for v in x['check'].get('violations', [])).most_common()) or "없음"))
        print("  자리 없음 이유: " + (", ".join(f"{k} {v}" for k, v in Counter(re.sub(r'(?<![/\d.])\d+(\.\d+)?초', 'N초', x['reason']) for x in nr).most_common()) or "없음"))
        if not full:
            return
        a, b = -1, 1e12
    for x in cells:
        if overlap(x["start"], x["end"], a, b):
            ck = x.get("check", {})
            v = (" ⚠ " + ", ".join(ck.get("violations", []))) if ck.get("violations") else ""
            print(f"  {x['id']} {ft(x['start'])}~{ft(x['end'])} [{x['kind']}] {' / '.join(x['lines'])}")
            print(f"      출처 {','.join(x.get('refs', []))} · 글자 {ck.get('chars')} · 초당 {ck.get('cps')}자 · {ck.get('dur')}초{v}")
    for x in nr:
        if a <= x["t"] <= b:
            print(f"  자리 없음 {x['item']} {ft(x['t'])} {x['text']} — {x['reason']}")


def st_export(c: Ctx, a, b, full):
    ex = c.w / "export"
    files = sorted(ex.glob("*")) if ex.exists() else []
    print("  " + (", ".join(f"{f.name} ({f.stat().st_size // 1024}KB)" for f in files) or "아직 안 내보냄"))


SHOW = {k: globals()[f"st_{k}"] for k in STAGES}


# ---------------------------------------------------------------- item trace
def trace_item(c: Ctx, iid: str):
    final = {it["id"]: it for it in c.items()}
    it = final.get(iid)
    if not it:
        sys.exit(f"{iid}: items.json 에 없음")
    print(f"# {iid} {it['kind']} {ft(it['onset'])}~{ft(it.get('t1'))}  (같은 시각 전체: --at {it['onset']:.1f})")
    if it["kind"] == "sfx":
        head("sfx")
        x = next((x for x in c.sfx() if x["id"] == iid), None)
        _sfx_row(x, c) if x else print("  (sfx.json 에 없음)")
        head("screen")
        print("  " + json.dumps(c.j("screen.json", {}).get("items", {}).get(iid), ensure_ascii=False))
    elif it["kind"] == "music":
        head("music")
        x = next((x for x in c.j("music.json", {}).get("items", []) if abs(x["onset"] - it["onset"]) < 0.05), None)
        _music_row(x) if x else print("  (music.json 에서 못 찾음)")
    else:
        lid = it["speaker"]["line_id"]
        head("align")
        ln = next((x for x in c.j("dialogue.json", {}).get("lines", []) if x["id"] == lid), {})
        print(f"  {lid} {ft(ln.get('start'))}~{ft(ln.get('end'))} [{ln.get('speaker')}] {TIMING_KO.get(ln.get('timing'))} "
              f"match {ln.get('match')} | {ln.get('text')}")
        head("speaker")
        print("  " + json.dumps(next((r for r in c.j("speaker.json", {}).get("lines", []) if r["line_id"] == lid), None),
                                ensure_ascii=False))
    if it.get("cue_id"):                       # the script cue it carries (or that made it, origin 'script')
        head("cues")
        q = next((q for q in c.j("cues.json", {}).get("cues", []) if q.get("id") == it["cue_id"]), None)
        _cue_row(q) if q else print("  (cues.json 에 없음)")
    if it["kind"] != "speaker":
        head("dialog_ev")
        print("  " + json.dumps(c.j("dialog_ev.json", {}).get("items", {}).get(iid), ensure_ascii=False))
    head("recommend")
    pre = c.pre_agent().get(iid, {})
    print(f"  에이전트 전 → {pre.get('rec')} : {pre.get('reason')}  (재확인 이유 {pre.get('agent_reasons')})")
    for e in pre.get("evidence", []):
        print(f"    [{e['kind']} {e['polarity']}] {ft(e['t'])} {e['text']}")
    head("agent")
    ag = c.j("agent_results.json", {}).get("items", {}).get(iid)
    if ag:
        print(f"  {ag.get('status')} · 도구 {ag.get('tools')} · 답 {ag.get('recommendation')} · 보임 {ag.get('visible')} "
              f"· 줄거리 {ag.get('plot')} · 종류 맞음 {ag.get('label_ok')}")
        print(f"  요약: {ag.get('summary')}")
        rec = c.j(f"agent/{iid}.json", {})
        for s in rec.get("trace", []):
            print(f"    도구 {s.get('tool')} {json.dumps(s.get('input'), ensure_ascii=False)}")
        print(f"  전체 기록(질문·답): {c.w / 'agent' / (iid + '.json')}")
    else:
        print("  (에이전트가 안 봄)")
    print(f"\n■ 최종 (items.json) → {it['rec']} : {it['reason']} | 문구 {it.get('phrase')}")
    rv = c.j("review.json", {}).get("items", {}).get(iid)
    print(f"  작가 결정: {rv}" if rv else "  작가 결정: 없음 (추천 그대로)")
    head("placement")
    ref = it["speaker"]["line_id"] if it["kind"] == "speaker" else iid
    d = c.j("draft.json", {})
    hit = [x for x in d.get("cells", []) if ref in x.get("refs", [])]
    for x in hit:
        print(f"  {x['id']} {ft(x['start'])}~{ft(x['end'])} {' / '.join(x['lines'])}  {x['check'].get('violations') or ''}")
    for x in d.get("no_room", []):
        if x["item"] == iid:
            print(f"  자리 없음: {x['text']} — {x['reason']}")
    if not hit and not any(x["item"] == iid for x in d.get("no_room", [])):
        print("  초안에 없음 (제외·확인 필요는 넣지 않음)")


# ---------------------------------------------------------------- clips
def write_clips(c: Ctx, a, b):
    import soundfile as sf
    t0 = float(c.meta.get("t0") or 0.0)
    out = c.w / "inspect_clips" / f"{ft(a).replace(':', '-')}_{ft(b).replace(':', '-')}"
    out.mkdir(parents=True, exist_ok=True)
    for name in ("mix", "speech", "music", "effects"):
        f = c.w / "audio" / f"{name}_48k.wav"
        if not f.exists():
            continue
        info = sf.info(str(f))
        s0, s1 = int(max(a - t0, 0) * info.samplerate), int(max(b - t0, 0) * info.samplerate)
        x, sr = sf.read(str(f), start=s0, stop=s1, dtype="float32", always_2d=True)
        sf.write(str(out / f"{name}.wav"), x, sr)
    print(f"\n트랙별 소리 저장: {out}  (mix=원본, speech=대사, music=음악, effects=효과음)")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="단계별 결과 보기")
    ap.add_argument("work")
    ap.add_argument("--at", help="이 시각 앞뒤를 모든 단계에서 보기 (예: 2:33)")
    ap.add_argument("--win", type=float, default=5.0, help="--at 앞뒤 몇 초 (기본 5)")
    ap.add_argument("--item", help="후보 하나를 단계마다 따라가기 (예: S0017, M0003, P0045)")
    ap.add_argument("--stage", help="한 단계 결과 전부: " + ", ".join(STAGES))
    ap.add_argument("--clips", action="store_true", help="--at 구간을 트랙마다 wav 로 잘라 저장")
    args = ap.parse_args()
    c = Ctx(Path(args.work).resolve())
    if not (c.w / "project.json").exists():
        sys.exit(f"{c.w}: project.json 없음 (작업 폴더가 맞는지 확인)")
    m = c.meta
    print(f"# {m.get('title')}  {c.w}")
    if args.item:
        trace_item(c, args.item.upper())
        return
    if args.stage:
        if args.stage not in SHOW:
            sys.exit("단계 이름: " + ", ".join(STAGES))
        head(args.stage)
        SHOW[args.stage](c, None, None, True)
        return
    if args.at:
        t = parse_t(args.at)
        a, b = t - args.win, t + args.win
        print(f"구간 {ft(a)} ~ {ft(b)}")
        import contextlib
        import io
        for s in STAGES:
            if s in NO_TIME:
                continue
            head(s)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                SHOW[s](c, a, b, False)
            sys.stdout.write(buf.getvalue() or "  (이 구간에 결과 없음)\n")
        if args.clips:
            write_clips(c, a, b)
        return
    rl = c.j("run_log.json", {})
    for s in STAGES:
        head(s)
        r = rl.get(s, {})
        if r:
            print(f"  (마지막 실행 {r.get('seconds')}초{' · 결과 있어서 건너뜀' if r.get('skipped') else ''})")
        SHOW[s](c, None, None, False)


if __name__ == "__main__":
    main()
