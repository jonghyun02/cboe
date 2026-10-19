# -*- coding: utf-8 -*-
"""II.2 character count and line breaking (cboe.textutil)."""
import pytest

from cboe import textutil
from cboe.textutil import char_units, end_bonus, fits, norm, units, wrap


# --------------------------------------------------------------------------- counting

@pytest.mark.parametrize("text, expected", [
    ("[책 떨어지는 소리]", 9.0),            # memo 5.2 example: 9 units -> 9/14 + 1 = 1.64 s
    ("[쿵, 책 떨어지는 소리]", 11.0),        # memo 7 fix: 11 units -> 1.79 s
    ("(BJ 옴뫄) 으아아아아", 10.0),
    ("- (BJ 옴뫄) 으아아아아", 11.0),        # the hyphen and its space count too
    ("♬ 무서운 음악", 7.0),                 # ♬ counts 1
    ("나도야!", 3.5),
    ("", 0.0),
])
def test_units_examples(text, expected):
    assert units(text) == expected


def test_char_units_classes():
    assert char_units("가") == 1.0                 # Hangul syllable
    assert char_units("ㅋ") == 1.0                 # compatibility jamo
    assert char_units("ᄀ") == 1.0             # conjoining jamo
    assert char_units("漢") == 1.0                 # CJK
    assert char_units("Ａ") == 1.0                 # full width
    assert char_units("a") == 0.5 and char_units("Z") == 0.5
    assert char_units("7") == 0.5                  # digits 0.5 (our rule)
    assert char_units(" ") == 0.5
    for p in ".,?!'\"()[]-…·‘’":
        assert char_units(p) == 0.5, p
    assert char_units("é") == 0.5                  # Latin with a diacritic
    assert char_units("♬") == 1.0 and char_units("♪") == 1.0 and char_units("☎") == 1.0
    assert char_units("\n") == 0.0


def test_units_ignores_outer_spaces_and_counts_inner_ones():
    assert units("  가 나  ") == 2.5
    assert units("BJ 123") == 3.0


def test_norm_and_end_bonus():
    assert norm("  가   나\t다 ") == "가 나 다"
    assert end_bonus("그림자?") > end_bonus("바,") > end_bonus("[쿵]") > end_bonus("그림자") == 0.0
    assert end_bonus("자..") == end_bonus("갔다.") == end_bonus("뭐…")


# --------------------------------------------------------------------------- wrapping

def _check_lines(text, lines, max_units=16.0):
    assert " ".join(lines) == norm(text)          # only line breaks are inserted
    assert all(units(x) <= max_units for x in lines)


def test_one_line_when_it_fits():
    assert wrap("내 얼굴 나오잖아") == ["내 얼굴 나오잖아"]
    assert wrap("가" * 16) == ["가" * 16]
    assert wrap("") == [] and wrap(None) == []


def test_two_balanced_lines():
    text = "(BJ 옴뫄) 하 하이씨 진짜 장난치지 마세요"     # 20 units
    lines = wrap(text)
    _check_lines(text, lines)
    assert len(lines) == 2
    assert abs(units(lines[0]) - units(lines[1])) <= 1.0


def test_break_after_punctuation_is_preferred():
    text = "(BJ 옴뫄) 근데 무슨 그림자? 호이씨 허 어떡해"
    lines = wrap(text)
    _check_lines(text, lines)
    assert lines[0].endswith("그림자?")


def test_sentence_end_beats_a_slightly_better_balance():
    text = "이건 정말 아니었어. 다시 가 보자 얼른"
    lines = wrap(text)
    _check_lines(text, lines)
    assert lines[0].endswith("아니었어.")


def test_never_breaks_inside_brackets_or_names_when_it_can():
    text = "(BJ 옴뫄) 하 하이씨 진짜 장난치지 마세요"
    assert not wrap(text)[0].endswith("(BJ")
    text = "[쿵, 책 떨어지는 소리] 이게 무슨 일이야"
    lines = wrap(text)
    _check_lines(text, lines)
    assert lines[0] == "[쿵, 책 떨어지는 소리]"


def test_long_label_breaks_inside_brackets_only_when_needed():
    text = "[쿵, 책 떨어지는 소리가 아주 크게 울린다]"      # 20 units, no space outside [ ]
    lines = wrap(text)
    _check_lines(text, lines)
    assert len(lines) == 2


def test_word_is_cut_only_when_longer_than_a_line():
    word = "아" * 20
    lines = wrap(word)
    assert "".join(lines) == word
    assert [units(x) for x in lines] == [16.0, 4.0]
    assert fits(word)                              # 20 units still fit in two lines
    lines = wrap("응 " + word)
    assert "".join(lines).replace(" ", "") == "응" + word
    assert all(units(x) <= 16 for x in lines)


def test_overflow_is_visible():
    text = "가나다라마바사 " * 6                    # 45.5 units: no two-line layout
    lines = wrap(text)
    assert len(lines) > 2
    assert " ".join(lines) == norm(text)
    assert not fits(text)
    assert fits("가나다라마바사 아자차카타파하")
    assert fits(["가" * 16, "나"]) and not fits(["가" * 17]) and not fits(["가", "나", "다"])


def test_kiwi_off_has_same_feasibility():
    for text in ["(BJ 옴뫄) 이.. 일단 제가 여기 자.. 잠입을 했는데", "가나다라마바사 " * 6,
                 "오늘 우리가 같이 본 무슨 영화가 그렇게 재미있었다고 그래"]:
        a, b = wrap(text), wrap(text, kiwi=False)
        assert (len(a) <= 2) == (len(b) <= 2)
        assert " ".join(a) == " ".join(b) == norm(text)


@pytest.mark.skipif(not textutil.kiwi_available(), reason="kiwipiepy not installed")
def test_kiwi_keeps_determiner_with_its_noun():
    text = "오늘 우리가 같이 본 무슨 영화가 그렇게 재미있었다고 그래"
    plain = wrap(text, kiwi=False)
    assert plain[0].endswith("무슨")               # the balanced break separates 무슨 | 영화가
    lines = wrap(text)
    _check_lines(text, lines)
    assert not lines[0].endswith("무슨")


@pytest.mark.skipif(not textutil.kiwi_available(), reason="kiwipiepy not installed")
def test_kiwi_hints_cover_auxiliary_and_negation():
    hints = textutil._hints("그건 내가 하지 않았다고 했잖아")
    i = "그건 내가 하지 않았다고 했잖아".index(" 않았다고")
    assert hints.get(i, 0) > 0                     # 하지 | 않았다고 (auxiliary / negation)
    hints = textutil._hints("나는 안 돼 정말로")
    assert hints.get("나는 안 돼 정말로".index(" 돼"), 0) > 0   # 안 | 돼
