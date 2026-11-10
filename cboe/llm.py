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
``<work>/llm_log.jsonl``: the model alias asked for (``model``), the model id the CLI
reported (``model_id``, e.g. the dated id behind 'sonnet'; null when the CLI did not say),
the caller's prompt version (``prompt``, e.g. 'screen/1'), seconds, tokens, cost estimate.

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


TOOL_OUTPUT_MAX = 4000                     # characters of a tool result kept in the trace


def _result_text(content) -> str:
    """Text of a tool_result block's content (a string, or blocks of text and images)."""
    if isinstance(content, str):
        return content
    bits = []
    for b in content or []:
        if not isinstance(b, dict):
            bits.append(str(b))
        elif b.get("type") == "text":
            bits.append(str(b.get("text", "")))
        else:
            bits.append(f"[{b.get('type', '?')}]")          # a frame: the picture itself is not kept
    return "\n".join(bits)


def parse_stream(out: str, trace: list | None = None, require_mcp: str | None = None) -> tuple[dict | None, str | None]:
    """(result message, model id) from the CLI's stream-json output.

    The model id is what the CLI reports for the call (init message, else the assistant
    messages), e.g. the dated id behind the alias 'sonnet'; None when it does not say.
    ``trace`` collects the model's tool calls as {"tool", "input"} and, when the stream carries
    the tool results, what each call returned: "output" (text, pictures as '[image]') and
    "error": True for a result the tool server marked as an error."""
    result, model_id, by_id = None, None, {}
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = msg.get("type")
        if kind == "system" and msg.get("subtype") == "init":
            model_id = msg.get("model") or model_id
            if require_mcp:
                # an answer given without the tools would look like a finished re-check
                status = {s.get("name"): s.get("status") for s in msg.get("mcp_servers") or []}
                if status.get(require_mcp) != "connected":
                    raise LLMError(f"MCP server '{require_mcp}' not connected: {status}")
        if kind == "result":
            result = msg
        elif kind == "assistant":
            message = msg.get("message") or {}
            model_id = model_id or message.get("model")
            if trace is None:
                continue
            for block in message.get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use" and not block.get("name", "").endswith("StructuredOutput"):
                    call = {"tool": block.get("name", "").replace("mcp__cboe__", ""), "input": block.get("input")}
                    trace.append(call)
                    if block.get("id"):
                        by_id[block["id"]] = call
        elif kind == "user" and trace is not None:
            content = (msg.get("message") or {}).get("content")
            for block in content if isinstance(content, list) else []:
                call = by_id.get(block.get("tool_use_id")) if isinstance(block, dict) else None
                if call is not None and block.get("type") == "tool_result":
                    call["output"] = _result_text(block.get("content"))[:TOOL_OUTPUT_MAX]
                    if block.get("is_error"):
                        call["error"] = True
    if model_id is None and result is not None and isinstance(result.get("modelUsage"), dict) \
            and len(result["modelUsage"]) == 1:
        model_id = next(iter(result["modelUsage"]))
    return result, model_id


def _run(cmd: list[str], stdin: bytes, timeout: float, trace: list | None = None,
         require_mcp: str | None = None) -> dict:
    from .util import NO_WINDOW
    # MCP_TOOL_TIMEOUT: the first describe_sound may wait for the sound model to load (slow GPUs)
    env = {"MCP_TOOL_TIMEOUT": "600000", **os.environ,
           "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1", "ENABLE_TOOL_SEARCH": "false"}
    env.pop("CLAUDECODE", None)            # also runs when the GUI was started from inside Claude Code
    r = subprocess.run(cmd, input=stdin, capture_output=True, timeout=timeout, env=env, creationflags=NO_WINDOW)
    out = r.stdout.decode("utf-8", "replace")
    result, model_id = parse_stream(out, trace, require_mcp)
    if result is None:
        raise LLMError(f"no result (rc={r.returncode}): {r.stderr.decode('utf-8', 'replace')[:400]} {out[-400:]}")
    result["_model_id"] = model_id
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
        trace: list | None = None, prompt_version: str | int | None = None) -> dict:
    """Structured answer (dict matching ``schema``) for text/JPEG ``parts``.
    ``trace`` (a list) collects the tool calls the model made (see ``parse_stream``).
    ``prompt_version`` is the caller's version of its prompt and schema; it goes into the log
    as ``prompt`` ('<tag before the colon>/<version>') next to the model id the CLI reported."""
    prompt = None if prompt_version is None else f"{tag.split(':')[0] or 'call'}/{prompt_version}"
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
            _log(work, {"tag": tag, "model": model, "model_id": None, "prompt": prompt, "ok": False,
                        "error": str(e)[:300], "attempt": attempt})
            continue
        u = res.get("usage") or {}
        _log(work, {"tag": tag, "model": model, "model_id": res.get("_model_id"), "prompt": prompt,
                    "ok": res.get("subtype") == "success", "attempt": attempt,
                    "seconds": round(time.time() - t, 1), "turns": res.get("num_turns"),
                    "in": u.get("input_tokens"), "cache_create": u.get("cache_creation_input_tokens"),
                    "cache_read": u.get("cache_read_input_tokens"), "out": u.get("output_tokens"),
                    "cost_usd": res.get("total_cost_usd"), "subtype": res.get("subtype")})
        so = res.get("structured_output")
        if res.get("subtype") == "success" and isinstance(so, dict):
            return so
        last = LLMError(f"{res.get('subtype')}: {str(res.get('result'))[:300]}")
    raise LLMError(str(last))
