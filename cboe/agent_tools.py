# -*- coding: utf-8 -*-
"""Tools of the 재확인 에이전트 (deck 5.8), served as an MCP server on localhost.

| 애매한 이유 | 도구 |
|---|---|
| 소리 종류가 명확하지 않음 | ``rescan_sound`` (원본·효과음 트랙 재검출), ``describe_sound`` (소리 설명 모델) |
| 화면과 소리가 안 맞음 | ``look_frames`` (관련 장면 프레임) |
| 줄거리 의미가 불명확 | ``dialogue_around``, ``search_dialogue``, ``scene_context`` (앞뒤 장면·전체 대사) |

``describe_sound`` runs Qwen2.5-Omni-3B (thinker only, bf16 — its language model int8 on a GPU
under 11 GiB — local GPU; Qwen research
license) on the clip and is serialised with a lock; the other tools read the files the
pipeline already wrote.

Every call goes through ``Tools.log`` (``CallLog``): what the tool returned, or the exception
it raised, is kept so that the agent stage can write it into agent/<id>.json and tell a
re-check whose tool failed from a finished one (감사 4-m1). The MCP library only tells the
model 'Error executing tool …'; the cause stays here.
"""
from __future__ import annotations

import re
import socket
import threading
import time

import numpy as np

from . import ontology, paths, phrases
from .media import Media, jpeg_bytes
from .stages import sed
from .stages.recommend import timing_estimated
from .util import device, fmt_time, free_gpu, log, read_json


OMNI_BF16_GIB = 11.0          # GPU memory the bf16 thinker needs with room for the clip
# The question put to the sound description model. Version 1 gave one example answer ('a heavy
# object falling on the floor'): 13 of 38 answers of a 21-minute run were that phrase (감사 4-m1).
# Version 2 gives no example to copy and lets the model say that it cannot tell. Not run yet:
# the answers of version 2 still have to be looked at on real clips.
DESCRIBE_PROMPT_VERSION = 2
DESCRIBE_PROMPT = (
    "Listen to this audio clip from a TV drama. Ignore speech words and background music. "
    "What sound effect or non-speech sound is heard? Answer in one short English phrase that names "
    "what makes the sound and what it does, from what you hear in this clip only. "
    "If it is a person's voice sound (scream, laugh, gasp), say so. "
    "If no sound stands out or you cannot tell what it is, answer 'unclear'.")


def _as_text(out) -> str:
    """What a tool returned as text for the record: pictures become '[사진]'."""
    if isinstance(out, str):
        return out
    if isinstance(out, (list, tuple)):
        return "\n".join(x if isinstance(x, str) else "[사진]" for x in out)
    return str(out)


def _number(x) -> float | None:
    if isinstance(x, bool):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def same_args(given: dict | None, called: dict) -> bool:
    """The arguments the model sent against those a tool was called with: numbers compare as
    numbers, and arguments the model left out took their defaults."""
    for k, v in (given or {}).items():
        if k not in called:
            return False
        a, b = _number(v), _number(called[k])
        if a is not None and b is not None:
            if abs(a - b) > 1e-6:
                return False
        elif str(v) != str(called[k]):
            return False
    return True


class CallLog:
    """What every tool call returned or raised.

    The tool server is stateless: it cannot tell which re-check a call belongs to. ``claim``
    matches the calls a re-check made (tool name and arguments, as the model sent them) to the
    rows here; each row is given to one call. The latest rows are matched first: when the LLM
    call was retried, the calls of the attempt that answered are the last ones."""
    OUTPUT_MAX = 4000

    def __init__(self):
        self._lock = threading.Lock()
        self.rows: list[dict] = []

    def run(self, tool: str, args: dict, fn):
        """``fn()`` for one tool call. An exception is recorded and raised again (the tool server
        turns it into an error result for the model)."""
        t = time.time()
        try:
            out = fn()
        except Exception as e:
            self._add(tool, args, t, None, f"{type(e).__name__}: {e}"[:500])
            raise
        self._add(tool, args, t, _as_text(out)[:self.OUTPUT_MAX], None)
        return out

    def _add(self, tool: str, args: dict, t: float, output: str | None, error: str | None) -> None:
        with self._lock:
            self.rows.append({"tool": tool, "args": dict(args), "at": t, "seconds": round(time.time() - t, 2),
                              "output": output, "error": error, "claimed": False})

    def errors(self) -> dict[str, int]:
        """Number of calls that raised, per tool."""
        with self._lock:
            out: dict[str, int] = {}
            for r in self.rows:
                if r["error"] is not None:
                    out[r["tool"]] = out.get(r["tool"], 0) + 1
            return out

    def claim(self, trace: list[dict], since: float | None = None) -> None:
        """Write ``output`` (the text returned, or the exception), ``seconds`` and ``error: True``
        (the call raised) into the tool calls of one re-check: ``trace`` rows {"tool", "input"}.
        ``since``: when the re-check began (time.time()); calls that started before it are not its."""
        with self._lock:
            for call in reversed(trace):
                row = next((r for r in reversed(self.rows) if not r["claimed"] and r["tool"] == call.get("tool")
                            and (since is None or r["at"] >= since) and same_args(call.get("input"), r["args"])), None)
                if row is None:
                    continue
                row["claimed"] = True
                call["seconds"] = row["seconds"]
                if row["error"] is not None:
                    call["error"], call["output"] = True, row["error"]
                elif "output" not in call:
                    call["output"] = row["output"]


def _gpu_gib(dev: str) -> float:
    """Total memory of the torch device in GiB (inf on the CPU: nothing to save there)."""
    import torch
    try:
        if dev.startswith("cuda"):
            return torch.cuda.get_device_properties(0).total_memory / 2**30
        if dev.startswith("xpu"):
            return torch.xpu.get_device_properties(0).total_memory / 2**30
    except Exception:
        pass
    return float("inf")


def _norm(s: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", s).lower()


def fmt_line(ln: dict) -> str:
    """A dialogue line for the agent: 'L0012 03:48.7 이름: 대사'. A line without a time (the scene
    is not in the video, or lies outside the processed part) says so instead, and a time that
    align could only guess is marked — a 'reaction right after the sound' on it proves little."""
    if ln.get("start") is None:
        when = {"absent": "(영상에 없는 장면)", "out_of_range": "(처리 구간 밖)"}.get(ln.get("status"), "(시각 없음)")
    else:
        when = fmt_time(ln["start"]) + (" (시각 추정)" if timing_estimated(ln) else "")
    return f"{ln['id']} {when} {ln.get('speaker') or '?'}: {ln['text']}"


class Tools:
    def __init__(self, p):
        self.p = p
        self.meta = p.meta
        self.media = Media(self.meta, p.work)
        self.P, self.names, self.t0 = sed.load(p, "effects")
        self.Q, _, _ = sed.load(p, "mix")
        self.onto = ontology.get()
        self.lines = (read_json(p.path("dialogue.json"), {}) or {}).get("lines", [])
        self.cuts = (read_json(p.path("cuts.json"), {}) or {}).get("cuts", [])
        self.items = (read_json(p.path("items.json"), {}) or {}).get("items", [])
        self._omni = None
        self._omni_lock = threading.RLock()                # preload holds it while _describe takes it
        self._closed = False
        self._frame_lock = threading.Lock()
        self.calls: dict[str, int] = {}
        self.log = CallLog()

    def _count(self, name):
        with self._frame_lock:             # tools run on several MCP worker threads
            self.calls[name] = self.calls.get(name, 0) + 1

    # ----------------------------------------------------------- 소리 재검출
    def rescan_sound(self, start: float, end: float) -> str:
        self._count("rescan_sound")
        start, end = self.media.clamp(float(start), float(end))
        a, b = int((start - self.t0) * sed.FPS), max(int((end - self.t0) * sed.FPS), int((start - self.t0) * sed.FPS) + 2)
        tab = phrases.table()
        out = [f"재검출 {fmt_time(start)}~{fmt_time(end)} (PretrainedSED 447종, 점수는 0~1, 0.3 이상이면 꽤 확실)"]
        for name, M in (("효과음 트랙", self.P), ("원본 소리", self.Q)):
            pk = M[a:b].max(axis=0)
            order = np.argsort(-pk)[:8]
            items = [f"{phrases.ko(self.names[i]) if tab.get(self.names[i], {}).get('desc') else self.names[i]}"
                     f"({self.names[i]}) {pk[i]:.2f}" for i in order]
            out.append(f"- {name}: " + ", ".join(items))
        top = self.names[int(np.argmax(self.P[a:b].max(axis=0)))]
        anc = " → ".join([phrases.ko(top)] + [phrases.ko(x) for x in sorted(self.onto.ancestors(top))[:3]])
        out.append(f"- 1위 종류의 상위 종류: {anc}")
        return "\n".join(out)

    # ----------------------------------------------------------- 소리 설명 모델
    def _load_omni(self):
        import torch
        from transformers import Qwen2_5OmniProcessor
        from transformers import Qwen2_5OmniThinkerForConditionalGeneration as Thinker
        dev = device()
        kw = {}
        if _gpu_gib(dev) < OMNI_BF16_GIB:
            # the thinker is 8.8 GiB in bf16; on a smaller GPU (8 GB Arc 130V) its language model
            # goes int8 weight-only while loading (6.2 GiB in all; audio and vision towers, lm_head
            # stay bf16). Checked on the test clip: laugh / gasp / thunder still told apart.
            try:
                from torchao.quantization import Int8WeightOnlyConfig
                from transformers import TorchAoConfig
                kw["quantization_config"] = TorchAoConfig(
                    Int8WeightOnlyConfig(), modules_to_not_convert=["visual", "audio_tower", "lm_head"])
            except ImportError:
                pass
        model = Thinker.from_pretrained(str(paths.QWEN_OMNI), dtype=torch.bfloat16, device_map=dev,
                                        attn_implementation="sdpa", **kw).eval()
        proc = Qwen2_5OmniProcessor.from_pretrained(str(paths.QWEN_OMNI))
        self._omni = (model, proc)

    def describe_sound(self, start: float, end: float, track: str = "effects") -> str:
        self._count("describe_sound")
        start, end = self.media.clamp(float(start), float(end))
        end = max(end, start + 0.5)
        name = {"effects": "effects_16k", "mix": "mix_16k"}.get(track, "effects_16k")
        x, sr = self.media.load_audio(name, start, min(end, start + 10.0))
        ans = self._describe(x)
        return f"소리 설명 모델(Qwen2.5-Omni-3B, {track} 트랙 {fmt_time(start)}~{fmt_time(end)}): {ans}"

    def _describe(self, x: np.ndarray) -> str:
        """Qwen2.5-Omni's one-phrase description of a 16 kHz mono clip."""
        with self._omni_lock:
            if self._closed:                              # a call that waited past the end of the stage
                raise RuntimeError("the agent stage is over; the sound description model was unloaded")
            if self._omni is None:
                self._load_omni()
            model, proc = self._omni
            sys_prompt = ("You are Qwen, a virtual human developed by the Qwen Team, Alibaba Group, capable of "
                          "perceiving auditory and visual inputs, as well as generating text and speech.")
            msgs = [{"role": "system", "content": [{"type": "text", "text": sys_prompt}]},
                    {"role": "user", "content": [{"type": "audio"}, {"type": "text", "text": DESCRIBE_PROMPT}]}]
            text = proc.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False)
            inp = proc(text=text, audio=[x.astype(np.float32)], return_tensors="pt", padding=True,
                       use_audio_in_video=False).to(model.device).to(model.dtype)
            import torch
            with torch.no_grad():
                out = model.generate(**inp, max_new_tokens=40, do_sample=False, eos_token_id=[151645, 151643],
                                     pad_token_id=151643)
            return proc.batch_decode(out[:, inp["input_ids"].shape[1]:], skip_special_tokens=True)[0].strip()

    def slow_load(self) -> bool:
        """True where loading the sound model takes longer than a tool call should (int8 path)."""
        return _gpu_gib(device()) < OMNI_BF16_GIB

    def preload(self) -> None:
        """Load the sound description model and run it once on silence, so that the agents'
        first describe_sound does not wait for it (int8 load + first GPU kernels: ~1.5 min on
        an Arc 130V, longer than a tool call may take)."""
        try:
            with self._omni_lock:
                if self._closed:
                    return
                if self._omni is None:
                    self._load_omni()
                self._describe(np.zeros(16000, np.float32))
        except Exception as e:                            # describe_sound will report it when called
            log.warning("sound description model not preloaded: %s", e)

    def close(self):
        with self._omni_lock:                             # waits for a preload still running
            self._closed = True
            if self._omni is not None:
                self._omni = None
                free_gpu()

    # ----------------------------------------------------------------- 화면
    def look_frames(self, time: float, span: float = 2.0, count: int = 4) -> list:
        self._count("look_frames")
        count = int(min(max(count, 1), 6))
        span = float(min(max(span, 0.2), 8.0))
        ts = [float(time) - span / 2 + span * k / max(count - 1, 1) for k in range(count)] if count > 1 else [float(time)]
        with self._frame_lock:
            frames = self.media.frames_at(ts, max_width=448)
        out: list = []
        from mcp.server.mcpserver import Image
        cuts = [c for c in self.cuts if ts[0] - 0.05 <= c <= ts[-1] + 0.05]
        out.append(f"프레임 {count}장 ({', '.join(fmt_time(t) for t in ts)})" +
                   (f"; 이 사이 장면 전환: {', '.join(fmt_time(c) for c in cuts)}" if cuts else ""))
        for t, fr in zip(ts, frames):
            out.append(f"{fmt_time(t)}")
            out.append(Image(data=jpeg_bytes(fr, 80), format="jpeg"))
        return out

    # ----------------------------------------------------------------- 대사
    def _fmt_line(self, ln) -> str:
        return fmt_line(ln)

    def dialogue_around(self, time: float, before: float = 15.0, after: float = 15.0) -> str:
        self._count("dialogue_around")
        rows = [fmt_line(ln) for ln in self.lines if ln.get("start") is not None and ln.get("end") is not None
                and ln["end"] >= float(time) - before and ln["start"] <= float(time) + after]
        return "\n".join(rows) if rows else "(이 구간에 대사 없음)"

    def search_dialogue(self, query: str) -> str:
        self._count("search_dialogue")
        keys = [_norm(k) for k in re.split(r"[\s,]+", query) if _norm(k)]
        rows = [self._fmt_line(ln) for ln in self.lines if any(k in _norm(ln["text"]) for k in keys)]
        return "\n".join(rows[:30]) if rows else f"('{query}'가 들어간 대사 없음)"

    def scene_context(self, time: float) -> str:
        self._count("scene_context")
        t = float(time)
        prev_c = [c for c in self.cuts if c <= t][-3:]
        next_c = [c for c in self.cuts if c > t][:3]
        near = [it for it in self.items if abs(it["onset"] - t) <= 20 and it["kind"] != "speaker"]
        rows = [f"장면 전환: 앞 {', '.join(fmt_time(c) for c in prev_c) or '없음'} / 뒤 {', '.join(fmt_time(c) for c in next_c) or '없음'}",
                "주변 후보(±20초):"]
        for it in near:
            rows.append(f"  {it['id']} {fmt_time(it['onset'])} {it['kind']} {it['phrase']} → {it['rec']} ({it['reason']})")
        return "\n".join(rows)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


TOOL_DOCS = {
    "rescan_sound": "Re-detect sounds between start and end seconds on the effects track and the original mix "
                    "(PretrainedSED, 447 AudioSet classes). Returns top classes with scores and the hierarchy.",
    "describe_sound": "Describe the non-speech sound between start and end seconds with a local audio-language "
                      "model (Qwen2.5-Omni-3B). track: 'effects' (separated effects) or 'mix' (original).",
    "look_frames": "Look at video frames around a time (seconds): count frames (1-6) spread over span seconds.",
    "dialogue_around": "Dialogue lines (id, time, speaker, text) from `before` seconds before to `after` seconds "
                       "after a time.",
    "search_dialogue": "Search the whole script for lines containing any of the words in query.",
    "scene_context": "Shot changes around a time and the other caption candidates within 20 seconds with their "
                     "current recommendations.",
}


def tool_functions(tools) -> dict:
    """The tools as the MCP server offers them (name -> function; the signatures are the tool
    schemas). Every call runs through ``tools.log``, which keeps its result or its exception."""
    run = tools.log.run

    def rescan_sound(start: float, end: float) -> str:
        return run("rescan_sound", {"start": start, "end": end}, lambda: tools.rescan_sound(start, end))

    def describe_sound(start: float, end: float, track: str = "effects") -> str:
        return run("describe_sound", {"start": start, "end": end, "track": track},
                   lambda: tools.describe_sound(start, end, track))

    def look_frames(time: float, span: float = 2.0, count: int = 4) -> list:
        return run("look_frames", {"time": time, "span": span, "count": count},
                   lambda: tools.look_frames(time, span, count))

    def dialogue_around(time: float, before: float = 15.0, after: float = 15.0) -> str:
        return run("dialogue_around", {"time": time, "before": before, "after": after},
                   lambda: tools.dialogue_around(time, before, after))

    def search_dialogue(query: str) -> str:
        return run("search_dialogue", {"query": query}, lambda: tools.search_dialogue(query))

    def scene_context(time: float) -> str:
        return run("scene_context", {"time": time}, lambda: tools.scene_context(time))

    return {f.__name__: f for f in (rescan_sound, describe_sound, look_frames, dialogue_around, search_dialogue,
                                    scene_context)}


def make_server(tools):
    """The MCP server with the six tools registered (not started)."""
    from mcp.server.mcpserver import MCPServer

    srv = MCPServer("cboe")
    for name, fn in tool_functions(tools).items():
        srv.tool(description=TOOL_DOCS[name])(fn)
    return srv


def serve(tools: Tools) -> tuple[str, object]:
    """Start the MCP server in a background thread; returns (url, uvicorn server)."""
    import uvicorn

    srv = make_server(tools)
    port = free_port()
    app = srv.streamable_http_app(stateless_http=True, json_response=True)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    if not server.started:
        server.should_exit = True
        raise RuntimeError(f"agent tool server did not start on port {port}")
    return f"http://127.0.0.1:{port}/mcp", server
