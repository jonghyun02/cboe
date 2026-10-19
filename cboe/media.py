# -*- coding: utf-8 -*-
"""Video/audio access limited to the project window [t0, t1).

Every reader refuses requests outside the window so a run on the development
episodes never decodes sealed parts of a test video.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import cv2
import numpy as np
import soundfile as sf

from .util import NO_WINDOW

FFMPEG = "ffmpeg"


class Media:
    def __init__(self, meta: dict, work: Path):
        self.video = str(meta["video"])
        self.t0 = float(meta.get("t0", 0.0))
        self.t1 = float(meta["t1"])
        self.fps = float(meta["fps"])
        self.width = int(meta.get("width", 0))
        self.height = int(meta.get("height", 0))
        self.work = Path(work)

    # ------------------------------------------------------------------ guards
    def clamp(self, a: float, b: float) -> tuple[float, float]:
        return max(a, self.t0), min(b, self.t1)

    def check(self, a: float, b: float) -> None:
        if not (self.t0 - 1e-6 <= a < b <= self.t1 + 1e-6):
            raise ValueError(f"window [{a:.3f}, {b:.3f}) leaves the project window [{self.t0}, {self.t1})")

    # ------------------------------------------------------------------- audio
    def audio_path(self, name: str) -> Path:
        return self.work / "audio" / f"{name}.wav"

    def load_audio(self, name: str, a: float | None = None, b: float | None = None) -> tuple[np.ndarray, int]:
        """Float32 mono samples of ``audio/<name>.wav`` between absolute times a..b."""
        path = self.audio_path(name)
        info = sf.info(str(path))
        sr = info.samplerate
        a = self.t0 if a is None else a
        b = self.t1 if b is None else b
        a, b = self.clamp(a, b)
        i0 = max(0, int(round((a - self.t0) * sr)))
        i1 = max(i0, int(round((b - self.t0) * sr)))
        x, _ = sf.read(str(path), start=i0, stop=i1, dtype="float32", always_2d=True)
        return x.mean(axis=1), sr

    # ------------------------------------------------------------------- video
    def frames(self, a: float, b: float, width: int, height: int, fps: float | None = None,
               pix_fmt: str = "rgb24") -> np.ndarray:
        """Decode [a, b) scaled to width x height -> uint8 (N, H, W, 3) via ffmpeg."""
        a, b = self.clamp(a, b)
        self.check(a, b)
        vf = f"scale={width}:{height}" if fps is None else f"fps={fps},scale={width}:{height}"
        s = max(a - 0.001, 0.0)
        cmd = [FFMPEG, "-v", "error", "-nostdin", "-ss", f"{s:.6f}", "-t", f"{b - a:.6f}", "-i", self.video,
               "-an", "-sn", "-vf", vf, "-fps_mode", "passthrough", "-pix_fmt", pix_fmt, "-f", "rawvideo", "pipe:"]
        raw = subprocess.run(cmd, capture_output=True, check=True, creationflags=NO_WINDOW).stdout
        frames = np.frombuffer(raw, np.uint8).reshape(-1, height, width, 3)
        if fps is not None:
            frames = frames[: int(round((b - a) * fps))]
        return frames

    def frame_index(self, t: float) -> int:
        return max(int(np.floor(t * self.fps + 1e-6)), 0)

    def frames_at(self, times, max_width: int | None = None) -> list[np.ndarray]:
        """BGR frames shown at each time (OpenCV, sorted sequential reads)."""
        times = [min(max(float(t), self.t0), self.t1 - 1.0 / self.fps) for t in times]
        idx = [self.frame_index(t) for t in times]
        out: list = [None] * len(idx)
        cap = cv2.VideoCapture(self.video)
        cur, last_i, last_f = -1, -1, None
        try:
            for k in np.argsort(idx, kind="stable"):
                target = idx[k]
                if target != last_i:
                    if cur < 0 or target < cur or target - cur > 60:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, target)
                        cur = target
                    while cur < target:
                        cap.grab()
                        cur += 1
                    ok, fr = cap.read()
                    if not ok:
                        raise RuntimeError(f"cannot read frame {target}")
                    cur += 1
                    last_i, last_f = target, fr
                f = last_f
                if max_width and f.shape[1] > max_width:
                    h = int(round(f.shape[0] * max_width / f.shape[1]))
                    f = cv2.resize(f, (max_width, h), interpolation=cv2.INTER_AREA)
                out[k] = f
        finally:
            cap.release()
        return out


def jpeg_bytes(bgr: np.ndarray, quality: int = 85) -> bytes:
    ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise RuntimeError("jpeg encode failed")
    return buf.tobytes()


def extract_audio(video: str, out: Path, t0: float, t1: float, sr: int, channels: int = 1) -> Path:
    """PCM16 wav of [t0, t1) (input seek, so the file's 0 s is t0)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + ".part.wav")
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t0:.6f}", "-t", f"{t1 - t0:.6f}",
           "-i", video, "-map", "0:a:0", "-vn", "-ac", str(channels), "-ar", str(sr), "-c:a", "pcm_s16le", str(tmp)]
    subprocess.run(cmd, check=True, creationflags=NO_WINDOW)
    tmp.replace(out)
    return out


def write_wav(path: Path, x: np.ndarray, sr: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.stem + ".part.wav")
    sf.write(str(tmp), np.clip(np.asarray(x, dtype=np.float32), -1.0, 1.0), sr, subtype="PCM_16")
    tmp.replace(path)
    return path
