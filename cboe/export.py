# -*- coding: utf-8 -*-
"""내보내기: the SRT / VTT / TTML writers for placed cells and the check that goes with them.

``write(cells, path, fmt, fps=...)`` writes the file and returns the result of the independent
guide check (``cboe.check.run``) on the same cells, with the draft's frame rate, so the review
page can show it. Limit problems are reported, not fixed: the writer decides (검수 화면 3.5).
Only what cannot be written at all is refused (no text, no times, end not after start).

``to_ttml`` follows General Requirements 13 ("All TTML files created for subtitles or SDH must
adhere to the following technical specifications" = 자막·SDH 용 TTML 파일은 다음 기술 규격을
따라야 한다): percentage values only, positioning with ``tts:textAlign`` and ``tts:displayAlign``
on two regions (bottom, top) that have static ``tts:extent`` and ``tts:origin``, ``tts:fontSize``
100%. Times are whole video frames (``240f`` with ``ttp:frameRate`` and, for 23.976 / 29.97 /
59.94, ``ttp:frameRateMultiplier="1000 1001"``) counted at the rate the header states, so that
the file gives back the seconds of the SRT within half a frame; ``xml:lang`` is ko; characters
XML 1.0 cannot hold (control characters) are left out. A cell's ``pos`` ("bottom" |
"top") picks the region. The sample snippet the guide links to was not at hand; the extent and
origin values are the usual 10 % safe margins and should be compared with the delivery spec.

``final_check(draft)`` keeps the earlier shape of the result for the limits placement promises
(16 units a line, 2 lines, reading speed, 5/6 s to 7 s, order, 2 frames between cells); it is a
view of ``cboe.check.run`` and holds no numbers of its own (they are in ``cboe.style``).
"""
from __future__ import annotations

import html
import math
import re
from fractions import Fraction
from pathlib import Path

from . import check

FORMATS = ("srt", "vtt", "ttml")
MEDIA_TYPES = {"srt": "application/x-subrip", "vtt": "text/vtt; charset=utf-8",
               "ttml": "application/ttml+xml; charset=utf-8"}
TTML_ORIGIN, TTML_EXTENT = "10% 10%", "80% 80%"      # static region box (General Requirements 13)
_LIMITS = ("line_length", "line_count", "reading_speed", "min_duration", "max_duration")


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
    lines = c.get("lines") or []
    if isinstance(lines, str):                       # one text instead of a list of lines
        lines = lines.split("\n")
    return [str(x) for x in lines if str(x).strip()]


def final_check(draft, fps: float | None = None, profile=None) -> dict:
    """Limit problems of a draft (``{"cells": [...], "no_room": [...]}`` or a bare list of cells).

    Returns ``{"ok", "fatal", "cells", "overlaps", "order", "no_room", "summary"}``: ``cells`` lists
    each cell with limit violations, ``overlaps`` cells closer than 2 frames to the next one,
    ``order`` cells that start before the one before them. ``ok`` only when nothing is listed.
    The notation and shot-change rules are in the full result of ``check.run``.
    """
    no_room = list(draft.get("no_room") or []) if isinstance(draft, dict) else []
    res = check.run(draft if isinstance(draft, dict) else list(draft or []), fps=fps, profile=profile)
    fatal, bad, overlaps, order = [], {}, [], []
    for v in res["violations"]:
        if v["code"] in check.FATAL:
            fatal.append({"cell": v["cell"], "index": v["index"], "reason": v["message"]})
        elif v["code"] in _LIMITS:
            bad.setdefault(v["index"], {"cell": v["cell"], "index": v["index"], "violations": []})
            bad[v["index"]]["violations"].append(v["message"])
        elif v["code"] in ("overlap", "frame_gap"):
            overlaps.append({"cell": v["cell"], "next": v["next"], "gap": v["gap"], "reason": v["message"]})
        elif v["code"] == "order":
            order.append({"cell": v["cell"], "index": v["index"], "after": v["after"]})
    bad = [bad[k] for k in sorted(bad)]
    pending = [{"item": r.get("item"), "t": r.get("t"), "reason": r.get("reason")} for r in no_room]
    ok = not (fatal or bad or overlaps or order or pending)
    return {"ok": ok, "fatal": fatal, "cells": bad, "overlaps": overlaps, "order": order, "no_room": pending,
            "summary": {"cells": res["cells"], "fatal": len(fatal), "violations": len(bad), "overlaps": len(overlaps),
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
        where = " line:10%" if c.get("pos") == "top" else ""       # a cell moved to the top of the picture
        out.append(f"{_ts(_num(c['start']), '.')} --> {_ts(_num(c['end']), '.')}{where}\n{body}\n")
    return "\n".join(out)


def frame_rate(fps: float) -> tuple[int, str | None]:
    """``ttp:frameRate`` and ``ttp:frameRateMultiplier`` of a video frame rate:
    24 -> (24, None), 23.976 -> (24, "1000 1001"), 29.97 -> (30, "1000 1001"). A rate a reader
    rounded (OpenCV gives 23.98 for 24000/1001 video) is taken as the NTSC rate next to it."""
    whole = max(1, round(fps))
    if abs(fps - whole) < 1e-3:
        return whole, None
    if abs(fps - whole * 1000 / 1001) < 5e-3:
        return whole, "1000 1001"
    exact = Fraction(fps).limit_denominator(1001)
    rate = max(1, math.ceil(exact))
    mult = exact / rate
    return rate, f"{mult.numerator} {mult.denominator}"


def effective_rate(fps: float) -> float:
    """The frame rate the TTML header of ``fps`` states (``ttp:frameRate`` x multiplier): frame
    numbers are counted with it, so that a reader of the file gets the seconds back (23.98 is
    written as 24 x 1000/1001, and so its frames are counted at 23.976 a second)."""
    rate, mult = frame_rate(fps)
    if not mult:
        return float(rate)
    num, den = (int(x) for x in mult.split())
    return rate * num / den


# XML 1.0 has no place for these characters (a control character from a hand-edited or HWP text
# would make the file unreadable): Char ::= #x9 | #xA | #xD | [#x20-#xD7FF] | [#xE000-#xFFFD] | ...
_NOT_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]")


def to_ttml(cells: list[dict], fps, title: str | None = None, lang: str = "ko") -> str:
    """The cells as a TTML document (module doc: General Requirements 13). ``fps`` is the frame
    rate of the video; every time is written as a whole frame."""
    fps = _num(fps)
    if not fps or fps <= 0:
        raise ValueError("TTML 로 내보내려면 영상의 fps 가 필요합니다")
    rate, mult = frame_rate(fps)
    per_s = effective_rate(fps)

    def esc(s) -> str:
        return html.escape(_NOT_XML.sub("", str(s)), quote=False)

    def region(name: str, align: str) -> str:
        return (f'      <region xml:id="{name}" tts:origin="{TTML_ORIGIN}" tts:extent="{TTML_EXTENT}" '
                f'tts:displayAlign="{align}" tts:textAlign="center"/>')

    rows = ['<?xml version="1.0" encoding="UTF-8"?>',
            '<tt xmlns="http://www.w3.org/ns/ttml" xmlns:ttp="http://www.w3.org/ns/ttml#parameter" '
            'xmlns:tts="http://www.w3.org/ns/ttml#styling" xmlns:ttm="http://www.w3.org/ns/ttml#metadata" '
            f'xml:lang="{html.escape(lang, quote=True)}" ttp:timeBase="media" ttp:frameRate="{rate}"'
            + (f' ttp:frameRateMultiplier="{mult}"' if mult else "") + ">",
            "  <head>"]
    if title:
        rows += ["    <metadata>", f"      <ttm:title>{esc(title)}</ttm:title>", "    </metadata>"]
    rows += ["    <styling>",
             '      <style xml:id="s1" tts:fontFamily="proportionalSansSerif" tts:fontSize="100%" '
             'tts:textAlign="center" tts:color="white"/>',
             "    </styling>",
             "    <layout>", region("bottom", "after"), region("top", "before"), "    </layout>",
             "  </head>", "  <body>", "    <div>"]
    for i, c in enumerate(_sorted_cells(cells), 1):
        begin, end = round(_num(c["start"]) * per_s), round(_num(c["end"]) * per_s)
        where = "top" if c.get("pos") == "top" else "bottom"
        text = "<br/>".join(t for t in (esc(x).strip() for x in _text_lines(c)) if t)
        rows.append(f'      <p xml:id="c{i}" begin="{begin}f" end="{end}f" region="{where}" style="s1">{text}</p>')
    rows += ["    </div>", "  </body>", "</tt>", ""]
    return "\n".join(rows)


def write(cells: list[dict], path, fmt: str = "srt", fps=None, cuts=None, profile=None,
          title: str | None = None) -> dict:
    """Write ``cells`` as SRT, VTT or TTML (UTF-8, ``\\n`` line ends) and check them against the guide.

    ``fps`` is the frame rate of the draft: the check counts frames with it (24 is assumed without
    it) and TTML needs it. ``cuts`` (shot-change times) switches on the shot-change rules of the
    check, ``profile`` is "adult" or "children". Returns ``{"path": Path, "format", "cells": number
    written, "check": check.run(...)}``. Raises ValueError for an unknown format and for cells that
    cannot be written at all.
    """
    fmt = str(fmt or "srt").lower().lstrip(".")
    if fmt not in FORMATS:
        raise ValueError(f"unknown subtitle format: {fmt}")
    res = check.run({"cells": cells, "fps": fps, "profile": profile}, cuts=cuts)
    fatal = [v for v in res["violations"] if v["code"] in check.FATAL]
    if fatal:
        raise ValueError("내보낼 수 없는 칸: " + ", ".join(f"{v['cell']} ({v['message']})" for v in fatal))
    text = to_srt(cells) if fmt == "srt" else to_vtt(cells) if fmt == "vtt" else to_ttml(cells, fps, title)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return {"path": path, "format": fmt, "cells": len(_sorted_cells(cells)), "check": res}
