# -*- coding: utf-8 -*-
"""음원 분리 (5.2): Bandit v2 multilingual checkpoint -> dialogue / music / effects.

Bandit v2 (Watcharasupat, Wu, Orife 2024), ``checkpoint-multi.ckpt`` trained on DnR v3
(Zenodo 12701995, CC BY-SA 4.0; code Apache-2.0). 48 kHz mono model; 8 s chunks
with a periodic Hann window overlap-added at a 2 s hop (same as the earlier demo).
Writes audio/{speech,music,effects}_48k.wav and 16 kHz copies.
"""
from __future__ import annotations

import sys
import types

import numpy as np
import soxr

from .. import paths
from ..media import write_wav
from ..util import free_gpu, gpu_lock, log, timed

STEMS = ("speech", "music", "effects")
SR = 48000
CHUNK_S, HOP_S, BATCH = 8.0, 2.0, 4


def _load_bandit():
    import torch
    root = str(paths.BANDIT_CODE)
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        import pytorch_lightning  # noqa: F401
    except ImportError:            # inference needs only LightningModule == nn.Module
        pl = types.ModuleType("pytorch_lightning")
        pl.LightningModule = torch.nn.Module
        sys.modules["pytorch_lightning"] = pl
    from src.models.bandit.bandit import Bandit
    model = Bandit(in_channels=1, stems=["speech", "music", "sfx"], band_type="musical", n_bands=64,
                   normalize_channel_independently=False, treat_channel_as_feature=True,
                   n_sqm_modules=8, emb_dim=128, rnn_dim=256, bidirectional=True, rnn_type="GRU",
                   mlp_dim=512, hidden_activation="Tanh", hidden_activation_kwargs=None,
                   complex_mask=True, use_freq_weights=True, n_fft=2048, win_length=2048,
                   hop_length=512, window_fn="hann_window", wkwargs=None, power=None, center=True,
                   normalized=True, pad_mode="reflect", onesided=True, fs=48000)
    ck = torch.load(paths.BANDIT_CKPT, map_location="cpu", weights_only=True)
    sd = {k[len("model."):]: v for k, v in ck["state_dict"].items() if k.startswith("model.")}
    missing, unexpected = model.load_state_dict(sd, strict=False)
    assert not unexpected, unexpected[:5]
    assert not [m for m in missing if "window" not in m], missing[:5]
    return model.eval()


def overlap_add(model, x: np.ndarray, device: str = "cuda") -> np.ndarray:
    """Chunked inference; returns [3, n] in STEMS order."""
    import torch
    C, H = int(round(CHUNK_S * SR)), int(round(HOP_S * SR))
    k = C // H
    n = len(x)
    front = C - H
    n_chunks = int(np.ceil((n + front) / H))
    total = (n_chunks - 1) * H + C
    pad_end = total - n - front
    xp = np.pad(x, (front, pad_end), mode="reflect" if n > max(front, pad_end) else "constant")
    win = torch.hann_window(C, periodic=True, device=device) / (k / 2)
    out = np.zeros((3, total), dtype=np.float32)
    xt = torch.from_numpy(xp.astype(np.float32))
    starts = [i * H for i in range(n_chunks)]
    from ..util import progress
    with torch.inference_mode():
        for b in range(0, len(starts), BATCH):
            progress("separate", b, len(starts))
            sts = starts[b:b + BATCH]
            xb = torch.stack([xt[s:s + C] for s in sts]).to(device)
            est = model({"mixture": {"audio": xb[:, None, :]}})["estimates"]
            y = torch.stack([est["speech"]["audio"][:, 0], est["music"]["audio"][:, 0], est["sfx"]["audio"][:, 0]], 1)
            y = (y * win).float().cpu().numpy()
            for s, yi in zip(sts, y):
                out[:, s:s + C] += yi
    return out[:, front:front + n]


def run(p, cfg: dict) -> dict:
    import soundfile as sf
    adir = p.path("audio")
    if all((adir / f"{s}_48k.wav").exists() and (adir / f"{s}_16k.wav").exists() for s in STEMS):
        return {"skipped": True}
    x, sr = sf.read(str(adir / "mix_48k.wav"), dtype="float32", always_2d=True)
    x = x.mean(axis=1)
    assert sr == SR
    with gpu_lock(), timed(f"Bandit v2 separation ({len(x) / SR:.0f}s audio)"):
        model = _load_bandit().to("cuda")
        try:
            y = overlap_add(model, x)
        finally:
            del model
            free_gpu()
    rms = {}
    for stem, sig in zip(STEMS, y):
        write_wav(adir / f"{stem}_48k.wav", sig, SR)
        write_wav(adir / f"{stem}_16k.wav", soxr.resample(sig, SR, 16000), 16000)
        rms[stem] = round(float(20 * np.log10(np.sqrt(np.mean(sig ** 2)) + 1e-12)), 1)
    log.info("separation rms dB: %s", rms)
    return {"rms_db": rms}
