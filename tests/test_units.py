# -*- coding: utf-8 -*-
"""Unit tests for the analysis stages that need no GPU and no LLM."""
from __future__ import annotations

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
