# -*- coding: utf-8 -*-
"""Review server (cboe.server): state, decisions, bulk, placement fixes, export, media."""
from __future__ import annotations

import gc
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


def test_state_carries_style_and_guide_check(client):
    st = client.get("/api/state").json()
    # the limits and the notation the page shows come from the server (cboe.style), not from the page
    style = st["style"]
    assert (style["profile"], style["cps"], style["max_units"], style["max_lines"]) == ("adult", 14.0, 16.0, 2)
    assert (style["open"], style["close"], style["note"], style["music"]) == ("[", "]", "♪", "음악")
    assert (style["joint_sep"], style["joint_all"]) == (", ", "함께")
    assert style["formats"] == ["srt", "vtt", "ttml"] and st["project"]["profile"] == "adult"
    # the independent guide check of the draft: cells that break an obligation / a recommendation
    guide = st["export_check"]["guide"]
    sm = st["summary"]
    assert sm["guide"] == {"must": guide["summary"]["cells_must"], "should": guide["summary"]["cells_should"],
                           "must_items": guide["summary"]["must"], "should_items": guide["summary"]["should"]}
    assert set(sm["flags"]) == {"timing_estimated", "overlap", "flash"} and guide["fps"] == st["project"]["fps"]
    assert guide["cut_checks"] and guide["profile"] == "adult"
    assert all(v["level"] in ("must", "should") and v["clause"] for v in guide["violations"])
    codes = {v["code"] for v in guide["violations"]}
    assert not codes & {"label_parens", "note_glyph", "line_end_punct", "sentence_period", "dots"}   # today's notation
    draft = st["draft"]
    assert draft["profile"] == "adult" and draft["notation"] == 2
    assert all(c["pos"] == "bottom" and isinstance(c["flags"], list) for c in draft["cells"])


def test_old_notation_is_shown_in_todays(client):
    # the sample folder is written like an older work folder: music phrases as '♬ …'
    st = client.get("/api/state").json()
    m1 = item(st, "M0001")
    assert m1["phrase"] == "[무서운 음악]" and m1["short"] == "[무서운 음악]"
    assert [p["text"] for p in m1["phrases"]] == ["[무서운 음악]", "[고조되는 무서운 음악]", "[음악]"]
    assert not any("♬" in ln for c in st["draft"]["cells"] for ln in c["lines"])
    assert any(c["lines"] == ["[무서운 음악]"] or "- [무서운 음악]" in c["lines"] for c in st["draft"]["cells"]) or m1["no_room"]
    # speaker items carry the label placement writes: [이름]
    p1 = item(st, "P0001")
    assert p1["speaker"]["label"] == "[BJ 옴뫄]" and p1["speaker"]["label_tail"] == ""
    named = [ln for c in st["draft"]["cells"] for ln in c["lines"] if ln.startswith(("[BJ 옴뫄] ", "- [BJ 옴뫄] "))]
    assert named and not any(ln.startswith(("(", "- (")) for c in st["draft"]["cells"] for ln in c["lines"])
    # a phrase the writer once confirmed in the old notation comes out as a label too
    old = {"decision": "include", "phrase": "♬ 고조되는 무서운 음악", "short": "♬ 음악"}
    st = client.post("/api/items/M0001", json=old).json()
    assert item(st, "M0001")["phrase"] == "[고조되는 무서운 음악]"
    assert not any("♬" in ln for c in st["draft"]["cells"] for ln in c["lines"])


def test_children_profile_from_project_json(work):
    meta = json.loads((work / "project.json").read_text(encoding="utf-8"))
    (work / "project.json").write_text(json.dumps(dict(meta, profile="children"), ensure_ascii=False), encoding="utf-8")
    with TestClient(create_app(work)) as c:
        st = c.get("/api/state").json()
        assert st["style"]["cps"] == 11.0 and st["style"]["profile_label"] == "어린이"
        assert st["draft"]["profile"] == "children"
        assert st["export_check"]["guide"]["profile"] == "children" and not st["error"]
    (work / "project.json").write_text(json.dumps(dict(meta, profile="teen"), ensure_ascii=False), encoding="utf-8")
    with TestClient(create_app(work)) as c:
        st = c.get("/api/state").json()                               # a wrong value is said, the page still opens
        assert "teen" in st["error"] and st["style"]["profile"] == "adult"


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
    assert any(ln.startswith("[희]") for c in st["draft"]["cells"] if "L0010" in c["refs"] for ln in c["lines"])
    assert item(st, "P0010")["speaker"]["label"] == "[희]" and item(st, "P0011")["speaker"]["label"] == "[양희]"
    # two people at once: '에루/마야카' in the script is shown as II.10 wants it
    st = client.post("/api/items/P0010", json={"name": "희/양희", "decision": "include"}).json()
    assert item(st, "P0010")["speaker"]["label"] == "[희, 양희]"
    assert any(ln.startswith("[희, 양희]") for c in st["draft"]["cells"] if "L0010" in c["refs"] for ln in c["lines"])


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
    from cboe import style
    slow = swap_prev_time(draft, it, fps, 0.0, style.get("children"))       # 11 units a second: shown longer
    assert slow < t and slow + 2 / 11 + 1 <= 15.0 - 2 / fps + 1e-6
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


def test_draft_of_an_older_version_is_shown_in_todays_notation(work, monkeypatch):
    # placement cannot run, so the page falls back on draft.json: one written before the notation
    # changed still has '(이름)' and '♬ …' in its lines
    from cboe import placement
    from cboe.util import write_json
    write_json(work / "draft.json", {"fps": 23.976, "no_room": [], "cells": [
        {"id": "C0001", "start": 181.0, "end": 182.5, "lines": ["- (BJ 옴뫄) 뭐?", "- [쿵]"], "refs": [], "kind": "mixed"},
        {"id": "C0002", "start": 183.0, "end": 185.0, "lines": ["♬ 무서운 음악"], "refs": [], "kind": "music"}]})
    monkeypatch.setattr(placement, "place", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no engine")))
    with TestClient(create_app(work)) as c:
        st = c.get("/api/state").json()
        assert "no engine" in st["error"]
        assert [x["lines"] for x in st["draft"]["cells"]] == [["- [BJ 옴뫄] 뭐?", "- [쿵]"], ["[무서운 음악]"]]


# ------------------------------------------------------------------ export
def test_export(client, work):
    r = client.post("/api/export", json={"format": "srt"}).json()
    path = Path(r["path"])
    assert path.is_file() and path.parent == work / "export" and r["url"] == "api/export/srt"
    text = path.read_text(encoding="utf-8")
    assert "-->" in text and "[BJ 옴뫄] 흐윽 후윽" in text and "(BJ 옴뫄)" not in text and "♬" not in text
    assert any("확인 필요" in w for w in r["warnings"]) and r["state"]["project"]["title"]
    d = client.get("/api/export/srt")
    assert d.status_code == 200 and "filename*=utf-8''" in d.headers["content-disposition"]
    assert d.content == path.read_bytes()

    r = client.post("/api/export", json={"format": "vtt"}).json()
    assert Path(r["path"]).read_text(encoding="utf-8").startswith("WEBVTT")
    assert client.get("/api/export/vtt").headers["content-type"].startswith("text/vtt")
    assert client.post("/api/export", json={"format": "ass"}).status_code == 422


def test_export_shows_the_guide_check_and_writes_ttml(client, work):
    from cboe import check
    r = client.post("/api/export", json={"format": "srt"}).json()
    # the check of the file just written: with the draft's frame rate (not 30) and clause by clause
    chk = r["check"]
    assert chk["fps"] == r["state"]["project"]["fps"] and not chk["fps_assumed"] and chk["cells"] == r["cells"]
    assert chk["summary"] == r["export_check"]["guide"]["summary"]      # the same result the page showed before saving
    again = check.run(Path(r["path"]), fps=chk["fps"], cuts=r["state"]["cuts"])
    assert again["summary"]["by_code"] == chk["summary"]["by_code"]     # and what the checker says of the file itself
    for w in r["warnings"]:
        assert "간격 2프레임 미만" not in w                              # the false alarm of the 30 fps guess is gone
    sm = chk["summary"]
    assert (sm["must"] == 0) == (not any(w.startswith("지침 검사: 의무") for w in r["warnings"]))
    assert (sm["should"] == 0) == (not any(w.startswith("지침 검사: 권고") for w in r["warnings"]))

    r = client.post("/api/export", json={"format": "ttml"}).json()
    path = Path(r["path"])
    assert path.suffix == ".ttml" and path.parent == work / "export" and r["url"] == "api/export/ttml"
    text = path.read_text(encoding="utf-8")
    assert 'xml:lang="ko"' in text and 'ttp:frameRate="24" ttp:frameRateMultiplier="1000 1001"' in text
    assert text.count("<p ") == r["cells"] == len(r["state"]["draft"]["cells"]) and 'region="bottom"' in text
    cells, meta = check.parse_ttml(text)
    assert [c["lines"] for c in cells] == [c["lines"] for c in r["state"]["draft"]["cells"]]
    d = client.get("/api/export/ttml")
    assert d.status_code == 200 and d.headers["content-type"].startswith("application/ttml+xml")
    assert d.content == path.read_bytes()


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


# ------------------------------------------------------------- the review layer
def test_an_older_sample_folder_carries_the_review_layer(client, work):
    # the sample folder is written like an older work folder: no line status, no extras, no scenes
    st = client.get("/api/state").json()
    assert len(st["lines"]) == len(st["dialogue"]) and st["extras"] == [] and st["scenes"] == [] and st["added"] == []
    sm = st["summary"]["lines"]
    assert sm["status"]["ok"] == sm["total"] == len(st["dialogue"]) and sm["included"] == sm["total"]
    assert all(r.get("include") and r["cells"] for r in st["lines"])
    chk = st["export_check"]
    assert chk["lines"]["differs_open"] == [] and chk["extras_open"] == [] and "names_early" in chk
    assert {p["name"] for p in st["people"]} >= {ln["speaker"] for ln in st["dialogue"] if ln.get("speaker")}
    # a line taken out and back through the new route; the old item routes are untouched
    first = st["lines"][0]["id"]
    st = client.post(f"/api/lines/{first}", json={"include": False}).json()
    assert not any(first in c["refs"] for c in st["draft"]["cells"])
    assert review(work)["lines"][first]["include"] is False and review(work)["items"] == {}
    st = client.post(f"/api/lines/{first}", json={"include": None}).json()
    assert any(first in c["refs"] for c in st["draft"]["cells"]) and first not in review(work)["lines"]


# ------------------------------------------------------------------- 7차: the state without jsonable_encoder's copy
def test_json_reply_sends_the_bytes_fastapi_would_send():
    from fastapi.encoders import jsonable_encoder

    from cboe.server.app import SafeJSONResponse, json_reply
    cases = [{"a": [1, 2.5, None, True, "가나"], "b": (1, (2, 3)), "c": {"x": {"y": [], "z": {}}}},
             {"_sa_hidden": 1, "kept": 2}, {"nested": [{"_sample": "x", "ok": 1}]},      # jsonable_encoder drops _sa…
             {"text": '"_sa는 글 속에'}, {"n": float("nan"), "i": [float("inf"), -0.0]}, {"s": {3, 1, 2}},
             {"p": Path("a") / "b"}, [1, "x", None], {1: "a number key", None: "none", 2.5: "float", True: "bool"}]
    for c in cases:
        assert json_reply(c).body == SafeJSONResponse(jsonable_encoder(c)).body, c
        assert json_reply(c).headers["content-type"] == SafeJSONResponse(jsonable_encoder(c)).headers["content-type"]


class _NoMedia:
    def start(self):
        return self

    def status(self):
        return {"state": "none", "t0": 0.0, "progress": None, "note": None}

    def close(self):
        pass


def test_state_and_decisions_answer_what_fastapi_would_answer(tmp_path):
    from fastapi.encoders import jsonable_encoder

    from cboe.server.app import SafeJSONResponse
    w = tmp_path / "w"
    w.mkdir()

    def words(text, s, e):
        toks = text.split()
        return [{"w": x, "s": round(s + i * (e - s) / len(toks), 3), "e": round(s + (i + 1) * (e - s) / len(toks), 3)}
                for i, x in enumerate(toks)]
    lines = [{"id": f"L000{k}", "speaker": sp, "text": t, "start": s, "end": e, "words": words(t, s, e),
              "status": "ok", "timing": "asr", "scene_id": "SC001"}
             for k, (sp, t, s, e) in enumerate([("하늘", "어디 가는 길이야?", 1.0, 2.5), ("바다", "집에 가 하늘아", 3.0, 4.2),
                                                 ("하늘", "그래 같이 가자 정말로 오늘은 아무 데도 가지 말고", 5.0, 9.0)], 1)]
    for name, obj in (("project.json", {"title": "시험", "t0": 0.0, "t1": 30.0, "fps": 24.0, "duration": 30.0}),
                      ("dialogue.json", {"lines": lines, "extras": []}), ("items.json", {"items": []}),
                      ("cuts.json", {"cuts": [4.9]})):
        (w / name).write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    with TestClient(create_app(w, media=_NoMedia())) as c:
        svc = c.app.state.review
        for path, body in ((None, None), ("/api/lines/L0002", {"sung": True}), ("/api/people", {"names": ["하늘"], "mode": "generic", "before": "여자"}),
                           ("/api/lines/L0003", {"breaks": [4]})):
            if path:
                r = c.post(path, json=body)
                assert r.status_code == 200, r.text
            r = c.get("/api/state")
            assert r.content == SafeJSONResponse(jsonable_encoder(svc.state())).body, path
        # the cycle collector rests while the work folder's lock is held, and only then
        seen = []
        real = svc.project.set_line

        def spy(*a, **k):
            seen.append(gc.isenabled())
            return real(*a, **k)
        svc.project.set_line = spy
        assert gc.isenabled()
        assert c.post("/api/lines/L0001", json={"sung": True}).status_code == 200
        assert c.post("/api/lines/L0001", json={"breaks": [99]}).status_code == 400     # refused inside the lock
        assert seen == [False, False] and gc.isenabled()


def test_the_work_lock_pauses_the_cycle_collector_while_any_is_held():
    import threading

    from cboe.server.app import WorkLock
    a, b = WorkLock(), WorkLock()
    assert gc.isenabled()
    with a:
        assert not gc.isenabled()
        with a:                                   # re-entrant
            with b:
                assert not gc.isenabled()
        assert not gc.isenabled()
    assert gc.isenabled()
    with pytest.raises(RuntimeError):
        with a:
            raise RuntimeError("x")
    assert gc.isenabled()
    # one thread holds a while another takes and leaves b: on again only when neither is held
    inside, leave = threading.Event(), threading.Event()

    def hold():
        with a:
            inside.set()
            leave.wait(10)
    t = threading.Thread(target=hold)
    t.start()
    inside.wait(10)
    with b:
        pass
    assert not gc.isenabled()
    leave.set()
    t.join(10)
    assert gc.isenabled()
    # a collector someone else turned off stays off
    gc.disable()
    try:
        with a:
            pass
        assert not gc.isenabled()
    finally:
        gc.enable()


# ------------------------------------------------------------------- 7차: a name heard otherwise
def test_a_line_whose_name_was_heard_otherwise_carries_what_was_heard(tmp_path):
    # hint name on a line the alignment calls ok (only the name sounded different): the state sends
    # what was heard and the difference, so the line's panel sets the script beside it and offers
    # 들린 대로 (old server: the hint only, nothing heard to compare)
    w = tmp_path / "w"
    w.mkdir()

    def words(text, s, e):
        toks = text.split()
        return [{"w": x, "s": round(s + i * (e - s) / len(toks), 3), "e": round(s + (i + 1) * (e - s) / len(toks), 3)}
                for i, x in enumerate(toks)]
    rows = [("L0001", "나래", "보라야, 내일 아침에 산으로 가자.", "포라야 내일 아침에 산으로 가자", "ok", ["name"],
             [{"kind": "word", "name": True, "script": "보라야,", "heard": "포라야", "at": [0, 1]}], 1.0, 3.0),
            ("L0002", "보라", "좋아 산에서 해를 보자.", "좋아 산에서 해를 보자.", "ok", None, None, 4.0, 6.0),
            ("L0003", "나래", "그때 꼭 와야 한다, 알았지?", "그때 꼭 와야 한다 고마워", "differs", None,
             [{"kind": "word", "script": "알았지?", "heard": "고마워", "at": [4, 5]}], 7.0, 9.0)]
    lines = [{"id": i, "speaker": sp, "text": t, "start": s, "end": e, "words": words(t, s, e), "status": st,
              "timing": "asr", "scene_id": "SC001", "asr_text": heard, "hints": h, "diff": d}
             for i, sp, t, heard, st, h, d, s, e in rows]
    for name, obj in (("project.json", {"title": "시험", "t0": 0.0, "t1": 12.0, "fps": 24.0, "duration": 12.0}),
                      ("dialogue.json", {"lines": lines, "extras": []}), ("items.json", {"items": []}),
                      ("cuts.json", {"cuts": []})):
        (w / name).write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    with TestClient(create_app(w, media=_NoMedia())) as c:
        got = {ln["id"]: ln for ln in c.get("/api/state").json()["lines"]}
    assert got["L0001"]["hints"] == ["name"] and got["L0001"]["status"] == "ok"
    assert got["L0001"]["asr_text"] == "포라야 내일 아침에 산으로 가자", got["L0001"]
    assert got["L0001"]["diff"] == rows[0][6]
    assert "asr_text" not in got["L0002"] and "diff" not in got["L0002"]          # nothing to say: kept small
    assert got["L0003"]["asr_text"] == "그때 꼭 와야 한다 고마워" and got["L0003"]["diff"] == rows[2][6]
