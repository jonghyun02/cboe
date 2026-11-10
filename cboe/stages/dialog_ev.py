# -*- coding: utf-8 -*-
"""대사 근거: what the dialogue around a sound says about it (반응·언급 찾기).

For every sound-effect candidate the dialogue lines from 6 s before to 6 s after the onset
are read (music candidates are not asked about: no rule reads a dialogue answer for music —
96 answers of the 21-minute run were all 'none', 감사 5-11 · 6-06). Lines without a time
(not in the video, outside the processed part) are not read.
- already: the sound is already in the dialogue text (a scream written '으아아아아',
  a spoken onomatopoeia '똑똑') → 이미 전해지는 정보
- reaction: a line reacts to it ('무슨 소리야?', '누구세요?', a scream right after a crash)
- mention: a line names the source ('전화 왔다', '비 온다')
- none
plus a short plot hint (사건·위험·단서) when the lines show one.
LLM: Claude, text only, 20 candidates per call. Without an LLM a keyword rule gives
'already' for vocal sounds on vocalisation lines and 'reaction' for typical reaction
phrases. ``cfg["llm_scope"]``: 'all' (default) asks about every candidate; 'useful' asks
only where the rule recommendation depends on the answer (``recommend.worth_asking``, with
the screen answers that are already there) — the others keep the keyword rule's answer,
marked ``skipped``, and are asked by a later run with 'all'.
Output dialog_ev.json {"items": {candidate_id: {...}}}.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

from .. import llm
from ..util import fmt_time, log, read_json, write_json
from . import recommend
from .align import is_vocalisation

PROMPT_VERSION = 1          # raise when SYSTEM, SCHEMA or the layout of the question changes (llm_log 'prompt')
BEFORE, AFTER = 6.0, 6.0
BATCH = 20
REACTION = re.compile(r"무슨 소리|뭐야|뭐지|누구(세요|야)?|깜짝|들렸|들려|소리|왜 이래|어떡해|살려|으악|꺅|헉|허억")
SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "relation": {"type": "string", "enum": ["already", "reaction", "mention", "none"]},
            "line_id": {"type": "string"},
            "evidence": {"type": "string"},
            "plot_hint": {"type": "string"},
        },
        "required": ["id", "relation", "line_id", "evidence", "plot_hint"]}}},
    "required": ["items"],
}
SYSTEM = (
    "너는 청각장애인용(SDH) 자막 작가를 돕는 대사 분석 보조다. 소리 후보마다 앞뒤 대사를 읽고 그 소리와 대사의 관계를 "
    "고른다. already: 그 소리가 대사 글에 이미 적혀 있음(비명이 '으아아아'로 적힘, 의성어를 말함). reaction: 대사가 "
    "그 소리에 반응함('무슨 소리야?', '누구세요?', 큰 소리 직후 비명). mention: 대사가 소리의 출처를 말함('전화 왔다'). "
    "none: 관계 없음. line_id는 근거 대사의 id(없으면 빈 문자열), evidence는 20자 안팎 한국어 근거(예: '직후 비명', "
    "'\"무슨 소리 안 들렸어요?\"'), plot_hint는 대사로 보아 이 소리가 사건·위험·단서와 관련 있으면 짧게(없으면 빈 문자열). "
    "검출 종류는 틀릴 수 있다."
)


def window_lines(lines: list[dict], t: float) -> list[dict]:
    return [ln for ln in lines if recommend.has_time(ln) and ln["end"] >= t - BEFORE and ln["start"] <= t + AFTER]


def rule(c: dict, lines: list[dict]) -> dict:
    t = c["onset"]
    near = window_lines(lines, t)
    if c.get("kind") == "sfx" and c.get("category") == "vocal":
        for ln in near:
            if abs(ln["start"] - t) <= 1.0 and is_vocalisation(ln["text"]):
                return {"id": c["id"], "relation": "already", "line_id": ln["id"], "evidence": f"대사에 '{ln['text']}'",
                        "plot_hint": "", "source": "rule"}
    for ln in near:
        if 0 <= ln["start"] - t <= 4.0 and REACTION.search(ln["text"]):
            return {"id": c["id"], "relation": "reaction", "line_id": ln["id"],
                    "evidence": f"직후 \"{ln['text'][:14]}\"", "plot_hint": "", "source": "rule"}
    return {"id": c["id"], "relation": "none", "line_id": "", "evidence": "", "plot_hint": "", "source": "rule"}


def _batch_call(batch: list[dict], lines: list[dict], cfg: dict, work) -> dict:
    parts = ["아래 소리 후보마다 앞뒤 6초 대사가 있다(+는 소리 뒤, -는 소리 앞, 초). 후보마다 items에 같은 id로 답하라.\n"]
    for c in batch:
        lab = c["label"]
        name = "알 수 없는 소리" if lab.get("unknown") else lab["ko"]
        rows = [f"  {ln['id']} {ln['start'] - c['onset']:+.1f}s {ln.get('speaker') or '?'}: {ln['text']}"
                for ln in window_lines(lines, c["onset"])]
        parts.append(f"\n[후보 {c['id']}] {fmt_time(c['onset'])} {name}\n" + ("\n".join(rows) if rows else "  (대사 없음)"))
    res = llm.ask(["\n".join(parts)], SCHEMA, model=cfg.get("llm_model", "sonnet"), system=SYSTEM, effort="low",
                  work=work, tag="dialog", prompt_version=PROMPT_VERSION)
    return {it["id"]: it for it in res.get("items", [])}


def run(p, cfg: dict) -> dict:
    lines = (read_json(p.path("dialogue.json"), {}) or {}).get("lines", [])
    cands = (read_json(p.path("sfx.json"), {}) or {}).get("candidates", [])      # sound effects only (see above)
    used_llm = cfg.get("llm", True) and llm.available()
    scope = "useful" if cfg.get("llm_scope") == "useful" else "all"
    old = read_json(p.path("dialog_ev.json"))
    if old is not None:
        # only candidates the model did not answer: a failed batch, an earlier run without LLM, or
        # (when this run asks about all) one that an earlier run with llm_scope 'useful' left out
        def again(c) -> bool:
            it = old["items"].get(c["id"])
            if it is None:
                return True
            if it.get("source") == "llm":
                return False
            if it.get("skipped"):
                return scope != "useful"
            return bool(it.get("failed", not old.get("llm")))
        todo = [c for c in cands if again(c)]
        if not todo or not used_llm:
            return {"skipped": True, "failed_left": len(todo)}
        out = dict(old["items"])
    else:
        todo = cands
        out = {c["id"]: rule(c, lines) for c in cands}
    errors = 0
    not_asked = 0
    if used_llm and scope == "useful":
        cuts = (read_json(p.path("cuts.json"), {}) or {}).get("cuts", [])
        seen = (read_json(p.path("screen.json"), {}) or {}).get("items", {})
        worth = recommend.worth_asking(cands, cuts, "dialog", seen)
        for c in todo:
            if c["id"] not in worth:                # the answer cannot change the recommendation
                out[c["id"]] = {**rule(c, lines), "skipped": True}
        not_asked = sum(1 for c in todo if c["id"] not in worth)
        todo = [c for c in todo if c["id"] in worth]
    if used_llm:
        for c in todo:                              # not asked yet: a stopped run asks these again
            if c["id"] not in out or out[c["id"]].get("source") != "llm":
                out[c["id"]] = {**rule(c, lines), "failed": True}
        batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
        with ThreadPoolExecutor(max_workers=max(1, int(cfg.get("jobs", 3)))) as ex:
            futs = [ex.submit(_batch_call, b, lines, cfg, p.work) for b in batches]
            from ..util import progress
            for bi, (b, f) in enumerate(zip(batches, futs)):
                try:
                    got = f.result()
                except Exception as e:
                    log.warning("dialog batch failed: %s", e)
                    got, errors = {}, errors + 1
                progress("dialog_ev", (bi + 1) * BATCH if bi + 1 < len(batches) else len(todo), len(todo))
                for c in b:
                    if c["id"] not in got:          # keep the rule answer, ask again next run
                        out[c["id"]] = {**rule(c, lines), "failed": True}
                        continue
                    if c["id"] in got:
                        it = got[c["id"]]
                        it["source"] = "llm"
                        # keep a rule 'already' for vocal sounds the model missed
                        if out[c["id"]]["relation"] == "already" and it["relation"] == "none":
                            out[c["id"]] = {k: v for k, v in out[c["id"]].items() if k != "failed"}
                            continue
                        out[c["id"]] = it
                write_json(p.path("dialog_ev.json"), {"items": out, "llm": used_llm, "errors": errors, "complete": False})
    write_json(p.path("dialog_ev.json"), {"items": out, "llm": used_llm, "errors": errors, "complete": True,
                                          "scope": scope, "prompt_version": PROMPT_VERSION})
    cnt = {}
    for it in out.values():
        cnt[it["relation"]] = cnt.get(it["relation"], 0) + 1
    asked = len(todo) if used_llm else 0
    log.info("dialog evidence: %s (asked %d, not asked %d)", cnt, asked, not_asked)
    return {"relations": cnt, "llm": used_llm, "asked": asked, "not_asked": not_asked}
