# -*- coding: utf-8 -*-
"""화면 근거 (5.5): for every sound candidate, is the source / the action that made it on screen?

장면 나누기 is TransNetV2 (cuts.json). 동작 확인 uses a vision-language model on frames
around the sound onset: Claude (``llm.ask``) sees 3 frames per candidate (just before
the onset, at the onset, ~0.9 s after; frames on both sides when a shot change falls
inside) and answers per candidate: visible 보임 / 안 보임 / 애매, a short observation
('걷는 모습이 보임'), a visible reaction of a character, and what on screen could have
made the sound (the detected class can be wrong). Candidates are batched (6 per call)
and calls run in parallel (cfg['jobs']).
Without an LLM (``--no-llm``) every candidate gets 애매 / '화면 확인 안 함'.
Output screen.json {"items": {candidate_id: {...}}}.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from .. import llm
from ..media import Media, jpeg_bytes
from ..util import fmt_time, log, read_json, write_json

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
    "소리를 낸 출처나 동작이 화면에 보이는지 판단한다. 넷플릭스 SDH 지침처럼 화면으로 알 수 있는 소리는 "
    "자막에서 빼고, 화면으로 알 수 없는 소리만 넣기 때문이다. 검출 종류는 틀릴 수 있다. 추측은 '애매'로 답한다. "
    "observation은 25자 안팎 한국어(예: '걷는 모습이 보임', '어두운 서가, 출처 안 보임'), reaction은 인물이 소리에 "
    "반응하는 모습이 보이면 짧게(없으면 빈 문자열), likely_source는 화면에서 그 소리를 냈을 법한 물체·동작(없으면 빈 문자열)."
)


def frame_times(c: dict, cuts: list[float]) -> list[float]:
    on = float(c["onset"])
    ts = [on - 0.25, on + 0.15, on + 0.9]
    cut = next((x for x in cuts if on - 0.3 <= x <= on + 1.0), None)
    if cut is not None:
        ts = [cut - 0.2, cut + 0.15, max(on + 0.9, cut + 0.6)]
    return ts


def _batch_call(batch: list[dict], m: Media, cuts: list[float], cfg: dict, work) -> dict:
    parts: list = ["아래 소리 후보마다 프레임 3장이 시간 순으로 있다. 후보마다 판단해 items에 같은 id로 답하라.\n"]
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
                  work=work, tag="screen")
    return {it["id"]: it for it in res.get("items", [])}


def run(p, cfg: dict) -> dict:
    sfx = (read_json(p.path("sfx.json"), {}) or {}).get("candidates", [])
    cuts = (read_json(p.path("cuts.json"), {}) or {}).get("cuts", [])
    old = read_json(p.path("screen.json"))
    use_llm = cfg.get("llm", True) and llm.available()
    if old is not None:
        # only candidates whose model call failed (or was skipped without an LLM) are asked again
        retry = [c for c in sfx if c["id"] not in old["items"] or (old["items"].get(c["id"]) or {}).get("failed")
                 or (use_llm and (old["items"].get(c["id"]) or {}).get("source") == "none")]
        if not retry or not use_llm:
            return {"skipped": True, "failed_left": len(retry)}
        out = dict(old["items"])
        todo = retry
    else:
        out, todo = {}, sfx
    if not use_llm:
        for c in todo:
            out[c["id"]] = {"id": c["id"], "visible": "애매", "observation": "화면 확인 안 함", "reaction": "",
                            "likely_source": "", "source": "none"}
        write_json(p.path("screen.json"), {"items": out, "llm": False})
        return {"llm": False, "items": len(out)}
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
                it["frames"] = [round(t, 3) for t in frame_times(c, cuts)]
                out[c["id"]] = it
            # saved after every batch: a stopped run keeps what was asked (the rest is asked next run)
            write_json(p.path("screen.json"), {"items": out, "llm": True, "errors": errors, "complete": False})
    write_json(p.path("screen.json"), {"items": out, "llm": True, "errors": errors, "complete": True})
    cnt = {}
    for it in out.values():
        cnt[it["visible"]] = cnt.get(it["visible"], 0) + 1
    log.info("screen: %s (batch errors %d)", cnt, errors)
    return {"visible": cnt, "errors": errors}
