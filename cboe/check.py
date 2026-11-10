# -*- coding: utf-8 -*-
"""지침 검사기: an independent check of finished subtitles against the Netflix Korean SDH guide.

It takes only the result: a list of cells (``{"start", "end", "lines"}``), a draft dict or a
``draft.json``, or an SRT / VTT / TTML file. It shares no code with placement — only the II.2
character count (``textutil.units``) and the numbers of ``cboe.style`` — so it also catches what
placement gets wrong and what a hand-edited file breaks. Every violation names its clause and
whether that clause is an obligation (``must``) or a recommendation (``should``).

    python -m cboe check <file.srt | file.vtt | file.ttml | draft.json> [--fps F] [--cuts cuts.json] [--children]

``run(source, fps=None, cuts=None, profile=None)`` returns::

    {"ok": no must-level violation, "profile", "fps", "fps_assumed": no frame rate was given (24 is used),
     "cut_checks": whether shot changes were given, "cells": n,
     "violations": [{"cell", "index", "start", "code", "clause", "level": "must" | "should",
                     "message", "line"?: index of the line, "text"?: the line or what was found,
                     "next"?, "gap"?, "after"?, "flash"?: a "min_duration" cell of a few frames}],
     "rules": [{"code", "clause", "level", "title", "count", "cells": [ids]}],      # only rules that were broken
     "summary": {"cells", "must", "should", "cells_must", "cells_should", "by_code": {code: n}}}

Clauses (``RULES``): II.n / I.n = Korean Timed Text Style Guide, TG n = Subtitle Timing Guidelines,
GR n = General Requirements; "형식" = a cell that cannot be written at all.

``must`` = an unconditional instruction whose breach the file itself proves: the guide says must /
never / shall / do not, gives a number, or prescribes a form ("Use …", "please use the single smart
character", "If an entire phrase or sentence is bleeped, use '****'"); its (X) examples are must
too. ``should`` = a preference or an allowance (try / prefer / may / if possible / usually / be
mindful of), even where an (X) example illustrates it: II.10 prints "[영희와 철수] 설마! (X)" under
"Be mindful of readability ... if possible" (가능하면 가독성을 생각하라), so "joint_id" is should.
A cell of a few frames is one breach of GR 1, reported as "min_duration" with ``"flash": True``.
Every shot-change rule is should too: those are weighed against each other ("Apply
good judgement as many timing decisions may feel subjective" = 타이밍 판단은 주관적일 수 있으니 잘
판단하라) and some depend on what a file does not hold. TG 4 "When dialogue starts before a shot
change, the in-time must be at least half a second before the shot change" (대사가 전환 전에 시작하면
시작 시각은 전환보다 0.5초 이상 앞이어야 한다) binds dialogue that crosses the cut, but a file only
shows that the cell crosses it — a short line that ends before the cut may stay half a second into
the new shot (TG 1) — so "cut_in_before" is reported as a recommendation.

What it cannot see: whether the words are what is heard (II.1), whether a sound needed a caption or
its wording is the best one (most (X) examples of II.9), and the Netflix glyph list (GR 3; the list
is not part of the guide text). Those stay with the writer.
"""
from __future__ import annotations

import bisect
import html
import json
import math
import re
from pathlib import Path

from . import style
from .textutil import units

MUST, SHOULD = "must", "should"
FATAL = ("no_text", "bad_time")             # a cell with one of these cannot be written to a file
LEVEL_KO = {MUST: "의무", SHOULD: "권고"}

# code, clause, level, title ({...} = a number of the style in use)
RULES = (
    ("no_text", "형식", MUST, "글 없음"),
    ("bad_time", "형식", MUST, "시각 없음"),
    ("order", "형식", MUST, "시작 순서가 뒤바뀜"),
    ("line_length", "II.2", MUST, "한 줄 {max_units:g}자 초과"),
    ("line_count", "II.4", MUST, "{max_lines}줄 초과"),
    ("reading_speed", "II.3", MUST, "초당 {cps:g}자 초과"),
    ("min_duration", "GR 1", MUST, "{min_dur}초 미만"),          # "flash": True when only a few frames
    ("max_duration", "GR 1", MUST, "{max_dur:g}초 초과"),
    ("overlap", "TG 5", MUST, "겹침"),
    ("frame_gap", "TG 5", MUST, "간격 {gap_frames}프레임 미만"),
    ("short_gap", "TG 5", MUST, "빈틈 {half:g}초 미만"),
    ("short_gap_cut", "TG 5", SHOULD, "전환이 낀 빈틈 {half:g}초 미만"),
    ("line_end_punct", "I.13", MUST, "줄 끝 마침표·쉼표"),
    ("sentence_period", "I.13", MUST, "문장 사이 마침표 (쉼표로)"),
    ("dots", "I.4", MUST, "점 여러 개 (… 한 글자로)"),
    ("label_parens", "II.8·II.9", MUST, "( ) 로 감싼 표시 ([ ] 로)"),
    ("bracket", "II.8·II.9", MUST, "[ ] 짝이 안 맞음"),
    ("language_last", "II.8", MUST, "언어 표시가 화자 표시 맨 뒤가 아님"),
    ("note_glyph", "II.7", MUST, "♬ 같은 지침 밖 음표 (음악은 [ ], 가사는 ♪)"),
    ("lyric_notes", "II.7", MUST, "가사 음표 꼴 (처음과 끝에 ♪, 음표 옆 빈칸)"),
    ("hyphen", "II.5", MUST, "하이픈 줄 꼴 ('- ', 두 줄 모두)"),
    ("foreign_generic", "II.6", MUST, "[외국어] 표시 (어느 말인지 적는다)"),
    ("italics", "I.10", MUST, "기울임 글꼴 (쓰지 않는다)"),
    ("bleep_label", "II.10", MUST, "소리 끔을 표시로 적음 (* 로)"),
    ("sound_ending", "II.9", SHOULD, "'소리'·'들린다'로 끝나는 효과음"),
    ("joint_id", "II.10", SHOULD, "'[A와 B]' 꼴 화자 표시 ([A, B] 나 [함께])"),
    ("cut_in_before", "TG 4", SHOULD, "전환 {half:g}초 안 앞에서 시작해 전환을 넘음"),
    ("cut_in_after", "TG 2", SHOULD, "전환 뒤 {half:g}초 안에 시작"),
    ("cut_out_before", "TG 2", SHOULD, "전환 {half:g}초 안 앞에서 끝남"),
    ("cut_out_close", "TG 2", SHOULD, "끝이 전환 {gap_frames}프레임 안"),
    ("cut_out_after", "TG 4", SHOULD, "전환 뒤 {half:g}초 안에 끝남"),
)
_RULE = {code: (clause, level, title) for code, clause, level, title in RULES}
_ORDER = {code: k for k, (code, *_rest) in enumerate(RULES)}

# the patterns below are written here on purpose and not taken from style.tidy_line: the checker
# has to notice when the code that writes the lines is wrong
_HYPHEN = re.compile(r"^-\s*")
_LABEL = re.compile(r"\[([^\[\]]*)\]")
_LEAD_LABELS = re.compile(r"^(?:\[[^\[\]]*\]\s*)+")
_PAREN_LABEL = re.compile(r"^(?:-\s*)?\([^()]{1,30}\)")
_PAREN = re.compile(r"\(([^()]{1,30})\)")             # '(재채기)' anywhere in a line; '(25)' is no label
# '..', '...', '.…' and, inside a line, '….': one … (I.4); '….' at the end is a line-end period (I.13)
_DOTS = re.compile(r"\.[.…]|…\.(?!\s*$)")
# language names, to see where a label puts its language (II.8); a name that is not here is not seen
_LANGUAGES = frozenset((
    "영어 일본어 중국어 독일어 프랑스어 러시아어 스페인어 이탈리아어 포르투갈어 아랍어 베트남어 태국어 몽골어 "
    "힌디어 인도네시아어 말레이어 터키어 튀르키예어 그리스어 라틴어 광둥어 네덜란드어 히브리어 "
    "우크라이나어 타갈로그어 필리핀어 페르시아어 폴란드어 스웨덴어 노르웨이어 덴마크어 핀란드어 체코어 헝가리어 "
    "루마니아어 세르비아어 크로아티아어 불가리아어 우르두어 벵골어 타밀어 캄보디아어 크메르어 라오어 미얀마어 "
    "네팔어 스와힐리어 수어 한국어 한국말 일본말 중국말").split()) | {"표준 중국어"}
_SENTENCE_PERIOD = re.compile(r"[^\s.…,?!\dA-Za-z．。，、][.．。]\s+[^\s\[♪]")
_NO_SPACE_HYPHEN = re.compile(r"^-(?=[^\s\d-])")
_JOINT = re.compile(r"^(?:-\s*)?\[[^\[\]/]*\S[와과]\s+\S[^\[\]]*\]\s*\S")
_COMMAS, _PERIODS = ",，、", ".．。"
_OTHER_NOTES = "♬♫♩"
_TIME = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{1,2})[,.](\d{1,3})\s*-->\s*(?:(\d+):)?(\d{1,2}):(\d{1,2})[,.](\d{1,3})")
_VTT_TAG = re.compile(r"</?[A-Za-z][^<>]*>|<\d+:\d[\d:.]*>")
_SRT_TAG = re.compile(r"</?(?:i|b|u|font)(?:\s[^<>]*)?>|\{\\an?\d\}", re.I)
_ITALIC = re.compile(r"<i[\s.>]", re.I)
_AN_TOP = re.compile(r"\{\\an?[789]\}")


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


# --------------------------------------------------------------------------- reading files

def _secs(h, m, s, ms) -> float:
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms) / (10 ** len(ms))


def parse_subtitles(text: str, vtt: bool = False) -> list[dict]:
    """Cells of an SRT or VTT text in file order: ``[{"id": "#n", "start", "end", "lines"}]``.
    Markup is not text: VTT tags, the SRT tags ``<i> <b> <u> <font>`` and ``{\\anN}`` are taken
    out (``{\\an7}``-``{\\an9}`` put the cell on top). A cell that had ``<i>`` carries
    ``"italic": True`` (I.10)."""
    cells = []
    for block in re.split(r"\n[ \t]*\n", text.replace("\r\n", "\n").replace("\r", "\n").strip()):
        rows = block.split("\n")
        k = next((i for i, r in enumerate(rows) if _TIME.search(r)), None)
        if k is None:
            continue
        g = _TIME.search(rows[k]).groups()
        lines = [r for r in rows[k + 1:]]
        slanted = any(_ITALIC.search(r) for r in lines)
        top = any(_AN_TOP.search(r) for r in lines)
        if vtt:
            lines = [html.unescape(_VTT_TAG.sub("", r)) for r in lines]
        else:
            lines = [_SRT_TAG.sub("", r) for r in lines]
        c = {"id": f"#{len(cells) + 1}", "start": _secs(*g[:4]), "end": _secs(*g[4:]),
             "lines": [r.strip() for r in lines if r.strip()]}
        if slanted:
            c["italic"] = True
        if top:
            c["pos"] = "top"
        cells.append(c)
    return cells


def _ttml_time(value: str, fps: float, ticks: float = 1.0):
    """Seconds of a TTML time expression: offset time (``240f``, ``1.5s``, ``1500ms``, ``2m``,
    ``1h``, ``10000000t`` at ``ticks`` a second) or clock time (``00:00:10.000``, ``00:00:10:05``)."""
    v = (value or "").strip()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|f|t|s|m|h)", v)
    if m:
        n = float(m.group(1))
        return {"f": n / fps, "t": n / ticks, "s": n, "ms": n / 1000, "m": n * 60, "h": n * 3600}[m.group(2)]
    m = re.fullmatch(r"(\d+):(\d\d):(\d\d)(?:\.(\d+)|:(\d+)(?:\.\d+)?)?", v)
    if not m:
        return None
    t = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    if m.group(4):
        t += float("0." + m.group(4))
    if m.group(5):
        t += int(m.group(5)) / fps
    return t


def _ratio(value, default: float) -> float:
    """A ``"num den"`` multiplier (``ttp:frameRateMultiplier``) as a number."""
    parts = str(value or "").split()
    try:
        num, den = (float(x) for x in parts) if len(parts) == 2 else (default, 1.0)
    except ValueError:
        return default
    return num / den if den else default


def parse_ttml(text: str) -> tuple[list[dict], dict]:
    """Cells and ``{"fps"}`` of a TTML (or DFXP) document, from this tool or another one.

    Each ``<p>`` is a cell; its text is gathered in document order through nested ``<span>``s,
    a ``<br/>`` ends a line wherever it is, and runs of white space are one space (TTML's default
    ``xml:space``). A cell styled ``tts:fontStyle="italic"`` (on the ``<p>``, a ``<span>`` or a
    style they name) carries ``"italic": True``. Raises ValueError for a document that is not
    well-formed XML or whose root is not ``<tt>``."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(text.encode("utf-8"))
    except ET.ParseError as exc:
        raise ValueError(f"TTML 문서를 읽을 수 없습니다 ({exc})") from None
    ns, _sep, local = root.tag[1:].partition("}") if root.tag.startswith("{") else ("", "", root.tag)
    if local != "tt":
        raise ValueError(f"TTML 문서가 아닙니다 (맨 바깥 요소가 <{local}>)")
    tt = "{" + ns + "}" if ns else ""
    ttp = "{" + ns + "#parameter}" if ns else ""
    tts = "{" + ns + "#styling}" if ns else ""
    xml_id = "{http://www.w3.org/XML/1998/namespace}id"
    rate = _num(root.get(ttp + "frameRate"))
    fps = rate * _ratio(root.get(ttp + "frameRateMultiplier"), 1.0) if rate else None
    sub = _num(root.get(ttp + "subFrameRate")) or 1.0
    # TTML 1 ttp:tickRate: "If not specified, then if a frame rate is specified, the tick rate must
    # be considered to be the effective frame rate multiplied by the effective sub-frame rate;
    # otherwise ... one (1) tick per second" (없으면 실효 fps x 서브프레임 수, fps 도 없으면 1초에 1틱)
    ticks = _num(root.get(ttp + "tickRate")) or (fps * sub if fps else 1.0)
    italic_styles = {s.get(xml_id) for s in root.iter(tt + "style") if s.get(tts + "fontStyle") == "italic"}

    def italic(el) -> bool:
        return el.get(tts + "fontStyle") == "italic" or bool(set((el.get("style") or "").split()) & italic_styles)

    def gather(el, lines: list[list[str]]) -> bool:
        """Text of the children of ``el`` into ``lines`` (the last one is open); whether any is italic."""
        slanted = False
        for child in el:
            name = child.tag.rpartition("}")[2]
            if name == "br":
                lines.append([])
            elif (child.tag.startswith(tt) if tt else True) and name not in ("metadata", "set", "animate"):
                slanted |= italic(child)                       # a <span>: its text, then its children
                lines[-1].append(child.text or "")
                slanted |= gather(child, lines)
            lines[-1].append(child.tail or "")                 # text after the child belongs to this element
        return slanted

    cells = []
    use = fps or style.DEFAULT_FPS
    for p in root.iter(tt + "p"):
        lines = [[p.text or ""]]
        slanted = gather(p, lines) | italic(p)
        begin, end = _ttml_time(p.get("begin"), use, ticks), _ttml_time(p.get("end"), use, ticks)
        if end is None and begin is not None and p.get("dur"):
            d = _ttml_time(p.get("dur"), use, ticks)
            end = None if d is None else begin + d
        c = {"id": p.get(xml_id) or f"#{len(cells) + 1}", "start": begin, "end": end,
             "lines": [t for t in (" ".join("".join(x).split()) for x in lines) if t],
             "pos": "top" if p.get("region") == "top" else "bottom"}
        if slanted:
            c["italic"] = True
        cells.append(c)
    return cells, {"fps": fps}


def _cell_list(cells) -> list[dict]:
    """``cells`` as a list of cell dicts; ValueError when it is something else (a JSON ``[1, 2]``)."""
    if cells is None:
        return []
    if isinstance(cells, (str, bytes, dict)) or not hasattr(cells, "__iter__"):
        raise ValueError("칸 목록이 아닙니다 (칸은 {start, end, lines} 의 목록)")
    out = list(cells)
    bad = next((k for k, c in enumerate(out) if not isinstance(c, dict)), None)
    if bad is not None:
        raise ValueError(f"{bad + 1}번째 칸이 칸이 아닙니다: {str(out[bad])[:40]!r}")
    return out


def load(source) -> tuple[list[dict], dict]:
    """``(cells, meta)`` of a list of cells, a draft dict or a file (.srt .vtt .ttml .json).
    ``meta`` holds what the source says about itself: ``fps``, ``profile``. Raises ValueError for
    what holds no cells: a list of other values, XML that is not TTML, a file of another type."""
    if isinstance(source, dict):
        return _cell_list(source.get("cells")), {"fps": source.get("fps"), "profile": source.get("profile")}
    if isinstance(source, (str, Path)):
        path = Path(source)
        ext = path.suffix.lower()
        text = path.read_text(encoding="utf-8-sig")
        if ext == ".json":
            data = json.loads(text)
            if not isinstance(data, (dict, list)):
                raise ValueError("draft.json 은 칸 목록이나 {\"cells\": [...]} 이어야 합니다")
            return load(data)
        if ext in (".srt", ".vtt"):
            return parse_subtitles(text, vtt=ext == ".vtt"), {}
        if ext in (".ttml", ".xml", ".dfxp"):
            return parse_ttml(text)
        raise ValueError(f"검사할 수 없는 파일 형식입니다: {path.name} (srt · vtt · ttml · draft.json)")
    return _cell_list(source), {}


def load_cuts(source) -> list[float]:
    """Shot-change times (s) from ``cuts.json`` ({"cuts": [...]}), a JSON list, or a text file with
    one time a line; a list comes back as it is."""
    if source is None:
        return []
    if not isinstance(source, (str, Path)):
        return [v for v in (_num(x) for x in source) if v is not None]
    text = Path(source).read_text(encoding="utf-8-sig")
    try:
        data = json.loads(text)
    except ValueError:
        data = text.split()
    if isinstance(data, dict):
        data = data.get("cuts") or []
    return [v for v in (_num(x) for x in data) if v is not None]


# --------------------------------------------------------------------------- text rules

def _text_lines(c: dict) -> list[str]:
    lines = c.get("lines") or []
    if isinstance(lines, str):                        # one text instead of a list of lines
        lines = lines.split("\n")
    return [str(x).strip() for x in lines if str(x).strip()]


def _dual(lines: list[str]) -> bool:
    """A cell of hyphen lines: two speakers, or a speaker and a sound (I.6, II.5)."""
    return len(lines) >= 2 and all(x.startswith("-") for x in lines)


def _ends_with_punct(line: str) -> bool:
    s = line.rstrip()
    if len(s) > 1 and s.endswith(style.NOTE):        # the closing note of a lyric is not the text's end
        s = s[:-1].rstrip()
    s = s.rstrip("'\"’”」』")                         # nor is a closing quote: '난 안 그랬어.'
    if not s:
        return False
    if s[-1] in _COMMAS:
        return True
    return s[-1] in _PERIODS and s[-2:] != ".."      # '..' at the end is the dots rule


def _brackets_ok(text: str) -> bool:
    depth = 0
    for ch in text:
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        if depth not in (0, 1):
            return False
    return depth == 0


def _lyric_ok(unit: str) -> bool:
    """One sung unit (a hyphen line, or a whole cell): notes at both ends, a space next to each."""
    note = style.NOTE
    body = _LEAD_LABELS.sub("", _HYPHEN.sub("", unit.strip()))
    return (len(body) >= 5 and body.startswith(note + " ") and body.endswith(" " + note)
            and body.count(note) == 2)


def _text_rules(lines: list[str]):
    """(code, line index or None, text) for each text rule the cell breaks."""
    dual = _dual(lines)
    for k, line in enumerate(lines):
        if _ends_with_punct(line):
            yield "line_end_punct", k, line
        if _SENTENCE_PERIOD.search(line):
            yield "sentence_period", k, line
        if _DOTS.search(line):
            yield "dots", k, line
        if _PAREN_LABEL.match(line) or any(any(ch.isalpha() for ch in m.group(1)) for m in _PAREN.finditer(line)):
            yield "label_parens", k, line
        if any(ch in line for ch in _OTHER_NOTES):
            yield "note_glyph", k, line
        if _NO_SPACE_HYPHEN.match(line):
            yield "hyphen", k, line
        if _JOINT.match(line):
            yield "joint_id", k, line
    starts = [x.startswith("- ") for x in lines]
    if len(lines) >= 2 and any(starts) and not all(starts) and not any(_NO_SPACE_HYPHEN.match(x) for x in lines):
        yield "hyphen", starts.index(False), lines[starts.index(False)]
    # a label can run over the two lines of a cell, but never from one hyphen line into the next
    spans = list(enumerate(lines)) if dual else [(None, " ".join(lines))]
    for k, text in spans:
        if not _brackets_ok(text):
            yield "bracket", k, text
        for label in _LABEL.findall(text):
            body = label.strip()
            # '[영수/쉰 목소리]' is a speaker label with a manner of speech (the guide's own example)
            if body.endswith(("소리", "들린다")) and not body.endswith("목소리") and style.LABEL_SEP not in body:
                yield "sound_ending", k, f"[{label}]"
            if "외국어" in body:
                yield "foreign_generic", k, f"[{label}]"
            if "음 소거" in body:
                yield "bleep_label", k, f"[{label}]"
            parts = [x.strip() for x in body.split(style.LABEL_SEP)]
            if len(parts) > 1 and any(x in _LANGUAGES for x in parts[:-1]):
                yield "language_last", k, f"[{label}]"
    if any(style.NOTE in x for x in lines):
        for k, text in (list(enumerate(lines)) if dual else [(None, "\n".join(lines))]):
            if style.NOTE in text and not _lyric_ok(text):
                yield "lyric_notes", k, text.replace("\n", " / ")


# --------------------------------------------------------------------------- the check

def run(source, fps=None, cuts=None, profile=None) -> dict:
    """Check ``source`` (module doc). ``fps`` and ``profile`` given here win over what the source
    says; ``cuts`` (shot-change times in seconds, or a file) switches on the shot-change rules."""
    cells, meta = load(source)
    st = style.get(profile if profile is not None else meta.get("profile"))
    f = _num(fps) or _num(meta.get("fps"))
    assumed = not (f and f > 0)
    f = f if f and f > 0 else style.DEFAULT_FPS
    numbers = dict(st.settings(), min_dur=style.seconds_text(st.min_dur))
    min_f = math.ceil(st.min_dur * f - 1e-6)
    max_f = math.floor(st.max_dur * f + 1e-6)
    flash_f = math.ceil(st.flash_dur * f - 1e-6)
    half_f = math.floor(st.half * f + 0.5)
    gap = st.gap_frames

    ids = [str(c.get("id") or f"#{k + 1}") for k, c in enumerate(cells)]
    texts = [_text_lines(c) for c in cells]
    start = [_num(c.get("start")) for c in cells]
    end = [_num(c.get("end")) for c in cells]
    fs = [None if t is None else round(t * f) for t in start]
    fe = [None if t is None else round(t * f) for t in end]
    out: list[dict] = []

    def add(code, k, message=None, **extra):
        clause, level, title = _RULE[code]
        out.append({"cell": ids[k], "index": k, "start": start[k], "code": code, "clause": clause,
                    "level": level, "message": message or title.format(**numbers),
                    **{key: v for key, v in extra.items() if v is not None}})

    usable = []                                       # cells that have text and a time span
    for k, c in enumerate(cells):
        if not texts[k]:
            add("no_text", k)
            continue
        if start[k] is None or end[k] is None:
            add("bad_time", k)
            continue
        if end[k] <= start[k]:
            add("bad_time", k, "끝이 시작보다 빠름")
            continue
        usable.append(k)
        chars = [units(x) for x in texts[k]]
        dur = fe[k] - fs[k]
        for n, u in enumerate(chars):
            if u > st.max_units + 1e-9:
                add("line_length", k, line=n, text=texts[k][n])
                break
        if len(chars) > st.max_lines:
            add("line_count", k)
        if dur <= 0 or sum(chars) * f / dur > st.cps + 1e-6:
            add("reading_speed", k)
        if dur < min_f:                               # one breach of GR 1, however short the cell is
            few = dur < flash_f
            add("min_duration", k, text=f"{dur}프레임 · 몇 프레임짜리 칸" if few else None, flash=few or None)
        if dur > max_f:
            add("max_duration", k)
        for code, n, text in _text_rules(texts[k]):
            add(code, k, line=n, text=text)
        if c.get("italic"):
            add("italics", k)

    timed = [k for k in range(len(cells)) if start[k] is not None and end[k] is not None]
    for a, b in zip(timed, timed[1:]):                # file order
        if start[b] < start[a]:
            add("order", b, after=ids[a])
    cut_f = sorted({round(x * f) for x in load_cuts(cuts)}) if cuts is not None else None

    def cuts_in(lo, hi) -> list[int]:
        """Shot changes (frames) from ``lo`` to ``hi``, both included."""
        return cut_f[bisect.bisect_left(cut_f, lo):bisect.bisect_right(cut_f, hi)] if cut_f else []

    by_time = sorted(timed, key=lambda k: (start[k], end[k]))
    for a, b in zip(by_time, by_time[1:]):
        g = fs[b] - fe[a]
        extra = {"next": ids[b], "gap": round(start[b] - end[a], 3)}
        if g < 0:
            add("overlap", a, **extra)
        elif g < gap:
            add("frame_gap", a, **extra)
        elif gap < g < st.half * f - 1e-9:
            add("short_gap_cut" if cuts_in(fe[a], fs[b]) else "short_gap", a, **extra)

    if cut_f:
        ok_cells = set(usable)
        order = [k for k in by_time if k in ok_cells]
        for pos, k in enumerate(order):
            s, e = fs[k], fe[k]
            prev_e = fe[order[pos - 1]] if pos else None
            next_s = fs[order[pos + 1]] if pos + 1 < len(order) else None
            behind = cuts_in(s - half_f, s - 1)                # the cell starts up to half a second after a cut
            if behind and (prev_e is None or prev_e + gap <= behind[-1]):     # the cut is free to start on
                add("cut_in_after", k, text=f"전환 {s - behind[-1]}프레임 뒤")
            ahead = [x for x in cuts_in(s + 1, s + half_f - 1) if e > x]      # ... less than half a second before one
            if ahead:
                add("cut_in_before", k, text=f"전환 {ahead[0] - s}프레임 앞")
            done = set()
            for x in cuts_in(e - half_f + 1, e + half_f):      # cuts around the out-time
                d = x - e
                if 0 <= d < gap:
                    code = "cut_out_close"
                elif gap < d <= half_f and (next_s is None or next_s >= x) and x - gap - s <= max_f:
                    code = "cut_out_before"
                elif s < x and 0 < -d < half_f:
                    code = "cut_out_after"
                else:
                    continue
                if code not in done:
                    done.add(code)
                    add(code, k, text=f"전환 {abs(d)}프레임 {'앞' if d >= 0 else '뒤'}")

    out.sort(key=lambda v: (v["index"], _ORDER[v["code"]]))
    must = [v for v in out if v["level"] == MUST]
    should = [v for v in out if v["level"] == SHOULD]
    groups: dict = {}
    for v in sorted(out, key=lambda v: (_ORDER[v["code"]], v["level"] != MUST, v["index"])):
        g = groups.setdefault((v["code"], v["level"]), {
            "code": v["code"], "clause": v["clause"], "level": v["level"],
            "title": _RULE[v["code"]][2].format(**numbers), "count": 0, "cells": []})
        g["count"] += 1
        if v["cell"] not in g["cells"]:
            g["cells"].append(v["cell"])
    by_code: dict = {}
    for v in out:
        by_code[v["code"]] = by_code.get(v["code"], 0) + 1
    return {"ok": not must, "profile": st.profile, "fps": f, "fps_assumed": assumed, "cut_checks": bool(cut_f),
            "cells": len(cells), "violations": out, "rules": list(groups.values()),
            "summary": {"cells": len(cells), "must": len(must), "should": len(should),
                        "cells_must": len({v["index"] for v in must}),
                        "cells_should": len({v["index"] for v in should}), "by_code": by_code}}


# --------------------------------------------------------------------------- report and command line

def _stamp(t) -> str:
    if t is None:
        return "--:--:--.---"
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def report(res: dict, name: str = "", limit: int | None = 8) -> str:
    """The result as text, grouped by clause: obligations first. ``limit`` = cells listed per rule."""
    st = style.get(res["profile"])
    sm = res["summary"]
    head = f"SDH 자막 지침 검사 · {name + ' · ' if name else ''}{res['cells']}칸 · {res['fps']:g}fps"
    head += " (fps 를 몰라 24로 가정)" if res["fps_assumed"] else ""
    rows = [head + f" · {st.label} 프로그램 (초당 {st.cps:g}자)",
            f"의무 {sm['must']}건 ({sm['cells_must']}칸) · 권고 {sm['should']}건 ({sm['cells_should']}칸)"
            + ("" if res["cut_checks"] else " · 장면 전환 규칙은 보지 않음 (--cuts 없음)")]
    by_rule: dict = {}
    for v in res["violations"]:
        by_rule.setdefault((v["code"], v["level"]), []).append(v)
    for g in sorted(res["rules"], key=lambda g: (g["level"] != MUST, _ORDER[g["code"]])):
        rows.append("")
        rows.append(f"[{LEVEL_KO[g['level']]}] {g['clause']} {g['title']}: {g['count']}건")    # no dash: cp949 consoles
        items = by_rule[(g["code"], g["level"])]
        for v in items if limit is None else items[:limit]:
            what = v.get("text") or (f"다음 칸 {v['next']}, 사이 {v['gap']:+.3f}초" if "next" in v else "")
            rows.append(f"  {v['cell']} {_stamp(v['start'])}  {what}".rstrip())
        if limit is not None and len(items) > limit:
            rows.append(f"  … 그 밖 {len(items) - limit}건 (--all 로 모두)")
    if not res["violations"]:
        rows.append("걸린 조항이 없습니다")
    return "\n".join(rows)


def main(path, fps=None, cuts=None, profile=None, as_json: bool = False, show_all: bool = False) -> int:
    """``python -m cboe check``: prints the report; returns 1 when an obligation is broken, 2 when
    the file cannot be read."""
    import sys
    try:
        res = run(path, fps=fps, cuts=cuts, profile=profile)
    except (OSError, ValueError, SyntaxError, TypeError, AttributeError) as exc:    # SyntaxError: XML ParseError
        print(f"검사할 수 없습니다 · {exc}", file=sys.stderr)
        if as_json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    text = json.dumps(res, ensure_ascii=False, indent=1) if as_json else report(res, Path(path).name,
                                                                              None if show_all else 8)
    try:
        sys.stdout.reconfigure(errors="replace")      # a console code page without ♪ or …
    except (AttributeError, ValueError):
        pass
    print(text)
    return 0 if res["ok"] else 1
