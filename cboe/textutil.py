# -*- coding: utf-8 -*-
"""Text helpers for 끼워 넣기 (5.9): Netflix Korean II.2 character count and line breaking.

``units(s)``  II.2 count of one displayed line: Hangul syllables and jamo, other full-width/CJK
              characters and symbols such as ♬ count 1; Latin letters, digits, spaces and
              punctuation count 0.5. II.2 names Latin characters, spaces and punctuation only;
              counting digits 0.5 is our rule (10-13 memo 5.1).
``wrap(text)`` at most two lines of at most 16 units (II.2, II.4), broken at eojeol (space)
              boundaries. One line when it fits (II.4 "usually kept to one line"). Otherwise the
              break with no overflow whose two lines are closest in length, preferring a break
              after punctuation (General Requirements 4 "after punctuation marks"; a sentence end
              more than a comma), a slightly bottom-heavy shape (I.11) and no break inside ( ) or
              [ ] (a speaker name or a sound label). Korean particles and endings are written
              without a space, so a space break never separates a noun from its particle; Kiwi
              adds the General Requirements 4 "should not separate" cases that a space does
              separate: a determiner from its noun (관형사 MM, "무슨 | 그림자"), an adnominal from
              its noun (ETM, "예쁜 | 꽃"), a verb from its auxiliary or negation (VX, "먹어 |
              버렸다", "하지 | 않았다"), a negation adverb from its verb ("안 | 돼"), a name from a
              name (NNP NNP) and a number from its counter; a connective or final ending (EC, EF)
              is a good place. Without kiwipiepy the same rules run without these hints.
              A word is cut inside only when it alone is longer than a line. When no two-line
              layout fits, more lines (or an over-long line) come back so that ``fits`` and the
              placement checks see the overflow. The text itself is never changed: joining the
              lines with spaces gives the input with runs of white space collapsed (a word cut in
              two joins without one).
``fits(x)``   whether a text (or a list of lines) stays within the line and line-count limits.
"""
from __future__ import annotations

import functools
import threading
import unicodedata

MAX_UNITS = 16.0        # II.2
MAX_LINES = 2           # II.4
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
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 1.0                                   # CJK and other full-width characters
    cat = unicodedata.category(ch)
    if cat[0] in "PZ" or cat == "Nd":
        return 0.5                                   # …, ·, ‘ ’, no-break space, other digits
    if cat[0] == "L" and "LATIN" in unicodedata.name(ch, ""):
        return 0.5                                   # é, ñ, ...
    return 1.0                                       # symbols (♬ ♪ ☎), other scripts


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


def _spaces(text: str) -> list[tuple[int, bool]]:
    """Positions of the spaces of ``text`` and whether each is inside ( ) or [ ]."""
    out, depth = [], 0
    for i, ch in enumerate(text):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        elif ch == " ":
            out.append((i, depth > 0))
    return out


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


def _greedy(text: str, max_units: float) -> list[str]:
    """Fill lines word by word; a word longer than a line is cut (only then)."""
    lines: list[str] = []
    cur = ""
    for word in text.split(" "):
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


def wrap(text, max_units: float = MAX_UNITS, max_lines: int = MAX_LINES, kiwi: bool = True) -> list[str]:
    """Lines of ``text`` (module doc). ``kiwi=False`` skips the Kiwi hints (same feasibility)."""
    text = norm(text)
    if not text:
        return []
    if units(text) <= max_units + EPS:
        return [text]
    if max_lines >= 2:
        hints = _hints(text) if kiwi else {}
        best = None
        for i, inside in _spaces(text):
            a, b = text[:i], text[i + 1:]
            ua, ub = units(a), units(b)
            if ua > max_units + EPS or ub > max_units + EPS:
                continue
            score = abs(ua - ub) + (0.5 if ua > ub else 0.0) - end_bonus(a) + hints.get(i, 0.0)
            if inside:
                score += 4.0
            key = (round(score, 6), i)
            if best is None or key < best[0]:
                best = (key, a, b)
        if best is not None:
            return [best[1], best[2]]
    return _greedy(text, max_units)


def fits(text, max_units: float = MAX_UNITS, max_lines: int = MAX_LINES) -> bool:
    """Whether ``text`` (a string, wrapped first, or a list of lines) stays within the limits."""
    lines = wrap(text, max_units, max_lines, kiwi=False) if isinstance(text, str) else [str(x) for x in text]
    return len(lines) <= max_lines and all(units(x) <= max_units + EPS for x in lines)


def line_units(lines) -> list[float]:
    return [units(x) for x in lines]
