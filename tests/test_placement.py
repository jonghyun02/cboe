# -*- coding: utf-8 -*-
"""끼워 넣기 (cboe.placement): limits, timing, positions ①-⑤, no_room, the sample work folder."""
import json
import math
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


def L(lid, text, start, end, speaker=None, name=None, show=False, words="even"):
    toks = text.split()
    if words == "even":
        step = (end - start) / len(toks)
        words = [{"w": w, "s": round(start + i * step, 3), "e": round(start + (i + 1) * step, 3)}
                 for i, w in enumerate(toks)]
    return {"id": lid, "speaker": speaker, "text": text, "start": start, "end": end, "words": words,
            "show_name": show, "name": name}


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
    assert named["lines"][0].startswith("(세바스찬) ")
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
    assert cs[0]["lines"][0].startswith("(양희) ")
    assert all("(양희)" not in " ".join(c["lines"]) for c in cs[1:])
    bodies = " ".join(" ".join(c["lines"]) for c in cs).replace("(양희) ", "", 1)
    assert bodies == text                                              # text never changed
    assert " ".join(cs[0]["lines"]).endswith(".")
    for c in cs:
        assert c["check"]["n_lines"] <= 2 and max(c["check"]["chars"]) <= 16
    words = text.split()
    step = 8.0 / len(words)
    k = len(" ".join(cs[0]["lines"]).replace("(양희) ", "").split())
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
    d = run([L("L1", "가나다라마바사 아자차카타", 10.0, 12.0, speaker="A"),
             L("L2", "파하가나다라 마바사아", 10.5, 11.5, speaker="B"),
             L("L3", "하나", 13.0, 13.4, speaker="A"), L("L4", "둘", 13.0, 13.4, speaker="B")])
    a, b = one(d, "L1"), one(d, "L2")
    assert nframes(a["end"], b["start"]) == 2
    assert "5/6초 미만" in a["check"]["violations"]
    assert len(d["cells"]) >= 3


def test_two_short_lines_of_different_speakers_share_a_hyphen_cell():
    lines = [L("L1", "뭐?", 10.0, 10.2, speaker="빌", name="빌", show=True),
             L("L2", "응", 10.45, 10.6, speaker="세바스찬", name="세바스찬", show=True),
             L("L3", "그래 가자", 11.1, 12.0, speaker="빌")]
    d = run(lines)
    c = one(d, "L1")
    assert c["refs"] == ["L1", "L2"] and c["lines"] == ["- (빌) 뭐?", "- (세바스찬) 응"]
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
    c = one(run(sounds=[S("M1", 60.0, "♬ 무서운 음악", **m)], cuts=[59.7]), "M1")
    assert c["start"] == sec(F(59.7)) and c["kind"] == "music"            # cut in the half second before
    assert nframes(c["start"], c["end"]) == round(3 * FPS)
    c = one(run(sounds=[S("M1", 60.0, "♬ 무서운 음악", **m)], cuts=[59.3]), "M1")
    assert c["start"] == sec(F(60.0))
    c = one(run(sounds=[S("M1", 60.0, "♬ 음악", kind="music", t1=61.5)]), "M1")
    assert c["end"] == sec(F(61.5) + 12)                                 # shorter music: its end + 0.5 s
    c = one(run(sounds=[S("M1", 60.0, "♬ 음악", kind="music", t1=60.1)]), "M1")
    assert nframes(c["start"], c["end"]) == MIN_F


def test_music_snap_never_takes_the_previous_speech():
    d = run([L("L1", "가나다라", 58.0, 59.68)], [S("M1", 60.0, "♬ 음악", kind="music", t1=70.0)], cuts=[59.7])
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
    c = one(run(sounds=[S("M1", 60.0, "♬ 무서운 음악", kind="music", t1=66.0)], cuts=[61.0]), "M1")
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
    assert c["lines"] == ["- (BJ 옴뫄) 으아아아아", "- [천둥 소리]"]          # heard order
    assert c["start"] == sec(F(230.357)) and c["check"]["violations"] == []
    d = run([ln], [S("S0008", 230.0, "[천둥 소리]", t1=231.0)])
    c = one(d, "L0049")
    assert c["lines"] == ["- [천둥 소리]", "- (BJ 옴뫄) 으아아아아"]          # the sound heard first
    assert c["start"] == sec(F(230.0))


def test_position_3_sound_of_the_speaker_on_one_line():
    ln = L("L1", "나도야!", 40.0, 40.8, speaker="지수")
    c = one(run([ln], [S("S1", 40.0, "[재채기]", src="L1")]), "L1")
    assert c["lines"] == ["[재채기] 나도야!"] and c["refs"] == ["L1", "S1"] and c["kind"] == "mixed"
    c = one(run([dict(ln, name="지수", show_name=True)], [S("S1", 40.1, "[재채기]", src="L1")]), "L1")
    assert c["lines"] == ["(지수) [재채기] 나도야!"]
    longer = L("L1", "그러니까 나도 같이 가자고", 40.0, 41.5, speaker="지수", name="지수", show=True)
    c = one(run([longer], [S("S1", 40.0, "[재채기]", src="L1")]), "L1")
    assert len(c["lines"]) == 2 and not any(x.startswith("- ") for x in c["lines"])
    assert " ".join(c["lines"]) == "(지수) [재채기] 그러니까 나도 같이 가자고"
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


def test_dialogue_text_is_never_changed_by_sounds():
    lines = [L("L1", ROW, 70.0, 74.0, name="양희", show=True), L("L2", "나도야!", 75.0, 75.8),
             L("L3", "응", 77.0, 77.3)]
    sounds = [S("S1", 72.3, "[첨벙]"), S("S2", 75.0, "[재채기]", src="L2"), S("S3", 77.1, "[쿵]")]
    d = run(lines, sounds)
    phrases = [s["phrase"] for s in sounds]
    for ln in lines:
        text = " ".join(" ".join(c["lines"]) for c in cells_of(d, ln["id"]))
        for p in phrases:
            text = text.replace(p, "")
        text = text.replace("- ", "").replace("(양희) ", "")
        assert text.split() == ln["text"].split()


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
    c = one(run(sounds=[S("S1", 140.0, "[쿵]"), S("M1", 140.2, "♬ 음악", kind="music", t1=150.0)]), "S1")
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
    named = [c for c in d["cells"] if c["lines"][0].startswith("(BJ 옴뫄)")]
    assert [c["refs"] for c in named] == [[f"L000{k}"] for k in range(1, 7)]
