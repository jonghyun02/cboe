# -*- coding: utf-8 -*-
"""음악 검출 (5.4): ① 구간 ② 분위기 ③ 악기·장르 ④ 멈춤.

① PretrainedSED 'Music' on the music track (40 ms) with hysteresis; segments shorter
  than 2 s are dropped and gaps under 2.5 s closed. A segment where the music track is
  next to silent and the original mix does not say music either is separation residue
  and is not a music segment (``split_silent``). The start time is then moved onto the
  10 ms level curve of the music track (``refine_start``, the rule of sfx.refine_onset:
  floor + 25 % of the rise; 넷플릭스 타이밍 1: 자막 시작 = 소리 첫 프레임).
② AST (MIT/ast-finetuned-audioset-10-10-0.4593, 527 AudioSet classes, BSD-3) on the
  music track in 10.24 s windows every 2 s: the 7 mood classes (Happy, Funny, Sad,
  Tender, Exciting, Angry, Scary music) and instrument classes. AST's mood sigmoids are
  small in absolute terms (often < 0.05), so the mood reading is the share of each
  mood among the seven ("비율"), and ``strength`` keeps the absolute sum. A candidate's
  mood is the mean over the windows starting in its first 8 s.
  New candidates inside a long segment: a new music cue (music-track level >= CUE_DB
  above the previous 2 s) or a mood change (new top mood for >= 3 windows, clear
  margin; the change time is only known to a few seconds).
③ 변화: the music-track level slope over the first 4 s → '고조되는' / '잦아드는'.
④ 멈춤: where a segment ends with the level falling steeply within a fraction of a second
  (``stop_at``) → a 'stop' candidate, '[음악이 멈춘다]' (넷플릭스 II.9: 줄거리에 필요한 음악이
  갑자기 끝날 때). Whether that silence matters to the plot is the writer's call, so the
  recommendation is always 확인 필요.
Candidates: music start, a new cue, a mood change and an abrupt stop. When to caption
music is this tool's own rule — II.7 describes how to write the caption, not when.
"""
from __future__ import annotations

import numpy as np
import soundfile as sf

from .. import paths, phrases
from ..util import device, free_gpu, gpu_lock, log, timed, write_json
from . import sed

T_ON, T_OFF = 0.30, 0.15
MIN_SEG_S, GAP_S = 2.0, 2.5
WIN_S, HOP_S = 10.24, 2.0
MOOD_KEYS = list(phrases.MOODS)
SLOPE_DB_S = 1.5
CUE_DB, CUE_SEP_S = 9.0, 6.0
# The values below were read off the stored level curves of three runs (a 21-minute animation
# dub and two clips of about a minute), not off a labelled set: they are not validated on
# live-action films yet.
SILENT_DB = -60.0                            # 90th percentile of a segment's music-track level under this: no music
START_BEFORE_S, START_AFTER_S = 0.6, 0.6     # where the level rise is looked for around the SED start
START_RISE_DB = 6.0                          # less rise than this (as in sfx.refine_onset): the SED start is kept
START_NEAR_DB = 20.0                         # the music has started once the level is this close to the window's peak
STOP_SEARCH_S, STOP_WIN_S = 1.0, 0.3         # the fall is looked for within 1 s of the segment end, 0.3 s windows
STOP_DROP_DB = 20.0                          # level before - level after to call the end abrupt
STOP_HOLD_S = 1.0                            # ... and it has to stay that quiet for this long


def near_silent(lev: np.ndarray, hop: float, t0: float, a: float, b: float) -> bool:
    """True when the music track holds next to nothing in [a, b) (90th percentile of the level)."""
    seg = lev[max(int((a - t0) / hop), 0):max(int((b - t0) / hop), 0)]
    return len(seg) > 0 and float(np.percentile(seg, 90)) < SILENT_DB


def split_silent(segs: list[list[float]], lev: np.ndarray, hop: float, t0: float, mix: np.ndarray | None = None,
                 fps: float = sed.FPS) -> tuple[list[list[float]], list[list[float]]]:
    """(music segments, dropped segments). The 'Music' score of the separated music track also
    rises on what the separation left over (three of eleven segments of the 21-minute run had a
    median level of -91 dB, 감사 5-m4). Such a segment is dropped unless the 'Music' score of
    the original mix (``mix``, one value per SED frame) reaches T_ON there — then the music is
    real and only the stem lost it."""
    keep, drop = [], []
    for a, b in segs:
        quiet = near_silent(lev, hop, t0, a, b)
        if quiet and mix is not None:
            part = mix[max(int((a - t0) * fps), 0):max(int((b - t0) * fps), 0)]
            if len(part) and float(part.max()) >= T_ON:
                quiet = False
        (drop if quiet else keep).append([a, b])
    return keep, drop


def refine_start(lev: np.ndarray, hop: float, t0: float, t_guess: float) -> float:
    """First frame of the music on its 10 ms level curve (the SED 'Music' score starts some
    tenths of a second before the level does, 감사 5-m3). The threshold is that of
    sfx.refine_onset, floor + 25 % of the rise, but music is sustained and has rests, so the
    first frame that stays over it for 50 ms is taken instead of walking back from the peak.
    Above digital silence 25 % of the rise is still far under the music (a leftover at -78 dB
    before music at -50 dB), so the level also has to be within START_NEAR_DB of the window's
    peak. The guess is kept when the window holds no rise (music already playing), nothing
    audible, or the level only at its very start."""
    k_lo = max(int(round((t_guess - START_BEFORE_S - t0) / hop)), 0)
    k_hi = min(int(round((t_guess + START_AFTER_S - t0) / hop)), len(lev) - 1)
    if k_hi - k_lo < 5:
        return round(t_guess, 3)
    seg = lev[k_lo:k_hi + 1]
    peak = float(seg.max())
    floor = float(np.percentile(lev[max(k_lo - 40, 0):k_hi + 1], 10))
    if peak - floor < START_RISE_DB or peak < SILENT_DB:
        return round(t_guess, 3)
    over = seg >= max(floor + 0.25 * (peak - floor), peak - START_NEAR_DB)
    hold = max(int(round(0.05 / hop)), 1)
    if over[:hold].all():                       # already sounding at the window start: the rise is not in here
        return round(t_guess, 3)
    for k in range(len(seg) - hold + 1):
        if over[k:k + hold].all():
            return round(t0 + (k_lo + k) * hop, 3)
    return round(t_guess, 3)


def stop_at(lev: np.ndarray, hop: float, t0: float, b: float) -> tuple[float, float] | None:
    """(time, fall in dB) where the music breaks off near the segment end ``b``, or None.

    For every frame within STOP_SEARCH_S of ``b`` the median level of the STOP_WIN_S before it is
    compared with that of the STOP_WIN_S after it. The largest fall must reach STOP_DROP_DB and
    start from an audible level; a fade-out loses less than that between two 0.3 s windows.
    The time is the first frame under after + 25 % of the fall, and the track has to stay
    STOP_DROP_DB under the level before for STOP_HOLD_S after it (a rest before the next hit is
    not the music stopping)."""
    w = max(int(round(STOP_WIN_S / hop)), 2)
    k_lo = max(int(round((b - STOP_SEARCH_S - t0) / hop)), w)
    k_hi = min(int(round((b + STOP_SEARCH_S - t0) / hop)), len(lev) - w)
    best_k, best = -1, 0.0
    for k in range(k_lo, k_hi + 1):
        d = float(np.median(lev[k - w:k]) - np.median(lev[k:k + w]))
        if d > best:
            best_k, best = k, d
    if best_k < 0 or best < STOP_DROP_DB:
        return None
    before, after = float(np.median(lev[best_k - w:best_k])), float(np.median(lev[best_k:best_k + w]))
    if before < SILENT_DB:                      # nothing audible was playing
        return None
    thr = after + 0.25 * (before - after)
    k = best_k
    while k > best_k - w and lev[k - 1] < thr:
        k -= 1
    while k < best_k + w and lev[k] >= thr:
        k += 1
    n = int(round(STOP_HOLD_S / hop))
    rest = lev[k:k + n]
    if len(rest) < n // 2 or float(np.percentile(rest, 75)) > before - STOP_DROP_DB:
        return None
    return round(t0 + k * hop, 3), round(best, 1)


def _cue_onsets(lev: np.ndarray, hop: float, t0: float, a: float, b: float) -> list[float]:
    """Music-track level jumps (a new cue) inside [a + 3 s, b)."""
    from scipy.ndimage import median_filter
    k0, k1 = int((a + 3.0 - t0) / hop), min(int((b - t0) / hop), len(lev))
    if k1 - k0 < 50:
        return []
    w = int(2.0 / hop)
    smooth = median_filter(lev, size=w + 1, mode="nearest")
    prev = np.concatenate([np.full(w, smooth[0]), smooth[:-w]])
    jump = lev - prev
    out: list[float] = []
    level_mid = np.percentile(lev[k0:k1], 50)
    # armed after the jump stayed 3 dB under the threshold for 0.3 s; values between that and the
    # threshold do not disarm (a real rise passes through them). A rise already under way at the
    # search start is the segment's own start, so the search starts disarmed.
    need = int(0.3 / hop)
    armed, quiet = False, 0
    k = k0
    while k < k1:
        j = jump[k]
        if j < CUE_DB - 3.0:
            quiet += 1
            if quiet >= need:
                armed = True
        else:
            quiet = 0
        if armed and j >= CUE_DB and lev[k:k + int(1.0 / hop)].mean() >= level_mid:
            t = t0 + k * hop
            if not out or t - out[-1] >= CUE_SEP_S:
                out.append(t)
            armed, quiet = False, 0
        k += 1
    return out


def _segments(m: np.ndarray, fps: float) -> list[list[float]]:
    on = m >= T_OFF
    segs, k = [], 0
    while k < len(on):
        if on[k]:
            j = k
            while j < len(on) and on[j]:
                j += 1
            if m[k:j].max() >= T_ON:
                segs.append([k / fps, j / fps])
            k = j
        else:
            k += 1
    merged: list[list[float]] = []
    for a, b in segs:
        if merged and a - merged[-1][1] < GAP_S:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    return [s for s in merged if s[1] - s[0] >= MIN_SEG_S]


def _ast_windows(x: np.ndarray, sr: int, starts: list[float]) -> tuple[np.ndarray, list[str]]:
    import torch
    from transformers import ASTFeatureExtractor, ASTForAudioClassification
    fe = ASTFeatureExtractor.from_pretrained(paths.AST_MODEL)
    dev = device()
    model = ASTForAudioClassification.from_pretrained(paths.AST_MODEL).to(dev).eval()
    labels = [model.config.id2label[i] for i in range(model.config.num_labels)]
    n = int(WIN_S * sr)
    out = []
    from ..util import progress
    try:
        for b in range(0, len(starts), 16):
            progress("music", b, len(starts))
            chunk = []
            for s in starts[b:b + 16]:
                seg = x[int(s * sr):int(s * sr) + n]
                if len(seg) < n:
                    seg = np.pad(seg, (0, n - len(seg)))
                chunk.append(seg)
            inp = fe(chunk, sampling_rate=sr, return_tensors="pt")
            with torch.no_grad():
                logits = model(input_values=inp["input_values"].to(dev)).logits
            out.append(torch.sigmoid(logits.float()).cpu().numpy())
    finally:
        del model
        free_gpu()
    return (np.concatenate(out) if out else np.zeros((0, len(labels)), np.float32)), labels


def _slope(lev: np.ndarray, hop: float, t0: float, a: float, span: float = 4.0) -> float:
    k0, k1 = int((a - t0) / hop), int((a + span - t0) / hop)
    seg = lev[k0:k1]
    if len(seg) < 50:
        return 0.0
    # smooth with 0.5 s blocks, fit a line (dB per second)
    blk = int(0.5 / hop)
    y = np.array([seg[i:i + blk].mean() for i in range(0, len(seg) - blk + 1, blk)])
    tt = np.arange(len(y)) * 0.5
    return float(np.polyfit(tt, y, 1)[0]) if len(y) >= 3 else 0.0


def run(p, cfg: dict) -> dict:
    if p.path("music.json").exists():
        return {"skipped": True}
    M, names, t0 = sed.load(p, "music")
    Q, _, _ = sed.load(p, "mix")
    mi = names.index("Music")
    m = np.maximum(M[:, mi], 0.9 * Q[:, mi])
    env = sed.load_env(p)
    lev, hop = env["music"], float(env["hop"])
    segs, silent = split_silent([[t0 + a, t0 + b] for a, b in _segments(m, sed.FPS)], lev, hop, t0, Q[:, mi])
    t_end = t0 + len(m) / sed.FPS
    x, sr = sf.read(str(p.path("audio") / "music_16k.wav"), dtype="float32", always_2d=True)
    x = x.mean(axis=1)
    starts = []
    for a, b in segs:
        s = a
        while s < b:
            starts.append(s)
            s += HOP_S
    probs = np.zeros((0, 527), np.float32)
    labels: list[str] = []
    if starts:
        with gpu_lock(), timed(f"AST on {len(starts)} music windows"):
            probs, labels = _ast_windows(x, sr, [s - t0 for s in starts])
    mood_idx = [labels.index(k) for k in MOOD_KEYS] if labels else []
    inst_keys = [k for k in phrases.INSTRUMENTS if k in labels]
    inst_idx = [labels.index(k) for k in inst_keys]
    st = np.array(starts)
    items = []
    for a, b in segs:
        sel = np.where((st >= a - 1e-6) & (st < b))[0]
        if len(sel) == 0:
            continue
        moods = probs[sel][:, mood_idx]
        moods = moods / np.maximum(moods.sum(axis=1, keepdims=True), 1e-9)      # share among the 7
        # start item: windows starting in the first 8 s; its time is the first frame of the level rise
        first = sel[st[sel] < a + 8.0]
        a_on = refine_start(lev, hop, t0, a)
        seg = [round(min(a, a_on), 3), round(b, 3)]        # every item of the segment starts inside it
        events = [("start", a_on, first)]
        for tc in _cue_onsets(lev, hop, t0, a, b):
            events.append(("cue", tc, sel[(st[sel] >= tc - 1.0) & (st[sel] < tc + 8.0)]))
        # mood changes: top mood switches for >= 3 windows with a clear margin
        top = moods.argmax(axis=1)
        k = 0
        cur = top[0]
        while k < len(top):
            if top[k] != cur:
                j = k
                while j < len(top) and top[j] == top[k]:
                    j += 1
                if j - k >= 3 and moods[k:j, top[k]].mean() - moods[k:j, cur].mean() >= 0.15:
                    tch = float(st[sel][k]) + WIN_S / 2 - HOP_S
                    if all(abs(tch - e[1]) >= CUE_SEP_S for e in events):
                        events.append(("mood_change", tch, sel[k:j]))
                    cur = top[k]
                k = j
            else:
                k += 1
        events.sort(key=lambda e: e[1])
        for kind, t, win in events:
            raw = probs[win][:, mood_idx].mean(axis=0) if len(win) else np.zeros(len(mood_idx))
            mv = raw / max(float(raw.sum()), 1e-9)
            iv = probs[win][:, inst_idx].mean(axis=0) if len(win) else np.zeros(len(inst_idx))
            order = np.argsort(-mv)
            mood_list = [{"key": MOOD_KEYS[i], "ko": phrases.MOODS[MOOD_KEYS[i]], "score": round(float(mv[i]), 3),
                          "raw": round(float(raw[i]), 4)} for i in order]
            inst_list = [{"key": inst_keys[i], "ko": phrases.INSTRUMENTS[inst_keys[i]], "score": round(float(iv[i]), 3)}
                         for i in np.argsort(-iv)[:3]]
            slope = _slope(lev, hop, t0, t)
            change = "고조되는" if slope >= SLOPE_DB_S else ("잦아드는" if slope <= -SLOPE_DB_S else None)
            items.append({"kind": kind, "onset": round(t, 3), "segment": seg,
                          "moods": mood_list, "mood_strength": round(float(raw.sum()), 4),
                          "instruments": inst_list, "slope_db_s": round(slope, 2),
                          "change": change, "music_score": round(float(m[int((t - t0) * sed.FPS):
                                                                           int((t - t0 + 2) * sed.FPS)].max(initial=0)), 3)})
            if kind == "start":
                items[-1]["onset_sed"] = round(a, 3)       # where the 'Music' score started, before refine_start
        # ④ the music breaks off (a segment that runs to the end of the processed part did not stop)
        stop = stop_at(lev, hop, t0, b) if b < t_end - STOP_SEARCH_S else None
        if stop is not None:
            ts = stop[0]
            items.append({"kind": "stop", "onset": ts, "segment": seg, "moods": [], "mood_strength": 0.0,
                          "instruments": [], "slope_db_s": 0.0, "change": None, "drop_db": stop[1],
                          "music_score": round(float(m[max(int((ts - t0 - 2) * sed.FPS), 0):
                                                       max(int((ts - t0) * sed.FPS), 0)].max(initial=0)), 3)})
    items.sort(key=lambda it: it["onset"])
    write_json(p.path("music.json"), {"segments": segs, "silent_segments": silent, "items": items,
                                      "params": {"T_ON": T_ON, "T_OFF": T_OFF, "WIN_S": WIN_S, "HOP_S": HOP_S,
                                                 "SILENT_DB": SILENT_DB, "START_NEAR_DB": START_NEAR_DB,
                                                 "STOP_DROP_DB": STOP_DROP_DB, "STOP_WIN_S": STOP_WIN_S,
                                                 "STOP_HOLD_S": STOP_HOLD_S}})
    n_stop = sum(1 for it in items if it["kind"] == "stop")
    log.info("music: %d segments (%d near-silent ones dropped), %d candidates (%d of them a stop)",
             len(segs), len(silent), len(items), n_stop)
    return {"segments": len(segs), "silent_segments": len(silent), "candidates": len(items), "stops": n_stop}
