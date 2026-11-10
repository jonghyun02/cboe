# -*- coding: utf-8 -*-
"""촬영 대본 읽기: a shooting script (PDF, HWP, text) as a document, not a list of lines.

A shooting script has scene headings, action (지문), speaker cells with the dialogue beside
them, directions in parentheses, on-screen text and transitions. This module

- pulls lines out of the files: ``pdf_lines`` (pdfminer.six: the lines are rebuilt from the
  position of every character, so text that is stored out of order comes out right) and
  ``hwp_paragraphs`` (olefile + the HWP 5 record format; tabs are kept);
- sorts the lines into kinds: ``classify_positioned`` for lines with positions (the columns
  are found from the x positions of that document, nothing is fixed) and ``classify_plain``
  for text lines (speaker cell and dialogue split by a tab or a wide gap, or a name line
  above its dialogue);
- reads the speaker cell (``parse_cue``) and the directions of a dialogue (``annotate``);
- builds the script document of ``cboe.script`` (``build``).

Kinds of a classified item (``type``): ``heading`` (장면 머리), ``action`` (지문), ``dialogue``
(화자 칸 + 대사), ``onscreen`` (화면 글자 지시: 자막), ``transition`` (F.O, DISSOLVE, INSERT,
CUT TO ...), ``other``.

The words of a dialogue are never changed here: ``text`` is the dialogue without its
directions, ``raw`` is the dialogue as written.
"""
from __future__ import annotations

import re
import struct
import unicodedata
import zlib
from collections import Counter
from pathlib import Path

LETTER_RE = re.compile(r"[0-9A-Za-z가-힣ㄱ-ㅎㅏ-ㅣぁ-んァ-ン一-龥]")
PAREN_RE = re.compile(r"[\(（]([^()（）]*)[\)）]")
_WS = re.compile(r"[ \t\u00a0\u3000]+")              # also the no-break and the full-width space
UNREADABLE = "\ufffd"     # stands for a glyph the PDF gives no character for
_WORD_CH = re.compile(r"[0-9A-Za-z가-힣]")


def clean(text: str) -> str:
    return _WS.sub(" ", text.replace("\r", " ").replace("\n", " ")).strip()


def spoken(text: str) -> str:
    """Dialogue text without the directions in parentheses and the ``/`` pause marks."""
    return clean(re.sub(r"(?<!\d)\s*/\s*(?!\d)", " ", PAREN_RE.sub(" ", text)))       # keeps "1/2"


# =============================================================== PDF: characters -> lines
BOLD_FONT = re.compile(r"bold|black|heavy|demi|[-,]b(d|$)|extrab", re.I)
SPLIT_GAP = 1.25      # em: a jump this wide without a space glyph is a new column (a tab)
SPLIT_GAP_SPACES = 2.0    # em: a gap this wide filled with spaces also is
WORD_GAP = 0.25       # em: a narrower jump without a space glyph is not a word break
MARGIN_BAND = 0.09    # top and bottom share of the page where page numbers and running titles sit
PAGE_NO = re.compile(r"[-–—~\s]*\d{1,4}[-–—~\s]*|\d{1,4}\s*/\s*\d{1,4}|(?:page|p\.?)\s*\d{1,4}|\d{1,4}\s*쪽", re.I)


def _drawn_twice(a: dict, b: dict) -> bool:
    """``b`` is ``a`` drawn again a hair to the side (faked bold): the same glyph whose box
    lies almost on the first one. Narrow glyphs set close together, like the dots of
    ``...``, are a step of most of their own width apart and are kept."""
    if b["text"] != a["text"]:
        return False
    dx = abs(b["x0"] - a["x0"])
    width = max(a["x1"] - a["x0"], b["x1"] - b["x0"], 0.0)
    return dx < 0.2 * max(a["size"], b["size"]) and dx <= 0.3 * width


def page_rows(chars: list[dict], page: int, height: float) -> list[dict]:
    """Characters of one page -> its lines, top to bottom and left to right.

    ``chars``: ``{"x0", "x1", "y" (baseline, PDF units from the bottom), "text", "size",
    "bold"}``. Characters on one baseline form a row whatever their order in the file; a row
    is cut where the text jumps to another column, so a speaker cell and the dialogue beside
    it come out as two lines with the same ``y0``. ``y0`` is the baseline measured from the
    top of the page; ``space_end`` says the row ended with a space (a wrapped line that ends
    inside a word has none) and ``lead`` how many spaces it began with (a line pushed to its
    column by hand).
    """
    ink = sorted((c for c in chars if c["text"]), key=lambda c: (-c["y"], c["x0"]))
    rows: list[list[dict]] = []
    for c in ink:
        if rows and abs(rows[-1][0]["y"] - c["y"]) <= 0.25 * max(c["size"], rows[-1][0]["size"], 4.0):
            rows[-1].append(c)
        else:
            rows.append([c])
    out = []
    for row in rows:
        row.sort(key=lambda c: c["x0"])
        glyphs = [c for c in row if c["text"].strip()]
        if not glyphs:
            continue
        # letters set wide apart (a title): the usual distance between neighbours is not a word break
        near = sorted(b["x0"] - a["x1"] for a, b in zip(row, row[1:]) if a["text"].strip() and b["text"].strip())
        track = near[len(near) // 2] if len(near) >= 3 else 0.0
        track = track if track >= 0.12 * glyphs[0]["size"] else 0.0
        segs, cur, prev, spaces, lead = [], [], None, 0, 0
        for c in row:
            if not c["text"].strip():
                spaces += 1
                continue
            if prev is None:
                lead = spaces
            else:
                if _drawn_twice(prev, c):
                    continue                          # a glyph drawn twice (faked bold)
                gap, em = c["x0"] - prev["x1"], max(prev["size"], c["size"], 1.0)
                if gap >= SPLIT_GAP_SPACES * em or (gap >= SPLIT_GAP * em and not spaces):
                    segs.append(cur)
                    cur = []
                elif spaces or gap - track >= WORD_GAP * em:
                    cur.append(None)                  # a word break
            cur.append(c)
            prev, spaces = c, 0
        segs.append(cur)
        for k, seg in enumerate(segs):
            marks = [c for c in seg if c is not None]
            text = unicodedata.normalize("NFC", "".join(" " if c is None else c["text"] for c in seg))
            sizes = Counter(round(c["size"], 1) for c in marks)
            # a space or two before the text does not move the line to another column
            x0 = row[0]["x0"] if k == 0 and 0 < lead <= 2 else marks[0]["x0"]
            out.append({"page": page, "x0": round(x0, 1), "x1": round(marks[-1]["x1"], 1),
                        "y0": round(height - row[0]["y"], 1), "text": clean(text),
                        "bold": sum(1 for c in marks if c["bold"]) * 2 >= len(marks),
                        "size": sizes.most_common(1)[0][0],
                        "space_end": k == len(segs) - 1 and spaces > 0,
                        "lead": lead if k == 0 else 0})
    return out


def drop_page_furniture(rows: list[dict], heights: dict[int, float]) -> list[dict]:
    """Rows without page numbers and running titles: a row alone on its line in the top or
    bottom margin that is a page number or has the same text on half of the pages. (A speaker
    cell that happens to top many pages has its dialogue beside it and stays.)"""
    alone = Counter((r["page"], round(r["y0"])) for r in rows)

    def band(r):
        h = heights.get(r["page"]) or 0.0
        return bool(h) and alone[(r["page"], round(r["y0"]))] == 1 \
            and (r["y0"] < MARGIN_BAND * h or r["y0"] > (1.0 - MARGIN_BAND) * h)

    def key(r):
        return re.sub(r"\d+", "0", r["text"])

    pages = len(heights)
    seen = Counter(key(r) for r in rows if band(r))
    repeat = {k for k, n in seen.items() if n >= max(3, 0.5 * pages)}
    return [r for r in rows if not (band(r) and (PAGE_NO.fullmatch(r["text"]) or key(r) in repeat))]


def _pdf_pages(path):
    """(width, height, characters) per page, without pdfminer's own layout analysis."""
    from pdfminer.converter import PDFPageAggregator
    from pdfminer.layout import LTChar, LTFigure
    from pdfminer.pdfinterp import PDFPageInterpreter, PDFResourceManager
    from pdfminer.pdfpage import PDFPage

    def walk(obj):
        for it in obj:
            if isinstance(it, LTChar):
                yield it
            elif isinstance(it, LTFigure):                # a form XObject holds its own characters
                yield from walk(it)

    rsrc = PDFResourceManager()
    dev = PDFPageAggregator(rsrc, laparams=None)
    interp = PDFPageInterpreter(rsrc, dev)
    with open(path, "rb") as f:
        for page in PDFPage.get_pages(f):
            interp.process_page(page)
            lt = dev.get_result()
            chars = []
            for c in walk(lt):
                if not c.upright:                         # rotated watermarks
                    continue
                text = c.get_text()
                chars.append({"x0": c.x0, "x1": c.x1, "y": c.matrix[5], "size": c.size,
                              "text": UNREADABLE if text.startswith("(cid:") else text,
                              "bold": bool(BOLD_FONT.search(c.fontname or ""))})
            yield lt.width, lt.height, chars


def pdf_lines(path) -> list[dict]:
    """Lines of a PDF with their place on the page, in reading order (page, height, x):
    ``{"page", "x0", "x1", "y0", "text", "bold", "size", "space_end", "lead"}`` (see
    ``page_rows``). Page numbers are left out."""
    try:
        from pdfminer.pdfdocument import PDFPasswordIncorrect
        from pdfminer.pdfparser import PDFSyntaxError
    except ImportError:
        raise ValueError("PDF 대본을 읽으려면 pdfminer.six 가 필요합니다 (setup.cmd 를 다시 실행하세요)") from None
    rows, heights, n_chars, n_bad = [], {}, 0, 0
    try:
        for no, (_, height, chars) in enumerate(_pdf_pages(path), 1):
            heights[no] = height
            n_chars += sum(1 for c in chars if c["text"].strip())
            n_bad += sum(1 for c in chars if c["text"] == UNREADABLE)
            rows.extend(page_rows(chars, no, height))
    except PDFPasswordIncorrect:
        raise ValueError("암호가 걸린 PDF 는 읽지 못합니다") from None
    except PDFSyntaxError as exc:
        raise ValueError(f"PDF 파일을 읽지 못했습니다 · {exc}") from None
    if not n_chars:
        raise ValueError("글자가 없는 PDF 입니다 (종이를 찍은 PDF 는 읽지 못합니다)")
    if n_bad > 0.2 * n_chars:
        raise ValueError("글자를 알아볼 수 없는 PDF 입니다 (글꼴에 글자 표가 없음)")
    return drop_page_furniture(rows, heights)


# ================================================================ HWP 5: records -> text
HWP_SIGNATURE = b"HWP Document File"
HWP_PARA_HEADER, HWP_PARA_TEXT, HWP_CTRL_HEADER = 66, 67, 71
HWP_CHAR_CONTROLS = frozenset((0, 10, 13, 24, 25, 26, 27, 28, 29, 30, 31))    # one code unit; the rest below 32 take eight
HWP_SKIP_CONTROLS = (b"daeh", b"toof")       # 'head', 'foot': running header and footer


def hwp_records(data: bytes):
    """(tag, level, payload) of every record of a decompressed HWP 5 stream."""
    pos, n = 0, len(data)
    while pos + 4 <= n:
        (head,) = struct.unpack_from("<I", data, pos)
        pos += 4
        tag, level, size = head & 0x3FF, (head >> 10) & 0x3FF, head >> 20
        if size == 0xFFF:                                # the real size follows
            if pos + 4 > n:
                return
            (size,) = struct.unpack_from("<I", data, pos)
            pos += 4
        yield tag, level, data[pos:pos + size]
        pos += size


def hwp_text(payload: bytes) -> str:
    """Text of a PARA_TEXT record: UTF-16LE with control codes. The tab stays a tab, a line
    break becomes ``\\n``, objects and fields (eight code units each) are dropped."""
    n = len(payload) // 2
    units = struct.unpack_from(f"<{n}H", payload)
    out, i = [], 0
    while i < n:
        c = units[i]
        if c >= 32:
            out.append(c)
            i += 1
        elif c in HWP_CHAR_CONTROLS:
            if c == 10:
                out.append(0x0A)
            elif c in (30, 31):                          # non-breaking and fixed-width space
                out.append(0x20)
            elif c == 24:
                out.append(0x2D)
            i += 1
        else:
            if c == 9:
                out.append(0x09)
            i += 8
    return struct.pack(f"<{len(out)}H", *out).decode("utf-16-le", "replace")


def hwp_section(data: bytes) -> list[str]:
    """Paragraphs of one decompressed BodyText section, empty ones included."""
    out: list[str] = []
    skip = None
    for tag, level, payload in hwp_records(data):
        if skip is not None:
            if level > skip:
                continue
            skip = None
        if tag == HWP_CTRL_HEADER and payload[:4] in HWP_SKIP_CONTROLS:
            skip = level
        elif tag == HWP_PARA_HEADER:
            out.append("")
        elif tag == HWP_PARA_TEXT and out:
            out[-1] = hwp_text(payload)
    return out


def hwp_paragraphs(path) -> list[str]:
    """Paragraphs of an HWP 5 file in order (tabs kept, a line break inside a paragraph
    starts a new entry, an empty paragraph is ``""``)."""
    try:
        import olefile
    except ImportError:
        raise ValueError("HWP 대본을 읽으려면 olefile 이 필요합니다 (setup.cmd 를 다시 실행하세요)") from None
    with open(path, "rb") as f:
        head = f.read(32)
    if head[:2] == b"PK":
        raise ValueError("HWPX(.hwpx) 는 아직 읽지 못합니다. 한글에서 HWP 나 PDF 로 저장해 주세요")
    if not olefile.isOleFile(str(path)):
        raise ValueError("HWP 5 파일이 아닙니다 (한글 97 이전 파일은 한글에서 HWP 나 PDF 로 다시 저장해 주세요)")
    ole = olefile.OleFileIO(str(path))
    try:
        if not ole.exists("FileHeader"):
            raise ValueError("HWP 5 파일이 아닙니다")
        header = ole.openstream("FileHeader").read()
        if not header.startswith(HWP_SIGNATURE) or len(header) < 40:
            raise ValueError("HWP 5 파일이 아닙니다")
        (flags,) = struct.unpack_from("<I", header, 36)
        if flags & 0x2:
            raise ValueError("암호가 걸린 HWP 는 읽지 못합니다")
        if flags & 0x4:
            raise ValueError("배포용 HWP 문서는 읽지 못합니다")
        sections = sorted((e for e in ole.listdir() if len(e) == 2 and e[0] == "BodyText"
                           and re.fullmatch(r"Section\d+", e[1])), key=lambda e: int(e[1][7:]))
        if not sections:
            raise ValueError("HWP 파일에 본문이 없습니다")
        out: list[str] = []
        for entry in sections:
            raw = ole.openstream(entry).read()
            try:
                data = zlib.decompress(raw, -15) if flags & 0x1 else raw
            except zlib.error as exc:
                raise ValueError(f"HWP 본문을 풀지 못했습니다 · {exc}") from None
            for para in hwp_section(data):
                out.extend(para.split("\n"))
        return out
    finally:
        ole.close()


# ============================================================ what a line is: patterns
# one word that describes the time before it: an adjective ending in ㄴ (이른, 짙은, 밝은, 어두운,
# 희뿌연) or 어스름
_N_FINAL = "".join(chr(0xAC00 + 28 * k + 4) for k in range(11172 // 28))      # syllables ending in ㄴ
_TIME_MOD = rf"(?:[가-힣]{{0,3}}[{_N_FINAL}]|어스름)"
_TIME = (rf"(?:{_TIME_MOD}\s*)?(?:새벽|아침|오전|한낮|낮|정오|오후|초저녁|저녁|해\s*질\s*녘|해\s*질\s*무렵|"
         r"한밤중|한밤|밤|심야|자정|동\s*틀\s*녘|일출|일몰|황혼|석양|어스름|DAY|NIGHT|D|N)")
_WEATHER = r"(?:비\s*그침|비|눈|맑음|흐림|안개|폭우|소나기|장마|눈보라)"
TIME_PART = re.compile(rf"\s*{_TIME}(?:\s*[-~→,·&]\s*{_TIME})*\s*(?:[\(（][^()（）]*[\)）])?\s*")
WEATHER_PART = re.compile(rf"\s*{_WEATHER}(?:\s*[-~→,·&]\s*{_WEATHER})*\s*")
SCENE_MARK = re.compile(r"\s*(?:S\s*#|#|씬|scene)\s*(\d+(?:-\d+)?[A-Za-z]?)\s*[.:：)\]]?\s*(.*?)\s*", re.I)
NUM_HEAD = re.compile(r"\s*(\d{1,3}(?:-\d+)?[A-Za-z]?)\s*[.)]\s*(\S.*?)\s*")
HEAD_MAX = 60         # characters: a longer line is a sentence, not a heading

_T_LATIN = (r"F\.?\s?[IO]\.?|FADE[\s\-]*(?:IN|OUT)|DIS+O?L?VE(?:\s*TO)?|O\.?L\.?|CUT\s*(?:TO|IN|OUT|BACK)|"
            r"JUMP\s*CUT|SMASH\s*CUT|INSERT|INS\.?|WIPE|IRIS\s*(?:IN|OUT)|MONTAGE|FLASH\s*BACK|"
            r"TITLE\s*(?:IN|OUT)|THE\s+END|END|CONTINUED|CUT")
_T_KO = (r"페이드\s*(?:인|아웃)|디졸브|오버랩|컷\s*투|컷\s*아웃|점프\s*컷|인서트|와이프|몽타주|몽타쥬|"
         r"플래시\s*백|회상\s*끝|회상|암전|화면\s*전환|시간\s*경과|끝")
# a Latin mark may be followed by what it shows ("INSERT) ..."); a Korean word is a mark only alone
TRANSITION_LATIN = re.compile(rf"\s*[\(\[<〈\-–—\s]*(?:{_T_LATIN})(?![A-Za-z0-9가-힣])[\)\]>〉\-–—.:：\s]*(.*)", re.I)
TRANSITION_KO = re.compile(rf"\s*[\(\[<〈\-–—\s]*(?:{_T_KO})[\)\]>〉\-–—.\s]*")
# a Korean mark that is also an everyday word for a person (와이프 = a wife): in a speaker cell,
# or alone on the line above a dialogue, it names that person, not the transition (WIPE)
PERSON_MARK = re.compile(r"[\s\(\[<〈]*와이프[\s\)\]>〉.:：]*")
_ONSCREEN = r"자막|字幕|SUBTITLE|SUPER|슈퍼|수퍼|타이틀|TITLE|CAPTION"
ONSCREEN = re.compile(rf"\s*(?:[\(\[<〈]\s*(?:{_ONSCREEN})\s*[\)\]>〉]\s*[:：\-–—]?|(?:{_ONSCREEN})\s*[:：\-–—])\s*(\S.*?)\s*",
                      re.I)
ONSCREEN_LEAD = re.compile(r"자막|타이틀|字幕")      # an action line that announces text on the screen
SLUG = re.compile(r"\s*[-–—]\s*\S.{0,28}")           # "- 강가": a place inside a scene
CUE_MAX = 20          # characters of a speaker cell
NAME_MAX = 12         # characters of a speaker name when only the look of the line says so


def split_heading(text: str) -> tuple[str | None, str | None]:
    """(place, time) of a heading without its number: ``다방 / 낮`` -> (``다방``, ``낮``)."""
    parts = [p.strip() for p in re.split(r"[/／]", text)]
    tail = []
    while len(parts) > 1 and (TIME_PART.fullmatch(parts[-1]) or WEATHER_PART.fullmatch(parts[-1])):
        tail.insert(0, parts.pop())
    if tail:
        return (" / ".join(p for p in parts if p) or None), " / ".join(tail)
    m = re.fullmatch(rf"(.*?\S)\s*(?:[\(（]\s*({_TIME})\s*[\)）]|\s[-–—]\s*({_TIME})|,\s*({_TIME}))\s*", text)
    if m:
        return m.group(1).strip(), next(g for g in m.groups()[1:] if g)
    return (clean(text) or None), None


def heading_candidate(text: str) -> dict | None:
    """How ``text`` could be a scene heading: ``{"number", "place", "time", "kind"}`` with
    kind ``mark`` (S#3, #3, 씬 3), ``number`` (3. ...) or ``slug`` (place / time), or None."""
    text = clean(text)
    if not text or len(text) > HEAD_MAX:
        return None
    m = SCENE_MARK.fullmatch(text)
    if m:
        place, time = split_heading(re.sub(r"^[\[\(]\s*(.*?)\s*[\]\)]$", r"\1", m.group(2)))
        return {"number": m.group(1), "place": place, "time": time, "kind": "mark", "text": text}
    m = NUM_HEAD.fullmatch(text)
    if m and not re.search(r"[?!…]\s*$|\.\.\s*$", m.group(2)):
        place, time = split_heading(m.group(2).rstrip(". "))
        return {"number": m.group(1), "place": place, "time": time, "kind": "number", "text": text}
    if re.search(r"[/／]", text) and not re.match(r"[\(\[<]", text) and "\t" not in text:
        place, time = split_heading(text)
        if place and time and not re.search(r"[.?!…,]\s*$", place):
            return {"number": None, "place": place, "time": time, "kind": "slug", "text": text}
    return None


def is_transition(text: str) -> bool:
    t = clean(text)
    return bool(t) and bool(TRANSITION_KO.fullmatch(t) or TRANSITION_LATIN.fullmatch(t))


def cue_like(text: str) -> bool:
    """Could be a speaker cell: short, with a letter, not a heading or a transition mark
    (``와이프`` is a person there, ``PERSON_MARK``)."""
    t = clean(text)
    return bool(t) and len(t) <= CUE_MAX and bool(LETTER_RE.search(t)) \
        and (not is_transition(t) or bool(PERSON_MARK.fullmatch(t))) and heading_candidate(t) is None


ABBREVIATION = re.compile(r"(?<![A-Za-z])[A-Za-z](?:\.[A-Za-z])+\.?(?![A-Za-z.])")      # D.J, P.D., M.C


def name_like(text: str) -> bool:
    """Looks like a speaker name on its own (stricter than ``cue_like``): no sentence
    punctuation (the dots of an abbreviation like ``D.J`` are none), a few short words."""
    t = clean(text)
    core = ABBREVIATION.sub(lambda m: m.group().replace(".", ""), PAREN_RE.sub(" ", t)).strip()
    if not cue_like(t) or len(t) > NAME_MAX + 8 or len(core) > NAME_MAX:
        return False
    return not re.search(r"[.!?…~\"'“”‘’:：;]|,$", core) and len(core.split()) <= 3 and not re.match(r"[-–—]", t)


def accept_headings(cands: list[tuple[int, dict]], looks: list | None = None) -> dict[int, dict]:
    """Which heading candidates are headings. ``cands``: (row index, candidate) in document
    order; ``looks[index]`` is a hashable look of every row (position, weight, size) when
    the rows have one.

    Marked headings (S#3) always are. Numbered ones (``3. 다방 / 낮``) must belong to the
    document's run of scene numbers — a numbered list in the text is not a scene — or name a
    time, or look like the headings of the run when that look is the headings' own. A place /
    time line without a number is a heading only in a script that does not number its scenes.
    """
    out = {i: c for i, c in cands if c["kind"] == "mark"}
    nums = [(i, c, int(re.match(r"\d+", c["number"]).group())) for i, c in cands if c["kind"] == "number"]
    chain: list[int] = []

    def run(weight) -> list[int]:
        # the heaviest run of rising numbers (steps of 1..3, the same number again only with a
        # sub-number like 12-1); of equal runs the one later in the document
        best = [weight(k) for k in range(len(nums))]
        back = [-1] * len(nums)
        for b in range(len(nums)):
            own = best[b]
            for a in range(b):
                step = nums[b][2] - nums[a][2]
                same = step == 0 and nums[a][1]["number"] != nums[b][1]["number"]
                if (1 <= step <= 3 or same) and best[a] + own >= best[b]:
                    best[b], back[b] = best[a] + own, a
        end = max(range(len(nums)), key=lambda k: (best[k], k))
        found = []
        while end >= 0:
            found.append(end)
            end = back[end]
        return found[::-1]

    if nums:
        def timed(k):
            return 0.25 if nums[k][1]["time"] else 0.0

        chain = run(lambda k: 1.0 + timed(k))
        keep = set()
        if len(chain) >= 2 and looks is not None:
            top, n = Counter(looks[nums[k][0]] for k in chain).most_common(1)[0]
            if n >= 0.6 * len(chain):                        # the headings of this document look alike
                chain = run(lambda k: 1.0 + timed(k) + 0.5 * (looks[nums[k][0]] == top))
                # one set like the body text still counts when it reads like a title, not like a sentence
                chain = [k for k in chain if looks[nums[k][0]] == top or nums[k][1]["time"]
                         or (len(nums[k][1]["text"]) <= 30 and not re.search(r"[.,]$|다$", nums[k][1]["text"]))]
                same_look = [k for k in range(len(nums)) if looks[nums[k][0]] == top]
                if looks.count(top) <= 1.5 * len(same_look):  # and hardly anything else looks like them
                    keep.update(same_look)
        if len(chain) < 2:
            chain = []
        keep.update(chain)
        keep.update(k for k in range(len(nums)) if nums[k][1]["time"])
        for k in keep:
            out[nums[k][0]] = nums[k][1]
    if len(chain) < 3 and not any(c["kind"] == "mark" for _, c in cands):
        out.update({i: c for i, c in cands if c["kind"] == "slug"})
    return out


# ====================================================== lines with positions -> rows
COLUMN_TOL = 3.0      # PDF units around a column position


def _group(lines: list[dict]) -> list[list[dict]]:
    rows: list[list[dict]] = []
    for ln in sorted(lines, key=lambda r: (r["page"], r["y0"], r["x0"])):
        if rows and rows[-1][0]["page"] == ln["page"] and abs(rows[-1][0]["y0"] - ln["y0"]) <= 2.0:
            rows[-1].append(ln)
        else:
            rows.append([ln])
    return rows


def _mode(values) -> tuple[float | None, int]:
    """The most common position (values within one unit count together) and how many."""
    count = Counter(round(v) for v in values)
    if not count:
        return None, 0
    best = max(count, key=lambda x: (count[x - 1] + count[x] + count[x + 1], count[x]))
    return float(best), count[best - 1] + count[best] + count[best + 1]


def find_columns(lines: list[dict]) -> dict:
    """Where this document puts things: ``left`` (margin of action and speaker cells),
    ``dialogue`` (start of the dialogue beside a speaker cell, None when the document has no
    such column) and ``right`` (edge that a full line reaches, None when unknown)."""
    rows = _group(lines)
    left, _ = _mode(r[0]["x0"] for r in rows)
    second = [r[1]["x0"] for r in rows if len(r) > 1 and abs(r[0]["x0"] - left) <= COLUMN_TOL
              and len(r[0]["text"]) <= CUE_MAX]
    dialogue, n = _mode(second)
    if n < max(3, 0.02 * len(rows)):
        dialogue = None
    # the right edge: the furthest end that three lines share (one overlong line does not move it;
    # None when no lines are full, as in a short text)
    ends = sorted((r[-1]["x1"] for r in rows if abs(r[0]["x0"] - left) <= COLUMN_TOL), reverse=True)
    right = next((e for k, e in enumerate(ends) if k + 2 < len(ends) and e - ends[k + 2] <= 1.0), None)
    return {"left": left, "dialogue": dialogue, "right": right}


def _positioned_rows(lines: list[dict], cols: dict) -> list[dict]:
    left, dial = cols["left"], cols["dialogue"]
    out, open_x = [], None
    for segs in _group(lines):
        first, last = segs[0], segs[-1]
        row = {"page": first["page"], "x0": first["x0"], "x1": last["x1"], "size": first.get("size") or 10.0,
               "bold": first.get("bold"), "space_end": bool(last.get("space_end")), "lead": first.get("lead") or 0,
               "cue": None}
        at_left = abs(first["x0"] - left) <= COLUMN_TOL
        if len(segs) > 1 and at_left and cue_like(first["text"]) and segs[1]["x0"] >= dial - COLUMN_TOL:
            row.update(cue=first["text"], text=" ".join(s["text"] for s in segs[1:]), col="dialogue")
            open_x = segs[1]["x0"]
        else:
            row["text"] = " ".join(s["text"] for s in segs)
            if at_left:
                row["col"], open_x = "left", None
            elif abs(first["x0"] - dial) <= COLUMN_TOL or (open_x is not None and abs(first["x0"] - open_x) <= COLUMN_TOL):
                row["col"] = "dialogue"
            elif open_x is not None and first["x0"] < dial:
                row["col"] = "indent"         # between the margin and the dialogue: a continuation set too far left
            else:
                row["col"], open_x = "other", None
        out.append(row)
    return out


# ============================================================== text lines -> rows
TAB_CUE = re.compile(r"[ \t]*([^\t]*?\S)[ ]*\t+[ \t]*(\S.*?)\s*")         # 이름<TAB>대사
TAB_INDENT = re.compile(r"([ ]*\t[ \t]*)(\S.*?)\s*")                       # <TAB><TAB>대사: the dialogue goes on
GAP_CUE = re.compile(r"[ ]{0,12}(\S(?:[^\t]*?\S)??)[ ]{3,}(\S.*?)\s*")     # 이름   대사
GAP_INDENT = re.compile(r"[ ]{6,}(\S.*?)\s*")
SENTENCE_END = re.compile(r"[.?!…~」”\"')\]]\s*$")
MIN_CUES = 3          # lines with a speaker cell before a text counts as a shooting script


def _cue_key(text: str) -> str:
    return re.sub(r"\s+", "", PAREN_RE.sub("", text)) or re.sub(r"\s+", "", text)


def plain_evidence(texts: list[str]) -> dict:
    """What makes a text a shooting script: lines whose speaker cell is set off by a tab
    (``tab``), by a wide gap (``gap``, names that come back) or stands on the line above
    (``stacked``, in a text with scene headings), and the scene headings found. What comes
    before the first scene heading (the title page: ``각본<TAB>이름``) is no evidence."""
    stripped = [clean(t) for t in texts]
    cands = [(i, c) for i, t in enumerate(texts) if "\t" not in t.strip() for c in [heading_candidate(t)] if c]
    heads = accept_headings(cands)
    first = min(heads) if heads else 0
    body = list(enumerate(texts))[first:]
    tab = sum(1 for _, t in body for m in [TAB_CUE.fullmatch(t)] if m and cue_like(m.group(1)))
    gap_names = Counter(_cue_key(m.group(1)) for _, t in body if "\t" not in t
                        for m in [GAP_CUE.fullmatch(t)] if m and name_like(m.group(1)))
    gap_ok = {k for k, n in gap_names.items() if n >= 2}
    gap = sum(n for k, n in gap_names.items() if k in gap_ok) if len(gap_ok) >= 2 else 0
    stacked_names: set[str] = set()
    stacked = 0
    scene_heads = sum(1 for c in heads.values() if c["kind"] != "mark")
    if scene_heads >= 2:
        alone = Counter(_cue_key(s) for i, s in enumerate(stripped) if s and i >= first and i not in heads and name_like(s)
                        and i + 1 < len(stripped) and stripped[i + 1] and "\t" not in texts[i])
        names = {k: n for k, n in alone.items() if n >= 3}
        filled = sum(1 for s in stripped if s)
        if len(names) >= 2 and sum(names.values()) >= max(10, 0.1 * filled):
            stacked_names, stacked = set(names), sum(names.values())
    return {"tab": tab, "gap": gap, "stacked": stacked, "headings": len(heads), "cues": tab + gap + stacked,
            "_heads": heads, "_gap_names": gap_ok if gap else set(), "_stacked_names": stacked_names}


def _gap_once(texts: list[str], i: int, names: set[str]) -> bool:
    """In a script whose speaker cells are set off by a gap, a name written only once is a
    speaker cell too when its dialogue goes on below it (an indented line) or it stands
    right next to the speaker cell of a name that comes back (an exchange). Only a single
    short word is taken so (``판사``, ``일진1``, ``사장/복희``): words of a sentence set apart by a
    gap are no name."""
    m = GAP_CUE.fullmatch(texts[i])
    core = clean(PAREN_RE.sub(" ", m.group(1))) if m else ""
    if not core or " " in core or len(core) > 8 or core.endswith("다"):
        return False
    nxt = texts[i + 1] if i + 1 < len(texts) else ""
    if clean(nxt) and (GAP_INDENT.fullmatch(nxt) or TAB_INDENT.fullmatch(nxt)):
        return True
    for j in (i - 1, i + 1):
        m = GAP_CUE.fullmatch(texts[j]) if 0 <= j < len(texts) and "\t" not in texts[j] else None
        if m and name_like(m.group(1)) and _cue_key(m.group(1)) in names:
            return True
    return False


def _plain_rows(texts: list[str], ev: dict) -> list[dict]:
    tab_mode, gap_names, stacked = ev["tab"] >= MIN_CUES, ev["_gap_names"], ev["_stacked_names"]
    out: list[dict] = []

    def in_dialogue() -> bool:
        return bool(out) and not out[-1].get("blank") and (out[-1]["cue"] is not None or out[-1]["col"] != "left")

    first = min(ev["_heads"]) if ev["_heads"] else -1

    pending = None        # a name line whose dialogue is on the next line
    running = False       # the dialogue under a name line goes on
    for i, t in enumerate(texts):
        s = clean(t)
        base = {"page": None, "cue": None, "src": i}
        if not s:
            if pending is not None:
                out.append({**base, "text": pending, "col": "left"})
                pending = None
            running = False
            out.append({**base, "text": "", "col": "left", "blank": True})
            continue
        if i < first:     # the title page: a credit set off like a speaker cell is kept apart (no dialogue, ``_assemble``)
            m = TAB_CUE.fullmatch(t) or GAP_CUE.fullmatch(t)
            if m and cue_like(m.group(1)):
                out.append({**base, "cue": clean(m.group(1)), "text": clean(m.group(2)), "col": "dialogue"})
                continue
        if pending is not None:
            if i in ev["_heads"] or is_transition(s):
                out.append({**base, "text": pending, "col": "left"})
                pending = None
            else:
                out.append({**base, "cue": pending, "text": s, "col": "dialogue"})
                pending, running = None, True
                continue
        # a script set off by gaps may use a tab here and there: after a known name, or to go on
        m = TAB_CUE.fullmatch(t) if tab_mode or gap_names else None
        if m and cue_like(m.group(1)) and (tab_mode or _cue_key(m.group(1)) in gap_names):
            out.append({**base, "cue": clean(m.group(1)), "text": clean(m.group(2)), "col": "dialogue"})
            running = False
            continue
        m = TAB_INDENT.fullmatch(t) if tab_mode or (gap_names and in_dialogue()) else None
        if m:      # two tabs reach the dialogue column; one stops at the speaker's (a mark like F.O may sit there)
            deep = tab_mode and m.group(1).count("\t") > 1
            out.append({**base, "text": clean(m.group(2)), "col": "dialogue" if deep else "indent"})
            continue
        m = GAP_CUE.fullmatch(t) if gap_names and "\t" not in t else None
        if m and name_like(m.group(1)) and (_cue_key(m.group(1)) in gap_names or _gap_once(texts, i, gap_names)):
            out.append({**base, "cue": clean(m.group(1)), "text": clean(m.group(2)), "col": "dialogue"})
            running = False
            continue
        if gap_names and GAP_INDENT.fullmatch(t):
            out.append({**base, "text": s, "col": "dialogue"})
            continue
        if stacked and i not in ev["_heads"] and name_like(s) and _cue_key(s) in stacked \
                and i + 1 < len(texts) and clean(texts[i + 1]):
            pending, running = s, False
            continue
        if running and i not in ev["_heads"] and not is_transition(s) and out and not SENTENCE_END.search(out[-1]["text"]):
            out.append({**base, "text": s, "col": "dialogue"})       # a wrapped dialogue line
            continue
        running = False
        out.append({**base, "text": s, "col": "left"})
    if pending is not None:
        out.append({"page": None, "cue": None, "src": len(texts), "text": pending, "col": "left"})
    return out


# ========================================================= rows -> classified items
def _cut_inside_word(a: str, b: str) -> bool:
    """Whether a line cut by hand between ``a`` and ``b`` was cut inside a word, by Kiwi's
    spacing of the two halves written together (False when kiwipiepy is not installed)."""
    left, right = a.rsplit(" ", 1)[-1], b.split(" ", 1)[0]
    try:
        from .textutil import _kiwi
        kiwi = _kiwi()
        spaced = kiwi.space(left + right) if kiwi is not None else None
    except Exception:
        spaced = None
    if not spaced:
        return False
    seen = 0
    for k, ch in enumerate(spaced):
        if seen == len(left):
            return not ch.isspace()
        seen += not ch.isspace()
    return False


def _join(a: str, prev: dict, row: dict, right: float | None) -> str:
    """Text of two rows of one paragraph. Text lines join with a space. A PDF line cut by
    the page width ends with a space unless it was cut inside a word — then the halves join
    without one; where the next line was pushed to its column with spaces the line was cut
    by hand, and only the words can tell (``_cut_inside_word``)."""
    b = row["text"]
    if not a or not b:
        return a or b
    size = prev.get("size") or 10.0
    tight = False
    if right is not None and prev.get("x1") is not None and not prev.get("space_end") \
            and _WORD_CH.match(a[-1]) and _WORD_CH.match(b[0]):
        if (row.get("lead") or 0) >= 3:
            tight = right - prev["x1"] < 3.0 * size and _cut_inside_word(a, b)
        else:
            tight = right - prev["x1"] < 0.3 * size
    return a + ("" if tight else " ") + b


def _wrapped(prev: dict, row: dict, right: float | None) -> bool:
    """The row before ended because the line was full, not because the paragraph ended."""
    if right is None or prev.get("x1") is None:
        return False
    word = row["text"].split(" ", 1)[0]
    need = min(len(word), 8) * 0.9 * (prev.get("size") or 10.0)
    return right - prev["x1"] < max(need, 1.2 * (prev.get("size") or 10.0))


def _assemble(rows: list[dict], right: float | None = None, looks: list | None = None) -> list[dict]:
    cands = [(i, c) for i, r in enumerate(rows) if r["cue"] is None and not r.get("blank") and r["col"] != "dialogue"
             for c in [heading_candidate(r["text"])] if c]
    heads = accept_headings(cands, looks)
    first = min(heads) if heads else -1      # before the first scene heading: the title page, a cast list
    items: list[dict] = []
    cur = None            # the dialogue being read
    last = None           # (index of the row before, its item)
    for i, r in enumerate(rows):
        if r.get("blank"):
            cur = last = None
            continue
        if i < first and r["cue"] is not None:      # no dialogue there: "각본<TAB>이름" names no speaker
            items.append({"type": "other", "text": clean(f"{r['cue']} {r['text']}"), "page": r["page"]})
            cur = last = None
            continue
        prev_row, prev_item = last if last else (None, None)
        item = None
        if r["cue"] is not None:
            cur = item = {"type": "dialogue", "cue": r["cue"], "text": r["text"], "page": r["page"]}
            items.append(item)
        elif cur is not None and (r["col"] == "dialogue" or (
                r["col"] == "indent" and i not in heads and not is_transition(r["text"])
                and not ONSCREEN.fullmatch(r["text"]))):
            cur["text"] = _join(cur["text"], rows[prev_row], r, right)       # in the dialogue column: it goes on
            item = cur
        elif r["col"] == "dialogue" and prev_item is not None and prev_item["type"] == "action" \
                and prev_item.get("_rows") == 1 and name_like(prev_item["text"]) and not is_transition(r["text"]):
            prev_item.pop("_rows")                           # a name line above its dialogue
            prev_item.update(type="dialogue", cue=prev_item["text"], text=r["text"])
            cur = item = prev_item
        else:
            cur = None
            text = r["text"]
            if i in heads:
                h = heads[i]
                item = {"type": "heading", "text": text, "number": h["number"], "place": h["place"], "time": h["time"]}
            elif is_transition(text) and not (PERSON_MARK.fullmatch(text) and i + 1 < len(rows)
                                              and rows[i + 1]["col"] == "dialogue" and rows[i + 1]["cue"] is None):
                item = {"type": "transition", "text": text}      # (와이프 above its dialogue is a name line)
            elif ONSCREEN.fullmatch(text):
                item = {"type": "onscreen", "text": text}
            elif r["col"] == "left" and not (SLUG.fullmatch(text) and r.get("bold")):       # a bold "- 강가"
                if prev_item is not None and prev_item["type"] == "action" and "_rows" in prev_item \
                        and rows[prev_row]["col"] == "left" and _wrapped(rows[prev_row], r, right):
                    prev_item["text"] = _join(prev_item["text"], rows[prev_row], r, right)
                    prev_item["_rows"] += 1
                    item = prev_item
                else:
                    item = {"type": "action", "text": text, "_rows": 1}
            else:
                # text off the columns: on-screen text when the line before announces it
                lead = prev_item is not None and (prev_item["type"] == "onscreen" or (
                    prev_item["type"] == "action" and ONSCREEN_LEAD.search(prev_item["text"])))
                item = {"type": "onscreen" if lead and r["col"] != "left" else "other", "text": text}
            if item is not prev_item:
                item["page"] = r["page"]
                items.append(item)
        last = (i, item)
    for it in items:
        it.pop("_rows", None)
        if it.get("page") is None:
            it.pop("page", None)
    return items


def classify_positioned(lines: list[dict]) -> list[dict] | None:
    """Lines with positions (``pdf_lines``) -> classified items, or None when the document
    has no speaker column beside a dialogue column (then its text is read like a text file).

    Item: ``{"type", "text", "page"}``; a dialogue also has ``cue`` (the speaker cell as
    written; ``text`` is the dialogue with its continuation lines), a heading ``number``,
    ``place`` and ``time``.
    """
    if not lines:
        return None
    cols = find_columns(lines)
    if cols["dialogue"] is None:
        return None
    rows = _positioned_rows(lines, cols)
    starts = Counter(round(r["x0"]) for r in rows)

    def snap(x: float) -> int:                            # 84.6 and 85.4 are the same column
        k = round(x)
        return max((k - 1, k, k + 1), key=lambda c: (starts.get(c, 0), -abs(c - k)))

    looks = [(snap(r["x0"]), bool(r.get("bold")), round(r["size"])) for r in rows]
    return _assemble(rows, cols["right"], looks)


def classify_plain(texts: list[str], evidence: dict | None = None) -> list[dict]:
    """Text lines (paragraphs of an HWP file, lines of a text file) -> classified items.
    The speaker cell is told from the dialogue by a tab, by a wide gap after a name that
    comes back, or as a name line above its dialogue in a text with scene headings."""
    ev = evidence or plain_evidence(texts)
    return _assemble(_plain_rows(texts, ev))


def positioned_text(lines: list[dict]) -> list[str]:
    """Positioned lines as text lines (columns of a row joined by a tab), for a PDF that is
    not laid out as a shooting script."""
    return ["\t".join(s["text"] for s in segs) for segs in _group(lines)]


# ================================================= speaker cell and directions
LANG_ABBR = {"영": "영어", "독": "독일어", "일": "일본어", "중": "중국어", "불": "프랑스어", "러": "러시아어", "한": None}
LANG_NAMES = {
    "영어": "영어", "독일어": "독일어", "일본어": "일본어", "중국어": "중국어", "프랑스어": "프랑스어",
    "러시아어": "러시아어", "스페인어": "스페인어", "이탈리아어": "이탈리아어", "이태리어": "이탈리아어",
    "포르투갈어": "포르투갈어", "아랍어": "아랍어", "베트남어": "베트남어", "태국어": "태국어", "몽골어": "몽골어",
    "힌디어": "힌디어", "인도네시아어": "인도네시아어", "터키어": "터키어", "튀르키예어": "튀르키예어",
    "그리스어": "그리스어", "라틴어": "라틴어", "광둥어": "광둥어", "네덜란드어": "네덜란드어", "히브리어": "히브리어",
    "우크라이나어": "우크라이나어", "타갈로그어": "타갈로그어",
    "일본말": "일본어", "중국말": "중국어", "독일말": "독일어", "러시아말": "러시아어", "프랑스말": "프랑스어",
    "한국어": None, "한국말": None, "우리말": None,       # the original language needs no label (지침 II.6)
}
# short names that are also everyday words (불어 = 불다, 일어 = 일어나다): only as the whole direction
LANG_SHORT = {"불어": "프랑스어", "일어": "일본어", "독어": "독일어", "노어": "러시아어"}
_LANG = "|".join(sorted(LANG_NAMES, key=len, reverse=True))
LANG_RE = re.compile(rf"(?:^|\s)({_LANG})(?:$|(?:로|로만)(?=$|\s))")
LANG_SHORT_RE = re.compile(rf"({'|'.join(LANG_SHORT)})(?:로|로만)?")
SUNG_RE = re.compile(r"^(?:노래|떼창|합창|열창|콧노래|가창)(?:로|를|하며|하듯|하면서|조로|처럼|한다|하고)?$|흥얼|콧노래|"
                     r"노래(?:를|도)?\s*(?:부르|따라|이어|시작|한다|하며|하고)|따라\s*부르|떼창|합창하|열창하")
# who or what the voice comes through, by the tag written in the script (spaces and dots dropped)
SOURCE_TAGS = {
    "VO": "offscreen", "OS": "offscreen", "OFF": "offscreen", "E": "offscreen", "소리": "offscreen",
    "목소리": "offscreen", "소리만": "offscreen", "목소리만": "offscreen", "화면밖": "offscreen", "화면밖목소리": "offscreen",
    "N": "narration", "NA": "narration", "NAR": "narration", "나레이션": "narration", "내레이션": "narration", "해설": "narration",
    "M": "inner", "속마음": "inner", "독백": "inner", "마음의소리": "inner", "속으로": "inner", "마음속으로": "inner",
    "F": "phone", "전화": "phone", "전화목소리": "phone", "전화기너머": "phone", "수화기너머": "phone", "통화": "phone",
    "무전": "radio", "무전기": "radio",
    "뉴스": "broadcast", "방송": "broadcast", "TV": "broadcast", "티비": "broadcast", "텔레비전": "broadcast",
    "라디오": "broadcast", "안내방송": "broadcast", "스피커": "broadcast", "확성기": "broadcast",
    "영상": "video", "비디오": "video", "동영상": "video", "녹화영상": "video",
}
# a cell that is only such a word names no person; a medium can stand as the label ([뉴스], [무전])
MEDIUM_SOURCES = ("broadcast", "radio")
# written without parentheses these still are no names (single letters could be: a character "M")
BARE_SOURCE_CUES = frozenset(("소리", "목소리", "내레이션", "나레이션", "해설", "NA", "NAR", "VO", "OS"))
# a sound the voice makes: a noun or a sound word (한숨, 폭소, 키득), or laughing and crying
VOCAL_RE = re.compile(
    r"한숨|웃음|깔깔|낄낄|킥킥|킬킬|키득|큭큭|폭소|실소|피식|울음|엉엉|흑흑|훌쩍|흐느|오열|통곡|울부짖|기침|콜록|쿨럭|켁켁|"
    r"재채기|에취|딸꾹|트림|비명|신음|끙끙|탄식|탄성|환호|하품|숨소리|숨을\s*(?:몰아|내쉬|들이|고르|헐떡)|헉헉|헐떡|가쁜\s*숨|"
    r"심호흡|콧방귀|쯧쯧|혀를\s*차|"
    r"침을\s*삼키|꿀꺽|휘파람|코를\s*골|코골이|킁킁|"
    r"(?:^|\s)(?:웃(?:다|는|고|으|어|었|자|더)|운다|우는|울(?:다|고|며|면|어|었|자|더))")
# a smile on the face makes no sound (씨익·빙그레: 소리 없이 웃는 모양)
SILENT_SMILE_RE = re.compile(r"쓴\s*웃음|웃음을?\s*(?:짓|띠|머금)|미소|씨익|씩\s*웃|빙그레|빙긋|방긋|싱긋|생긋|벙긋")
# said while making that sound (웃으며, 웃다가 심각, 한숨 쉬며, 웃는 얼굴로): the manner of the words
MANNER_END = re.compile(r"(?:며|면서|면서도|다가|듯|듯이|채|채로|로)$")
VOICE_MANNER_RE = re.compile(r"(?:목소리|말투|어조|억양|톤)(?:로|으로)?$|(?:소리|투)로$")      # 웃음 섞인 목소리
# whose sound it is when not the speaker's: a group (학생들 폭소, 다들 웃는다) or X의 웃음
GROUP_WORDS = frozenset(("일동", "모두", "모두들", "다들", "전원", "다같이", "주위", "주변", "관객", "청중", "객석",
                         "방청석", "방청객", "일행", "무리"))
_PARTICLE = re.compile(r"(?<=\S)(?:이|가|은|는|도)$")
SUBJECT_RE = re.compile(r"(\S{2,}?)(?:이|가)")          # 아기가 울음을 터뜨린다: the one right before the sound
ADVERBS = frozenset(("같이", "많이", "깊이", "높이", "굳이", "길이", "곧이", "일찍이", "나란히"))
DELIVERY_RE = re.compile(
    r"혼잣말|혼자말|중얼|웅얼|구시렁|궁시렁|투덜|속삭|귓속말|소곤|나직|나지막|작게|조용히|낮게|큰\s*소리|소리치|"
    r"소리\s*지르|소리를\s*지르|소리\s*높여|목청|버럭|외치|외친|고함|절규|악을\s*쓰|호통|윽박|목소리|말투|어조|억양|"
    r"톤으로|사투리|방언|발음|혀\s*짧|더듬|울먹|떨리는|다급|급하게|빠르게|천천히|또박또박|속사포|힘겹게|힘없이|힘주어|"
    r"단호|퉁명|쏘아|비꼬|비웃|빈정|비아냥|능청|너스레|애원|읍소|타이르|달래|다그치|흉내|성대모사|랩|읊|낭독|낭송|선창|구호|"
    r"취한|취해|잠꼬대|잠결|반말|존댓말")
TO_RE = re.compile(r"(?:에게|한테|께|더러)$|[을를]\s*향해$")
_TOKEN_SPLIT = re.compile(r"\s*[,，、·]\s*")


def _tag_key(token: str) -> str:
    return re.sub(r"[\s.·]", "", token).upper()


def vocal_kind(t: str) -> tuple[str, str | None] | None:
    """Whether a direction is about a sound of the voice: (``vocal``, whose) when it names the
    sound itself (``한숨``, ``폭소``, ``울음을 터뜨린다``, ``한숨을 내쉬더니 …``), (``delivery``,
    None) when the words are said while making it (``웃으며``, ``웃다가 심각``, ``한숨 쉬며``,
    ``웃는 얼굴로``), None when it is no sound (``쓴웃음``, ``미소``). ``whose`` is None for the
    speaker's own sound, or the others who make it: ``학생들 폭소`` -> 학생들, ``한경의 웃음`` -> 한경."""
    m = VOCAL_RE.search(t)
    if not m or SILENT_SMILE_RE.search(t):
        return None
    words = [(w.start(), w.end(), w.group()) for w in re.finditer(r"\S+", t)]
    at = next(k for k, (a, b, _) in enumerate(words) if b > m.start() + (t[m.start()] == " "))
    if any(MANNER_END.search(w) for _, _, w in words[at:at + 2]) or VOICE_MANNER_RE.search(t):
        return "delivery", None
    whose = None
    for k, (_, _, w) in enumerate(words):
        if k == at:
            continue
        core = _PARTICLE.sub("", w)
        subject = SUBJECT_RE.fullmatch(w) if k + 1 == at and w not in ADVERBS else None
        if core in GROUP_WORDS or (core.endswith("들") and len(core) >= 2):
            whose = core                                     # 학생들 폭소, 키득거리는 학생들, 다들 웃는다
        elif k + 1 == at and w.endswith("의") and len(w) > 1:
            whose = w[:-1]                                   # 한경의 웃음
        elif subject:
            whose = subject.group(1)                         # 아기가 울음을 터뜨린다
    return "vocal", whose


def direction_type(token: str) -> tuple[str, object]:
    """Kind of one direction and its value: (``language``, name or None for Korean),
    (``sung``, True), (``source``, key), (``vocal``, whose sound — None for the speaker's
    own, see ``vocal_kind``), (``delivery``|``to``|``action``, None)."""
    t = clean(token)
    if t in LANG_ABBR:
        return "language", LANG_ABBR[t]
    m = LANG_RE.search(t)
    if m:
        return "language", LANG_NAMES[m.group(1)]
    m = LANG_SHORT_RE.fullmatch(t)
    if m:
        return "language", LANG_SHORT[m.group(1)]
    if SUNG_RE.search(t):
        return "sung", True
    if _tag_key(t) in SOURCE_TAGS:
        return "source", SOURCE_TAGS[_tag_key(t)]
    if TO_RE.search(t):
        return "to", None
    voice = vocal_kind(t)
    if voice:
        return voice
    if DELIVERY_RE.search(t):
        return "delivery", None
    return "action", None


def directions_of(inner: str) -> list[dict]:
    """The directions of one pair of parentheses, one entry per comma-separated part."""
    out = []
    for tok in _TOKEN_SPLIT.split(inner.strip()):
        if tok:
            kind, value = direction_type(tok)
            out.append({"text": tok, "type": kind, "_value": value})
    return out


def _display_name(name: str) -> str:
    return clean(name).rstrip(":：.").strip()


def _spread(name: str) -> bool:
    """A short name written with spaces between its syllables ("보 라") to line it up."""
    parts = name.split(" ")
    return len(parts) > 1 and all(len(p) == 1 for p in parts)


def parse_cue(cue: str) -> dict:
    """The speaker cell: ``{"raw", "name", "source", "language", "sung", "delivery", "vocal"}``.

    ``길동(V.O)`` and ``(NA)길동`` are 길동 with a source; ``(길동)`` is 길동 off screen;
    ``(소리)`` is a voice off screen with no name; ``(뉴스)`` is the medium itself. Several
    speakers (``길동, 춘향``) come out as ``길동/춘향``.
    """
    raw = clean(cue).rstrip(":：").strip()
    out = {"raw": raw, "name": None, "source": None, "language": None, "sung": False, "delivery": [], "vocal": []}

    def apply(token: str) -> bool:
        kind, value = direction_type(token)
        if kind == "source":
            out["source"] = out["source"] or value
        elif kind == "language":
            out["language"] = value
        elif kind == "sung":
            out["sung"] = True
        elif kind == "delivery" or (kind == "vocal" and not value):      # a sound of others is not this speaker's
            out[kind].append(clean(token))
        elif kind != "vocal":
            return False
        return True

    whole = re.fullmatch(r"[\(（]\s*([^()（）]+?)\s*[\)）]\s*\.?", raw)
    bare = SOURCE_TAGS.get(_tag_key(raw)) if not whole else None
    if bare and (bare in MEDIUM_SOURCES or _tag_key(raw) in BARE_SOURCE_CUES):
        out["source"] = bare                                 # "뉴스", "목소리" as the whole cell
        out["name"] = _display_name(raw) if bare in MEDIUM_SOURCES else None
        return out
    if whole:
        inner = whole.group(1)
        src = SOURCE_TAGS.get(_tag_key(inner))
        if src:
            out["source"] = src
            out["name"] = _display_name(inner) if src in MEDIUM_SOURCES else None
            return out
        out["source"], core = "offscreen", inner
    else:
        for tag in PAREN_RE.findall(raw):
            for tok in _TOKEN_SPLIT.split(tag.strip()):
                if tok:
                    apply(tok)
        core = PAREN_RE.sub(" ", raw)
    names = [_display_name(n) for n in re.split(r"\s*[,，、·&/]\s*", core)]
    names = [n for n in names if n and LETTER_RE.search(n)]
    out["name"] = "/".join(names) or None
    return out


def annotate(raw: str, cue: dict | None = None) -> list[dict]:
    """A dialogue as written -> its line(s), each with ``raw``, ``text`` (the spoken words
    only) and what the directions say: ``language``, ``sung``, ``source``, ``delivery``,
    ``vocal``, ``directions``.

    One dialogue is one line, except where the language or singing changes after words have
    been spoken (``(영) ... (독) ...``): the label belongs to a part of the dialogue only,
    so the dialogue is cut there. A dialogue without spoken words gives no line.
    """
    cue = cue or {}
    raw = clean(raw)
    groups = [(m.start(), m.end(), directions_of(m.group(1))) for m in PAREN_RE.finditer(raw)]
    pieces: list[dict] = []
    start = 0
    lang, sung = cue.get("language"), bool(cue.get("sung"))
    cur = {"language": lang, "sung": sung, "dirs": []}

    def has_words(a: int, b: int) -> bool:
        return bool(LETTER_RE.search(spoken(raw[a:b])))

    for a, b, dirs in groups:
        new_lang = next((d["_value"] for d in dirs if d["type"] == "language"), cur["language"])
        new_sung = cur["sung"] or any(d["type"] == "sung" for d in dirs)
        if (new_lang != cur["language"] or new_sung != cur["sung"]) and has_words(start, a):
            pieces.append({**cur, "span": (start, a)})
            start = a
            cur = {"language": new_lang, "sung": new_sung, "dirs": []}
        cur["language"], cur["sung"] = new_lang, new_sung
        cur["dirs"].extend(dirs)
    pieces.append({**cur, "span": (start, len(raw))})
    out = []
    for p in pieces:
        a, b = p["span"]
        text = spoken(raw[a:b])
        if not LETTER_RE.search(text):
            if out:                                   # directions after the last words stay with them
                out[-1]["raw"] = clean(out[-1]["raw"] + " " + raw[a:b])
                _add_directions(out[-1], p["dirs"], cue.get("name"))
            continue
        line = {"raw": clean(raw[a:b]), "text": text, "language": p["language"], "sung": p["sung"],
                "source": cue.get("source"), "delivery": list(cue.get("delivery") or []),
                "vocal": list(cue.get("vocal") or []), "directions": []}
        _add_directions(line, p["dirs"], cue.get("name"))
        out.append(line)
    return out


def _public(d: dict, speaker: str | None = None) -> dict:
    """A direction as stored: ``{"text", "type"}``, and ``by`` for a sound that others make
    (``학생들 폭소`` in someone's line: not the speaker's sound)."""
    out = {"text": d["text"], "type": d["type"]}
    by = d["_value"] if d["type"] == "vocal" else None
    if by and re.sub(r"\s+", "", by) not in {re.sub(r"\s+", "", n) for n in (speaker or "").split("/")}:
        out["by"] = by
    return out


def _add_directions(line: dict, dirs: list[dict], speaker: str | None = None) -> None:
    """Directions into a line: ``vocal`` lists the speaker's own sounds only — a sound made
    by others stays in ``directions`` with its ``by``."""
    for d in dirs:
        pub = _public(d, speaker)
        line["directions"].append(pub)
        if d["type"] == "source" and not line["source"]:
            line["source"] = d["_value"]
        elif d["type"] in ("delivery", "vocal") and "by" not in pub:
            line[d["type"]].append(d["text"])


def public_directions(raw: str, speaker: str | None = None) -> list[dict]:
    """All directions written in ``raw`` as ``[{"text", "type", "by"?}]``."""
    return [_public(d, speaker) for m in PAREN_RE.finditer(raw) for d in directions_of(m.group(1))]


# ====================================================================== the document
VERSION = 2


class Builder:
    """Collects classified items into the script document (``cboe.script`` version 2)."""

    def __init__(self, fmt: str):
        self.fmt = fmt
        self.scenes: list[dict] = []
        self.elements: list[dict] = []
        self.lines: list[dict] = []
        self.people: dict[str, dict] = {}
        self.scene_id: str | None = None
        self._last: tuple | None = None        # the dialogue added last, for a line that goes on

    def _element(self, kind: str, text: str, item: dict | None = None, **extra) -> dict:
        el = {"id": f"E{len(self.elements) + 1:04d}", "type": kind, "scene_id": self.scene_id, "text": text, **extra}
        if item and item.get("page") is not None:
            el["page"] = item["page"]
        self.elements.append(el)
        return el

    def heading(self, text: str, number=None, place=None, time=None, item: dict | None = None) -> str:
        self.scene_id = f"SC{len(self.scenes) + 1:03d}"
        self.scenes.append({"id": self.scene_id, "number": number, "heading": text, "place": place, "time": time})
        self._element("heading", text, item)
        return self.scene_id

    def other(self, kind: str, text: str, item: dict | None = None, **extra) -> None:
        self._element(kind, text, item, **extra)

    def _person(self, cue: dict) -> None:
        for name in (cue["name"] or "").split("/"):
            name = name.strip()
            if not name:
                continue
            p = self.people.setdefault(re.sub(r"\s+", "", name), {"names": Counter(), "cues": []})
            p["names"][name] += 1
            if cue["raw"] and cue["raw"] not in p["cues"]:
                p["cues"].append(cue["raw"])

    def dialogue(self, cue_text: str | None, raw: str, item: dict | None = None, cue: dict | None = None,
                 **extra) -> list[dict]:
        """Add a dialogue; returns its lines (none when it has no spoken words — then it is
        kept as an action of that speaker)."""
        cue = cue or parse_cue(cue_text or "")
        self._last = (len(self.lines), len(self.elements), cue, raw, item, extra)
        parts = annotate(raw, cue)
        if not parts:
            if LETTER_RE.search(raw) or cue["raw"]:
                self._element("action", clean(raw), item, speaker=cue["name"], cue=cue["raw"],
                              directions=public_directions(raw, cue["name"]))
            return []
        self._person(cue)
        out = []
        for part in parts:
            lid = f"L{len(self.lines) + 1:04d}"
            el = self._element("dialogue", part["raw"], item, line_id=lid, cue=cue["raw"])
            line = {"id": lid, "speaker": cue["name"], "text": part["text"], "raw": part["raw"],
                    "scene_id": self.scene_id, "element_id": el["id"], "language": part["language"],
                    "sung": part["sung"], "source": part["source"], "delivery": part["delivery"],
                    "vocal": part["vocal"], "directions": part["directions"], **extra}
            self.lines.append(line)
            out.append(line)
        return out

    def extend_last(self, more: str) -> bool:
        """The dialogue added last goes on with ``more`` (a line that starts with ``/``).
        False when there is no dialogue to go on with."""
        if self._last is None:
            return False
        n_lines, n_elements, cue, raw, item, extra = self._last
        if len(self.elements) > n_elements + (len(self.lines) - n_lines or 1):      # something came in between
            if not self.lines:
                return False
            ln = self.lines[-1]
            ln["raw"] = clean(ln["raw"] + " " + more)
            ln["text"] = clean(ln["text"] + " " + spoken(more))
            ln["directions"].extend(public_directions(more, ln["speaker"]))
            return True
        del self.lines[n_lines:], self.elements[n_elements:]
        self.dialogue(None, clean(raw + " " + more), item, cue, **extra)
        return True

    def line(self, cue: dict, text: str, **extra) -> dict:
        """Add a dialogue whose words are given as they are (a table row, a subtitle): ``text``
        is kept whole and the directions written in it are only listed."""
        self._person(cue)
        lid = f"L{len(self.lines) + 1:04d}"
        line = {"id": lid, "speaker": cue["name"], "text": text, "raw": text, "scene_id": self.scene_id,
                "element_id": None, "language": cue["language"], "sung": cue["sung"], "source": cue["source"],
                "delivery": list(cue["delivery"]), "vocal": list(cue["vocal"]),
                "directions": public_directions(text, cue["name"]), **extra}
        self.lines.append(line)
        self._last = None
        return line

    def add(self, item: dict) -> None:
        kind = item["type"]
        if kind == "heading":
            self.heading(item["text"], item.get("number"), item.get("place"), item.get("time"), item)
        elif kind == "dialogue":
            self.dialogue(item.get("cue"), item["text"], item)
        else:
            self.other(kind, item["text"], item)

    def document(self, timed: bool = False) -> dict:
        # one name per person: of the spellings that differ only in spaces the most used one,
        # a compact one before one spread out over its syllables ("보라" before "보 라")
        names = {}
        for key, p in self.people.items():
            spellings = list(p["names"])
            names[key] = min(spellings, key=lambda n: (_spread(n), -p["names"][n], spellings.index(n)))

        def same(speaker: str | None) -> str | None:
            if not speaker:
                return speaker
            return "/".join(names.get(re.sub(r"\s+", "", part), part.strip()) for part in speaker.split("/"))

        for row in self.lines + [e for e in self.elements if "speaker" in e]:
            row["speaker"] = same(row["speaker"])
        people = [{"id": f"P{k:02d}", "name": names[key], "cues": p["cues"]}
                  for k, (key, p) in enumerate(self.people.items(), 1)]
        return {"version": VERSION, "format": self.fmt, "source": None, "timed": timed,
                "characters": [p["name"] for p in people], "people": people, "scenes": self.scenes,
                "elements": self.elements, "lines": self.lines}


def build(items: list[dict], fmt: str = "screenplay") -> dict:
    """Classified items -> the script document (without ``source``)."""
    b = Builder(fmt)
    for it in items:
        b.add(it)
    return b.document()


def read_pdf(path) -> dict | list[str]:
    """A PDF script: the document when it is laid out as a shooting script, otherwise its
    text lines (for the caller to read like a text file)."""
    lines = pdf_lines(Path(path))
    items = classify_positioned(lines)
    if items is None:
        return positioned_text(lines)
    doc = build(items)
    doc["layout"] = "columns"
    return doc
