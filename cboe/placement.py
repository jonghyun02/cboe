# -*- coding: utf-8 -*-
"""끼워 넣기 (5.9): place dialogue lines and included sound items into subtitle cells.

Contract (called by ``state.Project.recompute``, output = DESIGN.md §3 Draft)::

    place(lines, sounds, cuts, fps, window=(t0, t1), settings=None) -> draft dict

lines  : DialogueLine dicts (id, speaker, text, start, end, words[{w,s,e}]) plus
         ``show_name`` (bool) and ``name`` (str | None), and optionally ``label_extra`` (source or
         manner of speech shown after the name: ["속마음"]), ``language`` ("영어"), ``sung`` (bool),
         ``timing`` ("asr" | "script" | "voice" | "even"), ``status`` ("absent" and "out_of_range"
         lines have no time and are skipped; "unheard" = time is a guess) and ``pos`` ("top").
sounds : included sound items only: {id, kind: "sfx"|"music", onset, t1 (sound end),
         phrase, short, source_line (line id whose speaker made the sound, or None),
         move_to (writer-forced start time, or None)}.
cuts   : shot-change times (s).
returns: {"cells": [{"id", "start", "end", "lines": [str], "refs": [ids], "kind",
                     "pos": "bottom" | "top", "flags": ["timing_estimated", "overlap", "flash"],
                     "check": {"chars": [..], "n_lines", "cps", "dur", "gap_next", "violations": [..]}}],
          "no_room": [{"item", "t", "text", "reason"}],
          "summary": {"cells", "violations", "no_room", "timing_estimated", "overlap", "flash", ...},
          "fps": fps, "profile": "adult" | "children", "notation": style.NOTATION}

Rules: DESIGN.md §6 = 10-13 memo §5, with the Netflix sources in demo/third_party/netflix
(General Requirements 1 and 4, Subtitle Timing Guidelines = TG 1-6, Korean II.2-II.10). Every
time is a whole video frame (the nearest one, memo 1.1 ⑦), written in seconds rounded to the ms.
Pure Python and deterministic; the only library is Kiwi inside textutil.wrap (optional).

Limits and notation come from ``cboe.style`` (``settings`` = ``style.get(profile).settings()``):
16 units a line (II.2, textutil.units), 2 lines (II.4), 14 units/s or 11 for children (II.3),
5/6 s to 7 s (General Requirements 1), 2 frames between cells, and a gap under half a second is
closed to 2 frames (TG5).

Dialogue cells. One cell per script line. In front of it ``[이름/출처·말투/언어] `` (II.8: the name
when ``show_name``, then ``label_extra``, the language last; "에루/마야카" = both speak ->
``[에루, 마야카]`` or ``[함께]``, II.10). A sung line is ``♪ … ♪`` (II.7). A line that does not fit
2 x 16 units or whose speech is longer than 7 s is split at word boundaries into cells that fit,
few and balanced, cut after sentence punctuation (then commas) where possible (_split), using the
word times; the name only on the first piece, but the language label and the notes on every piece
(II.6 "in each event", II.7 "each subtitle"), all counted in the 16 units. After splitting and
line breaking the punctuation of each line is tidied for display (style.tidy_line, I.13 and I.4:
no period or comma at a line end, a comma between two sentences, … for dots); the words are never
changed. In-time = first word, moved back to a shot change at
most half a second before it unless that would cut the speech (or the 5/6 s) of the cell before
(TG2); speech that starts less than half a second before a cut and goes on past it starts on the
cut, or half a second before it when it starts 9-11 frames (at 24 fps) before it (TG4), also when
a cut before the line could not be used.
Out-time = last word + 0.5 s, raised to 5/6 s and to 14 units/s where there is room, never later
than 7 s or 2 frames before the next cell (TG1, TG3). This covers memo 5.2's name rule: a name that
pushes the cell over 14 units/s makes it longer. Two short lines of different speakers share one
hyphen cell ("- [빌] …" / "- [세바스찬] …", I.6, TG6) only when the first cannot get 5/6 s alone,
neither line's speech plus half a second reaches 5/6 s, and each fits one line.

Lines that overlap (_untangle). When a line starts while the line before is still being spoken
and so close that the cell before cannot be read (5/6 s and the reading speed), the cell before
is not cut down to a frame: ① two whole lines of different speakers that fit one line each share
a hyphen cell; ② else, when the time of one of them is only a guess (``timing`` voice / even), that
one gives way: to the side it has in the script when there is room there, else to the other side,
else right behind the reading time of the other; ③ else the later one waits until the earlier one
has had 5/6 s (when it still gets 5/6 s itself) and both cells carry the flag "overlap".
A cell from a line whose time is a guess carries "timing_estimated"; a cell shorter than a quarter
of a second carries "flash", so that it never goes out unnoticed.

Sound cells (memo 5.2). sfx: start = onset frame, never earlier, even near a cut (BBC 5.5);
length = units / 14 + 1 s (BBC 4.3.3) within [5/6, 7] s, whatever the sound's own length.
music: start = onset, or the shot change in the half second before it (TG2); length 3 s, or
music end + 0.5 s when that comes first, at least 5/6 s. ``move_to`` (the writer's) replaces
the onset.

Positions (memo 5.3), sounds in time order:
 ① no cell on screen at the sound (cells before it may be shortened to their floor: speech end,
   5/6 s and 14 units/s) and at least 5/6 s free after it: an own cell;
 ② within 0.5 s of the start of a one-line cell, other source: the same cell, "- " lines in heard
   order (two sounds of a sound-only cell too, II.5);
 ③ the same, sound made by the speaker (source_line is that line): no hyphen,
   "[지수] [재채기] 나도야!" on one line, or on two when it does not fit one (II.5);
 ④ more than 0.5 s after the start of a dialogue cell while its speech goes on: the cell is split at
   the word boundary nearest the sound (at most 0.5 s from it) and the sound joins the later part,
   which starts at the sound (BBC 5.5, II.9); both parts at least 5/6 s;
 ⑤ over a limit, in order: short phrase -> longer cell end into the gap after it -> an own cell in a
   gap that starts at most 0.5 s after the sound -> ``no_room`` with the reason ("소리 뒤 빈틈 0.42초").
One sound per dialogue cell, never into a cell that already has two lines, the words of the
dialogue are never changed (only line breaks and the display punctuation), and every sound ends up
in a cell or in ``no_room``. A sound phrase of an older work folder ('♬ 무서운 음악') is written in
today's notation ('[무서운 음악]').

End adjustment (memo 5.2 끝 조정, TG1, 2, 4, 5), every cell in time order: ① a gap under half a
second to the next cell is closed to 2 frames; ② an out-time within half a second before a shot
change moves to 2 frames before it (when the next cell starts after the cut); ③ a cell whose audio
is over before a shot change it crosses ends 2 frames before the cut when 5/6 s and 14 units/s
still hold (a dialogue cell that cannot stays at least half a second into the new shot); a dialogue
cell whose speech crosses the cut runs at least half a second past it where there is room; a sound
that goes on past the cut may cross it. A gap of 3 frames to half a second that ③ leaves without a
cut in it is closed again (or, when 7 s forbids it, widened to half a second). Such a gap with a cut
in it is fine (the out-time sits 2 frames before the cut); any other is reported ("빈틈 0.5초 미만").
"""
from __future__ import annotations

import itertools
import math

from . import style
from .textutil import EPS, end_bonus, fits, units, wrap

INF = float("inf")
ESTIMATED = ("voice", "even")               # dialogue.json ``timing`` of a line whose time is a guess
NO_TIME = ("absent", "out_of_range")        # dialogue.json ``status`` of a line that has no time in the video

# the numbers of cboe.style (adult profile) and whether textutil.wrap uses the Kiwi phrase hints;
# ``place(settings=...)`` overrides them, e.g. with style.get("children").settings()
DEFAULTS = dict(style.get().settings(), kiwi=True)


class _P:
    """Limits of one run, in frames."""

    def __init__(self, fps, settings=None):
        s = dict(DEFAULTS)
        s.update({k: v for k, v in (settings or {}).items() if v is not None})
        fps = _num(fps) or 0.0
        self.fps = f = fps if fps > 0 else 25.0
        self.profile = s["profile"]
        self.max_units = float(s["max_units"])
        self.max_lines = int(s["max_lines"])
        self.cps = float(s["cps"])
        self.min_dur, self.max_dur = float(s["min_dur"]), float(s["max_dur"])
        self.min_text = style.seconds_text(self.min_dur)            # "5/6" in the messages
        self.min_f = int(math.ceil(self.min_dur * f - 1e-6))        # 20 frames at 23.976 fps
        self.max_f = int(math.floor(self.max_dur * f + 1e-6))       # 167 frames at 23.976 fps
        self.gap = int(s["gap_frames"])
        self.half = int(math.floor(float(s["half"]) * f + 0.5))    # "within half a second": 12 frames
        self.half_exact = float(s["half"]) * f                      # "a gap under half a second": 3-11 frames
        self.near = int(math.ceil(float(s["near_cut"]) * f - 1e-6))  # TG4 "9, 10 or 11 frames before": from 9 at 24 fps
        self.tail = int(math.floor(float(s["tail"]) * f + 0.5))
        self.win = int(math.floor(float(s["merge_window"]) * f + 0.5))
        self.label_extra = float(s["label_extra"])
        self.music_f = int(round(float(s["music_dur"]) * f))
        self.flash_f = int(math.ceil(float(s["flash_dur"]) * f - 1e-6))   # 6 frames at 23.976 fps
        self.kiwi = bool(s["kiwi"])

    def fr(self, t: float) -> int:
        return int(round(float(t) * self.fps))

    def sec(self, k: int) -> float:
        return round(k / self.fps, 3)

    def need(self, u: float) -> int:
        """Frames needed to read ``u`` units at the reading speed limit."""
        return int(math.ceil(u / self.cps * self.fps - 1e-6)) if u > 0 else 0

    def cps_of(self, u: float, dur_f: int) -> float:
        return u * self.fps / dur_f if dur_f > 0 else INF

    def too_fast(self, u: float, dur_f: int) -> bool:
        return self.cps_of(u, dur_f) > self.cps + 1e-6

    def lines_ok(self, lines) -> bool:
        return fits(lines, self.max_units, self.max_lines)

    def wrap(self, text) -> list[str]:
        """The displayed lines of ``text``: broken on the text as written, punctuation tidied."""
        return wrap(text, self.max_units, self.max_lines, kiwi=self.kiwi, tidy=style.tidy_line)

    def fits(self, text: str) -> bool:
        """Whether ``text`` as displayed stays within the line and line-count limits."""
        return fits(text, self.max_units, self.max_lines, tidy=style.tidy_line)


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


# --------------------------------------------------------------------------- cells

def _hyphen_line(p: dict) -> str:
    """One line of a two-speaker (or speaker and sound) cell as displayed: "- [빌] 뭐?"."""
    return style.tidy_line(style.HYPHEN + p["text"])


def _render(parts: list[dict], mode: str, P: _P) -> list[str]:
    """Display lines: ``plain`` one part wrapped; ``hyphen`` one "- " line per part (II.5, I.6);
    ``inline`` a sound made by the speaker in front of the dialogue, "[지수] [재채기] 나도야!" (II.5).
    The punctuation is tidied for display after the lines are broken (style.tidy_line)."""
    if mode == "hyphen":
        return [_hyphen_line(p) for p in parts]
    if mode == "inline":
        d = next(p for p in parts if p["kind"] == "dialogue")
        s = next(p for p in parts if p["kind"] != "dialogue")
        return P.wrap(d["prefix"] + s["text"] + " " + d["body"])
    return P.wrap(parts[0]["text"])


class _Cell:
    __slots__ = ("start", "end", "parts", "mode", "lines", "seq", "marks")

    def __init__(self, start: int, end: int, parts: list[dict], mode: str, P: _P, seq: int):
        self.start, self.end, self.seq = start, end, seq
        self.marks: set[str] = set()                 # flags given while placing: "overlap"
        self.set(parts, mode, P)

    def set(self, parts: list[dict], mode: str, P: _P) -> None:
        self.parts, self.mode = parts, mode
        self.lines = _render(parts, mode, P)

    @property
    def units(self) -> float:
        return sum(units(x) for x in self.lines)

    def dialogue(self) -> list[dict]:
        return [p for p in self.parts if p["kind"] == "dialogue"]

    def sounds(self) -> list[dict]:
        return [p for p in self.parts if p["kind"] != "dialogue"]

    def speech_end(self):
        ends = [p["e"] for p in self.parts if p["kind"] == "dialogue"]
        return max(ends) if ends else None

    def audio_end(self) -> int:
        return max(p["e"] if p.get("e") is not None else p["t"] for p in self.parts)

    def kind(self) -> str:
        kinds = {p["kind"] for p in self.parts}
        return kinds.pop() if len(kinds) == 1 else "mixed"


def _floor(c: _Cell, P: _P) -> int:
    """The earliest out-time ``c`` may be shortened to: its speech, 5/6 s and the reading speed."""
    se = c.speech_end()
    lo = max(se if se is not None else c.start, c.start + P.min_f, c.start + P.need(c.units))
    return min(c.end, lo)


def _sorted(cells: list[_Cell]) -> None:
    cells.sort(key=lambda c: (c.start, c.seq))


# --------------------------------------------------------------------------- dialogue

def _word_times(ln: dict, toks: list[str]):
    """(start, end) of each eojeol: the line's word times when they match the text and make sense,
    else even spacing over start..end (DESIGN.md §3: words = the text's eojeols, same order)."""
    n = len(toks)
    start, end = _num(ln.get("start")), _num(ln.get("end"))
    words = ln.get("words") if isinstance(ln.get("words"), list) else []
    if len(words) == n:
        ws = [(_num(w.get("s")), _num(w.get("e"))) if isinstance(w, dict) else (None, None) for w in words]
        if all(s is not None and e is not None for s, e in ws):
            ordered = all(ws[k][0] >= ws[k - 1][0] - 1e-6 for k in range(1, n))
            span = max(e for _, e in ws) - ws[0][0]
            line_span = end - start if start is not None and end is not None else 0.0
            if ordered and (span > 1e-6 or line_span <= 1e-6):
                return [(s, max(s, e)) for s, e in ws]
    if start is None or end is None:
        vals = [v for w in words if isinstance(w, dict) for v in (_num(w.get("s")), _num(w.get("e")))
                if v is not None]
        start = start if start is not None else (min(vals) if vals else end)
        end = end if end is not None else (max(vals) if vals else start)
    if start is None:
        return None
    end = max(end, start)
    step = (end - start) / n
    return [(start + k * step, start + (k + 1) * step) for k in range(n)]


def _split(toks: list[str], times: list, text_of, P: _P) -> list[tuple[int, int]]:
    """Word ranges of the cells of one line: the whole line when it fits 2 x 16 units and 7 s, else
    the cheapest pieces that each fit. ``text_of(i, j)`` is the text of the piece of words i..j as
    it is written into a cell, with its label and notes, so those count in the 16 units. A piece
    costs a constant (fewer cells) plus its squared length (balanced cells); a piece that ends
    after sentence punctuation costs less (a comma a little less), so the line is cut at its
    sentence ends when that takes at most one more cell or so, and a piece too short to reach
    5/6 s costs more."""
    n = len(toks)

    def span(i, j):
        return P.fr(times[j - 1][1]) - P.fr(times[i][0])

    if P.fits(text_of(0, n)) and span(0, n) <= P.max_f:
        return [(0, n)]
    best: list = [None] * (n + 1)                    # (cost, start of the last piece)
    best[0] = (0.0, -1)
    for j in range(1, n + 1):
        for i in range(j - 1, -1, -1):
            text = text_of(i, j)
            if j - i > 1 and (span(i, j) > P.max_f or not P.fits(text)):
                break                                # longer pieces do not fit either
            cost = 20.0 + (units(text) / 4.0) ** 2
            if j < n:
                cost -= 2.0 * end_bonus(toks[j - 1])
            if span(i, j) + P.tail < P.min_f:
                cost += 3.0
            cost += best[i][0]
            if best[j] is None or cost < best[j][0] - 1e-9:
                best[j] = (cost, i)
    out, j = [], n
    while j > 0:
        i = best[j][1]
        out.append((i, j))
        j = i
    return out[::-1]


def _body(words: list, sung: bool) -> str:
    """The words of one piece as displayed: ♪ … ♪ around a sung piece (II.7)."""
    text = " ".join(w if isinstance(w, str) else w[0] for w in words)
    return style.lyric(text) if sung else text


def _piece(d: dict, words: list, prefix: str) -> dict:
    """The dialogue part ``d`` cut down to ``words`` (word, start frame, end frame), with the label
    ``prefix`` in front: its own label for the first piece of the line, ``d["cont"]`` (the
    language label, or nothing) for a later one."""
    body = _body(words, d["sung"])
    return dict(d, prefix=prefix, body=body, text=prefix + body, words=words, t=words[0][1],
                e=max(w[2] for w in words), whole=False)


def _dialogue_parts(ln: dict, P: _P, order: int = 0) -> list[dict]:
    """The pieces of one script line as cell parts (empty when it has no text or no time).
    ``order`` is the place of the line in the script."""
    text = " ".join(str(ln.get("text") or "").split())
    if not text or ln.get("status") in NO_TIME:
        return []
    toks = text.split(" ")
    times = _word_times(ln, toks)
    if times is None:
        return []
    head, cont = style.line_labels(ln)               # "[이름/출처·말투/언어] " and "[언어] "
    sung = bool(ln.get("sung"))
    ranges = _split(toks, times, lambda i, j: (head if i == 0 else cont) + _body(toks[i:j], sung), P)
    guess = ln.get("timing") in ESTIMATED or ln.get("status") == "unheard"
    parts = []
    for i, j in ranges:
        pre = head if i == 0 else cont
        body = _body(toks[i:j], sung)
        if not style.tidy_line(" ".join(toks[i:j])):     # a piece that is only punctuation shows nothing
            continue
        words = [(toks[x], P.fr(times[x][0]), P.fr(times[x][1])) for x in range(i, j)]
        parts.append({"kind": "dialogue", "id": ln.get("id"), "prefix": pre, "body": body, "text": pre + body,
                      "t": words[0][1], "e": max(w[2] for w in words), "words": words,
                      "speaker": ln.get("name") or ln.get("speaker"), "whole": len(ranges) == 1,
                      "cont": cont, "sung": sung, "guess": guess, "order": order, "pos": ln.get("pos")})
    return parts


def _snap_in(s: int, e: int, prev, cuts: list[int], P: _P, w0: int) -> int:
    """Dialogue in-time at shot changes (TG2, TG4), never taking the speech or 5/6 s of ``prev``."""
    lo = w0
    if prev is not None:
        se = prev.speech_end()
        lo = max(lo, max(se if se is not None else prev.start, prev.start + P.min_f) + P.gap)
    back = [c for c in cuts if s - P.half <= c <= s]
    if back and (back[-1] == s or back[-1] >= lo):
        return back[-1]
    # no cut before the line, or one that cannot be used: the rule for a cut right after it (TG4)
    ahead = [c for c in cuts if s < c < s + P.half]
    if ahead and e > ahead[0]:
        c = ahead[0]
        if c - s >= P.near and c - P.half >= lo:         # 9-11 frames before at 24 fps
            return c - P.half
        return c
    return s


def _pair_short(cells: list[_Cell], P: _P) -> list[_Cell]:
    """Two short lines of different speakers in one hyphen cell (module doc)."""
    out, i = [], 0
    while i < len(cells):
        a = cells[i]
        b = cells[i + 1] if i + 1 < len(cells) else None
        c = cells[i + 2] if i + 2 < len(cells) else None
        if b is not None and _can_pair(a, b, c, P):
            a.set([a.parts[0], b.parts[0]], "hyphen", P)
            out.append(a)
            i += 2
        else:
            out.append(a)
            i += 1
    return out


def _two_speakers(a: _Cell, b: _Cell, P: _P) -> bool:
    """Whether ``a`` and ``b`` can be the two lines of one hyphen cell (I.6): two whole script
    lines ("a contained sentence" each) of different, known speakers, one line each, within 7 s."""
    if a.mode != "plain" or b.mode != "plain":
        return False
    pa, pb = a.parts[0], b.parts[0]
    if not (pa["whole"] and pb["whole"]):
        return False
    if not pa.get("speaker") or not pb.get("speaker") or pa["speaker"] == pb["speaker"]:
        return False
    if any(units(_hyphen_line(p)) > P.max_units + EPS for p in (pa, pb)):
        return False
    return max(pa["e"], pb["e"]) - a.start <= P.max_f


def _can_pair(a: _Cell, b: _Cell, c, P: _P) -> bool:
    if not _two_speakers(a, b, P):
        return False
    pa, pb = a.parts[0], b.parts[0]
    if b.start - P.gap - a.start >= P.min_f:             # the first line can get 5/6 s alone
        return False
    nat_b = min(pb["e"] + P.tail, (c.start - P.gap) if c is not None else INF) - b.start
    return not (pa["e"] + P.tail - a.start >= P.min_f or nat_b >= P.min_f)


# lines that overlap in time (module doc "Lines that overlap")

def _need(c: _Cell, P: _P) -> int:
    """Frames ``c`` has to stay to be read: 5/6 s and the reading speed."""
    return max(P.min_f, P.need(c.units))


def _keep_end(c: _Cell, P: _P) -> int:
    """The earliest out-time that keeps both the speech and the reading time of ``c``."""
    return max(c.speech_end(), c.start + _need(c, P))


def _guessed(c: _Cell) -> bool:
    """Whether the time of every line in ``c`` is only a guess (timing voice / even)."""
    return all(p.get("guess") for p in c.dialogue())


def _clash(a: _Cell, b: _Cell, P: _P) -> bool:
    """``b`` starts while ``a`` is still being spoken, too early for ``a`` to be read before it."""
    return b.dialogue()[0]["t"] < a.speech_end() and b.start - P.gap - a.start < _need(a, P)


def _shift(c: _Cell, d: int) -> None:
    """Move a cell whose time is a guess by ``d`` frames, its words with it."""
    for p in c.parts:
        p["t"], p["e"] = p["t"] + d, p["e"] + d
        p["words"] = [(w, s + d, e + d) for w, s, e in p["words"]]
    c.start += d


def _give_way(cells: list[_Cell], i: int, P: _P, w0: int, w1) -> bool:
    """② of the two clashing cells ``i`` and ``i + 1`` the one whose time is a guess moves out of
    the way of the other. Tried in turn: the side it has in the script (before or after the
    other), the other side, and right behind the reading time of the other (which then ends
    before its speech does, as cells of overlapping speech do). It needs room there for its own
    reading time. Returns whether it moved."""
    a, b = cells[i], cells[i + 1]
    e, r = (a, b) if _guessed(a) else (b, a)
    prev = cells[i - 1] if i > 0 else None
    nxt = cells[i + 2] if i + 2 < len(cells) else None
    need = _need(e, P)
    lo = max(w0, _keep_end(prev, P) + P.gap) if prev is not None else w0
    hi = min(nxt.start - P.gap if nxt is not None else INF, w1 if w1 is not None else INF)
    before = ("before", min(e.start, r.start - P.gap - need))
    after = ("after", max(e.start, _keep_end(r, P) + P.gap))
    behind = ("after", max(e.start, r.start + _need(r, P) + P.gap))
    first = e.parts[0]["order"] < r.parts[0]["order"]
    for side, s in ([before, after] if first else [after, before]) + [behind]:
        room = (s >= lo) if side == "before" else (s + need <= hi)
        if room:
            _shift(e, s - e.start)
            _sorted(cells)
            return True
    return False


def _untangle(cells: list[_Cell], P: _P, w0: int, w1) -> list[_Cell]:
    """Lines that start while the line before is still spoken (module doc): ① one hyphen cell for
    two speakers, ② the line with a guessed time gives way, ③ the later line waits until the
    earlier one has had 5/6 s and both are marked "overlap". Never a one-frame cell in silence."""
    i, steps = 0, 6 * len(cells) + 8
    while i + 1 < len(cells) and steps > 0:
        steps -= 1
        a, b = cells[i], cells[i + 1]
        if not _clash(a, b, P):
            i += 1
            continue
        if _two_speakers(a, b, P):                       # ①
            a.set([a.parts[0], b.parts[0]], "hyphen", P)
            a.marks |= b.marks
            del cells[i + 1]
            continue
        if _guessed(a) != _guessed(b) and _give_way(cells, i, P, w0, w1):   # ②
            i = max(i - 1, 0)
            continue
        nxt = cells[i + 2] if i + 2 < len(cells) else None                 # ③
        wait = a.start + P.min_f + P.gap
        end = w1 if w1 is not None else INF
        room = wait + P.min_f <= min(nxt.start - P.gap if nxt is not None else INF, end)
        # no room, but the line after is spoken over this one too and will wait in its turn:
        # a pile of lines is shown one after the other while this one is still being spoken
        queue = (nxt is not None and nxt.dialogue()[0]["t"] < b.speech_end() and wait < b.speech_end()
                 and wait + P.min_f <= end)
        if b.start < wait and (room or queue):
            b.start = wait
            for j in range(i + 2, len(cells)):           # the cells behind keep their order
                if cells[j].start >= cells[j - 1].start + P.gap + 1:
                    break
                cells[j].start = cells[j - 1].start + P.gap + 1
        a.marks.add("overlap")
        b.marks.add("overlap")
        i += 1
    return cells


def _dialogue_outs(cells: list[_Cell], P: _P, w1) -> None:
    """Out-times: last word + 0.5 s, raised to 5/6 s and 14 units/s, at most 7 s and 2 frames
    before the next cell (TG1, TG3, TG5)."""
    for i, c in enumerate(cells):
        out = max(c.speech_end() + P.tail, c.start + P.min_f, c.start + P.need(c.units))
        out = min(out, c.start + P.max_f)
        if i + 1 < len(cells):
            out = min(out, cells[i + 1].start - P.gap)
        if w1 is not None:
            out = min(out, w1)
        c.end = max(out, c.start + 1)


def _dialogue_cells(lines, cuts: list[int], P: _P, w0: int, w1, seq) -> list[_Cell]:
    parts = []
    for k, ln in enumerate(lines or []):
        if isinstance(ln, dict):
            parts.extend(_dialogue_parts(ln, P, k))
    parts = [p for p in parts if p["e"] > w0 or p["t"] >= w0]
    if w1 is not None:
        parts = [p for p in parts if p["t"] < w1]
    parts.sort(key=lambda p: p["t"])                     # stable: script order for equal times
    cells: list[_Cell] = []
    prev = None
    for p in parts:
        s = _snap_in(max(p["t"], w0), p["e"], prev, cuts, P, w0)
        if prev is not None and s < prev.start + P.gap + 1:
            s = prev.start + P.gap + 1                   # lines that start together: keep the order
        c = _Cell(s, s + 1, [p], "plain", P, next(seq))
        cells.append(c)
        prev = c
    cells = _untangle(cells, P, w0, w1)                  # overlapping lines: never a one-frame cell
    cells = _pair_short(cells, P)
    _dialogue_outs(cells, P, w1)
    return cells


# --------------------------------------------------------------------------- sounds

def _sound_part(s: dict, P: _P):
    """A sound item as a cell part; None when it has no time."""
    kind = s.get("kind") if s.get("kind") in ("sfx", "music") else "sfx"
    # '♬ 무서운 음악' of an older work folder becomes '[무서운 음악]'; as displayed (no '[쿵].')
    phrase = style.tidy_line(style.modernize_phrase(s.get("phrase")))
    short = style.tidy_line(style.modernize_phrase(s.get("short")))
    long_, short = phrase or short, short or phrase
    onset, move, t1 = _num(s.get("onset")), _num(s.get("move_to")), _num(s.get("t1"))
    at = move if move is not None else onset
    if at is None:
        return None
    t = P.fr(at)
    e = None
    if t1 is not None:
        e = max(t, P.fr(t1 + (at - onset if onset is not None else 0.0)))
    return {"kind": kind, "id": s.get("id"), "text": long_, "long": long_, "short": short, "t": t, "e": e,
            "src": s.get("source_line"), "forced": move is not None, "at": at, "pos": s.get("pos")}


def _sound_len(sp: dict, text: str, start: int, P: _P) -> int:
    """Frames a sound line is shown (memo 5.2): sfx units / 14 + 1 s; music 3 s or music end + 0.5 s."""
    if sp["kind"] == "music":
        end = start + P.music_f
        if sp["e"] is not None:
            end = min(end, sp["e"] + P.tail)
        return min(max(end - start, P.min_f), P.max_f)
    d = int(round((units(text) / P.cps + P.label_extra) * P.fps))
    return min(max(d, P.min_f), P.max_f)


def _limit_after(x, start: int, cells: list[_Cell], P: _P, w1) -> int:
    """The latest out-time for a cell starting at ``start``: 2 frames before the next cell (other
    than ``x``), the window end."""
    nxt = [c.start for c in cells if c is not x and c.start >= start and (x is None or c.start > x.start)]
    lim = min(nxt) - P.gap if nxt else INF
    return min(lim, w1) if w1 is not None else lim


def _plan_own(_x, t: int, sp: dict, text: str, ext: bool, cells: list[_Cell], P: _P, w1):
    """① an own cell at ``t``. Returns (apply, reason)."""
    lines = P.wrap(text)
    if not P.lines_ok(lines):
        return None, f"문구가 {P.max_lines}줄 {P.max_units:g}자 초과"
    prev = [c for c in cells if c.start < t and c.end > t - P.gap]
    if any(_floor(c, P) > t - P.gap for c in prev):
        return None, None
    limit = _limit_after(None, t, cells, P, w1)
    if limit - t < P.min_f:
        return None, None
    u = sum(units(x) for x in lines)
    end = min(t + _sound_len(sp, text, t, P), limit, t + P.max_f)
    if ext:
        end = min(max(end, t + P.need(u), t + P.min_f), limit, t + P.max_f)
    if P.too_fast(u, end - t):
        return None, f"초당 {P.cps:g}자 초과"
    part = dict(sp, text=text)

    def apply(seq):
        for c in prev:
            c.end = t - P.gap
        cells.append(_Cell(t, end, [part], "plain", P, next(seq)))
    return apply, None


def _merge_parts(other: dict, sp: dict, text: str, dialogue: bool):
    """Parts and mode of a cell holding ``other`` and the sound: ③ inline when the speaker made the
    sound, else ② hyphen lines in heard order (the sound first when both start on the same frame)."""
    snd = dict(sp, text=text)
    if dialogue and sp["src"] is not None and sp["src"] == other["id"]:
        return [other, snd], "inline"
    return ([snd, other] if snd["t"] <= other["t"] else [other, snd]), "hyphen"


def _check_merged(lines, mode, P: _P):
    if mode == "hyphen" and any(units(x) > P.max_units + EPS for x in lines):
        return f"하이픈 줄이 {P.max_units:g}자 초과"
    if not P.lines_ok(lines):
        return f"{P.max_lines}줄 {P.max_units:g}자 초과"
    return None


def _plan_merge(x: _Cell, t: int, sp: dict, text: str, ext: bool, cells: list[_Cell], P: _P, w1):
    """② / ③ the sound joins the one-line cell ``x`` that starts within 0.5 s of it."""
    dl = x.dialogue()
    if dl and x.sounds():
        return None, "대사 칸에 이미 소리"
    if len(x.lines) >= 2 or len(x.parts) != 1:
        return None, "대사 칸이 이미 두 줄" if dl else "칸이 이미 두 줄"
    parts, mode = _merge_parts(x.parts[0], sp, text, bool(dl))
    lines = _render(parts, mode, P)
    why = _check_merged(lines, mode, P)
    if why:
        return None, why
    start = min(x.start, t)
    prev = [c for c in cells if c is not x and c.start < x.start and c.end > start - P.gap]
    if start < x.start and prev:
        start = min(x.start, max([start] + [_floor(c, P) + P.gap for c in prev]))
    limit = _limit_after(x, start, cells, P, w1)
    u = sum(units(y) for y in lines)
    end = min(max(x.end, t + _sound_len(sp, text, t, P)), limit, start + P.max_f)
    if ext:
        end = min(max(end, start + P.need(u), start + P.min_f), limit, start + P.max_f)
    if end - start < P.min_f:
        return None, f"{P.min_text}초 미만"
    if P.too_fast(u, end - start):
        return None, f"초당 {P.cps:g}자 초과"

    def apply(seq):
        for c in prev:
            c.end = min(c.end, start - P.gap)
        x.start, x.end = start, end
        x.set(parts, mode, P)
    return apply, None


def _plan_split(x: _Cell, t: int, sp: dict, text: str, ext: bool, cells: list[_Cell], P: _P, w1):
    """④ split the dialogue cell ``x`` at the word boundary nearest the sound; the sound joins the
    later part, which starts at the sound."""
    if x.mode != "plain" or x.sounds() or not x.dialogue():
        return None, "대사 칸 중간 (나눌 수 없는 칸)"
    d = x.parts[0]
    words = d["words"]
    if len(words) < 2:
        return None, "대사 중간, 나눌 어절 경계 없음"
    k = min(range(1, len(words)), key=lambda q: (abs(words[q][1] - t), q))
    if abs(words[k][1] - t) > P.win:
        return None, "소리 가까이 어절 경계 없음"
    if not all(style.tidy_line(" ".join(w[0] for w in side)) for side in (words[:k], words[k:])):
        return None, "소리 가까이 어절 경계 없음"          # one side would be punctuation only
    a = _piece(d, words[:k], d["prefix"])
    b = dict(_piece(d, words[k:], d["cont"]), e=d["e"])  # the later part: the language label again, no name
    a_end = t - P.gap
    a_lines = _render([a], "plain", P)
    if not P.lines_ok(a_lines):
        return None, "나눈 앞 칸 한도 초과"
    if a_end - x.start < P.min_f:
        return None, f"나누면 앞 칸 {P.min_text}초 미만"
    if P.too_fast(sum(units(y) for y in a_lines), a_end - x.start):
        return None, f"나누면 앞 칸 초당 {P.cps:g}자 초과"
    parts, mode = _merge_parts(b, sp, text, True)
    lines = _render(parts, mode, P)
    why = _check_merged(lines, mode, P)
    if why:
        return None, why
    limit = _limit_after(x, t, cells, P, w1)
    u = sum(units(y) for y in lines)
    end = min(max(x.end, t + _sound_len(sp, text, t, P)), limit, t + P.max_f)
    if ext:
        end = min(max(end, t + P.need(u), t + P.min_f), limit, t + P.max_f)
    if end - t < P.min_f:
        return None, f"나누면 뒤 칸 {P.min_text}초 미만"
    if P.too_fast(u, end - t):
        return None, f"초당 {P.cps:g}자 초과"

    def apply(seq):
        x.end = a_end
        x.set([a], "plain", P)
        cells.append(_Cell(t, end, parts, mode, P, next(seq)))
    return apply, None


def _music_start(t: int, cells: list[_Cell], cuts: list[int], P: _P) -> int:
    """Music starts on a shot change in the half second before its onset (memo 5.2, TG2) when the
    cells before can end 2 frames before that cut."""
    back = [c for c in cuts if t - P.half <= c < t]
    if not back:
        return t
    c = back[-1]
    if any(x.start < c and x.end > c - P.gap and _floor(x, P) > c - P.gap for x in cells):
        return t
    return c


def _no_room_reason(why, t: int, cells: list[_Cell], P: _P, w1) -> str:
    """'소리 뒤 빈틈 0.42초': the free time after the sound (after what cannot be shortened)."""
    blk = [c for c in cells if c.start <= t < c.end + P.gap]
    t2 = max([t] + [_floor(c, P) + P.gap for c in blk])
    nxt = [c.start for c in cells if c.start > t]
    limit = min(nxt) - P.gap if nxt else INF
    if w1 is not None:
        limit = min(limit, w1)
    free = max(0, limit - t2)
    if t2 - t > P.win and free >= P.min_f:
        gap_txt = f"빈틈이 소리 {(t2 - t) / P.fps:.2f}초 뒤"       # a gap, but too late for the sound
    elif limit < INF:
        gap_txt = f"소리 뒤 빈틈 {free / P.fps:.2f}초"
    else:
        gap_txt = ""
    return " · ".join(x for x in (why, gap_txt) if x) or "자리 없음"


def _place_sound(sp: dict, cells: list[_Cell], cuts: list[int], P: _P, w0: int, w1, seq):
    """Put one sound in a cell (positions ①-⑤). Returns None, or the no_room reason."""
    t = sp["t"]
    if t < w0 or (w1 is not None and t >= w1):
        return "처리 구간 밖"
    if not sp["long"]:
        return "문구 없음"
    _sorted(cells)
    if sp["kind"] == "music" and not sp["forced"]:
        t = _music_start(t, cells, cuts, P)
    texts = [sp["long"]] + ([sp["short"]] if sp["short"] and sp["short"] != sp["long"] else [])
    levels = [(x, False) for x in texts] + [(x, True) for x in texts]     # ⑤-1 short, ⑤-2 longer end
    starts = sorted((c for c in cells if abs(c.start - t) <= P.win),
                    key=lambda c: (abs(c.start - t), c.start, c.seq))
    if starts:
        targets = [(_plan_merge, c) for c in starts]
    else:
        mid = [c for c in cells if c.start + P.win < t < c.end + P.gap
               and c.speech_end() is not None and c.speech_end() > t]
        targets = [(_plan_split, c) for c in mid] if mid else [(_plan_own, None)]
    why = None
    for fn, x in targets:
        for text, ext in levels:
            apply, reason = fn(x, t, sp, text, ext, cells, P, w1)
            if apply is not None:
                apply(seq)
                return None
            why = why or reason
    near = (_floor(c, P) + P.gap for c in cells if c.start <= t + P.win and c.end + P.gap > t)
    floors = sorted({f for f in near if t < f <= t + P.win})
    for t2 in floors:                                                   # ⑤-3 a gap just after the sound
        for text in texts:
            apply, _ = _plan_own(None, t2, sp, text, True, cells, P, w1)
            if apply is not None:
                apply(seq)
                return None
    return _no_room_reason(why, t, cells, P, w1)


# --------------------------------------------------------------------------- end adjustment

def _linger(c: _Cell, x: int, cuts: list[int], cap: int, P: _P) -> bool:
    """Keep ``c`` at least half a second into the shot after cut ``x`` (TG1, TG4) where there is room."""
    later = [y for y in cuts if y > x]
    target = min(x + P.half, cap, (later[0] - P.gap) if later else INF)
    if target >= x + P.half and target > c.end:
        c.end = target
    return c.end >= x + P.half


def _cut_rules(c: _Cell, cuts: list[int], cap: int, P: _P) -> None:
    """Out-time at the shot changes the cell crosses or ends less than 2 frames before (module doc)."""
    dlg = bool(c.dialogue())
    ae = c.audio_end()
    u = c.units
    for x in cuts:
        if x <= c.start:
            continue
        if x >= c.end + P.gap:
            break
        back = x - P.gap
        back_ok = back - c.start >= P.min_f and not P.too_fast(u, back - c.start)
        if ae <= x:                                  # audio over before the cut: do not cross it
            if back_ok:
                c.end = back
                return
            if dlg:
                _linger(c, x, cuts, cap, P)
        elif dlg:                                    # speech goes on past the cut (TG4)
            if c.end < x + P.half and not _linger(c, x, cuts, cap, P) and c.end < x and back_ok:
                c.end = back
                return
        elif c.end < x and back_ok:                  # a sound that goes on may cross, but never
            c.end = back                             # end less than 2 frames before the cut
            return


def _adjust_ends(cells: list[_Cell], cuts: list[int], P: _P, w1) -> None:
    _sorted(cells)
    for i, c in enumerate(cells):
        nxt = cells[i + 1].start if i + 1 < len(cells) else None
        limit = nxt - P.gap if nxt is not None else INF
        if w1 is not None:
            limit = min(limit, w1)
        cap = min(limit, c.start + P.max_f)
        if nxt is not None and P.gap < nxt - c.end < P.half_exact - 1e-9 and nxt - P.gap <= cap:
            c.end = nxt - P.gap                      # ① close a gap under half a second (TG5)
        ahead = [x for x in cuts if c.end < x <= c.end + P.half]
        if ahead:                                    # ② out-time just before a cut (TG2)
            x = ahead[0]
            if c.end < x - P.gap <= cap and (nxt is None or nxt >= x):
                c.end = x - P.gap
        _cut_rules(c, cuts, cap, P)                  # ③ crossing a cut (TG1, TG4)
        c.end = max(min(c.end, limit), c.start + 1)
        if nxt is not None and P.gap < nxt - c.end < P.half_exact - 1e-9 \
                and not any(c.end <= x <= nxt for x in cuts):
            if nxt - P.gap <= cap:                   # ③ moved the end: close the gap again,
                c.end = nxt - P.gap
            else:                                    # or, at 7 s, make it half a second (TG5)
                half_gap = nxt - int(math.ceil(P.half_exact - 1e-9))
                if half_gap >= _floor(c, P):
                    c.end = max(half_gap, c.start + 1)


# --------------------------------------------------------------------------- output

def _out(cells: list[_Cell], cuts: list[int], P: _P) -> list[dict]:
    out = []
    for i, c in enumerate(cells):
        nxt = cells[i + 1] if i + 1 < len(cells) else None
        chars = [units(x) for x in c.lines]
        dur_f = c.end - c.start
        cps = P.cps_of(sum(chars), dur_f)
        viol = []
        if any(u > P.max_units + EPS for u in chars):
            viol.append(f"한 줄 {P.max_units:g}자 초과")
        if len(c.lines) > P.max_lines:
            viol.append(f"{P.max_lines}줄 초과")
        if cps > P.cps + 1e-6:
            viol.append(f"초당 {P.cps:g}자 초과")
        if dur_f < P.min_f:
            viol.append(f"{P.min_text}초 미만")
        if dur_f > P.max_f:
            viol.append(f"{P.max_dur:g}초 초과")
        gap_f = None
        if nxt is not None:
            gap_f = nxt.start - c.end
            if gap_f < P.gap:
                viol.append(f"간격 {P.gap}프레임 미만")
            elif P.gap < gap_f < P.half_exact - 1e-9 and not any(c.end <= x <= nxt.start for x in cuts):
                viol.append("빈틈 0.5초 미만")
        refs = []
        for p in c.parts:
            if p["id"] not in refs:
                refs.append(p["id"])
        flags = ["timing_estimated"] if any(p.get("guess") for p in c.dialogue()) else []
        flags += sorted(c.marks)
        if dur_f < P.flash_f:
            flags.append("flash")                    # a cell of a few frames never goes out unmarked
        out.append({"id": f"C{i + 1:04d}", "start": P.sec(c.start), "end": P.sec(c.end), "lines": list(c.lines),
                    "refs": refs, "kind": c.kind(),
                    "pos": "top" if any(p.get("pos") == "top" for p in c.parts) else "bottom", "flags": flags,
                    "check": {"chars": chars, "n_lines": len(c.lines), "cps": round(cps, 2) if cps < INF else None,
                              "dur": round(dur_f / P.fps, 3),
                              "gap_next": round(gap_f / P.fps, 3) if gap_f is not None else None,
                              "violations": viol}})
    return out


def place(lines, sounds, cuts, fps, window=(0.0, None), settings=None) -> dict:
    """Draft cells for dialogue ``lines`` and included ``sounds`` (module doc)."""
    P = _P(fps, settings)
    t0, t1 = (tuple(window) + (None, None))[:2] if window is not None else (None, None)
    w0 = P.fr(_num(t0) or 0.0)
    w1 = P.fr(_num(t1)) if _num(t1) is not None else None
    cut_f = sorted({P.fr(c) for c in (cuts or []) if _num(c) is not None})
    seq = itertools.count()
    cells = _dialogue_cells(lines, cut_f, P, w0, w1, seq)
    no_room = []
    parts = []
    for s in sounds or []:
        if not isinstance(s, dict):
            continue
        sp = _sound_part(s, P)
        if sp is None:
            no_room.append({"item": s.get("id"), "t": None, "text": s.get("phrase") or s.get("short"),
                            "reason": "시각 없음"})
            continue
        parts.append(sp)
    parts.sort(key=lambda p: (p["t"], str(p["id"])))
    for sp in parts:
        reason = _place_sound(sp, cells, cut_f, P, w0, w1, seq)
        if reason is not None:
            no_room.append({"item": sp["id"], "t": round(sp["at"], 3), "text": sp["long"], "kind": sp["kind"],
                            "reason": reason})
    _adjust_ends(cells, cut_f, P, w1)
    for c in list(cells):                            # window start (in-times never go before it)
        if c.start < w0:
            c.start = w0
        if c.end <= c.start:
            cells.remove(c)
            for p in c.sounds():
                no_room.append({"item": p["id"], "t": round(p["at"], 3), "text": p["long"], "kind": p["kind"],
                                "reason": "처리 구간 끝"})
    _sorted(cells)
    for a, b in zip(cells, cells[1:]):              # last guard: never overlap the next cell
        if b.start - a.end < P.gap:
            a.end = max(a.start + 1, b.start - P.gap)
    out = _out(cells, cut_f, P)
    n_viol = sum(1 for c in out if c["check"]["violations"])
    placed = sum(1 for c in cells for p in c.sounds())

    def flagged(name):
        return sum(1 for c in out if name in c["flags"])

    return {"cells": out, "no_room": no_room,
            "summary": {"cells": len(out), "violations": n_viol, "no_room": len(no_room),
                        "dialogue_cells": sum(1 for c in cells if c.dialogue()), "sounds_placed": placed,
                        "timing_estimated": flagged("timing_estimated"), "overlap": flagged("overlap"),
                        "flash": flagged("flash")},
            "fps": P.fps, "profile": P.profile, "notation": style.NOTATION}
