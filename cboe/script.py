# -*- coding: utf-8 -*-
"""대본 읽기: dialogue lines with speaker names.

Accepted formats
- ``.txt``: one dialogue line per text line. ``이름: 대사`` (``:`` or ``：``) names the
  speaker; a line without a name prefix keeps the previous line's speaker only when
  it starts with ``"`` continuation (otherwise the speaker is unknown). Lines that
  are empty, start with ``#``, or are wholly in parentheses/brackets (stage
  directions such as ``(문 열리는 소리)``) are skipped.
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

NAME_RE = re.compile(r"^\s*([^:：()\[\]]{1,20}?)\s*[:：]\s*(.+?)\s*$")
PAREN_NAME_RE = re.compile(r"^\s*\(([^()]{1,30})\)\s*(.*)$")
DIRECTION_RE = re.compile(r"^\s*[\(\[（【].*[\)\]）】]\s*$")
TIME_RE = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _read_txt(path: Path) -> list[dict]:
    out = []
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        s = raw.strip()
        if not s or s.startswith("#") or DIRECTION_RE.match(s):
            continue
        m = NAME_RE.match(s)
        if m and not re.search(r"\d$", m.group(1)):         # avoid "12:30" style times
            out.append({"speaker": _clean(m.group(1)), "text": _clean(m.group(2))})
        else:
            out.append({"speaker": None, "text": _clean(s)})
    return out


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
                speaker = m.group(1).split("/")[0].strip()
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
    else:
        rows = _read_txt(path)
    lines, chars = [], []
    for i, r in enumerate(rows, 1):
        r = {"id": f"L{i:04d}", **r}
        lines.append(r)
        if r.get("speaker") and r["speaker"] not in chars:
            chars.append(r["speaker"])
    return {"source": str(path), "timed": bool(lines) and all("start" in r for r in lines),
            "lines": lines, "characters": chars}
