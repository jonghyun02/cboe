# -*- coding: utf-8 -*-
"""지침 검사기 (cboe.check): the guide's own (O) / (X) examples, every rule, files and the command line.

The example texts are quoted from the Netflix Korean Timed Text Style Guide (chapter II = SDH, and
I.4 / I.6 / I.11-I.14 which II relies on). An (O) example must not break a notation or punctuation
rule; an (X) example whose fault is in its form must be caught. (X) examples whose fault is in the
content (wrong word, a description where an onomatopoeia was possible, a sound that needed no
caption) cannot be seen in the text alone: they are listed in ``NOT_VISIBLE`` so that the limit of
the checker is written down, not hidden.
"""
import ast
import json
from pathlib import Path

import pytest

from cboe import check, export, style
from cboe.placement import place

FPS = 24.0
ROOT = Path(__file__).resolve().parents[1]


def cell(lines, start=10.0, end=14.0, cid="C0001", **more):
    return {"id": cid, "start": start, "end": end, "lines": [lines] if isinstance(lines, str) else list(lines), **more}


def codes(lines, **kw):
    """Codes of the rules one cell breaks (4 s long: time never is the problem)."""
    return [v["code"] for v in check.run([cell(lines)], fps=FPS, **kw)["violations"]]


def F(k):
    return k / FPS


# --------------------------------------------------------------------------- the guide's examples

GOOD = [  # (clause, lines of one subtitle) — every one printed as correct in the guide
    ("II.1", "아, 맞다, 너희들이 그랬지?"),
    ("II.1", "아주 삐까뻔쩍하네, 마음에 쏙 들길 바래"),
    ("II.4", "[사령관] 다 좋은데 말이야… [한숨]"),
    ("II.5", ["- [한경의 웃음]", "- [재선] 나도야!"]),
    ("II.5", "[지수] [재채기] 나도야!"),
    ("II.5", "[지수] 나도야! [재채기]"),
    ("II.5", ["- [한경의 웃음]", "- [지수의 재채기]"]),
    ("II.5", ["- [달칵 - 문]", "- [지수의 재채기]"]),
    ("II.6", "[영희/중국어] 나도 갈래"),
    ("II.6", "[철수/영어] 아니다, 취소"),
    ("II.6", "[영희] 뭐라고 하는 거야, 못 알아듣게"),
    ("II.6", "[일어로 말한다]"),
    ("II.6", "[영희/북한 억양]"),
    ("II.6", "그러네, 베리 쏘리, 내 잘못이야"),
    ("II.6", "[가짜 언어로 말한다]"),
    ("II.7", "[양파의 ‘눈부신 계절’]"),
    ("II.7", "[양파의 ‘눈부신 계절’이 흐른다]"),
    ("II.7", "[엄정화의 '배반의 장미'가 흐른다]"),
    ("II.7", ["[영수/쉰 목소리] ♪ 기억하렴", "나의 서글픈 모습 ♪"]),
    ("II.7", "[록 음악 - 라디오]"),
    ("II.7", "[경쾌한 음악]"),
    ("II.8", "[영희]"), ("II.8", "[영희/영상]"), ("II.8", "[영희/속마음]"), ("II.8", "[영희/속마음/영어]"),
    ("II.8", "[영희/랩]"), ("II.8", "[영희/속삭임]"), ("II.8", "[영희/힘겹게]"), ("II.8", "[여자1]"), ("II.8", "[김 경위]"),
    ("II.9", "[개가 짖는다]"), ("II.9", "[직]"), ("II.9", "[직 - 지퍼]"), ("II.9", "[달칵 - 안전띠]"),
    ("II.9", "[총성 - 영상]"), ("II.9", "[퍽퍽 - 야구 배트]"), ("II.9", "[째깍째깍 - 효과음]"),
    ("II.9", "[긴박한 째깍째깍 효과음]"), ("II.9", "[펑!]"),
    ("II.9", "아으~ 난 아니라고 봐"),
    ("II.9", "하… 내가 진짜 못산다"),
    ("II.9", "[바스락]"), ("II.9", "[바스락 - 종이]"), ("II.9", "[달그락]"), ("II.9", "[달그락 - 찻잔]"),
    ("II.9", "[코골이]"), ("II.9", "[계속되는 질문 세례]"), ("II.9", "[음악이 멈춘다]"), ("II.9", "[매미 울음]"),
    ("II.9", "[들뜬 말투로]"), ("II.9", "[또박또박 끊어서]"), ("II.9", "[속삭이며]"), ("II.9", "[기침]"),
    ("II.9", "[콜록거린다]"), ("II.9", "그… 그게 아닌데!"), ("II.9", "[주저하며]"),
    ("II.9", ["- 내가 어제 봤는데…", "- [첨벙!]"]),
    ("II.9", "그런 상황은 아니었어"),
    ("II.9", "[웃음소리가 울려 퍼진다]"),
    ("II.9", "[우아한 국악]"),
    ("II.10", "[영희, 철수] 설마!"),
    ("II.10", "[함께] 설마!"),
    ("II.10", "우리 이제 * 됐네"),
    ("II.10", "우리 **리스 부부야"),
    ("II.10", "아, ****"),
    ("I.4", "그 사람이 내게 와서"), ("I.4", "다시 돌아오겠다고 했어"), ("I.4", "오늘 날씨는 아주…"),
    ("I.4", "그 사람 참 괜찮네"),
    ("I.4", ["- 내가 왜 해변에 가길 좋아하느냐면…", "- 왜?"]),
    ("I.4", "바다에서 수영하는 게 좋아서야"),
    ("I.6", ["- 하지만 왜요? 어디 가세요?", "- 미국에요"]),
    ("I.11", "난 말했어, 하지 말라고, 너 뭐야!"),
    ("I.11", ["난 말했어, 하지 말라고", "너 뭐야!"]),
    ("I.11", "내가 말했지, '난 안 그랬어'"),
    ("I.11", ["내가 말했지", "'난 안 그랬어'"]),
    ("I.12", "11개 고쳤어"), ("I.12", "두 개 고쳤어"),
    ("I.13", ["사랑해, 그게 내가", "하고 싶은 말이었어"]),
    ("I.14", "“그레이 아나토미”"), ("I.14", "“1992년 미국 시카고”"),
    ("I.14", ["우리 모두에게 분명히 말하더군요", "‘두 번째 창문 지나면 보이는’"]),
    ("I.14", ["‘첫 번째 출구로는 나가면 안 돼", "함정이니까’"]),
]

# the guide prints these on one line although they are over 16 units by its own II.2 count (they
# show wording, not line breaks; '[사령관] …' is 16.5: audit 2 noted it): only "한 줄 16자 초과" is
# allowed for them, nothing else
OVER_A_LINE = {"아주 삐까뻔쩍하네, 마음에 쏙 들길 바래", "[사령관] 다 좋은데 말이야… [한숨]",
               "[영희] 뭐라고 하는 거야, 못 알아듣게", "[엄정화의 '배반의 장미'가 흐른다]",
               "- 내가 왜 해변에 가길 좋아하느냐면…"}

BAD = [  # (clause, lines, code that must be reported, level) — printed as wrong, and wrong in form
    ("I.13", ["사랑해.", "그게 내가 하고 싶은 말이었어"], "line_end_punct", "must"),
    ("I.13", ["사랑해,", "그게 내가 하고  싶은 말이었어"], "line_end_punct", "must"),
    ("I.13", ["사랑해. 그게 내가", "하고 싶은 말이었어"], "sentence_period", "must"),
    ("II.9", "[질색하는 소리] 난 아니라고 봐", "sound_ending", "should"),
    ("II.9", "[웃음소리가 메아리친다", "bracket", "must"),              # as printed: the ] is missing
    ("II.10", "[영희와 철수] 설마!", "joint_id", "should"),
    ("II.10", "아, [음 소거 효과음]", "bleep_label", "should"),
    ("II.6", "[외국어로 말한다]", "foreign_generic", "must"),            # "should never be used"
]

NOT_VISIBLE = [  # printed as wrong, but the fault is in the content: the text alone does not show it
    ("II.1", "아, 맞는다, 너희들이 그랬지?"),                 # not what was said
    ("II.1", "아주 뻔쩍뻔쩍하네, 마음에 쏙 들길 바라"),
    ("II.6", "그러네, 베리 소리, 내 잘못이야"),               # transliteration
    ("II.9", "[큰 폭발음]"),                                  # an onomatopoeia was possible
    ("II.9", "[초조한 신음] 내가 진짜 못산다"),               # could have been written into the line
    ("II.9", "[종이를 넘긴다]"),                              # describes the action, not the sound
    ("II.9", "[잔을 달그락 내려놓는다]"),
    ("II.9", "[서진의 코골이]"),                              # who makes it is visible
    ("II.9", "[기자들이 계속 질문한다]"),
    ("II.9", "[매미 울음]"),                                  # not plot-pertinent in that scene
    ("II.9", "[웃음소리가 메아리친다]"),                      # not visual language (with its ] restored)
    ("II.9", "[고아한 풍류의 전통 음악]"),                    # obscure words
    ("II.10", "우리 이제 ***"),                               # one asterisk per bleeped syllable
    ("II.10", "우리 **** 부부야"),
]


def _name(clause, lines):
    return f"{clause} {lines if isinstance(lines, str) else ' / '.join(lines)}"


@pytest.mark.parametrize("clause, lines", GOOD, ids=[_name(c, x) for c, x in GOOD])
def test_guide_examples_marked_correct_pass(clause, lines):
    got = set(codes(lines))
    rows = [lines] if isinstance(lines, str) else lines
    allowed = {"line_length"} if any(r in OVER_A_LINE for r in rows) else set()
    assert got == allowed, (clause, lines, got)


def test_which_correct_examples_are_over_a_line():
    over = {r for _c, x in GOOD for r in ([x] if isinstance(x, str) else x) if check.units(r) > 16}
    assert over == OVER_A_LINE


@pytest.mark.parametrize("clause, lines, code, level", BAD, ids=[f"{c} {k}" for c, _x, k, _l in BAD])
def test_guide_examples_marked_wrong_in_form_are_caught(clause, lines, code, level):
    res = check.run([cell(lines)], fps=FPS)
    hit = [v for v in res["violations"] if v["code"] == code]
    assert hit and hit[0]["level"] == level and clause in hit[0]["clause"]
    assert res["ok"] == (level != "must")             # an obligation fails the file, a recommendation does not


def test_examples_wrong_in_content_are_beyond_a_text_check():
    for clause, lines in NOT_VISIBLE:
        assert set(codes(lines)) <= {"line_length"}, (clause, lines)


# --------------------------------------------------------------------------- limits (II.2-II.4, GR 1)

def test_limits_and_their_clauses():
    cells = [cell("가" * 17, 10.0, 14.0, "C1"),                       # 17 units
             cell(["하나", "둘", "셋"], 20.0, 23.0, "C2"),             # 3 lines
             cell("가나다라마바사아자차카타파하가", 30.0, 31.0, "C3"),   # 15 units in 1 s
             cell("응", 40.0, 40.5, "C4"),                             # 12 frames
             cell("응", 50.0, 50.0 + 4 / FPS, "C5"),                   # 4 frames
             cell("가나다", 60.0, 67.5, "C6")]                         # 7.5 s
    res = check.run(cells, fps=FPS)
    got = {(v["cell"], v["code"]): (v["clause"], v["level"], v["message"]) for v in res["violations"]}
    assert got == {("C1", "line_length"): ("II.2", "must", "한 줄 16자 초과"),
                   ("C2", "line_count"): ("II.4", "must", "2줄 초과"),
                   ("C3", "reading_speed"): ("II.3", "must", "초당 14자 초과"),
                   ("C4", "min_duration"): ("GR 1", "must", "5/6초 미만"),
                   ("C5", "min_duration"): ("GR 1", "must", "5/6초 미만"),
                   ("C5", "flash"): ("GR 1", "must", "몇 프레임짜리 칸"),
                   ("C6", "max_duration"): ("GR 1", "must", "7초 초과")}
    assert not res["ok"] and res["summary"]["must"] == 7 and res["summary"]["cells_must"] == 6
    assert res["summary"]["by_code"]["min_duration"] == 2
    assert [r["code"] for r in res["rules"]] == ["line_length", "line_count", "reading_speed", "min_duration",
                                                 "flash", "max_duration"]
    assert res["rules"][3] == {"code": "min_duration", "clause": "GR 1", "level": "must", "title": "5/6초 미만",
                               "count": 2, "cells": ["C4", "C5"]}


def test_exact_limits_pass():
    ok = [cell("가" * 16, 10.0, 12.0, "C1"),                           # 16 units, 8 units/s
          cell(["가" * 16, "나" * 12], 20.0, 22.0, "C2"),               # 2 lines, 14 units/s exactly
          cell("응", 30.0, 30.0 + 20 / FPS, "C3"),                      # 20 frames = 5/6 s
          cell("가나다", 40.0, 47.0, "C4")]                             # 7 s
    assert check.run(ok, fps=FPS)["violations"] == []


def test_children_read_eleven_units_a_second():
    c = [cell("가나다라마바사아자차카타", 10.0, 11.0)]                   # 12 units in 1 s
    assert check.run(c, fps=FPS)["violations"] == []
    res = check.run(c, fps=FPS, profile="children")
    assert res["profile"] == "children" and [v["message"] for v in res["violations"]] == ["초당 11자 초과"]
    assert check.run({"cells": c, "fps": FPS, "profile": "children"})["profile"] == "children"   # a draft says so


# --------------------------------------------------------------------------- gaps and order (TG 5)

def test_gaps_between_cells():
    def pair(gap_frames, **kw):
        cells = [cell("하나", F(240), F(264), "C1"), cell("둘", F(264 + gap_frames), F(312 + gap_frames), "C2")]
        return [(v["code"], v["level"]) for v in check.run(cells, fps=FPS, **kw)["violations"]]

    assert pair(-3) == [("overlap", "must")]
    assert pair(0) == pair(1) == [("frame_gap", "must")]
    assert pair(2) == []                                               # exactly 2 frames
    assert pair(3) == pair(11) == [("short_gap", "must")]              # "gaps of 3-11 frames ... must be closed"
    assert pair(12) == pair(40) == []                                  # half a second or more
    # a shot change inside the gap: the out-time sits 2 frames before the cut, the next cell starts late
    assert pair(7, cuts=[F(266)]) == [("short_gap_cut", "should"), ("cut_in_after", "should")]
    assert pair(7, cuts=[F(100)]) == [("short_gap", "must")]
    res = check.run([cell("하나", F(240), F(264), "C1"), cell("둘", F(265), F(320), "C2")], fps=FPS)
    assert res["violations"][0]["next"] == "C2" and res["violations"][0]["gap"] == pytest.approx(1 / FPS, abs=1e-3)


def test_two_frames_depend_on_the_frame_rate():
    cells = [cell("하나", 1.0, 2.0, "C1"), cell("둘", round(2.0 + 2 / 60, 3), 3.5, "C2")]
    assert check.run(cells, fps=60.0)["violations"] == []
    assert [v["code"] for v in check.run(cells, fps=30.0)["violations"]] == ["frame_gap"]
    res = check.run(cells)                                             # no frame rate at all
    assert res["fps"] == style.DEFAULT_FPS == 24.0 and res["fps_assumed"]
    assert check.run({"cells": cells, "fps": 60.0})["fps_assumed"] is False


def test_order_and_cells_that_cannot_be_written():
    cells = [cell("하나", 10.0, 11.0, "C1"), cell("둘", 5.0, 6.0, "C2"), cell("  ", 20.0, 21.0, "C3"),
             cell("넷", None, 31.0, "C4"), cell("다섯", 40.0, 40.0, "C5"), {"start": 50.0, "end": 51.0, "lines": ["여섯"]}]
    res = check.run(cells, fps=FPS)
    got = {(v["cell"], v["code"], v["message"]) for v in res["violations"]}
    assert got == {("C2", "order", "시작 순서가 뒤바뀜"), ("C3", "no_text", "글 없음"), ("C4", "bad_time", "시각 없음"),
                   ("C5", "bad_time", "끝이 시작보다 빠름")}
    assert next(v for v in res["violations"] if v["code"] == "order")["after"] == "C1"
    assert set(check.FATAL) == {"no_text", "bad_time"}
    assert check.run([cells[5]], fps=FPS)["violations"] == [] and check.run([], fps=FPS)["ok"]


# --------------------------------------------------------------------------- notation and punctuation

@pytest.mark.parametrize("lines, want", [
    ("그래서 갔어.", ["line_end_punct"]),
    ("그래서 갔어,", ["line_end_punct"]),
    (["그래서 갔어", "거기 있더라."], ["line_end_punct"]),
    ("♪ 달빛 아래. ♪", ["line_end_punct"]),                  # I.17: not in front of the closing note either
    ("그래서 갔어…", []),
    ("그래서 갔어?!", []),
    ("그가 말했지, '난 안 갔어.'", ["line_end_punct"]),     # a closing quote does not hide it
    ("그가 말했지, '난 안 갔어'", []),
    ("3.5 킬로미터 남았어", []),
    ("호머 J. 심슨이야", []),
    ("1992. 3. 5 맞지?", []),
    ("갔어. 없더라", ["sentence_period"]),
    ("갔어. [한숨]", []),                                   # a label follows, not a sentence
    ("그래서 갔어...", ["dots"]),
    ("이.. 일단 가자", ["dots"]),
    ("그래서 갔어….", ["line_end_punct"]),
    ("(빌) 뭐?", ["label_parens"]),
    ("- (빌) 뭐?", ["label_parens"]),
    ("(웃음)", ["label_parens"]),
    ("[빌] 뭐?", []),
    ("뭐라고 (웃으며) 했어", []),                           # only a label in front of the line counts
    ("♬ 슬픈 음악", ["note_glyph"]),
    ("[슬픈 음악]", []),
    ("[쿵] 뭐야]", ["bracket"]),
    ("[[쿵]]", ["bracket"]),
    (["[쿵, 책 떨어지는 소리가 아주", "크게 울린다]"], []),   # one label over two lines is whole
    (["- [쿵, 책이", "- 떨어진다]"], ["bracket", "bracket"]),  # but not over two hyphen lines
    ("[천둥 소리]", ["sound_ending"]),
    ("[울음소리가 들린다]", ["sound_ending"]),
    ("[우르릉]", []),
    ("[영희/쉰 목소리] 설마", []),                          # a speaker label with a manner of speech
    ("[영희/외국어] 뭐라고", ["foreign_generic"]),
    ("[영희와 철수]", []),                                  # a sound label on its own is not a speaker ID
    ("-뭐?", ["hyphen"]),
    (["- 뭐?", "응"], ["hyphen"]),
    (["- 뭐?", "- 응"], []),
    ("-5도야", []),                                         # a minus sign
    ("♪ 달빛 아래 ♪", []),
    ("♪달빛 아래♪", ["lyric_notes"]),                        # II.7: a space next to the note
    ("♪ 달빛 아래", ["lyric_notes"]),                        # and a note at the end
    ("[영수] ♪ 달빛 아래 ♪", []),
    (["♪ 달빛 아래", "걷던 그 길을 ♪"], []),              # one speaker: around the subtitle
    (["- ♪ 달빛 아래 ♪", "- ♪ 그 길을 ♪"], []),             # a duet: around each line
    (["- ♪ 달빛 아래", "- 그 길을 ♪"], ["lyric_notes", "lyric_notes"]),
    (["- [영희] ♪ 라라라 ♪", "- [쿵]"], []),
])
def test_text_rules(lines, want):
    assert sorted(codes(lines)) == sorted(want)


def test_every_violation_names_clause_level_cell_and_line():
    res = check.run([cell(["(빌) 가자.", "어서 가자..."], 10.0, 14.0, "C0007")], fps=FPS)
    assert [(v["code"], v["clause"], v["level"], v["cell"], v["index"], v.get("line")) for v in res["violations"]] == [
        ("line_end_punct", "I.13", "must", "C0007", 0, 0), ("dots", "I.4", "must", "C0007", 0, 1),
        ("label_parens", "II.8", "must", "C0007", 0, 0)]
    assert res["violations"][0]["text"] == "(빌) 가자." and res["violations"][0]["start"] == 10.0
    levels = {level for _code, _clause, level, _title in check.RULES}
    assert levels == {"must", "should"}
    assert {code for code, _c, level, _t in check.RULES if level == "should"} == {
        "short_gap_cut", "sound_ending", "joint_id", "bleep_label", "cut_in_before", "cut_in_after",
        "cut_out_before", "cut_out_close", "cut_out_after"}


# --------------------------------------------------------------------------- shot changes (TG 2, TG 4)

def test_shot_change_rules_need_the_cuts():
    one_cell = [cell("안녕하세요 여러분", F(240), F(288))]

    def got(cuts, cells=one_cell, only="C0001"):
        found = check.run(cells, fps=FPS, cuts=cuts)["violations"]
        return [(v["code"], v["level"]) for v in found if v["cell"] == only]

    assert got(None) == got([]) == got([F(500)]) == []
    assert check.run(one_cell, fps=FPS)["cut_checks"] is False
    assert check.run(one_cell, fps=FPS, cuts=[1.0])["cut_checks"] is True
    assert got([F(236)]) == [("cut_in_after", "should")]               # starts 4 frames after a cut
    assert got([F(228)]) == [("cut_in_after", "should")] and got([F(227)]) == []   # within half a second: 12 frames
    assert got([F(240)]) == []                                         # on the cut
    # starts 5 frames before a cut it crosses. TG 4 says "must" for dialogue that crosses the cut, but
    # the file does not say whether the dialogue or only its cell does (TG 1 allows that): a recommendation
    assert got([F(245)]) == [("cut_in_before", "should")]
    assert got([F(252)]) == []                                         # half a second before: fine
    assert got([F(284)]) == [("cut_out_after", "should")]              # ends 4 frames after a cut
    assert got([F(276)]) == []                                         # half a second after it
    assert got([F(289)]) == [("cut_out_close", "should")]              # ends 1 frame before a cut
    assert got([F(290)]) == []                                         # 2 frames before: where it belongs
    assert got([F(294)]) == [("cut_out_before", "should")]             # 6 frames before: could reach the cut
    assert got([F(300)]) == [("cut_out_before", "should")] and got([F(301)]) == []
    # what cannot be helped is not reported: the cell before holds the cut, the next cell starts before it
    before = [cell("앞 칸", F(200), F(238), "C0"), one_cell[0]]          # still on screen at the cut
    assert got([F(236)], before) == [] and got([F(236)], before, "C0") == [("cut_out_after", "should")]
    after = [one_cell[0], cell("뒤 칸", F(290), F(330), "C2")]           # starts before the cut
    assert got([F(294)], after) == [] and got([F(294)], after, "C2") == [("cut_in_before", "should")]
    assert check.run(one_cell, fps=FPS, cuts=[F(245)])["ok"]            # no shot-change rule is an obligation here


# --------------------------------------------------------------------------- files and the command line

CELLS = [cell(["- [빌] 뭐?", "- [쿵]"], 10.01, 11.512, "C0001"), cell("둘째 <줄> & 셋째", 12.0, 13.5, "C0002", pos="top"),
         cell("(빌) 그래.", 20.0, 21.5, "C0003")]


def test_srt_vtt_ttml_and_draft_files_are_read_alike(tmp_path):
    fps = 23.976
    want = [("#3", "line_end_punct"), ("#3", "label_parens")]
    for fmt in export.FORMATS:
        path = export.write(CELLS, tmp_path / f"a.{fmt}", fmt, fps=fps)["path"]
        cells, meta = check.load(path)
        assert [c["lines"] for c in cells] == [c["lines"] for c in CELLS], fmt
        assert [round(c["start"] * fps) for c in cells] == [round(c["start"] * fps) for c in CELLS], fmt
        res = check.run(path, fps=None if fmt == "ttml" else fps)
        assert res["fps"] == pytest.approx(fps, abs=1e-3) and not res["fps_assumed"]
        got = [(v["cell"], v["code"]) for v in res["violations"]]
        assert got == (want if fmt != "ttml" else [("c3", "line_end_punct"), ("c3", "label_parens")]), fmt
    draft = {"cells": CELLS, "fps": fps, "profile": "adult"}
    (tmp_path / "draft.json").write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
    res = check.run(tmp_path / "draft.json")
    assert res["fps"] == fps and [(v["cell"], v["code"]) for v in res["violations"]] == [
        ("C0003", "line_end_punct"), ("C0003", "label_parens")]
    (tmp_path / "b.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):                    # not a subtitle file
        check.run(tmp_path / "b.txt")


def test_vtt_without_hours_and_with_tags():
    text = ("WEBVTT\n\nNOTE a comment\n\nid-1\n00:10.000 --> 00:12.000 line:10%\n<v 빌>뭐? &lt;진짜&gt;</v>\n\n"
            "1:00:00.000 --> 1:00:02.000\n응\n")
    cells = check.parse_subtitles(text, vtt=True)
    assert [(c["start"], c["end"], c["lines"]) for c in cells] == [(10.0, 12.0, ["뭐? <진짜>"]), (3600.0, 3602.0, ["응"])]
    srt = "1\r\n00:00:01,000 --> 00:00:02,500\r\n첫 줄\r\n둘째 <줄>\r\n\r\n2\r\n00:00:03,000 --> 00:00:04,000\r\n셋\r\n"
    cells = check.parse_subtitles(srt)
    assert [(c["id"], c["lines"]) for c in cells] == [("#1", ["첫 줄", "둘째 <줄>"]), ("#2", ["셋"])]


def test_cuts_file_forms(tmp_path):
    (tmp_path / "cuts.json").write_text(json.dumps({"fps": 24, "cuts": [1.5, 2.25]}), encoding="utf-8")
    (tmp_path / "list.json").write_text("[3, 4.5]", encoding="utf-8")
    (tmp_path / "cuts.txt").write_text("5.0\n6.5\n", encoding="utf-8")
    assert check.load_cuts(tmp_path / "cuts.json") == [1.5, 2.25]
    assert check.load_cuts(tmp_path / "list.json") == [3.0, 4.5]
    assert check.load_cuts(tmp_path / "cuts.txt") == [5.0, 6.5]
    assert check.load_cuts([7, "x", 8.5]) == [7.0, 8.5] and check.load_cuts(None) == []


def test_command_line(tmp_path, capsys):
    from cboe.__main__ import main
    bad = export.write(CELLS, tmp_path / "bad.srt", "srt", fps=24.0)["path"]
    good = export.write(CELLS[:2], tmp_path / "good.srt", "srt", fps=24.0)["path"]
    assert main(["check", str(good), "--fps", "24"]) == 0
    out = capsys.readouterr().out
    assert "good.srt · 2칸 · 24fps · 성인 프로그램 (초당 14자)" in out and "걸린 조항이 없습니다" in out
    assert "장면 전환 규칙은 보지 않음" in out
    assert main(["check", str(bad), "--fps", "24"]) == 1               # an obligation is broken
    out = capsys.readouterr().out
    assert "의무 2건 (1칸) · 권고 0건 (0칸)" in out
    assert "[의무] I.13 줄 끝 마침표·쉼표: 1건" in out and "[의무] II.8 ( ) 로 감싼 표시 ([ ] 로): 1건" in out
    out.encode("cp949")                                                # a Korean Windows console can print it
    assert "#3 00:00:20.000  (빌) 그래." in out
    assert main(["check", str(bad)]) == 1 and "fps 를 몰라 24로 가정" in capsys.readouterr().out
    (tmp_path / "cuts.json").write_text(json.dumps({"cuts": [10.2]}), encoding="utf-8")
    assert main(["check", str(good), "--fps", "24", "--cuts", str(tmp_path / "cuts.json"), "--children"]) == 0
    out = capsys.readouterr().out                                      # a recommendation alone does not fail the file
    assert "어린이 프로그램 (초당 11자)" in out and "의무 0건 (0칸) · 권고 1건 (1칸)" in out
    assert "[권고] TG 4 전환 0.5초 안 앞에서 시작해 전환을 넘음: 1건" in out and "#1 00:00:10.010  전환 5프레임 앞" in out
    assert "장면 전환 규칙은 보지 않음" not in out
    slow = export.write([cell("가나다라마바사아자차카타", 10.0, 11.0)], tmp_path / "slow.srt", "srt", fps=24.0)["path"]
    assert main(["check", str(slow), "--fps", "24"]) == 0
    assert main(["check", str(slow), "--fps", "24", "--children"]) == 1
    assert "[의무] II.3 초당 11자 초과: 1건" in capsys.readouterr().out
    assert main(["check", str(bad), "--fps", "24", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["summary"]["must"] == 2
    assert main(["check", str(tmp_path / "none.srt")]) == 2 and "검사할 수 없습니다" in capsys.readouterr().err
    (tmp_path / "x.doc").write_text("x", encoding="utf-8")
    assert main(["check", str(tmp_path / "x.doc")]) == 2


def test_report_lists_obligations_first_and_can_be_cut_short():
    cells = [cell("[천둥 소리]", 10.0 + 3 * k, 12.0 + 3 * k, f"C{k}") for k in range(12)] + [cell("가자.", 60.0, 62.0, "C99")]
    res = check.run(cells, fps=FPS)
    text = check.report(res, "x.srt")
    assert text.index("[의무] I.13") < text.index("[권고] II.9") and "… 그 밖 4건 (--all 로 모두)" in text
    assert "그 밖" not in check.report(res, "x.srt", limit=None) and check.report(res, limit=None).count("[천둥 소리]") == 12


# --------------------------------------------------------------------------- independence and placement

def test_checker_shares_no_code_with_placement():
    tree = ast.parse((ROOT / "cboe" / "check.py").read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names |= {"." * node.level + ".".join(x for x in (node.module, a.name) if x) for a in node.names}
        elif isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
    ours = {n for n in names if n.startswith(".") or "cboe" in n}
    assert ours == {".style", ".textutil.units"}      # the numbers and the II.2 count, nothing of placement
    assert "tidy" not in (ROOT / "cboe" / "check.py").read_text(encoding="utf-8").replace("style.tidy_line", "")


def test_tidied_lines_never_break_the_punctuation_rules():
    # the checker's patterns and style.tidy_line are written apart; on random lines they must agree
    import random
    import re
    pieces = ["가자", "그래", "뭐", "응", "사랑해", "3", "5", "J", "Mr", "OK", ".", ".", ",", "..", "...", "…", "?", "!",
              " ", " ", " ", " ", "[쿵]", "[한숨]", "♪", "'", "\"", "’", "”", "-", "~", "。", "，", "(웃음)"]
    rnd = random.Random(20261110)
    for _ in range(20000):
        line = "".join(rnd.choice(pieces) for _ in range(rnd.randint(1, 12)))
        shown = style.tidy_line(line)
        assert style.tidy_line(shown) == shown, line                     # nothing left to do
        if shown:
            assert re.sub(r"[.,…。，\s]", "", shown) == re.sub(r"[.,…。，\s]", "", line), line   # the words stay
            broken = {c for c, _k, _t in check._text_rules([shown])} & {"line_end_punct", "sentence_period", "dots"}
            assert not broken, (line, shown, broken)


def test_placement_and_checker_agree_on_random_scenes():
    # made-up scenes with lines that overlap, guessed times, labels, lyrics, sounds and cuts
    import random
    rnd = random.Random(7)
    words = ["가나다", "라마", "바사아자", "차", "카타파", "하", "그래서", "말이야.", "뭐라고?", "아니,", "응...", "정말!",
             "그건", "내가", "어제", "분명히"]
    limits = {"line_length", "line_count", "reading_speed", "min_duration", "max_duration"}
    never = {"no_text", "bad_time", "order", "overlap", "frame_gap", "line_end_punct", "sentence_period", "dots",
             "label_parens", "bracket", "note_glyph", "lyric_notes", "hyphen", "foreign_generic", "joint_id",
             "bleep_label"}
    flash = 0
    for scene in range(120):
        fps = rnd.choice([23.976, 24.0, 25.0, 29.97, 30.0, 60.0])
        lines, t = [], 5.0
        for k in range(rnd.randint(1, 12)):
            n = rnd.randint(1, 9)
            dur = max(0.05, rnd.gauss(0.35 * n, 0.4))
            start = max(0.0, t + rnd.choice([-1.2, -0.6, -0.2, -0.04, 0.0, 0.05, 0.3, 0.8, 2.0, 4.0]) * rnd.random())
            who = rnd.choice(["A", "B", "C", None])
            lines.append({"id": f"L{k}", "text": " ".join(rnd.choice(words) for _ in range(n)),
                          "start": round(start, 3), "end": round(start + dur, 3), "words": [], "speaker": who,
                          "name": who, "show_name": bool(who) and rnd.random() < 0.4,
                          "timing": rnd.choice(["asr", "asr", "voice", "even"]),
                          "language": "영어" if rnd.random() < 0.15 else None, "sung": rnd.random() < 0.1,
                          "label_extra": ["속마음"] if rnd.random() < 0.1 else []})
            t = start + dur
        sounds = [{"id": f"S{k}", "kind": rnd.choice(["sfx", "music"]), "onset": round(rnd.uniform(4.0, t + 3), 3),
                   "phrase": rnd.choice(["[쿵]", "[우르릉 - 천둥]", "♬ 무서운 음악", "[음악]"]), "short": "[쿵]"}
                  for k in range(rnd.randint(0, 5))]
        for s in sounds:
            s["t1"] = s["onset"] + 0.5
        cuts = sorted(round(rnd.uniform(4.0, t + 3), 3) for _ in range(rnd.randint(0, 8)))
        d = place(lines, sounds, cuts, fps, settings=style.get(rnd.choice(["adult", "children"])).settings())
        res = check.run(d, cuts=cuts)
        assert res["fps"] == fps and res["profile"] == d["profile"]
        found = {(v["cell"], v["code"]) for v in res["violations"]}
        assert not {c for _cell, c in found} & never, (scene, found)        # the notation always holds
        mine = {c["id"] for c in d["cells"]
                if any(not x.startswith(("간격", "빈틈")) for x in c["check"]["violations"])}
        assert {cell for cell, c in found if c in limits} == mine, scene     # the same cells are over a limit
        marked = {c["id"] for c in d["cells"] if "flash" in c["flags"]}
        assert {cell for cell, c in found if c == "flash"} == marked, scene  # and no short cell is unmarked
        flash += len(marked)
    assert flash                                                             # (the scenes do produce some)


def test_placement_output_breaks_no_notation_rule():
    fps = 23.976

    def line(lid, text, start, end, **kw):
        return {"id": lid, "text": text, "start": start, "end": end, "words": [], **kw}

    lines = [line("L1", "오늘은 정말 이상한 날이었어. 아침부터 계속 이상한 소리가 들렸거든...", 10.0, 15.0, name="양희", show_name=True),
             line("L2", "뭐라고? 그게, 무슨 말이야.", 16.0, 18.0, name="피터", show_name=True, language="영어"),
             line("L3", "달빛 아래 걷던 그 길을 오늘도 혼자서", 20.0, 25.0, name="영수", show_name=True, sung=True),
             line("L4", "설마!", 27.0, 27.6, name="영희/철수", show_name=True),
             line("L5", "네.", 30.0, 30.3, speaker="A"), line("L6", "그래, 가자.", 30.1, 31.0, speaker="B")]
    sounds = [{"id": "S1", "kind": "sfx", "onset": 26.0, "t1": 26.5, "phrase": "[쿵]", "short": "[쿵]"},
              {"id": "M1", "kind": "music", "onset": 33.0, "t1": 40.0, "phrase": "♬ 무서운 음악", "short": "♬ 음악"}]
    d = place(lines, sounds, [], fps)
    res = check.run(d)
    text_codes = {"line_end_punct", "sentence_period", "dots", "label_parens", "bracket", "note_glyph", "lyric_notes",
                  "hyphen", "foreign_generic", "joint_id", "bleep_label", "line_length", "line_count"}
    assert not [v for v in res["violations"] if v["code"] in text_codes], res["violations"]
    assert res["fps"] == fps and res["ok"]
    shown = [x for c in d["cells"] for x in c["lines"]]
    assert "- 네" in shown and "- 그래, 가자" in shown and "[영희, 철수] 설마!" in shown and "[무서운 음악]" in shown
