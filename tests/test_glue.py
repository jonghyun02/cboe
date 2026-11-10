# -*- coding: utf-8 -*-
"""Seams left after the first round: a fourth kind of shooting script (speaker cells set off
by a gap, a title page before the first scene), what a direction says about the voice, and
the small joints between the script reader, the GUI, the command line and the tools.

Every script text here is made up for the test; no line of a real script is used.
"""
from __future__ import annotations

from cboe import screenplay as sp
from cboe import script

# a script whose speaker cells are set off by spaces, with a title page and long speeches
GAP_SCRIPT = [
    "", "‘바닷가’", "<seaside>", "",
    "\t\t\t\t각본/감독\t홍길순",
    "\t\t\t\t제  작\t\t물결 영화사",
    "",
    "1. 포구 - 밤",
    "배가 들어온다.",
    "",
    "나래      왔다.",
    "도윤      오랜만이야.",
    "나래      응. 가자.",
    "",
    "2. 방송실 - 낮",
    "D.J      여러분 안녕하세요. 오늘도 어김없이 찾아온",
    "          점심 방송입니다. 첫 곡 띄워 드립니다.",
    "",
    "도윤이 문을 연다.",
    "",
    "판사    피고는 이 바다를 함부로 쓴 죄로",              # a name used once, its speech goes on below
    "        벌금 백 원을 내야 합니다.",
    "",
    "도윤      (작게) 이게 뭐야.",
    "꼬마      뭐가요?",                                     # used once, in an exchange
    "나래      아무것도 아냐.",
    "",
    "D.J       다시 돌아왔습니다.",
    "도윤\t  라디오 꺼.",                                    # a tab after a name that comes back
    "D.J       한 곡 더.",
    "\t  마지막 곡입니다.",                                   # and a tab to go on
    "",
    "3. 골목 - 밤",
    "손님      한 번만 말하는 사람.",                         # used once, alone: no evidence it is a name
    "나래가 짐을 든다   그리고 걷는다.",                      # a gap in a sentence
]


def test_title_page_before_the_first_scene_is_no_dialogue():
    doc = sp.build(sp.classify_plain(GAP_SCRIPT))
    assert not {"각본", "감독", "각본/감독", "제 작"} & set(doc["characters"])
    cover = [(e["type"], e["text"]) for e in doc["elements"] if e["scene_id"] is None]
    assert cover == [("action", "‘바닷가’"), ("action", "<seaside>"),
                     ("other", "각본/감독 홍길순"), ("other", "제 작 물결 영화사")]
    assert all(ln["scene_id"] for ln in doc["lines"])
    # the title page is no evidence of the layout: the tabs there do not make this a tab script
    ev = sp.plain_evidence(GAP_SCRIPT)
    assert ev["tab"] == 1 and ev["gap"] > ev["tab"]


def test_a_script_without_scene_headings_keeps_its_first_lines():
    rows = ["나래      왔다.", "도윤      오랜만이야.", "나래      응.", "도윤      가자."]
    doc = sp.build(sp.classify_plain(rows))
    assert [(ln["speaker"], ln["text"]) for ln in doc["lines"]] == [
        ("나래", "왔다."), ("도윤", "오랜만이야."), ("나래", "응."), ("도윤", "가자.")]


def test_long_speeches_names_used_once_and_names_with_dots():
    doc = sp.build(sp.classify_plain(GAP_SCRIPT))
    assert [(ln["speaker"], ln["text"]) for ln in doc["lines"]] == [
        ("나래", "왔다."), ("도윤", "오랜만이야."), ("나래", "응. 가자."),
        ("D.J", "여러분 안녕하세요. 오늘도 어김없이 찾아온 점심 방송입니다. 첫 곡 띄워 드립니다."),
        ("판사", "피고는 이 바다를 함부로 쓴 죄로 벌금 백 원을 내야 합니다."),
        ("도윤", "이게 뭐야."), ("꼬마", "뭐가요?"), ("나래", "아무것도 아냐."),
        ("D.J", "다시 돌아왔습니다."), ("도윤", "라디오 꺼."), ("D.J", "한 곡 더. 마지막 곡입니다.")]
    kinds = [(e["type"], e["text"]) for e in doc["elements"] if e["scene_id"] == "SC003"]
    assert kinds == [("heading", "3. 골목 - 밤"), ("action", "손님 한 번만 말하는 사람."),
                     ("action", "나래가 짐을 든다 그리고 걷는다.")]
    assert not [e for e in doc["elements"] if e["type"] == "other" and e["scene_id"]]
    assert sp.name_like("D.J") and sp.name_like("P.D.") and not sp.name_like("왔다.")


def test_a_name_with_a_space_is_one_person():
    rows = ["1. 차 안 - 낮", "봉 기사   어디로 모실까요?", "나래      바다로요.", "봉기사    네.", "나래      고마워요."]
    doc = sp.build(sp.classify_plain(rows))
    assert [ln["speaker"] for ln in doc["lines"]] == ["봉 기사", "나래", "봉 기사", "나래"]
    assert doc["people"][0]["cues"] == ["봉 기사", "봉기사"]


def L(page, y0, x0, text, bold=False):
    return {"page": page, "y0": float(y0), "x0": float(x0), "x1": float(x0 + 10 * len(text)), "text": text,
            "bold": bold, "size": 10.0, "space_end": False, "lead": 0}


def test_title_page_of_a_positioned_script():
    rows = [L(1, 100, 72, "각본", bold=True), L(1, 100, 150, "홍길순"),
            L(1, 120, 72, "제작", bold=True), L(1, 120, 150, "물결 영화사"),
            L(2, 100, 72, "1. 포구 / 밤", bold=True),
            L(2, 120, 72, "나래", bold=True), L(2, 120, 150, "왔다."),
            L(2, 140, 72, "도윤", bold=True), L(2, 140, 150, "오랜만이야."),
            L(2, 160, 72, "나래", bold=True), L(2, 160, 150, "가자.")]
    doc = sp.build(sp.classify_positioned(rows))
    assert doc["characters"] == ["나래", "도윤"]
    assert [(e["type"], e["text"]) for e in doc["elements"][:2]] == [("other", "각본 홍길순"), ("other", "제작 물결 영화사")]


def test_summary_does_not_count_the_title_page_as_unread(tmp_path):
    f = tmp_path / "s.txt"
    f.write_text("\n".join(["\t각본\t홍길순"] * 12 + ["1. 포구 - 밤"] + ["나래      왔다.", "도윤      응."] * 3),
                 encoding="utf-8")
    doc = script.read(f)
    assert doc["format"] == "screenplay" and len(doc["lines"]) == 6
    assert not any("가르지 못한" in w for w in script.summary(doc)["warnings"])
