# -*- coding: utf-8 -*-
"""음원 분리 (5.2): Bandit v2 multilingual checkpoint -> dialogue / music / effects.

Bandit v2 (Watcharasupat, Wu, Orife 2024), ``checkpoint-multi.ckpt`` trained on DnR v3
(Zenodo 12701995, CC BY-SA 4.0; code Apache-2.0). 48 kHz mono model; 8 s chunks
with a periodic Hann window overlap-added at a 2 s hop (same as the earlier demo).
Writes audio/{speech,music,effects}_48k.wav and 16 kHz copies.

A feature film in one go (감사 6-09). The whole-array version held the mix, a padded copy, a
tensor copy and the three outputs at once (about 9.5 GB for 137 min). Now the mix is read one
batch of chunks at a time (``soundfile`` seek + read) and the overlap-add sums into a rolling
buffer one chunk long: after chunk i every sample before chunk i + 1 starts is finished, so one
hop of the three tracks is written out per chunk. Same chunks, same batches, same window and the
same order of the sum as before, so the samples are the ones the whole-array version gives.
Memory does not grow with the length of the film.

Stopping and going on. Finished samples go to ``audio/<stem>_48k.f32.part`` (raw float32, not a
wav) and ``audio/separate.part.json`` records how many batches and samples are on disk (written
after the part files are flushed). A stopped run goes on from the record: the batch before it
is computed again only to refill the rolling buffer (its samples are already on disk), so the
result is the one an unbroken run gives. The record names the mix file (size, modification
time), the checkpoint, the device and the chunking; when any of them differs the run starts
over. Only when every chunk is done are the six wav files written, under ``.part.wav`` names
(the 16 kHz copy through soxr's stream resampler, the rms summed on the way), and then renamed
to their own names; the part files and the record are removed last.
"""
from __future__ import annotations

import os
import sys
import types

import numpy as np
import soxr

from .. import paths
from ..util import device, free_gpu, gpu_lock, log, progress, read_json, timed, write_json

STEMS = ("speech", "music", "effects")
SR = 48000
SR_16K = 16000
CHUNK_S, HOP_S, BATCH = 8.0, 2.0, 4
# a saved point every minute of separated audio: a stopped run does at most this much (plus one
# batch) again. A save is three flushes to disk and a small json (재지 않은 값).
SAVE_EVERY_S = 60.0
# samples per read and write in the final pass: 30 s at 48 kHz float32 is 5.8 MB (재지 않은 값)
BLOCK_S = 30.0
STATE = "separate.part.json"
# raised when the samples a chunk gives change (window, padding, the order of the sum), so a part
# written by older code is not continued
PART_FORMAT = 1


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


class Plan:
    """The chunk grid for n samples: the padded signal xp = pad(x, (front, pad_end)) is cut into
    n_chunks chunks of C samples H apart; output sample t is xp sample front + t."""

    def __init__(self, n: int):
        self.C, self.H = int(round(CHUNK_S * SR)), int(round(HOP_S * SR))
        self.k = self.C // self.H                       # chunks over one sample
        self.n = int(n)
        self.front = self.C - self.H
        self.n_chunks = int(np.ceil((self.n + self.front) / self.H))
        self.total = (self.n_chunks - 1) * self.H + self.C
        self.pad_end = self.total - self.n - self.front
        self.reflect = self.n > max(self.front, self.pad_end)
        self.n_batches = -(-self.n_chunks // BATCH)

    def out_range(self, i: int) -> tuple[int, int, int]:
        """(lo, hi, offset): the output samples [lo, hi) finished once chunk i is added, and
        where they start in that chunk's first hop."""
        lo = i * self.H - self.front
        return max(lo, 0), min(lo + self.H, self.n), max(-lo, 0)

    def written_after(self, b: int) -> int:
        """Output samples finished once batch b is done."""
        last = min((b + 1) * BATCH, self.n_chunks) - 1
        return max(self.out_range(last)[1], 0)

    def restart_batch(self, done: int) -> int:
        """The batch to compute again to go on after ``done`` batches: the chunks still summing
        into the rolling buffer (the last k - 1) must be added again from zero."""
        return max(done * BATCH - (self.k - 1), 0) // BATCH


class _Padded:
    """``np.pad(x, (front, pad_end), 'reflect' | 'constant')`` of the mono mix, a piece at a time.
    ``read(a, b)`` gives x[a:b]; reflect does not repeat the edge sample (xp[j] = x[front - j]
    before the start, x[n - 2 - m] for the m-th sample after the end)."""

    def __init__(self, read, plan: Plan):
        self.read, self.p = read, plan

    def get(self, j0: int, j1: int) -> np.ndarray:
        p, out = self.p, np.zeros(j1 - j0, dtype=np.float32)
        F, N = p.front, p.n
        a, b = j0, min(j1, F)
        if a < b and p.reflect:
            out[a - j0:b - j0] = self.read(F - b + 1, F - a + 1)[::-1]
        a, b = max(j0, F), min(j1, F + N)
        if a < b:
            out[a - j0:b - j0] = self.read(a - F, b - F)
        a, b = max(j0, F + N), j1
        if a < b and p.reflect:
            m0, m1 = a - F - N, b - F - N
            out[a - j0:b - j0] = self.read(N - 1 - m1, N - 1 - m0)[::-1]
        return out


def _file_reader(f):
    """x[a:b] of an open soundfile.SoundFile: float32, channels averaged as before."""
    def read(a: int, b: int) -> np.ndarray:
        f.seek(a)
        y = f.read(b - a, dtype="float32", always_2d=True)
        if len(y) != b - a:
            raise RuntimeError(f"{f.name}: read {len(y)} of {b - a} samples at {a}")
        return y.mean(axis=1)
    return read


def _batches(model, plan: Plan, src: _Padded, dev: str, first: int, emit):
    """Run the model batch by batch from batch ``first``; ``emit(i, hop)`` gets the finished
    first hop [3, H] of the rolling buffer after chunk i is added (a view: use it at once).
    Yields each finished batch number."""
    import torch
    C, H = plan.C, plan.H
    win = torch.hann_window(C, periodic=True, device=dev) / (plan.k / 2)
    acc = np.zeros((3, C), dtype=np.float32)             # xp samples [i * H, i * H + C)
    with torch.inference_mode():
        for b in range(first, plan.n_batches):
            i0, i1 = b * BATCH, min((b + 1) * BATCH, plan.n_chunks)
            progress("separate", i0, plan.n_chunks)
            xt = torch.from_numpy(src.get(i0 * H, (i1 - 1) * H + C))
            xb = torch.stack([xt[(i - i0) * H:(i - i0) * H + C] for i in range(i0, i1)]).to(dev)
            est = model({"mixture": {"audio": xb[:, None, :]}})["estimates"]
            y = torch.stack([est["speech"]["audio"][:, 0], est["music"]["audio"][:, 0], est["sfx"]["audio"][:, 0]], 1)
            y = (y * win).float().cpu().numpy()
            for i, yi in zip(range(i0, i1), y):
                acc += yi
                emit(i, acc[:, :H])
                acc[:, :-H] = acc[:, H:]
                acc[:, -H:] = 0.0
            yield b


def overlap_add(model, x: np.ndarray, dev: str | None = None) -> np.ndarray:
    """Chunked inference of an in-memory clip; returns [3, n] in STEMS order (the stream of
    ``run`` with every hop kept: for short clips and checks, not for a film)."""
    dev = dev or device()
    x = np.asarray(x, dtype=np.float32)
    plan = Plan(len(x))
    out = np.zeros((3, plan.n), dtype=np.float32)

    def keep(i, hop):
        lo, hi, off = plan.out_range(i)
        if hi > lo:
            out[:, lo:hi] = hop[:, off:off + hi - lo]

    for _ in _batches(model, plan, _Padded(lambda a, b: x[a:b], plan), dev, 0, keep):
        pass
    return out


# ---------------------------------------------------------------- stopping and going on
def _part(adir, stem: str):
    return adir / f"{stem}_48k.f32.part"


def _wav_part(path):
    return path.with_name(path.stem + ".part.wav")       # the temp name of media.write_wav


def _finals(adir) -> list:
    return [adir / f"{s}_{r}.wav" for s in STEMS for r in ("48k", "16k")]


def _key(mix, f, dev: str) -> dict:
    """What the part files were made from: a different value means start over."""
    st = os.stat(mix)
    try:
        ck = os.stat(paths.BANDIT_CKPT)
        ckpt = [str(paths.BANDIT_CKPT), ck.st_size, ck.st_mtime_ns]
    except OSError:
        ckpt = [str(paths.BANDIT_CKPT), None, None]
    return {"format": PART_FORMAT, "mix": [st.st_size, st.st_mtime_ns], "frames": int(f.frames),
            "samplerate": int(f.samplerate), "channels": int(f.channels), "chunk": [CHUNK_S, HOP_S, BATCH],
            "ckpt": ckpt, "device": dev}


def _resume(adir, plan: Plan, key: dict) -> dict | None:
    """The saved point to go on from, or None (nothing saved, or it does not fit)."""
    try:
        st = read_json(adir / STATE)
        if not isinstance(st, dict) or st.get("key") != key:
            return None
        done, written = int(st["batches"]), int(st["samples"])
    except (ValueError, KeyError, TypeError):
        return None
    if not (0 < done <= plan.n_batches and written == plan.written_after(done - 1)):
        return None
    for s in STEMS:
        f = _part(adir, s)
        if not f.exists() or f.stat().st_size < written * 4:
            return None                                  # part of the data never reached the disk
    return {"batches": done, "samples": written}


def _clear(adir) -> None:
    for s in STEMS:
        _part(adir, s).unlink(missing_ok=True)
    for f in _finals(adir):
        _wav_part(f).unlink(missing_ok=True)
    (adir / STATE).unlink(missing_ok=True)


def _model_pass(adir, plan: Plan, mixf, key: dict, dev: str, saved: dict | None) -> None:
    """Separate the chunks not on disk yet into the part files, saving a point now and then."""
    done, written = (saved["batches"], saved["samples"]) if saved else (0, 0)
    files = {}
    try:
        for s in STEMS:
            if saved:
                fh = open(_part(adir, s), "r+b")
                fh.truncate(written * 4)                 # samples written after the last saved point
                fh.seek(written * 4)
            else:
                fh = open(_part(adir, s), "wb")
            files[s] = fh
        state = {"key": key, "batches": done, "samples": written}

        def emit(i, hop):
            lo, hi, off = plan.out_range(i)
            start = max(lo, state["samples"])            # below: on disk already (a batch done again)
            if hi <= start:
                return
            for s, row in zip(STEMS, hop):
                files[s].write(np.ascontiguousarray(row[off + start - lo:off + hi - lo], dtype="<f4").tobytes())

        def save():
            for fh in files.values():
                fh.flush()
                os.fsync(fh.fileno())
            write_json(adir / STATE, state)

        last_save = written
        first = plan.restart_batch(done) if saved else 0
        with gpu_lock(), timed(f"Bandit v2 separation ({plan.n / SR:.0f}s audio, {dev}"
                               + (f", going on from {written / SR:.0f}s" if saved else "") + ")"):
            model = _load_bandit().to(dev)
            try:
                for b in _batches(model, plan, _Padded(_file_reader(mixf), plan), dev, first, emit):
                    if b + 1 <= state["batches"]:
                        continue
                    state["batches"], state["samples"] = b + 1, plan.written_after(b)
                    if b + 1 == plan.n_batches or state["samples"] - last_save >= SAVE_EVERY_S * SR:
                        save()
                        last_save = state["samples"]
            finally:
                del model
                free_gpu()
    finally:
        for fh in files.values():
            fh.close()


def _write_tracks(adir, n: int) -> dict:
    """The six wav files from the part files (``.part.wav`` first, renamed when all are made);
    returns the rms (dB) of each 48 kHz track."""
    import soundfile as sf
    blk = max(int(BLOCK_S * SR), 1)
    rms, made = {}, []
    for stem in STEMS:
        p48, p16 = adir / f"{stem}_48k.wav", adir / f"{stem}_16k.wav"
        rs = soxr.ResampleStream(SR, SR_16K, 1, dtype="float32")       # soxr.resample's quality (HQ)
        sq = 0.0
        with open(_part(adir, stem), "rb") as fin, \
                sf.SoundFile(str(_wav_part(p48)), "w", SR, 1, "PCM_16") as w48, \
                sf.SoundFile(str(_wav_part(p16)), "w", SR_16K, 1, "PCM_16") as w16:
            a = 0
            while True:
                m = min(blk, n - a)
                x = np.frombuffer(fin.read(m * 4), dtype="<f4")
                if len(x) != m:
                    raise RuntimeError(f"{_part(adir, stem)}: {a + len(x)} of {n} samples")
                last = a + m >= n
                w48.write(np.clip(x, -1.0, 1.0))
                w16.write(np.clip(rs.resample_chunk(x, last=last), -1.0, 1.0))
                sq += float(np.square(x, dtype=np.float64).sum())
                del x                                    # before the next block is read
                a += m
                if last:
                    break
        rms[stem] = round(float(20 * np.log10(np.sqrt(sq / max(n, 1)) + 1e-12)), 1)
        made += [p48, p16]
    for f in made:
        os.replace(_wav_part(f), f)
    return rms


def run(p, cfg: dict) -> dict:
    import soundfile as sf
    adir = p.path("audio")
    if all(f.exists() for f in _finals(adir)):
        _clear(adir)                     # a run stopped after the renames leaves its part files
        return {"skipped": True}
    mix = adir / "mix_48k.wav"
    dev = device()
    with sf.SoundFile(str(mix)) as mixf:
        assert mixf.samplerate == SR
        plan = Plan(mixf.frames)
        key = _key(mix, mixf, dev)
        saved = _resume(adir, plan, key)
        if saved is None:
            _clear(adir)
        else:
            log.info("separation: going on from %.0f s of %.0f s (%d of %d batches)",
                     saved["samples"] / SR, plan.n / SR, saved["batches"], plan.n_batches)
        if saved is None or saved["batches"] < plan.n_batches:
            _model_pass(adir, plan, mixf, key, dev, saved)
    rms = _write_tracks(adir, plan.n)
    _clear(adir)
    log.info("separation rms dB: %s", rms)
    return {"rms_db": rms, "resumed_s": round(saved["samples"] / SR, 1) if saved else 0.0}
