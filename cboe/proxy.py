# -*- coding: utf-8 -*-
"""재생용 영상 (``<work>/review.mp4``): a copy of the processing window a browser can play.

The review page opens the video with ``<video>`` (``GET /media/video``). Browsers cannot play
many real film files (mkv; MPEG-4 Part 2, HEVC or ProRes video; AC3 or DTS sound): the page then
shows only still frames and the writer cannot hear the sound, so the sound effect and music
candidates cannot be decided. For such a file this module makes ``review.mp4``: only [t0, t1) of
the source, H.264 + AAC stereo, at most 960 wide (even height), the source frame rate, a key
frame about every second (fast seeking), ``+faststart``. Its 0 s is the film's t0.

* ``probe(path)`` — what ``util.ffprobe`` gives plus the container and the codecs.
* ``playable(info)`` — can a browser play the source as it is: mp4 · mov · m4v or webm with
  h264 · vp8 · vp9 · av1 video and aac · mp3 · opus · vorbis · flac sound (or none). mkv is
  never taken as playable (browsers differ even when the codecs fit).
* ``encoders()`` — the H.264 encoders that really work on this PC, GPU first: h264_qsv →
  h264_nvenc → h264_amf, each checked once per process with a short test encode; libx264 comes
  last and is used only when no GPU encoder works (or one fails on this file).
* ``command(...)`` — the ffmpeg arguments, apart so they can be tested. ``-ss t0 -t (t1-t0)``
  stand before the input, so nothing outside the window is read or written (the sealed data
  rule: ``build`` calls ``guard.check_window`` first, as ``pipeline.run`` does). Only the first
  video stream and the first sound stream are used; the frame rate is not changed.
* ``build(work, meta, ...)`` — runs it (``-progress`` → ``on_progress(0..1)``) into
  ``review.mp4.part``, renames it when done and writes ``review.mp4.json`` = ``{"video", "size",
  "mtime", "t0", "t1", "encoder", "width", "height"}``.
* ``fresh(work, meta)`` — is ``review.mp4`` still the copy of this source and this window.
* ``Worker`` — one background thread per work folder and the state the server shows
  (``status()``: none | source | building | ready | failed); ``close()`` ends ffmpeg.
"""
from __future__ import annotations

import atexit
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

from . import guard
from .util import NO_WINDOW, log, read_json, write_json

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"
OUT_NAME = "review.mp4"
MAX_WIDTH = 960
GPU_ENCODERS = ("h264_qsv", "h264_nvenc", "h264_amf")
CPU_ENCODER = "libx264"
STATES = ("none", "source", "building", "ready", "failed")

# what Chrome / Edge / Firefox / Safari play as it is
MP4_EXTS = frozenset({".mp4", ".m4v", ".mov"})
WEBM_EXTS = frozenset({".webm"})
VIDEO_CODECS = frozenset({"h264", "vp8", "vp9", "av1"})
AUDIO_CODECS = frozenset({"aac", "mp3", "opus", "vorbis", "flac"})

VIDEO_BITRATE, VIDEO_MAXRATE, VIDEO_BUFSIZE = "1500k", "3M", "4M"
AUDIO_BITRATE = "160k"


class BuildError(RuntimeError):
    """The playback copy could not be made (the message is one Korean line for the page)."""


class Cancelled(RuntimeError):
    """``build`` was stopped (``cancel`` set: the server is closing)."""


def _have(tool: str) -> bool:
    return shutil.which(tool) is not None


def _rate(text) -> float:
    try:
        num, _, den = str(text or "0/1").partition("/")
        num, den = float(num), float(den or 1)
        return num / den if den else 0.0
    except ValueError:
        return 0.0


# ------------------------------------------------------------------ probing
def probe(path, timeout: float = 60.0) -> dict:
    """ffprobe of a media file: the keys of ``util.ffprobe`` (duration, fps, width, height,
    has_audio) and ``format`` (ffprobe format_name, e.g. ``matroska,webm``), ``ext`` (lower-case
    suffix), ``vcodec`` / ``acodec`` (codec_name or None), ``vindex`` / ``aindex`` (stream index
    of the first video stream that is not a cover picture and of the first sound stream) and
    ``sar`` (sample aspect ratio, 1.0 when not given). Raises when ffprobe fails."""
    out = subprocess.run([FFPROBE, "-v", "error", "-print_format", "json", "-show_streams", "-show_format",
                          str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace",
                         check=True, timeout=timeout, creationflags=NO_WINDOW).stdout
    data = json.loads(out)
    streams = data.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"
              and not (s.get("disposition") or {}).get("attached_pic")), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fps = sar = 0.0
    if v:
        fps = _rate(v.get("avg_frame_rate")) or _rate(v.get("r_frame_rate"))
        sar = _rate(str(v.get("sample_aspect_ratio") or "1:1").replace(":", "/"))
    fmt = data.get("format") or {}
    return {"duration": float(fmt.get("duration") or 0.0), "fps": fps,
            "width": int(v.get("width") or 0) if v else 0, "height": int(v.get("height") or 0) if v else 0,
            "has_audio": a is not None, "format": str(fmt.get("format_name") or ""),
            "ext": Path(str(path)).suffix.lower(),
            "vcodec": v.get("codec_name") if v else None, "acodec": a.get("codec_name") if a else None,
            "vindex": v.get("index") if v else None, "aindex": a.get("index") if a else None,
            "sar": sar if sar > 0 else 1.0}


def playable(info: dict) -> bool:
    """True when a browser plays the file as it is (``info`` as ``probe`` gives it)."""
    fmt = set(str(info.get("format") or "").split(","))
    ext = str(info.get("ext") or "").lower()
    if ext in MP4_EXTS:
        container = bool(fmt & {"mov", "mp4"})
    elif ext in WEBM_EXTS:
        container = "webm" in fmt
    else:                                   # mkv, avi, ts ... (mkv: browsers differ even with fitting codecs)
        container = False
    sound = info.get("acodec")
    return container and info.get("vcodec") in VIDEO_CODECS and (sound is None or sound in AUDIO_CODECS)


# ------------------------------------------------------------------ encoding
def out_size(width: int, height: int, sar: float = 1.0, max_width: int = MAX_WIDTH) -> tuple[int, int]:
    """Frame size of the copy: the shown width (``width * sar``) cut to ``max_width``, both sides even."""
    if width <= 0 or height <= 0:
        raise ValueError(f"영상 크기를 모릅니다 ({width}x{height})")
    shown = width * (sar if sar and sar > 0 else 1.0)
    w = max(2, int(min(float(max_width), shown)) // 2 * 2)
    h = max(2, int(round(height * w / shown / 2.0)) * 2)
    return w, h


def video_args(encoder: str, fps: float) -> list[str]:
    """Encoder arguments: a key frame about every second (``-g`` = frames per second)."""
    gop = str(max(1, round(fps or 25.0)))
    rate = ["-b:v", VIDEO_BITRATE, "-maxrate", VIDEO_MAXRATE, "-bufsize", VIDEO_BUFSIZE]
    if encoder == "h264_qsv":
        return ["-c:v", encoder, "-preset", "veryfast", *rate, "-g", gop]
    if encoder == "h264_nvenc":
        return ["-c:v", encoder, "-preset", "p4", *rate, "-g", gop]
    if encoder == "h264_amf":
        return ["-c:v", encoder, "-quality", "speed", *rate, "-g", gop]
    if encoder == CPU_ENCODER:
        return ["-c:v", encoder, "-preset", "veryfast", "-crf", "23", "-maxrate", VIDEO_MAXRATE,
                "-bufsize", VIDEO_BUFSIZE, "-g", gop]
    raise ValueError(f"모르는 인코더입니다: {encoder}")


def _pix_fmt(encoder: str) -> str:
    return "yuv420p" if encoder == CPU_ENCODER else "nv12"      # 8-bit 4:2:0 in what each encoder takes


def command(video, out, t0: float, t1: float, encoder: str, *, width: int, height: int, fps: float,
            sar: float = 1.0, vindex: int | None = None, aindex: int | None = None, has_audio: bool = True,
            ffmpeg: str = FFMPEG) -> list[str]:
    """ffmpeg arguments that turn [t0, t1) of ``video`` into an H.264 + AAC mp4 at ``out``.

    ``-ss`` / ``-t`` are input options, so ffmpeg reads only the window and the copy's 0 s is
    the film's t0. No frame rate is given (the source rate is kept). ``-progress pipe:1``
    writes the progress to stdout (``build`` reads it)."""
    t0, t1 = float(t0), float(t1)
    if not (0.0 <= t0 < t1):
        raise ValueError(f"처리 구간이 비어 있습니다 [{t0}, {t1})")
    w, h = out_size(width, height, sar)
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-v", "error",
           "-ss", f"{t0:.6f}", "-t", f"{t1 - t0:.6f}", "-i", str(video),
           "-map", f"0:{vindex}" if vindex is not None else "0:v:0"]
    if has_audio:
        cmd += ["-map", f"0:{aindex}" if aindex is not None else "0:a:0?"]
    cmd += ["-map_metadata", "-1", "-map_chapters", "-1",
            "-vf", f"scale={w}:{h},setsar=1,format={_pix_fmt(encoder)}", *video_args(encoder, fps)]
    cmd += ["-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ac", "2"] if has_audio else ["-an"]
    cmd += ["-movflags", "+faststart", "-max_muxing_queue_size", "1024",
            "-progress", "pipe:1", "-nostats", "-f", "mp4", str(out)]
    return cmd


_enc_lock = threading.Lock()
_enc_found: list[str] | None = None


def _encoder_works(encoder: str, ffmpeg: str = FFMPEG) -> bool:
    """A 0.5 s test encode of a grey picture with the same encoder arguments as a real build."""
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=320x240:r=24:d=0.5",
           "-vf", f"format={_pix_fmt(encoder)}", *video_args(encoder, 24.0), "-f", "null", "-"]
    try:
        return subprocess.run(cmd, capture_output=True, timeout=60, creationflags=NO_WINDOW).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def encoders(refresh: bool = False) -> list[str]:
    """The H.264 encoders that work here, in the order ``build`` tries them: the GPU ones that
    pass a test encode (h264_qsv, h264_nvenc, h264_amf), then libx264 if it works. Measured
    once per process (``refresh`` measures again)."""
    global _enc_found
    with _enc_lock:
        if _enc_found is None or refresh:
            if not _have(FFMPEG):
                return []
            t = time.time()
            found = [e for e in GPU_ENCODERS if _encoder_works(e)]
            if _encoder_works(CPU_ENCODER):
                found.append(CPU_ENCODER)
            _enc_found = found
            log.info("재생용 영상: 되는 H.264 인코더 %s (%.1f초 걸려 확인)", found or "없음", time.time() - t)
        return list(_enc_found)


# ------------------------------------------------------------------ files
def paths(work) -> tuple[Path, Path, Path]:
    """``review.mp4``, ``review.mp4.json`` and ``review.mp4.part`` of a work folder."""
    work = Path(work)
    return work / OUT_NAME, work / f"{OUT_NAME}.json", work / f"{OUT_NAME}.part"


def window(meta: dict) -> tuple[float, float | None]:
    """[t0, t1) of the project (t1 is the duration when not given, None when neither is)."""
    t0 = float(meta.get("t0") or 0.0)
    t1 = meta.get("t1")
    if t1 is None:
        t1 = meta.get("duration")
    return t0, (float(t1) if t1 is not None else None)


def source_key(video, t0: float, t1: float) -> dict:
    """What the copy is the copy of: the source path, its size and mtime, and the window."""
    st = Path(video).stat()
    return {"video": str(video), "size": st.st_size, "mtime": st.st_mtime, "t0": float(t0), "t1": float(t1)}


def _same_path(a, b) -> bool:
    try:
        return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))
    except (TypeError, ValueError):
        return False


def _close(a, b, tol: float) -> bool:
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return False


def fresh(work, meta: dict) -> bool:
    """True when ``review.mp4`` exists and its ``review.mp4.json`` names this project's source
    (same path, size and mtime) and window."""
    out, side, _part = paths(work)
    video = meta.get("video")
    try:
        if not video or not out.is_file() or out.stat().st_size == 0:
            return False
        t0, t1 = window(meta)
        if t1 is None:
            return False
        want = source_key(video, t0, t1)
        old = read_json(side)
    except (OSError, ValueError):
        return False
    if not isinstance(old, dict):
        return False
    return (_same_path(old.get("video"), want["video"]) and old.get("size") == want["size"]
            and _close(old.get("mtime"), want["mtime"], 1e-3)
            and _close(old.get("t0"), want["t0"], 1e-6) and _close(old.get("t1"), want["t1"], 1e-6))


# ------------------------------------------------------------------ building
_live: set = set()                  # running ffmpeg processes, ended when Python exits
_live_lock = threading.Lock()


@atexit.register
def _end_live() -> None:
    with _live_lock:
        procs = list(_live)
    for p in procs:
        try:
            p.kill()
        except OSError:
            pass


def _run_ffmpeg(cmd: list[str], span: float, on_progress, cancel, on_process) -> tuple[int, str]:
    """Run one ffmpeg, report ``out_time / span`` and return (return code, last error line)."""
    with tempfile.TemporaryFile() as errf:          # stderr off a pipe: a flood of decode errors cannot block ffmpeg
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errf,
                                creationflags=NO_WINDOW)
        with _live_lock:
            _live.add(proc)
        if on_process is not None:
            on_process(proc)
        try:
            for raw in proc.stdout:
                if cancel is not None and cancel.is_set():
                    proc.kill()
                    break
                key, _, val = raw.decode("ascii", "replace").strip().partition("=")
                if key in ("out_time_us", "out_time_ms") and on_progress is not None:
                    try:
                        done = int(val) / 1e6                 # both are microseconds
                    except ValueError:
                        continue                              # N/A before the first frame
                    on_progress(min(1.0, max(0.0, done / span)) if span > 0 else 0.0)
            proc.wait()
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            with _live_lock:
                _live.discard(proc)
            if on_process is not None:
                on_process(None)
        if cancel is not None and cancel.is_set():
            raise Cancelled("재생용 영상 만들기를 멈췄습니다")
        errf.seek(0)
        err = errf.read()[-4000:].decode("utf-8", "replace").strip().splitlines()
    return proc.returncode, (err[-1].strip() if err else f"ffmpeg 종료 코드 {proc.returncode}")


def _replace(src: Path, dst: Path) -> None:
    """``os.replace`` that waits a little while a reader still holds the old copy (Windows)."""
    for attempt in range(100):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == 99:
                raise BuildError(f"{dst.name} 를 바꾸지 못했습니다 · 다른 곳에서 열려 있습니다") from None
            time.sleep(0.1)


def build(work, meta: dict, *, info: dict | None = None, encoder_list: list[str] | None = None,
          on_progress: Callable[[float], None] | None = None, cancel: threading.Event | None = None,
          on_process: Callable[[subprocess.Popen | None], None] | None = None) -> dict:
    """Make ``<work>/review.mp4`` from [t0, t1) of ``meta["video"]`` (``meta`` = project.json).

    The window is checked with ``guard.check_window`` first (as ``pipeline.run`` does). The
    encoders are tried in the order of ``encoder_list`` (default ``encoders()``); the next one
    only when one fails on this file. Returns what it writes to ``review.mp4.json``. Raises
    ``ValueError`` (sealed part), ``BuildError`` (cannot be made) or ``Cancelled``."""
    work = Path(work)
    video = str(meta.get("video") or "")
    t0, t1 = window(meta)
    if t1 is None or not t0 < t1:
        raise BuildError(f"처리 구간이 비어 있습니다 [{t0}, {t1})")
    if info is None:
        info = probe(video)
    guard.check_window(video, t0, t1, meta.get("duration") if meta.get("duration") is not None else info.get("duration"))
    if not info.get("width") or (info.get("vcodec") is None):
        raise BuildError("영상 스트림이 없습니다")
    encs = list(encoder_list) if encoder_list is not None else encoders()
    if not encs:
        raise BuildError("쓸 수 있는 H.264 인코더가 없습니다 (h264_qsv · h264_nvenc · h264_amf · libx264 모두 안 됨)")
    out, side, part = paths(work)
    key = source_key(video, t0, t1)                 # what is read, taken before reading it
    side.unlink(missing_ok=True)                    # an old copy is not this one any more
    w, h = out_size(info["width"], info["height"], info.get("sar") or 1.0)
    errors = []
    for n, enc in enumerate(encs):
        if cancel is not None and cancel.is_set():
            raise Cancelled("재생용 영상 만들기를 멈췄습니다")
        cmd = command(video, part, t0, t1, enc, width=info["width"], height=info["height"], fps=info.get("fps") or 0.0,
                      sar=info.get("sar") or 1.0, vindex=info.get("vindex"), aindex=info.get("aindex"),
                      has_audio=bool(info.get("has_audio")))
        log.info("재생용 영상: %s 로 만듭니다 · %s %.3f~%.3f초 → %dx%d", enc, Path(video).name, t0, t1, w, h)
        started = time.time()
        try:
            rc, err = _run_ffmpeg(cmd, t1 - t0, on_progress, cancel, on_process)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        if rc == 0 and part.is_file() and part.stat().st_size > 0:
            _replace(part, out)
            rec = {**key, "encoder": enc, "width": w, "height": h}
            write_json(side, rec)
            log.info("재생용 영상: 다 만들었습니다 · %s · %.1f초 걸림 · %.1f MB", enc, time.time() - started,
                     out.stat().st_size / 1e6)
            return rec
        part.unlink(missing_ok=True)
        errors.append(f"{enc}: {err}")
        log.warning("재생용 영상: %s 로 만들지 못했습니다 (%s)%s", enc, err,
                    f" · 다음은 {encs[n + 1]}" if n + 1 < len(encs) else "")
    raise BuildError("재생용 영상을 만들지 못했습니다 · " + " / ".join(errors))


# ------------------------------------------------------------------ worker
def _reason(exc: BaseException) -> str:
    """The last line ffprobe wrote (or the exception) as one line."""
    text = getattr(exc, "stderr", None)
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    lines = str(text or "").strip().splitlines() or [f"{type(exc).__name__}: {exc}"]
    return lines[-1].strip()


def _key_of(meta: dict) -> tuple:
    """(video, t0, t1, size, mtime): a change of any of them means deciding again."""
    t0, t1 = window(meta)
    video = str(meta.get("video") or "")
    try:
        st = os.stat(video) if video else None
    except OSError:
        st = None
    return video, t0, t1, (st.st_size if st else None), (st.st_mtime_ns if st else None)


class Worker:
    """The playback video of one work folder: what ``/media/video`` gives and the background
    thread (at most one at a time) that makes ``review.mp4`` when the browser cannot play the
    source. ``status()`` is the ``project.media`` object of the server:

        {"state": "none" | "source" | "building" | "ready" | "failed",
         "t0": seconds (the film time of the video's 0 s: 0 for source and none, the window
               start for ready, building and failed),
         "progress": 0..1 while building (None before the first progress line) else None,
         "note": one Korean line or None}

    ``start()`` decides in the background (an up-to-date ``review.mp4`` → ready; a source the
    browser plays → source; otherwise building → ready | failed). When project.json names
    another video or window, or the source file changes, the next ``status()`` decides again. ``request_build()`` makes the
    copy even of a source taken as playable (the page could not open it). Errors never leave
    this class: they become ``failed`` or ``source`` with the reason in ``note``."""

    def __init__(self, work, meta: Callable[[], dict] | None = None, wait: float = 5.0):
        self.work = Path(work)
        self._meta = meta or (lambda: read_json(self.work / "project.json", {}) or {})
        self.wait = wait                            # how long status() waits for the first decision
        self._lock = threading.Lock()
        self._state = {"state": "none", "t0": 0.0, "progress": None, "note": None}
        self._file: Path | None = None
        self._key = None
        self._pending_t0 = 0.0
        self._thread: threading.Thread | None = None      # not None while a pass runs
        self._force = False
        self._decided = threading.Event()
        self._cancel = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._closed = False
        self._last_tenth = -1

    # ------------------------------------------------------------ public
    def start(self) -> "Worker":
        with self._lock:
            if self._thread is None and not self._decided.is_set():
                self._launch()
        return self

    def status(self) -> dict:
        self._follow_project()
        self._decided.wait(self.wait)
        with self._lock:
            if not self._decided.is_set():
                return {"state": "building", "t0": self._pending_t0, "progress": None, "note": "영상 정보를 읽는 중입니다"}
            return dict(self._state)

    def file(self) -> Path | None:
        """The file ``/media/video`` gives: review.mp4 when ready, the source when source."""
        with self._lock:
            return self._file if self._decided.is_set() and self._state["state"] in ("ready", "source") else None

    def request_build(self) -> dict:
        """Make the copy unless it is there, being made, or there is no video (POST /api/media/build)."""
        with self._lock:
            if not self._closed:
                if self._thread is not None:        # a pass runs: it runs again when it ends at source
                    self._force = True
                    if self._decided.is_set() and self._state["state"] == "source":
                        self._decided.clear()       # answer with what the next pass decides
                elif self._state["state"] in ("source", "failed"):
                    self._force = True
                    self._launch()
        return self.status()

    def close(self, timeout: float = 5.0) -> None:
        """Stop: end a running ffmpeg and wait a little for the thread."""
        with self._lock:
            self._closed = True
            self._cancel.set()
            proc, th = self._proc, self._thread
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
        if th is not None and th is not threading.current_thread():
            th.join(timeout)

    # ------------------------------------------------------------ inside
    def _launch(self) -> None:                      # under self._lock
        if self._closed:
            return
        self._decided.clear()
        self._thread = threading.Thread(target=self._loop, name=f"cboe-proxy-{self.work.name}", daemon=True)
        self._thread.start()

    def _follow_project(self) -> None:
        try:
            key = _key_of(self._meta())
        except Exception:                           # unreadable project.json: keep what was decided
            return
        with self._lock:
            if self._thread is None and self._decided.is_set() and self._key is not None and key != self._key:
                log.info("재생용 영상: 영상 또는 처리 구간이 바뀌어 다시 정합니다")
                self._force = False
                self._launch()

    def _set(self, state: str, t0: float = 0.0, progress=None, note: str | None = None,
             file: Path | None = None) -> None:
        with self._lock:
            self._state = {"state": state, "t0": float(t0), "progress": progress, "note": note}
            self._file = file
            self._decided.set()

    def _progress(self, frac: float) -> None:
        with self._lock:
            if self._state["state"] == "building":
                self._state["progress"] = round(frac, 3)
        tenth = int(frac * 10)
        if tenth != self._last_tenth:
            self._last_tenth = tenth
            log.info("재생용 영상: %d%%", tenth * 10)

    def _on_process(self, proc) -> None:
        with self._lock:
            self._proc = proc
            closed = self._closed
        if closed and proc is not None:
            try:
                proc.kill()
            except OSError:
                pass

    def _loop(self) -> None:
        try:
            while True:
                try:
                    self._pass()
                except Exception as exc:            # never take the server down
                    log.exception("재생용 영상 일꾼 오류")
                    self._set("failed", self._pending_t0, note=f"재생용 영상을 만들지 못했습니다 · {type(exc).__name__}: {exc}")
                with self._lock:
                    again = self._force and self._state["state"] == "source" and not self._closed
                    if not again:
                        self._thread = None
                        return
        finally:
            with self._lock:
                self._thread = None
                self._decided.set()

    def _pass(self) -> None:
        meta = self._meta()
        key = _key_of(meta)
        video, t0, _t1 = key[:3]
        with self._lock:
            self._key = key
            self._pending_t0 = t0
            force, self._force = self._force, False
        if not video or not Path(video).is_file():
            return self._set("none", note="영상 파일이 없습니다" if video else None)
        src = Path(video)
        if fresh(self.work, meta):
            return self._set("ready", t0, file=paths(self.work)[0])
        if not _have(FFPROBE):
            return self._set("source", note="ffprobe 가 없어 원본을 그대로 줍니다", file=src)
        try:
            info = probe(src)
        except Exception as exc:
            return self._set("source", note=f"영상 정보를 읽지 못해 원본을 그대로 줍니다 · {_reason(exc)}", file=src)
        if playable(info) and not force:
            return self._set("source", file=src)
        if not _have(FFMPEG):
            return self._set("source", note="ffmpeg 가 없어 재생용 영상을 만들 수 없습니다 · 원본을 그대로 줍니다", file=src)
        if info.get("vcodec") is None:
            return self._set("source", note="영상 스트림이 없어 원본을 그대로 줍니다", file=src)
        self._last_tenth = -1
        self._set("building", t0, progress=0.0)
        try:
            build(self.work, meta, info=info, on_progress=self._progress, cancel=self._cancel,
                  on_process=self._on_process)
        except Cancelled as exc:
            return self._set("failed", t0, note=str(exc))
        except (ValueError, BuildError) as exc:     # ValueError: guard (sealed part), bad size or window
            log.warning("재생용 영상: %s", exc)
            return self._set("failed", t0, note=str(exc))
        self._set("ready", t0, file=paths(self.work)[0])
