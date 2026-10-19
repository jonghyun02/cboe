# -*- coding: utf-8 -*-
"""음악 검출 (5.4): ① 구간 ② 분위기 ③ 악기·장르.

① PretrainedSED 'Music' on the music track (40 ms) with hysteresis; segments shorter
  than 2 s are dropped and gaps under 2.5 s closed.
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
Candidates: music start and mood changes only (넷플릭스 II.7: 음악은 시작과 분위기가 바뀔 때).
"""
from __future__ import annotations

import numpy as np
import soundfile as sf

from .. import paths, phrases
from ..util import free_gpu, gpu_lock, log, timed, write_json
from . import sed

T_ON, T_OFF = 0.30, 0.15
MIN_SEG_S, GAP_S = 2.0, 2.5
WIN_S, HOP_S = 10.24, 2.0
MOOD_KEYS = list(phrases.MOODS)
SLOPE_DB_S = 1.5
CUE_DB, CUE_SEP_S = 9.0, 6.0


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
    model = ASTForAudioClassification.from_pretrained(paths.AST_MODEL).to("cuda").eval()
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
                logits = model(input_values=inp["input_values"].to("cuda")).logits
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
    segs = [[t0 + a, t0 + b] for a, b in _segments(m, sed.FPS)]
    x, sr = sf.read(str(p.path("audio") / "music_16k.wav"), dtype="float32", always_2d=True)
    x = x.mean(axis=1)
    env = sed.load_env(p)
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
        # start item: windows starting in the first 8 s
        first = sel[st[sel] < a + 8.0]
        events = [("start", a, first)]
        for tc in _cue_onsets(env["music"], float(env["hop"]), t0, a, b):
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
            slope = _slope(env["music"], float(env["hop"]), t0, t)
            change = "고조되는" if slope >= SLOPE_DB_S else ("잦아드는" if slope <= -SLOPE_DB_S else None)
            items.append({"kind": kind, "onset": round(t, 3), "segment": [round(a, 3), round(b, 3)],
                          "moods": mood_list, "mood_strength": round(float(raw.sum()), 4),
                          "instruments": inst_list, "slope_db_s": round(slope, 2),
                          "change": change, "music_score": round(float(m[int((t - t0) * sed.FPS):
                                                                           int((t - t0 + 2) * sed.FPS)].max(initial=0)), 3)})
    write_json(p.path("music.json"), {"segments": segs, "items": items,
                                      "params": {"T_ON": T_ON, "T_OFF": T_OFF, "WIN_S": WIN_S, "HOP_S": HOP_S}})
    log.info("music: %d segments, %d candidates", len(segs), len(items))
    return {"segments": len(segs), "candidates": len(items)}
