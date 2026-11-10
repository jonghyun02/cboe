# -*- coding: utf-8 -*-
"""Seams left after the first round: a fourth kind of shooting script (speaker cells set off
by a gap, a title page before the first scene), what a direction says about the voice, and
the small joints between the script reader, the GUI, the command line and the tools.

Every script text here is made up for the test; no line of a real script is used.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from cboe import screenplay as sp
from cboe import script

ROOT = Path(__file__).resolve().parents[1]

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


def test_a_sound_of_the_voice_or_the_manner_of_the_words():
    kind = sp.direction_type
    for sound in ("한숨", "통곡", "폭소", "비명", "키득", "피식", "울음을 터뜨린다", "한숨을 내쉬더니 돌아선다", "큰 웃음소리",
                  "비명 소리", "운다", "사레 들려 켁켁"):
        assert kind(sound) == ("vocal", None), sound
    for manner in ("웃으며", "웃다가 심각", "깔깔 웃으며", "한숨 쉬며", "흐느끼며", "울면서", "웃는 얼굴로",
                   "웃음 섞인 목소리로", "웃음 참으며", "비웃으며"):
        assert kind(manner) == ("delivery", None), manner
    for silent in ("쓴웃음", "씨익 웃는다", "미소", "울린다", "울컥"):        # no sound of the voice
        assert kind(silent)[0] not in ("vocal", "delivery"), silent
    # a sound that others make is a sound, but whose it is goes with it
    assert kind("학생들 폭소") == ("vocal", "학생들") and kind("키득거리는 아이들") == ("vocal", "아이들")
    assert kind("다들 웃는다") == ("vocal", "다들") and kind("일동 웃음") == ("vocal", "일동")
    assert kind("한경의 웃음") == ("vocal", "한경") and kind("아기가 울음을 터뜨린다") == ("vocal", "아기")
    assert kind("같이 웃는다") == ("vocal", None)                            # an adverb is nobody


def test_the_sound_of_others_stays_in_the_directions_only():
    rows = ["1. 교실 - 낮",
            "선생      (학생들 폭소) 조용히! (한숨) 다시 하자.",
            "나래      (웃으며) 네. (키득)",
            "학생들    (폭소) 네에!",
            "선생      (한경의 웃음) 한경이 웃었니?",
            "나래      (학생들 키득) ……"]
    doc = sp.build(sp.classify_plain(rows))
    a, b, c, d = doc["lines"]
    assert a["vocal"] == ["한숨"] and a["directions"] == [
        {"text": "학생들 폭소", "type": "vocal", "by": "학생들"}, {"text": "한숨", "type": "vocal"}]
    assert (b["delivery"], b["vocal"]) == (["웃으며"], ["키득"])
    assert c["vocal"] == ["폭소"] and "by" not in c["directions"][0]           # the students' own line
    assert d["vocal"] == [] and d["directions"][0]["by"] == "한경"
    silent = doc["elements"][-1]                                             # no words: kept as an action
    assert silent["speaker"] == "나래" and silent["directions"] == [{"text": "학생들 키득", "type": "vocal", "by": "학생들"}]
    # a group named as the speaker owns the sound in its own cell
    assert sp.annotate("(학생들 폭소) 네에!", sp.parse_cue("학생들"))[0]["vocal"] == ["학생들 폭소"]


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


# ------------------------------------------------------------ the GUI file dialog
def test_the_script_dialog_offers_every_kind_the_reader_reads():
    from cboe import gui
    text = gui.script_filter()
    assert "*.pdf" in text and "*.hwp" in text
    assert all(f"*{ext}" in text for ext in script.SUFFIXES)


# ------------------------------------------------------------ tools/inspect_stages.py
def _inspect():
    spec = importlib.util.spec_from_file_location("inspect_stages", ROOT / "tools" / "inspect_stages.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_inspect_reads_the_new_formats(tmp_path, capsys):
    ins = _inspect()
    assert ins.STAGES[:2] == ["ingest", "script"] and "script.json" not in ins.INFO["ingest"][2]
    assert set(ins.SHOW) == set(ins.STAGES)
    w = tmp_path / "work"
    w.mkdir()
    src = tmp_path / "s.txt"
    src.write_text("\n".join(["1. 포구 - 밤", "나래      (웃으며) 왔다.", "도윤      (학생들 폭소) 오랜만이야.",
                              "나래      응.", "도윤(V.O)    거기 누구야?", "도윤      가자."]), encoding="utf-8")
    doc = script.read(src)
    _write(w / "project.json", {"title": "시험", "script": str(src), "t0": 0.0, "t1": 60.0, "fps": 24.0})
    _write(w / "script.json", doc)
    lines = [{**ln, "words": [], "match": 1.0, "timing": "asr", "status": "ok", "start": 1.0 + 3 * k, "end": 2.0 + 3 * k}
             for k, ln in enumerate(doc["lines"])]
    lines[1].update(status="unheard", timing="voice")
    lines[2].update(status="absent", start=None, end=None, timing=None, words=None)     # as align writes them
    lines[4].update(status="out_of_range", start=None, end=None, timing=None, words=None)
    _write(w / "dialogue.json", {"mode": "test", "lines": lines,
                                 "extras": [{"id": "X0001", "start": 5.0, "end": 6.0, "text": "어", "words": []}]})
    _write(w / "music.json", {"segments": [[10.0, 20.0]], "items": [
        {"kind": "start", "onset": 10.0, "segment": [10.0, 20.0], "moods": [{"ko": "경쾌한", "score": 0.5}],
         "instruments": [], "mood_strength": 0.4, "music_score": 0.8, "change": None},
        {"kind": "stop", "onset": 20.0, "segment": [10.0, 20.0], "moods": [], "mood_strength": 0.0, "instruments": [],
         "slope_db_s": 0.0, "change": None, "drop_db": 18.0, "music_score": 0.5}]})
    _write(w / "speaker.json", {"lines": [
        {"line_id": "L0001", "face": "visible", "n_faces": 1, "best": None, "tracks": [], "window": [1.0, 2.0]},
        {"line_id": "L0004", "face": "not_visible", "n_faces": 0, "best": None, "tracks": [], "window": [10.0, 11.0],
         "by": "script", "source": "offscreen"}]})
    c = ins.Ctx(w)
    for name in ins.STAGES:
        ins.SHOW[name](c, None, None, True)                      # every stage, whole: nothing stops it
    for name in ins.STAGES:
        if name not in ins.NO_TIME:
            ins.SHOW[name](c, 0.0, 30.0, False)                  # and around a time
    out = capsys.readouterr().out
    assert "version 2 · screenplay (gap)" in out and "장면 1 · 대사 줄 5" in out
    assert "말투 웃으며" in out and "학생들의 소리 학생들 폭소" in out
    assert "줄 상태: 맞음 2, 못 맞춤 1, 장면이 영상에 없음 1, 처리 구간 밖 1" in out and "대본에 없는 들린 말 1" in out
    assert "음악이 멈춤 · 소리 18.0dB 떨어짐" in out and "대본에 적힌 출처 offscreen" in out
    assert "시각 없어 건너뛴 대사 줄 2" in out


# ------------------------------------------------------------ the command line
def test_run_passes_the_llm_scope_and_language_detection(monkeypatch, tmp_path, capsys):
    from cboe import pipeline
    from cboe.__main__ import main
    seen = []
    monkeypatch.setattr(pipeline, "run", lambda work, stages, force, cfg: seen.append(cfg) or {})
    assert main(["run", str(tmp_path)]) == 0
    assert (seen[-1]["llm_scope"], seen[-1]["asr_detect_language"]) == ("all", False)      # the stages' defaults
    assert main(["run", str(tmp_path), "--llm-scope", "useful", "--asr-detect-language", "--no-llm"]) == 0
    assert (seen[-1]["llm_scope"], seen[-1]["asr_detect_language"], seen[-1]["llm"]) == ("useful", True, False)
    with pytest.raises(SystemExit):
        main(["run", str(tmp_path), "--llm-scope", "some"])
    capsys.readouterr()


# ------------------------------------------------------------ phrases of classes without a description
class _Onto:
    PARENTS = {"Wind noise (microphone)": ["Microphone", "Wind"], "Microphone": ["Sound equipment"],
               "Speech": ["Human voice"], "Human voice": ["Human sounds"]}

    def parents(self, c):
        return self.PARENTS.get(c, [])


def test_a_class_without_a_description_is_never_named_in_english(monkeypatch):
    import re

    from cboe import ontology, phrases

    def missing():
        raise OSError("no hierarchy on this PC")

    monkeypatch.setattr(ontology, "get", missing)
    phrases.described_parent.cache_clear()
    plain = {"long": "[소리]", "short": "[소리]", "ono": None, "desc": "[소리]"}
    assert phrases.phrases("Speech") == plain and phrases.phrases("Not a class at all") == plain
    assert phrases.ko("Speech") == "소리" and phrases.ko("Not a class at all") == "소리"
    english = re.compile(r"[A-Za-z]{3}")                      # 'TV 소리' is Korean; a class name is not
    for cls, row in phrases.table().items():
        said = [phrases.ko(cls)] + [v for v in phrases.phrases(cls).values() if v]
        assert not any(cls in v for v in said), cls
        if not row["desc"]:
            assert not any(english.search(v) for v in said), cls
    # with the hierarchy: the nearest described parent speaks for it
    monkeypatch.setattr(ontology, "get", lambda: _Onto())
    phrases.described_parent.cache_clear()
    try:
        assert phrases.described_parent("Wind noise (microphone)") == "Wind"
        assert phrases.phrases("Wind noise (microphone)") == phrases.phrases("Wind")
        assert phrases.ko("Wind noise (microphone)") == phrases.ko("Wind")
        assert phrases.phrases("Speech") == plain              # no ancestor is described either
        assert phrases.ko("Thunder") == "천둥"                  # a described class is as before
    finally:
        phrases.described_parent.cache_clear()


# ------------------------------------------------------------ a subtitle read back as a script
def test_a_speaker_label_alone_on_its_row_names_the_words_below(tmp_path):
    f = tmp_path / "s.srt"
    blocks = ["[김철수]\n안녕하세요반갑습니다만나서정말로", "[박영희/속마음/영어]\n오늘은 정말로 날씨가 좋네요",
              "[쾅]\n뭐야 무슨 소리야", "[한숨]\n알았어 알았다고", "[음악]\n여기서 뭐 해", "[영어]\n이쪽으로 오세요 빨리요",
              "- [천둥]\n- [미나] 누구세요?", "[김철수]", "[김철수]\n[달칵 - 문]", "[미나]\n- 거기 누구야"]
    f.write_text("\n".join(f"{k}\n00:00:{k:02d},000 --> 00:00:{k:02d},800\n{b}\n" for k, b in enumerate(blocks, 1)),
                 encoding="utf-8")
    d = script.read(f)
    got = [(ln["speaker"], ln["text"]) for ln in d["lines"]]
    assert got == [("김철수", "안녕하세요반갑습니다만나서정말로"), ("박영희", "오늘은 정말로 날씨가 좋네요"),
                   (None, "뭐야 무슨 소리야"), (None, "알았어 알았다고"), (None, "여기서 뭐 해"),
                   (None, "이쪽으로 오세요 빨리요"), ("미나", "누구세요?"), (None, "거기 누구야")]
    second = d["lines"][1]
    assert (second["source"], second["language"]) == ("inner", "영어")
    assert d["lines"][3]["vocal"] == ["한숨"] and d["lines"][5]["language"] == "영어"
    assert d["characters"] == ["김철수", "박영희", "미나"]


def test_an_exported_subtitle_keeps_its_speakers_when_read_back(tmp_path):
    from cboe import export, placement
    said = [("김철수", [], None, "안녕하세요반갑습니다만나서정말로"), ("김철수", ["속마음"], "영어", "오늘은 정말로 날씨가 좋네요"),
            ("박영희", [], None, "응"), ("나래", [], None, "괜찮아 정말 괜찮다니까 걱정하지 마"),
            ("할아버지", ["속마음"], None, "이제와서뭘어쩌자는거냐너는"), ("도윤", [], "독일어", "이쪽으로 오세요 빨리요 시간이 없어요")]
    lines = [{"id": f"L{k:04d}", "speaker": name, "show_name": True, "name": name, "label_extra": extra, "language": lang,
              "text": text, "start": 10.0 * k + 1.0, "end": 10.0 * k + 4.0, "timing": "asr", "status": "ok", "words": []}
             for k, (name, extra, lang, text) in enumerate(said, 1)]
    draft = placement.place(lines, [], [], 24.0, window=(0.0, None))
    alone = [c["lines"] for c in draft["cells"] if c["lines"][0].startswith("[") and c["lines"][0].endswith("]")]
    assert alone, "the placement breaks a line right after a long label (the case this reading is for)"
    out = tmp_path / "out.srt"
    export.write(draft["cells"], out, "srt", fps=24.0)
    back = script.read(out)
    assert [ln["speaker"] for ln in back["lines"]] == [name for name, *_ in said]
    assert [ln["text"].replace(" ", "") for ln in back["lines"]] == [text.replace(" ", "") for *_, text in said]
    assert [(ln["language"], ln["source"]) for ln in back["lines"]] == [
        (None, None), ("영어", "inner"), (None, None), (None, None), (None, "inner"), ("독일어", None)]


def test_a_two_speaker_cell_is_read_back_as_two_lines(tmp_path):
    from cboe import export, placement

    def lines_of(said):
        return [{"id": f"L{k:04d}", "speaker": n, "show_name": True, "name": n, "label_extra": [], "language": lang,
                 "text": t, "start": s, "end": e, "timing": "asr", "status": "ok", "words": []}
                for k, (n, lang, t, s, e) in enumerate(said, 1)]

    said = [("미나", None, "누구세요?", 1.0, 2.0), ("준호", "영어", "나야", 1.4, 2.4), ("미나", None, "들어와", 6.0, 7.5)]
    draft = placement.place(lines_of(said), [], [], 24.0, window=(0.0, None))
    dual = draft["cells"][0]["lines"]
    assert len(dual) == 2 and all(r.startswith("- [") for r in dual), "the placement sets the two lines in one cell"
    out = tmp_path / "dual.srt"
    export.write(draft["cells"], out, "srt", fps=24.0)
    back = script.read(out)
    got = [(ln["speaker"], ln["text"], ln["language"], ln["delivery"]) for ln in back["lines"]]
    assert got == [("미나", "누구세요?", None, []), ("준호", "나야", "영어", []), ("미나", "들어와", None, [])]
    first, second = back["lines"][:2]
    assert (first["start"], first["end"]) == (second["start"], second["end"]) == (draft["cells"][0]["start"], draft["cells"][0]["end"])
    assert back["characters"] == ["미나", "준호"]
    # placed again, the two lines make the same cell
    again = placement.place(lines_of([(ln["speaker"], ln["language"], ln["text"], ln["start"], ln["end"])
                                      for ln in back["lines"]]), [], [], 24.0, window=(0.0, None))
    assert again["cells"][0]["lines"] == dual
    # written by hand: the label of one row is not the other's, one hyphen also starts a speaker
    f = tmp_path / "hand.srt"
    f.write_text("1\n00:00:01,000 --> 00:00:03,000\n- [영희/영어] 좋아.\n- [철수] 가자.\n\n"
                 "2\n00:00:04,000 --> 00:00:06,000\n[영희] 어디 가?\n- 집에.\n\n"
                 "3\n00:00:07,000 --> 00:00:09,000\n-[철수] 비가 오는데\n우산은 있어?\n- [영희] 응.\n", encoding="utf-8")
    got = [(ln["speaker"], ln["text"], ln["language"], ln["start"]) for ln in script.read(f)["lines"]]
    assert got == [("영희", "좋아.", "영어", 1.0), ("철수", "가자.", None, 1.0), ("영희", "어디 가?", None, 4.0),
                   (None, "집에.", None, 4.0), ("철수", "비가 오는데 우산은 있어?", None, 7.0), ("영희", "응.", None, 7.0)]


def test_a_sound_alone_on_its_row_names_nobody(tmp_path):
    # another writer's subtitle: a sound on the row above the words, without hyphens
    sounds = ["전화벨", "정적", "침묵", "문 열림", "음악 계속", "빵빵", "띵동", "멀리서", "통화 중"]
    blocks = [f"[{s}]\n잠깐만 기다려 {k}" for k, s in enumerate(sounds)]
    blocks += ["[보람] 저 왔어요", "[보람]\n잠깐만 기다려 줘", "[나래]\n오늘따라바람이정말많이부는구나"]
    f = tmp_path / "s.srt"
    f.write_text("\n".join(f"{k}\n00:00:{k:02d},000 --> 00:00:{k:02d},800\n{b}\n" for k, b in enumerate(blocks, 1)),
                 encoding="utf-8")
    d = script.read(f)
    assert [ln["speaker"] for ln in d["lines"]] == [None] * len(sounds) + ["보람", "보람", "나래"]
    assert d["characters"] == ["보람", "나래"]       # a name labelling words elsewhere, a label too long for one row
