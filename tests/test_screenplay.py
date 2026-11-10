# -*- coding: utf-8 -*-
"""촬영 대본 읽기 (cboe.screenplay), the script stage and the run log.

Every script text here is made up for the test; no line of a real script is used.
"""
from __future__ import annotations

import json
import os
import struct
import types
import zlib

import pytest

from cboe import pipeline, script
from cboe import screenplay as sp
from cboe.stages import script as script_stage
from cboe.state import Project
from cboe.util import read_json, write_json


# ------------------------------------------------------------ characters -> rows
def glyphs(text, x, y, size=10.0, bold=False):
    """Characters laid out left to right: Hangul one em wide, ASCII half, a space 0.3."""
    out = []
    for ch in text:
        w = size * (0.3 if ch == " " else 0.5 if ch.isascii() else 1.0)
        out.append({"x0": x, "x1": x + w, "y": y, "text": ch, "size": size, "bold": bold})
        x += w
    return out


def test_rows_are_rebuilt_from_positions_not_from_file_order():
    line = glyphs("잠깐, 저기 봐!", 150, 700)
    letters = [c for c in line if c["text"] not in ",! "]
    rest = [c for c in line if c["text"] in ",! "]
    cue = glyphs("나래", 70, 700, bold=True)
    rows = sp.page_rows(letters + cue + rest, 3, 800.0)       # punctuation and spaces come last in the file
    assert [(r["text"], r["x0"], r["bold"]) for r in rows] == [("나래", 70.0, True), ("잠깐, 저기 봐!", 150.0, False)]
    assert rows[0]["y0"] == rows[1]["y0"] == 100.0 and rows[0]["page"] == 3 and rows[1]["x1"] == 216.0


def test_rows_word_gaps_spaces_and_sizes():
    # a small jump without a space glyph is a word break; a trailing space is remembered
    row = glyphs("하나", 150, 600) + glyphs("둘 ", 174, 600)
    # twelve spaces push the text to its column by hand; one stray space does not move a line
    pushed = glyphs(" " * 12 + "이어서", 70, 580)
    stray = glyphs(" 지문이다.", 70, 560)
    # letters set wide apart are one word; a glyph drawn twice (faked bold) is one glyph
    title = [g for k, ch in enumerate("가나다라") for g in glyphs(ch, 200 + 20 * k, 540, 12.0)]
    twice = glyphs("굵", 70, 520) + glyphs("굵", 70.4, 520) + glyphs("다", 80, 520)
    # two sizes on one baseline are one row; rows come top to bottom whatever the input order
    mixed = glyphs("큰", 70, 500, 14.0) + glyphs("작은", 84, 500, 10.0)
    rows = sp.page_rows(mixed + twice + title + stray + pushed + row, 1, 800.0)
    assert [r["text"] for r in rows] == ["하나 둘", "이어서", "지문이다.", "가나다라", "굵다", "큰작은"]
    assert rows[0]["space_end"] and not rows[1]["space_end"]
    assert rows[1]["lead"] == 12 and rows[1]["x0"] == pytest.approx(106.0)        # 70 + 12 spaces of 3
    assert rows[2]["lead"] == 1 and rows[2]["x0"] == 70.0
    assert rows[5]["size"] == 10.0 and [r["y0"] for r in rows] == sorted(r["y0"] for r in rows)


def test_page_numbers_and_running_titles_are_dropped():
    def row(page, y0, text):
        return {"page": page, "x0": 280.0, "x1": 300.0, "y0": y0, "text": text, "bold": False, "size": 10.0}

    rows = []
    for page in (1, 2, 3, 4):
        rows += [row(page, 30.0, "어느 겨울 (3고)"), row(page, 400.0, "지문"), row(page, 770.0, f"- {page} -")]
        # a speaker cell that tops every page stays: its dialogue is on the same line
        rows += [{**row(page, 50.0, "나래"), "x0": 70.0}, {**row(page, 50.0, "12"), "x0": 150.0}]
    rows.append(row(2, 420.0, "1999"))                        # a number in the body stays
    kept = sp.drop_page_furniture(rows, {p: 800.0 for p in (1, 2, 3, 4)})
    assert sorted({r["text"] for r in kept}) == ["12", "1999", "나래", "지문"] and len(kept) == 13


# ------------------------------------------------------------- a real (tiny) PDF
def _pdf(pages: list[str], size=(595, 842)) -> bytes:
    """A PDF with one content stream per page and the fonts F1 Helvetica, F2 Helvetica-Bold."""
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            ("<< /Type /Pages /Kids [%s] /Count %d >>" % (" ".join(f"{5 + 2 * k} 0 R" for k in range(len(pages))),
                                                          len(pages))).encode(),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>"]
    for k, content in enumerate(pages):
        data = content.encode("ascii")
        objs.append((f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {size[0]} {size[1]}] "
                     f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {6 + 2 * k} 0 R >>").encode())
        objs.append(b"<< /Length %d >>\nstream\n" % len(data) + data + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def _t(font, size, x, y, text) -> str:
    text = text.replace("(", r"\(").replace(")", r"\)")
    return f"BT /{font} {size} Tf 1 0 0 1 {x} {y} Tm ({text}) Tj ET\n"


PDF_PAGE_1 = (
    _t("F1", 10, 160, 700, "Wait") + _t("F1", 10, 190, 700, "look")        # the words first,
    + _t("F2", 12, 72, 760, "1. HARBOR / NIGHT")
    + _t("F1", 10, 72, 730, "A boat comes in.")
    + _t("F2", 10, 72, 700, "MINA")
    + _t("F2", 10, 72, 684, "JUNO") + _t("F1", 10, 160, 684, "It is late")
    + _t("F1", 10, 160, 668, "and cold.")
    + _t("F2", 10, 72, 640, "MINA") + _t("F1", 10, 160, 640, "(softly) Go.")
    + _t("F1", 10, 290, 40, "- 1 -")
    + _t("F1", 10, 180, 700, ",") + _t("F1", 10, 208.4, 700, "!"))          # their punctuation at the end
PDF_PAGE_2 = (_t("F2", 12, 72, 760, "2. PIER / DAY") + _t("F1", 10, 72, 730, "They walk.")
              + _t("F2", 10, 72, 700, "JUNO") + _t("F1", 10, 160, 700, "Fine.") + _t("F1", 10, 290, 40, "- 2 -"))


def test_pdf_lines_and_document_from_a_made_up_pdf(tmp_path):
    f = tmp_path / "s.pdf"
    f.write_bytes(_pdf([PDF_PAGE_1, PDF_PAGE_2]))
    lines = sp.pdf_lines(f)
    assert [(r["page"], r["text"]) for r in lines] == [
        (1, "1. HARBOR / NIGHT"), (1, "A boat comes in."), (1, "MINA"), (1, "Wait, look!"), (1, "JUNO"),
        (1, "It is late"), (1, "and cold."), (1, "MINA"), (1, "(softly) Go."),
        (2, "2. PIER / DAY"), (2, "They walk."), (2, "JUNO"), (2, "Fine.")]          # no page numbers
    head, cue, said = lines[0], lines[2], lines[3]
    assert head["bold"] and head["size"] == 12.0 and head["x0"] == 72.0 and head["y0"] == 82.0
    assert cue["bold"] and not said["bold"] and cue["y0"] == said["y0"] == 142.0 and said["x0"] == 160.0
    assert sp.find_columns(lines)["left"] == 72.0 and sp.find_columns(lines)["dialogue"] == 160.0

    doc = script.read(f)
    assert doc["version"] == 2 and doc["format"] == "screenplay" and doc["layout"] == "columns"
    assert [(s["number"], s["place"], s["time"]) for s in doc["scenes"]] == [("1", "HARBOR", "NIGHT"), ("2", "PIER", "DAY")]
    assert [(ln["speaker"], ln["text"], ln["scene_id"]) for ln in doc["lines"]] == [
        ("MINA", "Wait, look!", "SC001"), ("JUNO", "It is late and cold.", "SC001"), ("MINA", "Go.", "SC001"),
        ("JUNO", "Fine.", "SC002")]
    assert doc["lines"][2]["raw"] == "(softly) Go." and doc["lines"][2]["directions"] == [{"text": "softly", "type": "action"}]
    assert [e["type"] for e in doc["elements"]] == ["heading", "action", "dialogue", "dialogue", "dialogue",
                                                    "heading", "action", "dialogue"]
    assert doc["elements"][2]["page"] == 1 and doc["elements"][-1]["page"] == 2
    assert doc["source"]["size"] == f.stat().st_size and len(doc["source"]["sha1"]) == 40


def test_dots_set_close_together_are_kept_a_glyph_drawn_twice_is_not(tmp_path):
    # Helvetica's dot is 2.78 wide at size 10: three dots a step of 1.5 apart (closer than 0.2 em)
    # are an ellipsis; an N drawn again 0.3 to the side is a faked bold N
    dots = "".join(_t("F1", 10, 179.5 + 1.5 * k, 700, ".") for k in range(3))
    two = "".join(_t("F1", 10, 179.5 + 1.5 * k, 680, ".") for k in range(2))
    bold = _t("F1", 10, 160, 660, "N") + _t("F1", 10, 160.3, 660, "N") + _t("F1", 10, 167.5, 660, "o")
    page = (_t("F2", 12, 72, 760, "1. ROOM / DAY")
            + _t("F2", 10, 72, 700, "MINA") + _t("F1", 10, 160, 700, "Well") + dots
            + _t("F2", 10, 72, 680, "JUNO") + _t("F1", 10, 160, 680, "Sure") + two
            + _t("F2", 10, 72, 660, "MINA") + bold)
    f = tmp_path / "dots.pdf"
    f.write_bytes(_pdf([page]))
    doc = script.read(f)
    assert [(ln["speaker"], ln["raw"]) for ln in doc["lines"]] == [("MINA", "Well..."), ("JUNO", "Sure.."), ("MINA", "No")]
    dot = {"x0": 10.0, "x1": 12.18, "y": 500.0, "text": ".", "size": 9.74, "bold": False}     # 9.74 pt, 2.18 wide
    assert not sp._drawn_twice(dot, {**dot, "x0": 11.5, "x1": 13.68})
    assert sp._drawn_twice(dot, {**dot, "x0": 10.5, "x1": 12.68})
    wide = glyphs("굵", 70, 500)[0]
    assert sp._drawn_twice(wide, {**wide, "x0": 70.4, "x1": 80.4})


def test_pdf_without_text_is_refused(tmp_path):
    f = tmp_path / "scan.pdf"
    f.write_bytes(_pdf([""]))
    with pytest.raises(ValueError, match="글자가 없는 PDF"):
        sp.pdf_lines(f)


# ------------------------------------------------------- lines with positions
def L(page, y0, x0, text, bold=False, size=10.0, x1=None, space_end=False, lead=0):
    return {"page": page, "y0": float(y0), "x0": float(x0), "x1": float(x1 if x1 is not None else x0 + 10 * len(text)),
            "text": text, "bold": bold, "size": size, "space_end": space_end, "lead": lead}


POSITIONED = [
    L(1, 60, 180, "어느 겨울", bold=True, size=14.0),
    L(1, 100, 72, "1. 포구 / 밤", bold=True, size=11.0),
    L(1, 120, 72, "배가 들어온다. 멀리서 뱃고동이 울린다."),
    L(1, 140, 72, "나래", bold=True), L(1, 140, 150, "(작게) 왔다."),
    L(1, 160, 72, "도윤(V.O)", bold=True), L(1, 160, 150, "오랜만이야, 정말 오랜만에 보는 얼굴이", x1=500),
    L(1, 176, 150, "라서 놀랐어."),                                    # cut inside a word at the right edge
    L(1, 196, 72, "(할머니)", bold=True), L(1, 196, 150, "밥은 먹었니? 어서 들어와서", x1=500, space_end=True),
    L(1, 212, 150, "앉아라."),                                         # cut at a space
    L(1, 232, 72, "생선 가게 주인", bold=True), L(1, 232, 190, "어서 오세요, 오늘 물건이"),   # a long name pushes the dialogue
    L(1, 248, 190, "좋습니다."),
    L(1, 270, 72, "화면이 어두워지고 자막이 뜬다,"),
    L(1, 286, 220, "“ 삼 년 뒤 ”", bold=True),
    L(1, 306, 72, "- 선착장", bold=True),
    L(1, 326, 72, "나래가 짐을 내려놓고 주위를 둘러보다가 문득 걸음을 멈", x1=500),
    L(1, 342, 72, "춘다."),
    L(1, 362, 72, "F.O"),
    L(2, 100, 72, "2. 선창가 / 낮", bold=True, size=11.0),
    L(2, 120, 72, "3. 문 앞에서 기다리는 사람들이 웅성거린다."),       # a numbered sentence is no heading
    L(2, 140, 72, "나래", bold=True), L(2, 140, 150, "(한숨)"),
    L(2, 160, 72, "도윤", bold=True), L(2, 160, 150, "가자."),
    L(2, 180, 110, "어서."),                                           # set too far left: still his line
    L(2, 200, 72, "3. 방파제", bold=True, size=11.0),
]


def test_classify_positioned_lines():
    assert sp.find_columns(POSITIONED) == {"left": 72.0, "dialogue": 150.0, "right": 500.0}
    items = sp.classify_positioned(POSITIONED)
    got = [(it["type"], it.get("cue"), it["text"]) for it in items]
    assert got == [
        ("other", None, "어느 겨울"),
        ("heading", None, "1. 포구 / 밤"),
        ("action", None, "배가 들어온다. 멀리서 뱃고동이 울린다."),
        ("dialogue", "나래", "(작게) 왔다."),
        ("dialogue", "도윤(V.O)", "오랜만이야, 정말 오랜만에 보는 얼굴이라서 놀랐어."),
        ("dialogue", "(할머니)", "밥은 먹었니? 어서 들어와서 앉아라."),
        ("dialogue", "생선 가게 주인", "어서 오세요, 오늘 물건이 좋습니다."),
        ("action", None, "화면이 어두워지고 자막이 뜬다,"),
        ("onscreen", None, "“ 삼 년 뒤 ”"),
        ("other", None, "- 선착장"),
        ("action", None, "나래가 짐을 내려놓고 주위를 둘러보다가 문득 걸음을 멈춘다."),
        ("transition", None, "F.O"),
        ("heading", None, "2. 선창가 / 낮"),
        ("action", None, "3. 문 앞에서 기다리는 사람들이 웅성거린다."),
        ("dialogue", "나래", "(한숨)"),
        ("dialogue", "도윤", "가자. 어서."),
        ("heading", None, "3. 방파제"),
    ]
    head = items[1]
    assert (head["number"], head["place"], head["time"], head["page"]) == ("1", "포구", "밤", 1)

    doc = sp.build(items)
    assert [(s["id"], s["number"], s["place"], s["time"]) for s in doc["scenes"]] == [
        ("SC001", "1", "포구", "밤"), ("SC002", "2", "선창가", "낮"), ("SC003", "3", "방파제", None)]
    assert [(ln["id"], ln["speaker"], ln["text"], ln["source"], ln["scene_id"]) for ln in doc["lines"]] == [
        ("L0001", "나래", "왔다.", None, "SC001"),
        ("L0002", "도윤", "오랜만이야, 정말 오랜만에 보는 얼굴이라서 놀랐어.", "offscreen", "SC001"),
        ("L0003", "할머니", "밥은 먹었니? 어서 들어와서 앉아라.", "offscreen", "SC001"),
        ("L0004", "생선 가게 주인", "어서 오세요, 오늘 물건이 좋습니다.", None, "SC001"),
        ("L0005", "도윤", "가자. 어서.", None, "SC002")]
    assert doc["lines"][0]["delivery"] == ["작게"] and doc["lines"][0]["raw"] == "(작게) 왔다."
    # a dialogue without words is no line: it stays as an action of that speaker
    sigh = next(e for e in doc["elements"] if e.get("speaker") == "나래" and e["type"] == "action")
    assert sigh["text"] == "(한숨)" and sigh["directions"] == [{"text": "한숨", "type": "vocal"}] and "line_id" not in sigh
    by_id = {e["id"]: e for e in doc["elements"]}
    assert all(by_id[ln["element_id"]]["line_id"] == ln["id"] for ln in doc["lines"])
    assert [e["id"] for e in doc["elements"]] == [f"E{k:04d}" for k in range(1, len(doc["elements"]) + 1)]
    assert doc["characters"] == ["나래", "도윤", "할머니", "생선 가게 주인"]
    assert doc["people"][1] == {"id": "P02", "name": "도윤", "cues": ["도윤(V.O)", "도윤"]}


def test_columns_come_from_the_document():
    """Another layout: other margins, no bold, headings set in from the margin, a number skipped
    and one heading set like the body text."""
    rows = [L(1, 50, 65, "1. 다방"), L(1, 70, 50, "문이 열린다."),
            L(1, 90, 50, "주인"), L(1, 90, 200, "어서 와요."),
            L(1, 110, 50, "손님"), L(1, 110, 200, "늘 마시던 걸로요."),
            L(1, 130, 65, "2. 골목"), L(1, 150, 50, "비가 내린다."),
            L(1, 170, 50, "3. 다방 안"),                               # the author forgot the indent
            L(1, 190, 50, "주인"), L(1, 190, 200, "비가 오네."),
            L(1, 210, 65, "5. 역 앞"), L(1, 230, 50, "손님"), L(1, 230, 200, "그러게요.")]
    cols = sp.find_columns(rows)
    assert (cols["left"], cols["dialogue"]) == (50.0, 200.0)
    items = sp.classify_positioned(rows)
    assert [it["number"] for it in items if it["type"] == "heading"] == ["1", "2", "3", "5"]
    assert [(it["cue"], it["text"]) for it in items if it["type"] == "dialogue"] == [
        ("주인", "어서 와요."), ("손님", "늘 마시던 걸로요."), ("주인", "비가 오네."), ("손님", "그러게요.")]


def test_a_line_cut_by_hand_is_joined_by_its_words(monkeypatch):
    """A continuation pushed to its column with spaces was cut by hand: whether the cut is
    inside a word is asked of the words (Kiwi), not of the page."""
    def doc(first, second, lead):
        rows = [L(1, 50, 60, "주인"), L(1, 50, 200, "앞 줄", x1=480), L(1, 66, 200, "뒷줄."),
                L(1, 90, 60, "손님"), L(1, 90, 200, "가득 찬 줄", x1=500), L(1, 106, 200, "다음."),
                L(1, 130, 60, "주인"), L(1, 130, 200, "가득 찬 줄", x1=500, space_end=True), L(1, 146, 200, "다음."),
                L(1, 170, 60, "지문 하나", x1=500), L(1, 190, 60, "지문 둘", x1=500),
                L(1, 210, 60, "손님"), L(1, 210, 200, first, x1=490), L(1, 226, 200, second, lead=lead)]
        return [it["text"] for it in sp.classify_positioned(rows) if it["type"] == "dialogue"]

    asked = []

    class Kiwi:
        def space(self, text):
            asked.append(text)
            return {"해바라기가": "해바라기가", "다시만나기로": "다시 만나기로"}[text]

    monkeypatch.setattr("cboe.textutil._kiwi", lambda: Kiwi())
    # by the page: short of the edge -> a space; at the edge without a space -> inside a word; with one -> a space
    assert doc("노란 해바", "라기가 핀다.", 0)[:3] == ["앞 줄 뒷줄.", "가득 찬 줄다음.", "가득 찬 줄 다음."]
    assert asked == []
    assert doc("노란 해바", "라기가 핀다.", 16)[-1] == "노란 해바라기가 핀다."
    assert doc("내일 다시", "만나기로 했다.", 16)[-1] == "내일 다시 만나기로 했다."
    assert asked == ["해바라기가", "다시만나기로"]
    monkeypatch.setattr("cboe.textutil._kiwi", lambda: None)      # without kiwipiepy: a space
    assert doc("노란 해바", "라기가 핀다.", 16)[-1] == "노란 해바 라기가 핀다."


def test_positioned_text_without_a_speaker_column_is_read_as_text():
    rows = [L(1, 50, 60, "미나: 안녕하세요"), L(1, 70, 60, "준호: 네 안녕하세요"), L(1, 90, 60, "(문이 열린다)")]
    assert sp.classify_positioned(rows) is None
    assert sp.positioned_text(rows) == ["미나: 안녕하세요", "준호: 네 안녕하세요", "(문이 열린다)"]
    doc = script.read_rows(sp.positioned_text(rows))
    assert doc["format"] == "lines" and [ln["speaker"] for ln in doc["lines"]] == ["미나", "준호"]


def test_a_name_line_above_its_dialogue_column():
    rows = [L(1, 50, 60, "문이 열린다."),
            L(1, 70, 60, "주인"), L(1, 70, 200, "어서 와요."), L(1, 90, 60, "손님"), L(1, 90, 200, "네."),
            L(1, 110, 60, "주인"), L(1, 110, 200, "앉아요."),
            L(1, 130, 60, "낯선 사람"), L(1, 146, 200, "여기가 그 다방입니까?")]      # the dialogue starts a row lower
    items = sp.classify_positioned(rows)
    assert (items[-1]["type"], items[-1]["cue"], items[-1]["text"]) == ("dialogue", "낯선 사람", "여기가 그 다방입니까?")


def test_a_speaker_called_wife_is_no_wipe():
    # 와이프 is also the Korean spelling of WIPE: as a speaker cell it is a person
    assert sp.cue_like("와이프") and sp.cue_like("(와이프)") and sp.name_like("와이프")
    assert sp.is_transition("와이프") and sp.is_transition("(와이프)") and sp.is_transition("WIPE")
    rows = ["1. 집 / 낮", "", "남편이 신발을 신는다.", "", "와이프\t어디 가?", "남편\t회사.", "와이프\t일찍 와.", "남편\t응.",
            "", "(와이프)"]
    doc = script.read_rows(rows)
    assert doc["format"] == "screenplay"
    assert [(ln["speaker"], ln["text"]) for ln in doc["lines"]] == [
        ("와이프", "어디 가?"), ("남편", "회사."), ("와이프", "일찍 와."), ("남편", "응.")]
    assert doc["elements"][-1]["type"] == "transition"                 # alone on its line it still is the mark
    lines = [L(1, 50, 60, "현관."), L(1, 70, 60, "와이프"), L(1, 70, 200, "다녀와."), L(1, 90, 60, "남편"), L(1, 90, 200, "응."),
             L(1, 110, 60, "와이프"), L(1, 110, 200, "우산."), L(1, 130, 60, "와이프"), L(1, 146, 200, "챙겨 가.")]
    items = sp.classify_positioned(lines)
    assert [(it["type"], it.get("cue"), it["text"]) for it in items] == [
        ("action", None, "현관."), ("dialogue", "와이프", "다녀와."), ("dialogue", "남편", "응."),
        ("dialogue", "와이프", "우산."), ("dialogue", "와이프", "챙겨 가.")]


# ----------------------------------------------------------------- text lines
TAB_SCRIPT = [
    "포구 / 밤", "", "배가 들어온다.", "뱃고동이 길게 울린다.", "",
    "\t나래\t(작게) 왔다.",
    "\t도윤\t오랜만이야.",
    "\t\t정말 오랜만이야.",
    "\t(할머니)\t밥은 먹었니?",
    "\t보 라\t……",
    "INSERT) 낡은 사진 한 장.",
    "(시간 경과)",
    " 선창가 : 가게 앞 / 낮 / 비",
    "          나래  \t가자. \t(돌아보며) 어서.",
    "-갈매기가 난다.",
    "\tF.O",
]


def test_classify_plain_tab_separated():
    ev = sp.plain_evidence(TAB_SCRIPT)
    assert ev["tab"] == 5 and ev["headings"] == 2 and ev["cues"] == 5
    items = sp.classify_plain(TAB_SCRIPT)
    assert [(it["type"], it.get("cue"), it["text"]) for it in items] == [
        ("heading", None, "포구 / 밤"),
        ("action", None, "배가 들어온다."), ("action", None, "뱃고동이 길게 울린다."),
        ("dialogue", "나래", "(작게) 왔다."),
        ("dialogue", "도윤", "오랜만이야. 정말 오랜만이야."),
        ("dialogue", "(할머니)", "밥은 먹었니?"),
        ("dialogue", "보 라", "……"),
        ("transition", None, "INSERT) 낡은 사진 한 장."),
        ("transition", None, "(시간 경과)"),
        ("heading", None, "선창가 : 가게 앞 / 낮 / 비"),
        ("dialogue", "나래", "가자. (돌아보며) 어서."),
        ("action", None, "-갈매기가 난다."),
        ("transition", None, "F.O"),
    ]
    doc = sp.build(items)
    assert [(s["number"], s["place"], s["time"]) for s in doc["scenes"]] == [
        (None, "포구", "밤"), (None, "선창가 : 가게 앞", "낮 / 비")]
    assert [(ln["speaker"], ln["text"], ln["source"]) for ln in doc["lines"]] == [
        ("나래", "왔다.", None), ("도윤", "오랜만이야. 정말 오랜만이야.", None), ("할머니", "밥은 먹었니?", "offscreen"),
        ("나래", "가자. 어서.", None)]
    quiet = next(e for e in doc["elements"] if e.get("cue") == "보 라")
    assert quiet["type"] == "action" and quiet["speaker"] == "보 라" and quiet["text"] == "……"


def test_classify_plain_wide_gap_after_a_name_that_comes_back():
    rows = ["1. 포구 / 밤", "배가 들어온다.",
            "나래      왔다.", "도윤      오랜만이야.", "            정말이야.",
            "나래      응.", "도윤      가자.",
            "나래가 짐을 든다   그리고 걷는다.",                       # a gap in a sentence: no name that comes back
            "2. 선창가 / 낮", "손님      한 번만 말하는 사람."]
    ev = sp.plain_evidence(rows)
    assert ev["gap"] == 4 and ev["tab"] == 0
    items = sp.classify_plain(rows)
    assert [(it["type"], it.get("cue"), it["text"]) for it in items] == [
        ("heading", None, "1. 포구 / 밤"), ("action", None, "배가 들어온다."),
        ("dialogue", "나래", "왔다."), ("dialogue", "도윤", "오랜만이야. 정말이야."),
        ("dialogue", "나래", "응."), ("dialogue", "도윤", "가자."),
        ("action", None, "나래가 짐을 든다 그리고 걷는다."),
        ("heading", None, "2. 선창가 / 낮"), ("action", None, "손님 한 번만 말하는 사람.")]


def test_a_dialogue_goes_on_in_its_column_whatever_the_words():
    # a line in the dialogue column continues the dialogue even when it reads like a mark;
    # in the speaker's column (one tab) a mark is a mark and other text goes on
    rows = ["\t나래\t이제 정말", "\t\t끝.", "\t도윤\t그래.", "\tF.O", "\t나래\t가자,", "\t어서.", "\t도윤\t응."]
    assert [(it["type"], it.get("cue"), it["text"]) for it in sp.classify_plain(rows)] == [
        ("dialogue", "나래", "이제 정말 끝."), ("dialogue", "도윤", "그래."), ("transition", None, "F.O"),
        ("dialogue", "나래", "가자, 어서."), ("dialogue", "도윤", "응.")]
    lines = [L(1, 50, 60, "나래"), L(1, 50, 200, "이제 정말"), L(1, 66, 200, "끝."), L(1, 82, 60, "도윤"), L(1, 82, 200, "그래."),
             L(1, 98, 60, "나래"), L(1, 98, 200, "가자."), L(1, 114, 60, "끝")]
    assert [(it["type"], it["text"]) for it in sp.classify_positioned(lines)] == [
        ("dialogue", "이제 정말 끝."), ("dialogue", "그래."), ("dialogue", "가자."), ("transition", "끝")]


def test_classify_plain_name_line_above_the_dialogue():
    rows = ["1. 포구 / 밤", "배가 들어온다."]
    for k in range(6):
        rows += ["나래", f"첫째로 하는 말 {k}.", "도윤", f"둘째로 하는 말이 길어서 {k}", "다음 줄로 넘어간다.", "갈매기가 난다."]
    rows += ["2. 선창가 / 낮", "나래", "(웃으며) 가자.", "F.O"]
    ev = sp.plain_evidence(rows)
    assert ev["stacked"] == 13 and ev["tab"] == 0 and ev["headings"] == 2
    items = sp.classify_plain(rows)
    assert [it["type"] for it in items[:6]] == ["heading", "action", "dialogue", "dialogue", "action", "dialogue"]
    assert (items[3]["cue"], items[3]["text"]) == ("도윤", "둘째로 하는 말이 길어서 0 다음 줄로 넘어간다.")
    assert (items[-2]["cue"], items[-2]["text"], items[-1]["type"]) == ("나래", "(웃으며) 가자.", "transition")
    doc = script.read_rows(rows)
    assert doc["format"] == "screenplay" and doc["layout"] == "stacked"
    assert any("어림" in w for w in script.summary(doc)["warnings"])


def test_plain_lines_that_are_no_screenplay():
    rows = ["엄마", "엄마 어디 가?", "엄마", "금방 올게.", "엄마", "기다려.", "3. 세 번째 줄", "4. 네 번째 줄"]
    ev = sp.plain_evidence(rows)                       # repeated short lines alone do not make name lines
    assert ev["cues"] == 0 and ev["stacked"] == 0


def test_headings():
    cand = sp.heading_candidate
    assert cand("12. 광장 / 해질녘")["time"] == "해질녘" and cand("12. 광장 / 해질녘")["kind"] == "number"
    assert (cand("S#3 교실 (낮)")["number"], cand("S#3 교실 (낮)")["place"], cand("S#3 교실 (낮)")["time"]) == ("3", "교실", "낮")
    assert cand("#12-1")["number"] == "12-1" and cand("씬 4. 옥상 - 밤")["time"] == "밤"
    assert cand("집 앞 / 버스 (맨 뒷자리) 번갈아 / 밤 / 비") == {"number": None, "place": "집 앞 / 버스 (맨 뒷자리) 번갈아", "time": "밤 / 비",
                                                    "kind": "slug", "text": "집 앞 / 버스 (맨 뒷자리) 번갈아 / 밤 / 비"}
    assert cand("40.2층 복도")["place"] == "2층 복도" and cand("7. 찻집.")["place"] == "찻집"
    # a word that describes the time comes with it (짙은 새벽, 밝은 새벽, 어두운 밤, 어스름)
    for when in ("짙은 새벽", "밝은 새벽", "이른 아침", "어두운 밤", "희뿌연 새벽", "어스름", "어스름한 저녁"):
        c = cand(f"국도 / {when}")
        assert (c["kind"], c["place"], c["time"]) == ("slug", "국도", when), when
    assert cand("국도 / 밤길") is None and cand("가자 / 우리 밤") is None
    for not_heading in ("그는 말이 없다.", "1999년 3월", "밥 / 국 / 김치", "(지나가며) 뭐야 / 밤", "3. 정말이야?", ""):
        assert cand(not_heading) is None
    # numbered lines are scenes only as a run of rising numbers; of two candidates the later one
    cands = [(i, cand(t)) for i, t in enumerate(["1. 나래 역", "2. 도윤 역", "1. 다방", "2. 골목", "3. 역 앞", "9. 엉뚱한 줄"])]
    assert sorted(sp.accept_headings(cands)) == [2, 3, 4]
    assert sorted(sp.accept_headings([(0, cand("3. 혼자 있는 번호")), (1, cand("7. 광장 / 낮"))])) == [1]
    # place / time lines count where scenes are not numbered
    slugs = [(i, cand(t)) for i, t in enumerate(["포구 / 밤", "선창가 / 낮"])]
    assert sorted(sp.accept_headings(slugs)) == [0, 1]
    assert sorted(sp.accept_headings([(0, cand("포구 / 밤")), (5, cand("국도 / 짙은 새벽")), (9, cand("국도 / 밝은 새벽"))])) == [0, 5, 9]
    assert sorted(sp.accept_headings(cands + [(9, cand("포구 / 밤"))])) == [2, 3, 4]


def test_transitions_and_onscreen_marks():
    for t in ("F.O", "(F.I)", "DISSOLVE", "DISSOVE", "CUT TO", "CUT TO: 다음 날", "INSERT) 낡은 사진", "INS. 골목 전경.",
              "- 시간경과", "(회상)", "<암전>", "-END-", "끝"):
        assert sp.is_transition(t), t
    for t in ("암전된 무대에 불이 켜진다.", "회상하는 나래.", "끝내 말이 없다.", "FOX 채널을 튼다.", "INSERTED 라고 적힌 표", "엔딩"):
        assert not sp.is_transition(t), t
    assert sp.ONSCREEN.fullmatch("자막: 삼 년 뒤") and sp.ONSCREEN.fullmatch("(자막) 삼 년 뒤")
    assert not sp.ONSCREEN.fullmatch("자막이 떠오른다.")
    items = sp.classify_plain(["\t나래\t가자.", "\t도윤\t응.", "\t나래\t어서.", "자막 - 삼 년 뒤", "CUT TO"])
    assert [it["type"] for it in items] == ["dialogue", "dialogue", "dialogue", "onscreen", "transition"]


# ------------------------------------------------- speaker cell and directions
def test_parse_cue():
    def cue(text):
        c = sp.parse_cue(text)
        return c["name"], c["source"]

    assert cue("도윤") == ("도윤", None)
    assert cue("도윤(V.O)") == ("도윤", "offscreen") and cue("(NA)도윤") == ("도윤", "narration")
    assert cue("도윤 (E)") == ("도윤", "offscreen") and cue("도윤(M)") == ("도윤", "inner") and cue("도윤(F)") == ("도윤", "phone")
    assert cue("(도윤)") == ("도윤", "offscreen") and cue("(소리)") == (None, "offscreen") and cue("(N)") == (None, "narration")
    assert cue("(뉴스)") == ("뉴스", "broadcast") and cue("(안내 방송)") == ("안내 방송", "broadcast")
    assert cue("(무전)") == ("무전", "radio") and cue("(영상)") == (None, "video") and cue("(전화)") == (None, "phone")
    assert cue("방송") == ("방송", "broadcast") and cue("목소리") == (None, "offscreen")
    assert cue("M") == ("M", None)                               # a single letter may be a name
    assert cue("보 라") == ("보 라", None) and cue("꽃집 주인") == ("꽃집 주인", None) and cue("도윤.") == ("도윤", None)
    assert cue("나래, 도윤") == ("나래/도윤", None) and cue("나래/도윤 :") == ("나래/도윤", None)
    assert cue("") == (None, None)
    c = sp.parse_cue("도윤(영)")
    assert (c["name"], c["language"], c["raw"]) == ("도윤", "영어", "도윤(영)")
    assert sp.parse_cue("도윤(노래)")["sung"] and sp.parse_cue("도윤(혼잣말)")["delivery"] == ["혼잣말"]


def test_direction_types():
    kinds = {t: sp.direction_type(t) for t in (
        "영", "독", "영어로", "서툰 영어", "일어", "불어로", "한국어로", "한",
        "노래", "떼창", "흥얼거리며", "노래를 따라 부르며",
        "V.O", "E", "소리", "전화", "무전", "뉴스", "속마음",
        "혼잣말로", "버럭", "속삭이며", "떨리는 목소리로", "북한 억양",
        "한숨", "웃으며", "기침", "비명을 지르며",
        "나래에게", "문 쪽을 향해",
        "돌아보며", "긴장", "일어나며", "바람 불어", "노래를 끄며", "영어 선생에게", "전화를 받으며", "TV를 끈다", "숨을 죽이고")}
    assert [kinds[t] for t in ("영", "독", "영어로", "서툰 영어", "일어", "불어로")] == [
        ("language", "영어"), ("language", "독일어"), ("language", "영어"), ("language", "영어"),
        ("language", "일본어"), ("language", "프랑스어")]
    assert kinds["한국어로"] == kinds["한"] == ("language", None)      # the original language gets no label
    assert all(kinds[t] == ("sung", True) for t in ("노래", "떼창", "흥얼거리며", "노래를 따라 부르며"))
    assert [kinds[t][1] for t in ("V.O", "E", "소리", "전화", "무전", "뉴스", "속마음")] == [
        "offscreen", "offscreen", "offscreen", "phone", "radio", "broadcast", "inner"]
    assert all(kinds[t][0] == "delivery" for t in ("혼잣말로", "버럭", "속삭이며", "떨리는 목소리로", "북한 억양"))
    assert all(kinds[t] == ("vocal", None) for t in ("한숨", "기침"))
    # said while laughing or screaming is the manner of the words, not a sound of its own
    assert all(kinds[t] == ("delivery", None) for t in ("웃으며", "비명을 지르며"))
    assert kinds["나래에게"][0] == kinds["문 쪽을 향해"][0] == "to"
    for t in ("돌아보며", "긴장", "일어나며", "바람 불어", "노래를 끄며", "전화를 받으며", "TV를 끈다", "숨을 죽이고"):
        assert kinds[t] == ("action", None), t
    assert kinds["영어 선생에게"][0] == "to"


def test_annotate_splits_directions_from_words():
    (ln,) = sp.annotate("(혼잣말로, 한숨) 이게 뭐야… (나래에게) 봤어? / 못 봤지. (나간다)")
    assert ln["text"] == "이게 뭐야… 봤어? 못 봤지."
    assert ln["raw"] == "(혼잣말로, 한숨) 이게 뭐야… (나래에게) 봤어? / 못 봤지. (나간다)"
    assert ln["delivery"] == ["혼잣말로"] and ln["vocal"] == ["한숨"] and ln["language"] is None and not ln["sung"]
    assert ln["directions"] == [{"text": "혼잣말로", "type": "delivery"}, {"text": "한숨", "type": "vocal"},
                                {"text": "나래에게", "type": "to"}, {"text": "나간다", "type": "action"}]
    (ln,) = sp.annotate("(영) 잠깐만 기다려 줘.")
    assert (ln["language"], ln["text"]) == ("영어", "잠깐만 기다려 줘.")
    (ln,) = sp.annotate("(기타를 치며, 노래) 달이 뜨면 / 가리라 1/2 박자로")
    assert ln["sung"] and ln["text"] == "달이 뜨면 가리라 1/2 박자로"
    (ln,) = sp.annotate("(V.O) 그날은 비가 왔다.", sp.parse_cue("도윤"))
    assert ln["source"] == "offscreen" and ln["directions"] == [{"text": "V.O", "type": "source"}]
    (ln,) = sp.annotate("여보세요?", sp.parse_cue("도윤(전화)"))
    assert ln["source"] == "phone" and ln["directions"] == []
    assert sp.annotate("(희미한 미소)") == [] and sp.annotate("……") == [] and sp.annotate("") == []


def test_annotate_cuts_where_the_language_or_singing_changes():
    a, b, c = sp.annotate("(영) 이쪽으로 와. (작게, 독) 내 말 들었지? (한국어로) 미안합니다. (고개를 숙인다)")
    assert [(x["language"], x["text"]) for x in (a, b, c)] == [
        ("영어", "이쪽으로 와."), ("독일어", "내 말 들었지?"), (None, "미안합니다.")]
    assert b["raw"] == "(작게, 독) 내 말 들었지?" and b["delivery"] == ["작게"]
    assert c["raw"] == "(한국어로) 미안합니다. (고개를 숙인다)" and c["directions"][-1] == {"text": "고개를 숙인다", "type": "action"}
    a, b = sp.annotate("자, 간다! (노래) 달이 뜨면 가리라")
    assert (a["sung"], a["text"], b["sung"], b["text"]) == (False, "자, 간다!", True, "달이 뜨면 가리라")
    (only,) = sp.annotate("(영) 하나. (영) 둘.")                 # the same language again is no cut
    assert only["text"] == "하나. 둘."


def test_people_group_the_cells_of_one_name_only():
    b = sp.Builder("screenplay")
    for cue, text in (("도윤", "하나."), ("(도윤)", "둘."), ("도윤(V.O)", "셋."), ("도 윤", "넷."), ("도윤 아버지", "다섯."),
                      ("도윤아버지", "여섯."), ("나래, 도윤", "일곱."), ("(소리)", "여덟."), ("(뉴스)", "아홉.")):
        b.dialogue(cue, text)
    doc = b.document()
    assert doc["characters"] == ["도윤", "도윤 아버지", "나래", "뉴스"]
    assert doc["people"] == [
        {"id": "P01", "name": "도윤", "cues": ["도윤", "(도윤)", "도윤(V.O)", "도 윤", "나래, 도윤"]},
        {"id": "P02", "name": "도윤 아버지", "cues": ["도윤 아버지", "도윤아버지"]},      # the same name, spaced differently
        {"id": "P03", "name": "나래", "cues": ["나래, 도윤"]},
        {"id": "P04", "name": "뉴스", "cues": ["(뉴스)"]}]
    assert [ln["speaker"] for ln in doc["lines"]][-3:] == ["나래/도윤", None, "뉴스"]
    assert [ln["speaker"] for ln in doc["lines"]][:6] == ["도윤"] * 4 + ["도윤 아버지"] * 2      # one name per person

    # a name spread over its syllables takes the compact spelling when the script has one, however rare;
    # a name that is only ever written with a space keeps it
    b = sp.Builder("screenplay")
    for cue, text in (("보 라", "하나."), ("보 라", "둘."), ("보라", "셋."), ("김 씨", "넷."), ("(보 라)", "……")):
        b.dialogue(cue, text)
    doc = b.document()
    assert doc["characters"] == ["보라", "김 씨"] and [ln["speaker"] for ln in doc["lines"]] == ["보라", "보라", "보라", "김 씨"]
    assert doc["people"][0]["cues"] == ["보 라", "보라"] and doc["elements"][-1]["speaker"] == "보라"


# --------------------------------------------------------------------- HWP 5
def rec(tag: int, level: int, payload: bytes) -> bytes:
    if len(payload) < 0xFFF:
        return struct.pack("<I", tag | level << 10 | len(payload) << 20) + payload
    return struct.pack("<II", tag | level << 10 | 0xFFF << 20, len(payload)) + payload


def wide(text: str) -> bytes:
    return text.encode("utf-16-le")


def ctrl(code: int, name: bytes = b"") -> bytes:
    """A control that takes eight code units: the code, twelve bytes, the code again."""
    return struct.pack("<H", code) + name.ljust(12, b"\x00") + struct.pack("<H", code)


def one(code: int) -> bytes:
    return struct.pack("<H", code)


def hwp_body() -> bytes:
    para = rec(66, 0, b"\x00" * 22)
    long_text = "가" * 3000                                        # 6,000 bytes: the size does not fit the header
    return b"".join([
        para, rec(67, 1, ctrl(2, b"dces") + ctrl(2, b"dloc") + wide("포구 / 밤") + one(13)),
        rec(71, 1, b"dces" + b"\x00" * 20), rec(73, 2, b"\x00" * 40),          # section definition and its page record
        para,                                                                  # an empty paragraph has no text record
        para, rec(67, 1, ctrl(9) + wide("나래") + ctrl(9) + wide("왔다.") + one(13)), rec(68, 1, b"\x00" * 8),
        para, rec(67, 1, wide("첫 줄") + one(10) + wide("둘째") + one(30) + wide("줄") + one(24) + wide("끝") + one(13)),
        para, rec(67, 1, wide("머리말이 달린 문단") + ctrl(16, b"daeh") + one(13)),
        rec(71, 1, b"daeh" + b"\x00" * 8), rec(72, 2, b"\x00" * 6), rec(66, 2, b"\x00" * 22),
        rec(67, 3, wide("쪽마다 찍히는 머리말") + one(13)),
        para, rec(67, 1, wide(long_text) + one(13)),
        para, rec(67, 1, ctrl(11, b" lbt") + wide("표가 든 문단") + one(13)),
        rec(71, 1, b" lbt" + b"\x00" * 8), rec(72, 2, b"\x00" * 6), rec(66, 2, b"\x00" * 22),
        rec(67, 3, wide("표 안의 글") + one(13)),
    ])


def test_hwp_records_and_paragraph_text():
    data = hwp_body()
    records = list(sp.hwp_records(data))
    assert [r[:2] for r in records[:4]] == [(66, 0), (67, 1), (71, 1), (73, 2)]
    assert max(len(r[2]) for r in records) == 6002                # the long record came through whole
    assert sp.hwp_text(ctrl(9) + wide("나래") + ctrl(9) + wide("왔다.") + one(13)) == "\t나래\t왔다."
    assert sp.hwp_text(wide("가") + ctrl(21, b"onwn") + wide("나") + one(0) + one(13)) == "가나"
    assert sp.hwp_text(wide("𝄞음") + one(13)) == "𝄞음"           # a surrogate pair stays whole
    paras = sp.hwp_section(data)
    assert paras[:5] == ["포구 / 밤", "", "\t나래\t왔다.", "첫 줄\n둘째 줄-끝", "머리말이 달린 문단"]
    assert paras[5] == "가" * 3000 and paras[6:] == ["표가 든 문단", "표 안의 글"]      # the header text is left out
    assert list(sp.hwp_records(data[:-3]))[-1][0] == 67           # a cut-off file does not raise


class FakeOle:
    def __init__(self, streams):
        self.streams = streams

    def exists(self, name):
        return name in self.streams

    def listdir(self):
        return [name.split("/") for name in self.streams]

    def openstream(self, name):
        name = "/".join(name) if isinstance(name, list) else name
        return types.SimpleNamespace(read=lambda: self.streams[name])

    def close(self):
        pass


def deflate(data: bytes) -> bytes:
    c = zlib.compressobj(wbits=-15)
    return c.compress(data) + c.flush()


def hwp_header(flags: int) -> bytes:
    return (b"HWP Document File".ljust(32, b"\x00") + struct.pack("<II", 0x05000107, flags)).ljust(256, b"\x00")


def test_hwp_paragraphs_through_the_container(tmp_path, monkeypatch):
    import olefile
    f = tmp_path / "s.hwp"
    f.write_bytes(b"\xd0\xcf\x11\xe0 not a real container")
    second = rec(66, 0, b"\x00" * 22) + rec(67, 1, ctrl(9) + wide("도윤") + ctrl(9) + wide("응.") + one(13))
    streams = {"FileHeader": hwp_header(0x1), "BodyText/Section10": deflate(second), "BodyText/Section2": deflate(hwp_body()),
               "DocInfo": b"", "PrvText": b""}
    monkeypatch.setattr(olefile, "isOleFile", lambda path: True)
    monkeypatch.setattr(olefile, "OleFileIO", lambda path: FakeOle(streams))
    paras = sp.hwp_paragraphs(f)
    assert paras[:3] == ["포구 / 밤", "", "\t나래\t왔다."] and paras[3:5] == ["첫 줄", "둘째 줄-끝"]      # a line break splits
    assert paras[-1] == "\t도윤\t응."                             # Section2 before Section10
    streams["FileHeader"] = hwp_header(0x0)                       # not compressed
    streams["BodyText/Section2"] = hwp_body()
    streams["BodyText/Section10"] = second
    assert sp.hwp_paragraphs(f) == paras
    for flags, msg in ((0x3, "암호"), (0x5, "배포용")):
        streams["FileHeader"] = hwp_header(flags)
        with pytest.raises(ValueError, match=msg):
            sp.hwp_paragraphs(f)
    streams["FileHeader"] = b"something else".ljust(256, b"\x00")
    with pytest.raises(ValueError, match="HWP 5 파일이 아닙니다"):
        sp.hwp_paragraphs(f)


def test_hwp_files_that_are_something_else(tmp_path):
    f = tmp_path / "s.hwp"
    f.write_bytes(b"PK\x03\x04 a zip, so an hwpx")
    with pytest.raises(ValueError, match="HWPX"):
        script.read(f)
    f.write_bytes(b"HWP Document File V3.00 \x1a\x01\x02\x03\x04\x05" + b"\x00" * 100)
    with pytest.raises(ValueError, match="HWP 5 파일이 아닙니다"):
        script.read(f)


# ------------------------------------------------------------ the script stage
def project(tmp_path, text="미나: 안녕하세요\n준호: 네 안녕하세요\n", name="s.txt") -> Project:
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    src = tmp_path / name
    if text is not None:
        src.write_text(text, encoding="utf-8")
    write_json(work / "project.json", {"title": "t", "video": "v.mp4", "script": str(src) if text is not None else None,
                                       "t0": 0.0, "t1": 10.0, "duration": 10.0})
    return Project(work)


RESULTS = ["audio/mix_16k.wav", "audio/speech_16k.wav", "cuts.json", "env.npz", "asr.json", "dialogue.json", "sfx.json",
           "music.json", "speaker.json", "screen.json", "dialog_ev.json", "items.json", "agent_done.json", "review.json"]


def fill(p: Project) -> None:
    for rel in RESULTS:
        f = p.path(rel)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("{}", encoding="utf-8")


def test_stage_graph():
    assert pipeline.STAGES[:3] == ["ingest", "script", "separate"]
    assert pipeline.OUTPUTS["script"] == ["script.json"] and "script.json" not in pipeline.OUTPUTS["ingest"]
    assert pipeline.dependents(["script"]) == ["script", "align", "speaker", "dialog_ev", "recommend"]
    assert "asr" not in pipeline.dependents(["script"])
    redo = pipeline.dependents(["ingest"])
    assert "script" not in redo and redo[:5] == ["ingest", "separate", "cuts", "sed", "asr"] and "align" in redo
    assert set(pipeline.OUTPUTS) == set(pipeline.USED_BY) == set(pipeline.STAGES)


def test_script_stage_reads_once_and_again_when_the_file_changes(tmp_path):
    p = project(tmp_path)
    first = script_stage.run(p, {})
    doc = read_json(p.path("script.json"))
    assert first == {"lines": 2, "characters": 2, "scenes": 0, "format": "lines", "timed": False, "changed": False,
                     "cleared": []}
    assert doc["version"] == 2 and doc["source"]["sha1"] == script.file_signature(tmp_path / "s.txt")["sha1"]
    fill(p)
    assert script_stage.run(p, {}) == {"skipped": True, "lines": 2, "characters": 2, "scenes": 0, "format": "lines"}
    assert all(p.path(rel).exists() for rel in RESULTS)           # nothing is removed while the file stays

    (tmp_path / "s.txt").write_text("미나: 안녕하세요\n준호: 네 안녕하세요\n미나: 또 봐요\n", encoding="utf-8")
    again = script_stage.run(p, {})
    assert again["lines"] == 3 and again["changed"] and again["cleared"] == ["align", "speaker", "dialog_ev", "recommend"]
    gone = {rel for rel in RESULTS if not p.path(rel).exists()}
    assert gone == {"dialogue.json", "speaker.json", "dialog_ev.json", "items.json"}       # 받아쓰기 and the audio stay
    assert len(read_json(p.path("script.json"))["lines"]) == 3
    assert script_stage.run(p, {})["skipped"]

    # the same bytes written again (a new time, the same checksum) are no change
    os.utime(tmp_path / "s.txt", (2_000_000_000, 2_000_000_000))
    assert script_stage.run(p, {})["skipped"]


def test_script_stage_without_a_script_or_file(tmp_path):
    p = project(tmp_path, text=None)
    assert script_stage.run(p, {}) == {"skipped": True, "lines": 0}
    assert not p.path("script.json").exists()

    p = project(tmp_path)
    (tmp_path / "s.txt").unlink()
    with pytest.raises(FileNotFoundError, match="대본 파일이 없습니다"):
        script_stage.run(p, {})
    write_json(p.path("script.json"), {"lines": [{"id": "L0001", "speaker": "미나", "text": "안녕"}], "characters": ["미나"]})
    assert script_stage.run(p, {}) == {"skipped": True, "lines": 1, "characters": 1, "scenes": 0, "format": "lines",
                                       "missing": True}


def test_script_stage_keeps_a_reading_of_the_old_format(tmp_path, caplog):
    p = project(tmp_path)
    old = {"source": str(tmp_path / "s.txt"), "timed": False, "characters": ["미나", "준호"],
           "lines": [{"id": "L0001", "speaker": "미나", "text": "안녕하세요"}, {"id": "L0002", "speaker": "준호", "text": "네 안녕하세요"}]}
    write_json(p.path("script.json"), old)
    fill(p)
    os.utime(tmp_path / "s.txt", (1_000_000_000, 1_000_000_000))  # the file is older than the reading
    assert script_stage.run(p, {})["skipped"]
    assert read_json(p.path("script.json")) == old and p.path("dialogue.json").exists()

    newer = p.path("script.json").stat().st_mtime + 100           # touched after it was read, the lines the same
    os.utime(tmp_path / "s.txt", (newer, newer))
    res = script_stage.run(p, {})
    assert res["changed"] and res["cleared"] == [] and p.path("dialogue.json").exists()
    assert read_json(p.path("script.json"))["version"] == 2       # only moved to the new format

    write_json(p.path("script.json"), old)                        # the old reading again; now the file is edited
    (tmp_path / "s.txt").write_text("미나: 안녕하세요\n준호: 네, 정말 오랜만이네요\n", encoding="utf-8")
    newer = p.path("script.json").stat().st_mtime + 100
    os.utime(tmp_path / "s.txt", (newer, newer))
    with caplog.at_level("INFO", logger="cboe"):
        res = script_stage.run(p, {})
    assert res["changed"] and res["cleared"] == ["align", "speaker", "dialog_ev", "recommend"]
    assert read_json(p.path("script.json"))["version"] == 2 and not p.path("dialogue.json").exists()
    assert p.path("asr.json").exists() and p.path("sfx.json").exists()
    assert "cleared outputs of: align, speaker, dialog_ev, recommend" in caplog.text     # said, not done silently


def test_script_stage_through_the_pipeline(tmp_path):
    p = project(tmp_path, "\n".join(TAB_SCRIPT))
    log = pipeline.run(p.work, stages=["script"])
    assert log["script"]["lines"] == 4 and log["script"]["scenes"] == 2 and log["script"]["format"] == "screenplay"
    assert read_json(p.path("script.json"))["layout"] == "tab"
    again = pipeline.run(p.work, stages=["script"])              # the skip leaves the first record
    assert again["script"] == log["script"] and "skipped" not in again["script"]
    hist = read_json(p.path("run_history.json"))
    assert [[s["name"] for s in h["stages"]] for h in hist] == [["script"], ["script"]]       # no other stage was started
    assert [h["stages"][0].get("skipped", False) for h in hist] == [False, True]


def test_script_given_to_a_run_that_had_none(tmp_path):
    p = project(tmp_path)                                         # analysed before the script was there
    fill(p)
    res = script_stage.run(p, {})
    assert not res["changed"] and res["cleared"] == ["align", "speaker", "dialog_ev", "recommend"]
    assert p.path("asr.json").exists() and not p.path("dialogue.json").exists()


# ------------------------------------------------------------------ the run log
def fake_stages(monkeypatch, behave):
    """Run the pipeline with stand-in stages: ``behave(name, p)`` gives the stage result."""
    calls = []

    def load(name, package=None):
        stage = name.rsplit(".", 1)[-1]

        def run(p, cfg):
            calls.append(stage)
            return behave(stage, p)
        return types.SimpleNamespace(run=run)

    monkeypatch.setattr(pipeline.importlib, "import_module", load)
    return calls


def test_run_log_keeps_the_time_of_the_run_that_did_the_work(tmp_path, monkeypatch):
    p = project(tmp_path, text=None)
    done = set()
    clock = iter(range(0, 100000, 7))
    monkeypatch.setattr(pipeline.time, "time", lambda: float(next(clock)))       # every stage "takes" 7 s

    def behave(stage, p):
        if stage in done:
            return {"skipped": True}
        done.add(stage)
        return {"made": stage}

    calls = fake_stages(monkeypatch, behave)
    first = pipeline.run(p.work, stages=["ingest", "separate", "asr"])
    assert calls == ["ingest", "separate", "asr"] and first["separate"] == {"made": "separate", "seconds": 7.0}
    second = pipeline.run(p.work, stages=["ingest", "separate", "asr", "cuts"])
    log = read_json(p.path("run_log.json"))
    assert second == log and log["separate"] == {"made": "separate", "seconds": 7.0}       # not overwritten by the skip
    assert log["asr"]["seconds"] == 7.0 and log["cuts"] == {"made": "cuts", "seconds": 7.0}
    history = read_json(p.path("run_history.json"))
    assert len(history) == 2 and history[0]["stages_asked"] == ["ingest", "separate", "asr"]
    assert [s["name"] for s in history[1]["stages"]] == ["ingest", "separate", "asr", "cuts"]
    assert [s.get("skipped", False) for s in history[1]["stages"]] == [True, True, True, False]
    assert history[1]["finished"] and history[1]["seconds"] > 0

    done.discard("separate")                                      # forced: the new result replaces the old one
    monkeypatch.setitem(pipeline.OUTPUTS, "separate", [])
    third = pipeline.run(p.work, stages=["separate"], force=["separate"])
    assert third["separate"]["made"] == "separate" and len(read_json(p.path("run_history.json"))) == 3


def test_a_changed_script_reruns_only_what_it_cleared(tmp_path, monkeypatch):
    p = project(tmp_path, text=None)

    def behave(stage, p):
        return {"cleared": ["align", "speaker", "dialog_ev", "recommend"]} if stage == "script" else {"skipped": True}

    calls = fake_stages(monkeypatch, behave)
    pipeline.run(p.work, stages=["script"])
    assert calls == ["script", "align", "speaker", "dialog_ev", "recommend"]
    del calls[:]
    pipeline.run(p.work)                                          # a whole run: every stage once, in order
    assert calls == pipeline.STAGES
    hist = read_json(p.path("run_history.json"))
    assert hist[0]["stages"][0] == {"name": "script", "seconds": 0.0, "cleared": ["align", "speaker", "dialog_ev", "recommend"]}


def test_ingest_reports_when_it_had_nothing_to_do(tmp_path, monkeypatch):
    from cboe.stages import ingest
    p = project(tmp_path, text=None)
    made = []
    monkeypatch.setattr(ingest, "extract_audio", lambda video, path, t0, t1, sr: (made.append(sr), path.write_bytes(b"w")))
    p.path("audio").mkdir()
    assert "skipped" not in ingest.run(p, {}) and made == [48000, 16000]
    assert ingest.run(p, {})["skipped"] and made == [48000, 16000]
    assert not p.path("script.json").exists()                     # reading the script is another stage


def test_documents_are_plain_json(tmp_path):
    f = tmp_path / "s.txt"
    f.write_text("\n".join(TAB_SCRIPT), encoding="utf-8")
    doc = script.read(f)
    assert json.loads(json.dumps(doc, ensure_ascii=False)) == doc and doc["format"] == "screenplay" and doc["layout"] == "tab"
    assert set(doc) >= {"version", "format", "source", "timed", "characters", "people", "scenes", "elements", "lines"}
    assert set(doc["lines"][0]) == {"id", "speaker", "text", "raw", "scene_id", "element_id", "language", "sung", "source",
                                    "delivery", "vocal", "directions"}
