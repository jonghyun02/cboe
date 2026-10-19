# -*- coding: utf-8 -*-
"""대본 줄 ↔ 받아쓰기 정렬: dialogue lines with times and per-word times.

1. Character alignment. Script text and ASR text are reduced to letters/digits and
   matched with difflib's longest-matching-blocks (contiguous matches first; a plain
   minimum-edit path happily pairs '으아아아' with '굴나오잖' when the ASR missed a
   line). Blocks of >= 2 characters are anchors; each anchor carries the time of its
   ASR character (a word's span divided evenly over its characters). Anchors further
   than 8 s from the median of their line are dropped.
2. A line with anchors on at least half of its characters is ``aligned``: its words
   take the span of their anchored characters, unanchored words are interpolated
   by character position inside the line.
3. Runs of lines without enough anchors (whispers, screams, words the ASR did not
   hear) are placed on human-voice activity between the neighbouring aligned
   lines: Silero VAD regions of the dialogue track, PretrainedSED voice classes on
   the mix, and — for vocalisation lines such as '으아아아아' or '악' — loud events of
   the effects track with any vocal score (Bandit v2 puts screams and laughter in
   the effects stem; the dialogue track is silent there). Lines are matched to the
   regions in order by a small dynamic programme on expected vs region duration.
   Such lines get ``timing: "voice"`` (or ``"even"`` when no region exists) and
   ``aligned: false``.

A timed script (.srt/.vtt) keeps its cue times; ASR words inside each cue only refine
the word times. Without a script the ASR words become the dialogue lines.
"""
from __future__ import annotations

import math
import re
from difflib import SequenceMatcher

import numpy as np

from ..util import log, read_json, write_json

KEEP = re.compile(r"[0-9A-Za-z가-힣ㄱ-ㆎ]")
VOICE = ("Speech", "Male speech, man speaking", "Female speech, woman speaking", "Child speech, kid speaking",
         "Conversation", "Narration, monologue", "Human voice", "Whispering", "Shout", "Yell", "Screaming",
         "Battle cry", "Children shouting", "Laughter", "Belly laugh", "Giggle", "Chuckle, chortle", "Snicker",
         "Crying, sobbing", "Whimper", "Wail, moan", "Groan", "Grunt", "Gasp", "Sigh", "Babbling")
VOICE_ON = 0.3
VOCAL_FX = ("Screaming", "Shout", "Yell", "Battle cry", "Children shouting", "Laughter", "Belly laugh", "Giggle",
            "Crying, sobbing", "Whimper", "Wail, moan", "Groan", "Grunt", "Gasp", "Sigh", "Human voice", "Breathing")
VOCAL_FX_ON = 0.08
# syllables of Korean vocalisations (비명·신음·웃음·감탄): a line made of these is a vocalisation
VOCAL_SYL = set("아악앗어억엇으윽읏흐흑흡흣허헉헐하학핫히힉흥흠음읍오옥우욱와왁에엑이익꺄캬꺅끄끅끙크큭킥후휴휘쿨컥켁푸풉퓨헤해웩퉤쯧칫챠힝앙엉응")
SEC_PER_CHAR = 0.13


def is_vocalisation(text: str) -> bool:
    cs = [c for c in text if "가" <= c <= "힣"]
    return bool(cs) and len(cs) <= 14 and sum(c in VOCAL_SYL for c in cs) >= 0.8 * len(cs)


def norm_chars(text: str) -> list[str]:
    return [c.lower() for c in text if KEEP.match(c)]


def _asr_chars(words: list[dict]):
    chars, times = [], []
    for w in words:
        cs = norm_chars(w["w"])
        if not cs:
            continue
        s, e = float(w["s"]), max(float(w["e"]), float(w["s"]) + 0.02)
        step = (e - s) / len(cs)
        for k, c in enumerate(cs):
            chars.append(c)
            times.append((s + k * step, s + (k + 1) * step))
    return chars, times


def _anchors(lines: list[dict], asr_words: list[dict]):
    """Per script char: (line, word) owner and anchor time or None."""
    s_chars, owner = [], []
    words = []
    for li, ln in enumerate(lines):
        ws = ln["text"].split()
        words.append(ws)
        for wi, w in enumerate(ws):
            for c in norm_chars(w):
                s_chars.append(c)
                owner.append((li, wi))
    a_chars, a_times = _asr_chars(asr_words)
    anchor: list = [None] * len(s_chars)
    if s_chars and a_chars:
        for blk in SequenceMatcher(None, s_chars, a_chars, autojunk=False).get_matching_blocks():
            if blk.size >= 2:
                for k in range(blk.size):
                    anchor[blk.a + k] = a_times[blk.b + k]
    # drop anchors far from the line's median anchor time
    by_line: dict[int, list[int]] = {}
    for k, (li, _) in enumerate(owner):
        by_line.setdefault(li, []).append(k)
    for li, ks in by_line.items():
        ts = [anchor[k][0] for k in ks if anchor[k] is not None]
        if ts:
            med = float(np.median(ts))
            for k in ks:
                if anchor[k] is not None and abs(anchor[k][0] - med) > 8.0:
                    anchor[k] = None
    return words, owner, anchor


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    out, k = [], 0
    while k < len(mask):
        if mask[k]:
            j = k
            while j < len(mask) and mask[j]:
                j += 1
            out.append((k, j))
            k = j
        else:
            k += 1
    return out


def _voice_regions(p, a: float, b: float) -> list[tuple[float, float, float, str]]:
    """Voice activity inside (a, b): [(start, end, score, kind)], kind 'speech' or 'vocal'."""
    regs: list[list] = []
    fps = 25.0
    try:
        from .sed import load as sed_load
        from .sed import load_env
        probs, names, t0 = sed_load(p, "mix")
        v = probs[:, [names.index(c) for c in VOICE if c in names]].max(axis=1)
        f0, f1 = max(int((a - t0) * fps), 0), min(int((b - t0) * fps), len(v))
        for s, e in _runs(v[f0:f1] >= VOICE_ON):
            regs.append([t0 + (f0 + s) / fps, t0 + (f0 + e) / fps, float(v[f0 + s:f0 + e].mean()), "speech"])
        # loud vocal events of the effects track (screams, laughs land there after separation)
        fx, _, _ = sed_load(p, "effects")
        vfx = np.maximum(fx[:, [names.index(c) for c in VOCAL_FX]].max(axis=1),
                         probs[:, [names.index(c) for c in VOCAL_FX]].max(axis=1))
        env = load_env(p)
        # quiet speech (whispers) that Silero VAD drops: the dialogue track is near silent otherwise
        sp, hop = env["speech"], float(env["hop"])
        sthr = max(float(np.percentile(sp, 10)) + 30.0, -65.0)
        s0, s1 = max(int((a - t0) / hop), 0), min(int((b - t0) / hop), len(sp))
        for s, e in _runs(sp[s0:s1] >= sthr):
            if (e - s) * hop >= 0.15:
                regs.append([t0 + (s0 + s) * hop, t0 + (s0 + e) * hop, 0.3, "speech"])
        lev = env["effects"]
        thr = max(float(np.percentile(lev, 60)) + 15.0, float(np.percentile(lev, 95)) - 25.0)
        e0, e1 = max(int((a - t0) / hop), 0), min(int((b - t0) / hop), len(lev))
        for s, e in _runs(lev[e0:e1] >= thr):
            ts, te = t0 + (e0 + s) * hop, t0 + (e0 + e) * hop
            if te - ts < 0.15:
                continue
            sc = float(vfx[int((ts - t0) * fps):int((te - t0) * fps) + 1].max(initial=0.0))
            if sc >= VOCAL_FX_ON:
                regs.append([ts, te, sc, "vocal"])
    except FileNotFoundError:
        pass
    for s, e in (read_json(p.path("asr.json"), {}) or {}).get("vad", []):
        if e > a and s < b:
            regs.append([max(s, a), min(e, b), 0.5, "speech"])
    return merge_regions(regs)


def merge_regions(regs: list) -> list[tuple[float, float, float, str]]:
    """Overlapping regions are one voice event whatever cue found them (a speech region and a
    vocal region over the same scream must not take two lines); same-kind gaps < 0.25 s close."""
    merged: list[list] = []
    for r in sorted(regs):
        if merged and (r[0] < merged[-1][1] or (r[3] == merged[-1][3] and r[0] - merged[-1][1] < 0.25)):
            merged[-1][1] = max(merged[-1][1], r[1])
            merged[-1][2] = max(merged[-1][2], r[2])
            if r[3] == "vocal":
                merged[-1][3] = "vocal"
        else:
            merged.append(list(r))
    return [(s, e, sc, kd) for s, e, sc, kd in merged if e - s >= 0.12]


def _assign(n_chars: list[int], vocal: list[bool], regions: list[tuple]) -> list[int] | None:
    """Monotone assignment of k lines to m regions (consecutive lines may share a long
    region) minimising duration and kind mismatch. Returns the region index per line."""
    k, m = len(n_chars), len(regions)
    if m == 0 or k == 0:
        return None
    # screams and sobs are drawn out: ~0.3 s per syllable instead of speech's 0.13 s
    exp = [max(0.8, 0.3 * c) if vocal[i] else max(0.3, SEC_PER_CHAR * c) for i, c in enumerate(n_chars)]
    dur = [r[1] - r[0] for r in regions]

    def dur_cost(i, j):
        ratio = (dur[j] + 0.2) / (exp[i] + 0.2)
        return abs(math.log(ratio)) * (0.5 if ratio > 1 else 1.0)    # long regions may hold pauses

    def kind_cost(i, j):
        kd = regions[j][3]
        if vocal[i]:
            return -0.4 if kd == "vocal" else 0.3
        return 0.0 if kd == "speech" else 1.0

    cost = [[dur_cost(i, j) - 0.3 * regions[j][2] + kind_cost(i, j) for j in range(m)] for i in range(k)]
    inf = float("inf")
    dp = [[inf] * m for _ in range(k)]
    back = [[-1] * m for _ in range(k)]
    for j in range(m):
        dp[0][j] = cost[0][j]
    for i in range(1, k):
        best, arg = inf, -1
        for j in range(m):
            if j - 1 >= 0 and dp[i - 1][j - 1] < best:
                best, arg = dp[i - 1][j - 1], j - 1
            new = best + cost[i][j] if best < inf else inf
            # share region j with the previous line: only when it is long enough for both
            share = inf
            if dp[i - 1][j] < inf and dur[j] >= 0.8 * (exp[i - 1] + exp[i]):
                share = dp[i - 1][j] + 0.4 + kind_cost(i, j)
            if new <= share:
                dp[i][j], back[i][j] = new, arg
            else:
                dp[i][j], back[i][j] = share, j
    j = int(np.argmin(dp[k - 1]))
    if dp[k - 1][j] == inf:
        return None
    out = [j]
    for i in range(k - 1, 0, -1):
        j = back[i][j]
        out.append(j)
    return out[::-1]


def _even_words(ws: list[str], a: float, b: float) -> list[dict]:
    lens = [max(len(norm_chars(w)), 1) for w in ws]
    tot = sum(lens)
    out, t = [], a
    for w, n in zip(ws, lens):
        d = (b - a) * n / tot
        out.append({"w": w, "s": round(t, 3), "e": round(t + d, 3), "anchored": False})
        t += d
    return out


def align_lines(p, lines: list[dict], asr_words: list[dict], t_lo: float, t_hi: float) -> list[dict]:
    words, owner, anchor = _anchors(lines, asr_words)
    char_ids: dict[tuple[int, int], list[int]] = {}
    for k, o in enumerate(owner):
        char_ids.setdefault(o, []).append(k)
    out = []
    for li, ln in enumerate(lines):
        ws = words[li]
        ks = [k for wi in range(len(ws)) for k in char_ids.get((li, wi), [])]
        n_anch = sum(1 for k in ks if anchor[k] is not None)
        match = n_anch / len(ks) if ks else 0.0
        rec = {**{k: v for k, v in ln.items() if k not in ("start", "end")}, "match": round(match, 3)}
        if match >= 0.5 or n_anch >= 3 or (n_anch >= 2 and match >= 0.3):
            # word times from anchored chars; unanchored chars interpolated by char position
            # inside the anchored span and extrapolated at SEC_PER_CHAR outside it
            pos = np.array(ks, dtype=float)
            have = np.array([anchor[k] is not None for k in ks])
            st = np.array([anchor[k][0] if anchor[k] is not None else np.nan for k in ks])
            en = np.array([anchor[k][1] if anchor[k] is not None else np.nan for k in ks])
            st_i = np.interp(pos, pos[have], st[have])
            en_i = np.interp(pos, pos[have], en[have])
            first, last = int(np.argmax(have)), len(have) - 1 - int(np.argmax(have[::-1]))
            for q in range(first):
                st_i[q] = st[first] - SEC_PER_CHAR * (first - q)
                en_i[q] = st_i[q] + SEC_PER_CHAR
            for q in range(last + 1, len(have)):
                st_i[q] = en[last] + SEC_PER_CHAR * (q - last - 1)
                en_i[q] = st_i[q] + SEC_PER_CHAR
            wt = []
            for wi, w in enumerate(ws):
                cks = char_ids.get((li, wi), [])
                if cks:
                    sel = [ks.index(k) for k in cks]
                    s, e = float(st_i[sel].min()), float(en_i[sel].max())
                    wt.append({"w": w, "s": round(s, 3), "e": round(max(e, s + 0.05), 3),
                               "anchored": bool(any(anchor[k] is not None for k in cks))})
                else:
                    wt.append({"w": w, "s": None, "e": None, "anchored": False})
            for k, w in enumerate(wt):                   # punctuation-only tokens
                if w["s"] is None:
                    ref = next((x for x in wt[k::-1] if x["s"] is not None), None) or \
                        next((x for x in wt[k:] if x["s"] is not None), None)
                    w["s"] = w["e"] = ref["e"]
            rec.update(start=min(w["s"] for w in wt), end=max(w["e"] for w in wt), words=wt,
                       aligned=True, timing="asr")
        else:
            rec.update(start=None, end=None, words=None, aligned=False, timing=None)
        out.append(rec)
    _place_unaligned(p, out, t_lo, t_hi)
    return out


def _place_unaligned(p, out: list[dict], t_lo: float, t_hi: float) -> None:
    i = 0
    while i < len(out):
        if out[i]["aligned"]:
            i += 1
            continue
        j = i
        while j < len(out) and not out[j]["aligned"]:
            j += 1
        a = out[i - 1]["end"] + 0.05 if i > 0 else t_lo
        b = out[j]["start"] - 0.05 if j < len(out) else t_hi
        if b <= a:
            b = a + 0.3 * (j - i)
        run = out[i:j]
        n_chars = [max(len(norm_chars(r["text"])), 1) for r in run]
        vocal = [is_vocalisation(r["text"]) for r in run]
        regions = _voice_regions(p, a, b)
        pick = _assign(n_chars, vocal, regions)
        if pick is not None:
            groups: dict[int, list[int]] = {}
            for q, jj in enumerate(pick):
                groups.setdefault(jj, []).append(q)
            for jj, qs in groups.items():            # lines sharing a region split it by length
                s0, e0 = regions[jj][0], regions[jj][1]
                tot = sum(max(0.3, SEC_PER_CHAR * n_chars[q]) for q in qs)
                t = s0
                for q in qs:
                    d = (e0 - s0) * max(0.3, SEC_PER_CHAR * n_chars[q]) / tot
                    s, e = t, min(t + d, t + max(0.6, 3 * SEC_PER_CHAR * n_chars[q]))
                    run[q].update(start=round(s, 3), end=round(e, 3),
                                  words=_even_words(run[q]["text"].split(), s, e), timing="voice")
                    t += d
        else:
            span = (b - a) / len(run)
            for k, r in enumerate(run):
                s = a + k * span
                e = min(s + max(0.6, SEC_PER_CHAR * n_chars[k]), s + span)
                r.update(start=round(s, 3), end=round(e, 3), words=_even_words(r["text"].split(), s, e),
                         timing="even")
        i = j


def _lines_from_asr(words: list[dict]) -> list[dict]:
    lines, cur = [], []
    for w in words:
        if cur and (w["s"] - cur[-1]["e"] > 0.8 or re.search(r"[.?!]$", cur[-1]["w"])):
            lines.append(cur)
            cur = []
        cur.append(w)
    if cur:
        lines.append(cur)
    return [{"id": f"L{i:04d}", "speaker": None, "text": " ".join(w["w"] for w in ws),
             "start": ws[0]["s"], "end": ws[-1]["e"],
             "words": [{"w": w["w"], "s": w["s"], "e": w["e"], "anchored": True} for w in ws],
             "aligned": True, "match": 1.0, "timing": "asr", "from_asr": True} for i, ws in enumerate(lines, 1)]


def _timed_script(p, lines: list[dict], asr_words: list[dict]) -> list[dict]:
    out = []
    t_lo, t_hi = float(p.meta.get("t0", 0.0)), float(p.meta.get("t1") or 1e12)
    for ln in lines:
        a, b = float(ln["start"]), float(ln["end"])
        if b <= t_lo or a >= t_hi - 0.2:               # outside the processing window
            continue
        a, b = max(a, t_lo), min(b, t_hi)
        inside = [w for w in asr_words if w["e"] > a - 0.3 and w["s"] < b + 0.3]
        al = align_lines(p, [dict(ln)], inside, a, b)[0]
        al["start"], al["end"] = a, b
        if al["aligned"]:
            for w in al["words"]:
                w["s"] = round(min(max(w["s"], a), b), 3)
                w["e"] = round(min(max(w["e"], w["s"]), b), 3)
        else:
            al["words"] = _even_words(ln["text"].split(), a, b)
        al["aligned"], al["timing"] = True, "script"
        out.append(al)
    return out


def run(p, cfg: dict) -> dict:
    if p.path("dialogue.json").exists():
        return {"skipped": True}
    meta = p.meta
    words = (read_json(p.path("asr.json"), {}) or {}).get("words", [])
    script = read_json(p.path("script.json"))
    if not script or not script.get("lines"):
        lines, mode = _lines_from_asr(words), "asr"
    elif script.get("timed"):
        lines, mode = _timed_script(p, script["lines"], words), "timed"
    else:
        lines, mode = align_lines(p, script["lines"], words, float(meta["t0"]), float(meta["t1"])), "aligned"
    write_json(p.path("dialogue.json"), {"mode": mode, "lines": lines})
    timing = {}
    for ln in lines:
        timing[ln.get("timing")] = timing.get(ln.get("timing"), 0) + 1
    log.info("align (%s): %d lines, timing %s", mode, len(lines), timing)
    return {"mode": mode, "lines": len(lines), "timing": timing}
