# -*- coding: utf-8 -*-
"""Agent answer normalisation and the LLM-free dialogue rule."""
from __future__ import annotations

from cboe.stages import agent, dialog_ev, recommend


def _sfx():
    return {"id": "S1", "kind": "sfx", "onset": 228.8, "t1": 229.7, "category": "unknown", "level_jump_db": 15.0,
            "phrase": "[소리]", "evidence": [], "agent_reasons": ["목록에 없는 소리"],
            "label": {"class": "Generic impact sounds", "ko": "부딪히는", "score": 0.17, "confident": False,
                      "unknown": True, "competing": [], "top": []}}


def _answer(**kw):
    a = {"label_ok": False, "label_ko": "", "ono": "", "mood": "", "visible": "안 보임", "visible_note": "어두운 복도",
         "plot": "필요", "plot_note": "비명 직전 큰 소리", "recommendation": "include", "short_reason": "출처 안 보임",
         "summary": "요약", "evidence": [{"kind": "화면", "t": 228.7, "text": "출처 안 보임"}]}
    a.update(kw)
    return a


def test_placeholder_label_is_not_a_name():
    res = agent.normalise(_sfx(), _answer(label_ko="[소리]"), [], 1.0)
    assert "phrases" not in res
    rec, reason, _ = recommend.decide_sfx(_sfx(), {"visible": "애매"}, None, None, res)
    assert rec == "check"


def test_agent_name_gives_phrases_and_include_with_phrase_check():
    res = agent.normalise(_sfx(), _answer(label_ko="책 떨어지는 소리", ono="쿵"), [{"tool": "describe_sound"}], 1.0)
    assert res["phrases"][0]["text"] == "[쿵, 책 떨어지는 소리]" and res["phrases"][0]["short"] == "[쿵]"
    rec, reason, _ = recommend.decide_sfx(_sfx(), {"visible": "애매"}, None, None, res)
    assert rec == "include" and "문구 확인" in reason
    assert res["tools"] == ["describe_sound"]


def test_unnamed_sound_with_written_scream_is_not_excluded():
    # scream written in the dialogue + a crash at the same moment: the rule must not drop the crash
    rec, reason, why = recommend.decide_sfx(_sfx(), {"visible": "보임", "observation": "소리 지르는 얼굴"},
                                            {"relation": "already"}, None)
    assert rec == "check" and "출처 판단 어긋남" in why


def test_music_answer_mood():
    m = {"id": "M1", "kind": "music", "onset": 225.7, "t1": 229.0,
         "music": {"moods": [], "change": "고조되는"}, "phrase": "♬ 음악"}
    res = agent.normalise(m, _answer(mood="Scary music", visible="애매"), [], 1.0)
    assert res["mood_ok"] and res["phrases"][0]["text"] == "♬ 무서운 음악"
    assert res["phrases"][1]["text"] == "♬ 고조되는 무서운 음악"


def test_dialog_rule_already_and_reaction():
    lines = [{"id": "L1", "start": 230.36, "end": 232.1, "text": "으아아아아"},
             {"id": "L2", "start": 130.0, "end": 131.7, "text": "방금 무슨 소리 안 들렸어요?"}]
    vocal = {"id": "S1", "kind": "sfx", "category": "vocal", "onset": 230.4}
    assert dialog_ev.rule(vocal, lines)["relation"] == "already"
    bang = {"id": "S2", "kind": "sfx", "category": "event", "onset": 128.0}
    r = dialog_ev.rule(bang, lines)
    assert r["relation"] == "reaction" and r["line_id"] == "L2"
