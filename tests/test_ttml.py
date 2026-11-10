# -*- coding: utf-8 -*-
"""TTML export (cboe.export.to_ttml): General Requirements 13, frame-based times, two regions."""
import re
import xml.etree.ElementTree as ET

import pytest

from cboe import check, export

TT = "{http://www.w3.org/ns/ttml}"
TTP = "{http://www.w3.org/ns/ttml#parameter}"
TTS = "{http://www.w3.org/ns/ttml#styling}"
XML = "{http://www.w3.org/XML/1998/namespace}"
FPS = 23.976


def cell(cid, start, end, lines, **more):
    return {"id": cid, "start": start, "end": end, "lines": lines, "refs": [], "kind": "dialogue", **more}


CELLS = [cell("C0002", 12.012, 13.514, ["둘째 <줄> & \"따옴표\""], pos="top"),
         cell("C0001", 10.01, 11.512, ["- [빌] 뭐?", "- [쿵]"]),
         cell("C0003", 20.02, 22.022, ["[영수] ♪ 달빛 아래", "걷던 그 길을 ♪"], pos="bottom")]


def parse(text):
    return ET.fromstring(text.encode("utf-8"))


def test_document_follows_general_requirements_13():
    text = export.to_ttml(CELLS, FPS, title="가즈아 <도서관>")
    assert text.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<tt ')
    root = parse(text)                                        # well-formed
    assert root.tag == TT + "tt" and root.get(XML + "lang") == "ko"
    assert root.get(TTP + "timeBase") == "media"
    assert root.find(f"{TT}head/{TT}metadata/{{http://www.w3.org/ns/ttml#metadata}}title").text == "가즈아 <도서관>"
    # "tts:fontSize shall be defined as 100%" (글자 크기는 100% 로)
    styles = root.findall(f"{TT}head/{TT}styling/{TT}style")
    assert [s.get(TTS + "fontSize") for s in styles] == ["100%"]
    # "Use tts:textAlign and tts:displayAlign for positioning along with static values for tts:extent
    #  and tts:origin" (위치는 textAlign·displayAlign 으로, extent·origin 은 고정 값으로): two regions
    regions = {r.get(XML + "id"): r for r in root.findall(f"{TT}head/{TT}layout/{TT}region")}
    assert set(regions) == {"bottom", "top"}
    assert regions["bottom"].get(TTS + "displayAlign") == "after"
    assert regions["top"].get(TTS + "displayAlign") == "before"
    for r in regions.values():
        assert r.get(TTS + "textAlign") == "center"
        assert r.get(TTS + "extent") == regions["bottom"].get(TTS + "extent") == "80% 80%"
        assert r.get(TTS + "origin") == regions["bottom"].get(TTS + "origin") == "10% 10%"
    # "Only use percentage values. Do not use pixel values." (퍼센트 값만, 픽셀 값은 쓰지 않는다)
    assert not re.search(r"\d\s*px\b", text)
    for name in ("extent", "origin", "fontSize"):
        for value in re.findall(rf'tts:{name}="([^"]*)"', text):
            assert re.fullmatch(r"\d+%( \d+%)?", value), (name, value)
    # no position or size on the subtitles themselves: only the two regions carry them
    for p in root.iter(TT + "p"):
        assert not any(k.startswith(TTS) for k in p.attrib) and p.get("region") in regions
    assert "♬" not in text and text.endswith("</tt>\n") and "\r" not in text


def test_times_are_whole_frames():
    root = parse(export.to_ttml(CELLS, FPS))
    assert (root.get(TTP + "frameRate"), root.get(TTP + "frameRateMultiplier")) == ("24", "1000 1001")
    ps = list(root.iter(TT + "p"))
    assert [(p.get("begin"), p.get("end")) for p in ps] == [("240f", "276f"), ("288f", "324f"), ("480f", "528f")]
    # in time order, each with the region of its ``pos`` (bottom when it has none)
    assert [p.get("region") for p in ps] == ["bottom", "top", "bottom"]
    assert [p.get(XML + "id") for p in ps] == ["c1", "c2", "c3"]
    # frame numbers come back exactly, whatever the ms rounding of the cell times was
    cells, meta = check.parse_ttml(export.to_ttml(CELLS, FPS))
    assert meta["fps"] == pytest.approx(24000 / 1001)
    assert [round(c["start"] * meta["fps"]) for c in cells] == [240, 288, 480]
    assert [c["pos"] for c in cells] == ["bottom", "top", "bottom"]


@pytest.mark.parametrize("fps, rate, mult", [
    (24.0, "24", None), (25.0, "25", None), (30.0, "30", None), (60.0, "60", None),
    (23.976, "24", "1000 1001"), (24000 / 1001, "24", "1000 1001"), (29.97, "30", "1000 1001"),
    (59.94, "60", "1000 1001"), (12.5, "13", "25 26")])
def test_frame_rate_attributes(fps, rate, mult):
    root = parse(export.to_ttml([cell("C1", 1.0, 2.0, ["가"])], fps))
    assert root.get(TTP + "frameRate") == rate and root.get(TTP + "frameRateMultiplier") == mult
    p = next(root.iter(TT + "p"))
    got = int(rate) * (eval(mult.replace(" ", "/")) if mult else 1)       # the effective frame rate
    assert got == pytest.approx(fps, abs=0.005) and got == pytest.approx(export.effective_rate(fps))
    assert (p.get("begin"), p.get("end")) == (f"{round(1.0 * got)}f", f"{round(2.0 * got)}f")


@pytest.mark.parametrize("fps", [23.980015987, 23.98, 23.976, 24000 / 1001, 24.0, 25.0, 29.97, 59.94, 12.5])
def test_ttml_gives_back_the_seconds_of_the_srt(fps):
    # a rate a reader rounded (OpenCV: 23.98 for 24000/1001 video) is written as 24 x 1000/1001; the
    # frame numbers have to be counted at that rate, or the file drifts late (1.2 s after two hours)
    cells = [cell("C1", 1128.523, 1130.0, ["가나"]), cell("C2", 1791.492, 1793.25, ["다라"]),
             cell("C3", 7400.0, 7402.0, ["마바"])]
    got, meta = check.parse_ttml(export.to_ttml(cells, fps))
    srt = check.parse_subtitles(export.to_srt(cells))
    assert meta["fps"] == pytest.approx(export.effective_rate(fps))
    half = 0.5 / meta["fps"] + 1e-6
    for a, b in zip(got, srt, strict=True):
        assert abs(a["start"] - b["start"]) <= half and abs(a["end"] - b["end"]) <= half, (fps, a, b)


@pytest.mark.parametrize("fps, end, start", [
    (29999 / 1251, 1128.44, 1128.523),      # ffprobe's rate of a real video (23.98002): its own rate in the header
    (23.98, 877.648, 877.731)])             # a rounded rate (written as 24 x 1000/1001): the gap is kept
def test_two_frames_between_cells_stay_two_frames_in_the_file(tmp_path, fps, end, start):
    # 4th review: TTML rounded each time at 23.976 on its own, so a 2-frame gap of a 23.98 draft became
    # 1 frame ("간격 2프레임 미만" in the file, not in the check the server showed)
    cells = [cell("C0001", round(end - 1.5, 3), end, ["가나다"]), cell("C0002", start, round(start + 1.5, 3), ["라마바"])]
    draft = check.run({"cells": cells, "fps": fps})
    assert draft["violations"] == [] and round(start * fps) - round(end * fps) == 2
    if fps != 23.98:
        assert export.frame_rate(fps) == (24, "29999 30024") and export.effective_rate(fps) == pytest.approx(fps)
    path = export.write(cells, tmp_path / "a.ttml", "ttml", fps=fps)["path"]
    got, meta = check.parse_ttml(path.read_text(encoding="utf-8"))
    (b1, e1), (b2, e2) = [(round(c["start"] * meta["fps"]), round(c["end"] * meta["fps"])) for c in got]
    assert b2 - e1 == 2                                       # in the file's own frames
    assert check.run(path, fps=fps)["violations"] == []       # what `python -m cboe check --fps` says
    assert check.run(path)["violations"] == []                # and at the rate the header states
    assert export.write(cells, tmp_path / "b.ttml", "ttml", fps=fps)["check"]["violations"] == []


@pytest.mark.parametrize("fps, rate, mult", [
    (30.00001486621153, "30", None), (60.00002, "60", None), (24.00001, "24", None), (25.000001, "25", None),
    (29.99999, "30", None), (23.97603, "24", "1000 1001"), (29.97003, "30", "1000 1001"),
    (29999 / 1251, "24", "29999 30024"), (12.5, "13", "25 26")])
def test_a_rate_a_hair_off_a_whole_or_ntsc_rate_is_that_rate(tmp_path, fps, rate, mult):
    # 4th round check: ffprobe's avg_frame_rate of a 30 fps video (frames over duration) was
    # 30.00001486621153, and the header said ttp:frameRate="31" x 22198001/22937923. A rate within
    # RATE_TOLERANCE of a whole or NTSC rate is that rate; a rate of its own (29999/1251) stays exact
    assert export.frame_rate(fps) == (int(rate), mult)
    cells = []
    for n, at in enumerate((1128.0, 7000.0)):                 # a draft at fps: two frames between two cells
        k = round(at * fps)
        end, start = round(k / fps, 3), round((k + 2) / fps, 3)
        cells += [cell(f"C{2 * n + 1}", round(end - 1.5, 3), end, ["가나다"]),
                  cell(f"C{2 * n + 2}", start, round(start + 1.5, 3), ["라마바"])]
    assert check.run({"cells": cells, "fps": fps})["violations"] == []
    path = export.write(cells, tmp_path / "a.ttml", "ttml", fps=fps)["path"]
    text = path.read_text(encoding="utf-8")
    root = parse(text)
    assert root.get(TTP + "frameRate") == rate and root.get(TTP + "frameRateMultiplier") == mult
    got, meta = check.parse_ttml(text)
    half = 0.5 / meta["fps"] + 1e-6
    for a, b in zip(got, cells, strict=True):                 # the seconds come back within half a frame
        assert abs(a["start"] - b["start"]) <= half and abs(a["end"] - b["end"]) <= half, (fps, a, b)
    frames = [(round(c["start"] * meta["fps"]), round(c["end"] * meta["fps"])) for c in got]
    assert [frames[1][0] - frames[0][1], frames[3][0] - frames[2][1]] == [2, 2]    # in the file's own frames
    assert check.run(path, fps=fps)["violations"] == [] and check.run(path)["violations"] == []


def test_characters_xml_cannot_hold_are_left_out():
    text = export.to_ttml([cell("C1", 1.0, 2.0, ["가\x02나\x1f", "\x0b다￾"])], 24, title="제\x00목")
    root = parse(text)                                        # still well-formed
    p = next(root.iter(TT + "p"))
    assert p.text == "가나" and p.find(TT + "br").tail == "다"
    assert root.find(f"{TT}head/{TT}metadata/{{http://www.w3.org/ns/ttml#metadata}}title").text == "제목"


def test_lines_and_escaping():
    root = parse(export.to_ttml(CELLS, FPS))
    ps = list(root.iter(TT + "p"))
    assert ps[0].text == "- [빌] 뭐?" and [b.tail for b in ps[0].findall(TT + "br")] == ["- [쿵]"]
    assert ps[1].text == '둘째 <줄> & "따옴표"' and not ps[1].findall(TT + "br")      # escaped in the file, text again here
    assert "둘째 &lt;줄&gt; &amp; \"따옴표\"" in export.to_ttml(CELLS, FPS)
    assert ps[2].text == "[영수] ♪ 달빛 아래" and ps[2].find(TT + "br").tail == "걷던 그 길을 ♪"


def test_write_ttml_and_what_it_refuses(tmp_path):
    res = export.write(CELLS, tmp_path / "out" / "a.ttml", "TTML", fps=FPS, title="제목")
    assert res["format"] == "ttml" and res["cells"] == 3 and res["path"].name == "a.ttml"
    assert res["path"].read_bytes().decode("utf-8") == export.to_ttml(CELLS, FPS, "제목")
    assert b"\r\n" not in res["path"].read_bytes()
    assert res["check"]["fps"] == FPS
    # CELLS is not in time order, the file is: the check is the check of the file (4th review)
    assert [(v["cell"], v["code"]) for v in check.run({"cells": CELLS, "fps": FPS})["violations"]] == [("C0001", "order")]
    assert res["check"]["violations"] == []
    with pytest.raises(ValueError, match="fps"):          # frames cannot be written without the frame rate
        export.write(CELLS, tmp_path / "b.ttml", "ttml")
    with pytest.raises(ValueError):
        export.to_ttml(CELLS, 0)
    with pytest.raises(ValueError, match="내보낼 수 없는 칸"):
        export.write([cell("C1", 2.0, 2.0, ["가"])], tmp_path / "c.ttml", "ttml", fps=FPS)
    assert not (tmp_path / "b.ttml").exists() and not (tmp_path / "c.ttml").exists()


def test_export_command_writes_ttml_and_reports_the_check(tmp_path, capsys):
    from pathlib import Path

    from cboe.__main__ import main
    from cboe.util import write_json
    write_json(tmp_path / "project.json", {"title": "제목", "video": "x.mp4", "t0": 0.0, "t1": 60.0, "fps": 24.0})
    write_json(tmp_path / "dialogue.json", {"lines": [
        {"id": "L0001", "speaker": "빌", "text": "안녕하세요.", "start": 10.0, "end": 11.0}]})
    write_json(tmp_path / "items.json", {"items": []})
    assert main(["export", str(tmp_path), "--format", "ttml"]) == 0
    out, err = capsys.readouterr()
    path = Path(out.strip())                                  # the path alone on stdout, as before
    assert path == tmp_path / "export" / "제목.ttml"
    text = path.read_text(encoding="utf-8")
    assert '<p xml:id="c1" begin="240f" end="276f" region="bottom" style="s1">안녕하세요</p>' in text
    assert "지침 검사: 의무 0건 (0칸) · 권고 0건 (0칸)" in err and "--fps 24" in err


def test_ttml_of_placement_output_checks_like_the_draft():
    from cboe.placement import place
    lines = [{"id": "L1", "text": "안녕하세요 여러분. 반갑습니다.", "start": 10.0, "end": 12.0, "words": [],
              "show_name": True, "name": "양희"},
             {"id": "L2", "text": "위에 뜨는 줄", "start": 14.0, "end": 15.0, "words": [], "pos": "top"}]
    d = place(lines, [{"id": "S1", "kind": "sfx", "onset": 17.0, "t1": 17.5, "phrase": "[쿵]", "short": "[쿵]"}], [], FPS)
    text = export.to_ttml(d["cells"], d["fps"])
    cells, meta = check.parse_ttml(text)
    assert [c["lines"] for c in cells] == [c["lines"] for c in d["cells"]]
    assert [c["pos"] for c in cells] == [c["pos"] for c in d["cells"]] == ["bottom", "top", "bottom"]
    a, b = check.run(d), check.run({"cells": cells, "fps": meta["fps"]})
    assert a["violations"] == [] and b["violations"] == []
