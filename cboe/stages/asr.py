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

Hotwords (``hotword_names``): the character names that are said in the dialogue text.
Names that only stand in the speaker column and numbered role labels ('친구1') are not
passed: Whisper wrote such hint text into stretches it could not make out, and the real
lines there lost their times (감사 6-m3, 1-10). A chunk that still comes back as nothing
but the hint names is left out of ``words`` and kept in ``hint_echo`` (``hint_echo``).

Language: one language for the whole run, as before. ``cfg["asr_detect_language"]`` (off
by default) asks the model for the language of each chunk first, where the backend can
(faster-whisper; not the OpenVINO pipeline), and transcribes the chunk in it. Either way
``chunks`` records the language used for every chunk, for the lines the script marks as
foreign (넷플릭스 II.6).

Output asr.json {"language", "words": [{"w","s","e","p"}], "vad": [[s, e]],
"chunks": [{"start","end","language","detected","words"}], "hint_echo": [words],
"hotwords": str | null} (absolute s).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import numpy as np
import soundfile as sf

from .. import paths
from ..util import device, free_gpu, gpu_lock, local_first, log, read_json, timed, write_json

SR = 16000
VAD = {"threshold": 0.35, "min_speech_duration_ms": 100, "min_silence_duration_ms": 300, "speech_pad_ms": 150}
MAX_CHUNK_S = 25.0
JOIN_GAP_S = 1.0
HOTWORDS_MAX = 30
# A chunk of at least ECHO_MIN_WORDS words that are all hint names is the hint repeated, and so
# is a run of at least ECHO_MIN_RUN such words naming two or more characters inside a longer
# chunk. From one 21-minute run (감사 6-m3): its two chunks made of hint words only were both
# echoes (a name and a numbered role label alternating six words long, and a two-word name
# over a groan) and no real line was a chunk of names. Not validated beyond that; a real line
# that is nothing but names loses its words here and gets its time from the voice activity
# instead. A single name stays, and so does a chunk that repeats one word (a name called out
# twice, '할머니, 할머니!': no different hint word in it).
ECHO_MIN_WORDS, ECHO_MIN_RUN = 2, 4
LANG_MIN_PROB = 0.5           # a detected chunk language under this is not trusted (not validated)


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


def hotword_names(script: dict | None) -> list[str]:
    """Character names to give Whisper as hotwords: those said somewhere in the dialogue text.

    Left out: names that only stand in the speaker column (a hint for a word nobody says can
    only do harm), numbered role labels such as '친구1' or '군인 2', and one-letter names
    (they match inside other words). At most HOTWORDS_MAX names, in script order."""
    if not script:
        return []
    said = re.sub(r"\s", "", "".join(str(ln.get("text") or "") for ln in script.get("lines") or [])).lower()
    out: list[str] = []
    for name in script.get("characters") or []:
        name = " ".join(str(name or "").split())
        key = name.replace(" ", "").lower()
        if len(key) < 2 or re.search(r"\d+$", key) or key not in said or name in out:
            continue
        out.append(name)
    return out[:HOTWORDS_MAX]


def _hint_key(word: str) -> str:
    """A word as compared with the hints: letters only, without a trailing number ('친구2.' -> '친구')."""
    return re.sub(r"\d+$", "", re.sub(r"[^0-9A-Za-z가-힣]", "", word)).lower()


def hint_echo(words: list[str], hotwords: str | list[str] | None) -> list[bool]:
    """For the words of ONE transcribed chunk: True where Whisper only repeated the hotword hint.

    ``hotwords`` is the hint: the list of names (a name may have several words), or the string
    given to the model (then every word is taken as a name of its own). A word counts as a hint
    word when it is a word of a name, with or without a number after it (hint '친구1', output
    '친구2 친구3'). Marked: the whole chunk when it has at least ECHO_MIN_WORDS words, all are
    hint words and at least two of them differ (two names, or the words of one name of several
    words); inside a longer chunk a run of at least ECHO_MIN_RUN hint words that names two or
    more different characters. A chunk that only repeats one word — a call such as '엄마! 엄마!'
    — is kept."""
    names = hotwords.split() if isinstance(hotwords, str) else list(hotwords or [])
    owner: dict[str, int] = {}
    for i, name in enumerate(names):
        for tok in str(name).split():
            if _hint_key(tok):
                owner.setdefault(_hint_key(tok), i)
    out = [False] * len(words)
    if not owner:
        return out
    wk = [_hint_key(w) for w in words]
    k = 0
    while k < len(words):
        if wk[k] not in owner:
            k += 1
            continue
        j = k
        while j < len(words) and wk[j] in owner:
            j += 1
        if k == 0 and j == len(words):
            # two different hint words (two names, or the words of a name of several words); one
            # word, however often, is someone calling out ('엄마! 엄마!')
            echo = j - k >= ECHO_MIN_WORDS and len(set(wk[k:j])) >= 2
        else:
            echo = j - k >= ECHO_MIN_RUN and len({owner[x] for x in wk[k:j]}) >= 2
        if echo:
            out[k:j] = [True] * (j - k)
        k = j
    return out


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
        self.model = local_first(WhisperModel, self.name, device="cuda" if cuda else "cpu",   # cached: no huggingface.co
                                 compute_type="float16" if cuda else "int8")

    def words(self, audio: np.ndarray, language: str, hotwords: str | None):
        """(word, start, end, probability) with times relative to ``audio``."""
        segs, _ = self.model.transcribe(audio, language=language, beam_size=5, word_timestamps=True,
                                        vad_filter=False, condition_on_previous_text=False,
                                        hotwords=hotwords or None)
        for seg in segs:
            for w in seg.words or []:
                yield w.word, w.start, w.end, float(w.probability)

    def detect_language(self, audio: np.ndarray) -> tuple[str, float]:
        """(language code, probability) of a chunk from Whisper's language tokens. Only called
        with ``cfg["asr_detect_language"]``."""
        lang, prob, _ = self.model.detect_language(audio)
        return lang, float(prob)


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
        model_dir = self.name if Path(self.name).is_dir() else local_first(
            snapshot_download, self.name, revision=paths.WHISPER_OV_REVISION)   # the tested revision (setup.cmd fetches it)
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


def transcribe_chunks(audio: np.ndarray, groups: list[tuple[int, int]], model, language: str | None,
                      hotwords: str | None, t0: float, detect: bool = False,
                      hint_names: list[str] | None = None) -> dict:
    """Transcribe every chunk (sample range of ``audio``) on its own with ``model.words``.

    Returns {"words", "chunks", "hint_echo"}: the words in time order without those that only
    repeat the hint, one row per chunk with the language it was transcribed in, and the words
    left out as hint echo. With ``detect`` and a model that has ``detect_language(clip)`` the
    chunk is transcribed in the language found (when its probability reaches LANG_MIN_PROB) and
    the name hints are given only to chunks in the script's language; without it every chunk
    gets ``language`` and the hints, exactly as before."""
    from ..util import progress
    base = language or "ko"
    det = getattr(model, "detect_language", None) if detect else None
    if detect and det is None:
        log.warning("asr: this backend cannot tell the language of a chunk; every chunk is transcribed as %s", base)
    words, echo, rows = [], [], []
    for gi, (a, b) in enumerate(groups):
        progress("asr", gi, len(groups))
        pad_a, pad_b = max(0, a - int(0.1 * SR)), min(len(audio), b + int(0.1 * SR))
        off = t0 + pad_a / SR
        clip = audio[pad_a:pad_b]
        lang, prob, found = base, None, False
        if det is not None:
            got = det(clip)
            if got and got[0]:
                prob = None if got[1] is None else float(got[1])
                if prob is None or prob >= LANG_MIN_PROB:
                    lang, found = str(got[0]), True
        hints = hotwords if lang == base else None
        chunk = []
        for w, s, e, pr in model.words(clip, lang, hints):
            txt = w.strip()
            if txt:
                chunk.append({"w": txt, "s": round(s + off, 3), "e": round(e + off, 3),
                              "p": None if pr is None else round(pr, 3)})
        flags = [False] * len(chunk)
        if hints:                                   # only a chunk that was given the hint can repeat it
            flags = hint_echo([w["w"] for w in chunk], hint_names if hint_names is not None else hints)
        words += [w for w, f in zip(chunk, flags) if not f]
        echo += [w for w, f in zip(chunk, flags) if f]
        row = {"start": round(t0 + a / SR, 3), "end": round(t0 + b / SR, 3), "language": lang, "detected": found,
               "words": len(chunk) - sum(flags)}
        if prob is not None:
            row["prob"] = round(prob, 3)
        if any(flags):
            row["hint_echo"] = sum(flags)
        rows.append(row)
    words.sort(key=lambda w: w["s"])
    return {"words": words, "chunks": rows, "hint_echo": echo}


def transcribe(wav: Path, language: str | None, hotwords: str | None, t0: float, dev: str | None = None,
               detect: bool = False, hint_names: list[str] | None = None) -> dict:
    audio, sr = sf.read(str(wav), dtype="float32", always_2d=True)
    audio = audio.mean(axis=1)
    assert sr == SR
    regions = speech_regions(audio)
    model = load_model(dev or device())
    name = model.name
    try:
        got = transcribe_chunks(audio, chunks(regions), model, language, hotwords, t0, detect, hint_names)
    finally:
        del model
        free_gpu()
    return {"language": language or "ko", "words": got["words"], "model": name, "input": wav.name,
            "vad": [[round(t0 + a / SR, 3), round(t0 + b / SR, 3)] for a, b in regions], "vad_options": VAD,
            "chunks": got["chunks"], "hint_echo": got["hint_echo"], "hotwords": hotwords,
            "detect_language": bool(detect)}


def run(p, cfg: dict) -> dict:
    if p.path("asr.json").exists():
        return {"skipped": True}
    script = read_json(p.path("script.json"))
    lang = script_language(script)
    names = hotword_names(script)
    hot = " ".join(names) or None
    wav = p.path("audio") / "speech_16k.wav"
    if not wav.exists():
        wav = p.path("audio") / "mix_16k.wav"
    dev = device()
    with gpu_lock(), timed(f"Whisper on {wav.name} ({dev})"):
        out = transcribe(wav, lang, hot, float(p.meta.get("t0", 0.0)), dev,
                         detect=bool(cfg.get("asr_detect_language", False)), hint_names=names)
    write_json(p.path("asr.json"), out)
    log.info("asr: %d words in %d speech regions (hint names: %s; %d words left out as hint echo)",
             len(out["words"]), len(out["vad"]), hot or "none", len(out["hint_echo"]))
    return {"words": len(out["words"]), "regions": len(out["vad"]), "hint_echo": len(out["hint_echo"])}
