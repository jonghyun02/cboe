# -*- coding: utf-8 -*-
"""받아쓰기 (5.2 대사 트랙): Whisper large-v3 word timestamps on the dialogue track.

faster-whisper (CTranslate2) on an NVIDIA GPU or the CPU; on an Intel GPU (xpu) the same weights
through OpenVINO (``OVWhisper``).

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
from ..util import device, free_gpu, gpu_lock, log, read_json, timed, write_json

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


class FasterWhisper:
    """CTranslate2 Whisper: NVIDIA GPU in float16, or the CPU in int8."""

    def __init__(self, dev: str):
        cuda = dev.startswith("cuda")
        if cuda:
            _cuda_dlls()
        from faster_whisper import WhisperModel
        self.name = paths.WHISPER_MODEL
        self.model = WhisperModel(self.name, device="cuda" if cuda else "cpu",
                                  compute_type="float16" if cuda else "int8")

    def words(self, audio: np.ndarray, language: str, hotwords: str | None):
        """(word, start, end, probability) with times relative to ``audio``."""
        segs, _ = self.model.transcribe(audio, language=language, beam_size=5, word_timestamps=True,
                                        vad_filter=False, condition_on_previous_text=False,
                                        hotwords=hotwords or None)
        for seg in segs:
            for w in seg.words or []:
                yield w.word, w.start, w.end, float(w.probability)


class OVWhisper:
    """The same Whisper large-v3 weights (fp16) through OpenVINO GenAI, for Intel GPUs (Arc,
    Core Ultra) where CTranslate2 does not run. Word times come from OpenVINO's DTW over the
    alignment heads, which gives faster-whisper's times to ~0.02 s; faster-whisper's clean-up of
    long first words after a pause is applied on top (``fw_segment_fixes``).
    Differences from FasterWhisper: greedy decoding (OpenVINO 2026.4 has no beam search for Whisper
    on the GPU), no temperature fallback, no word probability (``p`` is None; nothing reads it).
    Measured on an Arc 130V, 85 s clip: 10 s, against 119 s for faster-whisper on the CPU and
    50 s for transformers on the torch xpu device (kernel launches dominate there)."""

    def __init__(self, dev: str):
        import openvino_genai as og
        from huggingface_hub import snapshot_download
        self.name = paths.WHISPER_OV_MODEL
        model_dir = self.name if Path(self.name).is_dir() else snapshot_download(
            self.name, revision=paths.WHISPER_OV_REVISION)       # the tested revision (setup.cmd fetches it)
        cache = Path.home() / ".cboe" / "ov_cache"         # compiled kernels: 26 s first load, 8 s after
        cache.mkdir(parents=True, exist_ok=True)
        self.pipe = og.ASRPipeline(model_dir, "CPU" if dev == "cpu" else "GPU", word_timestamps=True,
                                   CACHE_DIR=str(cache))

    def words(self, audio: np.ndarray, language: str, hotwords: str | None):
        cfg = self.pipe.get_generation_config()
        cfg.language, cfg.task = f"<|{language}|>", "transcribe"
        cfg.return_timestamps = cfg.word_timestamps = True
        if hotwords:
            cfg.hotwords = " " + hotwords.strip()
        r = self.pipe.generate(np.ascontiguousarray(audio, dtype=np.float32).tolist(), cfg)
        # segments carry no token ids here, so words are matched to them by their characters
        words = [{"word": w.text, "start": float(w.start_ts), "end": float(w.end_ts), "n": _nchars(w.text)}
                 for w in (r.words[0] if r.words else [])]
        segs = [{"start": float(c.start_ts), "end": float(c.end_ts), "n": _nchars(c.text)}
                for c in (r.chunks[0] if r.chunks else [])]
        for w in fw_segment_fixes(words, segs, len(audio) / SR):
            if w["word"].strip():
                yield w["word"], w["start"], w["end"], None


SENTENCE_END = tuple(".。!！?？")


def _nchars(text: str) -> int:
    return len(re.sub(r"\s", "", text))


def fw_segment_fixes(words: list[dict], segs: list[dict], duration: float) -> list[dict]:
    """faster-whisper's word-time clean-up per timestamp segment (WhisperModel.add_word_timestamps):
    the first word after a pause is at most twice the median word length, and segment-level times
    win over a first/last word that runs 0.5 s past them. ``words`` [{word, start, end, n}] and
    ``segs`` [{start, end, n}] of one window; a segment takes words until their n add up to its n."""
    durs = np.array([w["end"] - w["start"] for w in words])
    durs = durs[durs.nonzero()]
    med = min(0.7, float(np.median(durs))) if len(durs) else 0.0
    mx = 2 * med
    # a long word right after a sentence end starts at most 2 x median before its end (faster-whisper
    # does this on the alignment before punctuation is merged into the words)
    words = [dict(w) for w in words]
    if len(durs):
        for i in range(1, len(words)):
            if words[i]["end"] - words[i]["start"] > mx and words[i - 1]["word"].rstrip()[-1:] in SENTENCE_END:
                words[i]["start"] = words[i]["end"] - mx
    if not segs:
        segs = [{"start": 0.0, "end": duration, "n": sum(w["n"] for w in words)}]
    out, wi, last_speech = [], 0, 0.0
    for k, seg in enumerate(segs):
        ws, saved = [], 0
        while wi < len(words) and (saved < seg["n"] or k == len(segs) - 1):
            ws.append(dict(words[wi]))
            saved += words[wi]["n"]
            wi += 1
        if ws:
            if ws[0]["end"] - last_speech > med * 4 and (
                    ws[0]["end"] - ws[0]["start"] > mx or (len(ws) > 1 and ws[1]["end"] - ws[0]["start"] > mx * 2)):
                if len(ws) > 1 and ws[1]["end"] - ws[1]["start"] > mx:
                    ws[0]["end"] = ws[1]["start"] = max(ws[1]["end"] / 2, ws[1]["end"] - mx)
                ws[0]["start"] = max(0.0, ws[0]["end"] - mx)
            s0, s1 = seg["start"], seg["end"]
            if s0 < ws[0]["end"] and s0 - 0.5 > ws[0]["start"]:
                ws[0]["start"] = max(0.0, min(ws[0]["end"] - med, s0))
            if s1 > ws[-1]["start"] and s1 + 0.5 < ws[-1]["end"]:
                ws[-1]["end"] = max(ws[-1]["start"] + med, s1)
                last_speech = s1
            else:
                last_speech = ws[-1]["end"]
        out += ws
    return out


def load_model(dev: str):
    """faster-whisper where CTranslate2 runs (NVIDIA GPU, CPU), OpenVINO on an Intel GPU (xpu).
    ``CBOE_ASR_BACKEND=faster-whisper|openvino`` forces one."""
    backend = os.environ.get("CBOE_ASR_BACKEND") or (
        "faster-whisper" if dev.startswith("cuda") or dev == "cpu" else "openvino")
    return FasterWhisper(dev) if backend == "faster-whisper" else OVWhisper(dev)


def transcribe(wav: Path, language: str | None, hotwords: str | None, t0: float, dev: str | None = None) -> dict:
    audio, sr = sf.read(str(wav), dtype="float32", always_2d=True)
    audio = audio.mean(axis=1)
    assert sr == SR
    regions = speech_regions(audio)
    from ..util import progress
    model = load_model(dev or device())
    name = model.name
    words = []
    groups = chunks(regions)
    try:
        for gi, (a, b) in enumerate(groups):
            progress("asr", gi, len(groups))
            pad_a, pad_b = max(0, a - int(0.1 * SR)), min(len(audio), b + int(0.1 * SR))
            off = t0 + pad_a / SR
            for w, s, e, pr in model.words(audio[pad_a:pad_b], language or "ko", hotwords):
                txt = w.strip()
                if txt:
                    words.append({"w": txt, "s": round(s + off, 3), "e": round(e + off, 3),
                                  "p": None if pr is None else round(pr, 3)})
    finally:
        del model
        free_gpu()
    words.sort(key=lambda w: w["s"])
    return {"language": language or "ko", "words": words, "model": name, "input": wav.name,
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
    dev = device()
    with gpu_lock(), timed(f"Whisper on {wav.name} ({dev})"):
        out = transcribe(wav, lang, hot, float(p.meta.get("t0", 0.0)), dev)
    write_json(p.path("asr.json"), out)
    log.info("asr: %d words in %d speech regions", len(out["words"]), len(out["vad"]))
    return {"words": len(out["words"]), "regions": len(out["vad"])}
