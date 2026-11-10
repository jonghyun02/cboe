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


@pytest.mark.parametrize("text, phrase", [
    ("문이 끼익 열린다.", "[끼익 - 문]"),
    ("지어낸 창고. 철문이 끼익 열린다.", "[끼익 - 철문]"),
    ("대문이 끼이익- 닫힌다.", "[끼이익 - 대문]"),
    ("현관문이 삐걱 열린다.", "[삐걱 - 현관문]"),
    ("문이 덜컹 닫힌다.", "[덜컹 - 문]"),
    ("문이 끼익- 하고 열린다.", "[끼익 - 문]"),
])
def test_an_onomatopoeia_between_the_door_and_its_verb_is_the_door(text, phrase):
    """'문 + 의성어 + 열린다' is the door that sounds (not brakes: '[급정거]', the car family)."""
    c = cues.sfx_cue(text)
    assert c["phrase"] == phrase and "door" in c["families"]
    assert "[급정거]" not in c["phrases"] and "car" not in c["families"]
    # a brake still is one
    car = cues.sfx_cue("문 앞에 차가 끼익 선다.")
    assert car["phrase"] == "[끼익]" and "[급정거]" in car["phrases"] and "car" in car["families"]
    assert "door" in cues.sfx_cue("끼익- 문이 열린다.")["families"]


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
    # a title without quotes said drawn out first, then whole: the whole is the title
    songs = cues.song_titles("자, 다음 곡은 glen moss오빠의 shine~. shine on forever!", True)
    assert songs == [{"artist": "glen moss", "title": "shine on forever"}]
    assert cues.title_phrases(songs[0], True)[0] == "[glen moss의 'shine on forever'가 흐른다]"
    assert cues.song_titles("glen moss의 shine~. 다들 좋아하는 곡!", True)[0]["title"] == "shine"
    assert cues.song_titles("glen moss의 shine. sunset drive도 이어서.", True)[0]["title"] == "shine"   # another title
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


def _scene_first(extra=False):
    """A script and the align output the way the scene-first alignment writes it: a found
    scene's start / end are its matched lines' range (감사 이음매: 0-second windows).
    SC001 found (L0001 45–47 s, L0002 50–52 s), SC002 not in the video, SC003 no dialogue,
    SC004 found (L0004 80–82 s), SC005 at the end of the processed stretch (L0005 90–92 s, then
    L0006–L0008 out of it), SC006 out of it."""
    d = Doc()
    s1 = d.scene(1)
    d.act(s1, "벽시계가 째깍째깍 소리를 낸다.")            # C0001: before the first line of the first scene
    d.act(s1, "자명종이 요란하게 울린다.")                 # C0002
    d.say(s1, "벌써 아침이야?")
    d.say(s1, "얼른 일어나.", speaker="나래")
    d.act(s1, "쾅! 방문이 닫힌다.")                       # C0003: after the last line
    if extra:
        d.act(s1, "덜컹! 창문이 흔들린다.")
    s2 = d.scene(2, "지어낸 부엌")
    d.act(s2, "냄비가 끓는 소리.")                        # C0004: a scene the video does not have
    d.say(s2, "밥 먹자.")
    s3 = d.scene(3, "지어낸 계단")
    d.act(s3, "멀리서 개 짖는 소리가 들려온다.")           # C0005: a scene without dialogue
    s4 = d.scene(4, "지어낸 교실")
    d.act(s4, "땡땡땡 종이 울린다.")                      # C0006: before the first line, after a gap
    d.say(s4, "자리에 앉아.")
    s5 = d.scene(5, "지어낸 운동장")
    d.say(s5, "공 이리 줘!")
    d.act(s5, "휘익- 호루라기 소리.")                     # C0007: the next lines are out of the stretch
    d.say(s5, "반칙이야.", speaker="나래")
    d.say(s5, "아니거든.")
    d.act(s5, "쿵! 공이 골대에 맞는다.")                  # C0008: after lines out of the stretch
    d.say(s5, "아깝다.", speaker="나래")
    s6 = d.scene(6, "지어낸 버스")
    d.act(s6, "빵빵 경적이 울린다.")                      # C0009: a scene out of the stretch
    d.say(s6, "다 왔다.")
    t = {"L0001": (45.0, 47.0), "L0002": (50.0, 52.0), "L0004": (80.0, 82.0), "L0005": (90.0, 92.0)}
    lines = []
    for ln in d.lines:
        s, e = t.get(ln["id"], (None, None))
        status = "ok" if s is not None else "absent" if ln["scene_id"] == "SC002" else "out_of_range"
        lines.append({"id": ln["id"], "scene_id": ln["scene_id"], "start": s, "end": e, "timing": "asr",
                      "status": status})
    scenes = [{"id": "SC001", "start": 45.0, "end": 52.0, "status": "found"},
              {"id": "SC002", "start": None, "end": None, "status": "absent"},
              {"id": "SC003", "start": 52.0, "end": 80.0, "status": "unknown"},
              {"id": "SC004", "start": 80.0, "end": 82.0, "status": "found"},
              {"id": "SC005", "start": 90.0, "end": 92.0, "status": "found"},
              {"id": "SC006", "start": None, "end": None, "status": "out_of_range"}]
    return d, lines, scenes


def test_a_window_beside_a_scene_edge_reaches_the_lines_of_the_next_scene():
    d, lines, scenes = _scene_first()
    w = {c["id"]: c for c in cues.build(d.doc(), lines, scenes, (0.0, 100.0))}
    assert [w[f"C{k:04d}"]["type"] for k in range(1, 10)] == ["sfx"] * 9
    # before the first line of the film: from the start of the processed stretch
    assert w["C0001"]["window"] == w["C0002"]["window"] == [0.0, 45.0]
    assert w["C0001"]["window_by"] == ["range", "line"] and w["C0001"]["window_lines"] == [None, "L0001"]
    # after the last line of a scene: to the next timed line, past a scene the video does not have
    assert w["C0003"]["window"] == [52.0, 80.0] and w["C0003"]["window_by"] == ["line", "other_scene"]
    assert w["C0003"]["window_lines"] == ["L0002", "L0004"]
    assert w["C0004"]["window"] is None                                    # its scene is not in the video
    assert w["C0005"]["window"] == [52.0, 80.0] and w["C0005"]["window_by"] == ["scene", "scene"]
    assert w["C0005"]["window_estimated"]                                  # where that scene lies is a guess
    assert w["C0006"]["window"] == [52.0, 80.0] and w["C0006"]["window_by"] == ["other_scene", "line"]
    assert not w["C0006"]["window_estimated"]
    # lines out of the stretch: before them the cue may still be in it, after them it is not
    assert w["C0007"]["window"] == [92.0, 100.0] and w["C0007"]["window_by"] == ["line", "range"]
    assert w["C0008"]["window"] is None and w["C0009"]["window"] is None
    assert not any(c["window"] and c["window"][0] == c["window"][1] for c in w.values())
    # the stretch unknown: no end there
    w = {c["id"]: c["window"] for c in cues.build(d.doc(), lines, scenes)}
    assert w["C0001"] is None and w["C0007"] is None and w["C0003"] == [52.0, 80.0]
    # the stage reads the stretch from project.json
    assert cue_stage.span(type("P", (), {"meta": {"t0": 5.0, "t1": None, "duration": 70.0}})()) == (5.0, 70.0)


def test_guessed_times_in_a_window_that_reaches_into_another_scene():
    d, lines, scenes = _scene_first()
    w = {c["id"]: c for c in cues.build(d.doc(), lines, scenes, (0.0, 100.0))}
    # before a scene's first line: spread in script order, none on the far end
    assert (w["C0001"]["guess"], w["C0002"]["guess"]) == (15.0, 30.0)
    assert w["C0006"]["guess"] == 66.0
    # after a scene's last line: right after it
    assert w["C0003"]["guess"] == 52.0 and w["C0007"]["guess"] == 92.0
    d, lines, scenes = _scene_first(extra=True)                # a second cue after the same last line
    got = [c for c in cues.build(d.doc(), lines, scenes, (0.0, 100.0)) if c["scene_id"] == "SC001"
           and c["window"] == [52.0, 80.0]]
    assert [c["guess"] for c in got] == [52.0, 52.0 + cues.GUESS_STEP_S]


def test_scenes_without_dialogue_in_a_row_share_the_gap_in_order():
    d = Doc()
    s1, s2, s3, s4 = d.scene(1), d.scene(2, "지어낸 옥상"), d.scene(3, "지어낸 지붕"), d.scene(4, "지어낸 마당")
    d.elements.clear()
    d._el("heading", s1, "1.")
    d.say(s1, "올라가 보자.")
    d._el("heading", s2, "2.")
    d.act(s2, "멀리서 천둥 소리가 들린다.")
    d._el("heading", s3, "3.")
    d.act(s3, "쿵! 기왓장이 떨어진다.")
    d._el("heading", s4, "4.")
    d.say(s4, "괜찮아?")
    lines = [{"id": "L0001", "scene_id": s1, "start": 10.0, "end": 12.0, "timing": "asr", "status": "ok"},
             {"id": "L0002", "scene_id": s4, "start": 40.0, "end": 42.0, "timing": "asr", "status": "ok"}]
    scenes = [{"id": s1, "start": 10.0, "end": 12.0, "status": "found"},
              {"id": s2, "start": 12.0, "end": 40.0, "status": "unknown"},
              {"id": s3, "start": 12.0, "end": 40.0, "status": "unknown"},
              {"id": s4, "start": 40.0, "end": 42.0, "status": "found"}]
    a, b = cues.build(d.doc(), lines, scenes, (0.0, 100.0))
    assert a["window"] == b["window"] == [12.0, 40.0] and a["window_by"] == ["scene", "scene"]
    assert (a["guess"], b["guess"]) == (12.0, 26.0)                       # in script order, not both at 12 s


def _timed(lid, sid, s, e, status="ok"):
    aligned = s is not None
    return {"id": lid, "scene_id": sid, "start": s, "end": e, "timing": "asr", "status": status, "aligned": aligned}


def test_first_and_last_scenes_without_dialogue_reach_the_edge_of_the_stretch():
    from cboe.stages.align import _scene_rows
    d = Doc()
    s1, s2, s3, s4, s5, s6 = (d.scene(k, f"지어낸 곳 {k}") for k in range(1, 7))
    d.act(s1, "새벽. 멀리서 문이 쾅 닫힌다.")                  # C0001: the film opens on action
    d.say(s2, "일어나.")
    d.say(s2, "응.", speaker="나래")
    d.act(s3, "자동차 경적이 빵빵 울린다.")                   # C0002, C0003: two scenes without dialogue in a row
    d.act(s4, "전화벨이 울린다.")
    d.say(s5, "여보세요.")
    d.act(s6, "쿵! 대문이 닫힌다.")                           # C0004: and ends on it
    out = [_timed("L0001", s2, 40.0, 41.0), _timed("L0002", s2, 42.0, 43.0), _timed("L0003", s5, 200.0, 201.0)]
    rows = _scene_rows(d.doc(), out, None)
    w = {c["id"]: c for c in cues.build(d.doc(), out, rows, (0.0, 600.0))}
    assert w["C0001"]["window"] == [0.0, 40.0] and w["C0001"]["window_by"] == ["range", "scene"]
    assert w["C0004"]["window"] == [201.0, 600.0] and w["C0004"]["window_by"] == ["scene", "range"]
    assert w["C0001"]["window_estimated"] and w["C0004"]["window_estimated"]
    assert w["C0002"]["window"] == w["C0003"]["window"] == [43.0, 200.0]
    # the stretch unknown: no end there
    assert all(c["window"] is None for c in cues.build(d.doc(), out, rows) if c["id"] in ("C0001", "C0004"))
    # in time: the first scene from the start of the stretch, the scenes in a row share the gap
    starts = cues.scene_starts(out, rows, (0.0, 600.0))
    assert starts == [(0.0, s1), (40.0, s2), (43.0, s3), (121.5, s4), (200.0, s5), (201.0, s6)]
    assert [cues.scene_at(t, starts) for t in (5.0, 50.0, 150.0, 250.0)] == [s1, s3, s4, s6]
    # the head cut off (lines before are out of the stretch): the scene may lie outside, no window
    h = Doc()
    a, b, c = h.scene(1), h.scene(2, "지어낸 계단"), h.scene(3, "지어낸 교실")
    h.say(a, "가자.")
    h.act(b, "쾅! 문이 닫힌다.")
    h.say(c, "누구야?")
    out = [_timed("L0001", a, None, None, status="out_of_range"), _timed("L0002", c, 50.0, 51.0)]
    got = cues.build(h.doc(), out, _scene_rows(h.doc(), out, None), (30.0, 600.0))
    assert got[0]["window"] is None


def _two_doors():
    """SC001: a line at 10–12 s, then a door slammed; SC002: a door opens before its line at
    100–102 s. One window [12, 100] for both cues (each reaches into the other scene)."""
    d = Doc()
    s1, s2 = d.scene(1), d.scene(2, "지어낸 창고")
    d.say(s1, "나 간다.")
    d.act(s1, "가온, 현관문을 쾅 닫고 나간다.")
    d.act(s2, "철문이 끼익 열린다.")
    d.say(s2, "누구야?", speaker="나래")
    out = [_timed("L0001", s1, 10.0, 12.0), _timed("L0002", s2, 100.0, 102.0)]
    scenes = [{"id": s1, "start": 10.0, "end": 12.0, "status": "found", "lines": ["L0001"]},
              {"id": s2, "start": 100.0, "end": 102.0, "status": "found", "lines": ["L0002"]}]
    return d, out, scenes


def test_a_wide_window_takes_the_sound_near_the_cues_own_scene():
    d, out, scenes = _two_doors()
    slam, creak = cues.build(d.doc(), out, scenes, (0.0, 600.0))
    assert slam["window"] == creak["window"] == [12.0, 100.0]
    near = _sound("S0001", 14.0, "Door", "문소리", level_jump_db=12.0)
    far = _sound("S0002", 96.0, "Door", "문소리", level_jump_db=25.0)        # louder, but by the next scene's line
    got = cues.match_sounds([slam, creak], [near, far])
    assert {k: v["id"] for k, v in got.items()} == {"S0001": "C0001", "S0002": "C0002"}
    # one sound only: it goes to the cue whose scene it is next to, though that cue comes later
    assert {k: v["id"] for k, v in cues.match_sounds([slam, creak], [far]).items()} == {"S0002": "C0002"}
    # the reason to include: not for a sound far from the cue's own scene
    assert cues.is_reason(slam, "kind", 14.0) and not cues.is_reason(slam, "kind", 60.0)
    assert cues.off_scene(slam, 60.0) and not cues.off_scene(creak, 96.0)
    # two cues before one line share the window in script order (the louder sound is the later one)
    d2 = Doc()
    s = d2.scene(1)
    d2.act(s, "쾅! 문이 닫힌다.")
    d2.act(s, "문이 덜컹 열린다.")
    d2.say(s, "왔어?")
    a, b = cues.build(d2.doc(), [_timed("L0001", s, 100.0, 102.0)],
                      [{"id": s, "start": 100.0, "end": 102.0, "status": "found", "lines": ["L0001"]}], (0.0, 600.0))
    early = _sound("S0003", 20.0, "Door", "문소리", level_jump_db=10.0)
    late = _sound("S0004", 90.0, "Door", "문소리", level_jump_db=30.0)
    got = cues.match_sounds([a, b], [early, late])
    assert {k: v["id"] for k, v in got.items()} == {"S0003": "C0001", "S0004": "C0002"}
    # a sound nearer the later cue's place stays with the earlier one when the later takes another
    d3 = Doc()
    s = d3.scene(1)
    d3.act(s, "벽시계가 째깍째깍 소리를 낸다.")
    d3.act(s, "자명종이 요란하게 울린다.")
    d3.say(s, "벌써 아침이야?")
    clock, alarm = cues.build(d3.doc(), [_timed("L0001", s, 143.0, 145.0)],
                              [{"id": s, "start": 143.0, "end": 145.0, "status": "found", "lines": ["L0001"]}],
                              (0.0, 600.0))
    tick, ring = _sound("S0007", 77.0, "Tick", "째깍"), _sound("S0008", 83.0, "Alarm clock", "따르릉")
    got = cues.match_sounds([clock, alarm], [tick, ring])
    assert {k: v["id"] for k, v in got.items()} == {"S0007": "C0001", "S0008": "C0002"}
    # a narrow window still takes the likeliest (louder) candidate
    c = {"id": "C0009", "type": "sfx", "window": [10.0, 20.0], "families": ["door"], "keywords": []}
    quiet, loud = _sound("S0005", 11.0, "Door", "문소리"), _sound("S0006", 19.0, "Door", "문소리", level_jump_db=20.0)
    assert list(cues.match_sounds([c], [quiet, loud])) == ["S0006"]


def test_wide_windows_take_the_named_kind_and_the_music_near_the_scene():
    d, lines, scenes = _scene_first()
    built = {c["id"]: c for c in cues.build(d.doc(), lines, scenes, (0.0, 100.0))}
    clock, alarm = built["C0001"], built["C0002"]
    tick = _sound("S0001", 12.0, "Tick", "째깍")
    ring = _sound("S0002", 15.0, "Alarm clock", "따르릉")
    bang = _sound("S0003", 20.0, "Generic impact sounds", "부딪히는", confident=False, unknown=True)
    got = cues.match_sounds([clock, alarm], [tick, ring, bang])
    assert {k: v["id"] for k, v in got.items()} == {"S0001": "C0001", "S0002": "C0002"}
    # a 30-second window: no unnamed sound
    assert cues.match_sounds([clock], [bang]) == {}
    # a candidate whose best guess is the kind goes before one that only has it as a runner-up
    cry = {"id": "C0001", "type": "sfx", "window": [10.0, 14.0], "families": ["crying"], "keywords": []}
    wind = _sound("S0004", 11.0, "Wind", "바람", confident=False)
    wind["label"]["competing"] = [{"class": "Crying, sobbing"}]
    sob = _sound("S0005", 12.5, "Crying, sobbing", "흐느낌", confident=False, unknown=True)
    assert list(cues.match_sounds([cry], [wind, sob])) == ["S0005"]
    assert list(cues.match_sounds([cry], [wind])) == ["S0004"]
    # music before the first line of the film: the music playing near that line, not the opening titles'
    d2 = Doc()
    s = d2.scene(1)
    d2.act(s, "라디오에서 잔잔한 음악이 흘러나온다.")
    d2.say(s, "좋은 아침.")
    song = cues.build(d2.doc(), [{"id": "L0001", "scene_id": s, "start": 60.0, "end": 62.0, "timing": "asr",
                                  "status": "ok"}], [{"id": s, "start": 60.0, "end": 62.0, "status": "found"}],
                      (0.0, 100.0))[0]
    assert song["type"] == "music" and song["window"] == [0.0, 60.0]

    def mus(iid, onset, seg, event="start"):
        return {"id": iid, "onset": onset, "music": {"event": event, "segment": seg}}

    titles, later, inner = mus("M0001", 2.0, [2.0, 20.0]), mus("M0002", 40.0, [40.0, 70.0]), \
        mus("M0003", 52.0, [40.0, 70.0], "cue")
    hits, _ = cues.match_music([song], [titles, later, inner])
    assert list(hits) == ["M0002"]
    # a narrow window still takes the first music that starts in it
    hits, _ = cues.match_music([{**song, "window": [38.0, 60.0], "window_by": ["line", "line"]}],
                               [titles, later, inner])
    assert list(hits) == ["M0002"]


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


def test_a_sound_far_from_the_cues_scene_is_no_reason(tmp_path):
    d = Doc()
    s1, s2 = d.scene(1), d.scene(2, "지어낸 마당")
    d.say(s1, "나 간다.")
    d.act(s1, "가온, 현관문을 쾅 닫고 나간다.")
    d.act(s2, "멀리서 개 짖는 소리가 들려온다.")
    d.say(s2, "누구야?", speaker="나래")
    dia = [{**_timed("L0001", s1, 10.0, 12.0), "speaker": "가온", "text": "나 간다."},
           {**_timed("L0002", s2, 90.0, 92.0), "speaker": "나래", "text": "누구야?"}]
    scenes = [{"id": s1, "start": 10.0, "end": 12.0, "status": "found", "lines": ["L0001"]},
              {"id": s2, "start": 90.0, "end": 92.0, "status": "found", "lines": ["L0002"]}]
    _, items = _rec(tmp_path, [_sound("S0001", 14.0, "Door", "문소리")], {"S0001": UNSEEN}, script=d.doc(),
                    dialogue=dia, scenes=scenes)
    it = next(x for x in items if x["id"] == "S0001")
    assert it["cue_id"] == "C0001" and it["cue_reason"] and it["rec"] == "include" and not it.get("cue_far")
    told = next(e for e in it["evidence"] if e["kind"] == "대본")
    assert told["window"] == [12.0, 90.0] and told["window_by"] == ["line", "other_scene"]
    dog = next(x for x in items if x["id"] == "C0002")                    # the cue's own candidate says it too
    assert dog["origin"] == "script" and dog["window_by"] == ["other_scene", "line"]
    # the same door 48 s after the line: maybe a sound of the next scene
    _, items = _rec(tmp_path, [_sound("S0001", 60.0, "Door", "문소리")], {"S0001": UNSEEN}, script=d.doc(),
                    dialogue=dia, scenes=scenes)
    it = next(x for x in items if x["id"] == "S0001")
    assert it["cue_id"] == "C0001" and not it["cue_reason"] and it["cue_far"]
    assert it["rec"] == "check" and "대본 단서의 장면에서 먼 소리" in it["reason"]


def test_the_scripts_word_for_the_same_sound_goes_first_when_the_detectors_ends_in_sori(tmp_path):
    """'(한숨)' on a line and a sigh detected there: '[한숨]' first, not '[한숨 소리]' (II.9, should);
    the detector's caption stays a choice. A caption that keeps II.9 ('[천둥]') stays first."""
    sigh = _sound("S0001", 20.5, "Sigh", "한숨 소리", category="vocal")
    _, items = _rec(tmp_path, [sigh], {"S0001": UNSEEN})
    it = next(x for x in items if x["id"] == "S0001")
    assert it["cue_id"] == "C0003" and it["phrase"] == "[한숨]" and it["short"] == "[한숨]"
    assert [o["text"] for o in it["phrases"]][:2] == ["[한숨]", "[한숨 소리]"]
    _, items = _rec(tmp_path, [_sound("S0001", 7.0)], {"S0001": UNSEEN})
    assert next(x for x in items if x["id"] == "S0001")["phrase"] == "[천둥]"


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
