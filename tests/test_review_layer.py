# -*- coding: utf-8 -*-
"""검수 층 (cboe.state + cboe.server): dialogue lines as review items, what the writer adds,
people and labels, and the recommendations worked out again after the writer's choices.
Every script line, name and sound here is made up."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from cboe import placement, style
from cboe.server.app import create_app, export_warnings
from cboe.state import Conflict, Project, ReviewError, extra_fp, line_fp
from cboe.util import read_json, write_json

FPS = 24.0


def words(text, s, e):
    toks = text.split()
    step = (e - s) / len(toks)
    return [{"w": w, "s": round(s + i * step, 3), "e": round(s + (i + 1) * step, 3)} for i, w in enumerate(toks)]


def line(lid, speaker, text, s, e, status="ok", scene="SC001", timing="asr", **more):
    out = {"id": lid, "speaker": speaker, "text": text, "raw": text, "start": s, "end": e,
           "words": words(text, s, e) if s is not None else [], "match": 1.0 if status == "ok" else 0.4,
           "aligned": status in ("ok", "differs"), "timing": timing if s is not None else None,
           "status": status, "scene_id": scene, "language": None, "sung": False, "source": None,
           "asr_text": text if status == "ok" else ""}
    out.update(more)
    return out


def speaker_item(ln, face="not_visible", rec="include", reason="말하는 얼굴 안 보임", **more):
    it = {"id": "P" + ln["id"][1:], "kind": "speaker", "t0": ln["start"], "t1": ln["end"], "onset": ln["start"],
          "category": "speaker", "label": {"class": "speaker", "ko": ln["speaker"]},
          "speaker": {"line_id": ln["id"], "name": ln["speaker"], "face": face, "n_faces": 0, "by": None,
                      "source": ln.get("source"), "estimated": ln.get("timing") in ("voice", "even")},
          "phrases": [], "phrase": ln["text"], "short": ln["text"], "flags": [], "rec": rec, "reason": reason,
          "evidence": []}
    it.update(more)
    return it


def sound(iid, onset, phrase, kind="sfx", rec="include", reason="", **more):
    it = {"id": iid, "kind": kind, "onset": onset, "t0": onset, "t1": onset + 1.0, "phrase": phrase, "short": phrase,
          "phrases": [{"text": phrase, "short": phrase}], "rec": rec, "reason": reason, "evidence": [], "flags": [],
          "label": {"class": "Thump, thud", "ko": "쿵", "confident": True}, "category": "event"}
    if kind == "music":
        it["music"] = {"moods": [], "event": "cue"}
    it.update(more)
    return it


LINES = [
    line("L0001", "하늘", "누구세요?", 10.0, 11.0),
    line("L0002", "바다", "옆집 사람이에요", 11.5, 13.0, status="differs", asr_text="옆집 사람이요",
         diff=[{"kind": "word", "script": "사람이에요", "heard": "사람이요", "at": [1, 2]}]),
    line("L0003", "하늘", "아 그래요", 14.0, 15.0, status="unheard", timing="voice"),
    line("L0004", "바다", "하늘 씨 맞죠?", 16.0, 17.5),
    line("L0005", "하늘", "네 맞아요", 18.0, 19.0),
    line("L0006", "구름", "여기 없는 장면의 말", None, None, status="absent", scene="SC002", guess=[30.0, 32.0]),
    line("L0007", "경찰1", "꼼짝 마", 40.0, 41.0, scene="SC003"),
    line("L0008", "하늘", "구간 밖의 말", None, None, status="out_of_range", scene="SC003"),
]
EXTRAS = [{"id": "X0001", "start": 20.0, "end": 21.2, "text": "저기요 잠깐만", "scene_id": "SC001",
           "words": [{"w": "저기요", "s": 20.0, "e": 20.5}, {"w": "잠깐만", "s": 20.6, "e": 21.2}]},
          {"id": "X0002", "start": 50.0, "end": 50.3, "text": "음", "scene_id": "SC003", "short": True,
           "words": [{"w": "음", "s": 50.0, "e": 50.3}]}]
ASR = [{"w": "옆집", "s": 11.6, "e": 12.0}, {"w": "사람이요", "s": 12.1, "e": 12.9}]


def items_for(lines):
    out = [speaker_item(ln) for ln in lines if ln["start"] is not None]
    out += [sound("S0001", 25.0, "[쿵]"),
            sound("S0002", 26.0, "[쿵]", rec="exclude", reason="앞 자막에서 이미 알림", follows="S0001"),
            sound("M0001", 60.0, "[잔잔한 음악]", kind="music"),
            sound("M0002", 70.0, "[잔잔한 음악]", kind="music", rec="exclude",
                  reason="같은 분위기 음악이 이어짐 · 앞 자막에서 이미 알림", follows="M0001"),
            sound("C0001", 80.0, "[쾅]", rec="check", reason="대본 지문에 적힌 소리 · 소리 검출로는 못 찾음",
                  origin="script", estimated=True, window=[78.0, 85.0], flags=["script", "timing_estimated"])]
    return sorted(out, key=lambda it: it["onset"])


def make(tmp_path, lines=None, extras=None, items=None, scenes=True, review=None):
    w = tmp_path / "work"
    w.mkdir(parents=True, exist_ok=True)
    lines = LINES if lines is None else lines
    write_json(w / "project.json", {"title": "검수 층 시험", "t0": 0.0, "t1": 120.0, "fps": FPS, "duration": 120.0})
    write_json(w / "dialogue.json", {"mode": "scenes", "extras": EXTRAS if extras is None else extras, "lines": lines})
    write_json(w / "items.json", {"items": items_for(lines) if items is None else items})
    write_json(w / "cuts.json", {"cuts": []})
    write_json(w / "asr.json", {"words": ASR})
    people = [{"id": f"P0{k}", "name": n, "cues": [n]} for k, n in enumerate(("하늘", "바다", "구름", "경찰1"), 1)]
    write_json(w / "script.json", {"version": 2, "characters": ["하늘", "바다", "구름", "경찰1"], "people": people,
                                   "lines": [{"id": ln["id"], "speaker": ln["speaker"], "text": ln["text"]} for ln in lines]})
    if scenes:
        write_json(w / "scenes.json", {"scenes": [{"id": "SC001", "start": 10.0, "end": 21.2, "status": "found"},
                                                  {"id": "SC002", "start": None, "end": None, "status": "absent"},
                                                  {"id": "SC003", "start": 40.0, "end": 41.0, "status": "found"}]})
    if review is not None:
        write_json(w / "review.json", review)
    return Project(w)


def by_id(rows):
    return {r["id"]: r for r in rows}


def refs(draft):
    return {r for c in draft["cells"] for r in c["refs"]}


# =================================================================== lines in and out (1-03, 1-06)
def test_lines_with_a_time_are_in_and_lines_without_one_are_out_with_the_reason(tmp_path):
    p = make(tmp_path)
    lines = by_id(p.effective_lines())
    assert [k for k, v in lines.items() if v["include"]] == ["L0001", "L0002", "L0003", "L0004", "L0005", "L0007"]
    assert lines["L0003"]["estimated"] and lines["L0003"]["include"]          # unheard is not left out (1-03)
    assert (lines["L0006"]["excluded"], lines["L0008"]["excluded"]) == ("absent", "out_of_range")
    assert all(v["origin"] == "script" for v in lines.values())
    d = p.recompute()
    assert refs(d) >= {"L0001", "L0002", "L0003", "L0004", "L0005", "L0007"} and not refs(d) & {"L0006", "L0008"}
    assert any("timing_estimated" in c["flags"] for c in d["cells"] if "L0003" in c["refs"])


def test_the_writer_takes_a_line_out_and_its_speaker_item_goes_with_it(tmp_path):
    p = make(tmp_path)
    p.set_line("L0005", include=False)
    ln = by_id(p.effective_lines())["L0005"]
    assert (ln["include"], ln["excluded"], ln["decided"]) == (False, "writer", True)
    assert "L0005" not in refs(p.recompute())
    it = by_id(p.effective_items())["P0005"]
    assert it["line_excluded"]
    chk = p.export_check()
    assert chk["lines"]["writer_excluded"] == ["L0005"] and "P0005" not in chk["check_speaker"]
    p.set_line("L0005", include=None)                                            # back to the default
    assert by_id(p.effective_lines())["L0005"]["include"]
    assert "L0005" not in read_json(p.path("review.json"))["lines"]


def test_a_line_without_a_time_comes_back_with_a_start_and_an_end(tmp_path):
    p = make(tmp_path)
    with pytest.raises(Conflict):
        p.set_line("L0006", include=True)                                        # no time: the writer must give one
    with pytest.raises(ReviewError):
        p.set_line("L0006", include=True, start=33.0, end=31.0)
    with pytest.raises(ReviewError):
        p.set_line("L0006", include=True, start=118.0, end=130.0)                # outside the processed part
    p.set_line("L0006", include=True, start=30.0, end=32.0)
    ln = by_id(p.effective_lines())["L0006"]
    assert ln["include"] and not ln["estimated"] and ln["pinned"] and ln["excluded"] is None
    assert [w["w"] for w in ln["words"]] == ln["text"].split() and ln["words"][0]["s"] == 30.0
    assert ln["words"][-1]["e"] == pytest.approx(32.0)
    cells = [c for c in p.recompute()["cells"] if "L0006" in c["refs"]]
    assert cells and cells[0]["start"] == pytest.approx(30.0) and "timing_estimated" not in cells[0]["flags"]


def test_a_start_and_an_end_alone_bring_a_line_without_a_time_back(tmp_path):
    p = make(tmp_path)
    p.set_line("L0008", start=50.0, end=51.5)                                   # out_of_range, no include sent
    ln = by_id(p.effective_lines())["L0008"]
    assert (ln["include"], ln["excluded"], ln["pinned"], ln["decided"]) == (True, None, True, False)
    assert "L0008" in refs(p.recompute())
    p.set_line("L0008", include=False)                                          # the writer can still leave it out
    assert by_id(p.effective_lines())["L0008"]["excluded"] == "writer"
    p.set_line("L0008", include=None, start=None, end=None)                     # back: no time, out
    assert by_id(p.effective_lines())["L0008"]["excluded"] == "out_of_range"


def test_a_text_too_long_to_be_a_line_is_refused(tmp_path):
    p = make(tmp_path)
    huge = "가나다라 " * 4000                                                     # 20,000 letters on a 1 s line
    with pytest.raises(ReviewError, match="1000자까지"):
        p.set_line("L0001", text=huge)
    with pytest.raises(ReviewError, match="1000자까지"):
        p.add("line", start=30.0, end=31.0, text=huge)
    with pytest.raises(ReviewError, match="200자까지"):
        p.add("sfx", start=30.0, end=31.0, phrase="쿵" * 300)
    assert "lines" not in read_json(p.path("review.json"), {}) or not read_json(p.path("review.json"))["lines"]
    p.set_line("L0001", text="누구세요 거기 누구 있어요?")                          # a normal one is fine
    with TestClient(create_app(p.work)) as c:
        r = c.post("/api/lines/L0001", json={"text": huge})
        assert r.status_code == 400 and "1000자까지" in r.json()["detail"]


def test_fixing_the_time_of_an_unheard_line_makes_it_no_guess(tmp_path):
    p = make(tmp_path)
    p.set_line("L0003", start=14.2, end=15.4)
    ln = by_id(p.effective_lines())["L0003"]
    assert not ln["estimated"] and ln["words"][0]["s"] == pytest.approx(14.2)    # its words move with it
    assert ln["words"][-1]["e"] == pytest.approx(15.4)
    lines, _ = p.placement_inputs()
    pl = by_id(lines)["L0003"]
    assert (pl["status"], pl["timing"]) == ("ok", "writer")
    c = next(c for c in p.recompute()["cells"] if "L0003" in c["refs"])
    assert "timing_estimated" not in c["flags"]
    assert p.export_check()["lines"]["unheard"] == []


# =================================================================== the text of a line (1-06)
def test_the_writer_picks_what_was_heard_and_its_word_times(tmp_path):
    p = make(tmp_path)
    p.set_line("L0002", text_from="heard")
    ln = by_id(p.effective_lines())["L0002"]
    assert (ln["text"], ln["text_from"], ln["script_text"]) == ("옆집 사람이요", "heard", "옆집 사람이에요")
    assert ln["words"] == [{"w": "옆집", "s": 11.6, "e": 12.0}, {"w": "사람이요", "s": 12.1, "e": 12.9}]
    assert ln["start"] == 11.6 and ln["end"] == 12.9
    lines = [x for c in p.recompute()["cells"] if "L0002" in c["refs"] for x in c["lines"]]
    assert any("옆집 사람이요" in x for x in lines)
    assert p.export_check()["lines"]["differs_open"] == []
    with pytest.raises(Conflict):
        p.set_line("L0003", text_from="heard")                                   # nothing was heard there


def test_a_text_of_the_writers_own_is_shared_out_over_the_line_by_letters(tmp_path):
    p = make(tmp_path)
    p.set_line("L0002", text="옆집 사는 사람이에요")
    ln = by_id(p.effective_lines())["L0002"]
    assert ln["text_from"] == "writer" and ln["script_text"] == "옆집 사람이에요"
    ws = ln["words"]
    assert [w["w"] for w in ws] == ["옆집", "사는", "사람이에요"] and ws[0]["s"] == 11.5
    assert ws[-1]["e"] == pytest.approx(13.0)
    assert ws[2]["e"] - ws[2]["s"] > ws[0]["e"] - ws[0]["s"]                    # five letters take longer than two
    p.set_line("L0002", text_from="script")                                      # the script's words, chosen
    ln = by_id(p.effective_lines())["L0002"]
    assert ln["text"] == "옆집 사람이에요" and ln["decision"] == {"text_from": "script"}
    assert p.export_check()["lines"]["differs_open"] == []                       # a choice was made
    p.set_line("L0002", text=None)
    assert p.export_check()["lines"]["differs_open"] == ["L0002"]


def test_breaks_and_keep_reach_placement(tmp_path):
    p = make(tmp_path)
    with pytest.raises(ReviewError):
        p.set_line("L0004", breaks=[3])                                          # 3 eojeols: 1 or 2 only
    p.set_line("L0004", breaks=[1])
    cells = [c for c in p.recompute()["cells"] if "L0004" in c["refs"]]
    assert [c["lines"] for c in cells] == [["[바다] 하늘"], ["씨 맞죠?"]]       # cut before the 2nd eojeol
    p.set_line("L0004", breaks=None, keep=True)
    lines, _ = p.placement_inputs()
    assert by_id(lines)["L0004"]["keep"] is True and "breaks" not in by_id(lines)["L0004"]


def test_language_and_sung_marks(tmp_path):
    p = make(tmp_path)
    p.set_line("L0001", language="영어")
    p.set_line("L0005", sung=True)
    d = p.recompute()
    first = next(c for c in d["cells"] if "L0001" in c["refs"])["lines"][0]
    assert first.startswith("[하늘/영어] ")
    assert next(c for c in d["cells"] if "L0005" in c["refs"])["lines"][-1].endswith(style.NOTE)


def test_a_decision_follows_its_line_when_the_ids_change(tmp_path):
    p = make(tmp_path)
    p.set_line("L0004", include=False)
    stored = read_json(p.path("review.json"))["lines"]["L0004"]
    assert stored["fp"] == line_fp(LINES[3], 0)
    # the script is read again with one more line in front: every id moves by one
    moved = [line("L0001", "구름", "새로 생긴 줄", 5.0, 6.0)] + [dict(ln, id=f"L{int(ln['id'][1:]) + 1:04d}") for ln in LINES]
    write_json(p.path("dialogue.json"), {"mode": "scenes", "extras": EXTRAS, "lines": moved})
    lines = by_id(p.effective_lines())
    assert lines["L0004"]["include"] and lines["L0004"]["text"] == "아 그래요"     # the id L0004 is another line now
    assert not lines["L0005"]["include"] and lines["L0005"]["text"] == "하늘 씨 맞죠?"
    p.set_line("L0002", include=False)                                           # a write keys today's ids
    st = read_json(p.path("review.json"))["lines"]
    assert set(st) == {"L0002", "L0005"} and st["L0005"]["include"] is False


def test_the_same_words_said_twice_are_two_lines(tmp_path):
    twice = [line("L0001", "하늘", "가자", 10.0, 10.8), line("L0002", "바다", "응", 11.5, 12.0),
             line("L0003", "하늘", "가자", 13.0, 13.8)]
    p = make(tmp_path, lines=twice)
    p.set_line("L0003", include=False)
    lines = by_id(p.effective_lines())
    assert lines["L0001"]["include"] and not lines["L0003"]["include"]
    assert line_fp(twice[0], 0) != line_fp(twice[2], 1)


TWICE = [line("L0001", "하늘", "가자", 10.0, 10.8), line("L0002", "바다", "응", 11.5, 12.0),
         line("L0003", "하늘", "가자", 13.0, 13.8)]


def renumbered(lines):
    return [dict(ln, id=f"L{k:04d}") for k, ln in enumerate(lines, 1)]


def test_a_line_of_the_same_words_added_in_front_does_not_move_the_decision(tmp_path):
    p = make(tmp_path, lines=TWICE, extras=[])
    p.set_line("L0003", include=False)                          # the second '가자'
    p.set_line("L0001", text="가자 얼른")                         # the first one
    # read again: one more '가자' of 하늘 at the very start, every id moves by one
    again = renumbered([line("L0000", "하늘", "가자", 5.0, 5.8)] + TWICE)
    write_json(p.path("dialogue.json"), {"mode": "scenes", "extras": [], "lines": again})
    got = {k: (v["start"], v["include"], v["text"]) for k, v in by_id(p.effective_lines()).items()}
    assert got == {"L0001": (5.0, True, "가자"), "L0002": (10.0, True, "가자 얼른"),
                   "L0003": (11.5, True, "응"), "L0004": (13.0, False, "가자")}
    p.set_line("L0003", include=False)                          # a write keys the decisions by today's ids
    st = read_json(p.path("review.json"))["lines"]
    assert set(st) == {"L0002", "L0003", "L0004"} and st["L0004"]["fp"] == line_fp(again[3], 2)


def test_a_decided_line_taken_out_of_the_script_leaves_the_other_one_alone(tmp_path):
    p = make(tmp_path, lines=TWICE, extras=[])
    p.set_line("L0001", include=False)                          # the first '가자'
    again = renumbered(TWICE[1:])                               # read again: that line is gone
    write_json(p.path("dialogue.json"), {"mode": "scenes", "extras": [], "lines": again})
    got = {k: (v["start"], v["include"]) for k, v in by_id(p.effective_lines()).items()}
    assert got == {"L0001": (11.5, True), "L0002": (13.0, True)}     # the '가자' at 13.0 was never touched
    p.set_line("L0001", include=False)
    st = read_json(p.path("review.json"))["lines"]
    aside = [k for k in st if k.startswith("fp:")]               # kept aside, not on a line
    assert set(st) == {"L0001"} | set(aside) and len(aside) == 1 and st[aside[0]]["include"] is False
    # the line comes back in the script: so does its decision
    write_json(p.path("dialogue.json"), {"mode": "scenes", "extras": [], "lines": TWICE})
    assert not by_id(p.effective_lines())["L0001"]["include"]


def test_lines_of_the_same_words_that_cannot_be_told_apart_get_no_decision(tmp_path):
    same = [line("L0001", "하늘", "가자", 10.0, 10.8), line("L0002", "하늘", "가자", 12.0, 12.8)]
    p = make(tmp_path, lines=same, extras=[])
    p.set_line("L0001", include=False)
    # read again with a third '가자' right behind: the neighbours no longer tell which one it was
    again = renumbered(same + [line("L0003", "하늘", "가자", 14.0, 14.8)])
    write_json(p.path("dialogue.json"), {"mode": "scenes", "extras": [], "lines": again})
    assert all(v["include"] for v in p.effective_lines())


def test_a_line_decision_of_an_older_version_goes_by_its_place_among_the_same_words(tmp_path):
    fp = line_fp(TWICE[2], 1)                                   # stored without a context
    p = make(tmp_path, lines=TWICE, extras=[], review={"lines": {"L0003": {"fp": fp, "include": False}}})
    lines = by_id(p.effective_lines())
    assert lines["L0001"]["include"] and not lines["L0003"]["include"]


def test_the_line_where_a_name_is_shown_follows_its_line_when_the_ids_change(tmp_path):
    ls = [line("L0001", "하늘", "가자", 10.0, 10.8), line("L0002", "바다", "응", 11.5, 12.0),
          line("L0003", "하늘", "가자", 13.0, 13.8), line("L0004", "하늘", "어서", 15.0, 15.8)]
    p = make(tmp_path, lines=ls, extras=[])
    p.set_people(["하늘"], mode="generic", before="여자", reveal="line", reveal_line="L0003")
    labels = {k: v["label_name"] for k, v in by_id(p.effective_lines()).items()}
    assert (labels["L0001"], labels["L0003"]) == ("여자", "하늘")
    again = renumbered([line("L0000", "하늘", "가자", 5.0, 5.8)] + ls)
    write_json(p.path("dialogue.json"), {"mode": "scenes", "extras": [], "lines": again})
    labels = {k: v["label_name"] for k, v in by_id(p.effective_lines()).items()}
    assert [labels[k] for k in ("L0001", "L0002", "L0004", "L0005")] == ["여자", "여자", "하늘", "하늘"]


# =================================================================== what the writer adds (4-05, 5-05, 7-m1)
def test_an_extra_becomes_a_line_or_is_set_aside(tmp_path):
    p = make(tmp_path)
    assert {x["id"]: x["state"] for x in p.compose()["extras"]} == {"X0001": "open", "X0002": "open"}
    out = p.set_extra("X0001", "line", name="바다")
    aid = out["added"]
    added = by_id(p.effective_lines())[aid]
    assert (added["origin"], added["text"], added["name"], added["scene_id"]) == ("heard", "저기요 잠깐만", "바다", "SC001")
    assert added["words"] == [{"w": "저기요", "s": 20.0, "e": 20.5}, {"w": "잠깐만", "s": 20.6, "e": 21.2}]
    d = p.recompute()
    cell = next(c for c in d["cells"] if aid in c["refs"])
    assert cell["lines"][0].startswith("[바다] 저기요")                          # a new line shows its name
    rows = by_id(p.compose()["extras"])
    assert rows["X0001"]["state"] == "line" and rows["X0001"]["added"] == aid
    assert read_json(p.path("review.json"))["added"][aid]["extra_fp"] == extra_fp(EXTRAS[0])
    with pytest.raises(Conflict):
        p.set_extra("X0001", "line")
    p.set_extra("X0002", "ignore")
    assert p.export_check()["extras_open"] == []
    p.set_extra("X0001", "ignore")                                               # takes the line back
    assert aid not in by_id(p.effective_lines()) and by_id(p.compose()["extras"])["X0001"]["state"] == "ignored"
    p.set_extra("X0001", "clear")
    assert by_id(p.compose()["extras"])["X0001"]["state"] == "open"
    with pytest.raises(KeyError):
        p.set_extra("X0999", "ignore")


def test_the_writer_adds_a_line_a_sound_and_a_note(tmp_path):
    p = make(tmp_path)
    a = p.add("line", start=30.0, end=31.5, text="그럼 또 봐", name="구름", sung=False)["id"]
    s = p.add("sfx", start=45.0, end=46.0, phrase="문 닫히는 소리")["id"]
    m = p.add("music", start=90.0, end=94.0, phrase="[음악이 멈춘다]")["id"]
    assert (a, s, m) == ("A0001", "A0002", "A0003")
    d = p.recompute()
    text = {r: c["lines"] for c in d["cells"] for r in c["refs"]}
    assert text[a][0].startswith("[구름] 그럼") and text[s] == ["[문 닫히는 소리]"] and text[m] == ["[음악이 멈춘다]"]
    items = by_id(p.effective_items())
    assert items[s]["final"] == "include" and items[s]["origin"] == "writer"
    p.update_added(s, phrase="[쾅]")
    assert by_id(p.effective_items())[s]["phrase"] == "[쾅]"
    p.delete_added(m)
    assert m not in by_id(p.effective_items())
    with pytest.raises(ReviewError):
        p.add("line", start=30.0, end=31.0, text="")
    with pytest.raises(ReviewError):
        p.add("sfx", start=50.0, end=49.0, phrase="[쿵]")
    with pytest.raises(ReviewError):
        p.add("credits", start=1.0, end=2.0)
    with pytest.raises(KeyError):
        p.delete_added("A0099")


# =================================================================== people and labels (3-01, 3-08, 3-12)
def test_names_shown_before_they_are_told_are_listed(tmp_path):
    p = make(tmp_path)
    p.recompute()
    early = {e["person"]: e for e in p.export_check()["names_early"]}
    assert early["하늘"] == {"person": "하늘", "line": "L0001", "told": "L0004"}
    assert early["바다"]["told"] is None                                           # never called by name
    assert "경찰1" not in early                                                    # a role name: name by default
    people = by_id([dict(x, id=x["name"]) for x in p.compose()["people"]])
    assert people["하늘"]["told"]["line"] == "L0004" and people["하늘"]["lines"] == 3


def test_a_generic_label_until_the_name_is_told(tmp_path):
    p = make(tmp_path)
    p.set_people(["하늘"], mode="generic", before="여자")
    lines = by_id(p.effective_lines())
    assert (lines["L0001"]["label_name"], lines["L0005"]["label_name"]) == ("여자", "하늘")
    d = p.recompute()
    text = {r: c["lines"][0] for c in d["cells"] for r in c["refs"]}
    assert text["L0001"].startswith("[여자]") and text["L0005"].startswith("[하늘]")
    assert "하늘" not in {e["person"] for e in p.export_check()["names_early"]}
    p.set_people(["바다", "구름"], mode="name")                                    # role names in one go
    assert p.export_check()["names_early"] == []
    with pytest.raises(ReviewError):
        p.set_people(["하늘"], mode="generic", before="")
    with pytest.raises(KeyError):
        p.set_people(["하늘", "없는사람"], mode="name")
    p.set_people(["하늘"], reveal="line", reveal_line="L0001")                     # e.g. a name tag on screen
    assert by_id(p.effective_lines())["L0001"]["label_name"] == "하늘"
    p.set_people(["하늘"], mode=None)
    assert "하늘" not in read_json(p.path("review.json"))["people"] or \
        read_json(p.path("review.json"))["people"]["하늘"].get("mode") is None


def test_two_people_under_one_generic_label_are_numbered_in_the_scene(tmp_path):
    lines = [line("L0001", "갑", "거기 누구야", 10.0, 11.0), line("L0002", "을", "나야 나", 12.0, 13.0),
             line("L0003", "갑", "왜 왔어", 14.0, 15.0), line("L0004", "을", "그냥 왔지", 20.0, 21.0, scene="SC003")]
    p = make(tmp_path, lines=lines)
    p.set_people(["갑", "을"], mode="generic", before="남자")
    got = {k: v["label_name"] for k, v in by_id(p.effective_lines()).items()}
    assert got == {"L0001": "남자1", "L0002": "남자2", "L0003": "남자1", "L0004": "남자"}
    text = {r: c["lines"] for c in p.recompute()["cells"] for r in c["refs"]}
    assert text["L0002"] == ["[남자2] 나야 나"] and text["L0004"] == ["[남자] 그냥 왔지"]


def test_a_speaker_called_man_in_the_script_and_a_person_labelled_man_are_two_in_the_subtitles(tmp_path):
    ls = [line("L0001", "남자", "거기 서 봐요", 10.0, 11.0), line("L0002", "가람", "왜요 무슨 일인데요", 12.0, 13.5),
          line("L0003", "남자", "이리 좀 와 봐요", 14.0, 15.0)]
    p = make(tmp_path, lines=ls, items=[speaker_item(x) for x in ls], extras=[])
    p.set_people(["가람"], mode="generic", before="남자")
    cells = [c["lines"] for c in p.recompute()["cells"]]
    assert cells == [["[남자1] 거기 서 봐요"], ["[남자2] 왜요 무슨 일인데요"], ["[남자1] 이리 좀 와 봐요"]]


def test_speaker_scopes_go_by_person_not_by_the_label(tmp_path):
    p = make(tmp_path)
    p.set_speaker("L0003", "한별", "from_here")                                  # 하늘's lines from L0003 on
    names = {k: v["name"] for k, v in by_id(p.effective_lines()).items()}
    assert (names["L0001"], names["L0003"], names["L0005"], names["L0008"]) == ("하늘", "한별", "한별", "한별")
    assert names["L0002"] == "바다"
    p.set_speaker("L0005", "하늘", "before")                                     # 한별's lines before L0005
    names = {k: v["name"] for k, v in by_id(p.effective_lines()).items()}
    assert (names["L0003"], names["L0005"]) == ("하늘", "한별")
    # two people the writer both called '남자' through the older API stay two after a scope change
    p.set_decision("P0001", name="남자")
    p.set_decision("P0002", name="남자")
    p.set_speaker("L0001", "하늘", "line")
    names = {k: v["name"] for k, v in by_id(p.effective_lines()).items()}
    assert (names["L0001"], names["L0002"]) == ("하늘", "남자")
    p.set_decision("P0004", name="파도", scope="speaker")                         # older scope: the person
    names = {k: v["name"] for k, v in by_id(p.effective_lines()).items()}
    assert names["L0004"] == "파도" and names["L0002"] == "남자"
    assert "파도" in p.characters()


# =================================================================== worked out again (3-07, 6-m2, 9-x1)
def run_lines():
    return [line("L0001", "하늘", "잠깐 기다려", 10.0, 11.0), line("L0002", "하늘", "금방 갈게", 16.0, 17.0),
            line("L0003", "하늘", "거의 다 왔어", 23.0, 24.0, scene="SC003")]


def test_a_speaker_seen_on_the_line_before_needs_no_name_however_long_the_pause(tmp_path):
    ls = run_lines()
    items = [speaker_item(ls[0], face="visible", rec="exclude", reason="말하는 얼굴이 보임"),
             speaker_item(ls[1]), speaker_item(ls[2])]
    p = make(tmp_path, lines=ls, items=items, extras=[])
    it = by_id(p.effective_items())
    assert (it["P0002"]["rec"], it["P0002"]["follows"], it["P0002"]["rec_stage"]) == ("exclude", "P0001", "include")
    assert it["P0002"]["reason"].startswith("같은 화자가 이어 말함")                # 5 s pause: no time limit
    assert it["P0003"]["rec"] == "include" and "follows" not in it["P0003"]        # another scene
    lines = by_id(p.effective_lines())
    assert not lines["L0002"]["show_name"] and lines["L0003"]["show_name"]


def test_continuing_lines_follow_the_writers_change_on_the_line_before(tmp_path):
    ls = run_lines()[:2]
    items = [speaker_item(ls[0]),
             speaker_item(ls[1], rec="exclude", reason="같은 화자가 이어 말함 · 앞 줄에 이름 표시", follows="P0001")]
    p = make(tmp_path, lines=ls, items=items, extras=[])
    assert by_id(p.effective_items())["P0002"]["final"] == "exclude"
    p.set_decision("P0001", name="바다")                                           # the line before is someone else
    it = by_id(p.effective_items())["P0002"]
    assert (it["rec"], it["final"]) == ("include", "include") and "follows" not in it
    assert by_id(p.effective_lines())["L0002"]["show_name"]
    p.set_decision("P0001", name=None)
    p.set_decision("P0002", decision="include")                                   # the writer's own: untouched
    assert by_id(p.effective_items())["P0002"]["final"] == "include"
    p.set_line("L0001", include=False)                                            # the run is gone with its line
    p.set_decision("P0002", decision=None)
    assert by_id(p.effective_items())["P0002"]["rec"] == "include"


def test_a_line_that_gets_no_cell_names_nobody_for_the_line_after_it(tmp_path):
    # L0002 (a guessed time squeezed after L0001) gets no cell: 하늘 was never named there, so
    # 하늘's next line shows the name instead of going on from it
    ls = [line("L0001", "바다", "어서 와", 10.0, 11.0),
          line("L0002", "하늘", "잠깐만요 지금 바로 갈게요", 11.05, 11.25, status="unheard", timing="voice"),
          line("L0003", "하늘", "거의 다 왔어", 11.3, 12.3)]
    p = make(tmp_path, lines=ls, items=[speaker_item(x) for x in ls], extras=[])
    d = p.recompute()
    assert [e["line"] for e in d["no_room"] if e.get("line")] == ["L0002"]
    assert [c["lines"] for c in d["cells"]] == [["[바다] 어서 와"], ["[하늘] 거의 다 왔어"]]
    it = by_id(p.effective_items(draft=d))["P0003"]
    assert it["final"] == "include" and "follows" not in it and not it["reason"].startswith("같은 화자가 이어 말함")
    comp = p.compose(draft=d)
    lines = by_id(comp["lines"])
    assert lines["L0003"]["show_name"] and lines["L0002"]["no_room"] and comp["unplaced"] == ["L0002"]
    assert read_json(p.path("draft.json"))["cells"] == d["cells"]
    with TestClient(create_app(p.work)) as c:                                      # the server shows the same
        st = c.get("/api/state").json()
        assert [x["lines"] for x in st["draft"]["cells"]] == [["[바다] 어서 와"], ["[하늘] 거의 다 왔어"]]
        assert next(x for x in st["items"] if x["id"] == "P0003")["final"] == "include"
    # with room for L0002 the run is back: L0003 goes on from the name shown on L0002
    roomy = [ls[0], dict(ls[1], start=11.5, end=12.5, words=words(ls[1]["text"], 11.5, 12.5)),
             line("L0003", "하늘", "거의 다 왔어", 13.0, 14.0)]
    p2 = make(tmp_path / "b", lines=roomy, items=[speaker_item(x) for x in roomy], extras=[])
    d2 = p2.recompute()
    assert d2["no_room"] == [] and by_id(p2.effective_items(draft=d2))["P0003"]["final"] == "exclude"


def test_already_told_only_when_the_root_goes_out(tmp_path):
    p = make(tmp_path)
    it = by_id(p.effective_items())
    assert (it["S0002"]["final"], it["M0002"]["final"]) == ("exclude", "exclude")
    p.set_decision("S0001", decision="exclude")                                   # the root is no caption
    p.set_decision("M0001", decision="exclude")
    it = by_id(p.effective_items())
    assert it["S0002"]["final"] == "check" and "S0001" in it["S0002"]["reason"] and it["S0002"]["rec_stage"] == "exclude"
    assert it["M0002"]["final"] == "check"
    assert set(p.export_check()["check"]) >= {"S0002", "M0002"}
    # a root the writer takes in turns a waiting follower into 'already told'
    items = items_for(LINES)
    for x in items:
        if x["id"] == "M0001":
            x.update(rec="check", reason="음악 · 분위기 판단 애매")
        if x["id"] == "M0002":
            x.update(rec="check", reason="앞 후보와 같은 분위기 · 앞 후보를 먼저 정해야 함")
    p2 = make(tmp_path / "b", items=items)
    assert by_id(p2.effective_items())["M0002"]["final"] == "check"
    p2.set_decision("M0001", decision="include")
    assert by_id(p2.effective_items())["M0002"]["final"] == "exclude"


def test_a_root_without_room_tells_nothing(tmp_path):
    p = make(tmp_path)
    draft = {"cells": [], "no_room": [{"item": "S0001", "t": 25.0, "text": "[쿵]", "reason": "자리 없음"}]}
    it = by_id(p.effective_items(draft=draft))
    assert it["S0002"]["final"] == "check" and it["M0002"]["final"] == "exclude"


def test_a_sound_the_script_writes_down_is_marked_and_can_be_moved(tmp_path):
    p = make(tmp_path)
    p.set_decision("C0001", decision="include")
    c = next(c for c in p.recompute()["cells"] if "C0001" in c["refs"])
    assert "timing_estimated" in c["flags"]
    p.set_placement("C0001", "move", 82.0)                                        # inside its window
    c = next(c for c in p.recompute()["cells"] if "C0001" in c["refs"])
    assert c["start"] == pytest.approx(82.0) and "timing_estimated" not in c["flags"]


# =================================================================== older work folders
def old_style(tmp_path):
    """A work folder of before the review layer: no status, no scenes, no extras, an older review.json."""
    old = [{k: v for k, v in ln.items() if k in ("id", "speaker", "text", "start", "end", "words", "match",
                                                 "aligned", "timing")}
           for ln in LINES if ln["start"] is not None]
    for ln in old:
        ln["timing"] = "asr"
    items = [speaker_item(ln) for ln in old] + [sound("S0001", 25.0, "[쿵]")]
    for it in items:
        it["speaker"] = {k: v for k, v in (it.get("speaker") or {}).items() if k in ("line_id", "name", "face", "n_faces")} \
            if it["kind"] == "speaker" else None
        if it["speaker"] is None:
            it.pop("speaker")
    w = tmp_path / "old"
    w.mkdir()
    write_json(w / "project.json", {"title": "예전", "t0": 0.0, "t1": 120.0, "fps": FPS})
    write_json(w / "dialogue.json", {"lines": old})
    write_json(w / "items.json", {"items": items})
    write_json(w / "cuts.json", {"cuts": []})
    write_json(w / "review.json", {"items": {"P0002": {"decision": "exclude", "fp": "speaker@L0002:옆집 사람이에요"}},
                                   "placement": {}})
    return Project(w), old, items


def test_an_older_work_folder_opens_and_gives_the_same_draft(tmp_path):
    p, old, items = old_style(tmp_path)
    # the inputs placement got before the review layer: the line as it is, the name of its item
    final = {it["speaker"]["line_id"]: ("exclude" if it["id"] == "P0002" else it["rec"]) for it in items
             if it["kind"] == "speaker"}
    before = [dict(ln, show_name=final[ln["id"]] == "include", name=ln["speaker"]) for ln in old]
    sounds = [{"id": "S0001", "kind": "sfx", "onset": 25.0, "t1": 26.0, "phrase": "[쿵]", "short": "[쿵]",
               "source_line": None, "move_to": None}]
    want = placement.place(before, sounds, [], FPS, window=(0.0, 120.0), settings=style.get().settings())
    got = p.recompute()
    assert got["cells"] == want["cells"] and got["no_room"] == want["no_room"]
    lines = p.effective_lines()
    assert all(ln["status"] == "ok" and ln["include"] for ln in lines)
    chk = p.export_check()
    assert chk["lines"] == {"differs_open": [], "unheard": [], "absent": [], "out_of_range": [], "writer_excluded": [],
                            "no_room": []} and chk["extras_open"] == []
    rv = read_json(p.path("review.json"))
    assert set(rv) == {"items", "placement"}                                      # reading writes nothing


# =================================================================== the server
@pytest.fixture()
def client(tmp_path):
    p = make(tmp_path)
    with TestClient(create_app(p.work)) as c:
        yield c, p


def test_state_carries_lines_extras_scenes_people_and_counts(client):
    c, p = client
    st = c.get("/api/state").json()
    for key in ("lines", "extras", "scenes", "people", "added", "line_codes"):
        assert key in st
    rows = by_id(st["lines"])
    assert rows["L0006"]["excluded"] == "absent" and not rows["L0006"].get("include")
    assert st["line_codes"]["excluded"]["absent"] == "영화에 없음"
    assert rows["L0002"]["asr_text"] == "옆집 사람이요" and rows["L0002"]["diff"]
    assert all("words" not in r for r in st["lines"]) and all("words" not in x for x in st["extras"])
    assert rows["L0001"]["label"] == "[하늘]" and rows["L0001"]["show_name"] and rows["L0001"]["cells"]
    sm = st["summary"]["lines"]
    assert sm["status"] == {"ok": 4, "differs": 1, "unheard": 1, "absent": 1, "out_of_range": 1}
    assert sm["excluded"]["absent"] == 1 and sm["excluded"]["out_of_range"] == 1 and sm["included"] == 6
    assert st["summary"]["extras"] == {"open": 2, "line": 0, "ignored": 0, "total": 2}
    assert st["summary"]["people"]["early"] == 2
    assert {x["id"] for x in st["scenes"]} == {"SC001", "SC002", "SC003"}
    chk = st["export_check"]
    assert chk["lines"]["differs_open"] == ["L0002"] and len(chk["names_early"]) == 2
    warn = export_warnings(chk)
    assert any("아직 고르지 않은 줄 1개" in w for w in warn) and any("이름 표시가 나가는 인물 2명" in w for w in warn)
    assert any("'대본에 없는 말' 2개" in w for w in warn) and any("영화에 없는 장면이라 뺀 줄 1개" in w for w in warn)


def test_line_api(client):
    c, p = client
    st = c.post("/api/lines/L0002", json={"text_from": "heard"}).json()
    assert by_id(st["lines"])["L0002"]["text"] == "옆집 사람이요"
    assert c.post("/api/lines/L0002", json={}).status_code == 400
    assert c.post("/api/lines/L0999", json={"include": False}).status_code == 404
    r = c.post("/api/lines/L0006", json={"include": True})
    assert r.status_code == 409 and "시작과 끝" in r.json()["detail"]
    assert c.post("/api/lines/L0006", json={"include": True, "start": 30, "end": 29}).status_code == 400
    assert c.post("/api/lines/L0004", json={"breaks": [9]}).status_code == 400
    assert c.post("/api/lines/L0004", json={"include": "maybe"}).status_code == 422
    st = c.post("/api/lines/L0006", json={"include": True, "start": 30, "end": 32}).json()
    assert by_id(st["lines"])["L0006"]["include"] and st["summary"]["lines"]["included"] == 7
    r = c.post("/api/lines/bulk", json={"ids": ["L0001", "L0999"], "include": False})
    assert r.status_code == 404 and "L0001" not in read_json(p.path("review.json"))["lines"]
    assert c.post("/api/lines/bulk", json={"ids": ["L0001", "L0008"], "include": True}).status_code == 409
    st = c.post("/api/lines/bulk", json={"ids": ["L0001", "L0003"], "include": False}).json()
    assert st["export_check"]["lines"]["writer_excluded"] == ["L0001", "L0003"]
    assert "P0001" not in {it["id"] for it in st["items"]}                       # its speaker item is out too
    st = c.post("/api/lines/L0005/speaker", json={"name": "한별", "scope": "from_here"}).json()
    assert by_id(st["lines"])["L0005"]["name"] == "한별" and by_id(st["lines"])["L0008"]["name"] == "한별"
    assert c.post("/api/lines/L0005/speaker", json={"name": "한별", "scope": "everyone"}).status_code == 422
    st = c.post("/api/items/P0004", json={"name": "파도", "scope": "person"}).json()
    assert by_id(st["lines"])["L0002"]["name"] == "파도"


def test_extras_added_and_people_api(client):
    c, p = client
    st = c.post("/api/extras/X0001", json={"action": "line", "name": "바다"}).json()
    aid = next(x["added"] for x in st["extras"] if x["id"] == "X0001")
    assert aid == "A0001" and by_id(st["lines"])[aid]["origin"] == "heard"
    assert c.post("/api/extras/X0001", json={"action": "line"}).status_code == 409
    assert c.post("/api/extras/X0404", json={"action": "ignore"}).status_code == 404
    assert c.post("/api/extras/X0002", json={"action": "shout"}).status_code == 422
    st = c.post("/api/added", json={"kind": "sfx", "start": 45.0, "end": 46.0, "phrase": "[쿵]"}).json()
    added = by_id(st["added"])
    assert added["A0002"]["cells"] and st["summary"]["kind"]["sfx"] >= 1
    assert c.post("/api/added", json={"kind": "sfx", "start": 46.0, "end": 45.0, "phrase": "[쿵]"}).status_code == 400
    assert c.post("/api/added", json={"kind": "line", "start": 45.0, "end": 46.0}).status_code == 400
    st = c.post("/api/added/A0002", json={"phrase": "[쾅]"}).json()
    assert by_id(st["items"])["A0002"]["phrase"] == "[쾅]"
    assert c.post("/api/added/A0099", json={"phrase": "[쾅]"}).status_code == 404
    assert c.delete("/api/added/A0002").status_code == 200 and c.delete("/api/added/A0002").status_code == 404
    st = c.post("/api/people", json={"names": ["하늘"], "mode": "generic", "before": "여자"}).json()
    assert by_id(st["lines"])["L0001"]["label_name"] == "여자"
    assert next(x for x in st["people"] if x["name"] == "하늘")["decision"]["before"] == "여자"
    assert c.post("/api/people", json={"names": ["바다"], "mode": "generic"}).status_code == 400   # no label given
    assert c.post("/api/people", json={"names": ["유령"], "mode": "name"}).status_code == 404
    assert c.post("/api/people", json={"names": []}).status_code == 400
    st = c.post("/api/people", json={"names": ["바다", "경찰1"], "mode": "name"}).json()
    assert st["export_check"]["names_early"] == []
    # a sound the script writes down: moved inside its window like any other sound, then no guess
    st = c.post("/api/placement/C0001", json={"action": "move", "t": 82.0}).json()
    cell = next(x for x in st["draft"]["cells"] if "C0001" in x["refs"])
    assert by_id(st["items"])["C0001"]["final"] == "include" and "timing_estimated" not in cell["flags"]
    # the older routes still work next to the new ones
    assert c.post("/api/items/S0001", json={"decision": "exclude"}).status_code == 200
    assert c.post("/api/placement/S0001", json={"action": "move", "t": 27.0}).status_code == 200
    assert c.post("/api/export", json={"format": "srt"}).status_code == 200


def test_name_display_of_lines_without_a_speaker_item(tmp_path):
    p = make(tmp_path)
    p.set_line("L0006", include=True, start=30.0, end=32.0)                     # brought back: no speaker item
    text = {r: c["lines"][0] for c in p.recompute()["cells"] for r in c["refs"]}
    assert text["L0006"].startswith("[구름] ")
    p.set_line("L0006", show_name=False)
    text = {r: c["lines"][0] for c in p.recompute()["cells"] for r in c["refs"]}
    assert text["L0006"].startswith("여기")
    with pytest.raises(ReviewError):
        p.set_line("L0001", show_name=False)                                     # that is the speaker item's choice
    # a timed line that simply has no speaker item (an older run): no name, as before
    q = make(tmp_path / "q", items=[it for it in items_for(LINES) if it["id"] != "P0001"])
    assert not by_id(q.effective_lines())["L0001"]["show_name"]
