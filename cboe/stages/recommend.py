# -*- coding: utf-8 -*-
"""추천 (5.7): every candidate gets 포함 추천 / 제외 추천 / 확인 필요 with its evidence.

효과음, in this order (deck 5.7):
1. 이미 전해지는 정보인가? (화면에 출처·동작이 보임 · 대사에 이미 적힘 · 같은 소리를 앞 자막이
   이미 알림) → 제외 추천
2. 넣을 근거가 있나? (출처가 화면에 안 보이고, 종류가 확실하고, 인물 반응 · 사건 · 위험 · 단서가
   있음: 대사 반응/언급, 화면 속 반응, 갑자기 큰 사건 소리, 장면 첫 배경음) → 포함 추천
3. 그 밖(근거 부족 · 서로 어긋남 · 종류 불확실 · 목록 밖 소리) → 재확인 에이전트 → 그래도
   모르면 확인 필요
음악: 시작·새 음악·분위기 바뀜만 후보. 같은 분위기 음악이 이어지면 제외, 분위기가 확실하면
포함, 애매하면 에이전트 → 확인 필요('♬ 음악').
화자: 대본 줄마다. 말하는 얼굴이 보이면 이름 생략(제외), 안 보이면 이름 표시(포함), 애매하거나
대본에 화자가 없으면 확인 필요.
Candidates are never dropped. ``decide()`` is re-run after the agent adds evidence.
"""
from __future__ import annotations

from .. import phrases
from ..util import log, read_json, write_json

SAME_SOUND_S = 20.0
LOUD_JUMP_DB = 15.0
MUSIC_CONF, MUSIC_MARGIN = 0.40, 0.12
MUSIC_SAME_S = 90.0
MUSIC_CUE_SAME_S = 20.0
OWN_SOUND_FAMILIES = {"laughter", "cough", "sneeze", "breath", "snore", "hum_voice", "whistle_voice"}


# ------------------------------------------------------------------ helpers
def ev(kind, t, text, polarity="neutral", source="rule", t1=None) -> dict:
    e = {"kind": kind, "t": round(float(t), 3), "text": text, "polarity": polarity, "source": source}
    if t1 is not None:
        e["t1"] = round(float(t1), 3)
    return e


def music_candidates(p) -> list[dict]:
    mj = read_json(p.path("music.json"), {}) or {}
    items = mj.get("items", [])
    out = []
    for i, m in enumerate(items, 1):
        nxt = items[i]["onset"] if i < len(items) else None
        t1 = min(m["segment"][1], nxt) if nxt else m["segment"][1]
        moods = m["moods"]
        top = moods[0] if moods else None
        second = moods[1]["score"] if len(moods) > 1 else 0.0
        confident = bool(top and top["score"] >= MUSIC_CONF and top["score"] - second >= MUSIC_MARGIN
                         and m.get("mood_strength", 1) >= 0.004)
        opts = []
        if top:
            opts.append({"text": phrases.mood_phrase(top["key"]), "short": phrases.mood_phrase(top["key"]),
                         "source": "mood", "note": "분위기만"})
            if m.get("change"):
                opts.append({"text": phrases.mood_phrase(top["key"], m["change"]),
                             "short": phrases.mood_phrase(top["key"]), "source": "mood", "note": "변화 + 분위기"})
            inst = next((x for x in m.get("instruments", []) if x["score"] >= 0.1), None)
            if inst:
                txt = phrases.mood_phrase(top["key"], None, inst["key"])
                opts.append({"text": txt, "short": txt, "source": "instrument", "note": "분위기 + 악기"})
        opts.append({"text": "♬ 음악", "short": "♬ 음악", "source": "mood", "note": "분위기 없이"})
        default = opts[0] if confident else opts[-1]
        out.append({"id": f"M{i:04d}", "kind": "music", "t0": m["onset"], "t1": round(t1, 3), "onset": m["onset"],
                    "category": "music",
                    "label": {"class": "Music", "ko": "음악", "score": m.get("music_score", 0.0),
                              "confident": confident},
                    "music": {"moods": moods[:3], "change": m.get("change"), "instruments": m.get("instruments", []),
                              "segment": m["segment"], "event": m["kind"], "strength": m.get("mood_strength")},
                    "phrases": opts, "phrase": default["text"], "short": default["short"], "flags": []})
    return out


def speaker_candidates(p) -> list[dict]:
    lines = (read_json(p.path("dialogue.json"), {}) or {}).get("lines", [])
    sp = {o["line_id"]: o for o in (read_json(p.path("speaker.json"), {}) or {}).get("lines", [])}
    out = []
    for ln in lines:
        o = sp.get(ln["id"], {"face": "unclear", "n_faces": 0, "best": None, "name_revealed": None})
        b = o.get("best") or {}
        out.append({"id": "P" + ln["id"][1:], "kind": "speaker", "t0": ln["start"], "t1": ln["end"],
                    "onset": ln["start"], "category": "speaker",
                    "label": {"class": "speaker", "ko": ln.get("speaker") or "화자 미상", "score": b.get("max_score")},
                    "speaker": {"line_id": ln["id"], "name": ln.get("speaker"), "face": o["face"],
                                "n_faces": o.get("n_faces", 0), "asd": b.get("max_score"),
                                "speaking_frames": b.get("speaking_frames"), "name_revealed": o.get("name_revealed"),
                                "timing": ln.get("timing")},
                    "phrases": [], "phrase": ln["text"], "short": ln["text"], "flags": []})
    return out


# ----------------------------------------------------------------- decisions
def sfx_evidence(c: dict, screen: dict | None, dia: dict | None) -> list[dict]:
    lab = c["label"]
    out = []
    if lab.get("unknown"):
        out.append(ev("소리", c["onset"], f"목록에 없는 소리 (갑자기 {c.get('level_jump_db', 0):.0f}dB 커짐)", "neutral", "sed"))
    else:
        path = " → ".join(lab.get("path_ko", [])[:3])
        txt = f"검출: {lab['ko']} {lab['score']:.2f}" + (f" ({path})" if len(lab.get("path_ko", [])) > 1 else "")
        if lab.get("competing"):
            txt += " · 경쟁: " + ", ".join(f"{x['ko']} {x['score']:.2f}" for x in lab["competing"][:2])
        out.append(ev("소리", c["onset"], txt, "neutral", "sed", c["t1"]))
    if screen:
        pol = {"보임": "exclude", "안 보임": "include"}.get(screen.get("visible"), "neutral")
        t = (screen.get("frames") or [c["onset"]])[1 if len(screen.get("frames") or []) > 1 else 0]
        out.append(ev("화면", t, screen.get("observation") or screen.get("visible", ""), pol, screen.get("source", "vlm")))
        if screen.get("reaction"):
            out.append(ev("화면", t, f"반응: {screen['reaction']}", "include", screen.get("source", "vlm")))
    if dia and dia.get("relation") != "none":
        pol = {"already": "exclude", "reaction": "include", "mention": "include"}.get(dia["relation"], "neutral")
        out.append(ev("대사", c["onset"], dia.get("evidence") or dia["relation"], pol, dia.get("source", "llm")))
    if dia and dia.get("plot_hint"):
        out.append(ev("맥락", c["onset"], dia["plot_hint"], "include", dia.get("source", "llm")))
    return out


def _short(text: str, n: int = 24) -> str:
    text = (text or "").strip().rstrip(".")
    return text if len(text) <= n else text[:n].rstrip() + "…"


def decide_sfx(c: dict, screen: dict | None, dia: dict | None, prev_same: dict | None,
               agent: dict | None = None) -> tuple[str, str, list[str]]:
    """(rec, reason, agent_reasons)."""
    lab = c["label"]
    vis = (agent or {}).get("visible") or (screen or {}).get("visible", "애매")
    rel = (dia or {}).get("relation", "none")
    confident = lab.get("confident") and not lab.get("unknown") and not lab.get("competing")
    if agent and agent.get("label_ok"):
        confident = True
    named = confident or any(p.get("source") == "agent" and p["text"] not in ("[소리]", "") for p in
                             (agent or {}).get("phrases") or [])
    # 1. 이미 전해지는 정보인가 — only when we know which sound it is: for an unnamed sound
    #    the visible person or the written scream may belong to another sound at the same time
    already, mixed = [], []
    if vis == "보임":
        obs = (agent or {}).get("visible_note") or (screen or {}).get("observation") or "소리 낸 동작이 보임"
        (already if named else mixed).append(_short(obs) if len(obs) <= 24 else "소리 낸 동작이 보임")
    if rel == "already":
        (already if named else mixed).append("대사에 이미 적힘")
    if prev_same is not None:
        already.append("앞 자막에서 이미 알림")
    if already:
        return "exclude", " · ".join(already), []
    # 2. 넣을 근거가 있나
    why = []
    if rel in ("reaction", "mention"):
        why.append(_short((dia or {}).get("evidence") or "대사 반응", 20))
    if (screen or {}).get("reaction"):
        why.append("인물 반응")
    if (dia or {}).get("plot_hint"):
        why.append("줄거리 단서")
    if c.get("category") == "event" and c.get("level_jump_db", 0) >= LOUD_JUMP_DB:
        why.append("갑자기 큰 소리")
    if c.get("category") == "ambient" and c.get("scene_first"):
        why.append("장면 첫 배경 소리")
    if agent and agent.get("plot") == "필요":
        why.append("줄거리에 필요")
    # deck 5.7: not already conveyed + a reason to include -> 포함 추천 (a dark / unclear screen
    # does not convey the sound either; the writer sees '화면 애매' in the reason)
    if why and not mixed and vis in ("안 보임", "애매"):
        head = "화면에 출처 없음" if vis == "안 보임" else "화면 애매"
        if confident:
            return "include", head + " · " + " · ".join(dict.fromkeys(why)), []
        if named and agent and agent.get("plot") == "필요":
            return "include", head + " · " + " · ".join(dict.fromkeys(why)) + " · 문구 확인", []
    if agent and agent.get("plot") == "불필요" and vis != "애매":
        return "exclude", _short(agent.get("plot_note") or "줄거리와 관계 없음", 30), []
    # 3. 근거 부족 · 서로 어긋남
    reasons = []
    if lab.get("unknown"):
        reasons.append("목록에 없는 소리")
    elif not confident:
        reasons.append("소리 종류가 불확실")
    if mixed:
        reasons.append("출처 판단 어긋남")
    if vis == "애매":
        reasons.append("화면 판단 애매")
    if vis == "안 보임" and not why:
        reasons.append("넣을 근거 부족")
    return "check", " · ".join(reasons) or "근거 부족", reasons


def decide_music(m: dict, prev: dict | None, agent: dict | None = None) -> tuple[str, str, list[str]]:
    mus = m["music"]
    top = (mus.get("moods") or [None])[0]
    confident = m["label"]["confident"] or bool(agent and agent.get("mood_ok"))
    if prev is not None:
        return "exclude", "같은 분위기 음악이 이어짐 · 앞 자막에서 이미 알림", []
    what = {"start": "음악 시작", "cue": "새 음악", "mood_change": "분위기 바뀜"}.get(mus.get("event"), "음악")
    if confident and top:
        return "include", f"{what} · 분위기 1위 '{top['ko']}'", []
    return "check", f"{what} · 분위기 판단 애매", ["음악 분위기 애매"]


def decide_speaker(s: dict, prev: dict | None) -> tuple[str, str, list[str]]:
    sp = s["speaker"]
    if sp["face"] == "visible":
        return "exclude", "말하는 얼굴이 보임", []
    if not sp.get("name"):
        return "check", "말하는 얼굴 " + ("안 보임" if sp["face"] == "not_visible" else "판정 애매") + " · 대본에 화자 없음", []
    if sp["face"] == "not_visible":
        if prev is not None:
            return "exclude", "같은 화자가 이어 말함 · 앞 줄에 이름 표시", []
        return "include", "말하는 얼굴 안 보임", []
    return "check", "말하는 얼굴 판정 애매", []


def speaker_evidence(s: dict) -> list[dict]:
    sp = s["speaker"]
    txt = {"visible": "말하는 얼굴이 보임", "not_visible": "말하는 얼굴 안 보임" if not sp.get("n_faces")
           else f"얼굴 {sp['n_faces']}개 보이지만 입이 움직이지 않음", "unclear": "말하는 얼굴 판정 애매"}[sp["face"]]
    pol = {"visible": "exclude", "not_visible": "include", "unclear": "neutral"}[sp["face"]]
    out = [ev("화면", s["onset"], txt, pol, "asd", s["onset"] + 1.0)]
    out.append(ev("대본", s["onset"], f"이 대사는 {sp['name']}" if sp.get("name") else "대본에 화자 없음", "neutral", "script"))
    if sp.get("timing") not in ("asr", "script", None):
        out.append(ev("대본", s["onset"], "대사 시각 추정 (받아쓰기로 못 맞춤)", "neutral", "align"))
    if sp.get("name") and sp.get("name_revealed") is False:
        out.append(ev("대본", s["onset"], "이름이 아직 대사에 나오지 않음", "neutral", "script"))
    return out


# ---------------------------------------------------------------------- run
def build(p, agent_results: dict | None = None) -> list[dict]:
    agent_results = agent_results or {}
    sfx = (read_json(p.path("sfx.json"), {}) or {}).get("candidates", [])
    screen = (read_json(p.path("screen.json"), {}) or {}).get("items", {})
    dia = (read_json(p.path("dialog_ev.json"), {}) or {}).get("items", {})
    cuts = (read_json(p.path("cuts.json"), {}) or {}).get("cuts", [])
    dlines = (read_json(p.path("dialogue.json"), {}) or {}).get("lines", [])
    items = []
    # 효과음 (time order so '앞 자막에서 이미 알림' sees earlier decisions)
    last_inc: dict[str, float] = {}
    seen_scene_class: set = set()
    def agent_for(c):
        ag = agent_results.get(c["id"])
        if ag and ag.get("fp") and ag["fp"] != f"{c['kind']}@{float(c['onset']):.2f}":
            return None                     # result of another candidate that had this id
        return ag

    for c in sorted(sfx, key=lambda x: x["onset"]):
        c = dict(c)
        ag = agent_for(c)
        cls = c["label"]["class"]
        shot = sum(1 for x in cuts if x <= c["onset"])
        c["scene_first"] = (cls, shot // 6) not in seen_scene_class
        seen_scene_class.add((cls, shot // 6))
        prev_t = last_inc.get(cls)
        # a continuing sound (rain, engine, rustle) is told once; separate events (each thunder
        # clap, each knock) get their own caption unless they are part of the same burst
        span = SAME_SOUND_S if c["category"] in ("ambient", "minor") else 3.0
        known = c["label"].get("confident") and not c["label"].get("unknown")
        prev_same = {"t": prev_t} if known and prev_t is not None and c["onset"] - prev_t <= span else None
        evid = sfx_evidence(c, screen.get(c["id"]), dia.get(c["id"]))
        if prev_same:
            evid.append(ev("자막", prev_t, "앞 자막에서 이미 알린 소리", "exclude"))
        if ag:
            evid.extend(ag.get("evidence", []))
            if ag.get("phrases"):
                c["phrases"] = ag["phrases"] + [o for o in c["phrases"] if o["text"] not in {x["text"] for x in ag["phrases"]}]
                c["phrase"], c["short"] = c["phrases"][0]["text"], c["phrases"][0]["short"]
        if c.get("category") == "vocal" and phrases.info(c["label"]["class"])["family"] in OWN_SOUND_FAMILIES:
            # a laugh / cough / sigh right where a line starts is the speaker's own sound:
            # placement writes it on the same line without a hyphen ('[재채기] 나도야!', memo 5.3 ③).
            # Screams and crying are left out: a line right after someone else's scream is often
            # a reaction to it, and then a hyphen (other source) is the safe reading.
            own = next((ln for ln in dlines if abs(ln["start"] - c["onset"]) <= 0.5), None)
            if own:
                c["source_line"] = own["id"]
        rec, reason, agent_why = decide_sfx(c, screen.get(c["id"]), dia.get(c["id"]), prev_same, ag)
        if ag and rec == "check":
            reason = (ag.get("short_reason") or reason) + " → 작가 확인"
        c.update(rec=rec, reason=reason, evidence=evid, agent_reasons=agent_why if not ag else [])
        if ag:
            c["agent"] = {k: ag.get(k) for k in ("status", "summary", "tools", "steps", "recommendation")}
            if ag.get("summary"):
                c["evidence"].append(ev("에이전트", c["onset"], ag["summary"], "neutral", "agent"))
            if ag.get("plot_note"):
                c["evidence"].append(ev("맥락", c["onset"], ag["plot_note"],
                                        "include" if ag.get("plot") == "필요" else "neutral", "agent"))
        if rec == "include":
            last_inc[cls] = c["onset"]
        items.append(c)
    # 음악
    prev_mood, prev_any = None, None
    for m in music_candidates(p):
        ag = agent_for(m)
        mus = m["music"]
        top = (mus.get("moods") or [{}])[0].get("key")
        if ag and ag.get("mood"):
            top = ag["mood"]
        ref = None                      # the earlier music item that already tells this one
        if mus["event"] == "cue":
            # a new cue right after another caption, or the same mood going on, adds nothing
            if prev_mood is not None and m["onset"] - prev_mood["t"] <= 10.0:
                ref = prev_mood
            elif (prev_any is not None and prev_any["mood"] == top and m["onset"] - prev_any["t"] <= MUSIC_CUE_SAME_S
                  and mus["segment"][0] <= prev_any["t"] + 1e-6):
                ref = prev_any
        elif (prev_mood is not None and prev_mood["mood"] == top and m["label"]["confident"]
              and m["onset"] - prev_mood["t"] <= MUSIC_SAME_S and mus["segment"][0] <= prev_mood["t"] + 1e-6):
            ref = prev_mood             # the same mood continuing in the same piece of music
        same = ref is not None
        prev_any = {"mood": top, "t": m["onset"]}
        rec, reason, why = decide_music(m, {"t": ref["t"]} if same else None, ag)
        evid = [ev("소리", m["onset"], {"start": "음악 시작", "cue": "새 음악 시작", "mood_change": "분위기 바뀜"}
                   .get(mus["event"], "음악"), "include" if mus["event"] in ("start", "cue") else "neutral", "sed")]
        if mus.get("moods"):
            evid.append(ev("분위기", m["onset"], "후보: " + ", ".join(f"{x['ko']} {x['score']:.2f}" for x in mus["moods"][:3]),
                           "include" if m["label"]["confident"] else "neutral", "ast"))
        if mus.get("change"):
            evid.append(ev("변화", m["onset"], "소리가 점점 커짐" if mus["change"] == "고조되는" else "소리가 점점 작아짐",
                           "neutral", "rule", m["onset"] + 4.0))
        if same:
            evid.append(ev("자막", ref["t"], "앞 자막에서 같은 분위기 음악을 알림", "exclude"))
        if ag:
            evid.extend(ag.get("evidence", []))
            if ag.get("phrases"):
                m["phrases"] = ag["phrases"] + m["phrases"]
                m["phrase"], m["short"] = ag["phrases"][0]["text"], ag["phrases"][0]["short"]
            m["agent"] = {k: ag.get(k) for k in ("status", "summary", "tools", "steps", "recommendation")}
            if ag.get("summary"):
                evid.append(ev("에이전트", m["onset"], ag["summary"], "neutral", "agent"))
            if rec == "check" and ag.get("short_reason"):
                reason = ag["short_reason"] + " → 작가 확인"
        m.update(rec=rec, reason=reason, evidence=evid, agent_reasons=why if not ag else [])
        if rec == "include":
            prev_mood = {"mood": top, "t": m["onset"]}
        items.append(m)
    # 화자: an off-screen speaker who keeps talking is named on the first line of the run only
    prev, named_run = None, False
    for s in speaker_candidates(p):
        sp = s["speaker"]
        cont = (named_run and prev is not None and prev["speaker"]["name"] == sp.get("name")
                and prev["speaker"]["face"] != "visible" and s["onset"] - prev["t1"] <= 2.0)
        rec, reason, _ = decide_speaker(s, prev if cont else None)
        s.update(rec=rec, reason=reason, evidence=speaker_evidence(s), agent_reasons=[])
        items.append(s)
        named_run = rec == "include" or (cont and rec == "exclude")
        prev = s
    items.sort(key=lambda it: (it["onset"], {"speaker": 0, "music": 1, "sfx": 2}[it["kind"]]))
    return items


def run(p, cfg: dict) -> dict:
    if p.path("items.json").exists():
        return {"skipped": True}
    agent_results = (read_json(p.path("agent_results.json"), {}) or {}).get("items", {})
    items = build(p, agent_results)
    write_json(p.path("items.json"), {"items": items})
    cnt: dict = {}
    for it in items:
        cnt.setdefault(it["kind"], {}).setdefault(it["rec"], 0)
        cnt[it["kind"]][it["rec"]] += 1
    log.info("recommend: %s", cnt)
    return {"counts": cnt, "agent_queue": sum(1 for it in items if it.get("agent_reasons"))}
