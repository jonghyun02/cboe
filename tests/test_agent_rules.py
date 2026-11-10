# -*- coding: utf-8 -*-
"""Agent answer normalisation and the LLM-free dialogue rule."""
from __future__ import annotations

from cboe import phrases
from cboe.stages import agent, dialog_ev, recommend


def _sfx():
    return {"id": "S1", "kind": "sfx", "onset": 228.8, "t1": 229.7, "category": "unknown", "level_jump_db": 15.0,
            "phrase": "[소리]", "evidence": [], "agent_reasons": ["목록에 없는 소리"],
            "label": {"class": "Generic impact sounds", "ko": "부딪히는", "score": 0.17, "confident": False,
                      "unknown": True, "competing": [], "top": []}}


def _answer(**kw):
    a = {"label_ok": False, "label_ko": "", "ono": "", "source_ko": "", "mood": "", "visible": "안 보임",
         "visible_note": "어두운 복도",
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
    # 의성어가 기본 문구, 설명은 그다음 선택지 — 쉼표 꼴 '[쿵, 책 떨어지는 소리]'은 만들지 않는다 (II.9)
    assert [o["text"] for o in res["phrases"]] == ["[쿵]", "[책 떨어지는 소리]"]
    assert all(o["short"] == "[쿵]" and o["source"] == "agent" for o in res["phrases"])
    rec, reason, _ = recommend.decide_sfx(_sfx(), {"visible": "애매"}, None, None, res)
    assert rec == "include" and "문구 확인" in reason
    assert res["tools"] == ["describe_sound"]


def test_agent_source_noun_gives_the_hyphen_form():
    res = agent.normalise(_sfx(), _answer(label_ko="책 떨어지는 소리", ono="[쿵]", source_ko="책"), [], 1.0)
    assert [o["text"] for o in res["phrases"]] == ["[쿵 - 책]", "[쿵]", "[책 떨어지는 소리]"]
    assert res["phrases"][0]["short"] == "[쿵]"
    assert not any(", " in o["text"] for o in res["phrases"])
    # 출처만 있거나 자리 채움 말이면 이름으로 치지 않는다; '문 소리'는 명사만 남긴다
    assert "phrases" not in agent.normalise(_sfx(), _answer(label_ko="", ono="쿵", source_ko="책"), [], 1.0)
    assert [o["text"] for o in agent.sound_phrases("문 닫히는 소리", "쾅", "문 소리")][0] == "[쾅 - 문]"
    assert [o["text"] for o in agent.sound_phrases("천둥 소리", "", "모름")] == ["[천둥 소리]"]
    assert [o["text"] for o in agent.sound_phrases("쿵", "쿵", "쿵")] == ["[쿵]"]
    assert "source_ko" in agent.SCHEMA["required"] and "source_ko" in agent.prompt(_sfx())


def test_unnamed_sound_with_written_scream_is_not_excluded():
    # scream written in the dialogue + a crash at the same moment: the rule must not drop the crash
    rec, reason, why = recommend.decide_sfx(_sfx(), {"visible": "보임", "observation": "소리 지르는 얼굴"},
                                            {"relation": "already"}, None)
    assert rec == "check" and "출처 판단 어긋남" in why


def test_music_answer_mood():
    m = {"id": "M1", "kind": "music", "onset": 225.7, "t1": 229.0,
         "music": {"moods": [], "change": "고조되는"}, "phrase": phrases.mood_phrase(None)}
    res = agent.normalise(m, _answer(mood="Scary music", visible="애매"), [], 1.0)
    # the wording itself belongs to phrases.mood_phrase (tested there): the mood alone, then with the change
    plain, rising = phrases.mood_phrase("Scary music"), phrases.mood_phrase("Scary music", "고조되는")
    assert res["mood_ok"] and res["mood"] == "Scary music"
    assert [o["text"] for o in res["phrases"]] == ([plain, rising] if rising != plain else [plain])
    assert phrases.MOODS["Scary music"] in plain and all(o["source"] == "agent" for o in res["phrases"])


def test_dialog_rule_already_and_reaction():
    lines = [{"id": "L1", "start": 230.36, "end": 232.1, "text": "으아아아아"},
             {"id": "L2", "start": 130.0, "end": 131.7, "text": "방금 무슨 소리 안 들렸어요?"}]
    vocal = {"id": "S1", "kind": "sfx", "category": "vocal", "onset": 230.4}
    assert dialog_ev.rule(vocal, lines)["relation"] == "already"
    bang = {"id": "S2", "kind": "sfx", "category": "event", "onset": 128.0}
    r = dialog_ev.rule(bang, lines)
    assert r["relation"] == "reaction" and r["line_id"] == "L2"
