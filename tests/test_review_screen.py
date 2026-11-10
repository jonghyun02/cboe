# -*- coding: utf-8 -*-
"""What the review screen reads besides the state: the sound level of the tracks for the
timeline (/api/envelope, 감사 9-x3). The levels here are made up.

The page itself (static/app.js) in a headless Edge driven over CDP by a small Node script: the
재생용 영상 (film time <-> video time, building -> ready, a source whose picture does not open),
writes answered while the writer goes on (끊을 자리 clicked fast, typing), a write that fails,
받아쓰기 헛소리 folded, the width of the page. Every name, line and video here is made up."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

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


# =========================================================== the page in a headless browser
def _edge() -> str | None:
    for p in (os.environ.get("CBOE_TEST_BROWSER"), r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe", shutil.which("msedge"),
              shutil.which("chromium"), shutil.which("google-chrome")):
        if p and Path(p).is_file():
            return p
    return None


EDGE, NODE = _edge(), shutil.which("node")
HAVE_FFMPEG = bool(shutil.which("ffmpeg"))
browser_only = pytest.mark.skipif(not (EDGE and NODE and HAVE_FFMPEG),
                                  reason="headless Edge, Node and ffmpeg are needed to drive the page")

# The Node side: opens a tab over CDP, loads the page, runs a scenario module (default export:
# async (p) => result) and prints "RESULT {json}". p: ev (evaluate), until (poll an expression),
# sleep, latency (slow network), fail (answer POSTs with an error, or drop them), type, idle
# (no write in flight), posts (POST urls seen), exceptions (uncaught page errors).
DRIVER = r"""
import { pathToFileURL } from 'node:url';
const [port, url, scen, W = '1280', H = '800'] = process.argv.slice(2);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const out = (o) => { console.log('RESULT ' + JSON.stringify(o)); process.exit(0); };
if (typeof WebSocket !== 'function') out({ error: 'this Node has no WebSocket' });
let t = null;
for (let i = 0; i < 120 && !t; i++) {
  try { const r = await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' }); if (r.ok) t = await r.json(); } catch (_) { /* not up */ }
  if (!t) await sleep(250);
}
if (!t) out({ error: 'no CDP' });
const ws = new WebSocket(t.webSocketDebuggerUrl);
await new Promise((res, rej) => { ws.addEventListener('open', res, { once: true }); ws.addEventListener('error', rej, { once: true }); });
let seq = 0, failMode = null;
const pending = new Map(), exceptions = [], posts = [];
const send = (method, params = {}) => new Promise((res, rej) => { const id = ++seq; pending.set(id, { res, rej }); ws.send(JSON.stringify({ id, method, params })); });
ws.addEventListener('message', (m) => {
  const msg = JSON.parse(m.data);
  if (msg.id && pending.has(msg.id)) { const { res, rej } = pending.get(msg.id); pending.delete(msg.id); if (msg.error) rej(new Error(JSON.stringify(msg.error))); else res(msg.result); return; }
  if (msg.method === 'Runtime.exceptionThrown') exceptions.push(JSON.stringify(msg.params.exceptionDetails).slice(0, 500));
  if (msg.method === 'Network.requestWillBeSent' && msg.params.request.method === 'POST') posts.push(msg.params.request.url.replace(/^.*?\/api\//, 'api/'));
  if (msg.method === 'Fetch.requestPaused') {
    const id = msg.params.requestId, f = failMode;
    if (!f || msg.params.request.method !== 'POST') send('Fetch.continueRequest', { requestId: id });
    else if (f.drop) send('Fetch.failRequest', { requestId: id, errorReason: 'ConnectionRefused' });
    else send('Fetch.fulfillRequest', { requestId: id, responseCode: f.status, responseHeaders: [{ name: 'Content-Type', value: f.ctype || 'application/json' }], body: Buffer.from(f.body, 'utf8').toString('base64') });
  }
});
await send('Runtime.enable');
await send('Page.enable');
await send('Network.enable');
// the tab in front and focused, as the writer has it: a hidden tab does not load a <video> it is given later
await send('Page.bringToFront');
await send('Emulation.setFocusEmulationEnabled', { enabled: true });
await send('Emulation.setDeviceMetricsOverride', { width: Number(W), height: Number(H), deviceScaleFactor: 1, mobile: false });
const ev = async (expr) => {
  const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(`eval: ${JSON.stringify(r.exceptionDetails).slice(0, 300)} :: ${expr.slice(0, 160)}`);
  return r.result.value;
};
const until = async (expr, ms = 8000) => { const end = Date.now() + ms; for (;;) { const v = await ev(expr); if (v) return v; if (Date.now() > end) return null; await sleep(100); } };
const busy = () => ev(`document.getElementById('app').classList.contains('is-busy')`);
const p = {
  ev, until, sleep, posts, exceptions,
  latency: (ms) => send('Network.emulateNetworkConditions', { offline: false, latency: ms, downloadThroughput: -1, uploadThroughput: -1 }),
  fail: async (mode) => { if (mode && !failMode) await send('Fetch.enable', { patterns: [{ urlPattern: '*/api/*', requestStage: 'Request' }] }); if (!mode) await send('Fetch.disable'); failMode = mode; },
  type: (text) => send('Input.insertText', { text }),
  idle: async (ms = 20000) => { const end = Date.now() + ms; await sleep(300); let calm = 0; while (calm < 3 && Date.now() < end) { calm = (await busy()) ? 0 : calm + 1; await sleep(200); } return calm >= 3; },
};
let result;
try {
  await send('Page.navigate', { url });
  await until(`!!(document.querySelector('#tabs .tab') || (document.getElementById('banner') && !document.getElementById('banner').hidden))`, 30000);
  await p.idle();
  result = await (await import(pathToFileURL(scen).href)).default(p);
} catch (e) { result = { error: String((e && e.stack) || e) }; }
try { await send('Target.closeTarget', { targetId: t.id }); } catch (_) { /* gone */ }
ws.close();
out({ result, exceptions, posts });
"""


@pytest.fixture(scope="module")
def browser(tmp_path_factory):
    """A headless Edge of its own (its own profile folder and a free debugging port); ended, with
    every process it started, when the module's tests are done."""
    if not (EDGE and NODE and HAVE_FFMPEG):
        pytest.skip("headless Edge, Node and ffmpeg are needed to drive the page")
    root = tmp_path_factory.mktemp("page")
    prof = Path(tempfile.mkdtemp(prefix="cboe-edge-"))            # an ASCII path: Edge writes its port here
    proc = subprocess.Popen([EDGE, "--headless=new", "--remote-debugging-port=0", f"--user-data-dir={prof}",
                             "--no-first-run", "--no-default-browser-check", "--mute-audio",
                             "--autoplay-policy=no-user-gesture-required", "--disable-renderer-backgrounding",
                             "--disable-backgrounding-occluded-windows", "--disable-background-timer-throttling", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        port_file, end = prof / "DevToolsActivePort", time.monotonic() + 30
        while not (port_file.is_file() and port_file.read_text().strip()):
            if time.monotonic() > end or proc.poll() is not None:
                pytest.fail("headless Edge did not open its debugging port")
            time.sleep(0.1)
        (root / "driver.mjs").write_text(DRIVER, encoding="utf-8")
        yield {"port": int(port_file.read_text().split()[0]), "root": root, "n": 0}
    finally:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        else:
            proc.kill()
        proc.wait(timeout=20)
        shutil.rmtree(prof, ignore_errors=True)


def run_page(browser, url, scenario, size=(1280, 800)):
    """Run one scenario (a JS module's source) against the page at ``url``: {result, exceptions, posts}."""
    browser["n"] += 1
    scen = browser["root"] / f"scenario{browser['n']}.mjs"
    scen.write_text(scenario, encoding="utf-8")
    out = subprocess.run([NODE, str(browser["root"] / "driver.mjs"), str(browser["port"]), url, str(scen), str(size[0]),
                          str(size[1])], capture_output=True, text=True, encoding="utf-8", timeout=180)
    lines = [ln for ln in out.stdout.splitlines() if ln.startswith("RESULT ")]
    assert lines, out.stdout + out.stderr
    data = json.loads(lines[-1][7:])
    res = data.get("result")
    assert not (isinstance(res, dict) and res.get("error")), res
    assert not data.get("exceptions"), data["exceptions"]
    return data


class Served:
    """The review app on a real port (the browser needs one), in a thread; the lifespan runs."""

    def __init__(self, app):
        import uvicorn
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        end = time.monotonic() + 30
        while not self.server.started:
            if time.monotonic() > end or not self.thread.is_alive():
                raise RuntimeError("the review server did not start")
            time.sleep(0.02)
        self.url = f"http://127.0.0.1:{self.server.servers[0].sockets[0].getsockname()[1]}/"
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(20)


class Media:
    """What create_app needs from the 재생용 영상 worker (proxy.Worker), with the states a test gives.
    ``then``: [(seconds, state, file)] it moves to by itself after start(); ``on_build``: the same,
    from the moment the page asks for the copy (POST api/media/build) while it is 'source'."""

    def __init__(self, state, file=None, t0=0.0, progress=None, note=None, then=(), on_build=()):
        self.m = {"state": state, "t0": t0, "progress": progress, "note": note}
        self.path, self.then, self.on_build = file, list(then), list(on_build)
        self.builds, self.closed, self.at = 0, False, time.monotonic()
        self.lock = threading.Lock()

    def start(self):
        self.at = time.monotonic()
        return self

    def _step(self):
        while self.then and time.monotonic() - self.at >= self.then[0][0]:
            _, self.m, self.path = self.then.pop(0)

    def status(self):
        with self.lock:
            self._step()
            return dict(self.m)

    def file(self):
        with self.lock:
            self._step()
            return self.path if self.m["state"] in ("ready", "source") else None

    def request_build(self):
        with self.lock:
            self.builds += 1
            if self.m["state"] in ("source", "failed") and self.on_build:
                self.at, self.then, self.on_build = time.monotonic(), self.on_build, []
                self._step()
            return dict(self.m)

    def close(self):
        self.closed = True


def _ffmpeg(args, out):
    subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-y", "-v", "error", *args, str(out)], check=True,
                   capture_output=True, timeout=120)
    return out


@pytest.fixture(scope="module")
def clips(tmp_path_factory, browser):
    """clip.mp4: 7 s H.264 + AAC 640x360 (what the browser opens; the 재생용 영상 of film 5~12 s);
    film.mkv: 7 s MPEG-4 Part 2 + MP3 in mkv (the browser opens its sound, not its picture)."""
    d = tmp_path_factory.mktemp("clips")
    src = ["-f", "lavfi", "-i", "testsrc2=s=640x360:r=25:d=7", "-f", "lavfi", "-i", "sine=frequency=330:duration=7:sample_rate=44100"]
    mp4 = _ffmpeg(src + ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-g", "25", "-c:a", "aac", "-ac", "2",
                         "-shortest", "-movflags", "+faststart"], d / "clip.mp4")
    mkv = _ffmpeg(src + ["-c:v", "mpeg4", "-q:v", "5", "-g", "25", "-c:a", "libmp3lame", "-ac", "2", "-shortest"], d / "film.mkv")
    return {"mp4": mp4, "mkv": mkv}


def _words(text, s, e):
    toks = text.split()
    step = (e - s) / len(toks)
    return [{"w": w, "s": round(s + i * step, 3), "e": round(s + (i + 1) * step, 3)} for i, w in enumerate(toks)]


def _line(lid, who, text, s, e):
    return {"id": lid, "speaker": who, "text": text, "raw": text, "start": s, "end": e, "words": _words(text, s, e),
            "match": 1.0, "aligned": True, "timing": "asr", "status": "ok", "scene_id": "SC001", "language": None,
            "sung": False, "source": None, "asr_text": text}


def _extra(xid, text, s, e, **more):
    return {"id": xid, "start": s, "end": e, "text": text, "scene_id": "SC001", "words": _words(text, s, e), **more}


PAGE_LINES = [_line("L0001", "하늘", "누구 계세요?", 5.4, 6.2),
              _line("L0002", "바다", "오늘 저녁 일곱 시에 학교 앞 공원에서 만나자", 8.0, 10.4),
              _line("L0003", "하늘", "알았어 늦지 않게 갈게", 10.8, 11.7)]
PAGE_EXTRAS = [_extra("X0001", "저기요 잠깐만요", 6.5, 7.4),
               _extra("X0002", "감사합니다", 7.5, 7.9, suspect="greeting", suspect_by={"speech": 0.02, "music": 0.61, "vad": 0.12}),
               _extra("X0003", "여기 맞나", 7.92, 7.98),
               _extra("X0004", "구독 좋아요 알림 설정", 11.75, 11.95, suspect="credit", suspect_by={"speech": 0.03, "music": 0.7})]


def page_folder(tmp_path, video, t0=5.0, t1=12.0, title="화면 시험"):
    """A made-up work folder for the page: three script lines, four speeches in no script line (two
    shaped like 받아쓰기 헛소리), a speaker candidate per line and one sound."""
    w = tmp_path / "work"
    w.mkdir()
    write_json(w / "project.json", {"title": title, "video": str(video), "t0": t0, "t1": t1, "fps": 25.0,
                                    "duration": 12.0, "width": 640, "height": 360})
    write_json(w / "dialogue.json", {"mode": "scenes", "extras": PAGE_EXTRAS, "lines": PAGE_LINES})
    items = [{"id": "P" + ln["id"][1:], "kind": "speaker", "t0": ln["start"], "t1": ln["end"], "onset": ln["start"],
              "category": "speaker", "label": {"class": "speaker", "ko": ln["speaker"]},
              "speaker": {"line_id": ln["id"], "name": ln["speaker"], "face": "not_visible", "n_faces": 0, "by": None,
                          "source": None, "estimated": False},
              "phrases": [], "phrase": ln["text"], "short": ln["text"], "flags": [], "rec": "include",
              "reason": "말하는 얼굴 안 보임", "evidence": []} for ln in PAGE_LINES]
    items.append({"id": "S0001", "kind": "sfx", "onset": 9.0, "t0": 9.0, "t1": 10.0, "phrase": "[문 닫히는 소리]",
                  "short": "[문 닫히는 소리]", "phrases": [{"text": "[문 닫히는 소리]", "short": "[문 닫히는 소리]"}],
                  "rec": "include", "reason": "", "flags": [], "category": "event",
                  "label": {"class": "Door", "ko": "문", "confident": True},
                  "evidence": [{"kind": "소리", "t": 9.0, "t1": 9.6, "text": "문 소리", "polarity": "include", "source": "sed"}]})
    write_json(w / "items.json", {"items": items})
    write_json(w / "cuts.json", {"cuts": []})
    write_json(w / "asr.json", {"words": []})
    write_json(w / "script.json", {"version": 2, "characters": ["하늘", "바다"],
                                   "people": [{"id": "P01", "name": "하늘", "cues": ["하늘"]}, {"id": "P02", "name": "바다", "cues": ["바다"]}],
                                   "lines": [{"id": ln["id"], "speaker": ln["speaker"], "text": ln["text"]} for ln in PAGE_LINES]})
    write_json(w / "scenes.json", {"scenes": [{"id": "SC001", "start": 5.4, "end": 11.7, "status": "found"}]})
    return w


# what the stage shows: the <video> (its file, offset, time), the still and the bar over it
STAGE = r"""(() => { const v = document.getElementById('video'), still = document.getElementById('stage-still'), msg = document.getElementById('stage-msg');
  return { src: v.getAttribute('src'), off: v._off === undefined ? null : v._off, rs: v.readyState, err: v.error && v.error.code, vw: v.videoWidth,
    ct: v.currentTime, paused: v.paused, still: !still.hidden, play: !document.getElementById('stage-play').hidden,
    msg: msg.hidden ? null : msg.textContent, clock: document.getElementById('stage-time').textContent,
    toasts: [...document.querySelectorAll('#toasts .toast')].map((t) => t.textContent) }; })()"""


def _clock(s):
    m, sec = s.split(":")
    return int(m) * 60 + float(sec)


@browser_only
def test_page_plays_the_copy_in_film_time(tmp_path, browser, clips):
    # the 재생용 영상 is film 5~12 s: film 6.5 s is 1.5 s into it, and a range played by 듣기 stops at
    # its film end (old page: it seeked the copy to 6.5 s and never stopped inside it)
    work = page_folder(tmp_path, clips["mkv"])
    media = Media("ready", clips["mp4"], t0=5.0)
    with Served(create_app(work, media=media)) as srv:
        r = run_page(browser, srv.url, r"""export default async (p) => {
          const S = () => p.ev(`@STAGE@`);
          const open = await p.until(`(() => { const v = document.getElementById('video'); return v.readyState >= 2 && v.videoWidth > 0; })()`, 15000);
          const first = await S();
          await p.ev(`document.querySelector('#tabs [data-view="extras"]').click()`);
          await p.sleep(150);
          await p.ev(`document.getElementById('extras-row-X0001').click()`);
          await p.sleep(200);
          await p.until(`!document.getElementById('video').seeking`, 5000);
          const seek = await S();
          await p.ev(`document.querySelector('#evidence [data-act="x-play"]').click()`);
          await p.sleep(400);
          const playing = await S();
          await p.until(`document.getElementById('video').paused`, 8000);
          await p.sleep(200);
          const stopped = await S();
          return { open, first, seek, playing, stopped };
        }""".replace("@STAGE@", STAGE))["result"]
    assert r["open"], r
    assert r["first"]["src"].startswith("media/video?v=") and r["first"]["off"] == 5.0 and not r["first"]["still"]
    assert r["seek"]["ct"] == pytest.approx(1.5, abs=0.05) and _clock(r["seek"]["clock"]) == pytest.approx(6.5, abs=0.06)
    assert not r["playing"]["paused"]
    assert r["stopped"]["ct"] == pytest.approx(7.4 + 0.3 - 5.0, abs=0.15)          # x.end + 0.3 in film time
    assert _clock(r["stopped"]["clock"]) == pytest.approx(r["stopped"]["ct"] + 5.0, abs=0.1)
    assert media.builds == 0


@browser_only
def test_page_shows_the_copy_being_made_then_opens_it(tmp_path, browser, clips):
    # building: stills, "만드는 중 40%", no file on the <video> (it would be 409); the page asks
    # api/media every 2 s and opens the copy with a new ?v= when it is ready
    work = page_folder(tmp_path, clips["mkv"])
    media = Media("building", t0=5.0, progress=0.4, then=[(4.0, {"state": "ready", "t0": 5.0, "progress": None, "note": None}, clips["mp4"])])
    with Served(create_app(work, media=media)) as srv:
        r = run_page(browser, srv.url, r"""export default async (p) => {
          const S = () => p.ev(`@STAGE@`);
          const building = await S();
          const t0 = Date.now();
          const ready = await p.until(`(() => { const v = document.getElementById('video'); return v.readyState >= 2 && v.videoWidth > 0 && (v.getAttribute('src') || '').includes('?v='); })()`, 15000);
          const after = await S();
          return { building, ready, after, waited: Date.now() - t0 };
        }""".replace("@STAGE@", STAGE))
    res = r["result"]
    b = res["building"]
    assert b["still"] and not b["play"] and not b["src"] and "재생용 영상을 만드는 중 40%" in b["msg"], b
    assert res["ready"], res
    a = res["after"]
    assert a["off"] == 5.0 and not a["still"] and a["play"] and a["msg"] is None and not a["err"]
    assert any("재생용 영상이 준비" in t for t in a["toasts"]), a["toasts"]
    assert media.builds == 0 and "api/media/build" not in r["posts"]


@browser_only
def test_page_shows_why_the_copy_could_not_be_made(tmp_path, browser, clips):
    work = page_folder(tmp_path, clips["mkv"])
    media = Media("failed", t0=5.0, note="재생용 영상을 만들지 못했습니다 · 시험용 까닭")
    with Served(create_app(work, media=media)) as srv:
        r = run_page(browser, srv.url, r"""export default async (p) => {
          const before = await p.ev(`@STAGE@`);
          await p.ev(`document.activeElement && document.activeElement.blur()`);
          await p.ev(`document.dispatchEvent(new KeyboardEvent('keydown', { code: 'Space', key: ' ', bubbles: true }))`);
          await p.sleep(200);
          return { before, after: await p.ev(`@STAGE@`) };
        }""".replace("@STAGE@", STAGE))["result"]
    s = r["before"]
    assert s["still"] and not s["play"] and not s["src"] and "시험용 까닭" in s["msg"], s
    assert any("시험용 까닭" in t for t in r["after"]["toasts"]), r["after"]["toasts"]      # Space says why
    assert media.builds == 0


@browser_only
def test_page_asks_once_for_the_copy_when_the_picture_does_not_open(tmp_path, browser, clips):
    # the source file opens with its sound but no picture (MPEG-4 Part 2 in an mkv): stills, one
    # POST api/media/build, then the copy it makes (old page: a black stage, no request)
    work = page_folder(tmp_path, clips["mkv"], t0=0.0, t1=7.0)
    media = Media("source", clips["mkv"],
                  on_build=[(0.0, {"state": "building", "t0": 0.0, "progress": 0.1, "note": None}, None),
                            (3.0, {"state": "ready", "t0": 0.0, "progress": None, "note": None}, clips["mp4"])])
    with Served(create_app(work, media=media)) as srv:
        r = run_page(browser, srv.url, r"""export default async (p) => {
          const S = () => p.ev(`@STAGE@`);
          const still = await p.until(`!document.getElementById('stage-still').hidden && document.getElementById('stage-msg').textContent.includes('만드는 중')`, 10000);
          const building = await S();
          const ready = await p.until(`(() => { const v = document.getElementById('video'); return v.readyState >= 2 && v.videoWidth > 0; })()`, 15000);
          return { still, building, ready, after: await S() };
        }""".replace("@STAGE@", STAGE))
    res = r["result"]
    assert res["still"] and res["building"]["still"], res
    assert res["ready"] and not res["after"]["still"] and res["after"]["src"].startswith("media/video?v="), res
    assert media.builds == 1 and r["posts"].count("api/media/build") == 1, r["posts"]


GAPS = "[...document.querySelectorAll('#evidence .toks .gap.is-on')].map((g) => Number(g.dataset.k))"


@browser_only
def test_fast_cut_clicks_all_reach_the_server(tmp_path, browser, clips):
    # 끊을 자리 2 and 4, then 6 after the first answer came but before the second: all three are
    # kept (old page: the first answer took 4 back and the third click sent 2,6)
    work = page_folder(tmp_path, clips["mp4"], t0=0.0, t1=12.0)
    with Served(create_app(work, media=Media("source", clips["mp4"]))) as srv:
        r = run_page(browser, srv.url, r"""export default async (p) => {
          await p.ev(`document.querySelector('#tabs [data-view="lines"]').click()`);
          await p.sleep(150);
          await p.ev(`document.getElementById('lines-row-L0002').click()`);
          await p.sleep(300);
          await p.latency(1000);
          await p.ev(`document.querySelector('#evidence .gap[data-k="2"]').click()`);
          await p.sleep(60);
          await p.ev(`document.querySelector('#evidence .gap[data-k="4"]').click()`);
          const two = await p.ev(`@GAPS@`);
          await p.sleep(1600);
          const between = await p.ev(`@GAPS@`);
          await p.ev(`document.querySelector('#evidence .gap[data-k="6"]').click()`);
          await p.idle();
          await p.latency(0);
          const server = await p.ev(`fetch('api/state').then((r) => r.json()).then((s) => s.lines.find((l) => l.id === 'L0002').breaks || [])`);
          return { two, between, dom: await p.ev(`@GAPS@`), server };
        }""".replace("@GAPS@", GAPS))["result"]
    assert r["two"] == [2, 4] and r["between"] == [2, 4], r
    assert r["server"] == [2, 4, 6] and r["dom"] == [2, 4, 6], r


@browser_only
def test_typed_text_stays_when_an_earlier_write_is_answered(tmp_path, browser, clips):
    # the writer types in 직접 고치기 / 대사로 넣기 while the answer to an earlier write is on its
    # way: the panel is drawn again from that answer, the typed text and the caret stay
    work = page_folder(tmp_path, clips["mp4"], t0=0.0, t1=12.0)
    with Served(create_app(work, media=Media("source", clips["mp4"]))) as srv:
        r = run_page(browser, srv.url, r"""export default async (p) => {
          const field = (box) => `(() => { const i = document.querySelector('#evidence [data-box="${box}"] [data-f="text"]'); return i ? { value: i.value, focused: document.activeElement === i } : null; })()`;
          await p.ev(`document.querySelector('#tabs [data-view="lines"]').click()`);
          await p.sleep(150);
          await p.ev(`document.getElementById('lines-row-L0002').click()`);
          await p.sleep(300);
          await p.latency(1000);
          await p.ev(`document.querySelector('#evidence [data-change="ln-keep"]').click()`);
          await p.ev(`document.querySelector('#evidence [data-act="ln-edit-text"]').click()`);
          await p.ev(`(() => { const i = document.querySelector('#evidence [data-box="text"] [data-f="text"]'); i.focus(); i.setSelectionRange(i.value.length, i.value.length); })()`);
          await p.type(' 꼭 와');
          const typed = await p.ev(field('text'));
          await p.idle();
          const text = await p.ev(field('text'));
          const keep = await p.ev(`document.querySelector('#evidence [data-change="ln-keep"]').checked`);
          // the 대사로 넣기 form of one speech while another is set aside
          await p.ev(`document.querySelector('#tabs [data-view="extras"]').click()`);
          await p.sleep(150);
          await p.ev(`document.getElementById('extras-row-X0003').click()`);
          await p.sleep(150);
          await p.ev(`document.querySelector('#evidence [data-act="x-ignore"]').click()`);
          await p.ev(`document.getElementById('extras-row-X0001').click()`);
          await p.ev(`(() => { const i = document.querySelector('#evidence [data-box="x-line"] [data-f="text"]'); i.focus(); i.setSelectionRange(i.value.length, i.value.length); })()`);
          await p.type(' 좀 봐요');
          const typed2 = await p.ev(field('x-line'));
          await p.idle();
          const form = await p.ev(field('x-line'));
          await p.latency(0);
          const st = await p.ev(`fetch('api/state').then((r) => r.json())`);
          return { typed, text, keep, typed2, form, srvKeep: st.lines.find((l) => l.id === 'L0002').keep,
                   x3: st.extras.find((x) => x.id === 'X0003').state };
        }""")["result"]
    assert r["typed"]["value"].endswith(" 꼭 와") and r["text"] == {"value": r["typed"]["value"], "focused": True}, r
    assert r["keep"] is True and r["srvKeep"] is True
    assert r["typed2"]["value"].endswith(" 좀 봐요") and r["form"] == {"value": r["typed2"]["value"], "focused": True}, r
    assert r["x3"] == "ignored"


@browser_only
def test_a_failed_write_puts_the_panel_back(tmp_path, browser, clips):
    # the server keeps nothing (500): the cut, the switch and the language box show what is stored,
    # and the next cut does not carry the failed one along; the toasts are Korean
    work = page_folder(tmp_path, clips["mp4"], t0=0.0, t1=12.0)
    with Served(create_app(work, media=Media("source", clips["mp4"]))) as srv:
        r = run_page(browser, srv.url, r"""export default async (p) => {
          const toast = () => p.ev(`[...document.querySelectorAll('#toasts .toast')].map((t) => t.textContent).pop() || ''`);
          await p.ev(`document.querySelector('#tabs [data-view="lines"]').click()`);
          await p.sleep(150);
          await p.ev(`document.getElementById('lines-row-L0002').click()`);
          await p.sleep(300);
          await p.fail({ status: 500, body: JSON.stringify({ detail: '시험용 서버 오류' }) });
          await p.ev(`document.querySelector('#evidence .gap[data-k="3"]').click()`);
          await p.idle();
          const gap = await p.ev(`@GAPS@`);
          await p.ev(`document.querySelector('#evidence [data-change="ln-keep"]').click()`);
          await p.idle();
          const keep = await p.ev(`document.querySelector('#evidence [data-change="ln-keep"]').checked`);
          await p.ev(`(() => { const i = document.querySelector('#evidence [data-change="ln-lang"]'); i.value = '영어'; i.dispatchEvent(new Event('change', { bubbles: true })); })()`);
          await p.idle();
          const lang = await p.ev(`document.querySelector('#evidence [data-change="ln-lang"]').value`);
          const t500 = await toast();
          await p.fail({ status: 500, body: 'Internal Server Error', ctype: 'text/plain' });
          await p.ev(`document.querySelector('#evidence .gap[data-k="1"]').click()`);
          await p.idle();
          const tText = await toast();
          await p.fail({ drop: true });
          await p.ev(`document.querySelector('#evidence .gap[data-k="1"]').click()`);
          await p.idle();
          const tDrop = await toast();
          await p.fail(null);
          await p.ev(`document.querySelector('#evidence .gap[data-k="5"]').click()`);
          await p.idle();
          const st = await p.ev(`fetch('api/state').then((r) => r.json()).then((s) => s.lines.find((l) => l.id === 'L0002'))`);
          return { gap, keep, lang, t500, tText, tDrop, after: await p.ev(`@GAPS@`), breaks: st.breaks || [], srvKeep: !!st.keep, srvLang: st.language || '' };
        }""".replace("@GAPS@", GAPS))["result"]
    assert r["gap"] == [] and r["keep"] is False and r["lang"] == "", r
    assert r["t500"] == "시험용 서버 오류"
    assert r["tText"] == "서버 오류 500 · 저장하지 못했습니다"
    assert r["tDrop"].startswith("서버에 연결할 수 없습니다")
    assert r["breaks"] == [5] and r["after"] == [5] and not r["srvKeep"] and r["srvLang"] == "", r


@browser_only
def test_suspect_speech_is_folded_and_not_counted(tmp_path, browser, clips):
    # 받아쓰기 헛소리 의심 (X0002 greeting, X0003 credit): folded, not in the tab's count, the kind
    # and the scores shown when opened, the same numbers in the export check; never deleted
    work = page_folder(tmp_path, clips["mp4"], t0=0.0, t1=12.0)
    with Served(create_app(work, media=Media("source", clips["mp4"]))) as srv:
        r = run_page(browser, srv.url, r"""export default async (p) => {
          await p.ev(`document.querySelector('#tabs [data-view="extras"]').click()`);
          await p.sleep(200);
          const rows = () => p.ev(`[...document.querySelectorAll('#rows .row[data-id]')].map((r) => r.dataset.id + (r.classList.contains('is-suspect') ? '*' : ''))`);
          const folded = { tab: await p.ev(`(document.querySelector('#tabs [data-view="extras"] .n') || {}).textContent || ''`), rows: await rows(),
            fold: await p.ev(`(document.querySelector('#rows .fold-row') || {}).textContent || ''`), note: await p.ev(`document.getElementById('view-note').textContent`) };
          await p.ev(`document.querySelector('#rows .fold-row').click()`);
          await p.sleep(150);
          const opened = await rows();
          await p.ev(`document.getElementById('extras-row-X0002').click()`);
          await p.sleep(200);
          const detail = await p.ev(`(document.querySelector('#evidence .x-suspect') || {}).textContent || ''`);
          await p.ev(`document.querySelector('[data-act="open-export"][data-fmt="srt"]').click()`);
          await p.until(`!!document.querySelector('#modal-box .xp-row')`, 8000);
          const xp = await p.ev(`[...document.querySelectorAll('#modal-box .xp-row')].map((r) => r.textContent).filter((t) => t.includes('대본에 없는 말'))`);
          return { folded, opened, detail, xp };
        }""")["result"]
    f = r["folded"]
    assert f["tab"] == "2" and f["rows"] == ["X0001", "X0003"], f                     # 4 open, 2 of them folded
    assert "헛소리" in f["fold"] and "2개" in f["fold"] and "정하지 않음 2 / 4개" in f["note"] and "의심 2개 접힘" in f["note"], f
    assert r["opened"] == ["X0001", "X0002*", "X0003", "X0004*"]
    assert "인사말만 들림" in r["detail"] and "말소리(SED) 0.02" in r["detail"] and "음악(SED) 0.61" in r["detail"], r["detail"]
    xp = [" ".join(t.split()) for t in r["xp"]]
    assert len(xp) == 1 and "2개 정하지 않음" in xp[0] and "의심 2개 접힘" in xp[0], xp


@browser_only
@pytest.mark.parametrize("width", [900, 1024])
def test_the_page_is_no_wider_than_the_window(tmp_path, browser, clips, width):
    # the top bar wraps instead of pushing the page sideways (the save buttons stay on screen)
    work = page_folder(tmp_path, clips["mp4"], t0=0.0, t1=12.0, title="화면 폭 시험 · 지어낸 작업 폴더 이름이 꽤 긴 경우")
    with Served(create_app(work, media=Media("source", clips["mp4"]))) as srv:
        r = run_page(browser, srv.url, r"""export default async (p) => {
          const out = [];
          for (const v of ['items', 'lines', 'extras', 'people', 'cells']) {
            await p.ev(`document.querySelector('#tabs [data-view="${v}"]').click()`);
            await p.sleep(150);
            out.push(await p.ev(`(() => { const r = document.querySelector('[data-act="open-export"][data-fmt="srt"]').getBoundingClientRect();
              return { view: '${v}', W: document.documentElement.clientWidth, sw: document.documentElement.scrollWidth, srt: Math.round(r.right) }; })()`));
          }
          return out;
        }""", size=(width, 760))["result"]
    for v in r:
        assert v["sw"] <= v["W"] and v["srt"] <= v["W"], v
