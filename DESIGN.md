# CBoE 구현 설계 (Captioning Based on Evidence)

고치는 사람을 위한 문서다. 처음 설계는 발표 덱 `Desktop\Study\아이디어 발표\CBoE(5조).pptx`(2026-10-15판) 05 시스템 설계(5.1~5.10)와
03 UI 활용 예시(3.1~3.5)이고, 배치 수치는 `시스템 구성 확정 2026-10-13.md`(이하 10-13 메모) 5절에서 왔다. 지금 자막의 기준은 넷플릭스
한국어 Timed Text 지침 II장 SDH(`II.n`, II장에 규칙이 없으면 I장 `I.n`), 자막 타이밍 지침(`TG n`), 일반 요건(`GR n`)이다.
덱·메모와 지침이 다르면 지침을 따른다. 코드 주석의 '감사 N-NN'은 2026-11-10 지침 감사의 지적 번호다(노트는 저장소 밖).

## 0. 범위와 원칙

- 입력: 영상 1개 + 촬영 대본(README '받는 대본 형식'). 대본 없이도 돈다(받아쓰기 줄을 대사로 씀, 화자 이름 없음).
- 후보(`kind`) 세 가지: 효과음 `sfx`, 음악 `music`, 화자 이름 표시 `speaker`. id 머리글자는 S / M / P, 대본 단서로 만든 후보는 단서 id(`C0001`)를 그대로 쓴다.
- 추천(`rec`) 세 가지: 포함 추천 `include`, 제외 추천 `exclude`, 확인 필요 `check`. 배치가 안 되면 배치 확인 필요(`no_room`).
- 지키는 것:
  1. 후보는 추천이 무엇이든 지우지 않는다. 마지막 결정은 작가(검수 화면)가 한다.
  2. 대사의 낱말은 저절로 바꾸지 않는다. 정렬은 대조를 표시만 하고(`differs`, `hints`), 배치는 칸·줄을 나누고 표시용 문장부호만
     다듬는다(`style.tidy_line`). 글은 작가가 고를 때만 바뀐다.
  3. '대본에 없는 말'(받아쓰기에만 있는 말, `extras`)은 저절로 넣지 않는다.
  4. 대본 단서만으로는 아무것도 포함 추천하지 않는다(쓰인 소리가 완성된 영화에 있는지 잰 적이 없다).
  5. 시각이 추정이면 그 표시가 칸까지 따라간다(줄·후보의 `estimated` → 칸 `flags`의 `timing_estimated`).
  6. 지침의 숫자와 표기 틀은 `cboe/style.py` 한 곳에만 둔다(§4.7).
- 내보내기: SRT, VTT, TTML.

## 1. 단계 그래프와 의존

`cboe/pipeline.py`의 `STAGES` 차례:

```
① 후보 검출 · 근거 수집  ingest → script → separate → cuts → sed → asr → align → cues → sfx → music → speaker → screen → dialog_ev
② 추천 · 에이전트 재확인  recommend → agent
③ 초안 배치             state.Project.recompute() = settle (§4.5): 실행 끝에 items.json·dialogue.json 이 있으면, 검수 서버는 고칠 때마다
```

| 단계 | 모듈 | 결과 파일 (`OUTPUTS`) | 그 결과를 읽는 단계 (`USED_BY`) |
|---|---|---|---|
| ingest | `stages/ingest.py`, `media.py` | `audio/mix_48k.wav` `audio/mix_16k.wav` | separate, cuts, sed, asr |
| script | `stages/script.py`, `script.py`, `screenplay.py` | `script.json` | align |
| separate | `stages/separate.py` | `audio/{speech,music,effects}_{48k,16k}.wav` | sed, asr |
| cuts | `stages/cuts.py` | `cuts.json` | speaker, screen, recommend |
| sed | `stages/sed.py` | `sed_effects.npz` `sed_mix.npz` `sed_music.npz` `env.npz` | sfx, music, align |
| asr | `stages/asr.py` | `asr.json` | align |
| align | `stages/align.py`, `scenes.py` | `dialogue.json` `scenes.json` | speaker, dialog_ev, recommend |
| cues | `stages/cues.py`, `cues.py` | `cues.json` | recommend |
| sfx | `stages/sfx.py`, `ontology.py`, `phrases.py` | `sfx.json` | screen, dialog_ev, recommend |
| music | `stages/music.py` | `music.json` | dialog_ev, recommend |
| speaker | `stages/speaker.py`, `people.py` | `speaker.json` `speaker.part.json` | recommend |
| screen | `stages/screen.py`, `llm.py` | `screen.json` | recommend |
| dialog_ev | `stages/dialog_ev.py`, `llm.py` | `dialog_ev.json` | recommend |
| recommend | `stages/recommend.py` | `items.json` | (없음) |
| agent | `stages/agent.py`, `agent_tools.py` | `agent_done.json` `agent_results.json` | (없음) |
| (배치) | `state.py`, `placement.py`, `textutil.py`, `people.py`, `style.py` | `draft.json` | 검수 서버, `export.py`, `check.py` |

- `--force x`는 x와 `dependents([x])`(USED_BY를 따라 닿는 모든 단계)의 결과 파일을 지우고 다시 돌린다. `--force all`은 모든 단계.
  후보 id가 차례 번호라서, 앞 결과가 바뀌면 옛 id로 묶인 뒤 결과를 남기면 안 된다.
- `script`는 실행마다 대본 파일을 본다. 읽어 둔 것(version 2)의 `source.sha1`과 다르면 다시 읽고, `dependents(["align"])`의 결과를 지우고
  (`clear_below`) 단계 결과에 `cleared`로 적는다. pipeline은 그 단계들을 같은 실행에서 다시 돈다. version이 없는 예전 `script.json`은
  대본 파일이 더 새것일 때만 다시 읽고, 대사 줄이 같으면 아무것도 지우지 않는다. 받아쓰기는 대본에서 이름 힌트만 받으므로
  대본이 바뀌어도 다시 하지 않는다(`USED_BY["script"] = ["align"]`).
- `cues`는 건너뛰지 않는다. 값싸고 순수해서 실행마다 새로 만든다. `recommend`도 `stages/cues.load`로 단서를 새로 만들어 쓰고 `cues.json`을
  다시 쓴다(`--stages script`처럼 cues가 빠진 실행에서도 묵은 단서를 읽지 않는다).
- `agent`도 건너뛰지 않는다. 남은 대기열을 이어 보고, 결과는 후보 지문(`kind@onset`)으로 붙인다. 그래서 `recommend`를 다시 돌려도
  바뀌지 않은 후보의 재확인은 남고, `USED_BY["recommend"]`는 비어 있다(`--force agent`만 지운다).
- 작업 폴더 하나에 실행 하나: `.run.lock`(filelock)을 2초 안에 못 잡으면 `RuntimeError`('다른 곳에서 이 작업을 분석하는 중').
  시작 전에 `guard.check_window`(연구 규칙)를 부른다.
- `run_log.json`: 단계마다 일을 한 실행의 결과(건너뛴 실행은 덮어쓰지 않음). `run_history.json`: 실행마다 하나, 최근 `RUN_HISTORY_MAX`(200)개.
- 로그 줄 `== stage x`, `== stage x: {요약}`, `PROGRESS x 3/12 메모`(`util.progress`)를 GUI가 읽어 진행을 보인다(§8).

## 2. 작업 폴더

모든 시각은 영상 처음부터의 절대 초(float). 처리 구간 `[t0, t1)`만 다룬다(`media.py`의 읽기는 그 밖을 거절한다).

```
<work>/
  project.json        title, video, script(경로 또는 null), t0, t1, fps, duration, width, height, profile(adult | children)
  audio/mix_48k.wav mix_16k.wav                     처리 구간만 (파일의 0초 = t0)
  audio/{speech,music,effects}_{48k,16k}.wav        분리 트랙
  script.json         대본 문서 (§3.1)
  cuts.json           {"fps", "cuts": [t], "shots": [[s, e]], "model"}
  sed_effects.npz sed_mix.npz sed_music.npz          probs[n,447] float16, fps=25, t0
  env.npz             효과음·음악·대사 트랙 소리 세기(100Hz, dB). 검수 화면의 소리 세기 곡선(/api/envelope)도 이것
  asr.json            {"language", "words": [{"w","s","e","p"}], "vad", "chunks": [{"start","end","language","detected","words"}],
                       "hint_echo", "hotwords"}
  dialogue.json       대사 줄과 대본에 없는 말 (§3.2)
  scenes.json         장면 자리 (§3.3)
  cues.json           대본 단서 (§3.4)
  sfx.json            효과음 후보 (추천 전)          music.json   음악 구간·후보·멈춤
  speaker.json        줄마다 말하는 얼굴 근거 (멈춘 실행은 speaker.part.json 에서 이어감)
  screen.json         효과음 후보마다 화면 근거       dialog_ev.json  효과음 후보마다 대사 근거
  items.json          추천까지 끝난 후보 전체 (§3.5, 검수 화면의 바탕)
  agent/<후보>.json   에이전트 기록(질문, 도구 호출과 돌려받은 것, 답)   agent_results.json  agent_done.json
  review.json         작가 결정 (§3.6, 검수 화면이 씀)
  draft.json          배치 결과 (§3.7, 작가 결정이 바뀔 때마다 다시 만듦)
  export/<제목>.srt .vtt .ttml
  run_log.json  run_history.json  llm_log.jsonl  .run.lock     실행 기록, LLM 호출 기록, 실행 잠금
  gui_run.log  gui_job.json                                    GUI 로 돌린 분석의 로그와 결과
  review.mp4  review.mp4.json                                  재생용 영상 (README '재생용 영상')
```

단계마다 결과 파일이 있으면 건너뛴다(`--force <단계>`로 다시). 예외는 §1의 `script`(파일을 견줌)·`cues`·`agent`.

## 3. 데이터 형식

예시의 이름·대사는 지어낸 것이다(README '받는 대본 형식'의 대본 토막).

### 3.1 대본 문서 (`script.json`, `script.read()`, version 2)
```json
{"version": 2, "format": "screenplay", "layout": "tab", "timed": false,
 "source": {"path": "…\\대본.txt", "sha1": "…", "size": 312, "mtime": 1762750000.0},
 "characters": ["나래", "가람"],
 "people": [{"id": "P01", "name": "나래", "cues": ["나래"]}, {"id": "P02", "name": "가람", "cues": ["가람(V.O)", "가람"]}],
 "scenes": [{"id": "SC001", "number": "12", "heading": "S#12. 버스 정류장 / 밤", "place": "버스 정류장", "time": "밤"}],
 "elements": [{"id": "E0002", "type": "action", "scene_id": "SC001", "text": "멀리서 버스 경적이 울린다."},
              {"id": "E0004", "type": "dialogue", "scene_id": "SC001", "text": "(한숨) 곧 온대.", "line_id": "L0002", "cue": "가람(V.O)"}],
 "lines": [{"id": "L0002", "speaker": "가람", "text": "곧 온대.", "raw": "(한숨) 곧 온대.", "scene_id": "SC001", "element_id": "E0004",
            "language": null, "sung": false, "source": "offscreen", "delivery": [], "vocal": ["한숨"],
            "directions": [{"text": "한숨", "type": "vocal"}]}]}
```
- `format`: `screenplay`(촬영 대본) | `lines`(한 줄에 한 대사, 표, JSON) | `timed`(모든 줄에 `start`·`end`가 있음: 자막). `layout`(촬영 대본만):
  화자 칸을 가른 방법 `columns` | `tab` | `gap` | `stacked`(이름 줄 아래 대사, 대사가 끝나는 곳은 어림).
- `elements[].type`: `heading` 장면 머리, `action` 지문, `dialogue` 대사, `onscreen` 화면 글자 지시, `transition` 전환, `other`.
  첫 장면 머리 앞(표지·배역표)은 `other`이고 `scene_id`가 없다. 장면 번호가 없는 대본은 `number`가 null이다.
- 말이 없는 대사(화자 칸 + 괄호 지시뿐, '……'뿐)는 줄이 아니라 `action` 요소로 남고 `speaker`·`cue`·`directions`를 단다(말하는 사람의 소리 단서가 됨).
- `lines[]`: `text`는 말하는 글(괄호 지시와 `/` 빼고), `raw`는 적힌 그대로. 한 대사 안에서 언어·노래 표시가 바뀌면 그 자리에서 줄을 나눈다.
  `directions[].type`: `language`(줄의 `language`는 외국어 이름, 한국어는 null) | `sung` | `source` | `delivery`(말투, '웃으며'처럼 소리를 내며 말하는 꼴 포함)
  | `vocal`(소리. 다른 사람의 소리는 `by`가 붙고 줄의 `vocal`에 넣지 않음) | `to`(말을 건네는 상대) | `action`.
- `source`: `offscreen` `narration` `inner` `phone` `radio` `broadcast` `video`. 화자 칸의 꼬리표(`(V.O)`, `(NA)`, `(E)`)나 괄호로 싼 이름(`(가람)` = 화면 밖)에서 온다.
  `(소리)`처럼 사람이 아닌 칸은 speaker null + source, `(뉴스)`·`(무전)` 같은 매체는 speaker를 그 낱말로 두고 source를 단다. `가람, 나래`는 `가람/나래`(함께 말함).
- version이 없는 예전 `script.json`(`{"lines": [{"id","speaker","text"}], "characters"}`)도 그대로 쓴다(장면·지시 정보 없음, §1).

### 3.2 대사 줄 (`dialogue.json`)
```json
{"mode": "scenes",
 "lines": [{"id": "L0005", "speaker": "나래", "text": "버스가 곧 온대.", "scene_id": "SC001", "...": "script.json 줄의 다른 칸 그대로",
            "start": 12.40, "end": 13.62,
            "words": [{"w": "버스가", "s": 12.40, "e": 12.81}, {"w": "곧", "s": 12.90, "e": 13.05}, {"w": "온대.", "s": 13.10, "e": 13.62}],
            "aligned": true, "match": 0.71, "timing": "asr", "status": "differs", "asr_text": "택시가 곧 온대",
            "diff": [{"kind": "word", "script": "버스가", "heard": "택시가", "at": [0, 1]}]}],
 "extras": [{"id": "X0001", "start": 20.1, "end": 20.9, "text": "어디 가", "words": [{"w": "어디", "s": 20.1, "e": 20.4}], "scene_id": "SC001"}]}
```
- `mode`: `asr`(대본 없음: 받아쓰기 줄이 대사) | `timed`(시각 있는 대본: 그 시각, 받아쓰기는 낱말 시각만) | `scenes`(장면 먼저, §4.2) | `aligned`(그 밖 대본, 한 번에 맞춤).
- 줄은 대본 줄의 칸을 그대로 싣는다(예전 대본이면 `align.LINE_DEFAULTS`). `words`는 대본 글의 어절과 같은 수·같은 차례이고, 맞추지 못한 어절은 앞뒤에서 보간한다.
- `timing`: `asr` 받아쓰기로 맞춤 | `script` 대본의 시각 | `voice` 목소리 구간에 놓음 | `even` 목소리 구간도 없어 고르게 놓음. `voice`·`even`은 추정이다.
- `status`(§4.1): `ok` | `differs` | `unheard` | `absent` | `out_of_range`. 뒤의 둘은 `start`·`end`가 null이고, 어림이 있으면 `guess` [s, e].
- `asr_text`: 그 줄 위에서 들린 말. `diff[]`: 대본과 다른 토막 — `kind` `word`(낱말이 다름, 이것만 `differs`로 올림) | `minor`(`MINOR_CHARS`=2글자 이하이고
  비슷한 글자: 받아쓰기 잘못일 수 있음) | `number`(숫자 표기), `script`·`heard`(양쪽 낱말), `at`(대본 어절 범위), `spelling`(맞춤법 차이로 보임 → 줄의 `hints: ["spelling"]`).
  띄어쓰기·문장부호 차이는 토막이 되지 않는다. 대본에 외국어로 표시된 줄은 대조하지 않고 `note: "foreign"`.
- `extras[]`: 어느 대본 줄도 쓰지 않은 받아쓰기 낱말 묶음(대본에 없는 말, `EXTRA_GAP` 0.8초 쉼으로 끊음). `INTERJECTIONS`·감탄뿐인 묶음은 버린다.
  `short`(`EXTRA_SHORT` 2글자 이하), `overlaps`(겹치는 못 들은 줄 id), `suspect`(`credit` | `greeting` | `repeat`: 받아쓰기 헛소리 꼴이면서 목소리가 없음,
  근거 점수는 `suspect_by`). 의심 표시가 있어도 지우지 않는다.

### 3.3 장면 자리 (`scenes.json`)
```json
{"scenes": [{"id": "SC001", "number": "12", "heading": "S#12. 버스 정류장 / 밤", "start": 10.2, "end": 31.7, "span": [9.8, 33.0],
             "status": "found", "score": 0.42, "how": "skeleton", "lines": ["L0001", "L0002"], "note": null}]}
```
- `status`: `found` | `unknown`(자리를 모름, 대사 없는 장면도) | `absent`(영화에 없음, 어림 자리는 `guess`) | `out_of_range`(처리 구간 밖).
  `how`: `skeleton` | `moved` | `weak` | `lines`(§4.2). `start`·`end`는 **맞춘 줄**의 범위라 장면 경계가 아니다. 시각 있는 모든 줄이 더 넓으면 `span`.
  대사 없는 장면이 이어지면 모두 같은 틈 [앞 장면 끝, 뒤 장면 시작]을 받는다. `note: "foreign"`: 다른 언어 장면(목소리 구간에 놓음).

### 3.4 대본 단서 (`cues.json`, `cues.build()`)
```json
{"version": 1, "counts": {"cues": 2, "windowed": 2, "elements": 2, "sfx": 1, "music": 0, "silence": 0, "vocal": 1},
 "cues": [{"id": "C0001", "type": "sfx", "scene_id": "SC001", "element_id": "E0002", "line_id": null, "speaker": null,
           "text": "멀리서 버스 경적이 울린다.", "keyword": "경적", "keywords": ["경적", "울린"],
           "phrase": "[빵빵 - 경적]", "phrases": ["[빵빵 - 경적]", "[경적]"], "families": ["horn", "car"], "offscreen": true,
           "written_ono": false, "vague": false, "after_line": null, "before_line": "L0001",
           "window": [3.0, 10.2], "window_by": ["range", "line"], "window_lines": [null, "L0001"],
           "window_estimated": false, "guess": 5.4}]}
```
- `type`: `sfx`(지문의 소리 낱말·의성어) | `music`(음악·노래, 가수와 곡명이 있으면 `songs`, 화면 속 매체면 `diegetic`·`source`, 이어지는 음악이면 `continues`)
  | `silence`(침묵·정적, 음악이 멈추면 `music_stop`) | `vocal`(대사의 `(한숨)` 같은 말하는 사람의 소리, 그 줄이 `line_id`).
- 문구 후보(`phrases`)는 II.9·II.7 꼴만: 적힌 의성어 먼저(`[쾅! 쾅!]`), `[의성어 - 출처]`, 설명(`[총성]`), 곡 `[가수의 '곡명']`·`[가수의 '곡명'이 흐른다]`,
  `[침묵]`·`[정적]`, `[음악이 멈춘다]`. '…소리'뿐인 설명은 고를 거리일 뿐 문구가 되지 않고, 그것만 있는 단서는 `vague`(문구 없음).
- 낱말 목록(`SOUND_WORD`, `ONO_STRONG`, `ONO_WEAK`, `SOURCES`, `MUSIC_WORD`, `SILENCE`, `VOCAL_PHRASES`, 빼는 것 `NOT_SOUND`)은 일반 한국어 소리 낱말이다.
- 시간 창과 붙이기는 §4.3.

### 3.5 후보 (`items.json` 의 `items`)
```json
{"id": "S0007", "kind": "sfx", "t0": 8.65, "t1": 10.33, "onset": 8.65, "category": "event",
 "label": {"class": "Vehicle horn, car horn, honking", "ko": "경적", "score": 0.62, "confident": true, "unknown": false,
           "top": [{"class": "…", "ko": "…", "score": 0.6}], "competing": []},
 "phrases": [{"text": "[빵빵]", "short": "[빵빵]", "source": "detected", "note": "검출 종류"},
             {"text": "[빵빵 - 경적]", "short": "[빵빵 - 경적]", "source": "script", "note": "대본 지문"}],
 "phrase": "[빵빵]", "short": "[빵빵]", "music": null, "speaker": null,
 "cue_id": "C0001", "cue_type": "sfx", "cue_reason": true, "cue_estimated": false,
 "evidence": [{"kind": "화면", "t": 8.7, "t1": 9.5, "text": "소리 낸 동작 안 보임", "source": "vlm", "polarity": "include"},
              {"kind": "대본", "t": 8.65, "text": "대본 지문: \"멀리서 버스 경적이 울린다.\" · 화면 밖 소리로 적힘",
               "source": "script", "polarity": "include", "window": [3.0, 10.2]}],
 "rec": "include", "reason": "화면에 출처 없음 · 대본 지문에 적힌 소리", "agent_reasons": [], "flags": []}
```
- 모든 후보: `evidence[].kind` = `화면` `대사` `소리` `분위기` `변화` `대본` `자막` `에이전트` `맥락`, `polarity` = 넣는 쪽 `include` | 빼는 쪽 `exclude` | 정보만 `neutral`.
  `agent_reasons`가 있으면 에이전트 대기열에 든다. 재확인한 후보는 `agent` {`status`, `summary`, `tools`, `steps`, `recommendation`}.
  `follows`: 이 추천이 기대는 앞 후보 id('앞 자막에서 이미 알림', '앞 후보를 먼저 정해야 함', '같은 화자가 이어 말함').
- 효과음: `category`(`event` | `ambient` | `minor` | `vocal`), 대본 단서가 붙으면 `cue_id`·`cue_type`·`cue_reason`(포함할 까닭이 되나, `cues.is_reason`)·`cue_estimated`,
  이름 없는 소리가 대본 낱말로 이름을 받으면 `named_by: "script"`, 말하는 사람이 낸 소리면 `source_line`(그 줄 id), 세기만으로 잡히고 근거가 없으면 `minor: true`(화면이 접음).
- 음악: `music` {`moods`(7종 안의 비율 상위 3), `change`(`고조되는` | `잦아드는` | null), `instruments`, `segment`, `event`(`start` | `cue` | `mood_change` | `stop` | `script`),
  `strength`, 멈춤이면 `drop_db`·`next_music_s`}, 단서가 붙으면 `cue_id`·`cue_ids`.
- 화자: id는 `P` + 줄 번호. `speaker` {`line_id`, `name`, `face`(`visible` | `not_visible` | `unclear`), `n_faces`, `asd`, `speaking_frames`, `name_revealed`, `timing`,
  `estimated`, `by`(`script`: 대본이 화면 밖이라 적어 모델 없이 정함), `source`}. `rec` include = 이름 표시, exclude = 이름 생략.
- 대본 단서 후보(맞는 검출 후보가 없는 단서, `recommend.script_candidate`): `id`·`cue_id`는 단서 id, `origin: "script"`, `estimated: true`,
  `onset`은 단서의 `guess`, `window`, `flags: ["script", "timing_estimated"]`, `rec`은 늘 `check`. 창이 없거나 `vague`이거나 이어지는 음악인 단서는 후보가 되지 않는다.

### 3.6 작가 결정 (`review.json`)
```json
{"items": {"S0007": {"decision": "include", "phrase": "[빵빵 - 경적]", "fp": "sfx@8.65", "at": "2026-11-10T22:10:00"}},
 "placement": {"S0011": {"action": "move", "t": 33.1, "fp": "sfx@32.40"}},
 "lines": {"L0005": {"text_from": "heard", "text": "택시가 곧 온대", "fp": "line@나래:버스가 곧 온대.#0",
                     "ctx": {"prev": ["line@가람:곧 온대.", "line@나래:왜 이렇게 안 와?"], "next": ["", ""], "n": 1}, "at": "…"}},
 "added": {"A0001": {"kind": "music", "start": 40.0, "end": 42.0, "phrase": "[음악이 멈춘다]", "at": "…"}},
 "extras": {"X0001": {"fp": "extra@20.1:어디 가", "action": "ignore", "at": "…"}},
 "people": {"나래": {"mode": "generic", "before": "여자", "reveal": "auto", "at": "…"}}}
```
| 구역 | 열쇠 | 값 |
|---|---|---|
| `items` | 후보 id | `decision`(`include` \| `exclude`, 없으면 추천대로), `phrase`, `short`, `name`, `fp`, `at` |
| `placement` | 후보 id | `action: "move"`, `t`, `fp` (배치 고치기) |
| `lines` | 줄 id | `include`, `text`, `text_from`(`script` \| `heard` \| `writer`), `name`, `language`, `sung`, `start`, `end`(둘 다 주면 시각 고정), `breaks`(새 칸이 시작할 어절 번호), `keep`(한 칸에 두기), `show_name`(화자 후보가 없는 줄만), `fp`, `ctx`, `at` |
| `added` | `A0001`… | `kind`(`line` \| `sfx` \| `music`), `start`, `end`, `text`(줄) 또는 `phrase`(소리), `name`, `language`, `sung`, `show_name`, `extra`·`extra_fp`(대본에 없는 말을 줄로 넣었을 때), `at` |
| `extras` | 대본에 없는 말 id | `fp`, `action: "ignore"`, `at` |
| `people` | 인물 이름 | `mode`(`name` \| `generic`), `before`(일반 표시), `reveal`(`auto` \| `line` \| `never`), `reveal_line`·`reveal_fp`·`reveal_ctx`, `at` |

- 어느 구역이 없어도 된다. 예전 `review.json`은 그대로 열린다.
- **지문으로 맞춰 읽기**: 후보 id·줄 id는 차례 번호라 앞 단계를 다시 돌리거나 대본을 다시 읽으면 바뀐다. 그래서 결정마다 지문을 함께 두고, 지금 같은 지문인 것에만 붙인다.
  - 후보(`Project.fingerprint`): `kind@onset`(소수 둘째 자리), 화자 후보는 `speaker@<줄 id>:<글>`. 지문이 다르면 그 id의 결정은 쓰지 않고 같은 지문의 후보를 찾는다.
    후보가 사라진 결정은 `fp:<지문>` 열쇠로 남았다가 그 후보가 다시 나오면 붙는다(`_normalise`).
  - 대사 줄(`state.line_fp`, `_pick_line`): `line@화자:글#k`(k = 같은 화자·같은 글 줄 가운데 몇째)와 `ctx`(앞 두 줄·뒤 두 줄의 `line@화자:글`, 대본 끝은 "", 같은 글 줄 수 `n`).
    ① 그때도 지금도 하나뿐인 글이면 그 줄 ② 같은 글 줄이 여럿이면 이웃이 더 맞는 줄(가까운 이웃 2점, 다음 이웃 1점, ""는 근거 아님) ③ 비기면 같은 글 줄 수가 그대로일 때만 `#k`
    ④ 그래도 못 가리면 어느 줄에도 붙이지 않고 `fp:<지문>` 열쇠로 따로 둔다. `ctx`가 없는 예전 결정은 `#k`로 맞춘다. 인물의 `reveal_line`도 같은 방법(`reveal_fp`, `reveal_ctx`).
  - 대본에 없는 말(`state.extra_fp`): `extra@<시작, 소수 첫째 자리>:<글>`.
- 글 길이 상한: 대사 글 `MAX_TEXT`(1000자), 문구 `MAX_PHRASE`(200자). 넘으면 `ReviewError`(서버는 400).

### 3.7 배치 결과 (`draft.json`, `placement.place()` 출력)
```json
{"cells": [{"id": "C0042", "start": 12.387, "end": 14.12, "lines": ["- [나래] 버스가 곧 온대", "- [빵빵]"],
            "refs": ["L0005", "S0007"], "kind": "mixed", "pos": "bottom", "flags": [],
            "check": {"chars": [11.5, 4.0], "n_lines": 2, "cps": 8.94, "dur": 1.733, "gap_next": 0.083, "violations": []}}],
 "no_room": [{"item": "S0011", "t": 32.4, "text": "[쿵]", "reason": "소리 뒤 빈틈 0.41초"}],
 "summary": {"cells": 120, "violations": 0, "no_room": 1, "dialogue_cells": 101, "sounds_placed": 19,
             "timing_estimated": 3, "overlap": 0, "flash": 0, "lines_no_room": 0},
 "fps": 23.976, "profile": "adult", "notation": 2}
```
- 칸 id(`C0001`…)는 배치마다 새로 매기는 차례 번호다(대본 단서 id와 머리글자만 같다).
- `kind`: `dialogue` | `sfx` | `music` | `mixed`. `pos`: `bottom` | `top`. `flags`: `timing_estimated`(시각 추정 줄·소리) | `overlap`(겹친 대사) | `flash`(`flash_dur` 0.25초보다 짧음).
- `check.notes`: 배치가 지키지 못한 까닭(작가의 끊을 자리로는 안 들어감, 장면 전환을 넘음 등). `no_room`의 대사 줄은 `line`·`kind: "dialogue"`가 붙는다.
- `notation`(`style.NOTATION`)이나 `profile`이 지금과 다른 draft는 내보내기 점검 전에 다시 만든다.

## 4. 규칙

### 4.1 줄 상태와 기본 넣기 (`stages/align.py` `_finish`, `state.Project._line`)

| `status` | 뜻 | 시각 | 기본 |
|---|---|---|---|
| `ok` | 받아쓰기와 맞음(대본이 외국어로 표시한 줄은 못 맞춰도 `ok` + `note: "foreign"`) | 있음 | 넣음 |
| `differs` | 맞췄지만 들린 낱말이 다름(`diff`에 `word`) | 있음 | 넣음. 글은 대본 그대로이고 작가가 고른다 |
| `unheard` | 못 맞춤. 자기 장면 안 목소리 구간에 놓음 | 추정(`estimated`) | 넣음(감사 1-03: 받아쓰기가 놓친 짧은 진짜 대사가 많다) |
| `absent` | 장면이 영화에 없음 | 없음(`guess`) | 뺌. 작가가 시작·끝을 주면 넣음 |
| `out_of_range` | 처리 구간 밖 | 없음 | 뺌. 작가가 시작·끝을 주면 넣음 |

- 작가가 뺀 줄(`include: false`)은 빠지고, 그 화자 후보도 목록·집계에서 빠진다(`line_excluded`). 빠진 까닭 `excluded`: `absent` | `out_of_range` | `writer` | `no_time`.
- 글을 바꾸면 낱말 시각을 새로 만든다: 들린 대로(`text_from: "heard"`)면 받아쓰기 낱말 시각(`heard_words`), 직접 쓴 글이면 줄 길이를 글자 폭으로
  나눈다(`even_words`). 시각만 바꾸면 있던 낱말을 함께 옮긴다. 대본 원문은 `script_text`에 남는다.
- 시작·끝을 둘 다 고정한 줄(`pinned`)은 추정이 아니다. 줄의 `origin`: `script` | `heard`(대본에 없는 말을 줄로 넣음) | `writer`(작가가 새로 넣음).
- 대조는 표시만 한다(§3.2 `diff`). 지어낸 예(`align.compare`): 대본 `버스가 곧 온대.` / 들림 `택시가 곧 온대` → `word`(differs),
  `곧 온대.` / `곧 온다` → `minor` + `spelling`, `오늘 세 시에 와.` / `오늘 3시에 와` → `number`, `왜 이렇게 안 와?` / `왜 이렇게 안와` → 토막 없음.

### 4.2 장면 먼저 정렬 (`cboe/scenes.py`, `stages/align.py` `_align_scenes`)

`format: "screenplay"`이고 장면이 있고 줄에 `scene_id`가 있으면 `mode: "scenes"`로 돈다. 대본 순서를 믿지 않고 장면 자리부터 찾는다.

| 차례 | 하는 일 | 상수 |
|---|---|---|
| 1 | 단위 = 같은 장면의 이어진 줄(`units`). 글자(띄어쓰기·문장부호 뺌) 3-gram이 받아쓰기 글자열에 있으면 그 시각에 투표. 너무 흔한 3-gram은 투표하지 않음. 창 점수 = 단위의 서로 다른 3-gram 가운데 창 안에 든 몫 | `N`=3, `COMMON_PER` `COMMON_MIN`, 창 = max(`SPAN_MIN` 25초, `SPAN_PER_CHAR` 0.3초 × 글자), 찾은 구간 = 3-gram의 `CORE_SHARE`(0.8)를 담는 가장 짧은 곳 |
| 2 | 뼈대: 단위마다 창 후보를 처리 구간 전체에서 순서 없이 찾고, 대본 순서와 시간 순서를 함께 지키는 사슬 가운데 점수 합이 가장 큰 것(`how: skeleton`) | `CANDIDATES` 3, `STRONG` 0.25, `MIN_HITS` 4, `OVERLAP_TOL` 2초 |
| 3 | 옮겨진 장면: 뼈대 밖 단위의 가장 좋은 창이 뼈대와 겹치지 않으면 그 자리(`moved`) | `MOVED` 0.5, `MOVED_HITS` 8 |
| 4 | 약한 장면: 뼈대 장면 사이 시간 안에서만 다시 찾음(`weak`) | `WEAK` 0.12, `MIN_HITS_WEAK` 3 |
| 5 | 찾은 장면마다 앞뒤 찾은 장면 사이의 받아쓰기 낱말과 글자 맞춤(아래) | |
| 6 | 못 찾은 장면: 쓰이지 않은 낱말과 맞춰 충분히 맞으면 찾음(`lines`), 자리가 없으면 `absent`, 그 밖 `unknown`. 3-gram이 많은 긴 장면이 어디서도 안 맞으면 `absent`(자리는 `guess`, 거기서 들린 말은 extras). 짧은 장면·외국어 장면·한 줄이라도 맞은 장면은 줄을 남긴다(`unheard`). 부분 구간 밖은 `out_of_range`, 구간 끝에 걸친 장면은 안쪽 줄만 | `FOUND_BY_LINES` 6글자(짧은 장면 `FOUND_BY_SHARE` 0.4), `ROOM_MIN` 1초, `LONG_SCENE_GRAMS` 20, `KEEP_MATCH` 0.5·`KEEP_LETTERS` 3 |
| 7 | 영화 순서로 시간이 거꾸로 가는 맞춤을 버리고(`_in_order`), 못 맞춘 줄을 목소리 구간에 놓는다. 찾은 장면의 못 들은 줄은 맞춘 줄 가까이 | `SCENE_SEC_PER_CHAR` 0.6초/글자 + `SCENE_SLACK` 4초 |

- 글자 맞춤(`align_lines`): difflib의 가장 긴 일치 블록(2글자 이상)이 기준점이다(최소 편집 경로는 받아쓰기가 놓친 줄을 엉뚱한 글자와 짝짓는다).
  줄 중앙값에서 `ANCHOR_FAR`(8초)보다 먼 기준점은 줄의 주된 연속 묶음(틈 ≤ `ANCHOR_GAP` 4초 + `ANCHOR_GAP_PER_CHAR` 0.5초 × 사이 글자)에 들지 않으면 버린다.
  글자의 절반 이상이 기준점이면 맞춘 줄(`aligned`)이고, 나머지 어절은 줄 안 글자 자리로 보간한다.
- 못 맞춘 줄 묶음은 앞뒤 맞춘 줄 사이의 목소리 구간(대사 트랙 Silero VAD, 원본 SED 사람 목소리, 비명·웃음 같은 줄은 효과음 트랙의 큰 소리)에
  작은 동적 계획으로 차례대로 놓는다(`timing: "voice"`, 구간이 없으면 `"even"`). 분리하면 비명·웃음은 효과음 트랙으로 간다.
- 장면이 없는 대본은 한 번에 맞춘다(`mode: "aligned"`). 부분 구간이면 맨 앞·뒤의 못 맞춘 줄 묶음이 `OUT_RUN_LINES`(5)줄 이상이거나 글자에 필요한
  시간의 절반도 못 받으면 `out_of_range`(구간 끝이 영화 끝에서 `EDGE_S` 1초보다 멀 때).
- 받아쓰기 헛소리 의심(`noise_shape`, `no_voice`): 인사말만·자막 크레디트·같은 말 되풀이 꼴이면서 목소리가 없으면(낱말 확률 < `NOISE_P` 0.5, 또는 SED 목소리 <
  `NOISE_SPEECH` 0.15, 또는 목소리 < `NOISE_SPEECH_UNDER_MUSIC` 0.4이고 음악 ≥ `NOISE_MUSIC` 0.5, SED가 없을 때만 VAD < `NOISE_VAD` 0.3) `suspect`.

### 4.3 대본 단서: 시간 창과 후보에 붙이기 (`cboe/cues.py`, `stages/recommend.py`)

창은 단서 앞의 가장 가까운 시각 있는 대사 줄 끝에서 뒤의 가장 가까운 줄 시작까지다(대본 순서). 말하는 사람의 소리(`vocal`)는 그 줄의 시각이다. 두 끝은 따로 찾는다:

| `window_by` | 그 끝을 정한 것 |
|---|---|
| `line` | 같은 장면의 시각 있는 줄 |
| `scene` | `scenes.json`의 장면 시각이 그 장면 줄 밖에 있을 때(대사 없는 장면, 첫 줄보다 이른 경계. 줄과 `EDGE_TOL_S` 0.05초 안이면 쓰지 않음) |
| `other_scene` | 그쪽에 시각 있는 줄이 장면 안에 없으면 가장 가까운 다른 장면의 시각 있는 줄 |
| `range` | 그것도 없으면 처리 구간 끝(`t0`/`t1`) |

- 창이 없는 단서: 시각 있는 줄까지 가는 길에 구간 밖 줄이 있음, 장면이 `absent`·`out_of_range`, 시각 있는 줄도 장면 시각도 없는 장면, `dialogue.json` 줄에
  `scene_id`가 없음(예전 파일). 창이 없는 단서는 `cues.json`에만 남는다.
- `window_estimated`: 기대는 줄의 시각이 추정(`voice`·`even`·`unheard`)이거나, `scene` 끝이 `found`가 아닌 장면에서 왔다.
- 어림 시각 `guess`: 같은 창의 단서들은 대본 순서로 나눈다. 앞 끝만 장면 밖이면(장면 첫 대사 앞) 창 안 (k+1)/(n+1), 뒤 끝만 장면 밖이면(마지막 대사 뒤)
  그 줄부터 `GUESS_STEP_S`(5초) 간격(창의 k/n을 넘지 않음), 그 밖은 k/n. 대사 없는 장면이 이어지면 같은 틈을 함께 나눈다.
- 효과음·말하는 사람의 소리 단서 → `match_sounds`: 창(앞뒤 `MATCH_PAD_S` 1초) 안에서 시작하는 검출 후보 가운데 종류가 맞는 것(`compatible`):
  `kind`(단서가 말한 종류 가족이거나 적힌 의성어가 검출 종류의 의성어와 맞음), `unnamed`(검출기가 이름을 못 붙인 소리. 사람 목소리와 물건 소리는 서로 받지 않음),
  `any`(단서가 무슨 소리인지 말하지 않음: 붙이지 않음). 고르는 차례: 검출 1위 종류가 맞는 것 → `kind` → `unnamed` → `minor`가 아닌 것 → 세기가 크게 오른 것 → 이른 것.
  후보 하나에 단서 하나. 창이 `WIDE_S`(30초)보다 넓으면 `kind`만 받는다.
- 음악 단서 → `match_music`: 창 안에서 처음 시작하는 음악 후보, 없으면 그때 흐르는 음악. 넓은 창에서 한쪽 끝만 자기 장면이면 그 끝(`anchor`)에 가까운 음악.
  침묵 단서 → 창 안의 음악 멈춤 후보(멈춤 후보 하나로 합침).
- 붙은 검출 후보: `cue_id`와 '대본' 근거. 이름 없는 소리는 단서 문구가 기본 문구가 된다(`named_by: "script"`, `vague`면 고를 거리만). 말하는 사람의 소리면 `source_line`.
  음악 단서의 문구(곡명)는 기본 문구 바로 뒤에 고를 거리로 들어간다.
- 포함할 까닭이 되나(`cues.is_reason`): 효과음 단서가 `kind`·`unnamed`로 붙었고, `vague`가 아니고, 창이 추정 시각에 기대지 않고, 사람 소리(웃음·기침·울음 같은
  `VOICE_FAMILIES`)라면 화면 밖으로 적혔을 때만(비명은 됨, 감사 8-12). 대사 속 말하는 사람의 소리(`vocal`)는 근거일 뿐이다(소리를 그 줄에 묶어 줄 뿐).
- 붙을 검출 후보가 없는 단서는 확인 필요 후보가 된다(§3.5). 시작은 `guess`, 길이는 `SCRIPT_SHOW_S`(효과음 2, 말하는 사람의 소리 1.5, 침묵 3, 음악 4초, 창 안에서).

### 4.4 추천 규칙의 차례 (`stages/recommend.py`)

효과음(`decide_sfx`), 위에서부터. 이 차례(보이면 제외가 줄거리 판단보다 먼저)는 덱 5.7 그대로다.

1. **이미 전해지나 → 제외 추천**: 화면에 소리 낸 출처·동작이 보임(에이전트 판단이 먼저), 대사에 이미 적힘(`dialog_ev` already), 앞 자막에서 이미 알림
   (종류가 확실한 소리이고, 같은 종류의 마지막 포함 추천이 이어지는 소리(`ambient`·`minor`)면 `SAME_SOUND_S` 20초, 사건 소리면 3초 안: `follows`).
   이름을 모르는 소리에서는 앞의 둘이 '출처 판단 어긋남'이 되어 빼지 않는다(같은 순간 다른 소리일 수 있음).
2. **넣을 근거가 있나 → 포함 추천**: 종류가 확실하고(confident, 목록 밖 아님, 경쟁 종류 없음, 또는 에이전트가 확인), 화면이 '안 보임'이나 '애매'이고, 까닭이 하나 이상:
   대사 반응·언급(그 대사의 시각이 추정이면 아님, 감사 1-08) · 화면 속 인물 반응 · 줄거리 단서 · 대본 지문에 적힌 소리(`cue_reason`) · 갑자기 큰 소리(`event`,
   `LOUD_JUMP_DB` 15dB 이상) · 장면 첫 배경 소리(`ambient`. 장면은 장면 시각을 알면 대본 장면, 아니면 화면 전환 6개 묶음) · 에이전트가 '줄거리에 필요'.
   이름만 정했고 에이전트가 '필요'라 하면 '· 문구 확인'을 붙여 포함. 에이전트가 '불필요'이고 화면이 애매하지 않으면 제외.
3. **그 밖 → 확인 필요**(까닭: 목록에 없는 소리, 소리 종류가 불확실, 출처 판단 어긋남, 화면 판단 애매·화면 확인 안 함, 넣을 근거 부족, 대사 근거의 시각이 추정,
   대본 단서의 시각이 추정). `agent_reasons`가 있으면 에이전트 대기열에 들고, 에이전트가 근거를 더하면 같은 규칙으로 다시 정한다.

음악(`decide_music`):
- 멈춤(`event: stop`, `[음악이 멈춘다]`)은 늘 확인 필요. `STOP_NEXT_S`(6초) 안에 다른 음악이 시작하면 그 말을 덧붙인다.
- 앞 음악을 잇기만 하는 후보는 사슬의 뿌리를 `follows`로 단다: 뿌리가 포함 추천이면 제외('앞 자막에서 이미 알림'), 아니면 확인 필요('앞 후보를 먼저 정해야 함').
  잇는다고 보는 것: 새 음악(`cue`)이 마지막 포함 추천 10초 안, 같은 분위기 앞 후보가 `MUSIC_CUE_SAME_S`(20초) 안의 같은 구간, 분위기 바뀜이 같은 분위기로 `MUSIC_SAME_S`(90초) 안 같은 구간.
- 분위기 1위가 `MUSIC_CONF`(0.40) 이상이고 2위보다 `MUSIC_MARGIN`(0.12) 이상 크면 포함 추천(`[무서운 음악]`), 아니면 확인 필요(`[음악]`, 에이전트 대기열).
  화면 기준과 대사 근거는 쓰지 않는다.

화자(`decide_speaker`, 시각 있는 줄마다):

| 판정 | 추천 |
|---|---|
| 말하는 얼굴이 보임 | 제외(이름 생략) |
| 대본에 화자가 없음 | 확인 필요 |
| 안 보임 + 앞 줄에서 같은 화자가 이어 말함 | 제외(`follows` = 이름을 단 줄) |
| 안 보임 + 대본이 화면 밖이라 적음 | 포함(이름 표시) |
| 안 보임 + 줄 시각이 추정 | 확인 필요(감사 3-03: 추정 시각에서 얼굴이 없다는 것은 증거가 아님) |
| 안 보임 | 포함 |
| 판정 애매 | 확인 필요 |

- 대본이 출처(`source`)를 적은 줄은 얼굴 판정과 상관없이 '안 보임'이다(`by: "script"`, 화면의 얼굴은 다른 사람).
- 이 단계의 '이어 말함'은 이름이 같고, 앞 줄 얼굴이 보이지 않고, 앞 줄 끝에서 2초 안(`recommend.build` 안의 숫자), 같은 출처일 때다. 앞 줄이 확인 필요면
  `follows`만 달고 '앞 줄을 먼저 정해야 함'. 작가 결정 뒤에는 §4.5가 다시 구한다.

### 4.5 작가 결정 뒤에 다시 구하기 (`state.Project.compose`, `settle`)

`compose()`는 dialogue.json 줄 + `review.lines` + `review.added` + items.json + `review.items`를 합쳐 '지금 쓰는' 줄과 후보를 만든다. 작가가 정한 항목은 바꾸지 않는다.
바뀐 추천은 `rec`에, 단계가 냈던 추천은 `rec_stage`에 둔다.

- **같은 화자가 이어 말함**(`_speaker_runs`, 감사 3-07): 영화 순서로 자막에 든 앞 줄이 같은 인물·같은 장면·같은 출처이고, 그 줄에서 식별됐으면(이름이 나감, 또는 얼굴이 보여
  이름이 필요 없음) 이어 말함이다. 시간 상수는 없다. 그때 얼굴이 안 보이는 줄의 화자 후보는 제외('같은 화자가 이어 말함 · 앞 줄에 이름 표시' / '앞 줄에서 이미 식별됨').
  자리 없음으로 칸을 받지 못한 줄(`unplaced`)은 아무도 알리지 못한 줄이라, 그 뒤 줄은 이어 말함이 아니다. 작가가 넣거나 되살린 줄(화자 후보 없음)은 `show_name`을 따로
  주지 않으면 이름을 보인다.
- **앞 자막에서 이미 알림**(`_followers`, 감사 6-m2·9-x1): `follows`가 달린 효과음·음악은 뿌리가 최종 포함이고, `no_room`이 아니고, 배치에서 빼지 않았을 때만 제외다.
  아니면 확인 필요('앞 후보(…)가 자막에 나가지 않음 · 확인 필요').
- **settle**: 배치 → `no_room`에 오른 줄을 알고 다시 `compose` → 표시가 바뀌었으면 다시 배치. 배치는 많아야 3번이다. `draft.json`은 이것이 쓴다.
  `Project.recompute()` = `settle()[1]`이고, 실행 끝과 검수 서버가 모두 이것을 쓴다.

### 4.6 인물: 이름이 밝혀지는 줄과 일반 표시 (`cboe/people.py`, II.8)

- 인물은 이름이다: 대본의 화자 이름(`script.json` `people`), 또는 작가가 줄에 준 이름. `가람/나래`는 둘이 함께 말한 줄이다(`SPLIT`).
- **이름이 밝혀지는 줄**(`revealed_at`): 영화 순서로 **다른 사람**이 그 이름을 낱말로 말한 첫 줄(낱말 처음부터, 뒤에 낱말 끝·부름말·호칭 `HONORIFICS`·조사 `PARTICLES`·
  서술 `COPULA`·`MORE` 꼴), 또는 스스로 이름을 댄 줄(`SELF`). 지어낸 예: `가온아`, `가온 씨`, `가온이라는`, `가온보다`는 밝힘, `운전기사` 안의 `기사`는 아님.
  한 글자 이름은 부름말(`VOCATIVES`)·호칭이 붙거나 분명한 자기소개(`SELF_ONE`)일 때만 친다. 자막에 든 줄만 읽는다.
- **표시**(`display`): 이름이 밝혀지기 전에는 II.8의 일반 표시를 쓴다("so as not to provide information that is not yet present in the narrative" =
  아직 이야기에 나오지 않은 정보를 주지 않도록). 남녀는 대본에 없으므로 작가가 인물마다 정한다: `mode: "generic"` + `before`(`남자`, `여자`, 직접 적은 말) 또는
  `mode: "name"`(처음부터 이름). 숫자로 끝나는 이름(`ROLE`, `경찰1`)은 역할 이름이라 기본이 `name`이다. `reveal`: `auto`(밝힌 줄 다음 줄부터 이름) | `line`(고른 줄부터) | `never`.
  정하지 않은 인물은 이름을 그대로 쓰고, 밝혀지기 전에 이름이 나가면 `early`(내보내기 점검의 `names_early`)에 올린다.
- **번호**: 한 장면에서 같은 표시 글자를 두 사람 이상 쓰면 그 장면에서 처음 말한 차례로 번호를 붙인다(`[남자1]`, `[남자2]`). 장면마다 다시 세고, 한 사람이면 번호가 없고,
  그 장면의 다른 표시가 이미 쓰는 번호는 건너뛴다. 장면이 없으면 영화 전체로 센다. 대본 화자 이름이 `남자`인 사람과 일반 표시 `남자`를 고른 사람도 둘로 센다.
- 표시 글은 저장하지 않고 매번 줄과 결정에서 새로 만든다.
- 화자 바꾸기 범위(`scope_lines`, `SCOPES`): `line` | `from_here` | `before` | `scene` | `person`(예전 이름 `speaker`) | `voice`. 표시 글이 아니라 인물로 고른다(두 사람이 `[남자]`로 보여도 두 사람).

### 4.7 표기는 `style.py` 한 곳

배치·내보내기·검사기·검수 서버·화면이 모두 `cboe/style.py`에서 숫자와 표기 틀을 받는다(화면은 서버 상태의 `style` = `Style.public()`). 다른 프로필이나 지침 개정은 이 파일만 고친다.
프로필은 `project.json`의 `profile`(`style.for_project`, `get("adult" | "children")`).

| 무엇 | 상수·함수 | 값·꼴 | 조항 |
|---|---|---|---|
| 한 줄 글자 | `MAX_UNITS` | 16 (한글 1, 라틴·숫자·공백·문장부호 0.5: `textutil.units`) | II.2 |
| 줄 수 | `MAX_LINES` | 2 | II.4 |
| 읽기 속도 | `PROFILES` | 성인 14, 어린이 11자/초 | II.3 |
| 칸 길이 | `MIN_DUR` `MAX_DUR` | 5/6초 ~ 7초 | GR 1 |
| 칸 사이 | `GAP_FRAMES` | 2프레임 | TG 5 |
| 반 초 · 끝 여유 | `HALF` · `Style.tail` | 0.5초 · 소리 끝 + 0.5초 | TG 1 |
| 전환 바로 앞 | `Style.near_cut` | 0.375초(24fps 9프레임) | TG 4 |
| 소리 칸 길이 | `Style.label_extra` | 글자 ÷ 초당 글자 + 1초 | 우리 선택(BBC 4.3.3) |
| 음악 칸 길이 · 합치는 범위 | `Style.music_dur` · `Style.merge_window` | 3초 · 0.5초 | 10-13 메모 5.2·5.3 |
| 몇 프레임짜리 칸 | `Style.flash_dur` | 0.25초 | 우리 선택 |
| 화자 표시 | `speaker_label`, `line_labels`, `source_label` | `[이름/출처·말투/언어] `(언어는 맨 뒤, 이름이 없으면 `[영어] `만) | II.8, II.6 |
| 함께 말함 | `joint_name` | `[가람, 나래]`, `JOINT_MAX_UNITS`(8)를 넘으면 `[함께]` | II.10 |
| 노래 | `lyric` | `♪ 가사 ♪` | II.7 |
| 효과음 | `sfx_phrase`, `sfx_with_source` | `[의성어]` 또는 `[설명]`, `[의성어 - 출처]` | II.9 |
| 음악 | `music_phrase` | `[경쾌한 음악]`, 모르면 `[음악]` | II.7 |
| 하이픈 | `HYPHEN` | `- ` | I.6, II.5 |
| 표시용 문장부호 | `tidy_line` | 줄 끝 마침표·쉼표 없앰, 한 줄의 두 문장 사이 쉼표, 점은 `…`(낱말은 그대로) | I.13, I.4 |
| 예전 표기 | `modernize_phrase`, `modernize_line` | `♬ …` → `[…]`, `(이름)` → `[이름]` | |

- `NOTATION`(2)은 표기 판이다. 다른 판으로 쓴 draft는 다시 만든다.

## 5. 재확인 에이전트 (`stages/agent.py`, `agent_tools.py`)

규칙으로 못 정한 후보(`agent_reasons`가 있는 것)만 본다. 모델이 도구를 스스로 골라 부르고, 근거가 충분해지거나 확인 한도에 닿으면 멈춘다.
에이전트는 근거·소리 이름·화면·줄거리 판단만 돌려주고, 추천은 같은 규칙(`recommend.build`)으로 다시 정한다. 포함·제외를 정하는 것은 작가다.

| 애매한 이유 | 도구 | 갱신할 내용 |
|---|---|---|
| 소리 종류가 불확실 · 목록 밖 소리 | `rescan_sound`(원본·효과음 트랙 다시 검출), `describe_sound`(소리 설명 모델 Qwen2.5-Omni-3B) | 종류, 문구 |
| 화면과 소리가 안 맞음 · 화면 애매 | `look_frames`(관련 장면 프레임) | 화면 근거 |
| 줄거리 의미가 불명확 · 음악 분위기 애매 | `dialogue_around`, `search_dialogue`, `scene_context`(앞뒤 장면·대사) | 맥락 근거, 분위기 |

- 한도: `MAX_TOOL_CALLS`(6, 모델에게 알리는 확인 한도), `MAX_TURNS`(14, CLI의 끝. 닿으면 실패로 보고 다음 실행에서 다시). 후보 수 한도는 `--agent-limit`(실행들을 합친 총수).
- 소리 이름을 정하면 문구를 II.9 꼴로 만든다(`sound_phrases`: 의성어 `ono`, 출처 명사 `source_ko`, 설명 `label_ko` → `[쿵 - 책]`, `[쿵]`, `[책 떨어지는 소리]`). 쉼표 꼴은 만들지 않는다.
  이름을 못 정하면 label을 비우고 확인 필요로 남긴다.
- 도구가 예외를 내고 답하지 못한 재확인은 `agent_results.json`의 `failed`에 `tool_error`로 남고, 한도에 세지 않으며, 다음 실행에서 다시 묻는다.
  `TOOL_ERROR_STOP`(3)번 잇따르면 남은 대기열은 다음 실행으로 미룬다.
- LLM 호출(`cboe/llm.py`)은 `claude -p` + `--json-schema`(구조화 출력)이고, 에이전트는 `--mcp-config`(로컬 MCP 서버의 도구 6개)를 더한다. 처음 메시지에서 MCP 서버가
  연결되지 않았으면 실패로 처리한다(도구 없이 낸 답을 재확인으로 저장하지 않음). 사용자 설정·CLAUDE.md·훅·플러그인·자동 메모리는 넣지 않는다(`--setting-sources ""`, `--strict-mcp-config`).

## 6. 배치 규칙 (`cboe/placement.py`, 5.9)

`place(lines, sounds, cuts, fps, window=(t0, t1), settings=None)`: 순수 계산이고 같은 입력이면 같은 결과다. 모든 시각은 가장 가까운 영상 프레임이다.
한도는 `style`에서 온다(§4.7): 한 줄 16자, 2줄, 초당 14자(어린이 11자), 5/6~7초, 칸 사이 2프레임(0.5초 미만 빈틈은 2프레임으로 붙임, TG 5).
입력 줄은 `state.Project.placement_inputs`가 만든다(넣은 줄만, `name`은 보일 표시 글 `label_name`, 작가가 시각을 고정한 줄은 추정이 아님).

대사 칸:
- 대본 줄 하나에 칸 하나. 앞머리는 `[이름/출처·말투/언어] `(`show_name`일 때 이름, `label_extra`, 언어는 맨 뒤). 노래 줄은 칸마다 `♪ … ♪`.
- 2×16자에 안 들거나 말이 7초보다 길면 어절 경계에서 적고 고르게 나눈다(`_split`, 될 수 있으면 문장부호·쉼표 뒤). 이름은 첫 조각에만, 언어 표시와 ♪는 조각마다
  (II.6 "in each event" = 자막마다, II.7 "each subtitle"). 나눈 뒤 줄바꿈을 하고, 그 다음에 표시용 문장부호를 다듬는다(낱말은 그대로).
- 시작 = 첫 낱말. 0.5초 안 앞의 화면 전환으로 당길 수 있다(앞 칸의 말·5/6초를 자르지 않을 때, TG 2). 전환 0.5초 안 앞에서 시작해 전환을 넘는 말은 전환에서,
  9~11프레임(24fps) 앞이면 0.5초 앞에서 시작한다(TG 4). 끝 = 마지막 낱말 + 0.5초, 자리가 있으면 5/6초·초당 14자까지 늘리고, 7초와 다음 칸 2프레임 앞을 넘지 않는다(TG 1, 3).
- 화자가 다른 짧은 두 줄은 앞 줄이 혼자 5/6초를 못 받고, 두 줄 모두 말 + 0.5초가 5/6초가 안 되고, 각각 한 줄에 들 때만 하이픈 칸 하나에 함께 둔다(I.6, TG 6).
- 겹친 줄(`_untangle`): 앞 칸을 읽을 수 없을 만큼 가까이 겹치면 ① 화자가 다른 두 줄이 한 줄씩 들면 하이픈 칸 ② 아니면 시각이 추정인 쪽이 비킨다 ③ 그래도 안 되면 뒤 줄이
  앞 칸 5/6초 뒤까지 기다리고 두 칸에 `overlap`. 한 사람의 두 줄은 표시가 무엇이든 하이픈 칸을 나누지 않는다(`person`).
- 시각 추정 줄의 조각이 5/6초를 못 받으면, 다른 줄 칸 사이 빈틈 안에 조각 순서대로 다시 편다(빈틈이 넉넉하면 조각마다 읽기 시간, 아니면 5/6초).
  그래도 안 들면 칸을 만들지 않고 `no_room`(대사, 사유에 실제 빈틈과 필요한 길이). 추정 줄 때문에 몇 프레임짜리 글을 보이지 않는다(감사 2-m1).
- 장면(TG 4 "Subtitles may not cross scene changes" = 자막은 장면 바뀜을 넘지 않는다): 다른 `scene_id`의 두 줄은 하이픈 칸을 나누지 않고, 장면의 마지막 칸은 다음 장면
  첫 줄과의 사이의 전환을 넘지 않는다. 말이 전환을 넘어가거나, 전환 앞에서 끝내면 5/6초가 안 되면 예외이고 `check.notes`에 적는다. 칸이 시작하는 프레임의 전환은 한도가 아니다.
- 작가의 `breaks`는 자동 끊을 자리를 바꾼다(안 들어가는 조각은 더 나누고 `check.notes`에 적음). `keep`은 소리가 그 칸을 나누지 못하게 한다(한 칸에 안 들면 무시하고 적음).
- 화자 표시만 첫 줄에 홀로 남는 줄바꿈(`[가람]` / 대사…)은 16자가 허락하는 한 첫 낱말을 표시 옆에 붙여 다시 바꾼다(첫 낱말 끝의 `.` `,`는 견줄 때 뗀다).

소리 칸(10-13 메모 5.2):
- 효과음: 시작 = 소리 첫 프레임, 전환이 가까워도 앞당기지 않는다. 길이 = 글자 ÷ 14 + 1초(`label_extra`), 5/6초~7초.
- 음악: 시작 = 음악 첫 프레임, 0.5초 안 앞에 전환이 있으면 전환. 길이 3초(`music_dur`), 음악이 먼저 끝나면 음악 끝 + 0.5초, 적어도 5/6초.
- `move_to`(작가가 옮김)가 시작을 바꾼다. 시작이 추정인 소리(대본 단서 후보)의 칸은 `timing_estimated`.

자리(소리를 시간 순서대로, 메모 5.3):
1. 그 시각에 화면에 칸이 없고 뒤로 5/6초가 비면 따로 한 칸(앞 칸은 말 끝·5/6초·초당 14자까지 줄일 수 있음).
2. 한 줄짜리 칸 시작과 0.5초 안, 출처가 다름 → 같은 칸에 하이픈 두 줄, 들린 순서대로(II.5).
3. 같은 자리, 말하는 사람이 낸 소리(`source_line`이 그 줄) → 하이픈 없이 `[지수] [재채기] 나도야!` 한 줄(안 들면 두 줄).
4. 대사 칸 시작보다 0.5초 넘게 뒤, 말이 이어지는 중 → 소리에서 0.5초 안의 어절 경계로 칸을 나누고 뒤 칸에 붙임(두 칸 모두 5/6초 이상일 때만).
5. 한도를 넘으면 차례로: 짧은 문구 → 칸 끝을 뒤 빈틈으로 늘림 → 소리 뒤 0.5초 안에 시작하는 빈틈의 따로 칸 → `no_room`(사유 예: '소리 뒤 빈틈 0.42초').

끝 조정(TG 1, 2, 4, 5, 모든 칸 시간 순서대로): ① 다음 칸과 0.5초 미만 틈은 2프레임으로 붙임 ② 전환 0.5초 안 앞에서 끝나면 전환 2프레임 앞으로 옮김(다음 칸이 전환 뒤일 때)
③ 소리가 전환 전에 끝났는데 칸이 전환을 넘으면, 5/6초와 초당 14자가 지켜질 때 전환 2프레임 앞에서 끝냄. 전환을 넘는 대사 칸은 자리가 있으면 전환 뒤 0.5초 이상.
③ 뒤에 남은 3프레임~0.5초 틈은 다시 붙이거나(7초가 막으면 0.5초로 넓힘), 그 안에 전환이 있으면 그대로 둔다. 그 밖은 '빈틈 0.5초 미만'으로 알린다.

원칙: 대사 글은 바꾸거나 줄이지 않는다(줄바꿈과 표시용 문장부호만). 소리를 저절로 빼지 않는다(칸이 되거나 `no_room`). 대사 칸 하나에 소리 하나만,
이미 두 줄인 대사 칸에는 합치지 않는다. 몇 프레임짜리 칸(`flash_dur`보다 짧음)은 `flash`로 표시한다. 예전 작업 폴더의 `♬ 무서운 음악`은 `[무서운 음악]`으로 쓴다.

## 7. 검수 서버와 화면 (`cboe/server/app.py`, `cboe/server/static/`)

- 한 쪽짜리 정적 페이지(`index.html`, `app.js`, `app.css`). 작가가 고칠 때마다 서버가 `Project.set_*`로 `review.json`에 쓰고, `settle`(§4.5)로 다시 배치하고,
  화면 상태 전체를 돌려준다. 목록·타임라인·영상 위 칸이 늘 같은 draft 하나에서 그려진다. 서버는 LLM을 부르지 않는다.
- 서버는 이 PC의 요청만 받는다(`LocalOnly`: Host가 localhost, 바꾸는 요청은 다른 사이트 Origin이면 거절). 모르는 id는 404, 쓸 수 없는 값은 400(한국어 문구), 작업 폴더와 부딪히면 409.
- 영상과 정지 화면은 처리 구간 [t0, t1) 안에서만: 페이지는 그 밖으로 가지 않고 `/api/frame`은 구간 안으로 묶는다(시험 영상의 봉인 구간을 보이지 않으려고).

`GET /api/state`의 칸: `version`, `project`(`title` `fps` `t0` `t1` `duration` `width` `height` `video` `video_url` `video_name` `sample` `work` `profile` `envelope`, 재생용 영상의 `media`),
`dialogue`(예전 화면용), `lines`(합친 줄, 영화 순서, 빠진 까닭·칸), `line_codes`, `extras`, `scenes`, `people`, `added`, `items`(줄이 빠진 화자 후보는 뺌, `cells`·`no_room`·`swap_prev`),
`draft`, `cuts`, `characters`, `style`(`Style.public()` + `formats`), `summary`(후보 집계, `guide`, `flags`, `lines`, `extras`, `people`), `export_check`, `error`.

| 방법 · 주소 | 받는 것 | 하는 일 |
|---|---|---|
| `POST /api/items/{id}` | `decision` `phrase` `short` `name` `scope` | 후보 결정, 화자 이름(범위는 §4.6) |
| `POST /api/items/bulk` | `ids` `decision` | 여러 후보 한꺼번에(제외 추천 묶어서 확정) |
| `POST /api/placement/{id}` | `action`(`exclude` \| `move` + `t` \| `swap_prev` \| `clear`) | 배치 확인 필요 처리. 옮기거나 바꾸면 포함으로 정한다 |
| `POST /api/lines/{id}` | `include` `text` `text_from` `name` `language` `sung` `start` `end` `breaks` `keep` `show_name` | 대사 줄 결정. null로 보낸 칸은 기본으로 |
| `POST /api/lines/bulk` | `ids` `include` | 여러 줄 넣기·빼기 |
| `POST /api/lines/{id}/speaker` | `name` `scope` | 화자 이름을 범위대로 |
| `POST /api/extras/{id}` | `action`(`line` \| `ignore` \| `clear`) + 줄로 넣을 때 `text` `name` `language` `sung` `start` `end` | 대본에 없는 말 다루기 |
| `POST /api/added` · `POST /api/added/{id}` · `DELETE /api/added/{id}` | `kind` `start` `end` `text`·`phrase` … | 작가가 넣는 줄·효과음·음악 |
| `POST /api/people` | `names` + `mode` `before` `reveal` `reveal_line` | 인물 표시(여럿 한꺼번에) |
| `POST /api/export` · `GET /api/export/{fmt}` | `format` | 내보내기와 그 파일의 검사 결과, 내려받기 |
| `GET /api/frame?t=&w=` | | 정지 화면 JPEG(원본에서, 구간 안으로 묶음) |
| `GET /api/envelope?step=` | | `env.npz`를 `step`초마다 가장 큰 값으로 줄인 트랙별 세기(정수 dB, 칸 수 `ENV_MAX_BUCKETS` 20000 이하, 바닥 `ENV_FLOOR_DB` -90). 파일이 없으면 404 |
| `GET /media/video` · `GET /api/media` · `POST /api/media/build` | | 영상과 재생용 영상(README '재생용 영상') |

화면은 보기 다섯 개(후보 · 대사 · 대본에 없는 말 · 인물 · 칸)와 타임라인이다(README '검수 화면'). 수치·표기는 상태의 `style`에서만 받는다.
200행씩 그리고(`CHUNK`) 내려가면 더 그린다.

### 7.1 구현하며 정한 것 (2026-10-19, 지금도 맞는 것)

- 받아쓰기: faster-whisper의 VAD 이어붙이기는 무음 뒤 첫 낱말 시각을 앞 구간 끝에 붙인다. Silero VAD를 직접 돌려 `MAX_CHUNK_S`(25초) 이하 묶음마다 따로 받아쓴다.
  대사에 나오는 인물 이름만 힌트로 주고(`hotword_names`), 힌트 이름만 되풀이한 묶음은 `words`에서 빼서 `hint_echo`에 둔다.
- 효과음: 창 대신 소리 시작 중심으로 찾는다(종류별 점수 상승 + AudioSet 'Sound effect' + 효과음 트랙 세기 급상승). 'Mechanisms' 같은 포괄 종류(`NO_TRIGGER`)는
  혼자서는 후보를 만들지 않는다. 종류를 못 정한 큰 소리는 '[소리]'(목록 밖 소리)로 에이전트에 넘긴다.
- 계층: PretrainedSED 이름과 ontology.json 이름이 다른 것은 AudioSet mid로 잇고, ontology 뒤에 생긴 종류는 가까운 부모를 손으로 붙였다(`ontology.EXTRA_PARENTS`).
- 음악: AST 분위기 점수는 절대값이 작아 7종 안의 비율로 읽고 절대 합은 `strength`로 둔다. 긴 음악 구간 안에서 음악 트랙 세기가 `CUE_DB` 이상 오르면 '새 음악' 후보.
- '앞 자막에서 이미 알림'에는 '같은 장면' 조건이 없다(같은 종류와 시간만 본다).

## 8. GUI (2026-10-20)

명령줄 없이 처음부터 끝까지 쓰는 데스크톱 창. `cboe-gui.cmd` 또는 바탕화면 `CBoE` 바로가기 → `pythonw -m cboe gui`.

- 창: pywebview(WebView2)가 localhost 의 스튜디오 서버(`cboe/server/studio_app.py`)를 띄운다. `--browser`면 같은 화면을 기본 브라우저로.
- 시작 화면(`static/home.*`): 작업 목록(썸네일·상태·작가 결정 수·내보냄), 고른 작업의 정보와 분석 과정(①②③ 묶음으로 16단계 = `pipeline.STAGES` + 초안 배치,
  이름은 `studio.STAGE_LABELS`), 새 작업(영상·대본 고르기, 대본 미리 보기 = `script.summary`, 제목, 처리 구간, LLM·에이전트 한도·동시 호출·모델), 분석(이어서 / 처음부터 다시),
  멈추기, 지우기. 받는 대본 확장자는 `script.SUFFIXES` 하나에서 만든다(검사·안내 글·파일 고르기 창). 프로필(어린이)과 `--llm-scope`는 시작 화면에 없다.
- 단계가 끝났는지는 단계마다 마지막에 쓰는 파일(`studio.KEY_OUTPUT`)로 본다. 대본 단서 단계가 생기기 전에 분석한 작업은 `items.json`이 있으면 그 단계를 끝난 것으로 본다.
- 작업 공간: 기본 `cboe\work`(또는 `CBOE_WORKSPACE`, `--workspace`). 작업 하나 = 작업 폴더 하나(이름은 제목에서 만든 ASCII).
  지우기는 `work\.trash` 로 옮길 뿐이다.
- 분석: `JobManager`(`cboe/studio.py`)가 `python -m cboe run <작업>`을 창 없이 하나씩 돌린다(GPU 하나). 로그의
  `== stage x`, `== stage x: {요약}`, `PROGRESS x 3/12 메모` 줄을 읽어 단계·진행률을 보이고, 로그는 `<작업>\gui_run.log`,
  결과는 `<작업>\gui_job.json`. 멈추기·창 닫기는 프로세스 나무째 끝낸다. 끝난 단계는 파일로 남으므로 다음에 이어서 한다
  (소리 검출 결과도 임시 파일을 거쳐 써서 멈춘 자리에 반쪽 파일이 남지 않고, 화면·대사 근거는 묶음마다 저장해 멈춘 뒤에는 남은 후보만 다시 묻는다).
- 지키는 것: 분석 프로세스는 Windows 작업 개체(닫히면 모두 끝냄)에 넣어 GUI가 죽거나 강제 종료돼도 혼자 남지 않는다.
  `run`은 작업 폴더의 `.run.lock`을 잡아 다른 창·명령줄과 같은 폴더를 동시에 돌리지 않는다(시작 화면은 '다른 곳에서 분석 중'으로 표시).
  서버는 이 PC의 요청만 받는다(Host 가 localhost, 바꾸는 요청은 다른 사이트 Origin 이면 거절). 작업 id 는 폴더 이름 그대로만 받고 화면에 넣을 때 이스케이프한다.
  '폴더 열기'는 작업 공간 안 폴더만 연다.
- 검수 화면은 작업마다 `/p/<작업>/` 아래에서 그대로 돈다. 페이지가 `<base href>`와 상대 주소를 써서 단독 `serve`와 GUI 양쪽에서 같은 파일이다.
  위쪽 '작업 목록'으로 돌아가고, 내보내기 창에 '폴더 열기'가 있다.
- 연구 규칙(`cboe/guard.py`): 시험 영상(파일 이름의 YouTube id, 이름을 바꾼 사본은 영상 길이로 알아봄)은 개발 회차 끝까지만 작업을 만들 수 있고, 볼링블링은 아예 안 된다.
  새 작업 창은 그 영상을 고르면 끝 시각을 개발 회차 끝으로 채운다. `init`·`run` 모두 같은 검사를 한다(`CBOE_RESEARCH_GUARD=0`으로 끔).

## 9. 환경 변수

| 변수 | 읽는 곳 | 뜻 |
|---|---|---|
| `CBOE_DEVICE` | `util.device` | 모델 장치(`cuda` · `xpu` · `cpu`). 없으면 CUDA → XPU → CPU 가운데 되는 것 |
| `CBOE_ASR_BACKEND` | `stages/asr.load_model` | `faster-whisper` \| `openvino`. 없으면 cuda·cpu는 faster-whisper, 인텔 GPU는 openvino |
| `CBOE_WHISPER_MODEL` | `paths` | faster-whisper 모델(기본 `large-v3`) |
| `CBOE_WHISPER_OV_MODEL` `CBOE_WHISPER_OV_REVISION` | `paths` | OpenVINO Whisper 저장소와 고정한 판 |
| `CBOE_AST_MODEL` | `paths` | 음악 분위기·악기 모델(기본 `MIT/ast-finetuned-audioset-10-10-0.4593`) |
| `CBOE_BANDIT_CODE` `CBOE_BANDIT_CKPT` `CBOE_PSED_CODE` `CBOE_PSED_CKPT` `CBOE_ONTOLOGY` `CBOE_TNV2_CODE` `CBOE_TNV2_WEIGHTS` `CBOE_YUNET` `CBOE_LRASD_CODE` `CBOE_LRASD_WEIGHTS` `CBOE_QWEN_OMNI` `CBOE_NETFLIX` | `paths._p` | 외부 코드·가중치 경로. 없으면 저장소 위 `demo` 폴더 → `third_party/`·`weights/` 가운데 있는 것 |
| `CBOE_WORKSPACE` | `studio.default_workspace` | GUI 작업 공간(기본 저장소의 `work`) |
| `CBOE_RESEARCH_GUARD` | `guard.limit` | `0`이면 연구 규칙 검사를 끈다(다른 사람의 사본용) |

## 10. 일부러 하지 않은 것

2026-11-10 지침 감사의 검증에서 '그 고칠 방법은 틀리거나 위험하다'로 나온 것이다. 고치는 사람이 다시 넣지 않도록 적는다.

| 하지 않는 것 | 까닭 | 지금 코드 |
|---|---|---|
| 못 맞춘 대사 줄을 기본으로 빼기 | 받아쓰기가 놓친 짧은 진짜 대사가 빠진다(감사 1-03) | `unheard` 줄은 기본으로 넣는다(`state.Project._line`) |
| 정렬률이 낮다고 실행을 멈추기 | 비명·짧은 말이 많은 맞는 짝에서 잘못 멈춘다(1-10) | 정렬은 줄 상태만 적고 멈추지 않는다 |
| 정렬 통과 기준을 줄 길이에 비례해 올리기 | 맞는 자리를 잃는 줄이 실제로 있었다(1-10) | 글자의 절반 이상이 기준점이면 맞춘 줄 |
| 끊긴 조각에 늘 '…' 붙이기 | 말이 실제로 끊겼을 때만 쓴다(1-11) | 칸 나누기는 글자를 더하지 않는다 |
| 표시용 문장부호를 칸 나누기 앞에서 다듬기 | (7-04) | 나누고 줄을 바꾼 뒤 `style.tidy_line` |
| 칸 id를 덮어쓰기 열쇠로 쓰기 | 칸 id는 배치마다 새로 매기는 차례 번호다(2-02) | 결정은 후보·줄 id와 지문(§3.6) |
| 대조 표시를 받아쓰기 잡음째로 올리기 | (1-01) | `minor`·`number`·띄어쓰기는 `differs`가 아니다 |
| '대본에 없는 말'을 저절로 넣기 | (1-04) | `extras`는 작가가 넣을 때까지 `open` |
| 대본 오타를 저절로 바꾸기 | (1-m3) | 맞춤법 차이는 `hints: ["spelling"]` 표시만 |
| 작가가 후보를 열 때 LLM에 묻기 | 검수 중 몇 초씩 기다리게 된다(6-06) | 검수 서버는 LLM을 부르지 않는다. LLM은 분석 단계에서만 |
| 대본 단서만으로 포함 추천 | 쓰인 소리가 영화에 있는지 잰 적이 없다(4-01 검증) | 단서 후보는 늘 확인 필요(§4.3) |

## 11. 시험 데이터

`C:\projects\barrier_free\data\sdh3`의 가즈아 도서관(RIt0WP6hHQE)·로코양(PgaKWtFNI6k) 사람 SDH 트랙.
연구 규칙대로 개발 회차(가즈아 0~1579.7초, 로코양 0~1127초)만 처리·채점한다. 봉인 회차와 볼링블링은 쓰지 않는다(`cboe/guard.py`).
이 웹드라마에는 촬영 대본이 없으므로 시험 대본은 사람 SDH의 대사 줄에서 만든다(`tools/make_test_script.py`, 효과음·음악 줄과 괄호 표시를 지우고 `(이름)`이 있는 줄만 화자 이름을 남김). 대본 화자가 없는 줄은 화자 이름 표시가 '확인 필요'로 간다.
진짜 영화 대본의 글은 저장소(시험 자료·주석·문서·커밋 글)에 넣지 않는다. 시험의 대본·대사·인물 이름은 지어낸 것으로 쓴다.
