# -*- coding: utf-8 -*-
"""재생용 영상 (cboe.proxy): which sources a browser plays, the ffmpeg arguments (only the
window is read, at most 960 wide, the frame rate kept), when the copy is still fresh, the
server state (none | source | building | ready | failed) and, when ffmpeg is on this PC, a
real copy of a made-up mkv (MPEG-4 Part 2 + MP3, one colour per second)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from cboe import proxy
from cboe.server.app import create_app
from cboe.util import write_json

MP4 = "mov,mp4,m4a,3gp,3g2,mj2"
MKV = "matroska,webm"


def info(ext=".mkv", fmt=MKV, vcodec="mpeg4", acodec="mp3", **kw):
    return {"duration": 60.0, "fps": 24.0, "width": 1280, "height": 544, "has_audio": acodec is not None,
            "format": fmt, "ext": ext, "vcodec": vcodec, "acodec": acodec, "vindex": 1,
            "aindex": 0 if acodec else None, "sar": 1.0, **kw}


# ------------------------------------------------------------------ playable
@pytest.mark.parametrize("ext, fmt, vcodec, acodec, ok", [
    (".mp4", MP4, "h264", "aac", True),
    (".mov", MP4, "h264", "mp3", True),
    (".m4v", MP4, "av1", "opus", True),
    (".mp4", MP4, "vp9", "flac", True),
    (".mp4", MP4, "h264", None, True),                 # no sound
    (".webm", MKV, "vp9", "opus", True),
    (".webm", MKV, "vp8", "vorbis", True),
    (".webm", MKV, "av1", None, True),
    (".mkv", MKV, "h264", "aac", False),               # mkv: browsers differ even with fitting codecs
    (".mkv", MKV, "mpeg4", "mp3", False),              # MPEG-4 Part 2 in mkv (a common film file)
    (".mp4", MP4, "mpeg4", "aac", False),
    (".mp4", MP4, "hevc", "aac", False),
    (".mov", MP4, "prores", "pcm_s16le", False),
    (".mp4", MP4, "h264", "ac3", False),
    (".mp4", MP4, "h264", "dts", False),
    (".mp4", MP4, "h264", "eac3", False),
    (".avi", "avi", "h264", "mp3", False),
    (".ts", "mpegts", "h264", "aac", False),
    (".mp4", MKV, "h264", "aac", False),               # the name says mp4, the file is mkv
    (".webm", MP4, "vp9", "opus", False),
    (".mp4", MP4, None, "aac", False),                 # no video stream
])
def test_playable_table(ext, fmt, vcodec, acodec, ok):
    assert proxy.playable(info(ext, fmt, vcodec, acodec)) is ok


# ------------------------------------------------------------------ command
def _value(cmd, flag, before=None):
    i = cmd.index(flag)
    if before is not None:
        assert i < cmd.index(before)
    return cmd[i + 1]


def test_command_reads_only_the_window():
    cmd = proxy.command("in.mkv", "out.part", 12.5, 30.0, "h264_qsv", width=1280, height=544, fps=23.98,
                        vindex=1, aindex=0)
    assert cmd[0] == "ffmpeg" and cmd.count("-i") == 1 and _value(cmd, "-i") == "in.mkv"
    assert float(_value(cmd, "-ss", before="-i")) == 12.5                  # input options: only the window is read
    assert float(_value(cmd, "-t", before="-i")) == pytest.approx(17.5)
    assert cmd.count("-ss") == 1 and cmd.count("-t") == 1
    assert not {"-to", "-sseof", "-stream_loop", "-itsoffset"} & set(cmd)
    assert [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"] == ["0:1", "0:0"]   # first video, first sound
    assert cmd[-1] == "out.part" and _value(cmd, "-f", before="out.part") == "mp4"
    assert _value(cmd, "-movflags") == "+faststart" and _value(cmd, "-progress") == "pipe:1"


def test_command_size_rate_and_codecs():
    cmd = proxy.command("in.mkv", "o", 0, 10, "h264_qsv", width=1280, height=544, fps=23.98)
    vf = _value(cmd, "-vf")
    assert vf.startswith("scale=960:408,") and "format=nv12" in vf
    assert "fps=" not in vf and "-r" not in cmd and "-fps_mode" not in cmd and "-vsync" not in cmd
    assert _value(cmd, "-c:v") == "h264_qsv" and _value(cmd, "-g") == "24"   # a key frame every second
    assert _value(cmd, "-c:a") == "aac" and _value(cmd, "-ac") == "2"
    assert [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"] == ["0:v:0", "0:a:0?"]
    x264 = proxy.command("in.mkv", "o", 0, 10, "libx264", width=640, height=360, fps=29.97)
    assert _value(x264, "-vf").startswith("scale=640:360,") and "format=yuv420p" in _value(x264, "-vf")
    assert _value(x264, "-c:v") == "libx264" and _value(x264, "-g") == "30"
    silent = proxy.command("in.mp4", "o", 0, 10, "h264_nvenc", width=1920, height=1080, fps=25, has_audio=False)
    assert "-an" in silent and "-c:a" not in silent and silent.count("-map") == 1
    with pytest.raises(ValueError):
        proxy.command("in.mkv", "o", 5, 5, "libx264", width=640, height=360, fps=24)
    with pytest.raises(ValueError):
        proxy.command("in.mkv", "o", 0, 5, "mpeg4", width=640, height=360, fps=24)


@pytest.mark.parametrize("w, h, sar, out", [
    (1280, 544, 1.0, (960, 408)),
    (1920, 1080, 1.0, (960, 540)),
    (1919, 1081, 1.0, (960, 540)),
    (640, 360, 1.0, (640, 360)),           # smaller: as it is
    (321, 241, 1.0, (320, 240)),           # odd sides become even
    (720, 480, 32 / 27, (852, 480)),       # anamorphic: the shown width
    (3840, 1606, 1.0, (960, 402)),
])
def test_out_size(w, h, sar, out):
    got = proxy.out_size(w, h, sar)
    assert got == out and got[0] <= 960 and got[0] % 2 == 0 and got[1] % 2 == 0


# ------------------------------------------------------------------ fresh
def test_fresh_follows_the_source_and_the_window(tmp_path):
    src = tmp_path / "film.mkv"
    src.write_bytes(b"x" * 1000)
    meta = {"video": str(src), "t0": 1.0, "t1": 2.5}
    out, side, _part = proxy.paths(tmp_path)
    assert not proxy.fresh(tmp_path, meta)                                  # nothing made yet
    out.write_bytes(b"made")
    assert not proxy.fresh(tmp_path, meta)                                  # no side file
    write_json(side, {**proxy.source_key(src, 1.0, 2.5), "encoder": "h264_qsv", "width": 960, "height": 408})
    assert proxy.fresh(tmp_path, meta)
    assert not proxy.fresh(tmp_path, {**meta, "t1": 3.0})                   # another window
    assert not proxy.fresh(tmp_path, {**meta, "t0": 0.5})
    assert not proxy.fresh(tmp_path, {**meta, "video": str(tmp_path / "other.mkv")})
    st = src.stat()
    os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))      # the source changed
    assert not proxy.fresh(tmp_path, meta)
    write_json(side, {**proxy.source_key(src, 1.0, 2.5), "encoder": "h264_qsv", "width": 960, "height": 408})
    assert proxy.fresh(tmp_path, meta)
    src.write_bytes(b"y" * 1001)                                            # another size
    assert not proxy.fresh(tmp_path, meta)
    side.write_text("{broken", encoding="utf-8")
    assert not proxy.fresh(tmp_path, meta)


# ------------------------------------------------------------------ server, stand-in worker
def folder(tmp_path, video=None, t0=0.0, t1=20.0, name="work"):
    w = tmp_path / name
    w.mkdir()
    meta = {"title": "재생 시험", "t0": t0, "t1": t1, "fps": 24.0, "duration": 60.0, "width": 1280, "height": 544}
    if video is not None:
        meta["video"] = str(video)
    write_json(w / "project.json", meta)
    write_json(w / "dialogue.json", {"lines": []})
    return w


class StandIn:
    """What create_app needs from a worker, with a state the test sets."""

    def __init__(self, state, t0=0.0, progress=None, note=None, path=None):
        self.m = {"state": state, "t0": t0, "progress": progress, "note": note}
        self.path = path
        self.started = self.closed = False
        self.builds = 0

    def start(self):
        self.started = True
        return self

    def status(self):
        return dict(self.m)

    def file(self):
        return self.path if self.m["state"] in ("ready", "source") else None

    def request_build(self):
        self.builds += 1
        if self.m["state"] in ("source", "failed"):
            self.m = {"state": "building", "t0": 5.0, "progress": 0.0, "note": None}
        return self.status()

    def close(self):
        self.closed = True


def test_server_building_then_ready_with_a_stand_in(tmp_path):
    work = folder(tmp_path, video=tmp_path / "film.mkv", t0=5.0)
    copy = work / "review.mp4"
    copy.write_bytes(bytes(range(256)) * 64)
    fake = StandIn("building", t0=5.0, progress=0.25)
    app = create_app(work, media=fake)
    assert fake.started
    with TestClient(app) as c:
        want = {"state": "building", "t0": 5.0, "progress": 0.25, "note": None}
        assert c.get("/api/state").json()["project"]["media"] == want
        assert c.get("/api/media").json() == want
        r = c.get("/media/video")
        assert r.status_code == 409 and "만드는 중" in r.json()["detail"]
        fake.m, fake.path = {"state": "ready", "t0": 5.0, "progress": None, "note": None}, copy
        st = c.get("/api/state").json()["project"]
        assert st["media"]["state"] == "ready" and st["video_url"] == "media/video"
        r = c.get("/media/video")
        assert r.status_code == 200 and r.content == copy.read_bytes() and r.headers["content-type"] == "video/mp4"
        r = c.get("/media/video", headers={"Range": "bytes=10-99"})
        assert r.status_code == 206 and r.content == copy.read_bytes()[10:100]
        assert c.post("/api/media/build").json()["state"] == "ready" and fake.builds == 1
    assert fake.closed                                                       # the lifespan ends the worker


def test_server_failed_none_and_build_request_with_a_stand_in(tmp_path):
    work = folder(tmp_path, video=tmp_path / "film.mkv")
    fake = StandIn("failed", t0=0.0, note="재생용 영상을 만들지 못했습니다 · 시험")
    with TestClient(create_app(work, media=fake)) as c:
        r = c.get("/media/video")
        assert r.status_code == 404 and "시험" in r.json()["detail"]
        m = c.post("/api/media/build").json()
        assert m["state"] == "building" and fake.builds == 1
        fake.m = {"state": "none", "t0": 0.0, "progress": None, "note": None}
        assert c.get("/media/video").status_code == 404


# ------------------------------------------------------------------ server, the real worker (no ffmpeg)
def _until(get, cond, timeout=10.0):
    end = time.time() + timeout
    while True:
        v = get()
        if cond(v) or time.time() > end:
            return v
        time.sleep(0.02)


def _fake_build(gate=None, calls=None, encoder="h264_qsv"):
    def build(work, meta, *, info, on_progress, cancel, on_process, **_kw):
        if calls is not None:
            calls.append(dict(meta))
        on_progress(0.5)
        if gate is not None:
            assert gate.wait(10)
        out, side, _part = proxy.paths(work)
        out.write_bytes(b"made from the window " + str(meta["t1"]).encode())
        t0, t1 = proxy.window(meta)
        rec = {**proxy.source_key(meta["video"], t0, t1), "encoder": encoder, "width": 960, "height": 408}
        write_json(side, rec)
        return rec
    return build


@pytest.fixture()
def no_ffmpeg(monkeypatch):
    """The real worker with probe and build replaced: no ffmpeg runs."""
    monkeypatch.setattr(proxy, "_have", lambda tool: True)

    def encoders(refresh=False):
        raise AssertionError("encoders() must not run here")
    monkeypatch.setattr(proxy, "encoders", encoders)
    return monkeypatch


def test_worker_makes_the_copy_of_an_unplayable_source(tmp_path, no_ffmpeg):
    src = tmp_path / "film.mkv"
    src.write_bytes(b"source bytes")
    work = folder(tmp_path, video=src, t0=5.0, t1=20.0)
    gate, calls = threading.Event(), []
    no_ffmpeg.setattr(proxy, "probe", lambda p: info())
    no_ffmpeg.setattr(proxy, "build", _fake_build(gate, calls))
    with TestClient(create_app(work)) as c:
        m = _until(lambda: c.get("/api/media").json(), lambda m: m.get("progress") == 0.5)
        assert m == {"state": "building", "t0": 5.0, "progress": 0.5, "note": None}
        assert c.get("/api/state").json()["project"]["media"] == m
        assert c.get("/media/video").status_code == 409
        assert c.post("/api/media/build").json()["state"] == "building"     # being made: left as it is
        gate.set()
        m = _until(lambda: c.get("/api/media").json(), lambda m: m["state"] != "building")
        assert m == {"state": "ready", "t0": 5.0, "progress": None, "note": None}
        r = c.get("/media/video")
        assert r.status_code == 200 and r.content == b"made from the window 20.0"
        assert r.headers["content-type"] == "video/mp4"
        assert len(calls) == 1
        # the window changes in project.json: the next status decides again and makes a new copy
        meta = json.loads((work / "project.json").read_text(encoding="utf-8"))
        write_json(work / "project.json", {**meta, "t1": 30.0})
        m = _until(lambda: c.get("/api/media").json(), lambda m: m["state"] == "ready" and len(calls) == 2)
        assert m["state"] == "ready" and len(calls) == 2
        assert c.get("/media/video").content == b"made from the window 30.0"
    # a new server on the same folder takes the fresh copy without making it again
    no_ffmpeg.setattr(proxy, "build", lambda *a, **k: pytest.fail("fresh copy made again"))
    with TestClient(create_app(work)) as c:
        assert c.get("/api/media").json() == {"state": "ready", "t0": 5.0, "progress": None, "note": None}


def test_worker_gives_a_playable_source_as_it_is_and_builds_on_request(tmp_path, no_ffmpeg):
    src = tmp_path / "film.mp4"
    src.write_bytes(b"playable source")
    work = folder(tmp_path, video=src, t0=5.0)
    gate, calls = threading.Event(), []
    no_ffmpeg.setattr(proxy, "probe", lambda p: info(".mp4", MP4, "h264", "aac"))
    no_ffmpeg.setattr(proxy, "build", _fake_build(gate, calls))
    with TestClient(create_app(work)) as c:
        assert c.get("/api/media").json() == {"state": "source", "t0": 0.0, "progress": None, "note": None}
        r = c.get("/media/video")
        assert r.status_code == 200 and r.content == b"playable source"
        m = c.post("/api/media/build").json()                               # the page could not open it
        assert m["state"] == "building" and m["t0"] == 5.0
        gate.set()
        m = _until(lambda: c.get("/api/media").json(), lambda m: m["state"] != "building")
        assert m["state"] == "ready" and m["t0"] == 5.0 and len(calls) == 1
        assert c.post("/api/media/build").json()["state"] == "ready" and len(calls) == 1   # there: left as it is


def test_worker_gives_the_source_when_ffprobe_fails_or_is_missing(tmp_path, no_ffmpeg):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"not a video")

    def bad_probe(p):
        raise subprocess.CalledProcessError(1, ["ffprobe"], output="", stderr="clip.mp4: Invalid data found\n")
    no_ffmpeg.setattr(proxy, "probe", bad_probe)
    no_ffmpeg.setattr(proxy, "build", lambda *a, **k: pytest.fail("no copy without ffprobe"))
    with TestClient(create_app(folder(tmp_path, video=src))) as c:
        m = c.get("/api/media").json()
        assert m["state"] == "source" and m["t0"] == 0.0
        assert "영상 정보를 읽지 못해" in m["note"] and "Invalid data found" in m["note"]
        assert c.get("/media/video").content == b"not a video"
    no_ffmpeg.setattr(proxy, "_have", lambda tool: tool != "ffprobe")
    with TestClient(create_app(folder(tmp_path, video=src, name="w2"))) as c:
        m = c.get("/api/media").json()
        assert m["state"] == "source" and "ffprobe" in m["note"]
    no_ffmpeg.setattr(proxy, "_have", lambda tool: tool != "ffmpeg")
    no_ffmpeg.setattr(proxy, "probe", lambda p: info())
    with TestClient(create_app(folder(tmp_path, video=src, name="w3"))) as c:
        m = c.get("/api/media").json()
        assert m["state"] == "source" and "ffmpeg" in m["note"]
        assert c.post("/api/media/build").json()["state"] == "source"      # still no ffmpeg: no loop


def test_worker_without_a_video_and_when_the_copy_fails(tmp_path, no_ffmpeg):
    with TestClient(create_app(folder(tmp_path, video=tmp_path / "gone.mkv"))) as c:
        assert c.get("/api/media").json()["state"] == "none"
        assert c.get("/media/video").status_code == 404
        assert c.post("/api/media/build").json()["state"] == "none"
        assert c.get("/api/state").json()["project"]["video"] is False
    src = tmp_path / "film.mkv"
    src.write_bytes(b"source")
    tries = []

    def failing(work, meta, **kw):
        tries.append(1)
        raise proxy.BuildError("재생용 영상을 만들지 못했습니다 · h264_qsv: 시험 오류")
    no_ffmpeg.setattr(proxy, "probe", lambda p: info())
    no_ffmpeg.setattr(proxy, "build", failing)
    with TestClient(create_app(folder(tmp_path, video=src, name="w2"))) as c:
        m = _until(lambda: c.get("/api/media").json(), lambda m: m["state"] != "building")
        assert m["state"] == "failed" and "시험 오류" in m["note"] and m["t0"] == 0.0
        assert c.get("/media/video").status_code == 404
        c.post("/api/media/build")                                          # failed: tried again
        _until(lambda: len(tries), lambda n: n == 2)
        assert len(tries) == 2


def test_worker_keeps_the_sealed_part_rule(tmp_path, no_ffmpeg):
    # a sealed test video (cboe.guard) is never read: the window check comes before any encoder
    no_ffmpeg.setenv("CBOE_RESEARCH_GUARD", "1")
    src = tmp_path / "video_FGJsSUPXCDs.mkv"
    src.write_bytes(b"sealed")
    no_ffmpeg.setattr(proxy, "probe", lambda p: info())
    with TestClient(create_app(folder(tmp_path, video=src, t1=10.0))) as c:
        m = _until(lambda: c.get("/api/media").json(), lambda m: m["state"] != "building")
        assert m["state"] == "failed" and "연구 규칙" in m["note"]
    assert not proxy.part_files(tmp_path / "work") and not (tmp_path / "work" / "review.mp4.part").exists()


def test_closing_the_server_stops_the_build(tmp_path, no_ffmpeg):
    src = tmp_path / "film.mkv"
    src.write_bytes(b"source")
    seen = threading.Event()

    def slow(work, meta, *, info, on_progress, cancel, on_process, **_kw):
        seen.set()
        assert cancel.wait(10)
        raise proxy.Cancelled("재생용 영상 만들기를 멈췄습니다")
    no_ffmpeg.setattr(proxy, "probe", lambda p: info())
    no_ffmpeg.setattr(proxy, "build", slow)
    app = create_app(folder(tmp_path, video=src))
    with TestClient(app) as c:
        assert seen.wait(10) and c.get("/api/media").json()["state"] == "building"
    worker = app.state.review.media
    assert worker._thread is None and worker.status()["state"] == "failed"


# ------------------------------------------------------------------ one build per work folder (no ffmpeg)
def _held_ffmpeg(gate, runs, data=b"one copy"):
    """``_run_ffmpeg`` stand-in: writes ``data`` to the part file it is given and waits for ``gate``."""
    running = []

    def run(cmd, span, on_progress, cancel, on_process):
        runs.append(os.path.basename(cmd[-1]))
        running.append(1)
        try:
            assert len(running) == 1, "two ffmpeg on one work folder"
            with open(cmd[-1], "wb") as f:
                f.write(data)
            if on_progress is not None:
                on_progress(0.5)
            while not gate.wait(0.02):
                if cancel is not None and cancel.is_set():
                    raise proxy.Cancelled("재생용 영상 만들기를 멈췄습니다")
            return 0, ""
        finally:
            running.pop()
    return run


@pytest.fixture()
def held(no_ffmpeg):
    """The real build (lock, part file, rename) with ffmpeg and the decoding check replaced."""
    no_ffmpeg.setattr(proxy, "probe", lambda p: info())
    no_ffmpeg.setattr(proxy, "encoders", lambda refresh=False: ["h264_qsv"])
    no_ffmpeg.setattr(proxy, "check_copy", lambda path, span, has_audio, timeout=60.0: None)
    return no_ffmpeg


READY5 = {"state": "ready", "t0": 5.0, "progress": None, "note": None}


def test_a_second_worker_on_the_same_folder_waits_for_the_first_build_and_takes_its_copy(tmp_path, held):
    src = tmp_path / "film.mkv"
    src.write_bytes(b"source")
    work = folder(tmp_path, video=src, t0=5.0, t1=20.0)
    gate, runs = threading.Event(), []
    held.setattr(proxy, "_run_ffmpeg", _held_ffmpeg(gate, runs))
    first = proxy.Worker(work).start()
    second = None
    try:
        assert _until(first.status, lambda m: m.get("progress") == 0.5)["state"] == "building"
        second = proxy.Worker(work).start()
        m = _until(second.status, lambda m: m.get("note") == proxy.WAIT_NOTE)
        assert m == {"state": "building", "t0": 5.0, "progress": None, "note": proxy.WAIT_NOTE}
        time.sleep(0.3)
        assert runs == [f"review.mp4.{os.getpid()}.part"]          # the second started no ffmpeg
        gate.set()
        assert _until(first.status, lambda m: m["state"] != "building") == READY5
        assert _until(second.status, lambda m: m["state"] != "building") == READY5
        assert len(runs) == 1 and second.file() == work / "review.mp4"
        assert (work / "review.mp4").read_bytes() == b"one copy" and not proxy.part_files(work)
        assert proxy.fresh(work, json.loads((work / "project.json").read_text(encoding="utf-8")))
    finally:
        gate.set()
        first.close()
        if second is not None:
            second.close()


def test_an_app_dropped_during_a_build_and_opened_again_starts_no_second_ffmpeg(tmp_path, held):
    # the studio drops a project's review app without ending its worker, then opens it again
    src = tmp_path / "film.mkv"
    src.write_bytes(b"source")
    work = folder(tmp_path, video=src, t0=5.0, t1=20.0)
    gate, runs = threading.Event(), []
    held.setattr(proxy, "_run_ffmpeg", _held_ffmpeg(gate, runs))
    dropped = create_app(work)
    try:
        _until(dropped.state.review.media.status, lambda m: m.get("progress") == 0.5)
        with TestClient(create_app(work)) as c:
            m = _until(lambda: c.get("/api/media").json(), lambda m: m.get("note") == proxy.WAIT_NOTE)
            assert m == {"state": "building", "t0": 5.0, "progress": None, "note": proxy.WAIT_NOTE}
            assert c.get("/api/state").json()["project"]["media"]["note"] == proxy.WAIT_NOTE
            assert c.get("/media/video").status_code == 409
            assert c.post("/api/media/build").json()["state"] == "building" and len(runs) == 1
            gate.set()
            assert _until(lambda: c.get("/api/media").json(), lambda m: m["state"] != "building") == READY5
            assert c.get("/media/video").content == b"one copy" and len(runs) == 1
    finally:
        gate.set()
        dropped.state.review.close()


def test_closing_a_worker_that_waits_for_another_build_stops_the_wait(tmp_path, held):
    src = tmp_path / "film.mkv"
    src.write_bytes(b"source")
    work = folder(tmp_path, video=src, t0=5.0, t1=20.0)
    gate, runs = threading.Event(), []
    held.setattr(proxy, "_run_ffmpeg", _held_ffmpeg(gate, runs))
    first = proxy.Worker(work).start()
    try:
        _until(first.status, lambda m: m.get("progress") == 0.5)
        second = proxy.Worker(work).start()
        _until(second.status, lambda m: m.get("note") == proxy.WAIT_NOTE)
        t = time.time()
        second.close()
        assert time.time() - t < 3 and second._thread is None
        assert second.status()["state"] == "failed" and len(runs) == 1
    finally:
        gate.set()
        first.close()
    assert _until(first.status, lambda m: m["state"] != "building") == READY5


def test_part_files_left_by_an_ended_process_are_removed_and_never_written_into(tmp_path, held):
    src = tmp_path / "film.mkv"
    src.write_bytes(b"source")
    work = folder(tmp_path, video=src, t0=5.0, t1=20.0)
    gone = work / "review.mp4.99999999.part"                    # a server killed during its build
    gone.write_bytes(b"half a copy")
    old = work / "review.mp4.part"                              # the earlier fixed name, still open in an ffmpeg
    gate, runs = threading.Event(), []
    gate.set()
    held.setattr(proxy, "_run_ffmpeg", _held_ffmpeg(gate, runs, b"this build"))
    meta = json.loads((work / "project.json").read_text(encoding="utf-8"))
    with open(old, "wb") as still_open:
        still_open.write(b"an ffmpeg left running")
        still_open.flush()
        rec = proxy.build(work, meta, info=info())
        assert runs == [f"review.mp4.{os.getpid()}.part"] and rec["encoder"] == "h264_qsv"
        assert (work / "review.mp4").read_bytes() == b"this build" and not gone.exists()
        if os.name == "nt":                                     # an open file cannot be removed there
            assert proxy.part_files(work) == [old]
    assert proxy.fresh(work, meta)
    # a fresh copy is taken as it is: no ffmpeg runs again
    assert proxy.build(work, meta, info=info()) == rec and len(runs) == 1


# ------------------------------------------------------------------ real ffmpeg
HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
COLOURS = ("red", "lime", "blue", "yellow")              # one per second
RGB = {"red": (255, 0, 0), "lime": (0, 255, 0), "blue": (0, 0, 255), "yellow": (255, 255, 0)}


def _make_film(path):
    """4 s of 1280x544 at 24 fps, one colour per second, MPEG-4 Part 2 + MP3 in mkv."""
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-v", "error"]
    for c in COLOURS:
        cmd += ["-f", "lavfi", "-i", f"color=c={c}:s=1280x544:r=24:d=1"]
    cmd += ["-f", "lavfi", "-i", "sine=frequency=440:duration=4:sample_rate=44100",
            "-filter_complex", "[0:v][1:v][2:v][3:v]concat=n=4:v=1:a=0[v]", "-map", "[v]", "-map", "4:a",
            "-c:v", "mpeg4", "-q:v", "3", "-g", "12", "-c:a", "libmp3lame", "-b:a", "128k", "-ac", "2", str(path)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=120)
    return path


def _frames(path, w=32, h=14):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-map", "0:v:0", "-vf", f"scale={w}:{h}",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], check=True, capture_output=True, timeout=120).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w, 3).reshape(-1, h * w, 3).mean(axis=1)


def _colour(rgb):
    return min(RGB, key=lambda k: float(np.abs(np.asarray(RGB[k]) - rgb).sum()))


def _streams(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
                         check=True, capture_output=True, text=True, timeout=60).stdout
    return json.loads(out)


@pytest.fixture(scope="module")
def film(tmp_path_factory):
    if not HAVE_FFMPEG:
        pytest.skip("ffmpeg / ffprobe are not on PATH")
    return _make_film(tmp_path_factory.mktemp("film") / "film.mkv")


def _check_copy(work, film_path, t0, t1, encoders):
    out, side, part = proxy.paths(work)
    assert out.is_file() and not part.exists()
    data = _streams(out)
    v = next(s for s in data["streams"] if s["codec_type"] == "video")
    a = next(s for s in data["streams"] if s["codec_type"] == "audio")
    assert "mp4" in data["format"]["format_name"].split(",")
    assert v["codec_name"] == "h264" and a["codec_name"] == "aac" and a["channels"] == 2
    assert (v["width"], v["height"]) == (960, 408)
    assert float(v["duration"]) == pytest.approx(t1 - t0, abs=1 / 24 + 1e-3)            # ±1 frame
    frames = _frames(out)
    n = round((t1 - t0) * 24)
    assert abs(len(frames) - n) <= 1
    colours = [_colour(f) for f in frames]
    assert colours[0] == COLOURS[int(t0)]                     # the first frame is the film at t0
    for i, c in enumerate(colours[:n - 1]):                   # frame i is the film at t0 + i/24
        assert c == COLOURS[int(t0 + (i + 0.5) / 24)], (i, c)
    outside = set(COLOURS) - set(COLOURS[int(t0):int(np.ceil(t1))])
    assert outside and not outside & set(colours)             # nothing from before t0 or after t1
    rec = json.loads(side.read_text(encoding="utf-8"))
    assert set(rec) == {"video", "size", "mtime", "t0", "t1", "encoder", "width", "height"}
    assert rec["t0"] == t0 and rec["t1"] == t1 and rec["encoder"] in encoders
    assert (rec["width"], rec["height"]) == (960, 408) and rec["video"] == str(film_path)
    assert proxy.playable(proxy.probe(out))
    return rec


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg / ffprobe are not on PATH")
def test_real_copy_of_a_made_up_mkv(tmp_path, film):
    found = proxy.encoders()
    assert found and found == [e for e in (*proxy.GPU_ENCODERS, proxy.CPU_ENCODER) if e in found]
    src_info = proxy.probe(film)
    assert (src_info["vcodec"], src_info["acodec"], src_info["ext"]) == ("mpeg4", "mp3", ".mkv")
    assert not proxy.playable(src_info)
    work = folder(tmp_path, video=film, t0=1.0, t1=2.5)
    meta = json.loads((work / "project.json").read_text(encoding="utf-8"))
    progress = []
    rec = proxy.build(work, meta, on_progress=progress.append)
    assert rec["encoder"] == found[0]                         # GPU first when there is one
    assert progress and progress[-1] == pytest.approx(1.0, abs=0.05) and progress == sorted(progress)
    _check_copy(work, film, 1.0, 2.5, found)
    assert proxy.fresh(work, meta)
    assert not proxy.fresh(work, {**meta, "t1": 3.0})
    st = film.stat()
    os.utime(film, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
    try:
        assert not proxy.fresh(work, meta)                    # the source changed
    finally:
        os.utime(film, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert proxy.fresh(work, meta)


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg / ffprobe are not on PATH")
def test_real_copy_with_libx264_when_there_is_no_gpu_encoder(tmp_path, film):
    if proxy.CPU_ENCODER not in proxy.encoders():
        pytest.skip("this ffmpeg has no libx264")
    work = folder(tmp_path, video=film, t0=1.0, t1=2.5)
    meta = json.loads((work / "project.json").read_text(encoding="utf-8"))
    rec = proxy.build(work, meta, encoder_list=[proxy.CPU_ENCODER])
    assert rec["encoder"] == "libx264"
    _check_copy(work, film, 1.0, 2.5, ["libx264"])


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg / ffprobe are not on PATH")
def test_real_ffmpeg_is_ended_when_the_build_is_stopped():
    # a 60 s source read at real speed: stopping it must end ffmpeg at once, not after 60 s
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-re", "-f", "lavfi", "-i",
           "color=c=gray:s=160x120:r=24:d=60", "-progress", "pipe:1", "-nostats", "-f", "null", "-"]
    cancel, procs, seen = threading.Event(), [], []

    def on_progress(frac):
        seen.append(frac)
        cancel.set()                                         # as Worker.close does
    t = time.time()
    with pytest.raises(proxy.Cancelled):
        proxy._run_ffmpeg(cmd, 60.0, on_progress, cancel, lambda p: procs.append(p))
    assert time.time() - t < 15 and seen
    assert procs[0] is not None and procs[0].poll() is not None and procs[-1] is None
    assert procs[0] not in proxy._live


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg / ffprobe are not on PATH")
def test_real_worker_makes_the_copy_again_for_another_window(tmp_path, film):
    work = folder(tmp_path, video=film, t0=1.0, t1=2.5)
    with TestClient(create_app(work)) as c:
        m = _until(lambda: c.get("/api/media").json(), lambda m: m["state"] not in ("building",), timeout=120)
        assert m == {"state": "ready", "t0": 1.0, "progress": None, "note": None}
        r = c.get("/media/video", headers={"Range": "bytes=0-99"})
        assert r.status_code == 206 and r.headers["content-type"] == "video/mp4"
        assert r.content[4:8] == b"ftyp"
        first = _check_copy(work, film, 1.0, 2.5, proxy.encoders())
        meta = json.loads((work / "project.json").read_text(encoding="utf-8"))
        write_json(work / "project.json", {**meta, "t0": 2.0, "t1": 3.5})      # another window: made again
        m = _until(lambda: c.get("/api/media").json(),
                   lambda m: m["state"] not in ("building",) and m["t0"] == 2.0, timeout=120)
        assert m["state"] == "ready" and m["t0"] == 2.0
        second = _check_copy(work, film, 2.0, 3.5, proxy.encoders())
        assert second["t0"] == 2.0 and first["t0"] == 1.0


def _decode_errors(path):
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    return r.returncode, [x for x in r.stderr.splitlines() if x.strip()]


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg / ffprobe are not on PATH")
def test_real_app_opened_again_during_a_build_gives_one_whole_copy(tmp_path, film, monkeypatch):
    # the studio drops the review app during the build (its worker goes on) and opens it again
    work = folder(tmp_path, video=film, t0=1.0, t1=2.5)
    real_run, gate, runs = proxy._run_ffmpeg, threading.Event(), []

    def held_start(cmd, span, on_progress, cancel, on_process):
        runs.append(cmd[-1])
        on_progress(0.01)
        assert gate.wait(30)
        return real_run(cmd, span, on_progress, cancel, on_process)
    monkeypatch.setattr(proxy, "_run_ffmpeg", held_start)
    dropped = create_app(work)
    try:
        _until(dropped.state.review.media.status, lambda m: m.get("progress") == 0.01)
        with TestClient(create_app(work)) as c:
            m = _until(lambda: c.get("/api/media").json(), lambda m: m.get("note") == proxy.WAIT_NOTE)
            assert m["state"] == "building" and m["note"] == proxy.WAIT_NOTE
            gate.set()
            m = _until(lambda: c.get("/api/media").json(), lambda m: m["state"] != "building", timeout=120)
            assert m == {"state": "ready", "t0": 1.0, "progress": None, "note": None}
            assert len(runs) == 1
            _check_copy(work, film, 1.0, 2.5, proxy.encoders())
            assert _decode_errors(work / "review.mp4") == (0, [])
    finally:
        gate.set()
        dropped.state.review.close()


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg / ffprobe are not on PATH")
def test_real_check_finds_a_copy_two_writers_broke_and_the_next_encoder_is_tried(tmp_path, film, monkeypatch):
    work = folder(tmp_path, video=film, t0=1.0, t1=2.5)
    meta = json.loads((work / "project.json").read_text(encoding="utf-8"))
    rec = proxy.build(work, meta)
    good = work / "review.mp4"
    assert proxy.check_copy(good, 1.5, True) is None
    # the length and the streams stay (the index is at the front), the frames are zeros:
    # what two ffmpeg writing one file gave ('Invalid NAL unit size')
    data = bytearray(good.read_bytes())
    at = data.index(b"mdat") + 64
    n = len(data) - 64 - at
    data[at:at + n] = bytes(n)
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(bytes(data))
    assert proxy.probe(broken)["duration"] == pytest.approx(1.5, abs=0.1)
    reason = proxy.check_copy(broken, 1.5, True)
    assert reason and "깨져" in reason
    assert "길이" in proxy.check_copy(good, 9.0, True)
    assert "H.264" in proxy.check_copy(film, 4.0, True)                 # the source itself is MPEG-4 Part 2
    # a result that fails the check is not taken: the next encoder makes it
    real_run, made = proxy._run_ffmpeg, []

    def first_broken(cmd, span, on_progress, cancel, on_process):
        made.append(cmd[cmd.index("-c:v") + 1])
        if len(made) == 1:
            with open(cmd[-1], "wb") as f:
                f.write(bytes(data))
            return 0, ""
        return real_run(cmd, span, on_progress, cancel, on_process)
    monkeypatch.setattr(proxy, "_run_ffmpeg", first_broken)
    meta2 = {**meta, "t1": 2.0}
    rec2 = proxy.build(work, meta2, encoder_list=[rec["encoder"], proxy.CPU_ENCODER])
    assert made == [rec["encoder"], proxy.CPU_ENCODER] and rec2["encoder"] == proxy.CPU_ENCODER
    assert _decode_errors(good) == (0, []) and proxy.fresh(work, meta2)

    # every encoder gives a broken file: nothing is taken, no review.mp4.json
    def always_broken(cmd, span, on_progress, cancel, on_process):
        with open(cmd[-1], "wb") as f:
            f.write(bytes(data))
        return 0, ""
    monkeypatch.setattr(proxy, "_run_ffmpeg", always_broken)
    with pytest.raises(proxy.BuildError, match="깨져"):
        proxy.build(work, meta, encoder_list=[proxy.CPU_ENCODER])
    assert not (work / "review.mp4.json").exists() and not proxy.part_files(work)


@pytest.mark.skipif(not HAVE_FFMPEG or os.name != "nt", reason="Windows Job Object · ffmpeg on PATH")
def test_real_ffmpeg_ends_when_its_python_is_killed_hard(tmp_path):
    psutil = pytest.importorskip("psutil")
    # taskkill /F of the server: atexit does not run, the Job Object still ends ffmpeg
    child = ("import os\n"
             "from cboe import proxy\n"
             "cmd = ['ffmpeg', '-hide_banner', '-nostdin', '-v', 'error', '-re', '-f', 'lavfi', '-i',"
             " 'color=c=gray:s=160x120:r=24:d=60', '-progress', 'pipe:1', '-nostats', '-f', 'null', '-']\n"
             "def on_process(p):\n"
             "    if p is not None:\n"
             "        print(os.getpid(), p.pid, flush=True)\n"
             "proxy._run_ffmpeg(cmd, 60.0, None, None, on_process)\n")
    root = os.path.dirname(os.path.dirname(os.path.abspath(proxy.__file__)))
    env = dict(os.environ, PYTHONPATH=root)
    p = subprocess.Popen([sys.executable, "-c", child], stdout=subprocess.PIPE, text=True, env=env, cwd=str(tmp_path))
    ff = None
    try:
        py, ff_pid = map(int, p.stdout.readline().split())
        ff = psutil.Process(ff_pid)
        assert ff.ppid() == py and "ffmpeg" in ff.name().lower()
        time.sleep(0.5)
        psutil.Process(py).kill()                                # the python that started ffmpeg
        ff.wait(10)                                              # psutil.TimeoutExpired when ffmpeg outlives it
    finally:
        if ff is not None and ff.is_running():
            ff.kill()
        p.wait(20)
