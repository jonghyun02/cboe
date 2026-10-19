# -*- coding: utf-8 -*-
"""Regression tests for the independent review of 2026-10-19 (HIGH 1-3, MED 4 and 7, LOW 11)."""
from __future__ import annotations

import numpy as np

from cboe import pipeline
from cboe.state import Project
from cboe.stages import align, music, recommend
from cboe.util import read_json, write_json


def _project(tmp_path, music_items=(), sfx=(), lines=(), items=None):
    write_json(tmp_path / "project.json", {"title": "t", "video": "x.mp4", "t0": 0.0, "t1": 100.0, "fps": 24.0})
    write_json(tmp_path / "music.json", {"items": list(music_items)})
    write_json(tmp_path / "sfx.json", {"candidates": list(sfx)})
    write_json(tmp_path / "dialogue.json", {"lines": list(lines)})
    write_json(tmp_path / "speaker.json", {"lines": []})
    write_json(tmp_path / "cuts.json", {"cuts": []})
    if items is not None:
        write_json(tmp_path / "items.json", {"items": items})
    return Project(tmp_path)


def _mood(key, score):
    return {"key": key, "ko": "무서운" if key == "Scary music" else "x", "score": score, "raw": 0.01}


def test_music_cue_after_unsure_start_does_not_crash(tmp_path):
    # HIGH 1: start is 분위기 애매 (no included music yet), a cue 3 s later has the same top mood
    unsure = [_mood("Scary music", 0.34), _mood("Sad music", 0.30)]
    items = [{"kind": "start", "onset": 10.0, "segment": [10.0, 40.0], "moods": unsure, "mood_strength": 0.02,
              "instruments": [], "change": None, "music_score": 0.5},
             {"kind": "cue", "onset": 13.0, "segment": [10.0, 40.0], "moods": unsure, "mood_strength": 0.02,
              "instruments": [], "change": None, "music_score": 0.5}]
    p = _project(tmp_path, music_items=items)
    out = [it for it in recommend.build(p, {}) if it["kind"] == "music"]
    assert [it["rec"] for it in out] == ["check", "exclude"]
    assert any(e["kind"] == "자막" for e in out[1]["evidence"])


def test_overlapping_voice_regions_merge_whatever_kind():
    # HIGH 2: speech / vocal / speech regions over the same moment are one event
    regs = [[426.16, 426.59, 0.5, "speech"], [426.17, 426.74, 0.2, "vocal"], [426.172, 426.55, 0.3, "speech"],
            [430.0, 431.0, 0.5, "speech"]]
    out = align.merge_regions(regs)
    assert len(out) == 2
    assert out[0][0] == 426.16 and out[0][1] == 426.74 and out[0][3] == "vocal"


def test_force_clears_dependent_stages():
    # HIGH 3: re-detecting sounds renumbers ids, so everything keyed by them goes too
    dep = pipeline.dependents(["sfx"])
    assert dep == ["sfx", "screen", "dialog_ev", "recommend"]
    assert "music" not in dep and "separate" not in dep
    assert pipeline.dependents(["align"]) == ["align", "speaker", "dialog_ev", "recommend"]
    # finished re-checks are kept (matched by fingerprint); only --force agent clears them
    assert "agent" not in pipeline.dependents(["recommend", "sfx"])


def test_agent_result_of_another_candidate_is_ignored(tmp_path):
    sfx = [{"id": "S0001", "kind": "sfx", "onset": 10.0, "t1": 11.0, "category": "event", "level_jump_db": 0.0,
            "phrases": [{"text": "[천둥 소리]", "short": "[우르릉]"}], "phrase": "[천둥 소리]", "short": "[우르릉]",
            "flags": [], "label": {"class": "Thunder", "ko": "천둥", "score": 0.5, "confident": True,
                                    "unknown": False, "competing": [], "path_ko": ["천둥"]}}]
    p = _project(tmp_path, sfx=sfx)
    stale = {"S0001": {"status": "done", "fp": "sfx@99.00", "visible": "보임", "visible_note": "엉뚱한 근거",
                       "evidence": [], "summary": "다른 소리"}}
    it = next(x for x in recommend.build(p, stale) if x["id"] == "S0001")
    assert "agent" not in it and it["rec"] != "exclude"


def _sfx_item(iid, onset, rec="include"):
    return {"id": iid, "kind": "sfx", "onset": onset, "t1": onset + 1, "rec": rec, "phrase": "[쿵]", "short": "[쿵]",
            "evidence": [], "label": {"class": "Thump, thud"}}


def test_decision_follows_the_candidate_not_the_id(tmp_path):
    p = _project(tmp_path, items=[_sfx_item("S0001", 10.0)])
    p.set_decision("S0001", decision="exclude", phrase="[쾅]")
    # the candidate list changes: a new sound before it takes the id S0001
    write_json(tmp_path / "items.json", {"items": [_sfx_item("S0001", 5.0), _sfx_item("S0002", 10.0)]})
    eff = {it["id"]: it for it in p.effective_items()}
    assert eff["S0001"]["final"] == "include" and eff["S0001"]["phrase"] == "[쿵]"
    assert eff["S0002"]["final"] == "exclude" and eff["S0002"]["phrase"] == "[쾅]"


def test_new_decision_after_renumbering_keeps_the_earlier_phrase(tmp_path):
    # HIGH 3 (re-review): after ids shift, a decision-only update (I/E key) must start from the
    # decision the screen shows, not from whatever is stored under the new id
    p = _project(tmp_path, items=[_sfx_item("S0001", 10.0)])
    p.set_decision("S0001", decision="include", phrase="[쾅, 문 닫히는 소리]", short="[쾅]")
    p.set_placement("S0001", "move", 10.5)
    write_json(tmp_path / "items.json", {"items": [_sfx_item("S0001", 5.0), _sfx_item("S0002", 10.0)]})
    p.set_decision("S0002", decision="include")
    eff = {it["id"]: it for it in p.effective_items()}
    assert eff["S0002"]["phrase"] == "[쾅, 문 닫히는 소리]" and eff["S0002"]["final"] == "include"
    assert eff["S0001"]["phrase"] == "[쿵]" and not eff["S0001"]["decided"]
    assert eff["S0002"]["placement"]["t"] == 10.5
    p.set_placement("S0002", "clear")
    eff = {it["id"]: it for it in p.effective_items()}
    assert "placement" not in eff["S0002"]
    assert list(read_json(tmp_path / "review.json")["placement"]) == []


def test_renumbering_chain_never_overwrites_another_candidates_decision(tmp_path):
    # HIGH 3 (2nd re-review): A excluded, C included with a phrase; a new B before both shifts
    # every id by one (A takes C's old id). Excluding A again must not touch C's decision.
    p = _project(tmp_path, items=[_sfx_item("S0001", 10.0), _sfx_item("S0002", 20.0)])
    p.set_decision("S0001", decision="exclude")
    p.set_decision("S0002", decision="include", phrase="[C 문구]")
    p.set_placement("S0002", "move", 20.5)
    write_json(tmp_path / "items.json", {"items": [_sfx_item("S0001", 5.0), _sfx_item("S0002", 10.0),
                                                   _sfx_item("S0003", 20.0)]})
    p.set_decision("S0002", decision="exclude")                 # A, now S0002
    p.set_placement("S0001", "clear")                           # B never had one: nothing else moves
    eff = {it["id"]: it for it in p.effective_items()}
    assert eff["S0002"]["final"] == "exclude"
    assert eff["S0003"]["final"] == "include" and eff["S0003"]["phrase"] == "[C 문구]"
    assert eff["S0003"]["placement"]["t"] == 20.5
    assert not eff["S0001"]["decided"]
    # a candidate that disappears keeps its decision aside and gets it back when it returns
    write_json(tmp_path / "items.json", {"items": [_sfx_item("S0001", 10.0)]})
    p.set_decision("S0001", decision="exclude")
    write_json(tmp_path / "items.json", {"items": [_sfx_item("S0001", 10.0), _sfx_item("S0002", 20.0)]})
    eff = {it["id"]: it for it in p.effective_items()}
    assert eff["S0002"]["phrase"] == "[C 문구]" and eff["S0002"]["final"] == "include"


def test_music_cue_survives_a_gradual_rise_and_a_segment_past_the_data():
    hop = 0.01
    lev = np.full(3000, -40.0, dtype=np.float32)
    ramp = np.linspace(-40.0, -10.0, 60)        # 0.6 s rise passing through the 6-9 dB band
    lev[1500:1560] = ramp
    lev[1560:] = -10.0
    cues = music._cue_onsets(lev, hop, 0.0, 0.0, 40.0)   # segment end beyond the level data
    assert len(cues) == 1 and 15.0 <= cues[0] <= 15.6


def _speaker_item(iid, line_id, name):
    return {"id": iid, "kind": "speaker", "onset": 1.0, "t1": 2.0, "rec": "include", "phrase": f"대사 {iid}",
            "evidence": [], "speaker": {"line_id": line_id, "name": name, "face": "not_visible"}}


def test_scope_speaker_leaves_renamed_lines_alone(tmp_path):
    # MED 4: L2 was renamed to 영희 (this line only); renaming 빌 everywhere must not touch it
    lines = [{"id": f"L{i}", "speaker": "빌", "text": f"대사 P{i}", "start": i, "end": i + 0.5} for i in (1, 2, 3)]
    items = [_speaker_item(f"P{i}", f"L{i}", "빌") for i in (1, 2, 3)]
    p = _project(tmp_path, lines=lines, items=items)
    p.set_decision("P2", name="영희", scope="line")
    p.set_decision("P1", name="빌 게이츠", scope="speaker")
    eff = {it["id"]: it["speaker"]["name"] for it in p.effective_items()}
    assert eff == {"P1": "빌 게이츠", "P2": "영희", "P3": "빌 게이츠"}


def test_music_cue_needs_a_rising_edge():
    # MED 7: a rise already under way when the search starts (segment start + 3 s) is not a cue
    hop, t0 = 0.01, 0.0
    lev = np.full(3000, -60.0, dtype=np.float32)
    lev[250:] = -20.0                       # music swells from 2.5 s on (segment starts at 0)
    lev[1800:] = 0.0                        # a real new cue at 18 s
    cues = music._cue_onsets(lev, hop, t0, 0.0, 30.0)
    assert all(abs(c - 3.0) > 0.5 for c in cues)
    assert any(abs(c - 18.0) < 0.2 for c in cues)


def test_vocal_sound_at_line_start_belongs_to_the_speaker(tmp_path):
    # LOW 11: a laugh at the start of a line is that speaker's sound (placement rule ③)
    sfx = [{"id": "S0001", "kind": "sfx", "onset": 20.1, "t1": 20.6, "category": "vocal", "level_jump_db": 5.0,
            "phrases": [{"text": "[웃음]", "short": "[웃음]"}], "phrase": "[웃음]", "short": "[웃음]", "flags": [],
            "label": {"class": "Laughter", "ko": "웃음", "score": 0.6, "confident": True, "unknown": False,
                      "competing": [], "path_ko": ["웃음"]}}]
    lines = [{"id": "L0001", "speaker": "빌", "text": "나도야", "start": 20.0, "end": 21.0}]
    p = _project(tmp_path, sfx=sfx, lines=lines)
    it = next(x for x in recommend.build(p, {}) if x["id"] == "S0001")
    assert it["source_line"] == "L0001"
    assert read_json(tmp_path / "project.json")["t1"] == 100.0
