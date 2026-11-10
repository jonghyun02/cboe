# -*- coding: utf-8 -*-
"""내보내기: the SRT / VTT / TTML writers for placed cells and the check that goes with them.

``write(cells, path, fmt, fps=...)`` writes the file and returns the result of the independent
guide check (``cboe.check.run``) on the file it wrote, read back with the draft's frame rate, so
that what the review page is told is what the checker says of the file. Limit problems are
reported, not fixed: the writer decides (검수 화면 3.5).
Only what cannot be written at all is refused (no text, no times, end not after start).

``to_ttml`` follows General Requirements 13 ("All TTML files created for subtitles or SDH must
adhere to the following technical specifications" = 자막·SDH 용 TTML 파일은 다음 기술 규격을
따라야 한다): percentage values only, positioning with ``tts:textAlign`` and ``tts:displayAlign``
on two regions (bottom, top) that have static ``tts:extent`` and ``tts:origin``, ``tts:fontSize``
100%. Times are whole video frames (``240f`` with ``ttp:frameRate`` and, for 23.976 / 29.97 /
59.94, ``ttp:frameRateMultiplier="1000 1001"``; a rate within ``RATE_TOLERANCE`` of a whole or
NTSC rate is that rate, 30.000015 = ``30``; another rate as it is, 29999/1251 = ``24`` x
``29999 30024``) counted at the rate the header states, so that the file gives back the seconds
of the SRT within half a frame and the draft's frames (2 frames between cells stay 2); a cell's
``pos`` "top" is ``{\\an8}`` in SRT and ``line:10%`` in VTT; ``xml:lang`` is ko; characters
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


SRT_TOP = "{\\an8}"      # the tag most SRT players read as "top centre" (the checker reads it too)


def to_srt(cells: list[dict]) -> str:
    """The cells as SRT; a cell whose ``pos`` is "top" starts with ``{\\an8}`` (SRT itself has no
    position, so this is the usual tag; without it the cell came back at the bottom)."""
    out = []
    for i, c in enumerate(_sorted_cells(cells), 1):
        stamp = f"{_ts(_num(c['start']), ',')} --> {_ts(_num(c['end']), ',')}"
        lines = _text_lines(c)
        if c.get("pos") == "top":
            lines = [SRT_TOP + lines[0]] + lines[1:]
        out.append(f"{i}\n{stamp}\n" + "\n".join(lines) + "\n")
    return "\n".join(out)


def to_vtt(cells: list[dict]) -> str:
    out = ["WEBVTT\n"]
    for c in _sorted_cells(cells):
        body = "\n".join(html.escape(x, quote=False) for x in _text_lines(c))
        where = " line:10%" if c.get("pos") == "top" else ""       # a cell moved to the top of the picture
        out.append(f"{_ts(_num(c['start']), '.')} --> {_ts(_num(c['end']), '.')}{where}\n{body}\n")
    return "\n".join(out)


def _rounded(fps: float) -> bool:
    """Whether ``fps`` looks like a rate a reader rounded to at most three decimals (23.98, 29.97)."""
    return abs(fps * 1000 - round(fps * 1000)) < 1e-6


# How close (relative) a rate has to be to a whole or an NTSC rate to be taken as that rate. ffprobe's
# avg_frame_rate is frames over duration and wobbles a little: a 30 fps video gave 30.00001486621153
# (5e-7 off), which the header must not write as 31 x 22198001/22937923 (4th round check). 29999/1251
# (23.98002, 1.7e-4 off 23.976 and 8e-4 off 24) is a rate of its own and stays as it is. Over two
# hours 1e-5 is under a tenth of a second, and the frames of the file still read back to the
# draft's (``_ttml_frames``).
RATE_TOLERANCE = 1e-5


def _near(fps: float, rate: float) -> bool:
    return abs(fps - rate) <= RATE_TOLERANCE * rate


def frame_rate(fps: float) -> tuple[int, str | None]:
    """``ttp:frameRate`` and ``ttp:frameRateMultiplier`` of a video frame rate:
    24 -> (24, None), 23.976 -> (24, "1000 1001"), 29.97 -> (30, "1000 1001"). A rate a reader
    rounded (OpenCV gives 23.98 for 24000/1001 video) is taken as the NTSC rate next to it, and so
    is a rate within ``RATE_TOLERANCE`` of a whole or NTSC rate (30.000015 -> 30). Any other rate is
    written as it is, so that the frames of the file are the frames of the draft: ffprobe gives
    29999/1251 (23.98002) for some videos, which is ``24`` x ``29999 30024``, not 24 x 1000/1001
    (counted at 23.976 a 2-frame gap of the draft became 1 frame in the file)."""
    whole = max(1, round(fps))
    ntsc = whole * 1000 / 1001
    exact = Fraction(fps).limit_denominator(1_000_000)
    if abs(float(exact) - fps) > 1e-9 * max(1.0, fps):
        exact = Fraction(fps).limit_denominator(1001)        # no small fraction: the nearest simple one
    if exact == whole or _near(fps, whole) or (_rounded(fps) and abs(fps - whole) < 1e-3):
        return whole, None
    if exact == Fraction(whole * 1000, 1001) or _near(fps, ntsc) or (_rounded(fps) and abs(fps - ntsc) < 5e-3):
        return whole, "1000 1001"
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


def _gap_class(g: int, half: float):
    """What the guide check makes of a gap of ``g`` frames (TG 5): overlap, under 2 frames, 2,
    3 frames to half a second, half a second or more."""
    return 0 if g < 0 else 1 if g < 2 else 2 if g == 2 else 3 if g < half - 1e-9 else 4


def _ttml_frames(cells: list[dict], fps: float, per: float, profile=None) -> list[tuple[int, int]]:
    """(begin, end) frames at the rate ``per`` the TTML header states, for the sorted ``cells`` of a
    draft at ``fps``. When the two rates are one (the usual case, ``frame_rate``) the frames are
    the draft's own. Otherwise (a rounded 23.98 written as 24 x 1000/1001) each time on the draft's
    frame grid gets the TTML frame that a reader counting at ``fps`` takes back to the same draft
    frame, so that 2-frame gaps, 5/6 s and the like come out of the file as they went in; at the
    few draft frames no TTML frame reads back to (the coarser grid skips one every few thousand
    frames) the neighbour is taken that keeps the gap and the duration on the same side of the
    guide's limits. A time off the draft's grid is rounded at ``per`` (within half a frame)."""
    from . import style as _style
    st = _style.get(profile)
    min_f = math.ceil(st.min_dur * fps - 1e-6)
    max_f = math.floor(st.max_dur * fps + 1e-6)
    flash_f = math.ceil(st.flash_dur * fps - 1e-6)
    half = st.half * fps
    r = fps / per                                     # draft frames per TTML frame

    def draft_frame(t: float):
        kd = round(t * fps)
        return kd if abs(t * fps - kd) <= 0.0005 * fps + 1e-6 else None   # placement times: frames in ms

    def candidates(t: float) -> list[int]:
        """TTML frames for ``t``: the exact one, or the two around a draft frame the grid skips."""
        kd = draft_frame(t)
        if kd is None:
            return [round(t * per)]
        k = round(kd / r)
        exact = [x for x in (k, k - 1, k + 1) if round(x * r) == kd]
        if exact:
            return [min(exact, key=lambda x: abs(x - kd / r))]
        lo = max(x for x in (k - 2, k - 1, k, k + 1) if round(x * r) < kd)
        return [lo, lo + 1]

    def dur_class(n: int, u: float):
        return (n < flash_f, n < min_f, n > max_f, n <= 0 or u * fps / n > st.cps + 1e-6)

    from .textutil import units as _units
    frames: list[tuple[int, int]] = []
    rows = [(_num(c["start"]), _num(c["end"]), sum(_units(x) for x in _text_lines(c))) for c in cells]
    for i, (s, e, u) in enumerate(rows):
        ks, ke = round(s * fps), round(e * fps)
        prev = frames[-1][1] if frames else None
        prev_d = round(rows[i - 1][1] * fps) if i else None
        nxt_d = round(rows[i + 1][0] * fps) if i + 1 < len(rows) else None

        def cost(b: int, en: int) -> int:
            """How many of the gap before, the duration and the gap after change their verdict."""
            n = 0
            if prev is not None:
                n += _gap_class(round(b * r) - round(prev * r), half) != _gap_class(ks - prev_d, half)
            n += dur_class(round(en * r) - round(b * r), u) != dur_class(ke - ks, u)
            if nxt_d is not None:
                n += _gap_class(nxt_d - round(en * r), half) != _gap_class(nxt_d - ke, half)
            return n

        options = [(b, en) for b in candidates(s) for en in candidates(e) if en > b] or \
                  [(round(s * per), max(round(e * per), round(s * per) + 1))]
        frames.append(min(options, key=lambda be: (cost(*be), abs(be[0] - s * per) + abs(be[1] - e * per))))
    return frames


def to_ttml(cells: list[dict], fps, title: str | None = None, lang: str = "ko", profile=None) -> str:
    """The cells as a TTML document (module doc: General Requirements 13). ``fps`` is the frame
    rate of the video (the draft's); every time is written as a whole frame (``_ttml_frames``).
    ``profile`` ("adult" | "children") only decides which limits a skipped frame must not cross."""
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
    keep = _sorted_cells(cells)
    for i, (c, (begin, end)) in enumerate(zip(keep, _ttml_frames(keep, fps, per_s, profile)), 1):
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
    written, "check": check.run(...)}``; ``check`` is the check of the file as written, read back
    (``file_check``), so it says what ``python -m cboe check <file> --fps <fps>`` says of it.
    Raises ValueError for an unknown format and for cells that cannot be written at all.
    """
    fmt = str(fmt or "srt").lower().lstrip(".")
    if fmt not in FORMATS:
        raise ValueError(f"unknown subtitle format: {fmt}")
    res = check.run({"cells": cells, "fps": fps, "profile": profile}, cuts=cuts)
    fatal = [v for v in res["violations"] if v["code"] in check.FATAL]
    if fatal:
        raise ValueError("내보낼 수 없는 칸: " + ", ".join(f"{v['cell']} ({v['message']})" for v in fatal))
    text = (to_srt(cells) if fmt == "srt" else to_vtt(cells) if fmt == "vtt"
            else to_ttml(cells, fps, title, profile=profile))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return {"path": path, "format": fmt, "cells": len(_sorted_cells(cells)),
            "check": file_check(path, cells, fps=fps, cuts=cuts, profile=profile)}


def file_check(path, cells: list[dict] | None = None, fps=None, cuts=None, profile=None) -> dict:
    """``check.run`` of a written file read back, with the draft's frame rate (``fps``) and, when
    ``cells`` are the cells it was written from, their ids in place of the file's own (``#3``,
    ``c3``): the file holds them in time order, as ``_sorted_cells`` does."""
    keep = _sorted_cells(cells or [])
    if cells is not None and not keep:               # nothing was written: an empty file has no cells
        return check.run({"cells": [], "fps": fps, "profile": profile}, cuts=cuts)
    found, _meta = check.load(path)
    if len(keep) == len(found):
        for c, src in zip(found, keep):
            if src.get("id"):
                c["id"] = src["id"]
    return check.run({"cells": found, "fps": fps, "profile": profile}, cuts=cuts)
