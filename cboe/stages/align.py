# -*- coding: utf-8 -*-
"""대본 줄 ↔ 받아쓰기 정렬: dialogue lines with times and per-word times.

0. Scenes first (a shooting script, ``format: "screenplay"`` with scenes): ``cboe.scenes``
   finds where each scene is in the video without assuming the script's order, and the
   steps below run inside each found scene only (감사 1-05, 8-02). A scene that is not found
   and leaves no room (no unclaimed ASR words or voice) between the found scenes around it is
   ``absent``; one with room is aligned there (``unknown``, see ``_align_scenes``); scenes
   before / after the found ones in a partial processing window are ``out_of_range``. Other
   scripts (``format`` lines / timed, no scenes) are one scene, as before.
1. Character alignment. Script text and ASR text are reduced to letters/digits and
   matched with difflib's longest-matching-blocks (contiguous matches first; a plain
   minimum-edit path happily pairs '으아아아' with '굴나오잖' when the ASR missed a
   line). Blocks of >= 2 characters are anchors; each anchor carries the time of its
   ASR character (a word's span divided evenly over its characters). Anchors further
   than 8 s from the median of their line are dropped unless they belong to the line's
   main run of anchors, the anchors that follow each other without a gap longer than the
   characters between them can take (a long line keeps its first and last words, 감사 8-m3).
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

Every line gets ``status`` (``_finish``): ``ok`` | ``differs`` (aligned, but the words heard
over it differ: ``compare``) | ``unheard`` (not aligned, placed on voice inside its scene; kept,
since most are short real lines the ASR did not catch, 감사 1-03) | ``absent`` (its scene is not
in the video) | ``out_of_range`` (outside a partial processing window). ``absent`` and
``out_of_range`` lines have ``start``/``end`` null (``guess`` [s, e] when there is an estimate).
A line the script marks as foreign (``language``) cannot match the Korean script text by
letters: it is placed like any other line, never ``differs``, and is ``ok`` with ``note``
"foreign" when it was not heard. ``asr_text`` is what the ASR heard over the line, ``diff``
the word-level differences and ``hints`` ["spelling"] when they look like spelling only.
ASR words that no aligned line uses are ``extras`` (speech that is in no script line, 감사 1-04).

dialogue.json {"mode", "extras": [{"id", "start", "end", "text", "words", "scene_id",
"overlaps"}], "lines": [...]}; scenes.json {"scenes": [{"id", "number", "heading", "start",
"end", "status", "score", "how", "lines"}]}.
"""
from __future__ import annotations

import bisect
import math
import re
import time
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
# anchors: farther than ANCHOR_FAR s from the line median and outside the line's main run (a
# gap between two anchors longer than ANCHOR_GAP + ANCHOR_GAP_PER_CHAR * characters between
# them ends a run) -> dropped. The gap allowance is generous: the old 8 s rule alone is kept for
# everything else, so only anchors it dropped wrongly come back (빙과: the long lines of 8-m3).
ANCHOR_FAR = 8.0
ANCHOR_GAP, ANCHOR_GAP_PER_CHAR = 4.0, 0.5

OK, DIFFERS, UNHEARD, ABSENT, OUT = "ok", "differs", "unheard", "absent", "out_of_range"
NO_TIME = (ABSENT, OUT)
# what a line carries over from script.json (version 2); an older script.json gets these defaults
LINE_DEFAULTS = {"scene_id": None, "language": None, "sung": False, "source": None, "delivery": [], "vocal": []}
EDGE_S = 1.0          # a window starting / ending farther than this from the film start / end is partial
# In a partial window a leading / trailing run of unaligned lines is outside the window when it
# has at least OUT_RUN_LINES lines, or when it was squeezed into less than half the time its
# characters need. Shorter runs stay (unheard), as before. Not tuned on data.
OUT_RUN_LINES = 5
ROOM_MIN = 1.0        # s of unclaimed ASR words or voice scenes that were not found need to be there
                      # (half the time their lines take, when that is less)
# a scene not found by 3-grams is found by its lines with this many anchored letters (or this
# share of its letters, for a short scene)
FOUND_BY_LINES, FOUND_BY_SHARE = 6, 0.4
# a scene not found with at least this many 3-grams (about two sentences) has its lines absent;
# a shorter one keeps them as unheard (감사 1-03: short lines are what the ASR misses)
LONG_SCENE_GRAMS = 20
MINOR_CHARS = 2       # letters that may differ in a stretch before it counts as a different word
EXTRA_GAP = 0.8       # s of silence that ends a group of ASR words that are in no script line
# one-word groups of these (and vocalisations) are not kept as speech that is in no script line
INTERJECTIONS = {"네", "예", "응", "야", "얘", "어머", "어머나", "아이고", "에이", "참", "뭐", "자", "음", "흠", "헐"}


def is_vocalisation(text: str) -> bool:
    cs = [c for c in text if "가" <= c <= "힣"]
    return bool(cs) and len(cs) <= 14 and sum(c in VOCAL_SYL for c in cs) >= 0.8 * len(cs)


def norm_chars(text: str) -> list[str]:
    return [c.lower() for c in text if KEEP.match(c)]


def _asr_chars(words: list[dict], with_word: bool = False):
    """ASR letters with their times (a word's span divided evenly); ``with_word`` also returns
    the index of the word each letter comes from."""
    chars, times, widx = [], [], []
    for wi, w in enumerate(words):
        cs = norm_chars(w["w"])
        if not cs:
            continue
        s, e = float(w["s"]), max(float(w["e"]), float(w["s"]) + 0.02)
        step = (e - s) / len(cs)
        for k, c in enumerate(cs):
            chars.append(c)
            times.append((s + k * step, s + (k + 1) * step))
            widx.append(wi)
    return (chars, times, widx) if with_word else (chars, times)


def _main_run(ks: list[int], anchor: list) -> set[int]:
    """The longest run of a line's anchors (script char indices ``ks`` in order) in which each
    anchor follows the previous one within the time the characters between them can take."""
    runs, cur = [], [ks[0]]
    for k0, k1 in zip(ks, ks[1:]):
        if anchor[k1][0] - anchor[k0][1] > ANCHOR_GAP + ANCHOR_GAP_PER_CHAR * (k1 - k0 - 1):
            runs.append(cur)
            cur = []
        cur.append(k1)
    runs.append(cur)
    return set(max(runs, key=len))


def _anchors(lines: list[dict], asr_words: list[dict]):
    """Per script char: (line, word) owner, anchor time or None, and the ASR word of the anchor."""
    s_chars, owner = [], []
    words = []
    for li, ln in enumerate(lines):
        ws = str(ln.get("text") or "").split()
        words.append(ws)
        for wi, w in enumerate(ws):
            for c in norm_chars(w):
                s_chars.append(c)
                owner.append((li, wi))
    a_chars, a_times, a_word = _asr_chars(asr_words, with_word=True)
    anchor: list = [None] * len(s_chars)
    anchor_w: list = [None] * len(s_chars)
    if s_chars and a_chars:
        for blk in SequenceMatcher(None, s_chars, a_chars, autojunk=False).get_matching_blocks():
            if blk.size >= 2:
                for k in range(blk.size):
                    anchor[blk.a + k] = a_times[blk.b + k]
                    anchor_w[blk.a + k] = a_word[blk.b + k]
    # drop anchors far from the line's median anchor time, unless they are in its main run
    by_line: dict[int, list[int]] = {}
    for k, (li, _) in enumerate(owner):
        by_line.setdefault(li, []).append(k)
    for li, ks in by_line.items():
        have = [k for k in ks if anchor[k] is not None]
        if have:
            med = float(np.median([anchor[k][0] for k in have]))
            far = [k for k in have if abs(anchor[k][0] - med) > ANCHOR_FAR]
            if far:
                keep = _main_run(have, anchor)
                for k in far:
                    if k not in keep:
                        anchor[k] = anchor_w[k] = None
    return words, owner, anchor, anchor_w


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


class Voice:
    """Human-voice activity of one work folder, for placing the lines that were not heard.

    The score files (PretrainedSED of the mix and the effects track, the level envelopes and the
    VAD of asr.json) are read once, on the first ``regions`` call, instead of once per run of
    unaligned lines (감사 1-09: 17 reads of two 56 MB arrays in a 21-minute run)."""

    FPS = 25.0

    def __init__(self, p):
        self.p = p
        self._s: dict | None = None

    def _load(self) -> dict:
        if self._s is not None:
            return self._s
        s: dict = {}
        try:                                   # what loads before a missing file is used, as before
            from .sed import load as sed_load
            from .sed import load_env
            probs, names, t0 = sed_load(self.p, "mix")
            s.update(t0=t0, v=probs[:, [names.index(c) for c in VOICE if c in names]].max(axis=1))
            fx, _, _ = sed_load(self.p, "effects")
            vfx = np.maximum(fx[:, [names.index(c) for c in VOCAL_FX]].max(axis=1),
                             probs[:, [names.index(c) for c in VOCAL_FX]].max(axis=1))
            del probs, fx
            env = load_env(self.p)
            sp, lev = env["speech"], env["effects"]
            s.update(vfx=vfx, sp=sp, hop=float(env["hop"]), lev=lev,
                     sthr=max(float(np.percentile(sp, 10)) + 30.0, -65.0),
                     thr=max(float(np.percentile(lev, 60)) + 15.0, float(np.percentile(lev, 95)) - 25.0))
        except FileNotFoundError:
            pass
        s["vad"] = (read_json(self.p.path("asr.json"), {}) or {}).get("vad", [])
        self._s = s
        return s

    def regions(self, a: float, b: float) -> list[tuple[float, float, float, str]]:
        """Voice activity inside (a, b): [(start, end, score, kind)], kind 'speech' or 'vocal'."""
        s, fps, regs = self._load(), self.FPS, []
        if "v" in s:
            t0, v = s["t0"], s["v"]
            f0, f1 = max(int((a - t0) * fps), 0), min(int((b - t0) * fps), len(v))
            for x, y in _runs(v[f0:f1] >= VOICE_ON):
                regs.append([t0 + (f0 + x) / fps, t0 + (f0 + y) / fps, float(v[f0 + x:f0 + y].mean()), "speech"])
        if "lev" in s:
            t0, hop, sp, lev, vfx = s["t0"], s["hop"], s["sp"], s["lev"], s["vfx"]
            # quiet speech (whispers) that Silero VAD drops: the dialogue track is near silent otherwise
            s0, s1 = max(int((a - t0) / hop), 0), min(int((b - t0) / hop), len(sp))
            for x, y in _runs(sp[s0:s1] >= s["sthr"]):
                if (y - x) * hop >= 0.15:
                    regs.append([t0 + (s0 + x) * hop, t0 + (s0 + y) * hop, 0.3, "speech"])
            # loud vocal events of the effects track (screams, laughs land there after separation)
            e0, e1 = max(int((a - t0) / hop), 0), min(int((b - t0) / hop), len(lev))
            for x, y in _runs(lev[e0:e1] >= s["thr"]):
                ts, te = t0 + (e0 + x) * hop, t0 + (e0 + y) * hop
                if te - ts < 0.15:
                    continue
                sc = float(vfx[int((ts - t0) * fps):int((te - t0) * fps) + 1].max(initial=0.0))
                if sc >= VOCAL_FX_ON:
                    regs.append([ts, te, sc, "vocal"])
        for x, y in s["vad"]:
            if y > a and x < b:
                regs.append([max(x, a), min(y, b), 0.5, "speech"])
        return merge_regions(regs)


def _voice_regions(p, a: float, b: float) -> list[tuple[float, float, float, str]]:
    """Voice activity inside (a, b) of a project (or of a ``Voice`` already read)."""
    return (p if isinstance(p, Voice) else Voice(p)).regions(a, b)


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


def align_lines(p, lines: list[dict], asr_words: list[dict], t_lo: float, t_hi: float,
                voice: Voice | None = None, heard: dict | None = None, place: bool = True) -> list[dict]:
    """Script lines (in order) against ASR words of the stretch [t_lo, t_hi]. ``voice``: the
    voice activity read once for the run (default: read from ``p``). ``heard``: a dict that
    receives, per aligned line index, the indices (into ``asr_words``) of the words its anchors
    came from. ``place=False`` leaves the lines that were not aligned without a time (the caller
    places them, ``_place_unaligned``)."""
    words, owner, anchor, anchor_w = _anchors(lines, asr_words)
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
            if heard is not None:
                heard[li] = sorted({anchor_w[k] for k in ks if anchor[k] is not None})
        else:
            rec.update(start=None, end=None, words=None, aligned=False, timing=None)
        out.append(rec)
    if place:
        _place_unaligned(voice if voice is not None else p, out, t_lo, t_hi)
    return out


def _place_unaligned(p, out: list[dict], t_lo: float, t_hi: float) -> None:
    voice = p if isinstance(p, Voice) else Voice(p)
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
        regions = voice.regions(a, b)
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


def _timed_script(p, lines: list[dict], asr_words: list[dict], heard_g: dict | None = None,
                  voice: Voice | None = None) -> list[dict]:
    """Lines with the script's own times. A line outside the processing window stays in the
    output as ``out_of_range`` (no time, ``guess`` = its script time)."""
    out = []
    t_lo, t_hi = float(p.meta.get("t0", 0.0)), float(p.meta.get("t1") or 1e12)
    voice = voice if voice is not None else Voice(p)
    for ln in lines:
        a, b = float(ln["start"]), float(ln["end"])
        if b <= t_lo or a >= t_hi - 0.2:               # outside the processing window
            out.append(_no_time(ln, OUT, [a, b]))
            continue
        a, b = max(a, t_lo), min(b, t_hi)
        ks = [k for k, w in enumerate(asr_words) if w["e"] > a - 0.3 and w["s"] < b + 0.3]
        heard: dict = {}
        al = align_lines(p, [dict(ln)], [asr_words[k] for k in ks], a, b, voice=voice, heard=heard)[0]
        al["start"], al["end"] = a, b
        if al["aligned"]:
            for w in al["words"]:
                w["s"] = round(min(max(w["s"], a), b), 3)
                w["e"] = round(min(max(w["e"], w["s"]), b), 3)
            if heard_g is not None and 0 in heard:
                heard_g[len(out)] = [ks[x] for x in heard[0]]
        else:
            al["words"] = _even_words(ln["text"].split(), a, b)
        al["status"] = OK                              # the script's own time, heard or not
        al["aligned"], al["timing"] = True, "script"
        out.append(al)
    return out


# ------------------------------------------------------------------ 장면 먼저
def _set_no_time(rec: dict, status: str, guess=None) -> dict:
    """Make ``rec`` (in place) a line without a time in the video."""
    rec.setdefault("match", 0.0)
    rec.update(start=None, end=None, words=None, aligned=False, timing=None, status=status)
    rec.pop("guess", None)
    if guess is not None:
        rec["guess"] = [round(float(guess[0]), 3), round(float(guess[1]), 3)]
    return rec


def _no_time(ln: dict, status: str, guess=None) -> dict:
    return _set_no_time({k: v for k, v in ln.items() if k not in ("start", "end")}, status, guess)


def _unalign(rec: dict) -> None:
    rec.update(start=None, end=None, words=None, aligned=False, timing=None)


def _in_order(seq: list[dict]) -> list[int]:
    """Positions (in ``seq``) of aligned lines that break the time order: the aligned lines not
    in the longest chain whose starts do not go back by more than 0.5 s."""
    pos = [q for q, r in enumerate(seq) if r.get("aligned")]
    if not pos:
        return []
    st = [seq[q]["start"] for q in pos]
    best, back = [1] * len(pos), [-1] * len(pos)
    for i in range(len(pos)):
        for j in range(i - 1, -1, -1):
            if st[j] <= st[i] + 0.5 and best[j] + 1 > best[i]:
                best[i], back[i] = best[j] + 1, j
                if best[j] == j + 1:                 # nothing before j can do better
                    break
    i = max(range(len(pos)), key=lambda x: best[x])
    keep = set()
    while i >= 0:
        keep.add(i)
        i = back[i]
    return [pos[i] for i in range(len(pos)) if i not in keep]


def _align_scenes(p, doc: dict, words: list[dict], t_lo: float, t_hi: float, head_cut: bool,
                  tail_cut: bool, voice: Voice, heard_g: dict) -> tuple[list[dict], list[dict], list]:
    """Scene-first alignment of a shooting script. Returns (lines, per-unit info, gaps), gaps
    being [(start, end, scene_id or None)] of the stretches given to scenes not found.

    1. Found scenes (``cboe.scenes.locate``) are matched one by one (letters, as in
       ``align_lines``) with the ASR words between the matched 3-grams of the found scenes
       before and after them in time: a scene in a film runs much longer than its matched
       3-grams (Sunny: lines with every letter heard 100 s after the last matched 3-gram).
    2. The scenes not found between two found ones (in script order) share the time between
       the matched lines of those two:
       - no room there (less than ROOM_MIN s of ASR words no line uses, and of voice) ->
         ``absent``;
       - room: their lines are matched with the unused words there. A scene with
         FOUND_BY_LINES matched letters (FOUND_BY_SHARE of a short scene's) is found after all
         (``how`` "lines"). Otherwise it is ``unknown``: a short one (fewer than
         LONG_SCENE_GRAMS 3-grams, too little text to be found reliably) keeps its lines, like
         the short lines the ASR misses inside found scenes (감사 1-03); a longer one, whose text
         matched nowhere although there is speech in its place, has its lines ``absent`` with
         the place as ``guess`` — the speech heard there is kept as extras with that scene's id.
       - before the first / after the last found scene of a partial window -> ``out_of_range``.
    3. All lines that keep a place, in film order (found scenes by time, the scenes not found
       after the found scene before them), lose matches that go back in time (``_in_order``),
       and the lines not matched are placed on voice between their matched neighbours
       (``_place_unaligned``, as for a script without scenes) -> ``unheard``."""
    from .. import scenes as S
    lines = doc["lines"]
    us = S.units(lines)
    loc = S.locate(us, words, t_lo, t_hi)
    out: list = [None] * len(lines)
    starts = [float(w["s"]) for w in words]
    claimed: set[int] = set()

    def align(uis, lo, hi):
        idx = [i for ui in uis for i in us[ui]["idx"]]
        k0, k1 = bisect.bisect_left(starts, lo), bisect.bisect_right(starts, hi)
        ks = [k for k in range(k0, k1) if k not in claimed]
        heard: dict = {}
        res = align_lines(p, [lines[i] for i in idx], [words[k] for k in ks], lo, hi, voice=voice,
                          heard=heard, place=False)
        for q, i in enumerate(idx):
            out[i] = res[q]
            if q in heard:
                heard_g[i] = [ks[x] for x in heard[q]]
                claimed.update(heard_g[i])
        return idx

    runs, prev, cur = [], None, []
    for ui in range(len(us)):
        if loc[ui]["status"] == "found":
            if loc[ui]["how"] == "moved":
                continue
            if cur:
                runs.append((prev, ui, cur))
                cur = []
            prev = ui
        else:
            cur.append(ui)
    if cur:
        runs.append((prev, None, cur))
    order = sorted((ui for ui in range(len(us)) if loc[ui]["status"] == "found"), key=lambda ui: (loc[ui]["a"], ui))
    for k, ui in enumerate(order):
        lo = loc[order[k - 1]]["b"] if k else t_lo
        hi = loc[order[k + 1]]["a"] if k + 1 < len(order) else t_hi
        align([ui], min(lo, loc[ui]["a"]), max(hi, loc[ui]["b"]))
    state = {ui: "found" for ui in order}

    def edge_time(ui, first):
        ts = [out[i]["start" if first else "end"] for i in us[ui]["idx"] if out[i]["aligned"]]
        if not ts:
            return loc[ui]["a"] if first else loc[ui]["b"]
        return min(ts) if first else max(ts)

    gaps, after = [], {}
    for prev, nxt, uis in runs:
        idx = [i for ui in uis for i in us[ui]["idx"]]
        after.setdefault(prev, []).extend(uis)
        if (prev is None and head_cut) or (nxt is None and tail_cut):
            for i in idx:
                out[i] = _no_time(lines[i], OUT)
            for ui in uis:
                state[ui] = OUT
            continue
        g0 = edge_time(prev, False) + 0.05 if prev is not None else t_lo
        g1 = edge_time(nxt, True) - 0.05 if nxt is not None else t_hi
        sid = us[uis[0]]["scene_id"] if len({us[ui]["scene_id"] for ui in uis}) == 1 else None
        # room: ROOM_MIN s of speech, or half the time the lines need when they are shorter
        need = min(ROOM_MIN, 0.5 * sum(max(0.3, SEC_PER_CHAR * len(norm_chars(str(lines[i].get("text") or ""))))
                                       for i in idx))
        if g1 - g0 >= need:
            gaps.append((g0, g1, sid))
        k0, k1 = bisect.bisect_left(starts, g0), bisect.bisect_right(starts, g1)
        room = sum(float(words[k]["e"]) - float(words[k]["s"]) for k in range(k0, k1) if k not in claimed)
        if g1 - g0 >= need and room < need:
            room += sum(r[1] - r[0] for r in voice.regions(g0, g1))
        if g1 - g0 < need or room < need:
            for i in idx:
                out[i] = _no_time(lines[i], ABSENT, (g0, g1) if g1 > g0 else None)
            for ui in uis:
                state[ui] = ABSENT
            continue
        align(uis, g0, g1)
        for ui in uis:
            got = sum(round(out[i]["match"] * len(norm_chars(str(lines[i].get("text") or ""))))
                      for i in us[ui]["idx"] if out[i]["aligned"])
            if got and got >= min(FOUND_BY_LINES, FOUND_BY_SHARE * us[ui]["chars"]):
                state[ui] = "found"
                loc[ui]["how"] = "lines"
                continue
            state[ui] = "unknown"
            # lines outside every scene (before the first scene heading: a cover or cast page the
            # reader took for dialogue) are not kept on a guess either
            if len(us[ui]["grams"]) >= LONG_SCENE_GRAMS or us[ui]["scene_id"] is None:
                for i in us[ui]["idx"]:
                    claimed.difference_update(heard_g.pop(i, []))
                    _set_no_time(out[i], ABSENT, (g0, g1))
    # film order: found scenes by time, the scenes not found after the found scene before them
    film = list(after.get(None, []))
    for ui in order:
        film.append(ui)
        film += after.get(ui, [])
    seq = [out[i] for ui in film for i in us[ui]["idx"] if out[i].get("status") not in NO_TIME]
    for q in _in_order(seq):
        _unalign(seq[q])
    gone = {id(r) for r in seq if not r["aligned"]}
    for i in list(heard_g):
        if id(out[i]) in gone:
            del heard_g[i]
    _edge_out(seq, t_lo, t_hi, head_cut, tail_cut)
    seq = [r for r in seq if r.get("status") not in NO_TIME]
    _place_unaligned(voice, seq, t_lo, t_hi)
    for r in seq:
        r["status"] = OK if r["aligned"] else UNHEARD
    info = [{"scene_id": u["scene_id"], "idx": u["idx"], "state": state.get(ui, ABSENT),
             "score": loc[ui].get("score", 0.0), "how": loc[ui].get("how"), "a": loc[ui].get("a"),
             "b": loc[ui].get("b")} for ui, u in enumerate(us)]
    return out, info, gaps


def _scene_rows(doc: dict, out: list[dict], info: list[dict] | None) -> list[dict]:
    """scenes.json rows for the scenes of the script. Without scene-first alignment (``info``
    None) a scene is found when one of its lines was aligned. A scene without dialogue is
    ``unknown`` with the time between its neighbours (``out_of_range`` beside such scenes)."""
    by_sid: dict = {}
    for i, ln in enumerate(out):
        by_sid.setdefault(ln.get("scene_id"), []).append(i)
    unit_by_sid: dict = {}
    for u in info or []:
        unit_by_sid.setdefault(u["scene_id"], []).append(u)
    rank = {"found": 0, "unknown": 1, ABSENT: 2, OUT: 3}
    rows = []
    for sc in doc.get("scenes") or []:
        idx = by_sid.get(sc.get("id"), [])
        timed = [out[i] for i in idx if out[i].get("start") is not None]
        row = {"id": sc.get("id"), "number": sc.get("number"), "heading": sc.get("heading"),
               "start": round(min(r["start"] for r in timed), 3) if timed else None,
               "end": round(max(r["end"] for r in timed), 3) if timed else None,
               "status": None, "score": None, "how": None, "lines": [out[i]["id"] for i in idx]}
        ui = unit_by_sid.get(sc.get("id"))
        if ui:
            best = min(ui, key=lambda u: rank[u["state"]])
            row.update(status=best["state"], score=max(u["score"] for u in ui), how=best["how"])
        elif idx:
            row["status"] = "found" if any(out[i].get("aligned") for i in idx) else \
                OUT if all(out[i].get("status") == OUT for i in idx) else "unknown"
        if row["status"] == ABSENT:
            gs = [out[i]["guess"] for i in idx if out[i].get("guess")]
            if gs:
                row["guess"] = [min(g[0] for g in gs), max(g[1] for g in gs)]
        rows.append(row)
    for k, row in enumerate(rows):                     # scenes without dialogue: between the neighbours
        if row["lines"] or row["status"]:
            continue
        before = next((r for r in rows[k - 1::-1] if r["lines"]), None) if k else None
        after = next((r for r in rows[k + 1:] if r["lines"]), None)
        if (before and before["status"] == OUT and (not after or after["status"] == OUT)) or \
                (after and after["status"] == OUT and not before):
            row["status"] = OUT
            continue
        row["status"] = "unknown"
        a = next((r["end"] for r in rows[k - 1::-1] if r["end"] is not None), None) if k else None
        b = next((r["start"] for r in rows[k + 1:] if r["start"] is not None), None)
        row["start"], row["end"] = a, b
    return rows


# ------------------------------------------------------------------ 대조
def _jamo(c: str):
    k = ord(c) - 0xAC00
    return (k // 588, (k % 588) // 28, k % 28) if 0 <= k < 11172 else None


def _spelling_like(a: str, b: str) -> bool:
    """Same length, Hangul, at most one jamo apart per syllable (에요/예요, 쨰/째, 안/않)."""
    if len(a) != len(b) or not a or a == b:
        return False
    for x, y in zip(a, b):
        if x != y:
            jx, jy = _jamo(x), _jamo(y)
            if jx is None or jy is None or sum(p != q for p, q in zip(jx, jy)) > 1:
                return False
    return True


def compare(text: str, heard: list[str], names=()) -> tuple[list[dict], list[str]]:
    """Word-level differences between a script line and the ASR words heard over it.

    Spaces, punctuation and case are ignored (both sides are compared as letters and digits).
    Each stretch where they differ is widened to whole words on both sides:
    ``{"kind", "script", "heard", "at": [i, j]}`` (``at``: script word range, end exclusive).
    kind ``"word"``: more than MINOR_CHARS letters differ, or two that are not alike — a
    different word (the line ``differs``). ``"minor"``: one or two letters (most are ASR
    mishearings). ``"spelling": true`` on a minor stretch whose words have as many letters on
    both sides and differ by one jamo per syllable (에요/예요, 쨰/째) and that holds none of
    ``names`` (the ASR mishears names that way): the line gets the hint "spelling" (감사
    1-m3; a hint only, the script text is kept). ``"number"``: the differing letters have
    digits (3층 / 삼 층; not compared). Returns (diff, hints)."""
    keys = [k for k in ("".join(norm_chars(str(n))) for n in names or ()) if len(k) >= 2]
    sw = text.split()
    s_c, s_o, h_c, h_o = [], [], [], []
    for i, w in enumerate(sw):
        for c in norm_chars(w):
            s_c.append(c)
            s_o.append(i)
    for j, w in enumerate(heard):
        for c in norm_chars(w):
            h_c.append(c)
            h_o.append(j)
    if not s_c or not h_c:
        return [], []
    chunks: list[dict] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, s_c, h_c, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        sws, hws = set(s_o[i1:i2]), set(h_o[j1:j2])
        if not sws and 0 < i1 < len(s_c) and s_o[i1 - 1] == s_o[i1]:
            sws = {s_o[i1]}                    # letters missing inside a word
        if not hws and 0 < j1 < len(h_c) and h_o[j1 - 1] == h_o[j1]:
            hws = {h_o[j1]}
        c = {"s": sws, "h": hws, "n": max(i2 - i1, j2 - j1),
             "sub": [("".join(s_c[i1:i2]), "".join(h_c[j1:j2]))]}
        if chunks and (c["s"] & chunks[-1]["s"] or c["h"] & chunks[-1]["h"]):
            last = chunks[-1]
            last["s"] |= sws
            last["h"] |= hws
            last["n"] += c["n"]
            last["sub"] += c["sub"]
        else:
            chunks.append(c)
    out, hints = [], []
    for c in chunks:
        s_txt = " ".join(sw[i] for i in sorted(c["s"]))
        h_txt = " ".join(heard[j] for j in sorted(c["h"]))
        row = {"kind": "word", "script": s_txt, "heard": h_txt,
               "at": [min(c["s"]), max(c["s"]) + 1] if c["s"] else None}
        s_key, h_key = "".join(norm_chars(s_txt)), "".join(norm_chars(h_txt))
        if any(not a or not b for a, b in c["sub"]) and not (s_key and h_key):
            alike = True                       # a short word not heard / heard in addition
        else:
            # letters swapped: one, or letters one jamo apart (sound-alike); letters added or lost
            # inside words: most letters kept on both sides
            alike = all(len(a) < 2 or _spelling_like(a, b) for a, b in c["sub"] if len(a) == len(b))
            if alike and any(len(a) != len(b) for a, b in c["sub"]):
                kept_s = len(s_key) - sum(len(a) for a, _ in c["sub"])
                kept_h = len(h_key) - sum(len(b) for _, b in c["sub"])
                alike = kept_s >= 0.5 * len(s_key) and kept_h >= 0.5 * len(h_key)
        if any(ch.isdigit() for a, b in c["sub"] for ch in a + b):
            row["kind"] = "number"
        elif c["n"] <= MINOR_CHARS and alike:
            row["kind"] = "minor"
            if all(_spelling_like(a, b) for a, b in c["sub"]) and len(s_key) == len(h_key) and \
                    not any(k in s_key or k in h_key for k in keys):
                row["spelling"] = True
                if "spelling" not in hints:
                    hints.append("spelling")
        out.append(row)
    return out, hints


# ------------------------------------------------------------------ 마무리
def _edge_out(out: list[dict], t_lo: float, t_hi: float, head_cut: bool, tail_cut: bool) -> int:
    """In a partial window: the leading / trailing run of lines that were not aligned (before
    the first / after the last aligned line) is outside the window when it is OUT_RUN_LINES
    long or was squeezed (감사 1-m1, 8-m1). Returns the number of lines set out of range."""
    loose = [i for i, r in enumerate(out) if not r.get("aligned") and r.get("status") not in NO_TIME]
    al = [i for i, r in enumerate(out) if r.get("aligned")]
    n = 0
    for cut, run, room in ((head_cut, [i for i in loose if not al or i < al[0]],
                            (out[al[0]]["start"] - t_lo) if al else t_hi - t_lo),
                           (tail_cut, [i for i in loose if al and i > al[-1]],
                            (t_hi - out[al[-1]]["end"]) if al else 0.0)):
        if not cut or not run:
            continue
        need = sum(max(0.3, SEC_PER_CHAR * max(len(norm_chars(str(out[i].get("text") or ""))), 1)) for i in run)
        if len(run) >= OUT_RUN_LINES or room < 0.5 * need:
            for i in run:
                _set_no_time(out[i], OUT)
                n += 1
    return n


def _finish(out: list[dict], words: list[dict], heard_g: dict, names=()) -> list[dict]:
    """Statuses and what was heard over each line (``asr_text``, ``diff``, ``hints``)."""
    names = set(names or ()) | {ln.get("speaker") for ln in out if ln.get("speaker")}
    for r in out:
        for k, v in LINE_DEFAULTS.items():
            r.setdefault(k, list(v) if isinstance(v, list) else v)
        r.setdefault("raw", r.get("text"))
        r.setdefault("status", OK if r.get("aligned") else UNHEARD)
    # every ASR word belongs to at most one aligned line: the one its anchors went to, else the
    # aligned line whose span holds its middle
    owner: dict[int, int] = {}
    for i, ks in sorted(heard_g.items()):
        if out[i].get("aligned") and out[i].get("start") is not None:
            for k in ks:
                owner.setdefault(k, i)
    spans = sorted((r["start"], r["end"], i) for i, r in enumerate(out)
                   if r.get("aligned") and r.get("start") is not None)
    s_starts = [s for s, _, _ in spans]
    for k, w in enumerate(words):
        if k in owner:
            continue
        m = (float(w["s"]) + float(w["e"])) / 2
        j = bisect.bisect_right(s_starts, m + 0.05)
        best = None
        for s, e, i in spans[max(0, j - 8):j]:
            if s - 0.05 <= m <= e + 0.05 and (best is None or abs(m - (s + e) / 2) < best[0]):
                best = (abs(m - (s + e) / 2), i)
        if best:
            owner[k] = best[1]
    by_line: dict[int, list[int]] = {}
    for k, i in owner.items():
        by_line.setdefault(i, []).append(k)
    free = sorted(((float(words[k]["s"]) + float(words[k]["e"])) / 2, k) for k in range(len(words)) if k not in owner)
    f_mid = [m for m, _ in free]
    for i, r in enumerate(out):
        if r.get("start") is None:
            r["asr_text"] = ""
            continue
        if r.get("aligned"):
            ks = sorted(by_line.get(i, []))
        else:                                  # what was heard where an unheard line was put
            ks = [k for _, k in free[bisect.bisect_left(f_mid, r["start"] - 0.05):bisect.bisect_right(f_mid, r["end"] + 0.05)]]
        r["asr_text"] = " ".join(str(words[k]["w"]).strip() for k in ks)
        r.pop("diff", None)
        r.pop("hints", None)
        if r.get("language"):                  # 외국어 줄: the script text is a translation
            r["note"] = "foreign"
            if r["status"] in (UNHEARD, DIFFERS):
                r["status"] = OK
            continue
        if (r.get("aligned") and r.get("timing") == "asr") or (r.get("timing") == "script" and i in heard_g):
            diff, hints = compare(str(r.get("text") or ""), [str(words[k]["w"]) for k in ks], names)
            if diff:
                r["diff"] = diff
            if hints:
                r["hints"] = hints
            if any(d["kind"] == "word" for d in diff):
                r["status"] = DIFFERS
    return out


def _extras(out: list[dict], words: list[dict], heard_g: dict, hotwords, gaps=()) -> list[dict]:
    """ASR words no aligned line uses, grouped where they follow each other (gap < EXTRA_GAP s,
    or a sentence end). Dropped: one-word groups that are an interjection or a vocalisation and
    groups that only repeat the ASR hint names (``asr.hint_echo``)."""
    from .asr import hint_echo
    used: set[int] = set()
    for i, ks in heard_g.items():
        if out[i].get("aligned") and out[i].get("start") is not None:
            used.update(ks)
    spans = [(r["start"], r["end"]) for r in out if r.get("aligned") and r.get("start") is not None]
    spans.sort()
    s_starts = [s for s, _ in spans]

    def inside(w):
        m = (float(w["s"]) + float(w["e"])) / 2
        j = bisect.bisect_right(s_starts, m + 0.05)
        return any(s - 0.05 <= m <= e + 0.05 for s, e in spans[max(0, j - 8):j])

    groups, cur, last = [], [], None
    for k, w in enumerate(words):
        if k in used or inside(w):
            if cur:
                groups.append(cur)
            cur, last = [], None
            continue
        if cur and (float(w["s"]) - float(words[last]["e"]) > EXTRA_GAP or re.search(r"[.?!]$", str(words[last]["w"]))):
            groups.append(cur)
            cur = []
        cur.append(k)
        last = k
    if cur:
        groups.append(cur)
    timed = sorted((r["start"], r["end"], r.get("scene_id"), r["id"], r.get("status")) for r in out
                   if r.get("start") is not None)
    extras = []
    for g in groups:
        ws = [words[k] for k in g]
        toks = [str(w["w"]).strip() for w in ws]
        if len(ws) == 1 and ("".join(norm_chars(toks[0])) in INTERJECTIONS or is_vocalisation(toks[0])
                             or len(norm_chars(toks[0])) < 1):
            continue
        if hotwords and all(hint_echo(toks, hotwords)):
            continue
        s, e = float(ws[0]["s"]), float(ws[-1]["e"])
        m = (s + e) / 2
        sid = next((g_sid for g0, g1, g_sid in gaps if g0 <= m <= g1), "none")
        if sid == "none":
            near = min(timed, key=lambda t: 0.0 if t[0] <= m <= t[1] else min(abs(m - t[0]), abs(m - t[1])),
                       default=None)
            sid = near[2] if near else None
        row = {"id": f"X{len(extras) + 1:04d}", "start": round(s, 3), "end": round(e, 3), "text": " ".join(toks),
               "words": [{"w": str(w["w"]).strip(), "s": w["s"], "e": w["e"]} for w in ws], "scene_id": sid}
        ov = [t[3] for t in timed if t[4] == UNHEARD and t[0] < e and t[1] > s]
        if ov:
            row["overlaps"] = ov
        extras.append(row)
    return extras


def run(p, cfg: dict) -> dict:
    if p.path("dialogue.json").exists():
        return {"skipped": True}
    t_start = time.time()
    meta = p.meta
    asr = read_json(p.path("asr.json"), {}) or {}
    words = asr.get("words", [])
    script = read_json(p.path("script.json"))
    t_lo = float(meta.get("t0") or 0.0)
    t_hi = float(meta["t1"]) if meta.get("t1") is not None else \
        float(meta.get("duration") or (max((float(w["e"]) for w in words), default=0.0) + 5.0))
    dur = meta.get("duration")
    head_cut = t_lo > EDGE_S
    tail_cut = dur is not None and t_hi < float(dur) - EDGE_S
    heard_g: dict = {}
    voice = Voice(p)
    info, gaps, extras = None, [], []
    if not script or not script.get("lines"):
        lines, mode = _lines_from_asr(words), "asr"
        for ln in lines:
            ln.update(status=OK, asr_text=ln["text"])
    else:
        hotwords = asr["hotwords"] if "hotwords" in asr else script.get("characters")
        if script.get("timed"):
            lines, mode = _timed_script(p, script["lines"], words, heard_g, voice), "timed"
        elif script.get("format") == "screenplay" and script.get("scenes") and \
                any(ln.get("scene_id") for ln in script["lines"]):
            words = sorted(words, key=lambda w: float(w["s"]))
            lines, info, gaps = _align_scenes(p, script, words, t_lo, t_hi, head_cut, tail_cut, voice, heard_g)
            mode = "scenes"
        else:
            lines = align_lines(p, script["lines"], words, t_lo, t_hi, voice=voice, heard=heard_g, place=False)
            _edge_out(lines, t_lo, t_hi, head_cut, tail_cut)
            seq = [r for r in lines if r.get("status") not in NO_TIME]
            _place_unaligned(voice, seq, t_lo, t_hi)
            mode = "aligned"
        _finish(lines, words, heard_g, script.get("characters"))
        extras = _extras(lines, words, heard_g, hotwords, gaps)
    write_json(p.path("dialogue.json"), {"mode": mode, "extras": extras, "lines": lines})
    rows = _scene_rows(script, lines, info) if script and script.get("lines") else []
    write_json(p.path("scenes.json"), {"scenes": rows})
    timing, status, sc = {}, {}, {}
    for ln in lines:
        timing[ln.get("timing")] = timing.get(ln.get("timing"), 0) + 1
        status[ln.get("status")] = status.get(ln.get("status"), 0) + 1
    for r in rows:
        sc[r["status"]] = sc.get(r["status"], 0) + 1
    log.info("align (%s): %d lines, timing %s, status %s, scenes %s, extras %d (%.1f s)", mode, len(lines),
             timing, status, sc, len(extras), time.time() - t_start)
    return {"mode": mode, "lines": len(lines), "timing": timing, "status": status, "scenes": sc,
            "extras": len(extras)}
