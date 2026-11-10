# -*- coding: utf-8 -*-
"""인물과 화자 표시 (지침 II.8; 감사 3-01 · 3-08 · 3-10 · 3-12 · 3-m2) — pure, no file I/O.

A *person* is a name: the script's speaker name (``script.json`` ``people``, else the speaker names
of the dialogue), or the name the writer gave a line — a line the writer renamed belongs to the
person of that name. Two people talking together ('에루/마야카') are the line of both.

When the name is told (``revealed_at``): the first line, in film order, in which **another**
person says the name as a word — from the start of a word, followed by the end of the word or by
a vocative, an honorific, a particle or a copula ('가온아', '가온 씨', '가온이가', '가온한테',
'가온이라는', '가온이었어', '가온보다'); a name inside a longer word ('운전기사' for '기사') does not
count — or in which the person says it of themselves ('가온입니다', '제 이름은 가온'). A name said
only with a copula or a comparison ('가온이라는', '가온이었어', '가온보다', '가온이나') follows a common
word just as well ('사랑이라는', '영화보다'): such a line never tells the name — it is told by the
first line that calls it in a sure form (as it is, a vocative, an honorific, a particle) or in
which the person tells it, even when a line of the weak form comes before. A name of
one letter is in too many words ('수' in '할 수 있어'): it counts only with a vocative or an
honorific ('수야', '수 씨'; not '씨' written onto it with a particle: '날씨가') or as a plain
self-introduction ('수입니다'). Only the lines given are read: the caller leaves out lines that
are not in the film or that the writer took out.

The label shown (``display``): until the name is told, II.8 asks for a generic ID ('[남자]',
'[여자]': "so as not to provide information that is not yet present in the narrative"). Whether
a person is a man or a woman is nowhere in the script, so the writer decides, per person:
``{"mode": "generic", "before": "남자"}`` (that label before the name is told, the name after) or
``{"mode": "name"}`` (the name all along — role names such as 경찰1 · 직원 · 노파; a name that ends
in a digit, and a speaker the script calls by a generic label itself — '남자', '여자', or a label
the writer gives people — is a role name by default). ``reveal`` moves the line from which the name is shown:
"auto" (the line after the one that tells it), "line" (``reveal_line``, from that line on: a name
shown on screen), "never". A person the writer has not decided keeps the name, and when that name
is shown before it is told the person is listed in ``early`` (for the review screen and the
export check). Two people under the same label in one scene — a generic one, or a speaker the
script itself calls '남자' next to a person the writer labels '남자' — are numbered in the order
they first speak there ('[남자1]', '[남자2]'; counting restarts with the scene; no '1' for one
person, unless the scene also shows that label with a number — '남자1' of the script and one
person labelled '남자' are [남자1] [남자2]; a number another label of the scene already shows is
skipped); without scenes the count runs over the whole film. Labels are never stored: they are made again
every time from the lines and the decisions.

``scope_lines`` picks the lines a speaker change applies to — by person, never by the label text
(two people shown as '남자' are two people, 감사 3-m2).
"""
from __future__ import annotations

import re
from functools import lru_cache

INF = float("inf")
SPLIT = "/"                                   # '에루/마야카': both speak (style.NAME_SPLIT)
ROLE = re.compile(r"\d$")                     # 경찰1, 군인2: a role name by default
LETTER = "0-9A-Za-z가-힣"
# what may follow a name that is said as a word: an honorific, then a vocative or a particle
HONORIFICS = ("선생님", "선배", "언니", "오빠", "누나", "씨", "님", "군", "양", "형")
PARTICLES = ("이한테", "이에게", "이랑은", "이라도", "이처럼", "이까지", "이부터", "이하고", "한테", "에게",
             "이랑", "하고", "처럼", "까지", "부터", "이가", "이는", "이를", "이도", "이의", "이와", "이야",
             "이요", "이지", "이네", "이냐", "이니", "이만", "이고", "께서", "께", "아", "야", "이", "가", "은",
             "는", "을", "를", "도", "랑", "의", "와", "과", "요", "만", "고")
# the name as a word in a sentence: '가온이라는 애', '가온이래', '가온이었어', '가온보다', '가온에게서',
# '가온이나' (the forms after a final consonant start with 이). These forms follow common words just
# as well ('사랑이라는 게', '영화보다', '하늘이나 보자', '보람이었어'): a name said only so tells
# nothing (``revealed_at`` goes by the surer forms; ``mentions`` with ``weak`` still finds them)
COPULA = (r"(?:이)?(?:었|였)[가-힣]{0,3}", r"(?:이)?라(?:는|고|서|면|니)?", r"(?:이)?란",
          r"(?:이)?래(?:요)?", r"이에요", r"예요", r"이세요", r"세요")
MORE = ("이한테서", "이에게서", "한테서", "에게서", "이보다", "보다", "이나", "나")
# words the writer uses as generic labels: a speaker the script calls so is a role, not a name to tell
GENERIC_WORDS = ("남자", "여자")
# a person telling their own name: '가온입니다', '가온이에요', '가온이요', '가온이라고 해요'
SELF = ("이라고", "이랍니다", "입니다", "이에요", "이예요", "인데요", "이고요", "라고", "랍니다", "예요",
        "이요", "이야", "이다", "인데", "요", "야", "다", "라")
# a name of one letter ('수', '진') is in too many words: only called with a vocative or an
# honorific ('수야', '진 선배'), or told as one's own name in so many words ('수입니다', '이름은 수')
VOCATIVES = ("아", "야")
SELF_ONE = ("이라고", "이랍니다", "입니다", "이에요", "이예요", "라고", "랍니다", "예요")
SCOPES = ("line", "from_here", "before", "scene", "person", "voice")
SCOPE_ALIASES = {"speaker": "person"}         # the review API's older word for '이 인물의 모든 줄'
SCOPE_KO = {"line": "이 줄", "from_here": "이 줄부터 끝까지", "before": "이 줄 앞까지", "scene": "이 장면",
            "person": "이 인물의 모든 줄", "voice": "같은 목소리"}
MODES = ("name", "generic")
REVEALS = ("auto", "line", "never")


def _alt(words) -> str:
    return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))


def parts(name) -> list[str]:
    """The people of a speaker name: '에루/마야카' -> ['에루', '마야카']."""
    return list(dict.fromkeys(p.strip() for p in str(name or "").split(SPLIT) if p.strip()))


def is_role(name, generic=()) -> bool:
    """A role name by default (no reveal to wait for): one that ends in a digit, or a generic label
    itself ('남자', '여자', or one of ``generic``: the labels the writer gives people)."""
    name = str(name or "").strip()
    return bool(ROLE.search(name)) or name in GENERIC_WORDS or name in set(generic or ())


def keys(name) -> list[str]:
    """How the name is written in a line: as it is, and without its spaces ('김 경위', '김경위')."""
    name = " ".join(str(name or "").split())
    out = [k for k in dict.fromkeys((name, name.replace(" ", ""))) if k]
    return [k for k in out if not re.fullmatch(r"[A-Za-z]", k)]           # 'A', 'B': speaker letters, no names


@lru_cache(maxsize=4096)
def _called(key: str, weak: bool = True):
    """The name said as a word: as it is, with an honorific or a particle (sure), and with
    ``weak`` also with a copula or a comparison (``COPULA``, ``MORE``)."""
    k, hon = re.escape(key), _alt(HONORIFICS)
    if len(key) == 1:
        # '수 씨', '수님', '수야'; '씨' written onto the letter and followed by a particle is a word
        # of its own ('날씨가', '솜씨는')
        attached = _alt([h for h in HONORIFICS if h != "씨"])
        return re.compile(rf"(?<![{LETTER}]){k}(?:\s(?:{hon})(?:{_alt(PARTICLES)})?|(?:{attached})(?:{_alt(PARTICLES)})?"
                          rf"|씨|{_alt(VOCATIVES)})(?![{LETTER}])")
    weak_tails = "|".join(list(COPULA) + [_alt(MORE)])
    sure = rf"(?:{hon})(?:{weak_tails}|{_alt(PARTICLES)})?|(?:{_alt(PARTICLES)})"
    tails = f"{sure}|{weak_tails}" if weak else sure
    return re.compile(rf"(?<![{LETTER}]){k}(?:{tails})?(?![{LETTER}])")


@lru_cache(maxsize=4096)
def _self(key: str):
    k = re.escape(key)
    own = _alt(SELF_ONE if len(key) == 1 else SELF)
    return re.compile(rf"(?<![{LETTER}]){k}(?:{own})(?![{LETTER}])"
                      rf"|이름[은이]\s*{k}(?![{LETTER}])|이름[은이]\s*{k}(?:{own})(?![{LETTER}])")


def mentions(text, name, weak: bool = True) -> bool:
    """Whether ``text`` says ``name`` as a word (module doc); ``weak=False``: only in the sure
    forms (as it is, with an honorific, a vocative or a particle — not only a copula or '보다')."""
    t = str(text or "")
    return any(k in t and _called(k, weak).search(t) for k in keys(name))


def introduces(text, name) -> bool:
    """Whether ``text`` — said by the person — tells their own name ('가온입니다', '제 이름은 가온')."""
    t = str(text or "")
    return any(k in t and _self(k).search(t) for k in keys(name))


def revealed_at(lines, name, key: str = "person"):
    """The first of ``lines`` (in the order given) that tells ``name``:
    ``{"line": id, "index": i, "by": speaker, "how": "called" | "self"}``, or None.
    ``line[key]`` is who speaks it (a name, or 'A/B' for two). A line that says the name only
    with a copula or a comparison ('하늘이나 보자', '보람이었어') tells nothing, even when a later
    line calls the name in a sure form or the person tells it: such a form follows a common word
    just as well ('하늘이나 보자' of the sky), so the name is told from that later line on (the
    generic label lasts longer — the safe side of II.8)."""
    if not name or not keys(name):
        return None
    for i, ln in enumerate(lines):
        who = parts(ln.get(key))
        text = ln.get("text")
        if name in who:
            if introduces(text, name):
                return {"line": ln.get("id"), "index": i, "by": ln.get(key), "how": "self"}
        elif mentions(text, name, weak=False):
            return {"line": ln.get("id"), "index": i, "by": ln.get(key), "how": "called"}
    return None


def generic_labels(decisions) -> set:
    """The generic labels the writer gives people (``before`` of the people labelled 'generic')."""
    return {" ".join(str(d.get("before")).split()) for d in (decisions or {}).values()
            if isinstance(d, dict) and d.get("mode") == "generic" and str(d.get("before") or "").strip()}


def table(script_people=(), names=(), generic=()) -> list[dict]:
    """The people: ``script.json`` ``people`` ({id, name, cues}) first, then every other name of
    ``names`` (speaker names of the lines, the writer's names) split into people. Each:
    ``{"id", "name", "cues", "role"}``; a name not in the script gets the id ``N:<name>``.
    ``generic``: the generic labels in use (``is_role``)."""
    out, seen = [], set()
    for p in script_people or ():
        if not isinstance(p, dict) or not p.get("name") or p["name"] in seen:
            continue
        seen.add(p["name"])
        out.append({"id": p.get("id") or f"N:{p['name']}", "name": p["name"], "cues": list(p.get("cues") or []),
                    "role": is_role(p["name"], generic)})
    for n in names or ():
        for one in parts(n):
            if one not in seen:
                seen.add(one)
                out.append({"id": f"N:{one}", "name": one, "cues": [], "role": is_role(one, generic)})
    return out


def _switch(name, dec, rev, index_of) -> float:
    """Index of the first line on which ``name`` itself may be shown."""
    how = (dec or {}).get("reveal") or "auto"
    if how == "never":
        return INF
    if how == "line" and (dec or {}).get("reveal_line") in index_of:
        return index_of[dec["reveal_line"]]
    return rev["index"] + 1 if rev else INF


def display(lines, decisions=None, script_people=()) -> dict:
    """Labels of ``lines`` — the lines in the film that are in the subtitles, in film order:
    ``{"id", "person" (name, 'A/B'), "scene_id", "show" (the label is shown), "text"}``.
    ``decisions``: {name: {"mode", "before", "reveal", "reveal_line"}} (module doc).

    Returns ``{"labels": {line id: name to show ('남자1', '가람', '남자/영희')},
    "generic": {line id: True when the label is a generic one}, "people": {name: info}}`` where
    info = ``{"name", "role", "mode" (decided or None), "before", "reveal", "told": revealed_at(),
    "from_line": first line shown under the name (id or None), "early": {"line", "told"} or None}``."""
    decisions = decisions or {}
    index_of = {ln.get("id"): i for i, ln in enumerate(lines)}
    names = list(dict.fromkeys(n for ln in lines for n in parts(ln.get("person"))))
    befores = generic_labels(decisions)
    info = {}
    for n in names:
        dec = decisions.get(n) or {}
        mode = dec.get("mode") if dec.get("mode") in MODES else None
        if mode == "generic" and not str(dec.get("before") or "").strip():
            mode = None
        rev = revealed_at(lines, n)
        info[n] = {"name": n, "role": is_role(n, befores), "mode": mode, "before": dec.get("before") if mode == "generic" else None,
                   "reveal": dec.get("reveal") or "auto", "reveal_line": dec.get("reveal_line"), "told": rev,
                   "switch": _switch(n, dec, rev, index_of), "from_line": None, "early": None}
    shown: dict = {}                      # line id -> [label of each person]
    generic: dict = {}
    for i, ln in enumerate(lines):
        labs, gen = [], False
        for n in parts(ln.get("person")):
            p = info[n]
            mode = p["mode"] or ("name" if p["role"] else None)
            if mode == "generic" and i < p["switch"]:
                labs.append(" ".join(str(p["before"]).split()))
                gen = True
                continue
            labs.append(n)
            if ln.get("show") and p["from_line"] is None and (mode == "name" or i >= p["switch"]):
                p["from_line"] = ln.get("id")
            if mode is None and ln.get("show") and i < p["switch"] and p["early"] is None:
                p["early"] = {"line": ln.get("id"), "told": (p["told"] or {}).get("line")}
        shown[ln.get("id")] = labs
        generic[ln.get("id")] = gen
    # [남자1] [남자2]: people under one label in one scene, in the order they first show it — by the
    # label as it is shown, so a speaker called '남자' in the script and a person the writer labels
    # '남자' are numbered too; a number another person's label already shows there is skipped
    order: dict = {}
    for i, ln in enumerate(lines):
        if not ln.get("show"):
            continue
        for n, lab in zip(parts(ln.get("person")), shown[ln.get("id")]):
            seen = order.setdefault((ln.get("scene_id"), lab), [])
            if n not in seen:
                seen.append(n)
    number: dict = {}
    for (scene, lab), who in order.items():
        # one person under the label, unless the scene also shows it with a number ('남자1' of the
        # script next to a person the writer labels '남자': [남자1] [남자2], never [남자1] [남자])
        if len(who) < 2 and not any(sc == scene and re.fullmatch(rf"{re.escape(lab)}\d+", lb) for sc, lb in order):
            continue
        taken = {lb for (sc, lb), w in order.items() if sc == scene and len(w) == 1 and lb != lab}
        k = 0
        for n in who:
            k += 1
            while f"{lab}{k}" in taken:
                k += 1
            number[(scene, lab, n)] = f"{lab}{k}"
    labels = {}
    for ln in lines:
        out = [number.get((ln.get("scene_id"), lab, n), lab)
               for n, lab in zip(parts(ln.get("person")), shown[ln.get("id")])]
        labels[ln.get("id")] = SPLIT.join(out) if out else None
    for p in info.values():
        p.pop("switch")
    return {"labels": labels, "generic": generic, "people": info}


def scope(value) -> str:
    """The scope name (older 'speaker' = 'person'); ValueError for an unknown one."""
    s = SCOPE_ALIASES.get(value or "line", value or "line")
    if s not in SCOPES:
        raise ValueError(f"모르는 범위입니다: {value} (쓸 수 있는 것: {', '.join(SCOPES)})")
    return s


def scope_lines(lines, line_id, which="line") -> list[str]:
    """Ids of the lines a speaker change on ``line_id`` applies to. ``lines``: every line in film
    order with ``id``, ``person``, ``scene_id`` and ``voice`` (a voice cluster, when there is one).
    'from_here' and 'before' count the person's own lines (from this one on / before this one),
    'scene' the person's lines in this scene, 'voice' the lines of the same voice cluster."""
    which = scope(which)
    idx = next((i for i, ln in enumerate(lines) if ln.get("id") == line_id), None)
    if idx is None:
        raise KeyError(line_id)
    me = lines[idx]
    if which == "line":
        return [line_id]
    if which == "voice":
        if me.get("voice") is None:
            return [line_id]
        return [ln["id"] for ln in lines if ln.get("voice") == me.get("voice")]
    if not me.get("person"):                  # a line nobody is named for: no 'same person' to go by
        return [line_id]
    same = [(i, ln) for i, ln in enumerate(lines) if ln.get("person") == me.get("person")]
    if which == "from_here":
        return [ln["id"] for i, ln in same if i >= idx]
    if which == "before":
        return [ln["id"] for i, ln in same if i < idx]
    if which == "scene":
        return [ln["id"] for i, ln in same if ln.get("scene_id") == me.get("scene_id")]
    return [ln["id"] for _, ln in same]
