# -*- coding: utf-8 -*-
"""끼워 넣기 (cboe.placement): limits, timing, positions ①-⑤, no_room, the sample work folder."""
import json
import math
import re
import shutil
from pathlib import Path

import pytest

from cboe import check
from cboe.placement import place
from cboe.textutil import units

FPS = 23.976
ROOT = Path(__file__).resolve().parents[1]
MIN_F = math.ceil(5 / 6 * FPS - 1e-6)            # 20 frames
MAX_F = math.floor(7 * FPS + 1e-6)               # 167 frames


def F(t):
    return round(t * FPS)


def sec(k):
    return round(k / FPS, 3)


def nframes(a, b):
    return round((b - a) * FPS)


def L(lid, text, start, end, speaker=None, name=None, show=False, words="even", **more):
    toks = text.split()
    if words == "even":
        step = (end - start) / len(toks)
        words = [{"w": w, "s": round(start + i * step, 3), "e": round(start + (i + 1) * step, 3)}
                 for i, w in enumerate(toks)]
    return {"id": lid, "speaker": speaker, "text": text, "start": start, "end": end, "words": words,
            "show_name": show, "name": name, **more}


def bare(text):
    """The words of a text without the punctuation that is tidied for display (. , …)."""
    return re.sub(r"[.,…]", "", text).split()


def S(sid, onset, phrase, short=None, t1=None, kind="sfx", src=None, move=None):
    return {"id": sid, "kind": kind, "onset": onset, "t1": onset + 0.5 if t1 is None else t1,
            "phrase": phrase, "short": short or phrase, "source_line": src, "move_to": move}


def run(lines=(), sounds=(), cuts=(), window=(0.0, None), **settings):
    d = place(list(lines), list(sounds), list(cuts), FPS, window=window, settings=settings or None)
    check_invariants(d, sounds)
    return d


def cells_of(d, ref):
    return [c for c in d["cells"] if ref in c["refs"]]


def one(d, ref):
    cs = cells_of(d, ref)
    assert len(cs) == 1, cs
    return cs[0]


def check_invariants(d, sounds=()):
    cells = d["cells"]
    assert [c["id"] for c in cells] == [f"C{i + 1:04d}" for i in range(len(cells))]
    assert [c["start"] for c in cells] == sorted(c["start"] for c in cells)
    for c in cells:
        assert c["end"] > c["start"], c
        assert c["lines"] and c["kind"] in ("dialogue", "sfx", "music", "mixed")
        assert c["pos"] in ("bottom", "top") and isinstance(c["flags"], list)
        for line in c["lines"]:                     # I.13, I.4: tidied for display
            assert not line.endswith((".", ",")) and ".." not in line, c
        for t in (c["start"], c["end"]):            # on the frame grid (ms rounding)
            assert abs(t * FPS - round(t * FPS)) < 0.03, (t, c)
        chk = c["check"]
        assert chk["n_lines"] == len(c["lines"]) and chk["chars"] == [units(x) for x in c["lines"]]
        assert chk["dur"] == pytest.approx(c["end"] - c["start"], abs=0.0015)
    for a, b in zip(cells, cells[1:]):              # never closer than 2 frames
        assert nframes(a["end"], b["start"]) >= 2, (a, b)
        assert a["check"]["gap_next"] == pytest.approx(b["start"] - a["end"], abs=0.0015)
    ids = [s["id"] for s in sounds]                 # every sound in exactly one cell or in no_room
    placed = [r for c in cells for r in c["refs"] if r in ids]
    assert sorted(placed + [r["item"] for r in d["no_room"]]) == sorted(ids)
    for c in cells:                                 # one sound per dialogue cell
        if any(r.startswith("L") for r in c["refs"]):
            assert sum(1 for r in c["refs"] if r in ids) <= 1
    assert d["summary"]["cells"] == len(cells)
    assert d["summary"]["no_room"] == len(d["no_room"])
    assert d["summary"]["violations"] == sum(1 for c in cells if c["check"]["violations"])


# --------------------------------------------------------------------------- basics

def test_empty_inputs():
    d = run()
    assert d["cells"] == [] and d["no_room"] == []
    assert d["summary"]["cells"] == 0 and d["summary"]["violations"] == 0
    assert place([], [], [], FPS)["cells"] == []
    assert place(None, None, None, FPS, window=None)["cells"] == []


def test_line_in_at_first_word_out_half_second_after_last_word():
    d = run([L("L1", "안녕하세요 여러분", 10.0, 11.0)])
    c = one(d, "L1")
    assert c["kind"] == "dialogue" and c["lines"] == ["안녕하세요 여러분"]
    assert c["start"] == sec(F(10.0))
    assert c["end"] == sec(F(11.0) + 12)          # TG1: half a second (12 frames) after the audio
    assert c["check"]["violations"] == []


def test_min_duration_five_sixths():
    d = run([L("L1", "악", 10.0, 10.2)])
    c = one(d, "L1")
    assert nframes(c["start"], c["end"]) == MIN_F
    assert c["check"]["violations"] == []


def test_name_pushing_reading_speed_extends_the_end():
    text = "가나다라마 바사아자차 카타파하가 나다"     # 18.5 units: 1.32 s needed, tail gives 1.5 s
    plain = one(run([L("L1", text, 20.0, 21.0)]), "L1")
    assert plain["end"] == sec(F(21.0) + 12)
    named = one(run([L("L1", text, 20.0, 21.0, name="세바스찬", show=True)]), "L1")
    assert named["lines"][0].startswith("[세바스찬] ")           # II.8: [ ] around a speaker ID
    u = sum(named["check"]["chars"])
    assert u == 23.5                                # the space at the line break is not shown
    assert nframes(named["start"], named["end"]) == math.ceil(u / 14 * FPS - 1e-6)   # 1.68 s
    assert named["check"]["cps"] <= 14 and named["check"]["violations"] == []


def test_end_never_past_next_cell_minus_two_frames():
    text = "가나다라마 바사아자차 카타파하가 나다"
    d = run([L("L1", text, 20.0, 21.0, name="세바스찬", show=True), L("L2", "응", 21.6, 22.0)])
    a, b = one(d, "L1"), one(d, "L2")
    assert nframes(a["end"], b["start"]) == 2
    assert "초당 14자 초과" in a["check"]["violations"]        # no room: reported, text unchanged


def test_gap_under_half_second_is_half_a_second_after_the_tail_else_two_frames():
    # audit 2-08, TG5: "If an out-time has been extended by half a second beyond the end of the audio,
    # avoid extending this further to close gaps … ensuring that the gap is half a second or more".
    # The cell has had its half second after the speech: it is not made longer, it comes forward
    for nxt in (11.8, 11.9):                        # (it used to end 2 frames before L2: speech + 0.8 s)
        d = run([L("L1", "안녕하세요", 10.0, 11.0), L("L2", "반가워요", nxt, nxt + 0.8)])
        a, b = one(d, "L1"), one(d, "L2")
        assert a["end"] == sec(F(nxt) - 12) and nframes(a["end"], b["start"]) == 12, (nxt, a)
        assert F(11.0) < F(a["end"]) < F(11.0) + 12 and a["check"]["violations"] == []
        res = check.run(d, cuts=[])                 # the checker's gap rules (short_gap) agree
        assert res["summary"]["must"] == 0 and not any("gap" in v["code"] for v in res["violations"])
    # the half second after the speech was cut short by the next line: the gap is closed to 2 frames
    d = run([L("L1", "안녕하세요", 10.0, 11.0), L("L2", "반가워요", 11.3, 12.0)])
    assert nframes(one(d, "L1")["end"], one(d, "L2")["start"]) == 2
    assert one(d, "L1")["check"]["gap_next"] in (0.083, 0.084)
    # 5/6 s comes first: half a second before L2 would leave '응' under 5/6 s, so the gap is closed
    d = run([L("L1", "응", 10.0, 10.1), L("L2", "반가워요", 11.2, 12.0)])
    a, b = one(d, "L1"), one(d, "L2")
    assert nframes(a["end"], b["start"]) == 2 and nframes(a["start"], a["end"]) > MIN_F
    assert d["summary"]["violations"] == 0
    # a shot change in the gap: the shot change rules decide, as before (2 frames before the cut,
    # the next line starts on it)
    d = run([L("L1", "안녕하세요", 10.0, 11.0), L("L2", "반가워요", 11.8, 12.6)], cuts=[11.75])
    a, b = one(d, "L1"), one(d, "L2")
    assert a["end"] == sec(F(11.75) - 2) and b["start"] == sec(F(11.75))
    d = run([L("L1", "안녕하세요", 10.0, 11.0), L("L2", "반가워요", 12.2, 12.8)])
    a = one(d, "L1")
    assert a["end"] == sec(F(11.0) + 12)          # 0.7 s gap stays
    assert a["check"]["violations"] == []


def test_two_frame_gaps_in_a_run_of_lines_at_23976():
    lines, t = [], 10.0
    for k in range(8):
        lines.append(L(f"L{k}", "가나다 라마바", t, t + 1.0))
        t += 1.0 + 0.1 + 0.05 * k                  # gaps from 0.1 s to 0.45 s after the speech
    d = run(lines)
    for a, b in zip(d["cells"], d["cells"][1:]):
        assert nframes(a["end"], b["start"]) == 2
        assert a["check"]["gap_next"] in (0.083, 0.084)
    assert d["summary"]["violations"] == 0


def test_max_seven_seconds_and_long_speech_is_split():
    c = one(run([L("L1", "가나다 라마바 사아자", 10.0, 16.9)]), "L1")
    assert nframes(c["start"], c["end"]) <= MAX_F
    assert "7초 초과" not in c["check"]["violations"]
    words = [{"w": "하나", "s": 10.0, "e": 11.0}, {"w": "둘", "s": 18.0, "e": 19.0}]
    d = run([L("L1", "하나 둘", 10.0, 19.0, words=words)])
    cs = cells_of(d, "L1")
    assert [c["lines"] for c in cs] == [["하나"], ["둘"]]
    assert cs[1]["start"] == sec(F(18.0))
    assert all(nframes(c["start"], c["end"]) <= MAX_F for c in cs)


# --------------------------------------------------------------------------- shot changes (dialogue)

def test_in_time_snaps_back_to_a_cut_within_half_a_second():
    c = one(run([L("L1", "안녕", 10.3, 11.0)], cuts=[10.0]), "L1")
    assert c["start"] == sec(F(10.0))                                  # TG2
    c = one(run([L("L1", "안녕", 10.3, 11.0)], cuts=[9.6]), "L1")
    assert c["start"] == sec(F(10.3))                                  # 0.7 s before: no


def test_in_time_snap_never_cuts_previous_speech():
    d = run([L("L1", "가나다라", 9.0, 10.05), L("L2", "안녕", 10.3, 11.0)], cuts=[10.0])
    assert one(d, "L2")["start"] == sec(F(10.3))
    assert one(d, "L1")["end"] >= sec(F(10.05))


def test_in_time_before_a_cut_tg4():
    cut = 20.0
    k = F(cut)
    c = one(run([L("L1", "안녕하세요", (k - 2) / FPS, 21.0)], cuts=[cut]), "L1")
    assert c["start"] == sec(k)                                        # 2 frames before -> on the cut
    c = one(run([L("L1", "안녕하세요", (k - 10) / FPS, 21.0)], cuts=[cut]), "L1")
    assert c["start"] == sec(k - 12)                                   # 10 frames before -> 12 before
    c = one(run([L("L1", "응", (k - 6) / FPS, (k - 1) / FPS)], cuts=[cut]), "L1")
    assert c["start"] == sec(k - 6)                                    # over before the cut: stays


def test_out_time_extends_to_two_frames_before_a_cut():
    c = one(run([L("L1", "안녕하세요", 10.0, 11.0)], cuts=[11.8]), "L1")
    assert c["end"] == sec(F(11.8) - 2)                                # TG2


def test_out_time_backs_off_a_cut_after_the_speech():
    c = one(run([L("L1", "안녕하세요", 10.0, 11.0)], cuts=[11.3]), "L1")
    assert c["end"] == sec(F(11.3) - 2)                                # TG1: do not cross
    c = one(run([L("L1", "응", 10.0, 10.3)], cuts=[10.4]), "L1")
    assert c["end"] == sec(F(10.4) + 12)                               # cannot keep 5/6 s: half a second in
    assert c["check"]["violations"] == []


# --------------------------------------------------------------------------- splitting lines

def test_long_line_is_split_at_sentence_ends_with_the_name_once():
    text = "오늘은 정말 이상한 날이었어. 아침부터 계속 이상한 소리가 들렸거든. 그래서 나는 도서관에 가 보기로 했어"
    d = run([L("L1", text, 10.0, 18.0, name="양희", show=True)])
    cs = cells_of(d, "L1")
    assert len(cs) >= 2
    assert cs[0]["lines"][0].startswith("[양희] ")
    assert all("[양희]" not in " ".join(c["lines"]) for c in cs[1:])
    bodies = " ".join(" ".join(c["lines"]) for c in cs).replace("[양희] ", "", 1)
    assert bare(bodies) == bare(text)                                  # the words are never changed
    assert " ".join(cs[0]["lines"]).endswith("날이었어")                # cut at the sentence end; its period is not shown
    for c in cs:
        assert c["check"]["n_lines"] <= 2 and max(c["check"]["chars"]) <= 16
    words = text.split()
    step = 8.0 / len(words)
    k = len(" ".join(cs[0]["lines"]).replace("[양희] ", "").split())
    assert cs[1]["start"] == sec(F(round(10.0 + k * step, 3)))         # starts at its first word


def test_missing_or_degenerate_word_times_fall_back_to_even_spacing():
    d = run([L("L1", "하나 둘 셋", 10.0, 11.5, words=[]),
             L("L2", "넷 다섯", 20.0, 21.0, words=[{"w": "넷", "s": 0, "e": 0}, {"w": "다섯", "s": 0, "e": 0}]),
             L("L3", "여섯 일곱", 30.0, 31.0, words=[{"w": "여섯", "s": 30.0, "e": 30.5}]),
             L("L4", "여덟", 40.0, 40.0, words=[{"w": "여덟", "s": None, "e": None}]),
             {"id": "L5", "text": "아홉", "words": [{"w": "아홉", "s": 50.0, "e": 50.6}]},
             {"id": "L6", "text": "열"},                                # no time at all: skipped
             {"id": "L7", "text": "   ", "start": 60.0, "end": 61.0}])  # no text: skipped
    assert one(d, "L1")["start"] == sec(F(10.0))
    assert one(d, "L2")["start"] == sec(F(20.0))
    assert one(d, "L3")["start"] == sec(F(30.0))
    assert nframes(one(d, "L4")["start"], one(d, "L4")["end"]) == MIN_F
    assert one(d, "L5")["start"] == sec(F(50.0))
    assert not cells_of(d, "L6") and not cells_of(d, "L7")


def test_overlapping_lines_keep_order_and_gaps():
    # the scenario is the one this test always had; what is expected changed on 2026-11-10 (audit
    # 2-04, 7-09): before, L1 was cut to 10 frames ("5/6초 미만") and L3 to one frame. Now two
    # speakers who talk over each other share a hyphen cell (I.6), in the order they start.
    d = run([L("L1", "가나다라마바사 아자차카타", 10.0, 12.0, speaker="A"),
             L("L2", "파하가나다라 마바사아", 10.5, 11.5, speaker="B"),
             L("L3", "하나", 13.0, 13.4, speaker="A"), L("L4", "둘", 13.0, 13.4, speaker="B")])
    a, b = one(d, "L1"), one(d, "L3")
    assert a["refs"] == ["L1", "L2"] and a["lines"] == ["- 가나다라마바사 아자차카타", "- 파하가나다라 마바사아"]
    assert b["refs"] == ["L3", "L4"] and b["lines"] == ["- 하나", "- 둘"]
    assert a["start"] == sec(F(10.0)) and a["end"] >= sec(F(12.0))     # both speeches are covered
    assert nframes(a["end"], b["start"]) >= 2
    assert a["check"]["violations"] == [] and b["check"]["violations"] == []
    assert a["flags"] == [] and len(d["cells"]) == 2
    assert all(nframes(c["start"], c["end"]) >= MIN_F for c in d["cells"])


def test_two_short_lines_of_different_speakers_share_a_hyphen_cell():
    lines = [L("L1", "뭐?", 10.0, 10.2, speaker="빌", name="빌", show=True),
             L("L2", "응", 10.45, 10.6, speaker="세바스찬", name="세바스찬", show=True),
             L("L3", "그래 가자", 11.1, 12.0, speaker="빌")]
    d = run(lines)
    c = one(d, "L1")
    assert c["refs"] == ["L1", "L2"] and c["lines"] == ["- [빌] 뭐?", "- [세바스찬] 응"]
    assert c["kind"] == "dialogue" and c["check"]["violations"] == []
    same = [dict(lines[0]), dict(lines[1], speaker="빌", name="빌")]
    assert len(run(same)["cells"]) == 2                                # same speaker: no
    renamed = [dict(lines[0]), dict(lines[1], name="빌")]              # the writer's name counts
    assert len(run(renamed)["cells"]) == 2
    # 6th round: the second line need not be short. Before, this L1 was left a cell of 9 frames
    # ('5/6초 미만', GR 1) because L2's speech plus half a second reached 5/6 s; one hyphen cell
    # holds both and is read in time (TG 6 "merging ... is encouraged in order to borrow time")
    longer = [lines[0], L("L2", "응 알았어 지금 갈게", 10.45, 11.6, speaker="세바스찬")]
    c = one(run(longer), "L1")
    assert c["refs"] == ["L1", "L2"] and c["lines"] == ["- [빌] 뭐?", "- 응 알았어 지금 갈게"]
    assert c["check"]["violations"] == []
    # a second line too long for one hyphen line: no hyphen cell (I.6 one speaker a line)
    wide = [lines[0], L("L2", "응 알았어 지금 바로 그쪽으로 갈게", 10.45, 11.6, speaker="세바스찬")]
    assert [x["refs"] for x in run(wide)["cells"]] == [["L1"], ["L2"]]
    # a cell of the two that could not be read before the next line starts: as before, no hyphen cell
    tight = [lines[0], L("L2", "알았어 지금 곧장 갈게 기다려", 10.45, 10.95, speaker="세바스찬"),
             L("L3", "그래", 11.4, 11.6, speaker="빌")]                  # 20 units in 31 frames: 15.5 a second
    assert [x["refs"] for x in run(tight)["cells"]] == [["L1"], ["L2"], ["L3"]]


def test_two_guessed_lines_make_no_hyphen_cell_that_7_s_would_end_just_before_the_next():
    # 7차 (G3-04): the cell of the two from the first in-time would end at 7 s, 4 frames before the
    # next line (TG 5 "gaps between subtitles of 3-11 frames inclusive must be closed to 2 frames"),
    # and half a second before it would cut the speech: two guessed lines give way instead (each
    # gets 5/6 s, no must); two heard lines keep the hyphen cell (alone, the first is 6 frames)
    from cboe import check

    def musts(d):
        return [v["code"] for v in check.run(d)["violations"] if v["level"] == "must"]
    for timing, paired in (("voice", False), ("even", False), ("asr", True)):
        lines = [L("L1", "뭐?", sec(240), sec(244), speaker="가람", timing=timing),
                 L("L2", "응 알았어", sec(248), sec(404), speaker="나래", timing=timing),
                 L("L3", "그래", sec(411), sec(418), speaker="다솜", timing="asr")]
        d = run(lines)
        assert any(c["refs"] == ["L1", "L2"] for c in d["cells"]) is paired, (timing, d["cells"])
        if not paired:
            assert [c["refs"] for c in d["cells"]] == [["L1"], ["L2"], ["L3"]] and musts(d) == [], (timing, d["cells"])
        else:
            assert musts(d) == ["short_gap"], d["cells"]
    # with half a second left before the next line, or the gap closed, the two guessed lines share it
    for c0 in (419, 409):
        lines = [L("L1", "뭐?", sec(240), sec(244), speaker="가람", timing="voice"),
                 L("L2", "응 알았어", sec(248), sec(404 if c0 == 409 else 400), speaker="나래", timing="voice"),
                 L("L3", "그래", sec(c0), sec(c0 + 7), speaker="다솜", timing="asr")]
        assert one(run(lines), "L1")["refs"] == ["L1", "L2"], c0


def test_a_guessed_line_waits_behind_a_short_heard_line_it_would_leave_under_5_6_s():
    # 7차 (G3-05): '뭐?' heard, a guessed line of 1.5 s starting 5 frames after it (not spoken over
    # it, too long to share a hyphen cell by ①): it waits until '뭐?' has had 5/6 s and still gets its
    # own (before: '뭐?' was 3 frames on screen, GR 1, a must)
    from cboe import check
    for who in ("나래", "가람"):                    # another person, or the same one (no hyphen cell)
        lines = [L("L1", "뭐?", sec(240), sec(244), speaker="가람", timing="asr"),
                 L("L2", "응 알았어 지금 갈게", sec(245), sec(281), speaker=who, timing="voice")]
        d = run(lines)
        a, b = one(d, "L1"), one(d, "L2")
        assert F(a["start"]) == 240 and F(a["end"]) - F(a["start"]) >= MIN_F and a["check"]["violations"] == [], d["cells"]
        assert F(b["start"]) == F(a["end"]) + 2 and F(b["end"]) - F(b["start"]) >= MIN_F and b["check"]["violations"] == []
        assert [v for v in check.run(d)["violations"] if v["level"] == "must"] == []
        # not when the guessed line then has no 5/6 s before the line after it, nor past a scene change
        tight = run(lines + [L("L3", "그래", sec(275), sec(285), speaker="다솜", timing="asr")])
        assert F(one(tight, "L2")["start"]) == 245, tight["cells"]
        scenes = [dict(lines[0], scene_id="SC1"), dict(lines[1], scene_id="SC1"),
                  L("L3", "그래", sec(300), sec(310), speaker="다솜", timing="asr", scene_id="SC2")]
        assert F(one(run(scenes, cuts=[sec(283)]), "L2")["start"]) == 245
        # a heard line after it never moves for it (another person shares a hyphen cell with it, ③)
        heard = run([lines[0], dict(lines[1], timing="asr")])
        assert F(one(heard, "L2")["start"]) == 245 if who == "가람" else one(heard, "L2")["refs"] == ["L1", "L2"]


def test_two_nameless_turns_of_a_subtitle_script_share_a_hyphen_cell_without_labels():
    # 5th round: a subtitle script's "- " cell without names comes as two lines whose person is
    # "#" + the turn (state.placement_inputs); two people, and the "#…" is never shown
    from cboe import check
    lines = [L("L1", "뭐라고?", 10.0, 10.2, person="#1-1", turn="1-1"),
             L("L2", "아니야", 10.45, 10.6, person="#1-2", turn="1-2"),
             L("L3", "그래 가자", 11.1, 12.0, person="#2", turn="2")]
    for ls in (lines, [dict(x, show_name=True) for x in lines]):      # nothing to show without a name
        d = run(ls)
        c = one(d, "L1")
        assert c["refs"] == ["L1", "L2"] and c["lines"] == ["- 뭐라고?", "- 아니야"]
        assert c["check"]["violations"] == [] and c["flags"] == []
        assert [x["lines"] for x in d["cells"]] == [["- 뭐라고?", "- 아니야"], ["그래 가자"]]
        assert not any("#" in t or "[" in t for x in d["cells"] for t in x["lines"])
        assert [v for v in check.run(d)["violations"] if v["level"] == "must"] == []
    one_person = [lines[0], dict(lines[1], person="#1-1"), lines[2]]  # one turn twice: no hyphen cell
    assert [x["refs"] for x in run(one_person)["cells"]] == [["L1"], ["L2"], ["L3"]]
    # lines that overlap: ① of the untangling makes the same hyphen cell (the turns come with the
    # persons, as state.placement_inputs gives them: 6th round, a '#' person is another speaker
    # than the other row of its own cell only)
    a = L("L1", "오늘은 어디 갔었어?", 10.0, 11.6, person="#1-1", turn="1-1")
    b = L("L2", "그냥 집에 있었어", 10.5, 12.0, person="#1-2", turn="1-2")
    c = one(run([a, b]), "L1")
    assert c["lines"] == ["- 오늘은 어디 갔었어?", "- 그냥 집에 있었어"] and c["check"]["violations"] == []


# a subtitle file as the script (made-up text): read, aligned, laid out as the review page does

def _srt_project(tmp, srt, asr):
    from cboe import script
    from cboe.stages import align
    from cboe.state import Project
    from cboe.util import write_json
    tmp.mkdir(parents=True, exist_ok=True)
    f = tmp / "subs.srt"
    f.write_text(srt, encoding="utf-8")
    write_json(tmp / "project.json", {"title": "t", "t0": 0.0, "t1": 120.0, "duration": 120.0, "fps": 24.0})
    write_json(tmp / "script.json", script.read(f))
    write_json(tmp / "asr.json", {"words": [{"w": w, "s": s, "e": e} for w, s, e in asr],
                                  "vad": [[s, e] for _, s, e in asr]})
    align.run(Project(tmp), {})
    write_json(tmp / "items.json", {"items": []})
    write_json(tmp / "cuts.json", {"cuts": []})
    return Project(tmp)


def _srt_cells(p):
    d = p.recompute()
    return [c["lines"] for c in d["cells"]], d["summary"]["violations"]


ROWS = ("1\n00:01:20,000 --> 00:01:21,375\n- 어디 가니?\n- 학교\n\n"
        "2\n00:01:25,000 --> 00:01:27,000\n그럼 이따 봐\n")
ROWS_ASR = [("어디", 80.0, 80.15), ("가니?", 80.15, 80.3), ("학교", 80.6, 80.9), ("그럼", 85.0, 85.3),
            ("이따", 85.3, 85.6), ("봐", 85.6, 85.9)]


def test_the_rows_of_one_subtitle_cell_share_it_when_the_first_cannot_stand_alone(tmp_path):
    # 5th-round check (reverify:e1): the first row could not get 5/6 s before the second and the
    # second row's speech plus half a second was exactly 5/6 s, so the two stayed apart and the first
    # cell went out at half a second ('5/6초 미만', GR 1); rows of one cell were one cell (②)
    assert _srt_cells(_srt_project(tmp_path / "a", ROWS, ROWS_ASR)) == ([["- 어디 가니?", "- 학교"], ["그럼 이따 봐"]], 0)
    named = ROWS.replace("- 어디 가니?", "- [가람] 어디 가니?").replace("- 학교", "- [나래] 학교")
    cells, violations = _srt_cells(_srt_project(tmp_path / "b", named, ROWS_ASR))
    assert len(cells) == 2 and len(cells[0]) == 2 and violations == 0
    # one row named, the other not: still the two rows of one cell
    mixed = ROWS.replace("- 어디 가니?", "- [가람] 어디 가니?")
    cells, violations = _srt_cells(_srt_project(tmp_path / "c", mixed, ROWS_ASR))
    assert len(cells) == 2 and [x.lstrip("- ").split("] ")[-1] for x in cells[0]] == ["어디 가니?", "학교"]
    assert violations == 0


CROSS = ("1\n00:01:19,000 --> 00:01:20,500\n- 오늘 점심에 뭐 먹었어?\n- 국수\n\n"
         "2\n00:01:20,700 --> 00:01:23,000\n- 맛있었어?\n- 그냥 좀 그랬어\n\n"
         "3\n00:01:30,000 --> 00:01:32,000\n이제 자자\n")
CROSS_ASR = [("오늘", 79.0, 79.3), ("점심에", 79.3, 79.5), ("뭐", 79.5, 79.7), ("먹었어?", 79.7, 80.0),
             ("국수", 80.1, 80.3), ("맛있었어?", 80.7, 80.9), ("그냥", 82.4, 82.6), ("좀", 82.6, 82.8),
             ("그랬어", 82.8, 83.0)]


def test_rows_of_two_subtitle_cells_never_share_a_hyphen_cell(tmp_path):
    # 5th-round check (reverify:e1): the nameless rows "#1-2" and "#2-1" (or "#1-2" and a named line
    # of the next cell) were taken for two known speakers and put in one hyphen cell; they may be one
    # person (I.6: a hyphen cell is two speakers). The short '국수' goes back to its own cell instead.
    want = [["- 오늘 점심에 뭐 먹었어?", "- 국수"], ["맛있었어?"], ["그냥 좀 그랬어"], ["이제 자자"]]
    p = _srt_project(tmp_path / "a", CROSS, CROSS_ASR)
    assert _srt_cells(p) == (want, 0)
    row = next(ln["id"] for ln in p.dialogue() if ln["text"] == "맛있었어?")
    p.set_line(row, start=80.7, end=80.9)                            # the writer sets the time
    assert _srt_cells(p) == (want, 0)
    named_next = CROSS.replace("- 맛있었어?\n- 그냥 좀 그랬어", "[가람] 맛있었어?")
    cells, violations = _srt_cells(_srt_project(tmp_path / "b", named_next, CROSS_ASR[:6]))
    assert ["- 국수", "- 맛있었어?"] not in cells and ["맛있었어?"] in cells and violations == 0


def test_placement_keeps_nameless_rows_of_two_cells_apart_short_or_spoken_over():
    def row(lid, words, turn):
        return L(lid, " ".join(w for w, _, _ in words), words[0][1], words[-1][2], timing="asr",
                 words=[{"w": w, "s": s, "e": e} for w, s, e in words], person="#" + turn, turn=turn)
    head = row("L1", [("오늘", 79.0, 79.3), ("점심에", 79.3, 79.5), ("뭐", 79.5, 79.7), ("먹었어?", 79.7, 80.0)], "1-1")
    tail = row("L4", [("그냥", 82.5, 82.7), ("좀", 82.7, 82.9), ("그랬어", 82.9, 83.2)], "2-2")
    for b, c in (([("국수", 80.1, 80.3)], [("맛있었어?", 80.7, 80.9)]),                     # both short
                 ([("국수", 80.1, 80.4), ("먹었지", 80.4, 80.9)], [("맛있었어?", 80.6, 80.8)])):  # spoken over
        d = place([head, row("L2", b, "1-2"), row("L3", c, "2-1"), tail], [], [], 24.0, window=(0.0, 120.0))
        assert all(set(x["refs"]) != {"L2", "L3"} for x in d["cells"])
    # a named line of another cell is no other speaker for a nameless row either (it may be that one)
    named = dict(row("L3", [("맛있었어?", 80.7, 80.9)], "2-1"), person="가람", name="가람", turn=None)
    d = place([head, row("L2", [("국수", 80.1, 80.3)], "1-2"), named], [], [], 24.0, window=(0.0, 120.0))
    assert all(set(x["refs"]) != {"L2", "L3"} for x in d["cells"])
    # two named lines of two cells are two known people, as before
    two = [dict(x, person=n, name=n) for x, n in ((row("L2", [("국수", 80.1, 80.3)], "1-2"), "나래"),
                                                 (row("L3", [("맛있었어?", 80.5, 80.7)], "2-1"), "가람"))]
    assert place(two, [], [], 24.0)["cells"][0]["refs"] == ["L2", "L3"]


# --------------------------------------------------------------------------- sound timing (memo 5.2)

@pytest.mark.parametrize("phrase, u, seconds", [("[책 떨어지는 소리]", 9, 1.64), ("[쿵, 책 떨어지는 소리]", 11, 1.79)])
def test_sfx_length_deck_examples(phrase, u, seconds):
    assert units(phrase) == u
    c = one(run(sounds=[S("S1", 50.0, phrase, t1=51.0)]), "S1")
    assert c["kind"] == "sfx" and c["lines"] == [phrase] and c["refs"] == ["S1"]
    assert c["start"] == sec(F(50.0))
    assert nframes(c["start"], c["end"]) == round((u / 14 + 1) * FPS)    # units / 14 + 1 s
    assert c["check"]["dur"] == pytest.approx(seconds, abs=1 / FPS)


def test_sfx_starts_on_its_onset_even_just_after_a_cut():
    c = one(run(sounds=[S("S1", 50.0, "[쿵]")], cuts=[49.8]), "S1")
    assert c["start"] == sec(F(50.0))                                  # BBC 5.5: never early


def test_sfx_length_has_bounds():
    c = one(run(sounds=[S("S1", 50.0, "[" + "가" * 14 + "]", t1=60.0)]), "S1")
    assert nframes(c["start"], c["end"]) == round((15 / 14 + 1) * FPS)  # the sound's own length is not used
    c = one(run(sounds=[S("S1", 50.0, "[쿵]")], cps=1000.0, label_extra=0.0), "S1")
    assert nframes(c["start"], c["end"]) == MIN_F


def test_music_start_and_length():
    m = dict(kind="music", t1=70.0)
    c = one(run(sounds=[S("M1", 60.0, "[무서운 음악]", **m)], cuts=[59.7]), "M1")
    assert c["start"] == sec(F(59.7)) and c["kind"] == "music"            # cut in the half second before
    assert c["lines"] == ["[무서운 음악]"]                                 # II.7: a music label is in brackets
    assert nframes(c["start"], c["end"]) == round(3 * FPS)
    c = one(run(sounds=[S("M1", 60.0, "[무서운 음악]", **m)], cuts=[59.3]), "M1")
    assert c["start"] == sec(F(60.0))
    c = one(run(sounds=[S("M1", 60.0, "[음악]", kind="music", t1=61.5)]), "M1")
    assert c["end"] == sec(F(61.5) + 12)                                 # shorter music: its end + 0.5 s
    c = one(run(sounds=[S("M1", 60.0, "[음악]", kind="music", t1=60.1)]), "M1")
    assert nframes(c["start"], c["end"]) == MIN_F


def test_music_phrase_of_an_older_work_folder_is_written_in_brackets():
    # items.json and review.json of older runs hold '♬ …' phrases: they come out as labels
    c = one(run(sounds=[S("M1", 60.0, "♬ 무서운 음악", short="♬ 음악", kind="music", t1=70.0)]), "M1")
    assert c["lines"] == ["[무서운 음악]"]
    d = run([L("L1", "응", 60.0, 60.4)], [S("M1", 60.1, "♬ 고조되는 아주 무서운 음악 소리", short="♬", kind="music", t1=70.0)])
    assert one(d, "L1")["lines"] == ["- 응", "- [음악]"]                  # the short phrase, modernised too
    assert not any("♬" in x for c in d["cells"] for x in c["lines"])


def test_music_snap_never_takes_the_previous_speech():
    d = run([L("L1", "가나다라", 58.0, 59.68)], [S("M1", 60.0, "[음악]", kind="music", t1=70.0)], cuts=[59.7])
    assert one(d, "M1")["start"] == sec(F(60.0))


def test_sound_end_chains_to_the_next_cell():
    d = run([L("L1", "안녕", 51.5, 52.0)], [S("S1", 50.0, "[쿵]")])
    assert nframes(one(d, "S1")["end"], one(d, "L1")["start"]) == 2


def test_sound_end_extends_to_a_cut_just_after_it():
    c = one(run(sounds=[S("S1", 50.0, "[쿵]")], cuts=[51.4]), "S1")
    assert c["end"] == sec(F(51.4) - 2)


def test_gap_left_by_a_cut_rule_is_closed_again():
    k = F(41.3)                                     # the sound goes on past the cut: the cell stays
    d = run([L("L1", "가나다 라마바", 40.0, 41.0), L("L2", "응", (k + 19) / FPS, (k + 30) / FPS)],
            [S("S1", 40.0, "[쿵]", t1=41.6)], cuts=[41.3])     # half a second into the new shot,
    a, b = one(d, "L1"), one(d, "L2")                          # then the 7-frame gap is closed
    assert a["refs"] == ["S1", "L1"] and nframes(a["end"], b["start"]) == 2
    assert a["check"]["violations"] == []


def test_sound_cell_ends_before_a_cut_when_its_sound_is_over():
    c = one(run(sounds=[S("S1", 50.0, "[책 떨어지는 소리]", t1=50.3)], cuts=[51.2]), "S1")
    assert c["end"] == sec(F(51.2) - 2)
    c = one(run(sounds=[S("S1", 50.0, "[책 떨어지는 소리]", t1=50.3)], cuts=[50.5]), "S1")
    assert c["end"] == sec(F(50.0) + 39)                               # less than 5/6 s would be left
    c = one(run(sounds=[S("M1", 60.0, "[무서운 음악]", kind="music", t1=66.0)], cuts=[61.0]), "M1")
    assert c["end"] == sec(F(60.0) + 72)                               # music goes on: may cross


# --------------------------------------------------------------------------- positions (memo 5.3)

def test_position_1_own_cell_in_a_gap():
    d = run([L("L1", "안녕하세요", 10.0, 11.0), L("L2", "네", 20.0, 20.5)], [S("S1", 13.0, "[쿵]")])
    c = one(d, "S1")
    assert c["refs"] == ["S1"] and c["kind"] == "sfx" and c["start"] == sec(F(13.0))
    d = run([L("L1", "안녕하세요", 10.0, 11.0)], [S("S1", 11.2, "[쿵]")])     # in the tail after the speech
    a, s = one(d, "L1"), one(d, "S1")
    assert s["start"] == sec(F(11.2)) and s["refs"] == ["S1"]
    assert nframes(a["end"], s["start"]) == 2 and a["end"] >= sec(F(11.0))


DECK_LINE = dict(speaker="BJ 옴뫄", name="BJ 옴뫄", show=True, words=[{"w": "으아아아아", "s": 230.357, "e": 232.162}])


def test_position_2_deck_scream_and_thunder():
    ln = L("L0049", "으아아아아", 230.357, 232.162, **DECK_LINE)
    d = run([ln], [S("S0008", 230.4, "[천둥 소리]", short="[천둥]", t1=232.2)], cuts=[230.35])
    c = one(d, "L0049")
    assert c["refs"] == ["L0049", "S0008"] and c["kind"] == "mixed"
    assert c["lines"] == ["- [BJ 옴뫄] 으아아아아", "- [천둥 소리]"]          # heard order
    assert c["start"] == sec(F(230.357)) and c["check"]["violations"] == []
    d = run([ln], [S("S0008", 230.0, "[천둥 소리]", t1=231.0)])
    c = one(d, "L0049")
    assert c["lines"] == ["- [천둥 소리]", "- [BJ 옴뫄] 으아아아아"]          # the sound heard first
    assert c["start"] == sec(F(230.0))


def test_position_3_sound_of_the_speaker_on_one_line():
    ln = L("L1", "나도야!", 40.0, 40.8, speaker="지수")
    c = one(run([ln], [S("S1", 40.0, "[재채기]", src="L1")]), "L1")
    assert c["lines"] == ["[재채기] 나도야!"] and c["refs"] == ["L1", "S1"] and c["kind"] == "mixed"
    c = one(run([dict(ln, name="지수", show_name=True)], [S("S1", 40.1, "[재채기]", src="L1")]), "L1")
    assert c["lines"] == ["[지수] [재채기] 나도야!"]                      # the guide's own example (II.5)
    longer = L("L1", "그러니까 나도 같이 가자고", 40.0, 41.5, speaker="지수", name="지수", show=True)
    c = one(run([longer], [S("S1", 40.0, "[재채기]", src="L1")]), "L1")
    assert len(c["lines"]) == 2 and not any(x.startswith("- ") for x in c["lines"])
    assert " ".join(c["lines"]) == "[지수] [재채기] 그러니까 나도 같이 가자고"
    other = one(run([ln], [S("S1", 40.0, "[재채기]", src="L9")]), "L1")    # someone else: hyphens
    assert other["lines"] == ["- [재채기]", "- 나도야!"]


ROW = "내가 어제 도서관에 갔는데 그런 상황은 아니었어"


def test_position_4_split_in_the_middle():
    # the piece before the sound is spoken 2.3 s, more than INTERRUPT_LEAD: the sound would show that
    # long before it is heard in its cell (II.9's order, test_a_sound_that_interrupts_…), so it goes with
    # the later piece, which starts at the sound, as before the 6th round
    d = run([L("L1", ROW, 70.0, 74.0)], [S("S1", 72.3, "[첨벙]")])
    a, b = cells_of(d, "L1")
    assert a["refs"] == ["L1"] and b["refs"] == ["S1", "L1"]
    assert a["lines"] == ["내가 어제 도서관에 갔는데"]
    assert b["lines"] == ["- [첨벙]", "- 그런 상황은 아니었어"]
    assert b["start"] == sec(F(72.3)) and nframes(a["end"], b["start"]) == 2     # starts at the sound
    assert all(nframes(c["start"], c["end"]) >= MIN_F for c in (a, b))
    assert not a["check"]["violations"] and not b["check"]["violations"]


def test_position_4_not_when_a_part_would_be_too_short():
    # the only word boundary near the sound is 0.3 s into the line: the piece before it gets no 5/6 s
    # in either order (with the sound from the in-time, or alone up to the sound), so no_room
    text = "응 그러니까 내일 다시 오면 돼"                              # made up
    times = [(70.0, 70.3), (70.3, 71.2), (71.2, 71.6), (71.6, 72.0), (72.0, 72.4), (72.4, 72.8)]
    d = run([L("L1", text, 70.0, 72.8, words=W(text, times), timing="asr")], [S("S1", 70.6, "[첨벙]")])
    assert len(cells_of(d, "L1")) == 1 and not cells_of(d, "S1")
    (r,) = d["no_room"]
    assert r["reason"] == f"나누면 앞 칸 5/6초 미만 · 빈틈이 소리 {(F(72.8) + 2 - F(70.6)) / FPS:.2f}초 뒤"
    assert "hint" not in r          # ending the line at the sound would leave it under 5/6 s (a must)
    # 6th round: a piece 0.5 s long before a sound that comes before the next word gets its 5/6 s in
    # the guide's order, the later piece waiting for it at most half a second after its first word
    # (before: no_room)
    times = [(70.0, 70.5), (70.85, 71.2), (71.2, 71.8), (71.8, 72.3), (72.3, 72.6), (72.6, 73.2), (73.2, 74.0)]
    d = run([L("L1", ROW, 70.0, 74.0, words=W(ROW, times), timing="asr")], [S("S1", 70.54, "[첨벙]")])
    a, b = cells_of(d, "L1")
    assert a["lines"] == ["- 내가", "- [첨벙]"] and a["refs"] == ["L1", "S1"] and b["refs"] == ["L1"]
    first_word = F(70.85)                                              # '어제'
    assert 0 < F(b["start"]) - first_word <= 12 and nframes(a["start"], a["end"]) >= MIN_F
    assert F(a["end"]) - F(70.54) >= 6          # the sound's words on screen when it is heard (refix f1)
    assert a["check"]["violations"] == [] and b["check"]["violations"] == [] and d["no_room"] == []
    # a sound heard after the next word started (here 1 frame after '어제', even word times): not in the
    # guide's order, whose cell would end 5 frames after the sound, before the words already heard
    # (refix f1); the old order leaves the piece before it under 5/6 s, so no_room, as before the 6th round
    d = run([L("L1", ROW, 70.0, 74.0)], [S("S1", 70.6, "[첨벙]")])
    assert len(cells_of(d, "L1")) == 1 and not cells_of(d, "S1")
    assert d["no_room"][0]["reason"].startswith("나누면 앞 칸 5/6초 미만 · ")


def test_dialogue_words_are_never_changed_by_sounds():
    # only the display punctuation is tidied (I.13, I.4); the words stay as the script has them
    lines = [L("L1", ROW + ".", 70.0, 74.0, name="양희", show=True), L("L2", "나도야!", 75.0, 75.8),
             L("L3", "응...", 77.0, 77.3)]
    sounds = [S("S1", 72.3, "[첨벙]"), S("S2", 75.0, "[재채기]", src="L2"), S("S3", 77.1, "[쿵]")]
    d = run(lines, sounds)
    phrases = [s["phrase"] for s in sounds]
    for ln in lines:
        text = " ".join(" ".join(c["lines"]) for c in cells_of(d, ln["id"]))
        for p in phrases:
            text = text.replace(p, "")
        text = text.replace("- ", "").replace("[양희] ", "")
        assert bare(text) == bare(ln["text"])
    assert one(d, "L2")["lines"] == ["[재채기] 나도야!"]                  # ! and ? are kept
    assert one(d, "L3")["lines"] == ["- 응…", "- [쿵]"]                   # ... becomes … (I.4)


# --------------------------------------------------------------------------- over the limits (memo 5.3 ⑤)

def test_fix_1_short_phrase_when_the_hyphen_line_is_too_long():
    long_ = "[쿵, 책이 바닥에 떨어지는 소리]"
    assert units("- " + long_) > 16
    c = one(run([L("L1", "응", 80.0, 80.4)], [S("S1", 80.1, long_, short="[쿵]")]), "L1")
    assert c["lines"] == ["- 응", "- [쿵]"] and c["refs"] == ["L1", "S1"]


def test_fix_2_longer_end_for_reading_speed():
    ln = L("L1", "아니 그게 무슨 말이야 진짜로?", 85.0, 85.6)
    c = one(run([ln], [S("S1", 85.0, "[쿵]")]), "L1")
    assert c["lines"] == ["- [쿵]", "- 아니 그게 무슨 말이야 진짜로?"]
    u = sum(c["check"]["chars"])
    assert nframes(c["start"], c["end"]) == math.ceil(u / 14 * FPS - 1e-6)   # longer than sound and speech
    assert c["check"]["cps"] <= 14 and c["check"]["violations"] == []


def test_fix_3_move_into_a_gap_just_after_the_sound():
    d = run([L("L1", "안녕", 90.0, 90.3)], [S("S1", 90.6, "[쿵]")])
    s = one(d, "S1")
    assert s["refs"] == ["S1"] and s["start"] == sec(F(90.0) + MIN_F + 2)
    assert 0 < nframes(90.6, s["start"]) <= 12
    assert nframes(one(d, "L1")["start"], one(d, "L1")["end"]) == MIN_F


def _crowded(t, sound):
    k = F(t)
    nxt = (k + 15) / FPS
    return run([L("L1", "안녕하세요", 100.0, 101.0), L("L2", "네", nxt, nxt + 0.5)], [sound])


def test_no_room_reports_the_gap_after_the_sound():
    d = _crowded(101.3, S("S1", 101.3, "[쿵]"))
    assert not cells_of(d, "S1")
    (r,) = d["no_room"]
    assert r["item"] == "S1" and r["t"] == 101.3 and r["text"] == "[쿵]"
    # 6th round: and what one change of one cell makes room (audit 7-07): the 5/6 s of the sound want
    # 20 frames where there are 13, so the line after it 7 frames later
    assert r["reason"] == f"소리 뒤 빈틈 {13 / FPS:.2f}초 · 뒤 대사 칸을 {7 / FPS:.2f}초 늦추면 들어감"
    assert r["reason"] == "소리 뒤 빈틈 0.54초 · 뒤 대사 칸을 0.29초 늦추면 들어감"
    assert r["hint"] == {"cell": one(d, "L2")["id"], "line": "L2", "by": round(7 / FPS, 3), "how": "move_later"}
    assert d["summary"]["no_room"] == 1


def test_writer_move_places_a_no_room_sound():
    d = _crowded(101.3, S("S1", 101.3, "[쿵]", move=105.0))
    assert d["no_room"] == [] and one(d, "S1")["start"] == sec(F(105.0))


def test_never_into_a_cell_that_already_has_two_lines():
    b0 = (F(110.0) + 12) / FPS
    d = run([L("L2", "가나다라마 바사아자차 카타파하가 나다", b0, b0 + 1.5)], [S("S1", 110.0, "[쿵]")])
    assert one(d, "L2")["refs"] == ["L2"] and len(one(d, "L2")["lines"]) == 2
    (r,) = d["no_room"]
    # no cut of the two lines leaves a first cell of 5/6 s (9 and 18 frames before the next word), so
    # the cell is not cut again (test_a_sound_at_a_cell_of_two_lines_…); the line could start 10 frames later
    assert r["reason"] == f"대사 칸이 이미 두 줄 · 소리 뒤 빈틈 {10 / FPS:.2f}초 · 뒤 대사 칸을 {10 / FPS:.2f}초 늦추면 들어감"
    assert r["reason"] == "대사 칸이 이미 두 줄 · 소리 뒤 빈틈 0.42초 · 뒤 대사 칸을 0.42초 늦추면 들어감"
    assert r["hint"] == {"cell": "C0001", "line": "L2", "by": round(10 / FPS, 3), "how": "move_later"}


def test_one_sound_per_dialogue_cell():
    d = run([L("L1", "응 그래", 120.0, 120.5)], [S("S1", 120.0, "[쿵]"), S("S2", 120.1, "[쾅]")])
    c = one(d, "L1")
    assert c["refs"] == ["S1", "L1"]
    assert [r["item"] for r in d["no_room"]] == ["S2"] and "이미 소리" in d["no_room"][0]["reason"]


def test_two_sounds_close_together_share_a_cell():
    c = one(run(sounds=[S("S1", 140.0, "[쿵]"), S("S2", 140.2, "[쨍그랑]")]), "S1")
    assert c["refs"] == ["S1", "S2"] and c["lines"] == ["- [쿵]", "- [쨍그랑]"] and c["kind"] == "sfx"
    c = one(run(sounds=[S("S1", 140.0, "[쿵]"), S("M1", 140.2, "[음악]", kind="music", t1=150.0)]), "S1")
    assert c["kind"] == "mixed"


def test_many_sounds_in_a_row_are_all_accounted_for():
    lines = [L("L1", "뭐야 이게", 129.0, 129.6)]
    sounds = [S(f"S{k}", 130.0 + 0.25 * k, f"[쿵{k}]") for k in range(8)]
    d1, d2 = run(lines, sounds), run(lines, list(reversed(sounds)))
    assert json.dumps(d1, ensure_ascii=False) == json.dumps(d2, ensure_ascii=False)   # deterministic
    assert all(c["check"]["n_lines"] <= 2 for c in d1["cells"])
    assert d1["no_room"] and all(r["reason"] for r in d1["no_room"])
    assert d1["summary"]["sounds_placed"] + len(d1["no_room"]) == len(sounds)


def test_sound_without_text_or_time_goes_to_no_room():
    d = run(sounds=[S("S1", 50.0, ""), dict(S("S2", 0.0, "[쿵]"), onset=None)])
    assert {r["item"]: r["reason"] for r in d["no_room"]} == {"S1": "문구 없음", "S2": "시각 없음"}


def test_window_clamps_cells_and_reports_sounds_outside():
    lines = [L("L1", "밖이야", 5.0, 6.0), L("L2", "안이야", 12.0, 13.0), L("L3", "끝에 걸쳐", 19.5, 21.0)]
    sounds = [S("S1", 8.0, "[쿵]"), S("S2", 15.0, "[쾅]"), S("S3", 25.0, "[펑]")]
    d = run(lines, sounds, window=(10.0, 20.0))
    assert not cells_of(d, "L1") and one(d, "S2")["refs"] == ["S2"]
    assert all(sec(F(10.0)) <= c["start"] and c["end"] <= sec(F(20.0)) for c in d["cells"])
    assert {r["item"]: r["reason"] for r in d["no_room"]} == {"S1": "처리 구간 밖", "S3": "처리 구간 밖"}
    assert one(d, "L3")["end"] == sec(F(20.0))
    d = run(lines, [], window=(10.0, None))
    assert cells_of(d, "L3") and not cells_of(d, "L1")


def test_other_frame_rates_keep_two_frames_and_half_seconds():
    for fps, half in ((25.0, 13), (29.97, 15), (60.0, 30)):         # half a second: 12.5 -> 13 frames
        d = place([L("L1", "안녕하세요", 10.0, 11.0), L("L2", "반가워요", 11.8, 12.6)], [], [], fps)
        a, b = d["cells"]
        assert round((b["start"] - a["end"]) * fps) == half          # after the tail: half a second (TG5)
        assert abs(a["start"] * fps - round(a["start"] * fps)) < 0.06
        d = place([L("L1", "안녕하세요", 10.0, 11.0), L("L2", "반가워요", 11.3, 12.0)], [], [], fps)
        a, b = d["cells"]
        assert round((b["start"] - a["end"]) * fps) == 2             # the tail cut short: 2 frames


def test_a_frame_rate_a_hair_off_a_whole_rate_counts_its_limits_at_that_rate():
    # 5th round check (verify:e4): at ffprobe's 24.00001 (a 24 fps video) 5/6 s was 21 frames, one more
    # than the TTML header's 24 gives; the limits are counted at the rate it stands for (style.nominal_fps)
    for fps, want in ((24.00001, 20), (24.0, 20), (30.00001486621153, 25), (30.0, 25), (29999 / 1251, 20)):
        d = place([L("L1", "악", 10.0, 10.2)], [], [], fps)
        c = d["cells"][0]
        assert round(c["end"] * fps) - round(c["start"] * fps) == want and c["check"]["violations"] == [], fps
        assert d["fps"] == fps and c["start"] == round(round(10.0 * fps) / fps, 3)   # the grid is the video's own
        # a line right behind it (the same speaker): its cell holds 5/6 s and no more, with no violation
        nxt = round((round(10.0 * fps) + want + 2) / fps, 3)
        d = place([L("L1", "악", 10.0, 10.2, speaker="A"), L("L2", "응 그래", nxt, nxt + 1.0, speaker="A")], [], [], fps)
        assert [round((x["end"] - x["start"]) * fps) for x in d["cells"][:1]] == [want] and d["summary"]["violations"] == 0


# --------------------------------------------------------------------------- labels and notes (II.6-II.8, II.10)

def test_speaker_label_is_name_then_extra_then_language():
    def first(**kw):
        return one(run([L("L1", kw.pop("text", "나도 갈래"), 10.0, 11.0, **kw)]), "L1")["lines"]

    assert first(name="영희", show=True, language="중국어") == ["[영희/중국어] 나도 갈래"]       # II.6 example
    extra = first(name="영희", show=True, label_extra=["속마음"], language="영어")
    assert extra == ["[영희/속마음/영어] 나도 갈래"]                                         # II.8: the language last
    assert first(name="철수", show=False, language="영어", text="아니다, 취소") == ["[영어] 아니다, 취소"]
    assert first(name="영희", show=True, language="한국어") == ["[영희] 나도 갈래"]              # the original language: no label
    assert first(name="영희", show=False, label_extra=["속삭임"]) == ["[속삭임] 나도 갈래"]
    assert first(name=None, show=True) == ["나도 갈래"]


def test_two_people_speaking_together():
    def first(name):
        return one(run([L("L1", "설마!", 10.0, 11.0, name=name, show=True)]), "L1")["lines"]

    assert first("영희/철수") == ["[영희, 철수] 설마!"]              # II.10 (O); '[영희와 철수]' is the (X) form
    assert first("영희/철수/민수") == ["[함께] 설마!"]               # a long joint label
    assert first("영희") == ["[영희] 설마!"]


def test_language_label_on_every_piece_and_counted_in_the_width():
    text = "어제 박물관에서 무슨 일이 있었는지 아무도 몰라 그래서 내가 직접 가 보려는 거야"      # a made-up line
    d = run([L("L1", text, 10.0, 16.0, name="피터", show=True, language="영어")])
    cs = cells_of(d, "L1")
    assert len(cs) >= 2
    assert cs[0]["lines"][0].startswith("[피터/영어] ")
    assert all(c["lines"][0].startswith("[영어] ") for c in cs[1:])       # II.6: "in each event"
    assert all(max(c["check"]["chars"]) <= 16 and c["check"]["n_lines"] <= 2 for c in cs)
    body = " ".join(" ".join(c["lines"]) for c in cs).replace("[피터/영어] ", "").replace("[영어] ", "")
    assert body.split() == text.split()
    plain = cells_of(run([L("L1", text, 10.0, 16.0, name="피터", show=True)]), "L1")
    assert all(not c["lines"][0].startswith("[") for c in plain[1:])      # without a language: the name once


def test_language_label_stays_when_a_sound_splits_the_line():
    d = run([L("L1", ROW, 70.0, 74.0, name="피터", show=True, language="영어")], [S("S1", 72.3, "[첨벙]")])
    a, b = cells_of(d, "L1")
    assert a["lines"][0].startswith("[피터/영어] ")
    assert b["lines"][0] == "- [첨벙]" and b["lines"][1].startswith("- [영어] ")


def test_sung_line_has_notes_on_every_cell():
    assert one(run([L("L1", "달빛 아래", 20.0, 21.0, sung=True)]), "L1")["lines"] == ["♪ 달빛 아래 ♪"]
    text = "달빛 아래 걷던 그 길을 오늘도 혼자서 다시 걸어 보네 바람만 불어오네"
    cs = cells_of(run([L("L1", text, 20.0, 27.0, name="영수", show=True, sung=True)]), "L1")
    assert len(cs) >= 2
    for k, c in enumerate(cs):
        joined = " ".join(c["lines"])
        assert joined.startswith("[영수] ♪ " if k == 0 else "♪ ")        # II.7: at the beginning ...
        assert joined.endswith(" ♪") and joined.count("♪") == 2          # ... and end of each subtitle
        assert max(c["check"]["chars"]) <= 16 and c["check"]["n_lines"] <= 2
        assert not any(x in ("♪", "[영수] ♪") for x in c["lines"])        # a note is never left alone on a line
    sung = " ".join(" ".join(c["lines"]) for c in cs).replace("[영수] ", "").replace("♪", "")
    assert sung.split() == text.split()


def test_duet_has_notes_on_each_line_of_the_hyphen_cell():
    d = run([L("L1", "별이 뜨면", 30.0, 32.0, name="영희", show=True, sung=True),
             L("L2", "나는 노래해", 30.4, 32.0, name="철수", show=True, sung=True)])
    c = one(d, "L1")
    assert c["lines"] == ["- [영희] ♪ 별이 뜨면 ♪", "- [철수] ♪ 나는 노래해 ♪"]      # II.7: both are singing


# --------------------------------------------------------------------------- display punctuation (I.13, I.4)

@pytest.mark.parametrize("text, lines", [
    ("사랑해. 그게 내가 하고 싶은 말이었어.", ["사랑해, 그게 내가", "하고 싶은 말이었어"]),      # the guide's (O) form
    ("사랑해,", ["사랑해"]),
    ("이.. 일단 제가 여기...", ["이… 일단 제가 여기…"]),
    ("오늘 날씨는 아주…", ["오늘 날씨는 아주…"]),
    ("3.5 킬로미터 남았어.", ["3.5 킬로미터 남았어"]),
    ("뭐?! 진짜야?", ["뭐?! 진짜야?"]),
    ("가나다라마바사아자차카타파하가나.", ["가나다라마바사아자차카타파하가나"]),          # 16 units once the period is gone
])
def test_display_punctuation_is_tidied_after_wrapping(text, lines):
    c = one(run([L("L1", text, 10.0, 13.0)]), "L1")
    assert c["lines"] == lines
    assert bare(" ".join(c["lines"])) == bare(text)                    # the words stay


def test_text_that_is_only_punctuation_makes_no_empty_cell():
    lines = [L("L1", ".", 10.0, 10.5), L("L2", "...", 12.0, 12.5), L("L3", "응 .", 14.0, 14.5),
             L("L4", ", ,", 16.0, 16.5)]
    d = run(lines, [S("S1", 20.0, "."), S("S2", 22.0, "[쿵]."), S("S3", 14.1, "[쾅]")])
    assert {c["refs"][0]: c["lines"] for c in d["cells"]} == {"L2": ["…"], "L3": ["- 응", "- [쾅]"], "S2": ["[쿵]"]}
    assert {r["item"]: r["reason"] for r in d["no_room"]} == {"S1": "문구 없음"}


def test_sentence_ends_still_decide_where_a_line_is_cut():
    # the period is used to choose the cut and only then taken away
    text = "오늘은 정말 이상한 날이었어. 아침부터 계속 이상한 소리가 들렸거든. 그래서 나는 도서관에 가 보기로 했어."
    cs = cells_of(run([L("L1", text, 10.0, 18.0)]), "L1")
    assert [" ".join(c["lines"]).split()[-1] for c in cs] == ["날이었어", "들렸거든", "했어"]


# --------------------------------------------------------------------------- lines that overlap (audit 2-04, 7-09)

LONG = "어째서 나를 여기까지 데리고 온 건지 알고 싶은 거야"      # made up; 25 units: two lines, no hyphen cell


def test_guessed_line_gives_way_to_the_aligned_line_it_lands_in():
    # audit 7-09: a short line with a guessed time (timing voice) was put inside a long aligned line,
    # which then showed for one frame. Neither side has room, so it follows the reading time of the
    # long line.
    lines = [L("L1", "그래서 말인데", 6.0, 8.0, speaker="A", timing="asr"),
             L("L2", "네?", 8.03, 8.34, speaker="B", timing="voice"),
             L("L3", LONG, 8.0, 11.46, speaker="A", timing="asr"),
             L("L4", "아니 그게", 11.6, 12.4, speaker="B", timing="asr")]
    d = run(lines)
    long_, guess = one(d, "L3"), one(d, "L2")
    assert long_["start"] == sec(F(8.0)) and long_["check"]["violations"] == []
    assert nframes(long_["start"], long_["end"]) >= math.ceil(sum(long_["check"]["chars"]) / 14 * FPS - 1e-6)
    assert guess["start"] > long_["end"] and nframes(guess["start"], guess["end"]) >= MIN_F
    assert guess["flags"] == ["timing_estimated"] and long_["flags"] == []
    assert all(nframes(c["start"], c["end"]) >= MIN_F and not c["check"]["violations"] for c in d["cells"])
    assert d["summary"]["timing_estimated"] == 1 and d["summary"]["overlap"] == 0


def test_guessed_line_moves_to_its_side_of_the_script_when_there_is_room():
    long_ = dict(speaker="A", timing="asr")
    d = run([L("L1", "네?", 10.3, 10.6, speaker="B", timing="voice"), L("L2", LONG, 10.0, 13.5, **long_)])
    g, a = one(d, "L1"), one(d, "L2")
    assert g["end"] < a["start"] == sec(F(10.0)) and nframes(g["end"], a["start"]) == 2     # in front, as in the script
    assert nframes(g["start"], g["end"]) == MIN_F and a["end"] >= sec(F(13.5))
    d = run([L("L1", LONG, 10.0, 13.5, **long_), L("L2", "네?", 10.3, 10.6, speaker="B", timing="even")])
    a, g = one(d, "L1"), one(d, "L2")
    assert g["start"] >= sec(F(13.5) + 2) and a["end"] >= sec(F(13.5))                       # behind the whole speech
    assert g["flags"] == ["timing_estimated"] and d["summary"]["violations"] == 0


def test_overlap_that_cannot_be_solved_is_marked_and_never_one_frame():
    other = "그건 내가 어제 분명히 말했던 것 같은데 기억 안 나"     # too long for a hyphen line as well
    d = run([L("L1", LONG, 10.0, 13.5, speaker="A", timing="asr"),
             L("L2", other, 10.04, 13.0, speaker="B", timing="asr")])
    a, b = one(d, "L1"), one(d, "L2")
    assert nframes(a["start"], a["end"]) == MIN_F                      # the later line waits: 5/6 s, not 1 frame
    assert nframes(a["end"], b["start"]) == 2 and nframes(b["start"], b["end"]) >= MIN_F
    assert a["flags"] == ["overlap"] and b["flags"] == ["overlap"]
    assert d["summary"]["overlap"] == 2 and d["summary"]["flash"] == 0
    assert "초당 14자 초과" in a["check"]["violations"]                 # still reported: the writer decides


def test_a_pile_of_overlapping_lines_is_shown_one_after_the_other():
    # three long lines of one speaker that start within three frames: no hyphen cell, no guess
    k = F(10.0)
    d = run([L(f"L{n}", LONG, (k + n) / FPS, 13.0, speaker="A", timing="asr") for n in range(3)])
    assert [c["refs"] for c in d["cells"]] == [["L0"], ["L1"], ["L2"]]
    assert all(nframes(c["start"], c["end"]) >= MIN_F for c in d["cells"])       # nobody is cut to a frame
    assert all(c["flags"] == ["overlap"] for c in d["cells"]) and d["summary"]["flash"] == 0
    assert d["cells"][2]["start"] < 13.0                               # the last one still starts while it is spoken


def test_a_cell_of_a_few_frames_never_goes_out_unmarked():
    # a one-syllable line and the next line of the same speaker three frames later: nothing to
    # join, nothing to move. The cell is as short as it always was, but it now carries a flag.
    k = F(10.0)
    d = run([L("L1", "아", k / FPS, (k + 1) / FPS, speaker="A"),
             L("L2", "그게 아니라니까", (k + 3) / FPS, 12.0, speaker="A")])
    tiny = one(d, "L1")
    assert nframes(tiny["start"], tiny["end"]) < 6 and tiny["flags"] == ["flash"]
    assert "5/6초 미만" in tiny["check"]["violations"] and d["summary"]["flash"] == 1
    assert one(d, "L2")["flags"] == []


# --------------------------------------------------------------------------- shot changes again (audit 2-m3, 2-12)

def test_in_time_uses_the_cut_ahead_when_the_cut_before_is_taken():
    k = F(20.0)
    line = L("L2", "안녕하세요 여러분", (k - 7) / FPS, (k + 30) / FPS)
    cuts = [(k - 15) / FPS, k / FPS]                                   # two cuts within half a second
    assert one(run([line], cuts=cuts), "L2")["start"] == sec(k - 15)   # alone: back to the cut before (TG2)
    prev = L("L1", "가나다라", (k - 60) / FPS, (k - 10) / FPS)          # its speech takes the cut before
    d = run([prev, line], cuts=cuts)
    assert one(d, "L2")["start"] == sec(k)                             # so: on the cut ahead (TG4), not 7 frames early
    assert one(d, "L1")["end"] >= sec(k - 10)


def test_a_cell_of_dialogue_and_the_sound_that_interrupts_it_starts_on_the_cut_as_the_dialogue_does():
    # 7차 (G3-09): the speech before runs up to the cut, the line after starts 1-3 frames past it and a
    # sound in its pause makes '- 잠깐만 이쪽으로' / '- [쿵]' (④): that cell starts on the cut as the
    # dialogue cell does without the sound (TG 2), the gap before it 2 frames (TG 5; before: 3-5
    # frames, cut_in_after and short_gap_cut)
    from cboe import check
    cut = sec(278)
    for a_end, b_at in ((11.55, 11.65), (11.6, 11.62), (11.6, 11.70)):
        a = L("L1", "안녕하세요 여러분", 10.0, a_end, speaker="가람", timing="asr")
        ws = [("잠깐만", 0.0, 0.4), ("이쪽으로", 0.4, 0.9), ("그래", 1.6, 2.0), ("알았어", 2.0, 2.5)]
        text = " ".join(w for w, _, _ in ws)
        b = L("L2", text, b_at, b_at + 2.5, words=W(text, [(round(b_at + s, 3), round(b_at + e, 3)) for _, s, e in ws]),
              speaker="나래", timing="asr")
        plain = run([a, b], cuts=[cut])
        d = run([a, b], [S("S1", round(b_at + 1.0, 3), "[쿵]", t1=round(b_at + 1.3, 3))], cuts=[cut])
        mixed = next(c for c in d["cells"] if "S1" in c["refs"])
        assert mixed["refs"] == ["L2", "S1"] and mixed["lines"] == ["- 잠깐만 이쪽으로…", "- [쿵]"], d["cells"]
        assert mixed["start"] == cells_of(plain, "L2")[0]["start"] == cut, (a_end, b_at, d["cells"])
        assert F(mixed["start"]) - F(one(d, "L1")["end"]) == 2
        assert not {"cut_in_after", "short_gap_cut"} & {v["code"] for v in check.run(d, cuts=[cut])["violations"]}
    # a cell a sound starts keeps the sound's onset (BBC 5.5)
    snd = run([L("L1", "안녕하세요 여러분", 10.0, 11.55, speaker="가람", timing="asr")], [S("S1", sec(281), "[쿵]")], cuts=[cut])
    assert F(one(snd, "S1")["start"]) == 281


def test_start_before_a_cut_follows_the_same_rule_at_every_frame_rate():
    for fps in (24.0, 30.0, 60.0):                                     # 0.417 s before the cut = 10 frames at 24 fps
        d = place([L("L1", "안녕하세요 여러분", 20.0 - 0.417, 21.0)], [], [20.0], fps)
        assert d["cells"][0]["start"] == pytest.approx(19.5, abs=1e-3), fps   # TG4: half a second before the cut


# --------------------------------------------------------------------------- flags, position, lines without a time

def test_cells_of_guessed_lines_are_flagged_and_counted():
    d = run([L("L1", "하나", 10.0, 10.5, timing="asr"), L("L2", "둘", 12.0, 12.5, timing="voice"),
             L("L3", "셋", 14.0, 14.5, timing="even"), L("L4", "넷", 16.0, 16.5, timing="script"),
             L("L5", "다섯", 18.0, 18.5, status="unheard"), L("L6", "여섯", 20.0, 20.5)])
    guess = ["timing_estimated"]
    assert {c["refs"][0]: c["flags"] for c in d["cells"]} == {
        "L1": [], "L2": guess, "L3": guess, "L4": [], "L5": guess, "L6": []}
    assert d["summary"]["timing_estimated"] == 3


def test_cells_carry_their_position():
    d = run([L("L1", "아래", 10.0, 10.5), L("L2", "위", 12.0, 12.5, pos="top")],
            [dict(S("S1", 14.0, "[쿵]"), pos="top"), S("S2", 16.0, "[쾅]")])
    assert {c["refs"][0]: c["pos"] for c in d["cells"]} == {"L1": "bottom", "L2": "top", "S1": "top", "S2": "bottom"}


def test_a_top_position_goes_with_the_cell_whatever_shares_it():
    # 6th round (약속 가): the review layer passes pos "top" for lines and sounds; one part on top puts
    # the cell on top, in a cell of its own, with a line, in a hyphen cell and in a joined cell
    line = L("L1", "나도 갈래", 10.0, 11.0, speaker="A", timing="asr")

    def top(s):
        return dict(s, pos="top")
    c = one(run([line], [top(S("S1", 10.0, "[쿵]"))]), "L1")                    # ② another source
    assert c["lines"] == ["- [쿵]", "- 나도 갈래"] and sorted(c["refs"]) == ["L1", "S1"] and c["pos"] == "top"
    c = one(run([line], [top(S("S1", 10.0, "[한숨]", src="L1"))]), "L1")       # ③ the speaker's own
    assert c["lines"] == ["[한숨] 나도 갈래"] and c["pos"] == "top"
    assert one(run([line], [S("S1", 10.0, "[쿵]")]), "L1")["pos"] == "bottom"
    cs = cells_of(run([L("L1", TEXT7, 10.0, 13.0, timing="asr")], [top(S("S1", 11.6, "[쾅]"))]), "L1")
    # ④ the part with it: here the next word starts 3 frames after the sound (even word times), so the
    # part after it, which starts at the sound (refix f1: II.9's order would take the sound's words off
    # the screen as it is heard) ...
    assert [(c["pos"], "S1" in c["refs"]) for c in cs] == [("bottom", False), ("top", True)]
    # ... and the part before it when the speech stops for the sound (II.9's order, 6th round)
    cs = cells_of(run([L("L1", GUIDE, 10.0, 13.0, words=W(GUIDE, GUIDE_T), timing="asr")],
                      [top(S("S1", 11.3, "[첨벙!]"))]), "L1")
    assert [(c["pos"], "S1" in c["refs"]) for c in cs] == [("top", True), ("bottom", False)]
    pair = [L("L1", "뭐?", 10.0, 10.2, speaker="빌"), L("L2", "응", 10.45, 10.6, speaker="세바스찬", pos="top")]
    c = one(run(pair), "L1")
    assert c["refs"] == ["L1", "L2"] and c["lines"] == ["- 뭐?", "- 응"] and c["pos"] == "top"
    joined = [L("L1", "어디 가?", 10.0, 10.5, speaker="빌", pos="top"), L("L2", "집에", 12.0, 12.4, speaker="빌", join=True)]
    c = one(run(joined), "L1")
    assert c["refs"] == ["L1", "L2"] and c["lines"] == ["어디 가? 집에"] and c["pos"] == "top"


def test_a_manner_of_speech_goes_in_the_label_and_counts_in_the_width():
    # 6th round (약속 가): the manner the writer picks comes in label_extra. II.8 "[영희/속삭임]",
    # "[영희/속마음/영어]": name, source, manner, the language last; without a shown name the label
    # alone (style.speaker_label), and it counts in the 16 units of the line (II.2)
    def lines_of(text="나도 갈래", **kw):
        return [c["lines"] for c in cells_of(run([L("L1", text, 10.0, 11.0, timing="asr", **kw)]), "L1")]
    assert lines_of(name="하늘", show=False, label_extra=["속삭이며"]) == [["[속삭이며] 나도 갈래"]]
    assert lines_of(name="하늘", show=True, label_extra=["속삭이며"]) == [["[하늘/속삭이며] 나도 갈래"]]
    assert lines_of(name="영희", show=True, label_extra=["속마음", "속삭임"], language="영어") == \
        [["[영희/속마음/속삭임/영어] 나도", "갈래"]]                                  # 12.5 units of label
    assert lines_of("오늘은 정말 고마웠어요") == [["오늘은 정말 고마웠어요"]]              # 11 units: one line
    assert lines_of("오늘은 정말 고마웠어요", label_extra=["속삭이며"]) == [["[속삭이며] 오늘은", "정말 고마웠어요"]]
    # in a hyphen cell; on a long line only its first cell has it (the language goes on every cell, II.6)
    pair = [L("L1", "뭐?", 10.0, 10.2, speaker="빌"), L("L2", "응", 10.45, 10.6, speaker="세바스찬", label_extra=["속삭이며"])]
    assert one(run(pair), "L1")["lines"] == ["- 뭐?", "- [속삭이며] 응"]
    text = "오늘은 정말 이상한 날이었어. 아침부터 계속 이상한 소리가 들렸거든. 그래서 도서관에 가 보기로 했어"
    cs = cells_of(run([L("L1", text, 10.0, 18.0, label_extra=["속삭이며"], language="영어", timing="asr")]), "L1")
    assert len(cs) >= 2 and cs[0]["lines"][0].startswith("[속삭이며/영어] ")
    assert all(c["lines"][0].startswith("[영어] ") and "속삭이며" not in " ".join(c["lines"]) for c in cs[1:])
    assert all(units(x) <= 16 for c in cs for x in c["lines"])


def test_lines_without_a_time_are_skipped():
    gone = {"id": "L2", "text": "영상에 없는 장면의 대사", "start": None, "end": None, "words": [],
            "status": "absent", "guess": [11.0, 12.0], "show_name": True, "name": "영희"}
    words = [{"w": "처리", "s": 30.0, "e": 30.3}, {"w": "구간", "s": 30.3, "e": 30.6}, {"w": "밖", "s": 30.6, "e": 31.0}]
    outside = {"id": "L3", "text": "처리 구간 밖", "start": None, "end": None, "status": "out_of_range", "words": words}
    d = run([L("L1", "안녕", 10.0, 10.5), gone, outside])
    assert [c["refs"] for c in d["cells"]] == [["L1"]]


def test_children_profile_reads_eleven_units_a_second():
    from cboe import style
    text = "가나다라마 바사아자차 카타파하가 나다"                       # 18 units on two lines
    child = style.get("children").settings()
    d = place([L("L1", text, 20.0, 21.0)], [], [], FPS, settings=child)
    c = d["cells"][0]
    adult = run([L("L1", text, 20.0, 21.0)])
    assert d["profile"] == "children" and adult["profile"] == "adult"
    assert sum(c["check"]["chars"]) == 18.0
    assert nframes(c["start"], c["end"]) == math.ceil(18 / 11 * FPS - 1e-6) == 40  # 1.67 s to read at 11 units/s
    assert adult["cells"][0]["end"] == sec(F(21.0) + 12) < c["end"]                # adults: speech + 0.5 s is enough
    tight = place([L("L1", text, 20.0, 21.0), L("L2", "응", 21.6, 22.0)], [], [], FPS, settings=child)
    assert "초당 11자 초과" in tight["cells"][0]["check"]["violations"]


# --------------------------------------------------------------------------- the sample work folder

def test_sample_work_folder_recompute(tmp_path):
    src = ROOT / "work" / "sample"
    if not (src / "dialogue.json").exists():
        pytest.skip("no work/sample")
    from cboe.state import Project
    work = tmp_path / "sample"
    shutil.copytree(src, work)
    proj = Project(work)
    d = proj.recompute()
    assert json.loads((work / "draft.json").read_text(encoding="utf-8")) == d
    check_invariants(d, proj.placement_inputs()[1])
    assert d["summary"]["violations"] == 0 and d["no_room"] == []
    book, thunder, bang = one(d, "S0003"), one(d, "S0004"), one(d, "S0005")
    assert book["lines"] == ["[쿵, 책 떨어지는 소리]"]
    assert book["check"]["dur"] == pytest.approx(1.63, abs=0.01)        # memo 7: 1.63 s, 6.8 units/s
    assert book["check"]["cps"] == pytest.approx(6.8, abs=0.1)
    assert nframes(book["end"], thunder["start"]) == 2
    assert thunder["refs"] == ["L0007", "S0004"] and thunder["lines"] == ["- 으아아아아", "- [천둥 소리]"]
    assert bang["refs"] == ["S0005", "L0008"] and bang["lines"] == ["- [쿵]", "- 으아아아아"]
    named = [c for c in d["cells"] if c["lines"][0].startswith("[BJ 옴뫄]")]
    assert [c["refs"] for c in named] == [[f"L000{k}"] for k in range(1, 7)]
    assert not any("♬" in x or x.startswith("(") for c in d["cells"] for x in c["lines"])   # today's notation


# --------------------------------------------------------------------------- review layer (2026-11 audit, round C)

def test_a_sound_whose_onset_is_a_guess_is_flagged():
    # a sound the script writes down that the detector did not find (origin 'script', estimated)
    est = dict(S("C0001", 20.0, "[쾅]"), estimated=True)
    d = run([], [est, S("S2", 30.0, "[쿵]")])
    assert one(d, "C0001")["flags"] == ["timing_estimated"] and one(d, "S2")["flags"] == []
    assert d["summary"]["timing_estimated"] == 1
    moved = dict(est, move_to=21.0)                                    # the writer put it there: no guess any more
    assert one(run([], [moved]), "C0001")["flags"] == []
    d = run([L("L1", "가자", 40.0, 40.6, timing="asr")], [dict(S("C0002", 40.1, "[쾅]"), estimated=True)])
    c = one(d, "C0002")
    assert c["refs"] == ["L1", "C0002"] and c["flags"] == ["timing_estimated"]   # the shared cell says so too


def test_the_last_cell_of_a_scene_does_not_run_over_the_scene_change():
    cut = 10.5
    a = L("L1", "가자", 10.0, 10.3, speaker="A", timing="asr")
    b = L("L2", "어서 와 기다렸어", 13.0, 14.5, speaker="B", timing="asr")
    plain = run([a, b], cuts=[cut])
    assert one(plain, "L1")["end"] >= sec(F(cut) + 12)                # no scenes: half a second into the next shot
    same = run([dict(a, scene_id="SC001"), dict(b, scene_id="SC001")], cuts=[cut])
    assert one(same, "L1")["end"] == one(plain, "L1")["end"]           # a shot change inside one scene: as before
    longer = L("L1", "그러니까 오늘은 여기까지만 하자", 10.0, 10.9, speaker="A", timing="asr")
    cut2 = 11.0
    without = one(run([longer, b], cuts=[cut2]), "L1")
    assert without["end"] > cut2                                       # without scenes it reads on over the cut
    d = run([dict(longer, scene_id="SC001"), dict(b, scene_id="SC002")], cuts=[cut2])
    # 4th review: this case used to end 2 frames before the scene change at 16.7 units a second with no
    # note. Ending there breaks the reading speed (II.3, an obligation), which comes before the scene
    # rule (TG 4, weighed against the others): it reads on as without scenes and says why
    c = one(d, "L1")
    assert (c["start"], c["end"]) == (without["start"], without["end"]) and c["check"]["violations"] == []
    assert c["check"]["notes"] == ["장면이 바뀌는 화면 전환을 넘음 · 그 앞에서 끝내면 초당 14자를 넘음"]
    assert one(d, "L2") == one(run([longer, b], cuts=[cut2]), "L2")
    # a cell that can end 2 frames before the scene change and still be read does (TG 4)
    short = L("L1", "그래 여기까지", 10.0, 10.9, speaker="A", timing="asr")
    nxt = L("L2", "어서 와", 11.2, 12.0, speaker="B", timing="asr")
    c = one(run([dict(short, scene_id="SC001"), dict(nxt, scene_id="SC002")], cuts=[cut2]), "L1")
    assert c["end"] == sec(F(cut2) - 2) and "notes" not in c["check"] and c["check"]["violations"] == []
    # ending before the change would leave it under 5/6 s: it goes over as without scenes, and says so
    d = run([dict(a, scene_id="SC001"), dict(b, scene_id="SC002")], cuts=[cut])
    c = one(d, "L1")
    assert c["end"] == one(plain, "L1")["end"] and nframes(c["start"], c["end"]) >= MIN_F
    assert c["check"]["notes"] == ["장면이 바뀌는 화면 전환을 넘음 · 그 앞에서 끝내면 5/6초가 안 됨"]
    # the speech goes on over the cut (it starts before it and belongs to the next scene): allowed
    over = L("L1", "가자 어서 가자", 10.0, 10.9, speaker="A", timing="asr", scene_id="SC001")
    d = run([over, dict(b, scene_id="SC002")], cuts=[cut])
    assert one(d, "L1")["end"] > cut and "notes" not in one(d, "L1")["check"]


def test_a_cell_that_starts_on_the_scene_change_keeps_its_five_sixths():
    # L2 is spoken over L1 and waits for it (③): its cell starts on the shot change itself, which is
    # no scene change it could cross; it gets its 5/6 s as without scenes (no one-frame cell)
    def lines(scenes):
        sc = (lambda k: {"scene_id": k}) if scenes else (lambda k: {})
        return [L("L1", "오늘은 정말로 그러니까 우리 같이 가자고 했잖아", 10.0, 12.0, speaker="A", timing="asr", **sc("SC001")),
                L("L2", "알았어", 10.3, 10.6, speaker="B", timing="asr", **sc("SC001")),
                L("L3", "다음 장면의 말", 15.0, 16.0, speaker="C", timing="asr", **sc("SC002"))]
    cut = sec(F(10.0) + MIN_F + 2)                                      # where L2 waits to
    plain, d = run(lines(False), cuts=[cut]), run(lines(True), cuts=[cut])
    c = one(d, "L2")
    assert c["start"] == cut and (c["start"], c["end"]) == (one(plain, "L2")["start"], one(plain, "L2")["end"])
    assert nframes(c["start"], c["end"]) >= MIN_F and "flash" not in c["flags"]


def test_closing_a_small_gap_stops_at_the_scene_change():
    a = L("L1", "그럼 내일 보자", 10.0, 11.5, speaker="A", timing="asr")
    b = L("L2", "응 잘 가", 12.3, 13.0, speaker="B", timing="asr")
    cut = 12.0
    plain = run([a, b], cuts=[cut])
    d = run([dict(a, scene_id="SC001"), dict(b, scene_id="SC002")], cuts=[cut])
    assert one(d, "L1")["end"] <= sec(F(cut) - 2) and one(d, "L1")["end"] <= one(plain, "L1")["end"]


def test_two_lines_of_different_scenes_never_share_a_hyphen_cell():
    lines = [L("L1", "뭐?", 10.0, 10.2, speaker="빌", name="빌", show=True, scene_id="SC001"),
             L("L2", "응", 10.45, 10.6, speaker="세바스찬", name="세바스찬", show=True, scene_id="SC002"),
             L("L3", "그래 가자", 11.1, 12.0, speaker="빌", scene_id="SC002")]
    d = run(lines)
    # L1 and L2 never share a cell. L2 and L3 are of one scene: since the 6th round they do (before,
    # L2 was left 13 frames, '5/6초 미만', because L3 was not short)
    assert one(d, "L1")["refs"] == ["L1"] and one(d, "L2")["refs"] == ["L2", "L3"]
    together = run([dict(ln, scene_id="SC001") for ln in lines])
    assert one(together, "L1")["refs"] == ["L1", "L2"]


def test_one_person_under_two_labels_is_still_one_speaker():
    # before and after the name is told: '[남자]' then '[가람]' are one person — no two-speaker cell
    lines = [L("L1", "뭐?", 10.0, 10.2, speaker="가람", name="남자", show=True, person="가람"),
             L("L2", "응", 10.45, 10.6, speaker="가람", name="가람", show=True, person="가람"),
             L("L3", "그래 가자", 11.1, 12.0, speaker="빌")]
    assert len(cells_of(run(lines), "L1")) == 1 and one(run(lines), "L1")["refs"] == ["L1"]
    two = [dict(lines[0], person="갑"), dict(lines[1], person="을", name="남자"), lines[2]]
    assert one(run(two), "L1")["refs"] == ["L1", "L2"]                 # two people: the label text does not decide


TEXT7 = "오늘은 여기까지 하자 내일 다시 오면 돼"          # made up; 19 units, 7 eojeols: one cell of two lines


def test_writer_breaks_cut_the_line_where_the_writer_said():
    line = L("L1", TEXT7, 10.0, 13.0, timing="asr")
    assert len(cells_of(run([line]), "L1")) == 1
    d = run([dict(line, breaks=[3])])
    cs = cells_of(d, "L1")
    assert [c["lines"] for c in cs] == [["오늘은 여기까지 하자"], ["내일 다시 오면 돼"]]
    assert cs[1]["start"] == sec(F(10.0 + 3 * 3.0 / 7)) and "notes" not in cs[0]["check"]
    assert cells_of(run([dict(line, breaks=[0, 7, 99, "x"])]), "L1")[0]["lines"] == run([line])["cells"][0]["lines"]
    # a piece the writer asked for that does not fit is cut further, and the cell says so
    long_ = L("L2", LONG + " " + TEXT7, 20.0, 26.0, timing="asr")
    d = run([dict(long_, breaks=[len(LONG.split()) + 7 - 1])])
    cs = cells_of(d, "L2")
    assert len(cs) >= 3 and cs[-1]["lines"] == ["돼"]
    assert any("작가가 정한 칸이 한도를 넘어 더 나눔" in c["check"].get("notes", []) for c in cs)


def test_keep_holds_the_line_against_a_sound_and_says_when_it_cannot():
    line = L("L1", TEXT7, 10.0, 13.0, timing="asr")
    snd = S("S1", 11.6, "[쾅]")
    assert len(cells_of(run([line], [snd]), "L1")) == 2                 # ④ the sound splits the cell
    d = run([dict(line, keep=True)], [snd])
    assert len(cells_of(d, "L1")) == 1 and "S1" not in one(d, "L1")["refs"]
    nr = next(e for e in d["no_room"] if e["item"] == "S1")
    assert "작가가 한 칸에 두기로 정한 대사" in nr["reason"]
    too_long = dict(L("L2", LONG + " " + LONG, 20.0, 26.0, timing="asr"), keep=True)   # over 2 x 16 units
    cs = cells_of(run([too_long]), "L2")
    assert len(cs) >= 2 and all(any(n.startswith("한 칸에 두기") for n in c["check"]["notes"]) for c in cs)


# the writer's join (6th round, audit 2-02, 7-03): "this line in one cell with the line before"

def test_join_puts_two_speakers_in_one_hyphen_cell_where_the_automatic_rule_would_not():
    a = L("L1", "어디 가는 거야?", 10.0, 10.8, speaker="가람", name="가람", show=True, timing="asr")
    b = L("L2", "집에 간다니까", 12.0, 12.6, speaker="나래", timing="asr")
    assert [c["refs"] for c in run([a, b])["cells"]] == [["L1"], ["L2"]]       # 1.2 s apart: two cells
    d = run([a, dict(b, join=True)])
    c = one(d, "L1")
    assert c["refs"] == ["L1", "L2"] and c["lines"] == ["- [가람] 어디 가는 거야?", "- 집에 간다니까"]
    assert c["start"] == sec(F(10.0)) and c["end"] == sec(F(12.6) + 12)       # both speeches, + 0.5 s
    assert c["check"]["violations"] == [] and "notes" not in c["check"] and len(d["cells"]) == 1
    # the join of the first line placed is no join at all; the line before is the one placed before it
    assert run([dict(a, join=True), b])["cells"] == run([a, b])["cells"]
    absent = {"id": "L9", "text": "어디서 들리는 말", "status": "absent"}
    assert one(run([a, absent, dict(b, join=True)]), "L2")["refs"] == ["L1", "L2"]


def test_join_of_one_speaker_makes_one_cell_of_the_two_texts():
    a = L("L1", "그러니까 오늘은", 10.0, 10.8, speaker="가람", name="가람", show=True, timing="asr")
    b = L("L2", "여기까지만 하고 내일 보자", 11.6, 12.8, speaker="가람", name="가람", show=True, timing="asr")
    assert len(run([a, b])["cells"]) == 2
    d = run([a, dict(b, join=True)], [S("S1", 20.0, "[쾅]")])
    c = one(d, "L1")
    assert c["refs"] == ["L1", "L2"] and c["kind"] == "dialogue"
    assert c["lines"] == ["[가람] 그러니까 오늘은", "여기까지만 하고 내일 보자"]       # 2 lines, the first label only
    assert all(units(x) <= 16 for x in c["lines"])
    assert c["start"] == sec(F(10.0)) and c["end"] == sec(F(12.8) + 12) and c["check"]["violations"] == []
    # the words go on with their own times: a sound in the middle of the second text cannot split the
    # cell back into the two lines (the writer made it one cell), and says so
    words = a["words"] + b["words"]
    d = run([a, dict(b, join=True)], [S("S1", words[3]["s"], "[쾅]")])
    assert one(d, "L1")["refs"] == ["L1", "L2"]
    assert any("한 칸에 두기로 정한" in e["reason"] for e in d["no_room"] if e["item"] == "S1")


def test_join_that_cannot_be_made_says_why_on_the_line():
    def note(d, ref):
        return [n for n in one(d, ref)["check"].get("notes", []) if n.startswith("앞 줄과 한 칸으로")]
    a = L("L1", "그러니까 오늘은 여기까지만", 10.0, 11.0, speaker="가람", timing="asr")
    long_ = L("L2", "하고 내일 아침 일찍 다시 만나서 이야기하자", 11.6, 13.0, speaker="가람", timing="asr")
    d = run([a, dict(long_, join=True)])
    assert len(d["cells"]) == 2 and note(d, "L2") == ["앞 줄과 한 칸으로 묶지 못함 · 너무 김: 2줄 16자 초과"]
    late = L("L2", "응", 17.5, 17.8, speaker="가람", timing="asr")                  # 7.8 s after L1 begins
    assert note(run([a, dict(late, join=True)]), "L2") == ["앞 줄과 한 칸으로 묶지 못함 · 7초 초과"]
    wide = L("L2", "아니 나는 내일 아침에는 절대 못 가", 11.6, 13.0, speaker="나래", timing="asr")   # 18 units as '- …'
    assert note(run([a, dict(wide, join=True)]), "L2") == ["앞 줄과 한 칸으로 묶지 못함 · 너무 김: 하이픈 줄 16자 초과"]
    # the line before is already in one cell with another line: two lines a cell
    b = L("L2", "왜?", 11.6, 11.9, speaker="나래", timing="asr")
    c = L("L3", "그냥", 12.5, 12.8, speaker="다솜", timing="asr")
    d = run([a, dict(b, join=True), dict(c, join=True)])
    assert one(d, "L1")["refs"] == ["L1", "L2"] and note(d, "L3") == ["앞 줄과 한 칸으로 묶지 못함 · 앞 줄이 이미 다른 줄과 한 칸"]
    # no note on a line whose join was made, nor on the cells of the lines around it
    assert all(not any(n.startswith("앞 줄과") for n in x["check"].get("notes", [])) for x in d["cells"] if "L3" not in x["refs"])
    # another line spoken between the two (out of script order) has its cell there
    between = L("L3", "뭐라고?", 11.2, 11.4, speaker="다솜", timing="asr")
    d = run([a, dict(L("L2", "응", 12.5, 12.8, speaker="나래", timing="asr"), join=True), between])
    assert note(d, "L2") == ["앞 줄과 한 칸으로 묶지 못함 · 사이에 다른 줄의 칸"]
    # a line the writer cut with breaks stays cut
    cut = L("L2", "응 그래 알았어", 11.6, 12.6, speaker="나래", timing="asr", breaks=[1], join=True)
    cs = cells_of(run([a, cut]), "L2")
    assert [c["lines"] for c in cs] == [["응"], ["그래 알았어"]]
    assert all("앞 줄과 한 칸으로 묶지 못함 · 작가가 정한 끊을 자리로 나뉜 줄" in c["check"]["notes"] for c in cs)


def test_a_joined_cell_gets_the_checks_of_any_cell():
    from cboe import check
    a = L("L1", "가나다라마바사아자차카타파", 10.0, 10.3, speaker="가람", timing="asr")
    b = L("L2", "하가나다라마바사아자차카타", 10.9, 11.2, speaker="나래", timing="asr")
    nxt = L("L3", "그래", 11.45, 11.7, speaker="가람", timing="asr")
    d = run([a, dict(b, join=True), nxt])
    c = one(d, "L1")
    assert c["refs"] == ["L1", "L2"] and "초당 14자 초과" in c["check"]["violations"]   # 28 units in 1.4 s
    assert "reading_speed" in [v["code"] for v in check.run(d)["violations"] if v["cell"] == c["id"]]
    # and the shot-change rules: a cut just after the speech of the two ends the cell before it (TG 1)
    short = [L("L1", "어디 가?", 10.0, 10.3, speaker="가람", timing="asr"),
             L("L2", "집에", 10.9, 11.2, speaker="나래", timing="asr", join=True)]
    assert one(run(short), "L1")["end"] == sec(F(11.2) + 12)
    assert one(run(short, cuts=[11.5]), "L1")["end"] == sec(F(11.5) - 2)


def test_a_speaker_label_is_not_left_alone_on_the_first_line():
    long_name = L("L1", "오늘은 정말 고마웠어", 10.0, 12.0, name="가나다라마바", show=True)
    c = one(run([long_name]), "L1")
    assert c["lines"] == ["[가나다라마바] 오늘은", "정말 고마웠어"]
    noted = L("L1", "오늘은 정말 고마웠어", 10.0, 12.0, name="가나다라마바", show=True)
    d = run([noted], [S("S1", 10.0, "[한숨]", src="L1")])                 # ③ the speaker's own sound in front
    # 6th round: the cell of two lines is cut in two for the sound (before: the sound had no room)
    first = cells_of(d, "L1")[0]
    assert first["refs"] == ["L1", "S1"] and not re.fullmatch(r"(\[[^\]]*\]\s?)+", first["lines"][0])
    assert " ".join(first["lines"]).startswith("[가나다라마바] [한숨] 오늘은")
    # only when the 16 units leave no other way: a label of 11 units and a first word of 5
    longer = L("L1", "고마웠어요 정말로", 10.0, 12.0, name="가나다라마바사아자차", show=True)
    assert one(run([longer]), "L1")["lines"] == ["[가나다라마바사아자차]", "고마웠어요 정말로"]


@pytest.mark.parametrize("text, first", [("잠깐만요, 지금 그러니까", "[하늘/영어] 잠깐만요"),
                                         ("저기요, 혹시 여기가 맞나요", "[하늘/영어] 저기요")])
def test_a_comma_after_the_first_word_does_not_leave_the_label_alone(text, first):
    # the comma goes at the end of the first line (I.13): the same words, so the label keeps its word
    d = run([L("L1", text, 10.0, 13.0, name="하늘", show=True, language="영어", timing="asr")])
    lines = one(d, "L1")["lines"]
    assert lines[0] == first and bare(" ".join(lines)) == bare(f"[하늘/영어] {text}")


def test_a_label_kept_off_a_line_of_its_own_takes_the_note_with_its_lyric():
    # a sung line: the word next to the label is the opening note, which may not stay behind without
    # its lyric (II.7): the label keeps the note and the first word of the lyric together
    sung = L("L1", "그리워했었는데 너를", 10.0, 13.0, name="세바스찬", show=True, sung=True)
    assert one(run([sung]), "L1")["lines"] == ["[세바스찬] ♪ 그리워했었는데", "너를 ♪"]
    # no room for the lyric's first word beside the label: the label alone, never '[이름] ♪' / '가사 ♪'
    wide = L("L1", "기다리고있었죠", 10.0, 13.0, name="세바스찬", show=True, sung=True, language="영어")
    assert one(run([wide]), "L1")["lines"] == ["[세바스찬/영어]", "♪ 기다리고있었죠 ♪"]


def test_a_guessed_line_squeezed_between_two_heard_lines_gets_no_cell():
    a = L("L1", "그래서 내가 말했잖아", 10.0, 11.6, speaker="A", timing="asr")
    g = L("L2", "그게 무슨 소리야 지금 당장 설명해 봐", 11.75, 11.9, speaker="B", timing="voice", status="unheard")
    b = L("L3", "됐어 그만해", 12.2, 13.2, speaker="A", timing="asr")
    d = place([a, g, b], [], [], FPS)
    assert [c["refs"] for c in d["cells"]] == [["L1"], ["L3"]]
    nr = d["no_room"]
    assert len(nr) == 1 and (nr[0]["item"], nr[0]["line"], nr[0]["kind"]) == ("L2", "L2", "dialogue")
    assert nr[0]["reason"].startswith("시각 추정 대사") and "0.46초" in nr[0]["reason"]   # 11 frames
    assert d["summary"]["lines_no_room"] == 1 and d["summary"]["flash"] == 0
    heard = place([a, dict(g, timing="asr", status="ok"), b], [], [], FPS)   # a line the ASR matched: as before
    assert len(heard["cells"]) == 3 and heard["no_room"] == []
    # room in the gap: the guessed line moves inside it instead
    g2 = L("L2", "그게 무슨 소리야", 12.9, 13.0, speaker="B", timing="even")
    a2 = L("L1", "그래서 내가 말했잖아", 10.0, 11.0, speaker="A", timing="asr")
    b2 = L("L3", "됐어 그만해", 13.2, 14.2, speaker="A", timing="asr")
    d = place([a2, g2, b2], [], [], FPS)
    c = one(d, "L2")
    assert c["start"] < 12.9 and nframes(c["start"], c["end"]) >= MIN_F and c["flags"] == ["timing_estimated"]
    assert nframes(c["end"], one(d, "L3")["start"]) >= 2 and d["no_room"] == []


LONG_GUESS = ("그러니까 내가 어제 시장에 갔는데 거기서 오랜만에 옛날 친구를 만나서 같이 밥도 먹고 "
              "차도 마시고 한참을 이야기하다가 늦게 들어왔어")      # made up; four cells or so


def test_a_long_guessed_line_is_spread_over_the_gap_it_has():
    # its pieces are squeezed against each other (a guessed 2.2 s for 66 units), not between other
    # lines: there are almost ten seconds before the next line, so the pieces are laid out there
    a = L("L1", "어디 갔다 왔어", 0.0, 2.0, speaker="A", timing="asr")
    g = L("L2", LONG_GUESS, 2.2, 4.4, speaker="B", timing="even", status="unheard")
    b = L("L3", "그랬구나", 12.0, 13.0, speaker="A", timing="asr")
    d = run([a, g, b])
    cs = cells_of(d, "L2")
    assert d["no_room"] == [] and len(cs) >= 3
    assert all(nframes(c["start"], c["end"]) >= MIN_F and c["flags"] == ["timing_estimated"] for c in cs)
    assert all(c["check"]["cps"] <= 14 for c in cs)                     # room for the reading time too
    assert bare(" ".join(x for c in cs for x in c["lines"])) == bare(LONG_GUESS)
    assert cs[0]["start"] == sec(F(2.2)) and one(d, "L3")["start"] == sec(F(12.0))


def test_a_long_guessed_line_without_room_for_its_cells_goes_to_no_room_with_the_real_gap():
    a = L("L1", "어디 갔다 왔어", 0.0, 2.0, speaker="A", timing="asr")
    g = L("L2", LONG_GUESS, 2.2, 4.4, speaker="B", timing="even", status="unheard")
    b = L("L3", "그랬구나", 4.6, 5.6, speaker="A", timing="asr")
    d = place([a, g, b], [], [], FPS)                                  # no_room has a line: no sound invariants
    assert cells_of(d, "L2") == [] and [c["refs"] for c in d["cells"]] == [["L1"], ["L3"]]
    (nr,) = d["no_room"]
    k = int(re.search(r"칸 (\d+)개", nr["reason"]).group(1))
    gap = float(re.search(r"빈틈 ([\d.]+)초", nr["reason"]).group(1))
    assert nr["line"] == "L2" and k >= 3 and gap < k * 5 / 6
    assert gap >= 4.6 - 2.0 - 0.2                                       # the gap between L1 and L3, not between its own pieces


# --------------------------------------------------------------------------- 4th review (made-up text)

def W(text, times):
    return [{"w": w, "s": s, "e": e} for w, (s, e) in zip(text.split(), times)]


SPLIT13 = "가나다라 마바사아 자차카 타파하 그래서 내가 어제 분명히 말했잖아 거기는 가지 말라고 정말로"
SPLIT13_T = [(10.00, 10.15), (10.15, 10.30), (10.30, 10.45), (10.45, 10.60), (10.60, 10.90), (10.90, 11.20),
             (11.20, 11.50), (11.50, 11.90), (11.90, 12.40), (12.40, 12.80), (12.80, 13.10), (13.10, 13.50),
             (13.50, 14.00)]


def test_a_line_is_cut_where_every_piece_can_be_read():
    # 4th review: the cut used to leave a first piece of half a second at 22 units a second although
    # cutting it in two after '말했잖아' breaks no limit (2.3 s and 2.1 s)
    d = run([L("L1", SPLIT13, 10.0, 14.0, words=W(SPLIT13, SPLIT13_T), timing="asr")])
    cs = cells_of(d, "L1")
    assert all(c["check"]["violations"] == [] for c in cs), [(c["lines"], c["check"]["violations"]) for c in cs]
    assert bare(" ".join(" ".join(c["lines"]) for c in cs)) == bare(SPLIT13)
    # '아…' of an eighth of a second ends a sentence, but a cell of it cannot be read: not cut there
    toks = "아… 그러니까 내가 어제 시장에 가서 오랜만에 옛날 친구를 만났는데 말이야".split()
    times = [(20.0, 20.125)] + [(round(20.25 + 0.32 * k, 3), round(20.55 + 0.32 * k, 3)) for k in range(len(toks) - 1)]
    ln = L("L2", " ".join(toks), 20.0, times[-1][1], words=W(" ".join(toks), times), timing="asr",
           name="경찰1", show=True, label_extra=["속마음"])
    cs = cells_of(run([ln]), "L2")
    assert len(cs) == 2 and cs[0]["lines"][0] == "[경찰1/속마음] 아…" and len(cs[0]["lines"]) == 2
    assert all(c["check"]["violations"] == [] and "flash" not in c["flags"] for c in cs)


PAUSED = "잠깐만 기다려 그래 알았어"                    # made up; 12 units, one line: a 3 s pause after '기다려'
PAUSED_T = [(10.0, 10.4), (10.4, 10.9), (13.9, 14.2), (14.2, 14.79)]


def test_a_pause_of_two_seconds_cuts_the_line_even_when_it_fits():
    # audit 2-03: the line was one cell of 5.29 s, '그래 알았어' on screen 3 s before it is said
    # (TG "Subtitles should be in sync with both the image and the audio", "Avoid spoilers")
    line = L("L1", PAUSED, 10.0, 14.79, words=W(PAUSED, PAUSED_T), timing="asr", name="하늘", show=True,
             language="영어")
    cs = cells_of(run([line]), "L1")
    assert [c["lines"] for c in cs] == [["[하늘/영어] 잠깐만 기다려"], ["[영어] 그래 알았어"]]   # name once, language on each
    assert cs[0]["start"] == sec(F(10.0)) and cs[0]["end"] == sec(F(10.9) + 12)
    assert cs[1]["start"] == sec(F(13.9)) and cs[1]["end"] == sec(F(14.79) + 12)
    assert all(c["check"]["violations"] == [] for c in cs)
    # a pause under 2 s is no cut; nor is one in a line whose time is a guess (its pauses are not known),
    # nor where the writer chose: the writer's cut points, or one cell
    short = [(10.0, 10.4), (10.4, 10.9), (12.85, 13.2), (13.2, 13.79)]
    assert len(cells_of(run([L("L1", PAUSED, 10.0, 13.79, words=W(PAUSED, short), timing="asr")]), "L1")) == 1
    bare_line = L("L1", PAUSED, 10.0, 14.79, words=W(PAUSED, PAUSED_T), timing="asr")
    for more in (dict(timing="voice", status="unheard"), dict(keep=True)):
        assert len(cells_of(run([dict(bare_line, **more)]), "L1")) == 1, more
    cs = cells_of(run([dict(bare_line, breaks=[3])]), "L1")
    assert [c["lines"] for c in cs] == [["잠깐만 기다려 그래"], ["알았어"]] and "notes" not in cs[0]["check"]


def test_a_pause_is_no_cut_where_a_piece_next_to_it_could_not_be_read():
    # 5/6 s and the reading speed are musts (General Requirements 1, II.3), the sync with the audio a
    # should: the piece after the pause would have 13 frames before the next line, so no cut there
    times = [(10.0, 10.4), (10.4, 10.9), (13.9, 14.1), (14.1, 14.3)]
    line = L("L1", PAUSED, 10.0, 14.3, words=W(PAUSED, times), timing="asr", speaker="A")
    nxt = L("L2", "응 그래", 14.5, 15.2, timing="asr", speaker="B")
    d = run([line, nxt])
    assert len(cells_of(d, "L1")) == 1 and cells_of(d, "L2")
    assert len(cells_of(run([line, dict(nxt, start=20.0, end=20.7, words="even")]), "L1")) == 2   # room: cut
    # another line spoken over the piece after the pause: the rules for lines that overlap decide, as before
    over = L("L2", "어디 가는데 그래서", 13.6, 15.0, timing="asr", speaker="B")
    line = L("L1", PAUSED, 10.0, 14.79, words=W(PAUSED, PAUSED_T), timing="asr", speaker="A")
    assert len(cells_of(run([line, over]), "L1")) == 1


def test_pieces_cut_at_a_pause_follow_the_rules_of_any_piece():
    # each side of the pause is cut further when it does not fit (2 x 16 units, 7 s); no writer's note
    head = "오늘 아침에 집 골목에서 정말 예쁜 고양이 한 마리를 봤는데"   # made up; 28.5 units, two lines
    toks = head.split() + ["진짜야"]
    times = [(round(20.0 + 0.3 * k, 3), round(20.28 + 0.3 * k, 3)) for k in range(len(toks) - 1)] + [(26.0, 26.5)]
    text = " ".join(toks)
    cs = cells_of(run([L("L1", text, 20.0, 26.5, words=W(text, times), timing="asr")]), "L1")
    assert cs[-1]["lines"] == ["진짜야"] and cs[-1]["start"] == sec(F(26.0))
    assert len(cs) == 2 and bare(" ".join(cs[0]["lines"])) == bare(head)
    assert all(c["check"]["violations"] == [] and "notes" not in c["check"] for c in cs)
    long_head = head + " 너무 귀여워서 한참 쳐다봤어"                                # 42.5 units before the pause
    toks = long_head.split() + ["진짜야"]
    times = [(round(20.0 + 0.3 * k, 3), round(20.28 + 0.3 * k, 3)) for k in range(len(toks) - 1)] + [(28.0, 28.5)]
    text = " ".join(toks)
    cs = cells_of(run([L("L1", text, 20.0, 28.5, words=W(text, times), timing="asr")]), "L1")
    assert len(cs) == 3 and cs[-1]["lines"] == ["진짜야"] and all(c["check"]["violations"] == [] for c in cs)


def test_a_writer_join_holds_a_line_with_a_pause_in_one_cell():
    # the writer's "one cell with the line before" comes before the cut at a pause
    a = L("L0", "어디야", 8.0, 8.4, speaker="가람", name="가람", timing="asr")
    b = L("L1", PAUSED, 10.0, 14.79, words=W(PAUSED, PAUSED_T), speaker="가람", name="가람", timing="asr")
    assert len(cells_of(run([a, b]), "L1")) == 2
    d = run([a, dict(b, join=True)])
    c = one(d, "L1")
    assert c["refs"] == ["L0", "L1"] and c["lines"] == ["어디야 잠깐만 기다려 그래 알았어"]
    assert "notes" not in c["check"] and len(d["cells"]) == 1
    d = run([dict(a, text="어디야 지금", words="even"), dict(b, join=True)])   # the line before has a pause too
    assert one(d, "L1")["refs"] == ["L0", "L1"]
    c = L("L2", "그럼 거기서 봐", 16.0, 16.8, speaker="가람", name="가람", timing="asr")
    cs = cells_of(run([a, b, dict(c, join=True)]), "L1")      # a join of the line after holds it too
    assert len(cs) == 1 and cs[0]["refs"] == ["L1", "L2"]


def test_a_writer_join_that_is_refused_leaves_the_line_cut_at_its_pause():
    # 7차 (G3-08): a join refused holds nothing; the line keeps the cut at its pause as without the
    # join, and every piece says why (before: one cell of 5.3 s, '그래 알았어' 3 s before it is said)
    def view(d, ref):
        return [(c["start"], c["end"], c["lines"]) for c in cells_of(d, ref)]
    why = {"가람": "앞 줄과 한 칸으로 묶지 못함 · 7초 초과", "나래": "앞 줄과 한 칸으로 묶지 못함 · 너무 김: 하이픈 줄 16자 초과"}
    for who, text, times in (("가람", PAUSED, PAUSED_T),
                             ("나래", "잠깐만 기다려 그래그래그래 알았다니까요오", [(10.0, 10.4), (10.4, 10.9), (13.9, 14.3), (14.3, 14.9)])):
        a = L("L0", "어디야", 6.5 if who == "가람" else 9.0, 6.9 if who == "가람" else 9.4, speaker="가람", name="가람", timing="asr")
        b = L("L1", text, 10.0, times[-1][1], words=W(text, times), speaker=who, name=who, timing="asr")
        alone, asked = run([a, b]), run([a, dict(b, join=True)])
        assert len(cells_of(alone, "L1")) == 2 and view(asked, "L1") == view(alone, "L1"), (who, asked["cells"])
        assert all(c["check"].get("notes") == [why[who]] for c in cells_of(asked, "L1")), asked["cells"]
        assert view(asked, "L0") == view(alone, "L0")
    # a line before with a pause whose join is refused is cut at its pause too
    a = L("L0", PAUSED, 10.0, 14.79, words=W(PAUSED, PAUSED_T), speaker="가람", name="가람", timing="asr")
    late = L("L1", "응", 17.5, 17.8, speaker="가람", name="가람", timing="asr", join=True)     # 7.8 s after L0 begins
    d = run([a, late])
    assert len(cells_of(d, "L0")) == 2 and one(d, "L1")["check"]["notes"] == ["앞 줄과 한 칸으로 묶지 못함 · 7초 초과"]


def test_a_writer_join_is_not_made_across_scenes_nor_under_a_label_the_second_line_lacks():
    # 7차: one cell never spans a scene change (G3-06, TG 4), and one speaker's two lines go under the
    # label of the first only when it does for the second (G3-07: II.6 "use [in language] at the
    # beginning of the dialogue in each event"; before: '[가람] 잠깐만 오케이', the [영어] gone)
    def note(d, ref):
        return sorted({n for c in cells_of(d, ref) for n in c["check"].get("notes", []) if n.startswith("앞 줄과")})
    a = L("L1", "어디 가는 거야?", 10.0, 10.8, speaker="가람", name="가람", timing="asr", scene_id="SC1")
    for who in ("나래", "가람"):
        b = L("L2", "집에 간다니까", 12.0, 12.6, speaker=who, name=who, timing="asr", scene_id="SC2", join=True)
        d = run([a, b], cuts=[11.5])
        assert [c["refs"] for c in d["cells"]] == [["L1"], ["L2"]] and note(d, "L2") == ["앞 줄과 한 칸으로 묶지 못함 · 장면이 다름"]
        assert one(run([a, dict(b, scene_id="SC1")], cuts=[11.5]), "L1")["refs"] == ["L1", "L2"]     # one scene: joined
        assert one(run([dict(a, scene_id=None), b], cuts=[11.5]), "L1")["refs"] == ["L1", "L2"]      # no scene known
    x = L("L1", "잠깐만", 10.0, 10.5, speaker="가람", name="가람", show=True, timing="asr")
    y = L("L2", "조용히 해", 11.0, 11.4, speaker="가람", name="가람", show=True, timing="asr", join=True)
    for first, second, label in (({}, {"language": "영어"}, "[가람/영어] 조용히 해"),
                                 ({"language": "영어"}, {}, "[가람] 조용히 해"),
                                 ({}, {"label_extra": ["속삭이며"]}, "[가람/속삭이며] 조용히 해"),
                                 ({"label_extra": ["속마음"]}, {"label_extra": ["속삭이며"]}, "[가람/속삭이며] 조용히 해")):
        d = run([dict(x, **first), dict(y, **second)])
        assert [c["refs"] for c in d["cells"]] == [["L1"], ["L2"]], d["cells"]
        assert one(d, "L2")["lines"] == [label] and note(d, "L2") == ["앞 줄과 한 칸으로 묶지 못함 · 화자 표시가 다름"]
    # the same language, or a source shown with the first name only (the second without its name
    # shows none and goes on under it): one cell under the first label, as before
    for first, second, text in (({"language": "영어"}, {"language": "영어", "show_name": False}, "[가람/영어] 잠깐만 조용히 해"),
                                ({"label_extra": ["속마음"]}, {"show_name": False}, "[가람/속마음] 잠깐만 조용히 해"),
                                ({"label_extra": ["속마음", "속삭이며"]}, {"show_name": False, "label_extra": ["속삭이며"]},
                                 "[가람/속마음/속삭이며] 잠깐만 조용히 해")):
        c = one(run([dict(x, **first), dict(y, **second)]), "L2")
        assert c["refs"] == ["L1", "L2"] and " ".join(c["lines"]) == text and "notes" not in c["check"], c
    # two speakers: each keeps its own label in the hyphen cell
    c = one(run([x, dict(y, speaker="나래", name="나래", language="영어")]), "L2")
    assert c["refs"] == ["L1", "L2"] and c["lines"] == ["- [가람] 잠깐만", "- [나래/영어] 조용히 해"]


QUOTED = "우리 반 반장이 '오늘은 청소 당번이 아니니까 먼저 가도 돼' 하고 말해 줬어 그래서 그냥 왔지"   # made up


def test_a_quotation_stays_in_one_cell_when_it_can():
    # I.14 "Quotes should be used at the start and end of each subtitle if the quote carries over more
    # than one subtitle" (인용이 자막 여러 개에 걸치면 자막마다 따옴표): the cut is not taken inside it
    toks = QUOTED.split()
    times = [(round(20.0 + 0.3 * k, 3), round(20.28 + 0.3 * k, 3)) for k in range(len(toks))]
    for kiwi in (True, False):
        d = run([L("L1", QUOTED, 20.0, times[-1][1], words=W(QUOTED, times), timing="asr")], kiwi=kiwi)
        cs = cells_of(d, "L1")
        assert len(cs) == 2 and all(c["check"]["violations"] == [] for c in cs)
        first = " ".join(cs[0]["lines"])
        assert first.count("'") == 2 and first.endswith("돼'"), (kiwi, [c["lines"] for c in cs])


@pytest.mark.skipif(not __import__("cboe.textutil", fromlist=["x"]).kiwi_available(), reason="kiwipiepy not installed")
def test_a_cell_cut_keeps_a_verb_with_its_auxiliary():
    # General Requirements 4 "The line break should not separate … a verb from an auxiliary" (줄바꿈으로 동사와
    # 보조 용언을 떼지 않는다): the cut between two cells too, when another cut does about as well
    text = "그러니까 내 말은 네가 어제 보여 준 그 사진 속에 있던 사람이 우리 옆집 아저씨 같다는 거야"   # made up
    toks = text.split()
    times = [(round(20.0 + 0.3 * k, 3), round(20.28 + 0.3 * k, 3)) for k in range(len(toks))]
    cs = cells_of(run([L("L1", text, 20.0, times[-1][1], words=W(text, times), timing="asr")]), "L1")
    assert not any(c["lines"][-1].endswith("보여") for c in cs), [c["lines"] for c in cs]
    assert bare(" ".join(" ".join(c["lines"]) for c in cs)) == bare(text)
    assert all(c["check"]["violations"] == [] for c in cs)


def test_pieces_of_one_line_are_not_overlapping_lines():
    # 4th review: the ASR's word ends run a little past the next word's start, and the pieces of one
    # line were untangled as if two people spoke over each other ('overlap', the next piece late)
    times = [(30.0, 30.4), (30.4, 30.8), (30.8, 31.3), (31.2, 31.5), (31.45, 31.9), (31.9, 32.4)]
    text = "오늘은 정말 이상한 날 아니었어 그래서"
    x = L("L1", text, 30.0, 32.4, words=W(text, times), timing="asr", breaks=[3, 4])
    d = run([x, L("L2", "응", 40.0, 40.5, timing="asr")])
    cs = cells_of(d, "L1")
    assert [c["lines"] for c in cs] == [["오늘은 정말 이상한"], ["날"], ["아니었어 그래서"]]
    assert all(c["flags"] == [] for c in cs)                           # no "overlap" among its own pieces
    # the short middle piece is laid out again with the others in the time there is: 5/6 s each,
    # never earlier than the line's first word (a cell of a few frames cannot be read)
    assert cs[0]["start"] == sec(F(30.0)) and all(nframes(c["start"], c["end"]) >= MIN_F for c in cs)
    assert all(c["check"]["violations"] == [] for c in cs)


def test_a_heard_line_cut_by_the_writer_gets_its_pieces_laid_out_or_a_note():
    # 4th review: three pieces of an ASR line within 0.6 s; nothing follows for 3 s, so each can have
    # 5/6 s one after the other (before: a first cell of 4 frames, flagged overlap and flash)
    words = [{"w": "빨리", "s": 9.903, "e": 10.19}, {"w": "아니야.", "s": 10.14, "e": 10.693},
             {"w": "오늘", "s": 10.393, "e": 10.498}]
    ln = L("L1", "빨리 아니야. 오늘", 9.903, 10.498, words=words, timing="asr", name="하늘", show=True, breaks=[1, 2])
    nxt = L("L2", "그래 알았어", 13.5, 14.5, timing="asr", name="바다")
    for cuts in ([], [10.158]):
        cs = cells_of(run([ln, nxt], cuts=cuts), "L1")
        assert [c["lines"] for c in cs] == [["[하늘] 빨리"], ["아니야"], ["오늘"]]
        assert all(nframes(c["start"], c["end"]) >= MIN_F and c["flags"] == [] for c in cs), cs
    # no time for three cells of 5/6 s before the next line: the cells stay, and the short one says why
    tight = L("L2", "그래 알았어", 11.6, 12.5, timing="asr", name="바다")
    cs = cells_of(run([ln, tight]), "L1")
    short = [c for c in cs if nframes(c["start"], c["end"]) < MIN_F]
    assert short and all("작가가 정한 끊을 자리로 나눈 칸이 5/6초가 안 됨" in c["check"]["notes"][0] for c in short)


def test_a_later_piece_of_a_heard_line_comes_before_its_words_rather_than_under_five_sixths():
    # 4th round check: laid out again, the last piece starts before its own word. Kept on purpose
    # (5th round, module doc): 5/6 s (General Requirements 1, must) before the in-time on the first
    # frame of audio (TG 1, should); holding every piece behind its words left more cells under
    # 5/6 s than it moved pieces. The first piece never starts before the line's first word
    words = [{"w": "가나", "s": 10.0, "e": 10.1}, {"w": "다라", "s": 10.1, "e": 10.2}, {"w": "마바", "s": 12.5, "e": 12.6}]
    ln = L("L1", "가나 다라 마바", 10.0, 12.6, words=words, timing="asr", breaks=[1, 2])
    cs = cells_of(run([ln, L("L2", "그래 알았어", 13.0, 14.0, timing="asr")]), "L1")
    assert [c["lines"] for c in cs] == [["가나"], ["다라"], ["마바"]]
    assert all(nframes(c["start"], c["end"]) >= MIN_F and not c["flags"] and not c["check"]["violations"] for c in cs)
    assert F(cs[0]["start"]) == F(10.0)
    assert F(cs[1]["start"]) - F(cs[0]["start"]) >= MIN_F + 2      # each after the one before has had 5/6 s
    assert F(cs[2]["start"]) == F(13.0) - 2 - MIN_F < F(12.5)       # as late as 5/6 s allows: before its word


def test_every_group_of_short_pieces_of_a_heard_line_says_why():
    # 4th round check: the writer's cut points left short pieces in two groups of one line (another
    # line's cell between them); only the first group said so in check.notes
    words = [{"w": "가자", "s": 10.0, "e": 10.1}, {"w": "어서", "s": 10.4, "e": 10.5},
             {"w": "빨리", "s": 12.0, "e": 12.5}, {"w": "와", "s": 12.5, "e": 13.0}]
    x = L("L1", "가자 어서 빨리 와", 10.0, 13.0, words=words, timing="asr", person="하늘", breaks=[1, 2])
    y = L("L2", "응 그래", 10.2, 10.35, timing="asr", person="바다")
    z = L("L3", "뭐", 10.6, 10.7, timing="asr", person="구름")
    d = run([x, y, z])
    assert [c["refs"] for c in d["cells"]] == [["L1"], ["L2"], ["L1"], ["L3"], ["L1"]]
    short = [c for c in cells_of(d, "L1") if nframes(c["start"], c["end"]) < MIN_F]
    assert [c["lines"] for c in short] == [["가자"], ["어서"]]
    assert all("5/6초 미만" in c["check"]["violations"] for c in short)
    assert all(any("작가가 정한 끊을 자리로 나눈 칸이 5/6초가 안 됨" in n for n in c["check"].get("notes") or ())
               for c in short)


def test_dialogue_after_a_cut_starts_on_it_when_the_cell_before_ends_two_frames_before():
    # 4th review: A's speech ends a frame before the cut, B's starts 2 frames after it. B used to start
    # on its word and A was cut back to 2 frames before the cut all the same: a 4-frame gap with the
    # cut inside. TG2 puts B on the cut, and the gap is 2 frames (TG5)
    k = 240                                                            # the cut, a frame number
    a_words = [{"w": "오늘은", "s": 7.0, "e": 7.5}, {"w": "정말", "s": 7.5, "e": 8.5},
               {"w": "늦었어", "s": 8.5, "e": (k - 1) / FPS}]
    b0 = (k + 2) / FPS
    b_words = [{"w": "그래도", "s": b0, "e": b0 + 0.5}, {"w": "왔잖아", "s": b0 + 0.5, "e": b0 + 1.0},
               {"w": "우리", "s": b0 + 1.0, "e": b0 + 1.5}]
    a = L("L1", "오늘은 정말 늦었어", 7.0, (k - 1) / FPS, words=a_words, timing="asr")
    b = L("L2", "그래도 왔잖아 우리", b0, b0 + 1.5, words=b_words, timing="asr")
    d = run([a, b], cuts=[k / FPS])
    ca, cb = one(d, "L1"), one(d, "L2")
    assert F(ca["end"]) == k - 2 and F(cb["start"]) == k
    from cboe import check
    assert check.run(d, cuts=[k / FPS])["violations"] == [] and check.run(d)["violations"] == []


# fz.gen scene 77 of the review (made-up words): a heard line, a guessed one in the middle of it
SCENE77 = [
    L("L0002", "이렇게나 모르겠어요 잠깐만요, 안 그래서 아... 안 우리 말이야", 6.643, 10.967, timing="asr",
      speaker="바다", name="바다", show=True, label_extra=["속마음"], scene_id="SC001", breaks=[3, 7],
      words=W("이렇게나 모르겠어요 잠깐만요, 안 그래서 아... 안 우리 말이야",
              [(6.643, 6.864), (6.864, 7.553), (7.553, 7.686), (7.686, 8.503), (8.503, 8.663), (8.663, 9.232),
               (9.232, 9.88), (9.88, 10.539), (10.539, 10.967)])),
    L("L0003", "우리", 10.467, 10.767, timing="asr", speaker="경찰1", name="경찰1", scene_id="SC002", keep=True),
    L("L0004", "다시는 3.5초 그래! 응 다시는 가자 있었어 왜", 10.267, 13.555, timing="even", status="unheard",
      label_extra=["속마음"], language="영어", scene_id="SC002",
      words=W("다시는 3.5초 그래! 응 다시는 가자 있었어 왜",
              [(10.267, 10.726), (10.726, 11.365), (11.365, 11.735), (11.735, 12.514), (12.514, 12.764),
               (12.764, 12.962), (12.962, 13.285), (13.285, 13.555)])),
    L("L0005", "엄청나게 지금 가자 OK 왜 안 아... 다시는 돼 아... 같이", 13.455, 16.3, timing="asr", speaker="하늘",
      name="하늘", label_extra=["전화"], language="영어", scene_id="SC002")]


def test_a_guessed_line_never_moves_a_heard_one():
    # 4th review: the heard L0003 was shown 1.25 s late, behind the guessed L0004 (② "the guessed one
    # gives way" the other way round). Heard lines are untangled first; the guessed one fits in after
    with_guess = run(SCENE77)
    heard_only = run([ln for ln in SCENE77 if ln["id"] != "L0004"])
    for lid in ("L0002", "L0003", "L0005"):
        assert [c["start"] for c in cells_of(with_guess, lid)] == [c["start"] for c in cells_of(heard_only, lid)], lid
    g = one(with_guess, "L0004")
    assert "timing_estimated" in g["flags"]
    assert one(with_guess, "L0003")["end"] < g["start"] < one(with_guess, "L0005")["start"]   # its place in the script


def test_a_scene_end_that_would_break_the_reading_speed_says_so():
    # 4th review (made-up 22 units): ending 2 frames before the scene change gave 18 units a second and
    # no note; the reading speed comes first, and the cell says it crosses the scene change and why
    a = L("L1", "그러니까 내가 어제 분명히 말했잖아 거기는 가지 말라고", 10.0, 11.2, timing="asr")
    b = L("L2", "알았어 미안해", 16.0, 17.0, timing="asr")
    plain = one(run([a, b], cuts=[11.5]), "L1")
    c = one(run([dict(a, scene_id="SC001"), dict(b, scene_id="SC002")], cuts=[11.5]), "L1")
    assert (c["start"], c["end"]) == (plain["start"], plain["end"]) and c["check"]["violations"] == []
    assert c["check"]["notes"] == ["장면이 바뀌는 화면 전환을 넘음 · 그 앞에서 끝내면 초당 14자를 넘음"]


def test_a_note_left_alone_by_one_long_word_is_a_violation_of_the_cell():
    # 4th review: the checker reports lyric_notes for this cell; placement said nothing about it
    ln = L("L1", "가나다라마바사아자차카타파하", 10.0, 12.0, timing="asr", name="영수", show=True, sung=True)
    c = one(run([ln]), "L1")
    assert c["lines"] == ["[영수] ♪", "가나다라마바사아자차카타파하 ♪"]
    assert c["check"]["violations"] == ["음표가 가사와 다른 줄"]


def test_a_dialogue_character_like_the_old_marker_does_not_change_the_line_breaks():
    # 4th review: U+2027 in the text was taken for the placement's own "no break here" marker
    from cboe.placement import _P
    P = _P(24.0, {"kiwi": False})
    for text in ("[세바스찬/영어] 지금 남{}북 열렸어", "[세바스찬] 잠깐만요, 남{}북 지금 잠깐만요"):
        a, b = P.wrap(text.format("‧")), P.wrap(text.format("·"))
        assert [x.replace("‧", "·") for x in a] == b
        assert not re.fullmatch(r"\[[^\]]*\]", a[0])                     # the label is not alone either way


# --------------------------------------------------------------------------- 6th round: sounds (made-up text)

TWO = "어제 우리 동네 빵집에서 새로 나온 케이크를 샀어"          # 27 units: one cell of two lines


def test_a_sound_at_a_cell_of_two_lines_cuts_it_into_two_cells_of_one_line():
    # audit 2-05, 4-m2: '대사 칸이 이미 두 줄' was the end of it, with three seconds free after the line;
    # now the cell is cut at a word boundary into two cells of one line, the sound shares the first
    line = L("L1", TWO, 20.0, 23.0, timing="asr")              # even word times: '새로' at 21.5
    d = run([line, L("L2", "맛있겠다", 30.0, 30.8)], [S("S1", 20.1, "[쿵]")])
    a, b = cells_of(d, "L1")
    assert a["lines"] == ["- 어제 우리 동네 빵집에서", "- [쿵]"] and a["refs"] == ["L1", "S1"]   # heard order
    assert b["lines"] == ["새로 나온 케이크를 샀어"] and b["refs"] == ["L1"]
    assert a["start"] == sec(F(20.0)) and b["start"] == sec(F(21.5)) and nframes(a["end"], b["start"]) == 2
    assert d["no_room"] == [] and a["check"]["violations"] == [] and b["check"]["violations"] == []
    assert all(nframes(c["start"], c["end"]) >= MIN_F and c["check"]["cps"] <= 14 for c in (a, b))
    assert bare(" ".join(a["lines"][:1] + b["lines"]).replace("- ", "")) == bare(TWO)   # the words as they were
    # a sound just before it: the sound first, the name stays on the first piece
    d = run([dict(line, name="하늘", show_name=True)], [S("S1", 19.8, "[쿵]")])
    a, b = cells_of(d, "L1")
    assert a["lines"] == ["- [쿵]", "- [하늘] 어제 우리 동네 빵집에서"] and a["start"] == sec(F(19.8))
    assert b["lines"] == ["새로 나온 케이크를 샀어"]
    # the writer's "one cell" (keep) is never cut: no room, as before
    d = run([dict(line, keep=True), L("L2", "맛있겠다", 30.0, 30.8)], [S("S1", 20.1, "[쿵]")])
    assert one(d, "L1")["lines"] == ["어제 우리 동네 빵집에서", "새로 나온 케이크를 샀어"] and not cells_of(d, "S1")
    (r,) = d["no_room"]
    assert r["reason"].split(" · ")[0] == "대사 칸이 이미 두 줄"


DENSE = [L("L1", "그래서 어떻게 됐는데 정말로", 40.0, 43.0, timing="asr", keep=True),
         L("L2", "진짜야", 43.2, 44.0, timing="asr")]


def test_a_long_sound_without_room_at_its_onset_takes_the_next_gap_while_it_is_heard():
    # audit 2-05: music that goes on finds no cell at its onset (in a line the writer keeps in one
    # cell, 0.2 s before the next line); the first gap that opens after it while it is still heard
    t2 = max(F(44.0), F(43.2) + MIN_F) + 2                     # 2 frames after the floor of L2 (5/6 s)
    d = run(DENSE, [S("M1", 42.0, "[잔잔한 음악]", kind="music", t1=60.0)])
    m = one(d, "M1")
    assert m["refs"] == ["M1"] and m["start"] == sec(t2) and nframes(m["start"], m["end"]) == round(3 * FPS)
    assert m["check"]["notes"] == [f"음악 시작 {(t2 - F(42.0)) / FPS:.2f}초 뒤"] == ["음악 시작 2.13초 뒤"]
    assert m["check"]["violations"] == [] and nframes(one(d, "L2")["end"], m["start"]) == 2 and d["no_room"] == []
    s = one(run(DENSE, [S("S1", 42.0, "[빗소리]", t1=50.0)]), "S1")             # any sound heard that long
    assert s["start"] == sec(t2) and s["check"]["notes"] == [f"소리 시작 {(t2 - F(42.0)) / FPS:.2f}초 뒤"]
    # a short sound keeps the half second of ⑤; no later than 3 s after the onset; the sound still heard
    # for 5/6 s in the cell; not a sound whose onset is a guess, nor one the writer moved
    for snd in (S("S1", 42.0, "[쿵]", t1=42.5), S("S2", 42.0, "[쿵]", t1=43.9),
                S("M1", (t2 - round(3 * FPS) - 1) / FPS, "[잔잔한 음악]", kind="music", t1=60.0),
                S("M1", 42.0, "[잔잔한 음악]", kind="music", t1=(t2 + MIN_F - 1) / FPS),
                dict(S("M1", 42.0, "[잔잔한 음악]", kind="music", t1=60.0), estimated=True),
                S("M1", 41.0, "[잔잔한 음악]", kind="music", t1=59.0, move=42.0)):
        d = run(DENSE, [snd])
        assert not cells_of(d, snd["id"]) and [r["item"] for r in d["no_room"]] == [snd["id"]], snd
    m = one(run(DENSE, [S("M1", 42.0, "[잔잔한 음악]", kind="music", t1=(t2 + MIN_F) / FPS)]), "M1")
    assert m["start"] == sec(t2)                                        # just 5/6 s of it left: still


def test_a_sound_without_room_says_what_change_of_one_cell_makes_room():
    # audit 7-07: "이 칸을 이만큼 줄이면 들어감". The cell before ends earlier (keeping its 5/6 s and
    # reading speed, not its speech) ...
    lines = [L("L1", "그건 정말 아니라고 생각해", 100.0, 102.0, keep=True, timing="asr"),
             L("L2", "알았어", 106.0, 106.6)]
    d = run(lines, [S("S1", 101.4, "[쿵]")])
    (r,) = d["no_room"]
    by = F(102.0) + 12 - (F(101.4) - 2)                         # its end (speech + 0.5 s) to 2 frames before the sound
    assert r["hint"] == {"cell": "C0001", "line": "L1", "by": round(by / FPS, 3), "how": "end_earlier"}
    assert r["reason"] == ("작가가 한 칸에 두기로 정한 대사 · 빈틈이 소리 "
                           f"{(F(102.0) + 2 - F(101.4)) / FPS:.2f}초 뒤 · 앞 대사 칸을 {by / FPS:.2f}초 줄이면 들어감")
    assert F(101.4) - 2 - F(100.0) >= MIN_F                            # what is left of it is still a cell
    # ... or the cell after starts later (at most half a second): and then the sound has its cell
    d = _crowded(101.3, S("S1", 101.3, "[쿵]"))
    h = d["no_room"][0]["hint"]
    assert (h["cell"], h["line"], h["how"]) == ("C0002", "L2", "move_later")
    nxt = (F(101.3) + 15 + round(h["by"] * FPS)) / FPS
    d = run([L("L1", "안녕하세요", 100.0, 101.0), L("L2", "네", nxt, nxt + 0.5)], [S("S1", 101.3, "[쿵]")])
    assert one(d, "S1")["start"] == sec(F(101.3)) and d["no_room"] == []
    # none when one change is not enough: both sides in the way, or a cell left under 5/6 s
    d = run([lines[0], L("L2", "알았어", 102.2, 102.8)], [S("S1", 101.4, "[쿵]")])
    (r,) = d["no_room"]
    assert "hint" not in r and not r["reason"].endswith("들어감")
    d = run(lines, [S("S1", 100.6, "[쿵]")])
    (r,) = d["no_room"]
    assert "hint" not in r and not r["reason"].endswith("들어감")
    # a guessed line without room: the gap between the cells around it made long enough for 5/6 s
    a = L("L1", "그래서 내가 말했잖아", 10.0, 11.6, speaker="A", timing="asr")
    g = L("L2", "그래 알았어", 11.75, 11.9, speaker="B", timing="voice", status="unheard")
    b = L("L3", "됐어 그만해", 12.2, 13.2, speaker="A", timing="asr")
    d = place([a, g, b], [], [], FPS)
    (r,) = d["no_room"]
    end = F(one(d, "L3")["start"]) - 2 - MIN_F - 2              # L1 ends here: 5/6 s and 2 frames each side
    by = F(one(d, "L1")["end"]) - end
    assert r["hint"] == {"cell": one(d, "L1")["id"], "line": "L1", "by": round(by / FPS, 3), "how": "end_earlier"}
    assert r["reason"].startswith("시각 추정 대사 · 앞뒤 칸 사이 빈틈 0.46초 (5/6초 미만) · 앞 대사 칸을 ")
    assert end - F(one(d, "L1")["start"]) >= MIN_F


GUIDE = "내가 어제 봤는데 그런 상황은 아니었어"                      # II.9's example, made into one line
GUIDE_T = [(10.0, 10.3), (10.3, 10.6), (10.6, 11.1), (11.8, 12.1), (12.1, 12.5), (12.5, 13.0)]   # stops 0.7 s


def test_a_sound_that_interrupts_the_dialogue_goes_with_the_piece_before_it():
    # II.9 "Sound effects which interrupt dialogue should be treated as follows, ideally following the
    # order of the original audio": "- 내가 어제 봤는데…" / "- [첨벙!]", then "그런 상황은 아니었어"
    line = L("L1", GUIDE, 10.0, 13.0, words=W(GUIDE, GUIDE_T), timing="asr")
    d = run([line], [S("S1", 11.3, "[첨벙!]")])
    a, b = cells_of(d, "L1")
    assert a["lines"] == ["- 내가 어제 봤는데…", "- [첨벙!]"] and a["refs"] == ["L1", "S1"] and a["kind"] == "mixed"
    assert b["lines"] == ["그런 상황은 아니었어"] and b["refs"] == ["L1"]
    assert a["start"] == sec(F(10.0)) and b["start"] == sec(F(11.8)) and nframes(a["end"], b["start"]) == 2
    assert a["check"]["violations"] == [] and b["check"]["violations"] == [] and d["no_room"] == []
    # "…" only where the speech stops for the sound (0.7 s here): not when the words go on soon after
    # it (0.4 s here; words that run on without a pause give the sound the later piece, refix f1: the
    # test after this one), nor after punctuation of its own (the words are never changed)
    on = [(10.0, 10.3), (10.3, 10.6), (10.6, 11.1), (11.5, 12.1), (12.1, 12.5), (12.5, 13.0)]
    a = cells_of(run([L("L1", GUIDE, 10.0, 13.0, words=W(GUIDE, on), timing="asr")], [S("S1", 11.15, "[첨벙!]")]), "L1")[0]
    assert a["lines"] == ["- 내가 어제 봤는데", "- [첨벙!]"]
    asked = "내가 정말 봤어? 그런 상황은 아니었어"
    a = cells_of(run([L("L1", asked, 10.0, 13.0, words=W(asked, GUIDE_T), timing="asr")], [S("S1", 11.3, "[첨벙!]")]), "L1")[0]
    assert a["lines"] == ["- 내가 정말 봤어?", "- [첨벙!]"]
    # the speaker's own sound: no hyphen, in front of the words it comes before (II.5), as before
    d = run([line], [S("S1", 11.3, "[재채기]", src="L1")])
    a, b = cells_of(d, "L1")
    assert a["lines"] == ["내가 어제 봤는데"] and b["lines"] == ["[재채기] 그런 상황은 아니었어"]
    assert b["refs"] == ["L1", "S1"] and b["start"] == sec(F(11.3))
    # a piece before the sound too long for one hyphen line: the order before the 6th round
    long_ = "그러니까 내가 어제 분명히 봤는데 그런 상황은 아니었어"
    lt = [(9.5, 9.9), (9.9, 10.2), (10.2, 10.5), (10.5, 10.8), (10.8, 11.1), (11.8, 12.1), (12.1, 12.5), (12.5, 13.0)]
    d = run([L("L1", long_, 9.5, 13.0, words=W(long_, lt), timing="asr")], [S("S1", 11.3, "[첨벙!]")])
    a, b = cells_of(d, "L1")
    assert a["refs"] == ["L1"] and " ".join(a["lines"]) == "그러니까 내가 어제 분명히 봤는데"
    assert b["lines"] == ["- [첨벙!]", "- 그런 상황은 아니었어"] and b["start"] == sec(F(11.3))
    assert a["check"]["violations"] == [] and b["check"]["violations"] == []


def test_the_words_of_a_sound_that_interrupts_the_dialogue_are_on_screen_when_it_is_heard():
    # refix f1 (verify f1c): in II.9's order the cell of the sound ends 2 frames before the later piece,
    # which starts on its first word (TG 1). When that word starts on, just before or after the sound
    # (the words run on, or the sound comes late in the pause), that cell ended on the frame the sound is
    # heard: its words were never on screen with it. Now the sound goes with the later piece, which
    # starts at the sound, in heard order (the order before the 6th round), unless its words stay a
    # quarter second (6 frames) after it
    run_on = [(10.0, 10.3), (10.3, 10.6), (10.6, 11.1), (11.1, 12.1), (12.1, 12.5), (12.5, 13.0)]   # no pause
    line = L("L1", GUIDE, 10.0, 13.0, words=W(GUIDE, run_on), timing="asr")
    for t, lines in ((11.05, ["- [첨벙!]", "- 그런 상황은 아니었어"]), (11.2, ["- 그런 상황은 아니었어", "- [첨벙!]"]),
                     (11.35, ["- 그런 상황은 아니었어", "- [첨벙!]"])):
        d = run([line], [S("S1", t, "[첨벙!]")])
        (c,) = cells_of(d, "S1")
        assert F(c["end"]) - F(t) >= 6, (t, c)
        assert c["lines"] == lines and c["start"] == sec(F(t)) and c["check"]["violations"] == []
        a = cells_of(d, "L1")[0]
        assert a["lines"] == ["내가 어제 봤는데"] and nframes(a["end"], c["start"]) == 2 and d["no_room"] == []
    # the sound late in the pause (the next word 2 frames after it), and in words that overlap
    for times, t in ((GUIDE_T, 11.75), ([(10.0, 10.3), (10.3, 10.6), (10.6, 11.2), (11.05, 12.1), (12.1, 12.5),
                                         (12.5, 13.0)], 11.1)):
        d = run([L("L1", GUIDE, 10.0, 13.0, words=W(GUIDE, times), timing="asr")], [S("S1", t, "[첨벙!]")])
        (c,) = cells_of(d, "S1")
        assert F(c["end"]) - F(t) >= 6 and c["start"] == sec(F(t)) and len(c["lines"]) == 2, (t, c)
        assert c["check"]["violations"] == [] and d["no_room"] == []
    # early enough in the pause the guide's order stays (10 frames after the sound here)
    d = run([L("L1", GUIDE, 10.0, 13.0, words=W(GUIDE, GUIDE_T), timing="asr")], [S("S1", 11.3, "[첨벙!]")])
    (c,) = cells_of(d, "S1")
    assert c["lines"] == ["- 내가 어제 봤는데…", "- [첨벙!]"] and F(c["end"]) - F(11.3) == 10


# --------------------------------------------------------------------------- remembered results (감사 6-08)

MEMO_WORDS = ["가", "나는", "오늘", "그래서", "정말로", "어디", "있었어", "모르겠어요", "괜찮아", "빨리", "왔어?", "아니야.",
              "지금", "그러니까", "말이야", "우리", "같이", "가자", "진짜", "뭐라고?", "엄청나게", "기다려", "잠깐만요,",
              "OK", "3.5초", "아...", "그래!", "누구세요", "이렇게나", "도대체가", "왜", "응", "다시는", "안", "돼"]


def random_scene(seed):
    """A made-up scene: lines of random words and word times (pauses, overlaps, guesses, the
    writer's cuts and joins, labels, languages, songs), sounds and shot changes."""
    import random
    rnd = random.Random(seed)
    t, lines, sounds = rnd.uniform(0, 3), [], []
    for i in range(rnd.randint(3, 14)):
        toks = [rnd.choice(MEMO_WORDS) for _ in range(rnd.randint(1, 16))]
        dur = max(0.3, sum(units(w) for w in toks) / rnd.uniform(4.0, 9.0))
        w8 = [rnd.uniform(0.3, 2.0) for _ in toks]
        ws, acc = [], t
        for k, w in enumerate(toks):
            b = acc + dur * w8[k] / sum(w8)
            gap = rnd.choice([0.0, 0.0, 0.05, 2.5]) if k + 1 < len(toks) else 0.0
            ws.append({"w": w, "s": round(acc, 3), "e": round(b, 3)})
            acc = b + gap
        sp = rnd.choice(["하늘", "바다", "구름", "영희/철수", "경찰1", None])
        ln = {"id": f"L{i + 1:04d}", "speaker": sp, "text": " ".join(toks), "start": round(t, 3), "end": round(acc, 3),
              "words": ws, "timing": "asr", "status": "ok", "name": sp, "person": sp,
              "show_name": bool(sp) and rnd.random() < 0.6, "label_extra": rnd.choice([[], [], ["속마음"], ["전화"]]),
              "language": rnd.choice([None, None, None, "영어"]), "sung": rnd.random() < 0.1,
              "scene_id": f"SC{i // 4 + 1:03d}"}
        if rnd.random() < 0.2:
            ln["timing"], ln["status"] = rnd.choice(["voice", "even"]), "unheard"
        if len(toks) > 2 and rnd.random() < 0.2:
            ln["breaks"] = sorted(rnd.sample(range(1, len(toks)), rnd.randint(1, min(3, len(toks) - 1))))
        elif rnd.random() < 0.15:
            ln["keep"] = True
        if i and rnd.random() < 0.1:
            ln["join"] = True
        if rnd.random() < 0.1:
            ln["pos"] = "top"
        lines.append(ln)
        t = acc + rnd.choice([-0.5, -0.1, 0.0, 0.1, 0.3, 0.6, 1.2, 3.0])
    for k in range(rnd.randint(0, 6)):
        on = round(rnd.uniform(0, t), 3)
        ph = rnd.choice(["[쿵]", "[문 닫히는 소리]", "[잔잔한 음악]", "[휴대폰 진동]"])
        sounds.append({"id": f"S{k + 1:04d}", "kind": "music" if "음악" in ph else "sfx", "onset": on,
                       "t1": on + rnd.uniform(0.2, 4), "phrase": ph, "short": ph, "source_line": None, "move_to": None})
    cuts = sorted(round(rnd.uniform(0, t), 3) for _ in range(rnd.randint(0, 12)))
    return lines, sounds, cuts, rnd.choice([23.976, 24.0, 25.0, 29.97]), t


def _placed(scene, settings=None):
    import copy
    lines, sounds, cuts, fps, t = scene
    d = place(copy.deepcopy(lines), copy.deepcopy(sounds), list(cuts), fps, window=(0.0, t + 10), settings=settings)
    return json.dumps(d, ensure_ascii=False)


def test_the_memo_gives_the_draft_computing_everything_again_gives(monkeypatch):
    from cboe import placement, style, textutil
    scenes = [random_scene(seed) for seed in range(40)]
    kinds = [None, style.get("children").settings()]
    monkeypatch.setattr(placement, "MEMO", False)
    monkeypatch.setattr(textutil, "MEMO", False)
    want = [_placed(sc, k) for sc in scenes for k in kinds]
    monkeypatch.setattr(placement, "MEMO", True)
    monkeypatch.setattr(textutil, "MEMO", True)
    placement.clear_memo()
    assert [_placed(sc, k) for sc in scenes for k in kinds] == want      # filling the memo
    assert placement._SPLIT_MEMO and textutil._WRAP_MEMO
    assert [_placed(sc, k) for sc in scenes for k in kinds] == want      # from the memo
    # the same lines at another frame rate or profile: what is remembered for one is not the other's
    lines, sounds, cuts, _fps, t = scenes[3]
    for fps in (23.976, 25.0, 30.0):
        for k in kinds:
            sc = (lines, sounds, cuts, fps, t)
            got = _placed(sc, k)
            monkeypatch.setattr(placement, "MEMO", False)
            monkeypatch.setattr(textutil, "MEMO", False)
            assert got == _placed(sc, k), (fps, k)
            monkeypatch.setattr(placement, "MEMO", True)
            monkeypatch.setattr(textutil, "MEMO", True)


def test_the_split_memo_keys_everything_split_reads():
    import random

    from cboe import placement, style

    def direct(toks, times, head, cont, sung, P, after, others):
        def text_of(i, j):
            return (head if i == 0 else cont) + placement._body(toks[i:j], sung)
        return placement._split(toks, times, text_of, P, after, others)

    placement.clear_memo()
    rnd = random.Random(7)
    adult, children = style.get("adult").settings(), style.get("children").settings()
    changed: dict = {}
    for trial in range(150):
        toks = [rnd.choice(MEMO_WORDS) for _ in range(rnd.randint(6, 16))]
        t0, times = 10.0, []
        for _w in toks:
            d = rnd.uniform(0.1, 0.5)
            times.append((t0, t0 + d))
            t0 += d + rnd.choice([0.0, 0.05, 0.3])
        P = placement._P(23.976, adult)
        common = {"toks": dict(toks=toks[:1] + ["도대체가가가가"] + toks[2:]),
                  "times": dict(times=[(10.0 + (a - 10.0) * 1.6, 10.0 + (b - 10.0) * 1.6) for a, b in times]),
                  "head": dict(head="[하늘/속마음/영어] "), "cont": dict(cont="[영어 아닌 다른 말] "),
                  "sung": dict(sung=True), "P": dict(P=placement._P(23.976, children))}
        line_end = P.fr(times[-1][1])
        bases = [  # the next line known, no next line, a writer's cut ('after' the next piece's frame)
            (dict(toks=toks, times=times, head="", cont="", sung=False, P=P, after=None, others=[P.fr(t0) + 3]),
             {"others": dict(others=None), "next line": dict(others=[P.fr(times[-1][0]) + 2])}),
            (dict(toks=toks, times=times, head="", cont="", sung=False, P=P, after=None, others=None),
             {"others": dict(others=[line_end + 1])}),
            (dict(toks=toks, times=times, head="", cont="", sung=False, P=P, after=line_end + 40, others=[line_end + 3]),
             {"after": dict(after=line_end + 2)})]
        for base, own in bases:
            # one input changed at a time: each of them is in the key (the memo is warm with the others)
            want0 = direct(**base)
            assert placement._split_memo(**base) == want0
            for name, change in dict(common, **own).items():
                args = dict(base, **change)
                want = direct(**args)
                assert placement._split_memo(**args) == want, (trial, name)
                changed[name] = changed.get(name, 0) + (want != want0)
            assert placement._split_memo(**base) == want0          # and the first one is still right
    variants = list(common) + ["others", "next line", "after"]
    # every input does change the cuts of some line: none of them may be left out of the key
    assert all(changed.get(name, 0) > 0 for name in variants), changed
