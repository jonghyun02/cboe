# -*- coding: utf-8 -*-
"""Review server (cboe.server): state, decisions, bulk, placement fixes, export, media."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from cboe.server.app import PlacementError, create_app, swap_prev_time  # noqa: E402

DATA = Path(r"C:\projects\barrier_free\data\sdh3")
SRT = DATA / "sub_RIt0WP6hHQE.ko.srt"
VIDEO = DATA / "video_RIt0WP6hHQE.mp4"


def _make_sample(out: Path) -> Path:
    spec = importlib.util.spec_from_file_location("make_sample", ROOT / "tools" / "make_sample.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.OUT = out
    mod.main()
    return out


@pytest.fixture()
def work(tmp_path):
    if not SRT.exists():
        pytest.skip("sample subtitles are not on this PC")
    return _make_sample(tmp_path / "sample")


@pytest.fixture()
def client(work):
    with TestClient(create_app(work)) as c:
        yield c


def review(work: Path) -> dict:
    p = work / "review.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"items": {}, "placement": {}}


def item(state: dict, item_id: str) -> dict:
    return next(it for it in state["items"] if it["id"] == item_id)


# ------------------------------------------------------------------- state
def test_index_and_static(client):
    r = client.get("/")
    assert r.status_code == 200 and "SDH 자막 검수" in r.text
    # relative URLs under a <base>: the same page also works at /p/<id>/ in the studio GUI
    assert 'src="static/app.js?v=' in r.text and '<base href="/">' in r.text and 'data-home=""' in r.text
    assert "{{" not in r.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/app.css").status_code == 200


def test_state_shape(client):
    st = client.get("/api/state").json()
    for key in ("project", "dialogue", "items", "draft", "cuts", "characters", "summary", "export_check"):
        assert key in st
    p = st["project"]
    assert (p["t0"], p["t1"], p["title"]) == (180.0, 265.0, "가즈아 도서관 (예시)")
    sm = st["summary"]
    assert sm["total"] == len(st["items"]) == 18
    assert sm["kind"] == {"sfx": 5, "music": 1, "speaker": 12}
    assert sum(sm["final"].values()) == sm["total"] and sm["decided"] == 0
    assert all("cells" in it and "no_room" in it and "final" in it for it in st["items"])
    assert "words" not in st["dialogue"][0]
    assert st["draft"]["cells"] and set(st["export_check"]) >= {"check", "no_room", "exclude_recommended", "violations"}
    # speaker items point at the draft cell of their dialogue line
    p1 = item(st, "P0001")
    assert p1["cells"] and any("L0001" in c["refs"] for c in st["draft"]["cells"] if c["id"] in p1["cells"])


# --------------------------------------------------------------- decisions
def test_decision_round_trip(client, work):
    st = client.post("/api/items/S0001", json={"decision": "include"}).json()
    assert review(work)["items"]["S0001"]["decision"] == "include"
    s1 = item(st, "S0001")
    assert s1["final"] == "include" and s1["decided"] is True and st["summary"]["decided"] == 1
    assert any("S0001" in c["refs"] for c in st["draft"]["cells"]) or s1["no_room"]   # recomputed with the sound

    st = client.post("/api/items/S0001", json={"decision": None}).json()
    assert "decision" not in review(work)["items"]["S0001"]
    s1 = item(st, "S0001")
    assert s1["final"] == "exclude" and s1["decided"] is False


def test_phrase_edit(client, work):
    body = {"decision": "include", "phrase": " [책 무더기 쏟아지는 소리] ", "short": "[쿵]"}
    st = client.post("/api/items/S0003", json=body).json()
    saved = review(work)["items"]["S0003"]
    assert (saved["decision"], saved["phrase"], saved["short"]) == ("include", "[책 무더기 쏟아지는 소리]", "[쿵]")
    s3 = item(st, "S0003")
    assert (s3["phrase"], s3["short"]) == ("[책 무더기 쏟아지는 소리]", "[쿵]")
    lines = [ln for c in st["draft"]["cells"] if "S0003" in c["refs"] for ln in c["lines"]]
    assert any("[책 무더기 쏟아지는 소리]" in ln or "[쿵]" in ln for ln in lines) or s3["no_room"]


def test_speaker_scope(client, work):
    st = client.post("/api/items/P0002", json={"name": "옴뫄", "scope": "speaker"}).json()
    lines = {ln["id"]: ln for ln in st["dialogue"]}
    for it in st["items"]:
        if it["kind"] != "speaker":
            continue
        script = lines[it["speaker"]["line_id"]]["speaker"]
        assert it["speaker"]["name"] == ("옴뫄" if script == "BJ 옴뫄" else script)
    assert "옴뫄" in st["characters"]
    # line scope touches one line only
    st = client.post("/api/items/P0010", json={"name": "희", "decision": "include", "scope": "line"}).json()
    assert item(st, "P0010")["speaker"]["name"] == "희" and item(st, "P0011")["speaker"]["name"] == "양희"
    assert any(ln.startswith("(희)") for c in st["draft"]["cells"] if "L0010" in c["refs"] for ln in c["lines"])


def test_validation(client, work):
    assert client.post("/api/items/NOPE", json={"decision": "include"}).status_code == 404
    assert client.post("/api/items/S0001", json={"decision": "maybe"}).status_code == 422
    assert client.post("/api/items/S0001", json={}).status_code == 400
    assert client.post("/api/items/P0001", json={"phrase": "[쿵]"}).status_code == 400
    assert client.post("/api/items/S0001", json={"name": "빌"}).status_code == 400
    assert review(work)["items"] == {}


def test_bulk(client, work):
    st = client.get("/api/state").json()
    ids = st["export_check"]["exclude_recommended"]
    assert ids
    r = client.post("/api/items/bulk", json={"ids": ids + ["NOPE"], "decision": "exclude"})
    assert r.status_code == 404 and review(work)["items"] == {}          # nothing half-applied
    st = client.post("/api/items/bulk", json={"ids": ids, "decision": "exclude"}).json()
    assert st["export_check"]["exclude_recommended"] == []
    assert all(review(work)["items"][i]["decision"] == "exclude" for i in ids)
    assert all(item(st, i)["decided"] for i in ids)


# --------------------------------------------------------------- placement
def test_placement_actions(client, work):
    st = client.post("/api/placement/S0005", json={"action": "move", "t": 233.1}).json()
    stored = review(work)["placement"]["S0005"]
    assert (stored["action"], stored["t"]) == ("move", 233.1) and stored.get("fp", "").startswith("sfx@")
    s5 = item(st, "S0005")
    assert s5["decided"] and s5["final"] == "include"
    # a moved sound may join a dialogue cell that starts within half a second of it (memo 5.3 ②)
    assert s5["no_room"] or any(abs(c["start"] - 233.1) <= 0.5 for c in st["draft"]["cells"] if "S0005" in c["refs"])

    assert client.post("/api/placement/S0005", json={"action": "move", "t": 500.0}).status_code == 400
    assert client.post("/api/placement/S0005", json={"action": "move"}).status_code == 422
    assert client.post("/api/placement/P0001", json={"action": "exclude"}).status_code == 400
    assert client.post("/api/placement/S0005", json={"action": "teleport"}).status_code == 422

    client.post("/api/placement/S0005", json={"action": "clear"})
    assert "S0005" not in review(work)["placement"]
    st = client.post("/api/placement/S0005", json={"action": "exclude"}).json()
    assert review(work)["items"]["S0005"]["decision"] == "exclude" and item(st, "S0005")["final"] == "exclude"

    r = client.post("/api/placement/S0004", json={"action": "swap_prev"})
    if r.status_code == 200:                                       # depends on the draft around 03:50
        pl = review(work)["placement"]["S0004"]
        assert pl["action"] == "move" and pl["t"] < 230.4
    else:
        assert r.status_code == 409 and "빈틈" in r.json()["detail"]


def test_swap_prev_time():
    fps = 24.0
    cells = [{"id": "C1", "start": 10.0, "end": 12.0, "refs": ["L1"]},
             {"id": "C2", "start": 15.0, "end": 17.0, "refs": ["L2"]}]
    it = {"id": "S9", "onset": 17.2, "t0": 17.2, "short": "[쿵]", "phrase": "[쿵]"}
    draft = {"cells": cells, "no_room": [{"item": "S9", "t": 17.2, "text": "[쿵]", "reason": "x"}]}
    t = swap_prev_time(draft, it, fps, 0.0)
    want = 2 / 14 + 1                                               # '[쿵]' = 2 reading chars
    assert 12.0 < t and t + want <= 15.0 - 2 / fps + 1e-6
    tight = {"cells": [dict(cells[0], end=14.9), cells[1]], "no_room": draft["no_room"]}
    with pytest.raises(PlacementError, match="빈틈"):
        swap_prev_time(tight, it, fps, 0.0)
    with pytest.raises(PlacementError, match="앞 칸"):
        swap_prev_time({"cells": [], "no_room": []}, it, fps, 0.0)


def test_state_survives_placement_error(work, monkeypatch):
    from cboe import placement

    def broken(*a, **k):
        raise RuntimeError("engine under construction")

    monkeypatch.setattr(placement, "place", broken)
    (work / "draft.json").unlink(missing_ok=True)
    with TestClient(create_app(work)) as c:
        st = c.get("/api/state").json()
        assert "engine under construction" in st["error"] and st["draft"]["cells"] == []
        st = c.post("/api/items/S0001", json={"decision": "include"}).json()
        assert review(work)["items"]["S0001"]["decision"] == "include"    # the decision is kept anyway
        assert item(st, "S0001")["final"] == "include" and st["error"]


# ------------------------------------------------------------------ export
def test_export(client, work):
    r = client.post("/api/export", json={"format": "srt"}).json()
    path = Path(r["path"])
    assert path.is_file() and path.parent == work / "export" and r["url"] == "api/export/srt"
    text = path.read_text(encoding="utf-8")
    assert "-->" in text and "(BJ 옴뫄) 흐윽 후윽" in text
    assert any("확인 필요" in w for w in r["warnings"]) and r["state"]["project"]["title"]
    d = client.get("/api/export/srt")
    assert d.status_code == 200 and "filename*=utf-8''" in d.headers["content-disposition"]
    assert d.content == path.read_bytes()

    r = client.post("/api/export", json={"format": "vtt"}).json()
    assert Path(r["path"]).read_text(encoding="utf-8").startswith("WEBVTT")
    assert client.get("/api/export/vtt").headers["content-type"].startswith("text/vtt")
    assert client.post("/api/export", json={"format": "ass"}).status_code == 422


# ------------------------------------------------------------------- media
def test_video_range(work, tmp_path):
    clip = tmp_path / "clip.mp4"
    data = bytes(range(256)) * 256                                  # 64 KiB stand-in, Range logic only
    clip.write_bytes(data)
    meta = json.loads((work / "project.json").read_text(encoding="utf-8"))
    meta["video"] = str(clip)
    (work / "project.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    with TestClient(create_app(work)) as c:
        r = c.get("/media/video", headers={"Range": "bytes=100-1123"})
        assert r.status_code == 206
        assert r.headers["content-range"] == f"bytes 100-1123/{len(data)}"
        assert r.content == data[100:1124]
        full = c.get("/media/video")
        assert full.status_code == 200 and len(full.content) == len(data)
        assert full.headers["content-type"] == "video/mp4" and full.headers.get("accept-ranges") == "bytes"


def test_video_missing(work):
    meta = json.loads((work / "project.json").read_text(encoding="utf-8"))
    meta["video"] = str(work / "nope.mp4")
    (work / "project.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    with TestClient(create_app(work)) as c:
        assert c.get("/media/video").status_code == 404
        assert c.get("/api/frame", params={"t": 200}).status_code == 404
        assert c.get("/api/state").json()["project"]["video"] is False


@pytest.mark.skipif(not VIDEO.exists(), reason="sample video is not on this PC")
def test_frame(client):
    import cv2
    import numpy as np

    r = client.get("/api/frame", params={"t": 228.65, "w": 320})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content[:2] == b"\xff\xd8"
    img = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
    assert img.shape[1] == 320
    # outside the project window the frame is clamped to the window edge, never decoded there
    late = client.get("/api/frame", params={"t": 5000, "w": 320})
    edge = client.get("/api/frame", params={"t": 265.0 - 1 / 23.976, "w": 320})
    assert late.status_code == 200 and late.content == edge.content
    assert client.get("/api/frame", params={"t": "nan"}).status_code == 400


def test_cli_rejects_folder_without_project(tmp_path):
    from cboe.server.__main__ import main

    with pytest.raises(SystemExit) as e:
        main([str(tmp_path)])
    assert e.value.code == 2
