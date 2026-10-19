# -*- coding: utf-8 -*-
"""장면 나누기 (5.5): TransNetV2 shot changes.

The official PyTorch port with the official weights; inference as the official
``predict_frames()``: 48x27 RGB frames, 100-frame windows at stride 50, keep the middle
50 predictions, transition frames where the single-frame head > 0.5.
Output cuts.json {"fps", "cuts": [t], "shots": [[s, e]]} in absolute seconds.
"""
from __future__ import annotations

import math
import sys

import numpy as np

from .. import paths
from ..media import Media
from ..util import free_gpu, gpu_lock, timed, write_json

THRESHOLD = 0.5


def _load(device: str):
    import torch
    sys.path.insert(0, str(paths.TNV2_CODE))
    try:
        from transnetv2_pytorch import TransNetV2
    finally:
        sys.path.remove(str(paths.TNV2_CODE))
    model = TransNetV2()
    model.load_state_dict(torch.load(paths.TNV2_WEIGHTS, map_location="cpu"))
    return model.eval().to(device)


def predict(model, frames: np.ndarray, device: str, batch: int = 8) -> np.ndarray:
    import torch
    n = len(frames)
    pad_end = 25 + 50 - (n % 50 if n % 50 != 0 else 50)
    padded = np.concatenate([np.repeat(frames[:1], 25, 0), frames, np.repeat(frames[-1:], pad_end, 0)])
    starts = list(range(0, len(padded) - 100 + 1, 50))
    single = []
    with torch.no_grad():
        for b in range(0, len(starts), batch):
            x = torch.from_numpy(np.stack([padded[s:s + 100] for s in starts[b:b + batch]])).to(device)
            one_hot, _ = model(x)
            single.append(torch.sigmoid(one_hot)[:, 25:75, 0].reshape(-1).cpu().numpy())
    return np.concatenate(single)[:n]


def to_shots(pred: np.ndarray, threshold: float = THRESHOLD) -> np.ndarray:
    p = (pred > threshold).astype(np.uint8)
    shots, t, t_prev, start, i = [], 0, 0, 0, 0
    for i, t in enumerate(p):
        if t_prev == 1 and t == 0:
            start = i
        if t_prev == 0 and t == 1 and i != 0:
            shots.append([start, i])
        t_prev = t
    if t == 0:
        shots.append([start, i])
    if not shots:
        return np.array([[0, len(p) - 1]], dtype=np.int32)
    return np.array(shots, dtype=np.int32)


def run(p, cfg: dict) -> dict:
    if p.path("cuts.json").exists():
        return {"skipped": True}
    m = Media(p.meta, p.work)
    with timed("TransNetV2 decode"):
        # decode at the native frame grid: frame i of the clip is shown at t0 + i / fps
        frames = m.frames(m.t0, m.t1, 48, 27)
    with gpu_lock(), timed(f"TransNetV2 ({len(frames)} frames)"):
        model = _load("cuda")
        try:
            pred = predict(model, frames, "cuda")
        finally:
            del model
            free_gpu()
    shots = to_shots(pred)
    fps = m.fps
    # ffmpeg's input seek returns frames whose pts >= t0 - 1 ms
    base = math.ceil((m.t0 - 0.001) * fps) / fps if m.t0 > 0 else 0.0
    # a cut sits between shot k and k+1: start of the next shot (middle of a gradual transition)
    cuts = [round(base + ((shots[k][1] + 1) + shots[k + 1][0]) / 2.0 / fps, 3) for k in range(len(shots) - 1)]
    shot_t = [[round(base + s / fps, 3), round(base + (e + 1) / fps, 3)] for s, e in shots]
    write_json(p.path("cuts.json"), {"fps": fps, "cuts": cuts, "shots": shot_t, "model": "TransNetV2",
                                     "threshold": THRESHOLD})
    return {"cuts": len(cuts)}
