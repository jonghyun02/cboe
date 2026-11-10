# -*- coding: utf-8 -*-
"""대본과 받아쓰기 대조의 힌트 (cboe.stages.align): a name the ASR misheard (감사 1-01, 8-m2), the
language the chunks were heard in (감사 5-02, 8-06) and speech sung where the stored SED scores say
somebody sings (감사 5-03, 5-m1).

Made-up lines and names only; the ASR words are made up with their times (0.13 s per letter). No model."""
from __future__ import annotations

import numpy as np

from cboe import check, screenplay
from cboe.stages import align, asr
from cboe.state import Project
from cboe.util import read_json, write_json

NAMES = ["가람", "나래", "남자", "오 반장"]


# ------------------------------------------------------------------ 이름만 다른 줄 (compare)
def test_a_name_heard_otherwise_is_a_name_hint_not_a_different_word():
    diff, hints = align.compare("가람아 오늘 바다로 가자", ["카라마", "오늘", "바다로", "가자"], NAMES)
    assert [(d["kind"], d.get("name"), d["script"], d["heard"], d["at"]) for d in diff] == \
        [("word", True, "가람아", "카라마", [0, 1])]
    assert hints == ["name"]
    # a name of two words, said with an honorific, heard as one word
    diff, hints = align.compare("오 반장님 여기요", ["도만창님", "여기요"], NAMES)
    assert diff[0]["name"] and diff[0]["at"] == [0, 2] and hints == ["name"]


def test_other_words_that_differ_keep_the_line_different():
    diff, hints = align.compare("가람아 오늘은 바다로 가자", ["카라마", "오늘은", "산골짜기로", "가자"], NAMES)
    assert [d.get("name", False) for d in diff if d["kind"] == "word"] == [True, False]
    assert "name" not in hints


def test_a_name_is_not_taken_for_misheard_when_it_may_not_be_so():
    # not heard at all: maybe not said
    assert align.compare("가람아, 오늘 바다로 가자", ["오늘", "바다로", "가자"], NAMES)[1] == []
    # another character's name heard there: maybe said so
    diff, hints = align.compare("가람아 오늘 바다로 가자", ["나래야", "오늘", "바다로", "가자"], NAMES)
    assert diff[0]["kind"] == "word" and "name" not in diff[0] and hints == []
    # the name heard right, the particle otherwise: a word said otherwise
    diff, hints = align.compare("가람이가 왔어", ["가람한테", "왔어"], NAMES)
    assert diff[0]["kind"] == "word" and "name" not in diff[0] and hints == []
    # much more heard than the name has letters
    assert align.compare("가람아 오늘 가자", ["그러니까말이야", "오늘", "가자"], NAMES)[1] == []
    # a role name is a common word, heard like any other
    diff, hints = align.compare("남자가 왔어", ["그사람이", "왔어"], NAMES)
    assert diff[0]["kind"] == "word" and "name" not in diff[0] and hints == []
    # without names nothing changes
    diff, hints = align.compare("가람아 오늘 바다로 가자", ["카라마", "오늘", "바다로", "가자"])
    assert diff[0]["kind"] == "word" and "name" not in diff[0] and hints == []


# ------------------------------------------------------------------ the align stage
def _words(said: list[tuple[str, float]]) -> list[dict]:
    """[(text, start)] -> ASR words at 0.13 s per letter, 0.03 s apart."""
    out = []
    for text, t in said:
        for w in text.split():
            d = align.SEC_PER_CHAR * max(len(align.norm_chars(w)), 1)
            out.append({"w": w, "s": round(t, 3), "e": round(t + d, 3)})
            t += d + 0.03
    return out


def _line(i, text, speaker="가람", **kw):
    ln = {"id": f"L{i:04d}", "speaker": speaker, "text": text, "raw": text, "scene_id": None, "language": None,
          "sung": False, "source": None, "delivery": [], "vocal": [], "directions": []}
    ln.update(kw)
    return ln


def _run(tmp_path, lines, words, t_end=60.0, chunks=None, vad=None):
    write_json(tmp_path / "project.json", {"t0": 0.0, "t1": t_end, "duration": t_end, "fps": 25.0})
    write_json(tmp_path / "script.json", {"version": 2, "format": "lines", "timed": False, "characters": ["가람", "나래"],
                                          "scenes": [], "elements": [], "lines": lines})
    got = {"words": words, "vad": vad if vad is not None else [[w["s"], w["e"]] for w in words]}
    if chunks is not None:
        got["chunks"] = chunks
    write_json(tmp_path / "asr.json", got)
    (tmp_path / "dialogue.json").unlink(missing_ok=True)
    res = align.run(Project(tmp_path), {})
    return read_json(tmp_path / "dialogue.json"), res


SCRIPT = [_line(1, "가람아 오늘 저녁에 바다로 가자", "나래"), _line(2, "그래 좋아 바다에서 별을 보자"),
          _line(3, "나래야 그럼 일곱 시에 만나자"), _line(4, "그때 꼭 와야 한다 알았지", "나래")]


def test_a_line_that_differs_only_in_a_name_is_ok_with_the_name_hint(tmp_path):
    said = [("카라마 오늘 저녁에 바다로 가자", 1.0), ("그래 좋아 바다에서 별을 보자", 6.0),
            ("나래야 그럼 일곱 시에 만나자", 11.0), ("그때 꼭 와야 한다 고마워", 16.0)]
    d, _ = _run(tmp_path, [dict(x) for x in SCRIPT], _words(said))
    by = {ln["id"]: ln for ln in d["lines"]}
    one = by["L0001"]
    assert one["status"] == "ok" and one["hints"] == ["name"]
    assert one["text"] == SCRIPT[0]["text"]                       # the script text is kept
    assert one["asr_text"] == said[0][0]                          # what was heard stays
    assert [x["name"] for x in one["diff"]] == [True]
    assert by["L0004"]["status"] == "differs" and "hints" not in by["L0004"]      # another word: as before
    assert by["L0002"]["status"] == by["L0003"]["status"] == "ok" and "hints" not in by["L0002"]


# ------------------------------------------------------------------ 들린 언어
def _chunk(a, b, lang, detected=True, prob=0.9):
    c = {"start": a, "end": b, "language": lang, "detected": detected, "words": 3}
    if prob is not None:
        c["prob"] = prob
    return c


FOREIGN = [_line(1, "가람아 오늘 저녁에 바다로 가자", "나래"), _line(2, "나는 지금 가야 해요", language="영어"),
           _line(3, "그래 좋아 바다에서 별을 보자"), _line(4, "다시 만나서 정말 반가워요", "나래", language="일본어"),
           _line(5, "나래야 그럼 일곱 시에 만나자")]
SAID = [("가람아 오늘 저녁에 바다로 가자", 1.0), ("I have to go now", 6.0), ("그래 좋아 바다에서 별을 보자", 10.0),
        ("nice to see you again", 15.0), ("나래야 그럼 일곱 시에 만나자", 20.0)]


def _foreign_chunks():
    return [_chunk(0.9, 4.9, "ko"), _chunk(5.9, 8.4, "en"), _chunk(9.9, 13.9, "ko"), _chunk(14.9, 18.0, "en"),
            _chunk(19.9, 24.0, "ko")]


def test_a_line_heard_in_another_language_than_the_script_marks_gets_the_language_hint(tmp_path):
    d, _ = _run(tmp_path, [dict(x) for x in FOREIGN], _words(SAID), chunks=_foreign_chunks())
    by = {ln["id"]: ln for ln in d["lines"]}
    assert "hints" not in by["L0002"] and "heard_language" not in by["L0002"]    # 영어, heard en
    four = by["L0004"]                                                           # 일본어, heard en
    assert four["hints"] == ["language"] and four["heard_language"] == "영어"
    assert four["status"] == "ok" and four["text"] == FOREIGN[3]["text"] and four["language"] == "일본어"
    for i in ("L0001", "L0003", "L0005"):                                        # Korean, heard ko
        assert "hints" not in by[i] and "heard_language" not in by[i] and by[i]["status"] == "ok"


def test_a_line_without_a_mark_heard_in_another_language_gets_the_language_hint(tmp_path):
    lines = [dict(x) for x in FOREIGN]
    lines[1]["language"] = None                                   # the script forgot the mark
    d, _ = _run(tmp_path, lines, _words(SAID), chunks=_foreign_chunks())
    two = next(ln for ln in d["lines"] if ln["id"] == "L0002")
    assert two["hints"] == ["language"] and two["heard_language"] == "영어"
    assert two["text"] == FOREIGN[1]["text"] and two["status"] == "unheard"      # the status stays


def test_chunk_languages_not_told_from_the_audio_tell_nothing(tmp_path):
    words = _words(SAID)
    base, _ = _run(tmp_path, [dict(x) for x in FOREIGN], words)                  # no chunks
    for chunks in ([_chunk(c["start"], c["end"], "ko", detected=False, prob=None) for c in _foreign_chunks()],
                   [{**c, "prob": 0.3} for c in _foreign_chunks()]):            # off / under LANG_MIN_PROB
        d, _ = _run(tmp_path, [dict(x) for x in FOREIGN], words, chunks=chunks)
        assert d["lines"] == base["lines"]
    assert not any("heard_language" in ln or "language" in ln.get("hints", []) for ln in base["lines"])


def test_language_names_come_from_the_tables_in_use():
    known = set(screenplay.LANG_NAMES) | {v for v in screenplay.LANG_NAMES.values() if v} | set(check._LANGUAGES)
    assert all(v in known for v in asr.LANG_KO.values())
    assert all(v in known for vs in asr.LANG_SAME.values() for v in vs)
    assert asr.language_name("en") == "영어" and asr.language_name("ko") == "한국어"
    assert asr.same_language("tr", "터키어") and asr.same_language("tr", "튀르키예어") and not asr.same_language("en", "독일어")
    assert align.heard_language(["en", "en", None, "ko"]) == "en"
    assert align.heard_language(["en", "ko"]) is None and align.heard_language([None]) is None


# ------------------------------------------------------------------ 노래 구간
SED_NAMES = sorted(set(align.VOICE) | set(align.VOCAL_FX) | {"Music", "Male singing", "Choir"})


def _scores(tmp_path, n, sing=()):
    probs = np.zeros((n, len(SED_NAMES)), np.float16)
    probs[:, SED_NAMES.index("Speech")] = 0.6
    for a, b, v in sing:
        probs[round(a * 25):round(b * 25), SED_NAMES.index("Male singing")] = v
    for track in ("mix", "effects"):
        np.savez(tmp_path / f"sed_{track}.npz", probs=probs, classes=np.array(SED_NAMES), t0=0.0, fps=25.0)
    np.savez(tmp_path / "env.npz", speech=np.full(n, -80.0), effects=np.full(n, -80.0), hop=0.04)


SONG_SCRIPT = [_line(1, "가람아 오늘 저녁에 바다로 가자", "나래"), _line(2, "그래 좋아 바다에서 별을 보자"),
               _line(3, "나래야 그럼 일곱 시에 만나자")]
SONG_SAID = [("가람아 오늘 저녁에 바다로 가자", 1.0), ("하얀 파도 위에 달빛이 흐르네", 8.0),
             ("그래 좋아 바다에서 별을 보자", 16.0), ("저기 푸른 등대 아래로 모여라", 22.0), ("나래야 그럼 일곱 시에 만나자", 28.0)]


def test_speech_in_no_script_line_inside_a_singing_region_is_sung(tmp_path):
    _scores(tmp_path, 40 * 25, sing=[(7.0, 13.0, 0.6), (21.0, 21.6, 0.6)])          # a song, and a blip
    d, res = _run(tmp_path, [dict(x) for x in SONG_SCRIPT], _words(SONG_SAID), t_end=40.0)
    assert d["singing"] == [[7.0, 13.0]] and res["singing"] == 1 and res["sung"] == 1
    got = {x["text"]: x for x in d["extras"]}
    assert set(got) == {SONG_SAID[1][0], SONG_SAID[3][0]}
    assert got[SONG_SAID[1][0]]["sung"] is True
    assert "sung" not in got[SONG_SAID[3][0]]                    # the blip is too short to be a song
    assert all(ln["status"] == "ok" for ln in d["lines"])          # the script lines are not touched


def test_singing_regions_join_over_short_gaps_and_need_a_clear_score(tmp_path):
    _scores(tmp_path, 40 * 25, sing=[(2.0, 4.0, 0.5), (5.0, 7.0, 0.5), (20.0, 26.0, 0.2)])
    assert align.Voice(Project(tmp_path)).singing() == [(2.0, 7.0)]      # the weak one never reaches SING_ON


def test_without_the_score_files_nothing_is_sung(tmp_path):
    d, res = _run(tmp_path, [dict(x) for x in SONG_SCRIPT], _words(SONG_SAID), t_end=40.0)
    assert "singing" not in d and res["singing"] == 0
    assert d["extras"] and not any("sung" in x for x in d["extras"])
