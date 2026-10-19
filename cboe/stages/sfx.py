# -*- coding: utf-8 -*-
"""효과음 후보 검출 (5.3): ① 시각 ② 확실한 만큼 구체적인 종류 ③ 목록에 없는 소리.

Onset-driven on the effects track (Bandit v2) — captions are about when a sound starts:
1. Onsets from two cues, merged within 0.3 s:
   - SED: rising edges / prominent peaks of the strongest effect score (447 classes
     restricted to effect categories, plus AudioSet's 'Sound effect' class, which in
     dramas fires on produced sound effects whose type the model cannot name);
   - level: the 10 ms effects-track level jumps >= LEVEL_JUMP dB above the median of
     the previous second.
2. 종류 on the window after the onset: peak score per class; from the best class we go
   down to the most specific child still >= TAU, or — if it is below TAU — up the
   AudioSet hierarchy (higher-scoring parent first) until a class reaches TAU_PARENT
   ('최소 자동차 소리까지'). 경쟁 종류 = unrelated classes close to the best score.
3. 시각: first frame of the sound on the level curve (floor + 25 % of the rise).
4. 목록 밖 소리: a clear onset (big level jump or a 'Sound effect' score) without a
   class above TAU_PARENT becomes '알 수 없는 소리' (재확인 에이전트로).
5. Generic catch-all classes (Mechanisms, Surface contact …) never trigger on their own.
Same-class onsets closer than the class merge gap (>= 1.5 s) are merged (walking is one
caption), and the sound end ``t1`` follows the class score / level down.
"""
from __future__ import annotations

import numpy as np
from scipy.ndimage import maximum_filter1d, median_filter

from .. import ontology, phrases
from ..util import log, write_json
from . import sed

FPS = sed.FPS
T_ON, T_OFF = 0.25, 0.12
PEAK_PROM = 0.12
TAU, TAU_PARENT = 0.30, 0.25
TAU_COMP, COMP_RATIO = 0.20, 0.7
LEVEL_JUMP, LEVEL_JUMP_UNKNOWN = 10.0, 15.0
SFX_TRIGGER, SFX_UNKNOWN = 0.25, 0.35
MERGE_ONSET_S = 0.3
WINDOW_S = 1.2
SAME_CLASS_GAP_S = 1.5
CATS = {"event", "ambient", "minor", "vocal"}
NO_TRIGGER = {"Mechanisms", "Surface contact", "Generic impact sounds", "Clicking", "Whir", "Rustle",
              "Deformable shell", "Tools", "Hands", "Human locomotion", "Human group actions", "Liquid", "Water",
              "Vehicle", "Animal", "Domestic animals, pets", "Wild animals"}
SOUND_EFFECT = "Sound effect"


def _runs(mask: np.ndarray) -> list[list[int]]:
    out, k, n = [], 0, len(mask)
    while k < n:
        if mask[k]:
            j = k
            while j < n and mask[j]:
                j += 1
            out.append([k, j])
            k = j
        else:
            k += 1
    return out


def resolve(scores: dict[str, float], onto) -> tuple[str, list[str], bool]:
    """Most specific confident class, the path up the hierarchy, and whether it is confident."""
    best = max(scores, key=scores.get)
    c = best
    if scores[c] >= TAU:
        while True:
            kids = [k for k in onto.children(c) if scores.get(k, 0.0) >= TAU]
            if not kids:
                break
            c = max(kids, key=lambda k: scores[k])
        path, q = [c], c
        while onto.parents(q):
            q = max(onto.parents(q), key=lambda r: scores.get(r, 0.0))
            path.append(q)
        return c, path, True
    if scores[c] >= TAU_PARENT and onto.children(c):     # a general class already is the top: '자동차 소리'
        path, q = [c], c
        while onto.parents(q):
            q = max(onto.parents(q), key=lambda r: scores.get(r, 0.0))
            path.append(q)
        return c, path, True
    path = [c]
    while True:
        ps = [r for r in onto.parents(c) if phrases.category(r) in CATS]
        if not ps:
            return best, path, False
        c = max(ps, key=lambda r: scores.get(r, 0.0))
        path.append(c)
        if scores.get(c, 0.0) >= TAU_PARENT:
            return c, path, True


def refine_onset(lev: np.ndarray, hop: float, t0: float, t_guess: float) -> float:
    """First frame of the sound on the 10 ms level curve (Netflix timing 1)."""
    k_lo = max(int((t_guess - 0.4 - t0) / hop), 0)
    k_hi = min(int((t_guess + 0.25 - t0) / hop), len(lev) - 1)
    if k_hi - k_lo < 3:
        return round(t_guess, 3)
    seg = lev[k_lo:k_hi + 1]
    kp = int(np.argmax(seg))
    peak = float(seg[kp])
    pre = lev[max(k_lo + kp - 40, 0):k_lo + kp + 1]
    floor = float(np.percentile(pre, 10)) if len(pre) else float(seg.min())
    if peak - floor < 6.0 or peak < -70.0:
        return round(t_guess, 3)
    thr = floor + 0.25 * (peak - floor)
    k = kp
    while k > 0 and seg[k - 1] >= thr:
        k -= 1
    return round(t0 + (k_lo + k) * hop, 3)


def sed_onsets(E: np.ndarray) -> list[int]:
    """Frames where a score curve rises: hysteresis starts and prominent peaks."""
    out = []
    for a, b in _runs(E >= T_OFF):
        if E[a:b].max() < T_ON:
            continue
        out.append(a + int(np.argmax(E[a:b] >= T_ON * 0.8)))
        # later peaks inside a long run (a second sound on top of the first)
        seg = E[a:b]
        mx = maximum_filter1d(seg, size=13, mode="nearest")
        for k in np.where((seg == mx) & (seg >= T_ON))[0]:
            lo = max(k - 25, 0)
            if seg[k] - seg[lo:k + 1].min() >= PEAK_PROM:
                out.append(a + int(np.argmin(seg[lo:k + 1])) + lo)
    return sorted(set(out))


def class_onsets(P: np.ndarray, idx: list[int]) -> list[int]:
    """Rising edges of each trigger class on its own (a second thunder clap inside a long
    rumble does not show on the max envelope)."""
    out = []
    for i in idx:
        x = P[:, i]
        if x.max() < T_ON:
            continue
        out.extend(sed_onsets(x))
    return out


def level_onsets(lev: np.ndarray, hop: float) -> tuple[list[int], np.ndarray]:
    """10 ms frames where the level jumps above the median of the previous second."""
    w = int(1.0 / hop)
    med = median_filter(lev, size=2 * w + 1, mode="nearest")
    prev = np.concatenate([np.full(w, med[0]), med[:-w]])          # median of [k - 2w, k] ≈ previous second
    jump = lev - prev
    out = []
    for a, b in _runs(jump >= LEVEL_JUMP):
        if b - a >= 3:
            out.append(a)
    return out, jump


def _scores(P: np.ndarray, a: int, b: int, names: list[str]) -> dict[str, float]:
    peak = P[a:b].max(axis=0)
    tab = phrases.table()
    return {c: float(peak[k]) for k, c in enumerate(names) if tab.get(c, {}).get("category") in CATS}


def _end(P: np.ndarray, cls_i: int, lev: np.ndarray, hop: float, a: int, onset_k: int, cat: str) -> int:
    """Frame where the sound stops: class score < T_OFF and level back near the floor."""
    limit = int((60.0 if cat == "ambient" else 8.0) * FPS)
    b = a + 1
    while b < min(len(P), a + limit) and P[b, cls_i] >= T_OFF:
        b += 1
    # level: at least until the level falls 10 dB below the peak of the first second
    k = onset_k
    k_peak = onset_k + int(np.argmax(lev[onset_k:onset_k + int(1.0 / hop)])) if onset_k < len(lev) else onset_k
    floor = lev[k_peak] - 10.0
    k = k_peak
    while k < min(len(lev), onset_k + int(8.0 / hop)) and lev[k] >= floor:
        k += 1
    return max(b, int(k * hop * FPS))


def run(p, cfg: dict) -> dict:
    if p.path("sfx.json").exists():
        return {"skipped": True}
    P, names, t0 = sed.load(p, "effects")
    Q, _, _ = sed.load(p, "mix")
    env = sed.load_env(p)
    lev, hop = env["effects"], float(env["hop"])
    onto = ontology.get()
    tab = phrases.table()
    trig = [k for k, c in enumerate(names) if tab.get(c, {}).get("category") in CATS and c not in NO_TRIGGER]
    sfx_i = names.index(SOUND_EFFECT)
    E = np.maximum(P[:, trig].max(axis=1), P[:, sfx_i])
    on_sed = sorted(set(sed_onsets(E)) | set(class_onsets(P, trig)))
    on_lev, jump = level_onsets(lev, hop)
    loud = max(float(np.percentile(lev, 50)), -55.0)
    loud_unknown = max(float(np.percentile(lev, 75)), -45.0)
    onsets = sorted([(f / FPS, "sed") for f in on_sed] +
                    [(k * hop, "level") for k in on_lev if lev[k:k + int(0.3 / hop)].max() >= loud])
    merged: list[list] = []
    for t, src in onsets:
        if merged and t - merged[-1][0] <= MERGE_ONSET_S:
            merged[-1][1].add(src)
        else:
            merged.append([t, {src}])
    cands = []
    for i, (t, srcs) in enumerate(merged):
        a = int(t * FPS)
        nxt = merged[i + 1][0] if i + 1 < len(merged) else t + WINDOW_S
        b = max(a + 3, min(int((t + WINDOW_S) * FPS), int(nxt * FPS) + 2, len(P)))
        sc = _scores(P, a, b, names)
        typed = {c: s for c, s in sc.items() if c not in NO_TRIGGER} or sc
        best, path, confident = resolve(sc, onto)
        lead = max(typed.values())
        k_on = int(t / hop)
        jmp = float(jump[k_on:k_on + int(0.3 / hop)].max(initial=0.0))
        sfx_score = float(P[a:b, sfx_i].max())
        peak_lev = float(lev[k_on:k_on + int(0.5 / hop)].max(initial=-120.0))
        unknown = not confident and (sfx_score >= SFX_UNKNOWN or
                                     (jmp >= LEVEL_JUMP_UNKNOWN and peak_lev >= loud_unknown))
        if not confident and not unknown:
            if not (lead >= 0.18 and (jmp >= LEVEL_JUMP or sfx_score >= SFX_TRIGGER)):
                continue                               # weak and unexplained: noise
        onset = refine_onset(lev, hop, t0, t0 + t)
        bi = names.index(best)
        end_f = _end(P, bi, lev, hop, a, int((onset - t0) / hop), tab[best]["category"])
        ranked = sorted(sc, key=sc.get, reverse=True)
        top = [{"class": c, "ko": phrases.ko(c), "score": round(sc[c], 3)} for c in ranked[:5]]
        competing = [{"class": c, "ko": phrases.ko(c), "score": round(sc[c], 3)} for c in ranked[:8]
                     if c not in NO_TRIGGER and not onto.related(c, best)
                     and sc[c] >= max(TAU_COMP, COMP_RATIO * sc[best])]
        k1 = int((t0 + end_f / FPS - t0) / hop)
        level = float(lev[int((onset - t0) / hop):max(k1, int((onset - t0) / hop) + 1)].max())
        ph = phrases.phrases(best)
        opts = [{"text": ph["long"], "short": ph["short"], "source": "detected", "note": "검출 종류"}]
        if ph["ono"]:
            opts.append({"text": ph["ono"], "short": ph["short"], "source": "detected", "note": "의성어 포함"})
        for up in path[1:3]:
            if phrases.category(up) in CATS and phrases.info(up)["desc"]:
                pu = phrases.phrases(up)
                opts.append({"text": pu["long"], "short": pu["short"], "source": "parent", "note": "상위 종류"})
        for c in competing[:2]:
            pc = phrases.phrases(c["class"])
            opts.append({"text": pc["long"], "short": pc["short"], "source": "competing", "note": "경쟁 종류"})
        if unknown:
            opts.insert(0, {"text": "[소리]", "short": "[소리]", "source": "unknown", "note": "알 수 없는 소리"})
        seen, uniq = set(), []
        for o in opts:
            if o["text"] not in seen:
                seen.add(o["text"])
                uniq.append(o)
        flags = (["unknown"] if unknown else []) + ([] if confident else ["label_uncertain"]) + \
                (["competing"] if competing else [])
        cands.append({
            "id": "", "kind": "sfx", "t0": round(t0 + t, 3), "t1": round(t0 + end_f / FPS, 3), "onset": onset,
            "category": "unknown" if unknown else tab[best]["category"],
            "label": {"class": best, "ko": phrases.ko(best), "score": round(sc[best], 3),
                      "mix_score": round(float(Q[a:b, bi].max()), 3), "path": path,
                      "path_ko": [phrases.ko(c) for c in path], "top": top, "competing": competing,
                      "unknown": unknown, "confident": confident, "sound_effect_score": round(sfx_score, 3)},
            "level_db": round(level, 1), "level_jump_db": round(jmp, 1), "onset_by": sorted(srcs),
            "phrases": uniq, "phrase": uniq[0]["text"], "short": uniq[0]["short"], "flags": flags})
    # merge repeats of the same class (one caption for a walking sequence) and onsets that
    # are just later parts of the sound before (same best class, or unknown right after)
    out: list[dict] = []
    for c in sorted(cands, key=lambda x: x["onset"]):
        prev = next((o for o in reversed(out[-4:]) if o["label"]["class"] == c["label"]["class"]
                     and o["label"]["unknown"] == c["label"]["unknown"]), None)
        gap = max(SAME_CLASS_GAP_S, phrases.info(c["label"]["class"])["merge_gap_s"])
        if out and c["label"]["unknown"] and c["onset"] - out[-1]["onset"] <= 0.6:
            prev = out[-1]
        if prev and c["onset"] - prev["t1"] <= gap and not (
                c["category"] == "event" and c["label"]["confident"] and c["level_jump_db"] >= LEVEL_JUMP_UNKNOWN
                and c["onset"] - prev["onset"] >= 3.0):    # a new clap / bang stays its own caption
            prev["t1"] = max(prev["t1"], c["t1"])
            prev["label"]["score"] = max(prev["label"]["score"], c["label"]["score"])
            prev["level_db"] = max(prev["level_db"], c["level_db"])
            prev["repeats"] = prev.get("repeats", 1) + 1
        else:
            out.append(c)
    for i, c in enumerate(out, 1):
        c["id"] = f"S{i:04d}"
    write_json(p.path("sfx.json"), {"candidates": out, "params": {
        "T_ON": T_ON, "T_OFF": T_OFF, "TAU": TAU, "TAU_PARENT": TAU_PARENT, "TAU_COMP": TAU_COMP,
        "COMP_RATIO": COMP_RATIO, "LEVEL_JUMP": LEVEL_JUMP, "SFX_TRIGGER": SFX_TRIGGER,
        "SAME_CLASS_GAP_S": SAME_CLASS_GAP_S}})
    n_unk = sum(1 for c in out if c["label"]["unknown"])
    log.info("sfx: %d candidates (%d unknown, %d uncertain) from %d onsets", len(out), n_unk,
             sum(1 for c in out if not c["label"]["confident"]), len(merged))
    return {"candidates": len(out), "unknown": n_unk, "onsets": len(merged)}
