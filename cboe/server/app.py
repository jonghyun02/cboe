# -*- coding: utf-8 -*-
"""Review screen server (검수 화면, deck 3.1~3.5 and 5.10).

The page is a static single-page app (``static/``). Every change the writer makes
is stored through ``Project.set_decision`` / ``Project.set_placement``; the server
then re-runs placement (끼워 넣기) and answers with the whole screen state, so the
list, timeline and video overlay are always drawn from one fresh draft —
"고칠 때마다 배치를 다시 계산".

The state also carries ``style`` (the limits and the notation of ``cboe.style`` for this work
folder's profile: the page keeps no copy of them) and, in ``export_check.guide`` and
``summary.guide``, the result of the independent guide check (``cboe.check``) on the draft.
Sound phrases stored in an older notation ('♬ …') are shown in today's ('[…]').

검수 층 (``cboe.state`` module doc): the dialogue lines are review items too. The state carries
``lines`` (the merged lines in film order, the ones that are out with the reason: ``line_row``),
``extras`` ('대본에 없는 말' with ``state`` open | line | ignored), ``scenes`` (scenes.json),
``people`` (the people, when their name is told, the writer's decision, ``early``), ``added``
(what the writer added) and the counts in ``summary.lines`` / ``summary.extras`` /
``summary.people``. Routes: ``/api/lines/{id}`` (in / out, text, name, language, sung, start /
end, breaks, keep, source, join: one cell with the line before, pos: "top", manner: the manner of
speech in the label), ``/api/lines/bulk``, ``/api/lines/{id}/speaker`` (a name by scope),
``/api/lines/aside`` (a decision kept aside since the script changed — state ``aside``, kind "line"
or "speaker": put it on a line, or on the speaker item of a line, or let it go),
``/api/extras/{id}`` (line | ignore | clear), ``/api/added`` (+ ``/{id}`` to change, DELETE to
remove; ``pos``), ``/api/people`` (``not_same``: not the person a ``maybe_same`` offers), and
``/api/items/{id}`` ``pos`` for a sound's cell. Unknown ids are 404, values that cannot be used 400,
clashes 409. A line row also has ``delivery`` (the manner of speech the script writes, always a
list) and ``joined``; ``export_check.heard_no_cell`` (``summary.heard_no_cell``) lists the speech
heard where no dialogue cell is.
``/api/envelope`` gives the timeline the level of the separated tracks (env.npz, cut down).

Video and frames are limited to the project window [t0, t1): the page never seeks
outside it and ``/api/frame`` clamps into it, so sealed parts of a test video are
not shown (same rule as ``media.Media``).

재생용 영상 (``cboe.proxy``): when the browser cannot play the source (mkv, MPEG-4 Part 2, HEVC,
AC3 ...), a background worker started with the app makes ``review.mp4`` of the window only
(H.264 + AAC, GPU encoder first) and ``/media/video`` gives that file; its 0 s is the film's t0.
``project.media`` in the state and ``GET /api/media`` say which: ``{"state": none | source |
building | ready | failed, "t0", "progress", "note"}`` (film time = video time + ``t0``).
``POST /api/media/build`` makes the copy when the page could not open the source. While it is
being made ``/media/video`` is 409. Stills (``/api/frame``) are still read from the source.
"""
from __future__ import annotations

import json
import math
from html import escape
from urllib.parse import quote
import mimetypes
import subprocess
import threading
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, StrictInt

from .. import export as export_mod
from .. import proxy, style
from ..state import EXCLUDED_KO, LINE_STATUSES, ORIGIN_KO, Conflict, Project, ReviewError, label_extra
from ..textutil import units
from ..util import log, read_json

STATIC = Path(__file__).resolve().with_name("static")
FINALS = ("include", "exclude", "check")
KINDS = ("sfx", "music", "speaker")
ExportFormat = Literal["srt", "vtt", "ttml"]      # export_mod.FORMATS
INPUT_FILES = ("project.json", "items.json", "dialogue.json", "review.json", "cuts.json", "script.json",
               "scenes.json", "asr.json")
Scope = Literal["line", "from_here", "before", "scene", "person", "speaker", "voice"]   # cboe.people.SCOPES


# --------------------------------------------------------------------- bodies
class ItemUpdate(BaseModel):
    decision: Literal["include", "exclude"] | None = None
    phrase: str | None = None
    short: str | None = None
    name: str | None = None
    scope: Scope | None = None
    pos: str | None = None                 # a sound's cell: "top", null = the bottom (state.POSITIONS)


class LineUpdate(BaseModel):
    """The writer's decision on one dialogue line; a field sent as null goes back to the default."""
    include: bool | None = None
    text: str | None = None
    text_from: Literal["script", "heard", "writer"] | None = None
    name: str | None = None
    language: str | None = None
    sung: bool | None = None
    start: float | None = None
    end: float | None = None
    breaks: list[StrictInt] | None = None
    keep: bool | None = None
    show_name: bool | None = None          # only for a line without a speaker item
    source: str | None = None              # state.SOURCES (inner, phone …) or "" for none; null: the script's
    join: bool | None = None               # true: one cell with the line before (false = null: not joined)
    pos: str | None = None                 # "top"; null: the bottom
    manner: str | None = None              # the manner of speech in the label ([하늘/속삭이며]); null: none


class LinesBulk(BaseModel):
    ids: list[str]
    include: bool | None = None


class AsideAction(BaseModel):
    key: str                               # state ``aside`` key ('fp:…')
    line: str | None = None                # the line to put it on; null: let it go


class SpeakerUpdate(BaseModel):
    name: str | None = None
    scope: Scope = "line"


class ExtraAction(BaseModel):
    action: Literal["line", "ignore", "clear"]
    text: str | None = None
    name: str | None = None
    language: str | None = None
    sung: bool | None = None
    start: float | None = None
    end: float | None = None


class AddedNew(BaseModel):
    kind: Literal["line", "sfx", "music"]
    start: float
    end: float
    text: str | None = None
    phrase: str | None = None
    name: str | None = None
    language: str | None = None
    sung: bool | None = None
    show_name: bool | None = None
    breaks: list[StrictInt] | None = None
    keep: bool | None = None
    pos: str | None = None                 # "top"; null: the bottom


class AddedUpdate(BaseModel):
    start: float | None = None
    end: float | None = None
    text: str | None = None
    phrase: str | None = None
    name: str | None = None
    language: str | None = None
    sung: bool | None = None
    show_name: bool | None = None
    breaks: list[StrictInt] | None = None
    keep: bool | None = None
    pos: str | None = None                 # "top"; null: the bottom


class PeopleUpdate(BaseModel):
    """How people are labelled before their name is told (cboe.people): several at once; ``not_same``:
    people who are not these ones (no longer offered as ``maybe_same``; null clears what was said)."""
    names: list[str]
    mode: Literal["name", "generic"] | None = None
    before: str | None = None
    reveal: Literal["auto", "line", "never"] | None = None
    reveal_line: str | None = None
    not_same: list[str] | None = None


class BulkUpdate(BaseModel):
    ids: list[str]
    decision: Literal["include", "exclude"] | None = None


class PlacementFix(BaseModel):
    action: Literal["exclude", "move", "swap_prev", "clear"]
    t: float | None = None


class ExportRequest(BaseModel):
    format: ExportFormat = "srt"


# ---------------------------------------------------------------- utilities
def _finite(x):
    """NaN/inf -> None so the browser's JSON.parse never chokes."""
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, dict):
        return {k: _finite(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_finite(v) for v in x]
    return x


LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}


def _host_of(value: str) -> str:
    v = (value or "").strip().lower()
    if v.startswith("["):                                   # [::1]:8765
        return v.split("]")[0] + "]"
    return v.rsplit(":", 1)[0] if v.count(":") == 1 else v


class LocalOnly:
    """Only this PC's own pages may use the server: the Host header must be localhost (no DNS
    rebinding), and requests that change something are refused when another site sent them."""

    def __init__(self, app, extra_hosts=()):
        self.app = app
        self.hosts = LOCAL_HOSTS | set(extra_hosts)

    def __getattr__(self, name):                            # .home, .state ... of the wrapped app
        return getattr(self.app, name)

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            from urllib.parse import urlsplit
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
            bad = _host_of(headers.get("host", "")) not in self.hosts
            if not bad and scope.get("method", "GET") not in ("GET", "HEAD", "OPTIONS"):
                origin = headers.get("origin")
                if origin is not None:
                    bad = origin == "null" or _host_of(urlsplit(origin).netloc) not in self.hosts
                if headers.get("sec-fetch-site") == "cross-site":
                    bad = True
            if bad:
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                    return
                from fastapi.responses import PlainTextResponse
                return await PlainTextResponse("다른 곳에서 온 요청은 받지 않습니다", 403)(scope, receive, send)
        return await self.app(scope, receive, send)


class SafeJSONResponse(JSONResponse):
    def render(self, content) -> bytes:
        try:
            text = json.dumps(content, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        except ValueError:
            text = json.dumps(_finite(content), ensure_ascii=False, separators=(",", ":"))
        return text.encode("utf-8")


class PlacementError(ValueError):
    """A placement fix that cannot be applied (answered as 409 with a Korean message)."""


def swap_prev_time(draft: dict, item: dict, fps: float, t_min: float = 0.0, st: style.Style | None = None) -> float:
    """Start time for '앞 칸과 바꾸기': the sound goes into the gap right before the
    cell that precedes it, as late as possible so it stays close to the real sound.

    Raises PlacementError when there is no previous cell or the gap is shorter
    than the shortest cell (5/6 s) plus the 2-frame margins. The numbers are those of
    ``st`` (cboe.style; the adult profile when not given).
    """
    st = st or style.get((draft or {}).get("profile"))
    fr2 = st.gap_frames / (fps or 25.0)
    iid = item["id"]
    nr = next((e for e in draft.get("no_room") or [] if e.get("item") == iid), None)
    t = nr.get("t") if nr else None
    t = float(t if t is not None else item.get("onset", item.get("t0", 0.0)))
    cells = [c for c in draft.get("cells") or [] if iid not in (c.get("refs") or [])]
    prev = [c for c in cells if float(c["start"]) < t - 1e-6]
    if not prev:
        raise PlacementError("앞 칸이 없어 바꿀 수 없습니다")
    pc = max(prev, key=lambda c: (float(c["start"]), float(c["end"])))
    gap_end = float(pc["start"])
    gap_start = max([float(c["end"]) for c in cells if c is not pc and float(c["start"]) < gap_end] + [t_min])
    text = (nr or {}).get("text") or item.get("short") or item.get("phrase") or ""
    want = min(st.max_dur, max(st.min_dur, units(text) / st.cps + st.label_extra))   # as placement shows a sound
    if gap_end - gap_start - 2 * fr2 < st.min_dur - 1e-6:
        raise PlacementError(f"앞 칸 앞의 빈틈이 {max(0.0, gap_end - gap_start):.2f}초뿐이라 옮길 자리가 없습니다")
    early, late = gap_start + fr2, gap_end - fr2 - want
    # millisecond grid without crossing either 2-frame margin
    return math.ceil(early * 1000) / 1000 if early >= late else math.floor(late * 1000) / 1000


def export_warnings(chk: dict) -> list[str]:
    out = []
    if chk.get("check"):
        out.append(f"확인 필요 {len(chk['check'])}개는 자막에 넣지 않았습니다")
    if chk.get("check_speaker"):
        out.append(f"화자 확인 필요 {len(chk['check_speaker'])}줄은 이름 없이 대사만 넣었습니다")
    if chk.get("no_room"):
        out.append(f"배치 확인 필요 {len(chk['no_room'])}개는 넣을 자리가 없어 빠졌습니다")
    if chk.get("exclude_recommended"):
        out.append(f"제외 추천 {len(chk['exclude_recommended'])}개를 아직 확정하지 않았습니다 (자막에는 넣지 않음)")
    if chk.get("unnamed"):
        out.append(f"문구가 [소리] 그대로인 효과음 {len(chk['unnamed'])}개")
    if chk.get("violations"):
        out.append(f"가독성 한도를 넘는 칸 {len(chk['violations'])}개")
    flags = chk.get("flags") or {}
    if flags.get("flash"):
        out.append(f"몇 프레임만 보이는 칸 {len(flags['flash'])}개 (읽을 수 없음)")
    if flags.get("overlap"):
        out.append(f"겹친 대사에 눌린 칸 {len(flags['overlap'])}개")
    if flags.get("timing_estimated"):
        out.append(f"대사 시각을 추정한 칸 {len(flags['timing_estimated'])}개 (받아쓰기로 못 맞춘 줄)")
    sm = (chk.get("guide") or {}).get("summary") or {}
    if sm.get("must"):
        out.append(f"지침 검사: 의무 조항에 걸린 칸 {sm['cells_must']}개 ({sm['must']}건)")
    if sm.get("should"):
        out.append(f"지침 검사: 권고 조항에 걸린 칸 {sm['cells_should']}개 ({sm['should']}건)")
    ln = chk.get("lines") or {}
    for key, text in (("differs_open", "대본과 들린 말이 다른데 아직 고르지 않은 줄 {}개 (대본 글로 넣었습니다)"),
                      ("unheard", "소리로 못 맞춰 시각을 추정한 줄 {}개"),
                      ("no_room", "시각을 추정한 줄 가운데 자리가 없어 빠진 줄 {}개"),
                      ("absent", "영화에 없는 장면이라 뺀 줄 {}개"),
                      ("out_of_range", "처리 구간 밖이라 뺀 줄 {}개"),
                      ("writer_excluded", "작가가 뺀 줄 {}개"),
                      ("unsettled", "배치를 거듭해도 이름 표시가 맞춰지지 않은 줄 {}개 (초안 칸의 이름 표시를 확인)"),
                      ("aside", "대본이 바뀌어 붙지 못한 작가 결정 {}개 (자막에 쓰지 않음 · 줄에 다시 붙이거나 버림)")):
        if ln.get(key):
            out.append(text.format(len(ln[key])))
    if chk.get("extras_open"):
        sus = len(chk.get("extras_suspect") or [])
        out.append(f"아직 정하지 않은 '대본에 없는 말' {len(chk['extras_open'])}개 (자막에 넣지 않음"
                   + (f" · 그중 크레디트·인사·반복 의심 {sus}개)" if sus else ")"))
    if chk.get("names_early"):
        out.append(f"이름이 밝혀지기 전에 이름 표시가 나가는 인물 {len(chk['names_early'])}명")
    if chk.get("heard_no_cell"):
        out.append(f"말이 들리는데 자막 칸이 없는 구간 {len(chk['heard_no_cell'])}곳")
    return out


ROW_KEYS = ("id", "idx", "seq", "origin", "speaker", "name", "label_name", "text", "text_from", "status", "timing",
            "start", "end", "guess", "scene_id", "language", "source", "note", "hints", "heard_language", "match",
            "voice", "speaker_item", "excluded", "extra", "breaks", "decision", "cells", "no_room", "source_label",
            "pos", "manner")
ROW_FLAGS = ("include", "estimated", "pinned", "decided", "show_name", "name_early", "generic", "sung", "keep",
             "joined")
DIALOGUE_KEYS = ("id", "speaker", "text", "start", "end", "voice", "status", "timing")


def line_row(ln: dict) -> dict:
    """A merged dialogue line as the page gets it (``Project.compose``), kept small for a long film:
    no word times; empty values, false flags (``ROW_FLAGS``) and ``origin`` / ``text_from``
    "script" are left out; the script text, the raw script line and what was heard only where
    they say something (``script_text`` when the shown text differs, ``asr_text`` / ``diff`` for
    differs · unheard lines and for a text taken from what was heard); ``label`` = the label as
    placed when the name is shown; ``delivery`` (the manner of speech the script writes) always,
    [] when there is none; ``joined``: in one cell with the line before it in the draft."""
    row = {k: ln[k] for k in ROW_KEYS if ln.get(k) not in (None, [], {}, "", "script")}
    row.update({k: True for k in ROW_FLAGS if ln.get(k)})
    row["delivery"] = list(ln.get("delivery") or [])
    if ln.get("script_text") and ln["script_text"] != ln.get("text"):
        row["script_text"] = ln["script_text"]
    if ln.get("raw") and ln["raw"] != ln.get("script_text"):
        row["raw"] = ln["raw"]
    if ln.get("status") in ("differs", "unheard") or ln.get("text_from") == "heard":
        row["asr_text"] = ln.get("asr_text") or ""
        if ln.get("diff"):
            row["diff"] = ln["diff"]
    if ln.get("label_name") or ln.get("language"):
        row["label"] = style.line_labels({"name": ln.get("label_name"), "language": ln.get("language"),
                                          "label_extra": _source_extra(ln)}, show_name=True)[0].strip()
    return row


def _source_extra(ln: dict) -> list[str]:
    """The source word and the writer's manner of speech placement shows with the name
    ('[하늘/속마음/속삭이며]'; ``Project.placement_inputs``, ``state.label_extra``)."""
    return label_extra(ln, bool(ln.get("label_name")))


def show_item(it: dict, lines: dict) -> dict:
    """An item as the page shows it: a sound phrase stored by an older version ('♬ 무서운 음악')
    in today's notation, and for a speaker item the label placement writes in front of the line
    (``speaker.label`` '[영희/영어]', ``speaker.label_tail`` '/영어')."""
    if it.get("kind") == "speaker":
        sp = it.get("speaker") or {}
        line = lines.get(sp.get("line_id")) or {}
        # the label placement writes: '[남자1]' before the name is told (cboe.people), else the name
        ln = dict(line, name=line.get("label_name") or sp.get("name"))
        ln["label_extra"] = _source_extra(dict(ln, label_name=ln["name"]))
        head, _cont = style.line_labels(ln, show_name=True)
        bare, _ = style.line_labels(dict(ln, name=None), show_name=True)
        sp["label"] = head.strip()
        sp["label_tail"] = (style.LABEL_SEP + bare.strip()[1:-1]) if bare.strip() else ""
        return it
    for key in ("phrase", "short"):
        if it.get(key):
            it[key] = style.modernize_phrase(it[key])
    if it.get("phrases"):
        it["phrases"] = [dict(p, **{k: style.modernize_phrase(p[k]) for k in ("text", "short") if p.get(k)})
                         for p in it["phrases"] if isinstance(p, dict)]
    return it


class FrameGrabber:
    """JPEG stills for the page (OpenCV seek; ffmpeg when OpenCV cannot open the file)."""

    def __init__(self, video: str):
        self.video = video
        self._lock = threading.Lock()
        self._cap = None            # cv2.VideoCapture | False (cannot open) | None (not tried)
        self._cache: OrderedDict = OrderedDict()

    def _read_cv(self, t: float):
        import cv2
        if self._cap is None:
            cap = cv2.VideoCapture(self.video)
            if not cap.isOpened():
                cap.release()
                self._cap = False
            else:
                self._cap = cap
        if self._cap is False:
            return None
        self._cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
        ok, frame = self._cap.read()
        return frame if ok else None

    def _read_ffmpeg(self, t: float):
        import cv2
        import numpy as np
        cmd = ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{t:.3f}", "-i", self.video, "-frames:v", "1",
               "-an", "-sn", "-f", "image2pipe", "-vcodec", "png", "pipe:"]
        from ..util import NO_WINDOW
        out = subprocess.run(cmd, capture_output=True, timeout=30, creationflags=NO_WINDOW)
        if out.returncode or not out.stdout:
            return None
        return cv2.imdecode(np.frombuffer(out.stdout, np.uint8), cv2.IMREAD_COLOR)

    def jpeg(self, t: float, width: int) -> bytes:
        import cv2
        key = (round(t, 2), int(width))
        with self._lock:
            hit = self._cache.get(key)
            if hit is not None:
                self._cache.move_to_end(key)
                return hit
            frame = self._read_cv(t)
            if frame is None:
                frame = self._read_ffmpeg(t)
            if frame is None:
                raise RuntimeError(f"cannot read a frame at {t:.2f}s")
            h, w = frame.shape[:2]
            if width < w:
                frame = cv2.resize(frame, (int(width), max(1, round(h * width / w))), interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 86])
            if not ok:
                raise RuntimeError("jpeg encode failed")
            data = buf.tobytes()
            self._cache[key] = data
            while len(self._cache) > 120:
                self._cache.popitem(last=False)
            return data

    def close(self) -> None:
        with self._lock:
            if self._cap not in (None, False):
                self._cap.release()
            self._cap = None


# ------------------------------------------------------------------ service
class ReviewService:
    """One work folder behind one lock: decisions, placement, screen state."""

    def __init__(self, work, media=None):
        self.project = Project(work)
        self.lock = threading.RLock()
        self.version = 0
        self.error: str | None = None
        self.exports: dict[str, Path] = {}
        self._draft: dict | None = None
        self._comp: dict | None = None
        self._sig = None
        self._grabber: FrameGrabber | None = None
        # 재생용 영상: what /media/video gives (proxy.Worker, or a stand-in with the same methods)
        self.media = media if media is not None else proxy.Worker(self.project.work, lambda: self.project.meta)

    def close(self) -> None:
        """Server stops: end the playback-video worker (and its ffmpeg) and the frame reader."""
        try:
            self.media.close()
        except Exception:
            log.exception("재생용 영상 일꾼을 끝내지 못했습니다")
        if self._grabber is not None:
            self._grabber.close()

    # ---------------------------------------------------------------- draft
    def _signature(self):
        sig = []
        for name in INPUT_FILES:
            try:
                st = self.project.path(name).stat()
                sig.append((name, st.st_mtime_ns, st.st_size))
            except FileNotFoundError:
                sig.append((name, None, None))
        return tuple(sig)

    def recompute(self) -> dict:
        self._comp = None
        try:
            # the composition the draft was placed from, used once by the state that follows
            self._comp, self._draft = self.project.settle()
            self.error = None
        except Exception as exc:                       # placement is still evolving; keep the page usable
            log.exception("placement failed")
            self.error = f"배치 계산 오류 · {type(exc).__name__}: {exc}"
            if self._draft is None:
                self._draft = read_json(self.project.path("draft.json")) or {}
                if self._draft.get("notation") != style.NOTATION:      # a draft of an older version:
                    for c in self._draft.get("cells") or []:           # show '(이름)' and '♬ …' as '[…]'
                        c["lines"] = [style.modernize_line(x) for x in c.get("lines") or []]
        self._draft.setdefault("cells", [])
        self._draft.setdefault("no_room", [])
        self._sig = self._signature()
        return self._draft

    def reload_draft(self) -> None:
        """After Project.export(), which recomputes and writes draft.json itself."""
        d = read_json(self.project.path("draft.json"))
        self._comp = None
        if d is not None:
            self._draft = d
            self._draft.setdefault("cells", [])
            self._draft.setdefault("no_room", [])
            self.error = None
            self._sig = self._signature()

    def draft(self) -> dict:
        if self._draft is None or self._sig != self._signature():
            return self.recompute()             # first call, or inputs changed outside the page
        return self._draft

    # ---------------------------------------------------------------- items
    def item(self, item_id: str) -> dict:
        for it in self.project.items():
            if it["id"] == item_id:
                return it
        raise HTTPException(404, f"없는 후보입니다: {item_id}")

    def window(self) -> tuple[float, float, float]:
        meta = self.project.meta
        fps = float(meta.get("fps") or 25.0)
        t0 = float(meta.get("t0") or 0.0)
        t1 = meta.get("t1")
        t1 = float(t1) if t1 is not None else float(meta.get("duration") or t0)
        return t0, t1, fps

    def grabber(self, video: str) -> FrameGrabber:
        if self._grabber is None or self._grabber.video != video:
            if self._grabber is not None:
                self._grabber.close()
            self._grabber = FrameGrabber(video)
        return self._grabber

    def export_check(self, comp: dict | None = None, draft: dict | None = None) -> tuple[dict | None, str | None]:
        """``Project.export_check()`` and an error message when it cannot run."""
        try:
            return self.project.export_check(comp=comp, draft=draft), None
        except Exception as exc:
            log.exception("export_check failed")
            return None, f"내보내기 검사 오류 · {type(exc).__name__}: {exc}"

    # ---------------------------------------------------------------- state
    def state(self, recompute: bool = False) -> dict:
        with self.lock:
            if recompute:
                self.version += 1
            draft = self.recompute() if recompute else self.draft()
            p = self.project
            meta = p.meta
            t0, t1, fps = self.window()
            try:
                st, st_error = style.for_project(meta), None
            except ValueError as exc:                     # project.json "profile" is not adult / children
                st, st_error = style.get(), f"project.json · {exc}"
            lines = p.dialogue()
            by_id = {ln.get("id"): ln for ln in lines}
            comp, self._comp = self._comp, None                  # the one placement was made from, if just made
            if comp is None:
                comp = p.compose(draft=draft)
            merged = {ln["id"]: ln for ln in comp["lines"]}
            # a speaker item of a line that is out is out of the list and the counts too
            eff = [show_item(it, merged) for it in comp["items"] if not it.get("line_excluded")]
            cells = draft.get("cells", [])
            no_room = {e.get("item"): e for e in draft.get("no_room", []) if not e.get("line")}

            cells_by_ref: dict[str, list[str]] = {}
            for c in cells:
                for r in c.get("refs") or []:
                    cells_by_ref.setdefault(r, []).append(c["id"])
            for it in eff:
                ids = list(cells_by_ref.get(it["id"], []))
                if it["kind"] == "speaker":
                    lid = (it.get("speaker") or {}).get("line_id")
                    ids += [c for c in cells_by_ref.get(lid, []) if c not in ids]
                it["cells"] = ids
                it["no_room"] = no_room.get(it["id"])
                if it["no_room"] is not None and it["kind"] in ("sfx", "music"):
                    try:                                  # can '앞 칸과 바꾸기' work? the page says why not
                        it["swap_prev"] = {"t": swap_prev_time(draft, it, fps, t0, st)}
                    except PlacementError as exc:
                        it["swap_prev"] = {"error": str(exc)}

            chk, chk_error = self.export_check(comp, draft)
            guide = ((chk or {}).get("guide") or {}).get("summary") or {}
            summary = {"total": len(eff), "decided": sum(1 for it in eff if it["decided"]),
                       "final": {f: 0 for f in FINALS}, "rec": {f: 0 for f in FINALS},
                       "kind": {k: 0 for k in KINDS}, "by_kind": {k: {f: 0 for f in FINALS} for k in KINDS},
                       "no_room": len(no_room), "cells": len(cells),
                       # cells over a limit by placement's own count, then the independent guide check:
                       # cells that break an obligation (must) or a recommendation (should), and its items
                       "violations": sum(1 for c in cells if (c.get("check") or {}).get("violations")),
                       "guide": {"must": guide.get("cells_must", 0), "should": guide.get("cells_should", 0),
                                 "must_items": guide.get("must", 0), "should_items": guide.get("should", 0)},
                       "flags": {name: sum(1 for c in cells if name in (c.get("flags") or []))
                                 for name in ("timing_estimated", "overlap", "flash")}}
            for it in eff:
                k, f = it.get("kind"), it.get("final", "check")
                r = it.get("rec") if it.get("rec") in FINALS else "check"
                summary["final"][f] = summary["final"].get(f, 0) + 1
                summary["rec"][r] += 1
                summary["kind"][k] = summary["kind"].get(k, 0) + 1
                summary["by_kind"].setdefault(k, {x: 0 for x in FINALS})[f] += 1
            # dialogue lines: by alignment status, in / out (and why), the writer's own; extras; people
            script_lines = [ln for ln in comp["lines"] if ln.get("idx") is not None]
            summary["lines"] = {
                "total": len(script_lines), "status": {s: sum(1 for ln in script_lines if ln["status"] == s)
                                                       for s in LINE_STATUSES},
                "included": sum(1 for ln in comp["lines"] if ln["include"]),
                "excluded": {r: sum(1 for ln in comp["lines"] if ln["excluded"] == r) for r in EXCLUDED_KO},
                "added": len(comp["lines"]) - len(script_lines),
                "estimated": sum(1 for ln in comp["lines"] if ln["include"] and ln["estimated"]),
                "text_changed": sum(1 for ln in script_lines if ln["text"] != ln.get("script_text")),
                "no_room": sum(1 for e in draft.get("no_room", []) if e.get("line")),
                "unsettled": len(draft.get("unsettled") or []),
                "aside": len(comp.get("aside") or []),
                "differs_open": len(((chk or {}).get("lines") or {}).get("differs_open") or [])}
            summary["extras"] = {s: sum(1 for x in comp["extras"] if x["state"] == s) for s in ("open", "line", "ignored")}
            summary["extras"]["total"] = len(comp["extras"])
            # suspects among those not decided yet (dialogue.json suspect: credit | greeting | repeat)
            sus = [x["suspect"] for x in comp["extras"] if x["state"] == "open" and x.get("suspect")]
            summary["extras"]["suspect"] = len(sus)
            summary["extras"]["by_suspect"] = {k: sus.count(k) for k in ("credit", "greeting", "repeat")}
            # decided = a way of labelling chosen (the screen counts the same); a reveal line alone is
            # not, nor is a '같은 사람 아님' alone (not_same)
            summary["people"] = {"total": len(comp["people"]),
                                 "early": sum(1 for x in comp["people"] if x.get("early")),
                                 "decided": sum(1 for x in comp["people"] if x.get("mode")),
                                 "reveal_only": sum(1 for x in comp["people"] if not x.get("mode")
                                                    and set(x.get("decision") or ()) - {"at", "not_same"})}
            # speech heard where no dialogue cell is (export check)
            summary["heard_no_cell"] = len((chk or {}).get("heard_no_cell") or [])

            video = meta.get("video")
            project = {"title": meta.get("title") or p.work.name, "fps": fps, "t0": t0, "t1": t1,
                       "duration": meta.get("duration"), "width": meta.get("width"), "height": meta.get("height"),
                       "video": bool(video and Path(video).is_file()), "video_url": "media/video",
                       "video_name": Path(video).name if video else None, "sample": bool(meta.get("sample")),
                       "work": str(p.work), "profile": st.profile,
                       "envelope": p.path("env.npz").is_file(),     # /api/envelope has the level curves
                       "media": self.media.status()}                # 재생용 영상 (proxy.Worker.status)
            added_cells = {r: [c["id"] for c in cells if r in (c.get("refs") or [])]
                           for r in (a["id"] for a in comp["added"])}
            return {"version": self.version, "project": project,
                    # the script lines as aligned (only what the older page reads; the rest is in "lines")
                    "dialogue": [{k: ln.get(k) for k in DIALOGUE_KEYS if k in ln} for ln in lines],
                    # the merged lines in film order (the writer's choices, why a line is out, its cells)
                    "lines": [line_row(ln) for ln in comp["lines"]],
                    "line_codes": {"origin": ORIGIN_KO, "excluded": EXCLUDED_KO, "status": list(LINE_STATUSES)},
                    "extras": comp["extras"], "scenes": comp["scenes"], "people": comp["people"],
                    # the writer's line decisions on no line since the script changed (Project.compose)
                    "aside": comp.get("aside") or [],
                    "added": [dict(a, cells=added_cells.get(a["id"], []), no_room=no_room.get(a["id"]))
                              for a in comp["added"]],
                    "items": eff, "draft": draft, "cuts": p.cuts(), "characters": p.characters(comp["review"]),
                    # the limits and the notation the page shows and composes with (cboe.style)
                    "style": dict(st.public(), formats=list(export_mod.FORMATS)),
                    "summary": summary, "export_check": chk, "error": self.error or st_error or chk_error}


# ---------------------------------------------------------------------- app
def create_app(work, media=None) -> FastAPI:
    """FastAPI app for one work folder (``project.json`` must exist). ``media`` replaces the
    playback-video worker (``proxy.Worker``: start, status, file, request_build, close), which
    starts deciding — and making ``review.mp4`` when needed — in the background right away."""
    work = Path(work)
    if not (work / "project.json").is_file():
        raise FileNotFoundError(f"{work}: project.json not found")
    svc = ReviewService(work, media=media)
    svc.media.start()

    @asynccontextmanager
    async def lifespan(_app):
        yield
        svc.close()

    app = FastAPI(title="CBoE 검수 화면", default_response_class=SafeJSONResponse, lifespan=lifespan,
                  docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")
    app.state.review = svc

    @app.get("/", include_in_schema=False)
    def index(request: Request):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        v = max(int((STATIC / n).stat().st_mtime) for n in ("index.html", "app.js", "app.css"))
        # under the studio the page lives at /p/<id>/: relative URLs resolve against this base,
        # and data-home tells the page where the project list is
        root = request.scope.get("root_path", "").rstrip("/")
        base = "/".join(quote(seg, safe="") for seg in root.split("/")) + "/"      # /p/<id>/ as a URL
        html = (html.replace("{{v}}", str(v)).replace("{{base}}", escape(base, quote=True))
                .replace("{{home}}", "/" if root else ""))
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

    # ---------------------------------------------------------------- state
    @app.get("/api/state")
    def get_state():
        return svc.state()

    @app.post("/api/items/bulk")
    def bulk_update(body: BulkUpdate):
        with svc.lock:
            known = {it["id"] for it in svc.project.items()}
            ids = list(dict.fromkeys(body.ids))
            missing = [i for i in ids if i not in known]
            if missing:
                raise HTTPException(404, f"없는 후보입니다: {', '.join(missing[:5])}")
            for i in ids:
                svc.project.set_decision(i, decision=body.decision)
            return svc.state(recompute=True)

    @app.post("/api/items/{item_id}")
    def update_item(item_id: str, body: ItemUpdate):
        fields = {k: getattr(body, k) for k in body.model_fields_set}
        for k in ("phrase", "short", "name"):
            if isinstance(fields.get(k), str):
                fields[k] = fields[k].strip() or None
        if not fields:
            raise HTTPException(400, "바꿀 내용이 없습니다")
        with svc.lock:
            it = svc.item(item_id)
            if it["kind"] == "speaker" and ("phrase" in fields or "short" in fields):
                raise HTTPException(400, "화자 항목은 대사 글을 바꾸지 않습니다 · 대사 글은 대사 줄 고치기로 바꿉니다")
            if it["kind"] != "speaker" and ("name" in fields or "scope" in fields):
                raise HTTPException(400, "이름과 범위는 화자 항목에만 씁니다")
            if "phrase" in fields and fields["phrase"] is None:
                fields.setdefault("short", None)          # back to the pipeline's phrase pair
            if fields.get("scope") is None:
                fields.pop("scope", None)
            try:
                svc.project.set_decision(item_id, **fields)
            except ReviewError as exc:                    # a name too long, a scope a nameless line cannot take
                raise HTTPException(409 if isinstance(exc, Conflict) else 400, str(exc)) from None
            return svc.state(recompute=True)

    @app.post("/api/placement/{item_id}")
    def fix_placement(item_id: str, body: PlacementFix):
        with svc.lock:
            it = svc.item(item_id)
            if it["kind"] not in ("sfx", "music"):
                raise HTTPException(400, "배치 고치기는 효과음과 음악 후보에만 씁니다")
            t0, t1, fps = svc.window()
            p = svc.project
            if body.action == "exclude":
                p.set_decision(item_id, decision="exclude")
            elif body.action == "clear":
                p.set_placement(item_id, "clear")
            else:
                if body.action == "move":
                    if body.t is None or not math.isfinite(body.t):
                        raise HTTPException(422, "옮길 시각(t)이 필요합니다")
                    if not (t0 <= body.t < t1):
                        raise HTTPException(400, f"처리 구간({t0:.1f}~{t1:.1f}초) 밖으로는 옮길 수 없습니다")
                    t = round(float(body.t), 3)
                else:
                    try:
                        t = swap_prev_time(svc.draft(), it, fps, t0)
                    except PlacementError as exc:
                        raise HTTPException(409, str(exc)) from None
                p.set_placement(item_id, "move", t)
                eff = next((e for e in p.effective_items() if e["id"] == item_id), {})
                if not (eff.get("decided") and eff.get("final") == "include"):
                    p.set_decision(item_id, decision="include")   # moving it means the writer keeps it
            return svc.state(recompute=True)

    # ---------------------------------------------------------- the review layer
    def apply(what: str, fn, *args, **kwargs):
        """Run a Project change; unknown ids -> 404, a value that cannot be used -> 400, a clash
        with the work folder -> 409 (Korean messages)."""
        try:
            return fn(*args, **kwargs)
        except KeyError as exc:
            raise HTTPException(404, f"없는 {what}입니다: {exc.args[0] if exc.args else ''}") from None
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from None
        except ReviewError as exc:
            raise HTTPException(400, str(exc)) from None

    def sent(body: BaseModel) -> dict:
        out = {k: getattr(body, k) for k in body.model_fields_set}
        for k in ("text", "phrase", "name", "before", "manner"):
            if isinstance(out.get(k), str):
                out[k] = out[k].strip() or None
        return out

    @app.post("/api/lines/bulk")
    def lines_bulk(body: LinesBulk):
        """Take several dialogue lines in or out at once (include null: back to the default)."""
        with svc.lock:
            apply("줄", svc.project.set_lines, body.ids, body.include)
            return svc.state(recompute=True)

    @app.post("/api/lines/aside")
    def line_aside(body: AsideAction):
        """A decision on no line since the script changed (state ``aside``): put it on the line
        ``line`` (its fields over that line's own; a kind "speaker" one on that line's speaker
        item — 400 when the line has none) or, with ``line`` null, let it go."""
        with svc.lock:
            apply("결정", svc.project.set_aside, body.key, body.line)
            return svc.state(recompute=True)

    @app.post("/api/lines/{line_id}")
    def update_line(line_id: str, body: LineUpdate):
        """A dialogue line: in / out, its text (script, heard, the writer's), name, language, sung,
        fixed start / end, where to cut it (eojeol numbers) and 'one cell'."""
        fields = sent(body)
        if not fields:
            raise HTTPException(400, "바꿀 내용이 없습니다")
        if "language" in fields and fields["language"] is not None:
            fields["language"] = fields["language"].strip()          # "": spoken in Korean
        with svc.lock:
            apply("줄", svc.project.set_line, line_id, **fields)
            return svc.state(recompute=True)

    @app.post("/api/lines/{line_id}/speaker")
    def update_speaker(line_id: str, body: SpeakerUpdate):
        """The name of this line and, by scope, of the same person's other lines: line ·
        from_here · before · scene · person (older: speaker) · voice."""
        name = (body.name or "").strip() or None
        with svc.lock:
            apply("줄", svc.project.set_speaker, line_id, name, body.scope)
            return svc.state(recompute=True)

    @app.post("/api/extras/{extra_id}")
    def extra_action(extra_id: str, body: ExtraAction):
        """'대본에 없는 말': make it a line (action line), set it aside (ignore) or undo (clear)."""
        fields = {k: v for k, v in sent(body).items() if k != "action" and v is not None}
        with svc.lock:
            apply("'대본에 없는 말'", svc.project.set_extra, extra_id, body.action, **fields)
            return svc.state(recompute=True)

    @app.post("/api/added")
    def add_item(body: AddedNew):
        """Something the writer adds: a line, or a sound / music / other note ([음악이 멈춘다])."""
        fields = {k: v for k, v in sent(body).items() if k != "kind"}
        with svc.lock:
            apply("항목", svc.project.add, body.kind, **fields)
            return svc.state(recompute=True)

    @app.post("/api/added/{added_id}")
    def update_added(added_id: str, body: AddedUpdate):
        fields = sent(body)
        if not fields:
            raise HTTPException(400, "바꿀 내용이 없습니다")
        with svc.lock:
            apply("항목", svc.project.update_added, added_id, **fields)
            return svc.state(recompute=True)

    @app.delete("/api/added/{added_id}")
    def delete_added(added_id: str):
        with svc.lock:
            apply("항목", svc.project.delete_added, added_id)
            return svc.state(recompute=True)

    @app.post("/api/people")
    def update_people(body: PeopleUpdate):
        """How people are labelled: mode name (이름 그대로) | generic (before: '남자') | null, and
        from which line the name is shown (reveal auto | line + reveal_line | never)."""
        fields = {k: v for k, v in sent(body).items() if k != "names"}
        if not body.names:
            raise HTTPException(400, "인물을 하나 이상 골라야 합니다")
        if not fields:
            raise HTTPException(400, "바꿀 내용이 없습니다")
        with svc.lock:
            apply("인물", svc.project.set_people, body.names, **fields)
            return svc.state(recompute=True)

    # --------------------------------------------------------------- export
    @app.post("/api/export")
    def export(body: ExportRequest):
        with svc.lock:
            try:
                res = svc.project.export(body.format, report=True)
            except Exception as exc:
                log.exception("export failed")
                raise HTTPException(500, f"내보내기 실패 · {type(exc).__name__}: {exc}") from None
            path = Path(res["path"])
            svc.exports[body.format] = path
            svc.reload_draft()
            st = svc.state()
            chk = st["export_check"]
            # "check" = the guide check of the file just written (clause by clause, must / should)
            return {"path": str(path), "name": path.name, "format": body.format,
                    "url": f"api/export/{body.format}", "cells": res["cells"], "check": res["check"],
                    "warnings": export_warnings(chk), "export_check": chk, "state": st}

    @app.get("/api/export/{fmt}")
    def download(fmt: ExportFormat):
        with svc.lock:
            path = svc.exports.get(fmt)
            if path is None or not path.is_file():
                path = Path(svc.project.export(fmt))
                svc.exports[fmt] = path
                svc.reload_draft()
        return FileResponse(path, media_type=export_mod.MEDIA_TYPES[fmt], filename=path.name)

    # ---------------------------------------------------------------- media
    @app.get("/media/video", include_in_schema=False)
    def video():
        """ready: review.mp4 (0 s = film t0) · source: the source file · building: 409 · else 404."""
        m = svc.media.status()
        if m["state"] == "building":
            raise HTTPException(409, "재생용 영상을 만드는 중입니다")
        path = svc.media.file() if m["state"] in ("ready", "source") else None
        if path is None or not Path(path).is_file():
            raise HTTPException(404, m.get("note") if m["state"] == "failed" and m.get("note") else "영상 파일이 없습니다")
        if m["state"] == "ready":
            return FileResponse(path, media_type="video/mp4")
        return FileResponse(path, media_type=mimetypes.guess_type(str(path))[0] or "video/mp4")

    @app.get("/api/media")
    def media_state():
        """The 재생용 영상 state (as ``project.media``); the page asks every 2 s while building."""
        return svc.media.status()

    @app.post("/api/media/build")
    def media_build():
        """Make review.mp4 (the page could not open the source); nothing when it is there or being made."""
        return svc.media.request_build()

    @app.get("/api/frame")
    def frame(t: float = Query(...), w: int = Query(640, ge=32, le=3840)):
        if not math.isfinite(t):
            raise HTTPException(400, "t must be a finite number")
        path = svc.project.meta.get("video")
        if not path or not Path(path).is_file():
            raise HTTPException(404, "영상 파일이 없습니다")
        t0, t1, fps = svc.window()
        t = min(max(t, t0), max(t0, t1 - 1.0 / fps))        # never decode outside the project window
        try:
            data = svc.grabber(path).jpeg(t, w)
        except Exception as exc:
            raise HTTPException(500, f"프레임을 읽지 못했습니다 · {exc}") from None
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})

    @app.get("/api/envelope")
    def envelope(step: float = Query(0.1, gt=0, le=60)):
        """The level of the separated tracks for the timeline (감사 9-x3: '파형을 기준으로'):
        env.npz of the sed stage cut down to the loudest value of each ``step`` seconds of the
        processed part, in whole dB. 404 when the stage has not run."""
        with svc.lock:
            t0, t1, _fps = svc.window()
            try:
                env = level_buckets(svc.project.path("env.npz"), t0, t1, step)
            except FileNotFoundError:
                raise HTTPException(404, "소리 세기 파일(env.npz)이 없습니다 · 소리 검출 단계를 돌리면 생깁니다") from None
        return env

    return app


ENV_TRACKS = ("speech", "effects", "music")
ENV_MAX_BUCKETS = 20000                  # a long film still comes as a few hundred kilobytes
ENV_FLOOR_DB = -90


def level_buckets(path: Path, t0: float, t1: float, step: float) -> dict:
    """``{"t0", "step", "tracks": {speech | effects | music: [dB, ...]}}`` — the loudest level of
    each ``step`` seconds from ``t0`` to ``t1`` (step widened so there are at most
    ``ENV_MAX_BUCKETS``); before the file starts, after it ends or silent: ``ENV_FLOOR_DB``."""
    import numpy as np

    if not path.is_file():
        raise FileNotFoundError(path)
    with np.load(path, allow_pickle=False) as d:
        hop = round(float(d["hop"]), 6) if "hop" in d.files else 0.01        # stored as float32
        e0 = round(float(d["t0"]), 6) if "t0" in d.files else 0.0
        raw = {k: np.asarray(d[k], dtype=np.float64) for k in ENV_TRACKS if k in d.files}
    span = max(0.0, t1 - t0)
    per = max(1, round(max(step, span / ENV_MAX_BUCKETS) / hop))      # samples per bucket
    n = max(1, math.ceil(span / (per * hop))) if span else 0
    tracks = {}
    for name, x in raw.items():
        x = np.where(np.isfinite(x), x, ENV_FLOOR_DB)
        i0 = round((t0 - e0) / hop)
        idx = np.arange(n * per) + i0
        ok = (idx >= 0) & (idx < len(x))
        v = np.full(n * per, float(ENV_FLOOR_DB))
        v[ok] = x[idx[ok]]
        tracks[name] = np.clip(np.round(v.reshape(n, per).max(axis=1)), ENV_FLOOR_DB, 0).astype(int).tolist() if n else []
    return {"t0": t0, "step": round(per * hop, 6), "tracks": tracks}
