# -*- coding: utf-8 -*-
"""Claude calls through the Claude Code CLI (``claude -p``) with structured output.

- ``ask()``: one structured answer for a prompt made of text and JPEG images
  (stream-json input), used for 화면 근거 and 대사 근거.
- ``agent()``: the same call with an MCP tool server attached (``--mcp-config``);
  the model decides which tools to call (재확인 에이전트, deck 5.8).

The CLI runs with the user's own Claude Code login (or ANTHROPIC_API_KEY when set).
User settings, CLAUDE.md, hooks, plugins and auto-memory are kept out of the call
(``--setting-sources ""``, ``--strict-mcp-config``, ``CLAUDE_CODE_DISABLE_AUTO_MEMORY``)
and built-in tools are disabled (``--tools ""``). Every call is appended to
``<work>/llm_log.jsonl`` (model, seconds, tokens, cost estimate).

A deployed product should call the Claude API with its own API key instead of a
personal claude.ai login (Agent SDK terms); the call shape stays the same.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

_log_lock = threading.Lock()


class LLMError(RuntimeError):
    pass


def available() -> bool:
    return shutil.which("claude") is not None


def _content(parts) -> list[dict]:
    out = []
    for p in parts:
        if isinstance(p, (bytes, bytearray)):
            out.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                    "data": base64.b64encode(p).decode("ascii")}})
        else:
            out.append({"type": "text", "text": str(p)})
    return out


def _run(cmd: list[str], stdin: bytes, timeout: float, trace: list | None = None,
         require_mcp: str | None = None) -> dict:
    from .util import NO_WINDOW
    # MCP_TOOL_TIMEOUT: the first describe_sound may wait for the sound model to load (slow GPUs)
    env = {"MCP_TOOL_TIMEOUT": "600000", **os.environ,
           "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1", "ENABLE_TOOL_SEARCH": "false"}
    env.pop("CLAUDECODE", None)            # also runs when the GUI was started from inside Claude Code
    r = subprocess.run(cmd, input=stdin, capture_output=True, timeout=timeout, env=env, creationflags=NO_WINDOW)
    out = r.stdout.decode("utf-8", "replace")
    result = None
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("type") == "system" and msg.get("subtype") == "init" and require_mcp:
            # an answer given without the tools would look like a finished re-check
            status = {s.get("name"): s.get("status") for s in msg.get("mcp_servers") or []}
            if status.get(require_mcp) != "connected":
                raise LLMError(f"MCP server '{require_mcp}' not connected: {status}")
        if msg.get("type") == "result":
            result = msg
        elif trace is not None and msg.get("type") == "assistant":
            for block in (msg.get("message") or {}).get("content") or []:
                if block.get("type") == "tool_use" and not block.get("name", "").endswith("StructuredOutput"):
                    trace.append({"tool": block.get("name", "").replace("mcp__cboe__", ""),
                                  "input": block.get("input")})
    if result is None:
        raise LLMError(f"no result (rc={r.returncode}): {r.stderr.decode('utf-8', 'replace')[:400]} {out[-400:]}")
    return result


def _log(work: Path | None, rec: dict) -> None:
    if not work:
        return
    with _log_lock:
        with open(Path(work) / "llm_log.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def ask(parts, schema: dict, *, model: str = "sonnet", system: str | None = None, effort: str | None = "low",
        mcp_url: str | None = None, tools: list[str] | None = None, max_turns: int | None = None,
        timeout: float = 600, retries: int = 1, work: Path | None = None, tag: str = "",
        trace: list | None = None) -> dict:
    """Structured answer (dict matching ``schema``) for text/JPEG ``parts``.
    ``trace`` (a list) collects the tool calls the model made."""
    cmd = ["claude", "-p", "--setting-sources", "", "--tools", "", "--strict-mcp-config",
           "--model", model, "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
           "--json-schema", json.dumps(schema, ensure_ascii=False), "--no-session-persistence",
           "--permission-mode", "dontAsk"]
    if effort:
        cmd += ["--effort", effort]
    if system:
        cmd += ["--system-prompt", system]
    if mcp_url:
        cmd += ["--mcp-config", json.dumps({"mcpServers": {"cboe": {"type": "http", "url": mcp_url}}})]
        if tools:
            cmd += ["--allowedTools", *[f"mcp__cboe__{t}" for t in tools]]
    if max_turns:
        cmd += ["--max-turns", str(max_turns)]
    msg = {"type": "user", "message": {"role": "user", "content": _content(parts)}, "parent_tool_use_id": None}
    stdin = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
    last = None
    for attempt in range(retries + 1):
        t = time.time()
        if trace is not None:
            trace.clear()
        try:
            res = _run(cmd, stdin, timeout, trace, require_mcp="cboe" if mcp_url else None)
        except (LLMError, subprocess.TimeoutExpired) as e:
            last = e
            _log(work, {"tag": tag, "model": model, "ok": False, "error": str(e)[:300], "attempt": attempt})
            continue
        u = res.get("usage") or {}
        _log(work, {"tag": tag, "model": model, "ok": res.get("subtype") == "success", "attempt": attempt,
                    "seconds": round(time.time() - t, 1), "turns": res.get("num_turns"),
                    "in": u.get("input_tokens"), "cache_create": u.get("cache_creation_input_tokens"),
                    "cache_read": u.get("cache_read_input_tokens"), "out": u.get("output_tokens"),
                    "cost_usd": res.get("total_cost_usd"), "subtype": res.get("subtype")})
        so = res.get("structured_output")
        if res.get("subtype") == "success" and isinstance(so, dict):
            return so
        last = LLMError(f"{res.get('subtype')}: {str(res.get('result'))[:300]}")
    raise LLMError(str(last))
