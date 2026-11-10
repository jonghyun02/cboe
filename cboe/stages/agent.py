# -*- coding: utf-8 -*-
"""재확인 에이전트 (5.8): only the candidates the rules could not settle.

For each queued candidate (근거 부족 · 서로 어긋남 · 종류 불확실 · 목록 밖 소리 · 음악 분위기 애매)
Claude runs with the MCP tools of ``agent_tools`` and decides by itself which to call:
소리 종류 → rescan_sound / describe_sound, 화면과 소리 → look_frames, 줄거리 → dialogue_around /
search_dialogue / scene_context. It stops when the evidence is enough or after the turn
limit (확인 한도) and returns updated evidence, a sound name if it found one, and its
judgement of 화면·줄거리. The recommendation is then decided again by the 5.7 rules with
that evidence (``recommend.build``); what cannot be settled stays 확인 필요 for the writer.
The writer decides include/exclude in the end; candidates are never deleted.

A sound the agent could name gets its caption put together here (``sound_phrases``) from the
answer's 의성어 (``ono``), 출처 명사 (``source_ko``) and 설명 (``label_ko``): '[쿵 - 책]', '[쿵]',
'[책 떨어지는 소리]' — the forms of 넷플릭스 II.9; the comma form '[쿵, 책 떨어지는 소리]' is no
longer made.

A re-check in which a tool raised and never answered (the sound description model out of
GPU memory, say) is not a finished re-check: it is kept as ``tool_error`` under "failed" in
agent_results.json, is not counted against ``agent_limit`` and is asked again by the next
run (감사 4-m1). After TOOL_ERROR_STOP such re-checks in a row the rest of the queue is not
asked in this run (it would be asked for nothing) and stays for the next one.

Writes agent/<id>.json (prompt, the tool calls with what each returned, answer),
agent_results.json, updates items.json.
"""
from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .. import llm, phrases
from ..agent_tools import DESCRIBE_PROMPT_VERSION, Tools, serve
from ..util import fmt_time, log, read_json, write_json
from . import recommend

PROMPT_VERSION = 2          # 2: source_ko added, no single example phrase (llm_log 'prompt' = agent/2)
TOOLS = ["rescan_sound", "describe_sound", "look_frames", "dialogue_around", "search_dialogue", "scene_context"]
MAX_TOOL_CALLS = 6          # 확인 한도 (told to the model)
MAX_TURNS = 14              # hard stop of the CLI; reaching it is an error that is retried next run
# A tool that raises for this many re-checks in a row is broken for this run (in one stored run the
# sound description model was out of GPU memory on all 19 calls): the rest of the queue is not asked
# now — every such re-check would be thrown away and asked again — and stays for the next run.
TOOL_ERROR_STOP = 3
NO_NAME = ("소리", "알 수 없는 소리", "효과음", "모름", "없음", "알 수 없음", "불명")
SYSTEM = (
    "너는 청각장애인용(SDH) 자막 작가를 돕는 재확인 에이전트다. 자동 판정이 애매했던 후보 하나를 도구로 다시 확인한다. "
    "기준: 화면으로 알 수 없고 줄거리에 필요한 소리만 자막에 넣는다(소리 낸 출처·동작이 화면에 보이거나 대사에 이미 "
    "적혔으면 뺀다). 애매한 이유에 맞는 도구를 골라 쓴다: 소리 종류가 불확실하면 rescan_sound와 describe_sound, 화면과 "
    "소리가 안 맞으면 look_frames, 줄거리 의미가 불명확하면 dialogue_around·search_dialogue·scene_context. 필요한 만큼만 "
    f"부르고(보통 2~5번, 많아야 {MAX_TOOL_CALLS}번), 근거가 충분하거나 한도에 닿으면 그때까지의 근거로 답한다. "
    "들리지 않는 소리를 만들어 내지 않는다. 소리 이름은 들은 것과 본 것으로 "
    "뒷받침될 때만 바꾸고, 그렇지 않으면 label_ok=false로 둔다. 최종 포함·제외는 작가가 정하므로, 너는 근거와 판단만 낸다. "
    "모든 글은 한국어로 짧게 쓴다."
)
SCHEMA = {
    "type": "object",
    "properties": {
        "label_ok": {"type": "boolean"},
        "label_ko": {"type": "string"},
        "ono": {"type": "string"},
        "source_ko": {"type": "string"},
        "mood": {"type": "string", "enum": [""] + list(phrases.MOODS)},
        "visible": {"type": "string", "enum": ["보임", "안 보임", "애매"]},
        "visible_note": {"type": "string"},
        "plot": {"type": "string", "enum": ["필요", "불필요", "모름"]},
        "plot_note": {"type": "string"},
        "recommendation": {"type": "string", "enum": ["include", "exclude", "check"]},
        "short_reason": {"type": "string"},
        "summary": {"type": "string"},
        "evidence": {"type": "array", "items": {
            "type": "object",
            "properties": {"kind": {"type": "string", "enum": ["소리", "화면", "대사", "맥락"]},
                           "t": {"type": "number"}, "text": {"type": "string"}},
            "required": ["kind", "t", "text"]}},
    },
    "required": ["label_ok", "label_ko", "ono", "source_ko", "mood", "visible", "visible_note", "plot", "plot_note",
                 "recommendation", "short_reason", "summary", "evidence"],
}


def priority(it: dict) -> float:
    base = {"sfx": 2.0, "music": 1.0}.get(it["kind"], 0.0)
    why = it.get("agent_reasons") or []
    if "목록에 없는 소리" in why or "소리 종류가 불확실" in why:
        base += 1.0
    return base + max(it.get("level_db", -60.0), -60.0) / 60.0


def prompt(it: dict) -> str:
    lines = [f"후보 {it['id']} ({'효과음' if it['kind'] == 'sfx' else '음악'}), 시각 {fmt_time(it['onset'])} "
             f"(초: {it['onset']:.2f}, 소리 끝 {it['t1']:.2f})",
             f"지금 문구: {it['phrase']}",
             f"애매한 이유: {', '.join(it.get('agent_reasons') or ['근거 부족'])}",
             "지금까지 모은 근거:"]
    for e in it.get("evidence", []):
        lines.append(f"  - {e['kind']} {fmt_time(e['t'])}: {e['text']}")
    if it["kind"] == "sfx":
        lab = it["label"]
        lines.append("검출 상위 종류: " + ", ".join(f"{t['ko']}({t['class']}) {t['score']:.2f}" for t in lab.get("top", [])))
        lines.append("답: label_ko에 소리 설명 문구(예: '천둥 소리', '유리 깨지는 소리'), 이름을 정할 근거가 없으면 빈 문자열. "
                     "ono에 의성어(예: '쿵', '쨍그랑'; 없으면 빈 문자열). source_ko에 소리를 낸 물건을 명사 하나로"
                     "(예: '문', '유리잔'; 모르면 빈 문자열) — 자막은 '[의성어 - 출처]' 꼴로 만들어진다. 예시는 형식만 "
                     "보인 것이니 그대로 쓰지 말고 이 후보에서 듣고 본 것을 적는다. mood는 빈 문자열. 같은 순간에 다른 "
                     "소리(예: 비명과 천둥)가 겹치면 이 후보가 어느 소리인지 summary에 적는다.")
    else:
        mus = it["music"]
        lines.append("분위기 후보: " + ", ".join(f"{m['ko']}({m['key']}) {m['score']:.2f}" for m in mus.get("moods", [])))
        lines.append("답: mood에 7가지 분위기 키 중 하나(모르겠으면 빈 문자열), label_ko·ono·source_ko는 빈 문자열, "
                     "visible은 '애매'(음악은 화면 기준을 쓰지 않음).")
    lines.append("evidence에는 도구로 새로 찾은 근거만 넣는다(t는 초 단위 시각). short_reason은 목록에 보일 20자 안팎 "
                 "이유(예: '출처 안 보임 · 종류 불확실'), summary는 근거를 정리한 두 문장 이내.")
    return "\n".join(lines)


def _word(s) -> str:
    """An answer field as caption text: no brackets, no outer blanks, '' for a placeholder."""
    s = " ".join(str(s or "").replace("[", " ").replace("]", " ").split())
    return "" if s in NO_NAME else s


def sound_phrases(desc: str, ono: str = "", source: str = "") -> list[dict]:
    """Caption options for a sound the agent named, the one to use first (넷플릭스 II.9: 의성어를
    먼저, '[바스락 - 종이]' 꼴): '[의성어 - 출처]' when both are given, '[의성어]', then '[설명]'.
    ``short`` (used where the cell has no room) is the 의성어 alone when there is one."""
    desc, ono = _word(desc), _word(ono)
    source = re.sub(r"\s*소리$", "", _word(source))      # the noun only: '문 소리' -> '문'
    if desc == ono:                                       # the description is the 의성어 itself
        desc = ""
    if source in (ono, desc):
        source = ""
    texts = []
    if ono and source:
        texts.append((f"[{ono} - {source}]", "에이전트 제안 (의성어 - 출처)"))
    if ono:
        texts.append((f"[{ono}]", "에이전트 제안 (의성어)"))
    if desc:
        texts.append((f"[{desc}]", "에이전트 제안 (설명)"))
    short = f"[{ono}]" if ono else (f"[{desc}]" if desc else "")
    return [{"text": t, "short": short, "source": "agent", "note": note} for t, note in texts]


def failed_tools(trace: list[dict]) -> list[str]:
    """Tools that raised in a re-check and never answered in it. Their evidence is missing, so the
    re-check is not finished; a tool that failed once and answered on another call is fine.
    ``trace`` rows carry ``error: True`` for a call that raised and ``output`` for what a call
    returned (``agent_tools.CallLog.claim``, ``llm.parse_stream``); a call nothing is known about
    is neither."""
    answered = {c.get("tool") for c in trace if "output" in c and not c.get("error")}
    return sorted({c.get("tool") for c in trace if c.get("error")} - answered)


def normalise(it: dict, ans: dict, trace: list[dict], seconds: float) -> dict:
    out = {"status": "done", "summary": ans.get("summary", ""), "short_reason": ans.get("short_reason", ""),
           "tools": [t["tool"] for t in trace],
           "steps": len(trace), "seconds": round(seconds, 1),
           "visible": ans.get("visible") if it["kind"] == "sfx" else None,
           "visible_note": ans.get("visible_note", ""), "plot": ans.get("plot"), "plot_note": ans.get("plot_note", ""),
           "label_ok": bool(ans.get("label_ok")), "recommendation": ans.get("recommendation")}
    ev = []
    for e in ans.get("evidence", [])[:6]:
        pol = "neutral"
        if e["kind"] == "화면":
            pol = "exclude" if ans.get("visible") == "보임" else ("include" if ans.get("visible") == "안 보임" else "neutral")
        elif e["kind"] in ("대사", "맥락") and ans.get("plot") == "필요":
            pol = "include"
        ev.append({"kind": "에이전트", "t": float(e.get("t", it["onset"])), "text": f"{e['kind']} · {e['text']}",
                   "polarity": pol, "source": "agent"})
    out["evidence"] = ev
    # a name for the sound needs the description; 의성어 or 출처 alone do not say what it is
    if it["kind"] == "sfx" and _word(ans.get("label_ko")):
        out["phrases"] = sound_phrases(ans.get("label_ko"), ans.get("ono"), ans.get("source_ko"))
    if it["kind"] == "music":
        mood = ans.get("mood") or ""
        out["mood"] = mood or None
        out["mood_ok"] = bool(mood)
        if mood:
            ph = phrases.mood_phrase(mood, it["music"].get("change"))
            out["phrases"] = [{"text": phrases.mood_phrase(mood), "short": phrases.mood_phrase(mood),
                               "source": "agent", "note": "에이전트 제안 분위기"}]
            if ph != out["phrases"][0]["text"]:
                out["phrases"].append({"text": ph, "short": phrases.mood_phrase(mood), "source": "agent",
                                       "note": "변화 + 분위기"})
    return out


def _fp(it: dict) -> str:
    return f"{it['kind']}@{float(it['onset']):.2f}"


def run(p, cfg: dict) -> dict:
    """Re-check the queue, continuing from earlier runs until ``agent_limit`` candidates in
    total have a finished result. Failed checks — the LLM call failed, or a tool raised and
    never answered — are retried on the next run and do not count against the limit; results
    whose candidate changed (fingerprint) are dropped. items.json is always rebuilt.
    agent_done.json: "ran" (asked in this run), "failed" (ids asked without a finished result),
    "tool_failed" (those of them a raising tool left unfinished), "stopped" (the queue was cut
    short after TOOL_ERROR_STOP of them in a row), "left" (ids not asked: over the limit, or
    after the stop)."""
    from ..util import gpu_lock
    items = recommend.build(p, {})                       # rule-only decisions make the queue
    fps = {it["id"]: _fp(it) for it in items}
    stored = (read_json(p.path("agent_results.json"), {}) or {}).get("items", {})
    previous = {k: v for k, v in stored.items()
                if v.get("status") == "done" and v.get("fp", fps.get(k)) == fps.get(k)}
    queue = sorted([it for it in items if it.get("agent_reasons") and it["id"] not in previous],
                   key=priority, reverse=True)
    limit = int(cfg.get("agent_limit", 40))
    budget = max(0, limit - len(previous))
    queue, rest = queue[:budget], queue[budget:]
    if not queue or not cfg.get("llm", True) or not llm.available():
        write_json(p.path("items.json"), {"items": recommend.build(p, previous)})
        write_json(p.path("agent_done.json"), {"ran": 0, "total": len(previous), "left": [it["id"] for it in rest],
                                               "reason": "no queue" if not queue else "no llm"})
        return {"ran": 0, "total": len(previous), "left": len(rest)}
    write_json(p.path("items.json"), {"items": recommend.build(p, previous)})   # tools see current state
    tools = Tools(p)
    url, server = serve(tools)
    log.info("agent: %d candidates (limit %d in total, %d done before, %d left as 확인 필요), tools at %s",
             len(queue), limit, len(previous), len(rest), url)
    results: dict[str, dict] = dict(previous)
    unfinished: dict[str, dict] = {}                      # this run's failed re-checks, for the record only
    not_asked: list[str] = []                             # queued, but skipped after a tool kept raising
    (p.path("agent")).mkdir(exist_ok=True)
    versions = {"prompt_version": PROMPT_VERSION, "describe_prompt_version": DESCRIBE_PROMPT_VERSION}
    broken = threading.Event()                            # set when a tool keeps raising (TOOL_ERROR_STOP)
    streak, streak_lock = [0], threading.Lock()           # re-checks in a row left unfinished by a raising tool

    def note(status: str) -> None:
        with streak_lock:
            streak[0] = streak[0] + 1 if status == "tool_error" else 0
            if streak[0] >= TOOL_ERROR_STOP and not broken.is_set():
                broken.set()
                log.warning("agent: a tool raised in %d re-checks in a row (%s); the rest of the queue is left for "
                            "the next run", streak[0], tools.log.errors())

    def one(it):
        if broken.is_set():                               # not asked in this run: left for the next one
            return it["id"], None
        trace: list[dict] = []
        t = time.time()
        q = prompt(it)
        try:
            ans = llm.ask([q], SCHEMA, model=cfg.get("llm_model", "sonnet"), system=SYSTEM, effort="low",
                          mcp_url=url, tools=TOOLS, max_turns=MAX_TURNS, timeout=900, work=p.work,
                          tag=f"agent:{it['id']}", trace=trace, prompt_version=PROMPT_VERSION)
        except Exception as e:
            tools.log.claim(trace, since=t)               # what the tools returned before the call failed
            write_json(p.path("agent") / f"{it['id']}.json", {"prompt": q, **versions, "error": str(e)[:500],
                                                              "trace": trace})
            note("error")
            return it["id"], {"status": "error", "summary": "재확인 실패", "tools": [x["tool"] for x in trace],
                              "steps": len(trace), "evidence": [], "fp": _fp(it)}
        tools.log.claim(trace, since=t)                   # each call's output, or the exception it raised
        res = normalise(it, ans, trace, time.time() - t)
        res["fp"] = _fp(it)
        bad = failed_tools(trace)
        if bad:                                           # answered without that tool: not a finished re-check
            res["status"] = "tool_error"
            res["tool_errors"] = [{"tool": c["tool"], "error": c.get("output")} for c in trace
                                  if c.get("error") and c["tool"] in bad]
        write_json(p.path("agent") / f"{it['id']}.json", {"prompt": q, **versions, "answer": ans, "trace": trace,
                                                          "result": res})
        note(res["status"])
        return it["id"], res

    def save(calls: dict, **more) -> None:
        write_json(p.path("agent_results.json"), {"items": results, "failed": unfinished, "tool_calls": calls, **more})

    try:
        # the sound description model stays on the GPU for the whole stage: hold the GPU lock
        from ..util import progress
        with gpu_lock(), ThreadPoolExecutor(max_workers=max(1, int(cfg.get("jobs", 3)))) as ex:
            try:
                if tools.slow_load():                     # ready before the first call
                    threading.Thread(target=tools.preload, daemon=True).start()
                for k, (iid, res) in enumerate(ex.map(one, queue), 1):
                    progress("agent", k, len(queue), iid)
                    if res is None:
                        not_asked.append(iid)
                        continue
                    if res.get("status") == "done":
                        results[iid] = res
                    else:
                        unfinished[iid] = {x: res[x] for x in ("status", "summary", "tools", "steps", "tool_errors",
                                                               "fp") if res.get(x) is not None}
                    log.info("agent %s%s: %s (%s)", iid, "" if res.get("status") == "done" else f" [{res.get('status')}]",
                             res.get("summary", "")[:60], ",".join(res.get("tools", [])))
                    save(dict(tools.calls))
            finally:
                tools.close()                             # still holding the GPU lock (waits for a preload)
    finally:
        tools.close()
        server.should_exit = True
    calls, raised = dict(tools.calls), tools.log.errors()
    save(calls, tool_errors=raised)
    new_items = recommend.build(p, results)
    write_json(p.path("items.json"), {"items": new_items})
    asked = [it["id"] for it in queue if it["id"] not in not_asked]
    failed = [i for i in asked if i not in results]
    tool_failed = [i for i in failed if unfinished.get(i, {}).get("status") == "tool_error"]
    if tool_failed:
        log.warning("agent: %d re-checks left unfinished because a tool raised (%s); they are asked again by the next "
                    "run and do not count against the limit", len(tool_failed), raised)
    write_json(p.path("agent_done.json"), {"ran": len(asked), "total": len(results), "failed": failed,
                                           "tool_failed": tool_failed, "stopped": broken.is_set(),
                                           "left": not_asked + [it["id"] for it in rest],
                                           "tool_calls": calls, "tool_errors": raised})
    changed = sum(1 for a, b in zip(items, new_items) if a.get("rec") != b.get("rec"))
    return {"ran": len(asked), "failed": len(failed), "tool_failed": len(tool_failed), "stopped": broken.is_set(),
            "tool_calls": calls, "rec_changed": changed}
