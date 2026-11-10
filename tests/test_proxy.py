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
    def build(work, meta, *, info, on_progress, cancel, on_process):
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
    assert not (tmp_path / "work" / "review.mp4.part").exists()


def test_closing_the_server_stops_the_build(tmp_path, no_ffmpeg):
    src = tmp_path / "film.mkv"
    src.write_bytes(b"source")
    seen = threading.Event()

    def slow(work, meta, *, info, on_progress, cancel, on_process):
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
