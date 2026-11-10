# -*- coding: utf-8 -*-
"""화면 근거 (5.5): for every sound candidate, can the picture alone tell that the sound was made?

장면 나누기 is TransNetV2 (cuts.json). 동작 확인 uses a vision-language model on frames
around the sound onset: Claude (``llm.ask``) sees 3 frames per candidate (just before
the onset, at the onset, ~0.9 s after; frames on both sides when a shot change falls
inside) and answers per candidate: visible 보임 (the frames alone, without the sound, tell that
this sound was made) / 안 보임 (they do not — also when what makes it is on screen: a wall clock
does not show its ticking) / 애매, a short observation ('컵이 깨지는 순간이 보임'), a visible
reaction of a character, and what on screen could have made the sound (the detected class can
be wrong). Netflix II.9: "Describe sound effects when they cannot be visually identified" (화면으로
알 수 없는 효과음을 설명한다, 감사 4-08). Candidates are batched (6 per call) and calls run in
parallel (cfg['jobs']).
Question 1 (PROMPT_VERSION 1) asked whether the source or the action was on screen. An answer
keeps the version it was asked with (``prompt_version`` of the item; none: question 1): a work
folder answered with question 1 is not asked again by a new version (each call costs) — the run
says how many such answers it keeps; remove screen.json to ask them all again.
Without an LLM (``--no-llm``) every candidate gets 애매 / '화면 확인 안 함' (source 'none').
``cfg["llm_scope"]``: 'all' (default) asks about every candidate — the observation is shown
to the writer and given to the agent. 'useful' asks only about candidates whose rule
recommendation depends on the answer (``recommend.worth_asking_in``: the candidates with the
script cues attached, in the script scenes; a sound the detector could not name stays 확인
필요 whatever the screen shows, 감사 6-06 — unless the script names it, then a visible source
excludes it); the others get source 'skipped' and are asked by a later run with 'all', or by
a later 'useful' run once their answer matters (the script or the alignment changed).
Output screen.json {"items": {candidate_id: {...}}}.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from .. import llm
from ..media import Media, jpeg_bytes
from ..util import fmt_time, log, read_json, write_json
from . import recommend

# raise when SYSTEM, SCHEMA or the layout of the question changes (llm_log 'prompt', items[].prompt_version)
# 2: 'can the picture alone tell that this sound was made', not 'is the source on screen' (감사 4-08)
PROMPT_VERSION = 2
BATCH = 6
WIDTH = 448
SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "visible": {"type": "string", "enum": ["보임", "안 보임", "애매"]},
            "observation": {"type": "string"},
            "reaction": {"type": "string"},
            "likely_source": {"type": "string"},
        },
        "required": ["id", "visible", "observation", "reaction", "likely_source"]}}},
    "required": ["items"],
}
SYSTEM = (
    "너는 청각장애인용(SDH) 자막 작가를 돕는 화면 분석 보조다. 소리 후보마다 그 순간의 영상 프레임을 보고, "
    "소리를 듣지 못하는 사람이 이 화면만 보고(소리 없이) 이 소리가 났다는 것을 알 수 있는지 판단한다. "
    "넷플릭스 SDH 지침은 화면으로 알 수 없는 소리를 자막에 적게 하기 때문이다. 소리를 낸 물체나 사람이 화면에 "
    "있어도 그 소리가 났다는 것이 화면에 드러나지 않으면 알 수 없는 것이다(예: 냉장고가 보여도 냉장고 돌아가는 "
    "소리, 잠든 사람이 보여도 코 고는 소리, 닫힌 문이 보여도 밖에서 문 두드리는 소리). visible은 소리를 내는 동작이나 "
    "그 결과가 프레임에 보여 화면만으로 이 소리가 났다는 것을 알 수 있으면 '보임'(예: 컵이 바닥에 떨어져 깨지는 순간, "
    "손뼉을 치는 손), 화면만으로는 알 수 없으면 '안 보임', 프레임으로 가리기 어려우면 '애매'다. 검출 종류는 틀릴 수 "
    "있다. 추측은 '애매'로 답한다. observation은 25자 안팎 한국어로 그렇게 본 까닭(예: '컵이 깨지는 순간이 보임', "
    "'냉장고는 보이나 소리는 알 수 없음'), reaction은 인물이 소리에 반응하는 모습이 보이면 짧게(없으면 빈 문자열), "
    "likely_source는 화면에서 그 소리를 냈을 법한 물체·동작(없으면 빈 문자열)."
)


def frame_times(c: dict, cuts: list[float]) -> list[float]:
    on = float(c["onset"])
    ts = [on - 0.25, on + 0.15, on + 0.9]
    cut = next((x for x in cuts if on - 0.3 <= x <= on + 1.0), None)
    if cut is not None:
        ts = [cut - 0.2, cut + 0.15, max(on + 0.9, cut + 0.6)]
    return ts


def _batch_call(batch: list[dict], m: Media, cuts: list[float], cfg: dict, work) -> dict:
    parts: list = ["아래 소리 후보마다 프레임 3장이 시간 순으로 있다. 후보마다 이 화면만 보고(소리 없이) 그 소리가 "
                   "났다는 것을 알 수 있는지 판단해 items에 같은 id로 답하라.\n"]
    for c in batch:
        lab = c["label"]
        name = "알 수 없는 소리" if lab.get("unknown") else f"{lab['ko']} (검출 점수 {lab['score']:.2f})"
        alts = ", ".join(t["ko"] for t in lab.get("top", [])[1:4])
        parts.append(f"\n[후보 {c['id']}] 시각 {fmt_time(c['onset'])}, 소리 길이 {max(c['t1'] - c['onset'], 0.1):.1f}초, "
                     f"검출 종류: {name}; 다른 후보 종류: {alts or '없음'}")
        for k, t in enumerate(frame_times(c, cuts)):
            fr = m.frames_at([t], max_width=WIDTH)[0]
            parts.append(f"{c['id']} 프레임 {k + 1} ({fmt_time(t)})")
            parts.append(jpeg_bytes(fr, 80))
    res = llm.ask(parts, SCHEMA, model=cfg.get("llm_model", "sonnet"), system=SYSTEM, effort="low",
                  work=work, tag="screen", prompt_version=PROMPT_VERSION)
    return {it["id"]: it for it in res.get("items", [])}


def _not_looked(c: dict, source: str) -> dict:
    return {"id": c["id"], "visible": "애매", "observation": "화면 확인 안 함", "reaction": "",
            "likely_source": "", "source": source}


def older_answers(items: dict) -> int:
    """How many model answers of a screen.json were asked with an older question than PROMPT_VERSION
    (an item without ``prompt_version`` was asked with question 1)."""
    return sum(1 for it in items.values() if it.get("source") == "vlm" and not it.get("failed")
               and int(it.get("prompt_version") or 1) < PROMPT_VERSION)


def run(p, cfg: dict) -> dict:
    sfx = (read_json(p.path("sfx.json"), {}) or {}).get("candidates", [])
    cuts = (read_json(p.path("cuts.json"), {}) or {}).get("cuts", [])
    old = read_json(p.path("screen.json"))
    older = older_answers(old.get("items") or {}) if old else 0
    if older:
        log.info("screen: %d answers were asked with an older question (before prompt %d); they are kept, not "
                 "asked again (remove screen.json to ask them again)", older, PROMPT_VERSION)
    use_llm = cfg.get("llm", True) and llm.available()
    scope = "useful" if cfg.get("llm_scope") == "useful" else "all"
    # the candidates as the recommendation sees them: with the script cues and the script scenes
    worth = recommend.worth_asking_in(p, sfx, "screen") if use_llm and scope == "useful" else set()
    if old is not None:
        # only candidates whose model call failed (or was skipped without an LLM, or left out by an
        # earlier run with llm_scope 'useful' when this one asks about all — or when the answer
        # now matters: the script, the alignment or the cues changed since) are asked again
        def again(c) -> bool:
            it = old["items"].get(c["id"])
            if it is None or it.get("failed"):
                return True
            return use_llm and (it.get("source") == "none" or (it.get("source") == "skipped"
                                                               and (scope != "useful" or c["id"] in worth)))
        retry = [c for c in sfx if again(c)]
        if not retry or not use_llm:
            return {"skipped": True, "failed_left": len(retry), "older_prompt": older}
        out = dict(old["items"])
        todo = retry
    else:
        out, todo = {}, sfx
    if not use_llm:
        for c in todo:
            out[c["id"]] = _not_looked(c, "none")
        write_json(p.path("screen.json"), {"items": out, "llm": False})
        return {"llm": False, "items": len(out)}
    not_asked = 0
    if scope == "useful":
        for c in todo:
            if c["id"] not in worth:
                out[c["id"]] = _not_looked(c, "skipped")
        not_asked = sum(1 for c in todo if c["id"] not in worth)
        todo = [c for c in todo if c["id"] in worth]
    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
    errors = 0
    with ThreadPoolExecutor(max_workers=max(1, int(cfg.get("jobs", 3)))) as ex:
        futs = [ex.submit(_batch_call, b, Media(p.meta, p.work), cuts, cfg, p.work) for b in batches]
        from ..util import progress
        for bi, (b, f) in enumerate(zip(batches, futs)):
            try:
                got = f.result()
            except Exception as e:                      # keep going; these candidates stay 애매
                log.warning("screen batch failed: %s", e)
                got, errors = {}, errors + 1
            progress("screen", (bi + 1) * BATCH if bi + 1 < len(batches) else len(todo), len(todo))
            for c in b:
                it = got.get(c["id"]) or {"id": c["id"], "visible": "애매", "observation": "화면 판단 실패",
                                          "reaction": "", "likely_source": "", "failed": True}
                it["source"] = "vlm"
                it["prompt_version"] = PROMPT_VERSION
                it["frames"] = [round(t, 3) for t in frame_times(c, cuts)]
                out[c["id"]] = it
            # saved after every batch: a stopped run keeps what was asked (the rest is asked next run)
            write_json(p.path("screen.json"), {"items": out, "llm": True, "errors": errors, "complete": False})
    write_json(p.path("screen.json"), {"items": out, "llm": True, "errors": errors, "complete": True,
                                       "scope": scope, "prompt_version": PROMPT_VERSION})
    cnt = {}
    for it in out.values():
        cnt[it["visible"]] = cnt.get(it["visible"], 0) + 1
    log.info("screen: %s (asked %d, not asked %d, batch errors %d)", cnt, len(todo), not_asked, errors)
    return {"visible": cnt, "errors": errors, "asked": len(todo), "not_asked": not_asked,
            "older_prompt": older_answers(out)}
