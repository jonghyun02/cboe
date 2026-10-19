# CBoE — 근거를 제시하는 SDH 자막 검수 보조 도구

발표 덱 `CBoE(5조).pptx`(2026-10-15)의 05 시스템 설계(5.1~5.10)와 03 UI 활용 예시(3.1~3.5)를 구현한 시제품.
영상과 대본을 넣으면 효과음·음악·화자 후보를 찾고, 화면·대사 근거를 모아 포함·제외·확인 필요를 추천하고,
애매한 후보는 재확인 에이전트가 도구로 다시 확인한 뒤, 가독성 규칙대로 자막 초안을 배치한다.
작가는 검수 화면에서 근거를 보고 확정하고 SRT·VTT로 내보낸다. 설계와 데이터 형식은 `DESIGN.md`.

## GUI로 쓰기

바탕화면의 **CBoE** 바로가기(또는 `cboe-gui.cmd`)를 누르면 창이 뜬다. 명령줄 없이 처음부터 끝까지 이 창에서 한다.

1. **새 작업**: 영상 파일을 고르고(필수) 대본 파일을 고른다(권장, 한 줄에 `이름: 대사`). 제목과 처리 구간(비우면 영상 전체),
   LLM 근거·재확인 에이전트 사용, 에이전트로 다시 볼 후보 수, 동시 호출 수, 모델을 정하고 **만들고 분석 시작**.
2. **분석**: 오른쪽에 14단계(덱 5.1의 ① 후보 검출·근거 수집 ② 추천·에이전트 재확인 ③ 초안 배치)가 진행률과 함께 보인다.
   한 번에 한 작업만 분석한다. **분석 멈추기**나 창 닫기로 멈춰도 끝난 단계는 남아 **이어서 분석**하면 남은 단계부터 한다.
   대략 영상 길이의 절반(26분 영상 약 13분, LLM 없이 약 7분).
3. **검수 열기**: 지금까지의 검수 화면(3.1~3.5)이 열린다. 위쪽 **작업 목록**으로 돌아온다. 결정은 고칠 때마다 저장된다.
4. **내보내기**: 검수 화면의 SRT·VTT 저장 → 작업 폴더의 `export`. 저장 창의 **폴더 열기**, 시작 화면의 **내보낸 자막**에서 바로 연다.

- 작업 폴더는 `cboe\work\<작업>`(시작 화면 아래쪽 경로를 누르면 탐색기로 열림). **지우기**는 `work\.trash`로 옮길 뿐이라 되돌릴 수 있다.
- 오른쪽 위 점검 칩: GPU · Claude 로그인 · 모델 파일. Claude 로그인이 없으면 규칙으로만 분석하고, 나중에 로그인해 **이어서 분석**하면 근거가 채워진다.
- `cboe-gui.cmd --browser`: 같은 화면을 기본 브라우저로 연다. 창 기록은 `%USERPROFILE%\.cboe\gui.log`.
- 연구 규칙: 시험 영상(가즈아 도서관·로코양)은 개발 회차 끝까지만 작업을 만들 수 있고(새 작업 창이 끝 시각을 채움), 볼링블링은 막는다.

## 명령줄

```
cd C:\projects\barrier_free\cboe
.venv\Scripts\python.exe -m cboe init work\내작품 --video 영상.mp4 --script 대본.txt [--start 0 --end 290] [--title 제목]
.venv\Scripts\python.exe -m cboe run work\내작품            # 모든 단계 (있는 결과는 건너뜀)
.venv\Scripts\python.exe -m cboe serve work\내작품          # 검수 화면 http://127.0.0.1:8765
.venv\Scripts\python.exe -m cboe export work\내작품 --format srt
```

- `run` 옵션: `--stages a,b`(그 단계만), `--force a,b|all`(다시 계산 — 그 결과를 쓰는 뒷단계도 함께 지우고 다시 돈다),
  `--no-llm`(화면·대사 근거와 에이전트를 LLM 없이 규칙으로만), `--agent-limit 40`(에이전트로 다시 볼 후보 수의 **총합**;
  한도를 올려 다시 `run` 하면 남은 후보부터 이어서 본다), `--llm-model sonnet`, `--jobs 3`(동시 LLM 호출 수).
- LLM 호출이 실패한 후보(화면·대사 근거·에이전트)는 표시해 두었다가 다음 `run`에서 그것만 다시 묻는다.
- 작가 결정(`review.json`)에는 후보 지문(종류@시각, 화자는 줄 id+대사)이 함께 저장된다. 앞 단계를 다시 돌려 후보 번호가 바뀌어도
  결정은 같은 후보에만 붙고, 사라진 후보의 결정은 적용되지 않는다.
- `python -m cboe doctor`: 모델 파일이 제자리에 있는지 확인. `python -m cboe gui [--browser] [--workspace 폴더]`: GUI.
- 대본 형식: `.txt`(`이름: 대사` 한 줄에 한 대사, 이름 없으면 화자 미상), `.csv/.tsv`(speaker,text), `.json`, 또는 시각이 있는 `.srt/.vtt`(그 시각을 그대로 씀).
  대본이 없으면 받아쓰기 줄을 대사로 쓰고 화자 이름은 비운다.

## 단계 (덱 5.1)

| 단계 | 덱 | 하는 일 | 모델 |
|---|---|---|---|
| ingest | 영상·대본 | 처리 구간 소리 뽑기, 대본 읽기 | ffmpeg |
| separate | 5.2 음원 분리 | 대사·음악·효과음 3트랙 | Bandit v2 다국어판 |
| cuts | 5.5 장면 나누기 | 장면 전환 | TransNetV2 |
| sed | 5.3·5.4 | 효과음·원본·음악 트랙 447종 점수(40ms), 트랙 소리 세기 | PretrainedSED (BEATs) |
| asr, align | 5.2 대사 트랙 | 받아쓰기 낱말 시각, 대본 줄 정렬(못 맞춘 줄은 목소리 구간에 배치) | faster-whisper large-v3 |
| sfx | 5.3 효과음 검출 | 소리 첫 프레임, 확실한 만큼 구체적인 종류(AudioSet 계층), 목록 밖 소리 | 규칙 + 계층 |
| music | 5.4 음악 검출 | 구간, 새 음악, 분위기 7종 비율, 악기, 고조·잦아듦 | PretrainedSED 'Music', AST |
| speaker | 5.6 화자 근거 | 줄마다 말하는 얼굴이 보이나 | YuNet + LR-ASD |
| screen | 5.5 화면 근거 | 소리 낸 출처·동작이 보이나, 인물 반응 | Claude (프레임 3장) |
| dialog_ev | 대사 근거 | 앞뒤 대사의 반응·언급, 대사에 이미 적힌 소리 | Claude (글) |
| recommend | 5.7 추천 | 이미 전해지나 → 넣을 근거 있나 → 에이전트 | 규칙 |
| agent | 5.8 재확인 | 애매한 후보만 도구(재검출·소리 설명·프레임·대사 검색)로 다시 확인 | Claude + MCP 도구, Qwen2.5-Omni-3B |
| (placement) | 5.9 초안 배치 | 자리·시간·가독성(16자·2줄·초당 14자·5/6~7초·2프레임) | 규칙 |
| server | 5.10, 3.x 검수 화면 | 근거 보고 확정, 고칠 때마다 배치 다시, SRT·VTT | FastAPI |

## 다른 PC에 설치

Windows · Python 3.11 · NVIDIA GPU(12GB, CUDA 12.8) · ffmpeg(PATH) · Claude Code CLI 로그인(LLM을 쓸 때).

```
git clone https://github.com/jonghyun02/cboe.git && cd cboe
uv venv --python 3.11 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt     # 시험한 그대로: requirements-lock.txt
.venv\Scripts\python.exe tools\setup_third_party.py                      # LR-ASD·Bandit v2·PretrainedSED·TransNetV2 코드
.venv\Scripts\python.exe -m cboe doctor                                   # 빠진 가중치 확인 (weights\ 아래에 둔다)
powershell -ExecutionPolicy Bypass -File tools\make_shortcut.ps1           # 바탕화면 바로가기
```

- 가중치: Bandit v2 다국어판 → `weights\bandit_v2\checkpoint-multi.ckpt`, PretrainedSED → `weights\pretrainedsed\BEATs_strong_1.pt`,
  TransNetV2 → `weights\transnetv2\transnetv2-pytorch-weights.pth`, Qwen2.5-Omni-3B → `weights\qwen25omni3b`(받는 곳은 각 저장소 README).
  LR-ASD 가중치는 저장소에 들어 있고, Whisper large-v3·AST는 처음 쓸 때 Hugging Face에서 받는다. 다른 곳에 두려면 `CBOE_<이름>` 환경 변수(`cboe/paths.py`).
- 이 PC는 예전 데모 폴더(`barrier_free\demo`)의 코드·가중치를 그대로 쓴다(있으면 그쪽이 먼저).
- 시험: `.venv\Scripts\python.exe -m pytest` (GPU 없이 도는 단위·서버·GUI 서버 시험).

## LLM 사용과 주의

- 화면·대사 근거와 에이전트는 이 PC의 Claude Code CLI(`claude -p`)를 부른다. 지금은 사용자 본인 로그인으로 개인 시제품을 돌리는 용도다.
  다른 사람에게 배포하는 제품은 Claude API 키로 호출해야 한다(Agent SDK 문서: 제3자 제품에 claude.ai 로그인 제공은 승인 필요). 호출 형식은 같다.
- 호출마다 `<work>/llm_log.jsonl`에 모델·시간·토큰·비용 추정을 남긴다. 사용자 설정·CLAUDE.md·훅·플러그인·자동 메모리는 호출에 넣지 않는다.
- 영상 프레임(장면 사진)이 Claude로 전송된다. 외부 전송이 안 되는 자료는 `--no-llm`으로 돌린다(그 경우 화면 근거는 '애매'가 되어 확인 필요가 늘어난다).

## 시험 데이터 규칙

`C:\projects\barrier_free\data\sdh3` 웹드라마 사람 SDH 트랙은 개발 회차만 쓴다(가즈아 도서관 0~1579.7초, 로코양 0~1127초).
실제 대본이 없어 `tools/make_test_script.py`가 사람 SDH의 대사 줄로 시험 대본을 만든다.
채점: `tools/eval_sfx.py <work> <srt> [--items]`(효과음 후보·추천 대 사람 효과음 줄 ±1초), `tools/eval_align.py <work> <srt>`(대사 줄 시작 오차).

## 라이선스 (가중치)

Bandit v2 CC BY-SA 4.0 · PretrainedSED MIT · TransNetV2 MIT · Whisper MIT · AST BSD-3 · YuNet MIT · LR-ASD MIT ·
Qwen2.5-Omni-3B Qwen research(비상업, 에이전트의 소리 설명 도구에만 씀).
