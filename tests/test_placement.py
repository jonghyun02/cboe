# -*- coding: utf-8 -*-
"""끼워 넣기 (cboe.placement): limits, timing, positions ①-⑤, no_room, the sample work folder."""
import json
import math
import re
import shutil
from pathlib import Path

import pytest

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


def test_gap_under_half_second_closed_to_two_frames():
    d = run([L("L1", "안녕하세요", 10.0, 11.0), L("L2", "반가워요", 11.8, 12.6)])
    a, b = one(d, "L1"), one(d, "L2")
    assert nframes(a["end"], b["start"]) == 2
    assert a["check"]["gap_next"] in (0.083, 0.084)
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
    longer = [lines[0], L("L2", "응 알았어 지금 갈게", 10.45, 11.6, speaker="세바스찬")]
    assert len(run(longer)["cells"]) == 2                              # second line is not short


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
    d = run([L("L1", ROW, 70.0, 74.0)], [S("S1", 72.3, "[첨벙]")])
    a, b = cells_of(d, "L1")
    assert a["refs"] == ["L1"] and b["refs"] == ["S1", "L1"]
    assert a["lines"] == ["내가 어제 도서관에 갔는데"]
    assert b["lines"] == ["- [첨벙]", "- 그런 상황은 아니었어"]
    assert b["start"] == sec(F(72.3)) and nframes(a["end"], b["start"]) == 2     # starts at the sound
    assert all(nframes(c["start"], c["end"]) >= MIN_F for c in (a, b))
    assert not a["check"]["violations"] and not b["check"]["violations"]


def test_position_4_not_when_a_part_would_be_too_short():
    d = run([L("L1", ROW, 70.0, 74.0)], [S("S1", 70.6, "[첨벙]")])
    assert len(cells_of(d, "L1")) == 1 and not cells_of(d, "S1")
    (r,) = d["no_room"]
    assert r["reason"].startswith("나누면 앞 칸 5/6초 미만")


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
    assert r["reason"] == f"소리 뒤 빈틈 {13 / FPS:.2f}초" == "소리 뒤 빈틈 0.54초"
    assert d["summary"]["no_room"] == 1


def test_writer_move_places_a_no_room_sound():
    d = _crowded(101.3, S("S1", 101.3, "[쿵]", move=105.0))
    assert d["no_room"] == [] and one(d, "S1")["start"] == sec(F(105.0))


def test_never_into_a_cell_that_already_has_two_lines():
    b0 = (F(110.0) + 12) / FPS
    d = run([L("L2", "가나다라마 바사아자차 카타파하가 나다", b0, b0 + 1.5)], [S("S1", 110.0, "[쿵]")])
    assert one(d, "L2")["refs"] == ["L2"] and len(one(d, "L2")["lines"]) == 2
    (r,) = d["no_room"]
    assert r["reason"] == f"대사 칸이 이미 두 줄 · 소리 뒤 빈틈 {10 / FPS:.2f}초"
    assert r["reason"] == "대사 칸이 이미 두 줄 · 소리 뒤 빈틈 0.42초"


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
    for fps in (25.0, 29.97, 60.0):
        d = place([L("L1", "안녕하세요", 10.0, 11.0), L("L2", "반가워요", 11.8, 12.6)], [], [], fps)
        a, b = d["cells"]
        assert round((b["start"] - a["end"]) * fps) == 2
        assert abs(a["start"] * fps - round(a["start"] * fps)) < 0.06


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
    assert one(run([longer, b], cuts=[cut2]), "L1")["end"] > cut2      # without scenes it reads on over the cut
    d = run([dict(longer, scene_id="SC001"), dict(b, scene_id="SC002")], cuts=[cut2])
    assert one(d, "L1")["end"] == sec(F(cut2) - 2)                     # TG 4: never over the scene change
    assert "notes" not in one(d, "L1")["check"] and one(d, "L2") == one(run([longer, b], cuts=[cut2]), "L2")
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
    assert one(d, "L1")["refs"] == ["L1"] and one(d, "L2")["refs"] == ["L2"]
    together = run([dict(ln, scene_id="SC001") for ln in lines])
    assert one(together, "L1")["refs"] == ["L1", "L2"]


def test_one_person_under_two_labels_is_still_one_speaker():
    # before and after the name is told: '[남자]' then '[재식]' are one person — no two-speaker cell
    lines = [L("L1", "뭐?", 10.0, 10.2, speaker="재식", name="남자", show=True, person="재식"),
             L("L2", "응", 10.45, 10.6, speaker="재식", name="재식", show=True, person="재식"),
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


def test_a_speaker_label_is_not_left_alone_on_the_first_line():
    long_name = L("L1", "오늘은 정말 고마웠어", 10.0, 12.0, name="가나다라마바", show=True)
    c = one(run([long_name]), "L1")
    assert c["lines"] == ["[가나다라마바] 오늘은", "정말 고마웠어"]
    noted = L("L1", "오늘은 정말 고마웠어", 10.0, 12.0, name="가나다라마바", show=True)
    d = run([noted], [S("S1", 10.0, "[한숨]", src="L1")])                 # ③ the speaker's own sound in front
    assert not re.fullmatch(r"(\[[^\]]*\]\s?)+", one(d, "L1")["lines"][0])
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
