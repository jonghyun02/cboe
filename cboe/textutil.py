# -*- coding: utf-8 -*-
"""Text helpers for 끼워 넣기 (5.9): Netflix Korean II.2 character count and line breaking.

The limits (16 units, 2 lines) and the notation come from ``cboe.style``.

``units(s)``  II.2 count of one displayed line: Hangul syllables and jamo, other full-width/CJK
              characters and symbols such as ♪ count 1; Latin letters, digits, spaces and
              punctuation (of any width: ， ？ 「 too) count 0.5. II.2 names Latin characters,
              spaces and punctuation only; counting digits 0.5 is our rule (10-13 memo 5.1).
``wrap(text)`` at most two lines of at most 16 units (II.2, II.4), broken at eojeol (space)
              boundaries. One line when it fits (II.4 "usually kept to one line"). Otherwise the
              break with no overflow that keeps together the words that stay together
              (``keep_costs``, weighed by ``KEEP_WEIGHT`` above any balance of the two lines,
              audit 2-10) and then whose two lines are closest in length, preferring a break
              after punctuation (General Requirements 4 "after punctuation marks"; a sentence end
              more than a comma), a slightly bottom-heavy shape (I.11), no break inside ( ) or
              [ ] (a speaker label or a sound label), none inside a quotation that closes in the
              text (I.14, I.11's example puts the quote on a line of its own) and none between a
              ♪ and the lyric next to it (taken only when nothing else fits; ``fits`` then says
              no, II.7). Korean particles and endings are written
              without a space, so a space break never separates a noun from its particle; Kiwi
              adds the General Requirements 4 "should not separate" cases that a space does
              separate: a determiner from its noun (관형사 MM, "무슨 | 그림자"), an adnominal from
              its noun (ETM, "예쁜 | 꽃", "새로 산 | 신발"), a verb from its auxiliary or negation (VX,
              "먹어 | 버렸다", "하지 | 않았다"), a negation adverb from its verb ("안 | 돼"), a name
              from a name (NNP NNP) and a number from its counter; a connective or final ending
              (EC, EF) is a good place. Without kiwipiepy the same rules run without these hints.
              A word is cut inside only when it alone is longer than a line. When no two-line
              layout fits, more lines (or an over-long line) come back so that ``fits`` and the
              placement checks see the overflow. The text itself is never changed: joining the
              lines with spaces gives the input with runs of white space collapsed (a word cut in
              two joins without one). With ``tidy`` (a function line -> displayed line, i.e.
              ``style.tidy_line``) the lines come back as they are displayed and it is the
              displayed width that has to fit; where to break is still scored on the text as
              written, so a sentence end stays a preferred place although its period is not shown.
``fits(x)``   whether a text (or a list of lines) stays within the line and line-count limits; for a
              text, as displayed (``tidy``) and with no music note cut off from its lyric.
"""
from __future__ import annotations

import functools
import threading
import unicodedata

from . import style

EPS = 1e-9

_SENTENCE_END = ("…", ".", "?", "!")        # "..", "?!" end with these too
_CLAUSE_END = (",", ";", ":")


def char_units(ch: str) -> float:
    """II.2 width of one character (see module doc)."""
    if ch in "\r\n":
        return 0.0
    o = ord(ch)
    if (0xAC00 <= o <= 0xD7A3 or 0x1100 <= o <= 0x11FF or 0x3130 <= o <= 0x318F
            or 0xA960 <= o <= 0xA97F or 0xD7B0 <= o <= 0xD7FF):
        return 1.0                                   # Hangul syllables and jamo
    if ch.isascii():
        return 0.5                                   # Latin letters, digits, space, punctuation
    cat = unicodedata.category(ch)
    if cat[0] in "PZ":
        return 0.5                                   # punctuation and spaces of any width: …, ·, ‘ ’, ，, ？, 「
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 1.0                                   # CJK and other full-width characters
    if cat == "Nd":
        return 0.5                                   # other digits
    if cat[0] == "L" and "LATIN" in unicodedata.name(ch, ""):
        return 0.5                                   # é, ñ, ...
    return 1.0                                       # symbols (♪ ☎), other scripts


def units(text) -> float:
    """II.2 units of one displayed line (leading and trailing spaces are not displayed)."""
    return _units(str(text or ""))


@functools.lru_cache(maxsize=65536)
def _units(text: str) -> float:
    return float(sum(char_units(c) for c in text.strip()))


def norm(text) -> str:
    """The text as displayed: runs of white space collapsed to one space."""
    return " ".join(str(text or "").split())


def end_bonus(piece: str) -> float:
    """How good a place the end of ``piece`` is for a break: sentence end 5, comma 2, after a
    bracketed label 1 (General Requirements 4 "The line should be broken after punctuation marks")."""
    piece = piece.rstrip()
    if piece.endswith(_SENTENCE_END):
        return 5.0
    if piece.endswith(_CLAUSE_END):
        return 2.0
    if piece.endswith("]"):
        return 1.0
    return 0.0


BY_NOTE = 100.0      # the cost of a break next to a music note: only when nothing else is left
# the cost of a break inside a quotation that closes in the text (I.14 "Quotes should be used at
# the start and end of each subtitle if the quote carries over more than one subtitle" = 인용이
# 자막 여러 개에 걸치면 자막마다 처음과 끝에 따옴표; I.11's example breaks before the quote:
# '내가 말했지' / '‘난 안 그랬어’'): as much as a word kept with its noun (``_hints`` 2), so a
# quotation stays on one line, and in one cell (placement._split), when it can
QUOTE_COST = 2.0
# how much a break that separates words that stay together (``keep_costs``: Kiwi hints, a quotation,
# a label) weighs against the balance of the two lines in ``wrap`` (audit 2-10: a break between a
# determiner and its noun won over a balance 2 units better, the cost was 1-3): two lines of a text that needs two
# differ by less than 16 units, so a cost of 2 (ETM, VX, a name, a number, a quotation) times 8
# outweighs any balance; the cost 1 of a genitive (JKG) is worth 8 units of balance. Not measured:
# the smallest weight that puts every cost of 2 above the balance of any two lines
KEEP_WEIGHT = 8.0
_QUOTES = {"‘": "s", "'": "s", "’": "s", "“": "d", '"': "d", "”": "d"}   # one family: ‘ ’ ', other: “ ” "
_OPENS, _CLOSES = "‘'“\"", "’'”\""
_STRIP_HEAD, _STRIP_TAIL = "-([<", ".,?!…~;:)]>"


def _spaces(text: str) -> list[tuple[int, float]]:
    """Positions of the spaces of ``text`` and the extra cost of a break there: 4 inside ( ) or
    [ ] (a label stays whole when it can), ``BY_NOTE`` between a music note and the lyric next to
    it (II.7 "Add a space between the music note and the preceding or subsequent text" = 음표와
    앞뒤 글 사이에 빈칸: the note stays on the line of its lyric, '[영수] ♪ 기억하렴'). ``wrap`` takes
    such a break only when no other one fits (one long word between the notes); ``fits`` never
    counts a layout with one as fitting, so placement splits that text instead."""
    out, depth = [], 0
    for i, ch in enumerate(text):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        elif ch == " ":
            # after the opening note or before the closing one: an odd number of notes so far
            by_note = style.NOTE in (text[i - 1:i], text[i + 1:i + 2]) and text.count(style.NOTE, 0, i) % 2 == 1
            out.append((i, BY_NOTE if by_note else 4.0 if depth > 0 else 0.0))
    return out


@functools.lru_cache(maxsize=8192)
def _quoted(text: str) -> frozenset:
    """Positions of the spaces of ``text`` inside a quotation: from the word that opens it (a
    quotation mark at its head, past '-', '(' …) to the word that closes it (a mark of the same
    kind at its end, before '.', '?' …): ‘ ’ and ' are one kind, “ ” and " the other. A mark inside
    a word (don't) and a quotation that does not close in ``text`` make no quotation here."""
    if not any(q in text for q in _QUOTES):
        return frozenset()
    out: set = set()
    open_at: dict = {}                               # kind -> the word that opened it
    words, pos, starts = text.split(" "), 0, []
    for w in words:
        starts.append(pos)
        pos += len(w) + 1
    for k, w in enumerate(words):
        head, tail = w.lstrip(_STRIP_HEAD), w.rstrip(_STRIP_TAIL)
        o = head[:1] if head[:1] and head[:1] in _OPENS else None
        c = tail[-1:] if tail[-1:] and tail[-1:] in _CLOSES else None
        if o is not None and _QUOTES[o] not in open_at:
            if not (c is not None and _QUOTES[c] == _QUOTES[o] and len(tail) > 1):
                open_at[_QUOTES[o]] = k              # (else the quotation is inside this word)
        elif c is not None and _QUOTES[c] in open_at:
            k0 = open_at.pop(_QUOTES[c])
            out.update(starts[x] - 1 for x in range(k0 + 1, k + 1))
    return frozenset(out)


def keep_costs(text, kiwi: bool = True) -> dict:
    """The cost of a break at each space of ``text`` (white space collapsed) that separates words
    which stay together: the Kiwi hints of General Requirements 4 ('The line break should not
    separate …', module doc; the good places, endings, are left out) and ``QUOTE_COST`` inside a
    quotation. {position of the space: cost}; a space that is not in it costs nothing."""
    text = norm(text)
    out = {i: h for i, h in (_hints(text) if kiwi else {}).items() if h > 0}
    for i in _quoted(text):
        out[i] = out.get(i, 0.0) + QUOTE_COST
    return out


def note_left_alone(lines) -> bool:
    """Whether a line break of ``lines`` is next to a music note: an opening note at the end of a
    line, or a closing note at the start of the next one ('[세바스찬/영어] ♪' / '가사 ♪')."""
    seen = 0
    for k, line in enumerate(lines):
        s = str(line).strip()
        if k and s.startswith(style.NOTE) and seen % 2 == 1:
            return True                              # the closing note went down to this line
        seen += s.count(style.NOTE)
        if k + 1 < len(lines) and s.endswith(style.NOTE) and seen % 2 == 1:
            return True                              # the opening note stayed up on this line
    return False


# --------------------------------------------------------------------------- Kiwi hints

_PUNCT_TAGS = frozenset({"SF", "SP", "SS", "SSO", "SSC", "SE", "SO", "SW"})
_KIWI = None
_KIWI_FAILED = False
_KIWI_LOCK = threading.Lock()


def _kiwi():
    """One shared Kiwi analyser, loaded on first use; None when kiwipiepy is not available."""
    global _KIWI, _KIWI_FAILED
    if _KIWI is None and not _KIWI_FAILED:
        with _KIWI_LOCK:
            if _KIWI is None and not _KIWI_FAILED:
                try:
                    from kiwipiepy import Kiwi
                    _KIWI = Kiwi()
                except Exception:                    # not installed or model missing: plain rules
                    _KIWI_FAILED = True
    return _KIWI


def kiwi_available() -> bool:
    return _kiwi() is not None


@functools.lru_cache(maxsize=8192)
def _hints(text: str) -> dict:
    """Extra cost of a break at each space of ``text`` from Kiwi tags (module doc). Read only."""
    kiwi = _kiwi()
    if kiwi is None:
        return {}
    try:
        with _KIWI_LOCK:
            toks = list(kiwi.tokenize(text))
    except Exception:
        return {}
    out: dict = {}
    word_start = 0
    for i, _inside in _spaces(text):
        word, w0 = text[word_start:i], word_start
        word_start = i + 1
        before = [t for t in toks if t.start + t.len <= i]
        after = [t for t in toks if t.start > i]
        if not before or not after:
            continue
        a = after[0]
        # the last morpheme of the eojeol before the space, past its punctuation ("갔다." -> 다/EF)
        own = [t for t in before if t.start >= w0 and t.tag not in _PUNCT_TAGS]
        b = own[-1] if own else before[-1]
        cost = 0.0
        if b.tag == "MM":
            cost += 3.0                              # 무슨 | 그림자, 이 | 사람
        elif b.tag == "ETM":
            cost += 2.0                              # 예쁜 | 꽃, 내가 본 | 영화
        elif b.tag == "JKG":
            cost += 1.0                              # 여러분의 | 지식
        elif b.tag in ("EC", "EF"):
            cost -= 1.0                              # 했는데 | ..., 갔다 | ...
        if word in ("안", "못"):
            cost += 3.0                              # 안 | 돼
        if a.tag == "VX":
            cost += 2.0                              # 먹어 | 버렸다, 하지 | 않았다
        if b.tag == "NNP" and a.tag == "NNP":
            cost += 2.0                              # first name | last name
        if b.tag == "SN" and a.tag == "NNB":
            cost += 2.0                              # 3 | 개
        if cost > 0 and word.rstrip("’'”\")]").endswith(_SENTENCE_END + _CLAUSE_END):
            # a word that ends with punctuation holds nothing to the next one (General Requirements
            # 4 "The line should be broken after punctuation marks"). Kiwi reads the 요 of
            # '잠깐만요,' as a determiner (MM); with KEEP_WEIGHT that misreading would leave a
            # speaker label alone on the first line
            cost = 0.0
        if cost:
            out[i] = cost
    return out


# --------------------------------------------------------------------------- wrapping

def _cut(word: str, room: float) -> tuple[str, str]:
    """The longest head of ``word`` that fits ``room`` units, and the rest."""
    acc = 0.0
    for k, ch in enumerate(word):
        acc += char_units(ch)
        if acc > room + EPS:
            return word[:k], word[k:]
    return word, ""


def _greedy(text: str, max_units: float, hold: int = 0) -> list[str]:
    """Fill lines word by word; a word longer than a line is cut (only then). The text up to
    ``hold`` is one word (``wrap``)."""
    lines: list[str] = []
    cur = ""
    words = text.split(" ")
    if hold > 0:
        k = text[:hold].count(" ") + 1
        words = [" ".join(words[:k])] + words[k:]
    for word in words:
        cand = f"{cur} {word}" if cur else word
        if units(cand) <= max_units + EPS:
            cur = cand
            continue
        if units(word) <= max_units + EPS:
            lines.append(cur)
            cur = word
            continue
        rest = word
        if cur:
            head, rest = _cut(rest, max_units - units(cur) - 0.5)
            lines.append(f"{cur} {head}" if head else cur)
            cur = ""
        while units(rest) > max_units + EPS:
            head, rest = _cut(rest, max_units)
            if not head:                             # a single character wider than a line
                head, rest = rest[:1], rest[1:]
            lines.append(head)
        cur = rest
    if cur:
        lines.append(cur)
    return lines


# 같은 입력의 결과를 기억해 둔다 (감사 6-08: 작가가 하나 고칠 때마다 배치를 다시 하는데, 바뀌지 않은 줄의
# 글도 매번 줄바꿈을 다시 계산했다). ``wrap`` 의 결과는 그 인자(빈칸을 줄인 글, 한도, kiwi, tidy, hold)와
# ``_hints``·``_quoted``·``_units`` 의 결과로만 정해지고 그 셋은 이미 글마다 기억된다. 그래서 인자를 열쇠로
# 기억해 둔 결과는 다시 계산한 것과 같다. ``MEMO = False`` 이면 기억하지 않고 늘 계산한다(시험이 두 길을
# 견준다). 이 모듈의 상수나 ``style`` 의 표기를 실행 중에 바꾸면 ``clear_memo()`` 를 부른다
MEMO = True
_WRAP_MEMO: dict = {}
_WRAP_MAX = 65536                      # 열쇠 수가 넘으면 비우고 다시 모은다 (2시간 영화 한 번 배치에 약 3만)


def clear_memo() -> None:
    """Forget every result this module remembers (``wrap`` and the caches of ``_units``,
    ``_quoted``, ``_hints``)."""
    _WRAP_MEMO.clear()
    _units.cache_clear()
    _quoted.cache_clear()
    _hints.cache_clear()


def wrap(text, max_units: float = style.MAX_UNITS, max_lines: int = style.MAX_LINES, kiwi: bool = True,
         tidy=None, hold: int = 0) -> list[str]:
    """Lines of ``text`` (module doc). ``kiwi=False`` skips the Kiwi hints (same feasibility);
    ``tidy`` makes each line as it is displayed (lines that become empty are dropped); ``hold``:
    no break at a space before that position of the text with its white space collapsed (a
    speaker label kept with the first word; the position is passed, not a marker character, so
    no character of the dialogue can take its place). The result of the same arguments is
    remembered (``MEMO``; only for ``tidy`` None or ``style.tidy_line``)."""
    text = norm(text)
    if not MEMO or (tidy is not None and tidy is not style.tidy_line):
        return _wrap(text, max_units, max_lines, kiwi, tidy, hold)
    # the types too: 16 and 16.0 are one key and give the same lines, but a hold of 1.0 is no slice
    key = (text, type(max_units), max_units, type(max_lines), max_lines, bool(kiwi), tidy, type(hold), hold)
    try:
        got = _WRAP_MEMO.get(key)
    except TypeError:                                # an argument that cannot be a key: no memo
        return _wrap(text, max_units, max_lines, kiwi, tidy, hold)
    if got is None:
        got = tuple(_wrap(text, max_units, max_lines, kiwi, tidy, hold))
        if len(_WRAP_MEMO) >= _WRAP_MAX:
            _WRAP_MEMO.clear()
        _WRAP_MEMO[key] = got
    return list(got)


def _wrap(text: str, max_units, max_lines, kiwi, tidy, hold) -> list[str]:
    """``wrap`` of a text whose white space is collapsed already (``norm``)."""
    if not text:
        return []
    shown = tidy or (lambda s: s)
    whole = shown(text)
    if units(whole) <= max_units + EPS:
        return [whole] if whole else []
    if max_lines >= 2:
        hints = _hints(text) if kiwi else {}
        quoted = _quoted(text)
        best = None
        for i, cost in _spaces(text):
            if i < hold:
                continue
            a, b = shown(text[:i]), shown(text[i + 1:])
            ua, ub = units(a), units(b)
            if ua > max_units + EPS or ub > max_units + EPS:
                continue
            # words that stay together outweigh the balance of the lines (KEEP_WEIGHT, audit 2-10)
            h = hints.get(i, 0.0)
            keep = cost + max(h, 0.0) + (QUOTE_COST if i in quoted else 0.0)
            score = abs(ua - ub) + (0.5 if ua > ub else 0.0) - end_bonus(text[:i]) + min(h, 0.0) + KEEP_WEIGHT * keep
            key = (round(score, 6), i)
            if best is None or key < best[0]:
                best = (key, a, b)
        if best is not None:
            return [x for x in best[1:] if x]
    return [x for x in (shown(y) for y in _greedy(text, max_units, hold)) if x]


def fits(text, max_units: float = style.MAX_UNITS, max_lines: int = style.MAX_LINES, tidy=None) -> bool:
    """Whether ``text`` (a string, wrapped first, or a list of lines) stays within the limits.
    With ``tidy`` it is the displayed lines that count."""
    def within(lines) -> bool:
        return len(lines) <= max_lines and all(units(x) <= max_units + EPS for x in lines)

    if not isinstance(text, str):
        return within([str(x) for x in text])
    # the displayed lines decide: tidying mostly narrows a line, but it can widen one ('가자♪'
    # is shown '가자 ♪'), so the written width is no shortcut. A layout that leaves a note on
    # the other side of the break from its lyric does not fit (II.7, ``_spaces``)
    lines = wrap(text, max_units, max_lines, kiwi=False, tidy=tidy)
    return within(lines) and not note_left_alone(lines)


def line_units(lines) -> list[float]:
    return [units(x) for x in lines]
