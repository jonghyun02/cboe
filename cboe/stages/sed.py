# -*- coding: utf-8 -*-
"""소리 검출 (5.3 / 5.4): PretrainedSED frame scores on the effects, mix and music tracks.

PretrainedSED (Schmid et al., ICASSP 2025), BEATs student trained on AudioSet-strong,
447 classes, 40 ms frames (``BEATs_strong_1.pt``). Long audio is cut into 10 s windows
with a 5 s hop and blended with a triangular weight. The scores are per-class
sigmoids, not calibrated probabilities ("점수").

Also writes env.npz: 100 Hz short-time level (dB) of the effects / music / speech tracks
for sound onsets (자막 시작 = 소리 첫 프레임), unknown sounds and music crescendos.
"""
from __future__ import annotations

import sys

import numpy as np
import soundfile as sf

from .. import paths
from ..util import device, free_gpu, gpu_lock, timed

SR = 16000
FPS = 25
SEG_S, HOP_S = 10, 5
SEG_F, HOP_F = SEG_S * FPS, HOP_S * FPS
TRACKS = {"effects": "effects_16k", "mix": "mix_16k", "music": "music_16k"}
ENV_HOP = 0.01
ENV_WIN = 0.04


def _vendor():
    p = str(paths.PSED_CODE)
    if p not in sys.path:
        sys.path.insert(0, p)


def class_names() -> list[str]:
    _vendor()
    from data_util.audioset_classes import as_strong_train_classes
    return list(as_strong_train_classes)


def build_model():
    import torch
    _vendor()
    from models.beats.BEATs_wrapper import BEATsWrapper
    from models.prediction_wrapper import PredictionsWrapper
    model = PredictionsWrapper(BEATsWrapper(), checkpoint=None)
    sd = torch.load(paths.PSED_CKPT, map_location="cpu", weights_only=True)
    sd = {("model.beats." + k[len("model.model."):] if k.startswith("model.model") else k): v for k, v in sd.items()}
    missing, unexpected = model.load_state_dict(sd, strict=False)
    missing = [k for k in missing if "mel_transform" not in k]
    assert not missing and not unexpected, (missing[:5], unexpected[:5])
    return model.eval()


def _tri() -> np.ndarray:
    w = 1.0 - np.abs(np.arange(SEG_F) - (SEG_F - 1) / 2) / (SEG_F / 2)
    return np.maximum(w, 1e-3).astype(np.float32)


def infer(model, x: np.ndarray, dev: str | None = None, batch: int = 16) -> np.ndarray:
    """Frame scores [n_frames, 447] for mono 16 kHz audio."""
    import torch
    dev = dev or device()
    n_frames = int(np.ceil(len(x) / SR * FPS))
    starts = list(range(0, max(n_frames - SEG_F, 0) + HOP_F, HOP_F))
    seg = SEG_S * SR
    w = _tri()
    acc = None
    norm = np.zeros(starts[-1] + SEG_F, dtype=np.float32)
    xt = torch.from_numpy(np.ascontiguousarray(x, dtype=np.float32))
    for b in range(0, len(starts), batch):
        sts = starts[b:b + batch]
        chunks = []
        for f0 in sts:
            c = xt[f0 * SR // FPS:f0 * SR // FPS + seg]
            if len(c) < seg:
                c = torch.nn.functional.pad(c, (0, seg - len(c)))
            chunks.append(c)
        with torch.no_grad():
            mel = model.mel_forward(torch.stack(chunks).to(dev))
            y, _ = model(mel)
            pr = torch.sigmoid(y.float()).transpose(1, 2).cpu().numpy()
        if acc is None:
            acc = np.zeros((starts[-1] + SEG_F, pr.shape[2]), dtype=np.float32)
        for f0, pi in zip(sts, pr):
            acc[f0:f0 + SEG_F] += pi * w[:, None]
            norm[f0:f0 + SEG_F] += w
    return (acc / norm[:, None])[:n_frames]


def level_db(x: np.ndarray, sr: int = SR) -> np.ndarray:
    """Short-time RMS level (dB), 10 ms hop, 40 ms window, frame k centred at k * 10 ms."""
    hop, win = int(ENV_HOP * sr), int(ENV_WIN * sr)
    n = int(np.ceil(len(x) / hop))
    xp = np.pad(x, (win // 2, win))
    sq = np.concatenate([[0.0], np.cumsum(xp.astype(np.float64) ** 2)])
    idx = np.arange(n) * hop
    e = (sq[idx + win] - sq[idx]) / win
    return (10 * np.log10(e + 1e-10)).astype(np.float32)


def load(p, track: str) -> tuple[np.ndarray, list[str], float]:
    """(scores [n, 447], class names, t0) for 'effects' | 'mix' | 'music'."""
    d = np.load(p.path(f"sed_{track}.npz"), allow_pickle=False)
    return d["probs"].astype(np.float32), [str(c) for c in d["classes"]], float(d["t0"])


def load_env(p) -> dict:
    d = np.load(p.path("env.npz"))
    return {k: d[k] for k in d.files}


def run(p, cfg: dict) -> dict:
    t0 = float(p.meta.get("t0", 0.0))
    todo = [k for k in TRACKS if not p.path(f"sed_{k}.npz").exists()]
    if not todo and p.path("env.npz").exists():
        return {"skipped": True}                       # nothing to do: run_log keeps the run that did it
    out = {}
    if todo:
        names = class_names()
        dev = device()
        with gpu_lock(), timed(f"PretrainedSED on {', '.join(todo)} ({dev})"):
            model = build_model().to(dev)
            try:
                from ..util import progress, save_npz
                for ki, k in enumerate(todo):
                    progress("sed", ki, len(todo), k)
                    x, sr = sf.read(str(p.path("audio") / f"{TRACKS[k]}.wav"), dtype="float32", always_2d=True)
                    assert sr == SR
                    probs = infer(model, x.mean(axis=1), dev)
                    save_npz(p.path(f"sed_{k}.npz"), probs=probs.astype(np.float16), fps=FPS,
                             classes=np.array(names), t0=t0)
                    out[k] = int(len(probs))
            finally:
                del model
                free_gpu()
    if not p.path("env.npz").exists():
        from ..util import save_npz
        env = {"hop": np.float32(ENV_HOP), "t0": np.float32(t0)}
        for stem in ("effects", "music", "speech"):
            x, sr = sf.read(str(p.path("audio") / f"{stem}_16k.wav"), dtype="float32", always_2d=True)
            env[stem] = level_db(x.mean(axis=1))
        save_npz(p.path("env.npz"), **env)
    return {"frames": out}
