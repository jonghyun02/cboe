# -*- coding: utf-8 -*-
"""화면 질문 (cboe.stages.screen, 감사 4-08): the model is asked whether the picture alone tells that
the sound was made — not whether its source is on screen (Netflix II.9 "Describe sound effects when
they cannot be visually identified", 화면으로 알 수 없는 효과음을 설명한다). The answer keeps its shape;
answers asked with the older question are kept, not asked again. No LLM is called: the model's
answers are made up."""
from __future__ import annotations

import numpy as np
import pytest

from cboe import llm
from cboe.stages import screen
from cboe.state import Project
from cboe.util import read_json, write_json


def _sound(iid, onset):
    return {"id": iid, "kind": "sfx", "onset": onset, "t0": onset, "t1": onset + 1.0, "category": "event",
            "label": {"class": "Knock", "ko": "노크", "score": 0.6, "confident": True, "unknown": False, "top": []}}


def _project(tmp_path, sounds, seen=None) -> Project:
    write_json(tmp_path / "project.json", {"title": "t", "video": "x.mp4", "t0": 0.0, "t1": 100.0, "fps": 24.0,
                                           "width": 64, "height": 36})
    write_json(tmp_path / "sfx.json", {"candidates": sounds})
    write_json(tmp_path / "cuts.json", {"cuts": []})
    if seen is not None:
        write_json(tmp_path / "screen.json", seen)
    return Project(tmp_path)


class _Frames:
    def frames_at(self, times, max_width=None):
        return [np.zeros((36, 64, 3), np.uint8) for _ in times]


def test_the_question_is_whether_the_picture_alone_tells_the_sound(monkeypatch, tmp_path):
    got = {}

    def fake_ask(parts, schema, **kw):
        got.update(kw, parts=parts, schema=schema)
        return {"items": [{"id": "S0001", "visible": "안 보임", "observation": "문은 보이나 소리는 알 수 없음",
                           "reaction": "", "likely_source": "문"}]}
    monkeypatch.setattr(llm, "ask", fake_ask)
    monkeypatch.setattr(screen, "jpeg_bytes", lambda fr, q=85: b"jpeg")
    out = screen._batch_call([_sound("S0001", 10.0)], _Frames(), [], {}, tmp_path)
    assert out["S0001"]["visible"] == "안 보임"
    system = got["system"]
    assert "이 화면만 보고(소리 없이) 이 소리가 났다는 것을 알 수 있는지" in system
    assert "출처나 동작이 화면에 보이는지" not in system                     # the question of version 1
    assert "화면에 있어도" in system                                          # a source on screen is not enough
    assert "소리 없이" in got["parts"][0]
    assert got["tag"] == "screen" and got["prompt_version"] == screen.PROMPT_VERSION == 2
    # the answer keeps the shape the recommendation reads
    row = got["schema"]["properties"]["items"]["items"]
    assert row["required"] == ["id", "visible", "observation", "reaction", "likely_source"]
    assert row["properties"]["visible"]["enum"] == ["보임", "안 보임", "애매"]


@pytest.fixture
def asked(monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "available", lambda: True)

    def batch(b, m, cuts, cfg, work):
        calls.append([c["id"] for c in b])
        return {c["id"]: {"id": c["id"], "visible": "안 보임", "observation": "빈 방", "reaction": "", "likely_source": ""}
                for c in b}
    monkeypatch.setattr(screen, "_batch_call", batch)
    return calls


def _old_answer(iid):
    return {"id": iid, "visible": "보임", "observation": "문이 보임", "reaction": "", "likely_source": "문",
            "source": "vlm", "frames": [9.75, 10.15, 10.9]}


def test_answers_to_the_older_question_are_kept_and_not_asked_again(tmp_path, asked):
    old = {"items": {"S0001": _old_answer("S0001"), "S0002": _old_answer("S0002")}, "llm": True, "errors": 0,
           "complete": True, "scope": "all", "prompt_version": 1}
    p = _project(tmp_path, [_sound("S0001", 10.0), _sound("S0002", 20.0)], old)
    res = screen.run(p, {})
    assert asked == [] and res["skipped"] and res["older_prompt"] == 2
    assert read_json(p.path("screen.json")) == old                             # untouched
    # a new candidate is asked with the new question; the old answers stay as they were
    write_json(p.path("sfx.json"), {"candidates": [_sound("S0001", 10.0), _sound("S0002", 20.0), _sound("S0003", 30.0)]})
    res = screen.run(p, {})
    assert asked == [["S0003"]] and res["asked"] == 1 and res["older_prompt"] == 2
    items = read_json(p.path("screen.json"))["items"]
    assert items["S0003"]["prompt_version"] == screen.PROMPT_VERSION
    assert items["S0001"] == old["items"]["S0001"] and items["S0002"] == old["items"]["S0002"]
    assert screen.older_answers(items) == 2


def test_a_new_work_folder_is_asked_with_the_new_question(tmp_path, asked):
    p = _project(tmp_path, [_sound("S0001", 10.0), _sound("S0002", 20.0)])
    res = screen.run(p, {})
    assert asked == [["S0001", "S0002"]] and res["older_prompt"] == 0
    seen = read_json(p.path("screen.json"))
    assert seen["prompt_version"] == 2 and {it["prompt_version"] for it in seen["items"].values()} == {2}
    assert screen.run(p, {})["skipped"] and len(asked) == 1
