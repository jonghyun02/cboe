# -*- coding: utf-8 -*-
"""대본 단서: the sounds, music, silences and speakers' own sounds that a shooting script writes
down, as cues with a suggested caption and the time window they can fall in.

A screenplay says '창밖에서 쿵! 쿵! 소리가 난다' in an action line and '(한숨)' in a dialogue — the
sound detector has to find these again from the audio alone, and the plot judgement only had
the dialogue around a sound (감사 4-01, 4-02, 8-05, 8-12). This module reads them out of
``script.json`` (version 2: ``elements`` and ``lines``); pure functions, no files.

Cue types:
- ``sfx``: a sound named in an action line — a sound word ('소리', '총성', '사이렌', '들린다',
  '울린다' …) or an onomatopoeia ('쾅', '끼익', '펑!').
- ``music``: music or a song — '음악', '노래', '흥얼', '흘러나온다' with music, a song title
  with its singer ("가수의 '곡명'", II.7 "Use song title identifiers when applicable").
- ``silence``: '침묵', '정적', '적막', the music stopping ('음악이 멈춘다', II.9
  "including important moments of silence … [음악이 멈춘다]").
- ``vocal``: a speaker's own sound written in the dialogue ('(한숨)', '(웃음)'; script.json
  ``vocal`` of a line, or a dialogue without words kept as an action of its speaker).

Every cue carries the written text, its key words, the suggested captions in the guide's forms
(II.9: ``[의성어]`` first when there is one, ``[의성어 - 출처]``, ``[설명]``; II.7: the song title
in quotes, ``[가수의 '곡명']`` / ``[가수의 '곡명'이 흐른다]``) and where it stands: the scene and
the dialogue lines before and after it. ``build`` adds the time window from the aligned
dialogue (``dialogue.json``) and the scene times (``scenes.json``). A '…소리' description is
a choice only, never a cue's caption (II.9: "try not to describe the sound as 들린다 … or
소리"); a sound cue that has nothing else is ``vague`` and suggests no caption. A line whose
only sound words are '소리' / '들린다' with nothing that names the sound is no cue.

A cue is a hint, not a caption: whether the sound is in the finished film, and how exactly the
window places it, was never measured (감사 4-01 검증) — callers attach it to a detected sound
as evidence or show it to the writer as 확인 필요; they never include it on their own. The word
lists are general Korean sound vocabulary, not one script's.
"""
from __future__ import annotations

import re
from bisect import bisect_right

from . import phrases, style

TYPES = ("sfx", "music", "silence", "vocal")
MATCH_PAD_S = 1.0          # a detected sound may start a little before / after the window
WIDE_S = 30.0              # a window this long says little about where the sound is
SHORT = 60                 # characters of an action line shown as context
NEAR_CHARS = 15            # an onomatopoeia and the source written this close make '[의성어 - 출처]'

# ----------------------------------------------------------------------------- word lists
# Text that holds a sound word but names no sound: removed before the sound words are looked for.
NOT_SOUND = re.compile(
    r"목소리|방송국|방송반|방송실|노래방|음악\s*감상실|음악감상실|음악실|음악\s*선생|음악\s*시간|음악\s*책|"
    r"잔소리|헛소리|군소리|딴소리|콧소리|혼잣소리|소리꾼|큰\s*소리로|큰소리\s*[치친쳐]|"
    r"소리\s*(?:없이|죽여|를\s*죽|도\s*없이|도\s*못|를?\s*내지|를?\s*낮)|소리없이|"
    r"소리\s*(?:[치친쳐]|를?\s*지르|를?\s*질러|지른|를?\s*높)|"
    r"(?:그|이|저)\s+소리|무슨\s*소리(?:인가|야|냐|예요|에요|지|람|래)|"
    r"울음[을이]?\s*(?:삼키|참|그치|그친|멈추|억누르|꾹)|웃음[을이]?\s*(?:참|삼키|억누르|꾹)|"
    r"들려\s*있|들려있|들린\s*채|(?:손|양손|두\s*손|품|팔|어깨|등)에\s*들[려린리]|들려\s*나[가오간온갔왔]|"
    r"(?<!귀)에\s*들려(?!\s*[오온왔])|"                      # '가게에 들려 …' (들르다), not '귀에 들려온다'
    r"(?:들리|울리)지\s*(?:않|도\s*않|를\s*않|못)|안\s*(?:들리|들려|들린|울리|울린)|"   # a sound that is not heard
    r"(?:터뜨릴|터질|터트릴|날|낼|울릴|들릴)\s*(?:것\s*같|듯)|"                    # '웃음이 터질 듯한 표정'
    r"(?:가슴|마음|심금)을\s*울리|울리지\s*마|"
    r"(?:짜증|분노|감정|화|울화|울분|성질|불만|인내심|참았던\s*[가-힣]+)[가이]?\s*폭발|폭발할\s*듯|폭발하듯|폭발적|[을를]\s*폭발|"
    r"한숨\s*(?:돌|도\s*못|도\s*안|자)|"
    r"우스꽝|깔깔이|멍멍이|야옹이|철[석썩]같이|빵빵(?:한|하게|해|히)|소리소리|"   # words that only hold a sound's letters
    r"덜컥\s*(?:겁|걱정|불안|약속|계약|믿)|훌쩍\s*(?:떠나|떠난|자라|자란|커|컸|넘|뛰어|지나|날아)")
SOUND_WORD = re.compile(
    r"소리|소음|굉음|폭음|폭발음|포성|총성|총격|발포|경적|클랙슨|클락션|클락숀|크랙션|크락션|사이렌|싸이렌|초인종|노크|"
    r"함성|비명|고함|환호|박수|아우성|웅성|야유|왁자지껄|시끌|시끄러|"
    r"통곡|오열|흐느|기침|한숨|천둥|메아리|폭발|급브레이크|급정거|(?:거친|가쁜)\s*(?:숨|호흡)|"
    r"웃음\s*(?:이\s*)?(?:터|소리)|웃음을\s*터뜨|폭소|울음[을이]?\s*(?:터|소리)|"
    # at the start of a word only: '흔들린다', '어울린다', '휘둘린다' hold the same letters
    r"(?<![가-힣])(?:울리|울려|울린|들리|들려|들린)")
# onomatopoeia that is a sound wherever it is written (at the start of a word: '우스꽝스러운' is none)
ONO_STRONG = (
    "콰르릉", "우르릉", "쿠궁", "콰광", "쾅", "꽝", "쿵", "끼이익", "끼기긱", "끼익", "덜컹", "덜컥", "철커덕", "철컥",
    "철컹", "찰칵", "따르릉", "딩동", "띵동", "우당탕", "와장창", "쨍그랑", "딸깍", "달칵", "드르륵", "부르릉", "부릉",
    "부아앙", "빵빵", "빠앙", "삐익", "삐삐", "탕탕", "따다다", "타타탕", "두두두", "콜록", "쿨럭", "켁켁", "캑캑",
    "깔깔", "낄낄", "킥킥", "키득", "엉엉", "흑흑", "훌쩍", "째깍", "똑딱", "철썩", "첨벙", "풍덩", "바스락", "달그락",
    "덜그럭", "지글지글", "웅성", "저벅", "뚜벅", "터벅", "멍멍", "왈왈", "야옹", "뿌우", "드르렁", "헉헉", "삐걱")
# onomatopoeia that is a sound only when written as one: '펑!', '탕-', '‘짝’', '퍽 하는', '탁 소리'
# ('텅 빈', '툭 치는', '울음 뚝', '매운탕' are not)
ONO_WEAK = ("휘익", "쉬익", "치익", "덜덜", "펑", "탕", "빵", "탁", "퍽", "툭", "휙", "쉭", "슝", "짝", "뚝", "삐", "텅", "팍",
            "쏴", "똑똑", "콰", "풉", "딱", "쩍", "휭", "윙", "칙칙", "찰싹", "푸드덕")
_ALT = lambda words: "|".join(sorted(map(re.escape, words), key=len, reverse=True))  # noqa: E731
ONO_STRONG_RE = re.compile(rf"(?<!쿵)(?:{_ALT(ONO_STRONG)})(?!푸)")      # word start: ``_strong_onos``
# a sound written as one though no table has it: 'XX-' or 'XX!' right before '하는', '소리', '울린다' …
# ('꽈당- 소리가 난다', '딸랑- 하고'); its last syllable ends in a consonant sounds end in, or it repeats
WRITTEN_ONO_RE = re.compile(r"(?<![가-힣])([가-힣]{1,4})(?:[-~!]+(?=\s*(?:하는|하고|하며|하더니|하자|하면|한다|소리|"
                            r"(?<![가-힣])울[리려린]|(?<![가-힣])들[리려린]))|(?=하는\s*소리))")     # also '윽하는 소리'
SORI_AFTER = re.compile(r"[-~!]*\s*(?:하는\s*)?[가-힣]{0,3}소리")    # '딸랑- 소리', '띵동- 하는 벨소리'
_ONO_FINALS = {1, 2, 8, 17, 19, 20, 21}          # ㄱ ㄲ ㄹ ㅂ ㅅ ㅆ ㅇ: '꽈당', '딸랑', '삐비빅', '털털털'
_TENSE_INITIALS = {1, 4, 8, 10, 13, 14, 15, 16, 17}   # ㄲ ㄸ ㅃ ㅆ ㅉ ㅊ ㅋ ㅌ ㅍ
_NOT_ONO = {"충성", "경례", "정말", "진짜", "그럼", "이놈", "저놈", "선생", "학생", "형", "동생", "사랑", "만세", "응", "안녕",
            "경찰", "얼른", "일어나", "멈춰", "씨발", "씨팔", "시발", "썅", "쌍", "젠장", "제길", "이런씨발", "칫", "쳇"}
ONO_WEAK_RE = re.compile(
    rf"(?<![가-힣])(?:{_ALT(ONO_WEAK)})(?:(?:{_ALT(ONO_WEAK)}))*"
    r"(?=\s*[!~…\-’'\"”」]|\s*(?:하는|하고|하며|하더니|하자|하면|한다|하고는|소리))")
_ONO_ANY = re.compile(rf"(?:{_ALT(ONO_STRONG + ONO_WEAK)})")
OFFSCREEN = re.compile(r"멀리서|먼\s*곳에서|밖에서|바깥에서|어디선가|어디에선가|어디서인가|화면\s*밖|문\s*밖|창\s*밖|"
                       r"벽\s*너머|너머로|너머에서|\((?:O\.?S\.?|E|V\.?O\.?|off)\)", re.I)

# What makes a sound, for naming the cue and matching it to a detected sound: (words,
# '- 출처' noun or None, onomatopoeia or None, description, families of cboe/data/sfx_classes.tsv).
# The first entry that matches names the cue; the families of every entry that matches count.
SOURCES = (
    (r"기관총|따발총|연발\s*사격", "기관총", "타타탕", "총성", ("gun", "explosion")),
    (r"총성|총소리|총격|총알|총탄|총을|총이|총구|발포|사격|(?<![가-힣])(?:탕탕|타앙)", "총", "탕", "총성", ("gun", "explosion")),
    (r"폭죽|불꽃놀이", "폭죽", "펑", "폭죽", ("explosion",)),
    (r"폭발|폭음|폭탄|포탄|포성|포격|수류탄|최루탄", None, "쾅", "폭발음", ("explosion", "gun")),
    (r"노크|두드리|두드린|두들기|똑똑", "문", "똑똑", "노크", ("door", "impact", "hit")),
    # also with the onomatopoeia between the door and its verb: '문이 끼익 열린다', '철문이 삐걱- 닫힌다',
    # '문이 끼익거리며 닫힌다', '문이 끼익, 열린다', '문을 끼익 연다'; and before '소리를 내며 / 소리와
    # 함께' ('문이 끼익 소리를 내며 열린다'), the word ``sori``: only the door's own sound when it is an
    # onomatopoeia (``_door_sound``) — '문이 사이렌 소리와 함께 열린다' is a siren
    (r"문[이을]?\s*(?:쾅|닫|열|여닫)|"
     r"문[이을]?\s+[가-힣]{1,4}(?:(?:거리|대)(?:며|고))?[-~!,]*\s*(?:(?:하고|하며|하더니)\s*)?"
     r"(?:닫|열|여닫|연다|연(?![가-힣]))|"
     r"문[이을]?\s+(?P<sori>[가-힣]{1,4})(?:(?:거리|대)(?:며|고))?[-~!,]*\s*"
     r"(?:하는\s*)?소리(?:를\s*내(?:며|고)|와\s*함께)\s*(?:닫|열|여닫|연다|연(?![가-힣]))|"
     r"문\s*닫히|문\s*열리|문소리|문\s*여는|문\s*닫는", "문", "덜컥", "문소리", ("door", "impact")),
    (r"전화벨|벨이?\s*울|벨소리|따르릉|휴대폰|핸드폰|휴대전화|전화가?\s*(?:울|온|와)|"
     r"울리는\s*(?:[가-힣]+\s+){0,3}전화(?![가-힣])", "전화", "따르릉", "전화벨", ("phone", "alarm", "bell")),
    (r"인터폰", "인터폰", None, "인터폰", ("doorbell", "phone", "bell", "alarm")),
    (r"초인종|딩동|띵동", "초인종", "딩동", "초인종", ("doorbell", "bell", "alarm")),
    (r"자명종|알람", "알람", "따르릉", "알람", ("alarm", "clock", "bell")),
    (r"사이렌|싸이렌|경보", "사이렌", "위이잉", "사이렌", ("siren", "alarm")),
    (r"경적|클랙슨|클락션|클락숀|크랙션|크락션|빵빵|빠앙", "경적", "빵빵", "경적", ("horn", "car")),
    (r"뱃고동|고동\s*소리|고동이?\s*울", "뱃고동", "부웅", "뱃고동", ("horn", "boat")),
    (r"급브레이크|급정거|브레이크|끼이익|끼익|끼기긱|타이어", None, "끼익", "급정거", ("car", "scrape", "creak")),
    (r"헬기|헬리콥터|프로펠러", "헬기", "두두두", "헬기", ("aircraft",)),
    (r"비행기|제트기|전투기", "비행기", "슈웅", "비행기", ("aircraft",)),
    (r"기차|열차|기적\s*소리", "기차", "덜컹덜컹", "기차", ("train", "horn")),
    (r"엔진|시동|부르릉|부릉|부아앙|오토바이|자동차|트럭|버스|차\s*소리", "엔진", "부릉", "엔진", ("car",)),
    (r"천둥|우르릉|콰르릉|번개", "천둥", "우르릉", "천둥", ("thunder", "rain", "explosion")),
    (r"빗소리|빗줄기|빗방울|소나기|장대비|비가?\s*(?:내리|쏟|온|오는)", "비", "쏴", "빗소리", ("rain", "water")),
    (r"파도|철썩", "파도", "철썩", "파도", ("sea", "water")),
    (r"바람", "바람", "휘잉", "바람", ("wind",)),
    (r"물소리|첨벙|풍덩|물방울|수돗물|샤워|폭포|개울|시냇물", "물", "첨벙", "물소리", ("water", "stream")),
    (r"유리|깨지|깨진|깨뜨|와장창|쨍그랑|박살", "유리", "와장창", "깨지는 소리", ("glass", "break", "impact")),
    (r"발소리|발자국|발걸음|저벅|뚜벅|터벅|구둣발|군홧발", None, "저벅저벅", "발소리", ("footsteps",)),
    (r"박수|짝짝", None, "짝짝짝", "박수", ("crowd_reaction", "hit")),
    (r"함성", None, None, "함성", ("crowd", "crowd_reaction", "scream")),
    (r"환호", None, None, "환호", ("crowd_reaction", "crowd", "scream")),
    (r"야유", None, None, "야유", ("crowd_reaction", "crowd")),
    (r"아우성|웅성|왁자지껄|시끌|소란|떠들썩", None, "웅성웅성", "웅성거림", ("crowd", "crowd_reaction")),
    (r"인기척", None, None, "인기척", ("footsteps", "door")),
    (r"비명", None, None, "비명", ("scream",)),
    (r"고함|절규", None, None, "고함", ("scream",)),
    (r"흐느", None, None, "흐느낀다", ("crying",)),
    (r"울음|통곡|오열|엉엉|흑흑|훌쩍|(?<![가-힣])우는\s*소리", None, None, "울음", ("crying",)),
    (r"웃음|폭소|깔깔|낄낄|킥킥|키득|(?<![가-힣])웃는\s*소리", None, None, "웃음", ("laughter",)),
    (r"기침|콜록|쿨럭|켁켁|캑캑", None, "콜록", "기침", ("cough",)),
    (r"(?:거친|가쁜)\s*(?:숨|호흡)|헐떡|헉헉|숨을\s*몰아", None, None, "거친 숨", ("breath",)),
    (r"한숨|숨소리", None, None, "한숨", ("breath",)),
    (r"개\s*짖|짖는|짖어|멍멍|왈왈", "개", "멍멍", "개가 짖는다", ("dog",)),
    (r"고양이|야옹", "고양이", "야옹", "고양이 울음", ("cat",)),
    (r"매미", "매미", "맴맴", "매미 울음", ("insect",)),
    (r"새소리|지저귀|까마귀|까치", "새", "짹짹", "새소리", ("bird",)),
    (r"시계|초침|째깍|똑딱", "시계", "째깍째깍", "초침 소리", ("clock",)),
    (r"종소리|종이?\s*울|교회\s*종|(?<![가-힣])종[을]?\s*(?:[가-힣]+\s+)?울[리려린]|"
     r"울리는\s*(?:[가-힣]+\s+){0,2}종(?![가-힣])", "종", "댕", "종소리", ("bell",)),
    (r"호루라기|호각", "호루라기", "삐익", "호루라기", ("whistle",)),
    (r"타자기|키보드|자판", "키보드", "타닥타닥", "자판 소리", ("typing",)),
    (r"카메라|셔터|찰칵|플래시", "카메라", "찰칵", "셔터 소리", ("camera",)),
    (r"뺨|따귀|싸대기|찰싹|퍽|주먹|때리|구타|후려", None, "퍽", "때리는 소리", ("hit", "impact")),
    (r"충돌|들이받|부딪", None, "쾅", "부딪히는 소리", ("impact", "car", "break")),
    (r"쿵|쾅|꽝|넘어지|쓰러지|떨어지", None, "쿵", None, ("impact", "hit", "explosion", "door")),
    (r"덜컹|덜컥|철컥|철커덕|철컹|딸깍|달칵|드르륵", None, None, None, ("door", "click", "impact", "tools", "car")),
    (r"삐걱", None, "삐걱", "삐걱거린다", ("creak", "door")),
    (r"(?<![가-힣])TV|티비|텔레비전", "TV", None, "TV 소리", ("media",)),
    (r"라디오", "라디오", None, "라디오 소리", ("media",)),
    (r"확성기|스피커|안내\s*방송|방송", "확성기", None, "방송 소리", ("media",)),
    (r"무전", "무전기", "치익", "무전 소리", ("media", "phone")),
)
_SOURCES = tuple((re.compile(p), noun, ono, desc, fam) for p, noun, ono, desc, fam in SOURCES)
_DOOR_NOUNS = ("현관문", "대문", "창문", "철문", "문")
_HEARD = {"들리", "들려", "들린", "울려", "울리", "울린"}      # 'is heard' / 'rings': say nothing of what sounds
_SOUND_NAMES = {"함성", "환호", "야유", "비명", "고함", "박수", "울음", "웃음", "기침", "한숨"}   # sounds, not sources
# families that say how a thing sounds, not what sounds: all an onomatopoeia written inside the
# words of the source that names the cue adds ('문이 끼익 열린다': a creak of the door, no car)
MANNER_FAMILIES = {"creak", "scrape", "click", "impact", "hit"}


def _inside_families(fam, word: str) -> tuple:
    """The families a SOURCES entry adds when ``word``, its match, lies inside the words of the
    source that names the cue: only how that source sounds (``MANNER_FAMILIES``) — all of them
    when the word is the onomatopoeia of a source of its own that has none of those ('문이 띵동
    열린다': a doorbell too)."""
    if _ONO_ANY.fullmatch(word.strip(" !~-…")) and not set(fam) & MANNER_FAMILIES:
        return tuple(fam)
    return tuple(f for f in fam if f in MANNER_FAMILIES)


_ONO_RUN = re.compile(rf"(?:{_ALT(ONO_STRONG + ONO_WEAK)})+")


def _door_sound(word: str) -> bool:
    """The word before '소리를 내며 / 소리와 함께' between the door and its verb is the door's own
    sound: an onomatopoeia ('끼익', '쾅쾅', also one no table has: '끽', '띵'), not what else sounds
    while the door opens ('문이 사이렌 소리와 함께 열린다', '빗소리', '초인종 소리', '음악 소리')."""
    if _ONO_RUN.fullmatch(word):
        return True
    names = MUSIC_WORD.search(word) or any(rx.search(word + "소리") for rx, *_ in _SOURCES)
    return _ono_shaped(word) and not names


def _sounds_else(m: re.Match) -> bool:
    """A door match whose word before '소리' is not the door's own sound (``_door_sound``)."""
    word = m.groupdict().get("sori")
    return word is not None and not _door_sound(word)

# music
MUSIC_WORD = re.compile(r"음악|노래(?!방)|노랫|선율|멜로디|연주|BGM|O\.?S\.?T|애국가|(?<![가-힣])가요(?![가-힣])|팝송|"
                        r"트로트|흥얼|떼창|합창(?!단)|허밍|턴테이블")
MUSIC_CONTEXT = re.compile(MUSIC_WORD.pattern + r"|(?<![가-힣])곡(?![가-힣])|신청\s*곡|곡명|흐르|흐른|흘러|울려\s*퍼|"
                           r"선행|라디오|레코드|오디오|전축|턴테이블|카세트|테이프|LP|L\.P|D\.?J|틀어|틀고|트는|"
                           r"부르|불러|들리|들려")
DIEGETIC = re.compile(r"라디오|레코드|오디오|전축|턴테이블|카세트|테이프|LP|L\.P|(?<![가-힣])TV|텔레비전|스피커|확성기|"
                      r"흘러나|틀어|틀고|트는|켜|부른다|부르며|부르는|불러|흥얼|연주|D\.?J|헤드폰|이어폰")
MUSIC_SOURCE = (("라디오", "라디오"), ("TV", "TV"), ("텔레비전", "TV"), ("오디오", "오디오"), ("레코드", "레코드"),
                ("전축", "전축"), ("카세트", "카세트"), ("테이프", "카세트"), ("스피커", "스피커"), ("확성기", "확성기"),
                ("LP", "LP"), ("L.P", "LP"), ("헤드폰", "헤드폰"), ("이어폰", "이어폰"))
SINGING = ((r"흥얼", "[흥얼거린다]"), (r"떼창", "[떼창]"), (r"합창", "[합창]"),
           (r"노래[를]?\s*(?:부[르른]|불러|따라\s*부[르른]|따라부[르른])|노래하|따라\s*부[르른]|따라부[르른]", "[노래를 부른다]"))
MUSIC_CONTINUES = re.compile(r"계속|이어지|이어진|이어서|여전히|위\s*음악|그\s*음악|흐르던|위의?\s*노래|같은\s*음악|"
                             r"아직|다시\s*흐르|다시\s*울려")
GENRES = (("락", "록"), ("록", "록"), ("클래식", "클래식"), ("재즈", "재즈"), ("팝", "팝"), ("댄스", "댄스"),
          ("트로트", "트로트"), ("국악", "국악"), ("행진곡", "행진곡"), ("힙합", "힙합"), ("발라드", "발라드"),
          ("디스코", "디스코"))
MOOD_BEFORE_MUSIC = re.compile(r"(?<![가-힣])([가-힣]{2,7})\s*(?:느낌의\s*)?(?:(?:락|록|클래식|재즈|팝|댄스|"
                               r"트로트|국악|힙합|발라드|디스코)\s*)?음악")
# II.7 asks for the mood or genre: an adjective ('경쾌한', '슬픈', '느린', '서정적인'), not what the music
# does ('깔리는', '들려오는', '틀어놓은', '이어지는') nor which music it is ('같은', '다른')
MOOD_NOT = re.compile(r"(?:깔리|깔린|들려오|들려온|틀어\s*놓|틀어놓|틀어\s*둔|틀어둔|켜\s*놓|켜놓|울려\s*퍼|울려퍼|이어지|이어진|"
                      r"퍼지|퍼진|시작되|시작된|바뀌|바뀐|커지|커진|작아지|작아진|나오|나온|흐르|흐른|흘러|들리|들린|"
                      r"울리|울린|멈춘|끊긴|꺼진|켜진|틀린|변한|반복된|녹음된)[가-힣]*$|"
                      r"^(?:같은|다른|새로운|이런|그런|저런|어떤|모든|많은|수많은|좋아하는|좋아한|아는|모르는)$")
MOOD_VERB_OK = {"신나는", "넘치는", "들뜨는", "설레는"}   # verbs (-는) that do give a mood


def _mood_word(word: str | None) -> str | None:
    """The mood of '<word> 음악' when ``word`` is one: an adnominal ending in ㄴ that is no
    movement or choice of the music; a '-는/-던' verb only from a short list."""
    if not word or (ord(word[-1]) - 0xAC00) % 28 != 4 or MOOD_NOT.search(word):
        return None
    if word[-1] in "는던" and word not in MOOD_VERB_OK:
        return None
    return word
# song titles: "가수의 '곡명'", "가수의 latin title", "'곡명'" in a line about music
_OPEN_Q, _CLOSE_Q = "‘'\"“「『", "’'\"”」』"
_QUOTED = rf"[{_OPEN_Q}]([^{_OPEN_Q}{_CLOSE_Q}\n]{{1,40}}?)[{_CLOSE_Q}]"
_ARTIST = r"([A-Za-z][A-Za-z.'’]*(?:(?:\s*&\s*|\s+)[A-Za-z][A-Za-z.'’]*){0,4}(?:[가-힣]{1,3})?|[가-힣]+(?:\s[가-힣]+)?)"
TITLE_ARTIST_Q = re.compile(_ARTIST + r"의\s*" + _QUOTED)
TITLE_ARTIST_LATIN = re.compile(_ARTIST + r"의\s+([A-Za-z][A-Za-z0-9 ,'’!?&-]{0,40})")
TITLE_AGAIN = re.compile(r"\s*[~～….\-]+\s*([A-Za-z][A-Za-z0-9 ,'’&-]{0,40})")   # after an unquoted title
# 'MOON - Slow Night 가 흐르며': singer, a dash, a Latin title
TITLE_DASH = re.compile(r"(?<![A-Za-z가-힣])([A-Za-z][A-Za-z.&'’ ]{0,24}?|[가-힣]{1,8})\s+-\s+([A-Za-z][A-Za-z0-9 ,'’!?&]{1,40})")
# words that stand before a singer's name and are not part of it ('이내 별무리의 …', '다시 하늘별의 …')
_NOT_NAME = re.compile(r"^(?:이내|곧|다시|이때|그때|갑자기|문득|이어|이어서|계속|마침|드디어|어느새|잠시|서서히|점점|여전히|"
                       r"그리고|또|역시|바로|함께|모두|그|이|저|위|위의|아래)$")
TITLE_QUOTED = re.compile(_QUOTED)
TITLE_LATIN = re.compile(r"(?<![A-Za-z.])([A-Za-z][A-Za-z'’]*(?:\s+[A-Za-z][A-Za-z'’]*)*)")
NOT_TITLE_AFTER = re.compile(r"\s*(?:에서|의\s|의$|라고|라는|라\s|라며|이라|하며|하고|하자|하는|하더니|까지|란\s|이란)")
LATIN_STOP = {"tv", "dj", "d", "j", "lp", "l", "p", "vhs", "mp3", "ost", "bgm", "cut", "cut to", "inter cut", "insert",
              "ins", "close-up", "close up", "close", "up", "main title", "title", "the end", "end", "fade in",
              "fade out", "f", "o", "v", "pov", "cu", "full shot", "flash back", "flashback", "montage", "na", "e",
              "os", "vo", "off", "zoom in", "zoom out", "zoom", "pan", "tilt", "over", "s", "n", "m", "x"}
_HONORIFIC = re.compile(r"(?:오빠|언니|형|누나|씨|님)$")
_ADNOMINAL_END = set("는은던한운인된온른진린간갈될할")

# silence
SILENCE = re.compile(r"침묵|(?<![가-힣])정적|적막|고요|조용해지|조용해진|숨\s*죽인\s*듯|아무\s*소리도|아무런\s*소리")
SILENCE_BROKEN = re.compile(r"(?:침묵|정적|적막|고요)[을를이가]?\s*(?:깨|깬|깨지|깨뜨)")
MUSIC_STOP = re.compile(r"(?:음악|노래|선율|멜로디)[이가은는을를]?\s*(?:멈|끊|꺼지|꺼진|꺼진다|그치|그친|사라지|사라진|뚝)|"
                        r"(?:음악|노래|라디오|오디오|카세트|테이프|전축)[을를]?\s*(?:끄|끈|꺼|멈추|멈춘|멈춰)")
SILENCE_PHRASES = (("침묵", ("[침묵]", "[정적]")), ("적막", ("[적막]", "[정적]")), ("정적", ("[정적]",)),
                   ("고요", ("[정적]", "[고요]")))
MUSIC_STOP_PHRASE = "[음악이 멈춘다]"          # 넷플릭스 II.9

# a speaker's own sound (script.json ``vocal`` / the vocal directions of a dialogue without words)
VOCAL_NOT = re.compile(r"참으며|참았|참는|참고|삼키|억누르|억지|애써|쓴\s*웃음|쓴웃음|비웃음|미소|씨익|빙긋|방긋|피식|싱긋|히죽")
VOCAL_BURST = re.compile(r"터지|터뜨|터진|터트")
VOCAL_PHRASES = (
    (r"재채기|에취", "[재채기]", ("sneeze",)),
    (r"헛기침", "[헛기침]", ("cough",)),
    (r"콜록", "[콜록]", ("cough",)), (r"쿨럭", "[쿨럭]", ("cough",)), (r"켁켁|캑캑", "[켁켁]", ("cough",)),
    (r"기침", "[기침]", ("cough",)),
    (r"딸꾹", "[딸꾹]", ("body",)), (r"트림", "[트림]", ("body",)),
    (r"하품", "[하품]", ("breath",)),
    (r"한숨", "[한숨]", ("breath",)),
    (r"헉헉|헐떡|가쁜\s*숨|숨을\s*몰아", "[헐떡인다]", ("breath",)),
    (r"심호흡", "[심호흡]", ("breath",)), (r"숨소리", "[숨소리]", ("breath",)),
    (r"(?<![가-힣])헉(?![가-힣])", "[헉]", ("breath",)),
    (r"킁킁", "[킁킁]", ("breath",)), (r"콧방귀", "[콧방귀]", ("breath",)),
    (r"쯧쯧|혀를\s*차", "[쯧쯧]", ("body",)),
    (r"꿀꺽|침을\s*삼키|마른침", "[꿀꺽]", ("body", "eating")),
    (r"휘파람", "[휘파람]", ("whistle_voice", "whistle")),
    (r"코를\s*골|코골이|드르렁", "[코골이]", ("snore",)),
    (r"비명", "[비명]", ("scream",)), (r"절규", "[절규]", ("scream",)),
    (r"신음|끙끙|(?<![가-힣])끙(?![가-힣])", "[신음]", ("moan", "breath")),
    (r"탄식", "[탄식]", ("breath", "moan")), (r"탄성", "[탄성]", ("crowd_reaction",)),
    (r"흐느", "[흐느낀다]", ("crying",)), (r"훌쩍", "[훌쩍]", ("crying", "breath")),
    (r"엉엉", "[엉엉]", ("crying",)), (r"흑흑", "[흑흑]", ("crying",)),
    (r"통곡|오열", "[통곡]", ("crying",)),
    (r"울음|(?<![가-힣])울며|(?<![가-힣])울면서|(?<![가-힣])운다|(?<![가-힣])우는", "[울음]", ("crying",)),
    (r"깔깔", "[깔깔]", ("laughter",)), (r"낄낄", "[낄낄]", ("laughter",)), (r"킥킥", "[킥킥]", ("laughter",)),
    (r"키득|큭큭", "[키득]", ("laughter",)), (r"폭소", "[폭소]", ("laughter",)), (r"헛웃음", "[헛웃음]", ("laughter",)),
    (r"웃음|(?<![가-힣])웃는다|웃어\s*젖", "[웃음]", ("laughter",)),
    (r"흥얼", "[흥얼거린다]", ("hum_voice", "music")),
    (r"(?<![가-힣])풉(?![가-힣])", "[풉]", ("laughter", "body")),
)
_VOCAL = tuple((re.compile(p), ph, fam) for p, ph, fam in VOCAL_PHRASES)


# ----------------------------------------------------------------------------- helpers
def _clean(text) -> str:
    return " ".join(str(text or "").split())


def _mask(text: str, pattern: re.Pattern) -> str:
    """``text`` with every match of ``pattern`` blanked out (positions kept)."""
    return pattern.sub(lambda m: " " * len(m.group(0)), text)


def short(text, n: int = SHORT) -> str:
    text = _clean(text)
    return text if len(text) <= n else text[:n].rstrip() + "…"


def has_time(ln: dict) -> bool:
    return ln.get("start") is not None and ln.get("end") is not None


def _estimated(ln: dict) -> bool:
    """The line's time is a guess (the same test as ``recommend.timing_estimated``)."""
    return ln.get("timing") in ("voice", "even") or ln.get("status") == "unheard"


def josa(word: str, pair=("이", "가")) -> str | None:
    """The particle after ``word``: pair[0] after a final consonant ('보이네' -> '가', '봄' -> '이').
    For a Latin word the Korean reading is guessed ('time' 타임 -> '이', 'reality' 리얼리티 -> '가');
    None when it cannot be told."""
    w = re.sub(r"[\s'’\"”!?.,~…)\]-]+$", "", str(word or ""))
    if not w:
        return None
    ch = w[-1]
    if "가" <= ch <= "힣":
        return pair[0] if (ord(ch) - 0xAC00) % 28 else pair[1]
    if ch.isdigit():
        return pair[0] if ch in "013678" else pair[1]
    if not ch.isascii() or not ch.isalpha():
        return None
    w = re.sub(r"[^a-z]", "", w.lower().split()[-1]) if w.split() else w.lower()
    if len(w) > 2 and w.endswith("e") and w[-2] not in "aeiouy":
        w = w[:-1]                                     # time -> tim: a silent e
    if not w:
        return None
    if w.endswith("ng") or w[-1] in "mnlkpb":
        return pair[0]
    return pair[1]


# ----------------------------------------------------------------------------- one text
def _ono_run(text: str, start: int) -> str:
    """The onomatopoeia written from ``start`` on, with the ones right after it ('펑! 펑!')."""
    out, i, head = "", start, start
    while head > 0 and text[head - 1] in text[start:start + 3] and "가" <= text[head - 1] <= "힣":
        head -= 1                                      # '타타타탕': the match began inside the word
    piece = re.compile(rf"(?:{_ALT(ONO_STRONG + ONO_WEAK)})+[!~…\-]*")
    gap = re.compile(r"[\s,]*")
    while True:
        m = piece.match(text, i)
        if not m:
            break
        out += ("" if not out else " ") + m.group(0)
        i = gap.match(text, m.end()).end()
        if len(out) > 16:
            break
    out = re.sub(r"!{2,}", "!", re.sub(r"[~\-]+", "", text[head:start] + out)).strip()
    return out


def _sound_name(text: str) -> tuple[str | None, list[str], list[str], tuple[int, int, int] | None]:
    """(description of what sounds, '- 출처' nouns, families, (where the naming entry's match
    starts, its index in SOURCES, where the match ends)) from the SOURCES table. An entry that
    matches only inside the naming entry's match (the '끼익' of '문이 끼익 열린다') adds only its
    ``MANNER_FAMILIES`` (``_inside_families``). A door that opens with what else sounds ('문이
    사이렌 소리와 함께 열린다', ``_sounds_else``) names nothing and swallows nothing: its families
    count when another entry names the cue."""
    desc, nouns, fams, onos, first, at, late = None, [], [], [], True, None, []
    for k, (rx, noun, ono, d, fam) in enumerate(_SOURCES):
        m = rx.search(text)
        if m and _sounds_else(m):
            late.extend(fam)
            m = next((x for x in rx.finditer(text, m.end()) if not _sounds_else(x)), None)
        if m:
            if desc is None:
                desc = d
            if first:                                  # the first entry that matches names the source
                first, at = False, (m.start(), k, m.end())
                if noun == "문":
                    noun = next((n for n in _DOOR_NOUNS if n in text), "문")
                if noun:
                    nouns.append(noun)
                if ono:
                    onos.append(ono)
            elif at[0] <= m.start() and m.end() <= at[2]:
                fam = _inside_families(fam, m.group(0))
            fams.extend(f for f in fam if f not in fams)
    if at is not None:
        fams.extend(f for f in late if f not in fams)
    return desc, nouns + [f"@{o}" for o in onos], fams, at


_FAMILY_ONOS: dict[str, set[str]] = {}


def _ono_fits(ono: str, entry: int) -> bool:
    """The onomatopoeia is one the SOURCES entry's source makes: its own, or one of a sound class
    of its first family in cboe/data/sfx_classes.tsv ('탕! 탕!' -> 총, '쿵쾅' -> 문)."""
    if not _FAMILY_ONOS:
        for r in phrases.table().values():
            o = re.sub(r"[^가-힣]", "", r.get("ono") or "")
            if o and r.get("family"):
                _FAMILY_ONOS.setdefault(r["family"], set()).add(o)
    _rx, _noun, own, _d, fam = _SOURCES[entry]
    core = re.sub(r"[^가-힣]", "", ono or "")
    known = ({own} if own else set()) | (_FAMILY_ONOS.get(fam[0], set()) if fam else set())
    # a one-letter sound fits only itself: '탕' is no gunshot inside '우당탕탕'
    return bool(core) and any(o == core or (len(o) > 1 and o in core) or (len(core) > 1 and core in o) for o in known)


def _strong_onos(text: str) -> list[tuple[int, str]]:
    """The table's onomatopoeia that start a word, or go on one that repeats them ('타타타탕');
    not the same letters inside another word ('우스꽝스러운')."""
    out = []
    for m in ONO_STRONG_RE.finditer(text):
        prev = text[m.start() - 1] if m.start() else ""
        if not ("가" <= prev <= "힣") or prev in m.group(0):
            out.append((m.start(), m.group(0)))
    return out


def _ono_shaped(tok: str) -> bool:
    """A written word that looks like an onomatopoeia: it repeats ('두두두', '털털털'), or starts
    with a tense or aspirated consonant and ends in a consonant sounds end in ('꽈당', '딸랑',
    '삐비빅', '쿠웅'), or is a cry ending in ㄱ ('으악', '악', '헉'). Not a word someone says
    ('형님들', '경찰', swear words)."""
    if tok in _NOT_ONO:
        return False
    if (len(tok) > 1 and len(set(tok)) == 1) or (len(tok) == 4 and tok[:2] == tok[2:]):
        return True
    first, last = ord(tok[0]) - 0xAC00, ord(tok[-1]) - 0xAC00
    initial, final = first // 588, last % 28
    if initial in _TENSE_INITIALS:
        return final in _ONO_FINALS
    return initial in (11, 18) and final == 1         # ㅇ / ㅎ … ㄱ


def _written_onos(text: str) -> list[tuple[int, str]]:
    """Sounds written as onomatopoeia that no table has ('꽈당- 소리', '딸랑- 하고', '으악- 소리'):
    (position, the written form with its '!'). Only for wording a cue: on their own they are as
    often a manner ('확- 하고 잡아끈다', '푹- 하고 빠진다') as a sound."""
    out = []
    for m in WRITTEN_ONO_RE.finditer(text):
        tok = m.group(1)
        if _ono_shaped(tok):
            out.append((m.start(1), tok + ("!" if "!" in m.group(0) else "")))
    return out


# words before '소리' that only say a sound is heard, made or said (들리는 …, …하는 …: a line just
# spoken, 부르는 …) and word endings that name no sound (…하며 + 소리)
_SAYS_NOTHING = re.compile(r"(?:하는|하던|하고|하며|하자|하면|라는|라고|이라는|이라고|부르는|부르던|외치는|지르는|"
                           r"들리는|들려오는|들려온|들린|울리는|울려오는|울려\s*퍼지는|나는|나던|내는|내던|나오는|"
                           r"흘러나오는|새어\s*나오는|새어나오는|퍼지는|들어오는|오는|온)$")
_NOT_HEAD_END = re.compile(r"(?:서|에|게|히|도|고|며|면|다|요|을|를|와|과|로|지|자|니|까|죠|네|의)$")


def _generic_desc(text: str, at: int) -> str | None:
    """'이상한 소리' from the words before '소리' at ``at`` (one word, or two when the first is a
    noun followed by an adnominal: '무언가 터지는 소리'); None when they name nothing. A '…소리'
    description is a choice only, never the cue's caption (II.9: 'try not to describe the sound
    as … 소리')."""
    before = re.findall(r"[가-힣A-Za-z]+", text[:at])
    attached = re.search(r"[가-힣]+$", text[:at])
    head = attached.group(0) if attached else ""
    if head:                                           # '신음소리', '뱃고동소리': written together
        return None if _SAYS_NOTHING.search(head) else head + "소리"
    if not before:
        return None
    w1 = before[-1]
    if w1 in ("이", "그", "저", "무슨", "어떤", "웬", "아무", "무언가", "뭔가", "누군가", "어디선가") or \
            _SAYS_NOTHING.search(w1) or _NOT_HEAD_END.search(w1):
        return None
    words = [w1]
    if w1[-1] in _ADNOMINAL_END and len(before) > 1:
        w0 = before[-2]
        if not re.search(r"(?:서|에|게|히|도|고|며|면|다|요|을|를|와|과|로|선가|에선가)$", w0) and len(w0) <= 5:
            words.insert(0, w0)
    return " ".join(words) + " 소리"


def sfx_cue(text: str) -> dict | None:
    """The sound an action line writes down: {"keyword", "keywords", "phrase", "phrases",
    "families", "offscreen", "written_ono", "vague"}; None when it names none ('들린다' or '소리'
    alone, a '…하는' sound that points at a line just spoken).

    ``phrases`` in the guide's order (II.9): the written onomatopoeia first ('[꽈당]', also one no
    table has), '[의성어 - 출처]', a description ('[총성]'), and last a '…소리' description, which
    is a choice only. ``vague``: nothing but such a '…소리' description says what sounds ('이상한
    소리가 난다') — ``phrase`` is then None: the cue suggests no caption, makes no candidate of its
    own and gives no reason to include (callers)."""
    raw = _clean(text)
    t = _mask(raw, NOT_SOUND)
    words = [m.group(0) for m in SOUND_WORD.finditer(t)]
    onos = _strong_onos(t) + [(m.start(), m.group(0)) for m in ONO_WEAK_RE.finditer(t)]
    quoted = [m for m in TITLE_QUOTED.finditer(t) if _ONO_ANY.fullmatch(m.group(1).strip(" !~…-"))]
    onos += [(m.start(1), m.group(1)) for m in quoted]
    if not words and not onos:
        return None
    desc, srcs, fams, src_at = _sound_name(t)
    written = {}
    if not onos:
        # an onomatopoeia no table has, written as one, says how it sounds ('꽈당- 소리가 난다') —
        # right before '소리', or anywhere when no table names the sound; it never makes a cue of a
        # line without a sound word
        named = bool(desc or fams)
        written = {p: o for p, o in _written_onos(t) if not named or SORI_AFTER.match(t, p + len(o.rstrip("!")))}
        door = _SOURCES[src_at[1]][0].match(t, src_at[0]) if src_at and not written else None
        if door and door.groupdict().get("sori"):      # '문이 끽 소리를 내며 열린다': the door's sound as written
            written = {door.start("sori"): door.group("sori")}
        onos += list(written.items())
    if not onos and set(words) <= _HEARD and "목소리" in raw:
        return None                                    # '그의 목소리가 들린다': a line spoken off screen
    onos.sort()
    ono = (written.get(onos[0][0]) or _ono_run(t, onos[0][0]) or onos[0][1]) if onos else ""
    if "소리" in t:                                    # '구둣발 소리와 함께 …': what is right before '소리' names it
        at = t.index("소리")
        near_desc, near_srcs, _, near_at = _sound_name(t[max(0, at - 8):at + 2])
        # not a '소리' inside the words of the source that names the cue ('문이 끼익 소리를 내며 열린다')
        if near_desc and near_at and not (src_at and src_at[0] <= at < src_at[2]):
            desc, srcs, src_at = near_desc, near_srcs, (max(0, at - 8) + near_at[0], near_at[1],
                                                        max(0, at - 8) + near_at[2])
    nouns = [s for s in srcs if not s.startswith("@")]
    if onos and src_at and abs(src_at[0] - onos[0][0]) > NEAR_CHARS and not _ono_fits(ono, src_at[1]):
        nouns = []           # '[의성어 - 출처]' only when they belong together: '끼이익' far from '사이렌' is a brake
    table_ono = next((s[1:] for s in srcs if s.startswith("@")), None)
    for pos, o in onos:                                # the written onomatopoeia says what it is, too
        inside = bool(src_at) and src_at[0] <= pos < src_at[2]      # how the named source sounds
        for rx, _n, _o, _d, fam in _SOURCES:
            if rx.search(o):
                fams.extend(f for f in (_inside_families(fam, o) if inside else fam) if f not in fams)
    generic = _generic_desc(t, t.index("소리")) if desc is None and "소리" in t else None
    if generic and ono and re.sub(r"[!\s]", "", ono) in generic.replace(" ", ""):
        generic = None                                 # '[꽈당]' says it: no '[꽈당 소리]'
    vague = not ono and not table_ono and desc is None and not fams
    if vague and not generic:
        return None                                    # nothing says what sounds ('들린다', '소리' alone)
    noun = nouns[0] if nouns and nouns[0] not in _SOUND_NAMES else None
    opts = []
    if ono:                                            # II.9: the written onomatopoeia first
        if noun:
            opts.append(style.sfx_with_source(ono, noun))
        opts.append(style.sfx_phrase(ono))
    elif table_ono:                                    # a usual onomatopoeia of what the script names
        opts.append(style.sfx_with_source(table_ono, noun) if noun else style.sfx_phrase(table_ono))
    if desc:
        opts.append(style.bracket(desc))
    if generic:                                        # '…소리': a choice, after everything else
        opts.append(style.bracket(generic))
    opts = list(dict.fromkeys(o for o in opts if o))
    keyword = ono or (words[0] if words else "")
    return {"keyword": keyword, "keywords": list(dict.fromkeys([o for _, o in onos] + words)),
            "phrase": None if vague else opts[0], "phrases": opts, "families": fams,
            "offscreen": bool(OFFSCREEN.search(raw)), "written_ono": bool(ono), "vague": vague}


def _artist(name: str) -> str | None:
    s = _clean(name).strip(" ,.(")
    s = re.sub(r"^.*[()\[\]]", "", s).strip()
    s = re.sub(r"^가수\s+", "", s)
    s = _HONORIFIC.sub("", s).strip()
    parts = s.split()
    if len(parts) == 2 and re.fullmatch(r"[가-힣]+", parts[0]) and (
            parts[0][-1] in _ADNOMINAL_END or re.search(r"(?:서|고|며|에|를|을|가|이|도|의)$", parts[0])
            or (ord(parts[0][-1]) - 0xAC00) % 28 == 4 or _NOT_NAME.match(parts[0])):  # '흐르는', '이내'
        s = parts[1]
    if s.startswith("가수 "):
        s = s[3:]
    return s or None


def _title_ok(text: str, end: int) -> bool:
    return not NOT_TITLE_AFTER.match(text, end)


def song_titles(text: str, context: bool) -> list[dict]:
    """Song titles written in an action line: [{"artist", "title"}]. ``context``: this line or
    the one next to it is about music (a quoted name alone is a song only then)."""
    t = _clean(text)
    out, used = [], []

    def add(artist, title, span):
        title = title.strip().strip(" ,.~-'’")
        if not title or any(a <= span[0] < b for a, b in used):
            return
        used.append(span)
        out.append({"artist": _artist(artist) if artist else None, "title": title})

    if not context:
        return out
    for m in TITLE_ARTIST_Q.finditer(t):
        if _title_ok(t, m.end()) and not _ONO_ANY.fullmatch(m.group(2).strip(" !~…-")):
            add(m.group(1), m.group(2), (m.start(), m.end()))
    for m in TITLE_ARTIST_LATIN.finditer(t):
        title = m.group(2).strip().rstrip("!? ")      # written without quotes: '!' is the line's, not the title's
        end = m.end()
        # 'the title~. the title in full!': said drawn out first, then whole — the whole is the title
        more = TITLE_AGAIN.match(t, end)
        if more and more.group(1).lower().startswith(title.lower() + " "):
            title, end = more.group(1).strip().rstrip("!? "), more.end()
        if title.lower() not in LATIN_STOP and len(title.replace(" ", "")) >= 3:
            add(m.group(1), title, (m.start(), end))
    for m in TITLE_DASH.finditer(t):
        title = m.group(2).strip()
        if title.lower() not in LATIN_STOP and m.group(1).strip().lower() not in LATIN_STOP | {"cut to"}:
            add(m.group(1), title, (m.start(), m.end()))
    if MUSIC_CONTEXT.search(t):                       # a title alone needs music on this very line
        for m in TITLE_QUOTED.finditer(t):
            inner = m.group(1).strip()
            if (_title_ok(t, m.end()) and not _ONO_ANY.fullmatch(inner.strip(" !~…-"))
                    and not re.search(r"[!?~]$", inner) and len(inner) <= 30):
                add(None, inner, (m.start(), m.end()))
        for m in TITLE_LATIN.finditer(t):
            w = m.group(1).strip()
            if (w.lower() not in LATIN_STOP and not w.isupper() and (" " in w or len(w) >= 4)
                    and not any(a <= m.start() < b for a, b in used)):
                add(None, w, (m.start(), m.end()))
    return out


def title_phrases(song: dict, diegetic: bool) -> list[str]:
    """II.7: ``[가수의 '곡명']`` (배경 음악) and ``[가수의 '곡명'이 흐른다]`` (장면 안에서 나오는
    음악) — the one for this line first. The particle after a Latin title is guessed and the
    form is left out when it cannot be told."""
    q = f"'{song['title']}'"
    head = f"{song['artist']}의 {q}" if song.get("artist") else q
    plain = style.bracket(head)
    j = josa(song["title"])
    flowing = style.bracket(f"{head}{j} 흐른다") if j else None
    opts = [flowing, plain] if diegetic else [plain, flowing]
    return [o for o in opts if o]


def music_cue(text: str, context: bool = False, near_songs=()) -> dict | None:
    """Music or a song an action line writes down; None when there is none. ``context``: the
    line next to it is about music (for a song title written alone). ``near_songs``: titles
    with their singer written just before it in the same scene (a DJ who names the record,
    then the music starts) — used when this line names none."""
    raw = _clean(text)
    t = _mask(raw, NOT_SOUND)
    has_word = bool(MUSIC_WORD.search(t))
    songs = song_titles(t, context or has_word or bool(MUSIC_CONTEXT.search(t)))
    if not has_word and not songs:
        return None
    near = False
    if not songs and near_songs and not MUSIC_CONTINUES.search(t):
        songs, near = list(near_songs), True
    if MUSIC_STOP.search(t) and not songs and not re.search(r"흘러나|흐르기\s*시작|틀어|틀고|트는", t):
        return None                                    # only the music stopping: a silence cue
    diegetic = bool(DIEGETIC.search(t))
    src = next((name for w, name in MUSIC_SOURCE if w in t), None) if diegetic else None
    opts = []
    for s in songs:
        opts.extend(title_phrases(s, diegetic))
    if not songs:                                      # people singing on screen (II.7: their song)
        sung = next((ph for rx, ph in SINGING if re.search(rx, t)), None)
        if sung:
            opts.append(sung)
    m = MOOD_BEFORE_MUSIC.search(t)
    mood = _mood_word(m.group(1)) if m else None
    genre = next((g for w, g in GENRES if re.search(rf"(?<![가-힣]){w}\s*음악", t)), None)
    words = ([mood] if mood else []) + ([genre] if genre else [])
    if words or MUSIC_WORD.search(t):
        base = style.music_phrase(*words, style.MUSIC)
        opts.append(style.sfx_with_source(base[1:-1], src) if src else base)
        if src:
            opts.append(base)
    opts = list(dict.fromkeys(o for o in opts if o))
    kw = songs[0]["title"] if songs else (MUSIC_WORD.search(t).group(0))
    return {"keyword": kw, "keywords": [s["title"] for s in songs] + [x.group(0) for x in MUSIC_WORD.finditer(t)],
            "phrase": opts[0], "phrases": opts, "songs": songs, "songs_near": near, "diegetic": diegetic, "source": src,
            "continues": bool(MUSIC_CONTINUES.search(t)) and not re.search(r"흘러나오기\s*시작|시작", t),
            "families": ["music"]}


def silence_cue(text: str) -> dict | None:
    """A silence or the music stopping; None when the line has none (or the silence is broken:
    '적막을 깨는 총소리' is a sound, '침묵을 깨고 말한다' a line of dialogue)."""
    t = _mask(_clean(text), NOT_SOUND)
    stop = MUSIC_STOP.search(t)
    if stop:
        return {"keyword": stop.group(0), "keywords": [stop.group(0)], "phrase": MUSIC_STOP_PHRASE,
                "phrases": [MUSIC_STOP_PHRASE, "[정적]"], "music_stop": True, "families": []}
    m = SILENCE.search(t)
    if not m or SILENCE_BROKEN.search(t):
        return None
    opts = next((ph for w, ph in SILENCE_PHRASES if w in m.group(0)), ("[정적]",))
    return {"keyword": m.group(0), "keywords": [m.group(0)], "phrase": opts[0], "phrases": list(opts),
            "music_stop": False, "families": []}


def vocal_cue(texts) -> dict | None:
    """A speaker's own sound from vocal directions ('한숨', '끝내 울음이 터진다', '목이 막혀 켁켁'):
    {"keyword", "phrase", "phrases", "families"}; None for a smile, a held-back laugh and the like."""
    for raw in [_mask(_clean(x), NOT_SOUND) for x in (texts if isinstance(texts, (list, tuple)) else [texts])]:
        if not raw.strip() or (VOCAL_NOT.search(raw) and not VOCAL_BURST.search(raw)):
            continue
        for rx, ph, fam in _VOCAL:
            m = rx.search(raw)
            if m:
                return {"keyword": m.group(0), "keywords": [raw], "phrase": ph, "phrases": [ph],
                        "families": list(fam)}
    return None


# ----------------------------------------------------------------------------- the script
def extract(doc: dict) -> list[dict]:
    """The cues of a script.json (version 2), in document order, without time windows.

    Each cue: {"id": "C0001", "type", "scene_id", "element_id", "line_id", "speaker", "text",
    "keyword", "keywords", "phrase", "phrases", "families", "after_line", "before_line", …type
    keys (``offscreen``; ``songs``·``diegetic``·``source``·``continues``; ``music_stop``)}.
    ``after_line`` / ``before_line``: the dialogue lines of the same scene right before and after
    it (a speaker's own sound: its line). A script without elements (``format`` lines) gives the
    speakers' own sounds of its lines only, and of the subtitle cells that are only such a sound
    (``wordless``: line_id None, ``at`` = the cell's [start, end], after the line before it)."""
    doc = doc or {}
    elements = doc.get("elements") or []
    lines = doc.get("lines") or []
    line_by_id = {ln.get("id"): ln for ln in lines}
    out: list[dict] = []

    def add(kind, cue, **where):
        out.append({"id": "", "type": kind, **where, **cue})

    if not elements:                                   # '이름: 대사' scripts, subtitles, tables
        # a subtitle cell that is only a speaker's own sound (script ``wordless``): after its line
        alone: dict[str | None, list[dict]] = {}
        for w in doc.get("wordless") or []:
            alone.setdefault(w.get("after_line"), []).append(w)

        def add_alone(after):
            for w in alone.get(after, []):
                v = vocal_cue(w.get("vocal") or [])
                if v and w.get("start") is not None and w.get("end") is not None:
                    add("vocal", v, scene_id=w.get("scene_id"), element_id=None, line_id=None,
                        speaker=w.get("speaker"), text=w.get("raw") or "", after_line=after, before_line=None,
                        at=[float(w["start"]), float(w["end"])])

        add_alone(None)
        for ln in lines:
            v = vocal_cue(ln.get("vocal") or [])
            if v:
                add("vocal", v, scene_id=ln.get("scene_id"), element_id=None, line_id=ln.get("id"),
                    speaker=ln.get("speaker"), text=ln.get("raw") or ln.get("text") or "",
                    after_line=ln.get("id"), before_line=ln.get("id"))
            add_alone(ln.get("id"))
        return _numbered(out)
    # the dialogue lines of each scene in document order, to find the neighbours of an element
    pos_lines: list[tuple[int, str, str | None]] = []   # (element index, line id, scene id)
    for i, e in enumerate(elements):
        if e.get("type") == "dialogue" and e.get("line_id"):
            pos_lines.append((i, e["line_id"], e.get("scene_id")))
    idx = [p[0] for p in pos_lines]

    def neighbours(i, scene):
        k = bisect_right(idx, i)
        before = pos_lines[k - 1] if k > 0 and pos_lines[k - 1][2] == scene else None
        j = k
        while j < len(pos_lines) and pos_lines[j][0] == i:
            j += 1
        after = pos_lines[j] if j < len(pos_lines) and pos_lines[j][2] == scene else None
        return (before[1] if before else None), (after[1] if after else None)

    def music_near(i):
        here = elements[i]
        for j in (i - 1, i, i + 1):
            if 0 <= j < len(elements) and elements[j].get("scene_id") == here.get("scene_id") \
                    and MUSIC_CONTEXT.search(_mask(_clean(elements[j].get("text")), NOT_SOUND)):
                return True
        return False

    def songs_before(i):
        """Titles with a singer in the two elements before (any kind: a DJ's line names the record)."""
        here = elements[i]
        for j in (i - 1, i - 2):
            if j >= 0 and elements[j].get("scene_id") == here.get("scene_id"):
                found = [s for s in song_titles(elements[j].get("text") or "", True) if s.get("artist")]
                if found:
                    return found
        return []

    for i, e in enumerate(elements):
        kind, scene, text = e.get("type"), e.get("scene_id"), e.get("text") or ""
        if kind == "dialogue":
            ln = line_by_id.get(e.get("line_id")) or {}
            v = vocal_cue(ln.get("vocal") or [])
            if v:
                add("vocal", v, scene_id=ln.get("scene_id", scene), element_id=e.get("id"), line_id=ln.get("id"),
                    speaker=ln.get("speaker"), text=ln.get("raw") or text, after_line=ln.get("id"),
                    before_line=ln.get("id"))
            continue
        if kind != "action":
            continue
        after, before = neighbours(i, scene)
        where = dict(scene_id=scene, element_id=e.get("id"), line_id=None, text=text, after_line=after,
                     before_line=before)
        if "speaker" in e:                             # a dialogue without words: its speaker's own sound
            v = vocal_cue([d.get("text") for d in e.get("directions") or [] if d.get("type") == "vocal"])
            if v:
                add("vocal", v, speaker=e.get("speaker"), **where)
                continue
        sil = silence_cue(text)
        mus = music_cue(text, music_near(i), songs_before(i))
        if sil and mus and not sil.get("music_stop"):
            sil = None                                 # people fall silent while music plays: the music is the cue
        if sil:
            add("silence", sil, speaker=None, **where)
        if mus:
            add("music", mus, speaker=None, **where)
        rest = text
        if mus:                                        # '음악 소리', '노래가 들린다' are the music
            rest = re.sub(r"(?:음악|노래|노랫|선율|멜로디)\s*소리|(?:음악|노래|곡|선율|멜로디)[이가은는을를]?\s*"
                          r"(?:들리|들려|울려|울리|흐르|흘러|시끄러)|시끄러운\s*(?:락|록)?\s*음악", " ", rest)
        if sil:
            rest = _mask(rest, SILENCE)
            rest = re.sub(r"아무\s*소리도\s*\S*", " ", rest)
        s = sfx_cue(rest)
        if s and mus and not s["families"] and not s["written_ono"] and set(s["keywords"]) <= _HEARD:
            s = None                                   # only the music's '들린다' was left
        if s:
            add("sfx", s, speaker=None, **where)
    return _numbered(out)


def _numbered(cues: list[dict]) -> list[dict]:
    for k, c in enumerate(cues, 1):
        c["id"] = f"C{k:04d}"
    return cues


# ----------------------------------------------------------------------------- time windows
def scene_times(scenes) -> dict[str, dict]:
    """scenes.json scenes by id: {"start", "end", "status"}; a scene marked absent has no time.
    One end may be missing: a scene without dialogue before the first scene with lines (or after
    the last) has only the time on the side of its neighbour."""
    out = {}
    for s in scenes or []:
        if not isinstance(s, dict) or not s.get("id"):
            continue
        ok = s.get("status") != "absent"
        out[s["id"]] = {"start": s.get("start") if ok else None, "end": s.get("end") if ok else None,
                        "status": s.get("status")}
    return out


def _quiet_ids(scenes) -> set[str]:
    """The scenes of scenes.json without dialogue lines."""
    return {s["id"] for s in scenes or [] if isinstance(s, dict) and s.get("id") and not s.get("lines")}


def _quiet_parts(st: dict[str, dict], quiet: set[str], t0=None, t1=None) -> dict[str, tuple[float, float]]:
    """{scene id: (start, end) of its part} for scenes without dialogue whose place is not found
    (``scene_times`` ``st``, status 'unknown'): those in a row hold the same gap between their
    neighbours and share it in script order. A first (last) one has only its end (start): the
    start ``t0`` (end ``t1``) of the processed stretch is its other end."""
    gaps: dict[tuple, list[str]] = {}
    for sid, v in st.items():                          # script order
        if sid not in quiet or v["status"] != "unknown" or (v["start"] is None and v["end"] is None):
            continue
        lo = v["start"] if v["start"] is not None else t0
        hi = v["end"] if v["end"] is not None else t1
        if lo is not None and hi is not None and float(hi) >= float(lo):
            gaps.setdefault((float(lo), float(hi)), []).append(sid)
    out = {}
    for (lo, hi), share in gaps.items():
        step = (hi - lo) / len(share)
        for k, sid in enumerate(share):
            out[sid] = (lo + step * k, lo + step * (k + 1))
    return out


EDGE_TOL_S = 0.05         # a scene start / end this close to its own first / last line says nothing more
NO_SCENE_TIME = ("absent", "out_of_range")         # scenes.json: the scene is not in the processed video
FAR_ENDS = ("other_scene", "range")                # window ends that are no bound of the cue's own scene
GUESS_STEP_S = 5.0         # guessed onsets of sounds written after a scene's last line, one after another


def _far_end(cue: dict) -> int:
    """-1: only the window's start is no bound of the cue's own scene (``window_by`` 'other_scene' /
    'range': the guess and ``anchor`` lean to the other end), +1: only its end, 0: neither or both.
    Whether such an end lies past another scene is ``window_out``."""
    by = cue.get("window_by") or [None, None]
    far = [b in FAR_ENDS for b in by]
    return -1 if far[0] and not far[1] else 1 if far[1] and not far[0] else 0


def windows(cues: list[dict], doc: dict, lines: list[dict], scenes=None, span=None) -> list[dict]:
    """Add ``window`` [lo, hi] (s) to every cue, or None: from the end of the nearest dialogue
    line before it that has a time to the start of the nearest one after it, in script order.
    A speaker's own sound: its line; one alone in a subtitle cell (``at``): that cell, inside
    the processed stretch.

    Each end looks in the cue's own scene first ('line'). Without a timed line there, a scene
    edge from scenes.json that lies outside the scene's own lines ends it ('scene': a scene
    without dialogue holds the time between its neighbours). The scene-first alignment writes a
    found scene's start / end as the range of its matched lines, so a scene's first or last
    line says nothing more and the end goes on to the nearest timed line of another scene
    ('other_scene'), else to the edge of the processed stretch ``span`` = (t0, t1) ('range'):
    a first (last) scene without dialogue has only its end (start) in scenes.json, and its
    other end is the start (end) of the stretch. A cue whose way to a timed line passes a line
    out of the processed stretch (align status out_of_range) is out of it too: no window; so is
    a cue of a scene without a timed line whose way to the edge of the stretch passes one (the
    head or tail was cut off: the scene may lie outside). A cue of a scene that is not in the
    video (scenes.json absent / out_of_range) gets none either; nor does a cue of a scene with
    no timed line and no scenes.json time (it is not known to be in the video).

    A dialogue.json line counts only when it carries the same ``scene_id`` as the script line
    (an older dialogue.json without it gives no window). ``window_by``: what each end came from
    ('line' | 'scene' | 'other_scene' | 'range'); ``window_out``: whether each end lies past
    another scene of the script — always for 'other_scene'; for 'range' when a scene that is not
    marked absent comes between the cue's scene and that edge in script order (the script's first
    scene reaches the start of the stretch, its last the end: no scene next to it there);
    ``window_lines``: [line before, line after]
    used; ``window_estimated``: an end rests on a line whose time is a guess (align ``timing``
    voice / even, ``status`` unheard) or on a scene whose place was not found for sure (status
    other than 'found')."""
    doc = doc or {}
    timed = {ln.get("id"): ln for ln in lines or [] if has_time(ln)}
    status = {ln.get("id"): ln.get("status") for ln in lines or []}
    script_lines = doc.get("lines") or []
    scene_of = {ln.get("id"): ln.get("scene_id") for ln in script_lines}
    st = scene_times(scenes)
    edge = tuple(span) if span else (None, None)

    def usable(lid):
        ln = timed.get(lid)
        if ln is None or "scene_id" not in ln or ln.get("scene_id") != scene_of.get(lid):
            return None
        return ln

    # the dialogue lines in document order (by element; a script without elements: its lines)
    elements = doc.get("elements") or []
    el_pos = {e.get("id"): i for i, e in enumerate(elements)}
    seq = [(i, e["line_id"]) for i, e in enumerate(elements) if e.get("type") == "dialogue" and e.get("line_id")]
    if not seq:
        seq = [(k, ln.get("id")) for k, ln in enumerate(script_lines)]
    at = {lid: k for k, (_, lid) in enumerate(seq)}
    idx = [i for i, _ in seq]
    # the scenes in script order that may be in the video: one before (after) the cue's scene may
    # lie between it and the start (end) of the stretch
    order = [s.get("id") for s in doc.get("scenes") or [] if isinstance(s, dict) and s.get("id")] or \
        list(dict.fromkeys([e.get("scene_id") for e in elements] + [ln.get("scene_id") for ln in script_lines]))
    rank = {sid: k for k, sid in enumerate(s for s in order if s and st.get(s, {}).get("status") != "absent")}

    def out_end(by, side, scene):
        if by != "range":
            return by == "other_scene"
        k = rank.get(scene)
        return k is None or (k > 0 if side < 0 else k < len(rank) - 1)

    own: dict[str, list[dict]] = {}                    # the timed lines of each scene
    for _, lid in seq:
        if usable(lid) is not None:
            own.setdefault(scene_of.get(lid), []).append(timed[lid])

    def scene_edge(scene, side):
        """The scene's start (side -1) / end (+1) when it bounds more than the scene's own lines."""
        t = st.get(scene, {}).get("start" if side < 0 else "end")
        if t is None:
            return None
        mine = own.get(scene)
        if not mine:
            return float(t)
        if side < 0:
            return float(t) if float(t) < min(float(ln["start"]) for ln in mine) - EDGE_TOL_S else None
        return float(t) if float(t) > max(float(ln["end"]) for ln in mine) + EDGE_TOL_S else None

    def bound(k, side, scene):
        """(time, by, line) of the window end, from position ``k`` going ``side``; None: no window."""
        passed_out, left = False, False
        while 0 <= k < len(seq):
            lid = seq[k][1]
            if not left and scene_of.get(lid) != scene:
                left = True
                t = scene_edge(scene, side)
                if t is not None:
                    return t, "scene", None
            ln = usable(lid)
            if ln is not None:
                if passed_out:
                    return None                        # lines out of the stretch lie between: so does the cue
                t = float(ln["end"] if side < 0 else ln["start"])
                return t, ("line" if scene_of.get(lid) == scene else "other_scene"), ln
            passed_out = passed_out or status.get(lid) == "out_of_range"
            k += side
        if not left:
            t = scene_edge(scene, side)
            if t is not None:
                return t, "scene", None
        if passed_out and scene not in own:
            return None                                # a scene without a timed line beyond lines out of the stretch
        t = edge[0] if side < 0 else edge[1]
        return (float(t), "range", None) if t is not None else None

    for c in cues:
        c["window"], c["window_by"], c["window_lines"] = None, None, [None, None]
        c["window_out"], c["window_estimated"] = None, False
        scene = c.get("scene_id")
        if c["type"] == "vocal" and c.get("line_id"):
            ln = usable(c["line_id"])
            if ln is not None:
                c["window"] = [round(float(ln["start"]), 3), round(float(ln["end"]), 3)]
                c["window_by"], c["window_out"] = ["line", "line"], [False, False]
                c["window_lines"] = [c["line_id"], c["line_id"]]
                c["window_estimated"] = _estimated(ln)
            continue
        if c["type"] == "vocal" and c.get("at"):        # a subtitle cell of the sound alone: its own time
            a, b = float(c["at"][0]), float(c["at"][1])
            if edge[0] is not None:
                a = max(a, float(edge[0]))
            if edge[1] is not None:
                b = min(b, float(edge[1]))
            if b > a:                                  # else out of the processed stretch
                c["window"] = [round(a, 3), round(b, 3)]
                c["window_by"], c["window_out"] = ["line", "line"], [False, False]
            continue
        sc = st.get(scene, {})
        if scene is None or sc.get("status") in NO_SCENE_TIME or (scene not in own and sc.get("start") is None
                                                                  and sc.get("end") is None):
            continue
        if c.get("element_id") in el_pos:              # the cue sits between seq[k - 1] and seq[k]
            k = bisect_right(idx, el_pos[c["element_id"]])
        elif c.get("after_line") in at:
            k = at[c["after_line"]] + 1
        elif c.get("before_line") in at:
            k = at[c["before_line"]]
        else:
            continue
        lo, hi = bound(k - 1, -1, scene), bound(k, 1, scene)
        if lo is None or hi is None:
            continue
        (lo, lo_by, prev), (hi, hi_by, nxt) = lo, hi
        if hi < lo:                                    # overlapping lines: the cue is somewhere in there
            lo, hi = hi, lo
        c["window"] = [round(lo, 3), round(hi, 3)]
        c["window_by"] = [lo_by, hi_by]
        c["window_out"] = [out_end(lo_by, -1, scene), out_end(hi_by, 1, scene)]
        c["window_lines"] = [prev["id"] if prev else None, nxt["id"] if nxt else None]
        c["window_estimated"] = any(_estimated(ln) for ln in (prev, nxt) if ln) or \
            ("scene" in (lo_by, hi_by) and sc.get("status") != "found")
    # several cues between the same two lines: each gets its share of the window for a guessed
    # time, in script order. Before a scene's first line with the window reaching into the scene
    # before (or to the start): spread inside it, none on the far end. After a scene's last line:
    # one after another from that line (써니 앞 30분: sounds written after a scene's last line
    # followed it within 4 s; those before a first line came 1–82 s before it). Scenes without
    # dialogue in a row share the time between their neighbours (or, at the head / tail of the
    # film, between the edge of the stretch and their neighbour) in script order, as
    # ``scene_starts`` places them: the cues of each share its part.
    parts = _quiet_parts(st, _quiet_ids(scenes), *edge)
    groups: dict[tuple, list[dict]] = {}
    for c in cues:
        if c.get("window") and c["type"] != "vocal":
            key, (lo, hi) = c["scene_id"], c["window"]
            if c["scene_id"] not in own and set(c["window_by"]) <= {"scene", "range"}:
                p = parts.get(c["scene_id"])
                if p and max(lo, p[0]) <= min(hi, p[1]):
                    lo, hi = max(lo, p[0]), min(hi, p[1])
                elif c["window_by"] == ["scene", "scene"]:
                    key = None                         # no part known: the cues of the row share the gap
            groups.setdefault((key, lo, hi), []).append(c)
    for (_, lo, hi), group in groups.items():
        n, far = len(group), _far_end(group[0])
        for k, c in enumerate(group):
            if far < 0:
                g = lo + (hi - lo) * (k + 1) / (n + 1)
            elif far > 0:
                g = lo + min(GUESS_STEP_S * k, (hi - lo) * k / n)
            else:
                g = lo + (hi - lo) * k / n
            c["guess"] = round(g, 3)
    for c in cues:
        if c.get("window") and "guess" not in c:
            c["guess"] = c["window"][0]
    return cues


def build(doc: dict, lines: list[dict], scenes=None, span=None) -> list[dict]:
    """``extract`` + ``windows``."""
    return windows(extract(doc), doc, lines, scenes, span)


def counts(cues: list[dict]) -> dict:
    out = {"cues": len(cues), "windowed": sum(1 for c in cues if c.get("window")),
           "elements": len({c.get("element_id") or c.get("line_id") for c in cues})}
    for t in TYPES:
        out[t] = sum(1 for c in cues if c["type"] == t)
    return out


# ----------------------------------------------------------------------------- scenes in time
def scene_starts(lines: list[dict], scenes=None, span=None) -> list[tuple[float, str]]:
    """(start, scene id) of the scenes with a known time, in time order: from scenes.json when
    it has times, else the first timed dialogue line of each scene (dialogue.json ``scene_id``).
    Empty for an older dialogue.json without scene ids.

    Scenes without dialogue in a row hold the same gap between their neighbours in scenes.json:
    they share it in script order (each its part, as ``windows`` shares it among their cues). A
    first (last) scene without dialogue has only its end (start): the start (end) of the
    processed stretch ``span`` = (t0, t1) is its other end, so such scenes in a row at the head
    (tail) of the film share the time between that edge and their neighbour the same way."""
    st = scene_times(scenes)
    t0 = span[0] if span and span[0] is not None else None
    t1 = span[1] if span and span[1] is not None else None
    parts = _quiet_parts(st, _quiet_ids(scenes), t0, t1)
    out = []
    for sid, v in st.items():                          # script order
        start = parts[sid][0] if sid in parts else v["start"] if v["start"] is not None else \
            t0 if v["end"] is not None else None
        if start is not None:
            out.append((round(float(start), 3), sid))
    if not out:
        first: dict[str, float] = {}
        for ln in lines or []:
            if has_time(ln) and ln.get("scene_id"):
                first[ln["scene_id"]] = min(first.get(ln["scene_id"], float("inf")), float(ln["start"]))
        out = [(t, sid) for sid, t in first.items()]
    return sorted(out)


def scene_at(t: float, starts: list[tuple[float, str]]) -> str | None:
    """The scene a time falls in: the last scene that started at or before it; None before the first."""
    k = bisect_right([s for s, _ in starts], float(t))
    return starts[k - 1][1] if k else None


def context_at(doc: dict, lines: list[dict], t: float, starts: list[tuple[float, str]], n: int = 3) -> dict | None:
    """The script around time ``t`` for a plot question: {"scene_id", "heading", "actions"} —
    the scene heading and up to ``n`` action lines of that scene written between the timed
    dialogue lines around ``t``. None when the scene is not known."""
    doc = doc or {}
    sid = scene_at(t, starts)
    if sid is None:
        return None
    heading = next((s.get("heading") for s in doc.get("scenes") or [] if s.get("id") == sid), None)
    elements = doc.get("elements") or []
    pos = {e.get("line_id"): i for i, e in enumerate(elements) if e.get("type") == "dialogue" and e.get("line_id")}
    timed = sorted((ln for ln in lines or [] if has_time(ln) and ln.get("scene_id") == sid and ln.get("id") in pos),
                   key=lambda ln: float(ln["start"]))
    before = [ln for ln in timed if float(ln["start"]) <= t]
    after = [ln for ln in timed if float(ln["start"]) > t]
    in_scene = [i for i, e in enumerate(elements) if e.get("scene_id") == sid]
    if not in_scene:
        return {"scene_id": sid, "heading": heading, "actions": []}
    lo = pos[before[-1]["id"]] if before else in_scene[0]
    hi = pos[after[0]["id"]] if after else in_scene[-1] + 1
    acts = [short(e.get("text")) for e in elements[lo:hi]
            if e.get("type") == "action" and e.get("scene_id") == sid and e.get("text")]
    if not acts:                                       # nothing between the two lines: the nearest before
        acts = [short(e.get("text")) for e in elements[in_scene[0]:lo]
                if e.get("type") == "action" and e.get("scene_id") == sid and e.get("text")][-1:]
    return {"scene_id": sid, "heading": heading, "actions": acts[:n]}


# ----------------------------------------------------------------------------- matching
def _family(c: dict) -> set[str]:
    lab = c.get("label") or {}
    fams = {phrases.info(lab.get("class", ""))["family"]}
    fams |= {phrases.info(x.get("class", ""))["family"] for x in lab.get("competing") or []}
    return {f for f in fams if f}


def compatible(cue: dict, c: dict) -> str | None:
    """How a detected sound candidate fits a sound cue: 'kind' (its family is one the cue
    names), 'unnamed' (the detector could not name it), 'any' (the cue does not say what
    sounds and the candidate is named) or None (a different sound)."""
    lab = c.get("label") or {}
    fams = set(cue.get("families") or [])
    if lab.get("unknown"):
        # even an unnamed sound leans one way: a person's voice and a thing do not stand for each other
        best = phrases.info(lab.get("class", "")).get("family") or ""
        if fams and best and (fams <= VOICE_FAMILIES) != (best in VOICE_FAMILIES):
            return None
        return "unnamed"
    ono = phrases.info(lab["class"]).get("ono") if lab.get("class") else ""
    if ono and any(ono in k or k in ono for k in cue.get("keywords") or [] if k):
        return "kind"                                  # '탕!' written, a gunshot detected
    if not fams:
        return "any"
    if _family(c) & fams:
        return "kind"
    return None


def is_reason(cue: dict, how: str | None, onset: float | None = None) -> bool:
    """Whether a cue attached to a detected sound (``how`` = ``compatible``) at ``onset`` is a
    reason to include it ('대본 지문에 적힌 소리'). Not when: the cue does not say which sound it is
    (``vague``, or a named sound matched to it only as 'any'); the window rests on guessed times
    (``window_estimated``, 감사 1-08); the sound lies far from the cue's own scene (``off_scene``);
    it is a person's own sound — a laugh, a cough, crying — that is not written as off screen
    (the person is mostly in view, 감사 8-12; a scream counts); or it is a speaker's own sound
    from the dialogue (``vocal``)."""
    if cue.get("type") != "sfx" or how not in ("kind", "unnamed") or cue.get("vague") or cue.get("window_estimated"):
        return False
    if onset is not None and off_scene(cue, onset):
        return False
    fams = set(cue.get("families") or [])
    if fams and fams <= VOICE_FAMILIES and "scream" not in fams and not cue.get("offscreen"):
        return False
    return True


_TIER = {"kind": 3, "unnamed": 2, "any": 1}
VOICE_FAMILIES = {"laughter", "crying", "scream", "cough", "breath", "moan", "sneeze", "snore", "hum_voice",
                  "whistle_voice", "body", "baby", "whisper"}


def in_window(cue: dict, onset: float, pad: float = MATCH_PAD_S) -> bool:
    w = cue.get("window")
    return bool(w) and w[0] - pad <= float(onset) <= w[1] + pad


def anchor(cue: dict) -> float | None:
    """The end of a cue's window that lies in its own scene, when only the other end reaches
    into another scene or to the edge of the processed stretch (``window_by``): music written
    before a scene's first line is the music playing near that line, not the music at the end
    of the scene before. None when both ends are of the same kind."""
    w, far = cue.get("window"), _far_end(cue)
    if not w or not far:
        return None
    return float(w[1]) if far < 0 else float(w[0])


NEAR_S = 5.0     # in a wide window: candidates this much farther from the cue's place than the nearest are as near


def _wide(cue: dict) -> bool:
    w = cue.get("window")
    return bool(w) and float(w[1]) - float(w[0]) > WIDE_S


def off_scene(cue: dict, onset: float) -> bool:
    """A sound in a wide window (> ``WIDE_S``) whose other end lies past another scene of the
    script (``window_out``: the lines of another scene, or the edge of the processed stretch with
    another scene between), farther than ``WIDE_S`` from the end in the cue's own scene
    (``anchor``): it may be a sound of that scene (where one scene ends inside the window is not
    known). Measured from that end even for cues that share it (``places`` looks near each one's
    ``guess``, which spreads over the part that may be the other scene's). Not off: a window of the
    script's first (last) scene that only reaches the start (end) of the stretch — no scene lies
    there. A cue without ``window_out`` (made before it): its 'other_scene' / 'range' end counts."""
    a = anchor(cue)
    if a is None or not _wide(cue) or abs(float(onset) - a) <= WIDE_S:
        return False
    out = cue.get("window_out")
    return out is None or bool(out[0 if _far_end(cue) < 0 else 1])


def places(cues: list[dict]) -> dict[int, float]:
    """{id(cue): where in its wide window its sound is looked for first}: the end in its own
    scene (``anchor``); cues that share the window and that end, or share a window without one,
    each their ``guess`` (script order). A cue alone in a wide window without an anchor has no
    place: the likeliest candidate of its kind is taken wherever it is."""
    groups: dict[tuple, list[dict]] = {}
    for c in cues:
        if c["type"] in ("sfx", "vocal") and _wide(c):
            groups.setdefault(tuple(c["window"]), []).append(c)
    out = {}
    for group in groups.values():
        for c in group:
            a = anchor(c)
            if a is not None and sum(1 for d in group if anchor(d) == a) == 1:
                out[id(c)] = a
            elif len(group) > 1 and c.get("guess") is not None:
                out[id(c)] = float(c["guess"])
    return out


def _fits(cue: dict, c: dict, wide: bool) -> str | None:
    """``compatible`` as matching takes it: None for a candidate the cue does not take."""
    how = compatible(cue, c)
    return None if how is None or how == "any" or (wide and how != "kind") else how


def match_sounds(cues: list[dict], sfx: list[dict]) -> dict[str, dict]:
    """{sound candidate id: cue} — every sound or speaker's-own-sound cue with a window takes the
    most likely detected candidate that starts in it (one whose best guess is of the kind the cue
    names before one that only has it as a runner-up; a named candidate of the kind before an
    unnamed one; louder first), each candidate at most once. A cue that does not say which sound
    it is takes no named candidate ('any'): nothing tells that the two are the same sound.

    In a window longer than ``WIDE_S`` only a candidate of the named kind is taken, and where the
    cue is matters before how likely the candidate is (``_match_placed``): the cues of one wide
    window with ``places`` take, together, the candidates nearest their places — for each cue the
    likeliest of those within ``NEAR_S`` of its nearest one, the nearest pairs first — in script
    order (a cue takes no sound before one an earlier cue of the window took)."""
    taken: dict[str, dict] = {}
    place = places(cues)
    order = [c for c in cues if c["type"] in ("sfx", "vocal") and c.get("window")]
    done: set[int] = set()
    for cue in order:
        if id(cue) in done:
            continue
        if id(cue) in place:                           # the cues of its wide window, together
            group = [d for d in order if id(d) in place and tuple(d["window"]) == tuple(cue["window"])]
            done.update(id(d) for d in group)
            taken.update(_match_placed(group, sfx, place, taken))
            continue
        wide = _wide(cue)
        pool = [(c, how) for c in sfx if c.get("id") not in taken and in_window(cue, c["onset"])
                for how in [_fits(cue, c, wide)] if how is not None]
        if pool:
            taken[max(pool, key=lambda ch: _likely(cue, *ch))[0]["id"]] = cue
    return taken


def _likely(cue: dict, c: dict, how: str) -> tuple:
    """How likely candidate ``c`` is the sound of ``cue``: its best guess is of a family the cue
    names (not only a runner-up), named before unnamed, not minor, louder, earlier."""
    first = phrases.info((c.get("label") or {}).get("class", "")).get("family") in set(cue.get("families") or [])
    return first, _TIER[how], not c.get("minor"), float(c.get("level_jump_db") or 0.0), -float(c["onset"])


def _match_placed(group: list[dict], sfx: list[dict], place: dict[int, float], taken: dict) -> dict[str, dict]:
    """{candidate id: cue} for the cues of one wide window (script order) that have places."""
    pairs = []
    for i, cue in enumerate(group):
        ref = place[id(cue)]
        pool = [(c, how) for c in sfx if c.get("id") not in taken and in_window(cue, c["onset"])
                for how in [_fits(cue, c, True)] if how is not None]
        if not pool:
            continue
        nearest = min(abs(float(c["onset"]) - ref) for c, _ in pool)
        for c, how in pool:
            d = abs(float(c["onset"]) - ref)
            pairs.append((nearest if d <= nearest + NEAR_S else d, _likely(cue, c, how), i, c))
    pairs.sort(key=lambda p: p[2])
    pairs.sort(key=lambda p: p[1], reverse=True)
    pairs.sort(key=lambda p: p[0])                     # nearest first, then the likeliest, then script order
    got: dict[int, float] = {}
    out: dict[str, dict] = {}
    for _, _, i, c in pairs:
        t = float(c["onset"])
        if i in got or c["id"] in out or any((j < i and tj > t) or (j > i and tj < t) for j, tj in got.items()):
            continue
        got[i] = t
        out[c["id"]] = group[i]
    return out


def match_music(cues: list[dict], music: list[dict]) -> tuple[dict[str, list[dict]], dict[str, dict]]:
    """({music candidate id: [music cues]}, {stop candidate id: silence cue}). A music cue goes
    to the music candidate that starts first in its window, else to the one whose music is
    playing there; a silence cue to a music-stop candidate in its window. In a window longer
    than ``WIDE_S`` with one end in the cue's own scene (``anchor``) the music nearest that end
    is taken (the first candidate of it in the window), not the first music of the window: a
    window that reaches back to the start of the video holds the opening titles' music too."""
    hits: dict[str, list[dict]] = {}
    stops: dict[str, dict] = {}
    plays = [m for m in music if (m.get("music") or {}).get("event") != "stop"]
    for cue in cues:
        if not cue.get("window"):
            continue
        if cue["type"] == "silence":
            for m in music:
                if (m.get("music") or {}).get("event") == "stop" and m["id"] not in stops \
                        and in_window(cue, m["onset"]):
                    stops[m["id"]] = cue
                    break
            continue
        if cue["type"] != "music":
            continue
        lo, hi = cue["window"]
        starting = [m for m in plays if in_window(cue, m["onset"])]
        near = anchor(cue) if hi - lo > WIDE_S else None
        if near is not None and starting:

            def gap(m):                                # how far the candidate's music is from the anchor
                a, b = (m.get("music") or {}).get("segment") or (m["onset"], m["onset"])
                return max(0.0, float(a) - near, near - float(b))

            closest = min(gap(m) for m in starting)
            starting = sorted((m for m in starting if gap(m) <= closest + 1e-6), key=lambda m: float(m["onset"]))
        if not starting:                               # the music of a candidate that began before is playing here
            seg = [m for m in plays if (m.get("music") or {}).get("segment")
                   and m["music"]["segment"][0] <= hi + MATCH_PAD_S and m["music"]["segment"][1] >= lo - MATCH_PAD_S
                   and m["onset"] <= hi + MATCH_PAD_S]
            starting = sorted(seg, key=lambda m: m["onset"])[-1:]
        for m in starting[:1]:
            hits.setdefault(m["id"], []).append(cue)
    return hits, stops
