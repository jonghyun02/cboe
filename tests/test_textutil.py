# -*- coding: utf-8 -*-
"""II.2 character count and line breaking (cboe.textutil)."""
import pytest

from cboe import textutil
from cboe.textutil import char_units, end_bonus, fits, norm, units, wrap


# --------------------------------------------------------------------------- counting

@pytest.mark.parametrize("text, expected", [
    ("[책 떨어지는 소리]", 9.0),            # memo 5.2 example: 9 units -> 9/14 + 1 = 1.64 s
    ("[쿵, 책 떨어지는 소리]", 11.0),        # memo 7 fix: 11 units -> 1.79 s
    ("[BJ 옴뫄] 으아아아아", 10.0),          # [ ] count like ( ): 0.5 each
    ("- [BJ 옴뫄] 으아아아아", 11.0),        # the hyphen and its space count too
    ("[무서운 음악]", 6.5),                 # a music label
    ("♪ 달빛 아래 ♪", 7.5),                  # a note counts 1, the space next to it 0.5
    ("[영희/속마음/영어] 설마!", 12.0),
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
    for p in "，？！「」。、":                        # II.2 "punctuation count as 0.5": whatever its width
        assert char_units(p) == 0.5, p
    assert char_units("　") == 0.5                  # the ideographic space is a space


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
    text = "[BJ 옴뫄] 하 하이씨 진짜 장난치지 마세요"     # 20 units
    lines = wrap(text)
    _check_lines(text, lines)
    assert len(lines) == 2
    assert abs(units(lines[0]) - units(lines[1])) <= 1.0


def test_break_after_punctuation_is_preferred():
    text = "[BJ 옴뫄] 근데 무슨 그림자? 호이씨 허 어떡해"
    lines = wrap(text)
    _check_lines(text, lines)
    assert lines[0].endswith("그림자?")


def test_sentence_end_beats_a_slightly_better_balance():
    text = "이건 정말 아니었어. 다시 가 보자 얼른"
    lines = wrap(text)
    _check_lines(text, lines)
    assert lines[0].endswith("아니었어.")


def test_never_breaks_inside_brackets_or_names_when_it_can():
    text = "[BJ 옴뫄] 하 하이씨 진짜 장난치지 마세요"
    assert not wrap(text)[0].endswith("[BJ")
    assert not wrap("(BJ 옴뫄) 하 하이씨 진짜 장난치지 마세요")[0].endswith("(BJ")   # ( ) of an older file too
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


def test_wrap_with_tidy_measures_and_returns_the_displayed_lines():
    from cboe.style import tidy_line
    # where to break is decided on the text as written (the sentence end), the lines come back tidied
    assert wrap("이건 정말 아니었어. 다시 가 보자 얼른", tidy=tidy_line) == ["이건 정말 아니었어", "다시 가 보자 얼른"]
    assert wrap("사랑해. 그게 내가 하고 싶은 말이었어", tidy=tidy_line) == ["사랑해, 그게 내가", "하고 싶은 말이었어"]
    word = "가" * 16
    assert wrap(word + ".") == [word, "."]                     # as written: 16.5 units, the period falls off
    assert wrap(word + ".", tidy=tidy_line) == [word]          # as displayed: one line of 16
    assert fits("가" * 16 + " " + "나" * 16 + ".", tidy=tidy_line) and not fits("가" * 16 + " " + "나" * 16 + ".")
    assert wrap(".", tidy=tidy_line) == [] and wrap("응.", tidy=tidy_line) == ["응"]
    text = "오늘은 날씨가 정말 좋아서 산책을 나갔어"            # nothing to tidy: the same lines as without
    assert wrap(text, tidy=tidy_line) == wrap(text)


def test_a_note_stays_with_its_lyric():
    lines = wrap("[속마음/영어] ♪ 그래서 말이야 ♪")              # 16.5 units: the break is not next to a note
    assert lines == ["[속마음/영어]", "♪ 그래서 말이야 ♪"]
    for text in ["♪ 달빛 아래 걷던 그 길을 오늘도 ♪", "[영수] ♪ 달빛 아래 걷던 그 길을 ♪",
                 "♪ 가 나 다 라 마 바 사 아 자 차 ♪", "- [영희] ♪ 별이 뜨면 나는 노래해 ♪"]:
        lines = wrap(text)
        _check_lines(text, lines)
        assert len(lines) == 2
        assert not lines[0].endswith("♪") and lines[1] != "♪"      # neither note is left alone at the break
    # a single long word between the notes: nowhere else to break, so next to a note after all
    assert wrap("♪ " + "가" * 14 + " ♪") == ["♪", "가" * 14 + " ♪"]


def test_kiwi_off_has_same_feasibility():
    for text in ["[BJ 옴뫄] 이.. 일단 제가 여기 자.. 잠입을 했는데", "가나다라마바사 " * 6,
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


@pytest.mark.skipif(not textutil.kiwi_available(), reason="kiwipiepy not installed")
def test_words_that_stay_together_win_over_the_balance_of_the_lines():
    # audit 2-10: a Kiwi cost of 2 lost to a balance 2 units better ('… 새로 산' / '신발을 …', 9.5 and
    # 10.5 units): General Requirements 4 "should not separate … a noun from an adjective"
    text = "어제 친구가 새로 산 신발을 자랑하더라 진짜"            # made up; 20.5 units
    assert wrap(text, kiwi=False)[0].endswith("산")              # the balance alone
    lines = wrap(text)
    _check_lines(text, lines)
    assert not lines[0].endswith("산")
    # a word that ends with punctuation holds nothing to the next one: Kiwi reads the 요 of '잠깐만요,' as
    # a determiner, and that no longer costs the break after the comma
    assert textutil.keep_costs("잠깐만요, 지금 그러니까") == {}
    assert wrap("[하늘/영어] 잠깐만요, 지금 그러니까")[0] == "[하늘/영어] 잠깐만요,"


def test_a_quotation_stays_on_one_line_when_it_can():
    # I.11's example breaks before the quote ('내가 말했지' / '‘난 안 그랬어’'); I.14: a quote over two
    # subtitles is quoted again in each, so one line (and one cell) is kept when that fits
    text = "아까 엄마가 '밥 먹고 가라' 하셨는데 그냥 나왔어"         # made up; 21.5 units
    for kiwi in (True, False):
        lines = wrap(text, kiwi=kiwi)
        _check_lines(text, lines)
        assert lines[0].endswith("가라'") or lines[1].startswith("'밥"), lines
    text = "그 사람이 “내일 다시 오겠다” 하고 갔어"                  # “ ” too
    lines = wrap(text)
    _check_lines(text, lines)
    assert "“" in lines[0] and "”" in lines[0] or "“" in lines[1] and "”" in lines[1], lines
    # where it cannot fit one line, it is broken as any other text
    long_quote = "'오늘은 정말로 아무 데도 가지 말고 집에만 있어'"
    assert len(wrap(long_quote)) == 2 and " ".join(wrap(long_quote)) == long_quote


def test_quoted_spaces_are_those_of_a_closed_quotation():
    def spaces(text):
        return sorted(textutil._quoted(text))
    assert spaces("가 '나 다' 라") == [4]                       # the space between 나 and 다
    assert spaces("가 ‘나 다’ 라 “마 바” 사") == [4, 12]
    assert spaces("가 '나다' 라") == []                         # a quotation inside one word
    assert spaces("가 '나 다 라") == []                         # not closed in the text: no quotation here
    assert spaces("I don't know what you mean") == []           # a mark inside a word
    assert spaces("(가 '나 다'.) 라") == [5]                      # past brackets and punctuation
    assert textutil.keep_costs("가 '나 다' 라", kiwi=False) == {4: textutil.QUOTE_COST}


def test_hold_keeps_the_head_of_a_text_on_one_line():
    # 4th review: placement used to mark the spaces that must not break with U+2027, a character a
    # line can hold itself (남‧북); the position is passed instead
    text = "[세바스찬/영어] 지금 남‧북 회담이 열렸어 오늘 아침"
    head = len("[세바스찬/영어] 지금")
    for kiwi in (False, True):
        lines = wrap(text, kiwi=kiwi, hold=head)
        assert lines[0].startswith("[세바스찬/영어] 지금") and " ".join(lines) == text
    assert wrap("가나다라 " * 3 + "마바사아자차카타파하가나다라", hold=14) == ["가나다라 가나다라 가나다라", "마바사아자차카타파하가나다라"]
    assert wrap("가나다라마바사아 자차 카타파하가나다라", kiwi=False) == ["가나다라마바사아", "자차 카타파하가나다라"]
    assert wrap("가나다라마바사아 자차 카타파하가나다라", kiwi=False, hold=9) == ["가나다라마바사아 자차", "카타파하가나다라"]
