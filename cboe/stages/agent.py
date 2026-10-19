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

Writes agent/<id>.json (prompt, tool calls, answer), agent_results.json, updates items.json.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from .. import llm, phrases
from ..agent_tools import Tools, serve
from ..util import fmt_time, log, read_json, write_json
from . import recommend

TOOLS = ["rescan_sound", "describe_sound", "look_frames", "dialogue_around", "search_dialogue", "scene_context"]
MAX_TOOL_CALLS = 6          # 확인 한도 (told to the model)
MAX_TURNS = 14              # hard stop of the CLI; reaching it is an error that is retried next run
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
    "required": ["label_ok", "label_ko", "ono", "mood", "visible", "visible_note", "plot", "plot_note",
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
        lines.append("답: label_ko에 소리 설명 문구(예: '책 떨어지는 소리', 소리 이름만이면 '천둥 소리'), 이름을 정할 근거가 "
                     "없으면 빈 문자열. ono에 의성어(예: '쿵', 없으면 빈 문자열), mood는 빈 문자열. 같은 순간에 다른 소리"
                     "(예: 비명과 천둥)가 겹치면 이 후보가 어느 소리인지 summary에 적는다.")
    else:
        mus = it["music"]
        lines.append("분위기 후보: " + ", ".join(f"{m['ko']}({m['key']}) {m['score']:.2f}" for m in mus.get("moods", [])))
        lines.append("답: mood에 7가지 분위기 키 중 하나(모르겠으면 빈 문자열), label_ko·ono는 빈 문자열, "
                     "visible은 '애매'(음악은 화면 기준을 쓰지 않음).")
    lines.append("evidence에는 도구로 새로 찾은 근거만 넣는다(t는 초 단위 시각). short_reason은 목록에 보일 20자 안팎 "
                 "이유(예: '출처 안 보임 · 종류 불확실'), summary는 근거를 정리한 두 문장 이내.")
    return "\n".join(lines)


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
    desc = (ans.get("label_ko") or "").strip().strip("[]").strip()
    if desc in ("소리", "알 수 없는 소리", "효과음", "모름"):
        desc = ""
    if it["kind"] == "sfx" and desc:
        ono = (ans.get("ono") or "").strip().strip("[]")
        long = f"[{ono}, {desc}]" if ono and ono not in desc else f"[{desc}]"
        short = f"[{ono}]" if ono else long
        out["phrases"] = [{"text": long, "short": short, "source": "agent", "note": "에이전트 제안 (소리 설명 모델·화면)"}]
        if long != f"[{desc}]":
            out["phrases"].append({"text": f"[{desc}]", "short": short, "source": "agent", "note": "에이전트 제안"})
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
    total have a finished result. Failed checks are retried on the next run; results whose
    candidate changed (fingerprint) are dropped. items.json is always rebuilt."""
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
    (p.path("agent")).mkdir(exist_ok=True)

    def one(it):
        trace: list[dict] = []
        t = time.time()
        q = prompt(it)
        try:
            ans = llm.ask([q], SCHEMA, model=cfg.get("llm_model", "sonnet"), system=SYSTEM, effort="low",
                          mcp_url=url, tools=TOOLS, max_turns=MAX_TURNS, timeout=900, work=p.work,
                          tag=f"agent:{it['id']}", trace=trace)
        except Exception as e:
            write_json(p.path("agent") / f"{it['id']}.json", {"prompt": q, "error": str(e)[:500], "trace": trace})
            return it["id"], {"status": "error", "summary": "재확인 실패", "tools": [x["tool"] for x in trace],
                              "steps": len(trace), "evidence": []}
        res = normalise(it, ans, trace, time.time() - t)
        res["fp"] = _fp(it)
        write_json(p.path("agent") / f"{it['id']}.json", {"prompt": q, "answer": ans, "trace": trace, "result": res})
        return it["id"], res

    try:
        # the sound description model stays on the GPU for the whole stage: hold the GPU lock
        from ..util import progress
        with gpu_lock(), ThreadPoolExecutor(max_workers=max(1, int(cfg.get("jobs", 3)))) as ex:
            for k, (iid, res) in enumerate(ex.map(one, queue), 1):
                progress("agent", k, len(queue), iid)
                if res.get("status") == "done":
                    results[iid] = res
                log.info("agent %s: %s (%s)", iid, res.get("summary", "")[:60], ",".join(res.get("tools", [])))
                write_json(p.path("agent_results.json"), {"items": results, "tool_calls": dict(tools.calls)})
    finally:
        tools.close()
        server.should_exit = True
    calls = dict(tools.calls)
    write_json(p.path("agent_results.json"), {"items": results, "tool_calls": calls})
    new_items = recommend.build(p, results)
    write_json(p.path("items.json"), {"items": new_items})
    failed = [it["id"] for it in queue if it["id"] not in results]
    write_json(p.path("agent_done.json"), {"ran": len(queue), "total": len(results), "failed": failed,
                                           "left": [it["id"] for it in rest], "tool_calls": calls})
    changed = sum(1 for a, b in zip(items, new_items) if a.get("rec") != b.get("rec"))
    return {"ran": len(queue), "failed": len(failed), "tool_calls": calls, "rec_changed": changed}
