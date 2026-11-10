# -*- coding: utf-8 -*-
"""A feature film in one go (감사 6-09): the separation streams the mix through a rolling
buffer, writes the tracks as it goes, and a stopped run goes on from its saved point.

A fake model stands in for Bandit v2 (it depends on where a sample sits in its chunk, so a
wrong chunk grid or a wrong overlap-add shows). The reference is the whole-array code of
round-f-base, copied here unchanged so it does not move with the module."""
from __future__ import annotations

import contextlib
import os
import tracemalloc
import types

import numpy as np
import pytest
import soundfile as sf
import soxr
import torch

from cboe.media import write_wav
from cboe.stages import separate
from cboe.util import read_json

SR = 48000
LSB = 1.0 / 32768          # one step of a 16-bit sample


# ------------------------------------------------------------------ the whole-array version
def _old_overlap_add(model, x: np.ndarray, dev: str = "cpu") -> np.ndarray:
    """cboe/stages/separate.py:overlap_add at round-f-base (unchanged but for the progress line)."""
    CHUNK_S, HOP_S, BATCH = 8.0, 2.0, 4
    C, H = int(round(CHUNK_S * SR)), int(round(HOP_S * SR))
    k = C // H
    n = len(x)
    front = C - H
    n_chunks = int(np.ceil((n + front) / H))
    total = (n_chunks - 1) * H + C
    pad_end = total - n - front
    xp = np.pad(x, (front, pad_end), mode="reflect" if n > max(front, pad_end) else "constant")
    win = torch.hann_window(C, periodic=True, device=dev) / (k / 2)
    out = np.zeros((3, total), dtype=np.float32)
    xt = torch.from_numpy(xp.astype(np.float32))
    starts = [i * H for i in range(n_chunks)]
    with torch.inference_mode():
        for b in range(0, len(starts), BATCH):
            sts = starts[b:b + BATCH]
            xb = torch.stack([xt[s:s + C] for s in sts]).to(dev)
            est = model({"mixture": {"audio": xb[:, None, :]}})["estimates"]
            y = torch.stack([est["speech"]["audio"][:, 0], est["music"]["audio"][:, 0], est["sfx"]["audio"][:, 0]], 1)
            y = (y * win).float().cpu().numpy()
            for s, yi in zip(sts, y):
                out[:, s:s + C] += yi
    return out[:, front:front + n]


def _old_run(model, adir) -> dict:
    """The writing of round-f-base's separate.run: whole arrays, soxr.resample, np.mean rms."""
    x, sr = sf.read(str(adir / "mix_48k.wav"), dtype="float32", always_2d=True)
    y = _old_overlap_add(model, x.mean(axis=1))
    rms = {}
    for stem, sig in zip(separate.STEMS, y):
        write_wav(adir / f"{stem}_48k.wav", sig, SR)
        write_wav(adir / f"{stem}_16k.wav", soxr.resample(sig, SR, 16000), 16000)
        rms[stem] = round(float(20 * np.log10(np.sqrt(np.mean(sig ** 2)) + 1e-12)), 1)
    return rms


# ------------------------------------------------------------------ a fake model and a film
class FakeBandit:
    """Deterministic stand-in: three different functions of the chunk, one of them by position."""

    def __init__(self, fail_at: int | None = None):
        self.calls, self.fail_at = 0, fail_at

    def to(self, dev):
        return self

    def __call__(self, batch):
        self.calls += 1
        if self.fail_at is not None and self.calls >= self.fail_at:
            raise RuntimeError("the run was stopped")
        x = batch["mixture"]["audio"]                       # [B, 1, C]
        ramp = torch.linspace(0.2, 1.0, x.shape[-1])
        return {"estimates": {"speech": {"audio": 0.6 * torch.tanh(2.0 * x)},
                              "music": {"audio": x * ramp},
                              "sfx": {"audio": 0.5 * torch.roll(x, 37, dims=-1) - 0.1 * x}}}


def _signal(n: int, channels: int = 1, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = np.arange(n) / SR
    base = 0.3 * np.sin(2 * np.pi * 220 * t) + 0.2 * np.sin(2 * np.pi * 3.1 * t) * np.sin(2 * np.pi * 1500 * t)
    x = np.stack([base + 0.05 * rng.standard_normal(n) for _ in range(channels)], 1)
    return x.astype(np.float32)


def _project(root, n: int, channels: int = 1, seed: int = 0):
    adir = root / "audio"
    adir.mkdir(parents=True, exist_ok=True)
    sf.write(str(adir / "mix_48k.wav"), _signal(n, channels, seed), SR, subtype="PCM_16")
    return types.SimpleNamespace(work=root, meta={}, path=lambda name: root / name)


@pytest.fixture
def fake(monkeypatch):
    """separate.run with the fake model on the cpu; ``fake.model`` is the next model handed out."""
    box = types.SimpleNamespace(model=FakeBandit(), made=[])

    def load():
        box.made.append(box.model)
        return box.model

    monkeypatch.setattr(separate, "_load_bandit", load)
    monkeypatch.setattr(separate, "device", lambda: "cpu")
    monkeypatch.setattr(separate, "gpu_lock", contextlib.nullcontext)
    monkeypatch.setattr(separate, "free_gpu", lambda: None)
    return box


def _tracks(adir) -> dict:
    return {f"{s}_{r}": sf.read(str(adir / f"{s}_{r}.wav"), dtype="int16")[0]
            for s in separate.STEMS for r in ("48k", "16k")}


def _wav_bytes(adir) -> dict:
    return {f.name: f.read_bytes() for f in separate._finals(adir)}


def _leftovers(adir) -> list[str]:
    return sorted(f.name for f in adir.iterdir() if ".part" in f.name)


# ------------------------------------------------------------------ A. the same samples
# shorter than a chunk (zero padding), the reflect / zero boundary (n == pad_end, n == pad_end + 1),
# one chunk, n + front a whole number of hops, and a long odd length with a short last batch
@pytest.mark.parametrize("n", [10, 48000, 336000, 336001, 384000, 480000, 48000 * 37 + 12345])
def test_streamed_overlap_add_gives_the_samples_of_the_whole_array_version(n):
    x = _signal(n).mean(axis=1)
    plan = separate.Plan(n)
    assert plan.reflect == (n > plan.pad_end) and plan.pad_end >= plan.front
    old = _old_overlap_add(FakeBandit(), x)
    new = separate.overlap_add(FakeBandit(), x, "cpu")
    assert new.shape == old.shape == (3, n)
    # same chunks, same batches, same window, same order of the sum: not just close, equal
    assert np.array_equal(new, old)


def test_padded_reader_is_np_pad_piece_by_piece():
    for n in (5, 336001, 400000):
        x = _signal(n).mean(axis=1)
        plan = separate.Plan(n)
        xp = np.pad(x, (plan.front, plan.pad_end), mode="reflect" if plan.reflect else "constant")
        src = separate._Padded(lambda a, b: x[a:b], plan)
        rng = np.random.default_rng(n)
        for _ in range(40):
            j0 = int(rng.integers(0, plan.total))
            j1 = int(rng.integers(j0, plan.total + 1))
            assert np.array_equal(src.get(j0, j1), xp[j0:j1]), (n, j0, j1)


@pytest.mark.parametrize("channels", [1, 2])
def test_run_writes_the_tracks_of_the_whole_array_version(tmp_path, fake, channels):
    n = 48000 * 21 + 777
    p = _project(tmp_path / "new", n, channels)
    res = separate.run(p, {})
    ref = tmp_path / "old" / "audio"
    ref.mkdir(parents=True)
    (ref / "mix_48k.wav").write_bytes((tmp_path / "new" / "audio" / "mix_48k.wav").read_bytes())
    rms_old = _old_run(FakeBandit(), ref)
    new, old = _tracks(tmp_path / "new" / "audio"), _tracks(ref)
    for s in separate.STEMS:
        assert np.array_equal(new[f"{s}_48k"], old[f"{s}_48k"]), s          # the same 16-bit samples
        assert len(new[f"{s}_16k"]) == len(old[f"{s}_16k"]) > 0
        # soxr's stream against soxr.resample of the whole track: measured equal; one step allowed
        assert np.abs(new[f"{s}_16k"].astype(int) - old[f"{s}_16k"].astype(int)).max() <= 1, s
        assert abs(res["rms_db"][s] - rms_old[s]) <= 0.1, s                  # summed in float64 on the way
    assert res["resumed_s"] == 0.0
    assert _leftovers(tmp_path / "new" / "audio") == []


def test_memory_does_not_grow_with_the_length_of_the_film(tmp_path, fake):
    """numpy's allocations (tracemalloc sees them) of a 40 s and a 160 s run: the same bound.
    The whole-array version needs about 6 x 4 bytes a sample, 184 MB for the 160 s one."""
    peaks = {}
    for secs in (40, 160):
        p = _project(tmp_path / str(secs), SR * secs + 123)
        fake.model = FakeBandit()
        tracemalloc.start()
        try:
            separate.run(p, {})
            peaks[secs] = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
    assert peaks[160] < peaks[40] * 1.1 + 2e6, peaks
    assert peaks[160] < 64e6, peaks


# ------------------------------------------------------------------ B. stopping and going on
N_STOP = SR * 50          # 28 chunks: 7 batches of 8 s


def _unbroken(tmp_path, fake, n=N_STOP, seed=0):
    p = _project(tmp_path / "unbroken", n, seed=seed)
    fake.model = FakeBandit()
    separate.run(p, {})
    return _wav_bytes(tmp_path / "unbroken" / "audio")


def test_a_stopped_run_goes_on_and_gives_the_result_of_an_unbroken_run(tmp_path, fake, monkeypatch):
    want = _unbroken(tmp_path, fake)
    assert fake.model.calls == 7
    monkeypatch.setattr(separate, "SAVE_EVERY_S", 16.0)
    p = _project(tmp_path / "stopped", N_STOP)
    adir = tmp_path / "stopped" / "audio"
    fake.model = FakeBandit(fail_at=5)                  # batches 0..3 done, saved after batch 2 (18 s)
    with pytest.raises(RuntimeError, match="stopped"):
        separate.run(p, {})
    # nothing under a finished name; the part files reach past the saved point (batch 3 unsaved)
    assert not any(f.exists() for f in separate._finals(adir))
    st = read_json(adir / separate.STATE)
    assert (st["batches"], st["samples"]) == (3, 18 * SR)
    assert all(separate._part(adir, s).stat().st_size == 26 * SR * 4 for s in separate.STEMS)
    fake.model = FakeBandit()
    res = separate.run(p, {})
    # batch 2 again (to refill the rolling buffer) and the four after it, not all seven
    assert fake.model.calls == 5 and res["resumed_s"] == 18.0
    assert _wav_bytes(adir) == want
    assert _leftovers(adir) == []
    assert separate.run(p, {}) == {"skipped": True}


def test_a_run_stopped_while_writing_the_wavs_does_not_separate_again(tmp_path, fake, monkeypatch):
    want = _unbroken(tmp_path, fake)
    p = _project(tmp_path / "stopped", N_STOP)
    adir = tmp_path / "stopped" / "audio"
    real, renamed = os.replace, []

    def replace(src, dst):
        if str(dst).endswith(".wav"):                   # not the record (write_json replaces too)
            if len(renamed) == 2:
                raise OSError("the run was stopped")
            renamed.append(dst)
        return real(src, dst)

    fake.model = FakeBandit()
    monkeypatch.setattr(os, "replace", replace)
    with pytest.raises(OSError):
        separate.run(p, {})
    monkeypatch.setattr(os, "replace", real)
    # two finished names made, four tracks still .part.wav: the stage is not taken as done
    assert sum(f.exists() for f in separate._finals(adir)) == 2
    assert len([f for f in _leftovers(adir) if f.endswith(".part.wav")]) == 4
    fake.model = FakeBandit(fail_at=1)                  # the model must not be needed again
    res = separate.run(p, {})
    assert res["resumed_s"] == 50.0 and fake.model.calls == 0
    assert _wav_bytes(adir) == want and _leftovers(adir) == []


def _stop_after_two_batches(p, fake, monkeypatch):
    monkeypatch.setattr(separate, "SAVE_EVERY_S", 0.0)  # a saved point after every batch
    fake.model = FakeBandit(fail_at=3)
    with pytest.raises(RuntimeError):
        separate.run(p, {})


def test_part_files_shorter_than_the_record_start_over(tmp_path, fake, monkeypatch):
    want = _unbroken(tmp_path, fake)
    p = _project(tmp_path / "stopped", N_STOP)
    adir = tmp_path / "stopped" / "audio"
    _stop_after_two_batches(p, fake, monkeypatch)
    assert read_json(adir / separate.STATE)["samples"] == 10 * SR
    with open(separate._part(adir, "music"), "r+b") as fh:   # the disk lost the end of one file
        fh.truncate(5 * SR * 4)
    fake.model = FakeBandit()
    res = separate.run(p, {})
    assert fake.model.calls == 7 and res["resumed_s"] == 0.0
    assert _wav_bytes(adir) == want


def test_a_changed_mix_starts_over(tmp_path, fake, monkeypatch):
    p = _project(tmp_path / "stopped", N_STOP)
    adir = tmp_path / "stopped" / "audio"
    _stop_after_two_batches(p, fake, monkeypatch)
    # the same length, other samples: the size is the same, the modification time is not
    sf.write(str(adir / "mix_48k.wav"), _signal(N_STOP, seed=5), SR, subtype="PCM_16")
    st = os.stat(adir / "mix_48k.wav")
    os.utime(adir / "mix_48k.wav", ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    fake.model = FakeBandit()
    res = separate.run(p, {})
    assert fake.model.calls == 7 and res["resumed_s"] == 0.0
    assert _wav_bytes(adir) == _unbroken(tmp_path, fake, seed=5)


def test_a_record_of_other_code_or_chunking_starts_over(tmp_path, fake, monkeypatch):
    p = _project(tmp_path / "stopped", N_STOP)
    adir = tmp_path / "stopped" / "audio"
    _stop_after_two_batches(p, fake, monkeypatch)
    monkeypatch.setattr(separate, "PART_FORMAT", separate.PART_FORMAT + 1)
    fake.model = FakeBandit()
    assert separate.run(p, {})["resumed_s"] == 0.0 and fake.model.calls == 7


def test_finished_tracks_with_part_files_left_are_skipped_and_cleaned(tmp_path, fake):
    _unbroken(tmp_path, fake)
    adir = tmp_path / "unbroken" / "audio"
    for s in separate.STEMS:
        separate._part(adir, s).write_bytes(b"\0" * 64)
    (adir / separate.STATE).write_text("{}", encoding="utf-8")
    p = types.SimpleNamespace(work=adir.parent, meta={}, path=lambda name: adir.parent / name)
    assert separate.run(p, {}) == {"skipped": True}
    assert _leftovers(adir) == []
