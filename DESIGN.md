# CBoE 구현 설계 (Captioning Based on Evidence)

고치는 사람을 위한 문서다. 처음 설계는 발표 덱 `Desktop\Study\아이디어 발표\CBoE(5조).pptx`(2026-10-15판) 05 시스템 설계(5.1~5.10)와
03 UI 활용 예시(3.1~3.5)이고, 배치 수치는 `시스템 구성 확정 2026-10-13.md`(이하 10-13 메모) 5절에서 왔다. 지금 자막의 기준은 넷플릭스
한국어 Timed Text 지침 II장 SDH(`II.n`, II장에 규칙이 없으면 I장 `I.n`), 자막 타이밍 지침(`TG n`), 일반 요건(`GR n`)이다.
덱·메모와 지침이 다르면 지침을 따른다. 코드 주석의 '감사 N-NN'은 2026-11-10 지침 감사의 지적 번호다(노트는 저장소 밖).
음원 분리(세 트랙)에 기대는 것은 §12에 모았다. 다른 절은 분리 트랙을 이름으로만 부른다.

## 0. 범위와 원칙

- 입력: 영상 1개 + 촬영 대본(USAGE.md '받는 대본 형식'). 대본 없이도 돈다(받아쓰기 줄을 대사로 씀, 화자 이름 없음).
- 후보(`kind`) 세 가지: 효과음 `sfx`, 음악 `music`, 화자 이름 표시 `speaker`. id 머리글자는 S / M / P, 대본 단서로 만든 후보는 단서 id(`C0001`)를 그대로 쓴다.
- 추천(`rec`) 세 가지: 포함 추천 `include`, 제외 추천 `exclude`, 확인 필요 `check`. 배치가 안 되면 배치 확인 필요(`no_room`).
- 지키는 것:
  1. 후보는 추천이 무엇이든 지우지 않는다. 마지막 결정은 작가(검수 화면)가 한다.
  2. 대사의 낱말은 저절로 바꾸지 않는다. 정렬은 대조를 표시만 하고(`differs`, `hints`), 배치는 칸·줄을 나누고 표시용 문장부호만
     다듬는다(`style.tidy_line`). 글은 작가가 고를 때만 바뀐다.
  3. '대본에 없는 말'(받아쓰기에만 있는 말, `extras`)은 저절로 넣지 않는다.
  4. 대본 단서만으로는 아무것도 포함 추천하지 않는다(쓰인 소리가 완성된 영화에 있는지 잰 적이 없다).
  5. 시각이 추정이면 그 표시가 칸까지 따라간다(줄·후보의 `estimated` → 칸 `flags`의 `timing_estimated`). 작가가 묶은 칸도 두 줄 가운데 하나만 추정이면 표시가 남는다(§6).
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
| separate | `stages/separate.py` (§12) | `audio/{speech,music,effects}_{48k,16k}.wav` | sed, asr |
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
  후보 id가 차례 번호라서, 앞 결과가 바뀌면 옛 id로 묶인 뒤 결과를 남기면 안 된다. 없는 단계 이름은 `__main__`이 아무것도 지우기 전에
  한 줄 오류(끝 코드 2)로 끝낸다(`placement`는 단계가 아니라 실행 끝에 늘 돈다는 말을 덧붙임).
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
  audio/{speech,music,effects}_{48k,16k}.wav        분리 트랙 (§12. 도는 동안 *.f32.part · separate.part.json · *.part.wav)
  script.json         대본 문서 (§3.1)
  cuts.json           {"fps", "cuts": [t], "shots": [[s, e]], "model"}
  sed_effects.npz sed_mix.npz sed_music.npz          probs[n,447] float16, fps=25, t0 (임시 파일을 거쳐 씀: util.save_npz)
  env.npz             분리 트랙 소리 세기(100Hz, dB, §12). 검수 화면의 소리 세기 곡선(/api/envelope)도 이것
  asr.json            {"language", "words": [{"w","s","e","p"}], "vad", "chunks": [{"start","end","language","detected","prob"?,"words"}],
                       "hint_echo", "hotwords"}   chunks.detected: 그 구간 언어를 소리에서 알아냈나(--asr-detect-language)
  dialogue.json       대사 줄과 대본에 없는 말 (§3.2)
  scenes.json         장면 자리 (§3.3)
  cues.json           대본 단서 (§3.4)
  sfx.json            효과음 후보 (추천 전)          music.json   음악 구간·후보·멈춤
  speaker.json        줄마다 말하는 얼굴 근거 (멈춘 실행은 speaker.part.json 에서 이어감)
  screen.json         효과음 후보마다 화면 근거(답마다 prompt_version, 없으면 질문 1)    dialog_ev.json  효과음 후보마다 대사 근거
                      (둘 다 묶음마다 "complete": false 로 저장하고 끝나면 true: 멈춘 뒤에는 남은 후보만 묻는다)
  items.json          추천까지 끝난 후보 전체 (§3.5, 검수 화면의 바탕)
  agent/<후보>.json   에이전트 기록(질문, 도구 호출과 돌려받은 것, 답)   agent_results.json  agent_done.json
  review.json         작가 결정 (§3.6, 검수 화면이 씀)
  draft.json          배치 결과 (§3.7, 작가 결정이 바뀔 때마다 다시 만듦)
  export/<제목>.srt .vtt .ttml
  run_log.json  run_history.json  llm_log.jsonl  .run.lock     실행 기록, LLM 호출 기록, 실행 잠금
  gui_run.log  gui_job.json                                    GUI 로 돌린 분석의 로그와 결과
  review.mp4  review.mp4.json  review.mp4.lock  review.mp4.<pid>.part   재생용 영상 (§7.2)
```

단계마다 결과 파일이 있으면 건너뛴다(`--force <단계>`로 다시). 예외는 §1의 `script`(파일을 견줌)·`cues`·`agent`.

## 3. 데이터 형식

예시의 이름·대사는 지어낸 것이다(USAGE.md '받는 대본 형식'의 대본 토막).

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
- 자막 대본(`format: "timed"`, `script._read_subtitle`): 줄마다 `start`·`end`(칸의 시각). 한 칸에 화자가 둘 이상이면 그 줄들에 `turn` `"<칸>-<무리>"`(1부터).
  하이픈 줄이나, 하이픈 없이 다른 화자 이름을 적은 줄(`[가람] 같이 가.`. 그 이름이 파일의 다른 곳에서 칸 첫 줄이나 하이픈 줄로 말을 시작할 때만:
  `_labelled_names(first_rows=True)`, `_row_name`)이 새 무리를 연다. 그런 이름이 아니면 말투(`delivery`)로 남는다. 이름 없는 하이픈 두 줄은 speaker null + `turn`.
  쌍점 꼴 줄(`나래: 응.`)도 같은 조건으로 새 무리를 연다: 칸 `가람: 가자.` / `나래: 응.`은 나래가 파일의 다른 곳에서 말을 시작하면 두 줄(`1-1`, `1-2`)이고,
  그 칸에만 나오는 이름이면 줄 하나(화자 `나래`, 글 `가자. 응.`)다. 이 읽기는 자막 대본(`.srt` `.vtt`)에만 있다.
- 자막 대본의 선택 칸 `wordless`: 대사 없이 화자 소리만 있는 무리(`[나래] [웃음]`, 두 화자 칸의 한 화자가 웃기만 함).
  `[{"id": "W0001", "speaker", "vocal": [...], "start", "end", "after_line": 파일에서 그 무리 바로 앞의 줄 id 또는 null, "raw", "turn"?}]`.
  같은 칸에 그 화자의 대사가 있으면 그 줄의 `vocal`로 가고 여기 오지 않는다. 줄도, `people`·`characters`도 되지 않는다(말 없는 대사와 같음).
  이름 없는 소리만의 칸(`[웃음]`)은 남지 않는다. 판(`VERSION`)은 그대로라 예전에 읽은 자막 대본은 `--force script`로 다시 읽어야 이 칸들이 생긴다.
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
- `diff[].name: true`(`kind`는 `word` 그대로): 대본이 인물 이름을 말하는 자리만 다르다. 다른 글자가 모두 이름을 말하는 낱말 안에 있고 이름 자체에 닿으며,
  그 자리에서 다른 인물 이름이 아닌 말이 들렸고, 들린 글자가 대본 글자와 소리로 닮았을 때(자모 소리 비교: 예사·된·거센소리, ㅐ/ㅔ를 같게, `NAME_SOUND_MIN` 0.6)만이다.
  엄마·선생님·담임 같은 흔한 가족·직함·역할 낱말로 된 화자 이름(`ROLE_WORDS`)은 오인식할 이름으로 보지 않는다(II.1 "follow the word choice … Slang and other
  dialectal features should not be changed" = 실제 낱말 선택을 따르고 속어·사투리는 바꾸지 않는다). 낱말 그대로 `ROLE_WORDS`에 있으면 끝의 '들'을 떼지 않는다
  (`아들`, `_role_word`). 다만 '그 자리에서 다른 인물 이름이 들렸나'를 볼 때는 이런 역할 낱말 이름도 이름으로 센다(`_name_keys(role_words=True)`:
  인물이 `오빈`과 `오빠`이면 `오빈아`가 `오빠`로 들린 줄은 다른 이름이 들린 것). 그 밖(조사만 다름, 이름이 안 들림, 다른 이름이 들림)은 `differs`.
  `name` 토막만 다른 줄은 `ok`이고 `hints`에 `"name"`.
- `hints`: `"spelling"` | `"name"` | `"language"`. `"language"`는 그 줄 구간의 받아쓰기 언어(`asr.trusted_language`: 소리에서 알아낸 구간, 확률 `LANG_MIN_PROB` 이상)가
  대본 표시와 다를 때이고, 그때만 `heard_language`(`asr.LANG_KO`의 한국어 이름 '영어', 표에 없는 코드는 그대로)가 붙는다. 표시 없는 줄은 들린 말이 한국어가 아닐 때만.
  힌트는 글도 상태도 바꾸지 않는다.
- `extras[]`: 어느 대본 줄도 쓰지 않은 받아쓰기 낱말 묶음(대본에 없는 말, `EXTRA_GAP` 0.8초 쉼으로 끊음). `INTERJECTIONS`·감탄뿐인 묶음은 버린다.
  `short`(`EXTRA_SHORT` 2글자 이하), `overlaps`(겹치는 못 들은 줄 id), `suspect`(`credit` | `greeting` | `repeat`: 받아쓰기 헛소리 꼴이면서 목소리가 없음,
  근거 점수는 `suspect_by`), `sung`(낱말의 `SUNG_SHARE` 0.5 이상이 노래 구간 안). 의심 표시가 있어도 지우지 않는다.
- 맨 위 `singing: [[s, e], …]`(있을 때만): 노래 구간. `sed_mix.npz`의 노래 계열 10종(Singing·Choir·Rapping … , Humming 제외) 점수를 음악 구간과 같은 꼴로 묶는다
  (`SING_ON` 0.30 / `SING_OFF` 0.15, 틈 `SING_GAP_S` 2.5초, 최소 `SING_MIN_S` 2.0초. 노래로 재지 않은 값). 대본 없는 실행의 받아쓰기 줄에는 `sung`을 달지 않는다.

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
           "window": [3.0, 10.2], "window_by": ["range", "line"], "window_out": [false, false], "window_lines": [null, "L0001"],
           "window_estimated": false, "guess": 5.4}]}
```
- `type`: `sfx`(지문의 소리 낱말·의성어) | `music`(음악·노래, 가수와 곡명이 있으면 `songs`, 화면 속 매체면 `diegetic`·`source`, 이어지는 음악이면 `continues`)
  | `silence`(침묵·정적, 음악이 멈추면 `music_stop`) | `vocal`(대사의 `(한숨)` 같은 말하는 사람의 소리, 그 줄이 `line_id`).
- 자막 대본의 `wordless`(§3.1)마다 `vocal` 단서: `wordless_id`, `at` [시작, 끝](그 칸의 시각), `after_line` = wordless의 것, `line_id`·`element_id`·`before_line` null.
  파일 차례대로 `after_line` 단서 바로 뒤에 번호가 붙어, 그런 칸이 있는 자막을 다시 읽으면 뒤 단서 번호가 밀린다(결정은 지문으로 맞춤, §3.6).
  `counts.elements`는 `element_id` → `line_id` → `wordless_id` 차례로 센다.
- `window_out` [앞 끝, 뒤 끝]: 그 끝이 대본의 다른 장면 너머에 있나(§4.3). 창이 없으면 null.
- 문구 후보(`phrases`)는 II.9·II.7 꼴만: 적힌 의성어 먼저(`[쾅! 쾅!]`), `[의성어 - 출처]`, 설명(`[총성]`), 곡 `[가수의 '곡명']`·`[가수의 '곡명'이 흐른다]`,
  `[침묵]`·`[정적]`, `[음악이 멈춘다]`. '…소리'뿐인 설명은 고를 거리일 뿐 문구가 되지 않고, 그것만 있는 단서는 `vague`(문구 없음).
- 낱말 목록(`SOUND_WORD`, `ONO_STRONG`, `ONO_WEAK`, `SOURCES`, `MUSIC_WORD`, `SILENCE`, `VOCAL_PHRASES`, 빼는 것 `NOT_SOUND`)은 일반 한국어 소리 낱말이다.
- 문 소리 읽기(`SOURCES`의 문 줄, `_door_match`, `_sound_name`): 문(현관문·대문·창문·철문·문) 앞뒤의 의성어와 동사(열·닫·여닫·연다)로 문의 소리를 읽는다. 받는 꼴(지어낸 예):
  `문이 끼익 열린다` · `끼익 소리를 내며 문이 열린다`(의성어가 문의 소리일 때만) · `문이 천천히 끼익 열린다` · `문이 끼익 천천히 열린다`(표의 의성어 하나 + 낱말 하나) ·
  `문이 끼익거리는 소리를 내며 열린다`(꼬리 -거리는·-대는·-하는·-이는) · `문이 끽 소리를 내며 열린다`(표에 없는 의성어 꼴은 적힌 대로 `[끽 - 문]`) ·
  `문이 바람 소리와 함께 쾅 닫힌다` · `바람 소리와 함께 문이 쾅 닫힌다`(문과 동사 사이의 의성어는 문의 소리: 둘 다 `[쾅 - 문]`, 바람 가족은 남김. 총·비·사이렌도 같다).
  `사이렌 소리와 함께 문이 열린다`처럼 문의 의성어 없이 다른 출처의 소리면 그 출처가 이름을 정하고 문 가족(door·impact)도
  함께 센다. 이름을 정할 출처가 없으면(`문이 큰 소리를 내며 닫힌다`) 문소리를 지어내지 않고 `…소리` 설명만 고를 거리로 남는다(`vague`).
  문이 아닌 `…문` 낱말 14개(`NOT_DOORS`: 주문·질문·논문·신문 …)는 문으로 읽지 않는다. 그 낱말로 끝나는 문 `격자문`·`내부문`·`외부문`(`DOORS_LIKE_NOT_DOORS`)은
  문이다(`격자문이 쾅 닫힌다` → `[쾅 - 문]`). 소문·전문·원문·서문은 파출소문·회전문 같은 문 낱말과 끝 두 글자가 같아 막지 않았다.
- 동사 '열'(`_DOOR_VERB`)은 뒤에 '열다'의 어미가 올 때만 받는다: 고·어·었·며·면·자·지·려·리·린·릴·렸·림·더·던·게·겠·곤·긴·까·래·든·죠·잖, 다(`열다섯` 빼고),
  라(띄어 쓴 속어 `열라` 빼고), 거든·거나(`열거` 빼고), 도록·수록, 기(`열기구` 빼고), 띄어 쓴 `열 때`·`열 수`(뒤에 명·살·개·마리가 오면 빼고), 문장 부호
  `.` `,` `!` `?` `…` `~`(`문을 끼익 열!`), 줄 끝. 그래서 `창문 너머 덜컹덜컹 열차가 지나간다`는 `[덜컹덜컹 - 기차]`이고, `열차`·`열쇠`·`열심히`·`열 명`·`열다섯`은 문을 열지 않는다.
- 아직 못 받는 꼴(지어낸 예, 지금 코드로 재현): `문이 끼익 소리를 내며 천천히 열린다`(→ `[끼익]`·`[급정거]`), `문이 바람 소리와 함께 닫힌다. 쾅!`(→ `[쾅! - 바람]`),
  `소문을 듣고 연다. 쾅!`(→ `[쾅! - 문]`), `문이 천천히 끽 열린다`(두 낱말 꼴은 표의 의성어만 받아 단서 없음), `문이 덜커덩거리는 소리를 내며 닫힌다`
  (→ `[문이 덜커덩거리는 소리]`, 표에 없고 의성어 꼴로도 안 읽힘), 문 동사 뒤의 의성어 `문을 열자 끼익 소리가 난다`·`문이 열리며 끼익 소리가 난다`(→ `[끼익]`·`[급정거]`.
  어떤 의성어를 문의 소리로 볼지, 총일 수도 있는 탕·쾅을 어떻게 가를지 정해야 한다), ㄹ이 빠진 꼴 `문을 끼익 여니`·`여는`·`엽니다`·`여세요`와 비표준 `열니`
  (→ `[끼익]`·`[급정거]`), 문 + 의성어 뒤의 `열기`(더운 기운)·`열 대`·`열 번`·`열 시`(→ 문 동사로 읽음: `문 앞에서 쿵 열기가 훅 들어온다` → `[쿵 - 문]`.
  대·번·시는 `열 대신`·`열 시간`처럼 동사 '열' 뒤에도 오는 말이라 막지 않았다).
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
  넓은 창에서 단서 장면과 먼 소리면 `cue_far: true`(`cue_reason` false, 확인 필요 사유 '대본 단서의 장면에서 먼 소리', §4.3),
  이름 없는 소리가 대본 낱말로 이름을 받으면 `named_by: "script"`, 말하는 사람이 낸 소리면 `source_line`(그 줄 id), 세기만으로 잡히고 근거가 없으면 `minor: true`(화면이 접음).
- '대본' 근거(`kind: "대본"`)와 대본 단서 후보는 단서의 `window`와 함께 `window_by`·`window_out`을 싣는다(화면의 '시각 범위가 장면 밖까지').
  자막 대본의 `wordless`에서 온 단서의 후보·근거에는 `source_line`이 없다.
- 음악: `music` {`moods`(7종 안의 비율 상위 3), `change`(`고조되는` | `잦아드는` | null), `instruments`, `segment`, `event`(`start` | `cue` | `mood_change` | `stop` | `script`),
  `strength`, 멈춤이면 `drop_db`·`next_music_s`}, 단서가 붙으면 `cue_id`·`cue_ids`.
- 화자: id는 `P` + 줄 번호. `speaker` {`line_id`, `name`, `face`(`visible` | `not_visible` | `unclear`), `n_faces`, `asd`, `speaking_frames`, `name_revealed`, `timing`,
  `estimated`, `by`(`script`: 대본이 화면 밖이라 적어 모델 없이 정함), `source`}. `rec` include = 이름 표시, exclude = 이름 생략.
  검수 서버는 상태에 `speaker.label`(배치가 줄 앞에 쓰는 표시 `[하늘/속마음/영어]`)과 `label_tail`(`/속마음/영어`)을 더한다(`show_item`).
- 대본 단서 후보(맞는 검출 후보가 없는 단서, `recommend.script_candidate`): `id`·`cue_id`는 단서 id, `origin: "script"`, `estimated: true`,
  `onset`은 단서의 `guess`, `window`, `flags: ["script", "timing_estimated"]`, `rec`은 늘 `check`. 창이 없거나 `vague`이거나 이어지는 음악인 단서는 후보가 되지 않는다.

### 3.6 작가 결정 (`review.json`)
```json
{"items": {"S0007": {"decision": "include", "phrase": "[빵빵 - 경적]", "pos": "top", "fp": "sfx@8.65", "at": "2026-11-10T22:10:00"},
           "P0002": {"decision": "include", "fp": "speaker@line@가람:곧 온대.#0", "ctx": {"prev": ["line@나래:왜 이렇게 안 와?", ""], "next": ["", ""], "n": 1}, "at": "…"}},
 "placement": {"S0011": {"action": "move", "t": 33.1, "fp": "sfx@32.40"}},
 "lines": {"L0005": {"text_from": "heard", "text": "택시가 곧 온대", "fp": "line@나래:버스가 곧 온대.#0",
                     "ctx": {"prev": ["line@가람:곧 온대.", "line@나래:왜 이렇게 안 와?"], "next": ["", ""], "n": 1}, "at": "…"},
           "L0002": {"source": "phone", "manner": "속삭이며", "join": true, "fp": "line@가람:곧 온대.#0", "ctx": {"…": "…"}, "at": "…"}},
 "added": {"A0001": {"kind": "music", "start": 40.0, "end": 42.0, "phrase": "[음악이 멈춘다]", "pos": "top", "at": "…"}},
 "extras": {"X0001": {"fp": "extra@20.1:어디 가", "action": "ignore", "at": "…"}},
 "people": {"나래": {"mode": "generic", "before": "여자", "reveal": "auto", "not_same": ["바다"], "at": "…"}}}
```
| 구역 | 열쇠 | 값 |
|---|---|---|
| `items` | 후보 id | `decision`(`include` \| `exclude`, 없으면 추천대로), `phrase`, `short`, `name`, `pos`(`"top"`, 효과음·음악만), `fp`, `ctx`(화자 후보), `at`, `not_on`(붙지 못한 결정) |
| `placement` | 후보 id | `action: "move"`, `t`, `fp` (배치 고치기) |
| `lines` | 줄 id | `include`, `text`, `text_from`(`script` \| `heard` \| `writer`), `name`, `language`, `sung`, `start`, `end`(둘 다 주면 시각 고정), `breaks`(새 칸이 시작할 어절 번호), `keep`(한 칸에 두기), `show_name`(화자 후보가 없는 줄만), `source`(`state.SOURCES` 가운데 하나, `""` = 없음, 칸이 없으면 대본 것), `join`(true: 앞 줄과 한 칸), `pos`(`"top"`), `manner`(말투 글, `MAX_MANNER` 20자, 대괄호 없이), `fp`, `ctx`, `at`, `not_on` |
| `added` | `A0001`… | `kind`(`line` \| `sfx` \| `music`), `start`, `end`, `text`(줄) 또는 `phrase`(소리), `name`, `language`, `sung`, `show_name`, `breaks`, `keep`, `pos`, `extra`·`extra_fp`(대본에 없는 말을 줄로 넣었을 때), `at` |
| `extras` | 대본에 없는 말 id | `fp`, `action: "ignore"`, `at` |
| `people` | 인물 이름 | `mode`(`name` \| `generic`), `before`(일반 표시), `reveal`(`auto` \| `line` \| `never`), `reveal_line`·`reveal_fp`·`reveal_ctx`, `not_same`(같은 사람이 아니라고 한 이름들), `at` |

- 어느 구역이 없어도 된다. 예전 `review.json`은 그대로 열린다. 기본값은 저장하지 않는다(`pos` bottom, `join` false는 null처럼 지움).
- **지문으로 맞춰 읽기**: 후보 id·줄 id는 차례 번호라 앞 단계를 다시 돌리거나 대본을 다시 읽으면 바뀐다. 그래서 결정마다 지문을 함께 두고, 지금 같은 지문인 것에만 붙인다.
  - 후보(`Project.fingerprint`): `kind@onset`(소수 둘째 자리). 지문이 다르면 그 id의 결정은 쓰지 않고 같은 지문의 후보를 찾는다.
    후보가 사라진 결정은 `fp:<지문>` 열쇠로 남았다가 그 후보가 다시 나오면 붙는다(`_normalise`).
  - 화자 후보: `speaker@` + 그 줄의 `line_fp`, 줄의 `ctx`와 함께. 줄 결정과 같은 방법으로 줄을 찾아 그 줄의 화자 후보에 붙는다(`_speaker_decisions`).
    예전 꼴 `speaker@<줄 id>:<글>`은 그대로 읽고(줄 id와 글로만 맞춤) 그 후보를 다음에 쓸 때 새 꼴로 다시 쓴다.
  - 대사 줄(`state.line_fp`, `_pick_line`): `line@화자:글#k`(k = 같은 화자·같은 글 줄 가운데 몇째)와 `ctx`(앞 두 줄·뒤 두 줄의 `line@화자:글`, 대본 끝은 "", 같은 글 줄 수 `n`).
    ① 그때도 지금도 하나뿐인 글이면 그 줄 ② 같은 글 줄이 여럿이면 이웃이 더 맞는 줄(가까운 이웃 2점, 다음 이웃 1점, ""는 근거 아님) ③ 비기면 같은 글 줄 수가 그대로일 때만 `#k`
    ④ 그래도 못 가리면 어느 줄에도 붙이지 않고 `fp:<지문>` 열쇠로 따로 둔다. 같은 글 줄 수가 줄었는데 한쪽 이웃만 맞으면 붙이지 않는다(`_contradicts`).
    같은 점수로 한 줄을 노리는 결정 둘은 둘 다 따로 둔다(`_resolve`: 다시 읽은 뒤의 열쇠 id는 근거가 아님). `ctx`가 없는 예전 결정은 `#k`로 맞춘다.
    인물의 `reveal_line`도 같은 방법(`reveal_fp`, `reveal_ctx`).
  - 대본에 없는 말(`state.extra_fp`): `extra@<시작, 소수 첫째 자리>:<글>`.
- **붙지 못한 결정**(`compose`의 `aside`, §4.5): 따로 둔 줄 결정과 화자 후보의 포함·제외 결정(`fp:speaker@line@…`). 작가가 `set_aside`로 줄에 붙이거나 버린다.
  작가가 후보 줄 하나를 직접 정하면(`set_line`·`set_lines`·`set_aside`·이 줄만 이름 바꾸기, 화자 후보는 `set_decision`) 후보가 그 줄 하나뿐인 결정은 지우고,
  여럿이면 그 줄을 `not_on` [{`fp`, `ctx`}]에 더한다. `not_on`이 있는 결정은 대본을 다시 읽어도 스스로 줄에 붙지 않는다(새 결정 위로 되돌아오지 않게).
- 글 길이 상한: 대사 글 `MAX_TEXT`(1000자), 문구 `MAX_PHRASE`(200자), 말투 `MAX_MANNER`(20자), 이름·언어·일반 표시는 한글 15자. 넘으면 `ReviewError`(서버는 400).

### 3.7 배치 결과 (`draft.json`, `placement.place()` 출력)
```json
{"cells": [{"id": "C0042", "start": 12.387, "end": 14.12, "lines": ["- [나래] 버스가 곧 온대", "- [빵빵]"],
            "refs": ["L0005", "S0007"], "kind": "mixed", "pos": "bottom", "flags": [],
            "check": {"chars": [11.5, 4.0], "n_lines": 2, "cps": 8.94, "dur": 1.733, "gap_next": 0.083, "violations": []}}],
 "no_room": [{"item": "S0011", "t": 32.4, "text": "[쿵]", "reason": "소리 뒤 빈틈 0.41초 · 앞 대사 칸을 0.42초 줄이면 들어감",
              "hint": {"cell": "C0041", "line": "L0004", "by": 0.42, "how": "end_earlier"}}],
 "summary": {"cells": 120, "violations": 0, "no_room": 1, "dialogue_cells": 101, "sounds_placed": 19,
             "timing_estimated": 3, "overlap": 0, "flash": 0, "lines_no_room": 0},
 "unplaced": [], "fps": 23.976, "profile": "adult", "notation": 2}
```
- 칸 id(`C0001`…)는 배치마다 새로 매기는 차례 번호다(대본 단서 id와 머리글자만 같다).
- `kind`: `dialogue` | `sfx` | `music` | `mixed`. `pos`: `bottom` | `top`(그 칸의 줄·소리 가운데 하나라도 `pos: "top"`이면). `flags`: `timing_estimated`(시각 추정 줄·소리,
  작가가 묶은 칸은 한 줄이라도 추정이면) |
  `overlap`(겹친 대사) | `flash`(`flash_dur` 0.25초보다 짧음). `refs`: 그 칸을 만든 줄·후보 id(작가의 '앞 줄과 한 칸'이면 두 줄 id, 앞 줄 먼저).
  한 줄이 여러 칸으로 나뉘면 칸마다 같은 줄 id다.
- `check.notes`: 배치가 지키지 못한 까닭과 늦게 뜬 까닭('작가가 정한 끊을 자리로 나눈 칸이 5/6초가 안 됨 · …', '장면이 바뀌는 화면 전환을 넘음 · …',
  '앞 줄과 한 칸으로 묶지 못함 · <까닭>', '음악 시작 1.29초 뒤' 등). `check.gap_next`는 대사 칸 뒤에서 0.5초일 수 있다(§6 끝 조정 ①).
- `no_room`의 대사 줄은 `line`·`kind: "dialogue"`가 붙는다. `hint`(선택): 칸 하나를 고치면 들어갈 때만 {`cell`(draft 칸 id), `line`(그 칸의 줄 id, 소리만 든 칸이면 null),
  `by`(초), `how`: `end_earlier`(앞 칸을 그만큼 일찍 끝냄) | `move_later`(뒤 칸을 그만큼 늦춤)}, `reason` 끝에 ' · 앞 대사 칸을 0.42초 줄이면 들어감' /
  ' · 뒤 대사 칸을 0.29초 늦추면 들어감'('소리 칸'일 수도). `reason`의 앞부분(' · ' 앞)은 예전 글 그대로다.
- `unplaced`: `settle`이 모은 자리 없는 줄(§4.5). 한도에 걸렸을 때만 `unsettled`(표시가 맞춰지지 않은 줄 id).
- `fps`는 영상 값 그대로(격자), 한도는 공칭 fps로 센다(§4.7 `nominal_fps`).
- `notation`(`style.NOTATION`)이나 `profile`이 지금과 다른 draft는 내보내기 점검 전에 다시 만든다.
- 독립 검사기(`check.load`)는 칸 목록, 이 꼴의 dict, `.srt` `.vtt` `.ttml` `.json` 파일을 받는다. `cells` 열쇠가 없는 JSON 객체(잘못 넣은 `items.json` 등)는
  칸이 없는 입력이라 `ValueError`(있는 열쇠 몇 개를 글에 넣음)이고 명령줄은 끝 코드 2다. `cells`가 `[]`나 null인 초안은 0칸이다.

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
  이름 힌트(§3.2 `name`)가 붙은 줄은 `ok`이지만 검수 서버의 줄 행에 `asr_text`·`diff`가 실린다(`line_row`는 들린 글을 `differs`·`unheard`·들린 대로 고른 줄과
  `hints`에 `name`이 있는 줄에 싣는다. 화면이 대본 글 옆에 들린 글을 보이고 '들린 대로'를 고를 수 있게).

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
  작은 동적 계획으로 차례대로 놓는다(`timing: "voice"`, 구간이 없으면 `"even"`). 분리하면 비명·웃음은 효과음 트랙으로 간다(§12).
  비명·웃음 점수(`VOCAL_FX`)와 노래 점수(`SINGING`)는 점수 파일에 있는 갈래로만 읽는다(`Voice._load`). 갈래가 하나도 없어도 멈추지 않는다
  (비명·웃음 점수는 0, 노래 구간은 없음).
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
- 자막 대본의 소리만 칸(`at`이 있는 `vocal` 단서)은 그 칸의 시각을 처리 구간 안으로 자른 것이 창이다(`window_by` ['line', 'line'], `window_out` [false, false]). 구간 밖이면 창이 없다.
- `window_out`: 그 끝이 대본의 다른 장면 너머에 있나. `other_scene`은 늘 참, `line`·`scene`은 거짓, `range`는 `absent`가 아닌 다른 장면이 대본 순서로 그 사이에 있을 때만 참
  (대본의 첫 장면이 처리 구간 시작에 닿는 것은 장면 밖이 아니다).
- `window_estimated`: 기대는 줄의 시각이 추정(`voice`·`even`·`unheard`)이거나, `scene` 끝이 `found`가 아닌 장면에서 왔다.
- 어림 시각 `guess`: 같은 창의 단서들은 대본 순서로 나눈다. 앞 끝만 장면 밖이면(장면 첫 대사 앞) 창 안 (k+1)/(n+1), 뒤 끝만 장면 밖이면(마지막 대사 뒤)
  그 줄부터 `GUESS_STEP_S`(5초) 간격(창의 k/n을 넘지 않음), 그 밖은 k/n. 대사 없는 장면이 이어지면 같은 틈을 함께 나눈다. 영화 머리·꼬리에 이어지는 대사 없는
  장면들도 처리 구간 끝과 이웃 사이를 대본 순서로 나눠 갖고(`scene_starts`, `_quiet_parts`), 그 장면들의 단서는 자기 몫 안에서 `guess`를 받는다.
- **먼 소리**(`off_scene`, `cue_far`): 창이 `WIDE_S`(30초)보다 넓고 한쪽 끝만 자기 장면(`anchor`)인데 반대쪽 끝의 `window_out`이 참이면, 그 `anchor`에서 `WIDE_S`보다 먼 소리는
  다른 장면의 소리일 수 있다. 그런 짝은 '대본' 근거가 붙지만 포함할 까닭이 되지 않고(`is_reason` false) 확인 필요 사유에 '대본 단서의 장면에서 먼 소리'가 붙는다.
  같은 끝을 나누는 단서들도 `guess`가 아니라 `anchor`에서 잰다(`guess`는 앞 장면 몫일 수 있는 부분까지 고르게 퍼지기 때문).
  넓은 창의 단서들은 함께, 각자의 자리(`places`)에 가까운 후보를 대본 순서로 고른다. 가장 가까운 후보에서 `NEAR_S`(5초) 안의 후보들은 같이 가깝다고 보고
  그 가운데 그럴듯한 것을 고르며, 앞 단서가 가져간 소리보다 앞의 소리는 받지 않는다(`match_sounds`, `_match_placed`).
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
  주지 않으면 이름을 보인다. 앞 줄이 아직 정하지 않은 확인 필요면 그 줄은 제 추천을 두고 `follows` = 앞 줄, 이유 끝에 ' · 앞 줄을 먼저 정해야 함'(`state.WAITS`).
  얼굴이 보이는 줄에는 달지 않는다(앞 줄이 어떻든 이름이 필요 없다. 추천 단계는 얼굴과 상관없이 단다). 작가가 얼굴이 안 보이거나 애매한 줄의 이름을 빼면 그 줄은 식별로 치지 않는다.
- **앞 자막에서 이미 알림**(`_followers`, 감사 6-m2·9-x1): `follows`가 달린 효과음·음악은 뿌리가 최종 포함이고, `no_room`이 아니고, 배치에서 빼지 않았을 때만 제외다.
  아니면 확인 필요('앞 후보(…)가 자막에 나가지 않음 · 확인 필요').
- **표시**: 줄마다 `source_label`(`style.source_label`, 화면 밖은 낱말 없음), `label_name`(이름이 밝혀지기 전이면 일반 표시, §4.6), 배치 입력의 `label_extra`
  = 이름을 보이는 줄은 [출처, 말투], 보이지 않는 줄은 [말투]만(`state.label_extra`. 출처는 이름 없이 나가지 않는다).
- **붙지 못한 결정**(`aside`, §3.6): `{"key", "kind": "line" | "speaker", "speaker", "text"(결정할 때의 줄), "decision", "at", "candidates", "why"}`.
  `why`: `text_changed`(같은 화자의 글이 바뀐 줄 하나, 앞뒤 이웃이 그대로) | `same_words`(같은 글 줄 가운데 문맥이 가장 맞는 것들) | `gone`(후보 없음).
  화자 행의 `decision`은 {`final`, 저장된 다른 칸}, 후보는 화자 후보가 있는 줄만. 줄 행 다음에 화자 행. 자막·배치에 쓰지 않는다.
- **settle**: 배치 → `no_room`에 오른 줄을 알고 다시 `compose` → 표시가 바뀌었으면 다시 배치. 자리 없는 줄 집합은 라운드마다 줄지 않고 모은다(`draft.unplaced`:
  이름을 한 번 더 보이는 쪽이 안전하고, '자리 없는 줄 → 다음 줄이 이름을 보임 → 긴 표시가 다음 추정 줄의 자리를 가져감' 같은 사슬이 줄마다 많아야 한 라운드라 끝난다). 라운드는 많아야 min(줄 수 + 2, `SETTLE_MAX` 64)번이고, 넘으면 표시가 다른 줄을
  `draft.unsettled`에 적고 내보내기 점검이 알린다. 돌려주는 구성은 `compose(draft)`. `draft.json`은 이것이 쓴다.
  `Project.recompute()` = `settle()[1]`이고, 실행 끝과 검수 서버가 모두 이것을 쓴다. `finish`가 줄마다 `cells`, `no_room`, `joined`(앞의 넣은 줄과 칸을 함께 씀:
  작가의 앞 줄과 한 칸, 또는 다른 화자의 하이픈 칸)를 단다.
- **내보내기 점검**(`export_check`): `check`(확인 필요 소리), `check_speaker`, `no_room`(소리), `exclude_recommended`(확정하지 않은 제외 추천), `unnamed`(`[소리]` 그대로인 포함 효과음),
  `violations`, `guide`(검사기 결과, 초안 fps·전환·프로필로), `flags`, `lines` {`differs_open`, `unheard`, `absent`, `out_of_range`, `writer_excluded`, `no_room`, `unsettled`, `aside`},
  `extras_open`, `extras_suspect`, `names_early`, `heard_no_cell`.
- **칸 없는 말**(`_heard_no_cell`, 감사 6-04, II.1): `asr.json` 낱말 가운데 대사 칸(앞뒤 `HEARD_PAD` 0.5초)이 덮지 않는 것을 `HEARD_GAP`(1초) 안쪽끼리 한 구간으로 묶는다.
  빼는 것: 작가가 무시한 대본에 없는 말·`suspect`인 말의 낱말, 감탄사·소리말뿐인 구간, `HEARD_SHORT`(0.3초) 미만 한 낱말 구간, 예전 `asr.json`(hint_echo 없음)의 힌트 되풀이.
  행: {`start`, `end`, `text`, `words`, `extra`(그 구간의 낱말이 가장 많이 든 대본에 없는 말 id. 같은 수면 먼저 들린 것), `line`(겹치는 대본 줄 id, 작가가 뺀 줄이어도)}.
  고르는 차례가 집합 차례(문자열 해시, `PYTHONHASHSEED`)를 따르지 않으므로 같은 입력이면 `extra`도 같다.

### 4.6 인물: 이름이 밝혀지는 줄과 일반 표시 (`cboe/people.py`, II.8)

- 인물은 이름이다: 대본의 화자 이름(`script.json` `people`), 또는 작가가 줄에 준 이름. `가람/나래`는 둘이 함께 말한 줄이다(`SPLIT`).
- **이름이 밝혀지는 줄**(`revealed_at`): 영화 순서로 **다른 사람**이 그 이름을 낱말로 말한 첫 줄(낱말 처음부터, 뒤에 낱말 끝·부름말·호칭 `HONORIFICS`·조사 `PARTICLES`·
  서술 `COPULA`·`MORE` 꼴), 또는 스스로 이름을 댄 줄(`SELF`). 지어낸 예: `가온아`, `가온 씨`, `가온이가`는 밝힘, `운전기사` 안의 `기사`는 아님.
  계사·비교뿐인 약한 꼴(`가온이라는`, `가온이었어`, `가온보다`, `가온이나`: `사랑이라는`, `영화보다`처럼 흔한 낱말에도 붙는다)만 있는 줄은 이름을 밝히지 않는다.
  그 뒤의 확실한 꼴(그대로·부름말·호칭·조사)이나 자기소개 줄이 밝힌 줄이다(앞에 약한 꼴 줄이 있어도. 일반 표시가 더 오래 가는 II.8의 안전한 쪽).
  한 글자 이름은 부름말(`VOCATIVES`)·호칭이 붙거나 분명한 자기소개(`SELF_ONE`)일 때만 친다. 자막에 든 줄만 읽는다. 화자 근거 단계의 '이름 밝혀짐'도 이 함수를 쓴다.
- **표시**(`display`): 이름이 밝혀지기 전에는 II.8의 일반 표시를 쓴다("so as not to provide information that is not yet present in the narrative" =
  아직 이야기에 나오지 않은 정보를 주지 않도록). 남녀는 대본에 없으므로 작가가 인물마다 정한다: `mode: "generic"` + `before`(`남자`, `여자`, 직접 적은 말) 또는
  `mode: "name"`(처음부터 이름). 숫자로 끝나는 이름(`ROLE`, `경찰1`)과 대본 스스로 일반 표시로 부르는 화자(`GENERIC_WORDS`의 `남자`·`여자`, 작가가 `before`로 쓰는 말:
  `is_role`, `generic_labels`)는 역할 이름이라 기본이 `name`이다. `reveal`: `auto`(밝힌 줄 다음 줄부터 이름) | `line`(고른 줄부터) | `never`.
  정하지 않은 인물은 이름을 그대로 쓰고, 밝혀지기 전에 이름이 나가면 `early`(내보내기 점검의 `names_early`)에 올린다.
- **번호**: 한 장면에서 같은 표시 글자를 두 사람 이상 쓰면 그 장면에서 처음 말한 차례로 번호를 붙인다(`[남자1]`, `[남자2]`). 장면마다 다시 세고, 한 사람이면 번호가 없고,
  그 장면의 다른 표시가 이미 쓰는 번호는 건너뛴다. 장면이 없으면 영화 전체로 센다. 대본 화자 이름이 `남자`인 사람과 일반 표시 `남자`를 고른 사람도 둘로 센다.
- 표시 글은 저장하지 않고 매번 줄과 결정에서 새로 만든다. 이름 없는 줄은 인물이 없어 번호·밝혀짐 셈에 들지 않는다.
- **같은 사람 후보**(`maybe_same`, `same_person_hint`, 감사 3-m3): 대본이 한 사람을 두 화자 이름으로 적는 경우를 내기만 하고 합치지 않는다. (가) 성 + 이름
  (`SURNAMES`·`DOUBLE_SURNAMES`, `김가람`↔`가람`, `김가람님`↔`가람님`) → `"surname"`. 짧은 쪽이 직함·직업·가족 말이면 아님(`NOT_GIVEN_NAMES`, `_surname_of`):
  `김형사`↔`형사`, 성과 같은 글자로 시작하는 직함 `부대장`↔`대장`·`박의원`↔`의원`, 사극 직함 `왕세자`↔`세자`·`김대감`↔`대감`·`이도령`↔`도령`,
  끝의 '님'을 뗀 말이 목록에 있는 꼴 `김교수님`↔`교수님`. (나) 나이·때 꾸밈말
  (`AGE_WORDS`·`AGE_ATTACHED`·`AGE_NUMBER`: `어린 가람`, `가람(아역)`, `가람 (17세)`) → `"age"`. 다만 사람을 뜻하는 꾸밈말(`AGE_OWNERS`: 아기·꼬마·소년·소녀·청년·노인)
  + 가족 말·직함은 그 사람의 가족이라 아님(`아기 엄마`↔`엄마`. `어린 엄마`·`엄마(아역)`·`20대 아빠`는 후보). 관계 이름(`가람 엄마`, `가람모`)도 후보가 아니다.
  목록은 손으로 쓴 것이라(데이터로 재지 않음) 빠진 직함은 후보로 남을 수 있다. 지금 줄이 부르는 이름끼리만 보고,
  작가가 `not_same`으로 아니라고 한 짝은 빼고 낸다. 작가는 화자 바꾸기 범위 `person`으로 합친다.
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
| 화자 표시 | `speaker_label`, `line_labels`, `LABEL_SEP` | `[이름/출처/말투/언어] `(언어는 맨 뒤, 이름이 없으면 `[말투/영어] `만) | II.8, II.6 |
| 출처 낱말 | `SOURCE_LABELS`, `source_label` | 속마음 · 영상 · 전화 · 무전 · 방송 · 내레이션(화면 밖은 낱말 없음) | II.8 |
| 공칭 fps | `RATE_TOLERANCE`, `near_rate`, `nominal_fps` | 정수·NTSC 값에서 상대 1e-5 안이면 그 값(24.00001 → 24), 그 밖은 그대로. 한도 프레임을 이것으로 센다 | GR 1, TG 5 |
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
한도 프레임은 공칭 fps(`style.nominal_fps`, `_P.rate`)로 세고, 시각 격자와 초안의 `fps`는 영상 값 그대로다. 규칙의 전문(측정값 포함)은 `placement.py` 머리말이다.

입력(`state.Project.placement_inputs`): 넣은 줄만. `name` = 보일 표시 글(`label_name`), `person` = 말하는 사람(줄의 이름, 없으면 자막 대본의 이름 없는 무리 줄은
`#<칸>-<무리>`(`#1-2`, 표시되지 않음), 그 밖은 None), `turn`, `label_extra`(§4.5), `join`·`pos`(정했을 때만), `breaks`·`keep`. 작가가 시작·끝을 고정한 줄은 추정이 아니다
(`status` ok, `timing` writer). 소리: 최종 포함인 효과음·음악(배치에서 뺀 것 제외)과 `move_to`, `source_line`, `estimated`(대본 단서 후보), `pos`.

대사 칸:
- 대본 줄 하나에 칸 하나. 앞머리는 `[이름/출처/말투/언어] `(`show_name`일 때 이름, `label_extra`, 언어는 맨 뒤). 노래 줄은 칸마다 `♪ … ♪`.
- 2×16자에 안 들거나 말이 7초보다 길면 어절 경계에서 적고 고르게 나눈다(`_split`): 문장부호 뒤(다음은 쉼표 뒤)를 먼저 고르고, 붙여 둘 말(관형사·관형형과 명사,
  용언과 보조 용언·부정, 이름, 숫자와 단위: Kiwi 힌트, 일반 요건 4)과 닫힌 인용 안(I.14)은 다른 끊기가 비슷하면 가르지 않으며(`CUT_KEEP`, `textutil.keep_costs`),
  될 수 있으면 조각마다 그 자리에서 읽힐 만큼(다음 조각까지 5/6초와 읽기 속도) 나눈다. 읽히는 끊기가 없으면 붙여 둘 말 비용 없이 고른다.
  이름은 첫 조각에만, 언어 표시와 ♪는 조각마다(II.6 "in each event" = 자막마다, II.7 "each subtitle" = 자막마다). 나눈 뒤 줄바꿈을 하고(`textutil.wrap`: 붙여 둘 말·괄호 안·음표 옆
  비용에 `KEEP_WEIGHT` 8을 곱해 위아래 균형보다 먼저 본다. 문장부호로 끝나는 낱말 뒤는 비용 없음), 그 다음에 표시용 문장부호를 다듬는다(낱말은 그대로).
- **쉼에서 나누기**: 낱말 사이가 `PAUSE_CUT`(2초, I.4가 쉼이라 부르는 길이) 이상 비면 글자 수·7초 안이어도 그 자리에서 칸을 나눈다(쉼 뒤 말이 먼저 뜨지 않게.
  TG "Subtitles should be in sync with both the image and the audio" = 자막은 화면·소리와 맞아야 한다, "Avoid spoilers" = 미리 보이지 않게). 나누지 않는 줄: 시각 추정 줄,
  작가가 `breaks`·`keep`을 정한 줄, `join`으로 묶인 두 줄, 쉼 앞뒤 조각이 그 자리에서 읽히지 않는 줄(다음 조각·다음 줄 앞에 5/6초와 읽기 시간이 없거나, 그동안 다른 줄이
  말함: GR 1·II.3 의무가 싱크 권고보다 먼저, `_clear`). 쉼 앞뒤 조각도 다른 조각과 같은 규칙이고 말줄임표는 붙이지 않는다. 지어낸 1000장면에 2~4초 쉼을 넣어 잰 위반 칸:
  나누지 않음 1254 · 늘 나눔 1276 · 이 조건 1255.
- 시작 = 첫 낱말. 0.5초 안 앞의 화면 전환으로 당길 수 있다(앞 칸의 말·5/6초를 자르지 않을 때, TG 2). 전환 0.5초 안 앞에서 시작해 전환을 넘는 말은 전환에서,
  9~11프레임(24fps) 앞이면 0.5초 앞에서 시작한다(TG 4, 앞의 전환을 쓸 수 없을 때도). 끝 = 마지막 낱말 + 0.5초, 자리가 있으면 5/6초·초당 14자까지 늘리고,
  7초와 다음 칸 2프레임 앞을 넘지 않는다(TG 1, 3).
- **하이픈 칸**(I.6, TG 6 "merging with previous or subsequent subtitles is encouraged in order to borrow time" = 앞뒤 자막과 합쳐 시간을 빌리는 것을 권한다): 앞 줄이 다음 줄
  앞에서 혼자 5/6초를 못 받을 때, 두 줄이 알려진 다른 두 화자(`person`)이고 같은 장면의 통째 줄이며 줄마다 한 줄(16자)에 들고(I.6 "Text in each line in a dual speaker
  subtitle must be a contained sentence" = 두 화자 자막의 줄마다 완결된 문장) 7초 안에 말해지면, 다음 가운데 하나일 때 한 칸에 둔다(`_can_pair`):
  ① 두 줄 모두 말 + 0.5초가 5/6초에 못 미침(6차 전의 규칙. 추정 줄과 들린 줄도) ② 둘 다 들린 줄이거나 둘 다 추정이고, 자막 대본의 한 칸에서 온 두 무리 줄
  ③ 둘 다 들린 줄이거나 둘 다 추정이고, 묶은 칸이 다음 칸 앞에서 5/6초와 두 줄의 읽기 시간을 받음. 둘 다 추정인 두 줄은 ②·③으로 묶은 칸이 7초 한도(첫 줄 시작부터)
  때문에 다음 칸 3~11프레임 앞에서 끝나고 다음 칸 0.5초 앞에서 끝낼 수도 없으면 묶지 않고 비킨다(TG 5 의무, `_short_gap_left`. 들린 두 줄은 그대로). 자막 칸의 무리 줄이 다음 줄과 못 묶이고 5/6초를 못 받으면
  제 칸의 앞 무리 줄로 돌아가 묶는다(`_back_to_row`). 시각 추정 줄은 ①로만 들린 줄과 묶이고, 그 밖에는 비키거나 칸을 받지 못한다(아래).
- 다른 화자인지(`_other_people`): `#` person은 `turn`과 함께 오며 같은 자막 칸의 다른 무리 줄과만 다른 화자다. 칸이 다른 줄은 이름이 있든 없든 같은 사람일 수 있어
  하이픈 칸으로 묶지 않는다(`turn` 없는 `#` 줄은 아무 줄과도 묶지 않는다). 한 사람의 두 줄은 표시가 무엇이든 하이픈 칸을 나누지 않는다.
- 줄의 차례(`_in_order`): 시각 차례로 놓고, 시각이 같으면 대본 차례다. 자막 대본의 한 칸에서 온 무리 줄들은 시각이 어떻든 파일에 적힌 차례다(지침 I.6 은
  한 칸에 두 화자면 "- "로 줄마다 한 화자를 적게 할 뿐이다. 파일이 그 줄들을 말한 차례로 적는다는 것은 CBoE 가 그렇다고 보는 것이다.
  들리지 않은 둘째 줄의 시각은 칸 시작부터 고르게 편 낱말이라 들린 첫 줄보다 이를 수 있다). 그 줄들이 시각 차례로 차지한 자리에 파일 차례로 다시 넣으므로
  다른 줄의 자리는 그대로다. 그래서 그 칸의 하이픈 칸은 첫 줄의 시작에서 시작하고, 두 칸이 되면 첫 줄이 먼저이고 둘째 줄이 그 뒤에서 기다린다. 드물게 한 칸의 두 줄 시각 사이에
  다른 자막 칸의 들린 줄이 끼면 그 줄이 첫 줄 뒤로 밀린다. 촬영 대본(`turn` 없음)은 '시각이 같을 때만 대본 차례' 그대로다.
- 겹친 줄(`_untangle`): 앞 칸을 읽을 수 없을 만큼 가까이 겹치면 ① 화자가 다른 두 통째 줄이 한 줄씩 들면 하이픈 칸 ② 아니면 시각이 추정인 쪽이 비킨다(대본 순서 쪽에
  자리가 있으면 거기, 아니면 다른 쪽, 아니면 다른 줄의 읽기 시간 바로 뒤) ③ 그래도 안 되면 뒤 줄이 앞 칸 5/6초 뒤까지 기다리고 두 칸에 `overlap`.
  들린 줄끼리 먼저 풀고 추정 줄을 그 뒤에 끼운다(들린 줄은 추정 줄을 기다리지도 밀리지도 않는다). 한 줄의 조각끼리는 겹침이 아니다.
- 조각이 5/6초를 못 받은 줄은 다른 줄 칸 사이 빈틈 안에 조각 순서대로 다시 편다(빈틈이 넉넉하면 조각마다 읽기 시간, 아니면 5/6초). 들린 줄은 첫 조각이 첫 낱말보다
  먼저 뜨지 않지만, 뒤 조각은 뒤 조각들의 자리를 위해 자기 말보다 먼저 뜰 수 있다(GR 1 의무가 TG 1 "first frame of audio" = 소리의 첫 프레임 권고보다 먼저. 5차에 지어낸 1000장면에서
  그렇게 앞당긴 조각 143개, 막으면 5/6초 미만 칸 +156·flash +30). 그래도 안 들면 추정 줄은 칸을 만들지 않고 `no_room`(대사, 사유에 실제 빈틈과 필요한 길이)이고,
  들린 줄은 칸을 남긴다. 추정 줄 때문에 몇 프레임짜리 글을 보이지 않는다(감사 2-m1).
  한 조각의 들린 줄이 바로 뒤에 시작하는 추정 줄(그 위로 겹쳐 말하지 않는 한 조각) 때문에 5/6초 미만이면, 추정 줄이 들린 줄의 읽기 시간(안 되면 5/6초) 뒤로
  옮겨 기다린다. 옮긴 뒤에도 다음 칸·구간 끝·장면 전환 앞에서 제 5/6초와 읽기 시간을 받을 때만이다(`_wait_behind`. 들린 줄이 먼저다).
- 장면(TG 4 "Subtitles may not cross scene changes" = 자막은 장면 바뀜을 넘지 않는다): 다른 `scene_id`의 두 줄은 하이픈 칸을 나누지 않고, 장면의 마지막 칸은 다음 장면
  첫 줄과의 사이의 전환을 넘지 않는다. 말이 전환을 넘어가거나, 전환 앞에서 끝내면 5/6초가 안 되면 예외이고 `check.notes`에 적는다. 칸이 시작하는 프레임의 전환은 한도가 아니다.
- 작가의 `breaks`는 자동 끊을 자리를 바꾼다(안 들어가는 조각은 더 나누고 `check.notes`에 적음). 그 끊을 자리 때문에 5/6초가 안 된 조각에는 같은 줄의 어느 묶음이든
  '작가가 정한 끊을 자리로 나눈 칸이 5/6초가 안 됨 · …' 메모가 붙는다(빈틈을 조각들에 고르게 나누지는 않는다). `keep`은 소리가 그 칸을 나누지 못하게 한다(한 칸에 안 들면 무시하고 적음).
- **작가의 앞 줄과 한 칸**(`join`, 감사 2-02·7-03): 대본 순서로 바로 앞에 배치된 줄(첫 줄은 없음)과 어떤 자동 규칙보다 먼저 한 칸에 둔다. 다른 화자면 멀리 떨어져
  말해도 하이픈 칸 하나, 같은 사람이면 두 글을 빈칸으로 이은 한 칸(두 줄의 낱말·시각, 앞 줄의 표시, 소리가 다시 쪼개지 않음). 두 줄이 한 장면(TG 4), 같은 사람이면
  앞 줄의 표시가 둘째 줄에도 맞음(언어가 같고, 둘째 줄의 출처·말투 `label_extra`가 앞 줄 것 안에 있음: II.6 "in each event" = 자막마다, II.8. `_label_lost`.
  출처는 이름과만 나가므로 이름을 보이지 않는 둘째 줄은 앞 줄 표시 아래로 그대로 잇는다), 하이픈 줄마다 한 줄(아니면 두 글이
  2×16자), 들어오는 시각부터 7초 안, 앞 줄이 아직 다른 줄과 한 칸이 아님, 어느 줄도 조각으로 나뉘지 않음, 사이에 다른 줄의 칸이 없음일 때만이고, 아니면 제 칸을 두고
  `check.notes`에 `JOIN_NOTE` '앞 줄과 한 칸으로 묶지 못함 · <까닭>'(앞 줄이 이미 다른 줄과 한 칸 | 장면이 다름 | 화자 표시가 다름 | 너무 김: 2줄 16자 초과 |
  너무 김: 하이픈 줄 16자 초과 | 7초 초과 | 작가가 정한 끊을 자리로 나뉜 줄 | 사이에 다른 줄의 칸). 같은 사람인지는 배치 조각의 화자(= `person`, 없으면 `name`, 없으면 `speaker`)로 본다: 이름도 person도 없는
  두 줄은 한 사람으로 보아 글을 잇고, `#1-2`와 `#2-1`은 글자가 달라 하이픈 칸이 된다(작가가 정한 묶기라 글자 그대로 따름). 칸의 `refs`에 두 줄 id(앞 줄 먼저).
  묶인 두 줄은 쉼에서 나누지 않는다. 거절된 join만 있는 줄은 묶지 않은 줄처럼 쉼에서 나누어 다시 배치하고, 처음 거절 까닭이 그 줄의 조각마다 붙는다(`_join(refused)`).
  묶은 칸에 시각 추정 줄이 있으면 들린 줄과 묶여도 칸에 `timing_estimated`가 남는다. 같은 사람의 한 칸은 둘 다 추정일 때만 추정 줄처럼 배치되고(`guess`),
  표시는 하나만 추정이어도 남는다(`some_guess`, `_out`). 다른 사람의 하이픈 칸은 두 줄이 따로 조각으로 남아(`_out`이 조각마다 본다) 전부터 표시가 남았다.
- 화자 표시만 첫 줄에 홀로 남는 줄바꿈(`[가람]` / 대사…)은 16자가 허락하는 한 첫 낱말을 표시 옆에 붙여 다시 바꾼다(첫 낱말 끝의 `.` `,`는 견줄 때 뗀다).

소리 칸(10-13 메모 5.2):
- 효과음: 시작 = 소리 첫 프레임, 전환이 가까워도 앞당기지 않는다. 길이 = 글자 ÷ 14 + 1초(`label_extra`), 5/6초~7초.
- 음악: 시작 = 음악 첫 프레임, 0.5초 안 앞에 전환이 있으면 전환. 길이 3초(`music_dur`), 음악이 먼저 끝나면 음악 끝 + 0.5초, 적어도 5/6초.
- `move_to`(작가가 옮김)가 시작을 바꾼다. 시작이 추정인 소리(대본 단서 후보)의 칸은 `timing_estimated`.

자리(소리를 시간 순서대로, 메모 5.3):
1. 그 시각에 화면에 칸이 없고 뒤로 5/6초가 비면 따로 한 칸(앞 칸은 말 끝·5/6초·초당 14자까지 줄일 수 있음. 그 칸 시작 뒤에 든 소리가 있으면 그 소리 뒤
   `INTERRUPT_SHOW`까지: 같은 쉼의 둘째 소리가 첫 소리 문구를 들리자마자 끊지 않게. 칸을 줄이는 곳은 모두 이 바닥 `_floor`를 지킨다).
2. 한 줄짜리 칸 시작과 0.5초 안, 출처가 다름 → 같은 칸에 하이픈 두 줄, 들린 순서대로(II.5. 소리만 든 칸의 두 소리도).
3. 같은 자리, 말하는 사람이 낸 소리(`source_line`이 그 줄) → 하이픈 없이 `[지수] [재채기] 나도야!` 한 줄(안 들면 두 줄).
4. **대사를 끊는 소리**(대사 칸 시작보다 0.5초 넘게 뒤, 말이 이어지는 중): 소리에서 0.5초 안의 어절 경계로 칸을 나눈다(두 낱말 사이 쉼 안의 소리는 그 경계, `_split_point`).
   II.9 "Sound effects which interrupt dialogue should be treated as follows, ideally following the order of the original audio" = 대사를 끊는 효과음은 될 수 있으면
   원래 소리의 순서대로: 소리가 뒤 조각 첫 낱말보다 먼저 오고, 소리 문구가 먼저 보이는 폭이 `INTERRUPT_LEAD`(2초) 이하이며 소리 뒤로 `INTERRUPT_SHOW`(0.25초) 이상 남고,
   앞 조각이 하이픈 한 줄에 들고 두 칸 모두 5/6초·읽기 속도를 받으면 → 소리는 앞 조각과 하이픈 한 칸(대사 칸 시작부터: `- 내가 어제 봤는데…` / `- [첨벙!]`), 뒤 조각은
   첫 낱말부터(⑤-2에서는 앞 칸 5/6초를 위해 0.5초까지 늦춤, `_interrupt`). '…'는 소리 자리에서 말이 `INTERRUPT_PAUSE`(0.5초) 이상 멈췄고(I.4 "an abrupt interruption" = 갑자기 끊김)
   앞 조각에 문장부호가 없을 때만 붙인다(끊긴 조각마다 붙이지 않음, 감사 1-11). 그 밖(말이 쉼 없이 이어짐, 소리가 쉼 끝·낱말 위)과 화자 자신의 소리(③의 꼴,
   `[재채기] 그런 상황은 아니었어`)는 6차 전의 꼴: 소리가 뒤 조각과 한 칸이고 그 칸은 소리에서 시작(BBC 5.5), 두 칸 모두 5/6초 이상일 때만.
5. 한도를 넘으면 차례로: 짧은 문구 → 칸 끝을 뒤 빈틈으로 늘림 → 소리 뒤 0.5초 안에 시작하는 빈틈의 따로 칸 → 그 시각(또는 0.5초 안 뒤)에 화면에 있는 두 줄 대사 칸
   (대본 한 줄, `keep`·작가 join 칸이 아님)을 낱말 경계에서 한 줄 두 칸으로 다시 나누고(칸마다 5/6초·읽기 속도, 세 번째로는 나누지 않음, TG 6 "re-segment and re-time" = 다시 나누고 시각을 다시 맞춘다,
   `_resplit`) 소리가 그 하나와 함께(②, ③) 또는 따로(①, ⑤-3) → `LONG_SOUND`(2초) 이상 들리는 소리(음악 등)는 아직 5/6초 들리는 동안 `LONG_LATE`(3초) 안에 열리는
   다음 빈틈의 따로 칸(`check.notes` '음악 시작 1.29초 뒤', 시작이 추정인 소리·작가가 옮긴 소리는 빼고, `_late`) → `no_room`(사유 예: '소리 뒤 빈틈 0.42초').
   칸 하나를 고쳐 들어가는 경우만 `hint`와 사유 꼬리를 단다(§3.7. 지금 칸들 위에서 계산, 고친 칸의 5/6초·읽기 시간을 지키고 시작 늦춤은 0.5초까지,
   `_sound_hint`·`_line_hint`. 시각 추정 대사 줄의 자리 없음에도).

끝 조정(TG 1, 2, 4, 5, 모든 칸 시간 순서대로): ① 다음 칸과 0.5초 미만 틈은 2프레임으로 붙임. 다만 대사가 든 칸이 이미 말(합쳐진 소리 포함) 끝 + 0.5초까지 나가 있으면
늘리지 않고 다음 칸 0.5초 앞에서 끝낸다(TG 5 "If an out-time has been extended by half a second beyond the end of the audio, avoid extending this further to close gaps"
= 소리 끝보다 0.5초 늘린 끝은 빈틈을 닫으려고 더 늘리지 않는다, 감사 2-08). 그러면 5/6초·읽기 속도를 못 지킬 때는 2프레임으로 닫고, 새 끝 앞 0.5초 안이나 빈틈 안에
전환이 있으면 전환 규칙(②~④)대로 간다. 소리만 든 칸과 0.5초를 아직 못 받은 칸은 2프레임으로 닫는다 ② 전환 0.5초 안 앞에서 끝나면 전환 2프레임 앞으로
옮김(다음 칸이 전환 뒤일 때) ③ 소리가 전환 전에 끝났는데 칸이 전환을 넘으면, 5/6초와 초당 14자가 지켜질 때 전환 2프레임 앞에서 끝냄. 전환을 넘는 대사 칸은 자리가
있으면 전환 뒤 0.5초 이상. ③ 뒤에 남은 3프레임~0.5초 틈은 ①처럼 다시 닫거나(7초가 막으면 0.5초로 넓힘), 그 안에 전환이 있으면 그대로 둔다 ④ 앞 칸이 전환
2프레임 앞(또는 전환)에서 끝나고 전환 뒤 0.5초 안에 시작하는 대사 칸은 전환에서 시작한다(TG 2, 그 빈틈은 2프레임). 대사로 시작하는 하이픈 대사+소리 칸
(소리 ④의 `- 내가 어제 봤는데…` / `- [첨벙!]`)도 대사 칸으로 보아 같다(소리 문구가 들리기 `INTERRUPT_LEAD` 넘게 먼저 뜨지 않을 때, `_start_on_cut`).
소리로 시작하는 칸은 소리 첫 프레임을 지킨다(BBC 5.5). 소리가 제 시각에 시작해 남은 전환 낀 빈틈은
배치가 알리지 않고(검사기의 `short_gap_cut` 권고), 그 밖은 '빈틈 0.5초 미만'으로 알린다.

원칙: 대사 글은 바꾸거나 줄이지 않는다(줄바꿈과 표시용 문장부호만, 끊는 소리 앞 '…'는 위 조건에서만). 소리를 저절로 빼지 않는다(칸이 되거나 `no_room`).
대사 칸 하나에 소리 하나만, 두 줄인 대사 칸에는 그대로 합치지 않는다(⑤에서 먼저 한 줄 두 칸으로 나눈다). 몇 프레임짜리 칸(`flash_dur`보다 짧음)은 `flash`로 표시한다.
예전 작업 폴더의 `♬ 무서운 음악`은 `[무서운 음악]`으로 쓴다.

배치 측정(7차 끝에 잼): 비교 기준은 커밋 0089a2a(쉼 나누기까지 든 코드. 6차의 소리 규칙 전: ④에서 소리를 앞 조각과 한 칸에, ⑤의 다시 나누기·늦은 칸, `hint`)이고,
지금은 그 뒤 규칙이 모두 든 코드다. 지어낸 1000장면(REAL=1 REALDUR=1)을 `placement.place`·`check.run`으로 돌려 셈: 자리를 얻은 소리 975 → 1278(3049개 가운데),
자리 없는 소리 2074 → 1771(효과음 1689 → 1439, 음악 385 → 332. 그 가운데 hint 0 → 93: 효과음 79, 음악 14), 자리 없는 대사 110 → 110(그 가운데 hint 0 → 42),
위반 칸 1579 → 1544, 검사기 의무 2038 → 1984, 5/6초 미만 칸 766 → 729, 칸 17311 → 17602. 실사 장편 앞 30분 사본(json·env.npz, `settle`): 칸 313 → 322,
위반 칸 10 → 7, 자리 없는 효과음 17 → 8, 자리 없는 대사 2 → 2, 검사기 의무 12 → 7(읽기 속도 8 → 6, 5/6초 4 → 1), 권고 34 → 34.

남은 빈 곳(고치지 않음):
- `_split`은 마지막 조각의 자리를 다음 줄의 시작으로만 잰다. 이미 시작해 겹쳐 말하는 긴 줄은 보지 않아, 읽힌다고 본 조각이 그 줄 조각 사이에 끼어 짧아질 수 있다
  (쉼 자르기는 `_clear`가 겹쳐 말하는 줄도 본다).
- 이미 소리가 든 칸(하이픈 칸 등) 위의 둘째 소리, 소리 0.5초 안에 어절 경계가 없는 긴 낱말 위의 소리는 나눌 길이 없어 `no_room`이 된다.
- 끊는 소리가 6차 전 꼴로 돌아간 경우는 말이 멈췄어도 '…'를 붙이지 않는다. `hint`는 칸 하나 고침만 싣는다.
- 자동 하이픈 칸(①②③, `_back_to_row`, `_untangle` ①)을 작가가 떼어 놓을 결정이 없다(`join`은 true \| null). 일반 대본 줄에서 둘째 줄이 혼자 5/6초를 못 받을 때
  앞 줄과 거꾸로 묶지 않는다(앞 줄 길이만큼 먼저 떠 타이밍 머리말의 'Avoid spoilers'(= 미리 보이지 않게)와 부딪힌다. 작가가 join으로 묶는다). ③에서 묶은 칸이 읽기 속도를 못 지키면
  따로 둔다(따로 두면 위반 칸이 둘, 묶으면 하나일 수 있지만 더 적은 쪽을 고르는 규칙은 지침에 없다).
- 쉼 나누기(`_clear`)는 조각이 그 자리에서 읽히는지를 전환 맞춤(`_snap_in`, TG 2·4) 전의 시각으로 잰다. 그래서 드물게 쉼 뒤 조각이 전환으로 당겨진 뒤
  읽기 속도를 넘는다(지어낸 장면에 쉼을 넣은 판에서 재현: 35프레임·31프레임 칸이 초당 14자 초과). 고치려면 전환을 `_split`까지 넘겨 `_split_memo` 열쇠에 넣거나,
  읽기 위반이 난 쉼 자르기를 되돌리는 되먹임을 새로 짜야 한다.

## 7. 검수 서버와 화면 (`cboe/server/app.py`, `cboe/server/static/`)

- 한 쪽짜리 정적 페이지(`index.html`, `app.js`, `app.css`). 작가가 고칠 때마다 서버가 `Project.set_*`로 `review.json`에 쓰고, `settle`(§4.5)로 다시 배치하고,
  화면 상태 전체를 돌려준다. 목록·타임라인·영상 위 칸이 늘 같은 draft 하나에서 그려진다. 서버는 LLM을 부르지 않는다.
- 서버는 이 PC의 요청만 받는다(`LocalOnly`: Host가 localhost, 바꾸는 요청은 다른 사이트 Origin이면 거절). 모르는 id는 404, 쓸 수 없는 값은 400(한국어 문구), 작업 폴더와 부딪히면 409.
- 영상과 정지 화면은 처리 구간 [t0, t1) 안에서만: 페이지는 그 밖으로 가지 않고 `/api/frame`은 구간 안으로 묶는다(시험 영상의 봉인 구간을 보이지 않으려고).

`GET /api/state`의 칸: `version`, `project`(`title` `fps` `t0` `t1` `duration` `width` `height` `video` `video_url` `video_name` `sample` `work` `profile` `envelope`, 재생용 영상의 `media`),
`dialogue`(예전 화면용), `lines`(합친 줄, 영화 순서: `line_row`), `line_codes`, `extras`, `scenes`, `people`(줄 수, `told`, `early`, `mode`·`before`, `decision`, `maybe_same`),
`aside`(붙지 못한 작가 결정, §4.5), `added`(+ `cells`·`no_room`, `pos`는 정했을 때만), `items`(줄이 빠진 화자 후보는 뺌, `cells`·`no_room`·`swap_prev`, 소리는 늘 `pos`
`"top"` | null, 화자는 `speaker.label`·`label_tail`), `draft`, `cuts`, `characters`, `style`(`Style.public()` + `formats`), `summary`, `export_check`(§4.5), `error`.

- `summary`: `total` `decided` `final` `rec` `kind` `by_kind` `no_room` `cells` `violations` `guide`{`must` `should` `must_items` `should_items`} `flags`,
  `lines`{`total` `status` `included` `excluded` `added` `estimated` `text_changed` `no_room` `unsettled` `aside` `differs_open`},
  `extras`{`open` `line` `ignored` `total` `suspect`(정하지 않은 것 가운데) `by_suspect`{`credit` `greeting` `repeat`}},
  `people`{`total` `early` `decided`(`mode`를 정한 인물) `reveal_only`(`not_same`만 있는 결정은 세지 않음)}, `heard_no_cell`(구간 수).
- 줄 행(`line_row`, 긴 영화를 위해 작게): `ROW_KEYS`(id · idx · seq · origin · speaker · name · label_name · text · text_from · status · timing · start · end · guess · scene_id ·
  language · source · note · hints · heard_language · match · voice · speaker_item · excluded · extra · breaks · decision · cells · no_room · source_label · pos · manner)
  가운데 빈 것과 `origin`·`text_from`의 "script"는 빼고, 참인 깃발(`ROW_FLAGS`: include · estimated · pinned · decided · show_name · name_early · generic · sung · keep · joined)만.
  `delivery`(대본의 말투, 늘 목록), `script_text`(보이는 글과 다를 때), `raw`, `asr_text`·`diff`(`differs`·`unheard` 줄, 들린 대로 고른 줄, `hints`에 `name`이 있는
  `ok` 줄만), `label`(이름을 보일 때 배치가 쓰는 표시).
- 내보내기 경고 글(`export_warnings`)은 `export_check`를 한 줄씩 글로 쓴 것이다(붙지 못한 결정, 맞춰지지 않은 이름 표시, 대본에 없는 말 가운데 크레디트·인사·반복 의심 수,
  '말이 들리는데 자막 칸이 없는 구간 N곳' 포함). `POST /api/export`의 `check`는 쓴 파일을 다시 읽어 초안 fps로 한 검사(`export.file_check`)이고, 화면의 지침 검사 목록
  (`export_check.guide`)은 초안 칸의 검사다(정확한 fps에서는 같다).

| 방법 · 주소 | 받는 것 | 하는 일 |
|---|---|---|
| `POST /api/items/{id}` | `decision` `phrase` `short` `name` `scope` `pos` | 후보 결정, 화자 이름(범위는 §4.6), 소리 칸 위치(`"top"` \| null, 효과음·음악만: 화자 후보면 400) |
| `POST /api/items/bulk` | `ids` `decision` | 여러 후보 한꺼번에(제외 추천 묶어서 확정) |
| `POST /api/placement/{id}` | `action`(`exclude` \| `move` + `t` \| `swap_prev` \| `clear`) | 배치 확인 필요 처리. 옮기거나 바꾸면 포함으로 정한다 |
| `POST /api/lines/{id}` | `include` `text` `text_from` `name` `language` `sung` `start` `end` `breaks` `keep` `show_name` `source` `join` `pos` `manner` | 대사 줄 결정. null로 보낸 칸은 기본으로(`source` null = 대본 것, `""` = 없음, 모르는 값 400; `join` false는 null처럼; `pos` "bottom"은 null처럼, 그 밖은 400; `manner` 21자 이상·안쪽 `[ ]`는 400; `breaks`는 정수 목록만(아니면 422), 한 어절 줄이면 400) |
| `POST /api/lines/bulk` | `ids` `include` | 여러 줄 넣기·빼기 |
| `POST /api/lines/aside` | `key`(`fp:…`) `line`(줄 id \| null) | 붙지 못한 결정을 그 줄(화자 결정이면 그 줄의 화자 후보)에 붙이거나, null이면 버린다. 모르는 key·줄 404, 화자 후보가 없는 줄 400 |
| `POST /api/lines/{id}/speaker` | `name` `scope` | 화자 이름을 범위대로(이름 없는 줄은 `line`만, 그 밖은 400). 같은 사람 합치기는 `scope: "person"` |
| `POST /api/extras/{id}` | `action`(`line` \| `ignore` \| `clear`) + 줄로 넣을 때 `text` `name` `language` `sung` `start` `end` | 대본에 없는 말 다루기(노래 구간의 말이면 `sung` 기본 참, 보낸 값이 이김) |
| `POST /api/added` · `POST /api/added/{id}` · `DELETE /api/added/{id}` | `kind` `start` `end` `text`·`phrase` `name` `language` `sung` `show_name` `breaks` `keep` `pos` | 작가가 넣는 줄·효과음·음악(소리에 `breaks`면 400) |
| `POST /api/people` | `names` + `mode` `before` `reveal` `reveal_line` `not_same` | 인물 표시(여럿 한꺼번에). `not_same`은 전에 말한 것에 더하고(자기 이름은 무시) null은 지움, 모르는 이름 404 |
| `POST /api/export` · `GET /api/export/{fmt}` | `format` | 내보내기(`path` `cells` `check` `warnings` `export_check` `state`), 내려받기 |
| `GET /api/frame?t=&w=` | | 정지 화면 JPEG(원본에서, 구간 안으로 묶음) |
| `GET /api/envelope?step=` | | `env.npz`를 `step`초마다 가장 큰 값으로 줄인 트랙별 세기(정수 dB, 칸 수 `ENV_MAX_BUCKETS` 20000 이하, 바닥 `ENV_FLOOR_DB` -90). 파일이 없으면 404 |
| `GET /media/video` · `GET /api/media` · `POST /api/media/build` | | 영상과 재생용 영상(§7.2). 만드는 중 `/media/video`는 409 |

화면은 보기 다섯 개(후보 · 대사 · 대본에 없는 말 · 인물 · 칸)와 타임라인이다(USAGE.md '검수 화면'). 수치·표기는 상태의 `style`에서만 받는다.
200행씩 그리고(`CHUNK`) 내려가면 더 그린다.

### 7.1 화면이 읽고 보내는 것 (`static/app.js`)

- 읽는 상태 칸(위 목록 가운데 화면이 기대는 것): `aside`(행의 `kind`가 없으면 line), `summary.lines.aside`, `lines[].decision`(source · join · pos · manner …),
  `lines[].source`(결정이 없을 때 대본 출처), `lines[].delivery`·`joined`·`hints`(`name` · `language`)·`heard_language`, `items[].reason`의 끝 ' · 앞 줄을 먼저 정해야 함'과 `follows`,
  `items[].evidence[].window_out`과 대본 후보의 `window_out`, `items[].pos`, `added[].pos`, `people[].maybe_same`, `extras[].sung`·`suspect`,
  `export_check.heard_no_cell`(없으면 `summary.heard_no_cell` 수만), `export_check.lines.unsettled`, `draft.cells[].pos`·`check.notes`(join을 못 묶은 메모는
  '앞 줄과 한 칸으로 묶지 못함'으로 시작한다고 본다. 그 메모는 줄 상세에서 '앞 줄과 한 칸으로' 스위치 옆에 한 번만 보이고,
  줄 상세의 칸 메모 목록에서는 뺀다(자막 칸 상세에는 그대로 나온다): `joinNowOf`),
  `lines[].asr_text`·`diff`(이름 힌트 줄도 대조에 들린 글), `project.media`(`building`이면서 `progress` null이고 `note`가 있으면 진행률 대신 그 글).
- 보내는 것: 위 표의 요청. 줄 칸은 한 칸씩 보낸다(`{join: true | null}`, `{pos: "top" | null}`, `{manner: 글 | null}`, `{source: null | "" | 코드}`). 작가가 넣은 줄에는
  source·join·manner를 보내지 않고 `api/added/{id}`에 `pos`만 보낸다. 출처 메뉴의 이름(`SOURCE_KO`)은 `app.js`에 있다(배치가 쓰는 낱말은 `style.SOURCE_LABELS`).
- 다시 그려도 지키는 것: 치던 글 칸과 그 포커스, 보내는 중인 값(`pend`: 줄의 `PEND_FIELDS` breaks · keep · sung · language · source · join · pos · manner, 인물의
  `PEND_PERSON` reveal · reveal_line). 거절된 쓰기는 저장된 값으로 돌아간다. 값을 바로 저장하는 스위치·select는 답이 온 뒤 포커스를 화면에 돌려준다
  (Space·↑↓가 그 컨트롤로 결정을 다시 쓰지 않게).
- 영화 시각 기준으로 일하고, `project.media`가 `/media/video`의 파일(원본 또는 t0부터의 재생용 영상)을 정한다. building 동안 2초마다 `api/media`를 묻고,
  원본을 열지 못하면 페이지당 한 번 `api/media/build`를 보낸다.

### 7.2 재생용 영상 (`cboe/proxy.py`)

- `playable`: mp4·mov·m4v 또는 webm이고 영상 h264·vp8·vp9·av1, 소리 aac·mp3·opus·vorbis·flac(또는 없음)이면 원본을 그대로 준다(mkv는 늘 만든다).
- `build`: `-ss t0 -t (t1-t0)`를 입력 앞에 두어 구간 밖은 읽지도 쓰지도 않는다(먼저 `guard.check_window`). H.264 + AAC, 폭 960 이하, 원본 fps, 키프레임 약 1초,
  `+faststart`. 인코더는 `encoders()`가 짧은 시험 인코딩으로 고른 h264_qsv → h264_nvenc → h264_amf, 다 안 되면(또는 이 파일에서 실패하면) libx264.
  `review.mp4.<pid>.part`(그 프로세스만의 이름)로 쓰고 확인한 뒤 이름을 바꾸고 `review.mp4.json`(`video` `size` `mtime` `t0` `t1` `encoder` `width` `height`)을 쓴다.
- 확인(`check_copy`): ffprobe로 H.264 영상 하나, 원본에 소리가 있으면 AAC, 길이가 구간과 맞음(짧으면 원본에서 사본 끝 뒤 `AFTER_S` 30초 안에 그림·소리 패킷이
  없을 때만 받음: `media_after`), 처음·가운데·끝 1초를 `-f framecrc`로 풀어 디코더 오류만 센다(`decode_errors`.
  먹서의 'non monotonically increasing dts'(= dts가 차례대로 늘지 않음) 메시지는 세지 않음).
  걸리면 다음 인코더로 간다.
- 잠금: 만드는 동안 `review.mp4.lock`(filelock)을 잡는다. 같은 폴더의 두 번째 `Worker`·서버는 기다리고(`on_wait` → 상태 note `WAIT_NOTE`) 끝나면 그 사본이 새것이면 쓴다.
  끝난 프로세스가 남긴 part 파일은 잠금 안에서 지운다(`part_files`). Windows에서는 만드는 ffmpeg를 이 프로세스의 작업 개체(KILL_ON_JOB_CLOSE)에 넣어 서버가 강제로
  꺼져도 남지 않는다. 짧은 ffprobe·시험 인코딩·확인 풀기는 넣지 않는다. Windows가 아니거나 작업 개체를 못 만들면 강제 종료 때 ffmpeg가 남을 수 있다(경고를 남김.
  part 이름과 잠금이 새 사본을 지킨다).
- `Worker`: 작업 폴더마다 뒤의 스레드 하나, `status()` = none | source | building | ready | failed, `close()`가 ffmpeg를 끝낸다. 서버가 멈출 때와 스튜디오가 앱을 내릴 때
  (`ReviewService.close`) 부른다.

### 7.3 구현하며 정한 것 (2026-10-19, 지금도 맞는 것)

- 받아쓰기: faster-whisper의 VAD 이어붙이기는 무음 뒤 첫 낱말 시각을 앞 구간 끝에 붙인다. Silero VAD를 직접 돌려 `MAX_CHUNK_S`(25초) 이하 묶음마다 따로 받아쓴다.
  대사에 나오는 인물 이름만 힌트로 주고(`hotword_names`), 힌트 이름만 되풀이한 묶음은 `words`에서 빼서 `hint_echo`에 둔다.
- 효과음: 창 대신 소리 시작 중심으로 찾는다(종류별 점수 상승 + AudioSet 'Sound effect'(= 효과음) 갈래 + 효과음 트랙 세기 급상승). 'Mechanisms'(= 기계 장치) 같은 포괄 종류(`NO_TRIGGER`)는
  혼자서는 후보를 만들지 않는다. 종류를 못 정한 큰 소리는 '[소리]'(목록 밖 소리)로 에이전트에 넘긴다.
- 계층: PretrainedSED 이름과 ontology.json 이름이 다른 것은 AudioSet mid로 잇고, ontology 뒤에 생긴 종류는 가까운 부모를 손으로 붙였다(`ontology.EXTRA_PARENTS`).
- 음악: AST 분위기 점수는 절대값이 작아 7종 안의 비율로 읽고 절대 합은 `strength`로 둔다. 긴 음악 구간 안에서 음악 트랙 세기가 `CUE_DB` 이상 오르면 '새 음악' 후보.
- '앞 자막에서 이미 알림'에는 '같은 장면' 조건이 없다(같은 종류와 시간만 본다).

### 7.4 결정 하나에 드는 시간 (감사 6-08, 7차)

작가가 하나 고칠 때마다 서버는 `settle`(§4.5)로 다시 배치하고 상태 전체를 다시 만든다. 결과를 바꾸지 않고 시간만 줄이는 장치가 셋 있다.
약속은 **결과가 기억이 없을 때와 같다**는 것이다(같은 바이트).

- 같은 입력의 결과 기억: 순수 계산의 결과를, 그 계산이 읽는 입력을 모두 열쇠에 넣어 기억한다. 열쇠 수가 한도를 넘으면 비우고 다시 모은다. 기억은 프로세스 안에만 있다.

| 무엇 | 열쇠 | 한도 |
|---|---|---|
| `textutil.wrap`(줄바꿈) | 빈칸을 줄인 글, 한도(타입까지), kiwi, tidy(None이나 `style.tidy_line`일 때만 기억), hold | 65,536 |
| `placement._split_memo`(줄 조각 나누기) | 낱말, 낱말 시각의 프레임(`P.fr`), 머리표, 이어지는 표, 노래 여부, `P.key`(fps·설정 전체), `after`, 낱말 첫 프레임마다 그 뒤에 처음 시작하는 줄 | 32,768 |
| `people.display`(이름 표시) | 줄마다 id·person·scene_id·text와 show의 참거짓(`_line_key`), 결정 전체(`_freeze`), 대본 인물(`script_people`) | 32 |
| `people.maybe_same`(같은 사람 후보) | 이름 목록(`names`), 결정 전체(`_freeze`) | 32 |
| `state._read`(작업 폴더 JSON) | 파일 경로. 파일 바이트를 지난번에 푼 바이트와 견주어 같을 때만 그때 푼 값을 준다(시각·크기가 아니라 바이트로 견줌) | 파일 64개 |
| `state._echo_only`(예전 `asr.json`의 힌트 되풀이 판정) | 낱말, 힌트 이름(타입까지) | 16,384 |

  기억한 값은 사본(`marshal`)으로 준다(받은 쪽이 고쳐도 기억은 그대로). 파일 기억의 한도는 개수뿐이고 바이트 한도는 없다(장편 앞 30분 사본의 `settle` 한 번에 8개,
  약 4.2 MB). 끄는 스위치: `cboe.textutil.MEMO`, `cboe.placement.MEMO`, `cboe.people.MEMO`, `cboe.state.MEMO`(기본 True. 끄면 기억 전의 길을 탄다).
  실행 중에 모듈 상수나 `style`의 표기를 바꾸는 코드는 `placement.clear_memo()`·`people.clear_memo()`·`state.clear_memo()`를 불러야 한다.
  계산이 읽는 것을 바꾸면 열쇠도 함께 고친다: `_split`·`_dialogue_parts`의 `text_of` → `_split_memo`의 열쇠, `people._display`가 줄에서 읽는 칸 → `_line_key`,
  `wrap`의 인자 → 그 열쇠.
- 상태 응답(`app.json_reply`): 상태를 돌려주는 끝점 모두와 내보내기가 쓴다. `json.dumps`한 바이트를 그대로 보내고, 본문에 `"_sa`가 있을 때만 FastAPI 방식
  (`jsonable_encoder` → `SafeJSONResponse`)을 따른다. FastAPI가 dict를 통째로 다시 복사하는 몫(장편 앞 30분 사본에서 상태 하나에 약 0.1초)이 없어진다.
  새 끝점이 큰 dict를 돌려주면 `return json_reply(...)`를 쓴다.
- 잠금(`app.WorkLock`): 작업 폴더 잠금(재진입, `with svc.lock:` 꼴만). 이 프로세스에서 잠금이 하나라도 잡혀 있는 동안 순환 쓰레기 수집(`gc`)을 쉬고, 모두 풀리면
  원래대로 돌린다(원래 꺼져 있었으면 그대로). 요청 안의 순환 수집이 0.06~0.23초를 먹었고, 요청 뒤 수집이 찾는 순환 쓰레기는 60여 개뿐이다. 언제 수집하든 결과는 같다.
- 약속을 지키는 시험: `test_placement`의 `test_the_memo_gives_the_draft_computing_everything_again_gives`(무작위 장면 × 성인·어린이 프로필, fps를 바꿔도: 기억 끔 =
  켬(채우며) = 켬(채운 뒤))와 `test_the_split_memo_keys_everything_split_reads`(열쇠 성분마다), `test_review_layer`의
  `test_the_memo_gives_the_drafts_and_states_computing_everything_again_gives`(무작위 작업 폴더 × 서버 결정: 응답·`draft.json`이 바이트까지 같음),
  `test_the_memo_follows_every_file_it_reads`(파일이 바뀌면 낡은 값을 주지 않음), `test_the_label_memo_keys_everything_display_reads`, `test_a_file_read_from_the_memo_is_a_copy`,
  `test_textutil`의 `test_wrap_remembers_only_what_it_would_compute_again`·`test_wrap_does_not_remember_a_tidy_of_its_own_and_forgets_on_clear`,
  `test_server`의 `test_json_reply_sends_the_bytes_fastapi_would_send`·`test_the_work_lock_pauses_the_cycle_collector_while_any_is_held`.
  결과를 바이트로 견줄 때 예전에는 `PYTHONHASHSEED`를 고정해야 했다(칸 없는 말의 `extra`). 이제 그 고르기는 해시를 따르지 않는다(§4.5,
  `test_review_layer`의 `test_the_extra_of_speech_heard_without_a_cell_does_not_follow_the_string_hash`가 씨앗 여섯 개로 `export_check` 전체를 견준다).
- 잰 값(장편 앞 30분 사본, 줄 761개, json·env.npz만. 같은 PC에서 이 장치들 전의 코드(태그 round-g-base)와 번갈아 잼. PC 부하에 따라 흔들린다):
  줄 결정 하나의 서버 시간 중앙값은 uvicorn + 헤드리스 Edge로 약 760 → 266ms, 따로 띄운 서버에 실제 HTTP로 다시 잰 확인에서 802 → 302ms.
  후보 결정 964 → 357ms, 인물 결정 916 → 370ms(같은 확인). 응답은 약 1.27 MB로 그대로다.
  새 프로세스의 첫 `settle`(형태소 분석기 Kiwi를 읽음)은 그대로 몇 초다. 같은 사본에서 첫 `settle` 2.3~3.8초, 서버를 켜고 처음 받는 상태 3~7초(TestClient),
  그 뒤 줄 결정은 0.3~0.5초였다.

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
  위쪽 '작업 목록'으로 돌아가고, 내보내기 창에 '폴더 열기'가 있다. 작업을 내리면(분석 끝·다시 분석·지우기) `ProjectApps.drop`이 그 앱의 `ReviewService.close()`를 불러
  재생용 영상 일꾼과 그 ffmpeg, 프레임 리더를 끝낸다(만드는 중에 지워도 폴더가 잡혀 있지 않다).
- 작업 목록의 집계(`Workspace.summary`의 `counts`·`recs`·`decided`·`total`)는 줄이 빠진 화자 후보(`line_excluded`)를 빼고 센다.
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

## 12. 음원 분리와 분리 트랙 (`stages/separate.py`)

분리 단계와 그 트랙에 기대는 곳을 여기 모았다(다른 절은 이 절을 가리킨다).

- 모델: Bandit v2(Watcharasupat, Wu, Orife 2024) 다국어 체크포인트 `checkpoint-multi.ckpt`(DnR v3로 학습, Zenodo 12701995, 가중치 CC BY-SA 4.0 · 코드 Apache-2.0,
  `paths.BANDIT_CKPT`·`BANDIT_CODE`). 48 kHz 모노, 8초 묶음(`CHUNK_S`)을 주기적 Hann 창으로 2초 걸음(`HOP_S`)씩 겹쳐 더하고, 묶음 4개(`BATCH`)씩 모델에 넣는다.
  결과는 `audio/{speech,music,effects}_48k.wav`와 16 kHz 사본(PCM_16 여섯 개). `run_log.json`의 `separate`: `rms_db`, `resumed_s`(앞 실행에서 이어받은 초, 처음부터면 0).
- 흘려 처리(감사 6-09): 원본을 배치마다 읽어(`soundfile` seek + read) 묶음 하나 길이의 굴러가는 버퍼에 겹쳐 더하고, 묶음 i를 더한 뒤 다음 묶음이 시작하기 전의 표본을
  한 걸음씩 내보낸다. 묶음·배치·창·더하는 차례가 통째 계산과 같아 표본도 같고(가짜 모델 시험, 실사 몇 분 실측), 메모리는 영화 길이와 상관없다
  (137분 산식: 통째 약 9.5 GB → 약 0.04 GB).
- 이어 하기: 끝난 48k 표본을 `audio/<트랙>_48k.f32.part`(raw float32 little-endian, wav 아님)에 쓰고, 셋을 flush한 뒤 `audio/separate.part.json`에
  {`key`: {`format`(`PART_FORMAT`), `mix`: [크기, mtime_ns], `frames`, `samplerate`, `channels`, `chunk`: [8.0, 2.0, 4], `ckpt`: [경로, 크기, mtime_ns], `device`},
  `batches`, `samples`}를 적는다(분리된 소리 `SAVE_EVERY_S` 60초마다). 다음 실행은 기록 길이로 part를 자르고, 기록 앞 배치를 다시 계산해 버퍼만 채운 뒤 이어 간다
  (그 배치의 표본은 이미 디스크에 있다). `key`가 다르거나 part가 짧으면 처음부터. 모든 묶음이 끝나야 여섯 wav를 `<트랙>_<sr>.part.wav`로 쓰고(16 kHz는 soxr 흘림
  리샘플러, `BLOCK_S` 30초씩) 제 이름으로 바꾸며, part 파일과 기록은 마지막에 지운다. 쓰는 동안 디스크는 영상 1초에 약 576 KB(48 kHz float32 세 트랙)다.
- 트랙을 쓰는 곳(§1 `USED_BY["separate"]` = sed, asr):

| 트랙 | 쓰는 곳 |
|---|---|
| `speech` | `asr`(Whisper·Silero VAD, `speech_16k.wav`가 없으면 `mix_16k.wav`를 쓴다), `sed`의 `env.npz` 'speech'(정렬이 속삭임 구간을 찾는 세기) |
| `effects` | `sed` → `sed_effects.npz`(효과음 후보 점수, 정렬의 비명·웃음 점수, 에이전트 `rescan_sound`), `env.npz` 'effects'(효과음 시작·세기 급상승, 정렬의 큰 소리), 에이전트 `describe_sound`의 기본 트랙 |
| `music` | `sed` → `sed_music.npz`(음악 구간), `music`의 AST 분위기·악기(`music_16k.wav`), `env.npz` 'music'(음악 시작·멈춤·새 음악) |
| 원본(`mix`) | 분리 트랙이 아니다: `sed_mix.npz`(사람 목소리·노래 구간·헛소리 의심), 화자 근거(`speaker`), 에이전트의 원본 비교 |

- `env.npz`(`sed.level_db_file`): 세 트랙의 10ms 걸음·40ms 창 세기를 `ENV_BLOCK_S`(60초) 묶음씩 읽어 제곱합 누적을 이어 간다(통째 계산과 값이 같다).
  검수 화면의 소리 세기 곡선(`/api/envelope`, `ENV_TRACKS` speech · effects · music)도 이것이다.
