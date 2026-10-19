# -*- coding: utf-8 -*-
"""영상 준비: probe the video, fix the processing window and extract the mix audio."""
from __future__ import annotations

from pathlib import Path

from .. import guard
from .. import script as script_mod
from ..media import extract_audio
from ..util import ffprobe, log, read_json, write_json


def init(work: Path, video: str, script_path: str | None, t0: float = 0.0, t1: float | None = None,
         title: str | None = None) -> dict:
    """Create/refresh project.json. Changing inputs invalidates nothing automatically —
    use a new work folder for a new video or window."""
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    info = ffprobe(video)
    if not info["has_audio"]:
        raise ValueError(f"{video}: no audio stream")
    end = info["duration"] if t1 is None else min(float(t1), info["duration"])
    if not (0.0 <= t0 < end):
        raise ValueError(f"bad window [{t0}, {end})")
    guard.check_window(video, t0, end, info["duration"])
    old = read_json(work / "project.json", {}) or {}
    meta = {**old, "title": title or old.get("title") or Path(video).stem, "video": str(Path(video).resolve()),
            "script": str(Path(script_path).resolve()) if script_path else None,
            "t0": float(t0), "t1": float(end), "fps": info["fps"], "duration": info["duration"],
            "width": info["width"], "height": info["height"]}
    write_json(work / "project.json", meta)
    return meta


def run(p, cfg: dict) -> dict:
    meta = p.meta
    out = {}
    for name, sr in (("mix_48k", 48000), ("mix_16k", 16000)):
        path = p.path("audio") / f"{name}.wav"
        if not path.exists():
            extract_audio(meta["video"], path, meta["t0"], meta["t1"], sr)
        out[name] = str(path)
    if meta.get("script") and not p.path("script.json").exists():
        sc = script_mod.read(meta["script"])
        write_json(p.path("script.json"), sc)
        log.info("script: %d lines, %d characters, timed=%s", len(sc["lines"]), len(sc["characters"]), sc["timed"])
    return out
