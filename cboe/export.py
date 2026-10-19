# -*- coding: utf-8 -*-
"""내보내기: last check of a draft and the SRT / VTT writer for placed cells.

``final_check(draft)`` re-validates what placement promises (DESIGN.md §6, 10-13 memo 5.1)
from the cells' text and times, without trusting their ``check``: every cell has text and a
start before its end, cells are in time order, do not overlap and keep 2 frames between them,
16 units a line (II.2), 2 lines (II.4), 14 units/s (II.3), 5/6 s to 7 s (General Requirements
1). The draft's ``no_room`` items are listed as still to place. Limit problems are reported,
not fixed: the writer decides (검수 화면 3.5). ``write`` runs it first and refuses only what
cannot be written at all (``fatal``: no text, no times, end not after start).
"""
from __future__ import annotations

import html
import math
from pathlib import Path

from .textutil import units

MAX_UNITS, MAX_LINES, CPS = 16.0, 2, 14.0
MIN_DUR, MAX_DUR, GAP_FRAMES = 5 / 6, 7.0, 2
DEFAULT_FPS = 30.0          # a draft without "fps": the most lenient common frame rate for 2 frames


def _ts(t: float, sep: str) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _text_lines(c: dict) -> list[str]:
    return [str(x) for x in (c.get("lines") or []) if str(x).strip()]


def final_check(draft, fps: float | None = None) -> dict:
    """Problems of a draft (``{"cells": [...], "no_room": [...]}`` or a bare list of cells).

    Returns ``{"ok", "fatal", "cells", "overlaps", "order", "no_room", "summary"}``: ``cells`` lists
    each cell with limit violations, ``overlaps`` cells closer than 2 frames to the next one,
    ``order`` cells that start before the one before them. ``ok`` only when nothing is listed.
    """
    if isinstance(draft, dict):
        cells = list(draft.get("cells") or [])
        no_room = list(draft.get("no_room") or [])
        fps = fps or _num(draft.get("fps"))
    else:
        cells, no_room = list(draft or []), []
    fps = fps if fps and fps > 0 else DEFAULT_FPS
    min_f = math.ceil(MIN_DUR * fps - 1e-6)
    max_f = math.floor(MAX_DUR * fps + 1e-6)
    fatal, bad, overlaps, order = [], [], [], []
    for i, c in enumerate(cells):
        cid = c.get("id") or f"#{i + 1}"
        s, e, lines = _num(c.get("start")), _num(c.get("end")), _text_lines(c)
        if s is None or e is None or e <= s or not lines:
            why = "글 없음" if not lines else "시각 없음" if s is None or e is None else "끝이 시작보다 빠름"
            fatal.append({"cell": cid, "index": i, "reason": why})
            continue
        chars = [units(x) for x in lines]
        dur_f = round((e - s) * fps)                 # whole frames, as placement counts them
        v = []
        if any(u > MAX_UNITS + 1e-9 for u in chars):
            v.append("한 줄 16자 초과")
        if len(lines) > MAX_LINES:
            v.append("2줄 초과")
        if dur_f <= 0 or sum(chars) * fps / dur_f > CPS + 1e-6:
            v.append("초당 14자 초과")
        if dur_f < min_f:
            v.append("5/6초 미만")
        if dur_f > max_f:
            v.append("7초 초과")
        if v:
            bad.append({"cell": cid, "index": i, "violations": v})
    good = [(i, c) for i, c in enumerate(cells) if _num(c.get("start")) is not None and _num(c.get("end")) is not None]
    for (i, a), (j, b) in zip(good, good[1:]):
        sa, sb = _num(a["start"]), _num(b["start"])
        if sb < sa:
            order.append({"cell": b.get("id") or f"#{j + 1}", "index": j, "after": a.get("id") or f"#{i + 1}"})
    ordered = sorted(good, key=lambda ic: (_num(ic[1]["start"]), _num(ic[1]["end"])))
    for (i, a), (j, b) in zip(ordered, ordered[1:]):
        gap_f = round((_num(b["start"]) - _num(a["end"])) * fps)
        if gap_f < GAP_FRAMES:
            overlaps.append({"cell": a.get("id") or f"#{i + 1}", "next": b.get("id") or f"#{j + 1}",
                             "gap": round(_num(b["start"]) - _num(a["end"]), 3),
                             "reason": "겹침" if gap_f < 0 else "간격 2프레임 미만"})
    pending = [{"item": r.get("item"), "t": r.get("t"), "reason": r.get("reason")} for r in no_room]
    ok = not (fatal or bad or overlaps or order or pending)
    return {"ok": ok, "fatal": fatal, "cells": bad, "overlaps": overlaps, "order": order, "no_room": pending,
            "summary": {"cells": len(cells), "fatal": len(fatal), "violations": len(bad), "overlaps": len(overlaps),
                        "order": len(order), "no_room": len(pending)}}


def _sorted_cells(cells: list[dict]) -> list[dict]:
    keep = [c for c in cells if _text_lines(c) and _num(c.get("start")) is not None and _num(c.get("end")) is not None]
    return sorted(keep, key=lambda c: (_num(c["start"]), _num(c["end"])))


def to_srt(cells: list[dict]) -> str:
    out = []
    for i, c in enumerate(_sorted_cells(cells), 1):
        stamp = f"{_ts(_num(c['start']), ',')} --> {_ts(_num(c['end']), ',')}"
        out.append(f"{i}\n{stamp}\n" + "\n".join(_text_lines(c)) + "\n")
    return "\n".join(out)


def to_vtt(cells: list[dict]) -> str:
    out = ["WEBVTT\n"]
    for c in _sorted_cells(cells):
        body = "\n".join(html.escape(x, quote=False) for x in _text_lines(c))
        out.append(f"{_ts(_num(c['start']), '.')} --> {_ts(_num(c['end']), '.')}\n{body}\n")
    return "\n".join(out)


def write(cells: list[dict], path, fmt: str = "srt") -> Path:
    """Write ``cells`` as SRT or VTT (UTF-8) after ``final_check``; raises ValueError on fatal cells."""
    fmt = str(fmt or "srt").lower().lstrip(".")
    if fmt not in ("srt", "vtt"):
        raise ValueError(f"unknown subtitle format: {fmt}")
    chk = final_check({"cells": cells})
    if chk["fatal"]:
        raise ValueError("내보낼 수 없는 칸: " + ", ".join(f"{x['cell']} ({x['reason']})" for x in chk["fatal"]))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = to_srt(cells) if fmt == "srt" else to_vtt(cells)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path
