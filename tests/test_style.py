# -*- coding: utf-8 -*-
"""지침 숫자와 표기 틀 (cboe.style): profiles, labels, notes, phrases, display punctuation, one home."""
import ast
import json
import re
from pathlib import Path

import pytest

from cboe import placement, style
from cboe.state import Project
from cboe.util import read_json, write_json

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- numbers and profiles

def test_profiles_and_guide_numbers():
    adult, child = style.get(), style.get("children")
    assert (adult.profile, adult.cps, adult.label) == ("adult", 14.0, "성인")            # II.3
    assert (child.profile, child.cps, child.label) == ("children", 11.0, "어린이")
    for st in (adult, child):
        assert (st.max_units, st.max_lines, st.gap_frames, st.half, st.max_dur) == (16.0, 2, 2, 0.5, 7.0)
        assert st.min_dur == pytest.approx(5 / 6)
    assert style.get("어린이") == style.get("kids") == style.get("Children") == child
    assert style.get(None) == style.get("") == style.get("성인") == adult
    with pytest.raises(ValueError, match="teen"):
        style.get("teen")
    assert style.for_project({"profile": "children"}) == child
    assert style.for_project({}) == style.for_project(None) == style.for_project({"profile": None}) == adult
    assert style.seconds_text(5 / 6) == "5/6" and style.seconds_text(7.0) == "7"
    assert style.DEFAULT_FPS == 24.0


def test_placement_takes_its_numbers_from_style():
    assert placement.DEFAULTS == dict(style.get().settings(), kiwi=True)
    assert style.get("children").settings()["cps"] == 11.0 and style.get().settings()["profile"] == "adult"
    public = style.get("children").public()
    json.dumps(public)                                         # goes to the page as JSON
    assert public["cps"] == 11.0 and public["profile_label"] == "어린이" and public["max_units"] == 16.0
    assert (public["open"], public["close"], public["note"], public["music"]) == ("[", "]", "♪", "음악")
    assert (public["joint_sep"], public["joint_all"], public["label_sep"]) == (", ", "함께", "/")


def _numbers(path):
    """Float constants and ``a / b`` constant fractions in the code of a module (not in its strings)."""
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            found.add(node.value)
        if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
                and isinstance(node.left, ast.Constant) and isinstance(node.right, ast.Constant)):
            found.add(f"{node.left.value}/{node.right.value}")
    return found


def test_guide_numbers_are_written_in_style_only():
    # audit 6-10: 16 units, 14 / 11 units a second, 5/6 s and 7 s used to be written in four files
    guide = {16.0, 14.0, 11.0, 7.0, "5/6"}
    assert guide <= _numbers(ROOT / "cboe" / "style.py")
    for name in ("textutil.py", "placement.py", "export.py", "check.py", "state.py", "phrases.py", "server/app.py"):
        assert not (_numbers(ROOT / "cboe" / name) & guide), name
    js = (ROOT / "cboe" / "server" / "static" / "app.js").read_text(encoding="utf-8")
    assert "초당 14자" not in js and "16자" not in js and "♬ ${" not in js     # the page shows what the server sends
    assert "(${" not in js.replace("translateX(${", "").replace("(${num(", "")          # no '(이름)' template left


# --------------------------------------------------------------------------- labels and notes

def test_speaker_label():
    assert style.speaker_label("영희") == "[영희] "                                      # II.8
    assert style.speaker_label("영희", ["영상"]) == "[영희/영상] "
    assert style.speaker_label("영희", ["속마음"], "영어") == "[영희/속마음/영어] "       # the language at the end
    assert style.speaker_label("영희", "속삭임") == "[영희/속삭임] "                      # one extra as a string
    assert style.speaker_label(None, (), "영어") == "[영어] "                            # II.6: no name shown
    assert style.speaker_label(None, ["속마음"]) == "[속마음] "
    assert style.speaker_label(None) == style.speaker_label("") == style.speaker_label("  ", [], None) == ""
    assert style.speaker_label("영희", ["영어"], "영어") == "[영희/영어] "                # not twice
    assert style.speaker_label("영희", (), "한국어") == "[영희] "                         # II.6: not for the original language
    assert style.speaker_label(" BJ   옴뫄 ") == "[BJ 옴뫄] " and style.speaker_label("[영희]") == "[영희] "


def test_joint_name():
    assert style.joint_name("영희/철수") == "영희, 철수"                                  # II.10 (O)
    assert style.speaker_label("에루/마야카") == "[에루, 마야카] "
    assert style.joint_name("영희/철수/민수") == "함께"                                   # wider than half a line
    assert style.joint_name("BJ 옴뫄/세바스찬") == "함께" and style.joint_name("영희/영희") == "영희"
    assert style.joint_name("영희") == "영희" and style.joint_name("") == style.joint_name(None) == ""
    assert style.joint_name("군인1/군인2") == "군인1, 군인2"


def test_line_labels():
    assert style.source_label("phone") == "전화" and style.source_label("inner") == "속마음"      # II.8 [영희/속마음]
    assert style.source_label("video") == "영상" and style.source_label("offscreen") == style.source_label(None) == ""
    ln = {"name": "피터", "show_name": True, "label_extra": [style.source_label("phone")], "language": "영어"}
    assert style.line_labels(ln) == ("[피터/전화/영어] ", "[영어] ")
    assert style.line_labels(dict(ln, show_name=False)) == ("[전화/영어] ", "[영어] ")
    assert style.line_labels(dict(ln, show_name=False), show_name=True)[0] == "[피터/전화/영어] "
    assert style.line_labels({"name": "피터", "show_name": True}) == ("[피터] ", "")
    assert style.line_labels({}) == ("", "")


def test_notes_and_brackets():
    assert style.lyric("달빛 아래") == "♪ 달빛 아래 ♪" == style.lyric("♪ 달빛 아래 ♪") == style.lyric("♪달빛 아래♪")
    assert style.lyric("") == ""
    assert style.bracket("쿵") == style.bracket("[쿵]") == style.bracket(" [ 쿵 ] ") == "[쿵]" and style.bracket("") == ""
    assert style.sfx_phrase("우르릉", "천둥 소리") == "[우르릉]"                           # II.9: the onomatopoeia first
    assert style.sfx_phrase("", "재채기") == style.sfx_phrase(None, "재채기") == "[재채기]"
    assert style.sfx_with_source("달칵", "안전띠") == "[달칵 - 안전띠]"                    # II.9 example
    assert style.sfx_with_source("[직]", "지퍼") == "[직 - 지퍼]" and style.sfx_with_source("직") == "[직]"
    assert style.music_phrase("경쾌한", "음악") == "[경쾌한 음악]" and style.music_phrase() == "[음악]"
    assert style.music_phrase(None, "록 음악 - 라디오") == "[록 음악 - 라디오]"            # II.7 example


# --------------------------------------------------------------------------- display punctuation

@pytest.mark.parametrize("line, shown", [
    ("사랑해.", "사랑해"),                                  # I.13 (X) -> no period at the end of a line
    ("사랑해,", "사랑해"),                                  # I.13 (X) -> no comma either
    ("사랑해. 그게 내가", "사랑해, 그게 내가"),             # I.13 (X) -> (O): a comma between two sentences
    ("하고 싶은 말이었어", "하고 싶은 말이었어"),
    ("가자...", "가자…"),                                   # I.4: one character
    ("이.. 일단 제가 여기 자.. 잠입을 했는데", "이… 일단 제가 여기 자… 잠입을 했는데"),
    ("가자....", "가자…"), ("가자….", "가자…"), ("가자…,", "가자…"),
    ("오늘 날씨는 아주…", "오늘 날씨는 아주…"),             # an ellipsis stays
    ("뭐……", "뭐……"),
    ("뭐?!", "뭐?!"), ("정말!", "정말!"), ("왜?.", "왜?"),
    ("3.5 킬로미터 남았어.", "3.5 킬로미터 남았어"),        # a point between digits stays
    ("버전 1.2.3 이야", "버전 1.2.3 이야"),
    ("호머 J. 심슨", "호머 J. 심슨"),                       # I.3: a period after a Latin initial
    ("1992. 3. 5 맞지", "1992. 3. 5 맞지"),
    ("그러네, 베리 쏘리, 내 잘못이야", "그러네, 베리 쏘리, 내 잘못이야"),      # commas inside the line stay
    ("- 응.", "- 응"), ("- 내가 어제 봤는데…", "- 내가 어제 봤는데…"),
    ("[지수] 나도야! [재채기]", "[지수] 나도야! [재채기]"),
    ("가자. [한숨]", "가자 [한숨]"),                        # a label follows: no period, no comma
    ("♪ 달빛 아래. ♪", "♪ 달빛 아래 ♪"),                      # I.17: not in front of the closing note
    ("♪ 달빛 아래, 그 길을 ♪", "♪ 달빛 아래, 그 길을 ♪"),
    ("♪ 달빛 아래,", "♪ 달빛 아래"),
    ("내가 말했지, '난 안 그랬어.'", "내가 말했지, '난 안 그랬어'"),           # I.11 (O) form: none before the closing quote
    ("'좋아.' 그가 말했다", "'좋아.' 그가 말했다"),                            # inside the line: left alone
    ("“그레이 아나토미”", "“그레이 아나토미”"), ("♪ '가사.' ♪", "♪ '가사' ♪"),
    ("좋아。", "좋아"), ("좋아，", "좋아"),
    ("  빈칸   정리.  ", "빈칸 정리"),
    (".", ""), (",", ""), ("", ""), ("…", "…"), ("♪", "♪"),
])
def test_tidy_line(line, shown):
    assert style.tidy_line(line) == shown
    assert style.tidy_line(shown) == shown                    # nothing more to do the second time
    strip = lambda s: re.sub(r"[.,…。，\s]", "", s)            # noqa: E731
    assert strip(style.tidy_line(line)) == strip(line)        # only punctuation and spaces differ: the words stay


def test_tidy_lines_drops_what_becomes_empty():
    assert style.tidy_lines(["가자.", ".", "어서, "]) == ["가자", "어서"]
    assert style.tidy_lines(None) == style.tidy_lines([]) == []


# --------------------------------------------------------------------------- older work folders

def test_modernize_old_notation():
    assert style.modernize_phrase("♬ 무서운 음악") == "[무서운 음악]"
    assert style.modernize_phrase("♬ 고조되는 무서운 음악") == "[고조되는 무서운 음악]"
    assert style.modernize_phrase("♬") == style.modernize_phrase("♬ 음악") == "[음악]"
    assert style.modernize_phrase("♬ [무서운 음악]") == "[무서운 음악]"
    for same in ("[쿵]", "[쿵, 책 떨어지는 소리]", "[무서운 음악]", "♪ 가사 ♪", "(웃음)", ""):
        assert style.modernize_phrase(same) == same           # only the old music mark is rewritten
    assert style.modernize_phrase(None) == ""
    assert style.modernize_line("(빌) 뭐?") == "[빌] 뭐?" and style.modernize_line("- (세바스찬) 응") == "- [세바스찬] 응"
    assert style.modernize_line("♬ 슬픈 음악") == "[슬픈 음악]" and style.modernize_line("- ♬ 슬픈 음악") == "- [슬픈 음악]"
    for same in ("[빌] 뭐?", "- [쿵]", "뭐라고 (웃으며) 했어", "(웃음)", "- 응"):
        assert style.modernize_line(same) == same


# --------------------------------------------------------------------------- the profile of a work folder

def _project(tmp_path, profile=None):
    meta = {"title": "t", "video": "x.mp4", "t0": 0.0, "t1": 100.0, "fps": 24.0}
    if profile is not None:
        meta["profile"] = profile
    write_json(tmp_path / "project.json", meta)
    lines = [{"id": "L0001", "speaker": "빌", "text": "가나다라마바사아자차카타.", "start": 10.0, "end": 10.3},
             {"id": "L0002", "speaker": "빌", "text": "응", "start": 11.2, "end": 11.5}]
    items = [{"id": "P0001", "kind": "speaker", "onset": 10.0, "t1": 10.3, "rec": "include", "phrase": lines[0]["text"],
              "evidence": [], "speaker": {"line_id": "L0001", "name": "빌", "face": "not_visible"}},
             {"id": "M0001", "kind": "music", "onset": 20.0, "t1": 30.0, "rec": "include", "phrase": "♬ 무서운 음악",
              "short": "♬ 음악", "evidence": []}]
    write_json(tmp_path / "dialogue.json", {"lines": lines})
    write_json(tmp_path / "items.json", {"items": items})
    write_json(tmp_path / "cuts.json", {"cuts": []})
    return Project(tmp_path)


def test_project_profile_reaches_placement_export_and_check(tmp_path):
    p = _project(tmp_path / "a")
    d = p.recompute()
    first = d["cells"][0]
    assert d["profile"] == "adult" and d["notation"] == style.NOTATION
    assert first["lines"] == ["[빌] 가나다라마바사아자차카타"] and first["check"]["violations"] == []
    assert 11.0 < first["check"]["cps"] <= 14.0                        # 14.5 units in 27 frames: fine for adults only
    assert d["cells"][-1]["lines"] == ["[무서운 음악]"]                 # an old phrase in today's notation
    chk = p.export_check()
    assert chk["profile"] == "adult" and chk["guide"]["ok"] and chk["guide"]["fps"] == 24.0
    assert chk["violations"] == [] and chk["flags"] == {}

    kids = _project(tmp_path / "b", "children")
    d = kids.recompute()
    assert d["profile"] == "children" and "초당 11자 초과" in d["cells"][0]["check"]["violations"]
    chk = kids.export_check()
    assert chk["profile"] == "children" and not chk["guide"]["ok"]
    found = [(v["cell"], v["code"], v["message"]) for v in chk["guide"]["violations"]]
    assert found == [("C0001", "reading_speed", "초당 11자 초과")]
    res = kids.export("srt", report=True)
    assert res["check"]["profile"] == "children" and res["check"]["fps"] == 24.0 and res["path"].name == "t.srt"
    assert kids.export("ttml").read_text(encoding="utf-8").count("<p ") == len(d["cells"])
    assert isinstance(kids.export("vtt"), Path)


def test_draft_of_an_older_version_is_recomputed_for_the_check(tmp_path):
    p = _project(tmp_path)
    old = {"cells": [{"id": "C0001", "start": 10.0, "end": 11.5, "lines": ["(빌) 가나다라마바사아자차카타."], "refs": ["L0001"],
                      "kind": "dialogue", "check": {"violations": []}}], "no_room": [], "fps": 24.0}
    write_json(tmp_path / "draft.json", old)                   # written before the notation changed: no "notation"
    chk = p.export_check()
    assert chk["guide"]["ok"] and chk["guide"]["cells"] == 3
    assert read_json(tmp_path / "draft.json")["notation"] == style.NOTATION
    write_json(tmp_path / "project.json", dict(read_json(tmp_path / "project.json"), profile="children"))
    assert p.export_check()["profile"] == "children" and read_json(tmp_path / "draft.json")["profile"] == "children"


def test_init_children_flag_writes_the_profile(tmp_path, monkeypatch, capsys):
    from cboe.__main__ import main
    from cboe.stages import ingest

    def fake_init(work, video, script, t0, t1, title):          # the real one probes a video file
        old = read_json(Path(work) / "project.json", {}) or {}
        meta = {**old, "title": title or "t", "video": video, "t0": t0, "t1": 10.0, "fps": 24.0}
        write_json(Path(work) / "project.json", meta)
        return meta

    monkeypatch.setattr(ingest, "init", fake_init)
    work = tmp_path / "w"
    assert main(["init", str(work), "--video", "v.mp4"]) == 0
    assert read_json(work / "project.json")["profile"] == "adult"
    capsys.readouterr()
    assert main(["init", str(work), "--video", "v.mp4", "--children"]) == 0
    assert read_json(work / "project.json")["profile"] == "children"
    assert json.loads(capsys.readouterr().out)["profile"] == "children"              # what the command prints
    assert main(["init", str(work), "--video", "v.mp4"]) == 0                       # kept until it is changed
    assert read_json(work / "project.json")["profile"] == "children"
    assert main(["init", str(work), "--video", "v.mp4", "--profile", "adult"]) == 0
    assert read_json(work / "project.json")["profile"] == "adult"
    assert style.for_project(read_json(work / "project.json")).cps == 14.0
