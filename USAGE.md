# CBoE 사용법

영상과 그 영화의 촬영 대본을 받아 SDH 자막 초안과 그 근거를 만들고, 작가가 검수 화면에서 정하게 하는 도구다.
처음 설계는 발표 덱 `CBoE(5조).pptx`(2026-10-15) 05 시스템 설계와 03 UI 예시이고, 자막의 기준은 넷플릭스의 세 문서다.

| 기준 문서 | 코드·검사 결과의 짧은 이름 | 이 도구에서 맡는 것 |
|---|---|---|
| Korean Timed Text Style Guide(한국어 Timed Text 지침) II장 SDH. II장에 규칙이 없으면 I장 | `II.n`, `I.n` | 한 줄 16자·2줄·초당 14자, 화자·언어·노래·음악·효과음 표기, 문장부호 |
| Timed Text Style Guide: Subtitle Timing Guidelines(자막 타이밍 지침) | `TG n` | 시작·끝 시각, 장면 전환, 칸 사이 2프레임 |
| Timed Text Style Guide: General Requirements(일반 요건) | `GR n` | 칸 길이 5/6초~7초, TTML 규격 |

- 하는 일: 대본을 장면·지문·대사로 읽고, 영상에서 장면 자리를 먼저 찾은 뒤 대사 줄을 맞춘다. 효과음·음악·화자 이름 표시 후보를 찾고,
  화면·대사·대본 지문에서 근거를 모아 포함 추천·제외 추천·확인 필요를 붙인다. 애매한 후보는 재확인 에이전트가 도구로 다시 본다.
  마지막에 지침의 한도대로 자막 칸을 배치한다.
- 정하는 사람은 작가다. 후보는 추천이 무엇이든 지우지 않는다. **대사의 낱말은 저절로 바꾸지 않는다**(작가가 '들린 대로'를 고르거나 직접 고칠 때만 바뀐다).
  받아쓰기에만 있는 '대본에 없는 말'도 저절로 넣지 않는다.
- 내보내기는 SRT · VTT · TTML. 내보낸 파일은 독립 검사기(`python -m cboe check`)로 다시 본다.
- 설계와 파일 형식은 `DESIGN.md`.

## 설치

Windows 10/11이면 된다. uv·git·ffmpeg가 없으면 `setup.cmd`가 winget으로 깐다(git이 없으면 GitHub의 Code → Download ZIP으로 받아 풀어도 된다).
GPU는 아래 셋 중 하나면 된다. 가중치까지 약 20GB를 받으니 디스크 여유를 확인한다.

```
git clone https://github.com/jonghyun02/cboe.git && cd cboe
setup.cmd                # GPU를 보고 맞는 PyTorch를 골라 .venv에 설치 → 외부 코드 → 가중치(체크섬 확인) → doctor
setup.cmd --shortcut     # + 바탕화면 바로가기
cboe-gui.cmd             # 실행 (uv·ffmpeg를 방금 깔았으면 새 창에서)
```

- LLM 근거·재확인 에이전트를 쓰려면 각자 [Claude Code](https://claude.com/claude-code)를 깔고 터미널에서 `claude`로 로그인한다.
  로그인이 없으면 규칙으로만 분석한다(아래 'LLM 사용과 주의').

| 이 PC의 GPU | PyTorch | 받아쓰기 | 얼굴 찾기(YuNet) | 소리 설명(Qwen2.5-Omni) |
|---|---|---|---|---|
| NVIDIA (CUDA 12.8, 12GB 이상) | `cu128` | faster-whisper, 빔 5 | OpenCV (CPU) | bf16 |
| 인텔 Arc · Core Ultra 내장 Arc (Arc 130V 8GB에서 시험) | `xpu` | 같은 가중치를 OpenVINO로, 그리디 | OpenVINO (GPU) | 언어 모델만 int8 |
| 없음 | `cpu` | faster-whisper int8 | OpenCV (CPU) | bf16 (매우 느림) |

- 모델 단계는 `cboe/util.py`의 `device()`가 고른 장치(CUDA → XPU → CPU 순)에서 돈다. `doctor` 첫 줄이 고른 장치이고,
  `CBOE_DEVICE=cpu`처럼 바꿀 수 있다. `setup.cmd --torch cu128|xpu|cpu`는 PyTorch 판을 강제하고, `--no-weights`는 코드·패키지만 깐다.
- 인텔 GPU에서 달라지는 것:
  - 받아쓰기: faster-whisper(CTranslate2)가 인텔 GPU를 못 써서 같은 large-v3 fp16 가중치를 OpenVINO로 돌린다(`CBOE_ASR_BACKEND`로 바꿀 수 있음).
    빔 검색이 아니라 그리디이고, 낱말 확률 `p`는 비운다. 구간마다 언어 알아내기(`--asr-detect-language`)는 이 경로에서 되지 않는다.
  - 얼굴 찾기: OpenCV 5가 GPU 지정을 무시해서 같은 YuNet을 OpenVINO GPU(fp32)로 돌린다(`stages/speaker.py`의 `OVYuNet`).
  - 소리 설명 모델: GPU 메모리가 `OMNI_BF16_GIB`(11GB)보다 작으면 언어 모델 부분만 int8로 올린다(`agent_tools.py`).
- 가중치(받는 곳·체크섬은 `tools/setup_env.py`): Bandit v2 다국어판 → `weights\bandit_v2\checkpoint-multi.ckpt`(Zenodo), PretrainedSED →
  `weights\pretrainedsed\BEATs_strong_1.pt`(GitHub 릴리스), TransNetV2 → `weights\transnetv2\transnetv2-pytorch-weights.pth`(공식 TF 가중치를
  `convert_weights.py`로 바꾼 것과 텐서가 같은 HF 사본), Qwen2.5-Omni-3B → `weights\qwen25omni3b`. LR-ASD 가중치는 저장소에 들어 있고,
  Whisper·AST는 Hugging Face 캐시에 받는다. 다른 곳에 두려면 `CBOE_<이름>` 환경 변수(`cboe/paths.py`).
- 손으로 깔 때(NVIDIA): `uv venv --python 3.11 .venv` → `uv pip install --python .venv\Scripts\python.exe -r requirements.txt`(시험한 그대로는
  `requirements-lock.txt`) → `.venv\Scripts\python.exe tools\setup_third_party.py` → `.venv\Scripts\python.exe -m cboe doctor`.
- Docker는 쓰지 않는다. Windows의 Docker(WSL2)는 인텔 GPU를 거의 못 쓰고, 데스크톱 창·파일 고르기 창이 컨테이너 안에서 안 뜨며, GPU 종류마다 이미지가 따로 필요하다.
- 저장소 바로 위 폴더에 예전 데모 폴더(`demo`)가 있으면 그 안의 코드·가중치를 먼저 쓴다(`cboe/paths.py`).
- 재생용 영상을 만들고 소리를 뽑는 데 `ffmpeg`·`ffprobe`가 PATH에 있어야 한다(`setup.cmd`가 없으면 winget으로 깐다).

## 받는 대본 형식

대본 읽기는 `cboe/script.py`(확장자를 보고 고름)와 `cboe/screenplay.py`(촬영 대본)이다. 받는 확장자는 `script.SUFFIXES` 하나에 모여 있고,
시작 화면의 파일 고르기 창도 그 목록을 쓴다.

| 확장자 | 꼴 | 읽는 법 |
|---|---|---|
| `.pdf` `.hwp` | 촬영 대본: 장면 머리, 지문, 화자 칸과 그 옆 대사, 괄호 지시, 화면 글자, 전환 | PDF는 글자마다의 자리로 열을 찾는다(pdfminer.six). HWP 5는 olefile로 문단을 읽는다. 화자 열이 없는 PDF는 아래 글 대본처럼 읽는다 |
| `.txt` `.docx` | 내용을 보고 가른다. 화자 칸이 탭이나 넓은 빈칸으로 대사와 떨어져 있거나 이름 줄 아래에 대사가 오면 촬영 대본, 아니면 한 줄에 대사 하나(`이름: 대사`, 이름이 없으면 화자 없음) | Word는 문단, 문단 안 줄바꿈, 표의 `이름 \| 대사` 행. 더빙 대본 꼴(`S#1 [장면]` 머리, `(NA)이름:`, 대사 속 `(한숨)`·`/`, `/`로 시작하는 이어지는 줄)도 읽는다 |
| `.csv` `.tsv` | `speaker`, `text` 열(머리줄 필요) | |
| `.json` | `[{"speaker": …, "text": …}]` 또는 `{"lines": [...]}` | |
| `.srt` `.vtt` | 시각이 있는 대사 자막. `[이름] 대사`, `[이름/영어] 대사`, `(이름) 대사`, `이름: 대사`, `♪ 가사 ♪`, 하이픈 줄은 다른 화자 | 시각을 그대로 쓰고 받아쓰기는 낱말 시각만 다듬는다. `[쾅]`처럼 `[...]`뿐인 줄은 소리라 대사가 아니다 |
| `.hwpx` | 읽지 못한다 | 한글에서 HWP나 PDF로 저장해 넣는다 |

- 글 파일은 UTF-8, UTF-16(BOM 있음), CP949로 읽는다.
- 촬영 대본에서 첫 장면 머리 앞(표지, 배역표)은 대사로 읽지 않는다. 대사의 괄호 지시는 자막 글자에서 빠지고(`raw`에는 남음) 종류별로 나뉜다:
  언어(`(영어로)`), 노래(`(노래)`), 말투(`(웃으며)`), 말하는 사람의 소리(`(한숨)`), 다른 사람의 소리(`by`가 붙음).
  화자 칸의 `(V.O)`·`(NA)`·`(E)`나 괄호로 싼 이름 `(가람)`은 화면 밖·내레이션 같은 출처(`source`)가 된다.
- 대본이 없으면 받아쓰기 줄을 대사로 쓰고 화자 이름은 비운다.
- 분석을 시작하기 전에 `script.summary`가 대본을 점검한다. 대사 줄이 없거나, 글이 조각나 있거나(PDF에서 복사한 글), 화자 자리에 장면 머리가 읽혔거나,
  `MANY_LINES`(30)줄 이상인데 화자 있는 줄이 `FEW_NAMED`(10%)보다 적으면 시작하지 않는다(촬영 대본을 글로 옮긴 파일은 원본 PDF·HWP를 넣으라고 알린다).

지어낸 촬영 대본 한 토막(화자 칸과 대사 사이는 탭)과 읽은 결과:

```
S#12. 버스 정류장 / 밤

멀리서 버스 경적이 울린다.

나래	(웃으며) 왜 이렇게 안 와?
가람(V.O)	(한숨) 곧 온대.
```

| 읽은 것 | `script.json` | 대본 단서(`cues.json`) |
|---|---|---|
| `S#12. 버스 정류장 / 밤` | 장면 `SC001`, 번호 `12`, 곳 `버스 정류장`, 때 `밤` | |
| `멀리서 버스 경적이 울린다.` | 지문(`action`) | 효과음 단서, 화면 밖, 문구 후보 `[빵빵 - 경적]`, `[경적]` |
| `나래 (웃으며) 왜 이렇게 안 와?` | 줄 `L0001`, 글 `왜 이렇게 안 와?`, 말투 `웃으며` | |
| `가람(V.O) (한숨) 곧 온대.` | 줄 `L0002`, 화자 `가람`, 출처 `offscreen`, 소리 `한숨` | 말하는 사람의 소리 단서 `[한숨]` |

## 실행

### 시작 화면 (GUI)

바탕화면의 **CBoE** 바로가기(또는 `cboe-gui.cmd`)를 누르면 창이 뜬다. 명령줄 없이 처음부터 끝까지 이 창에서 한다.

1. **새 작업**: 영상 파일(필수)과 대본 파일(위 표의 확장자)을 고른다. 대본 미리 보기에 점검 결과가 나온다. 제목, 처리 구간(비우면 영상 전체),
   LLM 근거·재확인 에이전트 사용, 에이전트로 다시 볼 후보 수, 동시 호출 수, 모델(sonnet · haiku · opus)을 정하고 **만들고 분석 시작**.
2. **분석**: 오른쪽에 16단계(아래 단계 표의 15단계 + 초안 배치)가 ① 후보 검출·근거 수집 ② 추천·에이전트 재확인 ③ 초안 배치로 묶여 진행률과 함께 보인다.
   한 번에 한 작업만 분석한다. **분석 멈추기**나 창 닫기로 멈춰도 끝난 단계는 남아, **이어서 분석**하면 남은 단계부터 한다.
3. **검수 열기**: 검수 화면이 열린다. 위쪽 **작업 목록**으로 돌아온다. 결정은 고칠 때마다 저장된다.
4. **내보내기**: 검수 화면의 TTML·VTT·SRT 저장 → 작업 폴더의 `export`. 저장 창의 **폴더 열기**, 시작 화면의 **내보낸 자막**에서 바로 연다.

- 작업 폴더는 `cboe\work\<작업>`(`CBOE_WORKSPACE`나 `gui --workspace`로 바꿈). **지우기**는 `work\.trash`로 옮길 뿐이라 되돌릴 수 있다.
- 오른쪽 위 점검 칩: GPU · Claude 로그인 · 모델 파일. Claude 로그인이 없으면 규칙으로만 분석하고, 나중에 로그인해 **이어서 분석**하면 근거가 채워진다.
- 어린이 프로그램(초당 11자)은 시작 화면에서 정할 수 없다. 명령줄 `init --children`으로 정한다.
- `cboe-gui.cmd --browser`: 같은 화면을 기본 브라우저로 연다. 창 기록은 `%USERPROFILE%\.cboe\gui.log`.
- 연구 규칙: 시험 영상(가즈아 도서관·로코양)은 개발 회차 끝까지만 작업을 만들 수 있고(새 작업 창이 끝 시각을 채움), 볼링블링은 막는다.

### 명령줄

`cboe.cmd`는 `.venv\Scripts\python.exe -m cboe`와 같다. 저장소 폴더에서:

```
cboe.cmd init work\내작품 --video 영화.mkv --script 대본.hwp [--start 0 --end 1800] [--title 제목] [--children]
cboe.cmd run work\내작품                       # 모든 단계 (있는 결과는 건너뜀)
cboe.cmd serve work\내작품                     # 검수 화면 http://127.0.0.1:8765
cboe.cmd export work\내작품 --format ttml      # srt · vtt · ttml
cboe.cmd check work\내작품\export\제목.srt --fps 23.976 --cuts work\내작품\cuts.json
cboe.cmd doctor                                # 고른 장치와 모델 파일
cboe.cmd gui [--browser] [--workspace 폴더]
```

| 명령 | 인자 | 뜻 |
|---|---|---|
| `init` | `--video`(필수) `--script` | 영상과 대본. 소리가 없는 영상은 받지 않는다 |
| | `--start` `--end` | 처리 구간 [t0, t1) 초. `--end`가 없거나 영상보다 길면 영상 끝 |
| | `--title` | 제목(내보내는 파일 이름). 없으면 전에 정한 제목, 그것도 없으면 영상 파일 이름 |
| | `--children` · `--profile adult\|children` | 읽기 속도 초당 14자(adult, 처음 값) / 11자(children). 적지 않으면 전에 정한 값 그대로 |
| `run` | `--stages a,b` | 그 단계만 돈다(지워진 단계는 함께) |
| | `--force a,b` · `--force all` | 그 단계와 그 결과를 읽는 뒷단계(`pipeline.USED_BY`)의 결과를 지우고 다시 |
| | `--no-llm` | 화면·대사 근거와 에이전트를 LLM 없이 규칙으로만 |
| | `--agent-limit 40` | 에이전트로 다시 볼 후보 수의 **총합**. 올려서 다시 돌리면 남은 후보부터 본다 |
| | `--llm-model sonnet` · `--jobs 3` | LLM 모델, 동시 호출 수 |
| | `--llm-scope all\|useful` | `useful`: 답이 추천을 바꿀 수 있는 후보만 LLM에 묻는다(나머지는 '안 물음'으로 두고 다음 실행에서 물을 수 있음) |
| | `--asr-detect-language` | 받아쓰기에서 구간마다 언어를 알아낸다(외국어 대사 표시용, faster-whisper일 때만) |
| `serve` | `--port 8765` · `--no-browser` | 검수 화면 서버(이 PC에서만 받음) |
| `export` | `--format srt\|vtt\|ttml` | 파일 경로를 찍고, 지침 검사 요약을 한 줄 찍는다 |
| `check` | 파일 · `--fps` · `--cuts` · `--children` · `--json` · `--all` | 독립 검사기(아래 '내보내기와 독립 검사기') |

- 영상이나 처리 구간을 바꾸면 결과를 저절로 지우지 않는다. 새 작업 폴더를 쓴다. 대본은 바꿔도 된다: 다음 `run`의 대본 읽기 단계가 파일의 SHA-1로
  바뀐 것을 알아채고 다시 읽은 뒤, 대본으로 만든 결과(정렬과 그 아래)를 지우고 같은 실행에서 다시 만든다. 소리·받아쓰기 결과는 남는다.
- LLM 호출이 실패한 후보(화면·대사 근거·에이전트)는 표시해 두었다가 다음 `run`에서 그것만 다시 묻는다.
- 한 작업 폴더는 한 번에 한 곳에서만 분석한다(`.run.lock`). 실행마다 `run_history.json`에 이력이 남는다.

## 단계

`cboe/pipeline.py`의 `STAGES` 차례대로 돈다. 결과 파일이 있으면 건너뛴다. 예외: 대본 읽기는 파일이 바뀌었는지 보고, 대본 단서는 늘 새로 만들고, 에이전트는 남은 대기열을 이어 본다.

| 차례 | 단계 | 하는 일 | 모델·방법 | 만드는 파일 |
|---|---|---|---|---|
| 1 | `ingest` 영상 준비 | 처리 구간의 소리 뽑기 | ffmpeg | `audio/mix_48k.wav`, `mix_16k.wav` |
| 2 | `script` 대본 읽기 | 대본을 장면·지문·대사 문서로. 실행마다 파일이 바뀌었는지 본다 | 규칙 (pdfminer.six, olefile) | `script.json` |
| 3 | `separate` 음원 분리 | 대사·음악·효과음 3트랙 | Bandit v2 다국어판 | `audio/{speech,music,effects}_{48k,16k}.wav` |
| 4 | `cuts` 장면 나누기 | 화면 전환 | TransNetV2 | `cuts.json` |
| 5 | `sed` 소리 검출 | 효과음·원본·음악 트랙 447종 점수(40ms), 트랙 소리 세기 | PretrainedSED (BEATs) | `sed_effects.npz` `sed_mix.npz` `sed_music.npz` `env.npz` |
| 6 | `asr` 받아쓰기 | 대사 트랙의 낱말 시각. 대사에 나오는 인물 이름만 힌트로 준다 | Whisper large-v3 (faster-whisper, 인텔 GPU는 OpenVINO) | `asr.json` |
| 7 | `align` 대본 정렬 | 촬영 대본은 장면 자리를 먼저 찾고 장면 안에서 줄을 맞춘다. 줄 상태, 대본과 들린 말 대조, 대본에 없는 말 | 글자 3-gram 투표, 글자 맞춤(difflib), 목소리 구간 | `dialogue.json`, `scenes.json` |
| 8 | `cues` 대본 단서 | 지문의 소리·음악·침묵과 말하는 사람의 소리, 그 시간 창. 실행마다 새로 만든다 | 규칙 | `cues.json` |
| 9 | `sfx` 효과음 후보 | 소리 첫 프레임, 확실한 만큼 구체적인 종류(AudioSet 계층), 목록 밖 소리 | 규칙 + 계층 | `sfx.json` |
| 10 | `music` 음악 후보 | 음악 시작·새 음악·분위기 바뀜·갑자기 멈춤, 분위기 7종 비율, 악기 | PretrainedSED 'Music', AST | `music.json` |
| 11 | `speaker` 화자 근거 | 시각 있는 줄마다 말하는 얼굴이 보이나. 대본이 화면 밖이라 적은 줄은 모델 없이 '안 보임' | YuNet + LR-ASD | `speaker.json` (멈추면 `speaker.part.json`에서 이어감) |
| 12 | `screen` 화면 근거 | 소리 낸 출처·동작이 보이나, 인물 반응 | Claude (후보마다 프레임 3장) | `screen.json` |
| 13 | `dialog_ev` 대사 근거 | 효과음 앞뒤 6초 대사의 반응·언급·이미 적힌 소리. 장면 머리와 둘레 지문도 함께 준다 | Claude (글) 또는 낱말 규칙 | `dialog_ev.json` |
| 14 | `recommend` 추천 | 대본 단서를 후보에 붙이고, 맞는 후보가 없는 단서는 확인 필요 후보로. 포함·제외·확인 필요 | 규칙 | `items.json` |
| 15 | `agent` 재확인 | 애매한 후보만 도구 6개로 다시 보고 추천을 다시 정함. 실행마다 남은 대기열을 이어 본다 | Claude + MCP 도구, Qwen2.5-Omni-3B(소리 설명) | `agent/<후보>.json`, `agent_results.json`, `agent_done.json` |
| 16 | 초안 배치 | `items.json`과 `dialogue.json`이 있으면 돈다. 작가 결정을 합쳐 칸을 배치 | 규칙, Kiwi 줄바꿈 | `draft.json` |

## 검수 화면

GUI의 **검수 열기** 또는 `serve`로 연다. 화면에서 고칠 때마다 서버가 결정을 `review.json`에 저장하고 배치를 다시 계산해 화면 전체를 새로 준다.

- 위: 작품 제목, 진행, TTML·VTT·SRT 저장.
- 왼쪽: 영상(배치된 칸을 영상 위에 그대로 겹쳐 보인다), 근거 칸(누르면 그 구간을 재생), 고른 것의 대조와 결정 단추.
- 오른쪽: 보기 다섯 개. 탭의 노란 수는 남은 할 일이다.
- 아래 타임라인: 장면 띠(찾은 장면, 모르는 장면, 영화에 없는 장면이 들어갈 자리), 네 줄(대사 칸·화자·효과음·음악), 장면 전환 눈금,
  소리 세기 곡선(`env.npz`가 있을 때, 줄마다 대사·효과음·음악 트랙), 프레임 이동, **+ 넣기**, 확대(15초·60초·전체).

| 보기 | 목록 | 작가가 하는 일 | 노란 수 |
|---|---|---|---|
| 1 후보 | 효과음·음악·화자 후보(시간 순). 거르기: 전체·포함 추천·제외 추천·확인 필요·배치 확인 필요, 종류별. 소리 세기만으로 잡히고 근거가 없는 작은 소리는 접힌다 | 포함·제외 확정, 두 번 눌러 고치기(효과음 문구, 음악 문구 재료, 화자 이름과 바꿀 범위), 자리 없는 소리 처리(제외 · 앞 칸과 바꾸기 · 직접 옮기기), 대본 단서 후보를 시각 범위 안에서 옮기기 | 정하지 않은 확인 필요 후보(화자 포함) |
| 2 대사 | 대본 줄을 장면 머리로 묶고, 자막에 든 줄이 없는 장면은 접는다. 상태: 맞음 · 들린 말이 다름 · 고름 · 못 들음·시각 추정 · 영화에 없음 · 구간 밖 · 작가가 뺌 · 작가가 넣음 · 되살림 · 자리 없음 | 대본 글과 들린 글을 견주어 **대본대로 · 들린 대로 · 직접 고치기**, 넣기·빼기, 시각 정하기와 되살리기, 끊을 자리·한 칸에 두기, 언어·노래 표시, 화자 바꾸기(이 줄 · 이 줄부터 끝까지 · 이 줄 앞까지 · 이 장면 · 이 인물의 모든 줄) | 고르지 않은 다른 줄 + 자리 없는 줄 |
| 3 대본에 없는 말 | 받아쓰기에만 있는 말. 받아쓰기 헛소리 꼴이면 의심 표시, 못 들은 줄과 겹치면 표시 | 대사로 넣기, 겹친 못 들은 줄에 그 시각 쓰기, 무시, 되돌리기 | 정하지 않은 것 |
| 4 인물 | 인물 표: 자막 줄 수, 이름이 밝혀지는 줄, 이름이 일찍 나가는지 | 이름이 밝혀지기 전 표시(이름 그대로 · `[남자]` · `[여자]` · 직접 적기), 이름으로 바뀌는 줄(저절로 · 고른 줄부터 · 끝까지 일반 표시) | 표시를 정하지 않은 인물(자막 줄이 있는 사람) |
| 5 칸 | 배치된 칸마다 지침 검사 결과. 거르기: 의무 위반 · 권고 · 시각 추정 · 겹침 | 칸 재생, 앞뒤 칸, 그 칸을 만든 줄·후보로 가기 | 의무 조항에 걸린 칸 |

단축키(글 칸에 있지 않을 때):

| 키 | 하는 일 |
|---|---|
| `↑` `↓` | 목록에서 위·아래 |
| `←` `→` (또는 `,` `.`) | 한 프레임 앞·뒤. `Shift`+`←` `→`는 1초 |
| `Space` | 재생·멈춤 |
| `1`~`5` | 보기 바꾸기 |
| `N` | 지금 재생 위치에 대사·효과음·음악 새로 넣기 |
| `Enter` | 후보 보기: 추천대로 확정. 글 칸 안에서는 그 칸의 주 단추 |
| `I` | 후보 보기: 포함. 대사 보기: 작가가 뺀 줄 다시 넣기 |
| `E` | 후보 보기: 제외. 대사 보기: 빼기. 대본에 없는 말 보기: 무시 |
| `Esc` | 창 닫기, 글 고치기 그만두기 |

## 내보내기와 독립 검사기

- 검수 화면의 **TTML·VTT·SRT 저장** 또는 `export` → `<작업>\export\<제목>.<srt|vtt|ttml>`. 내보내기 창은 남은 일(확인 필요로 남은 소리 ·
  확인 필요로 남은 화자 줄 · 자리 없는 후보 · 아직 확정하지 않은 제외 추천(묶어서 확정) · 문구가 `[소리]` 그대로인 포함 소리 · 고르지 않은 다른 줄 ·
  정하지 않은 대본에 없는 말 · 이름이 일찍 나가는 인물)과 방금 쓴 파일의 지침 검사 결과를 보인다.
- 확인 필요로 남은 효과음·음악은 자막에 넣지 않는다. 확인 필요로 남은 화자 줄은 이름 없이 나간다.
- TTML은 일반 요건 13절을 따른다: 퍼센트 값, 위·아래 두 영역, 프레임 단위 시각(23.976·29.97·59.94는 `ttp:frameRateMultiplier="1000 1001"`).
- 독립 검사기(`cboe/check.py`)는 결과 파일만 보고 배치와 코드를 나누지 않는다(같이 쓰는 것은 II.2 글자 셈과 `cboe/style.py`의 숫자뿐).
  조항마다 의무(must)와 권고(should)를 나눠 적는다. 받는 것: `.srt` `.vtt` `.ttml` `draft.json`. `--fps`가 없으면 draft·TTML 안의 값,
  그것도 없으면 24fps로 본다. `--cuts cuts.json`을 주면 장면 전환 규칙도 본다. 어린이 프로그램은 `--children`.

| 끝 코드 | 뜻 |
|---|---|
| 0 | 의무 위반 없음 |
| 1 | 의무 위반 있음 |
| 2 | 검사할 수 없음(깨진 파일, TTML이 아닌 XML, 칸 목록이 아닌 JSON, 다른 형식). `--json`이면 `{"ok": false, "error": …}` |

- 검사기가 못 보는 것: 글이 실제로 들린 말인지(II.1), 소리에 자막이 필요했는지와 문구가 가장 나은지, 넷플릭스 글리프 목록(GR 3). 이것은 작가 몫이다.

## 재생용 영상

검수 화면은 영상을 브라우저의 `<video>`로 연다. 브라우저가 못 여는 영상이면(컨테이너가 mp4·mov·m4v·webm이 아님 — mkv도 만든다 —,
영상 코덱이 h264·vp8·vp9·av1이 아님, 소리 코덱이 aac·mp3·opus·vorbis·flac이 아님) 검수 서버가 작업 폴더를 열 때 뒤에서 처리 구간 [t0, t1)만
H.264 + AAC로 바꾼 `<작업>\review.mp4`를 만든다(폭 960 이하, 키프레임 약 1초마다, 인코더는 h264_qsv → h264_nvenc → h264_amf 가운데 되는 것,
하나도 안 되면 libx264). 원본·구간·고른 인코더는 옆의 `review.mp4.json`에 남고, 원본이나 구간이 바뀌면 다시 만든다. 만드는 동안 화면은 정지 화면과
'재생용 영상을 만드는 중 NN%'를 보이다가 다 되면 영상을 다시 불러온다. 못 만들면 까닭을 보이고 정지 화면으로 둔다. 원본을 그대로 주는데 브라우저가 열지 못하면
화면이 한 번 만들기를 부탁한다(`POST /api/media/build`). 재생용 영상의 0초는 영화의 t0초다. 정지 화면(`/api/frame`)은 늘 원본에서 읽는다.
상태는 `/api/state`의 `project.media`와 `GET /api/media`(`state`: none · source · building · ready · failed).

## LLM 사용과 주의

- 화면·대사 근거와 에이전트는 이 PC의 Claude Code CLI(`claude -p`)를 부른다. 지금은 사용자 본인 로그인으로 개인 시제품을 돌리는 용도다.
  다른 사람에게 배포하는 제품은 Claude API 키로 호출해야 한다(Agent SDK 문서: 제3자 제품에 claude.ai 로그인 제공은 승인 필요). 호출 형식은 같다.
- 호출마다 `<work>/llm_log.jsonl`에 모델·시간·토큰·비용 추정을 남긴다. 사용자 설정·CLAUDE.md·훅·플러그인·자동 메모리는 호출에 넣지 않는다.
- 영상 프레임(장면 사진)이 Claude로 전송된다. 대사 근거에는 소리 앞뒤의 대사 글과, 장면 시각을 알면 장면 머리와 둘레 지문도 함께 간다.
  외부 전송이 안 되는 자료는 `--no-llm`으로 돌린다(그 경우 화면 근거는 '애매'가 되어 확인 필요가 늘어난다).
- 검수 화면은 LLM을 부르지 않는다(작가가 후보를 열 때 기다리지 않게).

## 알려진 한계

정답 자료가 없어 재지 못한 것:

| 무엇 | 사정 |
|---|---|
| 화자 판정(말하는 얼굴이 보이나)의 정확도 | 실사 영화의 정답이 없다. 문턱 `VISIBLE_MIN_RATIO`·`VISIBLE_MIN_SCORE`(`stages/speaker.py`)는 애니메이션 한 편의 점수 분포로 정했고 실사 영화로 확인하지 않았다 |
| 효과음·음악 후보가 맞는지 | 진짜 영화의 사람 SDH 자막이 없다. `tools/eval_sfx.py`는 시험 웹드라마의 사람 SDH로만 잰다 |
| 대본 단서가 실제 SDH 자막에 들어갈 소리와 겹치는 정도, 단서의 시간 창이 소리를 맞게 잡는지 | 잰 적이 없다(`cboe/cues.py` 설명). 그래서 대본 단서만으로는 아무것도 포함 추천하지 않는다 |
| 받아쓰기 글자 오류율 | 사람이 고친 대사 자막이 없다. 대본과 들린 말 대조(`differs`)는 받아쓰기 글에 기댄다 |
| 촬영 대본과 개봉본이 다른 정도 | 장면 먼저 정렬과 '영화에 없음' 판정이 여기에 기댄다 |

영화 한 편(또는 한두 실행)으로만 정한 숫자:

| 상수 | 곳 | 정한 바탕 |
|---|---|---|
| `STRONG` `MOVED` `WEAK` `MIN_HITS` `MIN_HITS_WEAK` `MOVED_HITS` `SPAN_PER_CHAR` `CORE_SHARE` (장면 찾기) | `cboe/scenes.py` | 장편 영화 한 편. 두 번째 영화로 확인하지 않았다 |
| `SCENE_SEC_PER_CHAR` `SCENE_SLACK` (못 들은 줄을 자기 장면 가까이) | `cboe/stages/align.py` | 장편 영화 한 편 |
| `NOISE_SPEECH` `NOISE_SPEECH_UNDER_MUSIC` `NOISE_MUSIC` `NOISE_VAD` (받아쓰기 헛소리 의심) | `cboe/stages/align.py` | 장편 한 편과 애니메이션 한 편. `NOISE_P`는 재지 않았다 |
| `GUESS_STEP_S` (단서의 어림 시각) | `cboe/cues.py` | 장편 영화 한 편의 앞 30분 |
| `OUT_RUN_LINES` (구간 밖 줄 묶음) | `cboe/stages/align.py` | 자료로 맞추지 않았다 |
| `SILENT_DB` `START_*` `STOP_*` (음악 시작·멈춤) | `cboe/stages/music.py` | 저장된 실행 몇 개의 소리 세기 곡선. 실사 영화로 확인하지 않았다 |
| `ECHO_MIN_WORDS` `ECHO_MIN_RUN` (힌트 이름 되풀이), `LANG_MIN_PROB` | `cboe/stages/asr.py` | 21분 실행 한 번 / 확인하지 않았다 |

손대지 않은 것:

- 대본의 출처(속마음·영상·전화 등)는 화자 표시에 아직 붙지 않는다. 배치는 `label_extra`를 `[이름/속마음]`으로 쓸 줄 알지만 `state.placement_inputs`가 채우지 않는다.
- 같은 목소리 범위(`people.SCOPES`의 `voice`)는 목소리 묶기가 없어 그 줄 하나만 바꾼다. 검수 화면은 이 범위를 내놓지 않는다.
- 화자 후보의 결정 지문은 `speaker@<줄 id>:<글>`이다. 대본을 다시 읽어 줄 id가 밀리면 화자 후보의 넣기·빼기 결정이 같은 id의 다른 줄로 갈 수 있다(대사 줄 결정은 문맥으로 맞춰 읽어 이 문제가 없다).
- 시각 추정 줄을 다른 줄 사이 빈틈에 다시 펼 때 읽기 시간이 모자라면 칸마다 5/6초만 준다. 초당 글자 수 위반은 검사기 결과에 남는다.
- `.hwpx`는 읽지 못한다. 넷플릭스가 SRT를 받는지와 글리프 목록은 확인하지 않았다.

## 시험과 채점 도구

- 시험: `.venv\Scripts\python.exe -m pytest -q` (GPU 없이 도는 단위·서버·GUI 서버 시험). 예시 작업 폴더(`work/sample`)·시험 영상·PretrainedSED 코드가
  없는 사본에서는 몇 개가 건너뛰어진다. 진짜 영화 대본의 글은 저장소에 넣지 않는다(시험 자료도 지어낸 글로).
- `tools/inspect_stages.py <작업> [--at 2:33] [--item S0017] [--stage sfx]`: 단계별 결과를 파일만 읽어 보인다(어느 단계에서 틀어졌는지 찾을 때).
- `tools/summarize_run.py <작업>`: 단계 시간·후보·추천·에이전트·LLM 사용을 한 화면에.
- 시험 데이터 규칙: `C:\projects\barrier_free\data\sdh3` 웹드라마 사람 SDH 트랙은 개발 회차만 쓴다(가즈아 도서관 0~1579.7초, 로코양 0~1127초, `cboe/guard.py`).
  이 웹드라마에는 촬영 대본이 없어 `tools/make_test_script.py`가 사람 SDH의 대사 줄로 시험 대본을 만든다.
  채점: `tools/eval_sfx.py <작업> <srt> [--items]`(효과음 후보·추천 대 사람 효과음 줄), `tools/eval_align.py <작업> <srt>`(대사 줄 시작 오차, 시각 없는 줄은 세기만 함).

## 라이선스 (가중치)

Bandit v2 CC BY-SA 4.0 · PretrainedSED MIT · TransNetV2 MIT · Whisper MIT · AST BSD-3 · YuNet MIT · LR-ASD MIT ·
Qwen2.5-Omni-3B Qwen research(비상업, 에이전트의 소리 설명 도구에만 씀).
