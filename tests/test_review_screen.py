# -*- coding: utf-8 -*-
"""What the review screen reads besides the state: the sound level of the tracks for the
timeline (/api/envelope, 감사 9-x3). The levels here are made up.

The page itself (static/app.js) in a headless Edge driven over CDP by a small Node script: the
재생용 영상 (film time <-> video time, building -> ready, a source whose picture does not open),
writes answered while the writer goes on (끊을 자리 clicked fast, typing, a source or a reveal line
picked), a write that fails, 받아쓰기 헛소리 folded and counted as the tab counts, the width of the
page, the decisions kept aside since the script changed (put back, let go; a 화자 항목 row the
server does not send yet is added to its answers in the page), a line's source, a speaker line
that waits for the line before, a line without a name, a script cue's range past its scene, a
재생용 영상 made elsewhere. Every name, line and video here is made up."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from cboe.proxy import WAIT_NOTE
from cboe.server.app import ENV_FLOOR_DB, ENV_MAX_BUCKETS, create_app, level_buckets
from cboe.state import Project
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
EDGE_EXIT_WAIT = 30          # s to wait for the killed Edge to be seen gone (재지 않은 값; 20 s was too short, see browser)
HAVE_FFMPEG = bool(shutil.which("ffmpeg"))
browser_only = pytest.mark.skipif(not (EDGE and NODE and HAVE_FFMPEG),
                                  reason="headless Edge, Node and ffmpeg are needed to drive the page")

# The Node side: opens a tab over CDP, loads the page, runs a scenario module (default export:
# async (p) => result) and prints "RESULT {json}". p: ev (evaluate), until (poll an expression),
# sleep, latency (slow network), fail (answer POSTs with an error, or drop them), type, idle
# (no write in flight), click / key (the writer's own mouse click on an element's middle and key
# press, through CDP Input: they focus and toggle as a person's do), posts (POST urls seen),
# exceptions (uncaught page errors), reload (load the page again; ``before``: a script run in it
# before the page's own, e.g. one that changes what fetch answers), send (any CDP call).
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
const ready = async () => { await until(`!!(document.querySelector('#tabs .tab') || (document.getElementById('banner') && !document.getElementById('banner').hidden))`, 30000); await p.idle(); };
const p = {
  ev, until, sleep, posts, exceptions, send,
  reload: async (before) => {
    if (before) await send('Page.addScriptToEvaluateOnNewDocument', { source: before });
    await send('Page.reload', { ignoreCache: true });
    await sleep(200);
    await ready();
  },
  latency: (ms) => send('Network.emulateNetworkConditions', { offline: false, latency: ms, downloadThroughput: -1, uploadThroughput: -1 }),
  fail: async (mode) => { if (mode && !failMode) await send('Fetch.enable', { patterns: [{ urlPattern: '*/api/*', requestStage: 'Request' }] }); if (!mode) await send('Fetch.disable'); failMode = mode; },
  type: (text) => send('Input.insertText', { text }),
  click: async (sel) => {
    const c = await ev(`(() => { const el = document.querySelector(${JSON.stringify(sel)}); if (!el) return null; el.scrollIntoView({ block: 'nearest' }); const r = el.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()`);
    if (!c) throw new Error(`no element ${sel}`);
    for (const type of ['mouseMoved', 'mousePressed', 'mouseReleased']) await send('Input.dispatchMouseEvent', { type, x: c.x, y: c.y, button: 'left', buttons: type === 'mousePressed' ? 1 : 0, clickCount: 1 });
  },
  key: async (code) => {
    const [key, vk] = { Space: [' ', 32], ArrowDown: ['ArrowDown', 40], ArrowUp: ['ArrowUp', 38] }[code];
    const text = key.length === 1 ? key : undefined;
    await send('Input.dispatchKeyEvent', { type: text ? 'keyDown' : 'rawKeyDown', code, key, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk, ...(text ? { text, unmodifiedText: text } : {}) });
    await send('Input.dispatchKeyEvent', { type: 'keyUp', code, key, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk });
  },
  idle: async (ms = 20000) => { const end = Date.now() + ms; await sleep(300); let calm = 0; while (calm < 3 && Date.now() < end) { calm = (await busy()) ? 0 : calm + 1; await sleep(200); } return calm >= 3; },
};
let result;
try {
  await send('Page.navigate', { url });
  await ready();
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
    prof = tmp_path_factory.mktemp("edge")                        # its profile; Edge writes its port here
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
        try:
            proc.wait(timeout=EDGE_EXIT_WAIT)
        except subprocess.TimeoutExpired:
            # killed already (taskkill /F said so); on a busy Windows its handle can stay unsignalled
            # for half a minute and more (2026-11-11: still not signalled 30 s after taskkill, with
            # other headless Edges running) — nothing is left to end, so the tests' results stand
            pass
        for _ in range(40):                     # its helper processes let go of the profile a moment later
            shutil.rmtree(prof, ignore_errors=True)
            if not prof.exists():
                break
            time.sleep(0.25)


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


def _speaker_item(ln, **more):
    it = {"id": "P" + ln["id"][1:], "kind": "speaker", "t0": ln["start"], "t1": ln["end"], "onset": ln["start"],
          "category": "speaker", "label": {"class": "speaker", "ko": ln["speaker"]},
          "speaker": {"line_id": ln["id"], "name": ln["speaker"], "face": "not_visible", "n_faces": 0, "by": None,
                      "source": None, "estimated": False},
          "phrases": [], "phrase": ln["text"], "short": ln["text"], "flags": [], "rec": "include",
          "reason": "말하는 얼굴 안 보임", "evidence": []}
    it.update(more)
    return it


def _write_script(w, lines):
    """dialogue.json and script.json of these lines (the script read again)."""
    write_json(w / "dialogue.json", {"mode": "scenes", "extras": PAGE_EXTRAS, "lines": lines})
    write_json(w / "script.json", {"version": 2, "characters": ["하늘", "바다"],
                                   "people": [{"id": "P01", "name": "하늘", "cues": ["하늘"]}, {"id": "P02", "name": "바다", "cues": ["바다"]}],
                                   "lines": [{"id": ln["id"], "speaker": ln["speaker"], "text": ln["text"]} for ln in lines]})


def page_folder(tmp_path, video, t0=5.0, t1=12.0, title="화면 시험", lines=None, speakers=None, sounds=()):
    """A made-up work folder for the page: three script lines, four speeches in no script line (two
    shaped like 받아쓰기 헛소리), a speaker candidate per line and one sound. ``lines``: other
    script lines; ``speakers``: other speaker candidates; ``sounds``: more sound candidates."""
    lines = PAGE_LINES if lines is None else lines
    w = tmp_path / "work"
    w.mkdir()
    write_json(w / "project.json", {"title": title, "video": str(video), "t0": t0, "t1": t1, "fps": 25.0,
                                    "duration": 12.0, "width": 640, "height": 360})
    items = [_speaker_item(ln) for ln in lines if ln["speaker"]] if speakers is None else list(speakers)
    items.append({"id": "S0001", "kind": "sfx", "onset": 9.0, "t0": 9.0, "t1": 10.0, "phrase": "[문 닫히는 소리]",
                  "short": "[문 닫히는 소리]", "phrases": [{"text": "[문 닫히는 소리]", "short": "[문 닫히는 소리]"}],
                  "rec": "include", "reason": "", "flags": [], "category": "event",
                  "label": {"class": "Door", "ko": "문", "confident": True},
                  "evidence": [{"kind": "소리", "t": 9.0, "t1": 9.6, "text": "문 소리", "polarity": "include", "source": "sed"}]})
    items.extend(sounds)
    write_json(w / "items.json", {"items": items})
    write_json(w / "cuts.json", {"cuts": []})
    write_json(w / "asr.json", {"words": []})
    _write_script(w, lines)
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


FOCUS = r"""(() => { const a = document.activeElement; return a.tagName + (a.dataset && a.dataset.change ? '[' + a.dataset.change + ']' : ''); })()"""


@browser_only
def test_space_and_arrow_keys_work_after_a_switch_is_answered(tmp_path, browser, clips):
    # the writer clicks 한 칸에 두기 with the mouse and the answer comes: Space plays and ↓ goes to
    # the next line (the panel drawn again did not give the focus back to the switch: Space ticked
    # it again, writing keep false, and ↓ stayed on the line)
    work = page_folder(tmp_path, clips["mp4"], t0=0.0, t1=12.0)
    with Served(create_app(work, media=Media("source", clips["mp4"]))) as srv:
        r = run_page(browser, srv.url, r"""export default async (p) => {
          const keep = () => p.ev(`fetch('api/state').then((r) => r.json()).then((s) => !!s.lines.find((l) => l.id === 'L0001').keep)`);
          await p.ev(`document.querySelector('#tabs [data-view="lines"]').click()`);
          await p.sleep(150);
          await p.ev(`document.getElementById('lines-row-L0001').click()`);
          await p.sleep(300);
          await p.click('#evidence label.switch:has([data-change="ln-keep"]) span');
          await p.idle();
          const answered = { focus: await p.ev(`@FOCUS@`), keep: await keep(),
                             box: await p.ev(`document.querySelector('#evidence [data-change="ln-keep"]').checked`) };
          await p.ev(`(() => { window._played = 0; document.getElementById('video').addEventListener('play', () => { window._played += 1; }); })()`);
          await p.key('Space');
          await p.idle();
          const space = { played: await p.ev(`window._played`), keep: await keep(), focus: await p.ev(`@FOCUS@`) };
          await p.ev(`(() => { const v = document.getElementById('video'); if (!v.paused) v.pause(); })()`);
          await p.key('ArrowDown');
          await p.idle();
          return { answered, space, sel: await p.ev(`(document.querySelector('#rows .row.is-sel') || {}).id || null`), keep: await keep(),
                   posts: p.posts.filter((u) => u.startsWith('api/lines')).length };
        }""".replace("@FOCUS@", FOCUS))["result"]
    assert r["answered"]["keep"] is True and r["answered"]["box"] is True, r
    assert r["answered"]["focus"] != "INPUT[ln-keep]", r
    assert r["space"]["played"] >= 1 and r["space"]["keep"] is True, r
    assert r["sel"] == "lines-row-L0002" and r["keep"] is True, r
    assert r["posts"] == 1, r                                              # the click's write, nothing more


@browser_only
def test_arrow_goes_to_the_next_person_after_the_reveal_line_is_picked(tmp_path, browser, clips):
    # 인물 하늘: [남자] before the name, then 이 줄부터 L0001 picked in the select and answered; ↓
    # goes to the next person and the picked line stays (the select kept the focus: ↓ picked the
    # next line in it and wrote reveal_line L0003)
    work = page_folder(tmp_path, clips["mp4"], t0=0.0, t1=12.0)
    with Served(create_app(work, media=Media("source", clips["mp4"]))) as srv:
        r = run_page(browser, srv.url, r"""export default async (p) => {
          const dec = () => p.ev(`fetch('api/state').then((r) => r.json()).then((s) => ((s.people || []).find((q) => q.name === '하늘') || {}).decision || {})`);
          await p.ev(`document.querySelector('#tabs [data-view="people"]').click()`);
          await p.sleep(200);
          const names = await p.ev(`[...document.querySelectorAll('#rows .row[data-id]')].map((r) => r.dataset.id)`);
          await p.ev(`document.querySelector('#rows .row[data-id="하늘"]').click()`);
          await p.sleep(200);
          await p.ev(`document.querySelector('#evidence [data-act="pp-mode"][data-mode="generic"]').click()`);
          await p.idle();
          // the pick leaves the focus on the select, as a mouse pick does
          await p.ev(`(() => { const s = document.querySelector('#evidence [data-change="pp-reveal-line"]'); s.focus(); s.selectedIndex = 1; s.dispatchEvent(new Event('change', { bubbles: true })); })()`);
          await p.idle();
          const picked = { dec: await dec(), focus: await p.ev(`@FOCUS@`) };
          await p.key('ArrowDown');
          await p.idle();
          return { names, picked, dec: await dec(),
                   sel: await p.ev(`(() => { const r = document.querySelector('#rows .row.is-sel'); return r ? r.dataset.id : null; })()`) };
        }""".replace("@FOCUS@", FOCUS))["result"]
    names = r["names"]
    assert "하늘" in names and names.index("하늘") + 1 < len(names), names
    assert r["picked"]["dec"].get("reveal") == "line" and r["picked"]["dec"].get("reveal_line") == "L0001", r
    assert r["picked"]["focus"] != "SELECT[pp-reveal-line]", r
    assert r["sel"] == names[names.index("하늘") + 1], r
    assert r["dec"].get("reveal_line") == "L0001" and r["dec"].get("reveal") == "line", r


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


# ------------------------------------------------- 붙지 못한 작가 결정 (state aside)
ASIDE_TYPO = "fp:line@바다:오늘 저녁 일곱 시에 학교 앞 공원에서 만나자#0"
ASIDE_GONE = "fp:line@바다:그럼 내일 봐#0"
# rows the server of today does not send: a 화자 항목 decision (kind speaker) and a key it does not know
ASIDE_SPK = {"key": "fp:speaker@line@하늘:알았어 늦지 않게 갈게#0", "kind": "speaker", "speaker": "하늘",
             "text": "알았어 늦지 않게 갈게", "decision": {"final": "exclude"}, "at": "2026-11-10T09:30:00",
             "candidates": ["L0001", "L0003"], "why": "same_words"}
ASIDE_BAD = {"key": "fp:line@바다:지어낸 다른 말#0", "speaker": "바다", "text": "지어낸 다른 말", "decision": {"sung": True},
             "at": "2026-11-10T09:31:00", "candidates": ["L0001"], "why": "same_words"}
# run in the page before its own script: every state it is answered gets the rows of window.__more
# (those not gone); a put-back of a 화자 row is answered here with the server's state (it would
# say 404 today); every POST api/lines/aside body is kept in window.__puts
ASIDE_HOOK = r"""(() => {
  const real = window.fetch.bind(window);
  window.__more = @MORE@;
  window.__puts = [];
  const add = (data) => {
    const extra = window.__more.filter((a) => !a.gone);
    data.aside = [...(data.aside || []), ...extra];
    if (data.summary && data.summary.lines) data.summary.lines.aside = (data.summary.lines.aside || 0) + extra.length;
    return new Response(JSON.stringify(data), { status: 200, headers: { 'Content-Type': 'application/json' } });
  };
  window.fetch = async (url, init = {}) => {
    const u = String(url);
    if (u.endsWith('api/lines/aside') && init.method === 'POST') {
      const body = JSON.parse(init.body);
      window.__puts.push(body);
      const mine = window.__more.find((a) => a.key === body.key && a.kind === 'speaker');
      if (mine) { mine.gone = true; return add(await (await real('api/state')).json()); }
    }
    const r = await real(url, init);
    if (!r.ok || u.includes('api/media') || u.includes('api/envelope')) return r;
    const data = await r.clone().json().catch(() => null);
    return data && Array.isArray(data.aside) ? add(data) : r;
  };
})();"""


def aside_folder(tmp_path, video):
    """Two decisions of the writer, then the script read again: the words of L0002 fixed (its
    decision waits for the writer: text_changed, candidate L0002) and L0004 cut (gone)."""
    lines = PAGE_LINES + [_line("L0004", "바다", "그럼 내일 봐", 12.2, 13.0)]
    w = page_folder(tmp_path, video, t0=0.0, t1=14.0, lines=lines, speakers=[_speaker_item(ln) for ln in PAGE_LINES])
    p = Project(w)
    p.set_line("L0002", include=False, name="구름", language="영어")
    p.set_line("L0004", text="그럼 내일 또 봐", breaks=[2], source="phone")
    _write_script(w, [PAGE_LINES[0], _line("L0002", "바다", "오늘 저녁 여덟 시에 학교 앞 공원에서 만나자", 8.0, 10.4), PAGE_LINES[2]])
    return w


@browser_only
def test_page_shows_the_decisions_kept_aside_and_puts_them_back(tmp_path, browser, clips):
    # the decisions on no line since the script changed: a fold at the top of the 대사 view (old
    # page: nothing at all) with who and what, what was decided, why, the candidate lines; 이 줄에
    # 붙이기, 버리기 (asks first), a row of the export check that opens the fold, a 404 in Korean
    work = aside_folder(tmp_path, clips["mp4"])
    hook = ASIDE_HOOK.replace("@MORE@", json.dumps([ASIDE_SPK, ASIDE_BAD], ensure_ascii=False))
    with Served(create_app(work, media=Media("source", clips["mp4"]))) as srv:
        r = run_page(browser, srv.url, r"""export default async (p) => {
          await p.reload(@HOOK@);
          const row = (k) => `#aside-box .as-row[data-key="${k}"]`;
          const box = () => p.ev(`(() => { const b = document.getElementById('aside-box'); if (!b) return null;
            return { head: b.querySelector('.aside-head').textContent.replace(/\\s+/g, ' ').trim(), open: b.classList.contains('is-open'),
              rows: [...b.querySelectorAll('.as-row')].map((r) => ({ key: r.dataset.key, text: r.textContent.replace(/\\s+/g, ' ').trim(),
                cands: [...r.querySelectorAll('[data-act="aside-put"]')].map((x) => x.dataset.line) })) }; })()`);
          const toast = () => p.ev(`[...document.querySelectorAll('#toasts .toast')].map((t) => t.textContent).pop() || ''`);
          const sent = () => p.posts.filter((u) => u.startsWith('api/lines/aside')).length;
          await p.ev(`document.querySelector('#tabs [data-view="lines"]').click()`);
          await p.sleep(200);
          const first = await box();
          await p.ev(`document.querySelector('${row('@K1@')} [data-act="goto-line"]').click()`);
          await p.sleep(200);
          const went = { sel: await p.ev(`(document.querySelector('#rows .row.is-sel') || {}).id || null`),
                         panel: await p.ev(`document.querySelector('#evidence .ev-subject').textContent`) };
          await p.ev(`document.querySelector('#aside-box .aside-head').click()`);
          await p.sleep(100);
          const folded = await box();
          await p.ev(`document.querySelector('#tabs [data-view="items"]').click()`);
          await p.ev(`document.querySelector('[data-act="open-export"][data-fmt="srt"]').click()`);
          await p.until(`!!document.querySelector('#modal-box .xp-row')`, 8000);
          const xp = await p.ev(`[...document.querySelectorAll('#modal-box .xp-row')].map((r) => r.textContent.replace(/\\s+/g, ' ').trim()).filter((t) => t.includes('붙지 못한'))`);
          await p.ev(`document.querySelector('#modal-box [data-act="xp-aside"]').click()`);
          await p.sleep(200);
          const back = { view: await p.ev(`document.querySelector('#tabs .tab.is-on').dataset.view`), modal: await p.ev(`!document.getElementById('modal').hidden`), box: await box() };
          await p.ev(`document.querySelector('${row('@KB@')} [data-act="aside-put"]').click()`);
          await p.idle();
          const bad = { toast: await toast(), box: await box() };
          await p.ev(`window.__more.find((a) => a.key === '@KB@').gone = true`);
          await p.ev(`document.querySelector('${row('@K1@')} [data-act="aside-put"][data-line="L0002"]').click()`);
          await p.idle();
          const put = await box();
          await p.ev(`document.querySelector('${row('@KS@')} [data-act="aside-put"][data-line="L0003"]').click()`);
          await p.idle();
          const spk = await box();
          const before = sent();
          await p.ev(`document.querySelector('${row('@K2@')} [data-act="aside-drop"]').click()`);
          await p.sleep(150);
          const asked = { label: await p.ev(`document.querySelector('${row('@K2@')} [data-act="aside-drop"]').textContent`), sent: sent() - before };
          await p.ev(`document.querySelector('${row('@K2@')} [data-act="aside-drop"]').click()`);
          await p.idle();
          const st = await p.ev(`fetch('api/state').then((r) => r.json())`);
          return { first, went, folded, xp, back, bad, put, spk, asked, end: await box(), puts: await p.ev(`window.__puts`),
                   l2: st.lines.find((l) => l.id === 'L0002'), aside: st.aside.map((a) => a.key), n: st.summary.lines.aside };
        }""".replace("@HOOK@", json.dumps(hook)).replace("@K1@", ASIDE_TYPO).replace("@K2@", ASIDE_GONE)
                     .replace("@KS@", ASIDE_SPK["key"]).replace("@KB@", ASIDE_BAD["key"]))["result"]
    f = r["first"]
    assert f and f["open"] and "대본이 바뀌어 붙지 못한 작가 결정 4개" in f["head"], f
    rows = {x["key"]: x for x in f["rows"]}
    assert list(rows) == [ASIDE_TYPO, ASIDE_GONE, ASIDE_SPK["key"], ASIDE_BAD["key"]], list(rows)
    typo, gone, spk = rows[ASIDE_TYPO], rows[ASIDE_GONE], rows[ASIDE_SPK["key"]]
    for want in ("바다", "오늘 저녁 일곱 시에", "대본 글이 바뀜", "줄 빼기", "이름 · 구름", "언어 · [영어]", "오늘 저녁 여덟 시에"):
        assert want in typo["text"], (want, typo["text"])
    assert typo["cands"] == ["L0002"]
    for want in ("대본에서 사라짐", "글 고침 「그럼 내일 또 봐」", "출처 · 전화", "끊을 자리 · 2번째 어절 뒤", "맞는 줄이 대본에 없다"):
        assert want in gone["text"], (want, gone["text"])
    assert gone["cands"] == []
    assert "화자 항목" in spk["text"] and "화자 표시 빼기" in spk["text"] and "같은 글 줄이 여럿" in spk["text"], spk
    assert spk["cands"] == ["L0001", "L0003"]
    assert r["went"]["sel"] == "lines-row-L0002" and "여덟 시에" in r["went"]["panel"], r["went"]
    assert not r["folded"]["open"] and r["folded"]["rows"] == [] and "펼치기" in r["folded"]["head"], r["folded"]
    assert len(r["xp"]) == 1 and "4개" in r["xp"][0], r["xp"]
    b = r["back"]
    assert b["view"] == "lines" and not b["modal"] and b["box"]["open"] and len(b["box"]["rows"]) == 4, b
    assert r["bad"]["toast"].startswith("없는 결정입니다") and len(r["bad"]["box"]["rows"]) == 4, r["bad"]
    assert [x["key"] for x in r["put"]["rows"]] == [ASIDE_GONE, ASIDE_SPK["key"]], r["put"]
    assert [x["key"] for x in r["spk"]["rows"]] == [ASIDE_GONE], r["spk"]
    assert "한 번 더 누르면 버림" in r["asked"]["label"] and r["asked"]["sent"] == 0, r["asked"]
    assert r["end"] is None and r["aside"] == [] and r["n"] == 0, r
    assert r["puts"] == [{"key": ASIDE_BAD["key"], "line": "L0001"}, {"key": ASIDE_TYPO, "line": "L0002"},
                         {"key": ASIDE_SPK["key"], "line": "L0003"}, {"key": ASIDE_GONE, "line": None}], r["puts"]
    l2 = r["l2"]                                                  # the decision is on L0002 now
    assert not l2.get("include") and l2.get("name") == "구름" and l2.get("language") == "영어", l2


# ----------------------------------------------------------------------- 줄 출처
SOURCE_SEL = "document.querySelector('#evidence [data-change=\"ln-source\"]')"


@browser_only
def test_line_source_is_picked_kept_while_sent_and_put_back_when_refused(tmp_path, browser, clips):
    # the line panel picks where a line comes from (the script's, none, a source): stored as the
    # line's source, the label follows ([하늘/속마음]); kept while on its way when an earlier write
    # is answered, back to what is stored when refused (old page: no way to pick it)
    work = page_folder(tmp_path, clips["mp4"], t0=0.0, t1=12.0)
    with Served(create_app(work, media=Media("source", clips["mp4"]))) as srv:
        r = run_page(browser, srv.url, r"""export default async (p) => {
          const sel = `@SEL@`;
          const pick = (v) => p.ev(`(() => { const s = ${sel}; s.value = '${v}'; s.dispatchEvent(new Event('change', { bubbles: true })); })()`);
          const line = () => p.ev(`fetch('api/state').then((r) => r.json()).then((s) => { const l = s.lines.find((x) => x.id === 'L0001'); return { dec: l.decision || {}, label: l.label || null }; })`);
          await p.ev(`document.querySelector('#tabs [data-view="lines"]').click()`);
          await p.sleep(150);
          await p.ev(`document.getElementById('lines-row-L0001').click()`);
          await p.sleep(300);
          const opts = await p.ev(`${sel} ? [...${sel}.options].map((o) => [o.value, o.textContent]) : null`);
          await pick('inner');
          await p.idle();
          const inner = { line: await line(), shown: await p.ev(`${sel}.value`) };
          await pick('none');
          await p.idle();
          const none = { line: await line(), shown: await p.ev(`${sel}.value`) };
          await p.latency(900);
          await p.ev(`document.querySelector('#evidence [data-change="ln-sung"]').click()`);
          await p.sleep(60);
          await pick('phone');
          const seen = [];
          for (let i = 0; i < 24; i++) { seen.push(await p.ev(`${sel}.value`)); await p.sleep(100); }
          await p.idle();
          await p.latency(0);
          const phone = { line: await line(), shown: await p.ev(`${sel}.value`) };
          await p.fail({ status: 500, body: JSON.stringify({ detail: '시험용 서버 오류' }) });
          await pick('radio');
          await p.idle();
          await p.fail(null);
          const refused = { shown: await p.ev(`${sel}.value`), line: await line() };
          await pick('');
          await p.idle();
          return { opts, inner, none, seen, phone, refused, script: { line: await line(), shown: await p.ev(`${sel}.value`) } };
        }""".replace("@SEL@", SOURCE_SEL))["result"]
    assert r["opts"] and [k for k, _ in r["opts"]] == ["", "none", "offscreen", "inner", "video", "phone", "radio", "broadcast", "narration"], r["opts"]
    assert [lab for _, lab in r["opts"]][1:] == ["없음", "화면 밖 (표시 없음)", "속마음", "영상", "전화", "무전", "방송", "내레이션"]
    assert r["opts"][0][1].startswith("대본대로")
    assert r["inner"] == {"line": {"dec": {"source": "inner"}, "label": "[하늘/속마음]"}, "shown": "inner"}, r["inner"]
    assert r["none"] == {"line": {"dec": {"source": ""}, "label": "[하늘]"}, "shown": "none"}, r["none"]
    assert r["seen"] and set(r["seen"]) == {"phone"}, r["seen"]                 # never back to 'none' meanwhile
    assert r["phone"]["shown"] == "phone" and r["phone"]["line"]["dec"] == {"source": "phone", "sung": True}, r["phone"]
    assert r["refused"]["shown"] == "phone" and r["refused"]["line"]["dec"].get("source") == "phone", r["refused"]
    assert r["script"]["shown"] == "" and "source" not in r["script"]["line"]["dec"], r["script"]


# ------------------------------------------------- 앞 줄을 먼저 정해야 함 · 이름 없는 줄
WAIT_LINES = [_line("L0001", "하늘", "누구 계세요?", 5.4, 6.2), _line("L0002", "하늘", "거기 아무도 없어요?", 6.6, 7.6),
              _line("L0003", "바다", "오늘 저녁 일곱 시에 학교 앞 공원에서 만나자", 8.0, 10.4)]


@browser_only
def test_a_speaker_line_waiting_for_the_line_before_says_so_and_goes_to_it(tmp_path, browser, clips):
    # 하늘's second line goes on right after a line still 확인 필요: the link says it waits for it
    # (old page: "이어 말함 · 앞 줄" as if the name were already out) and goes to that line; once
    # it is decided the link says 이어 말함 again
    speakers = [_speaker_item(WAIT_LINES[0], rec="check", reason="말하는 얼굴 판정 애매", speaker=dict(
                    _speaker_item(WAIT_LINES[0])["speaker"], face="unclear")),
                _speaker_item(WAIT_LINES[1], follows="P0001", reason="말하는 얼굴 안 보임 · 앞 줄을 먼저 정해야 함"),
                _speaker_item(WAIT_LINES[2])]
    work = page_folder(tmp_path, clips["mp4"], t0=0.0, t1=12.0, lines=WAIT_LINES, speakers=speakers)
    with Served(create_app(work, media=Media("source", clips["mp4"]))) as srv:
        r = run_page(browser, srv.url, r"""export default async (p) => {
          const link = () => p.ev(`(() => { const a = document.querySelector('#evidence .ev-note [data-act="goto-item"]'); return a ? { text: a.textContent.replace(/\\s+/g, ' ').trim(), id: a.dataset.id } : null; })()`);
          await p.ev(`document.getElementById('row-P0002').click()`);
          await p.sleep(200);
          const waits = await link();
          await p.ev(`document.querySelector('#evidence .ev-note [data-act="goto-item"]').click()`);
          await p.sleep(200);
          const went = await p.ev(`(document.querySelector('#rows .row.is-sel') || {}).id || null`);
          await p.ev(`document.querySelector('#evidence [data-act="decide"][data-decision="include"]').click()`);
          await p.idle();
          await p.ev(`document.getElementById('row-P0002').click()`);
          await p.sleep(200);
          return { waits, went, after: await link() };
        }""")["result"]
    w = r["waits"]
    assert w and w["id"] == "P0001" and w["text"].startswith("앞 줄을 먼저 정해야 함 · 앞 줄") and "이어 말함" not in w["text"], w
    assert r["went"] == "row-P0001", r
    assert r["after"] and r["after"]["text"].startswith("이어 말함 · 앞 줄") and r["after"]["id"] == "P0001", r["after"]


@browser_only
def test_a_line_without_a_name_offers_this_line_only(tmp_path, browser, clips):
    # 화자 미상: every unnamed line would be 'the same person', so the server takes 이 줄 only (400
    # for the rest); the page shows only that one (old page: five scopes with line counts)
    lines = PAGE_LINES + [_line("L0004", None, "거기 누구 없어요", 11.72, 11.98)]
    work = page_folder(tmp_path, clips["mp4"], t0=0.0, t1=12.0, lines=lines)
    with Served(create_app(work, media=Media("source", clips["mp4"]))) as srv:
        r = run_page(browser, srv.url, r"""export default async (p) => {
          const scopes = () => p.ev(`[...document.querySelectorAll('#modal-box input[name="m-scope"]')].map((i) => i.value)`);
          await p.ev(`document.querySelector('#tabs [data-view="lines"]').click()`);
          await p.sleep(150);
          await p.ev(`document.getElementById('lines-row-L0001').click()`);
          await p.sleep(200);
          await p.ev(`document.querySelector('#evidence [data-act="ln-speaker"]').click()`);
          await p.sleep(300);
          const named = await scopes();
          await p.ev(`document.querySelector('#modal-box [data-act="close"]').click()`);
          await p.sleep(150);
          await p.ev(`document.getElementById('lines-row-L0004').click()`);
          await p.sleep(200);
          await p.ev(`document.querySelector('#evidence [data-act="ln-speaker"]').click()`);
          await p.sleep(300);
          const unnamed = { scopes: await scopes(), sub: await p.ev(`[...document.querySelectorAll('#modal-box .sec')].map((s) => s.textContent).find((t) => t.includes('범위')) || ''`) };
          await p.ev(`(() => { const i = document.getElementById('f-newname'); i.focus(); })()`);
          await p.type('구름');
          await p.ev(`document.querySelector('#modal-box [data-act="m-confirm"]').click()`);
          await p.idle();
          const st = await p.ev(`fetch('api/state').then((r) => r.json())`);
          return { named, unnamed, modal: await p.ev(`!document.getElementById('modal').hidden`),
                   toasts: await p.ev(`[...document.querySelectorAll('#toasts .toast')].map((t) => t.className + ' ' + t.textContent)`),
                   l4: st.lines.find((l) => l.id === 'L0004').name || null, l1: st.lines.find((l) => l.id === 'L0001').name };
        }""")["result"]
    assert r["named"] == ["line", "from_here", "before", "scene", "person"], r["named"]
    assert r["unnamed"]["scopes"] == ["line"] and "이 줄만" in r["unnamed"]["sub"], r["unnamed"]
    assert not r["modal"] and not any("toast-error" in t for t in r["toasts"]), r["toasts"]
    assert r["l4"] == "구름" and r["l1"] == "하늘", r


# ---------------------------------------------- 같은 화면의 수 · 장면 밖 · 기다리는 재생용 영상 · 인물
@browser_only
def test_extras_chips_count_as_the_tab_and_the_note_do(tmp_path, browser, clips):
    # 4 speeches open, 2 of them 받아쓰기 헛소리 의심: the chip 정하지 않음 says 2 as the tab and the
    # note do (old page: 4); the 헛소리 의심 chip keeps its 2
    work = page_folder(tmp_path, clips["mp4"], t0=0.0, t1=12.0)
    with Served(create_app(work, media=Media("source", clips["mp4"]))) as srv:
        r = run_page(browser, srv.url, r"""export default async (p) => {
          const look = () => p.ev(`({ chips: Object.fromEntries([...document.querySelectorAll('#state-chips .chip')].map((c) => [c.dataset.filter, Number(c.querySelector('.n').textContent)])),
            tab: (document.querySelector('#tabs [data-view="extras"] .n') || {}).textContent || '0', note: document.getElementById('view-note').textContent,
            rows: document.querySelectorAll('#rows .row[data-id]').length })`);
          await p.ev(`document.querySelector('#tabs [data-view="extras"]').click()`);
          await p.sleep(200);
          const first = await look();
          await p.ev(`document.getElementById('extras-row-X0001').click()`);
          await p.sleep(150);
          await p.ev(`document.querySelector('#evidence [data-act="x-ignore"]').click()`);
          await p.idle();
          return { first, after: await look() };
        }""")["result"]
    f = r["first"]
    assert f["chips"]["open"] == 2 and f["tab"] == "2" and "정하지 않음 2 / 4개" in f["note"] and f["rows"] == 2, f
    assert f["chips"]["suspect"] == 2 and f["chips"]["all"] == 2, f
    a = r["after"]
    assert a["chips"]["open"] == 1 and a["tab"] == "1" and "정하지 않음 1 / 4개" in a["note"], a
    assert a["chips"]["ignored"] == 1, a


@browser_only
def test_a_script_cue_whose_range_reaches_past_its_scene_says_so(tmp_path, browser, clips):
    # window_out [before, after]: the 대본 evidence row (and the range of a candidate the script
    # gives) says which side reaches past another scene; window_by 'range' with window_out false
    # says nothing (old page: nothing anywhere); cue_far's reason is shown as the server gives it
    far = "넣을 근거 부족 · 대본 단서의 장면에서 먼 소리"
    script_ev = lambda t, wo, by: {"kind": "대본", "t": t, "text": "대본 지문: \"문이 쾅 닫힌다\"", "polarity": "include", "source": "script",
                                   "window": [3.0, 9.0], "window_by": by, "window_out": wo}
    sounds = [{"id": "S0002", "kind": "sfx", "onset": 3.5, "t0": 3.5, "t1": 4.0, "phrase": "[쾅]", "short": "[쾅]", "phrases": [{"text": "[쾅]", "short": "[쾅]"}],
               "rec": "check", "reason": far, "flags": ["script"], "category": "event", "cue_id": "C0001", "cue_far": True,
               "label": {"class": "Slam", "ko": "쾅", "confident": True},
               "evidence": [script_ev(3.5, [True, False], ["other_scene", "line"]),
                            {"kind": "소리", "t": 3.5, "t1": 4.0, "text": "쾅 소리", "polarity": "include", "source": "sed"}]},
              {"id": "C0002", "kind": "sfx", "origin": "script", "cue_id": "C0002", "estimated": True, "onset": 8.4, "t0": 8.4, "t1": 9.4,
               "phrase": "[초인종 소리]", "short": "[초인종]", "phrases": [{"text": "[초인종 소리]", "short": "[초인종]"}], "rec": "check",
               "reason": "대본 단서 · 소리를 찾지 못함", "flags": ["script", "timing_estimated"], "category": "event",
               "label": {"class": "", "ko": "초인종", "confident": False}, "window": [8.0, 11.0],
               "window_by": ["line", "range"], "window_out": [False, True], "evidence": [script_ev(8.4, [False, True], ["line", "range"])]},
              {"id": "S0003", "kind": "sfx", "onset": 6.0, "t0": 6.0, "t1": 6.5, "phrase": "[똑똑]", "short": "[똑똑]", "phrases": [{"text": "[똑똑]", "short": "[똑똑]"}],
               "rec": "include", "reason": "대본 지문에 적힌 소리", "flags": ["script"], "category": "event", "cue_id": "C0003",
               "label": {"class": "Knock", "ko": "똑똑", "confident": True},
               "evidence": [script_ev(6.0, [False, False], ["range", "line"])]}]
    work = page_folder(tmp_path, clips["mp4"], t0=0.0, t1=12.0, sounds=sounds)
    with Served(create_app(work, media=Media("source", clips["mp4"]))) as srv:
        r = run_page(browser, srv.url, r"""export default async (p) => {
          const look = async (id) => {
            await p.ev(`document.getElementById('row-${id}').click()`);
            await p.sleep(200);
            return p.ev(`({ out: [...document.querySelectorAll('#evidence .ev-row')].map((e) => (e.querySelector('.ev-out') || {}).textContent || ''),
              box: (document.querySelector('#evidence .move-box .ev-out') || {}).textContent || '', note: (document.querySelector('#evidence .ev-note') || {}).textContent || '',
              sub: (document.querySelector('#row-${id} .r-sub') || {}).textContent || '' })`);
          };
          return { s2: await look('S0002'), c2: await look('C0002'), s3: await look('S0003') };
        }""")["result"]
    assert r["s2"]["out"] == ["시각 범위가 장면 밖까지 (앞 장면 쪽)", ""], r["s2"]
    assert far in r["s2"]["note"] and far in r["s2"]["sub"], r["s2"]
    assert r["c2"]["out"] == ["시각 범위가 장면 밖까지 (뒤 장면 쪽)"] and r["c2"]["box"] == "시각 범위가 장면 밖까지 (뒤 장면 쪽)", r["c2"]
    assert r["s3"]["out"] == [""] and not r["s3"]["box"], r["s3"]


@browser_only
def test_page_says_it_waits_for_the_copy_made_elsewhere(tmp_path, browser, clips):
    # building with no progress of its own and the note of a wait (another worker makes this
    # work's copy): the bar and the Space toast give the note, not an empty "만드는 중  %"
    work = page_folder(tmp_path, clips["mkv"])
    media = Media("building", t0=5.0, progress=None, note=WAIT_NOTE)
    with Served(create_app(work, media=media)) as srv:
        r = run_page(browser, srv.url, r"""export default async (p) => {
          const before = await p.ev(`@STAGE@`);
          await p.ev(`document.activeElement && document.activeElement.blur()`);
          await p.ev(`document.dispatchEvent(new KeyboardEvent('keydown', { code: 'Space', key: ' ', bubbles: true }))`);
          await p.sleep(200);
          return { before, after: await p.ev(`@STAGE@`) };
        }""".replace("@STAGE@", STAGE))["result"]
    s = r["before"]
    assert s["still"] and not s["src"] and s["msg"].startswith(WAIT_NOTE) and "%" not in s["msg"] and "만드는 중  " not in s["msg"], s
    assert any(t.startswith(WAIT_NOTE) for t in r["after"]["toasts"]), r["after"]["toasts"]


@browser_only
def test_the_reveal_line_picked_stays_while_an_earlier_write_is_answered(tmp_path, browser, clips):
    # 하늘 [남자]: 끝까지 일반 표시, then right after it 이 줄부터 L0001 in the select; the answer to
    # the first write comes while the pick is on its way: the select keeps L0001 (old page: back
    # to 고르기 until its own answer came)
    work = page_folder(tmp_path, clips["mp4"], t0=0.0, t1=12.0)
    with Served(create_app(work, media=Media("source", clips["mp4"]))) as srv:
        r = run_page(browser, srv.url, r"""export default async (p) => {
          const sel = `document.querySelector('#evidence [data-change="pp-reveal-line"]')`;
          await p.ev(`document.querySelector('#tabs [data-view="people"]').click()`);
          await p.sleep(200);
          await p.ev(`document.querySelector('#rows .row[data-id="하늘"]').click()`);
          await p.sleep(200);
          await p.ev(`document.querySelector('#evidence [data-act="pp-mode"][data-mode="generic"]').click()`);
          await p.idle();
          await p.latency(900);
          await p.ev(`document.querySelector('#evidence [data-act="pp-reveal"][data-reveal="never"]').click()`);
          await p.sleep(60);
          await p.ev(`(() => { const s = ${sel}; s.value = 'L0001'; s.dispatchEvent(new Event('change', { bubbles: true })); })()`);
          const seen = [];
          for (let i = 0; i < 26; i++) { seen.push(await p.ev(`${sel}.value + (document.querySelector('#evidence .pp-line.is-on') ? '+' : '')`)); await p.sleep(100); }
          await p.idle();
          await p.latency(0);
          const dec = await p.ev(`fetch('api/state').then((r) => r.json()).then((s) => (s.people.find((q) => q.name === '하늘') || {}).decision || {})`);
          return { seen, dec, end: await p.ev(`${sel}.value`) };
        }""")["result"]
    assert r["seen"] and set(r["seen"]) == {"L0001+"}, r["seen"]
    assert r["dec"].get("reveal") == "line" and r["dec"].get("reveal_line") == "L0001" and r["end"] == "L0001", r


# --------------------------------------- the review layer's new fields (6차 약속 나)
# The server of this branch neither sends nor takes them yet: run in the page before its own
# script, every state it is answered gets the fields of window.__over (lines · items · added ·
# people · extras by id or name; ``decision`` merged, a null takes the field away; cells by a
# line or item they are made of: pos, check.notes; the export check, its lines, the summary;
# more aside rows). A POST that window.__answer(url, body) takes (it changes __over as the server
# would) is answered here with the server's state and __over on it, or — window.__refuse — with
# that error and __over as it was. A POST the real server answers is shown first to
# window.__kept(url, body, state) (it puts in __over what the new server would keep of that
# body and this one drops, e.g. the pos of a new api/added row whose id the server gives), then
# __over goes on it. Every POST is kept in window.__seen as [url, body].
NEW_HOOK = r"""(() => {
  const real = window.fetch.bind(window);
  window.__over = Object.assign({ lines: {}, items: {}, added: {}, people: {}, extras: {}, cells: {}, check: {}, check_lines: {}, summary: {}, aside: [] }, @OVER@);
  window.__seen = [];
  window.__answer = null;
  window.__kept = null;
  window.__refuse = null;
  window.__dec = (id, fields) => { const o = (window.__over.lines[id] = window.__over.lines[id] || {}); o.decision = { ...(o.decision || {}), ...fields }; };
  const put = (row, o) => {
    for (const [k, v] of Object.entries(o)) {
      if (k !== 'decision') { row[k] = v; continue; }
      const d = { ...(row.decision || {}) };
      for (const [a, b] of Object.entries(v)) { if (b === null) delete d[a]; else d[a] = b; }
      if (Object.keys(d).length) row.decision = d; else delete row.decision;
    }
  };
  const fix = (st) => {
    const O = window.__over;
    for (const ln of st.lines || []) if (O.lines[ln.id]) put(ln, O.lines[ln.id]);
    for (const it of st.items || []) if (O.items[it.id]) put(it, O.items[it.id]);
    for (const a of st.added || []) if (O.added[a.id]) put(a, O.added[a.id]);
    for (const q of st.people || []) if (O.people[q.name]) put(q, O.people[q.name]);
    for (const x of st.extras || []) if (O.extras[x.id]) put(x, O.extras[x.id]);
    for (const c of (st.draft && st.draft.cells) || []) {
      for (const [ref, o] of Object.entries(O.cells)) {
        if (!(c.refs || []).includes(ref)) continue;
        if ('pos' in o) { if (o.pos) c.pos = o.pos; else delete c.pos; }
        if (o.notes) c.check = { ...(c.check || {}), notes: o.notes };
      }
    }
    if (st.export_check) { Object.assign(st.export_check, O.check); st.export_check.lines = { ...(st.export_check.lines || {}), ...O.check_lines }; }
    if (st.summary) Object.assign(st.summary, O.summary);
    if (O.aside.length) { st.aside = [...(st.aside || []), ...O.aside]; if (st.summary && st.summary.lines) st.summary.lines.aside = st.aside.length; }
    return st;
  };
  const json = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
  window.fetch = async (url, init = {}) => {
    const u = String(url);
    const post = init.method === 'POST';
    const body = post && init.body ? JSON.parse(init.body) : null;
    if (post) {
      window.__seen.push([u.replace(/^.*?(api\/)/, '$1'), body]);
      const was = JSON.stringify(window.__over);
      if (window.__answer && window.__answer(u, body || {})) {
        if (window.__refuse) {
          const e = window.__refuse;
          window.__refuse = null;
          window.__over = JSON.parse(was);
          return json({ detail: e.detail }, e.status);
        }
        return json(fix(await (await real('api/state')).json()));
      }
    }
    const r = await real(url, init);
    if (!r.ok || u.includes('api/media') || u.includes('api/envelope')) return r;
    const data = await r.clone().json().catch(() => null);
    if (post && data && window.__kept) window.__kept(u, body || {}, data.state || data);
    if (data && Array.isArray(data.lines)) return json(fix(data));
    if (data && data.state && Array.isArray(data.state.lines)) { fix(data.state); data.export_check = data.state.export_check; return json(data); }
    return r;
  };
})();"""


def new_hook(over=None):
    return json.dumps(NEW_HOOK.replace("@OVER@", json.dumps(over or {}, ensure_ascii=False)))


JOIN_WHY = "앞 줄과 한 칸으로 묶지 못함 · 두 줄이 7초를 넘음"
ASIDE_NEW = {"key": "fp:line@하늘:지어낸 다른 말이야#0", "speaker": "하늘", "text": "지어낸 다른 말이야", "at": "2026-11-11T09:30:00",
             "decision": {"join": True, "pos": "top", "manner": "속삭이며"}, "candidates": [], "why": "gone"}


@browser_only
def test_a_line_is_put_in_one_cell_with_the_line_before_or_says_why_not(tmp_path, browser, clips):
    # 앞 줄과 한 칸으로 (line join): a switch on every line in but the first; joined shows 앞 줄과
    # 한 칸, asked and not joined shows the cell's note; kept while it is sent when an earlier write
    # is answered, back to what is stored when refused; a kept-aside join · pos · manner in words
    # (old page: no switch at all)
    work = page_folder(tmp_path, clips["mp4"], t0=0.0, t1=12.0)
    over = {"lines": {"L0003": {"joined": True}}, "aside": [ASIDE_NEW]}
    with Served(create_app(work, media=Media("source", clips["mp4"]))) as srv:
        r = run_page(browser, srv.url, r"""export default async (p) => {
          await p.reload(@HOOK@);
          await p.ev(`window.__ok = true; window.__answer = (u, b) => {
            if (!u.endsWith('api/lines/L0002') || !('join' in b)) return false;
            window.__dec('L0002', { join: b.join });
            window.__over.lines.L0002.joined = !!b.join && window.__ok;
            window.__over.cells.L0002 = { notes: b.join && !window.__ok ? ['@WHY@'] : [] };
            return true; }`);
          const sw = `document.querySelector('#evidence [data-change="ln-join"]')`;
          const look = () => p.ev(`({ sw: !!${sw}, on: !!(${sw} || {}).checked, now: (document.querySelector('#evidence .join-now') || {}).textContent || '',
            prev: (document.querySelector('#evidence .join-prev') || {}).textContent || '' })`);
          const pick = async (id) => { await p.ev(`document.getElementById('lines-row-${id}').click()`); await p.sleep(250); };
          const flip = async () => { await p.ev(`${sw}.click()`); await p.idle(); return look(); };
          await p.ev(`document.querySelector('#tabs [data-view="lines"]').click()`);
          await p.sleep(200);
          const aside = await p.ev(`[...document.querySelectorAll('#aside-box .as-row')].map((r) => r.textContent.replace(/\\s+/g, ' ').trim())`);
          await pick('L0001');
          const first = await look();
          await pick('L0003');
          const auto = await look();
          await pick('L0002');
          const off = await look();
          const joined = await flip();
          const apart = await flip();
          await p.ev(`window.__ok = false`);
          const refused = await flip();
          await p.latency(900);
          await p.ev(`document.querySelector('#evidence [data-change="ln-sung"]').click()`);
          await p.sleep(60);
          await p.ev(`${sw}.click()`);
          const seen = [];
          for (let i = 0; i < 24; i++) { seen.push(await p.ev(`!!${sw}.checked`)); await p.sleep(100); }
          await p.idle();
          await p.latency(0);
          const sent = await look();
          await p.ev(`window.__refuse = { status: 500, detail: '시험용 서버 오류' }`);
          await p.ev(`${sw}.click()`);
          await p.idle();
          const back = { ...(await look()), toast: await p.ev(`[...document.querySelectorAll('#toasts .toast')].map((t) => t.textContent).pop() || ''`) };
          return { aside, first, auto, off, joined, apart, refused, seen, sent, back,
                   bodies: await p.ev(`window.__seen.filter(([u, b]) => u === 'api/lines/L0002' && 'join' in b).map(([, b]) => b)`) };
        }""".replace("@HOOK@", new_hook(over)).replace("@WHY@", JOIN_WHY))["result"]
    assert not r["first"]["sw"], r["first"]                                   # the first line has no line before
    assert r["auto"]["sw"] and not r["auto"]["on"] and r["auto"]["now"] == "앞 줄과 한 칸", r["auto"]
    o = r["off"]
    assert o["sw"] and not o["on"] and o["now"] == "" and "하늘" in o["prev"] and "누구 계세요?" in o["prev"], o
    assert r["joined"]["on"] and r["joined"]["now"] == "앞 줄과 한 칸", r["joined"]
    assert not r["apart"]["on"] and r["apart"]["now"] == "", r["apart"]
    assert r["refused"]["on"] and r["refused"]["now"] == JOIN_WHY, r["refused"]
    assert r["seen"] and set(r["seen"]) == {False}, r["seen"]                 # never back to 'on' meanwhile
    assert not r["sent"]["on"] and r["sent"]["now"] == "", r["sent"]
    b = r["back"]
    assert not b["on"] and b["toast"] == "시험용 서버 오류", b
    assert r["bodies"] == [{"join": True}, {"join": None}, {"join": True}, {"join": None}, {"join": True}], r["bodies"]
    a = [t for t in r["aside"] if "지어낸 다른 말이야" in t]
    assert len(a) == 1 and "앞 줄과 한 칸으로" in a[0] and "위치 · 위" in a[0] and "말투 · [속삭이며]" in a[0] and "join ·" not in a[0], r["aside"]


@browser_only
def test_the_cell_of_a_line_a_sound_or_an_added_one_goes_up_or_down(tmp_path, browser, clips):
    # 위치 아래 · 위 on the line panel (api/lines {pos}), on a sound's panel (api/items {pos}), in
    # the 새로 넣기 dialog (api/added · api/added/{id} pos) and on a line the writer added (its
    # api/added/{id}); a cell at the top has a mark on its row (old page: nothing to pick).
    # The added row keeps the pos it was made or changed with (약속 나: added[].pos); this branch's
    # server drops it, so __kept plays the one that keeps it (there it changes nothing). Its
    # 넣은 것 고치기 dialog opens at the row's pos, so saving new words there keeps 위.
    work = page_folder(tmp_path, clips["mp4"], t0=0.0, t1=12.0)
    with Served(create_app(work, media=Media("source", clips["mp4"]))) as srv:
        r = run_page(browser, srv.url, r"""export default async (p) => {
          await p.reload(@HOOK@);
          await p.ev(`window.__answer = (u, b) => {
            if (!('pos' in b)) return false;
            const id = u.split('/').pop();
            if (u.includes('api/lines/')) { window.__dec(id, { pos: b.pos }); window.__over.cells[id] = { pos: b.pos }; return true; }
            if (u.includes('api/items/')) { window.__over.items[id] = { pos: b.pos }; window.__over.cells[id] = { pos: b.pos }; return true; }
            if (u.includes('api/added/') && Object.keys(b).length === 1) { window.__over.added[id] = { pos: b.pos }; return true; }
            return false; }`);
          await p.ev(`window.__kept = (u, b, s) => {
            const at = u.slice(u.indexOf('api/')).split('/');
            if (at[1] !== 'added' || !('pos' in b)) return;
            const id = at.length === 2 ? ((s.added || []).find((a) => a.text === b.text) || {}).id : at[2];
            if (id) window.__over.added[id] = { pos: b.pos }; }`);
          const chips = (act) => p.ev(`[...document.querySelectorAll('#evidence [data-act="${act}"]')].map((b) => b.textContent + (b.getAttribute('aria-pressed') === 'true' ? '*' : ''))`);
          const go = async (act, v) => { await p.ev(`document.querySelector('#evidence [data-act="${act}"][data-v="${v}"]').click()`); await p.idle(); return chips(act); };
          await p.ev(`document.querySelector('#tabs [data-view="lines"]').click()`);
          await p.sleep(200);
          await p.ev(`document.getElementById('lines-row-L0002').click()`);
          await p.sleep(250);
          const line0 = await chips('ln-pos');
          const lineUp = await go('ln-pos', 'top');
          await p.ev(`document.querySelector('#tabs [data-view="cells"]').click()`);
          await p.sleep(200);
          const tops = await p.ev(`[...document.querySelectorAll('#rows .row-cell')].filter((r) => r.querySelector('.t-top')).map((r) => r.id)`);
          const nCells = await p.ev(`document.querySelectorAll('#rows .row-cell').length`);
          const ofLine = await p.ev(`fetch('api/state').then((r) => r.json()).then((s) => s.draft.cells.filter((c) => (c.refs || []).includes('L0002')).map((c) => 'cells-row-' + c.id))`);
          await p.ev(`document.querySelector('#tabs [data-view="lines"]').click()`);
          await p.sleep(200);
          const lineDown = await go('ln-pos', '');
          await p.ev(`document.querySelector('#tabs [data-view="items"]').click()`);
          await p.sleep(200);
          await p.ev(`document.getElementById('row-P0001').click()`);
          await p.sleep(200);
          const speaker = await chips('it-pos');
          await p.ev(`document.getElementById('row-S0001').click()`);
          await p.sleep(200);
          const sound0 = await chips('it-pos');
          const soundUp = await go('it-pos', 'top');
          await p.ev(`document.querySelector('[data-act="open-add"]').click()`);
          await p.sleep(200);
          const radios = await p.ev(`[...document.querySelectorAll('#modal-box input[name="add-pos"]')].map((i) => i.value + (i.checked ? '*' : ''))`);
          await p.ev(`document.getElementById('add-text').value = '지어낸 새 줄이다'`);
          await p.ev(`document.querySelector('#modal-box input[name="add-pos"][value="top"]').click()`);
          await p.ev(`document.querySelector('#modal-box [data-act="add-save"]').click()`);
          await p.idle();
          const st = await p.ev(`fetch('api/state').then((r) => r.json())`);
          const aid = (st.added[0] || {}).id;
          const added0 = await chips('ln-pos');
          const addedDown = await go('ln-pos', '');
          await p.ev(`document.querySelector('#evidence [data-act="added-edit"]').click()`);
          await p.sleep(200);
          const editRadios = await p.ev(`[...document.querySelectorAll('#modal-box input[name="add-pos"]')].map((i) => i.value + (i.checked ? '*' : ''))`);
          await p.ev(`document.querySelector('#modal-box input[name="add-pos"][value="top"]').click()`);
          await p.ev(`document.querySelector('#modal-box [data-act="add-save"]').click()`);
          await p.idle();
          const addedEnd = await chips('ln-pos');
          await p.ev(`document.querySelector('#evidence [data-act="added-edit"]').click()`);
          await p.sleep(200);
          const editAgain = await p.ev(`[...document.querySelectorAll('#modal-box input[name="add-pos"]')].map((i) => i.value + (i.checked ? '*' : ''))`);
          await p.ev(`document.getElementById('add-text').value = '지어낸 고친 줄이다'`);
          await p.ev(`document.querySelector('#modal-box [data-act="add-save"]').click()`);
          await p.idle();
          const wordsOnly = await chips('ln-pos');
          return { line0, lineUp, tops, nCells, ofLine, lineDown, speaker, sound0, soundUp, radios, aid, added0, addedDown, editRadios, addedEnd, editAgain, wordsOnly,
                   seen: await p.ev(`window.__seen.filter(([, b]) => 'pos' in b)`) };
        }""".replace("@HOOK@", new_hook()))["result"]
    assert r["line0"] == ["아래*", "위"] and r["lineUp"] == ["아래", "위*"] and r["lineDown"] == ["아래*", "위"], r
    assert r["ofLine"] and sorted(r["tops"]) == sorted(r["ofLine"]) and r["nCells"] > len(r["ofLine"]), r   # the cells of L0002 only
    assert r["speaker"] == [], r["speaker"]                                  # a speaker's line has its own
    assert r["sound0"] == ["아래*", "위"] and r["soundUp"] == ["아래", "위*"], r
    assert r["radios"] == ["*", "top"] and r["aid"], r
    # made with 위 it is up (the reply's added[].pos), 아래 on its panel, the dialog opens at 아래, saved at 위
    assert r["added0"] == ["아래", "위*"] and r["addedDown"] == ["아래*", "위"] and r["editRadios"] == ["*", "top"], r
    assert r["addedEnd"] == ["아래", "위*"], r
    # stored at 위, the dialog opens at 위: it reads the row's added[].pos (아래 above is also what it
    # opens at by itself); a change of the words alone keeps 위
    assert r["editAgain"] == ["", "top*"] and r["wordsOnly"] == ["아래", "위*"], r
    seen = r["seen"]
    assert seen[0] == ["api/lines/L0002", {"pos": "top"}] and seen[1] == ["api/lines/L0002", {"pos": None}], seen
    assert seen[2] == ["api/items/S0001", {"pos": "top"}], seen
    assert seen[3][0] == "api/added" and seen[3][1]["pos"] == "top" and seen[3][1]["text"] == "지어낸 새 줄이다", seen
    assert seen[4] == [f"api/added/{r['aid']}", {"pos": None}], seen
    assert seen[5][0] == f"api/added/{r['aid']}" and seen[5][1]["pos"] == "top" and seen[5][1]["text"] == "지어낸 새 줄이다", seen
    assert seen[6][0] == f"api/added/{r['aid']}" and seen[6][1]["pos"] == "top" and seen[6][1]["text"] == "지어낸 고친 줄이다", seen
    assert len(seen) == 7, seen


@browser_only
def test_the_manner_the_script_writes_is_put_on_written_or_taken_off(tmp_path, browser, clips):
    # 말투 in 말 표시: the script's words (delivery) with 표시에 넣기, the writer's own (20 letters at
    # most: a longer one is refused here and not sent), 빼기; what is on now (old page: nothing)
    work = page_folder(tmp_path, clips["mp4"], t0=0.0, t1=12.0)
    over = {"lines": {"L0002": {"delivery": ["속삭이며", "다급하게"]}}}
    with Served(create_app(work, media=Media("source", clips["mp4"]))) as srv:
        r = run_page(browser, srv.url, r"""export default async (p) => {
          await p.reload(@HOOK@);
          await p.ev(`window.__answer = (u, b) => {
            if (!u.includes('api/lines/') || !('manner' in b)) return false;
            window.__dec(u.split('/').pop(), { manner: b.manner });
            return true; }`);
          const inp = `document.querySelector('#evidence [data-change="ln-manner"]')`;
          const look = () => p.ev(`({ now: (document.querySelector('#evidence .manner-now') || {}).textContent || null,
            from: (document.querySelector('#evidence .manner-from') || {}).textContent || null, value: (${inp} || {}).value ?? null,
            cues: [...document.querySelectorAll('#evidence .manner-cue')].map((c) => c.querySelector('.tx').textContent + (c.querySelector('button').getAttribute('aria-pressed') === 'true' ? '*' : '')),
            drop: !!document.querySelector('#evidence [data-act="ln-manner-drop"]') })`);
          const type = async (v) => { await p.ev(`(() => { const i = ${inp}; i.value = ${JSON.stringify(v)}; i.dispatchEvent(new Event('change', { bubbles: true })); })()`); await p.idle(); return look(); };
          await p.ev(`document.querySelector('#tabs [data-view="lines"]').click()`);
          await p.sleep(200);
          await p.ev(`document.getElementById('lines-row-L0001').click()`);
          await p.sleep(250);
          const none = await look();
          await p.ev(`document.getElementById('lines-row-L0002').click()`);
          await p.sleep(250);
          const cues = await look();
          await p.ev(`document.querySelector('#evidence [data-act="ln-manner-put"][data-v="속삭이며"]').click()`);
          await p.idle();
          const put = await look();
          const own = await type('[숨죽여]');
          const n0 = await p.ev(`window.__seen.length`);
          const long = await type('가'.repeat(21));
          const longSent = (await p.ev(`window.__seen.length`)) - n0;
          const toast = await p.ev(`[...document.querySelectorAll('#toasts .toast')].map((t) => t.textContent).pop() || ''`);
          await p.ev(`document.querySelector('#evidence [data-act="ln-manner-drop"]').click()`);
          await p.idle();
          const dropped = await look();
          const st = await p.ev(`fetch('api/state').then((r) => r.json()).then((s) => s.lines.find((l) => l.id === 'L0002').decision || {})`);
          return { none, cues, put, own, long, longSent, toast, dropped, st,
                   bodies: await p.ev(`window.__seen.filter(([, b]) => 'manner' in b).map(([u, b]) => [u, b])`) };
        }""".replace("@HOOK@", new_hook(over)))["result"]
    assert r["none"]["now"] == "붙은 말투 없음" and "대본에 적힌 말투 없음" in r["none"]["from"] and r["none"]["cues"] == [], r["none"]
    c = r["cues"]
    assert c["cues"] == ["속삭이며", "다급하게"] and c["now"] == "붙은 말투 없음" and c["value"] == "" and not c["drop"], c
    assert r["put"]["now"] == "지금 붙은 말투 [속삭이며]" and r["put"]["value"] == "속삭이며" and r["put"]["cues"] == ["속삭이며*", "다급하게"], r["put"]
    assert r["own"]["now"] == "지금 붙은 말투 [숨죽여]" and r["own"]["value"] == "숨죽여" and r["own"]["drop"], r["own"]
    assert r["longSent"] == 0 and r["long"]["value"] == "숨죽여" and "20자" in r["toast"], r
    assert r["dropped"]["now"] == "붙은 말투 없음" and r["dropped"]["value"] == "" and not r["dropped"]["drop"], r["dropped"]
    assert "manner" not in r["st"], r["st"]
    assert r["bodies"] == [["api/lines/L0002", {"manner": "속삭이며"}], ["api/lines/L0002", {"manner": "숨죽여"}],
                           ["api/lines/L0002", {"manner": None}]], r["bodies"]


@browser_only
def test_a_person_who_may_be_another_is_merged_or_told_apart(tmp_path, browser, clips):
    # people[].maybe_same on a person's row: 같은 사람으로 (asks once; then every line of the person
    # takes the other name through api/lines/{id}/speaker scope person), 아님 (api/people not_same)
    # (old page: nothing on the row)
    work = page_folder(tmp_path, clips["mp4"], t0=0.0, t1=12.0)
    over = {"people": {"하늘": {"maybe_same": ["바다"]}, "바다": {"maybe_same": ["하늘"]}}}
    with Served(create_app(work, media=Media("source", clips["mp4"]))) as srv:
        r = run_page(browser, srv.url, r"""export default async (p) => {
          await p.reload(@HOOK@);
          await p.ev(`window.__answer = (u, b) => {
            if (u.endsWith('api/people') && b.not_same) { for (const n of b.names) window.__over.people[n] = { maybe_same: [] }; return true; }
            if (u.endsWith('/speaker') && b.scope === 'person') window.__over.people['바다'] = { maybe_same: [] };
            return false; }`);
          const row = (n) => `#rows .row[data-id="${n}"]`;
          const same = (n) => p.ev(`[...document.querySelectorAll('${row(n)} .r-same')].map((s) => ({ text: s.querySelector('.flag').textContent,
            acts: [...s.querySelectorAll('button')].map((b) => b.dataset.act + ':' + b.textContent.trim()) }))`);
          await p.ev(`document.querySelector('#tabs [data-view="people"]').click()`);
          await p.sleep(250);
          const first = { sky: await same('하늘'), sea: await same('바다') };
          await p.ev(`document.querySelector('${row('하늘')} [data-act="pp-not-same"]').click()`);
          await p.idle();
          const told = { sky: await same('하늘'), sea: await same('바다') };
          const n0 = await p.ev(`window.__seen.length`);
          await p.ev(`document.querySelector('${row('바다')} [data-act="pp-same"]').click()`);
          await p.sleep(150);
          const asked = { label: await p.ev(`document.querySelector('${row('바다')} [data-act="pp-same"]').textContent`), sent: (await p.ev(`window.__seen.length`)) - n0 };
          await p.ev(`document.querySelector('${row('바다')} [data-act="pp-same"]').click()`);
          await p.idle();
          const st = await p.ev(`fetch('api/state').then((r) => r.json())`);
          return { first, told, asked, seen: await p.ev(`window.__seen`), names: st.lines.map((l) => [l.id, l.name]),
                   after: await same('바다') };
        }""".replace("@HOOK@", new_hook(over)))["result"]
    f = r["first"]
    assert [s["text"] for s in f["sky"]] == ["같은 사람일 수 있음: 바다"] and [s["text"] for s in f["sea"]] == ["같은 사람일 수 있음: 하늘"], f
    assert f["sea"][0]["acts"] == ["pp-same:같은 사람으로", "pp-not-same:아님"], f
    assert r["told"]["sky"] == [] and len(r["told"]["sea"]) == 1, r["told"]
    assert "한 번 더 누르면 하늘로 합침" in r["asked"]["label"] and "되돌릴 수 없음" in r["asked"]["label"] and r["asked"]["sent"] == 0, r["asked"]
    assert r["seen"] == [["api/people", {"names": ["하늘"], "not_same": ["바다"]}],
                         ["api/lines/L0002/speaker", {"name": "하늘", "scope": "person"}]], r["seen"]
    assert dict(r["names"])["L0002"] == "하늘" and r["after"] == [], r


@browser_only
def test_the_export_check_lists_speech_heard_where_no_cell_is_and_names_that_did_not_settle(tmp_path, browser, clips):
    # 내보내기 전 검사: 말이 들리는데 자막 칸이 없는 구간 N곳 with a list that opens (each goes to its
    # time, and to its 대본에 없는 말 or its line), and 배치를 거듭해도 이름 표시가 맞춰지지 않은 줄 N개
    # with its first line (old page: neither row)
    work = page_folder(tmp_path, clips["mp4"], t0=0.0, t1=12.0)
    heard = [{"start": 6.5, "end": 7.4, "text": "저기요 잠깐만요", "words": [], "extra": "X0001", "line": None},
             {"start": 2.4, "end": 3.0, "text": "어디 가는 거야", "words": [], "extra": None, "line": "L0002"},
             {"start": 4.1, "end": 4.5, "text": "응 그래", "words": [], "extra": None, "line": None}]
    over = {"check": {"heard_no_cell": heard}, "summary": {"heard_no_cell": 3}, "check_lines": {"unsettled": ["L0003"]}}
    with Served(create_app(work, media=Media("source", clips["mp4"]))) as srv:
        r = run_page(browser, srv.url, r"""export default async (p) => {
          await p.reload(@HOOK@);
          const open = async () => { await p.ev(`document.querySelector('[data-act="open-export"][data-fmt="srt"]').click()`); await p.until(`!!document.querySelector('#modal-box .xp-row')`, 8000); };
          const where = () => p.ev(`({ modal: !document.getElementById('modal').hidden, view: document.querySelector('#tabs .tab.is-on').dataset.view,
            sel: (document.querySelector('#rows .row.is-sel') || {}).id || null, clock: document.getElementById('stage-time').textContent })`);
          const go = async (i) => { await p.ev(`document.querySelector('#hnc-list [data-act="hnc-go"][data-i="${i}"]').click()`); await p.sleep(300); return where(); };
          await open();
          const rows = await p.ev(`[...document.querySelectorAll('#modal-box .xp-row')].map((r) => r.textContent.replace(/\\s+/g, ' ').trim())`);
          const shut = await p.ev(`!!document.getElementById('hnc-list')`);
          await p.ev(`document.querySelector('#modal-box [data-act="xp-hnc"]').click()`);
          await p.sleep(100);
          const list = await p.ev(`[...document.querySelectorAll('#hnc-list .hnc')].map((b) => b.textContent.replace(/\\s+/g, ' ').trim())`);
          const toExtra = await go(0);
          await open();
          const toLine = await go(1);
          await open();
          const toTime = await go(2);
          await open();
          await p.ev(`[...document.querySelectorAll('#modal-box .xp-row')].find((r) => r.textContent.includes('맞춰지지 않은')).querySelector('[data-act="goto-line"]').click()`);
          await p.sleep(300);
          return { rows, shut, list, toExtra, toLine, toTime, unsettled: await where() };
        }""".replace("@HOOK@", new_hook(over)))["result"]
    heardRow = [t for t in r["rows"] if "말이 들리는데 자막 칸이 없는 구간" in t]
    assert len(heardRow) == 1 and "구간 3곳" in heardRow[0] and "펼치기" in heardRow[0], r["rows"]
    unset = [t for t in r["rows"] if "배치를 거듭해도 이름 표시가 맞춰지지 않은 줄" in t]
    assert len(unset) == 1 and "줄 1개" in unset[0] and "첫 줄 보기" in unset[0], r["rows"]
    assert r["shut"] is False and len(r["list"]) == 3, r
    assert "00:06.5" in r["list"][0] and "저기요 잠깐만요" in r["list"][0] and "대본에 없는 말" in r["list"][0], r["list"]
    assert "어디 가는 거야" in r["list"][1] and "대본 줄" in r["list"][1] and "대본" not in r["list"][2], r["list"]
    e = r["toExtra"]
    assert not e["modal"] and e["view"] == "extras" and e["sel"] == "extras-row-X0001" and _clock(e["clock"]) == pytest.approx(6.5, abs=0.06), e
    ln = r["toLine"]
    assert not ln["modal"] and ln["view"] == "lines" and ln["sel"] == "lines-row-L0002" and _clock(ln["clock"]) == pytest.approx(2.4, abs=0.06), ln
    t = r["toTime"]
    assert not t["modal"] and t["view"] == "lines" and t["sel"] == "lines-row-L0002" and _clock(t["clock"]) == pytest.approx(4.1, abs=0.06), t
    u = r["unsettled"]
    assert not u["modal"] and u["view"] == "lines" and u["sel"] == "lines-row-L0003", u


@browser_only
def test_sung_speech_and_the_name_and_language_hints_are_shown_and_filtered(tmp_path, browser, clips):
    # 대본에 없는 말 in a 노래 구간 (extras[].sung): a 노래 tag, its form puts it in as sung; a line's
    # hints name · language on its row, on its panel and as two chips of the 대사 filter (old page:
    # neither tag nor hint nor chip)
    work = page_folder(tmp_path, clips["mp4"], t0=0.0, t1=12.0)
    over = {"extras": {"X0001": {"sung": True}},
            "lines": {"L0001": {"hints": ["name"]}, "L0002": {"hints": ["language"], "heard_language": "영어"}}}
    with Served(create_app(work, media=Media("source", clips["mp4"]))) as srv:
        r = run_page(browser, srv.url, r"""export default async (p) => {
          await p.reload(@HOOK@);
          const sub = (id) => p.ev(`(document.querySelector('#${id} .r-sub') || {}).textContent || ''`);
          const rows = () => p.ev(`[...document.querySelectorAll('#rows .row[data-id]')].map((r) => r.dataset.id)`);
          await p.ev(`document.querySelector('#tabs [data-view="extras"]').click()`);
          await p.sleep(200);
          const tags = await p.ev(`[...document.querySelectorAll('#rows .row[data-id]')].map((r) => [r.dataset.id, [...r.querySelectorAll('.t-sung')].map((t) => t.textContent)])`);
          await p.ev(`document.getElementById('extras-row-X0003').click()`);
          await p.sleep(200);
          const plain = await p.ev(`document.querySelector('#evidence [data-box="x-line"] [data-f="sung"]').checked`);
          await p.ev(`document.getElementById('extras-row-X0001').click()`);
          await p.sleep(200);
          const sung = { box: await p.ev(`document.querySelector('#evidence [data-box="x-line"] [data-f="sung"]').checked`),
                         note: await p.ev(`document.querySelector('#evidence .ev-note').textContent`) };
          await p.ev(`document.querySelector('#evidence [data-act="x-line"]').click()`);
          await p.idle();
          await p.ev(`document.querySelector('#tabs [data-view="lines"]').click()`);
          await p.sleep(200);
          const subs = { l1: await sub('lines-row-L0001'), l2: await sub('lines-row-L0002'), l3: await sub('lines-row-L0003') };
          const chips = await p.ev(`Object.fromEntries([...document.querySelectorAll('#state-chips .chip')].map((c) => [c.dataset.filter, [c.firstChild.textContent, Number(c.querySelector('.n').textContent)]]))`);
          await p.ev(`document.querySelector('#state-chips [data-filter="name"]').click()`);
          await p.sleep(150);
          const byName = await rows();
          await p.ev(`document.querySelector('#state-chips [data-filter="language"]').click()`);
          await p.sleep(150);
          const byLang = await rows();
          await p.ev(`document.getElementById('lines-row-L0002').click()`);
          await p.sleep(200);
          const panel2 = await p.ev(`document.querySelector('#evidence .ev-note').textContent`);
          await p.ev(`document.querySelector('#state-chips [data-filter="name"]').click()`);
          await p.sleep(150);
          await p.ev(`document.getElementById('lines-row-L0001').click()`);
          await p.sleep(200);
          const panel1 = await p.ev(`document.querySelector('#evidence .ev-note').textContent`);
          return { tags, plain, sung, subs, chips, byName, byLang, panel1, panel2,
                   put: await p.ev(`window.__seen.filter(([u]) => u === 'api/extras/X0001').map(([, b]) => b)`) };
        }""".replace("@HOOK@", new_hook(over)))["result"]
    tags = dict(r["tags"])
    assert tags["X0001"] == ["노래"] and all(v == [] for k, v in tags.items() if k != "X0001"), r["tags"]
    assert r["plain"] is False and r["sung"]["box"] is True and "노래 구간 안의 말" in r["sung"]["note"], r
    assert len(r["put"]) == 1 and r["put"][0]["action"] == "line" and r["put"][0]["sung"] is True, r["put"]
    s = r["subs"]
    assert "이름만 다르게 들림" in s["l1"] and "들린 언어: 영어 · 언어 표시 확인" in s["l2"], s
    assert "이름만" not in s["l3"] and "들린 언어" not in s["l3"], s
    c = r["chips"]
    assert c["name"] == ["이름만 다르게 들림", 1] and c["language"] == ["들린 언어 다름", 1], c
    keys = list(c)
    assert keys.index("spelling") < keys.index("name") < keys.index("language") < keys.index("lang"), keys
    assert r["byName"] == ["L0001"] and r["byLang"] == ["L0002"], r
    assert "이름만 다르게 들림" in r["panel1"] and "들린 언어: 영어 · 언어 표시 확인" in r["panel2"], r
