# -*- coding: utf-8 -*-
"""장면 찾기: where each script scene is in the video, before the lines are aligned (감사 1-05, 8-02, 8-07).

A *unit* is a run of consecutive script lines of one scene (``units``). Its dialogue is reduced
to letters and digits (``align.norm_chars``, spaces and punctuation dropped) and cut into
character 3-grams. Every 3-gram that also occurs in the ASR letter stream votes with the time
where it occurs (a 3-gram that occurs very often in the stream does not vote). A window of
``span`` seconds (SPAN_PER_CHAR s per letter of the unit, at least SPAN_MIN) scores the share
of the unit's distinct 3-grams that occur inside it; the found interval is the shortest stretch
of the window holding CORE_SHARE of those 3-grams (a scene shares words with its neighbours, and
their votes would stretch the interval over them). The interval is a core: the lines are matched
over the whole time up to the neighbouring found scenes (``align._align_scenes``).

1. Skeleton: up to CANDIDATES windows per unit with score >= STRONG and >= MIN_HITS 3-grams,
   searched over the whole processing window with no order constraint. The chain of candidates
   that follows both the script order and the time order with the largest total score is the
   skeleton (most scenes of a film keep the script's order).
2. Moved: a unit outside the skeleton whose best window scores >= MOVED with >= MOVED_HITS
   3-grams and does not overlap the skeleton is taken where it is (``how`` "moved").
3. Weak: the units between two skeleton scenes (or before the first / after the last) are
   searched again only inside the time between them, with the lower WEAK and MIN_HITS_WEAK, and
   chained in order (``how`` "weak").

What is left is decided by ``cboe.stages.align`` (absent / unknown / out of range).

The numbers come from one feature film (Sunny: the original mix transcribed whole, 94 scenes,
753 lines; scratch notes, not in the repository): the scenes that are certainly there score
0.3-0.6 (about half of a scene's 3-grams survive the ASR and the actors' changes), the best
window of a scene that is not there scores up to 0.11 anywhere in the film, and weak scenes'
best windows over the whole film are often 30 minutes away from their neighbours, which is why
they are searched again only between found scenes. Not validated on a second film.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict

from .stages.align import _asr_chars, norm_chars

N = 3
STRONG, MOVED, WEAK = 0.25, 0.5, 0.12
MIN_GRAMS = 8                 # fewer 3-grams: too little text to find a scene by itself
MIN_HITS, MIN_HITS_WEAK, MOVED_HITS = 4, 3, 8
CANDIDATES = 3
SPAN_MIN, SPAN_PER_CHAR = 25.0, 0.3
COMMON_PER, COMMON_MIN = 350, 10   # a 3-gram seen more than max(COMMON_MIN, letters / COMMON_PER) times does not vote
CORE_SHARE = 0.8             # the found interval: the shortest stretch with this share of the window's 3-grams
OVERLAP_TOL = 2.0             # s two found scenes may overlap and still be in order


def units(lines: list[dict]) -> list[dict]:
    """Runs of consecutive lines with the same ``scene_id``: [{"scene_id", "idx", "chars", "grams",
    "foreign"}]. ``foreign``: at least half the letters are in lines the script marks with a
    ``language`` — the script has the Korean translation and the film another language, so the
    letters cannot find the scene (it is placed on voice, ``align._align_scenes``)."""
    out: list[dict] = []
    for i, ln in enumerate(lines):
        sid = ln.get("scene_id")
        if out and out[-1]["scene_id"] == sid:
            out[-1]["idx"].append(i)
        else:
            out.append({"scene_id": sid, "idx": [i]})
    for u in out:
        cs = [c for i in u["idx"] for c in norm_chars(str(lines[i].get("text") or ""))]
        u["chars"] = len(cs)
        u["grams"] = sorted({"".join(cs[k:k + N]) for k in range(len(cs) - N + 1)})
        foreign = sum(len(norm_chars(str(lines[i].get("text") or ""))) for i in u["idx"] if lines[i].get("language"))
        u["foreign"] = bool(cs) and foreign >= 0.5 * len(cs)
    return out


class Stream:
    """The ASR letters of the processing window with a 3-gram index (gram -> start times)."""

    def __init__(self, words: list[dict]):
        chars, times = _asr_chars(words)
        self.index: dict[str, list[float]] = defaultdict(list)
        for k in range(len(chars) - N + 1):
            self.index["".join(chars[k:k + N])].append(times[k][0])
        self.cap = max(COMMON_MIN, len(chars) // COMMON_PER)

    def hits(self, grams: list[str], lo: float = float("-inf"), hi: float = float("inf"),
             skip: list[tuple[float, float]] = ()) -> list[tuple[float, int]]:
        out = []
        for gi, g in enumerate(grams):
            occ = self.index.get(g)
            if not occ or len(occ) > self.cap:
                continue
            for t in occ:
                if lo <= t <= hi and not any(a <= t <= b for a, b in skip):
                    out.append((t, gi))
        out.sort()
        return out


def windows(hits: list[tuple[float, int]], n_grams: int, span: float, k: int = CANDIDATES) -> list[dict]:
    """Up to ``k`` non-overlapping windows with the most distinct 3-grams, best first:
    [{"a", "b", "hits", "score"}]."""
    if not hits or not n_grams:
        return []
    ts = [h[0] for h in hits]
    cnt: Counter = Counter()
    rows, r = [], 0
    for left in range(len(hits)):
        while r < len(hits) and ts[r] <= ts[left] + span:
            cnt[hits[r][1]] += 1
            r += 1
        rows.append((len(cnt), left, r))
        g = hits[left][1]
        cnt[g] -= 1
        if not cnt[g]:
            del cnt[g]
    rows.sort(key=lambda x: (-x[0], x[1]))
    picked: list[dict] = []
    for d, left, right in rows:
        if any(ts[left] <= p["wb"] and ts[right - 1] >= p["wa"] for p in picked):
            continue
        lo, hi = _core(hits, left, right, d)
        picked.append({"a": round(ts[lo], 3), "b": round(ts[hi - 1] + 0.3, 3), "hits": d,
                       "score": round(d / n_grams, 3), "wa": ts[left], "wb": ts[right - 1]})
        if len(picked) >= k:
            break
    for p in picked:
        del p["wa"], p["wb"]
    return picked


def _core(hits: list[tuple[float, int]], left: int, right: int, d: int) -> tuple[int, int]:
    """The shortest run of hits[left:right] holding CORE_SHARE of its ``d`` distinct 3-grams: the
    window without the stray votes at its edges (3-grams the scene shares with its neighbours)."""
    need = max(1, math.ceil(CORE_SHARE * d))
    cnt: Counter = Counter()
    best, r = (left, right), left
    for lo in range(left, right):
        while r < right and len(cnt) < need:
            cnt[hits[r][1]] += 1
            r += 1
        if len(cnt) < need:
            break
        if hits[r - 1][0] - hits[lo][0] < hits[best[1] - 1][0] - hits[best[0]][0]:
            best = (lo, r)
        g = hits[lo][1]
        cnt[g] -= 1
        if not cnt[g]:
            del cnt[g]
    return best


def _chain(cands: list[tuple[int, dict]]) -> list[tuple[int, dict]]:
    """The chain of candidates, increasing in unit (script order) and in time, with the largest
    total score (at most one candidate per unit)."""
    if not cands:
        return []
    cs = sorted(cands, key=lambda c: (c[0], c[1]["a"]))
    best = [c[1]["score"] for c in cs]
    back = [-1] * len(cs)
    for i, (ui, w) in enumerate(cs):
        for j in range(i):
            uj, wj = cs[j]
            if uj < ui and wj["b"] <= w["a"] + OVERLAP_TOL and best[j] + w["score"] > best[i]:
                best[i], back[i] = best[j] + w["score"], j
    i = max(range(len(cs)), key=lambda x: best[x])
    out = []
    while i >= 0:
        out.append(cs[i])
        i = back[i]
    return out[::-1]


def _span(u: dict) -> float:
    return max(SPAN_MIN, SPAN_PER_CHAR * u["chars"])


def locate(us: list[dict], words: list[dict], t_lo: float, t_hi: float) -> list[dict]:
    """Per unit {"status": "found" | "none", "score", "hits", "a", "b", "how"} (a, b, how only
    when found; ``score`` is the best window score anywhere, for a unit not found)."""
    st = Stream([w for w in words if t_lo <= float(w["s"]) <= t_hi])
    res: list[dict] = [{"status": "none", "score": 0.0, "hits": 0} for _ in us]
    cands = []
    for ui, u in enumerate(us):
        if len(u["grams"]) < MIN_GRAMS or u.get("foreign"):
            continue
        ws = windows(st.hits(u["grams"]), len(u["grams"]), _span(u))
        if ws:
            res[ui].update(score=ws[0]["score"], hits=ws[0]["hits"])
        cands += [(ui, w) for w in ws if w["score"] >= STRONG and w["hits"] >= MIN_HITS]

    def take(ui, w, how):
        res[ui].update(status="found", a=w["a"], b=w["b"], score=w["score"], hits=w["hits"], how=how)

    chain = _chain(cands)
    for ui, w in chain:
        take(ui, w, "skeleton")
    claimed = [(w["a"], w["b"]) for _, w in chain]
    moved = []
    for ui, w in sorted(cands, key=lambda c: -c[1]["score"]):
        if res[ui]["status"] == "found" or w["score"] < MOVED or w["hits"] < MOVED_HITS:
            continue
        if any(w["a"] < b - OVERLAP_TOL and w["b"] > a + OVERLAP_TOL for a, b in claimed):
            continue
        take(ui, w, "moved")
        claimed.append((w["a"], w["b"]))
        moved.append((w["a"], w["b"]))
    ends = [(-1, t_lo, t_lo)] + [(ui, w["a"], w["b"]) for ui, w in chain] + [(len(us), t_hi, t_hi)]
    for (u0, _, g0), (u1, g1, _) in zip(ends, ends[1:]):
        todo = [ui for ui in range(u0 + 1, u1) if res[ui]["status"] != "found" and len(us[ui]["grams"]) >= MIN_GRAMS
                and not us[ui].get("foreign")]
        if not todo or g1 - g0 < 1.0:
            continue
        weak = []
        for ui in todo:
            ws = windows(st.hits(us[ui]["grams"], g0, g1, moved), len(us[ui]["grams"]), _span(us[ui]))
            weak += [(ui, w) for w in ws if w["score"] >= WEAK and w["hits"] >= MIN_HITS_WEAK]
        for ui, w in _chain(weak):
            take(ui, w, "weak")
    return res
