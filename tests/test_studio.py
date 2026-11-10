# -*- coding: utf-8 -*-
"""GUI 시작 화면 서버 (cboe.server.studio_app, cboe.studio): project list, review apps under
/p/<id>/, analysis jobs (with a stand-in command instead of the GPU pipeline), research guard."""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from filelock import FileLock  # noqa: E402

from cboe import guard, studio  # noqa: E402
from cboe.server import studio_app  # noqa: E402
from cboe.studio import JobManager, Workspace, safe_id  # noqa: E402

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
def ws_root(tmp_path):
    if not SRT.exists():
        pytest.skip("sample subtitles are not on this PC")
    root = tmp_path / "ws"
    _make_sample(root / "sample")
    (root / "inputs").mkdir()                                  # folders without project.json are not projects
    (root / "broken").mkdir()
    (root / "broken" / "project.json").write_text("{not json", encoding="utf-8")
    return root


@pytest.fixture()
def client(ws_root):
    # the server only answers requests addressed to this PC (LocalOnly)
    with TestClient(studio_app.create_studio_app(ws_root), base_url="http://127.0.0.1") as c:
        yield c


def wait_job(c, timeout=30.0) -> dict:
    t = time.time()
    while time.time() - t < timeout:
        j = c.get("/api/job").json()["job"]
        if j and j["state"] != "running":
            return j
        time.sleep(0.1)
    raise AssertionError("job did not finish")


# -------------------------------------------------------------- workspace
def test_safe_id_is_ascii_and_unique(tmp_path):
    assert safe_id("Gazua Library ep1", tmp_path) == "gazua-library-ep1"
    (tmp_path / "gazua-library-ep1").mkdir()
    assert safe_id("Gazua Library ep1", tmp_path) == "gazua-library-ep1-2"
    pid = safe_id("가즈아 도서관", tmp_path)                    # no ASCII letters: a dated name
    assert pid.startswith("work-") and pid.isascii()


def test_project_list(client, ws_root):
    d = client.get("/api/studio").json()
    ids = [p["id"] for p in d["projects"]]
    assert set(ids) == {"sample", "broken"}                   # 'inputs' has no project.json
    s = next(p for p in d["projects"] if p["id"] == "sample")
    assert s["sample"] and s["counts"] == {"speaker": 12, "sfx": 5, "music": 1}
    assert s["total"] == 18 and s["decided"] == 0 and s["review_url"] == "/p/sample/"
    assert s["status"] in ("partial", "analysed") and "items.json" not in s["stages_done"]
    b = next(p for p in d["projects"] if p["id"] == "broken")
    assert b["status"] == "broken" and b["error"]
    assert d["job"] is None and "llm" in d["env"] and d["stage_labels"]["agent"] == "재확인 에이전트"


def test_home_page(client):
    r = client.get("/")
    assert r.status_code == 200 and "/static/home.js?v=" in r.text and "{{" not in r.text
    for name in ("home.js", "home.css", "app.css", "cboe.ico"):
        assert client.get(f"/static/{name}").status_code == 200


# ------------------------------------------------------- review under /p/<id>
def test_review_app_under_prefix(client, ws_root):
    r = client.get("/p/sample", follow_redirects=False)
    assert r.status_code in (301, 302, 307, 308) and r.headers["location"].endswith("/p/sample/")
    r = client.get("/p/sample/")
    assert r.status_code == 200 and '<base href="/p/sample/">' in r.text and 'data-home="/"' in r.text
    assert client.get("/p/sample/static/app.js").status_code == 200
    st = client.get("/p/sample/api/state").json()
    assert st["project"]["video_url"] == "media/video" and len(st["items"]) == 18
    iid = st["items"][0]["id"]
    st = client.post(f"/p/sample/api/items/{iid}", json={"decision": "exclude"}).json()
    assert next(it for it in st["items"] if it["id"] == iid)["final"] == "exclude"
    rev = json.loads((ws_root / "sample" / "review.json").read_text(encoding="utf-8"))
    assert rev["items"][iid]["decision"] == "exclude"
    assert client.get("/p/nope/").status_code == 404
    assert client.get("/p/broken/").status_code == 404
    # the list shows the decision
    s = next(p for p in client.get("/api/studio").json()["projects"] if p["id"] == "sample")
    assert s["decided"] == 1


def test_open_refuses_paths_outside_workspace(client, ws_root, monkeypatch):
    opened = []
    monkeypatch.setattr(studio.os, "startfile", opened.append, raising=False)
    assert client.post("/api/open", json={"path": r"C:\Windows"}).status_code == 400
    assert client.post("/api/open", json={"path": str(ws_root / "sample" / "project.json")}).status_code == 400
    assert client.post("/api/open", json={"path": str(ws_root / "sample")}).status_code == 200
    assert opened == [str((ws_root / "sample").resolve())]


def test_delete_moves_to_trash(client, ws_root):
    r = client.post("/api/projects/sample/delete").json()
    assert r["ok"] and Path(r["moved_to"]).parent == ws_root / ".trash"
    assert (Path(r["moved_to"]) / "project.json").is_file() and not (ws_root / "sample").exists()
    assert [p["id"] for p in client.get("/api/studio").json()["projects"]] == ["broken"]
    assert client.post("/api/projects/sample/delete").status_code == 404


def test_thumbnail_inside_window(client, ws_root):
    if not VIDEO.exists():
        pytest.skip("video not on this PC")
    r = client.get("/api/projects/sample/thumb")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content[:2] == b"\xff\xd8"
    assert (ws_root / "sample" / "thumb.jpg").is_file()


# -------------------------------------------------------------- new project
def test_inspect_and_create_errors(client, tmp_path):
    r = client.post("/api/inspect", json={"video": str(tmp_path / "none.mp4"), "script": str(tmp_path / "none.txt")}).json()
    assert r["video"]["ok"] is False and r["script"]["ok"] is False
    sc = tmp_path / "s.txt"
    sc.write_text("미나: 안녕하세요\n준호: 네 안녕하세요\n(문이 열린다)\n", encoding="utf-8")
    r = client.post("/api/inspect", json={"script": str(sc)}).json()["script"]
    assert r["ok"] and r["lines"] == 2 and r["named"] == 2 and r["characters"] == ["미나", "준호"]
    r = client.post("/api/projects", json={"video": str(tmp_path / "none.mp4"), "run": False})
    assert r.status_code == 400 and "영상 파일이 없습니다" in r.json()["detail"]
    r = client.post("/api/projects", json={"video": str(tmp_path / "none.mp4"), "start": 30, "end": 10, "run": False})
    assert r.status_code == 400


def test_research_guard_blocks_sealed_episodes(client, ws_root):
    if not VIDEO.exists():
        pytest.skip("video not on this PC")
    v = client.post("/api/inspect", json={"video": str(VIDEO)}).json()["video"]
    assert v["ok"] and v["guard"]["dev_end"] == 1579.7
    r = client.post("/api/projects", json={"video": str(VIDEO), "run": False})          # whole video
    assert r.status_code == 400 and "연구 규칙" in r.json()["detail"]
    assert not any(d.name.startswith("video-rit0wp6hhqe") for d in ws_root.iterdir())
    r = client.post("/api/projects", json={"video": str(VIDEO), "start": 1500, "end": 1600, "run": False})
    assert r.status_code == 400
    r = client.post("/api/projects", json={"video": str(VIDEO), "title": "gazua part", "start": 180, "end": 200,
                                           "run": False}).json()
    p = r["project"]
    assert p["id"] == "gazua-part" and p["status"] == "new" and p["t0"] == 180 and p["t1"] == 200
    assert r["job"] is None


def test_guard_rules(monkeypatch):
    assert guard.limit(r"D:\copies\video_RIt0WP6hHQE.mp4")["title"] == "가즈아 도서관"
    assert guard.limit("other.mp4") is None
    guard.check_window("video_PgaKWtFNI6k.mp4", 0, 1127.0)
    with pytest.raises(ValueError, match="연구 규칙"):
        guard.check_window("video_PgaKWtFNI6k.mp4", 0, 1130.0)
    with pytest.raises(ValueError, match="처리하지 않는"):
        guard.check_window("video_FGJsSUPXCDs.mp4", 0, 10.0)
    monkeypatch.setenv("CBOE_RESEARCH_GUARD", "0")
    guard.check_window("video_FGJsSUPXCDs.mp4", 0, 10.0)


# --------------------------------------------------------------------- jobs
FAKE_RUN = r"""
import sys, time
print("10:00:00 INFO cboe: == stage ingest", flush=True)
print("10:00:00 INFO cboe: == stage ingest: {'seconds': 0.0, 'skipped': True}", flush=True)
print("10:00:00 INFO cboe: == stage separate", flush=True)
for i in range(3):
    print(f"10:00:00 INFO cboe: PROGRESS separate {i}/3 ", flush=True)
    time.sleep(0.05)
print("10:00:01 INFO cboe: == stage separate: {'seconds': 0.2}", flush=True)
print("10:00:01 INFO cboe: == stage sfx: {'candidates': 12, 'unknown': 3, 'seconds': 0.1}", flush=True)
mode = sys.argv[1]
if mode == "fail":
    print("Traceback (most recent call last):", flush=True)
    print("RuntimeError: CUDA out of memory", flush=True)
    sys.exit(1)
if mode == "hang":
    print("10:00:01 INFO cboe: == stage agent", flush=True)
    time.sleep(60)
if mode == "slow":
    time.sleep(1.5)
"""


def fake(monkeypatch, mode):
    monkeypatch.setattr(JobManager, "command", staticmethod(lambda work, o, force: [sys.executable, "-c", FAKE_RUN, mode]))


def test_job_follows_stage_log(client, ws_root, monkeypatch):
    fake(monkeypatch, "ok")
    r = client.post("/api/projects/sample/run", json={"mode": "continue", "options": {"llm": False, "agent_limit": 5}})
    assert r.status_code == 200 and r.json()["job"]["state"] == "running"
    j = wait_job(client)
    assert j["state"] == "done" and j["returncode"] == 0 and j["options"]["llm"] is False
    st = {s["name"]: s for s in j["stages"]}
    assert st["ingest"]["state"] == "skipped" and st["separate"]["state"] == "done"
    assert st["separate"]["seconds"] == 0.2 and st["sfx"]["note"] == "후보 12개 (목록 밖 3)"
    assert st["agent"]["state"] == "pending" and j["current"] is None
    assert any("PROGRESS separate 2/3" in ln for ln in j["log_tail"])
    saved = json.loads((ws_root / "sample" / "gui_job.json").read_text(encoding="utf-8"))
    assert saved["state"] == "done" and [s["name"] for s in saved["stages"]][:3] == ["ingest", "script", "separate"]
    assert st["script"]["label"] == "대본 읽기" and st["script"]["state"] == "pending"
    assert "== stage separate" in (ws_root / "sample" / "gui_run.log").read_text(encoding="utf-8")
    s = next(p for p in client.get("/api/studio").json()["projects"] if p["id"] == "sample")
    assert s["last_run"]["state"] == "done"


def test_job_failure_is_reported(client, ws_root, monkeypatch):
    fake(monkeypatch, "fail")
    client.post("/api/projects/sample/run", json={})
    j = wait_job(client)
    assert j["state"] == "failed" and "CUDA out of memory" in j["error"]
    s = next(p for p in client.get("/api/studio").json()["projects"] if p["id"] == "sample")
    assert s["status"] == "failed" and s["last_run"]["error"]


def test_one_job_at_a_time_and_cancel(client, ws_root, monkeypatch):
    fake(monkeypatch, "hang")
    assert client.post("/api/projects/sample/run", json={}).status_code == 200
    t = time.time()
    while time.time() - t < 10:
        j = client.get("/api/job").json()["job"]
        if j["current"] and j["current"]["stage"] == "agent":
            break
        time.sleep(0.05)
    assert j["fraction"] > 0 and j["current"]["label"] == "재확인 에이전트"
    r = client.post("/api/projects/sample/run", json={})
    assert r.status_code == 409 and "이미 분석 중" in r.json()["detail"]
    assert client.post("/api/projects/sample/delete").status_code == 409
    s = next(p for p in client.get("/api/studio").json()["projects"] if p["id"] == "sample")
    assert s["status"] == "running"
    client.post("/api/job/cancel")
    j = wait_job(client)
    assert j["state"] == "cancelled"
    assert next(s for s in j["stages"] if s["name"] == "agent")["state"] == "cancelled"


def test_run_rejects_unknown_project(client):
    assert client.post("/api/projects/nope/run", json={}).status_code == 404
    assert client.post("/api/projects/sample/run", json={"mode": "everything"}).status_code == 422


def test_workspace_create_cleans_up_on_error(tmp_path):
    ws = Workspace(tmp_path / "w")
    with pytest.raises(ValueError):
        ws.create(str(tmp_path / "missing.mp4"), None, "x", None, None)
    assert not any((tmp_path / "w").iterdir())


# ---------------------------------------------------------- review fixes
def test_missing_project_page_escapes_the_id(client):
    r = client.get("/p/%3Cimg%20src=x%20onerror=alert(1)%3E/")
    assert r.status_code == 404 and "<img" not in r.text and "&lt;img" in r.text


def test_project_id_must_be_the_folder_name(client, ws_root):
    ws = Workspace(ws_root)
    assert ws.work("sample").name == "sample"
    for bad in ("SAMPLE", "sample.", "sample\\x\\..", "x/../sample", "..", ".", "", "a\x00b", 'x"<y'):
        with pytest.raises(KeyError):
            ws.work(bad)
    assert client.get("/p/SAMPLE/").status_code == 404
    assert client.get("/p/sample%5Cx%5C../").status_code == 404
    assert client.post("/api/projects/%00/delete").status_code == 404
    assert client.post("/api/projects/SAMPLE/delete").status_code == 404


def test_base_href_is_a_quoted_url(client, ws_root):
    import shutil
    shutil.copytree(ws_root / "sample", ws_root / "ep#1 가")
    s = next(p for p in client.get("/api/studio").json()["projects"] if p["id"] == "ep#1 가")
    assert s["review_url"] == "/p/ep%231%20%EA%B0%80/"
    r = client.get(s["review_url"])
    assert r.status_code == 200 and '<base href="/p/ep%231%20%EA%B0%80/">' in r.text
    assert client.get(s["review_url"] + "api/state").status_code == 200


def test_only_local_requests(ws_root):
    app = studio_app.create_studio_app(ws_root)
    with TestClient(app, base_url="http://evil.example") as c:          # DNS rebinding: foreign Host
        assert c.get("/api/studio").status_code == 403
    with TestClient(app, base_url="http://127.0.0.1") as c:
        assert c.get("/api/studio").status_code == 200
        assert c.post("/api/job/cancel", headers={"Origin": "http://evil.example"}).status_code == 403
        assert c.post("/api/job/cancel", headers={"Origin": "null"}).status_code == 403
        assert c.post("/api/job/cancel", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
        assert c.post("/api/job/cancel", headers={"Origin": "http://127.0.0.1:5555"}).status_code == 200
        assert c.post("/p/sample/api/items/bulk", json={"ids": [], "decision": "exclude"},
                      headers={"Origin": "http://evil.example"}).status_code == 403
    with TestClient(app, base_url="http://localhost:8765") as c:
        assert c.get("/").status_code == 200


def test_failed_start_leaves_no_job(client, monkeypatch):
    monkeypatch.setattr(JobManager, "command", staticmethod(lambda work, o, force: [r"C:\nope\missing.exe"]))
    r = client.post("/api/projects/sample/run", json={})
    assert r.status_code == 500 and "시작하지 못했습니다" in r.json()["detail"]
    assert client.get("/api/job").json()["job"] is None
    fake(monkeypatch, "ok")
    assert client.post("/api/projects/sample/run", json={}).status_code == 200      # not stuck on 'running'
    assert wait_job(client)["state"] == "done"


def test_exit_0_wins_over_a_late_cancel(client, monkeypatch):
    fake(monkeypatch, "slow")
    client.post("/api/projects/sample/run", json={})
    jobs = client.app.home.state.jobs
    jobs._cancel = True                    # a cancel whose taskkill came too late
    assert wait_job(client)["state"] == "done"


def test_delete_forgets_the_finished_job(client, monkeypatch):
    fake(monkeypatch, "ok")
    client.post("/api/projects/sample/run", json={})
    wait_job(client)
    assert client.post("/api/projects/sample/delete").status_code == 200
    assert client.get("/api/job").json()["job"] is None


def test_run_lock_held_elsewhere(client, ws_root):
    lock = FileLock(str(ws_root / "sample" / ".run.lock"))
    with lock:                             # another window / a command line run on the same folder
        s = next(p for p in client.get("/api/studio").json()["projects"] if p["id"] == "sample")
        assert s["status"] == "external"
        r = client.post("/api/projects/sample/run", json={})
        assert r.status_code == 409 and "다른 창이나 명령줄" in r.json()["detail"]
        assert client.post("/api/projects/sample/delete").status_code == 409
    s = next(p for p in client.get("/api/studio").json()["projects"] if p["id"] == "sample")
    assert s["status"] != "external"


def test_run_options_are_bounded(client):
    for opts in ({"jobs": 500}, {"jobs": 0}, {"agent_limit": -1}, {"model": "gpt"}):
        assert client.post("/api/projects/sample/run", json={"options": opts}).status_code == 422


def test_inspect_reads_only_script_files(client, tmp_path):
    f = tmp_path / "secret.ini"
    f.write_text("token: abc\n", encoding="utf-8")
    r = client.post("/api/inspect", json={"script": str(f)}).json()["script"]
    assert r["ok"] is False and "preview" not in r


# ------------------------------------------------- 대본 미리 보기, 대본 읽기 단계
@pytest.fixture()
def bare(tmp_path):
    """The studio server on an empty work space (needs no sample data)."""
    with TestClient(studio_app.create_studio_app(tmp_path / "ws"), base_url="http://127.0.0.1") as c:
        yield c


SHOOTING_SCRIPT = ["포구 / 밤", "배가 들어온다.", "\t나래\t(영) 왔다.", "\t도윤\t(노래) 오랜만이야.", "\t(할머니)\t밥은 먹었니?",
                   "\t(소리)\t거기 누구요?", "선창가 / 낮", "\t나래\t가자."]


def test_inspect_shows_what_a_shooting_script_read_as(bare, tmp_path):
    f = tmp_path / "s.txt"
    f.write_text("\n".join(SHOOTING_SCRIPT), encoding="utf-8")
    r = bare.post("/api/inspect", json={"script": str(f)}).json()["script"]
    assert r["ok"] and r["error"] is None and r["name"] == "s.txt" and r["format"] == "screenplay" and not r["timed"]
    assert (r["scenes"], r["lines"], r["named"], r["actions"], r["n_characters"]) == (2, 5, 4, 1, 3)
    assert r["characters"] == ["나래", "도윤", "할머니"] and r["named_ratio"] == 0.8
    assert (r["language"], r["languages"], r["sung"], r["offscreen"]) == (1, {"영어": 1}, 1, 2)
    assert r["warnings"] == ["화자 없는 대사가 1줄 있습니다 (20%)"] and r["preview"][0] == "나래: 왔다."


def test_a_script_that_did_not_read_starts_no_analysis(bare, tmp_path):
    f = tmp_path / "pasted.txt"
    f.write_text("\n".join(f"화자 없이 적은 줄 {k}" for k in range(40)), encoding="utf-8")
    r = bare.post("/api/inspect", json={"script": str(f)}).json()["script"]
    assert r["ok"] is False and "화자가 하나도 없습니다" in r["error"] and r["lines"] == 40 and r["named"] == 0
    # the form does not send it, and the server refuses it too
    r = bare.post("/api/projects", json={"video": str(tmp_path / "none.mp4"), "script": str(f), "run": True})
    assert r.status_code == 400 and r.json()["detail"].startswith("대본 · ") and "화자" in r.json()["detail"]
    assert not any((tmp_path / "ws").iterdir())
    f.write_text("미나: 안녕하세요\n", encoding="utf-8")                 # the file was fixed: asked again, not remembered
    r = bare.post("/api/projects", json={"video": str(tmp_path / "none.mp4"), "script": str(f), "run": False})
    assert r.status_code == 400 and "영상 파일이 없습니다" in r.json()["detail"]


def test_inspect_takes_pdf_and_hwp(bare, tmp_path):
    for name, data in (("s.pdf", b"not a pdf at all"), ("s.hwp", b"not an hwp at all")):
        f = tmp_path / name
        f.write_bytes(data)
        r = bare.post("/api/inspect", json={"script": str(f)}).json()["script"]
        assert r["ok"] is False and r["error"].startswith("대본을 읽지 못했습니다"), name       # read, not turned away
    f = tmp_path / "s.hwpx"
    f.write_bytes(b"PK")
    r = bare.post("/api/inspect", json={"script": str(f)}).json()["script"]
    assert r["ok"] is False and "pdf · hwp · txt" in r["error"]
    assert ".pdf" in studio_app.SCRIPT_TYPES and ".hwp" in studio_app.SCRIPT_TYPES and "*.pdf *.hwp" in studio_app.PICK_CODE


def test_script_stage_on_the_start_screen(tmp_path):
    assert studio.ALL_STAGES[:3] == ["ingest", "script", "separate"] and studio.STAGE_LABELS["script"] == "대본 읽기"
    assert set(studio.STAGE_LABELS) == set(studio.KEY_OUTPUT) == set(studio.ALL_STAGES)
    assert studio._note("script", {"lines": 827, "scenes": 110}) == "대사 827줄 · 장면 110개"
    assert studio._note("script", {"lines": 3, "scenes": 0, "changed": True}).endswith("바뀐 대본을 다시 읽음")
    assert studio._note("script", {"skipped": True, "lines": 3}) == "이전 결과 사용"
    page = (ROOT / "cboe" / "server" / "static" / "home.js").read_text(encoding="utf-8")
    assert "'ingest', 'script', 'separate'" in page and "s.warnings" in page

    ws = Workspace(tmp_path / "ws")
    work = ws.root / "p1"
    work.mkdir()
    meta = {"title": "p1", "video": "", "script": None, "t0": 0.0, "t1": 10.0}
    (work / "project.json").write_text(json.dumps(meta), encoding="utf-8")
    s = ws.summary("p1")                                   # no script: nothing to read, and still a new project
    assert s["status"] == "new" and s["stages_done"] == ["script"]
    meta["script"] = str(tmp_path / "s.txt")
    (work / "project.json").write_text(json.dumps(meta), encoding="utf-8")
    assert ws.summary("p1")["stages_done"] == []
    (work / "script.json").write_text('{"lines": []}', encoding="utf-8")
    s = ws.summary("p1")
    assert s["status"] == "new" and s["stages_done"] == ["script"]
    (work / "cuts.json").write_text("{}", encoding="utf-8")
    assert ws.summary("p1")["status"] == "partial"


def test_a_folder_analysed_before_the_cues_stage_is_still_analysed(tmp_path):
    # every stage's file of an older run, but no cues.json (the stage came later): the
    # recommendation reads the script's cues afresh, so the folder is analysed, not '일부만 분석'
    ws = Workspace(tmp_path / "ws")
    work = ws.root / "old"
    work.mkdir()
    (work / "project.json").write_text(json.dumps({"title": "old", "video": "", "script": "s.txt", "t0": 0.0,
                                                   "t1": 10.0}), encoding="utf-8")
    for stage, rel in studio.KEY_OUTPUT.items():
        if stage != "cues":
            (work / rel).parent.mkdir(parents=True, exist_ok=True)
            (work / rel).write_text("{}", encoding="utf-8")     # draft.json last: not older than the candidates
    s = ws.summary("old")
    assert s["status"] == "analysed" and "cues" in s["stages_done"]
    (work / "items.json").unlink()                         # without the recommendation the cues are not done
    assert "cues" not in ws.summary("old")["stages_done"]


def test_guard_knows_renamed_copies():
    assert guard.limit("renamed.mp4", 3606.57)["title"] == "가즈아 도서관"
    assert guard.limit("renamed.mp4", 3600.0) is None
    with pytest.raises(ValueError):
        guard.check_window("copy.mp4", 0, 700, duration=623.85)


def test_review_app_rebuilt_once_after_a_run(client, monkeypatch):
    client.get("/p/sample/")
    projects = client.app.home.state.projects
    assert "sample" in projects.apps
    fake(monkeypatch, "ok")
    client.post("/api/projects/sample/run", json={})
    wait_job(client)
    client.get("/api/job")
    assert "sample" not in projects.apps   # dropped for the finished run
    client.get("/p/sample/")
    client.get("/api/job")
    client.get("/api/studio")
    assert "sample" in projects.apps       # but only once


def test_partial_evidence_file_is_not_finished(client, ws_root):
    import json as _json
    w = ws_root / "sample"
    (w / "screen.json").write_text(_json.dumps({"items": {}, "llm": True, "complete": False}), encoding="utf-8")
    s = next(p for p in client.get("/api/studio").json()["projects"] if p["id"] == "sample")
    assert "screen" not in s["stages_done"]
    (w / "screen.json").write_text(_json.dumps({"items": {}, "llm": True, "complete": True}), encoding="utf-8")
    s = next(p for p in client.get("/api/studio").json()["projects"] if p["id"] == "sample")
    assert "screen" in s["stages_done"]
