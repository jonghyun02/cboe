# -*- coding: utf-8 -*-
"""Small shared helpers: JSON I/O, logging, GPU lock, ffmpeg, time formatting."""
from __future__ import annotations

import contextlib
import json
import logging
import os
import subprocess
import time
from pathlib import Path

from filelock import FileLock

ROOT = Path(__file__).resolve().parents[1]          # C:\projects\barrier_free\cboe
GPU_LOCK = ROOT / ".gpu.lock"
# console programs started from the windowless GUI would each pop up a black window
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

log = logging.getLogger("cboe")
_progress_last: dict[str, float] = {}


def progress(stage: str, done: int, total: int, note: str = "") -> None:
    """One parseable progress line for the GUI (``PROGRESS <stage> <done>/<total> <note>``),
    at most twice a second per stage except for the last step."""
    now = time.time()
    if done < total and now - _progress_last.get(stage, 0.0) < 0.5:
        return
    _progress_last[stage] = now
    log.info("PROGRESS %s %d/%d %s", stage, done, total, note)


def setup_logging(level: int = logging.INFO) -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                            datefmt="%H:%M:%S")


def read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, obj, indent: int | None = 1) -> Path:
    """Atomic UTF-8 JSON write (unique tmp file + replace). On Windows a reader holding the
    file open makes ``os.replace`` fail for a moment, so it is retried briefly."""
    import threading
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=indent), encoding="utf-8")
    for attempt in range(20):
        try:
            os.replace(tmp, path)
            return path
        except PermissionError:
            if attempt == 19:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.05)
    return path


@contextlib.contextmanager
def gpu_lock(timeout: float = -1):
    """Serialize GPU-heavy work across processes (one model on the GPU at a time)."""
    with FileLock(str(GPU_LOCK), timeout=timeout):
        yield


def device() -> str:
    """Torch device for the model stages: ``CBOE_DEVICE`` if set, else the first of
    cuda (NVIDIA), xpu (Intel Arc / Core Ultra GPU, torch ``+xpu`` build), cpu."""
    env = os.environ.get("CBOE_DEVICE")
    if env:
        return env
    import torch
    if torch.cuda.is_available():
        return "cuda"
    if hasattr(torch, "xpu") and torch.xpu.is_available():
        return "xpu"
    return "cpu"


def device_name() -> str | None:
    """Human-readable name of the accelerator ``device()`` picks (None on cpu)."""
    import torch
    dev = device()
    try:
        if dev.startswith("cuda"):
            return torch.cuda.get_device_name(0)
        if dev.startswith("xpu"):
            return torch.xpu.get_device_name(0)
    except Exception:
        pass
    return None


def free_gpu() -> None:
    try:
        import gc

        import torch
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        if hasattr(torch, "xpu") and torch.xpu.is_available():
            torch.xpu.empty_cache()
    except Exception:
        pass


@contextlib.contextmanager
def timed(what: str):
    t = time.time()
    log.info("%s ...", what)
    yield
    log.info("%s done in %.1fs", what, time.time() - t)


def save_npz(path, **arrays) -> None:
    """np.savez_compressed through a temp file: a cancelled run never leaves half a file
    that the next run would take as finished."""
    import numpy as np
    path = Path(path)
    tmp = path.with_name(f"{path.stem}.{os.getpid()}.tmp.npz")
    np.savez_compressed(tmp, **arrays)
    os.replace(tmp, path)


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    kw.setdefault("check", True)
    kw.setdefault("creationflags", NO_WINDOW)
    return subprocess.run(cmd, **kw)


def ffprobe(path) -> dict:
    """Duration, fps, size and audio presence of a media file."""
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format",
                          str(path)], capture_output=True, text=True, encoding="utf-8", check=True,
                         creationflags=NO_WINDOW).stdout
    info = json.loads(out)
    v = next((s for s in info["streams"] if s.get("codec_type") == "video"), None)
    a = next((s for s in info["streams"] if s.get("codec_type") == "audio"), None)
    fps = 0.0
    if v:
        num, den = (v.get("avg_frame_rate") or v.get("r_frame_rate") or "0/1").split("/")
        fps = float(num) / float(den) if float(den) else 0.0
    return {"duration": float(info["format"].get("duration", 0.0)), "fps": fps,
            "width": int(v["width"]) if v else 0, "height": int(v["height"]) if v else 0,
            "has_audio": a is not None}


def fmt_time(t: float) -> str:
    """mm:ss.s as on the review screen (03:48.7)."""
    t = max(0.0, float(t))
    m, s = divmod(t, 60)
    return f"{int(m):02d}:{s:04.1f}"


def snap_frame(t: float, fps: float, mode: str = "round") -> float:
    """Snap a time to the video frame grid."""
    import math
    if not fps:
        return t
    f = t * fps
    f = {"round": round, "floor": math.floor, "ceil": math.ceil}[mode](f)
    return f / fps
