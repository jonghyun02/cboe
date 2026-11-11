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

``maybe_same`` offers two names that may be one person (감사 3-m3: a script writes one person under
more than one speaker name) — offered only, never merged: the writer merges them by giving all the
lines of one the other name (scope 'person') or says they are not (``not_same``). Kept narrow: (가) a
family name in front of the given name ('김가람' and '가람'; not a title or a word of family:
'김형사' and '형사', '전남편' and '남편', '부대장' and '대장'), (나) a word of age or time with the
name ('어린 가람', '가람(아역)', '가람 (17세)' and '가람', or '가람(성인)'; not a word for a person
in front of a word of family, which may be its owner: '아기 엄마' and '엄마'). A name of a relation
('가람 엄마', '가람 친구', '가람모') is another person and never offered.
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
# maybe_same (감사 3-m3) — lists written by hand, not measured on data:
# (가) family names that may stand in front of a given name ('김가람': 김 + 가람)
SURNAMES = set("김이박최정강조윤장임한오서신권황안송류유전홍고문양손배백허남심노하곽성차주우구민진나지엄채원천방공"
               "현함변염여추도소석선설마길연위표명기반라왕금옥육인맹제모탁국어은편용예경봉사부가복태목형피두감음빈동온호범"
               "좌팽승간상시갈단견당")
DOUBLE_SURNAMES = ("남궁", "제갈", "선우", "황보", "독고", "사공", "서문", "동방")
# ... but not in front of a title or a word of family: '김형사' and '형사' are not one name told two
# ways, and '전남편' (a former husband) or '양아버지' is not '남편' / '아버지'
NOT_GIVEN_NAMES = {"남편", "아내", "부인", "엄마", "아빠", "어머니", "아버지", "남친", "여친", "애인", "아들", "딸내미",
                   "오빠", "언니", "누나", "동생", "할머니", "할아버지", "삼촌", "이모", "고모", "외삼촌", "아저씨",
                   "아줌마", "형사", "경찰", "경위", "경사", "순경", "반장", "팀장", "과장", "부장", "사장", "회장",
                   "실장", "대리", "기사", "선생", "선생님", "의사", "교수", "박사", "검사", "판사", "기자", "감독",
                   "주임", "계장", "소장", "원장", "국장", "차장", "대표", "이사", "비서", "사모", "학생", "손님",
                   "주인", "직원", "대위", "중위", "소위", "병장", "상병", "일병", "이병", "하사", "중사", "상사",
                   "소령", "중령", "대령", "장군", "남자", "여자",
                   # titles and jobs after a prefix that is also a family name (부·소·전·여:
                   # '부대장', '소대장', '전 대통령', '여배우') or after a family name ('박의원')
                   "대장", "중장", "준장", "원사", "준위", "경감", "경정", "총경", "경장", "군인", "병사",
                   "대통령", "총리", "장관", "의원", "의장", "시장", "군수", "이장", "총장", "단장",
                   "선장", "기장", "주장", "선수", "코치", "배우", "가수", "작가", "화가", "변호사", "회계사",
                   "목사", "신부", "수녀", "스님", "간호사", "약사", "교사", "강사", "요리사", "주방장",
                   "종업원", "점원", "점장", "지배인", "매니저", "사원", "회사원", "공무원", "연구원",
                   "승무원", "운전사", "경비", "경비원", "소방관", "경찰관", "교도관", "집사", "여사",
                   "사모님", "아가씨", "총각", "영감", "형님", "누님", "선배", "후배", "친구",
                   # titles of a period drama after a family-name letter ('왕세자', '황태자', '김대감');
                   # a title with '님' is looked up without it ('김교수님' / '교수님', _surname_of)
                   "세자", "태자", "왕자", "공주", "왕비", "왕후", "대군", "중전", "대비", "상궁", "내관",
                   "대감", "도령", "도련님", "나리", "마님", "아씨", "마마", "전하", "사또", "판서", "정승"}
# (나) words of age or time said with the name: before it with a space ('소년 가람'), or as a tail
# ('가람(아역)', '가람 아역'); the adjectives also written onto the name ('어린가람')
AGE_WORDS = ("어린 시절", "학창 시절", "어린시절", "학창시절", "어린", "젊은", "늙은", "꼬마", "아기", "아역", "성인",
             "청년", "중년", "노년", "노인", "소년", "소녀", "과거", "현재", "미래")
AGE_ATTACHED = ("어린", "젊은", "늙은")
AGE_NUMBER = r"\d+\s*(?:대|살|세|년도|년)"       # '20대', '17살', '17세', '1986년'
# ... but a word for a person in front of a word of family or a title may be its owner: '아기 엄마'
# (a baby's mother) or '소녀 할머니' is not '엄마' / '할머니' at another age; an adjective, a time or
# a tail ('어린 엄마', '과거 엄마', '엄마(아역)') still is
AGE_OWNERS = ("아기", "꼬마", "소년", "소녀", "청년", "노인")


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


# 같은 입력의 결과를 기억해 둔다 (감사 6-08: 작가가 하나 고칠 때마다 줄 수 × 인물 수만큼 이름을 찾았다). 이 모듈은
# 순수 함수라 ``display`` 와 ``maybe_same`` 의 결과는 인자로만 정해진다. 열쇠에는 함수가 읽는 것을 모두 넣는다:
# ``display`` 는 줄마다 id·person·scene_id·text 와 show 의 참거짓만 읽고, 결정은 통째로(``_freeze``) 넣는다.
# 돌려줄 때는 기억해 둔 것의 사본을 준다(받은 쪽이 고쳐도 기억은 그대로). ``MEMO = False`` 이면 늘 계산한다
MEMO = True
_DISPLAY_MEMO: dict = {}
_SAME_MEMO: dict = {}
_MEMO_MAX = 32                                # 열쇠 수 (넘으면 비운다): 작가가 결정을 바꿔 가는 동안의 몇 판


def clear_memo() -> None:
    """Forget what ``display`` and ``maybe_same`` remember."""
    _DISPLAY_MEMO.clear()
    _SAME_MEMO.clear()


_PLAIN = (str, int, float, bool, type(None))


def _freeze(x):
    """A key equal for two values only when they are the same JSON-like data, types included
    (a dict with its items in order, list, tuple, str, int, float, bool, None); TypeError else."""
    t = type(x)
    if t is dict:
        return (dict, tuple((_freeze(k), _freeze(v)) for k, v in x.items()))
    if t is list or t is tuple:
        return (t, tuple(_freeze(v) for v in x))
    if t in _PLAIN:
        return (t, x)
    raise TypeError(f"no key for {t.__name__}")


def _line_key(ln) -> tuple:
    """What ``_display`` reads of one line (``.get`` of id, person, scene_id, text; show as true
    or false); TypeError for a value whose type is not str, int or None."""
    if type(ln) is not dict:
        raise TypeError("a line is a dict")
    out = (ln.get("id"), ln.get("person"), ln.get("scene_id"), ln.get("text"))
    for v in out:
        if v is not None and type(v) is not str and type(v) is not int:
            raise TypeError("no key")
    return out + (bool(ln.get("show")),)


def _copy(blob: bytes):
    import marshal
    return marshal.loads(blob)


def _remember(memo: dict, key, value):
    import marshal
    try:
        blob = marshal.dumps(value)
    except ValueError:                        # something marshal cannot copy: not remembered
        return value
    if len(memo) >= _MEMO_MAX:
        memo.clear()
    memo[key] = blob
    return value


def display(lines, decisions=None, script_people=()) -> dict:
    """``_display``, remembered by everything it reads (``MEMO``); a fresh copy every time."""
    if MEMO and type(lines) in (list, tuple):     # (a generator would be used up by the key)
        try:
            key = (tuple(_line_key(ln) for ln in lines), _freeze(decisions or {}), _freeze(script_people))
            got = _DISPLAY_MEMO.get(key)
        except (TypeError, ValueError):
            got, key = None, None
        if got is not None:
            return _copy(got)
        if key is not None:
            return _remember(_DISPLAY_MEMO, key, _display(lines, decisions, script_people))
    return _display(lines, decisions, script_people)


def _display(lines, decisions=None, script_people=()) -> dict:
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


_AGE = _alt(AGE_WORDS)
_AGE_HEAD = re.compile(rf"^(?:(?:{_AGE}|{AGE_NUMBER})\s+|(?:{_alt(AGE_ATTACHED)})(?=[가-힣]{{2}}))(?P<base>.+)$")
_AGE_TAIL = re.compile(rf"^(?P<base>.+?)(?:\s*[(（\[]\s*(?:{_AGE}|{AGE_NUMBER}|\d+)\s*[)）\]]|\s+(?:{_AGE}|{AGE_NUMBER}))$")
_OWNER_HEAD = re.compile(rf"^(?:{_alt(AGE_OWNERS)})\s")


def _core(name) -> str:
    """The name without the words of age or time said with it ('어린 가람', '가람(아역)' -> '가람'),
    without spaces."""
    n = " ".join(str(name or "").split())
    for _ in range(2):                                  # '어린 가람(아역)': both sides
        m = _AGE_HEAD.match(n) or _AGE_TAIL.match(n)
        if not m:
            break
        n = m.group("base").strip()
    return n.replace(" ", "")


def _surname_of(longer: str, shorter: str) -> bool:
    """Whether ``longer`` is a family name and the given name ``shorter`` ('김가람', '가람'); not a
    title or a word of family, also with '님' after it ('김교수님', '교수님')."""
    if len(shorter) < 2 or not re.fullmatch(r"[가-힣]+", longer + shorter):
        return False
    if shorter in NOT_GIVEN_NAMES or (shorter.endswith("님") and shorter[:-1] in NOT_GIVEN_NAMES):
        return False
    head = longer[:-len(shorter)] if longer.endswith(shorter) else None
    return bool(head) and (head in SURNAMES or head in DOUBLE_SURNAMES)


def same_person_hint(a, b) -> str | None:
    """Why two names may be one person: "surname" (가), "age" (나), or None (module doc)."""
    a, b = " ".join(str(a or "").split()), " ".join(str(b or "").split())
    if not a or not b or a == b or is_role(a) or is_role(b):
        return None
    ca, cb = _core(a), _core(b)
    if not ca or not cb:
        return None
    if ca == cb:
        if ca in NOT_GIVEN_NAMES and (_OWNER_HEAD.match(a) or _OWNER_HEAD.match(b)):
            return None                                 # '아기 엄마': the baby's mother (AGE_OWNERS)
        return "age"
    if _surname_of(ca, cb) or _surname_of(cb, ca):
        return "surname"
    return None


def maybe_same(names, decisions=None) -> dict:
    """``_maybe_same``, remembered by its arguments (``MEMO``); a fresh copy every time."""
    if MEMO and type(names) in (list, tuple):
        try:
            key = (_freeze(names), _freeze(decisions or {}))
            got = _SAME_MEMO.get(key)
        except (TypeError, ValueError):
            got, key = None, None
        if got is not None:
            return _copy(got)
        if key is not None:
            return _remember(_SAME_MEMO, key, _maybe_same(names, decisions))
    return _maybe_same(names, decisions)


def _maybe_same(names, decisions=None) -> dict:
    """``{name: [other names that may be the same person]}`` among ``names`` (people, in their
    order), leaving out the pairs the writer said are not (``decisions[name]["not_same"]``, either
    way round). Only offered: nothing is merged (module doc)."""
    names = list(dict.fromkeys(n for n in names or () if n))
    decisions = decisions or {}

    def told_apart(a, b):
        return any(isinstance(decisions.get(x), dict) and y in (decisions[x].get("not_same") or ())
                   for x, y in ((a, b), (b, a)))
    out: dict = {}
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if same_person_hint(a, b) and not told_apart(a, b):
                out.setdefault(a, []).append(b)
                out.setdefault(b, []).append(a)
    return {n: [m for m in names if m in out[n]] for n in out}
