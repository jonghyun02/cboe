# -*- coding: utf-8 -*-
"""끝에서 끝: a made-up shooting script and hand-made model results, run through the real code.

Every stage that needs a model (받아쓰기, 장면 나누기, 소리 검출, 음악 검출, 말하는 얼굴) has its result
written by hand into the work folder, in the format the stage writes; the face model of the speaker
stage is the only piece replaced in code (``FakeFaces``). Everything else is the real code:
``pipeline.run`` (대본 읽기 → 정렬 → 대본 단서 → 근거 → 추천 → 배치) without an LLM, the review layer
(``state.Project``), the export (srt · vtt · ttml), the independent guide check (``cboe.check``),
the export read back as a timed script, and the review server. The seams between the stages —
one stage's file read by the next — are what is tested here.

The script, the names and the song are made up for this test. No model, no LLM, no network, no
video and no ffmpeg run.
"""
from __future__ import annotations

import shutil
import wave
from pathlib import Path

import numpy as np
import pytest

from cboe import check, phrases, pipeline, script
from cboe.stages import align as align_stage
from cboe.stages import speaker as speaker_stage
from cboe.state import Project
from cboe.util import read_json, write_json

T1 = 100.0                          # the processed part is the whole (made-up) film: 0–100 s
FPS = 24.0
CUTS = [29.0, 57.0]                 # shot changes where the scenes change (scene 3 is not in the film)

# ------------------------------------------------------------------ the shooting script
# Tab layout: speaker cell and dialogue split by a tab, numbered scene headings, action lines.
SCRIPT = "\n".join([
    "지어낸 영화 촬영 대본",
    "",
    "1. 지어낸 골목 / 밤",
    "멀리서 개 짖는 소리가 들려온다.",
    "\t가온\t(작게) 여기가 정말 그 집이 맞는 거지?",
    "\t나래\t응, 파란 대문 집이라고 했어.",
    "쾅! 하고 대문이 닫힌다.",
    "\t보람\t거기 누구세요? 이 시간에 무슨 일이에요?",
    "\t가온\t(한숨) 늦어서 미안해요.",
    "",
    "2. 지어낸 다방 / 낮",
    "라디오에서 가수 별무리의 ‘여름 정거장’이 흘러나온다.",
    "\t도윤(V.O)\t그해 여름 우리는 매일 이 다방에 모였다.",
    "\t나래\t보람아, 이리 와서 같이 앉자.",
    "\t보람\t(노래) 여름 정거장에 비가 내리면",
    "\t가온\t(영) 커피 두 잔 주세요.",
    "음악이 뚝 끊기고, 다방에 긴 침묵이 흐른다.",
    "\t나래\t다들 왜 갑자기 조용해진 거야?",
    "",
    "3. 지어낸 옥상 / 새벽",
    "총소리가 크게 울린다.",
    "\t가온\t모두 엎드려, 지금 당장 숨어야 해!",
    "\t나래\t저기 옥상 끝에 누군가 서 있었어.",
    "",
    "4. 지어낸 정류장 / 아침",
    "\t보람\t버스가 벌써 떠나 버렸으면 어떡하지?",
    "멀리서 버스 경적이 울린다.",
    "\t가온\t걱정 마, 다음 차는 금방 올 거야.",
    "\t가온\t(속삭이며) 조용히 따라와.",
    "\t나래\t누구야?",
    "\t보람\t나야.",
    "",
])

# ------------------------------------------------------------------ what the film has (받아쓰기)
# (text heard, start, end). The film is scenes 1, 2 and 4; scene 3 was cut. L0004 is said a little
# differently from the script, L0008 is said in English, L0014 is whispered (the ASR heard nothing),
# and between L0012 and L0013 someone says a line that is in no script line.
HEARD = [
    ("여기가 정말 그 집이 맞는 거지?", 4.0, 6.2),          # L0001
    ("응, 파란 대문 집이라고 했어.", 7.0, 8.9),             # L0002
    ("거기 누구세요? 이 시간에 무슨 일이에요?", 12.0, 15.0),  # L0003
    ("늦어서 죄송해요.", 16.5, 17.8),                       # L0004: '미안해요' in the script
    ("그해 여름 우리는 매일 이 다방에 모였다.", 32.0, 35.5),   # L0005
    ("보람아, 이리 와서 같이 앉자.", 37.0, 39.0),            # L0006
    ("여름 정거장에 비가 내리면", 40.0, 43.0),               # L0007 (sung)
    ("Two coffees, please.", 44.5, 46.0),                  # L0008: the script has the Korean words
    ("다들 왜 갑자기 조용해진 거야?", 50.0, 52.0),           # L0009
    ("버스가 벌써 떠나 버렸으면 어떡하지?", 62.0, 64.5),       # L0012
    ("저기 버스 온다.", 66.0, 67.3),                         # in no script line
    ("걱정 마, 다음 차는 금방 올 거야.", 69.0, 71.5),         # L0013
    ("누구야?", 80.0, 80.3),                                # L0015
    ("나야.", 80.6, 80.9),                                  # L0016
]
WHISPER = (73.0, 74.2)              # L0014: voice the ASR did not transcribe (VAD only)


def heard_words(text: str, s: float, e: float) -> list[dict]:
    """Words of one stretch of speech, the time shared out by letters, as Whisper writes them."""
    toks = text.split()
    lens = [max(len(align_stage.norm_chars(t)), 1) for t in toks]
    out, t = [], s
    for tok, n in zip(toks, lens):
        d = (e - s) * n / sum(lens)
        out.append({"w": tok, "s": round(t, 3), "e": round(t + d - 0.02, 3), "p": None})
        t += d
    return out


def asr_json() -> dict:
    words = [w for text, s, e in HEARD for w in heard_words(text, s, e)]
    vad = sorted([[s, e] for _, s, e in HEARD] + [list(WHISPER)])
    return {"language": "ko", "words": words, "vad": vad,
            "chunks": [{"start": 0.0, "end": T1, "language": "ko", "detected": None, "words": len(words)}],
            "hint_echo": [], "hotwords": None}


# ------------------------------------------------------------------ sounds the detectors found
def sfx_candidate(iid: str, cls: str, onset: float, t1: float, score: float, jump: float) -> dict:
    """A candidate as stages/sfx.py writes it (the phrases from the same table it uses)."""
    ph = phrases.phrases(cls)
    opts = [{"text": ph["long"], "short": ph["short"], "source": "detected", "note": "검출 종류"}]
    if ph["ono"]:
        opts.append({"text": ph["ono"], "short": ph["short"], "source": "detected", "note": "의성어 포함"})
    return {"id": iid, "kind": "sfx", "t0": round(onset - 0.04, 3), "t1": t1, "onset": onset,
            "category": phrases.category(cls),
            "label": {"class": cls, "ko": phrases.ko(cls), "score": score, "mix_score": round(score * 0.8, 3),
                      "path": [cls], "path_ko": [phrases.ko(cls)],
                      "top": [{"class": cls, "ko": phrases.ko(cls), "score": score}], "competing": [],
                      "unknown": False, "unknown_by": None, "confident": True, "sound_effect_score": 0.05},
            "level_db": -20.0, "level_jump_db": jump, "onset_by": ["level", "sed"], "minor": False,
            "phrases": opts, "phrase": opts[0]["text"], "short": opts[0]["short"], "flags": []}


SFX = [
    sfx_candidate("S0001", "Bark", 1.5, 3.0, 0.62, 8.0),       # the dog the script writes before L0001
    sfx_candidate("S0002", "Slam", 10.0, 10.6, 0.71, 18.0),    # the gate slammed between L0002 and L0003
    sfx_candidate("S0003", "Sigh", 16.2, 16.6, 0.55, 6.0),     # L0004's '(한숨)', 0.3 s before the line
]


def mood(key: str, score: float) -> dict:
    return {"key": key, "ko": phrases.MOODS[key], "score": score, "raw": round(score / 20, 4)}


def music_item(kind: str, onset: float, seg: list[float], moods=(), **kw) -> dict:
    it = {"kind": kind, "onset": onset, "segment": seg, "moods": list(moods),
          "mood_strength": 0.05 if moods else 0.0, "instruments": [], "slope_db_s": 0.0, "change": None,
          "music_score": 0.8}
    it.update(kw)
    return it


MUSIC = {"segments": [[30.0, 47.5], [75.0, 88.0]], "silent_segments": [], "params": {},
         "items": [
             # the radio song of scene 2: a clear mood
             music_item("start", 30.0, [30.0, 47.5], [mood("Happy music", 0.62), mood("Tender music", 0.2),
                                                      mood("Sad music", 0.1)], onset_sed=30.0),
             # it breaks off where the script writes the silence
             music_item("stop", 47.5, [30.0, 47.5], drop_db=31.0),
             # music in scene 4 whose mood is not clear, fading out at 88 s (no stop found)
             music_item("start", 75.0, [75.0, 88.0], [mood("Sad music", 0.31), mood("Tender music", 0.28),
                                                      mood("Happy music", 0.2)], onset_sed=75.0),
         ]}

# ------------------------------------------------------------------ faces (말하는 얼굴)
# the lines whose speaker talks on screen (by their words: line ids move when the script changes);
# every other timed line has no face at all, and L0005 never reaches the model (the script says V.O)
SEEN = {"여기가 정말 그 집이 맞는 거지?", "응, 파란 대문 집이라고 했어.", "늦어서 미안해요.",     # L0001 L0002 L0004
        "보람아, 이리 와서 같이 앉자.", "여름 정거장에 비가 내리면", "다들 왜 갑자기 조용해진 거야?",   # L0006 L0007 L0009
        "걱정 마, 다음 차는 금방 올 거야.", "누구야?", "나야."}                                    # L0013 L0015 L0016


class FakeFaces:
    """Stands in for YuNet + LR-ASD (``speaker.Analyser``): a speaking face for the lines in SEEN,
    no face for the others."""

    asked: list[str] = []

    def __init__(self, p):
        pass

    def tracks(self, ln, cuts):
        FakeFaces.asked.append(ln["id"])
        s0 = float(ln["start"])
        win = [round(s0, 3), round(s0 + 1.0, 3)]
        if ln["text"] in SEEN:
            return [{"size": 120.0, "speaking_frames": 20, "frames": 25, "mean_score": 1.1, "max_score": 2.0,
                     "box": [10.0, 10.0, 130.0, 130.0]}], win
        return [], win

    def close(self):
        pass


# ------------------------------------------------------------------ the work folder
SED_CLASSES = sorted(set(align_stage.VOICE) | set(align_stage.VOCAL_FX) | {"Music"})


def _silent_wav(path: Path, sr: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"\0\0" * (sr // 10))


def make_work(work: Path) -> Path:
    """A work folder in which every model stage has its result: project.json, the script file,
    the audio files (only looked for by name: silent stand-ins), the sound scores (all zero: the
    voice of the film is asr.json's VAD), asr.json, cuts.json, sfx.json, music.json."""
    work.mkdir(parents=True, exist_ok=True)
    (work / "script.txt").write_text(SCRIPT, encoding="utf-8")
    write_json(work / "project.json", {"title": "여름 정거장 (지어낸 영화)", "video": str(work / "film.mkv"),
                                       "script": str(work / "script.txt"), "t0": 0.0, "t1": T1, "fps": FPS,
                                       "duration": T1, "width": 640, "height": 272})
    for stem in ("mix", "speech", "music", "effects"):
        for sr in (48000, 16000):
            _silent_wav(work / "audio" / f"{stem}_{sr // 1000}k.wav", sr)
    n = int(T1 * 25)
    for track in ("effects", "mix", "music"):
        np.savez_compressed(work / f"sed_{track}.npz", probs=np.zeros((n, len(SED_CLASSES)), np.float16), fps=25,
                            classes=np.array(SED_CLASSES), t0=0.0)
    flat = np.full(int(T1 / 0.01), -80.0, np.float32)
    np.savez_compressed(work / "env.npz", hop=np.float32(0.01), t0=np.float32(0.0), effects=flat, music=flat,
                        speech=flat)
    write_json(work / "asr.json", asr_json())
    write_json(work / "cuts.json", {"fps": FPS, "cuts": CUTS, "shots": [[0.0, 29.0], [29.0, 57.0], [57.0, T1]],
                                    "model": "TransNetV2"})
    write_json(work / "sfx.json", {"candidates": SFX, "params": {}})
    write_json(work / "music.json", MUSIC)
    return work


def run_pipeline(work: Path, monkeypatch) -> dict:
    FakeFaces.asked = []
    monkeypatch.setattr(speaker_stage, "Analyser", FakeFaces)
    return pipeline.run(work, cfg={"llm": False})


def copy_work(src: Path, dst: Path) -> Path:
    """A copy of a work folder whose project.json points at its own script and video."""
    shutil.copytree(src, dst)
    meta = read_json(dst / "project.json")
    meta.update(script=str(dst / "script.txt"), video=str(dst / "film.mkv"))
    write_json(dst / "project.json", meta)
    return dst


@pytest.fixture(scope="module")
def ran(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    try:
        work = make_work(tmp_path_factory.mktemp("e2e") / "work")
        log = run_pipeline(work, mp)
        asked = list(FakeFaces.asked)
    finally:
        mp.undo()
    return work, log, asked


# the writer's decisions (검수 층), made on a copy of the analysed folder
def decide(p: Project) -> None:
    p.set_line("L0009", include=False)                         # a line taken out
    p.set_line("L0004", text_from="heard")                     # what was heard instead of the script's words
    p.set_extra("X0001", "line", name="나래")                   # the speech in no script line becomes a line
    p.set_people(["보람"], mode="generic", before="여자")        # a generic label until the name is told
    p.add("music", start=88.0, end=90.0, phrase="음악이 멈춘다")   # the scene-4 music fades out at 88 s
    p.set_decision("S0003", decision="include")                # 확인 필요 -> in: 가온's sigh
    p.set_decision("M0002", decision="include")                # 확인 필요 -> in: the music breaks off
    p.set_decision("P0008", decision="include")                # 확인 필요 -> in: name the English line
    p.set_decision("C0007", decision="exclude")                # 확인 필요 -> out: the horn the script wrote
    p.set_decision("M0001", phrase="[별무리의 '여름 정거장']")     # the song title the script gave


@pytest.fixture(scope="module")
def decided(ran, tmp_path_factory):
    work = copy_work(ran[0], tmp_path_factory.mktemp("e2e") / "decided")
    p = Project(work)
    decide(p)
    draft = p.recompute()
    exports = {fmt: p.export(fmt, report=True) for fmt in ("srt", "vtt", "ttml")}
    return work, p, draft, exports


def by_id(rows: list[dict]) -> dict:
    return {r["id"]: r for r in rows}


# ================================================================== 1. the run
def test_the_pipeline_runs_every_stage_without_a_model_or_an_llm(ran):
    work, log, asked = ran
    assert list(log) == pipeline.STAGES + ["placement"]
    # every model stage found its result in the folder and did not run
    for stage in ("ingest", "separate", "cuts", "asr", "sfx", "music"):
        assert log[stage].get("skipped") is True, stage
    # the script was read once, with nothing below it to clear yet
    assert {k: log["script"][k] for k in ("lines", "characters", "scenes", "format", "cleared")} == {
        "lines": 16, "characters": 4, "scenes": 4, "format": "screenplay", "cleared": []}
    # scenes first: 3 scenes of the film found, the cut scene 3 absent; 12 lines heard as written,
    # L0004 heard differently, L0014 whispered (not heard), L0010 · L0011 of scene 3 absent
    al = log["align"]
    assert al["mode"] == "scenes" and al["lines"] == 16 and al["extras"] == 1
    assert al["status"] == {"ok": 12, "differs": 1, "unheard": 1, "absent": 2}
    assert al["scenes"] == {"found": 3, "absent": 1}
    # the face model was asked about every timed line but the V.O line (the script says off screen)
    assert asked == ["L0001", "L0002", "L0003", "L0004", "L0006", "L0007", "L0008", "L0009", "L0012", "L0013",
                     "L0014", "L0015", "L0016"]
    assert log["speaker"]["by_script"] == 1 and log["speaker"]["no_time"] == 2
    # without an LLM: the screen not looked at, the dialogue read by the keyword rule, no re-check
    assert {k: v for k, v in log["screen"].items() if k != "seconds"} == {"llm": False, "items": 3}
    assert log["dialog_ev"]["llm"] is False and log["dialog_ev"]["relations"] == {"none": 2, "reaction": 1}
    assert log["agent"]["ran"] == 0
    # 16 cells: 13 lines that are in (L0015 · L0016 share one) + the 3 sounds recommended
    assert log["placement"]["cells"] == 16 and log["placement"]["no_room"] == 0
    for f in ("script.json", "dialogue.json", "scenes.json", "cues.json", "speaker.json", "screen.json",
              "dialog_ev.json", "items.json", "draft.json"):
        assert (work / f).is_file(), f


def test_the_script_is_read_as_a_shooting_script(ran):
    doc = read_json(ran[0] / "script.json")
    assert doc["layout"] == "tab" and [s["heading"] for s in doc["scenes"]] == [
        "1. 지어낸 골목 / 밤", "2. 지어낸 다방 / 낮", "3. 지어낸 옥상 / 새벽", "4. 지어낸 정류장 / 아침"]
    ln = by_id(doc["lines"])
    assert len(ln) == 16 and doc["characters"] == ["가온", "나래", "보람", "도윤"]
    assert (ln["L0001"]["text"], ln["L0001"]["delivery"]) == ("여기가 정말 그 집이 맞는 거지?", ["작게"])   # (작게)
    assert (ln["L0004"]["text"], ln["L0004"]["vocal"]) == ("늦어서 미안해요.", ["한숨"])                   # (한숨)
    assert (ln["L0005"]["speaker"], ln["L0005"]["source"]) == ("도윤", "offscreen")                     # 도윤(V.O)
    assert ln["L0007"]["sung"] and ln["L0007"]["text"] == "여름 정거장에 비가 내리면"                       # (노래)
    assert (ln["L0008"]["language"], ln["L0008"]["text"]) == ("영어", "커피 두 잔 주세요.")              # (영)
    assert (ln["L0014"]["delivery"], ln["L0014"]["text"]) == (["속삭이며"], "조용히 따라와.")
    assert [ln[f"L{k:04d}"]["scene_id"] for k in (1, 5, 10, 12)] == ["SC001", "SC002", "SC003", "SC004"]
    assert doc["people"][3] == {"id": "P04", "name": "도윤", "cues": ["도윤(V.O)"]}


# ================================================================== 2. alignment
def test_line_states_and_the_speech_in_no_script_line(ran):
    work = ran[0]
    dj = read_json(work / "dialogue.json")
    ln = by_id(dj["lines"])
    status = {k: v["status"] for k, v in ln.items()}
    assert [k for k, s in status.items() if s == "ok"] == [
        "L0001", "L0002", "L0003", "L0005", "L0006", "L0007", "L0008", "L0009", "L0012", "L0013", "L0015", "L0016"]
    # said '죄송해요' where the script has '미안해요': matched on the other letters, the words kept
    assert status["L0004"] == "differs" and ln["L0004"]["asr_text"] == "늦어서 죄송해요."
    assert ln["L0004"]["text"] == "늦어서 미안해요." and (ln["L0004"]["start"], ln["L0004"]["end"]) == (16.5, 17.78)
    # the English line cannot match the Korean words: placed on the voice between its neighbours
    # (the VAD region 44.5–46.0 s), 'ok' with the note 'foreign', its time a guess
    assert (ln["L0008"]["note"], ln["L0008"]["timing"], ln["L0008"]["start"], ln["L0008"]["end"]) == (
        "foreign", "voice", 44.5, 46.0)
    # the whisper the ASR missed: on the only voice between L0013 and L0015 (73.0–74.2 s)
    assert (status["L0014"], ln["L0014"]["timing"], ln["L0014"]["start"], ln["L0014"]["end"]) == (
        "unheard", "voice", 73.0, 74.2)
    # scene 3 is not in the film: no time, the guess is the stretch between L0009 and L0012
    # (L0009's last word ends 51.98 s, L0012 starts 62.0 s; 0.05 s off each)
    for lid in ("L0010", "L0011"):
        assert (status[lid], ln[lid]["start"], ln[lid]["guess"]) == ("absent", None, [52.03, 61.95])
    # a heard line's words are where the ASR put them (first word 4.0 s, last word ends 6.18 s)
    assert (ln["L0001"]["start"], ln["L0001"]["end"], ln["L0001"]["timing"]) == (4.0, 6.18, "asr")
    # '저기 버스 온다' is in no script line; the English words lie inside L0008 and are no extra
    (x,) = dj["extras"]
    assert (x["id"], x["text"], x["start"], x["end"], x["scene_id"]) == ("X0001", "저기 버스 온다.", 66.0, 67.28, "SC004")
    assert not x.get("suspect") and not x.get("short")
    sc = by_id(read_json(work / "scenes.json")["scenes"])
    assert {k: v["status"] for k, v in sc.items()} == {"SC001": "found", "SC002": "found", "SC003": "absent",
                                                        "SC004": "found"}
    assert (sc["SC002"]["start"], sc["SC002"]["end"]) == (32.0, 51.98)          # L0005 start .. L0009 end
    assert sc["SC003"]["lines"] == ["L0010", "L0011"] and sc["SC003"]["start"] is None


# ================================================================== 3. script cues
def test_script_cues_get_windows_from_the_aligned_lines(ran):
    cues = by_id(read_json(ran[0] / "cues.json")["cues"])
    assert [(k, c["type"], c["scene_id"]) for k, c in cues.items()] == [
        ("C0001", "sfx", "SC001"), ("C0002", "sfx", "SC001"), ("C0003", "vocal", "SC001"),
        ("C0004", "music", "SC002"), ("C0005", "silence", "SC002"), ("C0006", "sfx", "SC003"),
        ("C0007", "sfx", "SC004")]
    win = {k: c["window"] for k, c in cues.items()}
    # the dog before the first line: from the start of the processed part to L0001
    assert win["C0001"] == [0.0, 4.0] and cues["C0001"]["window_by"] == ["range", "line"]
    assert win["C0002"] == [8.88, 12.0]                       # between L0002's end and L0003
    assert win["C0003"] == [16.5, 17.78] and cues["C0003"]["line_id"] == "L0004"   # 가온's own (한숨)
    # the song before scene 2's first line: back to the last timed line of scene 1
    assert win["C0004"] == [17.78, 32.0] and cues["C0004"]["window_by"] == ["other_scene", "line"]
    assert cues["C0004"]["songs"] == [{"artist": "별무리", "title": "여름 정거장"}]
    # the silence after the English line: its window rests on that line's guessed time
    assert win["C0005"] == [46.0, 50.0] and cues["C0005"]["window_estimated"] and cues["C0005"]["music_stop"]
    assert win["C0006"] is None                               # the gunshot of the scene the film does not have
    assert win["C0007"] == [64.48, 69.0]
    assert all(w[1] > w[0] for w in win.values() if w)         # no window of 0 s
    # the captions they suggest, in the guide's forms (II.9 [의성어 - 출처], II.7 the song title)
    assert cues["C0001"]["phrases"][0] == "[멍멍 - 개]"
    assert "[별무리의 '여름 정거장']" in cues["C0004"]["phrases"]
    assert cues["C0005"]["phrases"][0] == "[음악이 멈춘다]"


# ================================================================== 4. recommendations
def test_recommendations_with_their_reasons(ran):
    items = by_id(read_json(ran[0] / "items.json")["items"])
    got = {k: (it["rec"], it["reason"]) for k, it in items.items()}
    assert got == {
        # no LLM: the screen was not looked at; the cue names the dog; the first background sound of SC001
        "S0001": ("include", "화면 확인 안 함 · 대본 지문에 적힌 소리 · 장면 첫 배경 소리"),
        # L0003 '누구세요' starts 2 s after it (keyword rule), the cue names the gate, an 18 dB jump
        "S0002": ("include", "화면 확인 안 함 · 직후 \"거기 누구세요? 이 시간에\" · 대본 지문에 적힌 소리 · 갑자기 큰 소리"),
        # the speaker's own (한숨) is evidence only: nothing else speaks for it
        "S0003": ("check", "화면 확인 안 함"),
        "M0001": ("include", "음악 시작 · 분위기 1위 '경쾌한' · 대본에 곡명"),        # 0.62 vs 0.20: a clear mood
        "M0002": ("check", "음악이 갑자기 멈춤 · 줄거리에 필요한지 확인 · 대본 지문에도 적힘"),
        "M0003": ("check", "음악 시작 · 분위기 판단 애매"),                          # 0.31 vs 0.28
        # the horn the script writes and no detector found: a candidate of its own, always 확인 필요
        "C0007": ("check", "대본 지문에 적힌 소리 · 소리 검출로는 못 찾음"),
        "P0001": ("exclude", "말하는 얼굴이 보임"), "P0002": ("exclude", "말하는 얼굴이 보임"),
        "P0003": ("include", "말하는 얼굴 안 보임"),                                 # no face at a heard time
        "P0004": ("exclude", "말하는 얼굴이 보임"),
        "P0005": ("include", "대본에 화면 밖 표시(목소리만)"),                         # 도윤(V.O)
        "P0006": ("exclude", "말하는 얼굴이 보임"), "P0007": ("exclude", "말하는 얼굴이 보임"),
        "P0008": ("check", "말하는 얼굴 판정 애매 · 대사 시각 추정"),               # no face at a guessed time
        "P0009": ("exclude", "말하는 얼굴이 보임"),
        "P0012": ("include", "말하는 얼굴 안 보임"),
        "P0013": ("exclude", "말하는 얼굴이 보임"),
        "P0014": ("check", "말하는 얼굴 판정 애매 · 대사 시각 추정"),               # the whisper's time is a guess
        "P0015": ("exclude", "말하는 얼굴이 보임"), "P0016": ("exclude", "말하는 얼굴이 보임"),
    }
    # candidates with the script's evidence: the detected ones the cues fit, and the one the script raised
    assert {k: it.get("cue_id") for k, it in items.items() if it.get("cue_id")} == {
        "S0001": "C0001", "S0002": "C0002", "S0003": "C0003", "M0001": "C0004", "M0002": "C0005", "C0007": "C0007"}
    assert [k for k, it in items.items() if it.get("origin") == "script"] == ["C0007"]
    c7 = items["C0007"]
    assert c7["estimated"] and c7["window"] == [64.48, 69.0] and c7["phrase"] == "[빵빵 - 경적]"
    assert items["S0003"]["source_line"] == "L0004"            # the script says whose sigh it is
    # the script's wording is offered next to the detector's (the detector's default stays)
    assert [o["text"] for o in items["S0002"]["phrases"]][:2] == ["[쾅]", "[쾅! - 대문]"]
    assert "[별무리의 '여름 정거장']" in [o["text"] for o in items["M0001"]["phrases"]]
    assert items["M0001"]["phrase"] == "[경쾌한 음악]"
    ev = {k: [e["text"] for e in it["evidence"]] for k, it in items.items()}
    assert "대본 지문: \"멀리서 개 짖는 소리가 들려온다.\" · 화면 밖 소리로 적힘" in ev["S0001"]
    assert "대본에 적힌 말하는 사람의 소리: 가온의 한숨" in ev["S0003"]
    assert any("앞뒤 대사의 시각이 추정" in t for t in ev["M0002"])
    assert "이름이 아직 대사에 나오지 않음" in ev["P0003"]      # 보람's name is called only in L0006
    assert "이름이 아직 대사에 나오지 않음" not in ev["P0012"]
    # no speaker item for a line without a time
    assert "P0010" not in items and "P0011" not in items


# ================================================================== 5. the review layer
def test_writer_decisions_and_placement_again(decided):
    work, p, draft, _ = decided
    cells = [(c["start"], " / ".join(c["lines"]), c["refs"]) for c in draft["cells"]]
    assert [(s, t) for s, t, _ in cells] == [
        (1.5, "[멍멍]"),                                          # S0001 at its onset
        (4.0, "여기가 정말 그 집이 맞는 거지?"),                       # visible speakers: no name
        (7.0, "응, 파란 대문 집이라고 했어"),                          # the period at the end is dropped (I.13)
        (10.0, "[쾅]"),
        (12.0, "[여자] 거기 누구세요? / 이 시간에 무슨 일이에요?"),        # 보람 before L0006 tells the name
        (16.208, "[한숨 소리] 늦어서 죄송해요"),                         # her sigh at 16.2 s (frame 389) on the line
        (30.0, "[별무리의 '여름 정거장']"),                             # the phrase the writer chose
        (32.0, "[도윤] 그해 여름 우리는 / 매일 이 다방에 모였다"),
        (37.0, "보람아, 이리 와서 같이 앉자"),
        (40.0, "♪ 여름 정거장에 비가 내리면 ♪"),                         # sung (II.7)
        (44.5, "[가온/영어] 커피 두 잔 주세요"),                         # named by the writer, the language (II.6)
        (47.5, "[음악이 멈춘다]"),                                     # M0002; L0009 after it is out
        (62.0, "[보람] 버스가 벌써 / 떠나 버렸으면 어떡하지?"),            # after the name is told: the name
        (66.0, "[나래] 저기 버스 온다"),                                # the extra, now a line of 나래
        (69.0, "걱정 마, 다음 차는 금방 올 거야"),
        (73.0, "조용히 따라와"),                                        # P0014 still 확인 필요: no name
        (80.0, "- 누구야? / - 나야"),                                  # two short lines share a hyphen cell
        (88.0, "[음악이 멈춘다]"),                                     # the writer's own
    ]
    refs = [r for _, _, r in cells]
    assert ["L0004", "S0003"] in refs and ["L0015", "L0016"] in refs and ["A0001"] in refs and ["A0002"] in refs
    assert not any("L0009" in r or "C0007" in r or "M0003" in r for r in refs)
    flagged = [c["refs"] for c in draft["cells"] if "timing_estimated" in c["flags"]]
    assert flagged == [["L0008"], ["L0014"]] and draft["no_room"] == []
    comp = p.compose(draft=draft)
    ln = by_id(comp["lines"])
    assert (ln["L0004"]["text"], ln["L0004"]["script_text"], ln["L0004"]["text_from"]) == (
        "늦어서 죄송해요.", "늦어서 미안해요.", "heard")
    assert (ln["L0009"]["include"], ln["L0009"]["excluded"]) == (False, "writer")
    assert (ln["A0001"]["origin"], ln["A0001"]["name"], ln["A0001"]["start"]) == ("heard", "나래", 66.0)
    assert (ln["L0003"]["label_name"], ln["L0003"]["generic"]) == ("여자", True)
    assert ln["L0012"]["label_name"] == "보람" and ln["L0007"]["label_name"] == "보람"
    people = {r["name"]: r for r in comp["people"]}
    assert people["보람"]["told"]["line"] == "L0006" and people["보람"]["from_line"] == "L0012"
    items = by_id(comp["items"])
    assert {k: items[k]["final"] for k in ("S0003", "M0002", "P0008", "C0007", "M0003", "P0014")} == {
        "S0003": "include", "M0002": "include", "P0008": "include", "C0007": "exclude", "M0003": "check",
        "P0014": "check"}
    assert items["P0009"].get("line_excluded") and items["A0002"]["final"] == "include"
    chk = p.export_check(comp=comp, draft=draft)
    assert (chk["check"], chk["check_speaker"]) == (["M0003"], ["P0014"])     # left at 확인 필요
    assert chk["lines"] == {"differs_open": [], "unheard": ["L0014"], "absent": ["L0010", "L0011"],
                            "out_of_range": [], "writer_excluded": ["L0009"], "no_room": []}
    assert chk["extras_open"] == [] and chk["guide"]["summary"]["must"] == 0
    # names shown that no line has told yet (II.8): 가온 · 나래 · 도윤 are never called
    assert [(e["person"], e["line"]) for e in chk["names_early"]] == [("가온", "L0008"), ("나래", "A0001"),
                                                                      ("도윤", "L0005")]


# ================================================================== 6. export and the guide check
DIALOGUE_BACK = [   # (speaker, text, sung, language) of each dialogue line read back from the SRT
    (None, "여기가 정말 그 집이 맞는 거지?", False, None),
    (None, "응, 파란 대문 집이라고 했어", False, None),
    ("여자", "거기 누구세요? 이 시간에 무슨 일이에요?", False, None),
    (None, "늦어서 죄송해요", False, None),            # '[한숨 소리]' in front is a sound, not a speaker
    ("도윤", "그해 여름 우리는 매일 이 다방에 모였다", False, None),
    (None, "보람아, 이리 와서 같이 앉자", False, None),
    (None, "여름 정거장에 비가 내리면", True, None),
    ("가온", "커피 두 잔 주세요", False, "영어"),
    ("보람", "버스가 벌써 떠나 버렸으면 어떡하지?", False, None),
    ("나래", "저기 버스 온다", False, None),
    (None, "걱정 마, 다음 차는 금방 올 거야", False, None),
    (None, "조용히 따라와", False, None),
    (None, "누구야?", False, None),                   # the hyphen cell: two lines with the cell's time
    (None, "나야", False, None),
]


def test_export_formats_pass_the_guide_check_and_read_back(decided):
    work, p, draft, exports = decided
    for fmt, res in exports.items():
        path = Path(res["path"])
        assert path.is_file() and path.suffix == f".{fmt}" and res["cells"] == len(draft["cells"]) == 18
        # the independent check of the written file (not of the draft): no obligation broken
        got = check.run(path, fps=FPS, cuts=CUTS, profile="adult")
        assert got["ok"] and got["summary"]["must"] == 0 and got["cells"] == 18, fmt
        assert got["summary"]["must"] == res["check"]["summary"]["must"]
    text = Path(exports["srt"]["path"]).read_text(encoding="utf-8")
    for form in ("[멍멍]", "[쾅]", "[여자] 거기 누구세요?", "[가온/영어] 커피 두 잔 주세요", "♪ 여름 정거장에 비가 내리면 ♪",
                 "- 누구야?\n- 나야", "[별무리의 '여름 정거장']", "[음악이 멈춘다]"):
        assert form in text, form
    assert "미안해요" not in text and "다들 왜 갑자기" not in text and "경적" not in text
    # the SRT read back as a timed script (자막 대본): the same speakers and words
    doc = script.read(exports["srt"]["path"])
    assert doc["format"] == "timed" and doc["timed"]
    assert [(ln["speaker"], ln["text"], ln["sung"], ln["language"]) for ln in doc["lines"]] == DIALOGUE_BACK
    starts = [ln["start"] for ln in doc["lines"]]
    assert starts[3] == 16.208 and starts[-2] == starts[-1] == 80.0
    vtt = script.read(exports["vtt"]["path"])
    assert [(ln["speaker"], ln["text"]) for ln in vtt["lines"]] == [(s, t) for s, t, _, _ in DIALOGUE_BACK]


# ================================================================== 7. the review server
def test_review_server_shows_the_same_state(decided):
    from fastapi.testclient import TestClient

    from cboe.server.app import create_app
    work, p, draft, _ = decided
    with TestClient(create_app(work)) as client:
        r = client.get("/api/state")
        assert r.status_code == 200
        st = r.json()
    sm = st["summary"]
    # 21 candidates + the writer's music note, without P0009 (its line is out)
    assert sm["total"] == len(st["items"]) == 21
    assert sm["kind"] == {"sfx": 4, "music": 4, "speaker": 13}
    assert sm["final"] == {"include": 10, "exclude": 9, "check": 2}
    assert sm["decided"] == 5                       # S0003 M0002 P0008 C0007 and the added A0002
    assert sm["cells"] == 18 and sm["guide"]["must"] == 0 and sm["no_room"] == 0
    assert sm["lines"]["total"] == 16 and sm["lines"]["status"]["absent"] == 2
    assert {k: sm["lines"]["status"][k] for k in ("ok", "differs", "unheard")} == {"ok": 12, "differs": 1, "unheard": 1}
    assert sm["lines"]["included"] == 14            # 16 - 2 absent - 1 taken out + the extra made a line
    assert (sm["lines"]["added"], sm["lines"]["estimated"], sm["lines"]["text_changed"]) == (1, 2, 1)
    assert sm["extras"] == {"open": 0, "line": 1, "ignored": 0, "total": 1}
    assert sm["people"] == {"total": 4, "early": 3, "decided": 1}
    assert [c["lines"] for c in st["draft"]["cells"]] == [c["lines"] for c in draft["cells"]]
    assert st["export_check"]["check"] == ["M0003"] and st["error"] is None
    rows = by_id(st["lines"])
    assert rows["L0008"]["label"] == "[가온/영어]" and rows["L0003"]["label"] == "[여자]"
    assert rows["L0004"]["asr_text"] == "늦어서 죄송해요." and rows["L0004"]["script_text"] == "늦어서 미안해요."


def test_the_same_decisions_through_the_server_give_the_same_subtitles(ran, decided, tmp_path):
    from fastapi.testclient import TestClient

    from cboe.server.app import create_app
    work = copy_work(ran[0], tmp_path / "served")
    calls = [("/api/lines/L0009", {"include": False}),
             ("/api/lines/L0004", {"text_from": "heard"}),
             ("/api/extras/X0001", {"action": "line", "name": "나래"}),
             ("/api/people", {"names": ["보람"], "mode": "generic", "before": "여자"}),
             ("/api/added", {"kind": "music", "start": 88.0, "end": 90.0, "phrase": "음악이 멈춘다"}),
             ("/api/items/S0003", {"decision": "include"}),
             ("/api/items/M0002", {"decision": "include"}),
             ("/api/items/P0008", {"decision": "include"}),
             ("/api/items/C0007", {"decision": "exclude"}),
             ("/api/items/M0001", {"phrase": "[별무리의 '여름 정거장']"})]
    with TestClient(create_app(work)) as client:
        for url, body in calls:
            r = client.post(url, json=body)
            assert r.status_code == 200, (url, r.text)
        st = r.json()                                   # every change answers with the state placed again
        r = client.post("/api/export", json={"format": "srt"})
        assert r.status_code == 200 and r.json()["check"]["ok"] and r.json()["cells"] == 18
        srt = client.get("/api/export/srt")
        assert srt.status_code == 200
    want = [(c["start"], c["lines"], c["refs"]) for c in decided[2]["cells"]]
    assert [(c["start"], c["lines"], c["refs"]) for c in st["draft"]["cells"]] == want
    assert srt.content.decode("utf-8") == Path(decided[3]["srt"]["path"]).read_text(encoding="utf-8")


def test_the_exported_srt_as_the_script_of_a_new_work_folder(decided, tmp_path, monkeypatch):
    # 자막 대본 길: the same film, with the SRT of the review as its (timed) script
    work = make_work(tmp_path / "from_srt")
    shutil.copy(decided[3]["srt"]["path"], work / "subs.srt")
    meta = read_json(work / "project.json")
    write_json(work / "project.json", dict(meta, script=str(work / "subs.srt")))
    log = run_pipeline(work, monkeypatch)
    assert (log["script"]["format"], log["script"]["lines"], log["script"]["timed"]) == ("timed", 14, True)
    # the cells' own times are kept: every line is 'ok' at the time of its cell
    assert log["align"]["mode"] == "timed" and log["align"]["status"] == {"ok": 14}
    dj = read_json(work / "dialogue.json")
    assert [(ln["speaker"], ln["text"], ln["sung"], ln["language"]) for ln in dj["lines"]] == DIALOGUE_BACK
    assert [ln["start"] for ln in dj["lines"]][3:5] == [16.208, 32.0]
    # the line the writer took out is not in these subtitles: now it is speech in no script line
    assert [(x["text"], x["start"]) for x in dj["extras"]] == [("다들 왜 갑자기 조용해진 거야?", 50.0)]
    cells = [" / ".join(c["lines"]) for c in read_json(work / "draft.json")["cells"]]
    assert "[여자] 거기 누구세요? / 이 시간에 무슨 일이에요?" in cells and "♪ 여름 정거장에 비가 내리면 ♪" in cells


# ================================================================== 8. running again
def test_a_second_run_and_an_edited_script_keep_the_writers_decisions(decided, tmp_path, monkeypatch):
    work = copy_work(decided[0], tmp_path / "again")
    before = [c["lines"] for c in read_json(work / "draft.json")["cells"]]
    run_pipeline(work, monkeypatch)
    last = read_json(work / "run_history.json")[-1]["stages"]
    # nothing is made again (the model stages, the script, the alignment and what follows it) ...
    assert {s["name"] for s in last if s.get("skipped")} >= {
        "ingest", "script", "separate", "cuts", "asr", "align", "sfx", "music", "speaker", "screen", "dialog_ev",
        "recommend"} and FakeFaces.asked == []
    # ... but what is made on every run: the cues, the agent's items.json, the placement
    assert {"cues", "agent", "placement"} <= {s["name"] for s in last if not s.get("skipped")}
    assert [c["lines"] for c in read_json(work / "draft.json")["cells"]] == before

    # a line added to the end of scene 1: the ids after it move by one, and the script stage clears
    # the alignment and what follows it, which this run makes again
    f = work / "script.txt"
    f.write_text(f.read_text(encoding="utf-8").replace(
        "\t가온\t(한숨) 늦어서 미안해요.\n", "\t가온\t(한숨) 늦어서 미안해요.\n\t나래\t괜찮아, 어서 들어가자.\n"),
        encoding="utf-8")
    log = run_pipeline(work, monkeypatch)
    assert log["script"]["lines"] == 17 and log["script"]["cleared"] == ["align", "speaker", "dialog_ev", "recommend"]
    p = Project(work)
    comp = p.compose()
    ln = {x["text"]: x for x in comp["lines"]}
    # the line decisions follow their lines (by fingerprint), not the old ids
    assert ln["다들 왜 갑자기 조용해진 거야?"]["id"] == "L0010" and ln["다들 왜 갑자기 조용해진 거야?"]["excluded"] == "writer"
    assert ln["늦어서 죄송해요."]["text_from"] == "heard"
    assert ln["저기 버스 온다."]["name"] == "나래"
    # the sound decisions follow their sounds (kind and onset)
    items = by_id(comp["items"])
    assert {k: items[k]["final"] for k in ("S0003", "M0002", "C0007", "A0002")} == {
        "S0003": "include", "M0002": "include", "C0007": "exclude", "A0002": "include"}
    cells = [" / ".join(c["lines"]) for c in read_json(work / "draft.json")["cells"]]
    assert "[여자] 거기 누구세요? / 이 시간에 무슨 일이에요?" in cells and "[별무리의 '여름 정거장']" in cells
    assert "괜찮아, 어서 들어가자" in cells                      # the new line is in (not heard: a guessed time)
