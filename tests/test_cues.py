# -*- coding: utf-8 -*-
"""대본 단서 (cboe.cues, stage ``cues``) and how the recommendation uses them (감사 4-01 · 4-02 ·
8-05 · 8-11 · 8-12). Every script text here is made up; no model and no LLM runs."""
from __future__ import annotations

import pytest

from cboe import cues, pipeline, studio
from cboe.state import Project
from cboe.stages import cues as cue_stage
from cboe.stages import dialog_ev, recommend
from cboe.util import read_json, write_json


# ------------------------------------------------------------------ made-up script
class Doc:
    """A script.json (version 2) written element by element."""

    def __init__(self):
        self.elements, self.lines, self.scenes = [], [], []

    def _el(self, kind, sid, text, **kw):
        e = {"id": f"E{len(self.elements) + 1:04d}", "type": kind, "scene_id": sid, "text": text, **kw}
        self.elements.append(e)
        return e

    def scene(self, n, place="지어낸 골목"):
        sid = f"SC{n:03d}"
        self.scenes.append({"id": sid, "number": str(n), "heading": f"{n}. {place} / 밤", "place": place, "time": "밤"})
        self._el("heading", sid, f"{n}. {place} / 밤")
        return sid

    def act(self, sid, text, **kw):
        return self._el("action", sid, text, **kw)

    def say(self, sid, text, speaker="가온", vocal=()):
        lid = f"L{len(self.lines) + 1:04d}"
        e = self._el("dialogue", sid, text, line_id=lid, cue=speaker)
        self.lines.append({"id": lid, "speaker": speaker, "text": text, "raw": text, "scene_id": sid,
                           "element_id": e["id"], "language": None, "sung": False, "source": None, "delivery": [],
                           "vocal": list(vocal), "directions": [{"text": v, "type": "vocal"} for v in vocal]})
        return lid

    def doc(self):
        return {"version": 2, "format": "screenplay", "timed": False, "characters": ["가온", "나래"], "people": [],
                "scenes": self.scenes, "elements": self.elements, "lines": self.lines}


def _story():
    """SC001: a sound before the first line, one between the lines, one after the last line.
    SC002: a scene without dialogue. SC003: a scene the video does not have."""
    d = Doc()
    s1 = d.scene(1)
    d.act(s1, "멀리서 천둥 소리가 들린다.")
    d.say(s1, "비 오려나 봐.")
    d.act(s1, "쿵! 하고 무언가 떨어진다.")
    d.say(s1, "뭐야, 방금?", speaker="나래", vocal=["한숨"])
    d.act(s1, "거실에 긴 침묵이 흐른다.")
    s2 = d.scene(2, "지어낸 다방")
    d.act(s2, "라디오에서 가수 하늘별의 ‘푸른 바다’가 흘러나온다.")
    s3 = d.scene(3, "지어낸 옥상")
    d.act(s3, "총소리가 크게 울린다.")
    d.say(s3, "엎드려!")
    return d


def _dialogue(scene_ids=True, **times):
    """dialogue.json lines of _story(): L0001 10–12 s, L0002 20–22 s, L0003 not in the video."""
    t = {"L0001": (10.0, 12.0), "L0002": (20.0, 22.0), "L0003": (None, None)}
    t.update(times)
    sid = {"L0001": "SC001", "L0002": "SC001", "L0003": "SC003"}
    out = []
    for lid, (s, e) in t.items():
        ln = {"id": lid, "speaker": "가온", "text": "지어낸 대사", "start": s, "end": e, "timing": "asr"}
        if scene_ids:
            ln.update(scene_id=sid[lid], status="ok" if s is not None else "absent")
        out.append(ln)
    return out


SCENES = [{"id": "SC001", "start": 5.0, "end": 30.0, "status": "found"},
          {"id": "SC002", "start": 30.0, "end": 40.0, "status": "found"},
          {"id": "SC003", "start": None, "end": None, "status": "absent"}]


def _sound(iid="S0001", onset=10.0, cls="Thunder", ko="천둥", confident=True, unknown=False, **kw) -> dict:
    c = {"id": iid, "kind": "sfx", "onset": onset, "t0": onset, "t1": onset + 1.0,
         "category": "unknown" if unknown else "event", "level_jump_db": 0.0,
         "phrases": [{"text": f"[{ko}]", "short": f"[{ko}]", "source": "detected"}], "phrase": f"[{ko}]",
         "short": f"[{ko}]", "flags": [],
         "label": {"class": cls, "ko": ko, "score": 0.5 if confident else 0.15, "confident": confident,
                   "unknown": unknown, "competing": [], "path_ko": [ko], "top": []}}
    c.update(kw)
    return c


def _project(tmp_path, script=None, dialogue=None, scenes=None, **files) -> Project:
    write_json(tmp_path / "project.json", {"title": "t", "video": "x.mp4", "t0": 0.0, "t1": 100.0, "fps": 24.0,
                                           "width": 64, "height": 36})
    files.setdefault("sfx", {"candidates": []})
    files.setdefault("music", {"items": []})
    files.setdefault("speaker", {"lines": []})
    files.setdefault("cuts", {"cuts": []})
    files["dialogue"] = {"lines": dialogue or []}
    if script is not None:
        files["script"] = script
    if scenes is not None:
        files["scenes"] = {"scenes": scenes}
    for name, obj in files.items():
        write_json(tmp_path / f"{name}.json", obj)
    return Project(tmp_path)


UNSEEN = {"visible": "안 보임", "observation": "빈 복도", "reaction": "", "likely_source": "", "source": "vlm"}


# ================================================================== reading the cues
@pytest.mark.parametrize("text, first", [
    ("멀리서 쾅! 쾅! 하고 무언가 터진다.", "[쾅! 쾅!]"),               # the written onomatopoeia first (II.9)
    ("어디선가 개 짖는 소리가 들려온다.", "[멍멍 - 개]"),               # [의성어 - 출처]
    ("총소리가 크게 울린다.", "[탕 - 총]"),
    ("‘쾅’ 문을 닫고 나간다.", "[쾅 - 문]"),                          # a quoted sound word
    ("탁! 하고 책상을 내려친다.", "[탁!]"),
    ("타타타탕!!! 골목에 총소리.", "[타타타탕! - 총]"),
    ("밖에서 사이렌이 울린다.", "[위이잉 - 사이렌]"),
    ("적막을 깨는 총소리.", "[탕 - 총]"),                            # the silence is broken: a sound
    ("꽈당- 소리가 난다.", "[꽈당]"),                                 # written as a sound, though no table has it
    ("딸랑- 소리와 함께 문이 열린다.", "[딸랑 - 문]"),             # right before '소리': before the table's
    ("쏟아지는 빗소리. 헉- 하며 숨을 몰아쉰다.", "[쏴 - 비]"),          # a cry further on is another sound
    # '[의성어 - 출처]' only when they belong together: a siren far off does not make the brakes squeal
    ("끼이익- 차가 멈춰 선다. 저 멀리 경찰차 한 대가 사이렌을 켠 채 서 있다.", "[끼이익]"),
    ("우당탕탕- 뭔가 부서지는 소리.", "[우당탕탕]"),                    # '탕탕' inside it is no gunshot
    ("이때 울리는 낡은 인터폰.", "[인터폰]"),
    ("요란하게 울리는 가온의 전화.", "[따르릉 - 전화]"),                # what rings comes after '울리는'
    ("들리는 인기척에 고개를 든다.", "[인기척]"),
    ("급브레이크를 세게 밟는다.", "[끼익]"),
])
def test_sound_cues_that_are_sounds(text, first):
    c = cues.sfx_cue(text)
    assert c is not None and c["phrase"] == first
    assert not any(", " in p for p in c["phrases"])                  # no '[의성어, 설명]' (style 3절)


@pytest.mark.parametrize("text", [
    "책상을 툭 친다.",                    # a manner word, not written as a sound
    "텅 빈 거리.",
    "매운탕을 끓인다.",
    "방송국 앞에 차가 선다.",             # '방송' in a place name (감사 노트 8: a wrong hit of the word list)
    "울음을 삼키며 돌아선다.",            # holding back the tears (the other wrong hit)
    "그 소리에 뒤를 돌아본다.",           # points back at a sound already written
    "한 손에 우산이 들려 있다.",          # '들리다' = held
    "끝내 짜증이 폭발해 버린다.",          # a feeling
    "밖에서 그의 목소리가 들린다.",        # an off-screen line of dialogue, not a sound effect
    "부정적인 기사가 실려 있다.",
    "큰 소리로 웃으며 말한다.",
    "소리 없이 문을 연다.",
])
def test_words_that_name_no_sound(text):
    assert cues.sfx_cue(text) is None and cues.silence_cue(text) is None


@pytest.mark.parametrize("text", [
    "차가 심하게 흔들린다.",               # 흔들리다: the letters of '들린' inside another word
    "둘은 잘 어울린다.",                   # 어울리다
    "칼을 마구 휘둘린다.",
    "손에 들린 낡은 가방.",                # 들다: held
    "서점에 들려 책을 고른다.",            # 들르다
    "아무리 불러도 들리지 않는다.",        # a sound that is not heard
    "종이 끝내 울리지 않는다.",
    "우스꽝스러운 모자를 쓴 사내.",        # '꽝' inside a word
    "깔깔이를 걸친 병사.",                 # '깔깔' inside a word
    "금방이라도 웃음이 터질 듯한 표정.",       # a sound that has not happened
    "그때 하는 소리에 돌아본다.",          # points at a line just spoken
    "뒤쪽에서 들리는 소리.",               # says only that something is heard
    "가온을 부르는 소리에 고개를 돌린다.",  # someone calling: a line of dialogue
    "멀리서 꾸중이 들린다.",
])
def test_letters_of_a_sound_word_inside_other_words_are_no_cue(text):
    assert cues.sfx_cue(text) is None


def test_a_cue_that_only_has_a_sori_description_suggests_no_caption():
    # II.9: 'try not to describe the sound as 들린다 … or 소리' — kept as a choice only
    c = cues.sfx_cue("이상한 소리가 난다.")
    assert c["vague"] and c["phrase"] is None and c["phrases"] == ["[이상한 소리]"]
    for text in ("멀리서 쾅! 쾅! 하고 무언가 터진다.", "총소리가 크게 울린다.", "꽈당- 소리가 난다."):
        c = cues.sfx_cue(text)
        assert not c["vague"] and not c["phrase"].endswith("소리]") and "[소리]" not in c["phrases"]
    assert cues.sfx_cue("꽈당- 소리가 난다.")["phrases"] == ["[꽈당]"]          # no '[꽈당 소리]'
    # a '…소리' description goes after the others
    assert cues.sfx_cue("의자가 넘어지는 소리.")["phrases"][0] == "[쿵]"


def test_music_cues_and_song_titles():
    m = cues.music_cue("라디오에서 가수 하늘별의 ‘푸른 바다’가 흘러나온다.")
    assert m["songs"] == [{"artist": "하늘별", "title": "푸른 바다"}] and m["diegetic"]
    # II.7: in quotes, the scene's own music '…이/가 흐른다' first, the title alone next
    assert m["phrases"][:2] == ["[하늘별의 '푸른 바다'가 흐른다]", "[하늘별의 '푸른 바다']"]
    m = cues.music_cue("rain cats의 slow tide가 깔리며 장면이 열린다.", context=True)
    assert m["songs"] == [{"artist": "rain cats", "title": "slow tide"}] and not m["diegetic"]
    assert m["phrase"] == "[rain cats의 'slow tide']"
    m = cues.music_cue("이내 별무리의 ‘밤길’ 선행되고")                 # an adverb is no part of the name
    assert m["songs"] == [{"artist": "별무리", "title": "밤길"}] and m["phrase"] == "[별무리의 '밤길']"
    assert cues.music_cue("MOON - Slow Night 가 흐르며 엔딩.")["songs"] == [{"artist": "MOON", "title": "Slow Night"}]
    assert cues.music_cue("경쾌한 음악이 흐르고")["phrase"] == "[경쾌한 음악]"
    assert cues.music_cue("시끄러운 락 음악이 가득한 지하.")["phrase"] == "[시끄러운 록 음악]"
    assert cues.music_cue("오디오에서 음악이 흘러나온다.")["phrases"][:2] == ["[음악 - 오디오]", "[음악]"]
    assert cues.music_cue("다들 노래를 따라 부른다.")["phrase"] == "[노래를 부른다]"
    assert cues.music_cue("그 음악 계속 흐르고.")["continues"]
    # not songs: a radio programme, a name tag, words someone says
    assert cues.music_cue("라디오. 하늘별의 ‘밤의 편지’에서 잔잔한 음악이 나온다.")["songs"] == []
    assert cues.music_cue("문에 붙은 이름표 ‘나 래’.") is None
    assert cues.music_cue("그게 다 미리 짜 둔 건가요?") is None          # '가요' inside '건가요'
    assert cues.music_cue("혼자 반주를 한다.") is None                 # 반주 = a drink with the meal


def test_silence_cues():
    assert cues.silence_cue("거실에 긴 침묵이 흐른다.")["phrases"] == ["[침묵]", "[정적]"]
    stop = cues.silence_cue("음악이 뚝 멈춘다.")
    assert stop["music_stop"] and stop["phrase"] == "[음악이 멈춘다]"          # 넷플릭스 II.9 예
    assert cues.silence_cue("라디오를 끄는 나래.")["music_stop"]
    assert cues.silence_cue("침묵을 깨고 입을 여는 가온.") is None             # dialogue breaks it
    assert cues.silence_cue("부정적인 반응.") is None


@pytest.mark.parametrize("vocal, phrase", [
    (["한숨"], "[한숨]"), (["물을 마시다 목이 막혀 켁켁"], "[켁켁]"), (["끝내 울음이 터진다"], "[울음]"),
    (["참고 참던 웃음이 결국 터진다"], "[웃음]"), (["재채기"], "[재채기]"),
    (["환히 웃어 보이며"], None), (["웃음을 꾹 참는다"], None), (["쓴웃음"], None), (["씨익 웃어 준다"], None),
    (["한숨 돌리며"], None),
])
def test_vocal_cues(vocal, phrase):
    got = cues.vocal_cue(vocal)
    assert (got["phrase"] if got else None) == phrase


def test_josa():
    assert cues.josa("푸른 바다") == "가" and cues.josa("밤길") == "이"
    assert cues.josa("silver line") == "이" and cues.josa("melody") == "가" and cues.josa("march") == "가"
    assert cues.josa("jump up") == "이" and cues.josa("") is None


def test_extract_places_cues_between_lines():
    d = _story()
    d.act("SC003", "(으악)", speaker="나래", cue="나래", directions=[{"text": "비명", "type": "vocal"}])
    got = cues.extract(d.doc())
    kinds = [(c["id"], c["type"], c["scene_id"]) for c in got]
    assert kinds == [("C0001", "sfx", "SC001"), ("C0002", "sfx", "SC001"), ("C0003", "vocal", "SC001"),
                     ("C0004", "silence", "SC001"), ("C0005", "music", "SC002"), ("C0006", "sfx", "SC003"),
                     ("C0007", "vocal", "SC003")]
    first, between, sigh, quiet, song, shot, scream = got
    assert (first["after_line"], first["before_line"]) == (None, "L0001") and first["offscreen"]
    assert (between["after_line"], between["before_line"]) == ("L0001", "L0002")
    assert sigh["line_id"] == "L0002" and sigh["speaker"] == "나래" and sigh["phrase"] == "[한숨]"
    assert (quiet["after_line"], quiet["before_line"]) == ("L0002", None)
    assert song["songs"][0]["title"] == "푸른 바다" and (song["after_line"], song["before_line"]) == (None, None)
    assert scream["speaker"] == "나래" and scream["line_id"] is None and scream["phrase"] == "[비명]"
    assert "thunder" in first["families"] and "gun" in shot["families"]
    # '이름: 대사' scripts have no elements: the speakers' own sounds of the lines only
    doc = {"version": 2, "elements": [], "lines": [dict(d.lines[1])]}
    assert [c["type"] for c in cues.extract(doc)] == ["vocal"]
    assert cues.extract({}) == [] and cues.extract({"lines": [{"id": "L0001", "text": "말", "speaker": "가"}]}) == []


def test_windows_from_lines_and_scene_times():
    doc = _story().doc()
    w = {c["id"]: c for c in cues.build(doc, _dialogue(), SCENES)}
    assert w["C0001"]["window"] == [5.0, 10.0] and w["C0001"]["window_by"] == ["scene", "line"]
    assert w["C0002"]["window"] == [12.0, 20.0] and w["C0002"]["window_lines"] == ["L0001", "L0002"]
    assert w["C0003"]["window"] == [20.0, 22.0]                            # the speaker's own sound: its line
    assert w["C0004"]["window"] == [22.0, 30.0]
    assert w["C0005"]["window"] == [30.0, 40.0]                            # no dialogue: the scene
    assert w["C0006"]["window"] is None                                    # the scene is not in the video
    # without scenes.json: only the cues between two timed lines
    w = {c["id"]: c["window"] for c in cues.build(doc, _dialogue(), None)}
    assert w == {"C0001": None, "C0002": [12.0, 20.0], "C0003": [20.0, 22.0], "C0004": None, "C0005": None,
                 "C0006": None}
    # a line without a time is passed over: the window reaches to the next thing with a time
    got = {c["id"]: c["window"] for c in cues.build(doc, _dialogue(L0002=(None, None)), SCENES)}
    assert got["C0002"] == [12.0, 30.0] and got["C0003"] is None
    # an older work folder (dialogue.json without scene ids, no scenes.json): no window at all
    assert all(c["window"] is None for c in cues.build(doc, _dialogue(scene_ids=False), None))
    # its lines are not used even next to scene times: the cues get the scene's times only
    got = {c["id"]: c["window"] for c in cues.build(doc, _dialogue(scene_ids=False), SCENES)}
    assert got["C0002"] == [5.0, 30.0] and got["C0003"] is None and got["C0005"] == [30.0, 40.0]
    assert all(c["window"] is None for c in cues.build(doc, [], None))


def test_cues_between_the_same_lines_share_the_window():
    d = Doc()
    s = d.scene(1)
    d.say(s, "간다.")
    d.act(s, "쾅! 문이 닫힌다.")
    d.act(s, "멀리서 개 짖는 소리.")
    d.say(s, "누구야?")
    lines = [{"id": "L0001", "start": 0.0, "end": 2.0, "scene_id": s}, {"id": "L0002", "start": 10.0, "end": 11.0,
                                                                         "scene_id": s}]
    a, b = cues.build(d.doc(), lines, None)
    assert a["window"] == b["window"] == [2.0, 10.0] and (a["guess"], b["guess"]) == (2.0, 6.0)


def test_scene_of_a_time():
    starts = cues.scene_starts(_dialogue(), SCENES)
    assert starts == [(5.0, "SC001"), (30.0, "SC002")]
    assert cues.scene_at(4.0, starts) is None and cues.scene_at(29.9, starts) == "SC001"
    assert cues.scene_at(31.0, starts) == "SC002"
    assert cues.scene_starts(_dialogue(), None) == [(10.0, "SC001")]     # from the first timed line of a scene
    assert cues.scene_starts(_dialogue(scene_ids=False), None) == []


# ================================================================== matching detected sounds
def _cue(window, families=("thunder",), kind="sfx", **kw):
    return {"id": "C0001", "type": kind, "window": list(window), "families": list(families), "keywords": [],
            "phrases": ["[우르릉]"], **kw}


def test_match_takes_the_likeliest_candidate_once():
    thunder, dog = _sound("S0001", 14.0), _sound("S0002", 13.0, "Bark", "개")
    unnamed = _sound("S0003", 15.0, "Generic impact sounds", "부딪히는", confident=False, unknown=True,
                     level_jump_db=25.0)
    late = _sound("S0004", 25.0)
    got = cues.match_sounds([_cue((12.0, 20.0))], [dog, unnamed, thunder, late])
    assert got == {"S0001": got["S0001"]}                     # its kind before an unnamed one; a dog is another sound
    got = cues.match_sounds([_cue((12.0, 20.0))], [dog, unnamed])
    assert list(got) == ["S0003"]
    assert cues.match_sounds([_cue((12.0, 20.0))], [dog]) == {}
    # a window much longer than a scene beat takes only a sound of the named kind
    assert cues.match_sounds([_cue((0.0, 90.0))], [unnamed]) == {}
    assert list(cues.match_sounds([_cue((0.0, 90.0))], [thunder])) == ["S0001"]
    # two cues, one candidate: the second gets none
    two = [_cue((12.0, 20.0)), {**_cue((12.0, 20.0)), "id": "C0002"}]
    assert list(cues.match_sounds(two, [thunder]).values())[0]["id"] == "C0001"
    assert cues.match_sounds([{**_cue((12.0, 20.0)), "window": None}], [thunder]) == {}


# ================================================================== recommendation
def _rec(tmp_path, sfx=(), screen=None, script=None, dialogue=None, scenes=SCENES, **kw):
    p = _project(tmp_path, script=_story().doc() if script is None else script,
                 dialogue=_dialogue() if dialogue is None else dialogue, scenes=scenes,
                 sfx={"candidates": list(sfx)}, screen={"items": screen or {}}, **kw)
    return p, recommend.build(p, {})


def test_a_cue_is_a_reason_to_include_a_detected_sound(tmp_path):
    p, items = _rec(tmp_path, [_sound("S0001", 7.0)], {"S0001": UNSEEN})
    it = next(x for x in items if x["id"] == "S0001")
    assert it["cue_id"] == "C0001" and it["rec"] == "include" and recommend.SCRIPT_WHY in it["reason"]
    told = next(e for e in it["evidence"] if e["kind"] == "대본")
    assert told["polarity"] == "include" and "천둥" in told["text"] and told["window"] == [5.0, 10.0]
    assert "[우르릉 - 천둥]" in [o["text"] for o in it["phrases"]]          # the script's wording as a choice
    assert it["phrase"] == "[천둥]"                                         # the detector's name stays the default
    assert not any(x["id"] == "C0001" for x in items)                      # no second candidate for the cue
    # the same sound without the script: nothing speaks for it
    rec, reason, _ = recommend.decide_sfx(recommend.with_scene_first([_sound("S0001", 7.0)], [])[0], UNSEEN, None,
                                          None)
    assert rec == "check" and "넣을 근거 부족" in reason


def test_a_visible_source_is_still_excluded_first(tmp_path):
    seen = {"S0001": {**UNSEEN, "visible": "보임", "observation": "창밖 번개가 보임"}}
    _, items = _rec(tmp_path, [_sound("S0001", 7.0)], seen)
    it = next(x for x in items if x["id"] == "S0001")
    assert it["cue_id"] == "C0001" and it["rec"] == "exclude" and "번개" in it["reason"]
    c = {**_sound(), "cue_id": "C0001"}
    assert recommend.decide_sfx(c, UNSEEN, {"relation": "already"}, None)[0] == "exclude"
    assert recommend.decide_sfx(c, UNSEEN, None, {"t": 5.0})[0] == "exclude"   # told by the caption before


def test_an_unnamed_sound_takes_the_script_wording(tmp_path):
    unnamed = _sound("S0001", 15.0, "Generic impact sounds", "부딪히는", confident=False, unknown=True,
                     phrases=[{"text": "[소리]", "short": "[소리]", "source": "unknown"}], phrase="[소리]")
    _, items = _rec(tmp_path, [unnamed], {"S0001": UNSEEN})
    it = next(x for x in items if x["id"] == "S0001")
    assert it["cue_id"] == "C0002" and it["named_by"] == "script" and it["phrase"] == "[쿵!]"
    assert "[소리]" not in [o["text"] for o in it["phrases"]]
    assert it["rec"] == "check" and "이름은 대본에서" in it["reason"]            # never included on the script alone
    # named by the script, a visible source excludes it like any named sound
    seen = {"S0001": {**UNSEEN, "visible": "보임", "observation": "책이 떨어짐"}}
    _, items = _rec(tmp_path, [unnamed], seen)
    assert next(x for x in items if x["id"] == "S0001")["rec"] == "exclude"


def test_cues_nothing_detected_become_script_candidates(tmp_path):
    _, items = _rec(tmp_path)
    made = {x["id"]: x for x in items if x.get("origin") == "script"}
    assert set(made) == {"C0001", "C0002", "C0003", "C0004", "C0005"}      # C0006: its scene is not in the video
    for it in made.values():
        assert it["rec"] == "check" and it["estimated"] and it["agent_reasons"] == []
        assert it["reason"].startswith(recommend.SCRIPT_NOT_FOUND) and "timing_estimated" in it["flags"]
    bang = made["C0002"]
    assert bang["kind"] == "sfx" and bang["t0"] == 12.0 and bang["phrase"] == "[쿵!]" and bang["window"] == [12.0, 20.0]
    sigh = made["C0003"]
    assert sigh["source_line"] == "L0002" and sigh["onset"] == 20.0 and sigh["phrase"] == "[한숨]"
    quiet = made["C0004"]
    assert quiet["kind"] == "sfx" and quiet["category"] == "silence" and quiet["phrase"] == "[침묵]"
    song = made["C0005"]
    assert song["kind"] == "music" and song["phrase"] == "[하늘별의 '푸른 바다'가 흐른다]"
    assert song["music"]["event"] == "script"
    assert not any(x["kind"] == "speaker" and x["speaker"]["line_id"] == "L0003" for x in items)   # no time, no name


def test_a_speakers_own_sound_joins_a_detected_one_on_its_line(tmp_path):
    sigh = _sound("S0001", 20.2, "Sigh", "한숨", category="vocal")
    _, items = _rec(tmp_path, [sigh], {"S0001": UNSEEN})
    it = next(x for x in items if x["id"] == "S0001")
    assert it["cue_id"] == "C0003" and it["source_line"] == "L0002"
    assert not any(x.get("origin") == "script" and x["id"] == "C0003" for x in items)
    # whose sound it is, not a reason to caption it (the speaker is mostly in view, 감사 8-12)
    assert it["rec"] == "check" and recommend.SCRIPT_WHY not in it["reason"]
    assert next(e for e in it["evidence"] if e["kind"] == "대본")["polarity"] == "neutral"
    # a thing's sound and a person's voice do not stand for each other, even unnamed
    thud = _sound("S0001", 22.5, "Generic impact sounds", "부딪히는", confident=False, unknown=True)
    _, items = _rec(tmp_path, [thud], {"S0001": UNSEEN})
    assert "cue_id" not in next(x for x in items if x["id"] == "S0001")


def test_a_silence_cue_joins_the_music_stop_there(tmp_path):
    stop = {"kind": "stop", "onset": 24.0, "segment": [15.0, 24.0], "moods": [], "mood_strength": 0.0,
            "drop_db": 18.0, "music_score": 0.8}
    _, items = _rec(tmp_path, music={"items": [stop]})
    st = next(x for x in items if x["kind"] == "music" and x["music"]["event"] == "stop")
    assert st["cue_id"] == "C0004" and st["rec"] == "check" and "대본 지문에도 적힘" in st["reason"]
    assert any(e["kind"] == "대본" for e in st["evidence"])
    assert not any(x["id"] == "C0004" for x in items)                       # one candidate, not two


def test_a_song_title_is_a_choice_of_the_music_there(tmp_path):
    start = {"kind": "start", "onset": 31.0, "segment": [31.0, 39.0], "music_score": 0.9, "mood_strength": 0.01,
             "moods": [{"key": "Happy music", "ko": "경쾌한", "score": 0.8}, {"key": "Sad music", "ko": "슬픈", "score": 0.1}],
             "instruments": []}
    _, items = _rec(tmp_path, music={"items": [start]})
    m = next(x for x in items if x["id"] == "M0001")
    texts = [o["text"] for o in m["phrases"]]
    assert m["cue_id"] == "C0005" and m["rec"] == "include" and m["phrase"] == "[경쾌한 음악]"   # the default stays
    assert texts[1] == "[하늘별의 '푸른 바다'가 흐른다]" and "대본에 곡명" in m["reason"]
    assert not any(x["id"] == "C0005" for x in items)


def test_older_work_folders_get_the_recommendations_they_had(tmp_path):
    lines = [{"id": "L0001", "speaker": "가온", "text": "지어낸 대사", "start": 10.0, "end": 12.0, "timing": "asr"}]
    sounds = [_sound("S0001", 7.0), _sound("S0002", 15.0, "Rain", "비", category="ambient")]
    seen = {"S0001": UNSEEN, "S0002": UNSEEN}

    def run(path, script):
        path.mkdir()
        p = _project(path, script=script, dialogue=lines, sfx={"candidates": sounds}, screen={"items": seen},
                     cuts={"cuts": [1.0, 2.0]})
        return [(x["id"], x["rec"], x["reason"]) for x in recommend.build(p, {})], recommend.build(p, {})

    base, items = run(tmp_path / "none", None)
    assert not any(x.get("cue_id") or x.get("origin") for x in items)
    assert [(i, r) for i, r, _ in base] == [("S0001", "check"), ("P0001", "check"), ("S0002", "include")]
    assert "넣을 근거 부족" in base[0][2] and "장면 첫 배경 소리" in base[2][2]
    old_script = {"lines": [{"id": "L0001", "speaker": "가온", "text": "지어낸 대사"}], "characters": ["가온"]}
    assert run(tmp_path / "v1", old_script)[0] == base                     # an older script.json: no elements
    # a version-2 script with a dialogue.json from before (no scene ids): no window, the same result
    assert run(tmp_path / "v2", _story().doc())[0] == base


# ------------------------------------------------------------------ one action line between two lines
def _between(text, timing="asr"):
    """SC001 (5–30 s): L0001 10–12 s, the action line, L0002 20–22 s."""
    d = Doc()
    s = d.scene(1)
    d.say(s, "출발하자.")
    d.act(s, text)
    d.say(s, "가자.")
    lines = [{"id": "L0001", "speaker": "가온", "text": "출발하자.", "start": 10.0, "end": 12.0, "timing": timing,
              "scene_id": s, "status": "ok"},
             {"id": "L0002", "speaker": "가온", "text": "가자.", "start": 20.0, "end": 22.0, "timing": timing,
              "scene_id": s, "status": "ok"}]
    return d.doc(), lines


def _rec_between(tmp_path, text, sounds, screen=None, timing="asr"):
    doc, lines = _between(text, timing)
    return _rec(tmp_path, sounds, screen or {s["id"]: UNSEEN for s in sounds}, script=doc, dialogue=lines,
                scenes=SCENES[:1])


@pytest.mark.parametrize("text", ["차가 심하게 흔들린다.", "둘은 잘 어울린다."])
def test_a_word_that_only_holds_sound_letters_includes_nothing(tmp_path, text):
    # the review case: a sure dog bark between the lines stays 'check', as without a script
    _, items = _rec_between(tmp_path, text, [_sound("S0001", 15.0, "Bark", "개")])
    it = next(x for x in items if x["id"] == "S0001")
    assert "cue_id" not in it and it["rec"] == "check" and "넣을 근거 부족" in it["reason"]
    assert not any(x.get("origin") == "script" for x in items)


def test_a_cue_that_does_not_say_which_sound_takes_no_named_sound(tmp_path):
    bark = _sound("S0001", 15.0, "Bark", "개")
    _, items = _rec_between(tmp_path, "이상한 소리가 난다.", [bark])
    it = next(x for x in items if x["id"] == "S0001")
    assert "cue_id" not in it and it["rec"] == "check"                     # no 'any' match
    assert not any(x.get("origin") == "script" for x in items)             # and no '[이상한 소리]' candidate
    # an unnamed sound there: the script's words are evidence and a choice, not its caption
    unnamed = _sound("S0001", 15.0, "Generic impact sounds", "부딪히는", confident=False, unknown=True,
                     phrases=[{"text": "[소리]", "short": "[소리]", "source": "unknown"}], phrase="[소리]")
    _, items = _rec_between(tmp_path, "이상한 소리가 난다.", [unnamed])
    it = next(x for x in items if x["id"] == "S0001")
    assert it["cue_id"] == "C0001" and not it["cue_reason"] and "named_by" not in it
    assert it["phrase"] == "[소리]" and [o["text"] for o in it["phrases"]] == ["[소리]", "[이상한 소리]"]
    assert recommend.SCRIPT_WHY not in it["reason"]
    assert next(e for e in it["evidence"] if e["kind"] == "대본")["polarity"] == "neutral"


def test_an_onomatopoeia_no_table_has_is_a_candidate_of_its_own(tmp_path):
    # '꽈당' says how it sounds but not what: a sure dog bark there is another sound
    _, items = _rec_between(tmp_path, "꽈당- 소리가 난다.", [_sound("S0001", 15.0, "Bark", "개")])
    assert "cue_id" not in next(x for x in items if x["id"] == "S0001")
    made = next(x for x in items if x.get("origin") == "script")
    assert made["phrase"] == "[꽈당]" and made["rec"] == "check"
    # an unnamed sound there takes the written onomatopoeia as its caption
    unnamed = _sound("S0001", 15.0, "Generic impact sounds", "부딪히는", confident=False, unknown=True,
                     phrases=[{"text": "[소리]", "short": "[소리]", "source": "unknown"}], phrase="[소리]")
    _, items = _rec_between(tmp_path, "꽈당- 소리가 난다.", [unnamed])
    it = next(x for x in items if x["id"] == "S0001")
    assert it["named_by"] == "script" and it["phrase"] == "[꽈당]" and it["rec"] == "check"


def test_a_window_on_guessed_line_times_is_no_reason_to_include(tmp_path):
    shot = [_sound("S0001", 15.0, "Gunshot, gunfire", "총")]
    _, items = _rec_between(tmp_path, "멀리서 총소리가 크게 울린다.", shot)
    it = next(x for x in items if x["id"] == "S0001")
    assert it["rec"] == "include" and recommend.SCRIPT_WHY in it["reason"] and not it["cue_estimated"]
    # the same lines placed by voice activity only (align could not match them, 감사 1-08)
    _, items = _rec_between(tmp_path, "멀리서 총소리가 크게 울린다.", shot, timing="even")
    it = next(x for x in items if x["id"] == "S0001")
    assert it["cue_id"] == "C0001" and it["cue_estimated"] and not it["cue_reason"]
    assert it["rec"] == "check" and "대본 단서의 시각이 추정" in it["reason"]
    told = next(e for e in it["evidence"] if e["kind"] == "대본")
    assert told["polarity"] == "neutral" and "시각이 추정" in told["text"]
    doc, lines = _between("멀리서 총소리가 크게 울린다.", "voice")
    assert cues.build(doc, lines, SCENES[:1])[0]["window_estimated"]
    lines[1]["timing"], lines[1]["status"] = "asr", "unheard"
    assert cues.build(doc, lines, SCENES[:1])[0]["window_estimated"]


def test_a_persons_own_sound_in_an_action_line_counts_only_off_screen(tmp_path):
    laugh = [_sound("S0001", 15.0, "Laughter", "웃음", category="vocal")]
    unseen = {"S0001": {**UNSEEN, "source": "none", "visible": "애매"}}    # the screen was not looked at
    _, items = _rec_between(tmp_path, "둘이 낄낄대며 웃는다.", laugh, unseen)
    it = next(x for x in items if x["id"] == "S0001")
    assert it["cue_id"] == "C0001" and not it["cue_reason"] and it["rec"] == "check"   # as '(낄낄)' in the dialogue
    assert recommend.decide_sfx(laugh[0], unseen["S0001"], None, None)[0] == it["rec"]
    _, items = _rec_between(tmp_path, "문 밖에서 웃음소리가 들린다.", laugh, unseen)
    it = next(x for x in items if x["id"] == "S0001")
    assert it["cue_reason"] and it["rec"] == "include"                     # written as off screen
    scream = [_sound("S0001", 15.0, "Screaming", "비명", category="vocal")]
    _, items = _rec_between(tmp_path, "비명이 터진다.", scream, unseen)
    assert next(x for x in items if x["id"] == "S0001")["cue_reason"]       # a scream is an event


def test_music_mood_words_are_moods_not_what_the_music_does():
    assert cues.music_cue("잔잔하게 깔리는 음악.")["phrase"] == "[음악]"
    assert cues.music_cue("어디선가 들려오는 음악.")["phrase"] == "[음악]"
    assert cues.music_cue("틀어놓은 음악이 흐른다.")["phrase"] == "[음악]"
    assert cues.music_cue("같은 음악이 다시 흐른다.")["phrase"] == "[음악]"
    assert cues.music_cue("슬픈 음악이 흐른다.")["phrase"] == "[슬픈 음악]"
    assert cues.music_cue("느린 음악이 흐른다.")["phrase"] == "[느린 음악]"
    assert cues.music_cue("신나는 음악이 터진다.")["phrase"] == "[신나는 음악]"


def test_scene_first_uses_the_script_scene_when_it_is_known():
    rain = [_sound("S0001", 6.0, "Rain", "비", category="ambient"), _sound("S0002", 31.0, "Rain", "비", category="ambient")]
    cuts = [1.0]                                         # both in one six-shot group
    assert [c["scene_first"] for c in recommend.with_scene_first(rain, cuts)] == [True, False]
    starts = cues.scene_starts(_dialogue(), SCENES)
    assert [c["scene_first"] for c in recommend.with_scene_first(rain, cuts, starts)] == [True, True]


# ================================================================== stage, pipeline, plot prompt
def test_cues_stage_writes_every_cue_and_runs_after_align(tmp_path):
    p = _project(tmp_path, script=_story().doc(), dialogue=_dialogue(), scenes=SCENES)
    res = cue_stage.run(p, {})
    got = read_json(p.path("cues.json"))
    assert res["cues"] == 6 and res["windowed"] == 5 and got["counts"] == res and len(got["cues"]) == 6
    assert next(c for c in got["cues"] if c["id"] == "C0006")["window"] is None   # kept, without a window
    assert pipeline.STAGES.index("align") < pipeline.STAGES.index("cues") < pipeline.STAGES.index("recommend")
    assert pipeline.OUTPUTS["cues"] == ["cues.json"] and "recommend" in pipeline.dependents(["cues"])
    assert "speaker.part.json" in pipeline.OUTPUTS["speaker"] and "scenes.json" in pipeline.OUTPUTS["align"]
    assert studio.STAGE_LABELS["cues"] == "대본 단서" and studio.KEY_OUTPUT["cues"] == "cues.json"
    assert studio._note("cues", res) == "단서 6개 · 시간 창 5개"
    # the recommendation writes cues.json again from the same files: after the script was read
    # again by itself ('--stages script' reruns align … recommend, not cues) the file is not stale
    p.path("cues.json").unlink()
    recommend.run(p, {})
    assert read_json(p.path("cues.json"))["counts"] == res
    # no script: an empty file, so the stage counts as done
    (tmp_path / "bare").mkdir()
    q = _project(tmp_path / "bare")
    assert cue_stage.run(q, {})["cues"] == 0 and read_json(q.path("cues.json"))["cues"] == []


def test_plot_question_carries_the_scene_heading_and_nearby_action_lines(tmp_path, monkeypatch):
    doc, lines = _story().doc(), _dialogue()
    starts = cues.scene_starts(lines, SCENES)
    ctx = cues.context_at(doc, lines, 15.0, starts)
    assert ctx == {"scene_id": "SC001", "heading": "1. 지어낸 골목 / 밤", "actions": ["쿵! 하고 무언가 떨어진다."]}
    assert cues.context_at(doc, lines, 2.0, starts) is None                # before the first scene
    bang = _sound("S0001", 15.0)
    q = dialog_ev.question([bang], lines, dialog_ev.contexts([bang], doc, lines, starts))
    assert "장면: 1. 지어낸 골목 / 밤" in q and "지문: 쿵! 하고 무언가 떨어진다." in q
    # without a script (or scene times) the question is what it was
    plain = dialog_ev.question([bang], lines)
    assert "장면:" not in plain and "지문:" not in plain and dialog_ev.contexts([bang], {}, lines, starts) == {}
    assert dialog_ev.contexts([bang], doc, lines, []) == {}
    # the stage hands the context to the call when there is one
    got = []
    monkeypatch.setattr(dialog_ev.llm, "available", lambda: True)
    monkeypatch.setattr(dialog_ev, "_batch_call", lambda b, ln, cfg, work, context=None: got.append(context) or {})
    p = _project(tmp_path, script=doc, dialogue=lines, scenes=SCENES, sfx={"candidates": [bang]})
    dialog_ev.run(p, {})
    assert got and got[0]["S0001"]["heading"] == "1. 지어낸 골목 / 밤"


def test_without_an_llm_the_script_still_speaks_for_a_sound(tmp_path, monkeypatch):
    monkeypatch.setattr(dialog_ev.llm, "available", lambda: False)
    p = _project(tmp_path, script=_story().doc(), dialogue=_dialogue(), scenes=SCENES,
                 sfx={"candidates": [_sound("S0001", 7.0)]}, screen={"items": {"S0001": UNSEEN}})
    res = dialog_ev.run(p, {"llm": False})
    assert res["llm"] is False and read_json(p.path("dialog_ev.json"))["items"]["S0001"]["source"] == "rule"
    it = next(x for x in recommend.build(p, {}) if x["id"] == "S0001")
    assert it["rec"] == "include" and recommend.SCRIPT_WHY in it["reason"]


@pytest.mark.parametrize("text", [
    "(그게 대체 무슨 소리야?)",            # here 소리 means talk, not a sound
    "참았던 욕을 폭발하는 나래.",           # bursting out with words
])
def test_idioms_that_name_no_sound(text):
    assert cues.sfx_cue(text) is None


def test_a_dj_names_the_record_before_the_music_starts():
    d = Doc()
    s = d.scene(1, "지어낸 방송실")
    d.act(s, "판을 고르는 손.")
    d._el("other", s, "sea lamps의 low tide!")             # a DJ's line the reader left as 'other'
    d.act(s, "스피커에서 음악이 쏟아져 나온다.")
    music = [c for c in cues.extract(d.doc()) if c["type"] == "music"]
    assert music[-1]["songs"] == [{"artist": "sea lamps", "title": "low tide"}] and music[-1]["songs_near"]
    assert music[-1]["phrase"] == "[sea lamps의 'low tide'가 흐른다]"
