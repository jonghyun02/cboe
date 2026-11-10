# -*- coding: utf-8 -*-
"""Unit tests for the analysis stages that need no GPU and no LLM."""
from __future__ import annotations

import json

import numpy as np
import pytest

from cboe import ontology, paths, phrases, script
from cboe.stages import align, recommend, sfx

# the class list comes from the PretrainedSED code (tools/setup_third_party.py on a fresh clone)
needs_psed = pytest.mark.skipif(not paths.PSED_CODE.exists(), reason="PretrainedSED code is not set up")


# ------------------------------------------------------------------ 대본 읽기
def test_script_txt(tmp_path):
    f = tmp_path / "s.txt"
    f.write_text("# 주석\n빌: 아니, 너무 늦었어\n(문 열리는 소리)\n세바스찬：경고하는 거죠?\n그냥 대사\n12:30 회의\n",
                 encoding="utf-8")
    d = script.read(f)
    assert [(ln["speaker"], ln["text"]) for ln in d["lines"]] == [
        ("빌", "아니, 너무 늦었어"), ("세바스찬", "경고하는 거죠?"), (None, "그냥 대사"), (None, "12:30 회의")]
    assert d["characters"] == ["빌", "세바스찬"]
    assert not d["timed"]


DUB_SCRIPT = ["9월 활동 <작품> - 가, 나", "", "—", "역할 [홍길동, 성춘향]", "가: 홍길동", "나: 성춘향", "—",
              "S#1 [교실]", "(NA)길동: 학교에 온 지 한 달. 이곳은 생각보다", " / 편한 곳이 되었다.",
              "춘향: 안녕하세요. / 오랜만이에요.", "길동: (물 마시고 뱉는 호)", "춘향 : (호) 네!", "<회상>",
              "친구1 : 어서 오세요.", "춘향/길동 : 어?!", "\t길동(E): 시너(thinner) 냄새… 1/2만", "S#2 [복도]"]


def test_script_dubbing_conventions(tmp_path):
    f = tmp_path / "s.txt"
    f.write_text("\n".join(DUB_SCRIPT), encoding="utf-8")
    d = script.read(f)
    assert [(ln["speaker"], ln["text"]) for ln in d["lines"]] == [
        ("길동", "학교에 온 지 한 달. 이곳은 생각보다 편한 곳이 되었다."), ("춘향", "안녕하세요. 오랜만이에요."),
        ("춘향", "네!"), ("친구1", "어서 오세요."), ("춘향/길동", "어?!"), ("길동", "시너 냄새… 1/2만")]
    assert d["characters"] == ["길동", "춘향", "친구1"]


def _docx(path, paragraphs, table_rows=()):
    import zipfile
    from xml.sax.saxutils import escape
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    para = lambda t: "<w:p>" + "".join(                                      # noqa: E731
        (f'<w:r><w:t xml:space="preserve">{escape(seg)}</w:t></w:r>' if i == 0 else
         f'<w:r><w:br/><w:t xml:space="preserve">{escape(seg)}</w:t></w:r>')
        for i, seg in enumerate(t.split("\n"))) + "</w:p>"
    tbl = "".join("<w:tr>" + "".join(f"<w:tc>{para(c)}</w:tc>" for c in row) + "</w:tr>" for row in table_rows)
    body = "".join(para(t) for t in paragraphs) + (f"<w:tbl>{tbl}</w:tbl>" if table_rows else "")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", f'<w:document xmlns:w="{w}"><w:body>{body}</w:body></w:document>')


def test_script_docx_paragraphs_breaks_and_tables(tmp_path):
    f = tmp_path / "s.docx"
    _docx(f, DUB_SCRIPT[:9] + ["춘향: 안녕하세요.\n길동: 어."], table_rows=[("춘향", "표 대사예요."), ("S#3 [끝]", "")])
    d = script.read(f)
    assert [(ln["speaker"], ln["text"]) for ln in d["lines"]] == [
        ("길동", "학교에 온 지 한 달. 이곳은 생각보다"), ("춘향", "안녕하세요."), ("길동", "어."), ("춘향", "표 대사예요.")]


def test_script_srt_keeps_times_and_names(tmp_path):
    f = tmp_path / "s.srt"
    f.write_text("1\n00:00:01,000 --> 00:00:02,500\n(빌) 아니, 너무 늦었어\n\n2\n00:00:03,000 --> 00:00:04,000\n"
                 "[천둥 소리]\n\n3\n00:00:05,000 --> 00:00:06,000\n- 경고하는 거죠?\n", encoding="utf-8")
    d = script.read(f)
    assert d["timed"]
    assert [(ln["speaker"], ln["text"], ln["start"]) for ln in d["lines"]] == [
        ("빌", "아니, 너무 늦었어", 1.0), (None, "경고하는 거죠?", 5.0)]


def test_script_csv(tmp_path):
    f = tmp_path / "s.csv"
    f.write_text("speaker,text\n빌,안녕\n,혼잣말\n", encoding="utf-8")
    d = script.read(f)
    assert [(ln["speaker"], ln["text"]) for ln in d["lines"]] == [("빌", "안녕"), (None, "혼잣말")]


def test_script_version_2_keeps_what_the_dubbing_script_says(tmp_path):
    f = tmp_path / "s.txt"
    f.write_text("\n".join(DUB_SCRIPT), encoding="utf-8")
    d = script.read(f)
    assert d["version"] == 2 and d["format"] == "lines" and not d["timed"]
    assert d["source"] == {"path": str(f), "sha1": script.file_signature(f)["sha1"], "size": f.stat().st_size,
                           "mtime": f.stat().st_mtime} and len(d["source"]["sha1"]) == 40
    assert [(s["id"], s["number"], s["heading"], s["place"]) for s in d["scenes"]] == [
        ("SC001", "1", "S#1 [교실]", "교실"), ("SC002", "2", "S#2 [복도]", "복도")]
    lines = d["lines"]
    assert [ln["id"] for ln in lines] == [f"L000{k}" for k in range(1, 7)] and {ln["scene_id"] for ln in lines} == {"SC001"}
    assert (lines[0]["speaker"], lines[0]["source"]) == ("길동", "narration")           # (NA)길동
    assert lines[0]["raw"] == "학교에 온 지 한 달. 이곳은 생각보다 / 편한 곳이 되었다."
    assert (lines[2]["text"], lines[2]["raw"], lines[2]["directions"]) == ("네!", "(호) 네!", [{"text": "호", "type": "action"}])
    assert (lines[5]["speaker"], lines[5]["source"], lines[5]["raw"]) == ("길동", "offscreen", "시너(thinner) 냄새… 1/2만")
    assert all(ln["language"] is None and ln["sung"] is False and ln["delivery"] == [] for ln in lines)
    assert d["people"] == [{"id": "P01", "name": "길동", "cues": ["(NA)길동", "춘향/길동", "길동(E)"]},
                           {"id": "P02", "name": "춘향", "cues": ["춘향", "춘향/길동"]},
                           {"id": "P03", "name": "친구1", "cues": ["친구1"]}]
    assert [e["type"] for e in d["elements"]] == ["other"] * 4 + [
        "heading", "dialogue", "dialogue", "action", "dialogue", "transition", "dialogue", "dialogue", "dialogue", "heading"]
    silent = d["elements"][7]                             # 길동: (물 마시고 뱉는 호) — no words, so no line
    assert silent["speaker"] == "길동" and silent["text"] == "(물 마시고 뱉는 호)" and "line_id" not in silent
    assert [d["elements"][k]["line_id"] for k in (5, 6, 8)] == ["L0001", "L0002", "L0003"]
    assert script.parse_lines(DUB_SCRIPT) == [{"speaker": ln["speaker"], "text": ln["text"]} for ln in lines]


def test_script_reading_fixes(tmp_path):
    f = tmp_path / "s.txt"
    f.write_text("\n".join([
        "어느 겨울", "배역: 가 나", "#1", "# 주석",
        "미나: (웃으며) 그래. (나간다)",
        "(웃으며) 그래요. (나간다)",                      # directions at both ends, words between
        "(문 열리는 소리)",
        "지금 시간은 3:30 이야",                          # a time, not a speaker called '지금 시간은 3'
        "준호: 3시에 보자", "준호:30분 뒤에",
        "남자2: 왜요?", "남자2:30분만", "남자3:30분만",
        "(소리): 거기 누구요?", "(미나): 나야.",
        "http://example.com/x",
        "#2 [복도]", "미나: 가자."]), encoding="utf-8")
    d = script.read(f)
    got = [(ln["speaker"], ln["text"]) for ln in d["lines"]]
    assert got[:10] == [
        ("미나", "그래."), (None, "그래요."), (None, "지금 시간은 3:30 이야"), ("준호", "3시에 보자"), ("준호", "30분 뒤에"),
        ("남자2", "왜요?"), ("남자2", "30분만"), (None, "남자3:30분만"), (None, "거기 누구요?"), ("미나", "나야.")]
    assert got[-1] == ("미나", "가자.") and len(got) == 12
    assert d["characters"] == ["미나", "준호", "남자2"]              # no '어느 겨울', '배역', '지금 시간은 3', 'http'
    assert [ln["source"] for ln in d["lines"][8:10]] == ["offscreen", "offscreen"]
    assert d["lines"][1]["directions"] == [{"text": "웃으며", "type": "vocal"}, {"text": "나간다", "type": "action"}]
    assert [(s["number"], s["place"]) for s in d["scenes"]] == [("1", None), ("2", "복도")]      # '#1' is a scene heading
    assert [ln["scene_id"] for ln in d["lines"]] == ["SC001"] * 11 + ["SC002"]
    assert [(e["type"], e["text"]) for e in d["elements"][:4]] == [
        ("other", "어느 겨울"), ("other", "배역: 가 나"), ("heading", "#1"), ("dialogue", "(웃으며) 그래. (나간다)")]
    assert ("action", "(문 열리는 소리)") in [(e["type"], e["text"]) for e in d["elements"]]


def test_script_text_encodings(tmp_path):
    text = "미나: 안녕하세요\n준호: 네 안녕하세요\n"
    want = [("미나", "안녕하세요"), ("준호", "네 안녕하세요")]
    for name, data in (("utf8.txt", text.encode("utf-8")), ("bom.txt", text.encode("utf-8-sig")),
                       ("utf16.txt", text.encode("utf-16")), ("utf16le.txt", text.encode("utf-16-le")),
                       ("cp949.txt", text.encode("cp949"))):
        f = tmp_path / name
        f.write_bytes(data)
        assert [(ln["speaker"], ln["text"]) for ln in script.read(f)["lines"]] == want, name
    f = tmp_path / "cp949.csv"
    f.write_bytes("speaker,text\n미나,안녕\n".encode("cp949"))
    assert [(ln["speaker"], ln["text"]) for ln in script.read(f)["lines"]] == [("미나", "안녕")]
    f = tmp_path / "broken.txt"
    f.write_bytes(b"\x80\x81\xfe\xff\xfa\xfb not text \xff")
    with pytest.raises(ValueError, match="UTF-8"):
        script.read(f)


def _srt(rows):
    return "\n".join(f"{k}\n00:00:{k:02d},000 --> 00:00:{k:02d},800\n{text}\n" for k, text in enumerate(rows, 1))


def test_script_srt_bracket_speakers_languages_and_songs(tmp_path):
    f = tmp_path / "s.srt"
    f.write_text(_srt(["[미나] 안녕하세요", "[준호/영어] 반가워요", "[천둥]", "- [문이 열린다]\n- [미나] 누구세요?",
                       "[영어] 들어가도 될까요?", "♪ 달이 뜨면 가리라 ♪", "[미나/속마음] 이상한 사람이야", "(준호) 저예요",
                       "[한숨] 알았어요", "♬ 잔잔한 음악", "[미나, 준호] 네!", "[준호/힘겹게] ♪ 가리라 ♪", "[달칵 - 문] 왔다"]),
                 encoding="utf-8")
    d = script.read(f)
    assert d["timed"] and d["format"] == "timed" and d["version"] == 2 and d["elements"] == []
    assert [(ln["speaker"], ln["text"], ln["start"]) for ln in d["lines"]] == [
        ("미나", "안녕하세요", 1.0), ("준호", "반가워요", 2.0), ("미나", "누구세요?", 4.0), (None, "들어가도 될까요?", 5.0),
        (None, "달이 뜨면 가리라", 6.0), ("미나", "이상한 사람이야", 7.0), ("준호", "저예요", 8.0), (None, "알았어요", 9.0),
        ("미나/준호", "네!", 11.0), ("준호", "가리라", 12.0), (None, "왔다", 13.0)]
    by_start = {ln["start"]: ln for ln in d["lines"]}
    assert by_start[2.0]["language"] == by_start[5.0]["language"] == "영어" and by_start[1.0]["language"] is None
    assert by_start[6.0]["sung"] and by_start[12.0]["sung"] and by_start[12.0]["delivery"] == ["힘겹게"]
    assert by_start[7.0]["source"] == "inner" and by_start[9.0]["vocal"] == ["한숨"] and not by_start[1.0]["sung"]
    assert d["characters"] == ["미나", "준호"]


def test_script_tables_and_json_as_version_2(tmp_path):
    f = tmp_path / "s.tsv"
    f.write_text("화자\t대사\n길동(E)\t(작게) 안녕\n\t혼잣말\n", encoding="utf-8")
    d = script.read(f)
    assert d["version"] == 2 and d["format"] == "lines" and d["elements"] == [] and d["scenes"] == []
    one, two = d["lines"]
    assert (one["speaker"], one["source"], one["text"]) == ("길동", "offscreen", "(작게) 안녕")      # the text is kept whole
    assert one["directions"] == [{"text": "작게", "type": "delivery"}] and two["speaker"] is None
    j = tmp_path / "s.json"
    j.write_text('[{"speaker": "미나", "text": "안녕", "start": 1, "end": 2.5}]', encoding="utf-8")
    d = script.read(j)
    assert d["timed"] and d["format"] == "timed" and (d["lines"][0]["start"], d["lines"][0]["end"]) == (1.0, 2.5)
    # a script.json that read() wrote reads back with what it knew
    t = tmp_path / "s.txt"
    t.write_text("S#1 [교실]\n미나(E): (영) 안녕. (노래) 라라라\n", encoding="utf-8")
    first = script.read(t)
    j.write_text(json.dumps(first, ensure_ascii=False), encoding="utf-8")
    back = script.read(j)
    keys = ("id", "speaker", "text", "raw", "language", "sung", "source", "scene_id")
    assert [[ln[k] for k in keys] for ln in back["lines"]] == [[ln[k] for k in keys] for ln in first["lines"]]
    assert [(ln["language"], ln["sung"], ln["source"]) for ln in back["lines"]] == [("영어", False, "offscreen"),
                                                                                  ("영어", True, "offscreen")]
    assert back["scenes"] == first["scenes"] and back["characters"] == ["미나"]


def test_script_docx_of_a_shooting_script(tmp_path):
    f = tmp_path / "s.docx"
    _docx(f, ["포구 / 밤", "배가 들어온다.", "\t나래\t(작게) 왔다.", "\t도윤\t오랜만이야.", "\t\t정말 오랜만이야.",
              "\t(할머니)\t밥은 먹었니?", "선창가 / 낮", "\t나래\t가자."])
    d = script.read(f)
    assert d["format"] == "screenplay" and d["layout"] == "tab" and len(d["scenes"]) == 2
    assert [(ln["speaker"], ln["text"], ln["source"], ln["scene_id"]) for ln in d["lines"]] == [
        ("나래", "왔다.", None, "SC001"), ("도윤", "오랜만이야. 정말 오랜만이야.", None, "SC001"),
        ("할머니", "밥은 먹었니?", "offscreen", "SC001"), ("나래", "가자.", None, "SC002")]


def test_script_summary(tmp_path):
    def summary(name, text, encoding="utf-8"):
        f = tmp_path / name
        f.write_text(text, encoding=encoding)
        return script.summary(script.read(f))

    s = summary("ok.txt", "S#1 [교실]\n미나: (영) 안녕.\n준호(E): (노래) 라라라\n(문 열리는 소리)\n그냥 대사\n")
    assert s["ok"] and s["error"] is None and s["format"] == "lines" and not s["timed"]
    assert (s["scenes"], s["lines"], s["named"], s["actions"], s["characters"]) == (1, 3, 2, 1, 2)
    assert s["named_ratio"] == 0.667 and (s["language"], s["languages"], s["sung"], s["offscreen"]) == (1, {"영어": 1}, 1, 1)
    assert s["sources"] == {"offscreen": 1} and s["warnings"] == ["화자 없는 대사가 1줄 있습니다 (33%)"]

    plain = "\n".join(f"화자 없이 적은 대사 {k}" for k in range(40))
    s = summary("plain.txt", plain)                       # a script pasted as plain text: nothing to name a speaker
    assert not s["ok"] and "화자가 하나도 없습니다" in s["error"] and (s["lines"], s["named"]) == (40, 0)
    s = summary("short.txt", "\n".join(f"화자 없이 적은 대사 {k}" for k in range(12)))
    assert s["ok"] and s["warnings"] == ["화자 없는 대사가 12줄 있습니다 (100%)"]
    s = summary("plain.srt", _srt([f"자막 {k}" for k in range(1, 41)]))      # subtitles rarely name speakers
    assert s["ok"] and s["timed"] and s["lines"] == 40
    s = summary("plain.csv", "speaker,text\n" + "\n".join(f",대사 {k}" for k in range(40)))
    assert s["ok"] and s["named"] == 0

    s = summary("numbered.txt", plain + "\n96. 추격: 골목에서 큰길로\n")
    assert not s["ok"] and "장면 머리" in s["error"] and "96. 추격" in s["error"]
    s = summary("places.txt", plain + "\n" + "\n".join(f"나래의 집 : 방 {k} / 밤" for k in range(4)))
    assert not s["ok"] and "장면 머리" in s["error"] and "나래의 집" in s["error"]
    s = summary("pieces.txt", "\n".join(f"낱말{k}\n, \n. " for k in range(30)))
    assert not s["ok"] and "조각" in s["error"]
    s = summary("empty.txt", "(문 열리는 소리)\n# 주석\n")
    assert not s["ok"] and s["error"] == "대사 줄을 찾지 못했습니다" and s["lines"] == 0

    old = {"source": "x.txt", "timed": False, "characters": ["미나"], "lines": [{"id": "L0001", "speaker": "미나", "text": "안녕"}]}
    s = script.summary(old)                               # a script.json from before version 2
    assert s["ok"] and (s["lines"], s["named"], s["scenes"], s["language"]) == (1, 1, 0, 0)
    assert s["warnings"] == ["예전 형식으로 읽은 대본입니다 (장면·괄호 지시 정보 없음)"]


# ------------------------------------------------------------------ 정렬
def test_vocalisation():
    assert align.is_vocalisation("으아아아아")
    assert align.is_vocalisation("악")
    assert align.is_vocalisation("흐윽 후윽")
    assert not align.is_vocalisation("내 얼굴 나오잖아")
    assert not align.is_vocalisation("")


def test_assign_shares_long_region_and_prefers_vocal():
    regions = [(10.0, 10.4, 0.5, "speech"), (20.0, 26.0, 0.2, "vocal"), (30.0, 31.0, 0.5, "speech")]
    # two screams share the long vocal region, the spoken line takes the speech region after it
    pick = align._assign([5, 5, 2], [True, True, False], regions)
    assert pick == [1, 1, 2]


def test_assign_none_when_no_region():
    assert align._assign([3], [False], []) is None


class _P:
    """Minimal project stand-in for align_lines without SED files."""
    def __init__(self, tmp):
        self.work = tmp

    def path(self, name):
        return self.work / name


def test_align_lines_anchors_and_interpolation(tmp_path):
    lines = [{"id": "L1", "speaker": "A", "text": "안녕하세요 여러분"},
             {"id": "L2", "speaker": "A", "text": "으아아아"},
             {"id": "L3", "speaker": "B", "text": "내 얼굴 나오잖아"}]
    words = [{"w": "안녕하세요", "s": 10.0, "e": 10.6}, {"w": "여러분", "s": 10.6, "e": 11.0},
             {"w": "내", "s": 20.0, "e": 20.2}, {"w": "얼굴", "s": 20.2, "e": 20.5}, {"w": "나오잖아", "s": 20.5, "e": 21.0}]
    out = align_lines = align.align_lines(_P(tmp_path), lines, words, 0.0, 30.0)
    assert out[0]["aligned"] and out[0]["start"] == pytest.approx(10.0, abs=0.01)
    assert out[2]["aligned"] and out[2]["start"] == pytest.approx(20.0, abs=0.01)
    assert not out[1]["aligned"] and 11.0 <= out[1]["start"] < 20.0     # placed in the gap
    assert len(out[0]["words"]) == 2 and out[0]["words"][1]["w"] == "여러분"
    assert align_lines is out


# ------------------------------------------------------------------ 계층
@needs_psed
def test_ontology_horn_path():
    o = ontology.get()
    horn = "Vehicle horn, car horn, honking, toot"
    assert set(o.parents(horn)) == {"Car", "Alarm"}
    assert {"Car", "Motor vehicle (road)", "Vehicle"} <= o.ancestors(horn)
    assert o.related("Car", horn) and not o.related("Thunder", horn)


@needs_psed
def test_resolve_descends_and_climbs():
    o = ontology.get()
    horn = "Vehicle horn, car horn, honking, toot"
    # confident child below a confident parent -> most specific
    best, path, ok = sfx.resolve({"Car": 0.6, horn: 0.5, "Thunder": 0.1}, o)
    assert ok and best == horn and path[:2] == [horn, "Car"]
    # specific class unsure -> climb to the parent that reaches TAU_PARENT ('최소 자동차 소리')
    best, path, ok = sfx.resolve({horn: 0.2, "Car": 0.27, "Alarm": 0.05, "Thunder": 0.01}, o)
    assert ok and best == "Car"
    # nothing confident -> not confident
    best, path, ok = sfx.resolve({horn: 0.1, "Car": 0.05, "Alarm": 0.02}, o)
    assert not ok


def test_refine_onset_finds_rise():
    hop, t0 = 0.01, 0.0
    lev = np.full(500, -60.0, dtype=np.float32)
    lev[200:260] = -10.0                       # sound starts at 2.00 s
    lev[198:200] = -40.0                       # short rise
    t = sfx.refine_onset(lev, hop, t0, 2.2)    # SED guess is late
    assert 1.97 <= t <= 2.0


def test_phrases_long_short():
    ph = phrases.phrases("Thunder")
    assert ph["long"] == "[천둥 소리]" and ph["short"].startswith("[") and ph["ono"]
    assert phrases.mood_phrase("Happy music") == "♬ 경쾌한 음악"          # II.7: 행복한 대신 경쾌한
    assert phrases.mood_phrase("Scary music", "고조되는") == "♬ 고조되는 무서운 음악"


# ------------------------------------------------------------------ 추천 규칙
def _sfx(**kw):
    c = {"id": "S1", "kind": "sfx", "onset": 10.0, "t1": 11.0, "category": "event", "level_jump_db": 20.0,
         "label": {"class": "Thunder", "ko": "천둥", "score": 0.5, "confident": True, "unknown": False,
                   "competing": [], "path_ko": ["천둥"]}}
    c.update(kw)
    return c


def test_visible_source_is_excluded():
    rec, reason, _ = recommend.decide_sfx(_sfx(), {"visible": "보임", "observation": "걷는 모습이 보임"}, None, None)
    assert rec == "exclude" and "걷는 모습" in reason


def test_already_in_dialogue_is_excluded():
    rec, reason, _ = recommend.decide_sfx(_sfx(), {"visible": "안 보임"}, {"relation": "already"}, None)
    assert rec == "exclude" and "대사" in reason


def test_previous_caption_excludes_repeat():
    rec, reason, _ = recommend.decide_sfx(_sfx(), {"visible": "안 보임"}, None, {"t": 5.0})
    assert rec == "exclude" and "앞 자막" in reason


def test_offscreen_with_reaction_is_included():
    rec, reason, _ = recommend.decide_sfx(_sfx(level_jump_db=0.0), {"visible": "안 보임", "reaction": ""},
                                          {"relation": "reaction", "evidence": "직후 비명"}, None)
    assert rec == "include" and "직후 비명" in reason


def test_unknown_sound_goes_to_agent():
    lab = {"class": "Generic impact sounds", "ko": "부딪히는", "score": 0.17, "confident": False, "unknown": True,
           "competing": []}
    rec, reason, why = recommend.decide_sfx(_sfx(label=lab), {"visible": "안 보임"}, None, None)
    assert rec == "check" and "목록에 없는 소리" in why


def test_agent_evidence_can_settle():
    lab = {"class": "Generic impact sounds", "ko": "부딪히는", "score": 0.17, "confident": False, "unknown": True,
           "competing": []}
    agent = {"label_ok": True, "visible": "안 보임", "plot": "필요", "plot_note": "직후 비명"}
    rec, reason, _ = recommend.decide_sfx(_sfx(label=lab, level_jump_db=0.0), {"visible": "애매"}, None, None, agent)
    assert rec == "include"


def test_music_and_speaker_rules():
    m = {"music": {"moods": [{"key": "Scary music", "ko": "무서운", "score": 0.8}], "event": "start"},
         "label": {"confident": True}}
    assert recommend.decide_music(m, None)[0] == "include"
    assert recommend.decide_music(m, {"t": 1.0})[0] == "exclude"
    s = {"speaker": {"face": "visible", "name": "빌"}}
    assert recommend.decide_speaker(s, None)[0] == "exclude"
    s = {"speaker": {"face": "not_visible", "name": "빌"}}
    assert recommend.decide_speaker(s, None)[0] == "include"
    assert recommend.decide_speaker(s, {"rec": "include"})[0] == "exclude"   # same speaker continues
    s = {"speaker": {"face": "not_visible", "name": None}}
    assert recommend.decide_speaker(s, None)[0] == "check"
