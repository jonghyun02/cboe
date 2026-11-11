# -*- coding: utf-8 -*-
"""끼워 넣기 (5.9): place dialogue lines and included sound items into subtitle cells.

Contract (called by ``state.Project.recompute``, output = DESIGN.md §3 Draft)::

    place(lines, sounds, cuts, fps, window=(t0, t1), settings=None) -> draft dict

lines  : DialogueLine dicts (id, speaker, text, start, end, words[{w,s,e}]) plus
         ``show_name`` (bool) and ``name`` (str | None), and optionally ``label_extra`` (source or
         manner of speech shown after the name: ["속마음"]), ``language`` ("영어"), ``sung`` (bool),
         ``timing`` ("asr" | "script" | "voice" | "even"), ``status`` ("absent" and "out_of_range"
         lines have no time and are skipped; "unheard" = time is a guess) and ``pos`` ("top"),
         ``scene_id`` (the script scene), ``person`` (who speaks: two lines of one person never
         share a hyphen cell, whatever label they show; default ``name`` or ``speaker``; a line of
         a subtitle script with no name has "#" + its turn, "#1-1" and "#1-2" for the two "- "
         lines of one cell: two people, and the "#…" is never shown), ``turn`` (a row of a
         subtitle cell, "<cell>-<row>": a "#" line is another person than the other row of its
         own cell only, never than a line of another cell, named or not, which may be the same
         person),
         ``breaks`` (the writer's cuts: eojeol numbers 1..n-1 a new cell starts at), ``keep``
         (the writer's "one cell": no sound splits it; ignored, and said in ``check.notes``, when
         the line does not fit one cell) and ``join`` (the writer's "one cell with the line
         before": module doc "Writer's join").
sounds : included sound items only: {id, kind: "sfx"|"music", onset, t1 (sound end),
         phrase, short, source_line (line id whose speaker made the sound, or None),
         move_to (writer-forced start time, or None), estimated (the onset is a guess: a sound
         the script writes down that was not detected; its cell gets "timing_estimated")}.
cuts   : shot-change times (s).
returns: {"cells": [{"id", "start", "end", "lines": [str], "refs": [ids], "kind",
                     "pos": "bottom" | "top", "flags": ["timing_estimated", "overlap", "flash"],
                     "check": {"chars": [..], "n_lines", "cps", "dur", "gap_next", "violations": [..]}}],
          "no_room": [{"item", "t", "text", "reason"}]  (a dialogue line: also "line" and kind "dialogue";
                     "hint" {"cell", "line", "by", "how"} where one change of one cell makes room, ⑤),
          "summary": {"cells", "violations", "no_room", "timing_estimated", "overlap", "flash", ...},
          "fps": fps, "profile": "adult" | "children", "notation": style.NOTATION}

Rules: DESIGN.md §6 = 10-13 memo §5, with the Netflix sources in demo/third_party/netflix
(General Requirements 1 and 4, Subtitle Timing Guidelines = TG 1-6, Korean II.2-II.10). Every
time is a whole video frame (the nearest one, memo 1.1 ⑦), written in seconds rounded to the ms.
Pure Python and deterministic; the only library is Kiwi inside textutil.wrap (optional).

Limits and notation come from ``cboe.style`` (``settings`` = ``style.get(profile).settings()``):
16 units a line (II.2, textutil.units), 2 lines (II.4), 14 units/s or 11 for children (II.3),
5/6 s to 7 s (General Requirements 1), 2 frames between cells, and a gap under half a second is
closed to 2 frames (TG5). These limits are counted in frames at the rate ``fps`` stands for
(``style.nominal_fps``: ffprobe's 24.00001 is 24, so 5/6 s is 20 frames, as the checker and the TTML
header count it; 29999/1251 is a rate of its own); times are frames of ``fps`` as given.

Dialogue cells. One cell per script line. In front of it ``[이름/출처·말투/언어] `` (II.8: the name
when ``show_name``, then ``label_extra``, the language last; "에루/마야카" = both speak ->
``[에루, 마야카]`` or ``[함께]``, II.10). A sung line is ``♪ … ♪`` (II.7). A line that does not fit
2 x 16 units or whose speech is longer than 7 s is split at word boundaries into cells that fit,
few and balanced, cut after sentence punctuation (then commas) where possible, not between words
that stay together (General Requirements 4 by Kiwi: '무슨 | 그림자', '하지 | 않았다') nor inside a
quotation (I.14) where another cut does about as well (``CUT_KEEP``), and each long
enough to be read where it stands (from its first word to the next piece, 5/6 s and the reading
speed) whenever the line can be cut that way (_split), using the
word times. A pause of 2 s or more between two words (``PAUSE_CUT``, I.4's pause) is a cut of its
own however short the line, so that the words after it do not show before they are spoken (TG
"Subtitles should be in sync with both the image and the audio", "Avoid spoilers"; audit 2-03),
except in a line whose time is a guess, a line the writer cut (``breaks``) or kept in one cell
(``keep``), the two lines of a writer's join, and where a piece next to the pause could not then
be read where it stands (its 5/6 s and reading time before the next piece or line, with no other
line spoken over it: General Requirements 1 and II.3 are musts, the sync with the audio a should;
``_clear``). Measured in the 6th round on the 1000 made-up scenes with a pause of 2-4 s put into
2414 lines: cells with a violation 1254 without the cut, 1276 cutting at every pause (pieces after
the pause squeezed by the lines around them), 1255 with ``_clear``. The name only on the first
piece, but the language label and the notes on every piece (II.6 "in each event", II.7 "each
subtitle"), all counted in the 16 units. After splitting and
line breaking the punctuation of each line is tidied for display (style.tidy_line, I.13 and I.4:
no period or comma at a line end, a comma between two sentences, … for dots); the words are never
changed. In-time = first word, moved back to a shot change at
most half a second before it unless that would cut the speech (or the 5/6 s) of the cell before
(TG2); speech that starts less than half a second before a cut and goes on past it starts on the
cut, or half a second before it when it starts 9-11 frames (at 24 fps) before it (TG4), also when
a cut before the line could not be used.
Out-time = last word + 0.5 s, raised to 5/6 s and to 14 units/s where there is room, never later
than 7 s or 2 frames before the next cell (TG1, TG3). This covers memo 5.2's name rule: a name that
pushes the cell over 14 units/s makes it longer. A line that cannot get 5/6 s alone because the
next line starts too soon shares one hyphen cell with it ("- [빌] …" / "- [세바스찬] …", I.6; TG 6
"merging with previous or subsequent subtitles is encouraged in order to borrow time" = 앞뒤 자막과
합쳐 시간을 빌리는 것을 권한다) when the two are whole lines of two known speakers (``person``) and
of one scene, each fits one line (I.6 "Text in each line in a dual speaker subtitle must be a
contained sentence" = 두 화자 자막의 줄마다 완결된 문장) and the two are spoken within 7 s, and
① neither line's speech plus half a second reaches 5/6 s (the rule before the 6th round, which
also pairs a guessed line with a heard one); or, both heard or both guessed, ② the two are the rows
of one cell of a subtitle script (they came in one cell); or ③ the cell of the two gets 5/6 s and
the reading time of both before the next cell (before, a second line that was not short left the
first a cell under 5/6 s, GR 1). Two guessed lines make no cell by ② or ③ that 7 s from the first
in-time would end 3-11 frames before the next cell, with no room to end it half a second before
(TG 5, a must): they give way instead (``_short_gap_left``; 72 cases of the B sweep of verify f1a,
one must less each, none more). A row of a subtitle cell that cannot get 5/6 s before the next
line and cannot share a cell with it goes back to the other row of its cell before it (②). A line
whose time is a guess is drawn to a heard line by ① only: else it gives way or gets no cell (below).

Lines that overlap (_untangle). When a line starts while the line before is still being spoken
and so close that the cell before cannot be read (5/6 s and the reading speed), the cell before
is not cut down to a frame: ① two whole lines of different speakers that fit one line each share
a hyphen cell; ② else, when the time of one of them is only a guess (``timing`` voice / even), that
one gives way: to the side it has in the script when there is room there, else to the other side,
else right behind the reading time of the other (each side with its reading time, else with 5/6 s);
③ else the later one waits until the earlier one has had 5/6 s (when it still gets 5/6 s itself)
and both cells carry the flag "overlap". The heard lines are untangled among themselves first and
the guessed ones fitted in after: a heard line never waits for, nor is pushed by, a guessed one (a
guessed line with no place keeps clear of it and is left to the next step). Two pieces of one line
never overlap each other (one speaker; a piece's speech ends where the next one begins).
A cell from a line whose time is a guess carries "timing_estimated" (so does a cell of a sound whose
onset is a guess, and the cell of a writer's join with such a line in it, also when the other line
was heard and the cell is placed as a heard one); a cell shorter than a quarter of a second carries "flash", so that it never goes
out unnoticed. A line with a piece left less than 5/6 s is laid out again inside the gap between
the cells of the other lines around it (its pieces in order, each with its reading time when the
gap holds that, else 5/6 s; a heard line from the in-time of its first piece on, never earlier).
A heard line of one piece that a guessed line starting right after it (not spoken over it) leaves
under 5/6 s has that line wait until it has had its reading time, else 5/6 s, when the guessed
line still gets its own before the cell after it (``_wait_behind``; 7th round, 1000 made-up scenes:
cells under 5/6 s 766 -> 729, the checker's musts 2038 -> 1984, one sound more without room).
A later piece of a heard line may then come after its words, and also before them when the pieces
after it need the room (each still after the piece before it has had its time): 5/6 s (General
Requirements 1, a must for the checker) comes before the in-time "on the first frame of audio or as
close to it as possible" (TG 1, should: 첫 소리 프레임에, 안 되면 될 수 있는 대로 가깝게), as in
the scene rule below. Keeping every piece behind its words was measured in the 5th round on 1000
made-up scenes: 143 later pieces start before their first word; holding every piece behind its
words leaves 127 groups of pieces in which no layout gives each piece 5/6 s, and it gave 156 more
cells under 5/6 s and 30 more flashes. When the gap cannot hold its pieces, a guessed line (not one matched
by the ASR) gets no cell and goes to ``no_room`` (the writer decides: a guessed line is no reason
to show a few frames of text, 감사 2-m1); a heard line keeps its cells, and when the writer's cut
points made them that short, the short cell says so in ``check.notes``.

Scenes (TG 4 "Subtitles may not cross scene changes"): two lines of different ``scene_id`` never
share a hyphen cell, and the last cell of a scene never runs (longer out-time, closed gap) over a
shot change between it and the first line of the next scene — unless its speech goes on over that
cut, or ending before it would leave the cell under 5/6 s or over the reading speed (the cell then
says so in ``check.notes``): 5/6 s (General Requirements 1) and the reading speed (II.3) come
before the scene rule, as the checker weighs them (must against a shot-change rule, should). A cut
the cell starts on is no limit. Lines without ``scene_id`` are not affected.
Writer's cuts: ``breaks`` replaces the automatic cut points of the line (a piece that does not fit
is cut further and the cell says so in ``check.notes``). A line wrapped right after its label
("[김철수]" alone on the first line) is wrapped again with the first word kept next to the label,
unless the 16 units leave no other way.
Writer's join (``join``, audit 2-02, 7-03): the line goes into one cell with the line placed just
before it in script order (the first line placed has none), before any automatic rule: two
speakers (``person`` differs) make one hyphen cell however far apart they are spoken, one speaker
one cell of the two texts with a space between, the words and times of both and the label of the
first (no sound splits that cell again). Only when each hyphen line fits one line (else the two
texts 2 x 16 units), the speech of the two lasts at most 7 s from the in-time, the line before is
in no cell with another line yet (two lines a cell), neither line is cut in pieces and no other
line has a cell between the two; else the line keeps its cells and they say why in ``check.notes``
("앞 줄과 한 칸으로 묶지 못함 · 7초 초과"). The cell has both line ids in ``refs`` and gets every
check and end rule of any other cell.

Sound cells (memo 5.2). sfx: start = onset frame, never earlier, even near a cut (BBC 5.5);
length = units / 14 + 1 s (BBC 4.3.3) within [5/6, 7] s, whatever the sound's own length.
music: start = onset, or the shot change in the half second before it (TG2); length 3 s, or
music end + 0.5 s when that comes first, at least 5/6 s. ``move_to`` (the writer's) replaces
the onset.

Positions (memo 5.3), sounds in time order:
 ① no cell on screen at the sound (cells before it may be shortened to their floor: speech end,
   5/6 s and 14 units/s, and ``INTERRUPT_SHOW`` after a sound they hold after their in-time, ④;
   this floor holds wherever a cell is shortened for a sound) and at least 5/6 s free after it: an
   own cell;
 ② within 0.5 s of the start of a one-line cell, other source: the same cell, "- " lines in heard
   order (two sounds of a sound-only cell too, II.5);
 ③ the same, sound made by the speaker (source_line is that line): no hyphen,
   "[지수] [재채기] 나도야!" on one line, or on two when it does not fit one (II.5);
 ④ more than 0.5 s after the start of a dialogue cell while its speech goes on (a sound that
   interrupts the dialogue): the cell is split at the word boundary nearest the sound (at most 0.5 s
   from it; a sound in the pause between two words is at that boundary). The sound joins the part
   before it, in the order of the audio (II.9 "Sound effects which interrupt dialogue should be
   treated as follows, ideally following the order of the original audio" = 대사를 끊는 효과음은 될
   수 있으면 원래 소리의 순서대로: "- 내가 어제 봤는데…" / "- [첨벙!]", then "그런 상황은 아니었어"):
   that cell from the in-time of the dialogue cell, the later part from its first word (at ⑤-2 up
   to half a second later, for the 5/6 s of the cell before). Only when the sound comes before that
   first word, its words show at most ``INTERRUPT_LEAD`` (2 s) before it is heard and stay at least
   ``INTERRUPT_SHOW`` (0.25 s) after it (a sound heard just before, on or after the first word of the
   later part, as when the words run on, would leave the screen as it is heard), the part before
   fits one hyphen line and both cells get 5/6 s and their reading time;
   "…" ends the part before only where the speech stops for the sound (``INTERRUPT_PAUSE``, 0.5 s
   between the two words; I.4 "an abrupt interruption") and it has no punctuation of its own, never
   on every cut piece (audit 1-11). Otherwise, and always for a sound the speaker made (③'s form,
   no hyphen: "[재채기] 그런 상황은 아니었어"), the sound joins the later part, which starts at the
   sound (BBC 5.5), both parts at least 5/6 s: the order before the 6th round;
 ⑤ over a limit, in order: short phrase -> longer cell end into the gap after it -> an own cell in a
   gap that starts at most 0.5 s after the sound -> a dialogue cell of two lines on screen at the
   sound (or starting within 0.5 s after it; one line of the script, not kept in one cell by the
   writer) cut at a word boundary into two cells of one line each, each with 5/6 s and its reading
   time, the sound then sharing one of them (②, ③) or getting its own cell (①, ⑤-3), never a third
   cut (TG 6 "You may need to re-segment and re-time subtitles and their neighbouring subtitles";
   audit 2-05, 4-m2; ``_resplit``) -> a sound heard ``LONG_SOUND`` (2 s) or longer, music most of
   all: an own cell in the first gap that opens while it is still heard for 5/6 s, at most
   ``LONG_LATE`` (3 s) after the onset, which its ``check.notes`` say ("음악 시작 1.29초 뒤"; not a
   sound whose onset is a guess nor one the writer moved; ``_late``) -> ``no_room`` with the reason
   ("소리 뒤 빈틈 0.42초"), and the one change of one cell that would make room where one change is
   enough: ``hint`` {"cell", "line", "by" (s), "how": "end_earlier" | "move_later"} and the reason
   ending in it (" · 앞 대사 칸을 0.42초 줄이면 들어감", audit 7-07; also for a guessed line without
   room): worked out on the cells as written, a change that keeps that cell's 5/6 s and reading
   time and moves an in-time by at most half a second (``_sound_hint``, ``_line_hint``).
One sound per dialogue cell, never into a cell that already has two lines (⑤ cuts it first), the
words of the dialogue are never changed (only line breaks and the display punctuation), and every
sound ends up in a cell or in ``no_room``. A sound phrase of an older work folder ('♬ 무서운 음악')
is written in today's notation ('[무서운 음악]').
Measured at the end of the 7th round, from commit 0089a2a (before the 6th round's sound rules: ④'s
cell with the part before the sound, ⑤'s re-split and late cell, the hints) to this code with every
rule after it (1000 made-up scenes, REAL=1 REALDUR=1): sounds placed 975 -> 1278 of 3049, sounds in
no_room 2074 -> 1771 (0 -> 93 with a hint: 79 sound effects, 14 music), lines in no_room 110 -> 110
(0 -> 42 with a hint), cells with a violation 1579 -> 1544, the checker's musts 2038 -> 1984; the copy
of the first 30 minutes of a film: cells 313 -> 322, sound effects without room 17 -> 8, lines without
room 2 -> 2, musts 12 -> 7.

End adjustment (memo 5.2 끝 조정, TG1, 2, 4, 5), every cell in time order: ① a gap under half a
second to the next cell is closed to 2 frames, except after a cell with dialogue that already stays
half a second after its audio: that one is not made longer and ends half a second before the next
cell instead (TG5 "If an out-time has been extended by half a second beyond the end of the audio,
avoid extending this further to close gaps … ensuring that the gap is half a second or more",
audit 2-08), unless that leaves it under 5/6 s or over the reading speed (closed to 2 frames: the
musts first) or a shot change is within half a second before that out-time or in the gap (the
shot change rules decide, as before: TG2 applies them after the half second too); ② an out-time
within half a second before a shot
change moves to 2 frames before it (when the next cell starts after the cut); ③ a cell whose audio
is over before a shot change it crosses ends 2 frames before the cut when 5/6 s and 14 units/s
still hold (a dialogue cell that cannot stays at least half a second into the new shot); a dialogue
cell whose speech crosses the cut runs at least half a second past it where there is room; a sound
that goes on past the cut may cross it. A gap of 3 frames to half a second that ③ leaves without a
cut in it is closed again as in ① (or, when 7 s forbids it, widened to half a second). ④ a dialogue cell
that starts less than half a second after a cut the cell before ends 2 frames before (or on) starts
on the cut (TG2), which closes that gap to 2 frames (TG5). A gap with a cut in it that is left
(a sound starts on its onset, BBC 5.5) is not reported by placement; any other is ("빈틈 0.5초 미만").
"""
from __future__ import annotations

import itertools
import math
import re
import unicodedata

from . import style
from .textutil import EPS, end_bonus, fits, keep_costs, note_left_alone, units, wrap

INF = float("inf")
ESTIMATED = ("voice", "even")               # dialogue.json ``timing`` of a line whose time is a guess
NO_TIME = ("absent", "out_of_range")        # dialogue.json ``status`` of a line that has no time in the video
_LABELS_ONLY = re.compile(r"(?:-\s)?(?:\[[^\[\]]*\]\s?)+")     # "[김철수]", "- [지수] [재채기]"
_LABELS_HEAD = re.compile(r"(?:-\s)?(?:\[[^\[\]]*\]\s)+")

# the numbers of cboe.style (adult profile) and whether textutil.wrap uses the Kiwi phrase hints;
# ``place(settings=...)`` overrides them, e.g. with style.get("children").settings()
DEFAULTS = dict(style.get().settings(), kiwi=True)


class _P:
    """Limits of one run, in frames."""

    def __init__(self, fps, settings=None):
        s = dict(DEFAULTS)
        s.update({k: v for k, v in (settings or {}).items() if v is not None})
        fps = _num(fps) or 0.0
        self.fps = fps if fps > 0 else 25.0              # the frame grid: time <-> frame
        # the limits in frames are counted at the rate this one stands for (style.nominal_fps:
        # ffprobe's 24.00001 is 24, so 5/6 s is 20 frames as in the checker and the TTML header)
        self.rate = f = style.nominal_fps(self.fps)
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
        # the sound rules of this module (``INTERRUPT_LEAD`` …), in frames as the limits above
        self.lead = int(math.floor(INTERRUPT_LEAD * f + 0.5))
        self.pause_f = int(math.floor(INTERRUPT_PAUSE * f + 0.5))
        self.show_f = int(math.floor(INTERRUPT_SHOW * f + 0.5))     # 6 frames at 23.976 fps
        self.long_f = int(math.floor(LONG_SOUND * f + 0.5))
        self.late_f = int(math.floor(LONG_LATE * f + 0.5))
        # everything above comes from the frame rate and the settings: the key of what is
        # remembered for these limits (``_split_memo``); None when a setting cannot be a key
        try:
            self.key = (self.fps, tuple(sorted(s.items())))
            hash(self.key)
        except TypeError:
            self.key = None

    def fr(self, t: float) -> int:
        return int(round(float(t) * self.fps))

    def sec(self, k: int) -> float:
        return round(k / self.fps, 3)

    def need(self, u: float) -> int:
        """Frames needed to read ``u`` units at the reading speed limit."""
        return int(math.ceil(u / self.cps * self.rate - 1e-6)) if u > 0 else 0

    def cps_of(self, u: float, dur_f: int) -> float:
        return u * self.rate / dur_f if dur_f > 0 else INF

    def too_fast(self, u: float, dur_f: int) -> bool:
        return self.cps_of(u, dur_f) > self.cps + 1e-6

    def lines_ok(self, lines) -> bool:
        return fits(lines, self.max_units, self.max_lines)

    def wrap(self, text) -> list[str]:
        """The displayed lines of ``text``: broken on the text as written, punctuation tidied. A
        first line that would hold the speaker label alone ("[김철수]" / "대사…") is avoided by
        keeping the first word next to the label, when that still fits the limits."""
        lines = wrap(text, self.max_units, self.max_lines, kiwi=self.kiwi, tidy=style.tidy_line)
        if len(lines) > 1 and _LABELS_ONLY.fullmatch(lines[0].strip()):
            alt = self._label_kept(text)
            if alt and _words(alt) == _words(lines):       # no word cut in two
                return alt
        return lines

    def _label_kept(self, text):
        t = " ".join(str(text or "").split())
        m = _LABELS_HEAD.match(t)
        if not m or m.end() >= len(t):
            return None
        words = t[m.end():].split(" ")
        # the opening note of a sung line is no word of its own: it stays by its lyric (II.7 "Add a
        # space between the music note and the preceding or subsequent text" = 음표와 앞뒤 글 사이에
        # 빈칸), so the label keeps the note and the first word of the lyric, never the note alone
        k = 2 if words[0] == style.NOTE and len(words) > 1 else 1
        head = t[:m.end()] + " ".join(words[:k])
        lines = wrap(t, self.max_units, self.max_lines, kiwi=self.kiwi, tidy=style.tidy_line, hold=len(head))
        ok = (lines and len(lines) <= self.max_lines and all(units(x) <= self.max_units + EPS for x in lines)
              and not _LABELS_ONLY.fullmatch(lines[0].strip()) and not note_left_alone(lines))
        return lines if ok else None

    def fits(self, text: str) -> bool:
        """Whether ``text`` as displayed stays within the line and line-count limits."""
        return fits(text, self.max_units, self.max_lines, tidy=style.tidy_line)


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _words(lines) -> list[str]:
    """The words of displayed lines without the period or comma that tidying takes off a line end
    ('잠깐만요,' at the end of a line is '잠깐만요'), to compare two wrappings of one text."""
    return [w.rstrip(".,") for w in " ".join(lines).split()]


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
    """The earliest out-time ``c`` may be shortened to: its speech, 5/6 s and the reading speed,
    and ``INTERRUPT_SHOW`` after a sound it holds after its in-time (④'s "- 내가 어제 봤는데…" /
    "- [첨벙!]": the words of the sound stay after it is heard; 7th round, G3-12)."""
    se = c.speech_end()
    lo = max(se if se is not None else c.start, c.start + P.min_f, c.start + P.need(c.units),
             max((p["t"] + P.show_f for p in c.sounds() if p["t"] > c.start), default=c.start))
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


UNREADABLE = 1000.0     # the cost of a piece that cannot stay 5/6 s and its reading time (_split)
# the cost of a cell cut between words that stay together (_split), times textutil.keep_costs: a
# cost of 2 (an adnominal and its noun '예쁜 | 꽃', an auxiliary, a name, a number, inside a
# quotation) is 20, the constant of one more piece, so the cut moves to another word when that
# costs less than about one more cell (audit 2-03). Not measured
CUT_KEEP = 10.0
# a pause of this many seconds between two words of a line is a cut point of its own (_dialogue_parts):
# I.4 "Use ellipses to indicate a pause (2 seconds or more)" (2초 이상 쉼) is the length the Korean
# guide calls a pause, and a cell held over it shows the words after it before they are spoken
# (TG "Subtitles should be in sync with both the image and the audio" = 자막은 화면·소리와 맞아야
# 한다, "Avoid spoilers" = 스포일러를 피하라)
PAUSE_CUT = 2.0


def _cut_costs(toks: list[str], P: _P) -> list[float]:
    """The cost of a cell cut before word j of ``toks`` (index j, 0 < j < n) for words that stay
    together: ``CUT_KEEP`` times ``textutil.keep_costs`` of the space there."""
    kc = keep_costs(" ".join(toks), kiwi=P.kiwi)
    out, pos = [0.0] * (len(toks) + 1), -1
    for j in range(1, len(toks)):
        pos += len(toks[j - 1]) + 1                  # the space before word j
        out[j] = CUT_KEEP * kc.get(pos, 0.0)
    return out


def _pauses(times: list) -> list[int]:
    """The words of a line that come after a pause of ``PAUSE_CUT`` or more (their index)."""
    return [k for k in range(1, len(times)) if times[k][0] - times[k - 1][1] >= PAUSE_CUT - 1e-6]


def _room(times: list, i: int, j: int, P: _P, after=None, others=None) -> int:
    """Frames the piece of words i..j of a line (word ``times``) can stay where it stands (``_split``):
    to 2 frames before the next piece, ``after`` or the next line that starts after it (``others``,
    sorted start frames; without them its last word + 0.5 s), at most 7 s."""
    import bisect
    s = P.fr(times[i][0])
    if j < len(times):
        e = P.fr(times[j][0]) - P.gap
    elif after is not None:
        e = after - P.gap
    elif others is not None:
        k = bisect.bisect_right(others, s)
        e = others[k] - P.gap if k < len(others) else INF
    else:
        e = P.fr(times[-1][1]) + P.tail
    return min(e - s, P.max_f)


def _reading(text: str, P: _P) -> int:
    """Frames a piece showing ``text`` has to stay: 5/6 s and its reading time (II.3, General
    Requirements 1), the units as displayed, as the cell counts them."""
    shown = sum(units(x) for x in wrap(text, P.max_units, P.max_lines, kiwi=False, tidy=style.tidy_line))
    return max(P.min_f, P.need(shown))


def _readable(text: str, room, P: _P) -> bool:
    return room >= _reading(text, P)


def _clear(text: str, times: list, i: int, j: int, P: _P, others, spans, order) -> bool:
    """Whether the piece of words i..j next to a pause can be read where it stands: its reading time
    (``_reading``) before the next piece of its line and before the next line that starts after its
    first word, and no other line spoken over that time (``spans``: two people talking over each
    other is left to the rules for lines that overlap, as before the cut at pauses)."""
    import bisect
    s, need = P.fr(times[i][0]), _reading(text, P)
    room = _room(times, i, j, P, others=others)
    if others:
        k = bisect.bisect_right(others, s)
        if k < len(others):
            room = min(room, others[k] - P.gap - s)
    if room < need:
        return False
    return not any(o != order and a < s + need and b > s for a, b, o in spans or ())


def _split(toks: list[str], times: list, text_of, P: _P, after=None, others=None) -> list[tuple[int, int]]:
    """Word ranges of the cells of one line: the whole line when it fits 2 x 16 units and 7 s, else
    the cheapest pieces that each fit. ``text_of(i, j)`` is the text of the piece of words i..j as
    it is written into a cell, with its label and notes, so those count in the 16 units. A piece
    costs a constant (fewer cells) plus its squared length (balanced cells); a piece that ends
    after sentence punctuation costs less (a comma a little less), so the line is cut at its
    sentence ends when that takes at most one more cell or so; a cut between words that stay
    together costs more (``_cut_costs``: General Requirements 4 'The line break should not
    separate …', a quotation, I.14), so a cell does not end on '무슨' of '무슨 그림자' or inside a
    quotation when another cut does about as well. A piece that cannot be read where
    it stands costs ``UNREADABLE``, far more than any of that: its cell lasts from its first word
    to 2 frames before the first word of the next piece (``after``: the frame the next piece
    starts on when these words end before the line does, a writer's cut), the last one until 2
    frames before the next line that starts after it (``others``: the sorted start frames of the
    lines; without them, its last word + 0.5 s), at most 7 s, and that has to hold 5/6 s and the
    reading time of its text (II.3, General Requirements 1). So a cut right after '아…' that
    leaves an eighth of a second is never taken while another way of cutting the line can be
    read (4th review)."""
    n = len(toks)

    def span(i, j):
        return P.fr(times[j - 1][1]) - P.fr(times[i][0])

    def room(i, j) -> int:
        return _room(times, i, j, P, after, others)

    if P.fits(text_of(0, n)) and span(0, n) <= P.max_f:
        return [(0, n)]
    pieces: list = [[] for _ in range(n + 1)]        # pieces[j]: (i, cost) of the pieces i..j that fit
    for j in range(1, n + 1):
        for i in range(j - 1, -1, -1):
            text = text_of(i, j)
            if j - i > 1 and (span(i, j) > P.max_f or not P.fits(text)):
                break                                # longer pieces do not fit either
            u = units(text)
            cost = 20.0 + (u / 4.0) ** 2
            if j < n:
                cost -= 2.0 * end_bonus(toks[j - 1])
            if not _readable(text, room(i, j), P):
                cost += UNREADABLE
            pieces[j].append((i, cost))

    def cheapest(keep):
        best: list = [None] * (n + 1)                # (cost, start of the last piece)
        best[0] = (0.0, -1)
        for j in range(1, n + 1):
            for i, cost in pieces[j]:
                cost += best[i][0] + (keep[j] if j < n else 0.0)
                if best[j] is None or cost < best[j][0] - 1e-9:
                    best[j] = (cost, i)
        return best

    best = cheapest(_cut_costs(toks, P))
    if best[n][0] >= UNREADABLE:
        # no way to cut it where every piece can be read: the words that stay together do not choose
        # among such ways (``_make_room`` lays the pieces out again), so it is cut as before them
        best = cheapest([0.0] * (n + 1))
    out, j = [], n
    while j > 0:
        i = best[j][1]
        out.append((i, j))
        j = i
    return out[::-1]


# 같은 입력의 결과를 기억해 둔다 (감사 6-08: 작가가 하나 고칠 때마다 배치를 처음부터 다시 하고, 그때마다 바뀌지
# 않은 줄도 조각 나누기를 다시 계산했다). ``_split`` 이 읽는 것은 낱말, 낱말 시각을 프레임으로 바꾼 값(``P.fr``
# 로만 읽는다), 조각의 글을 만드는 머리표·이어지는 표·노래 여부, 한도(``P.key``), ``after``, 그리고 ``others``
# 에서는 낱말 첫 프레임마다 그 뒤에 처음 시작하는 줄의 프레임뿐이다(``_room``: ``after`` 가 없을 때만). 그것을
# 모두 열쇠에 넣으므로 기억해 둔 결과는 다시 계산한 것과 같다. ``MEMO = False`` 이면 기억하지 않는다
MEMO = True
_SPLIT_MEMO: dict = {}
_SPLIT_MAX = 32768


def clear_memo() -> None:
    """Forget the remembered cuts of lines (``_split_memo``) and the wrapped lines (textutil)."""
    from . import textutil
    _SPLIT_MEMO.clear()
    textutil.clear_memo()


def _split_memo(toks: list[str], times: list, head: str, cont: str, sung: bool, P: _P, after=None,
                others=None) -> list[tuple[int, int]]:
    """``_split`` of the words ``toks`` whose piece i..j is written ``(head if i == 0 else cont) +
    _body(toks[i:j], sung)``, remembered by everything it reads (above)."""
    def text_of(i, j):
        return (head if i == 0 else cont) + _body(toks[i:j], sung)

    if not MEMO or P.key is None:
        return _split(toks, times, text_of, P, after, others)
    import bisect
    try:
        frames = tuple((P.fr(s), P.fr(e)) for s, e in times)
        nxt = None
        if after is None and others is not None:   # _room: the next line start after a first word
            nxt = tuple(others[k] if k < len(others) else None
                        for k in (bisect.bisect_right(others, a) for a, _ in frames))
        key = (tuple(toks), frames, head, cont, bool(sung), P.key, type(after), after, others is None, nxt)
        got = _SPLIT_MEMO.get(key)
    except (TypeError, ValueError, OverflowError):   # a value that cannot be a key: no memo
        return _split(toks, times, text_of, P, after, others)
    if got is None:
        got = tuple(_split(toks, times, text_of, P, after, others))
        if len(_SPLIT_MEMO) >= _SPLIT_MAX:
            _SPLIT_MEMO.clear()
        _SPLIT_MEMO[key] = got
    return list(got)


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


def _dialogue_parts(ln: dict, P: _P, order: int = 0, others=None, pauses: bool = True, spans=None) -> list[dict]:
    """The pieces of one script line as cell parts (empty when it has no text or no time).
    ``order`` is the place of the line in the script; ``others`` the sorted start frames of the
    lines (``_split``: how long the last piece can stay); ``spans`` (first word, last word, order)
    frames of the lines. A pause of ``PAUSE_CUT`` between two words is a cut point whatever the
    length of the line (audit 2-03), unless the writer chose the cuts (``breaks``) or one cell
    (``keep``), the time of the line is a guess (its word times are shared out, so its pauses are
    not known) or ``pauses`` is False (a writer's join, _dialogue_cells); and only where the pieces
    on both sides of it can then be read where they stand (``_clear``)."""
    text = " ".join(str(ln.get("text") or "").split())
    if not text or ln.get("status") in NO_TIME:
        return []
    toks = text.split(" ")
    times = _word_times(ln, toks)
    if times is None:
        return []
    head, cont = style.line_labels(ln)               # "[이름/출처·말투/언어] " and "[언어] "
    sung = bool(ln.get("sung"))
    n = len(toks)
    guess = ln.get("timing") in ESTIMATED or ln.get("status") == "unheard"
    cuts = sorted({int(b) for b in ln.get("breaks") or () if isinstance(b, (int, float)) and 0 < int(b) < n})
    paused = [] if cuts or ln.get("keep") or guess or not pauses else _pauses(times)

    def text_of(i, j):
        return (head if i == 0 else cont) + _body(toks[i:j], sung)

    def cut_at(points):                              # the writer's cut points, or the pauses; a piece
        ranges, more = [], False                     # that does not fit is cut further
        for a, b in zip([0] + points, points + [n]):
            # text_of(a + i, a + j): the line's label only on the piece that starts the line
            sub = _split_memo(toks[a:b], times[a:b], head if a == 0 else cont, cont, sung, P,
                              after=P.fr(times[b][0]) if b < n else None, others=others)
            more = more or len(sub) > 1
            ranges += [(a + i, a + j) for i, j in sub]
        return ranges, more

    notes = []
    if cuts:
        ranges, more = cut_at(cuts)
        if more:
            notes.append("작가가 정한 칸이 한도를 넘어 더 나눔")
    else:
        ranges = None
        while paused:
            # a pause is no cut where a piece next to it could not then be read where it stands (5/6 s
            # and the reading speed, musts, before the sync with the audio): it is left as before
            ranges, _ = cut_at(paused)
            bad = [k for k in paused if any((i == k or j == k) and not _clear(text_of(i, j), times, i, j, P, others,
                                                                               spans, order) for i, j in ranges)]
            if not bad:
                break
            paused.remove(bad[0])
            ranges = None
        if ranges is None:
            ranges = _split_memo(toks, times, head, cont, sung, P, others=others)
    keep = bool(ln.get("keep")) and not cuts
    if keep and len(ranges) > 1:
        notes.append(f"한 칸에 두기: {P.max_lines}줄 {P.max_units:g}자·{P.max_dur:g}초를 넘어 나눔")
        keep = False
    parts = []
    for i, j in ranges:
        pre = head if i == 0 else cont
        body = _body(toks[i:j], sung)
        if not style.tidy_line(" ".join(toks[i:j])):     # a piece that is only punctuation shows nothing
            continue
        words = [(toks[x], P.fr(times[x][0]), P.fr(times[x][1])) for x in range(i, j)]
        parts.append({"kind": "dialogue", "id": ln.get("id"), "prefix": pre, "body": body, "text": pre + body,
                      "t": words[0][1], "e": max(w[2] for w in words), "words": words,
                      "speaker": ln.get("person") or ln.get("name") or ln.get("speaker"), "whole": len(ranges) == 1,
                      "cont": cont, "sung": sung, "guess": guess, "order": order, "pos": ln.get("pos"),
                      "scene": ln.get("scene_id"), "timing": ln.get("timing"), "keep": keep,
                      "turn": ln.get("turn"), "breaks": bool(cuts), "paused": bool(paused),
                      "notes": list(dict.fromkeys(notes))})
    for p, q in zip(parts, parts[1:]):           # one speaker: a piece is over where the next one begins
        if p["e"] > q["t"]:                      # (word times of the ASR may overlap by a few frames)
            p["e"] = max(p["t"], q["t"])
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


def _pair_short(cells: list[_Cell], P: _P, w1=None) -> list[_Cell]:
    """A line too short to be read alone and the line after it, of another speaker, in one hyphen
    cell (module doc, ``_can_pair``); a row of a subtitle cell that cannot be paired so goes back
    to the other row of its cell before it (``_back_to_row``)."""
    out, i = [], 0
    while i < len(cells):
        a = cells[i]
        b = cells[i + 1] if i + 1 < len(cells) else None
        c = cells[i + 2] if i + 2 < len(cells) else None
        if b is not None and _can_pair(a, b, c, P, w1):
            a.set([a.parts[0], b.parts[0]], "hyphen", P)
            out.append(a)
            i += 2
        elif out and b is not None and _back_to_row(out[-1], a, b, P):
            out[-1].set([out[-1].parts[0], a.parts[0]], "hyphen", P)
            i += 1
        else:
            out.append(a)
            i += 1
    return out


def _back_to_row(p: _Cell, a: _Cell, b: _Cell, P: _P) -> bool:
    """② of ``_can_pair`` the other way: ``a`` cannot get 5/6 s before ``b`` and cannot share a
    cell with it, and ``p`` before it is the other row of its subtitle cell (both heard or both
    guessed): the two go back into the one cell they came in, from the in-time of ``p``."""
    if b.start - P.gap - a.start >= P.min_f or not _two_speakers(p, a, P):
        return False
    pp, pa = p.parts[0], a.parts[0]
    return bool(pp.get("guess")) == bool(pa.get("guess")) and _one_cell(pp, pa)


def _turn_cell(p: dict):
    """The subtitle cell a row of a subtitle script comes from ("1" of its turn "1-2"), or None."""
    return str(p.get("turn") or "").strip().partition("-")[0] or None


def _one_cell(pa: dict, pb: dict) -> bool:
    """Whether ``pa`` and ``pb`` are rows of one cell of a subtitle script (their turns name it)."""
    ca = _turn_cell(pa)
    return ca is not None and ca == _turn_cell(pb)


def _other_people(pa: dict, pb: dict) -> bool:
    """Whether the lines of ``pa`` and ``pb`` are known to be spoken by two different people. A
    row nobody is named for ("#<cell>-<row>", ``place``) is another person than the other row of
    its own cell only: the hyphen of that cell said so (I.6 "two characters speak in one
    subtitle" = 두 인물이 한 자막에서 말함), but a row of another cell, named or not, may be the
    same person, so the two are never taken for two speakers. Named lines as they are."""
    a, b = pa.get("speaker"), pb.get("speaker")
    if not a or not b or a == b:
        return False
    if str(a).startswith("#") or str(b).startswith("#"):
        return _one_cell(pa, pb)
    return True


def _two_speakers(a: _Cell, b: _Cell, P: _P) -> bool:
    """Whether ``a`` and ``b`` can be the two lines of one hyphen cell (I.6): two whole script
    lines ("a contained sentence" each) of different, known speakers (``_other_people``), one line
    each, within 7 s."""
    if a.mode != "plain" or b.mode != "plain":
        return False
    pa, pb = a.parts[0], b.parts[0]
    if not (pa["whole"] and pb["whole"]):
        return False
    if not _other_people(pa, pb):
        return False
    if pa.get("scene") and pb.get("scene") and pa["scene"] != pb["scene"]:
        return False                                 # one cell never spans a scene change (TG 4)
    if any(units(_hyphen_line(p)) > P.max_units + EPS for p in (pa, pb)):
        return False
    return max(pa["e"], pb["e"]) - a.start <= P.max_f


def _can_pair(a: _Cell, b: _Cell, c, P: _P, w1=None) -> bool:
    """Whether ``a`` and the line ``b`` after it share one hyphen cell (module doc): two speakers
    (``_two_speakers``), ``a`` cannot get 5/6 s alone because ``b`` starts too soon, and
    ① neither line's speech plus half a second reaches 5/6 s (the rule before the 6th round, also
    for a line whose time is a guess beside a heard one); or, both heard or both guessed,
    ② they are the two rows of one cell of a subtitle script (that cell held them together); or
    ③ the cell of the two still gets 5/6 s and the reading time of both lines before ``c``, the cell
    after ``b`` (and the window end ``w1``): GR 1 (5/6 s, a must) is kept by TG 6 "merging with
    previous or subsequent subtitles is encouraged in order to borrow time" (앞뒤 자막과 합쳐 시간을
    빌리는 것을 권한다) where a hyphen cell can hold the two."""
    if not _two_speakers(a, b, P):
        return False
    pa, pb = a.parts[0], b.parts[0]
    if b.start - P.gap - a.start >= P.min_f:             # the first line can get 5/6 s alone
        return False
    nat_b = min(pb["e"] + P.tail, (c.start - P.gap) if c is not None else INF) - b.start
    if not (pa["e"] + P.tail - a.start >= P.min_f or nat_b >= P.min_f):
        return True                                      # ① both short
    if bool(pa.get("guess")) != bool(pb.get("guess")):
        return False         # a guessed line beside a heard one: it gives way or gets no cell (_make_room)
    need = max(P.min_f, P.need(units(_hyphen_line(pa)) + units(_hyphen_line(pb))))
    # two guessed lines can give way instead (_untangle, _make_room): no cell of the two that 7 s would
    # end 3-11 frames before the next one (TG 5, a must). Two heard lines keep it: alone, the first
    # would be a few frames on screen (GR 1, a must too; measured on the B sweep of verify f1a)
    avoid = bool(pa.get("guess")) and _short_gap_left(a, pa, pb, c, need, P)
    if _one_cell(pa, pb):
        return not avoid                                 # ②
    room = min((c.start - P.gap) if c is not None else INF, a.start + P.max_f,
               w1 if w1 is not None else INF) - a.start
    return room >= need and not avoid                    # ③


def _short_gap_left(a: _Cell, pa: dict, pb: dict, c, need: int, P: _P) -> bool:
    """Whether the hyphen cell of ``pa`` and ``pb`` from ``a.start`` would end 3-11 frames before
    the cell after, ``c``: 7 s from its in-time comes before 2 frames before ``c``, and half a second
    before ``c`` comes before the speech of the two and the reading time (``need``) — TG 5 "gaps
    between subtitles of 3-11 frames inclusive must be closed to 2 frames" (a must). The in-time of
    the cell is the first line's, so the second line alone may still close that gap."""
    if c is None:
        return False
    cap = a.start + P.max_f
    if c.start - P.gap <= cap or c.start - cap >= P.half_exact - 1e-9:
        return False
    return _half_before(c.start, P) < max(pa["e"], pb["e"], a.start + need)


# the writer's "one cell with the line before" (module doc "Writer's join")

JOIN_NOTE = "앞 줄과 한 칸으로 묶지 못함 · {}"


def _ids(p: dict) -> list:
    """The line ids of a part: one, or the lines a writer's join put together (same speaker)."""
    return p.get("ids") or [p.get("id")]


def _whole(ln: dict) -> tuple[str, str]:
    """(label, words) of a whole line as one cell would show it, cut or not."""
    return style.line_labels(ln)[0], _body(" ".join(str(ln.get("text") or "").split()).split(" "), bool(ln.get("sung")))


def _join(cells: list[_Cell], lines: list, P: _P, refused=None) -> set:
    """``join`` of a line (the writer): one cell with the line before it, the one placed just
    before it in script order. Two speakers: one hyphen cell, '- ' a line each (I.6), however far
    apart the two are; one speaker (the same ``person``): one cell of the two texts, a space between,
    the words and their times of both, the label of the first. Only when the two are of one scene
    (TG 4), the label of the first does for both of one speaker (``_label_lost``: the same language,
    II.6 "in each event"; no source or manner the first does not show), that cell fits (one line of
    16 units each as hyphen lines, else
    2 x 16 units) and the speech of the two lasts at most 7 s (GR 1), the line before is in no cell
    with another line yet (two lines a cell), neither is cut in pieces and no other line has a cell
    between them. Otherwise the line keeps its own cells and they say why in ``check.notes``
    (``JOIN_NOTE``). The cell gets the checks of any other cell. ``refused``: {line id: why} of the
    joins refused already (``_dialogue_cells``: not tried again, only said); a join refused here is
    added to it. Returns the script places (``order``) of the lines in a join made."""
    refused = {} if refused is None else refused
    made = set()
    seen = sorted({(p["order"], p["id"]) for c in cells for p in c.dialogue()})
    for (k0, before), (k, lid) in zip(seen, seen[1:]):
        if not isinstance(lines[k], dict) or not lines[k].get("join"):
            continue
        mine = [c for c in cells if any(lid in _ids(p) for p in c.dialogue())]
        theirs = [c for c in cells if any(before in _ids(p) for p in c.dialogue())]
        why = refused.get(lid) or _join_cells(cells, theirs, mine, lines[k0], lines[k], P)
        if why:
            refused[lid] = why
            note = JOIN_NOTE.format(why)
            for c in mine:
                for p in c.dialogue():
                    if lid in _ids(p) and note not in p["notes"]:
                        p["notes"].append(note)
        else:
            made.update((k0, k))
    return made


def _label_lost(ln0: dict, ln: dict) -> bool:
    """Whether one cell of a speaker's two lines, under the label of the first ``ln0``, would show
    the second ``ln`` wrongly: another language (II.6 "use [in language] at the beginning of the
    dialogue in each event"), or a word after the name the first does not have (``label_extra``: a
    source, a manner of speech). The source goes with the name only, so a second line without its
    name shows none and goes on under the first's (state.label_extra)."""
    if (ln0.get("language") or None) != (ln.get("language") or None):
        return True
    return not set(ln.get("label_extra") or ()) <= set(ln0.get("label_extra") or ())


def _join_cells(cells: list[_Cell], theirs: list[_Cell], mine: list[_Cell], ln0: dict, ln: dict, P: _P):
    """Put the line of the cells ``mine`` with the line before it (cells ``theirs``) when ``_join``
    allows it; returns None, or why not."""
    a, b = theirs[-1], mine[0]
    pa, pb = a.dialogue()[0], b.dialogue()[0]
    if a.mode != "plain" or len(a.parts) > 1:
        return "앞 줄이 이미 다른 줄과 한 칸"
    if pa.get("scene") and pb.get("scene") and pa["scene"] != pb["scene"]:
        return "장면이 다름"                         # one cell never spans a scene change (TG 4)
    one = len(theirs) == 1 and len(mine) == 1
    (h0, w0), (h1, w1) = ((pa["prefix"], pa["body"]) if len(theirs) == 1 else _whole(ln0),
                          (pb["prefix"], pb["body"]) if len(mine) == 1 else _whole(ln))
    same = pa.get("speaker") == pb.get("speaker")
    if same and _label_lost(ln0, ln):
        return "화자 표시가 다름"                     # [영어] · 출처 · 말투 of the second line (II.6, II.8)
    if same and not P.fits(h0 + w0 + " " + w1):
        return f"너무 김: {P.max_lines}줄 {P.max_units:g}자 초과"
    if not same and any(units(style.tidy_line(style.HYPHEN + h + w)) > P.max_units + EPS
                        for h, w in ((h0, w0), (h1, w1))):
        return f"너무 김: 하이픈 줄 {P.max_units:g}자 초과"
    parts = [p for c in theirs + mine for p in c.dialogue()]
    start = min(a.start, b.start)
    if max(p["e"] for p in parts) - start > P.max_f:
        return f"{P.max_dur:g}초 초과"
    if not one:
        return "작가가 정한 끊을 자리로 나뉜 줄"
    if abs(cells.index(b) - cells.index(a)) != 1:
        return "사이에 다른 줄의 칸"
    if same:
        body = pa["body"] + " " + pb["body"]
        merged = dict(pa, body=body, text=pa["prefix"] + body, words=pa["words"] + pb["words"],
                      t=min(pa["t"], pb["t"]), e=max(pa["e"], pb["e"]), whole=True, keep=True, breaks=False,
                      ids=_ids(pa) + _ids(pb), guess=bool(pa.get("guess") and pb.get("guess")),
                      # placed as a heard line when one of the two was heard, but the guessed time
                      # of the other still marks the cell ("timing_estimated", ``_out``)
                      some_guess=any(p.get("guess") or p.get("some_guess") for p in (pa, pb)),
                      timing="asr" if "asr" in (pa.get("timing"), pb.get("timing")) else pa.get("timing"),
                      pos="top" if "top" in (pa.get("pos"), pb.get("pos")) else pa.get("pos"),
                      notes=list(dict.fromkeys(pa["notes"] + pb["notes"])))
        a.set([merged], "plain", P)
    else:
        a.set([pa, pb], "hyphen", P)
    a.start = start
    a.marks |= b.marks
    cells.remove(b)
    return None


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


def _line_of(c: _Cell):
    """The line id when ``c`` is one piece of one line alone (plain, no sound), else None."""
    d = c.dialogue()
    return d[0]["id"] if c.mode == "plain" and len(c.parts) == 1 and len(d) == 1 else None


def _clash(a: _Cell, b: _Cell, P: _P) -> bool:
    """``b`` starts while ``a`` is still being spoken, too early for ``a`` to be read before it.
    Two pieces of one line never clash: one person does not talk over himself (a piece that is
    too short where it stands is laid out again with the others of its line, ``_make_room``)."""
    la = _line_of(a)
    if la is not None and la == _line_of(b):
        return False
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
    reading time, or else for 5/6 s; a side with room for 5/6 s comes before the next side, so
    the line stays on its side of the script when it can (4th review). When that place is where
    it already is (it can stay with 5/6 s, ending before the other starts), it does not move
    and ③ shortens it. Returns whether it moved."""
    a, b = cells[i], cells[i + 1]
    e, r = (a, b) if _guessed(a) else (b, a)
    prev = cells[i - 1] if i > 0 else None
    nxt = cells[i + 2] if i + 2 < len(cells) else None
    lo = max(w0, _keep_end(prev, P) + P.gap) if prev is not None else w0
    hi = min(nxt.start - P.gap if nxt is not None else INF, w1 if w1 is not None else INF)
    first = e.parts[0]["order"] < r.parts[0]["order"]
    needs = list(dict.fromkeys((_need(e, P), P.min_f)))
    for side in (["before", "after"] if first else ["after", "before"]) + ["behind"]:
        for need in needs:
            if side == "before":
                s = min(e.start, r.start - P.gap - need)
                room = s >= lo
            else:
                s = max(e.start, _keep_end(r, P) + P.gap if side == "after" else r.start + _need(r, P) + P.gap)
                room = s + need <= hi
            if not room:
                continue
            if s == e.start:
                return False
            _shift(e, s - e.start)
            _sorted(cells)
            return True
    return False


def _untangle(cells: list[_Cell], P: _P, w0: int, w1) -> list[_Cell]:
    """Lines that start while the line before is still spoken (module doc): ① one hyphen cell for
    two speakers, ② the line with a guessed time gives way, ③ the later line waits until the
    earlier one has had 5/6 s and both are marked "overlap". Never a one-frame cell in silence.
    The heard lines are untangled among themselves first and the guessed ones are fitted in
    after, so that no heard line ever waits for, or is pushed back by, a line whose time is only
    a guess (4th review: a heard line shown 1.25 s late behind a guessed one)."""
    heard = [c for c in cells if not _guessed(c)]
    if heard and len(heard) < len(cells):
        heard = _untangle_pass(heard, P, w0, w1)
        cells = heard + [c for c in cells if _guessed(c)]
        _sorted(cells)
    return _untangle_pass(cells, P, w0, w1)


def _untangle_pass(cells: list[_Cell], P: _P, w0: int, w1) -> list[_Cell]:
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
        ga, gb = _guessed(a), _guessed(b)
        if ga != gb and _give_way(cells, i, P, w0, w1):   # ②
            i = max(i - 1, 0)
            continue
        nxt = cells[i + 2] if i + 2 < len(cells) else None
        wait = a.start + P.min_f + P.gap
        end = w1 if w1 is not None else INF
        if ga != gb:
            # ③ with one guessed line that ② found no other place for: the heard one keeps its
            # in-time. A guessed later line waits behind the 5/6 s of the heard one, but never
            # past the next cell; a guessed earlier line ends before the heard one. Left under
            # 5/6 s, it is laid out again in the gap of its line or gets no cell (_make_room).
            if gb and b.start < wait:
                lim = min(nxt.start - P.gap - 1 if nxt is not None else INF, end - 1)
                _shift(b, max(b.start, min(wait, lim)) - b.start)
            (a if ga else b).marks.add("overlap")
            i += 1
            continue
        # ③ (two heard lines, or two guessed ones)
        room = wait + P.min_f <= min(nxt.start - P.gap if nxt is not None else INF, end)
        # no room, but the line after is spoken over this one too and will wait in its turn:
        # a pile of lines is shown one after the other while this one is still being spoken
        queue = (nxt is not None and nxt.dialogue()[0]["t"] < b.speech_end() and wait < b.speech_end()
                 and wait + P.min_f <= end)
        resorted = False
        if b.start < wait and (room or queue):
            b.start = wait
            for j in range(i + 2, len(cells)):           # the cells behind keep their order
                if cells[j].start >= cells[j - 1].start + P.gap + 1:
                    break
                if gb and not _guessed(cells[j]):        # a guessed line never pushes a heard one:
                    _sorted(cells)                       # it goes behind it (and is fitted in there)
                    resorted = True
                    break
                cells[j].start = cells[j - 1].start + P.gap + 1
        a.marks.add("overlap")
        b.marks.add("overlap")
        i = max(i - 1, 0) if resorted else i + 1
    return cells


def _dialogue_outs(cells: list[_Cell], P: _P, w1, caps=None) -> None:
    """Out-times: last word + 0.5 s, raised to 5/6 s and 14 units/s, at most 7 s and 2 frames
    before the next cell (TG1, TG3, TG5) and never over a scene change (``caps``)."""
    for i, c in enumerate(cells):
        out = max(c.speech_end() + P.tail, c.start + P.min_f, c.start + P.need(c.units))
        out = min(out, c.start + P.max_f, (caps or {}).get(id(c), INF))
        if i + 1 < len(cells):
            out = min(out, cells[i + 1].start - P.gap)
        if w1 is not None:
            out = min(out, w1)
        c.end = max(out, c.start + 1)


def _scene_of(c: _Cell):
    scenes = {p.get("scene") for p in c.dialogue()}
    return scenes.pop() if len(scenes) == 1 else None


def _scene_ends(cells: list[_Cell], cuts: list[int]):
    """(last cell of a scene, the scene change): the first shot change after both its speech and
    its in-time that comes before the next scene's first cell. Only lines with a ``scene_id``. A
    cut the speech goes over is no scene change for it (TG 4), nor is one the cell starts on or
    after (it waited there for the cell before)."""
    import bisect
    dl = sorted((c for c in cells if c.dialogue()), key=lambda c: (c.start, c.seq))
    for a, b in zip(dl, dl[1:]):
        sa, sb = _scene_of(a), _scene_of(b)
        if not sa or not sb or sa == sb:
            continue
        k = bisect.bisect_right(cuts, max(a.speech_end(), a.start))
        if k < len(cuts) and cuts[k] <= b.start:
            yield a, cuts[k]


def _scene_caps(cells: list[_Cell], cuts: list[int], P: _P) -> dict:
    """The latest out-time of the last cell of a scene (module doc "Scenes"): 2 frames before the
    scene change (``_scene_ends``), keyed by ``id(cell)``. No cap when that would leave the cell
    under 5/6 s or over the reading speed (it then goes over the change, and says so:
    ``_scene_notes``)."""
    return {id(a): x - P.gap for a, x in _scene_ends(cells, cuts) if x - P.gap >= a.start + _need(a, P)}


def _scene_notes(cells: list[_Cell], cuts: list[int], P: _P) -> dict:
    """The last cells of a scene that go over the scene change because ending before it would leave
    them under 5/6 s or over the reading speed: {id(cell): [note]} for ``check.notes``."""
    out = {}
    for a, x in _scene_ends(cells, cuts):
        if a.end <= x:
            continue
        if x - P.gap < a.start + P.min_f:
            out[id(a)] = [f"장면이 바뀌는 화면 전환을 넘음 · 그 앞에서 끝내면 {P.min_text}초가 안 됨"]
        elif x - P.gap < a.start + _need(a, P):
            out[id(a)] = [f"장면이 바뀌는 화면 전환을 넘음 · 그 앞에서 끝내면 초당 {P.cps:g}자를 넘음"]
    return out


def _guess_only(c: _Cell) -> bool:
    """One piece of one line whose time is a guess (not matched by the ASR)."""
    d = c.dialogue()
    return (c.mode == "plain" and len(c.parts) == 1 and len(d) == 1 and bool(d[0].get("guess"))
            and d[0].get("timing") != "asr")


def _lay_out(run: list[_Cell], lo: int, hi, P: _P):
    """In-times for the pieces ``run`` of one line inside lo..hi, in order, each as near its time as
    it can be: every piece gets its reading time when the gap holds that, else at least 5/6 s; with
    2 frames between them. A piece is moved earlier than its time when the pieces after it need the
    room (a heard line: before its own words, module doc). (starts, lengths) or None when the gap
    is too short."""
    for need in (lambda x: max(P.min_f, P.need(x.units)), lambda x: P.min_f):
        r = [need(x) for x in run]
        rest = sum(r) + (len(run) - 1) * P.gap
        if hi - lo < rest:
            continue
        starts, t = [], lo
        for x, rx in zip(run, r):
            s = min(max(x.start, t), hi - rest)        # leaves room for this piece and the ones after it
            starts.append(s)
            rest -= rx + P.gap
            t = s + rx + P.gap
        return starts, r
    return None


def _wait_behind(cells: list[_Cell], j: int, c: _Cell, P: _P, w1, cuts=()) -> bool:
    """A heard cell ``c`` of one piece left under 5/6 s because the line after it (``cells[j]``, one
    piece of one guessed line, not spoken over ``c``: that one ``_untangle`` ③ makes wait) starts
    too soon: the guessed line moves (``_shift``) to where ``c`` has had its reading time, else 5/6 s,
    when it then still gets its own (5/6 s and the reading speed) before the cell after it, the
    window end and a scene change (TG 4) — a guessed line gives way to a heard one (module doc).
    The ends are set for now; the caller sets them again."""
    x = cells[j]
    if not _guess_only(x):
        return False
    after = cells[j + 1] if j + 1 < len(cells) else None
    lim = min(after.start - P.gap if after is not None else INF, w1 if w1 is not None else INF,
              min((cut - P.gap for a, cut in _scene_ends(cells, list(cuts)) if a is x), default=INF))
    for need in dict.fromkeys((_need(c, P), P.min_f)):
        s = c.start + need + P.gap
        if s <= x.start:
            return False                             # it leaves that room already
        if s + _need(x, P) <= lim:
            _shift(x, s - x.start)
            c.end, x.end = s - P.gap, s + _need(x, P)
            return True
    return False


def _make_room(cells: list[_Cell], P: _P, w0: int, w1, cuts=()) -> tuple[list[dict], bool]:
    """A line with a piece left under 5/6 s (module doc): the pieces of the line that sit together
    between two cells of other lines are laid out again inside that gap (``_lay_out``; the cell
    before may shrink to its floor). A heard line (timing asr) is laid out from the in-time of its
    first piece on, never earlier, and its words keep their times; a later piece may come after
    its words or, when the pieces after it need the room, before them (a cell of a few frames
    cannot be read at all, 4th review; 5/6 s before TG 1, module doc). A heard line of one piece
    that the guessed line after it leaves under 5/6 s has that line wait for it (``_wait_behind``,
    ``cuts``: the shot changes, for the scene changes). When the
    gap cannot hold the pieces, a guessed line gets no cell at all (``no_room`` entries, with the
    gap between the other lines); a heard line keeps its cells, and when the writer's cut points
    made them that short, the short cells say so in ``check.notes``. Returns (no_room entries,
    whether a cell moved); ends are set again by the caller."""
    dropped, moved = [], False
    _sorted(cells)
    kept: set = set()                                # pieces (seq) of heard lines that cannot be helped
    i = 0
    while i < len(cells):
        c = cells[i]
        lid = _line_of(c)
        if c.end - c.start >= P.min_f or lid is None or c.seq in kept:
            i += 1
            continue

        def of_line(x, lid=lid):
            return any(p["kind"] == "dialogue" and p["id"] == lid for p in x.parts)
        mine = [x for x in cells if of_line(x)]
        if not all(_line_of(x) == lid for x in mine):    # a piece shares a cell with another line: leave it
            i += 1
            continue
        guess = all(_guess_only(x) for x in mine)
        a, b = i, i                                  # the pieces of this line next to this one
        while a > 0 and of_line(cells[a - 1]):
            a -= 1
        while b + 1 < len(cells) and of_line(cells[b + 1]):
            b += 1
        run = cells[a:b + 1]
        prev = cells[a - 1] if a > 0 else None
        nxt = cells[b + 1] if b + 1 < len(cells) else None
        lo = max(w0, _floor(prev, P) + P.gap) if prev is not None else w0
        if not guess:
            lo = max(lo, run[0].start)
        hi = min(nxt.start - P.gap if nxt is not None else INF, w1 if w1 is not None else INF)
        plan = _lay_out(run, lo, hi, P) if guess or len(run) > 1 else None
        if plan is not None:
            starts, r = plan
            for x, s in zip(run, starts):
                if guess:
                    _shift(x, s - x.start)
                else:
                    x.start = s
            for j, x in enumerate(run):              # for now; the caller sets the ends again
                x.end = run[j + 1].start - P.gap if j + 1 < len(run) else x.start + r[j]
            if prev is not None and prev.end > run[0].start - P.gap:
                prev.end = run[0].start - P.gap
            moved = True
            i = b + 1
            continue
        k = len(run)
        gap = max(0, hi - lo) / P.fps
        want = (k * P.min_f + (k - 1) * P.gap) / P.fps
        if not guess and k == 1 and nxt is not None and _wait_behind(cells, b + 1, c, P, w1, cuts):
            moved = True                             # the guessed line after it waits (G3-05)
            i = b + 1
            continue
        if not guess:
            kept.update(x.seq for x in run)          # this group only: a later group of the line is seen
            if c.parts[0].get("breaks"):             # on its own (after a cell of another line)
                note = (f"작가가 정한 끊을 자리로 나눈 칸이 {P.min_text}초가 안 됨 · 이 줄의 칸 {k}개에 "
                        f"빈틈 {gap:.2f}초 (칸마다 {P.min_text}초면 {want:.2f}초 필요)")
                for x in run:
                    if x.end - x.start < P.min_f and note not in x.parts[0]["notes"]:
                        x.parts[0]["notes"].append(note)
            i += 1
            continue
        first = min(x.parts[0]["t"] for x in mine)
        for rid in dict.fromkeys(q for x in mine for q in _ids(x.parts[0])):   # a writer's join: each line
            dropped.append({"item": rid, "line": rid, "t": P.sec(first), "kind": "dialogue",
                            "text": " ".join(x.parts[0]["text"] for x in sorted(mine, key=lambda x: x.start)),
                            "reason": (f"시각 추정 대사 · 앞뒤 칸 사이 빈틈 {gap:.2f}초 ({P.min_text}초 미만)" if k == 1 else
                                       f"시각 추정 대사 · 칸 {k}개 · 앞뒤 칸 사이 빈틈 {gap:.2f}초 "
                                       f"(칸마다 {P.min_text}초면 {want:.2f}초 필요)"),
                            # for the hint of place (``_line_hint``): the cells around and the frames its
                            # pieces want with their reading time (II.3: a hint that makes room makes it
                            # for a cell within the limits)
                            "_around": (prev, nxt, sum(_need(x, P) for x in run) + (k - 1) * P.gap)})
        for x in mine:
            cells.remove(x)
        i = max(0, a - 1)
    return dropped, moved


def _in_window(parts: list[dict], w0: int, w1) -> list[dict]:
    parts = [p for p in parts if p["e"] > w0 or p["t"] >= w0]
    return [p for p in parts if p["t"] < w1] if w1 is not None else parts


def _joined(parts: list[dict], lines: list) -> set:
    """The script places (``order``) of the lines a writer's join puts in one cell: a line with
    ``join`` and the line placed just before it (as ``_join`` finds them)."""
    seen = sorted({(p["order"], p["id"]) for p in parts})
    out = set()
    for (k0, _), (k, _) in zip(seen, seen[1:]):
        if isinstance(lines[k], dict) and lines[k].get("join"):
            out.update((k0, k))
    return out


def _in_order(parts: list) -> None:
    """Sort ``parts`` in time order, script order for equal times (a shooting script). The rows of one
    cell of a subtitle script (``_turn_cell``) keep the order the file writes them in whatever their
    times: the file has them in the order they are spoken (I.6 "- " a speaker a line), and the time of
    a row that was not heard is only its words shared out from the cell's start, before the heard row
    above it. Those rows take the places the group has in time order, in script order (a line's
    pieces in time order), so no other line moves."""
    parts.sort(key=lambda p: (p["t"], p["order"]))
    rows: dict = {}
    for i, p in enumerate(parts):
        cell = _turn_cell(p)
        if cell is not None:
            rows.setdefault(cell, []).append(i)
    for at in rows.values():
        if len(at) > 1:
            for i, p in zip(at, sorted((parts[i] for i in at), key=lambda p: (p["order"], p["t"]))):
                parts[i] = p


def _dialogue_cells(lines, cuts: list[int], P: _P, w0: int, w1, seq) -> tuple[list[_Cell], list[dict]]:
    parts = []
    starts = []                                          # where each line begins (_split: room of a last piece)
    spans = []                                           # (first word, last word, place) of each line
    for k, ln in enumerate(lines or []):
        if isinstance(ln, dict) and ln.get("status") not in NO_TIME and str(ln.get("text") or "").split():
            times = _word_times(ln, str(ln.get("text")).split())
            if times:
                starts.append(P.fr(times[0][0]))
                spans.append((P.fr(times[0][0]), P.fr(max(e for _, e in times)), k))
    starts.sort()

    def all_parts():
        out = []
        for k, ln in enumerate(lines or []):
            if isinstance(ln, dict):
                out.extend(_dialogue_parts(ln, P, k, starts, spans=spans))
        return _in_window(out, w0, w1)
    parts = all_parts()
    # a writer's join holds both lines whole: no cut at a pause in either (the writer's choice first);
    # a line none of whose joins is made keeps its cuts at the pauses: laid out again without the
    # hold, the joins refused only say why again (``refused``), as the first time
    held = {p["order"] for p in parts if p["paused"]} & _joined(parts, list(lines or []))
    refused: dict = {}
    while True:
        if held:
            parts = [p for p in parts if p["order"] not in held]
            for k in sorted(held):
                parts.extend(_in_window(_dialogue_parts(lines[k], P, k, starts, pauses=False), w0, w1))
        _in_order(parts)                                 # script order for equal times; rows of a subtitle cell
        cells: list[_Cell] = []
        prev = None
        for p in parts:
            s = _snap_in(max(p["t"], w0), p["e"], prev, cuts, P, w0)
            if prev is not None and s < prev.start + P.gap + 1:
                s = prev.start + P.gap + 1               # lines that start together: keep the order
            c = _Cell(s, s + 1, [p], "plain", P, next(seq))
            cells.append(c)
            prev = c
        made = _join(cells, list(lines or []), P, refused)     # the writer's "one cell with the line before"
        if not held - made:
            break
        held &= made
        parts = all_parts()                              # fresh parts: _join wrote notes into these
    cells = _untangle(cells, P, w0, w1)                  # overlapping lines: never a one-frame cell
    cells = _pair_short(cells, P, w1)
    _dialogue_outs(cells, P, w1, _scene_caps(cells, cuts, P))
    dropped, moved = _make_room(cells, P, w0, w1, cuts)  # a guessed line squeezed between two others
    if moved or dropped or any(c.end - c.start < P.min_f for c in cells):
        _sorted(cells)
        _dialogue_outs(cells, P, w1, _scene_caps(cells, cuts, P))
    return cells, dropped


# --------------------------------------------------------------------------- sounds

# ④ a sound that comes while a line is spoken (II.9 "Sound effects which interrupt dialogue should be
# treated as follows, ideally following the order of the original audio" = 대사를 끊는 효과음은 될 수
# 있으면 원래 소리의 순서대로: "- 내가 어제 봤는데…" / "- [첨벙!]", then "그런 상황은 아니었어") shares the
# cell of the piece before it (``_interrupt``). That cell shows from the first word of the piece, so the
# words of the sound are on screen before it is heard, by as long as the piece is spoken: at most this
# many seconds, else the sound goes with the piece after it, which starts at the sound, as before the 6th
# round. Not measured (the guide's example piece has 8 syllables, about 1.5 s of speech)
INTERRUPT_LEAD = 2.0
# ... and only when the sound comes before the first word of the piece after it (the audio's order is
# piece, sound, piece) early enough that the words of the sound are still on screen this many seconds
# after it is heard, before that piece starts on its first word (TG "Subtitles should be in sync with both
# the image and the audio" = 자막은 화면과 소리 둘 다에 맞아야 한다; TG 1 "an in-time which is on the first
# frame of audio ... (within 1-2 frames of the first frame of audio is acceptable)" = 들어오는 시각은 소리의
# 첫 프레임, 1-2프레임 안은 허용: so the later piece is not held back for it). Else the cell would end on the
# frame the sound is heard (verify f1c: 121 such cells in 1000 made-up scenes) and the sound goes with the
# later piece, which starts at the sound, as before the 6th round. The same quarter second as
# ``flash_dur`` (style: a cell shorter than this cannot be read at all); not measured
INTERRUPT_SHOW = 0.25
# "…" ends that piece (I.4 "Use ellipses to indicate a pause (2 seconds or more), hesitation or an abrupt
# interruption" = 갑작스러운 끊김에는 말줄임표) only when the words on both sides of the sound are this many
# seconds apart (the speech stops for the sound) and the piece does not end in punctuation already; never
# on every cut piece (audit note §8 "끊긴 조각에 항상 '…'를 붙이지 않는다. 말이 실제로 끊겼을 때만").
# Not measured
INTERRUPT_PAUSE = 0.5
# a sound heard this many seconds or longer (music, rain, an engine) that finds no cell at its onset gets
# one in the next gap that opens while it is still heard (``_late``, audit 2-05 "음악처럼 길게 이어지는 소리는
# 소리가 끝나기 전의 다음 빈틈까지"): its first 5/6 s inside the sound, at most ``LONG_LATE`` after the onset,
# and its ``check.notes`` say how late ("음악 시작 1.29초 뒤"). A shorter sound keeps the half second of ⑤.
# Not measured
LONG_SOUND = 2.0
# the length a music cell is shown (memo 5.2: 3 s): a late cell starts within the time a cell at the
# onset would have stood. Not measured
LONG_LATE = 3.0


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
            "src": s.get("source_line"), "forced": move is not None, "at": at, "pos": s.get("pos"),
            "guess": bool(s.get("estimated")) and move is None}


def _sound_len(sp: dict, text: str, start: int, P: _P) -> int:
    """Frames a sound line is shown (memo 5.2): sfx units / 14 + 1 s; music 3 s or music end + 0.5 s."""
    if sp["kind"] == "music":
        end = start + P.music_f
        if sp["e"] is not None:
            end = min(end, sp["e"] + P.tail)
        return min(max(end - start, P.min_f), P.max_f)
    d = int(round((units(text) / P.cps + P.label_extra) * P.rate))
    return min(max(d, P.min_f), P.max_f)


def _limit_after(x, start: int, cells: list[_Cell], P: _P, w1) -> int:
    """The latest out-time for a cell starting at ``start``: 2 frames before the next cell (other
    than ``x``), the window end."""
    nxt = [c.start for c in cells if c is not x and c.start >= start and (x is None or c.start > x.start)]
    lim = min(nxt) - P.gap if nxt else INF
    return min(lim, w1) if w1 is not None else lim


def _inside(x: _Cell, cells: list[_Cell], P: _P, w1) -> int:
    """The latest out-time of a cell cut from ``x``: 2 frames before the cell after ``x`` (the words
    of ``x`` may go on under that cell, overlapping speech), the window end."""
    return _limit_after(x, x.start + 1, cells, P, w1)


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
    if dialogue and sp["src"] is not None and sp["src"] in _ids(other):
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


def _split_point(x: _Cell, t: int, P: _P):
    """④ where the dialogue cell ``x`` is cut for the sound at ``t``: the word boundary nearest the
    sound, at most 0.5 s from it: from the first word after it, or none when the sound comes in the
    pause between the two words (a sound that stops the speech, II.9, however long the pause).
    Returns (k, part before, part after) or (None, reason)."""
    if x.mode != "plain" or x.sounds() or not x.dialogue():
        return None, "대사 칸 중간 (나눌 수 없는 칸)"
    d = x.parts[0]
    words = d["words"]
    if d.get("keep"):
        return None, "작가가 한 칸에 두기로 정한 대사"
    if len(words) < 2:
        return None, "대사 중간, 나눌 어절 경계 없음"

    def far(q):
        return 0 if words[q - 1][2] <= t <= words[q][1] else abs(words[q][1] - t)
    k = min(range(1, len(words)), key=lambda q: (far(q), q))
    if far(k) > P.win:
        return None, "소리 가까이 어절 경계 없음"
    if not all(style.tidy_line(" ".join(w[0] for w in side)) for side in (words[:k], words[k:])):
        return None, "소리 가까이 어절 경계 없음"          # one side would be punctuation only
    a = _piece(d, words[:k], d["prefix"])
    b = dict(_piece(d, words[k:], d["cont"]), e=d["e"])  # the later part: the language label again, no name
    return k, a, b


def _plan_interrupt(x: _Cell, t: int, sp: dict, text: str, ext: bool, cells: list[_Cell], P: _P, w1):
    """④ the sound joins the part before it, in the order of the audio (II.9, ``_interrupt``); not a
    sound the speaker made (II.5 "[지수] [재채기] 나도야!", no hyphen: ``_plan_split``). Tried before
    ``_plan_split`` at every level of ⑤; gives no reason of its own (the reason is that of the old
    order)."""
    cut = _split_point(x, t, P)
    if cut[0] is None or (sp["src"] is not None and sp["src"] in _ids(x.parts[0])):
        return None, None
    return _interrupt(x, t, sp, text, ext, cells, P, w1, *cut), None


def _plan_split(x: _Cell, t: int, sp: dict, text: str, ext: bool, cells: list[_Cell], P: _P, w1):
    """④ split the dialogue cell ``x`` at the word boundary nearest the sound (``_split_point``); the
    sound joins the later part, which starts at the sound: the order before the 6th round, used when
    the guide's order (``_plan_interrupt``) cannot be read, and for a sound the speaker made."""
    cut = _split_point(x, t, P)
    if cut[0] is None:
        return cut
    k, a, b = cut
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


def _punct_end(word: str) -> bool:
    """Whether ``word`` ends in punctuation ('봤는데,' '정말?' '그…' '말했잖아)')."""
    return bool(word) and unicodedata.category(word[-1]).startswith("P")


def _interrupt(x: _Cell, t: int, sp: dict, text: str, ext: bool, cells: list[_Cell], P: _P, w1, k: int,
               a: dict, b: dict):
    """④ in the guide's order (II.9 "Sound effects which interrupt dialogue … ideally following the
    order of the original audio"): the piece ``a`` before the sound and the sound in one hyphen cell
    from the in-time of ``x``, "- 내가 어제 봤는데…" / "- [첨벙!]", and the piece ``b`` after it in
    the next cell from its first word. Only when the sound comes before that word, the words of the
    sound show at most ``INTERRUPT_LEAD`` before it and stay ``INTERRUPT_SHOW`` after it (on screen
    when it is heard; the first cell ends 2 frames before ``b``), each hyphen line fits one line
    (II.5, I.6) and both cells get 5/6 s and their reading time (General Requirements 1, II.3). "…"
    ends ``a`` only where the speech stops for the sound (``INTERRUPT_PAUSE``) and ``a`` has no
    punctuation of its own: the words are never changed. Returns apply, or None (the caller tries the
    order before the 6th round)."""
    words = x.parts[0]["words"]
    if t - x.start > P.lead or t >= words[k][1]:
        return None                                     # the sound comes after the later piece starts
    if words[k][1] - words[k - 1][2] >= P.pause_f and not _punct_end(words[k - 1][0]):
        toks = [w[0] for w in words[:k]]
        toks[-1] += style.ELLIPSIS                       # the speech stops: an abrupt interruption (I.4)
        body = _body(toks, a["sung"])
        a = dict(a, body=body, text=a["prefix"] + body)
    parts = [a, dict(sp, text=text)]
    lines = _render(parts, "hyphen", P)
    if _check_merged(lines, "hyphen", P):
        return None
    s2 = max(words[k][1], t + P.gap)
    u1 = sum(units(y) for y in lines)
    if ext:
        # ⑤-2: the later piece waits for the 5/6 s and the reading time of the cell before, at most half
        # a second after its first word (as the later piece of the old order starts at the sound, at
        # most half a second away): 5/6 s, a must, before the in-time on its audio (TG 1, a should)
        s2 = max(s2, min(x.start + max(P.min_f, P.need(u1)) + P.gap, words[k][1] + P.win))
    end1 = s2 - P.gap
    if end1 - t < P.show_f:
        return None                     # the words of the sound would be gone when it is heard
    if end1 - x.start < P.min_f or P.too_fast(u1, end1 - x.start):
        return None
    b_lines = _render([b], "plain", P)
    if not P.lines_ok(b_lines):
        return None
    limit = _inside(x, cells, P, w1)
    ub = sum(units(y) for y in b_lines)
    end2 = min(x.end, limit, s2 + P.max_f)
    if ext:
        end2 = min(max(end2, s2 + P.need(ub), s2 + P.min_f), limit, s2 + P.max_f)
    if end2 - s2 < P.min_f or P.too_fast(ub, end2 - s2):
        return None

    def apply(seq):
        x.end = end1
        x.set(parts, "hyphen", P)
        cells.append(_Cell(s2, end2, [b], "plain", P, next(seq)))
    return apply


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


def _try_at(sp: dict, t: int, texts: list, cells: list[_Cell], P: _P, w1, seq, split: bool = True):
    """Positions ①-④ and ⑤ for the sound at frame ``t``; ``split`` False: no ④ (a cell just cut in
    two by ``_resplit`` is not cut again). Returns (placed, the first reason a position gave)."""
    levels = [(x, False) for x in texts] + [(x, True) for x in texts]     # ⑤-1 short, ⑤-2 longer end
    starts = sorted((c for c in cells if abs(c.start - t) <= P.win),
                    key=lambda c: (abs(c.start - t), c.start, c.seq))
    if starts:
        targets = [(_plan_merge, c) for c in starts]
    else:
        mid = [c for c in cells if c.start + P.win < t < c.end + P.gap
               and c.speech_end() is not None and c.speech_end() > t]
        targets = ([(fn, c) for c in mid for fn in (_plan_interrupt, _plan_split)] if mid and split
                   else [(_plan_own, None)])
    why = None
    for fn, x in targets:
        for text, ext in levels:
            apply, reason = fn(x, t, sp, text, ext, cells, P, w1)
            if apply is not None:
                apply(seq)
                return True, None
            why = why or reason
    near = (_floor(c, P) + P.gap for c in cells if c.start <= t + P.win and c.end + P.gap > t)
    floors = sorted({f for f in near if t < f <= t + P.win})
    for t2 in floors:                                                   # ⑤-3 a gap just after the sound
        for text in texts:
            apply, _ = _plan_own(None, t2, sp, text, True, cells, P, w1)
            if apply is not None:
                apply(seq)
                return True, None
    return False, why


def _two_line_cells(t: int, cells: list[_Cell], P: _P) -> list[_Cell]:
    """The dialogue cells of two lines that ``_resplit`` may cut for the sound at ``t``: one piece
    of one line (one line of the script broken over two lines), no sound in it, not kept in one cell
    by the writer (``keep``, also a writer's join of one speaker), on screen at the sound or starting
    within half a second after it. Nearest first."""
    out = [c for c in cells if c.mode == "plain" and len(c.parts) == 1 and c.dialogue() and len(c.lines) == 2
           and not c.parts[0].get("keep") and c.start - P.win <= t < c.end + P.gap]
    return sorted(out, key=lambda c: (abs(c.start - t), c.start, c.seq))


def _resplit(x: _Cell, sp: dict, t: int, texts: list, cells: list[_Cell], P: _P, w1, seq) -> bool:
    """⑤ a sound that finds no room at a dialogue cell of two lines (``_two_line_cells``; audit 2-05,
    4-m2: TG 6 "You may need to re-segment and re-time subtitles and their neighbouring subtitles" =
    이웃 자막과 함께 다시 나누고 시간을 다시 맞춰야 할 수 있다): ``x`` is cut at a word boundary into two
    cells of one line each, the first from the in-time of ``x`` to 2 frames before the first word of
    the second, the second to the out-time of ``x`` (longer when its reading time needs it, where
    there is room), each with 5/6 s and its reading time (General Requirements 1, II.3); then the
    sound shares one of the two (②, ③) or gets its own cell (①, ⑤), never a third cut. The ways of
    cutting are tried cheapest first (as ``_split`` prices them: balanced, after sentence punctuation,
    not between words that stay together). Returns whether the sound got a cell; else ``x`` is as
    it was."""
    d = x.parts[0]
    words = d["words"]
    toks = [w[0] for w in words]
    keep = _cut_costs(toks, P)
    lim = _inside(x, cells, P, w1)
    ways = []
    for k in range(1, len(words)):
        if not (style.tidy_line(" ".join(toks[:k])) and style.tidy_line(" ".join(toks[k:]))):
            continue
        a = _piece(d, words[:k], d["prefix"])
        b = dict(_piece(d, words[k:], d["cont"]), e=d["e"])
        la, lb = _render([a], "plain", P), _render([b], "plain", P)
        if len(la) != 1 or len(lb) != 1 or not (P.lines_ok(la) and P.lines_ok(lb)):
            continue
        ua, ub = units(la[0]), units(lb[0])
        s_b = words[k][1]
        need_a, need_b = max(P.min_f, P.need(ua)), max(P.min_f, P.need(ub))
        end_b = min(max(x.end, s_b + need_b), lim, s_b + P.max_f)
        if s_b - P.gap - x.start < need_a or end_b - s_b < need_b:
            continue
        cost = (ua / 4.0) ** 2 + (ub / 4.0) ** 2 - 2.0 * end_bonus(toks[k - 1]) + keep[k]
        ways.append((cost, k, a, b, s_b, end_b))
    for _, _, a, b, s_b, end_b in sorted(ways, key=lambda w: (w[0], w[1])):
        first = _Cell(x.start, s_b - P.gap, [a], "plain", P, x.seq)
        second = _Cell(s_b, end_b, [b], "plain", P, next(seq))
        first.marks, second.marks = set(x.marks), set(x.marks)
        i = cells.index(x)
        cells[i:i + 1] = [first, second]
        placed, _ = _try_at(sp, t, texts, cells, P, w1, seq, split=False)
        if placed:
            return True
        cells.remove(first)                          # nothing else was changed: the cell as it was
        cells.remove(second)
        cells.insert(i, x)
    return False


def _late(sp: dict, t: int, texts: list, cells: list[_Cell], P: _P, w1, seq) -> bool:
    """⑤ a long sound (``LONG_SOUND``) with no cell at its onset: an own cell in the first gap that
    opens after it while the sound is still heard for 5/6 s, at most ``LONG_LATE`` after the onset;
    its ``check.notes`` say how late ("음악 시작 1.29초 뒤"). Not for a sound whose onset is a guess
    (when it ends is not known either) nor one the writer moved (the writer chose that time)."""
    e = sp.get("e")
    if e is None or sp["forced"] or sp["guess"] or e - sp["t"] < P.long_f:
        return False
    last = min(sp["t"] + P.late_f, e - P.min_f)
    if w1 is not None:
        last = min(last, w1 - P.min_f)
    after = max(t, sp["t"])
    starts = sorted({_floor(c, P) + P.gap for c in cells if c.start < last})     # where a gap can open
    for t2 in (s for s in starts if after < s <= last):
        what = "음악" if sp["kind"] == "music" else "소리"
        late = dict(sp, notes=[f"{what} 시작 {(t2 - sp['t']) / P.fps:.2f}초 뒤"])
        for text in texts:
            apply, _ = _plan_own(None, t2, late, text, True, cells, P, w1)
            if apply is not None:
                apply(seq)
                return True
    return False


def _place_sound(sp: dict, cells: list[_Cell], cuts: list[int], P: _P, w0: int, w1, seq):
    """Put one sound in a cell (positions ①-⑤). Returns None, or the no_room reason. ``sp["tried"]``
    is the frame it was tried at (music: on the shot change before it, ``_music_start``)."""
    t = sp["t"]
    if t < w0 or (w1 is not None and t >= w1):
        return "처리 구간 밖"
    if not sp["long"]:
        return "문구 없음"
    _sorted(cells)
    if sp["kind"] == "music" and not sp["forced"]:
        t = _music_start(t, cells, cuts, P)
    sp["tried"] = t
    texts = [sp["long"]] + ([sp["short"]] if sp["short"] and sp["short"] != sp["long"] else [])
    placed, why = _try_at(sp, t, texts, cells, P, w1, seq)
    if placed:
        return None
    for x in _two_line_cells(t, cells, P):
        if _resplit(x, sp, t, texts, cells, P, w1, seq):
            return None
    if _late(sp, t, texts, cells, P, w1, seq):
        return None
    return _no_room_reason(why, t, cells, P, w1)


# --------------------------------------------------------------------------- what would make room

# A no_room entry carries the one change of one cell that would make room ("hint", audit 7-07 "자리 없음
# 항목마다 '이 칸을 이만큼 줄이면 들어감' 같은 구체적인 안을 계산해 작가에게 보인다"), worked out on the
# cells as they are written: end the cell before earlier ("end_earlier") or start the cell after later
# ("move_later"), and the reason ends in it (" · 앞 대사 칸을 0.42초 줄이면 들어감"). Only a change that
# keeps that cell's 5/6 s and reading time (General Requirements 1, II.3: musts; a dialogue cell may end
# before its speech, as cells of overlapping speech do) and moves an in-time by at most half a second
# (``merge_window``, as ② and ⑤ take a sound: further, the cell is out of sync with its audio, TG 1);
# none when one change is not enough.

def _end_earlier(c: _Cell, end: int, P: _P):
    """Frames ``c`` is shortened by to end at ``end``, or None when that breaks 5/6 s or the reading speed."""
    if end >= c.end or end - c.start < _need(c, P):
        return None
    return c.end - end


def _move_later(c: _Cell, by: int, cells: list[_Cell], P: _P, w1):
    """``by`` when the in-time of ``c`` can come that many frames later (at most half a second) with its
    5/6 s and reading time before the next cell, else None."""
    if by <= 0 or by > P.win:
        return None
    i = cells.index(c)
    lim = cells[i + 1].start - P.gap if i + 1 < len(cells) else INF
    if w1 is not None:
        lim = min(lim, w1)
    s = c.start + by
    end = max(c.end, s + _need(c, P))
    return by if end <= lim and end - s <= P.max_f else None


def _sound_hint(sp: dict, cells: list[_Cell], P: _P, w1):
    """(cell, how, frames) for a sound without room: its own cell at the frame it was tried at, as long
    as its shortest phrase needs (5/6 s and its reading time), when one cell is in the way."""
    t = sp["tried"]
    needs = [max(P.min_f, P.need(sum(units(y) for y in P.wrap(x)))) for x in (sp["short"], sp["long"])
             if x and P.lines_ok(P.wrap(x))]
    if not needs or (w1 is not None and t + min(needs) > w1):
        return None
    need = min(needs)
    blocking = [c for c in cells if c.start < t and c.end > t - P.gap and _floor(c, P) > t - P.gap]
    later = [c for c in cells if c.start >= t]
    room = later[0].start - P.gap - t if later else INF
    if w1 is not None:
        room = min(room, w1 - t)
    if len(blocking) == 1 and room >= need:
        by = _end_earlier(blocking[0], t - P.gap, P)
        return (blocking[0], "end_earlier", by) if by else None
    if not blocking and room < need and later:
        by = _move_later(later[0], need - room, cells, P, w1)
        return (later[0], "move_later", by) if by else None
    return None


def _line_hint(around, cells: list[_Cell], P: _P, w0: int, w1):
    """(cell, how, frames) for a guessed line without room (``_make_room``): the gap between the cells
    around it (``around`` = cell before, cell after, frames its pieces want) made that long."""
    prev, nxt, want = around
    if not all(c is None or any(c is x for x in cells) for c in (prev, nxt)):
        return None                                  # a cell around it was cut again (a sound)
    lo = _floor(prev, P) + P.gap if prev is not None else w0
    hi = nxt.start - P.gap if nxt is not None else (w1 if w1 is not None else INF)
    if hi - lo >= want:
        return None
    if prev is not None and hi < INF:
        by = _end_earlier(prev, hi - want - P.gap, P)
        if by:
            return prev, "end_earlier", by
    if nxt is not None:
        by = _move_later(nxt, lo + want - hi, cells, P, w1)
        if by:
            return nxt, "move_later", by
    return None


def _line_ref(c: _Cell, how: str):
    """The line id of the hint's cell: the line that ends last (end_earlier) or starts first
    (move_later); None for a cell of sounds only."""
    d = c.dialogue()
    if not d:
        return None
    if how == "end_earlier":
        return _ids(max(d, key=lambda p: (p["e"], p["t"])))[-1]
    return _ids(min(d, key=lambda p: (p["t"], p["e"])))[0]


def _hint_text(c: _Cell, how: str, by: int, P: _P) -> str:
    what = "대사 칸" if c.dialogue() else "소리 칸"
    if how == "end_earlier":
        return f"앞 {what}을 {by / P.fps:.2f}초 줄이면 들어감"
    return f"뒤 {what}을 {by / P.fps:.2f}초 늦추면 들어감"


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
    caps = _scene_caps(cells, cuts, P)
    for i, c in enumerate(cells):
        nxt = cells[i + 1].start if i + 1 < len(cells) else None
        limit = nxt - P.gap if nxt is not None else INF
        if w1 is not None:
            limit = min(limit, w1)
        limit = min(limit, caps.get(id(c), INF))       # never over a scene change (TG 4)
        cap = min(limit, c.start + P.max_f)
        if nxt is not None and P.gap < nxt - c.end < P.half_exact - 1e-9:
            if _rested(c, nxt, cuts, P):             # ① its half second after the audio: no longer (TG5)
                c.end = _half_before(nxt, P)
            elif nxt - P.gap <= cap:
                c.end = nxt - P.gap                  # ① close a gap under half a second (TG5)
        ahead = [x for x in cuts if c.end < x <= c.end + P.half]
        if ahead:                                    # ② out-time just before a cut (TG2)
            x = ahead[0]
            if c.end < x - P.gap <= cap and (nxt is None or nxt >= x):
                c.end = x - P.gap
        _cut_rules(c, cuts, cap, P)                  # ③ crossing a cut (TG1, TG4)
        c.end = max(min(c.end, limit), c.start + 1)
        if nxt is not None and P.gap < nxt - c.end < P.half_exact - 1e-9 \
                and not any(c.end <= x <= nxt for x in cuts):
            if _rested(c, nxt, cuts, P):             # ③ moved the end: as ①,
                c.end = _half_before(nxt, P)
            elif nxt - P.gap <= cap:                 # close the gap again,
                c.end = nxt - P.gap
            else:                                    # or, at 7 s, make it half a second (TG5)
                half_gap = _half_before(nxt, P)
                if half_gap >= _floor(c, P):
                    c.end = max(half_gap, c.start + 1)
    _start_on_cut(cells, cuts, P)


def _half_before(nxt: int, P: _P) -> int:
    """The out-time that leaves a gap of half a second before ``nxt`` (12 frames at 24 fps)."""
    return nxt - int(math.ceil(P.half_exact - 1e-9))


def _rested(c: _Cell, nxt: int, cuts: list[int], P: _P) -> bool:
    """① of the end adjustment for a gap under half a second before ``nxt``: whether ``c`` ends half a
    second before ``nxt`` instead of closing the gap. TG5 "If an out-time has been extended by half a
    second beyond the end of the audio, avoid extending this further to close gaps. Instead, consider
    either re-segmenting or merging subtitles, or ensuring that the gap is half a second or more"
    (끝 시각을 이미 소리 끝 뒤 0.5초까지 늘렸으면 빈틈을 닫으려고 더 늘리지 않는다. 대신 다시 나누거나
    합치거나, 빈틈이 0.5초 이상이 되게 한다; audit 2-08): a cell with dialogue that stays half a second
    after its audio (TG1) comes forward to half a second before the next cell. Not when that leaves it
    under 5/6 s or over the reading speed (GR 1, II.3, musts: first; the gap is then closed to 2
    frames as before), nor when a shot change lies within half a second before that out-time or in
    the gap: there the shot change rules decide (② ③; TG2 "These rules can be applied when the
    out-time has already been extended by half a second past the audio"), as before."""
    if not c.dialogue() or c.end - c.audio_end() < P.tail:
        return False
    half_gap = _half_before(nxt, P)
    return half_gap >= _floor(c, P) and not any(half_gap - P.half < x <= nxt for x in cuts)


def _start_on_cut(cells: list[_Cell], cuts: list[int], P: _P) -> None:
    """④ of the end adjustment: a cell that ends 2 frames before a shot change (or on it) and a
    dialogue cell that starts less than half a second after it. TG2 "Where dialogue starts on the
    shot change or within half a second past the shot change, please set the in-time to the first
    frame of the shot change" (전환 뒤 0.5초 안에 시작하는 대사는 전환의 첫 프레임에서 시작) and TG5
    "gaps between subtitles of 3-11 frames inclusive must be closed to 2 frames" (3~11프레임 빈틈은
    2프레임으로): the dialogue starts on the cut and the cell before ends 2 frames before it,
    when that keeps its 5/6 s and reading speed. In-time placement could not take the cut when
    the speech before ran up to it (the in-time never takes the speech of the cell before); the
    out-time is moved there all the same (③), so the gap is closed here (4th review). A cell of
    dialogue and a sound that the dialogue starts ("- 내가 어제 봤는데…" / "- [첨벙!]", ④ of the
    sounds) is a dialogue cell here too, while its sound still shows at most ``INTERRUPT_LEAD``
    before it is heard (7th round); a cell a sound starts keeps the sound's onset (BBC 5.5)."""
    for a, b in zip(cells, cells[1:]):
        if b.kind() != "dialogue" and not (b.mode == "hyphen" and b.parts[0]["kind"] == "dialogue"):
            continue
        xs = [x for x in cuts if a.start < x < b.start and b.start - x <= P.half]
        if not xs:
            continue
        x = xs[-1]
        if any(p["t"] - x > P.lead for p in b.sounds()):
            continue
        end = x - P.gap
        if not end <= a.end < x + P.gap or b.start - a.end <= P.gap:
            continue
        dur = end - a.start
        if dur < P.min_f or P.too_fast(a.units, dur) or b.end - x > P.max_f:
            continue
        a.end, b.start = end, x


# --------------------------------------------------------------------------- output

def _out(cells: list[_Cell], cuts: list[int], P: _P, more_notes=None) -> list[dict]:
    """The cells as draft.json writes them. ``more_notes``: {id(cell): [note]} besides the notes of
    the parts (``_scene_notes``)."""
    more_notes = more_notes or {}
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
        if note_left_alone(c.lines):                 # the checker's lyric_notes (II.7), so both name the cell
            viol.append("음표가 가사와 다른 줄")
        gap_f = None
        if nxt is not None:
            gap_f = nxt.start - c.end
            if gap_f < P.gap:
                viol.append(f"간격 {P.gap}프레임 미만")
            elif P.gap < gap_f < P.half_exact - 1e-9 and not any(c.end <= x <= nxt.start for x in cuts):
                viol.append("빈틈 0.5초 미만")
        refs = []
        for p in c.parts:
            for r in _ids(p):
                if r not in refs:
                    refs.append(r)
        flags = ["timing_estimated"] if any(p.get("guess") or p.get("some_guess") for p in c.parts) else []
        flags += sorted(c.marks)
        if dur_f < P.flash_f:
            flags.append("flash")                    # a cell of a few frames never goes out unmarked
        check = {"chars": chars, "n_lines": len(c.lines), "cps": round(cps, 2) if cps < INF else None,
                 "dur": round(dur_f / P.fps, 3), "gap_next": round(gap_f / P.fps, 3) if gap_f is not None else None,
                 "violations": viol}
        notes = list(dict.fromkeys([n for p in c.parts for n in p.get("notes") or ()] + more_notes.get(id(c), [])))
        if notes:                                    # what became of a writer's choice (breaks, keep), a scene change
            check["notes"] = notes
        out.append({"id": f"C{i + 1:04d}", "start": P.sec(c.start), "end": P.sec(c.end), "lines": list(c.lines),
                    "refs": refs, "kind": c.kind(),
                    "pos": "top" if any(p.get("pos") == "top" for p in c.parts) else "bottom", "flags": flags,
                    "check": check})
    return out


def place(lines, sounds, cuts, fps, window=(0.0, None), settings=None) -> dict:
    """Draft cells for dialogue ``lines`` and included ``sounds`` (module doc)."""
    P = _P(fps, settings)
    t0, t1 = (tuple(window) + (None, None))[:2] if window is not None else (None, None)
    w0 = P.fr(_num(t0) or 0.0)
    w1 = P.fr(_num(t1)) if _num(t1) is not None else None
    cut_f = sorted({P.fr(c) for c in (cuts or []) if _num(c) is not None})
    seq = itertools.count()
    cells, no_room = _dialogue_cells(lines, cut_f, P, w0, w1, seq)
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
    tried = []                                       # (no_room entry, sound) of a sound tried in the cells
    for sp in parts:
        reason = _place_sound(sp, cells, cut_f, P, w0, w1, seq)
        if reason is not None:
            no_room.append({"item": sp["id"], "t": round(sp["at"], 3), "text": sp["long"], "kind": sp["kind"],
                            "reason": reason})
            if sp.get("tried") is not None:
                tried.append((no_room[-1], sp))
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
    names = {id(c): f"C{i + 1:04d}" for i, c in enumerate(cells)}       # the ids _out gives
    sound_of = {id(x): s for x, s in tried}
    for e in no_room:                                # what one change of one cell would make room (audit 7-07)
        around = e.pop("_around", None)
        sp = sound_of.get(id(e))
        h = (_sound_hint(sp, cells, P, w1) if sp is not None else
             _line_hint(around, cells, P, w0, w1) if around is not None else None)
        if h is not None:
            c, how, by = h
            e["hint"] = {"cell": names[id(c)], "line": _line_ref(c, how), "by": round(by / P.fps, 3), "how": how}
            e["reason"] += " · " + _hint_text(c, how, by, P)
    out = _out(cells, cut_f, P, _scene_notes(cells, cut_f, P))
    n_viol = sum(1 for c in out if c["check"]["violations"])
    placed = sum(1 for c in cells for p in c.sounds())

    def flagged(name):
        return sum(1 for c in out if name in c["flags"])

    return {"cells": out, "no_room": no_room,
            "summary": {"cells": len(out), "violations": n_viol, "no_room": len(no_room),
                        "dialogue_cells": sum(1 for c in cells if c.dialogue()), "sounds_placed": placed,
                        "timing_estimated": flagged("timing_estimated"), "overlap": flagged("overlap"),
                        "flash": flagged("flash"),
                        "lines_no_room": sum(1 for e in no_room if e.get("kind") == "dialogue")},
            "fps": P.fps, "profile": P.profile, "notation": style.NOTATION}
