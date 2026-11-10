# -*- coding: utf-8 -*-
"""Stage fixes after the 2026-11 guideline audit (화자·음악·효과음·받아쓰기·에이전트·추천 규칙·LLM 근거).

No model and no LLM runs here: the changed logic is in pure functions fed with made-up arrays
and lines, and the stages that need a model are driven with stand-ins for it.
"""
from __future__ import annotations

import contextlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from cboe import agent_tools, llm, phrases
from cboe.state import Project
from cboe.stages import agent, asr, dialog_ev, music, recommend, screen, sfx, speaker
from cboe.util import read_json, write_json


# ------------------------------------------------------------------ helpers
def _project(tmp_path, **files) -> Project:
    """A work folder with project.json and the given stage outputs (name without .json -> object)."""
    write_json(tmp_path / "project.json", {"title": "t", "video": "x.mp4", "t0": 0.0, "t1": 100.0, "fps": 24.0,
                                           "width": 64, "height": 36})
    for name in ("music", "sfx", "dialogue", "speaker", "cuts"):
        files.setdefault(name, {"music": {"items": []}, "sfx": {"candidates": []}, "dialogue": {"lines": []},
                                "speaker": {"lines": []}, "cuts": {"cuts": []}}[name])
    for name, obj in files.items():
        if obj is not None:
            write_json(tmp_path / f"{name}.json", obj)
    return Project(tmp_path)


def _line(i, start, end=None, **kw) -> dict:
    ln = {"id": f"L{i:04d}", "speaker": "가온", "text": f"지어낸 대사 {i}", "start": start,
          "end": None if start is None else (start + 1.5 if end is None else end), "timing": "asr"}
    ln.update(kw)
    return ln


def _sound(iid="S0001", onset=10.0, cls="Thunder", ko="천둥", confident=True, unknown=False, **kw) -> dict:
    c = {"id": iid, "kind": "sfx", "onset": onset, "t0": onset, "t1": onset + 1.0,
         "category": "unknown" if unknown else "event",
         "level_jump_db": 0.0, "phrases": [{"text": f"[{ko} 소리]", "short": f"[{ko}]"}], "phrase": f"[{ko} 소리]",
         "short": f"[{ko}]", "flags": [],
         "label": {"class": cls, "ko": ko, "score": 0.5 if confident else 0.15, "confident": confident,
                   "unknown": unknown, "competing": [], "path_ko": [ko], "top": []}}
    c.update(kw)
    return c


def _mood(key, score) -> dict:
    return {"key": key, "ko": phrases.MOODS[key], "score": score, "raw": 0.01}


SURE = [_mood("Scary music", 0.8), _mood("Sad music", 0.1)]
UNSURE = [_mood("Scary music", 0.34), _mood("Sad music", 0.30)]


def _music(kind, onset, seg, moods=SURE, **kw) -> dict:
    m = {"kind": kind, "onset": onset, "segment": list(seg), "moods": moods, "mood_strength": 0.02, "instruments": [],
         "change": None, "music_score": 0.5}
    m.update(kw)
    return m


def _null_lock(monkeypatch, module=None):
    """No GPU lock file from a test: the stand-in models need none."""
    import cboe.util
    monkeypatch.setattr(cboe.util, "gpu_lock", contextlib.nullcontext)
    if module is not None:
        monkeypatch.setattr(module, "gpu_lock", contextlib.nullcontext)


# ================================================================== 화자 (3-03, 3-06, 3-09, 3-m1, 6-07)
def _track(speaking, frames=25, best=0.2) -> dict:
    return {"size": 80.0, "speaking_frames": speaking, "frames": frames, "mean_score": 0.0, "max_score": best, "box": []}


def test_speaker_visible_needs_a_margin():
    assert speaker.judge([_track(20, best=0.2)]) == "visible"            # speaks on most of the window
    assert speaker.judge([_track(8, best=0.7)]) == "visible"             # few frames, but a clear best score
    assert speaker.judge([_track(8, best=0.16)]) == "unclear"            # weak on both counts (감사 3-m1)
    assert speaker.judge([_track(8, best=0.16), _track(20, best=0.1)]) == "visible"   # another face does speak
    assert speaker.judge([_track(4, frames=6, best=0.9)]) == "unclear"   # under SPEAK_FRAMES: as before
    assert speaker.judge([_track(5, frames=8, best=0.1)]) == "visible"   # a short line: 5 of 8 frames


def test_speaker_no_speaking_face_at_a_guessed_time_is_unclear():
    silent = [_track(0, best=-0.5)]
    assert speaker.judge([], estimated=False) == "not_visible"
    assert speaker.judge(silent, estimated=False) == "not_visible"
    assert speaker.judge([], estimated=True) == "unclear"                # no face at all: still no proof (감사 3-03)
    assert speaker.judge(silent, estimated=True) == "unclear"
    assert speaker.judge([_track(20)], estimated=True) == "visible"
    assert recommend.timing_estimated({"timing": "voice"}) and recommend.timing_estimated({"timing": "even"})
    assert recommend.timing_estimated({"timing": "asr", "status": "unheard"})
    assert not recommend.timing_estimated({"timing": "asr"}) and not recommend.timing_estimated({"timing": "script"})
    assert not recommend.timing_estimated({})                            # an old line without the keys


def test_speaker_rows_without_the_models():
    row = speaker.preset(_line(1, 10.0, 14.0, source="narration"), 0.0, 100.0)
    assert row["face"] == "not_visible" and row["by"] == "script" and row["source"] == "narration"
    assert row["window"] == [10.0, 11.0] and row["tracks"] == [] and row["best"] is None
    assert speaker.preset(_line(2, 99.9), 0.0, 100.0)["face"] == "unclear"          # at the edge of the window
    assert speaker.preset(_line(3, 99.9, source="phone"), 0.0, 100.0)["by"] == "script"
    assert speaker.preset(_line(4, 10.0), 0.0, 100.0) is None                         # the models have to look
    assert not recommend.has_time(_line(5, None)) and recommend.has_time(_line(6, 3.0))


def test_speaker_part_file_is_only_continued_for_the_same_lines():
    lines = [_line(1, 1.0), _line(2, 5.0)]
    meta = {"video": "x.mp4", "t0": 0.0, "t1": 100.0, "fps": 24.0}
    sig = speaker.signature(lines, [3.0], meta)
    part = {"signature": sig, "lines": [{"line_id": "L0001", "face": "visible"}]}
    assert speaker.resumable(part, sig) == part["lines"]
    assert speaker.resumable(None, sig) == [] and speaker.resumable({"lines": part["lines"]}, sig) == []
    for other in (speaker.signature([_line(1, 1.0), _line(2, 5.2)], [3.0], meta),              # a line moved
                  speaker.signature([_line(1, 1.0), _line(2, 5.0, text="다른 말")], [3.0], meta),  # a line was rewritten
                  speaker.signature(lines, [3.5], meta),                                        # the cuts changed
                  speaker.signature(lines, [3.0], {**meta, "t1": 90.0})):                        # another window
        assert other != sig and speaker.resumable(part, other) == []


class _FakeAnalyser:
    """Stands in for YuNet + LR-ASD: one silent face per line; fails on the line named in ``fail_on``."""
    made, seen, closed, fail_on, on_call = 0, [], 0, None, None

    def __init__(self, p):
        type(self).made += 1

    def tracks(self, ln, cuts):
        cls = type(self)
        if cls.on_call:
            cls.on_call(ln)
        if ln["id"] == cls.fail_on:
            raise RuntimeError("[GPU] clFinish, error code: -5")
        cls.seen.append(ln["id"])
        return [_track(0, best=-0.4)], [ln["start"], ln["start"] + 1.0]

    def close(self):
        type(self).closed += 1


@pytest.fixture
def fake_analyser(monkeypatch):
    for k, v in (("made", 0), ("seen", []), ("closed", 0), ("fail_on", None), ("on_call", None)):
        setattr(_FakeAnalyser, k, v)
    monkeypatch.setattr(speaker, "Analyser", _FakeAnalyser)
    return _FakeAnalyser


def _speaker_lines():
    return [_line(1, 2.0), _line(2, 6.0, source="narration"), _line(3, None, status="absent"), _line(4, 12.0),
            _line(5, 16.0, timing="voice"), _line(6, None, status="out_of_range"), _line(7, 24.0), _line(8, 30.0)]


def test_speaker_stage_saves_and_continues_where_it_stopped(tmp_path, fake_analyser, monkeypatch):
    p = _project(tmp_path, dialogue={"lines": _speaker_lines()}, speaker=None)
    monkeypatch.setattr(speaker, "SAVE_EVERY_LINES", 2)
    saved_before_l7 = []
    fake_analyser.on_call = lambda ln: ln["id"] == "L0007" and saved_before_l7.append(read_json(p.path(speaker.PART)))
    fake_analyser.fail_on = "L0007"
    with pytest.raises(RuntimeError):
        speaker.run(p, {})
    assert fake_analyser.seen == ["L0001", "L0004", "L0005"] and fake_analyser.closed == 1
    assert not p.path("speaker.json").exists()                           # not finished: the stage runs again
    part = read_json(p.path(speaker.PART))
    assert [r["line_id"] for r in part["lines"]] == ["L0001", "L0002", "L0004", "L0005"] and part["complete"] is False
    # it had already been saved on the way (every SAVE_EVERY_LINES lines), not only when the stage failed
    assert [r["line_id"] for r in saved_before_l7[0]["lines"]] == ["L0001", "L0002", "L0004", "L0005"]

    fake_analyser.seen, fake_analyser.fail_on, fake_analyser.on_call = [], None, None
    res = speaker.run(p, {})
    assert fake_analyser.seen == ["L0007", "L0008"]                      # only what was left
    assert res["resumed"] == 4 and res["no_time"] == 2 and res["by_script"] == 1
    out = read_json(p.path("speaker.json"))
    assert out["complete"] is True and not p.path(speaker.PART).exists()
    rows = {r["line_id"]: r for r in out["lines"]}
    assert list(rows) == ["L0001", "L0002", "L0004", "L0005", "L0007", "L0008"]   # line order, none without a time
    assert rows["L0002"]["by"] == "script" and rows["L0002"]["face"] == "not_visible"
    assert rows["L0001"]["face"] == "not_visible" and rows["L0005"]["face"] == "unclear"    # L0005: a guessed time
    assert all("name_revealed" in r and len(r["window"]) == 2 for r in rows.values())
    assert speaker.run(p, {}) == {"skipped": True}


def test_speaker_stage_starts_over_when_the_lines_changed(tmp_path, fake_analyser):
    lines = _speaker_lines()
    p = _project(tmp_path, dialogue={"lines": lines}, speaker=None)
    fake_analyser.fail_on = "L0007"
    with pytest.raises(RuntimeError):
        speaker.run(p, {})
    lines[0]["start"], lines[0]["end"] = 2.4, 3.9                        # the alignment was redone
    write_json(p.path("dialogue.json"), {"lines": lines})
    fake_analyser.seen, fake_analyser.fail_on = [], None
    assert speaker.run(p, {})["resumed"] == 0
    assert fake_analyser.seen == ["L0001", "L0004", "L0005", "L0007", "L0008"]


def test_speaker_stage_needs_no_model_for_marked_and_untimed_lines(tmp_path, fake_analyser):
    lines = [_line(1, 2.0, source="phone"), _line(2, None, status="absent"), _line(3, 9.0, source="broadcast")]
    p = _project(tmp_path, dialogue={"lines": lines}, speaker=None)
    res = speaker.run(p, {})
    assert fake_analyser.made == 0 and res["faces"] == {"not_visible": 2} and res["no_time"] == 1
    assert [r["source"] for r in read_json(p.path("speaker.json"))["lines"]] == ["phone", "broadcast"]


def test_analyser_measures_the_window_at_the_line_start(tmp_path, monkeypatch):
    """The clip handling around the two models, with stand-ins for the models and the video:
    one face in every frame, 'speaking' (score +1) only from frame 20 of each clip on."""
    import soundfile as sf
    from cboe import media
    p = _project(tmp_path)
    (tmp_path / "audio").mkdir()
    sf.write(str(tmp_path / "audio" / "mix_16k.wav"), np.zeros(100 * 1600, np.int16), 1600)
    asked = []

    def frames(self, a, b, width, height, fps=None, pix_fmt="rgb24"):
        asked.append((round(a, 2), round(b, 2)))
        return np.zeros((int(round((b - a) * fps)), height, width, 3), np.uint8)
    face = np.array([[20.0, 4.0, 28.0, 28.0] + [0.0] * 10 + [0.9]], np.float32)
    monkeypatch.setattr(media.Media, "frames", frames)
    monkeypatch.setattr(speaker, "face_detector", lambda w, h: SimpleNamespace(detect=lambda bgr: face))
    monkeypatch.setattr(speaker, "_load_lrasd", lambda: ("model", "head"))
    monkeypatch.setattr(speaker, "asd_scores",
                        lambda model, head, faces, mfcc: np.where(np.arange(len(faces)) >= 20, 1.0, -1.0))
    _null_lock(monkeypatch, speaker)
    an = speaker.Analyser(p)
    try:
        # clip [9.5, 12.0): the line starts at frame 12, its first second is frames 12..36 -> 17 of 25 speak
        info, window = an.tracks(_line(1, 10.0, 14.0), [])
        assert asked[-1] == (9.5, 12.0) and window == [10.0, 11.0] and len(info) == 1
        assert info[0]["frames"] == 25 and info[0]["speaking_frames"] == 17 and info[0]["max_score"] == 1.0
        assert speaker.judge(info) == "visible"
        # a cut 0.25 s before the line: the clip starts on the cut, the window is frames 6..30 -> 11 of 25
        info, _ = an.tracks(_line(2, 30.0, 34.0), [29.75])
        assert asked[-1] == (29.75, 32.0) and info[0]["frames"] == 25 and info[0]["speaking_frames"] == 11
        # a short line: the window is its 0.4 s, frames 12..21 -> 2 speak: under SPEAK_FRAMES
        info, window = an.tracks(_line(3, 50.0, 50.4), [])
        assert window == [50.0, 50.4] and info[0]["frames"] == 10 and info[0]["speaking_frames"] == 2
        assert speaker.judge(info) == "unclear"
    finally:
        an.close()
    # ... and through the stage, with the real Analyser on the stand-ins
    write_json(p.path("dialogue.json"), {"lines": [_line(1, 10.0, 14.0), _line(2, 50.0, 50.4)]})
    p.path("speaker.json").unlink()
    assert speaker.run(p, {})["faces"] == {"visible": 1, "unclear": 1}
    rows = read_json(p.path("speaker.json"))["lines"]
    assert rows[0]["best"]["speaking_frames"] == 17 and rows[0]["n_faces"] == 1 and rows[1]["face"] == "unclear"


def test_name_revealed_ignores_scenes_that_are_not_in_the_video():
    lines = [{"id": "L1", "speaker": "나래", "text": "가온아 어디 가", "start": None, "end": None, "status": "absent"},
             {"id": "L2", "speaker": "가온", "text": "금방 올게", "start": 3.0, "end": 4.0}]
    assert speaker.name_revealed(lines, 0, "가온") is False               # the only line that says it was cut
    lines[0].update(status="ok", start=1.0, end=2.0)
    assert speaker.name_revealed(lines, 0, "가온") is True
    assert speaker.name_revealed(lines, 0, None) is None


# ================================================================== 추천 규칙 (6-m2, 9-x1, 1-08, 3-06)
def test_music_that_continues_waits_for_its_root(tmp_path):
    seg = [10.0, 60.0]
    items = [_music("start", 10.0, seg, UNSURE), _music("cue", 14.0, seg, UNSURE), _music("cue", 30.0, seg, UNSURE)]
    p = _project(tmp_path, music={"items": items})
    a, b, c = [it for it in recommend.build(p, {}) if it["kind"] == "music"]
    assert [a["rec"], b["rec"], c["rec"]] == ["check", "check", "check"]
    # B and C both lean on A (the root of the chain), which is no caption yet
    assert "follows" not in a and b["follows"] == a["id"] and c["follows"] == a["id"]
    assert "앞 후보를 먼저 정해야 함" in b["reason"] and "앞 자막" not in b["reason"]
    assert a["agent_reasons"] == ["음악 분위기 애매"] and b["agent_reasons"] == [] and c["agent_reasons"] == []
    told = [e for e in b["evidence"] if e["kind"] == "자막"]
    assert len(told) == 1 and told[0]["polarity"] == "neutral" and a["id"] in told[0]["text"]
    # the agent settles the root's mood: now there is a caption before them
    done = {a["id"]: {"status": "done", "fp": f"music@{a['onset']:.2f}", "mood": "Scary music", "mood_ok": True,
                      "evidence": [], "summary": "긴장되는 현악"}}
    a2, b2, c2 = [it for it in recommend.build(p, done) if it["kind"] == "music"]
    assert [a2["rec"], b2["rec"], c2["rec"]] == ["include", "exclude", "exclude"]
    assert b2["follows"] == a2["id"] and c2["follows"] == a2["id"] and "앞 자막에서 이미 알림" in c2["reason"]
    assert any(e["kind"] == "자막" and e["polarity"] == "exclude" for e in c2["evidence"])


def test_music_exclude_only_after_an_include_recommendation(tmp_path):
    seg = [10.0, 80.0]
    items = [_music("start", 10.0, seg, SURE), _music("cue", 15.0, seg, SURE), _music("cue", 50.0, seg, UNSURE),
             _music("cue", 58.0, seg, UNSURE)]
    p = _project(tmp_path, music={"items": items})
    a, b, c, d = [it for it in recommend.build(p, {}) if it["kind"] == "music"]
    assert [a["rec"], b["rec"]] == ["include", "exclude"] and b["follows"] == a["id"]
    assert [c["rec"], d["rec"]] == ["check", "check"] and "follows" not in c and d["follows"] == c["id"]
    every = [a, b, c, d]
    assert not [x for x in every if x["rec"] == "exclude"
                and next(y for y in every if y["id"] == x["follows"])["rec"] != "include"]
    # the rule itself
    m = {"music": {"moods": SURE, "event": "cue"}, "label": {"confident": True}}
    assert recommend.decide_music(m, {"t": 1.0})[0] == "exclude"                       # as before: an include root
    assert recommend.decide_music(m, {"t": 1.0, "id": "M0001", "rec": "include"})[0] == "exclude"
    rec, reason, why = recommend.decide_music(m, {"t": 1.0, "id": "M0001", "rec": "check"})
    assert rec == "check" and "앞 후보를 먼저 정해야 함" in reason and why == []


def test_music_stop_candidate_is_always_the_writers_call(tmp_path):
    seg = [10.0, 40.0]
    items = [_music("start", 10.0, seg, SURE),
             {"kind": "stop", "onset": 40.2, "segment": seg, "moods": [], "mood_strength": 0.0, "instruments": [],
              "change": None, "music_score": 0.6, "drop_db": 33.5},
             _music("start", 50.0, [50.0, 70.0], SURE)]
    p = _project(tmp_path, music={"items": items})
    a, stop, c = [it for it in recommend.build(p, {}) if it["kind"] == "music"]
    assert stop["phrase"] == "[음악이 멈춘다]" and stop["short"] == "[음악이 멈춘다]"
    assert stop["rec"] == "check" and stop["agent_reasons"] == [] and "follows" not in stop
    assert stop["music"]["event"] == "stop" and stop["t1"] == pytest.approx(42.2)
    assert "34dB" in stop["evidence"][0]["text"]
    assert a["rec"] == "include" and a["t1"] == 40.0                    # the start still ends with its segment
    assert c["rec"] == "include" and "follows" not in c                 # the stop does not join a chain
    assert "다른 음악" not in stop["reason"]                              # the next music is 10 s later


def test_music_stop_right_before_the_next_music_says_so(tmp_path):
    segs = [[0.0, 30.0], [32.7, 50.0], [60.0, 80.0]]
    assert music.next_music(segs, 0, 30.0) == 2.7
    assert music.next_music(segs, 0, 26.7) == 6.0                        # STOP_NEXT_S itself still counts
    assert music.next_music(segs, 0, 26.6) is None
    assert music.next_music(segs, 1, 50.0) is None                       # 10 s of quiet: the music stopped
    assert music.next_music(segs, 2, 80.0) is None and music.next_music([], 0, 1.0) is None
    assert music.next_music(segs, 0, 33.0) == 0.0                        # a stop found after the next start: 0, not negative
    items = [_music("start", 0.0, segs[0], SURE),
             {"kind": "stop", "onset": 30.0, "segment": segs[0], "moods": [], "mood_strength": 0.0, "instruments": [],
              "change": None, "music_score": 0.6, "drop_db": 30.0, "next_music_s": 2.7},
             _music("start", 32.7, segs[1], SURE)]
    p = _project(tmp_path, music={"items": items})
    stop = next(it for it in recommend.build(p, {}) if it["kind"] == "music" and it["music"]["event"] == "stop")
    assert stop["rec"] == "check" and "2.7초 뒤 곧 다른 음악이 시작함" in stop["reason"]
    assert stop["music"]["next_music_s"] == 2.7
    assert any(e["text"] == "2.7초 뒤 다른 음악이 시작함" and e["t"] == pytest.approx(32.7) for e in stop["evidence"])


def test_no_mood_phrase_comes_from_phrases(tmp_path):
    p = _project(tmp_path, music={"items": [_music("start", 10.0, [10.0, 40.0], UNSURE)]})
    m = next(it for it in recommend.build(p, {}) if it["kind"] == "music")
    assert m["phrase"] == phrases.mood_phrase(None) and m["phrases"][-1]["text"] == phrases.mood_phrase(None)
    for mod in (recommend, agent, music):                               # no music sign of its own in these stages
        assert "♬" not in Path(mod.__file__).read_text(encoding="utf-8")


def test_speaker_item_uses_the_scripts_off_screen_mark(tmp_path):
    lines = [_line(1, 2.0, source="narration"), _line(2, 3.8, source="narration"), _line(3, 5.6),
             _line(4, 20.0, speaker="나래", source="phone"), _line(5, None, status="absent"), _line(6, 40.0, source="inner")]
    rows = [{"line_id": "L0001", "face": "visible", "n_faces": 2, "best": {"max_score": 0.9, "speaking_frames": 20}},
            {"line_id": "L0002", "face": "not_visible", "n_faces": 0, "best": None, "by": "script", "source": "narration"},
            {"line_id": "L0003", "face": "not_visible", "n_faces": 0, "best": None},
            {"line_id": "L0004", "face": "not_visible", "n_faces": 0, "best": None, "by": "script", "source": "phone"}]
    p = _project(tmp_path, dialogue={"lines": lines}, speaker={"lines": rows})
    sp = {it["speaker"]["line_id"]: it for it in recommend.build(p, {}) if it["kind"] == "speaker"}
    assert "L0005" not in sp                                             # no time: no name item
    # L0001: the models saw someone talking, but the script says narration -> the name is shown
    assert sp["L0001"]["rec"] == "include" and sp["L0001"]["reason"] == "대본에 화면 밖 표시(내레이션)"
    assert sp["L0001"]["speaker"]["face"] == "not_visible" and sp["L0001"]["speaker"]["by"] == "script"
    first = sp["L0001"]["evidence"][0]
    assert first["kind"] == "대본" and first["polarity"] == "include" and "내레이션" in first["text"]
    # L0002 goes on as narration: named once; L0003 is the same person speaking aloud: named again
    assert sp["L0002"]["rec"] == "exclude" and sp["L0002"]["follows"] == sp["L0001"]["id"]
    assert sp["L0003"]["rec"] == "include" and "follows" not in sp["L0003"]
    assert sp["L0004"]["reason"] == "대본에 화면 밖 표시(전화)"
    assert sp["L0006"]["rec"] == "include" and "속마음" in sp["L0006"]["reason"]   # no speaker.json row needed


def test_speaker_name_is_not_added_on_a_guessed_time(tmp_path):
    lines = [_line(1, 2.0, timing="voice"), _line(2, 10.0, timing="asr", status="unheard"), _line(3, 20.0)]
    rows = [{"line_id": ln["id"], "face": "not_visible", "n_faces": 0, "best": None} for ln in lines]
    p = _project(tmp_path, dialogue={"lines": lines}, speaker={"lines": rows})
    a, b, c = [it for it in recommend.build(p, {}) if it["kind"] == "speaker"]
    assert a["rec"] == "check" and "대사 시각 추정" in a["reason"] and a["speaker"]["estimated"] is True
    assert b["rec"] == "check" and c["rec"] == "include" and c["reason"] == "말하는 얼굴 안 보임"
    assert all(e["polarity"] != "include" for e in a["evidence"])
    assert any("대사 시각 추정" in e["text"] for e in b["evidence"])
    # the rule alone, with and without the new keys
    s = {"speaker": {"face": "not_visible", "name": "가온", "estimated": True}}
    assert recommend.decide_speaker(s, None)[0] == "check"
    assert recommend.decide_speaker(s, {"rec": "include"})[0] == "exclude"            # still a continuing line
    assert recommend.decide_speaker({"speaker": {"face": "not_visible", "name": "가온"}}, None)[0] == "include"
    marked = {"speaker": {"face": "not_visible", "name": "가온", "estimated": True, "by": "script", "source": "radio"}}
    assert recommend.decide_speaker(marked, None) == ("include", "대본에 화면 밖 표시(무전)", [])
    assert recommend.decide_speaker({"speaker": {"face": "not_visible", "name": None, "by": "script",
                                                 "source": "broadcast"}}, None)[0] == "check"


def test_speaker_going_on_after_a_line_still_to_check_follows_it(tmp_path):
    """L0001 (guessed time, no face) is 확인 필요; the same speaker goes on off screen right after:
    the next line keeps its own recommendation but follows L0001, so that putting the name on
    L0001 can drop it here (the name would be shown twice)."""
    lines = [_line(1, 2.0, 4.0, timing="even"), _line(2, 4.05, 6.0), _line(3, 6.5, 8.0),
             _line(4, 8.5, 9.5, speaker=None), _line(5, 9.6, 10.5, speaker=None)]
    rows = [{"line_id": ln["id"], "face": "not_visible", "n_faces": 0, "best": None} for ln in lines]
    p = _project(tmp_path, dialogue={"lines": lines}, speaker={"lines": rows})
    a, b, c, d, e = [it for it in recommend.build(p, {}) if it["kind"] == "speaker"]
    assert a["rec"] == "check" and "follows" not in a
    assert b["rec"] == "include" and b["follows"] == a["id"] and b["reason"].endswith("앞 줄을 먼저 정해야 함")
    assert any(a["id"] in x["text"] and x["polarity"] == "neutral" for x in b["evidence"])
    assert c["rec"] == "exclude" and c["follows"] == b["id"]               # the run named on b, as before
    assert "follows" not in d and "follows" not in e                       # no speaker in the script: no run
    # a line still to check after a check: both wait for the first
    lines[1] = _line(2, 4.05, 6.0, timing="voice")
    p = _project(tmp_path, dialogue={"lines": lines}, speaker={"lines": rows})
    a, b, c = [it for it in recommend.build(p, {}) if it["kind"] == "speaker"][:3]
    assert [a["rec"], b["rec"]] == ["check", "check"] and b["follows"] == a["id"] and c["follows"] == b["id"]
    # another source (the script marks narration) or a gap over 2 s: no run, no follows
    lines[1] = _line(2, 4.05, 6.0, source="narration")
    lines[2] = _line(3, 9.0, 10.0)
    p = _project(tmp_path, dialogue={"lines": lines[:3]}, speaker={"lines": rows[:3]})
    assert all("follows" not in it for it in recommend.build(p, {}) if it["kind"] == "speaker")


def test_detection_evidence_leaves_out_plain_sound_in_the_path():
    def text(path_ko):
        c = _sound(label={"class": "Breathing", "ko": "숨", "score": 0.52, "confident": True, "unknown": False,
                          "competing": [], "path_ko": path_ko, "top": []})
        return recommend.sfx_evidence(c, None, None)[0]["text"]
    assert text(["숨", phrases.SOUND, phrases.SOUND]) == "검출: 숨 0.52"               # nothing above it is named
    assert text(["발걸음", "발걸음", phrases.SOUND]) == "검출: 숨 0.52"
    assert text(["뛰는", "발걸음", phrases.SOUND]) == "검출: 숨 0.52 (뛰는 → 발걸음)"
    assert text(["웅성거리는", "사람들", phrases.SOUND, "목소리"]) == "검출: 숨 0.52 (웅성거리는 → 사람들 → 목소리)"


def _reaction(line_id="L0001") -> dict:
    return {"id": "S0001", "relation": "reaction", "line_id": line_id, "evidence": "직후 \"방금 뭐였지\"", "plot_hint": "",
            "source": "llm"}


def test_dialogue_evidence_on_a_guessed_line_time_does_not_include(tmp_path):
    seen = {"S0001": {"id": "S0001", "visible": "안 보임", "observation": "빈 복도", "reaction": "", "likely_source": "",
                      "source": "vlm"}}
    for timing, rec in (("asr", "include"), ("voice", "check"), ("even", "check")):
        p = _project(tmp_path, sfx={"candidates": [_sound()]}, screen={"items": seen},
                     dialog_ev={"items": {"S0001": _reaction()}},
                     dialogue={"lines": [_line(1, 11.0, timing=timing, text="방금 뭐였지")]})
        it = next(x for x in recommend.build(p, {}) if x["kind"] == "sfx")
        told = next(e for e in it["evidence"] if e["kind"] == "대사")
        assert it["rec"] == rec, timing
        if rec == "check":
            assert "대사 근거의 시각이 추정" in it["reason"] and "대사 근거의 시각이 추정" in it["agent_reasons"]
            assert told["polarity"] == "neutral" and "추정" in told["text"]
        else:
            assert "방금 뭐였지" in it["reason"] and told["polarity"] == "include"
    # other reasons to include are not touched: a sudden loud event stays an include recommendation
    loud = _sound(level_jump_db=30.0)
    rec, reason, _ = recommend.decide_sfx(loud, seen["S0001"], {**_reaction(), "estimated": True}, None)
    assert rec == "include" and "갑자기 큰 소리" in reason and "방금" not in reason
    # 'already written in the dialogue' is about a vocalisation line that align places by voice activity: kept
    rec, _, _ = recommend.decide_sfx(_sound(), seen["S0001"], {"relation": "already", "estimated": True}, None)
    assert rec == "exclude"


def test_build_copes_with_lines_that_have_no_time(tmp_path):
    laugh = _sound("S0001", 20.1, "Laughter", "웃음", category="vocal")
    lines = [_line(1, None, status="absent"), _line(2, 20.0), _line(3, None, status="out_of_range")]
    p = _project(tmp_path, sfx={"candidates": [laugh]}, dialogue={"lines": lines})
    items = recommend.build(p, {})
    assert [it["id"] for it in items if it["kind"] == "speaker"] == ["P0002"]
    assert next(it for it in items if it["kind"] == "sfx")["source_line"] == "L0002"
    assert dialog_ev.window_lines(lines, 20.0) == [lines[1]]
    assert dialog_ev.rule(laugh, lines)["relation"] == "none"


def test_repeated_sound_follows_the_caption_before_it(tmp_path):
    seen = {i: {"id": i, "visible": "안 보임", "observation": "", "reaction": "놀람", "likely_source": "", "source": "vlm"}
            for i in ("S0001", "S0002")}
    p = _project(tmp_path, sfx={"candidates": [_sound("S0001", 10.0), _sound("S0002", 12.0)]}, screen={"items": seen})
    a, b = [it for it in recommend.build(p, {}) if it["kind"] == "sfx"]
    assert a["rec"] == "include" and "follows" not in a
    assert b["rec"] == "exclude" and b["follows"] == "S0001" and "앞 자막에서 이미 알림" in b["reason"]


def test_level_only_sound_stays_folded_until_something_speaks_for_it(tmp_path):
    def build(dia=None, seen=None):
        c = _sound("S0001", 10.0, "Generic impact sounds", "부딪히는", confident=False, unknown=True, minor=True)
        p = _project(tmp_path, sfx={"candidates": [c, _sound("S0002", 50.0)]},
                     dialog_ev={"items": {"S0001": dia} if dia else {}}, screen={"items": {"S0001": seen} if seen else {}},
                     dialogue={"lines": [_line(1, 11.0)]})
        return {it["id"]: it for it in recommend.build(p, {}) if it["kind"] == "sfx"}
    got = build()
    assert got["S0001"]["minor"] is True and got["S0001"]["rec"] == "check"       # kept, only folded
    assert "minor" not in got["S0002"]                                              # an old candidate without the key
    assert build(dia=_reaction())["S0001"]["minor"] is False                        # a line reacts to it
    assert build(seen={"id": "S0001", "visible": "애매", "observation": "", "reaction": "돌아봄",
                       "likely_source": "", "source": "vlm"})["S0001"]["minor"] is False


# ================================================================== LLM 근거 단계 (5-11, 6-06)
def test_answer_matters_only_for_sounds_the_rules_can_settle():
    named = recommend.with_scene_first([_sound()], [])[0]
    unnamed = recommend.with_scene_first([_sound(confident=False, unknown=True, level_jump_db=30.0)], [])[0]
    assert named["scene_first"] is True
    assert recommend.answer_matters(named, "screen") and recommend.answer_matters(named, "dialog")
    # a sound without a name stays 확인 필요 whatever the screen or the dialogue says
    assert not recommend.answer_matters(unnamed, "screen") and not recommend.answer_matters(unnamed, "dialog")
    # the screen already shows the source: no dialogue answer brings the sound back
    assert not recommend.answer_matters(named, "dialog", {"visible": "보임", "reaction": ""})
    assert recommend.answer_matters(named, "dialog", {"visible": "안 보임", "reaction": ""})
    both = [_sound("S0001", 10.0), _sound("S0002", 30.0, confident=False, unknown=True)]
    assert recommend.worth_asking(both, [], "screen") == {"S0001"}
    seen = {"S0001": {"visible": "보임", "source": "vlm"}}
    assert recommend.worth_asking(both, [], "dialog", seen) == set()
    assert recommend.worth_asking(both, [], "dialog", {"S0001": {"visible": "애매", "source": "skipped"}}) == {"S0001"}
    assert recommend.worth_asking(both, [], "dialog", {"S0001": {"visible": "보임", "failed": True}}) == {"S0001"}


@pytest.fixture
def asked(monkeypatch):
    """The LLM is 'there'; each batch call is recorded instead of made."""
    calls = {"dialog": [], "screen": []}
    monkeypatch.setattr(llm, "available", lambda: True)

    def dialog_batch(batch, lines, cfg, work, context=None):
        calls["dialog"].append([c["id"] for c in batch])
        return {c["id"]: {"id": c["id"], "relation": "none", "line_id": "", "evidence": "", "plot_hint": ""} for c in batch}

    def screen_batch(batch, m, cuts, cfg, work):
        calls["screen"].append([c["id"] for c in batch])
        return {c["id"]: {"id": c["id"], "visible": "안 보임", "observation": "빈 방", "reaction": "", "likely_source": ""}
                for c in batch}
    monkeypatch.setattr(dialog_ev, "_batch_call", dialog_batch)
    monkeypatch.setattr(screen, "_batch_call", screen_batch)
    return calls


def _evidence_project(tmp_path):
    sounds = [_sound("S0001", 10.0), _sound("S0002", 30.0, confident=False, unknown=True)]
    return _project(tmp_path, sfx={"candidates": sounds}, dialogue={"lines": [_line(1, 11.0), _line(2, None)]},
                    music={"items": [_music("start", 5.0, [5.0, 60.0], UNSURE), _music("cue", 20.0, [5.0, 60.0], UNSURE)]})


def test_dialogue_evidence_is_not_asked_for_music(tmp_path, asked):
    p = _evidence_project(tmp_path)
    res = dialog_ev.run(p, {})
    assert asked["dialog"] == [["S0001", "S0002"]] and res["asked"] == 2 and res["not_asked"] == 0
    out = read_json(p.path("dialog_ev.json"))
    assert sorted(out["items"]) == ["S0001", "S0002"] and out["complete"] is True and out["scope"] == "all"
    assert out["prompt_version"] == dialog_ev.PROMPT_VERSION
    assert dialog_ev.run(p, {}) == {"skipped": True, "failed_left": 0}


def test_llm_scope_useful_asks_only_where_the_answer_can_change_the_recommendation(tmp_path, asked):
    p = _evidence_project(tmp_path)
    res = screen.run(p, {"llm_scope": "useful"})
    assert asked["screen"] == [["S0001"]] and res["asked"] == 1 and res["not_asked"] == 1
    seen = read_json(p.path("screen.json"))
    assert seen["items"]["S0001"]["source"] == "vlm" and seen["items"]["S0002"]["source"] == "skipped"
    assert seen["scope"] == "useful" and seen["complete"] is True
    res = dialog_ev.run(p, {"llm_scope": "useful"})
    assert asked["dialog"] == [["S0001"]] and res["not_asked"] == 1
    told = read_json(p.path("dialog_ev.json"))["items"]
    assert told["S0001"]["source"] == "llm" and told["S0002"]["skipped"] is True and told["S0002"]["source"] == "rule"
    # the recommendation of the sound that was not asked about is what it would be with any answer
    it = next(x for x in recommend.build(p, {}) if x["id"] == "S0002")
    assert it["rec"] == "check" and "화면 확인 안 함" in it["reason"] and "화면 판단 애매" not in it["reason"]
    # the same scope again: nothing left to ask; 'all' later: the ones left out are asked
    assert screen.run(p, {"llm_scope": "useful"})["skipped"] and dialog_ev.run(p, {"llm_scope": "useful"})["skipped"]
    assert len(asked["screen"]) == 1 and len(asked["dialog"]) == 1
    screen.run(p, {})
    dialog_ev.run(p, {"llm_scope": "all"})
    assert asked["screen"][1:] == [["S0002"]] and asked["dialog"][1:] == [["S0002"]]
    assert read_json(p.path("screen.json"))["items"]["S0002"]["source"] == "vlm"
    assert "skipped" not in read_json(p.path("dialog_ev.json"))["items"]["S0002"]


def test_default_scope_asks_about_every_sound(tmp_path, asked):
    p = _evidence_project(tmp_path)
    assert screen.run(p, {})["asked"] == 2 and asked["screen"] == [["S0001", "S0002"]]
    assert read_json(p.path("screen.json"))["scope"] == "all"


# a made-up script: one scene, a sound written between two lines (no real script text)
CUE_SCRIPT = {"version": 2, "format": "screenplay", "timed": False, "characters": ["가온"], "people": [],
              "scenes": [{"id": "SC001", "number": "1", "heading": "1. 지어낸 부엌 / 밤", "place": "지어낸 부엌", "time": "밤"}],
              "elements": [{"id": "E0001", "type": "heading", "scene_id": "SC001", "text": "1. 지어낸 부엌 / 밤"},
                           {"id": "E0002", "type": "dialogue", "scene_id": "SC001", "text": "지어낸 대사 1", "line_id": "L0001"},
                           {"id": "E0003", "type": "action", "scene_id": "SC001", "text": "탁자 위의 컵이 쨍그랑 깨진다."},
                           {"id": "E0004", "type": "dialogue", "scene_id": "SC001", "text": "지어낸 대사 2", "line_id": "L0002"}],
              "lines": [{"id": f"L000{i}", "speaker": "가온", "text": f"지어낸 대사 {i}", "raw": f"지어낸 대사 {i}",
                         "scene_id": "SC001", "element_id": f"E000{2 * i}", "language": None, "sung": False, "source": None,
                         "delivery": [], "vocal": [], "directions": []} for i in (1, 2)]}
CUE_LINES = [_line(1, 10.0, 12.0, scene_id="SC001", status="ok"), _line(2, 20.0, 22.0, scene_id="SC001", status="ok")]
CUE_SCENES = {"scenes": [{"id": "SC001", "start": 5.0, "end": 30.0, "status": "found"}]}


def _unnamed(iid="S0001", onset=15.0) -> dict:
    return _sound(iid, onset, "Generic impact sounds", "부딪히는", confident=False, unknown=True,
                  phrases=[{"text": "[소리]", "short": "[소리]", "source": "unknown"}], phrase="[소리]", short="[소리]")


def test_useful_scope_asks_about_an_unnamed_sound_the_script_names(tmp_path, asked):
    """An unnamed sound that a script cue names (named_by 'script') is excluded when its source is
    on screen and stays 확인 필요 when it is not: the screen answer matters, so it is asked."""
    p = _project(tmp_path, sfx={"candidates": [_unnamed()]}, dialogue={"lines": CUE_LINES}, script=CUE_SCRIPT,
                 scenes=CUE_SCENES)
    sfx_before = read_json(p.path("sfx.json"))
    assert recommend.worth_asking_in(p, sfx_before["candidates"], "screen") == {"S0001"}
    assert read_json(p.path("sfx.json")) == sfx_before and "cue_id" not in sfx_before["candidates"][0]
    res = screen.run(p, {"llm_scope": "useful"})
    assert asked["screen"] == [["S0001"]] and res["asked"] == 1 and res["not_asked"] == 0
    # what the answer decides, through build (the same candidate the stage looked at)
    recs = {}
    for vis in ("보임", "안 보임"):
        write_json(p.path("screen.json"), {"items": {"S0001": {"id": "S0001", "visible": vis, "observation": "컵이 깨짐",
                                                               "reaction": "", "likely_source": "", "source": "vlm"}}})
        it = next(x for x in recommend.build(p, {}) if x["id"] == "S0001")
        assert it["named_by"] == "script"
        recs[vis] = it["rec"]
    assert recs == {"보임": "exclude", "안 보임": "check"}
    # the dialogue answer matters too when the screen does not show the source ('이미 적힘' excludes it)
    res = dialog_ev.run(p, {"llm_scope": "useful"})
    assert asked["dialog"] == [["S0001"]] and res["not_asked"] == 0
    # the same sound without the script: no name, 확인 필요 whatever is answered -> not asked
    (tmp_path / "bare").mkdir()
    bare = _project(tmp_path / "bare", sfx={"candidates": [_unnamed()]}, dialogue={"lines": CUE_LINES})
    assert screen.run(bare, {"llm_scope": "useful"})["not_asked"] == 1 and len(asked["screen"]) == 1


def test_useful_scope_asks_again_when_the_answer_now_matters(tmp_path, asked):
    # screen: skipped while the script was not there; once the cue names the sound, the next run asks it
    (tmp_path / "s").mkdir()
    p = _project(tmp_path / "s", sfx={"candidates": [_unnamed()]}, dialogue={"lines": CUE_LINES})
    assert screen.run(p, {"llm_scope": "useful"})["not_asked"] == 1 and asked["screen"] == []
    assert read_json(p.path("screen.json"))["items"]["S0001"]["source"] == "skipped"
    write_json(p.path("script.json"), CUE_SCRIPT)
    write_json(p.path("scenes.json"), CUE_SCENES)
    res = screen.run(p, {"llm_scope": "useful"})
    assert asked["screen"] == [["S0001"]] and res["asked"] == 1
    assert read_json(p.path("screen.json"))["items"]["S0001"]["source"] == "vlm"
    assert screen.run(p, {"llm_scope": "useful"})["skipped"] and len(asked["screen"]) == 1
    # dialog: the screen showed the source (no dialogue answer brings it back), then the screen was
    # asked again (--force screen keeps dialog_ev.json) and says 안 보임: now the answer matters
    (tmp_path / "d").mkdir()
    q = _project(tmp_path / "d", sfx={"candidates": [_sound("S0001", 10.0)]}, dialogue={"lines": [_line(1, 10.5)]},
                 screen={"items": {"S0001": {"visible": "보임", "observation": "번개가 보임", "reaction": "", "source": "vlm"}}})
    assert dialog_ev.run(q, {"llm_scope": "useful"})["not_asked"] == 1 and asked["dialog"] == []
    assert read_json(q.path("dialog_ev.json"))["items"]["S0001"]["skipped"] is True
    write_json(q.path("screen.json"), {"items": {"S0001": {"visible": "안 보임", "observation": "어두운 방",
                                                           "reaction": "", "source": "vlm"}}})
    res = dialog_ev.run(q, {"llm_scope": "useful"})
    assert asked["dialog"] == [["S0001"]] and res["asked"] == 1
    assert read_json(q.path("dialog_ev.json"))["items"]["S0001"]["source"] == "llm"


def test_screen_stage_groups_by_the_script_scenes(tmp_path, asked, monkeypatch):
    got = []
    real = recommend.worth_asking

    def spy(sfx, cuts, which, screen_items=None, starts=None):
        got.append((which, starts))
        return real(sfx, cuts, which, screen_items, starts)
    monkeypatch.setattr(recommend, "worth_asking", spy)
    p = _project(tmp_path, sfx={"candidates": [_sound()]}, dialogue={"lines": CUE_LINES}, script=CUE_SCRIPT,
                 scenes=CUE_SCENES)
    screen.run(p, {"llm_scope": "useful"})
    dialog_ev.run(p, {"llm_scope": "useful"})
    assert got == [("screen", [(5.0, "SC001")]), ("dialog", [(5.0, "SC001")])]      # the same scenes in both


def test_evidence_stages_pass_their_prompt_version(tmp_path, monkeypatch):
    got = {}

    def fake_ask(parts, schema, **kw):
        got.update(kw)
        return {"items": []}
    monkeypatch.setattr(llm, "ask", fake_ask)
    dialog_ev._batch_call([_sound()], [_line(1, 11.0)], {}, tmp_path)
    assert got["tag"] == "dialog" and got["prompt_version"] == dialog_ev.PROMPT_VERSION


# ================================================================== 음악 (4-06, 5-10, 5-m3, 5-m4)
HOP = 0.01


def _level(seconds, value=-90.0) -> np.ndarray:
    return np.full(int(seconds / HOP), value, dtype=np.float32)


def test_near_silent_music_segment_is_not_music():
    lev = _level(60)
    lev[1000:2000] = -35.0                         # 10~20 s: music on the music track
    lev[3000:4000] = -80.0                         # 30~40 s: what the separation left over
    lev[3500:3520] = -62.0                         # ... with a short bump
    segs = [[10.0, 20.0], [30.0, 40.0], [45.0, 55.0]]
    assert not music.near_silent(lev, HOP, 0.0, 10.0, 20.0) and music.near_silent(lev, HOP, 0.0, 30.0, 40.0)
    keep, drop = music.split_silent(segs, lev, HOP, 0.0)
    assert keep == [[10.0, 20.0]] and drop == [[30.0, 40.0], [45.0, 55.0]]
    # the original mix says music at 45~55 s: the stem lost it, the music is real
    mix = np.zeros(60 * 25, dtype=np.float32)
    mix[45 * 25:55 * 25] = 0.6
    mix[30 * 25:40 * 25] = 0.05
    keep, drop = music.split_silent(segs, lev, HOP, 0.0, mix)
    assert keep == [[10.0, 20.0], [45.0, 55.0]] and drop == [[30.0, 40.0]]
    # times are absolute: a window that starts at 100 s
    keep, drop = music.split_silent([[110.0, 120.0], [130.0, 140.0]], lev, HOP, 100.0)
    assert keep == [[110.0, 120.0]] and drop == [[130.0, 140.0]]


def test_music_start_moves_onto_the_level_rise():
    lev = _level(30)
    lev[1035:] = -40.0                             # the music really starts at 10.35 s
    assert music.refine_start(lev, HOP, 0.0, 10.0) == pytest.approx(10.35, abs=0.011)   # the SED start was early
    assert music.refine_start(lev, HOP, 0.0, 10.6) == pytest.approx(10.35, abs=0.011)   # ... or late
    assert music.refine_start(lev, HOP, 100.0, 110.0) == pytest.approx(110.35, abs=0.011)
    # a leftover far under the music before it starts is not the start
    foot = _level(30)
    foot[1000:1030] = -78.0
    foot[1035:] = -50.0
    assert music.refine_start(foot, HOP, 0.0, 10.0) == pytest.approx(10.35, abs=0.011)
    # nothing to hold on to: the guess stays
    assert music.refine_start(_level(30, -40.0), HOP, 0.0, 10.0) == 10.0               # already playing
    assert music.refine_start(lev, HOP, 0.0, 12.0) == 12.0                             # the rise is outside the window
    quiet = _level(30)
    quiet[1035:] = -72.0
    assert music.refine_start(quiet, HOP, 0.0, 10.0) == 10.0                           # nothing audible
    assert music.refine_start(lev, HOP, 0.0, 0.0) == 0.0                               # at the start of the data


def test_music_stop_is_an_abrupt_fall_that_stays():
    lev = _level(60)
    lev[1000:2000] = -30.0                         # music 10~20 s, cut off at 20.0
    t, drop = music.stop_at(lev, HOP, 0.0, 20.3)   # the SED end is a little late
    assert t == pytest.approx(20.0, abs=0.011) and drop == pytest.approx(60.0, abs=0.5)
    assert music.stop_at(lev, HOP, 0.0, 19.6)[0] == pytest.approx(20.0, abs=0.011)
    assert music.stop_at(lev, HOP, 0.0, 22.0) is None                                  # no fall near this end
    fade = _level(60)
    fade[1000:2000] = -30.0
    fade[2000:2400] = np.linspace(-30.0, -90.0, 400)                                    # 4 s fade-out
    assert music.stop_at(fade, HOP, 0.0, 20.0) is None and music.stop_at(fade, HOP, 0.0, 23.0) is None
    rest = lev.copy()
    rest[2030:2600] = -28.0                                                             # a rest, then the next hit
    assert music.stop_at(rest, HOP, 0.0, 20.0) is None
    faint = _level(60)
    faint[1000:2000] = -68.0                                                            # nothing audible stopped
    assert music.stop_at(faint, HOP, 0.0, 20.0) is None
    late = lev.copy()
    late[2000:2020] = np.linspace(-30.0, -90.0, 20)                                     # a short decay
    assert 20.0 <= music.stop_at(late, HOP, 0.0, 20.0)[0] <= 20.2


def test_music_stage_with_made_up_tracks(tmp_path, monkeypatch):
    import soundfile as sf
    dur, fps = 80.0, 25
    lev = _level(dur)
    lev[1035:3000] = -35.0                         # music 10.35~30.0 s, cut off
    lev[6000:] = -40.0                             # music from 60.0 s to the end of the window
    lev[4000:4600] = -85.0                         # 40~46 s: near silent, yet the 'Music' score is up
    score = np.zeros((int(dur * fps), 2), dtype=np.float32)
    for a, b in ((10.0, 30.2), (40.0, 46.0), (59.7, dur)):
        score[int(a * fps):int(b * fps), 0] = 0.7
    classes = np.array(["Music", "Speech"])
    p = _project(tmp_path, music=None)
    np.savez(p.path("sed_music.npz"), probs=score, classes=classes, t0=0.0)
    np.savez(p.path("sed_mix.npz"), probs=np.zeros_like(score), classes=classes, t0=0.0)
    np.savez(p.path("env.npz"), hop=np.float32(HOP), t0=np.float32(0.0), music=lev, effects=_level(dur), speech=_level(dur))
    (tmp_path / "audio").mkdir()
    sf.write(str(tmp_path / "audio" / "music_16k.wav"), np.zeros(int(dur * 1600), np.float32), 1600)
    labels = list(music.MOOD_KEYS) + list(phrases.INSTRUMENTS)

    def fake_ast(x, sr, starts):                   # AST: every window 'scary', no instrument
        probs = np.zeros((len(starts), len(labels)), np.float32)
        probs[:, labels.index("Scary music")] = 0.05
        return probs, labels
    monkeypatch.setattr(music, "_ast_windows", fake_ast)
    _null_lock(monkeypatch, music)
    res = music.run(p, {})
    out = read_json(p.path("music.json"))
    assert res == {"segments": 2, "silent_segments": 1, "candidates": 3, "stops": 1}
    assert out["silent_segments"] == [[40.0, 46.0]]
    assert [s[0] for s in out["segments"]] == [10.0, pytest.approx(59.72, abs=0.05)]      # as the 'Music' score found them
    kinds = [(it["kind"], it["onset"]) for it in out["items"]]
    assert [k for k, _ in kinds] == ["start", "stop", "start"] and kinds == sorted(kinds, key=lambda x: x[1])
    first, stop, last = out["items"]
    assert first["onset"] == pytest.approx(10.35, abs=0.011) and first["onset_sed"] == 10.0
    assert first["segment"][0] <= first["onset"] and first["moods"][0]["key"] == "Scary music"
    assert stop["onset"] == pytest.approx(30.0, abs=0.011) and stop["drop_db"] >= music.STOP_DROP_DB and stop["moods"] == []
    assert last["onset"] == pytest.approx(60.0, abs=0.011)             # runs to the end of the window: no stop
    assert "next_music_s" not in stop                                  # the next music is 30 s later
    assert out["params"]["SILENT_DB"] == music.SILENT_DB and out["params"]["STOP_NEXT_S"] == music.STOP_NEXT_S
    # ... and what the recommendation makes of it
    m = [it for it in recommend.build(p, {}) if it["kind"] == "music"]
    assert [it["rec"] for it in m] == ["include", "check", "include"] and m[1]["phrase"] == "[음악이 멈춘다]"
    assert music.run(p, {}) == {"skipped": True}


# ================================================================== 효과음 (4-m3)
def test_sound_found_by_the_level_jump_alone_is_marked():
    loud = -40.0
    assert sfx.unknown_by(True, 0.9, 30.0, -10.0, loud) is None                     # a named sound
    assert sfx.unknown_by(False, 0.40, 2.0, -70.0, loud) == "sound_effect"          # 'Sound effect' fired
    assert sfx.unknown_by(False, 0.10, 20.0, -30.0, loud) == "level"                # only the level says so -> minor
    assert sfx.unknown_by(False, 0.10, 20.0, -50.0, loud) is None                   # a jump, but a quiet one
    assert sfx.unknown_by(False, 0.10, 12.0, -30.0, loud) is None                   # loud, but no jump
    assert sfx.unknown_by(False, 0.40, 20.0, -30.0, loud) == "sound_effect"


# ================================================================== 에이전트 (4-m1, 4-10)
def test_recheck_whose_tool_never_answered_is_not_finished():
    ok = {"tool": "look_frames", "input": {"time": 3.0}, "output": "프레임 4장"}
    bad = {"tool": "describe_sound", "input": {"start": 1.0, "end": 2.0}, "error": True, "output": "RuntimeError: 메모리 부족"}
    again = {"tool": "describe_sound", "input": {"start": 1.0, "end": 2.5}, "output": "소리 설명 모델: a door closing"}
    assert agent.failed_tools([ok, bad]) == ["describe_sound"]
    assert agent.failed_tools([ok, bad, again]) == []                    # it failed once and then answered
    assert agent.failed_tools([ok]) == [] and agent.failed_tools([]) == []
    assert agent.failed_tools([{"tool": "rescan_sound", "input": {}}]) == []           # nothing known about the call


def test_call_log_keeps_what_each_tool_returned():
    log = agent_tools.CallLog()
    assert log.run("rescan_sound", {"start": 1.0, "end": 2.5}, lambda: "재검출 결과") == "재검출 결과"
    frames = log.run("look_frames", {"time": 3.0, "span": 2.0, "count": 2}, lambda: ["프레임 2장", "00:03.0", object()])
    assert frames[0] == "프레임 2장" and len(frames) == 3                 # the tool's own result is passed on untouched
    with pytest.raises(ZeroDivisionError):
        log.run("describe_sound", {"start": 5.0, "end": 6.0, "track": "effects"}, lambda: 1 / 0)
    assert log.errors() == {"describe_sound": 1}
    # the model's side of the calls: numbers as it sent them, defaults left out
    trace = [{"tool": "describe_sound", "input": {"start": 5, "end": "6"}},
             {"tool": "rescan_sound", "input": {"start": 1, "end": 2.5}},
             {"tool": "look_frames", "input": {"time": 3}},
             {"tool": "scene_context", "input": {"time": 9}}]                          # never reached the tool
    log.claim(trace)
    assert trace[0]["error"] is True and trace[0]["output"].startswith("ZeroDivisionError")
    assert trace[1]["output"] == "재검출 결과" and "error" not in trace[1]
    assert trace[2]["output"] == "프레임 2장\n00:03.0\n[사진]"
    assert "output" not in trace[3]
    again = [{"tool": "rescan_sound", "input": {"start": 1, "end": 2.5}}]              # a row is given once
    log.claim(again)
    assert "output" not in again[0]


def test_call_log_pairs_the_calls_of_the_attempt_that_answered():
    import time
    log = agent_tools.CallLog()
    args = {"start": 1.0, "end": 2.0, "track": "effects"}
    old = time.time()
    log.run("rescan_sound", {"start": 0.0, "end": 9.0}, lambda: "앞선 재확인의 호출")
    log.rows[-1]["at"] = old - 60.0                                                    # made before this re-check
    with pytest.raises(RuntimeError):                                                  # first attempt of the LLM call
        log.run("describe_sound", args, lambda: (_ for _ in ()).throw(RuntimeError("메모리 부족")))
    log.run("describe_sound", args, lambda: "소리 설명 모델: a knock")                    # the retry made the same call
    trace = [{"tool": "describe_sound", "input": {"start": 1, "end": 2}},
             {"tool": "rescan_sound", "input": {"start": 0, "end": 9}}]
    log.claim(trace, since=old)
    assert trace[0]["output"] == "소리 설명 모델: a knock" and "error" not in trace[0]
    assert "output" not in trace[1]                                                    # not this re-check's call
    assert agent.failed_tools(trace) == []
    # two equal calls in one attempt keep their order: the first one raised, the second answered
    log = agent_tools.CallLog()
    with pytest.raises(RuntimeError):
        log.run("describe_sound", args, lambda: (_ for _ in ()).throw(RuntimeError("메모리 부족")))
    log.run("describe_sound", args, lambda: "소리 설명 모델: a knock")
    trace = [{"tool": "describe_sound", "input": {"start": 1, "end": 2}} for _ in range(2)]
    log.claim(trace)
    assert trace[0].get("error") is True and trace[1]["output"] == "소리 설명 모델: a knock"
    assert agent_tools.same_args({"start": "1.0"}, {"start": 1, "end": 2}) and agent_tools.same_args(None, {"a": 1})
    assert not agent_tools.same_args({"x": 1}, {"start": 1})
    assert not agent_tools.same_args({"track": "mix"}, {"track": "effects"})


def test_call_log_raises_what_the_tool_raised():
    log = agent_tools.CallLog()

    def broken():
        raise RuntimeError("level_zero backend failed with error: 39")
    with pytest.raises(RuntimeError, match="error: 39"):
        log.run("describe_sound", {"start": 1.0, "end": 2.0}, broken)
    assert log.rows[0]["error"] == "RuntimeError: level_zero backend failed with error: 39" and log.rows[0]["output"] is None


def test_tool_server_marks_a_raising_tool_and_the_log_keeps_the_cause():
    """The tools through the MCP library itself (in process: no network, no model). A tool that
    raises reaches the model as an error result (mcp 2.3 passes only 'Error executing tool …');
    why it failed is in the call log."""
    import asyncio

    from mcp import Client

    class Stub:
        def __init__(self):
            self.log = agent_tools.CallLog()

        def rescan_sound(self, start, end):
            return f"재검출 {start}~{end}"

        def describe_sound(self, start, end, track="effects"):
            raise RuntimeError("UR_RESULT_ERROR_OUT_OF_DEVICE_MEMORY")

        def dialogue_around(self, time, before=15.0, after=15.0):
            return f"{time - before}~{time + after}"

    tools = Stub()

    async def model_side():
        async with Client(agent_tools.make_server(tools)) as client:
            names = [t.name for t in (await client.list_tools()).tools]
            ok = await client.call_tool("rescan_sound", {"start": "1", "end": 2.5})        # '1' is read as a number
            around = await client.call_tool("dialogue_around", {"time": 20})              # the defaults are filled in
            bad = await client.call_tool("describe_sound", {"start": 1, "end": 2})
            wrong = await client.call_tool("describe_sound", {"start": "어제", "end": 2})   # never reaches the tool
            return names, ok, around, bad, wrong
    names, ok, around, bad, wrong = asyncio.run(model_side())
    assert names == agent.TOOLS and set(names) == set(agent_tools.TOOL_DOCS)
    assert not ok.is_error and ok.content[0].text == "재검출 1.0~2.5" and around.content[0].text == "5.0~35.0"
    assert bad.is_error and wrong.is_error
    assert [(r["tool"], r["error"]) for r in tools.log.rows] == [
        ("rescan_sound", None), ("dialogue_around", None),
        ("describe_sound", "RuntimeError: UR_RESULT_ERROR_OUT_OF_DEVICE_MEMORY")]
    assert tools.log.rows[1]["args"] == {"time": 20.0, "before": 15.0, "after": 15.0}
    # ... which is how the stage tells the unfinished re-check apart
    trace = [{"tool": "describe_sound", "input": {"start": 1, "end": 2}}]
    tools.log.claim(trace)
    assert agent.failed_tools(trace) == ["describe_sound"]


def test_sound_description_prompt_gives_no_phrase_to_copy():
    q = agent_tools.DESCRIBE_PROMPT.lower()
    assert "for example" not in q and "heavy object" not in q and "falling" not in q
    assert "unclear" in q and agent_tools.DESCRIBE_PROMPT_VERSION >= 2
    assert "무거운 물체" not in agent.SYSTEM and "책 떨어지는" not in agent.prompt(
        {"id": "S1", "kind": "sfx", "onset": 1.0, "t1": 2.0, "phrase": "[소리]", "label": {"top": []}})


def test_tools_show_lines_without_a_time_as_such():
    absent = {"id": "L0003", "start": None, "end": None, "status": "absent", "speaker": "가온", "text": "빠진 장면의 말"}
    assert agent_tools.fmt_line(absent) == "L0003 (영상에 없는 장면) 가온: 빠진 장면의 말"
    assert "(처리 구간 밖)" in agent_tools.fmt_line({**absent, "status": "out_of_range"})
    assert agent_tools.fmt_line(_line(1, 65.25, timing="voice")) == "L0001 01:05.2 (시각 추정) 가온: 지어낸 대사 1"
    assert agent_tools.fmt_line(_line(2, 5.0, speaker=None)) == "L0002 00:05.0 ?: 지어낸 대사 2"
    tools = SimpleNamespace(lines=[absent, _line(1, 65.25), _line(2, 5.0)], _count=lambda name: None)
    assert agent_tools.Tools.dialogue_around(tools, 66.0).splitlines() == ["L0001 01:05.2 가온: 지어낸 대사 1"]
    tools._fmt_line = agent_tools.fmt_line
    assert "L0003 (영상에 없는 장면)" in agent_tools.Tools.search_dialogue(tools, "빠진")


class _FakeTools:
    """The tool set of the agent stage without its models: describe_sound fails while ``broken``."""
    broken = True

    def __init__(self, p):
        self.calls, self.log = {}, agent_tools.CallLog()

    def describe_sound(self, start, end, track="effects"):
        self.calls["describe_sound"] = self.calls.get("describe_sound", 0) + 1
        if type(self).broken and start < 20:
            raise RuntimeError("UR_RESULT_ERROR_OUT_OF_DEVICE_MEMORY")
        return "소리 설명 모델: a short knock"

    def look_frames(self, time, span=2.0, count=4):
        return ["프레임 1장", "00:10.0", object()]

    def slow_load(self):
        return False

    def preload(self):
        pass

    def close(self):
        pass


def test_agent_stage_asks_again_when_a_tool_raised(tmp_path, monkeypatch):
    sounds = [_sound("S0001", 10.0, confident=False, unknown=True, level_jump_db=20.0),
              _sound("S0002", 30.0, confident=False, unknown=True, level_jump_db=20.0)]
    p = _project(tmp_path, sfx={"candidates": sounds})
    made = []

    def fake_serve(tools):
        made.append(tools)
        return "http://127.0.0.1:0/mcp", SimpleNamespace(should_exit=False)

    asked_ids, kwargs = [], {}

    def fake_ask(parts, schema, **kw):
        """The model's turn: it calls describe_sound and look_frames through the tool functions, then answers."""
        kwargs.update(kw)
        iid = kw["tag"].split(":")[1]
        asked_ids.append(iid)
        fns = agent_tools.tool_functions(made[-1])
        t = 10.0 if iid == "S0001" else 30.0
        for name, args in (("describe_sound", {"start": t, "end": t + 1}), ("look_frames", {"time": t})):
            kw["trace"].append({"tool": name, "input": args})
            try:
                fns[name](**args)
            except RuntimeError:
                pass                                # the model only sees 'Error executing tool'
        return {"label_ok": False, "label_ko": "", "ono": "", "source_ko": "", "mood": "", "visible": "애매",
                "visible_note": "", "plot": "모름", "plot_note": "", "recommendation": "check", "short_reason": "종류 불확실",
                "summary": "소리 설명 도구가 오류를 냈다" if iid == "S0001" and _FakeTools.broken else "짧은 두드림",
                "evidence": []}
    monkeypatch.setattr(agent, "Tools", _FakeTools)
    monkeypatch.setattr(agent, "serve", fake_serve)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "ask", fake_ask)
    _null_lock(monkeypatch)
    _FakeTools.broken = True
    res = agent.run(p, {"agent_limit": 2, "jobs": 1})
    assert sorted(asked_ids) == ["S0001", "S0002"] and res["failed"] == 1 and res["tool_failed"] == 1
    assert kwargs["prompt_version"] == agent.PROMPT_VERSION and kwargs["tools"] == agent.TOOLS
    stored = read_json(p.path("agent_results.json"))
    assert list(stored["items"]) == ["S0002"] and stored["items"]["S0002"]["status"] == "done"   # only the finished one
    assert stored["failed"]["S0001"]["status"] == "tool_error" and stored["tool_errors"] == {"describe_sound": 1}
    assert stored["failed"]["S0001"]["tool_errors"][0]["tool"] == "describe_sound"
    done = read_json(p.path("agent_done.json"))
    assert done["failed"] == ["S0001"] and done["tool_failed"] == ["S0001"] and done["total"] == 1
    record = read_json(p.path("agent") / "S0001.json")
    assert record["result"]["status"] == "tool_error" and record["prompt_version"] == agent.PROMPT_VERSION
    assert record["trace"][0]["error"] is True and "OUT_OF_DEVICE_MEMORY" in record["trace"][0]["output"]
    assert record["trace"][1]["output"] == "프레임 1장\n00:10.0\n[사진]"             # what a tool returned is kept
    assert read_json(p.path("agent") / "S0002.json")["trace"][0]["output"] == "소리 설명 모델: a short knock"
    items = {it["id"]: it for it in read_json(p.path("items.json"))["items"]}
    assert "agent" not in items["S0001"] and items["S0001"]["agent_reasons"] and items["S0002"]["agent"]["status"] == "done"

    # next run, the tool works: only the unfinished one is asked, and it did not use up the limit of 2
    _FakeTools.broken = False
    asked_ids.clear()
    res = agent.run(p, {"agent_limit": 2, "jobs": 1})
    assert asked_ids == ["S0001"] and res["failed"] == 0 and res["tool_failed"] == 0
    stored = read_json(p.path("agent_results.json"))
    assert sorted(stored["items"]) == ["S0001", "S0002"] and stored["failed"] == {}
    asked_ids.clear()
    assert agent.run(p, {"agent_limit": 2, "jobs": 1})["ran"] == 0 and asked_ids == []


def test_agent_stage_stops_asking_when_a_tool_keeps_raising(tmp_path, monkeypatch):
    sounds = [_sound(f"S{i:04d}", float(i), confident=False, unknown=True, level_jump_db=20.0) for i in range(1, 7)]
    p = _project(tmp_path, sfx={"candidates": sounds})
    made, asked_ids = [], []

    def fake_serve(tools):
        made.append(tools)
        return "http://127.0.0.1:0/mcp", SimpleNamespace(should_exit=False)

    def fake_ask(parts, schema, **kw):
        iid = kw["tag"].split(":")[1]
        asked_ids.append(iid)
        kw["trace"].append({"tool": "describe_sound", "input": {"start": float(iid[1:]), "end": float(iid[1:]) + 1}})
        try:
            agent_tools.tool_functions(made[-1])["describe_sound"](**kw["trace"][-1]["input"])
        except RuntimeError:
            pass
        return {"label_ok": False, "label_ko": "", "ono": "", "source_ko": "", "mood": "", "visible": "애매",
                "visible_note": "", "plot": "모름", "plot_note": "", "recommendation": "check", "short_reason": "",
                "summary": "도구 오류", "evidence": []}
    monkeypatch.setattr(agent, "Tools", _FakeTools)
    monkeypatch.setattr(agent, "serve", fake_serve)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "ask", fake_ask)
    _null_lock(monkeypatch)
    _FakeTools.broken = True                         # describe_sound raises for every one of them (onsets under 20 s)
    res = agent.run(p, {"agent_limit": 5, "jobs": 1})
    # three in a row were asked for nothing: the other two of the five are not asked in this run
    assert len(asked_ids) == agent.TOOL_ERROR_STOP == 3 and res["ran"] == 3 and res["stopped"] is True
    done = read_json(p.path("agent_done.json"))
    assert sorted(done["failed"]) == sorted(asked_ids) == sorted(done["tool_failed"]) and done["stopped"] is True
    assert len(done["left"]) == 3 and not set(done["left"]) & set(asked_ids)      # 2 not asked + 1 over the limit
    assert read_json(p.path("agent_results.json"))["items"] == {}
    assert all(it["rec"] == "check" and "agent" not in it for it in read_json(p.path("items.json"))["items"])
    # the next run starts again from the whole queue
    _FakeTools.broken = False
    asked_ids.clear()
    res = agent.run(p, {"agent_limit": 5, "jobs": 1})
    assert len(asked_ids) == 5 and res["ran"] == 5 and res["failed"] == 0 and res["stopped"] is False
    assert len(read_json(p.path("agent_results.json"))["items"]) == 5


def test_agent_counts_a_raising_tool_when_the_call_then_fails(tmp_path, monkeypatch):
    """The tool raised and the model never answered (the call failed afterwards): still a tool
    error — counted for TOOL_ERROR_STOP and kept as tool_error, not a plain call error."""
    sounds = [_sound(f"S{i:04d}", float(i), confident=False, unknown=True, level_jump_db=20.0) for i in range(1, 7)]
    p = _project(tmp_path, sfx={"candidates": sounds})
    made, asked_ids = [], []

    def fake_serve(tools):
        made.append(tools)
        return "http://127.0.0.1:0/mcp", SimpleNamespace(should_exit=False)

    def fake_ask(parts, schema, **kw):
        iid = kw["tag"].split(":")[1]
        asked_ids.append(iid)
        kw["trace"].append({"tool": "describe_sound", "input": {"start": float(iid[1:]), "end": float(iid[1:]) + 1}})
        try:
            agent_tools.tool_functions(made[-1])["describe_sound"](**kw["trace"][-1]["input"])
        except RuntimeError:
            pass
        raise llm.LLMError("max turns")                 # the model gave no answer in the end
    monkeypatch.setattr(agent, "Tools", _FakeTools)
    monkeypatch.setattr(agent, "serve", fake_serve)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "ask", fake_ask)
    _null_lock(monkeypatch)
    _FakeTools.broken = True
    res = agent.run(p, {"agent_limit": 5, "jobs": 1})
    assert len(asked_ids) == agent.TOOL_ERROR_STOP and res["stopped"] is True and res["tool_failed"] == 3
    stored = read_json(p.path("agent_results.json"))
    assert stored["items"] == {} and all(stored["failed"][i]["status"] == "tool_error" for i in asked_ids)
    assert stored["failed"][asked_ids[0]]["tool_errors"][0]["tool"] == "describe_sound"
    record = read_json(p.path("agent") / f"{asked_ids[0]}.json")
    assert record["error"] == "max turns" and record["result"]["status"] == "tool_error"
    # a call that fails with no tool error stays a plain error and does not stop the queue
    _FakeTools.broken = False
    asked_ids.clear()
    res = agent.run(p, {"agent_limit": 5, "jobs": 1})
    assert len(asked_ids) == 5 and res["failed"] == 5 and res["tool_failed"] == 0 and res["stopped"] is False
    assert {v["status"] for v in read_json(p.path("agent_results.json"))["failed"].values()} == {"error"}


# ================================================================== tools/inspect_stages.py
def _inspect_tool():
    import importlib.util
    spec = importlib.util.spec_from_file_location("inspect_stages_fa", Path(__file__).resolve().parents[1] / "tools"
                                                  / "inspect_stages.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_inspect_tool_shows_the_cues_and_names_plain_sounds(tmp_path, capsys):
    ins = _inspect_tool()
    assert ins.STAGES.index("cues") == ins.STAGES.index("align") + 1 and set(ins.SHOW) == set(ins.STAGES)
    p = _project(tmp_path, sfx={"candidates": [_unnamed()]})
    write_json(p.path("cues.json"), {"version": 1, "counts": {}, "cues": [
        {"id": "C0001", "type": "sfx", "scene_id": "SC001", "text": "탁자 위의 컵이 쨍그랑 깨진다.", "phrase": "[쨍그랑 - 유리]",
         "window": [12.0, 20.0], "window_by": ["line", "line"]},
        {"id": "C0002", "type": "vocal", "scene_id": "SC001", "speaker": "가온", "text": "한숨", "phrase": "[한숨]",
         "window": [40.0, 80.0], "window_by": ["scene", "line"]},
        {"id": "C0003", "type": "music", "scene_id": "SC009", "text": "지어낸 노래가 흐른다.", "phrase": "[음악]", "window": None}]})
    write_json(p.path("items.json"), {"items": [
        {**_unnamed(), "cue_id": "C0001", "named_by": "script", "rec": "check", "reason": "x", "evidence": []},
        {"id": "C0002", "kind": "sfx", "origin": "script", "cue_id": "C0002", "onset": 41.0, "t0": 41.0, "t1": 42.5,
         "rec": "check", "reason": "x", "evidence": [], "label": {"class": "", "ko": "한숨"}}]})
    c = ins.Ctx(tmp_path)
    ins.SHOW["cues"](c, None, None, False)
    out = capsys.readouterr().out
    assert "단서 3 · 소리 1, 말하는 사람의 소리 1, 음악 1" in out and "시각 범위 있음 2 (범위 끝을 정한 것: 대사 시각 3, 장면 시각 1;" in out
    assert "넓음 1)" in out and "검출 후보에 붙음 1 (이름을 대본에서 받음 1) · 대본만으로 만든 확인 필요 후보 1" in out
    ins.SHOW["cues"](c, 10.0, 15.0, False)                 # around a time: the cues whose window meets it
    out = capsys.readouterr().out
    assert "C0001" in out and "[쨍그랑 - 유리]" in out and "C0002" not in out
    ins.SHOW["cues"](c, None, None, True)                  # the whole stage: also the cue without a window
    out = capsys.readouterr().out
    assert "C0003" in out and "시각 범위 없음" in out
    ins.trace_item(c, "C0002")
    assert "■ cues" in capsys.readouterr().out
    # a class the table does not describe is '소리' in Korean: the list shows which one it is
    assert c.name("Not a class at all") == "소리(Not a class at all)" and c.name("Thunder") == phrases.ko("Thunder")
    assert c.name("Gunshot, gunfire", "총소리") == "총소리"
    (tmp_path / "gone").mkdir()
    ins.SHOW["cues"](ins.Ctx(tmp_path / "gone"), None, None, False)
    assert "결과 없음: cues.json" in capsys.readouterr().out


# ================================================================== 받아쓰기 (1-10, 6-m3, 5-02)
def test_hotwords_are_the_names_said_in_the_dialogue():
    script = {"characters": ["가온", "나래", "친구1", "군인 2", "다솜이", "박 선생님", "솔", "가온"],
              "lines": [{"text": "가온아, 박 선생님 오셨어."}, {"text": "나래 어디 갔어?"}, {"text": "솔직히 몰라."},
                        {"text": "친구1은 없는데"}]}
    # '다솜이' is never said, '친구1' / '군인 2' are numbered labels, '솔' is one letter (and only inside '솔직히')
    assert asr.hotword_names(script) == ["가온", "나래", "박 선생님"]
    assert asr.hotword_names(None) == [] and asr.hotword_names({"characters": ["가온"], "lines": []}) == []
    many = {"characters": [f"이름{chr(0xAC00 + i)}" for i in range(40)],
            "lines": [{"text": " ".join(f"이름{chr(0xAC00 + i)}" for i in range(40))}]}
    assert len(asr.hotword_names(many)) == asr.HOTWORDS_MAX


def test_hint_echo_marks_a_chunk_that_only_repeats_the_names():
    hint = "가온 나래 친구1 박 선생님"
    echo = ["나래", "친구2", "나래", "친구3."]
    assert asr.hint_echo(echo, hint) == [True] * 4                       # numbered variants of a hint count too
    assert asr.hint_echo(["박", "선생님"], hint) == [True, True]          # a whole chunk of hint words
    assert asr.hint_echo(["가온아!"], hint) == [False]                   # a real word
    assert asr.hint_echo(["가온"], hint) == [False]                      # one name called out stays
    assert asr.hint_echo(["가온,", "이리", "와", "봐"], hint) == [False] * 4
    # inside a longer chunk only a long run of several names
    mixed = ["그러니까", "가온", "나래", "가온", "나래", "말이야"]
    assert asr.hint_echo(mixed, hint) == [False, True, True, True, True, False]
    assert asr.hint_echo(["그러니까", "가온", "나래", "말이야"], hint) == [False] * 4
    assert asr.hint_echo(["그래", "가온", "가온", "가온", "가온"], hint) == [False] * 5      # one name, however often
    assert asr.hint_echo(["가온", "나래"], None) == [False, False] and asr.hint_echo([], hint) == []
    # with the names as a list a two-word name is one name
    assert asr.hint_echo(["어", "박", "선생님", "박", "선생님"], ["가온", "박 선생님"]) == [False] * 5
    assert asr.hint_echo(["어", "박", "선생님", "박", "선생님"], "가온 박 선생님") == [False, True, True, True, True]


def test_hint_echo_keeps_a_chunk_that_calls_one_name_again():
    """A chunk of one word said again ('엄마! 엄마!' after a pause of its own) is someone calling
    out, not the hint read back: kept. Two different hint words still are the hint."""
    hint = ["가온", "엄마", "박 선생님", "할머니"]
    assert asr.hint_echo(["엄마!", "엄마!"], hint) == [False, False]
    assert asr.hint_echo(["할머니,", "할머니!", "할머니"], hint) == [False] * 3
    assert asr.hint_echo(["가온", "가온!"], "가온 나래") == [False, False]
    assert asr.hint_echo(["엄마", "가온"], hint) == [True, True]                  # two names
    assert asr.hint_echo(["박", "선생님"], hint) == [True, True]                  # the words of one name of two words
    assert asr.hint_echo(["박", "선생님", "박", "선생님"], hint) == [True] * 4


class _FakeWhisper:
    """model.words() of a made-up transcript per chunk; detect_language from a table."""
    name = "fake"

    def __init__(self, script, langs=None):
        self.script, self.langs, self.calls = script, langs, []
        if langs is not None:
            self.detect_language = lambda clip: self.langs[len(self.calls)]

    def words(self, audio, language, hotwords):
        k = len(self.calls)
        self.calls.append({"n": len(audio), "language": language, "hotwords": hotwords})
        yield from self.script[k]


def test_chunks_keep_their_language_and_drop_the_hint_echo():
    sr = asr.SR
    audio = np.zeros(40 * sr, dtype=np.float32)
    groups = [(1 * sr, 4 * sr), (10 * sr, 13 * sr), (20 * sr, 23 * sr)]
    script = [[(" 어디", 0.5, 0.9, 0.9), (" 가", 0.9, 1.1, None), ("  ", 1.1, 1.2, 0.1)],
              [(" 가온", 0.2, 0.6, 0.5), (" 나래", 0.6, 1.0, 0.5), (" 가온", 1.0, 1.4, 0.5)],
              [(" 금방", 0.3, 0.7, 0.8), (" 올게", 0.7, 1.0, 0.8)]]
    model = _FakeWhisper(script)
    out = asr.transcribe_chunks(audio, groups, model, None, "가온 나래", 100.0, hint_names=["가온", "나래"])
    assert [w["w"] for w in out["words"]] == ["어디", "가", "금방", "올게"]
    assert out["words"][0] == {"w": "어디", "s": 101.4, "e": 101.8, "p": 0.9}      # t0 + chunk start - 0.1 s pad + time
    assert out["words"][1]["p"] is None and out["words"][2]["s"] == pytest.approx(120.2)
    assert [w["w"] for w in out["hint_echo"]] == ["가온", "나래", "가온"] and out["hint_echo"][0]["s"] == pytest.approx(110.1)
    assert out["chunks"] == [
        {"start": 101.0, "end": 104.0, "language": "ko", "detected": False, "words": 2},
        {"start": 110.0, "end": 113.0, "language": "ko", "detected": False, "words": 0, "hint_echo": 3},
        {"start": 120.0, "end": 123.0, "language": "ko", "detected": False, "words": 2}]
    # the calls themselves are what they were: every chunk in the one language, with the hint
    assert [(c["language"], c["hotwords"]) for c in model.calls] == [("ko", "가온 나래")] * 3
    assert model.calls[0]["n"] == int(3.2 * sr)


def test_chunk_language_detection_is_an_option():
    sr = asr.SR
    audio = np.zeros(40 * sr, dtype=np.float32)
    groups = [(1 * sr, 4 * sr), (10 * sr, 13 * sr), (20 * sr, 23 * sr)]
    script = [[(" 어디", 0.5, 0.9, 0.9)], [(" where", 0.2, 0.6, 0.5), (" now", 0.6, 1.0, 0.5)], [(" 올게", 0.7, 1.0, 0.8)]]
    model = _FakeWhisper(script, langs=[("ko", 0.99), ("en", 0.93), ("ja", 0.31)])
    out = asr.transcribe_chunks(audio, groups, model, "ko", "가온 나래", 0.0, detect=True)
    assert [(c["language"], c["hotwords"]) for c in model.calls] == [("ko", "가온 나래"), ("en", None), ("ko", "가온 나래")]
    assert [(c["language"], c["detected"], c.get("prob")) for c in out["chunks"]] == [
        ("ko", True, 0.99), ("en", True, 0.93), ("ko", False, 0.31)]     # too unsure: the run's language is kept
    assert [w["w"] for w in out["words"]] == ["어디", "where", "now", "올게"]
    # off (the default): the model is not asked, even though it could tell
    model = _FakeWhisper(script, langs=[("en", 0.9)] * 3)
    out = asr.transcribe_chunks(audio, groups, model, "ko", None, 0.0)
    assert {c["language"] for c in model.calls} == {"ko"} and not any(c["detected"] for c in out["chunks"])
    # on, with a backend that cannot tell: every chunk in the run's language
    plain = _FakeWhisper(script)
    out = asr.transcribe_chunks(audio, groups, plain, "ko", None, 0.0, detect=True)
    assert [c["language"] for c in out["chunks"]] == ["ko"] * 3 and not hasattr(plain, "detect_language")


def test_asr_stage_passes_the_filtered_names_and_the_option(tmp_path, monkeypatch):
    p = _project(tmp_path, script={"characters": ["가온", "친구1", "다솜이"], "lines": [{"text": "가온아 밥 먹자"}]})
    (tmp_path / "audio").mkdir()
    (tmp_path / "audio" / "speech_16k.wav").write_bytes(b"")
    got = {}

    def fake_transcribe(wav, language, hotwords, t0, dev=None, detect=False, hint_names=None):
        got.update(wav=wav.name, language=language, hotwords=hotwords, detect=detect, hint_names=hint_names)
        return {"language": "ko", "words": [], "vad": [], "chunks": [], "hint_echo": [], "hotwords": hotwords}
    monkeypatch.setattr(asr, "transcribe", fake_transcribe)
    monkeypatch.setattr(asr, "device", lambda: "cpu")
    _null_lock(monkeypatch, asr)
    asr.run(p, {"asr_detect_language": True})
    assert got == {"wav": "speech_16k.wav", "language": "ko", "hotwords": "가온", "detect": True, "hint_names": ["가온"]}
    p.path("asr.json").unlink()
    asr.run(p, {})
    assert got["detect"] is False and read_json(p.path("asr.json"))["hotwords"] == "가온"


# ================================================================== llm (6-12)
def _stream(*msgs) -> str:
    return "\n".join(json.dumps(m, ensure_ascii=False) for m in msgs) + "\nnot json\n"


def test_stream_gives_the_model_id_and_what_the_tools_returned():
    init = {"type": "system", "subtype": "init", "model": "claude-test-4-20260101",
            "mcp_servers": [{"name": "cboe", "status": "connected"}]}
    call = {"type": "assistant", "message": {"model": "claude-test-4-20260101", "content": [
        {"type": "text", "text": "확인해 보겠습니다"},
        {"type": "tool_use", "id": "tu_1", "name": "mcp__cboe__describe_sound", "input": {"start": 1.0, "end": 2.0}},
        {"type": "tool_use", "id": "tu_2", "name": "mcp__cboe__look_frames", "input": {"time": 1.5}},
        {"type": "tool_use", "id": "tu_3", "name": "StructuredOutput", "input": {}}]}}
    back = {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "tu_1", "is_error": True, "content": "Error executing tool describe_sound"},
        {"type": "tool_result", "tool_use_id": "tu_2", "content": [{"type": "text", "text": "프레임 1장"},
                                                                   {"type": "image", "source": {"data": "…"}}]}]}}
    done = {"type": "result", "subtype": "success", "structured_output": {"ok": True},
            "modelUsage": {"claude-test-4-20260101": {}, "claude-small-1": {}}}
    trace: list = []
    result, model_id = llm.parse_stream(_stream(init, call, back, done), trace, require_mcp="cboe")
    assert result["structured_output"] == {"ok": True} and model_id == "claude-test-4-20260101"
    assert trace == [{"tool": "describe_sound", "input": {"start": 1.0, "end": 2.0},
                      "output": "Error executing tool describe_sound", "error": True},
                     {"tool": "look_frames", "input": {"time": 1.5}, "output": "프레임 1장\n[image]"}]
    # the id from the assistant message, or from the result's usage when only one model is named
    assert llm.parse_stream(_stream({"type": "system", "subtype": "init"}, call, done))[1] == "claude-test-4-20260101"
    only = {"type": "result", "subtype": "success", "modelUsage": {"claude-other-9": {}}}
    assert llm.parse_stream(_stream(only))[1] == "claude-other-9"
    assert llm.parse_stream(_stream(done))[1] is None and llm.parse_stream("")[0] is None
    with pytest.raises(llm.LLMError):
        llm.parse_stream(_stream({**init, "mcp_servers": [{"name": "cboe", "status": "failed"}]}), [], require_mcp="cboe")


def test_ask_reads_the_cli_output_into_answer_trace_and_log(tmp_path, monkeypatch):
    """llm.ask with the CLI replaced by canned stream-json output (no process is started)."""
    stream = _stream(
        {"type": "system", "subtype": "init", "model": "claude-test-4-20260101",
         "mcp_servers": [{"name": "cboe", "status": "connected"}]},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "tu_1", "name": "mcp__cboe__rescan_sound", "input": {"start": 1.0, "end": 2.0}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "tu_1", "content": "재검출 결과"}]}},
        {"type": "result", "subtype": "success", "structured_output": {"summary": "끝"}, "num_turns": 3,
         "usage": {"input_tokens": 5, "output_tokens": 7}, "total_cost_usd": 0.01})
    started = []

    def fake_process(cmd, **kw):
        started.append(cmd)
        return SimpleNamespace(stdout=stream.encode("utf-8"), stderr=b"", returncode=0)
    monkeypatch.setattr(llm.subprocess, "run", fake_process)
    trace: list = []
    ans = llm.ask(["질문"], {"type": "object"}, mcp_url="http://127.0.0.1:1/mcp", tools=["rescan_sound"], work=tmp_path,
                  tag="agent:S0001", trace=trace, prompt_version=2)
    assert ans == {"summary": "끝"} and len(started) == 1 and started[0][:2] == ["claude", "-p"]
    assert trace == [{"tool": "rescan_sound", "input": {"start": 1.0, "end": 2.0}, "output": "재검출 결과"}]
    row = json.loads((tmp_path / "llm_log.jsonl").read_text(encoding="utf-8"))
    assert (row["model"], row["model_id"], row["prompt"], row["ok"]) == ("sonnet", "claude-test-4-20260101", "agent/2", True)
    assert (row["turns"], row["in"], row["out"], row["cost_usd"]) == (3, 5, 7, 0.01)


def test_llm_log_has_the_model_id_and_the_prompt_version(tmp_path, monkeypatch):
    def fake_run(cmd, stdin, timeout, trace=None, require_mcp=None):
        return {"subtype": "success", "structured_output": {"items": []}, "usage": {"input_tokens": 3},
                "num_turns": 1, "_model_id": "claude-test-4-20260101"}
    monkeypatch.setattr(llm, "_run", fake_run)
    assert llm.ask(["질문"], {"type": "object"}, work=tmp_path, tag="screen", prompt_version=3) == {"items": []}
    llm.ask(["질문"], {"type": "object"}, work=tmp_path, tag="agent:S0007", prompt_version=2, model="opus")
    llm.ask(["질문"], {"type": "object"}, work=tmp_path, tag="dialog")

    def failing(cmd, stdin, timeout, trace=None, require_mcp=None):
        raise llm.LLMError("no result")
    monkeypatch.setattr(llm, "_run", failing)
    with pytest.raises(llm.LLMError):
        llm.ask(["질문"], {"type": "object"}, work=tmp_path, tag="screen", prompt_version=3, retries=0)
    rows = [json.loads(x) for x in (tmp_path / "llm_log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(r["tag"], r["model"], r["model_id"], r["prompt"], r["ok"]) for r in rows] == [
        ("screen", "sonnet", "claude-test-4-20260101", "screen/3", True),
        ("agent:S0007", "opus", "claude-test-4-20260101", "agent/2", True),
        ("dialog", "sonnet", "claude-test-4-20260101", None, True),
        ("screen", "sonnet", None, "screen/3", False)]
    assert rows[0]["in"] == 3 and rows[0]["turns"] == 1                  # what was logged before is still there


# ================================================================== 문구 표 (4-12)
def test_sound_table_has_no_english_names_and_no_doubled_words():
    table = phrases.table()
    shown = {c: r for c, r in table.items() if r["category"] in ("event", "ambient", "minor", "vocal")}
    assert len(shown) > 300
    no_korean = [c for c, r in shown.items() if not any("가" <= ch <= "힣" for ch in r["desc"])]
    assert no_korean == []                                               # an empty desc showed '[Animal]'
    for cls in ("Animal", "Domestic animals, pets", "Wild animals"):
        assert table[cls]["desc"] == "동물 울음소리" and table[cls]["ono"] == ""
    assert table["Livestock, farm animals, working animals"]["desc"] == "가축 울음소리"
    assert table["Deformable shell"]["desc"] == "바스락거리는 소리" and table["Deformable shell"]["ono"] == "바스락"
    fixed = {"Error signal": "오류음", "Fusillade": "총성", "Gunshot, gunfire": "총성", "Reversing beeps": "후진 경고음",
             "Sonic boom": "음속 폭음", "Whoop": "환호성"}
    assert {c: table[c]["desc"] for c in fixed} == fixed
    assert not [r["desc"] for r in table.values() if r["desc"].endswith(("음 소리", "성 소리"))
                and r["desc"] not in ("신음 소리", "발걸음 소리")]
    assert len(table) == 447 and all(len(r) == 7 for r in table.values())           # no row lost a column
