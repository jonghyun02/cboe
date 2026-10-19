# -*- coding: utf-8 -*-
"""받아쓰기 (5.2 대사 트랙): faster-whisper large-v3 word timestamps on the dialogue track.

faster-whisper's own ``vad_filter`` concatenates speech chunks and maps timestamps
back afterwards; a word that the model places a little before a chunk boundary is
mapped onto the end of the previous chunk (seen here: '안녕하세요' at 67.0 s instead
of 93.9 s). So we run Silero VAD ourselves (lower threshold so that whispers and
shouts survive), group the speech regions into chunks of at most 25 s, and
transcribe each chunk on its own with timestamps relative to the chunk.
Character names from the script are passed as hotwords.

Output asr.json {"language", "words": [{"w","s","e","p"}], "vad": [[s, e]]} (absolute s).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import numpy as np
import soundfile as sf

from .. import paths
from ..util import free_gpu, gpu_lock, log, read_json, timed, write_json

SR = 16000
VAD = {"threshold": 0.35, "min_speech_duration_ms": 100, "min_silence_duration_ms": 300, "speech_pad_ms": 150}
MAX_CHUNK_S = 25.0
JOIN_GAP_S = 1.0


def _cuda_dlls() -> None:
    """CTranslate2 needs cublas64_12.dll; the cu128 torch wheel ships it in torch/lib."""
    import torch
    lib = Path(torch.__file__).parent / "lib"
    if str(lib) not in os.environ.get("PATH", ""):
        os.environ["PATH"] = str(lib) + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(lib))


def script_language(script: dict | None) -> str | None:
    if not script or not script.get("lines"):
        return None
    text = "".join(ln["text"] for ln in script["lines"])
    hangul = len(re.findall(r"[가-힣]", text))
    return "ko" if hangul > 0.3 * max(len(re.sub(r"\s", "", text)), 1) else None


def speech_regions(audio: np.ndarray) -> list[tuple[int, int]]:
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    ts = get_speech_timestamps(audio, VadOptions(**VAD), sampling_rate=SR)
    return [(int(t["start"]), int(t["end"])) for t in ts]


def chunks(regions: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for a, b in regions:
        while b - a > MAX_CHUNK_S * SR:                     # very long region: hard split
            out.append([a, a + int(MAX_CHUNK_S * SR)])
            a += int(MAX_CHUNK_S * SR)
        if out and a - out[-1][1] < JOIN_GAP_S * SR and b - out[-1][0] <= MAX_CHUNK_S * SR:
            out[-1][1] = b
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def transcribe(wav: Path, language: str | None, hotwords: str | None, t0: float) -> dict:
    _cuda_dlls()
    from faster_whisper import WhisperModel
    audio, sr = sf.read(str(wav), dtype="float32", always_2d=True)
    audio = audio.mean(axis=1)
    assert sr == SR
    regions = speech_regions(audio)
    from ..util import progress
    model = WhisperModel(paths.WHISPER_MODEL, device="cuda", compute_type="float16")
    words = []
    groups = chunks(regions)
    try:
        for gi, (a, b) in enumerate(groups):
            progress("asr", gi, len(groups))
            pad_a, pad_b = max(0, a - int(0.1 * SR)), min(len(audio), b + int(0.1 * SR))
            segs, info = model.transcribe(audio[pad_a:pad_b], language=language or "ko", beam_size=5,
                                          word_timestamps=True, vad_filter=False,
                                          condition_on_previous_text=False, hotwords=hotwords or None)
            off = t0 + pad_a / SR
            for seg in segs:
                for w in seg.words or []:
                    txt = w.word.strip()
                    if txt:
                        words.append({"w": txt, "s": round(w.start + off, 3), "e": round(w.end + off, 3),
                                      "p": round(float(w.probability), 3)})
    finally:
        del model
        free_gpu()
    words.sort(key=lambda w: w["s"])
    return {"language": language or "ko", "words": words, "model": paths.WHISPER_MODEL, "input": wav.name,
            "vad": [[round(t0 + a / SR, 3), round(t0 + b / SR, 3)] for a, b in regions], "vad_options": VAD}


def run(p, cfg: dict) -> dict:
    if p.path("asr.json").exists():
        return {"skipped": True}
    script = read_json(p.path("script.json"))
    lang = script_language(script)
    hot = " ".join((script or {}).get("characters", [])[:30]) or None
    wav = p.path("audio") / "speech_16k.wav"
    if not wav.exists():
        wav = p.path("audio") / "mix_16k.wav"
    with gpu_lock(), timed(f"faster-whisper {paths.WHISPER_MODEL} on {wav.name}"):
        out = transcribe(wav, lang, hot, float(p.meta.get("t0", 0.0)))
    write_json(p.path("asr.json"), out)
    log.info("asr: %d words in %d speech regions", len(out["words"]), len(out["vad"]))
    return {"words": len(out["words"]), "regions": len(out["vad"])}
