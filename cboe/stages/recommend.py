# -*- coding: utf-8 -*-
"""추천 (5.7): every candidate gets 포함 추천 / 제외 추천 / 확인 필요 with its evidence.

효과음, in this order (deck 5.7):
1. 이미 전해지는 정보인가? (화면에 출처·동작이 보임 · 대사에 이미 적힘 · 같은 소리를 앞 자막이
   이미 알림) → 제외 추천
2. 넣을 근거가 있나? (출처가 화면에 안 보이고, 종류가 확실하고, 인물 반응 · 사건 · 위험 · 단서가
   있음: 대사 반응/언급, 화면 속 반응, 갑자기 큰 사건 소리, 장면 첫 배경음) → 포함 추천
3. 그 밖(근거 부족 · 서로 어긋남 · 종류 불확실 · 목록 밖 소리) → 재확인 에이전트 → 그래도
   모르면 확인 필요
   A dialogue reaction / mention that rests on a line whose time is a guess (align could not
   match it) is shown as evidence but does not make an include recommendation (감사 1-08).
음악: 시작·새 음악·분위기 바뀜·멈춤이 후보. 분위기가 확실하면 포함, 애매하면 에이전트 → 확인
필요(분위기 없는 문구). 같은 분위기 음악이 이어지는 후보는 그 앞의 '뿌리' 후보를 따른다
(``follows`` = 뿌리 id): 뿌리가 포함 추천이면 '앞 자막에서 이미 알림'으로 제외, 뿌리가 아직
확인 필요면 이 후보도 확인 필요('앞 후보를 먼저 정해야 함') — 자막이 아닌 후보를 '앞 자막'이라
부르지 않는다 (감사 9-x1, 6-m2). 멈춤('[음악이 멈춘다]')은 늘 확인 필요.
화자: 시각이 있는 대본 줄마다. 말하는 얼굴이 보이면 이름 생략(제외), 안 보이면 이름 표시(포함),
애매하거나 대본에 화자가 없으면 확인 필요. 대본이 화면 밖이라고 적은 줄(내레이션·전화·V.O. …)은
얼굴 판정과 상관없이 '안 보임'이고, 시각이 추정인 줄은 얼굴이 없어도 확인 필요. 화면 밖에서 같은
화자가 이어 말하면 이름은 첫 줄에만('같은 화자가 이어 말함', ``follows`` = 이름을 단 줄); 앞 줄이
아직 확인 필요면 이 줄의 추천은 그대로 두고 ``follows`` = 앞 줄, 이유에 '앞 줄을 먼저 정해야 함'.
Candidates are never dropped. ``decide()`` is re-run after the agent adds evidence.

``follows`` on an item (sound, music or speaker) is the id of the earlier item its recommendation
leans on ('앞 자막에서 이미 알림', '앞 후보를 먼저 정해야 함', '같은 화자가 이어 말함'), so the review
screen can show them together and re-check them when the writer changes the earlier one.
``minor`` on a sound item: found by a level jump alone and nothing speaks for it yet — the screen
may fold it (감사 4-m3).

대본 단서 (``cboe.cues``, 감사 4-01 · 4-02 · 8-05 · 8-11 · 8-12): a sound, music, silence or a
speaker's own sound the script writes down, with the window between the aligned lines around it.
- A detected sound that starts in the window and is of the kind the cue names (or one the
  detector could not name) gets ``cue_id`` and a '대본' evidence; the script's wording becomes the
  default caption of an unnamed one (``named_by`` 'script'). One candidate per cue, the likeliest.
  The cue counts as a reason to include ('대본 지문에 적힌 소리', like a plot hint) — after the
  rules that exclude (visible, already in the dialogue, told by the caption before), which keep
  their order. It is evidence only (``cue_reason`` false, ``cues.is_reason``) when it does not
  say which sound it is (a cue with only a '…소리' description takes no named sound at all),
  when its window rests on guessed line times (``cue_estimated``, 감사 1-08), and for a person's
  own sound — a speaker's '(한숨)' (it ties the sound to its line, ``source_line``) or a laugh,
  a cough written in an action line that is not off screen: the person is mostly in view (감사 8-12).
- A music cue adds its wording (a song title: "[가수의 '곡명']") to the music candidate there as
  a choice; a silence cue joins the music-stop candidate at the same place.
- A cue no detected candidate fits becomes a candidate of its own: ``origin`` 'script', the id
  of the cue, ``estimated`` (the time is a guess inside the window), always 확인 필요 — whether the
  written sound is in the finished film was never measured, so the script alone includes nothing.
  A speaker's own sound is such a candidate on its line (``source_line``). A cue without a window
  makes no candidate (it stays in cues.json).
'장면' (the first background sound of a scene) is the script scene the sound falls in when the
alignment knows the scene times, else a group of six shots.
"""
from __future__ import annotations

from .. import cues as cue_mod
from .. import phrases
from ..util import log, read_json, write_json
from . import cues as cue_stage

SAME_SOUND_S = 20.0
LOUD_JUMP_DB = 15.0
MUSIC_CONF, MUSIC_MARGIN = 0.40, 0.12
MUSIC_SAME_S = 90.0
MUSIC_CUE_SAME_S = 20.0
MUSIC_STOP_PHRASE = "[음악이 멈춘다]"         # 넷플릭스 II.9
MUSIC_STOP_SHOW_S = 2.0                       # the stop has no length of its own: how long the quiet is 'heard'
OWN_SOUND_FAMILIES = {"laughter", "cough", "sneeze", "breath", "snore", "hum_voice", "whistle_voice"}
UNSEEN = ("none", "skipped")                  # screen.json sources that are not an answer of the model
ESTIMATED_TIMING = ("voice", "even")          # align could not match the words: the line time is a guess
# dialogue line ``source`` (from the script: speaker-column tags and bracketed speakers) in evidence text
SOURCE_KO = {"offscreen": "목소리만", "narration": "내레이션", "inner": "속마음", "phone": "전화",
             "radio": "무전", "broadcast": "방송", "video": "영상"}
SCRIPT_WHY = "대본 지문에 적힌 소리"                # a script cue: a reason to include, like a plot hint
SCRIPT_NOT_FOUND = "대본 지문에 적힌 소리 · 소리 검출로는 못 찾음"
SCRIPT_KIND = {"sfx": "sfx", "vocal": "sfx", "silence": "sfx", "music": "music"}
SCRIPT_CATEGORY = {"sfx": "script", "vocal": "vocal", "silence": "silence", "music": "music"}
SCRIPT_SHOW_S = {"sfx": 2.0, "vocal": 1.5, "silence": 3.0, "music": 4.0}   # how long a guessed caption is shown


# ------------------------------------------------------------------ helpers
def ev(kind, t, text, polarity="neutral", source="rule", t1=None) -> dict:
    e = {"kind": kind, "t": round(float(t), 3), "text": text, "polarity": polarity, "source": source}
    if t1 is not None:
        e["t1"] = round(float(t1), 3)
    return e


def has_time(ln: dict) -> bool:
    """False for a script line without a time (dialogue.json ``status`` absent / out_of_range:
    ``start`` and ``end`` are null). Such lines are skipped by the speaker stage, get no
    speaker item and are not read as dialogue evidence."""
    return ln.get("start") is not None and ln.get("end") is not None


def timing_estimated(ln: dict) -> bool:
    """True when the line time is a guess: align put the line on voice activity ('voice') or
    spread it evenly ('even'), or the scene-first alignment could not hear it ('unheard')."""
    return ln.get("timing") in ESTIMATED_TIMING or ln.get("status") == "unheard"


def music_candidates(p) -> list[dict]:
    mj = read_json(p.path("music.json"), {}) or {}
    items = mj.get("items", [])
    out = []
    for i, m in enumerate(items, 1):
        if m["kind"] == "stop":                 # the music breaks off: one fixed phrase, no mood
            opt = {"text": MUSIC_STOP_PHRASE, "short": MUSIC_STOP_PHRASE, "source": "stop", "note": "음악이 갑자기 멈춤"}
            out.append({"id": f"M{i:04d}", "kind": "music", "t0": m["onset"],
                        "t1": round(m["onset"] + MUSIC_STOP_SHOW_S, 3), "onset": m["onset"], "category": "music",
                        "label": {"class": "Music", "ko": "음악 멈춤", "score": m.get("music_score", 0.0),
                                  "confident": False},
                        "music": {"moods": [], "change": None, "instruments": [], "segment": m["segment"],
                                  "event": "stop", "strength": None, "drop_db": m.get("drop_db"),
                                  "next_music_s": m.get("next_music_s")},
                        "phrases": [opt], "phrase": opt["text"], "short": opt["short"], "flags": []})
            continue
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
        plain = phrases.mood_phrase(None)       # the caption without a mood word
        opts.append({"text": plain, "short": plain, "source": "mood", "note": "분위기 없이"})
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
        if not has_time(ln):                  # not in the video / outside the processed part: nothing to name
            continue
        o = sp.get(ln["id"], {"face": "unclear", "n_faces": 0, "best": None, "name_revealed": None})
        b = o.get("best") or {}
        face, by = o["face"], o.get("by")
        if ln.get("source"):                  # the script says off screen: a face the models found is someone else's
            face, by = "not_visible", "script"
        out.append({"id": "P" + ln["id"][1:], "kind": "speaker", "t0": ln["start"], "t1": ln["end"],
                    "onset": ln["start"], "category": "speaker",
                    "label": {"class": "speaker", "ko": ln.get("speaker") or "화자 미상", "score": b.get("max_score")},
                    "speaker": {"line_id": ln["id"], "name": ln.get("speaker"), "face": face,
                                "n_faces": o.get("n_faces", 0), "asd": b.get("max_score"),
                                "speaking_frames": b.get("speaking_frames"), "name_revealed": o.get("name_revealed"),
                                "timing": ln.get("timing"), "estimated": timing_estimated(ln),
                                "by": by, "source": ln.get("source")},
                    "phrases": [], "phrase": ln["text"], "short": ln["text"], "flags": []})
    return out


# ----------------------------------------------------------------- decisions
def sfx_evidence(c: dict, screen: dict | None, dia: dict | None) -> list[dict]:
    lab = c["label"]
    out = []
    if lab.get("unknown"):
        out.append(ev("소리", c["onset"], f"목록에 없는 소리 (갑자기 {c.get('level_jump_db', 0):.0f}dB 커짐)", "neutral", "sed"))
    else:
        # the classes above it, without the ones that are only '소리' in Korean ('숨 → 소리 → 소리')
        # and without a name said twice in a row
        names = [k for k in lab.get("path_ko", []) if k and k != phrases.SOUND]
        names = [k for i, k in enumerate(names) if i == 0 or k != names[i - 1]]
        path = " → ".join(names[:3])
        txt = f"검출: {lab['ko']} {lab['score']:.2f}" + (f" ({path})" if len(names) > 1 else "")
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
        txt = dia.get("evidence") or dia["relation"]
        if dia.get("estimated") and dia["relation"] in ("reaction", "mention"):
            pol, txt = "neutral", f"{txt} (그 대사의 시각은 추정)"      # shown, but not counted (decide_sfx)
        out.append(ev("대사", c["onset"], txt, pol, dia.get("source", "llm")))
    if dia and dia.get("plot_hint"):
        out.append(ev("맥락", c["onset"], dia["plot_hint"], "include", dia.get("source", "llm")))
    return out


def _short(text: str, n: int = 24) -> str:
    text = (text or "").strip().rstrip(".")
    return text if len(text) <= n else text[:n].rstrip() + "…"


def decide_sfx(c: dict, screen: dict | None, dia: dict | None, prev_same: dict | None,
               agent: dict | None = None) -> tuple[str, str, list[str]]:
    """(rec, reason, agent_reasons).

    ``dia["estimated"]`` (set by ``build``): the line the dialogue evidence rests on has a guessed
    time, so a reaction / mention there is no reason to include. ``screen["source"]`` 'none' or
    'skipped': the screen was not looked at (no LLM, or llm_scope 'useful')."""
    lab = c["label"]
    vis = (agent or {}).get("visible") or (screen or {}).get("visible", "애매")
    unseen = not (agent or {}).get("visible") and (screen or {}).get("source") in UNSEEN
    rel = (dia or {}).get("relation", "none")
    weak = rel in ("reaction", "mention") and bool((dia or {}).get("estimated"))
    confident = lab.get("confident") and not lab.get("unknown") and not lab.get("competing")
    if agent and agent.get("label_ok"):
        confident = True
    by_script = c.get("named_by") == "script"          # an unnamed sound the script names (대본 단서)
    named = confident or by_script or any(p.get("source") == "agent" and p["text"] not in ("[소리]", "") for p in
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
    if rel in ("reaction", "mention") and not weak:
        why.append(_short((dia or {}).get("evidence") or "대사 반응", 20))
    if (screen or {}).get("reaction"):
        why.append("인물 반응")
    if (dia or {}).get("plot_hint"):
        why.append("줄거리 단서")
    if c.get("cue_id") and c.get("cue_reason", c.get("cue_type") != "vocal"):
        # ``cue_reason`` (cues.is_reason): not for a cue that does not say which sound it is, a
        # window on guessed line times, or a person's own sound in view ('(웃음)', 감사 8-12)
        why.append(SCRIPT_WHY)
    if c.get("category") == "event" and c.get("level_jump_db", 0) >= LOUD_JUMP_DB:
        why.append("갑자기 큰 소리")
    if c.get("category") == "ambient" and c.get("scene_first"):
        why.append("장면 첫 배경 소리")
    if agent and agent.get("plot") == "필요":
        why.append("줄거리에 필요")
    # deck 5.7: not already conveyed + a reason to include -> 포함 추천 (a dark / unclear screen
    # does not convey the sound either; the writer sees '화면 애매' in the reason)
    if why and not mixed and vis in ("안 보임", "애매"):
        head = "화면에 출처 없음" if vis == "안 보임" else ("화면 확인 안 함" if unseen else "화면 애매")
        if confident:
            return "include", head + " · " + " · ".join(dict.fromkeys(why)), []
        if named and agent and agent.get("plot") == "필요":
            return "include", head + " · " + " · ".join(dict.fromkeys(why)) + " · 문구 확인", []
    if agent and agent.get("plot") == "불필요" and vis != "애매":
        return "exclude", _short(agent.get("plot_note") or "줄거리와 관계 없음", 30), []
    # 3. 근거 부족 · 서로 어긋남
    reasons = []
    if lab.get("unknown"):
        reasons.append("목록에 없는 소리 · 이름은 대본에서" if by_script else "목록에 없는 소리")
    elif not confident:
        reasons.append("소리 종류가 불확실")
    if mixed:
        reasons.append("출처 판단 어긋남")
    if vis == "애매":
        reasons.append("화면 확인 안 함" if unseen else "화면 판단 애매")
    if vis == "안 보임" and not why:
        reasons.append("넣을 근거 부족")
    if weak:
        reasons.append("대사 근거의 시각이 추정")
    if c.get("cue_id") and c.get("cue_estimated"):
        reasons.append("대본 단서의 시각이 추정")
    return "check", " · ".join(reasons) or "근거 부족", reasons


def decide_music(m: dict, prev: dict | None, agent: dict | None = None) -> tuple[str, str, list[str]]:
    """``prev``: the earlier music item this one only continues — {"t", "id", "rec"} of the root of
    the chain ("rec" missing = include) — or None when the item stands on its own."""
    mus = m["music"]
    if mus.get("event") == "stop":
        return "check", "음악이 갑자기 멈춤 · 줄거리에 필요한지 확인", []
    top = (mus.get("moods") or [None])[0]
    confident = m["label"]["confident"] or bool(agent and agent.get("mood_ok"))
    if prev is not None:
        if prev.get("rec", "include") == "include":
            return "exclude", "같은 분위기 음악이 이어짐 · 앞 자막에서 이미 알림", []
        # the earlier item is not a caption (yet): nothing was told, so this one waits for it
        return "check", "앞 후보와 같은 분위기 · 앞 후보를 먼저 정해야 함", []
    what = {"start": "음악 시작", "cue": "새 음악", "mood_change": "분위기 바뀜"}.get(mus.get("event"), "음악")
    if confident and top:
        return "include", f"{what} · 분위기 1위 '{top['ko']}'", []
    return "check", f"{what} · 분위기 판단 애매", ["음악 분위기 애매"]


def _script_mark(sp: dict) -> str | None:
    """'대본에 화면 밖 표시(내레이션)' for a line the script marks as spoken off screen."""
    if sp.get("by") != "script":
        return None
    return f"대본에 화면 밖 표시({SOURCE_KO.get(sp.get('source'), sp.get('source') or '화면 밖')})"


def decide_speaker(s: dict, prev: dict | None) -> tuple[str, str, list[str]]:
    sp = s["speaker"]
    mark = _script_mark(sp)
    guess = " · 대사 시각 추정" if sp.get("estimated") else ""
    if sp["face"] == "visible":
        return "exclude", "말하는 얼굴이 보임", []
    if not sp.get("name"):
        return "check", (mark or "말하는 얼굴 " + ("안 보임" if sp["face"] == "not_visible" else "판정 애매")) \
            + " · 대본에 화자 없음", []
    if sp["face"] == "not_visible":
        if prev is not None:
            return "exclude", "같은 화자가 이어 말함 · 앞 줄에 이름 표시", []
        if mark:
            return "include", mark, []
        if guess:               # no face at a guessed time is no proof that the speaker is off screen (감사 3-03)
            return "check", "말하는 얼굴 안 보임" + guess, []
        return "include", "말하는 얼굴 안 보임", []
    return "check", "말하는 얼굴 판정 애매" + guess, []


def speaker_evidence(s: dict) -> list[dict]:
    sp = s["speaker"]
    mark = _script_mark(sp)
    if mark:
        out = [ev("대본", s["onset"], f"{mark}: 화면의 얼굴과 상관없이 '안 보임'으로 둠", "include", "script")]
    else:
        txt = {"visible": "말하는 얼굴이 보임", "not_visible": "말하는 얼굴 안 보임" if not sp.get("n_faces")
               else f"얼굴 {sp['n_faces']}개 보이지만 입이 움직이지 않음", "unclear": "말하는 얼굴 판정 애매"}[sp["face"]]
        pol = {"visible": "exclude", "not_visible": "include", "unclear": "neutral"}[sp["face"]]
        if pol == "include" and sp.get("estimated"):
            pol = "neutral"
        out = [ev("화면", s["onset"], txt, pol, "asd", s["onset"] + 1.0)]
    out.append(ev("대본", s["onset"], f"이 대사는 {sp['name']}" if sp.get("name") else "대본에 화자 없음", "neutral", "script"))
    if sp.get("estimated") or sp.get("timing") not in ("asr", "script", None):
        out.append(ev("대본", s["onset"], "대사 시각 추정 (받아쓰기로 못 맞춤)", "neutral", "align"))
    if sp.get("name") and sp.get("name_revealed") is False:
        out.append(ev("대본", s["onset"], "이름이 아직 대사에 나오지 않음", "neutral", "script"))
    return out


# ------------------------------------------------------------------ 대본 단서
def _script_opts(cue: dict) -> list[dict]:
    """The cue's captions as choices ('[소리]', the cue that says nothing of the sound, is none)."""
    return [{"text": t, "short": t, "source": "script", "note": "대본 지문"} for t in cue.get("phrases") or []
            if t and t != "[소리]"]


def _merge_opts(first: list[dict], rest: list[dict]) -> list[dict]:
    seen, out = set(), []
    for o in first + rest:
        if o["text"] not in seen:
            seen.add(o["text"])
            out.append(o)
    return out


def script_evidence(cue: dict, t: float, polarity: str = "include") -> dict:
    """'대본' evidence of a cue: what the script writes ('대본 지문: "…"', a speaker's own sound
    '대본: 가온의 한숨'), with where the window came from."""
    if cue["type"] == "vocal":
        who = f"{cue['speaker']}의 " if cue.get("speaker") else ""
        txt = f"대본에 적힌 말하는 사람의 소리: {who}{cue.get('keyword') or ''}".rstrip()
    else:
        txt = f"대본 지문: \"{cue_mod.short(cue.get('text'), 40)}\""
        if cue.get("offscreen"):
            txt += " · 화면 밖 소리로 적힘"
    if cue.get("songs"):
        txt += " · 곡명: " + ", ".join(s["title"] for s in cue["songs"])
    if cue.get("window_estimated"):
        txt += " · 앞뒤 대사의 시각이 추정"
    w = cue.get("window")
    e = ev("대본", t, txt, polarity, "script")
    if w:
        e["window"] = list(w)
    return e


def attach_sound(c: dict, cue: dict) -> None:
    """A detected sound candidate the script cue ``cue`` speaks for: ``cue_id``, whether the cue
    is a reason to include it (``cue_reason``, ``cues.is_reason``) and rests on guessed times
    (``cue_estimated``), the script's wording (the default caption of a sound the detector could
    not name — unless the cue only has a '…소리' description, ``vague``: then a choice only), the
    line of a speaker's own sound."""
    c["cue_id"] = cue["id"]
    c["cue_type"] = cue["type"]
    c["cue_reason"] = cue_mod.is_reason(cue, cue_mod.compatible(cue, c))
    c["cue_estimated"] = bool(cue.get("window_estimated"))
    opts = _script_opts(cue)
    if c["label"].get("unknown") and opts and not cue.get("vague"):
        c["phrases"] = _merge_opts(opts, [o for o in c.get("phrases") or [] if o.get("source") != "unknown"])
        c["phrase"], c["short"] = c["phrases"][0]["text"], c["phrases"][0]["short"]
        c["named_by"] = "script"
    elif opts:
        c["phrases"] = _merge_opts(c.get("phrases") or [], opts)
    if cue["type"] == "vocal" and cue.get("line_id"):
        c["source_line"] = cue["line_id"]           # the script says whose sound it is


def attach_music(m: dict, cues: list[dict]) -> None:
    """Music cues at a music candidate: their wording (a song title) as choices right after the
    default caption, which stays."""
    m["cue_id"] = cues[0]["id"]
    m["cue_ids"] = [c["id"] for c in cues]
    opts = [o for c in cues for o in _script_opts(c)]
    head = (m.get("phrases") or [])[:1]
    m["phrases"] = _merge_opts(head + opts, m.get("phrases") or [])


def script_candidate(cue: dict) -> dict | None:
    """A 확인 필요 candidate for a script cue no detected candidate fits; None without a window,
    for music that only goes on ('그 음악 계속 흐르고') and for a cue with no caption to suggest
    (``vague``: only a '…소리' description, which II.9 asks not to write)."""
    w = cue.get("window")
    opts = _script_opts(cue)
    if not w or not opts or cue.get("vague") or (cue["type"] == "music" and cue.get("continues")):
        return None
    kind = "music" if cue["type"] == "music" or (cue["type"] == "silence" and cue.get("music_stop")) else \
        SCRIPT_KIND[cue["type"]]
    t0 = float(cue.get("guess", w[0]))
    t1 = max(t0 + 1.0, min(float(w[1]), t0 + SCRIPT_SHOW_S[cue["type"]]))
    it = {"id": cue["id"], "kind": kind, "origin": "script", "cue_id": cue["id"], "estimated": True,
          "t0": round(t0, 3), "t1": round(t1, 3), "onset": round(t0, 3),
          "category": SCRIPT_CATEGORY[cue["type"]],
          "label": {"class": "", "ko": cue.get("keyword") or "대본 단서", "score": 0.0, "confident": False,
                    "unknown": False, "competing": [], "path_ko": [], "top": []},
          "window": list(w), "phrases": opts, "phrase": opts[0]["text"], "short": opts[0]["short"],
          "flags": ["script", "timing_estimated"]}
    if kind == "music":
        it["music"] = {"moods": [], "change": None, "instruments": [], "segment": [it["t0"], it["t1"]],
                       "event": "stop" if cue.get("music_stop") else "script", "strength": None}
    if cue["type"] == "vocal" and cue.get("line_id"):
        it["source_line"] = cue["line_id"]
    reason = SCRIPT_NOT_FOUND
    if cue["type"] == "silence":
        reason += " · 중요한 정적인지 확인"
    elif cue["type"] == "vocal":
        reason += " · 소리 낸 사람이 보이면 안 적어도 됨"
    elif w[1] - w[0] > cue_mod.WIDE_S:
        reason += " · 시각 범위가 넓음"
    it.update(rec="check", reason=reason, agent_reasons=[],
              evidence=[script_evidence(cue, t0, "neutral"),
                        ev("대본", t0, "시각은 앞뒤 대사로 어림 (소리로 확인 못 함)", "neutral", "script", w[1])])
    return it


# ---------------------------------------------------------------------- run
def build(p, agent_results: dict | None = None) -> list[dict]:
    agent_results = agent_results or {}
    sfx = (read_json(p.path("sfx.json"), {}) or {}).get("candidates", [])
    screen = (read_json(p.path("screen.json"), {}) or {}).get("items", {})
    dia = (read_json(p.path("dialog_ev.json"), {}) or {}).get("items", {})
    cuts = (read_json(p.path("cuts.json"), {}) or {}).get("cuts", [])
    all_lines = (read_json(p.path("dialogue.json"), {}) or {}).get("lines", [])
    line_by_id = {ln["id"]: ln for ln in all_lines}
    dlines = [ln for ln in all_lines if has_time(ln)]
    scenes = (read_json(p.path("scenes.json"), {}) or {}).get("scenes")
    starts = cue_mod.scene_starts(all_lines, scenes)
    script_cues = cue_stage.load(p)
    sound_hits = cue_mod.match_sounds(script_cues, sfx)
    used_cues = {cue["id"] for cue in sound_hits.values()}
    items = []
    # 효과음 (time order so '앞 자막에서 이미 알림' sees earlier decisions)
    last_inc: dict[str, dict] = {}          # class -> the last sound of it that is an include recommendation

    def agent_for(c):
        ag = agent_results.get(c["id"])
        if ag and ag.get("fp") and ag["fp"] != f"{c['kind']}@{float(c['onset']):.2f}":
            return None                     # result of another candidate that had this id
        return ag

    for c in with_scene_first(sfx, cuts, starts):
        ag = agent_for(c)
        cls = c["label"]["class"]
        sc, dd = screen.get(c["id"]), dia.get(c["id"])
        cue = sound_hits.get(c["id"])
        if cue:
            attach_sound(c, cue)
        if dd and dd.get("line_id") and timing_estimated(line_by_id.get(dd["line_id"]) or {}):
            dd = {**dd, "estimated": True}  # the evidence rests on a line whose time is a guess (감사 1-08)
        last = last_inc.get(cls)
        # a continuing sound (rain, engine, rustle) is told once; separate events (each thunder
        # clap, each knock) get their own caption unless they are part of the same burst
        span = SAME_SOUND_S if c["category"] in ("ambient", "minor") else 3.0
        known = c["label"].get("confident") and not c["label"].get("unknown")
        prev_same = dict(last) if known and last is not None and c["onset"] - last["t"] <= span else None
        evid = sfx_evidence(c, sc, dd)
        if cue:
            evid.append(script_evidence(cue, c["onset"], "include" if c.get("cue_reason") else "neutral"))
        if prev_same:
            evid.append(ev("자막", prev_same["t"], "앞 자막에서 이미 알린 소리", "exclude"))
            c["follows"] = prev_same["id"]
        if ag:
            evid.extend(ag.get("evidence", []))
            if ag.get("phrases"):
                c["phrases"] = ag["phrases"] + [o for o in c["phrases"] if o["text"] not in {x["text"] for x in ag["phrases"]}]
                c["phrase"], c["short"] = c["phrases"][0]["text"], c["phrases"][0]["short"]
        # (a source_line set by attach_sound: the script says whose sound it is)
        if not c.get("source_line") and c.get("category") == "vocal" \
                and phrases.info(c["label"]["class"])["family"] in OWN_SOUND_FAMILIES:
            # a laugh / cough / sigh right where a line starts is the speaker's own sound:
            # placement writes it on the same line without a hyphen ('[재채기] 나도야!', memo 5.3 ③).
            # Screams and crying are left out: a line right after someone else's scream is often
            # a reaction to it, and then a hyphen (other source) is the safe reading.
            own = next((ln for ln in dlines if abs(ln["start"] - c["onset"]) <= 0.5), None)
            if own:
                c["source_line"] = own["id"]
        rec, reason, agent_why = decide_sfx(c, sc, dd, prev_same, ag)
        if ag and rec == "check":
            reason = (ag.get("short_reason") or reason) + " → 작가 확인"
        c.update(rec=rec, reason=reason, evidence=evid, agent_reasons=agent_why if not ag else [])
        if c.get("minor"):                  # found by the level jump alone: folded until something speaks for it
            c["minor"] = rec == "check" and not _supported(sc, dd, ag) and not c.get("cue_id")
        if ag:
            c["agent"] = {k: ag.get(k) for k in ("status", "summary", "tools", "steps", "recommendation")}
            if ag.get("summary"):
                c["evidence"].append(ev("에이전트", c["onset"], ag["summary"], "neutral", "agent"))
            if ag.get("plot_note"):
                c["evidence"].append(ev("맥락", c["onset"], ag["plot_note"],
                                        "include" if ag.get("plot") == "필요" else "neutral", "agent"))
        if rec == "include":
            last_inc[cls] = {"t": c["onset"], "id": c["id"]}
        items.append(c)
    # 음악. An item that only continues an earlier one follows the root of its chain: prev_inc is
    # the last include recommendation, prev_any the item just before whatever its recommendation.
    prev_inc, prev_any = None, None
    music_items = music_candidates(p)
    music_hits, stop_hits = cue_mod.match_music(script_cues, music_items)
    used_cues |= {cue["id"] for cs in music_hits.values() for cue in cs} | {cue["id"] for cue in stop_hits.values()}
    for m in music_items:
        ag = agent_for(m)
        mus = m["music"]
        if mus["event"] == "stop":      # the music breaks off: always the writer's call, no chain
            rec, reason, why = decide_music(m, None, ag)
            drop = f" (세기 {mus['drop_db']:.0f}dB 떨어짐)" if mus.get("drop_db") is not None else ""
            evid = [ev("소리", m["onset"], "음악이 갑자기 멈춤" + drop, "neutral", "rule")]
            if mus.get("next_music_s") is not None:     # a pause between two pieces (music.STOP_NEXT_S)
                gap = float(mus["next_music_s"])
                evid.append(ev("소리", m["onset"] + gap, f"{gap:.1f}초 뒤 다른 음악이 시작함", "neutral", "rule"))
                reason += f" · {gap:.1f}초 뒤 곧 다른 음악이 시작함"
            cue = stop_hits.get(m["id"])
            if cue:                     # the script writes the silence here too: one candidate
                attach_music(m, [cue])
                evid.append(script_evidence(cue, m["onset"]))
                reason += " · 대본 지문에도 적힘"
            m.update(rec=rec, reason=reason, agent_reasons=why, evidence=evid)
            items.append(m)
            continue
        top = (mus.get("moods") or [{}])[0].get("key")
        if ag and ag.get("mood"):
            top = ag["mood"]
        ref = None                      # the earlier music item that this one only continues
        if mus["event"] == "cue":
            # a new cue right after another caption, or the same mood going on, adds nothing
            if prev_inc is not None and m["onset"] - prev_inc["t"] <= 10.0:
                ref = prev_inc
            elif (prev_any is not None and prev_any["mood"] == top and m["onset"] - prev_any["t"] <= MUSIC_CUE_SAME_S
                  and mus["segment"][0] <= prev_any["t"] + 1e-6):
                ref = prev_any
        elif (prev_inc is not None and prev_inc["mood"] == top and m["label"]["confident"]
              and m["onset"] - prev_inc["t"] <= MUSIC_SAME_S and mus["segment"][0] <= prev_inc["t"] + 1e-6):
            ref = prev_inc              # the same mood continuing in the same piece of music
        root = {"t": ref["root_t"], "id": ref["root"], "rec": ref["root_rec"]} if ref else None
        rec, reason, why = decide_music(m, root, ag)
        me = {"mood": top, "t": m["onset"], "root": root["id"] if root else m["id"],
              "root_t": root["t"] if root else m["onset"], "root_rec": root["rec"] if root else rec}
        prev_any = me
        evid = [ev("소리", m["onset"], {"start": "음악 시작", "cue": "새 음악 시작", "mood_change": "분위기 바뀜"}
                   .get(mus["event"], "음악"), "include" if mus["event"] in ("start", "cue") else "neutral", "sed")]
        if mus.get("moods"):
            evid.append(ev("분위기", m["onset"], "후보: " + ", ".join(f"{x['ko']} {x['score']:.2f}" for x in mus["moods"][:3]),
                           "include" if m["label"]["confident"] else "neutral", "ast"))
        if mus.get("change"):
            evid.append(ev("변화", m["onset"], "소리가 점점 커짐" if mus["change"] == "고조되는" else "소리가 점점 작아짐",
                           "neutral", "rule", m["onset"] + 4.0))
        mcues = music_hits.get(m["id"])
        if mcues:
            attach_music(m, mcues)
            evid.extend(script_evidence(cue, m["onset"]) for cue in mcues)
            if any(cue.get("songs") for cue in mcues):
                reason += " · 대본에 곡명"
        if root:
            m["follows"] = root["id"]
            if root["rec"] == "include":
                evid.append(ev("자막", root["t"], "앞 자막에서 같은 분위기 음악을 알림", "exclude"))
            else:                       # what it follows is no caption yet (감사 9-x1)
                evid.append(ev("자막", root["t"], f"앞 후보({root['id']})와 같은 분위기 · 그 후보는 아직 확인 필요", "neutral"))
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
            prev_inc = me
        items.append(m)
    # 대본 단서 that no detected candidate fits: a 확인 필요 candidate of their own
    for cue in script_cues:
        if cue["id"] not in used_cues:
            it = script_candidate(cue)
            if it is not None:
                items.append(it)
    # 화자: an off-screen speaker who keeps talking is named on the first line of the run only
    # (the same name from the same source: a line that turns into narration is labelled again)
    prev, named_run, named_id = None, False, None
    for s in speaker_candidates(p):
        sp = s["speaker"]
        near = (prev is not None and bool(sp.get("name")) and prev["speaker"]["name"] == sp.get("name")
                and prev["speaker"]["face"] != "visible" and s["onset"] - prev["t1"] <= 2.0
                and prev["speaker"].get("source") == sp.get("source"))
        cont = named_run and near
        rec, reason, _ = decide_speaker(s, prev if cont else None)
        evid = speaker_evidence(s)
        if cont and rec == "exclude" and sp["face"] == "not_visible":
            s["follows"] = named_id         # the line of the run that carries the name
        elif near and not named_run and prev.get("rec") == "check":
            # the same speaker goes on right after a line that is still 확인 필요: whether the name
            # goes there or here is not settled yet — shown together, decided again with it
            s["follows"] = prev["id"]
            reason += " · 앞 줄을 먼저 정해야 함"
            evid.append(ev("대본", s["onset"], f"앞 줄({prev['id']})과 같은 화자가 이어 말함 · 앞 줄은 아직 확인 필요"
                           " (앞 줄에 이름을 넣으면 이 줄은 이름 생략)", "neutral", "script"))
        s.update(rec=rec, reason=reason, evidence=evid, agent_reasons=[])
        items.append(s)
        if rec == "include":
            named_id = s["id"]
        named_run = rec == "include" or (cont and rec == "exclude")
        prev = s
    items.sort(key=lambda it: (it["onset"], {"speaker": 0, "music": 1, "sfx": 2}[it["kind"]]))
    return items


def _supported(screen: dict | None, dia: dict | None, agent: dict | None) -> bool:
    """Something besides the level jump speaks for a sound: a reaction on screen, a line that reacts
    to it or names it, a plot hint, or the agent named it / found the plot needs it."""
    dia, agent = dia or {}, agent or {}
    return bool((screen or {}).get("reaction") or dia.get("relation") in ("reaction", "mention")
                or dia.get("plot_hint") or agent.get("plot") == "필요" or agent.get("phrases"))


def with_scene_first(sfx: list[dict], cuts: list[float], starts=None) -> list[dict]:
    """Copies of the sound candidates in time order with ``scene_first``: the first sound of its
    class in its scene — the script scene it falls in when ``starts`` (``cues.scene_starts``:
    scene times from the alignment) places it, else a 'scene' of six shots (cuts.json has shot
    changes, not scenes)."""
    out, seen = [], set()
    for c in sorted(sfx, key=lambda x: x["onset"]):
        c = dict(c)
        sid = cue_mod.scene_at(c["onset"], starts) if starts else None
        key = (c["label"]["class"], sid if sid is not None else sum(1 for x in cuts if x <= c["onset"]) // 6)
        c["scene_first"] = key not in seen
        seen.add(key)
        out.append(c)
    return out


# ------------------------------------------------- which candidates the LLM evidence can change
SCREEN_ANSWERS = tuple({"visible": v, "reaction": r} for v in ("보임", "안 보임", "애매") for r in ("", "반응"))
DIALOG_ANSWERS = tuple({"relation": rel, "plot_hint": h, "evidence": "대사"}
                       for rel in ("already", "reaction", "mention", "none") for h in ("", "단서"))


def answer_matters(c: dict, which: str, screen: dict | None = None) -> bool:
    """True when the answer of the 화면 근거 stage (``which`` 'screen') or the 대사 근거 stage
    ('dialog') can change this sound candidate's rule recommendation: among the possible answers
    two give different recommendations (``decide_sfx`` without an earlier caption and without
    the agent). ``c`` carries ``scene_first`` (``with_scene_first``); for 'dialog', ``screen`` is
    the screen answer when it is already known. A sound the detector could not name stays 확인
    필요 whatever is answered, so it is False for it (감사 6-06)."""
    if which == "screen":
        return any(len({decide_sfx(c, s, d, None)[0] for s in SCREEN_ANSWERS}) > 1 for d in DIALOG_ANSWERS)
    screens = (screen,) if screen is not None else SCREEN_ANSWERS
    return any(len({decide_sfx(c, s, d, None)[0] for d in DIALOG_ANSWERS}) > 1 for s in screens)


def worth_asking(sfx: list[dict], cuts: list[float], which: str, screen_items: dict | None = None,
                 starts=None) -> set[str]:
    """Ids of the sound candidates whose recommendation depends on the answer of the stage
    ``which`` ('screen' | 'dialog'): what ``cfg["llm_scope"] == "useful"`` asks the LLM about.
    ``screen_items`` (screen.json "items") narrows 'dialog' by the screen answers already given;
    ``starts``: the scene times (``with_scene_first``)."""
    known = screen_items or {}
    out = set()
    for c in with_scene_first(sfx, cuts, starts):
        sc = known.get(c["id"]) if which == "dialog" else None
        if sc is not None and (sc.get("failed") or sc.get("source") in UNSEEN):
            sc = None                       # no answer of the model: any answer is still possible
        if answer_matters(c, which, sc):
            out.add(c["id"])
    return out


def worth_asking_in(p, sfx: list[dict], which: str, screen_items: dict | None = None) -> set[str]:
    """``worth_asking`` for the work folder ``p`` on the candidates as ``build`` sees them: the
    script cues attached (``attach_sound``: an unnamed sound the script names is ``named_by``
    'script', and then a visible source or the sound written in the dialogue excludes it — the
    answer matters for it) and the scenes of the script (``cues.scene_starts``) for
    ``scene_first``. ``sfx`` itself is not changed."""
    cuts = (read_json(p.path("cuts.json"), {}) or {}).get("cuts", [])
    lines = (read_json(p.path("dialogue.json"), {}) or {}).get("lines", [])
    scenes = (read_json(p.path("scenes.json"), {}) or {}).get("scenes")
    hits = cue_mod.match_sounds(cue_stage.load(p), sfx)
    seen = []
    for c in sfx:
        c = dict(c)
        if c["id"] in hits:
            attach_sound(c, hits[c["id"]])
        seen.append(c)
    return worth_asking(seen, cuts, which, screen_items, cue_mod.scene_starts(lines, scenes))


def run(p, cfg: dict) -> dict:
    if p.path("items.json").exists():
        return {"skipped": True}
    agent_results = (read_json(p.path("agent_results.json"), {}) or {}).get("items", {})
    items = build(p, agent_results)
    write_json(p.path("items.json"), {"items": items})
    # cues.json made again from the same files: a run that names only some stages ('--stages
    # script' reruns align … recommend, not cues) leaves no cues.json older than the items
    cue_stage.run(p, cfg)
    cnt: dict = {}
    for it in items:
        cnt.setdefault(it["kind"], {}).setdefault(it["rec"], 0)
        cnt[it["kind"]][it["rec"]] += 1
    log.info("recommend: %s", cnt)
    script = {"attached": sum(1 for it in items if it.get("cue_id") and it.get("origin") != "script"),
              "new": sum(1 for it in items if it.get("origin") == "script")}
    return {"counts": cnt, "agent_queue": sum(1 for it in items if it.get("agent_reasons")), "script_cues": script}
