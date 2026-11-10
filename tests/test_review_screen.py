# -*- coding: utf-8 -*-
"""What the review screen reads besides the state: the sound level of the tracks for the
timeline (/api/envelope, 감사 9-x3). The levels here are made up."""
from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from cboe.server.app import ENV_FLOOR_DB, ENV_MAX_BUCKETS, create_app, level_buckets
from cboe.util import write_json


def folder(tmp_path, t0=0.0, t1=10.0, env=True, e0=0.0):
    w = tmp_path / "work"
    w.mkdir()
    write_json(w / "project.json", {"title": "세기 시험", "t0": t0, "t1": t1, "fps": 25.0, "duration": t1})
    if env:
        n = 1000                                          # 10 s at 100 Hz
        speech = np.full(n, -60.0, dtype=np.float32)
        speech[250:260] = -12.0                           # a loud moment at 2.5 s
        music = np.linspace(-80, -20, n).astype(np.float32)
        effects = np.full(n, -70.0, dtype=np.float32)
        effects[500] = np.nan
        np.savez(w / "env.npz", hop=np.float32(0.01), t0=np.float32(e0), speech=speech, music=music, effects=effects)
    return w


def test_envelope_gives_the_loudest_level_of_each_step(tmp_path):
    c = TestClient(create_app(folder(tmp_path)))
    r = c.get("/api/envelope", params={"step": 0.5})
    assert r.status_code == 200
    env = r.json()
    assert env["t0"] == 0.0 and env["step"] == pytest.approx(0.5)
    sp = env["tracks"]["speech"]
    assert len(sp) == 20 and sp[5] == -12 and sp[4] == -60 and sp[6] == -60
    mu = env["tracks"]["music"]
    assert mu == sorted(mu) and mu[-1] == -20                 # rising: the last value of each step
    assert env["tracks"]["effects"][10] == -70                 # NaN does not spread into the step


def test_envelope_follows_the_processed_part_and_the_file_start(tmp_path):
    # the processed part starts at 2 s and the level file at 1 s: bucket 0 is 2.0~2.5 s
    w = folder(tmp_path, t0=2.0, t1=12.0, e0=1.0)
    env = level_buckets(w / "env.npz", 2.0, 12.0, 0.5)
    sp = env["tracks"]["speech"]
    assert len(sp) == 20
    assert sp[3] == -12                                       # 3.5~4.0 s = file 2.5~3.0 s
    assert sp[-1] == ENV_FLOOR_DB                             # after the file ends


def test_envelope_never_sends_more_than_the_bucket_cap(tmp_path):
    w = folder(tmp_path)
    env = level_buckets(w / "env.npz", 0.0, 10.0, 0.0001)
    assert len(env["tracks"]["speech"]) <= ENV_MAX_BUCKETS
    assert env["step"] >= 0.01


def test_envelope_without_the_sed_stage_is_404(tmp_path):
    c = TestClient(create_app(folder(tmp_path, env=False)))
    r = c.get("/api/envelope")
    assert r.status_code == 404 and "env.npz" in r.json()["detail"]
    assert c.get("/api/envelope", params={"step": 0}).status_code == 422


@pytest.mark.parametrize("env", [True, False])
def test_the_state_says_whether_there_is_a_level_curve(tmp_path, env):
    # the page asks for the curve only when there is one (no 404 in the browser's console)
    w = folder(tmp_path, env=env)
    write_json(w / "dialogue.json", {"lines": []})
    st = TestClient(create_app(w)).get("/api/state").json()
    assert st["project"]["envelope"] is env
