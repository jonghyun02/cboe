# -*- coding: utf-8 -*-
"""인물과 화자 표시 (cboe.people): when a name is told, labels before and after it, numbers per
scene, speaker scopes by person. All lines are made up."""
import pytest

from cboe import people


def ln(lid, person, text, scene="SC001", show=True, voice=None):
    return {"id": lid, "person": person, "text": text, "scene_id": scene, "show": show, "voice": voice}


# ------------------------------------------------------------------ is the name said?
@pytest.mark.parametrize("text", ["하늘아 어디 가", "하늘 씨 왔어요?", "하늘이가 그랬어", "이거 하늘한테 줘",
                                  "하늘! 빨리 와", "하늘이랑 같이 가", "저기, 하늘 선배"])
def test_a_name_said_as_a_word(text):
    assert people.mentions(text, "하늘")


@pytest.mark.parametrize("text", ["하늘색 옷 입었네", "밤하늘이 예쁘다", "오늘 하늘다람쥐 봤어"])
def test_letters_of_the_name_inside_another_word_are_not_the_name(text):
    assert not people.mentions(text, "하늘")


def test_a_role_word_inside_a_name_or_a_longer_word_tells_no_name():
    # the old check took any two-letter piece: '기사님' told the name '황기사', '운전기사' told '기사'
    assert not people.mentions("기사님 여기서 세워 주세요", "황기사")
    assert not people.mentions("운전기사가 먼저 왔어", "기사")
    assert people.mentions("황기사님 오셨어요", "황기사")
    assert people.mentions("김경위 어디 있어", "김 경위")            # written with or without the space


@pytest.mark.parametrize("text", ["하늘이라는 애 알아?", "걔 이름이 하늘이래", "하늘이에요 제 동생", "하늘보다 내가 낫지",
                                  "하늘에게서 연락 왔어", "하늘이나 불러", "하늘이었어", "하늘이란 사람"])
def test_a_name_said_in_a_sentence_with_a_copula_or_a_particle(text):
    assert people.mentions(text, "하늘")


@pytest.mark.parametrize("text", ["민수라는 애 알아?", "민수래", "민수였어", "민수나 불러", "민수예요"])
def test_the_same_forms_after_a_name_that_ends_in_a_vowel(text):
    assert people.mentions(text, "민수")


@pytest.mark.parametrize("text, called", [("그럴 수도 있지", False), ("할 수가 없어", False), ("수다 떨지 마", False),
                                          ("수야 이리 와", True), ("수 씨 계세요?", True), ("수님 오셨어요", True)])
def test_a_one_letter_name_counts_only_when_it_is_called(text, called):
    assert people.mentions(text, "수") is called
    assert not people.introduces("수다 떨지 마", "수") and people.introduces("저는 수입니다", "수")


def test_a_short_latin_name_is_found_as_a_word():
    assert people.mentions("Tom, come here", "Tom") and not people.mentions("Tomorrow then", "Tom")
    assert people.keys("A") == [] and people.keys("Tom") == ["Tom"]


@pytest.mark.parametrize("text", ["저는 하늘입니다", "하늘이에요", "하늘이요", "제 이름은 하늘", "하늘이라고 해요"])
def test_a_person_telling_their_own_name(text):
    assert people.introduces(text, "하늘")


def test_revealed_at_counts_another_persons_line_or_a_self_introduction():
    lines = [ln("L1", "하늘", "하늘 말고 나 혼자 갈래"),          # own line, no introduction
             ln("L2", "바다", "뭐라고?"),
             ln("L3", "바다", "하늘아 같이 가자"),
             ln("L4", "하늘", "싫어")]
    r = people.revealed_at(lines, "하늘")
    assert (r["line"], r["index"], r["by"], r["how"]) == ("L3", 2, "바다", "called")
    intro = [ln("L1", "하늘", "안녕하세요 저는 하늘입니다"), ln("L2", "바다", "하늘아")]
    assert people.revealed_at(intro, "하늘")["how"] == "self"
    assert people.revealed_at(lines[:2], "하늘") is None
    assert people.revealed_at(lines, None) is None
    joint = [ln("L1", "하늘/바다", "하늘아"), ln("L2", "구름", "바다야")]      # said by both of them: not by 'another'
    assert people.revealed_at(joint, "하늘") is None and people.revealed_at(joint, "바다")["line"] == "L2"


# ------------------------------------------------------------------ table
def test_table_from_the_script_people_then_the_other_names():
    t = people.table([{"id": "P01", "name": "하늘", "cues": ["하늘", "하늘(V.O)"]}], ["바다", "하늘", "구름/바다", "경찰2"])
    assert [(p["id"], p["name"], p["role"]) for p in t] == [
        ("P01", "하늘", False), ("N:바다", "바다", False), ("N:구름", "구름", False), ("N:경찰2", "경찰2", True)]
    assert t[0]["cues"] == ["하늘", "하늘(V.O)"]


# ------------------------------------------------------------------ labels
def story():
    return [ln("L1", "하늘", "누구세요?"),
            ln("L2", "바다", "저 옆집 사람이에요"),
            ln("L3", "하늘", "아 그래요?"),
            ln("L4", "바다", "하늘 씨 맞죠?"),                    # the name is told here
            ln("L5", "하늘", "네 맞아요"),
            ln("L6", "하늘", "그런데 왜요?", scene="SC002")]


def test_an_undecided_person_keeps_the_name_and_is_listed_when_it_shows_early():
    d = people.display(story())
    assert [d["labels"][f"L{k}"] for k in range(1, 7)] == ["하늘", "바다", "하늘", "바다", "하늘", "하늘"]
    sky = d["people"]["하늘"]
    assert sky["mode"] is None and sky["early"] == {"line": "L1", "told": "L4"} and sky["told"]["line"] == "L4"
    assert sky["from_line"] == "L5"
    sea = d["people"]["바다"]
    assert sea["early"] == {"line": "L2", "told": None}                    # never called by name: '불리지 않음'
    hidden = [dict(x, show=False) if x["id"] == "L1" else x for x in story()]
    assert people.display(hidden)["people"]["하늘"]["early"]["line"] == "L3"   # only a shown label counts


def test_a_generic_label_until_the_name_is_told():
    d = people.display(story(), {"하늘": {"mode": "generic", "before": "여자"}})
    assert [d["labels"][f"L{k}"] for k in (1, 3, 5, 6)] == ["여자", "여자", "하늘", "하늘"]
    assert d["generic"]["L1"] and not d["generic"]["L5"]
    p = d["people"]["하늘"]
    assert p["early"] is None and p["from_line"] == "L5" and p["mode"] == "generic"
    never = people.display(story(), {"하늘": {"mode": "generic", "before": "여자", "reveal": "never"}})
    assert never["labels"]["L6"] == "여자"
    later = people.display(story(), {"하늘": {"mode": "generic", "before": "여자", "reveal": "line", "reveal_line": "L6"}})
    assert later["labels"]["L5"] == "여자" and later["labels"]["L6"] == "하늘"    # a name shown on screen, say
    named = people.display(story(), {"바다": {"mode": "name"}})
    assert named["people"]["바다"]["early"] is None and named["labels"]["L2"] == "바다"
    blank = people.display(story(), {"하늘": {"mode": "generic", "before": "  "}})   # no label given: undecided
    assert blank["people"]["하늘"]["mode"] is None and blank["labels"]["L1"] == "하늘"


def test_role_names_are_names_by_default():
    d = people.display([ln("L1", "경찰1", "꼼짝 마"), ln("L2", "직원", "어서 오세요")])
    assert d["people"]["경찰1"]["role"] and d["people"]["경찰1"]["early"] is None
    assert d["people"]["직원"]["early"] == {"line": "L2", "told": None}         # the writer says 'name' for it


def test_two_people_under_one_generic_label_are_numbered_per_scene():
    lines = [ln("L1", "갑", "거기 누구야", "SC001"), ln("L2", "을", "나야", "SC001"), ln("L3", "갑", "왜 왔어", "SC001"),
             ln("L4", "을", "그냥", "SC002"),
             ln("L5", "을", "또 왔어", "SC003"), ln("L6", "갑", "어", "SC003")]
    dec = {"갑": {"mode": "generic", "before": "남자"}, "을": {"mode": "generic", "before": "남자"}}
    d = people.display(lines, dec)
    assert [d["labels"][x["id"]] for x in lines] == ["남자1", "남자2", "남자1", "남자", "남자1", "남자2"]
    flat = people.display([dict(x, scene_id=None) for x in lines], dec)        # no scenes: one count for the film
    assert [flat["labels"][x["id"]] for x in lines] == ["남자1", "남자2", "남자1", "남자2", "남자2", "남자1"]
    one_shown = [dict(x, show=x["person"] == "갑") for x in lines[:3]]          # only one of them shows the label
    assert people.display(one_shown, dec)["labels"]["L1"] == "남자"


def test_a_speaker_the_script_calls_man_and_a_person_labelled_man_are_numbered():
    # the script's own speaker '남자' (shown as it is) and 가람 shown as '남자' until named, one scene
    lines = [ln("L1", "남자", "거기 서 봐요", "SC001"), ln("L2", "가람", "왜요 무슨 일인데요", "SC001"),
             ln("L3", "남자", "이리 좀 와 봐요", "SC001"), ln("L4", "남자", "또 보네요", "SC002")]
    for dec in ({"가람": {"mode": "generic", "before": "남자"}},
                {"가람": {"mode": "generic", "before": "남자"}, "남자": {"mode": "name"}}):
        d = people.display(lines, dec)
        assert [d["labels"][x["id"]] for x in lines] == ["남자1", "남자2", "남자1", "남자"]


def test_a_number_another_label_of_the_scene_shows_is_skipped():
    # '남자1' is a role name of the script; two more people the writer labels '남자' in that scene
    lines = [ln("L1", "남자1", "어서 오세요"), ln("L2", "가람", "안녕하세요"), ln("L3", "나래", "반가워요")]
    dec = {n: {"mode": "generic", "before": "남자"} for n in ("가람", "나래")}
    d = people.display(lines, dec)
    assert [d["labels"][x["id"]] for x in lines] == ["남자1", "남자2", "남자3"]


def test_two_people_speaking_together():
    d = people.display([ln("L1", "하늘/바다", "안녕하세요")], {"바다": {"mode": "generic", "before": "남자"}})
    assert d["labels"]["L1"] == "하늘/남자"


# ------------------------------------------------------------------ scopes
def scope_story():
    return [ln("L1", "가람", "a", "SC001", voice=1), ln("L2", "나래", "b", "SC001", voice=2),
            ln("L3", "가람", "c", "SC001", voice=1), ln("L4", "가람", "d", "SC002", voice=1),
            ln("L5", "가람", "e", "SC002", voice=3)]


@pytest.mark.parametrize("which, want", [
    ("line", ["L3"]), ("from_here", ["L3", "L4", "L5"]), ("before", ["L1"]), ("scene", ["L1", "L3"]),
    ("person", ["L1", "L3", "L4", "L5"]), ("speaker", ["L1", "L3", "L4", "L5"]), ("voice", ["L1", "L3", "L4"])])
def test_scope_lines_by_person(which, want):
    assert people.scope_lines(scope_story(), "L3", which) == want


def test_scope_never_reaches_another_person_under_the_same_label():
    # 감사 3-m2: both are shown as '남자' for now; the scope is about the person, not the label
    lines = scope_story()
    assert "L2" not in people.scope_lines(lines, "L1", "person")
    no_voice = [dict(x, voice=None) for x in lines]
    assert people.scope_lines(no_voice, "L1", "voice") == ["L1"]
    with pytest.raises(ValueError):
        people.scope_lines(lines, "L1", "everyone")
    with pytest.raises(KeyError):
        people.scope_lines(lines, "L9", "line")


# ------------------------------------------------------------------ 4차: 흔한 낱말과 겹치는 이름, 역할, 번호
@pytest.mark.parametrize("text, name", [("하나였어 그때는", "하나"), ("영화보다 책이 좋아", "영화"),
                                        ("사랑이라는 게 뭔데", "사랑"), ("하늘이나 보자", "하늘"),
                                        ("바다보다 넓어", "바다"), ("보람이었어 그 일이", "보람")])
def test_a_common_word_said_with_a_copula_or_a_comparison_tells_no_name(text, name):
    assert not people.mentions(text, name, weak=False)
    assert people.revealed_at([ln("L1", "구름", text), ln("L2", name, "응")], name) is None


def test_such_a_line_tells_the_name_once_another_line_calls_it():
    lines = [ln("L1", "구름", "하늘이나 불러"), ln("L2", "바다", "뭐?"), ln("L3", "바다", "하늘아 이리 와")]
    assert people.revealed_at(lines, "하늘")["line"] == "L1"
    assert people.revealed_at(lines[:2], "하늘") is None
    intro = [ln("L1", "구름", "하늘이라는 애 알아?"), ln("L2", "하늘", "저는 하늘입니다")]
    assert people.revealed_at(intro, "하늘")["line"] == "L1"


@pytest.mark.parametrize("text, name", [("하나라도 남겨 둬", "하나"), ("날씨가 좋다", "날"), ("솜씨는 좋네", "솜")])
def test_words_that_only_look_like_a_name_with_a_tail(text, name):
    assert not people.mentions(text, name)


def test_a_speaker_the_script_calls_by_a_generic_label_is_a_role():
    assert people.is_role("남자") and people.is_role("여자") and people.is_role("노인", {"노인"})
    assert not people.is_role("하늘")
    d = people.display([ln("L1", "남자", "거기 서 봐요"), ln("L2", "하늘", "그 남자가 왜 저래"),
                        ln("L3", "남자", "잠깐 얘기 좀 해요")])
    assert d["people"]["남자"]["early"] is None and d["people"]["남자"]["role"]
    t = people.table([], ["남자", "가람", "노인"], generic={"노인"})
    assert [p["role"] for p in t] == [True, False, True]


def test_a_role_name_with_a_number_and_one_person_under_the_same_label_are_both_numbered():
    lines = [ln("L1", "남자1", "여기 누구 없어요"), ln("L2", "가람", "저쪽으로 가 봐요"),
             ln("L3", "가람", "또 보네요", "SC002")]
    d = people.display(lines, {"가람": {"mode": "generic", "before": "남자"}})
    assert [d["labels"][x["id"]] for x in lines] == ["남자1", "남자2", "남자"]       # alone in the next scene


def test_a_line_nobody_is_named_for_has_no_same_person():
    lines = [ln("L1", None, "a"), ln("L2", "가람", "b"), ln("L3", None, "c")]
    for which in ("from_here", "before", "scene", "person"):
        assert people.scope_lines(lines, "L1", which) == ["L1"]
