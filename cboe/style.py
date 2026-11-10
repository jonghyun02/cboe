# -*- coding: utf-8 -*-
"""지침 숫자와 표기 틀 (한 곳): the limits and the notation of the Netflix Korean SDH guide.

Sources and the short names used in the code and in the checker's reports:

- ``II.n`` / ``I.n``  Korean Timed Text Style Guide. Chapter II is SDH; chapter I applies where II
  has no rule of its own (II opens with "In case of conflicting instructions, the guidelines in
  this section should take precedence over those of Section I" = 지시가 부딪히면 II장이 I장보다
  우선한다), which is the case for punctuation (I.13) and ellipses (I.4).
- ``TG n``  Timed Text Style Guide: Subtitle Timing Guidelines.
- ``GR n``  Timed Text Style Guide: General Requirements.

Placement, export, the checker, the review server and its page take every limit and every
notation template from this module only, so another profile or a guide revision is one edit
here. The page gets the values from the server (``Style.public()``).

Numbers: ``Style`` (``get("adult" | "children")``, ``for_project(project.json)``).
Notation: ``speaker_label`` ``[이름/출처·말투/언어] ``, ``joint_name`` ``영희, 철수`` | ``함께``,
``lyric`` ``♪ … ♪``, ``bracket`` / ``sfx_phrase`` / ``sfx_with_source`` / ``music_phrase``,
``tidy_line`` (표시용 문장부호 다듬기, 낱말은 그대로), ``modernize_phrase`` / ``modernize_line``
(예전 작업 폴더의 ``♬ …`` 와 ``(이름)`` 을 새 꼴로).
"""
from __future__ import annotations

import functools
import re
from dataclasses import dataclass, fields

NOTATION = 2          # version of the notation below; a draft written with another one is recomputed

# --------------------------------------------------------------------------- numbers

MAX_UNITS = 16.0      # II.2 "16 characters per line" (한 줄 16자)
MAX_LINES = 2         # II.4 "Maximum 2 lines." (최대 2줄)
MIN_DUR = 5 / 6       # GR 1 "Minimum duration: 5/6 (five-sixths) of a second per subtitle event" (자막 하나에 최소 5/6초)
MAX_DUR = 7.0         # GR 1 "Maximum duration: 7 seconds per subtitle event" (자막 하나에 최대 7초)
GAP_FRAMES = 2        # TG 5 "Subtitles must have a minimum of 2 frames between them." (자막 사이 최소 2프레임)
HALF = 0.5            # TG "half a second" (0.5초 = 24fps 12프레임, 30fps 15프레임, 60fps 30프레임)
DEFAULT_FPS = 24.0    # TG "These guidelines are written for content in 24 fps" (이 지침은 24fps 기준으로 적었다)

# How close (relative) a frame rate has to be to a whole rate or an NTSC rate (whole x 1000/1001)
# to be taken as that rate. ffprobe's avg_frame_rate is frames over duration and wobbles a little:
# a 30 fps video gave 30.00001486621153 (5e-7 off), which the TTML header must not write as
# 31 x 22198001/22937923 (4th round check), and 24.00001 counted 5/6 s as 21 frames, one more than
# at 24 (5th round check). 29999/1251 (23.98002, 1.7e-4 off 23.976 and 8e-4 off 24) is a rate of its
# own and stays as it is. Over two hours 1e-5 is under a tenth of a second.
RATE_TOLERANCE = 1e-5


def near_rate(fps: float, rate: float) -> bool:
    """Whether ``fps`` is within ``RATE_TOLERANCE`` of ``rate``."""
    return abs(fps - rate) <= RATE_TOLERANCE * rate


def nominal_fps(fps) -> float:
    """The frame rate the limits are counted at (5/6 s, 7 s, half a second, the reading speed in
    frames): the whole or NTSC rate ``fps`` lies within ``RATE_TOLERANCE`` of (24.00001 -> 24,
    30.000015 -> 30, 23.976 -> 24000/1001), else ``fps`` itself (29999/1251 stays). Placement, the
    checker and the TTML writer (``export.frame_rate``, the header) all go by it, so that a cell of
    20 frames is 5/6 s in each of them. The frame grid (time <-> frame) stays at ``fps``."""
    f = float(fps)
    whole = max(1, round(f))
    for rate in (float(whole), whole * 1000 / 1001):
        if near_rate(f, rate):
            return rate
    return f

# II.3 "Adult programs: Up to 14 characters per second / Children's programs: Up to 11 characters
# per second" (성인 프로그램 초당 14자까지, 어린이 프로그램 초당 11자까지)
PROFILES = {"adult": {"cps": 14.0, "label": "성인"}, "children": {"cps": 11.0, "label": "어린이"}}
DEFAULT_PROFILE = "adult"
_PROFILE_NAMES = {"adult": "adult", "성인": "adult", "children": "children", "child": "children",
                  "kids": "children", "어린이": "children"}


def profile_name(value=None) -> str:
    """``project.json`` 의 ``profile`` 값 -> "adult" | "children" (None or "" = adult)."""
    if value is None or str(value).strip() == "":
        return DEFAULT_PROFILE
    key = str(value).strip().lower()
    if key not in _PROFILE_NAMES:
        raise ValueError(f"모르는 프로필입니다: {value!r} (adult 또는 children)")
    return _PROFILE_NAMES[key]


@dataclass(frozen=True)
class Style:
    """The numbers of one profile. The first eight are guide clauses; the rest are this tool's own
    choices where the guide gives no number (they live here so that nothing is written twice)."""
    profile: str = DEFAULT_PROFILE
    max_units: float = MAX_UNITS
    max_lines: int = MAX_LINES
    cps: float = PROFILES[DEFAULT_PROFILE]["cps"]
    min_dur: float = MIN_DUR
    max_dur: float = MAX_DUR
    gap_frames: int = GAP_FRAMES
    half: float = HALF
    # TG 1 "the out-time should ideally be at least half a second past the timecode at which the
    # audio ends" (끝 시각은 소리가 끝난 뒤 0.5초 이상이 좋다)
    tail: float = HALF
    # TG 4 "when the dialogue starts 9, 10 or 11 frames before the shot change in 24 fps content"
    # (24fps 에서 대사가 전환 9~11프레임 앞에서 시작할 때): 9 frames at 24 fps = 0.375 s
    near_cut: float = 0.375
    label_extra: float = 1.0      # ours (BBC 4.3.3): a sound label is shown its reading time plus a second
    music_dur: float = 3.0        # ours (10-13 memo 5.2): a music label is shown 3 s
    merge_window: float = HALF    # ours (memo 5.3): a sound within 0.5 s of a cell start joins that cell
    flash_dur: float = 0.25       # ours: a cell shorter than this cannot be read at all ('몇 프레임짜리 칸')

    @property
    def label(self) -> str:
        return PROFILES[self.profile]["label"]

    def settings(self) -> dict:
        """The numbers as ``placement.place(settings=...)`` takes them."""
        return {f.name: getattr(self, f.name) for f in fields(self)}

    def public(self) -> dict:
        """Numbers and notation for the review page (JSON)."""
        d = self.settings()
        d.update(profile_label=self.label, profiles={k: v["label"] for k, v in PROFILES.items()},
                 notation=NOTATION, open=OPEN, close=CLOSE, label_sep=LABEL_SEP, name_split=NAME_SPLIT,
                 joint_sep=JOINT_SEP, joint_all=JOINT_ALL, joint_max_units=JOINT_MAX_UNITS, hyphen=HYPHEN,
                 note=NOTE, music=MUSIC, source_sep=SOURCE_SEP, ellipsis=ELLIPSIS)
        return d


def get(profile=None) -> Style:
    name = profile_name(profile)
    return Style(profile=name, cps=PROFILES[name]["cps"])


def seconds_text(seconds: float) -> str:
    """A duration limit as it is written in messages: '5/6' for five sixths of a second, else '7'."""
    return "5/6" if abs(seconds - MIN_DUR) < 1e-9 else f"{seconds:g}"


def for_project(meta) -> Style:
    """The style of a work folder: ``project.json`` ``"profile"`` (adult when it has none)."""
    return get((meta or {}).get("profile"))


# --------------------------------------------------------------------------- notation

# II.8 "Use parentheses [ ] to enclose speaker IDs." (화자 표시는 [ ] 로 감싼다)
# II.9 "Use brackets [ ] to enclose sound effects." (효과음은 [ ] 로 감싼다)
OPEN, CLOSE = "[", "]"
LABEL_SEP = "/"           # II.8 [영희/속마음/영어] "place the language label at the end" (언어 표시는 맨 뒤)
NAME_SPLIT = "/"          # a script speaker '에루/마야카': both speak together (script.py)
JOINT_SEP = ", "          # II.10 "[영희, 철수] 설마! (O)"
JOINT_ALL = "함께"         # II.10 "[함께] 설마! (O)"
JOINT_MAX_UNITS = 8.0     # ours: a joint label wider than half a line becomes [함께] (II.10: keep labels short)
HYPHEN = "- "             # I.6, II.5 "Use a hyphen followed by a space" (하이픈 뒤에 빈칸)
# II.7 "Song lyrics should be enclosed with a music note (♪) at the beginning and end of each
# subtitle." (가사는 자막마다 처음과 끝에 음표를 붙인다). ♬ is not in the guide.
NOTE = "♪"
MUSIC = "음악"             # II.7 [경쾌한 음악]
SOURCE_SEP = " - "        # II.9 [달칵 - 안전띠]
ELLIPSIS = "…"            # I.4 "use the single smart character (U+2026)" (한 글자짜리 말줄임표)
OLD_NOTES = "♬♫♩"         # music marks of older work folders ('♬ 무서운 음악')
# II.6 "For the original language spoken (e.g, 한국어), there is no need to add a language
# identifier." (원래 언어에는 언어 표시를 달지 않는다)
NATIVE = ("한국어", "한국말", "우리말", "korean", "ko")


def _norm(text) -> str:
    return " ".join(str(text or "").split())


def bracket(text) -> str:
    """``[text]`` (a label that already has brackets keeps one pair); "" for no text."""
    s = _norm(text)
    while s.startswith(OPEN):
        s = s[1:].lstrip()
    while s.endswith(CLOSE):
        s = s[:-1].rstrip()
    return f"{OPEN}{s}{CLOSE}" if s else ""


def joint_name(name) -> str:
    """The name part of a speaker label. '에루/마야카' (both speak) -> '에루, 마야카'; a joint label
    wider than ``JOINT_MAX_UNITS`` -> '함께' (II.10: '[영희와 철수]' (X), '[영희, 철수]' (O), '[함께]' (O))."""
    names = [n.strip("[] ") for n in (_norm(x) for x in str(name or "").split(NAME_SPLIT))]
    names = list(dict.fromkeys(n for n in names if n))
    if len(names) < 2:
        return names[0] if names else ""
    from .textutil import units                       # here, not at the top: textutil imports this module
    joined = JOINT_SEP.join(names)
    return joined if units(OPEN + joined + CLOSE) <= JOINT_MAX_UNITS + 1e-9 else JOINT_ALL


def speaker_label(name=None, extras=(), language=None) -> str:
    """``[이름/출처·말투/언어] `` with its trailing space, or "" when there is nothing to show
    (II.8: name first, the language last; II.6: ``[영어] `` alone when no name is shown)."""
    if isinstance(extras, str):
        extras = [extras]
    lang = _norm(language).strip("[] ")
    if lang.lower() in NATIVE:
        lang = ""
    parts = [joint_name(name)] + [_norm(x).strip("[] ") for x in (extras or ())]
    parts = [p for p in dict.fromkeys(parts) if p and p != lang]
    if lang:
        parts.append(lang)
    return f"{OPEN}{LABEL_SEP.join(parts)}{CLOSE} " if parts else ""


# II.8 [영희/영상], [영희/속마음]: the word for where a line comes from (script.json ``source``).
# "offscreen" has none: showing the name is what tells an off-screen speaker.
SOURCE_LABELS = {"inner": "속마음", "video": "영상", "phone": "전화", "radio": "무전", "broadcast": "방송",
                 "narration": "내레이션"}


def source_label(source) -> str:
    """The label word for a script ``source`` ("inner" -> "속마음"); "" when there is none. For the
    caller that fills ``label_extra``; placement itself shows only what ``label_extra`` holds."""
    return SOURCE_LABELS.get(str(source or "").strip().lower(), "")


def line_labels(ln: dict, show_name=None) -> tuple[str, str]:
    """(label of the first piece, label of each later piece) of one dialogue line dict
    (``name``, ``show_name``, ``label_extra``, ``language``). A later piece repeats the language
    only (II.6 "use [in language] at the beginning of the dialogue in each event" = 자막마다 대사
    앞에 [언어])."""
    show = ln.get("show_name") if show_name is None else show_name
    lang = ln.get("language")
    return (speaker_label(ln.get("name") if show else None, ln.get("label_extra") or (), lang),
            speaker_label(None, (), lang))


def lyric(text) -> str:
    """``♪ 가사 ♪`` (II.7 "Add a space between the music note and the preceding or subsequent
    text" = 음표와 글 사이에 빈칸)."""
    s = _norm(text).strip(NOTE).strip()
    return f"{NOTE} {s} {NOTE}" if s else ""


def sfx_phrase(ono=None, desc=None) -> str:
    """The default caption of a sound: ``[의성어]`` when there is one, else ``[설명]`` (II.9 "Use
    onomatopoeic wording to create more intuitive descriptions when possible" = 될 수 있으면 의성어)."""
    return bracket(ono) or bracket(desc)


def sfx_with_source(ono, source=None) -> str:
    """``[의성어 - 출처]`` (II.9 [직 - 지퍼], [달칵 - 안전띠]); ``[의성어]`` without a source."""
    o, s = bracket(ono)[1:-1], bracket(source)[1:-1]
    return f"{OPEN}{o}{SOURCE_SEP}{s}{CLOSE}" if o and s else bracket(o or s)


def music_phrase(*words) -> str:
    """``[경쾌한 음악]`` from the words given; ``[음악]`` when nothing is known (II.7)."""
    return bracket(" ".join(w for w in (_norm(x) for x in words) if w) or MUSIC)


# --------------------------------------------------------------------------- display punctuation

_DOT_RUN = re.compile(r"[.…]{2,}")                    # '..', '...', '….': one … (I.4)
_ENDS = ".,．，。、"                                    # never at the end of a subtitle line (I.13)
_QUOTE_END = re.compile(r"['\"’”」』]+$")              # a closing quote after the last word: '난 안 그랬어.'
_NOTE_END = re.compile(r"\s*" + NOTE + r"\s*$")
_BEFORE = r"(?<=[^\s.…,?!\dA-Za-z．。，、])[.．。]"      # a sentence period: not 3.5, J. 심슨, 1992. 3., ?.
_BEFORE_LABEL = re.compile(_BEFORE + r"(?=\s+\[)")    # '가자. [한숨]': the sentence ends, a label follows
_SENTENCE = re.compile(_BEFORE + r"(?=\s+\S)")        # '사랑해. 그게': two sentences in one line


def _tidy_once(s: str) -> str:
    s = _DOT_RUN.sub(lambda m: ELLIPSIS if "." in m.group(0) else m.group(0), s)
    tail = ""
    m = _NOTE_END.search(s)
    if m and m.start() > 0:                           # the closing note of a lyric stays last
        s, tail = s[:m.start()], " " + NOTE
    s = s.rstrip()
    q = _QUOTE_END.search(s)
    if q and q.start() > 0 and s[q.start() - 1] in _ENDS:     # ... and so does a closing quote
        s, tail = s[:q.start()], q.group(0) + tail
    while s and s[-1] in _ENDS:
        s = s[:-1].rstrip()
    s = _SENTENCE.sub(",", _BEFORE_LABEL.sub("", s))
    return s + tail if s else ""


@functools.lru_cache(maxsize=65536)
def _tidy(s: str) -> str:
    for _ in range(6):                                # '가자.♪.' needs a second look; then nothing changes
        t = _tidy_once(s)
        if t == s:
            break
        s = t
    return s


def tidy_line(line) -> str:
    """One displayed line with its punctuation as the guide wants it; the words are not changed.

    - no period or comma at the end of the line (I.13 "There should never be a period or comma at
      the end of a subtitle line" = 자막 줄 끝에는 마침표나 쉼표가 절대 없어야 한다), also in front
      of the closing ♪ of a lyric (I.17) and of a closing quote (the guide writes '난 안 그랬어');
    - a period between two sentences in the line becomes a comma (I.13 "Commas should be used when
      separating two sentences within a subtitle" = 한 자막 안의 두 문장은 쉼표로 나눈다);
    - ``..`` / ``...`` become ``…`` (I.4).
    ``?`` ``!`` ``…`` and a point between digits (3.5), after a digit (1992. 3.) or after a Latin
    letter (J. 심슨, Mr. 김) stay inside a line; so a sentence that ends in a number or a Latin word
    keeps its period there (it is still taken off at the end of the line).
    Apply it to lines that are already split and wrapped: where to split and break is decided on
    the text as written, because sentence ends are the preferred places. A line that was only
    punctuation comes back empty.
    """
    return _tidy(_norm(line))


def tidy_lines(lines) -> list[str]:
    return [t for t in (tidy_line(x) for x in (lines or [])) if t]


# --------------------------------------------------------------------------- older work folders

_OLD_MUSIC = re.compile(rf"^\s*[{OLD_NOTES}]\s*(.*?)\s*$")
_OLD_PHRASE = re.compile(rf"^\s*[{OLD_NOTES}]|[{OLD_NOTES}]\s*$")
_OLD_NOTE = re.compile(rf"[{OLD_NOTES}]")
_OLD_NAME = re.compile(r"^(-\s*)?\(([^()]{1,30})\)(\s+\S.*)$")


def modernize_phrase(text) -> str:
    """A sound phrase stored by an older version, in today's notation: '♬ 무서운 음악' and
    '♬ 무서운 음악 ♬' -> '[무서운 음악]', '♬' -> '[음악]' (no ♬ ♫ ♩ is left, at the ends or inside:
    the guide has none). Anything else comes back as it is (white space collapsed)."""
    s = _norm(text)
    if not _OLD_PHRASE.search(s):
        return s
    return bracket(_OLD_NOTE.sub(" ", s)) or bracket(MUSIC)


def modernize_line(line) -> str:
    """A cell line of an older draft, in today's notation: '(빌) 뭐?' -> '[빌] 뭐?',
    '- ♬ 슬픈 음악' -> '- [슬픈 음악]'."""
    s = _norm(line)
    m = _OLD_NAME.match(s)
    if m:
        return f"{HYPHEN if m.group(1) else ''}{OPEN}{m.group(2).strip()}{CLOSE}{m.group(3)}"
    head = HYPHEN if s.startswith(HYPHEN) else ""
    rest = s[len(head):]
    return head + modernize_phrase(rest) if _OLD_MUSIC.match(rest) else s
