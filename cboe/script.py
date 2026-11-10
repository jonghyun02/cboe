# -*- coding: utf-8 -*-
"""대본 읽기: the script of a film as a document (version 2).

Accepted files
- ``.pdf`` / ``.hwp``: a shooting script (촬영 대본) — scene headings, action, speaker cells with
  the dialogue beside them, directions in parentheses. Read by ``cboe.screenplay``. A PDF without
  a speaker column is read as text (below); a row that only goes on with the words of a full
  row above it is joined to that row, so a long ``이름: 대사`` stays one line.
- ``.txt`` / ``.docx``: told apart by what is in them. A text whose speaker cells are set off by
  a tab or a wide gap (or stand on the line above, under scene headings) is a shooting script.
  Otherwise one dialogue line per text line (a Word paragraph, a line break inside one, or a
  table row ``이름 | 대사``): ``이름: 대사`` (``:`` or ``：``) names the speaker and a line
  without a name has no speaker. Dubbing-script conventions are understood:
  - not dialogue: empty lines, comments starting with ``#``, lines without letters (``—``),
    lines that are nothing but directions (``(문 열리는 소리)``, ``<회상>`` — kept as action),
    scene headings ``S#1 [...]`` / ``#1`` (kept as scenes);
  - when there are scene headings, everything before the first one (title, cast list) is no
    dialogue;
  - directions inside a line (``(한숨)``, ``(호)``) and ``/`` pause marks are taken out of
    ``text`` (kept in ``raw`` and ``directions``); a line left without words is no line;
  - a line that starts with ``/`` continues the previous line.
- ``.tsv`` / ``.csv``: columns ``speaker`` and ``text`` (header required).
- ``.json``: ``[{"speaker": ..., "text": ...}]`` or ``{"lines": [...]}``.
- ``.srt`` / ``.vtt``: a timed dialogue subtitle; ``[이름] 대사``, ``[이름/영어] 대사``,
  ``(이름) 대사`` or ``이름: 대사`` give the speaker (and language), ``♪ 가사 ♪`` is sung, a row
  that is only ``[...]`` is a sound and no dialogue — except ``[이름]`` alone with the words going
  on in the next row without a hyphen (a cell broken right after a long label): it names the
  speaker of those words when the name labels words elsewhere in the file or the label and the
  words would not fit on one row, and it does not read as a sound (``[전화벨]``, ``[정적]``). Each
  row that starts with a hyphen, or names another speaker than the rows before it (``[가람] 가자.``
  / ``[나래] 응.``), is the line of another speaker (a two-speaker cell gives two lines with the
  time of the cell). Times are kept (``start``/``end``) so alignment can be skipped.
Text files are read as UTF-8, UTF-16 (with a byte order mark) or CP949.

Output (``read``)::

    {"version": 2, "format": "screenplay" | "lines" | "timed",
     "source": {"path", "sha1", "size", "mtime"}, "timed": bool,
     "characters": [names in order of appearance],
     "people": [{"id": "P01", "name", "cues": [the speaker cells written for this person]}],
     "scenes": [{"id": "SC001", "number", "heading", "place", "time"}],
     "elements": [{"id": "E0001", "type": "heading" | "action" | "dialogue" | "onscreen" |
                   "transition" | "other", "scene_id", "text", "line_id"?, ...}],
     "lines": [{"id": "L0001", "speaker", "text", "raw", "scene_id", "element_id", "language",
                "sung", "source", "delivery", "vocal", "directions", "start"?, "end"?}]}

``lines`` are the dialogues only, in order, as before: ``text`` is what is spoken (no
directions), ``raw`` the dialogue as written. ``language`` is the foreign language named by
``(영)``, ``(영어로)`` ...; ``sung`` is set by ``(노래)``; ``source`` (offscreen, narration, inner,
phone, radio, broadcast, video) comes from the speaker cell — ``길동(V.O)``, ``(NA)길동``, a cell
in parentheses ``(길동)`` — or a direction; ``delivery`` lists directions on the manner of
speech (``(웃으며)``, ``(웃다가 심각)``: said while laughing) and ``vocal`` the sounds the speaker
makes (``(한숨)``, ``(통곡)``, ``(키득)``); ``directions`` lists every direction with its kind, and
a sound that others make (``(학생들 폭소)``) carries ``by`` (``"학생들"``) and stays out of
``vocal``. In a shooting script nothing before the first scene heading is dialogue (title page,
cast list). ``summary`` gives the counts and warnings shown before an analysis starts.
"""
from __future__ import annotations

import codecs
import csv
import hashlib
import io
import json
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

from . import screenplay as sp
from . import style, textutil

VERSION = sp.VERSION
_TAG = r"[\(（][^()（）]{1,10}[\)）]"
NAME_RE = re.compile(rf"^\s*((?:{_TAG}\s*)?[^:：()\[\]（）]{{1,20}}?(?:\s*{_TAG})?)\s*[:：]\s*(.+?)\s*$")
PAREN_CUE_RE = re.compile(r"^\s*([\(（][^()（）]{1,12}[\)）])\s*[:：]\s*(.+?)\s*$")       # (소리): ...
TAG_RE = re.compile(_TAG)
PAREN_NAME_RE = re.compile(r"^\s*\(([^()]{1,30})\)\s*(.*)$")
BRACKET_NAME_RE = re.compile(r"^\s*\[([^\[\]]{1,30})\]\s*(.*)$")
SCENE_RE = re.compile(r"^\s*(?:S\s*)?#\s*\d+", re.I)       # S#1, #1
BRACKETS_RE = re.compile(r"\[[^\[\]]*\]|<[^<>]*>|【[^【】]*】|〈[^〈〉]*〉|《[^《》]*》")
INLINE_DIRECTION_RE = sp.PAREN_RE
LETTER_RE = sp.LETTER_RE
TIME_RE = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")
SOUND_LABEL_RE = re.compile(r"\s[-–—]\s|\s.*(?:다|소리|음|음악|효과음)$")      # [달칵 - 문], [문이 열린다]
MANY_LINES = 30       # from this many dialogue lines a script without speakers is not started
FEW_NAMED = 0.1       # ... nor one whose lines name a speaker this seldom
NOT_SPEAKER_RE = re.compile(rf"\s*(?:{sp._ONSCREEN}|시간|장소|날짜|일시|배경|때|곳)\s*", re.I)    # 자막: …, 시간: …

_clean = sp.clean
_spoken = sp.spoken


# ------------------------------------------------------------------ text files
def decode(data: bytes) -> str:
    """Text of a script file: UTF-8 (with or without a mark), UTF-16 with a mark, CP949 — and
    UTF-16 without a mark when that reads as plain Korean text."""
    if data.startswith(codecs.BOM_UTF8):
        data, order = data[3:], ("utf-8", "cp949")
    elif data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        order = ("utf-16",)
    else:
        order = ("utf-8", "cp949")
    for enc in order:
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    for enc in ("utf-16-le", "utf-16-be") if len(data) % 2 == 0 and len(order) > 1 else ():
        try:
            text = data.decode(enc)
        except UnicodeDecodeError:
            continue
        plain = sum(1 for ch in text if ch in "\r\n\t…“”‘’·" or " " <= ch <= "~" or "가" <= ch <= "힣")
        if text and plain >= 0.95 * len(text):
            return text
    raise ValueError("글 파일로 읽지 못했습니다. 메모장에서 '다른 이름으로 저장 → 인코딩: UTF-8'로 저장해 주세요")


def read_text(path) -> str:
    return decode(Path(path).read_bytes())


# ---------------------------------------------------------- 이름: 대사 줄 대본
def _split_named(s: str, known: set | None = None):
    """(speaker cell, dialogue) of an ``이름: 대사`` line, or None when the colon names nobody:
    ``12:30 회의`` (digits only), ``지금 3:30 이야`` (a time — unless the words before it are a
    name used elsewhere), ``http://...``."""
    m = PAREN_CUE_RE.match(s)
    if m:
        return m.group(1), m.group(2)
    m = NAME_RE.match(s)
    if not m:
        return None
    name = _clean(TAG_RE.sub(" ", m.group(1)))
    if not name or re.fullmatch(r"[\d\s]+", name) or m.group(2).startswith("//"):
        return None
    if re.search(r"\d\s*$", m.group(1)) and re.match(r"\d", m.group(2)) and re.search(r"\d[:：]\d", s):
        if known is None or re.sub(r"\s+", "", name) not in known:
            return None
    return m.group(1), m.group(2)


def _direction_only(s: str) -> bool:
    """Nothing but directions: ``(문 열리는 소리)``, ``<회상>``, ``[천둥] (잠시 뒤)``."""
    return not LETTER_RE.search(BRACKETS_RE.sub(" ", INLINE_DIRECTION_RE.sub(" ", s)))


def _starts_line(text: str) -> bool:
    """A row of a PDF read as text that begins a line of its own: ``이름: 대사``, a scene
    heading, a comment, a ``/`` continuation, a direction alone, a row without letters."""
    s = text.strip()
    return not LETTER_RE.search(s) or bool(SCENE_RE.match(s)) or s.startswith(("/", "#")) \
        or _direction_only(s) or bool(_split_named(s))


def lines_document(raw_lines: list[str]) -> dict:
    """Text lines of an ``이름: 대사`` script (.txt, .docx) -> the document, format ``lines``."""
    rows = [r.strip() for r in raw_lines]
    heads = [i for i, s in enumerate(rows) if SCENE_RE.match(s)]
    start = heads[0] if heads else 0                  # title and cast list come before the first scene
    known = {re.sub(r"\s+", "", _clean(TAG_RE.sub(" ", m.group(1))))
             for s in rows[start:] for m in [NAME_RE.match(s)] if m and not re.search(r"\d[:：]\d", s)}
    b = sp.Builder("lines")
    for i, s in enumerate(rows):
        scene = SCENE_RE.match(s)
        if not s or not LETTER_RE.search(s) or (s.startswith("#") and not scene):     # "# ...": a comment
            continue
        if i < start:
            b.other("other", s)
        elif scene:
            h = sp.heading_candidate(s) or {"number": re.search(r"\d+", s).group()}
            b.heading(s, h.get("number"), h.get("place"), h.get("time"))
        elif _direction_only(s):
            b.other("transition" if sp.is_transition(s) else "action", s)
        elif s.startswith("/"):                       # the previous line goes on
            b.extend_last(s)
        else:
            cue, text = _split_named(s, known) or (None, s)
            b.dialogue(cue, text)
    return b.document()


def parse_lines(raw_lines: list[str]) -> list[dict]:
    """Script text lines (from .txt or .docx) -> [{"speaker", "text"}] (the dialogues only)."""
    return [{"speaker": ln["speaker"], "text": ln["text"]} for ln in lines_document(raw_lines)["lines"]]


def _colon_cells(rows: list[str], ev: dict) -> list[str]:
    """In a script whose speaker cells are set off by a tab or a gap, ``이름: 대사`` of a name
    those cells use is a speaker cell too: the colon is read as the tab of the others (the
    words stay as written). A colon after any other word (``자막: …``) is left alone."""
    names = set(ev["_gap_names"]) | {sp._cue_key(m.group(1)) for r in rows for m in [sp.TAB_CUE.fullmatch(r)]
                                     if m and sp.cue_like(m.group(1))}
    out = []
    for r in rows:
        named = _split_named(r.strip()) if "\t" not in r else None
        if named and sp._cue_key(named[0]) in names and sp.cue_like(named[0]):
            r = f"{named[0].strip()}\t{named[1]}"
        out.append(r)
    return out


def read_rows(rows: list[str]) -> dict:
    """Text lines of a script of either kind: a shooting script when its speaker cells are
    set off from the dialogue (``cboe.screenplay.plain_evidence``) on more lines than name
    their speaker with a colon, else an ``이름: 대사`` script."""
    ev = sp.plain_evidence(rows)
    colon = sum(1 for r in rows if _split_named(r.strip()))
    if ev["cues"] >= sp.MIN_CUES and ev["cues"] > colon:
        doc = sp.build(sp.classify_plain(_colon_cells(rows, ev) if colon else rows, ev))
        doc["layout"] = max(("tab", "gap", "stacked"), key=lambda k: ev[k])
    else:
        doc = lines_document(rows)
    filled = [r for r in rows if r.strip()]
    if len(filled) >= 50:                             # text copied out of a PDF comes in pieces
        doc["fragments"] = round(sum(1 for r in filled if not LETTER_RE.search(r)) / len(filled), 3)
    return doc


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx_text(el) -> str:
    parts = []
    for n in el.iter():
        if n.tag == _W + "t":
            parts.append(n.text or "")
        elif n.tag == _W + "tab":
            parts.append("\t")
        elif n.tag in (_W + "br", _W + "cr"):
            parts.append("\n")
        elif n.tag == _W + "p" and parts:             # paragraphs inside a table cell
            parts.append("\n")
    return "".join(parts)


def docx_lines(path: Path) -> list[str]:
    """Text lines of a Word document in reading order: paragraphs (line breaks split them) and
    table rows, a row with a short first cell read as ``이름: 대사``."""
    import xml.etree.ElementTree as ET
    import zipfile
    try:
        with zipfile.ZipFile(path) as z:
            root = ET.fromstring(z.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError, EOFError):
        raise ValueError("Word(.docx) 파일을 읽지 못했습니다 (깨졌거나 Word 문서가 아닙니다)") from None
    out: list[str] = []

    def walk(parent):
        for el in parent:
            if el.tag == _W + "p":
                out.extend(_docx_text(el).split("\n"))
            elif el.tag == _W + "tbl":
                for row in el.iter(_W + "tr"):
                    cells = [_clean(_docx_text(c)) for c in row.findall(_W + "tc")]
                    cells = [c for c in cells if c]
                    if len(cells) >= 2 and len(cells[0]) <= 20 and not re.search(r"[:：]", cells[0]):
                        out.append(f"{cells[0]}: {' '.join(cells[1:])}")
                    elif cells:
                        out.append(" ".join(cells))
            elif el.tag == _W + "sdt":                # content controls hold paragraphs too
                content = el.find(_W + "sdtContent")
                if content is not None:
                    walk(content)

    walk(root.find(_W + "body"))
    return out


# ------------------------------------------------- tables, JSON, timed subtitles
def _times(r: dict) -> dict:
    return {"start": float(r["start"]), "end": float(r["end"])} if r.get("start") is not None \
        and r.get("end") is not None else {}


def _read_table(path: Path) -> dict:
    delim = "\t" if path.suffix.lower() == ".tsv" else ","
    rows = list(csv.DictReader(io.StringIO(read_text(path), newline=""), delimiter=delim))
    b = sp.Builder("lines")
    for r in rows:
        text = _clean(r.get("text") or r.get("대사") or "")
        if text:
            b.line(sp.parse_cue(r.get("speaker") or r.get("화자") or ""), text)
    return b.document()


LINE_KEYS = ("raw", "language", "sung", "source", "delivery", "vocal", "directions", "scene_id")


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(read_text(path))
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 파일을 읽지 못했습니다 ({exc.lineno}번째 줄 {exc.colno}번째 글자)") from None
    rows = data.get("lines", []) if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError('JSON 대본은 [{"speaker": …, "text": …}] 꼴의 줄 목록이어야 합니다')
    b = sp.Builder("lines")
    for r in rows:
        if not isinstance(r, dict):
            continue
        text = _clean(r.get("text") or "")
        if not text:
            continue
        ln = b.line(sp.parse_cue(r.get("speaker") or ""), text, **_times(r))
        ln.update({k: r[k] for k in LINE_KEYS if k in r})          # a script.json written by read()
    doc = b.document()
    if isinstance(data, dict) and isinstance(data.get("scenes"), list):
        doc["scenes"] = data["scenes"]
    return doc


def _secs(h, m, s, ms) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / (10 ** len(ms))


def _same_name(a: str | None, b: str | None) -> bool:
    """Two spellings of one name (spaces do not count)."""
    return bool(a) and bool(b) and re.sub(r"\s+", "", a) == re.sub(r"\s+", "", b)


def _label(text: str, cue: dict) -> bool:
    """Read a speaker label of a subtitle (``피터/영어``, ``영희/속마음``, ``영어``) into
    ``cue``. False when it is a sound label (``달칵 - 문``, ``문이 열린다``) and names nobody."""
    parts = [p.strip() for p in text.split("/") if p.strip()]
    kinds = [sp.direction_type(p) for p in parts]
    if len(parts) == 1 and (kinds[0][0] == "vocal" or (kinds[0][0] == "action" and SOUND_LABEL_RE.search(parts[0]))):
        if kinds[0][0] == "vocal":
            cue["vocal"].append(parts[0])
        return False
    for part, (kind, value) in zip(parts, kinds):
        if kind == "language":
            cue["language"] = value
        elif kind == "source":
            cue["source"] = cue["source"] or value
        elif kind == "sung":
            cue["sung"] = True
        elif cue["name"] is None and kind in ("action", "to"):
            cue.update(name=sp.parse_cue(part)["name"], raw=part)
        elif kind in ("action", "to") and _same_name(sp.parse_cue(part)["name"], cue["name"]):
            continue                                  # the speaker named again ('[나래] [웃음]' / '[나래] …'): no delivery
        else:
            cue["delivery" if kind != "vocal" else "vocal"].append(part)
    return True


@lru_cache(maxsize=1)
def _sound_words() -> frozenset:
    """The sound words and descriptions of the phrase table ('쾅', '천둥', '천둥 소리')."""
    from . import phrases
    out = set()
    for r in phrases.table().values():
        for w in (r["ono"], r["desc"]):
            if w:
                out.update((w, re.sub(r"\s*소리$", "", w)))
    return frozenset(out)


# a label alone on its row that reads as a sound, not a person: 음악 계속, 전화벨, 문 열림, 통화 중,
# 정적, 빵빵 (a sound word said twice)
SOUND_NAME_RE = re.compile(r"(?:음악|소리|효과음|벨|열림|닫힘|울림|계속)$|\s중$|^(?:정적|침묵|무음|적막|고요)$|^(\S+)\1$")
HYPHEN_RE = re.compile(r"[-–]\s*")


def _label_line(r: str, following: list[str], known: frozenset | set = frozenset()) -> bool:
    """A row that is only ``[이름]`` is the speaker label of the words on the next row when the
    cell was broken right after the label (a long label, ``[김철수/속마음]`` then the words): the
    next row goes on without a hyphen, and the label names a language, a source or somebody.
    Somebody is a name the file writes before words elsewhere (``known``), or a label that
    could not stand on one row with the words (why the cell was broken there) and does not
    read as a sound (``[쾅]``, ``[음악 계속]``, ``[전화벨]``, ``[정적]``, ``[한숨]``)."""
    m = BRACKET_NAME_RE.fullmatch(r)
    nxt = next((x for x in following if x), None)
    if not m or m.group(2) or nxt is None or HYPHEN_RE.match(nxt):
        return False
    if not LETTER_RE.search(BRACKETS_RE.sub(" ", INLINE_DIRECTION_RE.sub(" ", nxt))):
        return False                                  # the next row is a sound too
    probe = sp.parse_cue("")
    if not _label(m.group(1), probe):
        return False
    name = probe["name"]
    if not name or re.sub(r"\s+", "", name) in known:
        return True
    if name in _sound_words() or SOUND_NAME_RE.search(name):
        return False
    rest = " ".join(x for x in following if x)
    return "/" in m.group(1) or textutil.units(f"{r} {rest}") > style.MAX_UNITS + 1e-9


def _row_name(row: str, following: list[str], known) -> str | None:
    """The person a row of a cell names as its speaker: ``[나래] 응.``, ``(나래) 응.``, ``[나래]
    [웃음]``, or ``[나래]`` alone with the words on the next row (``_label_line``); None for a row
    that names nobody (``[쾅]``, ``(웃으며) 응.``, words alone)."""
    m = BRACKET_NAME_RE.match(row) or PAREN_NAME_RE.match(row)
    if not m or (m.group(2) and not LETTER_RE.search(m.group(2))):
        return None
    if not m.group(2) and not (row.lstrip().startswith("[") and _label_line(row, following, known)):
        return None
    probe = sp.parse_cue("")
    return probe["name"] if _label(m.group(1), probe) else None


def _speaker_rows(shown: list[str], known=None) -> list[list[str]]:
    """The rows of a cell by speaker: a row that starts with a hyphen starts the words of
    another speaker (II.5: ``- [미나] 누구세요?`` / ``- [준호] 나야``), rows without one go on with
    the speaker before — except, with ``known`` (``_labelled_names``), a row that names another
    speaker than the one its rows named (``- [가람] 가자.`` / ``[나래] 응.``: I.6, one speaker per
    row; II.8, the speaker ID where a speaker starts). The hyphen of a group's first row is taken
    off."""
    groups: list[list[str]] = []
    names: list[str | None] = []                     # the speaker each group's rows named so far
    for k, row in enumerate(shown):
        m = HYPHEN_RE.match(row)
        body = row[m.end():].strip() if m else row
        name = _row_name(body, shown[k + 1:], known) if known is not None else None
        if m or not groups or (name and names[-1] and not _same_name(name, names[-1])):
            groups.append([body])
            names.append(name)
        else:
            groups[-1].append(row)
            names[-1] = names[-1] or name
    return groups


def _subtitle_cells(path: Path) -> list[tuple[float, float, list[str]]]:
    """(start, end, shown rows) of every cell of an .srt / .vtt file."""
    out = []
    blocks = re.split(r"\n\s*\n", read_text(path).replace("\r\n", "\n").replace("\r", "\n").strip())
    for block in blocks:
        rows = block.strip().splitlines()
        tl = next((i for i, r in enumerate(rows) if TIME_RE.search(r)), None)
        if tl is None:
            continue
        g = TIME_RE.search(rows[tl]).groups()
        out.append((_secs(*g[:4]), _secs(*g[4:]), [re.sub(r"<[^>]+>", "", r).strip() for r in rows[tl + 1:]]))
    return out


def _labelled_names(cells) -> set[str]:
    """Names the file writes as the label of words (``[미나] 누구세요?``, ``(미나) 누구세요?``)."""
    out = set()
    for _, _, shown in cells:
        for group in _speaker_rows(shown):
            for r in group:
                m = BRACKET_NAME_RE.match(r) or PAREN_NAME_RE.match(r)
                if m and LETTER_RE.search(m.group(2)):
                    probe = sp.parse_cue("")
                    _label(m.group(1), probe)
                    if probe["name"]:
                        out.add(re.sub(r"\s+", "", probe["name"]))
    return out


def _vocal_heads(text: str) -> tuple[list[str], str]:
    """(the speaker's own sounds in brackets at the head of ``text``, the rest): ``[재채기] 나도야!``
    -> (['재채기'], '나도야!'). A bracket that is no such sound stays in the rest."""
    sounds = []
    text = text.strip()
    while True:
        m = BRACKET_NAME_RE.match(text)
        if not m or sp.direction_type(m.group(1).strip())[0] != "vocal":
            return sounds, text
        sounds.append(m.group(1).strip())
        text = m.group(2).strip()


def _subtitle_line(rows: list[str], known: set) -> tuple[dict, list[str]]:
    """The speaker cue and the words of the rows of one speaker in a cell."""
    cue = sp.parse_cue("")
    texts = []
    for k, r in enumerate(rows):
        if not r or r.startswith("♬"):
            continue                              # a music line
        if r.startswith("[") and _direction_only(r) and not _label_line(r, rows[k + 1:], known):
            own = BRACKET_NAME_RE.fullmatch(r)
            if own and not own.group(2) and sp.direction_type(own.group(1))[0] == "vocal":
                cue["vocal"].append(own.group(1).strip())     # in the speaker's rows: the speaker's own sound (II.5)
            elif own and own.group(2):            # '[나래] [웃음]': a speaker label, then the speaker's own sound
                probe = sp.parse_cue("")
                sounds = _vocal_heads(own.group(2))
                if sounds[0] and not sounds[1] and _label(own.group(1), probe) and probe["name"] and \
                        (cue["name"] is None or re.sub(r"\s+", "", cue["name"]) == re.sub(r"\s+", "", probe["name"])):
                    if cue["name"] is None:
                        _label(own.group(1), cue)
                    cue["vocal"].extend(sounds[0])
            continue                              # a sound line (a speaker label alone is read below)
        if r.startswith("♪"):                     # ♪ 가사 ♪
            r = r.strip("♪ ").strip()
            cue["sung"] = True
        m = BRACKET_NAME_RE.match(r) or PAREN_NAME_RE.match(r)
        if m:
            _label(m.group(1), cue)
            r = m.group(2).strip()
            sounds, r = _vocal_heads(r)           # '[가람] [재채기] 나도야!': the speaker's own sound (II.5)
            cue["vocal"].extend(sounds)
            if r.startswith("♪"):                 # [영희] ♪ 가사 ♪
                r = r.strip("♪ ").strip()
                cue["sung"] = True
        else:
            named = _split_named(r)
            if named:
                named_cue = sp.parse_cue(named[0])
                cue.update({k: v for k, v in named_cue.items() if v})
                r = named[1].strip()
        if r and LETTER_RE.search(r):
            texts.append(r)
    return cue, texts


def _read_subtitle(path: Path) -> dict:
    """A timed subtitle: one line per speaker of a cell — the two rows of a two-speaker cell
    (``- [미나] …`` / ``- [준호] …``, also ``[미나] …`` / ``[준호] …``: ``_speaker_rows``) are two lines
    with the time of the cell. Such lines carry
    ``turn`` "<cell>-<speaker>" (1-based): the hyphen says they are two speakers even when no
    name is written (``- 누구야?`` / ``- 나야``), and the speaker stays None. A speaker's own sound
    written alone after the label (``[미나] [웃음]``) goes to that speaker's words in the cell; with
    no words of that speaker in the cell (the cell is only that sound, or the other speaker of a
    two-speaker cell only laughs) it is kept in ``wordless``: {"id", "speaker", "vocal", "start",
    "end", "after_line" (the line before it in the file, or None), "raw", and ``turn`` in a cell of
    two speakers} — a speaker's own sound (``cboe.cues``), not a line."""
    b = sp.Builder("timed")
    cells = _subtitle_cells(path)
    known = _labelled_names(cells)
    wordless: list[dict] = []
    for n, (start, end, shown) in enumerate(cells, 1):
        groups = _speaker_rows(shown, known)
        read = [_subtitle_line(rows, known) for rows in groups]
        mine: list[dict] = []
        before: list[str | None] = []              # the line before each group in the file
        for g, (cue, texts) in enumerate(read, 1):
            before.append(b.lines[-1]["id"] if b.lines else None)
            if texts:
                extra = {"turn": f"{n}-{g}"} if len(groups) > 1 else {}
                mine.append(b.line(cue, _clean(" ".join(texts)), start=start, end=end, **extra))
        for g, ((cue, texts), rows) in enumerate(zip(read, groups), 1):
            if texts or not (cue["vocal"] and cue["name"]):
                continue
            to = next((ln for ln in mine if _same_name(ln["speaker"], cue["name"])), None)
            if to is not None:
                to["vocal"].extend(v for v in cue["vocal"] if v not in to["vocal"])
                continue
            w = {"id": f"W{len(wordless) + 1:04d}", "speaker": cue["name"], "vocal": list(cue["vocal"]),
                 "start": start, "end": end, "after_line": before[g - 1],
                 "raw": _clean(" ".join(r for r in rows if r))}
            if len(groups) > 1:
                w["turn"] = f"{n}-{g}"
            wordless.append(w)
    doc = b.document()
    if wordless:
        names = {re.sub(r"\s+", "", p["name"]): p["name"] for p in doc["people"]}
        for w in wordless:                            # the spelling the lines use
            w["speaker"] = names.get(re.sub(r"\s+", "", w["speaker"]), w["speaker"])
        doc["wordless"] = wordless
    return doc


# ------------------------------------------------------------------------ read
SUFFIXES = (".pdf", ".hwp", ".txt", ".docx", ".srt", ".vtt", ".csv", ".tsv", ".json")
FREE_TEXT = (".pdf", ".hwp", ".txt", ".docx")       # files that had to be told apart line by line


def file_signature(path) -> dict:
    """What identifies the script file a reading was made from."""
    path = Path(path)
    data = path.read_bytes()
    return {"path": str(path), "sha1": hashlib.sha1(data).hexdigest(), "size": len(data),
            "mtime": path.stat().st_mtime}


def read(path) -> dict:
    """The script document of a file (module doc)."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext in (".srt", ".vtt"):
        doc = _read_subtitle(path)
    elif ext in (".tsv", ".csv"):
        doc = _read_table(path)
    elif ext == ".json":
        doc = _read_json(path)
    elif ext == ".pdf":
        doc = sp.read_pdf(path, starts=_starts_line)
        if not isinstance(doc, dict):                 # a PDF without a speaker column: read its text
            doc = read_rows(doc)
    elif ext == ".hwp":
        doc = read_rows(sp.hwp_paragraphs(path))
    elif ext == ".hwpx":
        raise ValueError("HWPX(.hwpx) 는 아직 읽지 못합니다. 한글에서 HWP 나 PDF 로 저장해 주세요")
    elif ext == ".docx":
        doc = read_rows(docx_lines(path))
    else:
        doc = read_rows(read_text(path).splitlines())
    lines = doc["lines"]
    doc["source"] = file_signature(path)
    doc["timed"] = bool(lines) and all("start" in r for r in lines)
    if doc["timed"]:
        doc["format"] = "timed"
    return doc


def summary(doc: dict) -> dict:
    """Counts and warnings of a script document for the preview before an analysis.

    ``ok`` is False when starting an analysis with it would only waste the run: no dialogue,
    many dialogue lines of a text hardly any of which names its speaker (under a tenth, not
    counting ``자막:`` or ``시간:`` lines — a shooting script pasted as plain text), or
    "speakers" that are scene headings.
    """
    lines = doc.get("lines") or []
    elements = doc.get("elements") or []
    names = doc.get("characters") or []
    fmt = doc.get("format") or ("timed" if doc.get("timed") else "lines")
    n = len(lines)
    named = sum(1 for ln in lines if ln.get("speaker"))
    src = doc.get("source")
    ext = Path(src["path"] if isinstance(src, dict) else src or "").suffix.lower()
    free = ext in FREE_TEXT and fmt != "timed"
    languages = Counter(ln["language"] for ln in lines if ln.get("language"))
    sources = Counter(ln["source"] for ln in lines if ln.get("source"))
    out = {"ok": True, "error": None, "warnings": [], "format": fmt, "timed": bool(doc.get("timed")),
           "scenes": len(doc.get("scenes") or []), "lines": n, "named": named,
           "named_ratio": round(named / n, 3) if n else 0.0,
           "actions": sum(1 for e in elements if e.get("type") == "action"), "characters": len(names),
           "language": sum(languages.values()), "languages": dict(languages),
           "sung": sum(1 for ln in lines if ln.get("sung")),
           "offscreen": sum(sources.values()), "sources": dict(sources)}
    warn = out["warnings"]

    def fail(msg: str) -> dict:
        out.update(ok=False, error=msg)
        return out

    if not n:
        return fail("대사 줄을 찾지 못했습니다")
    if (doc.get("fragments") or 0.0) >= 0.3:
        return fail("글이 조각나 있습니다 (문장부호만 있는 줄이 많음). PDF 에서 글을 복사한 파일이면 PDF 파일을 그대로 넣어 주세요")
    heading_names = [nm for nm in names if sp.heading_candidate(nm)]
    heading_lines = sum(1 for ln in lines if ln.get("speaker") and sp.heading_candidate(
        f"{ln['speaker']} : {ln.get('raw') or ln.get('text') or ''}"))
    if free and (heading_names or heading_lines >= max(3, 0.2 * named)):
        eg = heading_names[0] if heading_names else next(
            ln["speaker"] for ln in lines if ln.get("speaker")
            and sp.heading_candidate(f"{ln['speaker']} : {ln.get('raw') or ln.get('text') or ''}"))
        return fail(f"인물 이름 자리에 장면 머리가 읽혔습니다 (예: {eg}). 촬영 대본은 PDF 나 HWP 파일을 그대로 넣어 주세요")
    # '자막: 1980년 서울', '시간: 오후 3시' name nobody: a line or two like that does not make a script with speakers
    people = sum(1 for ln in lines if ln.get("speaker") and not NOT_SPEAKER_RE.fullmatch(ln["speaker"]))
    if free and n >= MANY_LINES and people < FEW_NAMED * n:
        how = "하나도 없습니다" if not people else f"{people}줄에만 있습니다"
        return fail(f"대사 {n}줄에 화자가 {how}. 촬영 대본을 글로 옮긴 파일이면 PDF 나 HWP 원본을 넣고, "
                    "아니면 줄마다 '이름: 대사'로 적어 주세요")
    if named < n:
        share = round(100 * (n - named) / n)
        warn.append(f"화자 없는 대사가 {n - named}줄 있습니다" + (f" ({share}%)" if share else ""))
    if fmt == "screenplay":
        if doc.get("layout") == "stacked":
            warn.append("이름 줄 아래에 대사가 오는 글로 읽었습니다. 대사가 끝나는 곳은 어림한 것이니 PDF 나 HWP 원본이 있으면 "
                        "그 파일을 넣어 주세요")
        if not out["scenes"]:
            warn.append("장면 머리를 찾지 못했습니다")
        # what comes before the first scene (title page, cast list) is no line left unread
        other = sum(1 for e in elements if e.get("type") == "other" and (e.get("scene_id") or not out["scenes"]))
        if other > max(10, 0.05 * len(elements)):
            warn.append(f"무엇인지 가르지 못한 줄이 {other}개 있습니다")
    long_lines = sum(1 for ln in lines if len(ln.get("text") or "") > 600)
    if long_lines:
        warn.append(f"아주 긴 대사가 {long_lines}줄 있습니다 (지문이 대사에 붙었을 수 있음)")
    if doc.get("version") != VERSION:
        warn.append("예전 형식으로 읽은 대본입니다 (장면·괄호 지시 정보 없음)")
    return out
