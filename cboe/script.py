# -*- coding: utf-8 -*-
"""대본 읽기: dialogue lines with speaker names.

Accepted formats
- ``.txt`` / ``.docx``: one dialogue line per text line (a Word paragraph, a line break inside
  one, or a table row ``이름 | 대사``). ``이름: 대사`` (``:`` or ``：``) names the speaker; a tag
  in parentheses next to the name, as in ``(NA)호타로:`` or ``호타로(E):``, is dropped from it.
  A line without a name has no speaker. Dubbing-script conventions are understood:
  - skipped lines: empty, starting with ``#``, wholly in parentheses/brackets (stage directions
    such as ``(문 열리는 소리)`` or ``<회상>``), without letters (``—``), scene headings ``S#1 [...]``;
  - when there are scene headings, everything before the first one (title, cast list) is skipped;
  - directions inside a line (``(한숨)``, ``(호)``) and ``/`` pause marks are taken out of the text,
    and a line left empty by that is skipped;
  - a line that starts with ``/`` continues the previous line.
- ``.tsv`` / ``.csv``: columns ``speaker`` and ``text`` (header required).
- ``.json``: ``[{"speaker": ..., "text": ...}]`` or ``{"lines": [...]}``.
- ``.srt`` / ``.vtt``: a timed dialogue subtitle; ``(이름) 대사`` or ``이름: 대사``
  prefixes give the speaker. Times are kept (``start``/``end``) so alignment can
  be skipped.

Output: ``{"lines": [{"id", "speaker", "text", "start"?, "end"?}], "characters": [...]}``
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

_TAG = r"[\(（][^()（）]{1,10}[\)）]"
NAME_RE = re.compile(rf"^\s*((?:{_TAG}\s*)?[^:：()\[\]（）]{{1,20}}?(?:\s*{_TAG})?)\s*[:：]\s*(.+?)\s*$")
TAG_RE = re.compile(_TAG)
PAREN_NAME_RE = re.compile(r"^\s*\(([^()]{1,30})\)\s*(.*)$")
DIRECTION_RE = re.compile(r"^\s*[\(\[（【<〈《].*[\)\]）】>〉》]\s*$")
SCENE_RE = re.compile(r"^\s*S\s*#\s*\d+", re.I)
INLINE_DIRECTION_RE = re.compile(r"[\(（][^()（）]*[\)）]")
LETTER_RE = re.compile(r"[0-9A-Za-z가-힣ㄱ-ㅎㅏ-ㅣぁ-んァ-ン一-龥]")
TIME_RE = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _spoken(text: str) -> str:
    """Dialogue text without in-line directions and ``/`` pause marks."""
    return _clean(re.sub(r"(?<!\d)\s*/\s*(?!\d)", " ", INLINE_DIRECTION_RE.sub(" ", text)))   # keeps "1/2"


def parse_lines(raw_lines: list[str]) -> list[dict]:
    """Script text lines (from .txt or .docx) -> [{"speaker", "text"}]."""
    rows = [r.strip() for r in raw_lines]
    scenes = [i for i, s in enumerate(rows) if SCENE_RE.match(s)]
    if scenes:                                        # title and cast list come before S#1
        rows = rows[scenes[0]:]
    out: list[dict] = []
    for s in rows:
        if not s or s.startswith("#") or SCENE_RE.match(s) or DIRECTION_RE.match(s) or not LETTER_RE.search(s):
            continue
        if s.startswith("/"):                         # the previous line goes on
            if out:
                out[-1]["text"] = _clean(out[-1]["text"] + " " + _spoken(s))
            continue
        m = NAME_RE.match(s)
        name = _clean(TAG_RE.sub(" ", m.group(1))) if m else ""
        if m and name and not re.fullmatch(r"[\d\s]+", name):     # "12:30 회의" is not a name
            speaker, text = name, _spoken(m.group(2))
        else:
            speaker, text = None, _spoken(s)
        if text and LETTER_RE.search(text):
            out.append({"speaker": speaker, "text": text})
    return out


def _read_txt(path: Path) -> list[dict]:
    return parse_lines(path.read_text(encoding="utf-8-sig").splitlines())


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
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
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


def _read_docx(path: Path) -> list[dict]:
    return parse_lines(docx_lines(path))


def _read_table(path: Path) -> list[dict]:
    delim = "\t" if path.suffix.lower() == ".tsv" else ","
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f, delimiter=delim))
    out = []
    for r in rows:
        text = _clean(r.get("text") or r.get("대사") or "")
        if text:
            sp = _clean(r.get("speaker") or r.get("화자") or "") or None
            out.append({"speaker": sp, "text": text})
    return out


def _read_json(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    rows = data.get("lines", []) if isinstance(data, dict) else data
    return [{"speaker": (_clean(r.get("speaker") or "") or None), "text": _clean(r["text"]),
             **({"start": float(r["start"]), "end": float(r["end"])} if "start" in r and "end" in r else {})}
            for r in rows if _clean(r.get("text", ""))]


def _secs(h, m, s, ms) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / (10 ** len(ms))


def _read_subtitle(path: Path) -> list[dict]:
    out = []
    blocks = re.split(r"\n\s*\n", path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").strip())
    for block in blocks:
        rows = block.strip().splitlines()
        tl = next((i for i, r in enumerate(rows) if TIME_RE.search(r)), None)
        if tl is None:
            continue
        g = TIME_RE.search(rows[tl]).groups()
        a, b = _secs(*g[:4]), _secs(*g[4:])
        speaker, texts = None, []
        for r in rows[tl + 1:]:
            r = re.sub(r"<[^>]+>", "", r).strip().lstrip("-").strip()
            if not r or r.startswith("[") or r.startswith("♬") or r.startswith("♪"):
                continue
            m = PAREN_NAME_RE.match(r) or NAME_RE.match(r)
            if m:
                speaker = TAG_RE.sub(" ", m.group(1)).split("/")[0].strip()
                r = m.group(2).strip()
            if r:
                texts.append(r)
        if texts:
            out.append({"speaker": speaker, "text": _clean(" ".join(texts)), "start": a, "end": b})
    return out


def read(path) -> dict:
    path = Path(path)
    ext = path.suffix.lower()
    if ext in (".srt", ".vtt"):
        rows = _read_subtitle(path)
    elif ext in (".tsv", ".csv"):
        rows = _read_table(path)
    elif ext == ".json":
        rows = _read_json(path)
    elif ext == ".docx":
        rows = _read_docx(path)
    else:
        rows = _read_txt(path)
    lines, chars = [], []
    for i, r in enumerate(rows, 1):
        r = {"id": f"L{i:04d}", **r}
        lines.append(r)
        for name in (r.get("speaker") or "").split("/"):       # "에루/마야카": both speak
            name = name.strip()
            if name and name not in chars:
                chars.append(name)
    return {"source": str(path), "timed": bool(lines) and all("start" in r for r in lines),
            "lines": lines, "characters": chars}
