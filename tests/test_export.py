# -*- coding: utf-8 -*-
"""Last check and SRT / VTT writing (cboe.export)."""
import shutil
from pathlib import Path

import pytest

from cboe import export
from cboe.placement import place

FPS = 23.976
ROOT = Path(__file__).resolve().parents[1]


def cell(cid, start, end, lines):
    return {"id": cid, "start": start, "end": end, "lines": lines, "refs": [], "kind": "dialogue"}


def test_srt_and_vtt_text():
    cells = [cell("C0002", 12.0, 13.5, ["둘째 <줄>"]), cell("C0001", 10.01, 11.512, ["- [빌] 뭐?", "- [쿵]"])]
    srt = export.to_srt(cells)
    assert srt == ("1\n00:00:10,010 --> 00:00:11,512\n- [빌] 뭐?\n- [쿵]\n\n"
                   "2\n00:00:12,000 --> 00:00:13,500\n둘째 <줄>\n")
    vtt = export.to_vtt(cells)
    assert vtt.startswith("WEBVTT\n\n00:00:10.010 --> 00:00:11.512\n- [빌] 뭐?\n- [쿵]\n")
    assert "둘째 &lt;줄&gt;" in vtt                    # cue text is escaped in VTT
    assert export._ts(3723.4567, ",") == "01:02:03,457"
    top = export.to_vtt([dict(cells[0], pos="top")])
    assert "00:00:12.000 --> 00:00:13.500 line:10%\n" in top and "line:" not in vtt   # a cell at the top


def test_write_utf8_and_formats(tmp_path):
    cells = [cell("C0001", 1.0, 2.0, ["[BJ 옴뫄] 으아아아아"])]
    res = export.write(cells, tmp_path / "out" / "a.srt", "srt", fps=FPS)
    p = res["path"]
    assert p.read_bytes().decode("utf-8") == export.to_srt(cells)
    assert b"\r\n" not in p.read_bytes()
    assert (res["format"], res["cells"]) == ("srt", 1)
    p = export.write(cells, tmp_path / "a.vtt", "VTT")["path"]
    assert p.read_text(encoding="utf-8").startswith("WEBVTT")
    with pytest.raises(ValueError):
        export.write(cells, tmp_path / "a.txt", "txt")
    assert set(export.FORMATS) == {"srt", "vtt", "ttml"} == set(export.MEDIA_TYPES)


def test_write_returns_the_guide_check_with_the_drafts_frame_rate(tmp_path):
    # audit 2-07, 6-04: the check used to run without fps (30 assumed) and its result was thrown away.
    # At 60 fps two frames are 33 ms: fine at 60, "간격 2프레임 미만" when 24 or 30 is assumed.
    two = [cell("C0001", 1.0, 2.0, ["하나"]), cell("C0002", round(2.0 + 2 / 60, 3), 3.5, ["둘"])]
    res = export.write(two, tmp_path / "a.srt", "srt", fps=60.0)
    chk = res["check"]
    assert chk["ok"] and chk["fps"] == 60.0 and not chk["fps_assumed"] and chk["violations"] == []
    assumed = export.write(two, tmp_path / "b.srt", "srt")["check"]
    assert assumed["fps_assumed"] and assumed["fps"] == 24.0
    assert [v["code"] for v in assumed["violations"]] == ["frame_gap"]
    # what the old check never looked at: notation and punctuation, clause by clause
    bad = [cell("C0001", 1.0, 2.5, ["(빌) 사랑해."]), cell("C0002", 3.0, 5.0, ["♬ 슬픈 음악"])]
    chk = export.write(bad, tmp_path / "c.srt", "srt", fps=FPS)["check"]
    assert not chk["ok"]
    assert {(v["cell"], v["clause"], v["code"]) for v in chk["violations"]} == {
        ("C0001", "II.8·II.9", "label_parens"), ("C0001", "I.13", "line_end_punct"), ("C0002", "II.7", "note_glyph")}
    assert (tmp_path / "c.srt").is_file()             # reported, not refused: the writer decides
    kids = export.write([cell("C0001", 1.0, 2.0, ["가나다라마바사아자차카타파"])], tmp_path / "d.srt", fps=FPS,
                        profile="children")["check"]
    assert kids["profile"] == "children" and [v["message"] for v in kids["violations"]] == ["초당 11자 초과"]


def test_write_refuses_cells_that_cannot_be_written(tmp_path):
    for bad in (cell("C0001", 2.0, 2.0, ["가"]), cell("C0001", 1.0, 2.0, ["  "]), cell("C0001", None, 2.0, ["가"])):
        with pytest.raises(ValueError):
            export.write([bad], tmp_path / "x.srt")
        assert export.final_check([bad])["fatal"]


def test_final_check_finds_limits_overlaps_and_order():
    cells = [cell("C0001", 9.0, 11.0, ["가" * 17]),                   # 17 units
             cell("C0002", 11.05, 11.5, ["가나다라마바사아자차"]),        # 2 frames short, too fast, under 5/6 s
             cell("C0003", 20.0, 28.0, ["하나", "둘", "셋"]),             # 3 lines, over 7 s
             cell("C0004", 15.0, 16.0, ["응"])]                          # out of order
    chk = export.final_check({"cells": cells, "fps": FPS, "no_room": [{"item": "S1", "t": 3.0, "reason": "x"}]})
    assert not chk["ok"]
    v = {x["cell"]: x["violations"] for x in chk["cells"]}
    assert v["C0001"] == ["한 줄 16자 초과"]
    assert set(v["C0002"]) == {"초당 14자 초과", "5/6초 미만"}
    assert set(v["C0003"]) == {"2줄 초과", "7초 초과"}
    assert [o["cell"] for o in chk["overlaps"]] == ["C0001"]
    assert [o["cell"] for o in chk["order"]] == ["C0004"]
    assert chk["no_room"] == [{"item": "S1", "t": 3.0, "reason": "x"}]
    assert chk["summary"]["violations"] == 3


def test_final_check_two_frames_at_23976():
    two = 2 / FPS
    ok = [cell("C0001", 10.01, 11.011, ["가"]), cell("C0002", round(11.011 + two, 3), 12.5, ["나"])]
    assert export.final_check({"cells": ok, "fps": FPS})["overlaps"] == []
    close = [cell("C0001", 10.01, 11.011, ["가"]), cell("C0002", round(11.011 + 1 / FPS, 3), 12.5, ["나"])]
    assert export.final_check({"cells": close, "fps": FPS})["overlaps"][0]["reason"] == "간격 2프레임 미만"


def test_final_check_passes_placement_output():
    lines = [{"id": "L1", "text": "안녕하세요 여러분", "start": 10.0, "end": 11.0, "words": [],
              "show_name": True, "name": "양희"},
             {"id": "L2", "text": "으아아아아", "start": 11.3, "end": 12.5, "words": []}]
    sounds = [{"id": "S1", "kind": "sfx", "onset": 11.35, "t1": 12.0, "phrase": "[천둥 소리]", "short": "[천둥]",
               "source_line": None, "move_to": None}]
    d = place(lines, sounds, [11.0], FPS)
    chk = export.final_check(d)
    assert chk["ok"], chk
    assert chk["summary"]["cells"] == len(d["cells"])


def test_project_export_on_the_sample(tmp_path):
    src = ROOT / "work" / "sample"
    if not (src / "dialogue.json").exists():
        pytest.skip("no work/sample")
    from cboe.state import Project
    work = tmp_path / "sample"
    shutil.copytree(src, work)
    proj = Project(work)
    out = proj.export("srt")
    text = out.read_text(encoding="utf-8")
    assert "- 으아아아아\n- [천둥 소리]" in text and "[쿵, 책 떨어지는 소리]" in text
    assert "[BJ 옴뫄] " in text and "(BJ 옴뫄)" not in text and "♬" not in text      # today's notation
    assert export.final_check(proj.draft())["ok"]
    assert proj.export("vtt").read_text(encoding="utf-8").startswith("WEBVTT")
    res = proj.export("ttml", report=True)
    assert res["path"].suffix == ".ttml" and res["check"]["fps"] == proj.draft()["fps"]
