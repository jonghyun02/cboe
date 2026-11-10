# -*- coding: utf-8 -*-
"""화자 근거 (5.6): for every dialogue line, is the speaking face on screen?

Per line, a clip [start - 0.5 s, start + 2.0 s] at 25 fps:
1. 얼굴 찾기: YuNet (OpenCV FaceDetectorYN, opencv_zoo 2023mar, score >= 0.6) on every frame;
   on an Intel GPU the same network and decoding through OpenVINO (``OVYuNet``).
2. 따라가며 묶기: IoU tracking inside each shot (LR-ASD demo values: IoU 0.5, 10 missed
   frames, tracks longer than 10 frames), boxes interpolated over missed frames.
3. 말하는 얼굴: LR-ASD (IJCV 2025, MIT, TalkSet fine-tuned weights) with the repo's
   preprocessing — median-smoothed box, crop scale 0.40, 224 → 112 grey centre crop,
   13 MFCC at 100 Hz from the mix, multi-duration averaging — speaking where the
   ±2-frame smoothed score >= 0.
Decision window: the first second of the line (shorter lines: their length).
face (``judge``) = visible (a track speaks on >= 5 frames, and on at least half of the
window or with a best score of 0.5), not_visible (no face, or faces that do not speak on
any frame), unclear (a few speaking frames, a 'speaking' track that is weak on both
counts, or no speaking face at an estimated line time — a guessed time proves nothing
about who is on screen, 감사 3-03).
Lines the models are not run on:
- no time (``start`` is null: the scene is not in the video, or the line lies outside the
  processed part): no row at all;
- the script says the voice comes from off screen (``source``: narration, phone, V.O. …):
  not_visible with ``by: "script"`` (감사 3-06 — the faces on screen belong to others).
Also records whether the speaker's name was said in an earlier line (넷플릭스 II.8:
이름은 공개된 뒤에만).

The stage takes about 6 s a line, so the rows are saved to ``speaker.part.json`` every
SAVE_EVERY_LINES lines (or SAVE_EVERY_S seconds) and when the stage fails; the next run
continues after the last saved line. The part file carries a signature of the dialogue
lines, the cuts, the video window and the constants below: when any of them changed the
stage starts over. ``speaker.json`` is written only when every line is done.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import math
import re
import sys
import time

import cv2
import numpy as np

from .. import paths
from ..media import Media
from ..util import device, free_gpu, gpu_lock, log, read_json, write_json
from .recommend import has_time, timing_estimated      # what a dialogue line's time is worth (shared rules)

FPS = 25
PRE, POST, WIN = 0.5, 2.0, 1.0
SCORE_THR = 0.6
IOU_THR, NUM_FAILED, MIN_TRACK = 0.5, 10, 10
MIN_FACE_PX = 24
CROP_SCALE = 0.40
SPEAK_FRAMES = 5
DURATIONS = (1, 1, 2, 2, 3)
# Margin of '말하는 얼굴이 보임' (감사 3-m1): a track with SPEAK_FRAMES speaking frames is only
# taken as the speaker when it speaks on this share of the decision window or its best score
# reaches VISIBLE_MIN_SCORE; weak on both counts is 'unclear'. The two values come from the
# score distribution of one animation run (a narration line counted as 'visible' with 8 of 25
# frames and a best score of 0.16) and are NOT validated on live-action film.
VISIBLE_MIN_RATIO = 0.5
VISIBLE_MIN_SCORE = 0.5
SAVE_EVERY_LINES, SAVE_EVERY_S = 20, 60.0
PART = "speaker.part.json"


def _load_lrasd():
    import torch
    root = str(paths.LRASD_CODE)
    if root not in sys.path:
        sys.path.insert(0, root)
    from loss import lossAV
    from model.Model import ASD_Model
    model, head = ASD_Model(), lossAV()
    sd = torch.load(paths.LRASD_WEIGHTS, map_location="cpu")
    ms, hs = model.state_dict(), head.state_dict()
    for k, v in sd.items():
        k = k.replace("module.", "")
        if k.startswith("model.") and k[6:] in ms:
            ms[k[6:]].copy_(v)
        elif k.startswith("lossAV.") and k[7:] in hs:
            hs[k[7:]].copy_(v)
    dev = device()
    return model.to(dev).eval(), head.to(dev).eval()


class OVYuNet:
    """YuNet through OpenVINO on an Intel GPU, computing what OpenCV's FaceDetectorYN computes
    (OpenCV 5's DNN engine ignores GPU targets): the frame padded to a multiple of 32, the same
    decoding of the 8/16/32 strides, score = sqrt(cls * obj), cv2.dnn.NMSBoxes on int boxes.
    fp32 on the GPU: 434/434 frames of the test clip gave the same faces as OpenCV (0.000 px),
    18 ms a 1080p frame against 128-233 ms on the CPU. The NPU (9 ms) is fp16 only and changed
    the face count on 2 frames, so it is not used."""

    def __init__(self, w: int, h: int, device: str = "GPU"):
        import openvino as ov
        from openvino.preprocess import PrePostProcessor
        self.pw, self.ph = ((w - 1) // 32 + 1) * 32, ((h - 1) // 32 + 1) * 32
        core = ov.Core()
        m = core.read_model(str(paths.YUNET))
        m.reshape({"input": [1, 3, self.ph, self.pw]})
        ppp = PrePostProcessor(m)                         # uint8 BGR HWC -> float NCHW on the device
        ppp.input().tensor().set_element_type(ov.Type.u8).set_layout(ov.Layout("NHWC"))
        ppp.input().model().set_layout(ov.Layout("NCHW"))
        ppp.input().preprocess().convert_element_type(ov.Type.f32)
        m = ppp.build()
        self.names = [o.get_any_name() for o in m.outputs]
        self.req = core.compile_model(m, device, {"INFERENCE_PRECISION_HINT": "f32"}).create_infer_request()

    def detect(self, bgr: np.ndarray):
        """Rows [x, y, w, h, 5 landmarks (x, y), score] like FaceDetectorYN.detect()[1], or None."""
        h, w = bgr.shape[:2]
        pad = cv2.copyMakeBorder(bgr, 0, self.ph - h, 0, self.pw - w, cv2.BORDER_CONSTANT, value=0)
        res = self.req.infer({0: pad[None]})
        out = {n: res[i] for i, n in enumerate(self.names)}
        faces = []
        for st in (8, 16, 32):
            cols = self.pw // st
            score = np.sqrt(np.clip(out[f"cls_{st}"].reshape(-1), 0, 1) * np.clip(out[f"obj_{st}"].reshape(-1), 0, 1))
            keep = np.nonzero(score >= SCORE_THR)[0]
            r, c = (keep // cols).astype(np.float32), (keep % cols).astype(np.float32)
            bb, kp = out[f"bbox_{st}"].reshape(-1, 4)[keep], out[f"kps_{st}"].reshape(-1, 10)[keep].copy()
            bw, bh = np.exp(bb[:, 2]) * st, np.exp(bb[:, 3]) * st
            kp[:, 0::2] = (kp[:, 0::2] + c[:, None]) * st
            kp[:, 1::2] = (kp[:, 1::2] + r[:, None]) * st
            faces.append(np.column_stack([(c + bb[:, 0]) * st - bw / 2, (r + bb[:, 1]) * st - bh / 2,
                                          bw, bh, kp, score[keep]]).astype(np.float32))
        faces = np.concatenate(faces)
        if len(faces) > 1:
            idx = cv2.dnn.NMSBoxes([(int(f[0]), int(f[1]), int(f[2]), int(f[3])) for f in faces],
                                   faces[:, 14].tolist(), SCORE_THR, 0.3, top_k=50)
            faces = faces[np.array(idx, dtype=int).reshape(-1)]
        return faces if len(faces) else None


class _CVYuNet:
    def __init__(self, w: int, h: int):
        self.det = cv2.FaceDetectorYN.create(str(paths.YUNET), "", (w, h), SCORE_THR, 0.3, 50)

    def detect(self, bgr: np.ndarray):
        return self.det.detect(bgr)[1]


def face_detector(w: int, h: int):
    """OpenVINO on the Intel GPU when the model stages run on one (xpu), else OpenCV on the CPU."""
    if device().startswith("xpu"):
        try:
            return OVYuNet(w, h)
        except Exception as e:                            # no openvino / no GPU plugin
            log.warning("YuNet on the Intel GPU failed (%s); using OpenCV on the CPU", e)
    return _CVYuNet(w, h)


def _iou(a, b) -> float:
    xa, ya, xb, yb = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, xb - xa) * max(0.0, yb - ya)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def track(dets: list[list[dict]]) -> list[dict]:
    """LR-ASD style greedy IoU tracking; dets[f] = [{'frame', 'bbox', 'score'}]."""
    dets = [list(f) for f in dets]
    tracks = []
    while True:
        tr = []
        for frame_faces in dets:
            for face in list(frame_faces):
                if not tr:
                    tr.append(face)
                    frame_faces.remove(face)
                elif face["frame"] - tr[-1]["frame"] <= NUM_FAILED:
                    if _iou(face["bbox"], tr[-1]["bbox"]) > IOU_THR:
                        tr.append(face)
                        frame_faces.remove(face)
                        continue
                else:
                    break
        if not tr:
            break
        if len(tr) > MIN_TRACK:
            fr = np.array([f["frame"] for f in tr])
            bb = np.array([f["bbox"] for f in tr], dtype=float)
            fi = np.arange(fr[0], fr[-1] + 1)
            bi = np.stack([np.interp(fi, fr, bb[:, j]) for j in range(4)], axis=1)
            size = max(float(np.mean(bi[:, 2] - bi[:, 0])), float(np.mean(bi[:, 3] - bi[:, 1])))
            tracks.append({"frame": fi, "bbox": bi, "size": size, "conf": float(np.mean([f["score"] for f in tr]))})
    return tracks


def crops(frames: np.ndarray, tr: dict) -> np.ndarray:
    from scipy import signal
    s = signal.medfilt(np.maximum(tr["bbox"][:, 3] - tr["bbox"][:, 1], tr["bbox"][:, 2] - tr["bbox"][:, 0]) / 2, 13)
    y = signal.medfilt((tr["bbox"][:, 1] + tr["bbox"][:, 3]) / 2, 13)
    x = signal.medfilt((tr["bbox"][:, 0] + tr["bbox"][:, 2]) / 2, 13)
    out = []
    for k, f in enumerate(tr["frame"]):
        bs = s[k]
        bsi = int(bs * (1 + 2 * CROP_SCALE))
        img = np.pad(frames[f], ((bsi, bsi), (bsi, bsi), (0, 0)), "constant", constant_values=(110, 110))
        my, mx = y[k] + bsi, x[k] + bsi
        face = img[int(my - bs):int(my + bs * (1 + 2 * CROP_SCALE)), int(mx - bs * (1 + CROP_SCALE)):int(mx + bs * (1 + CROP_SCALE))]
        if face.size == 0:
            face = np.full((224, 224, 3), 110, np.uint8)
        g = cv2.cvtColor(cv2.resize(face, (224, 224)), cv2.COLOR_RGB2GRAY)
        out.append(g[56:168, 56:168])
    return np.array(out)


def asd_scores(model, head, faces: np.ndarray, mfcc: np.ndarray) -> np.ndarray:
    import torch
    length = min((mfcc.shape[0] - mfcc.shape[0] % 4) / 100, faces.shape[0] / FPS)
    if length < 0.2:
        return np.full(len(faces), -9.0)
    a = mfcc[:int(round(length * 100))]
    v = faces[:int(round(length * FPS))]
    dev = next(model.parameters()).device
    allsc = []
    with torch.no_grad():
        for d in DURATIONS:
            sc = []
            for i in range(int(math.ceil(length / d))):
                ia = torch.FloatTensor(a[i * d * 100:(i + 1) * d * 100]).unsqueeze(0).to(dev)
                iv = torch.FloatTensor(v[i * d * FPS:(i + 1) * d * FPS]).unsqueeze(0).to(dev)
                if ia.shape[1] < 4 or iv.shape[1] < 1:
                    continue
                out = model.forward_audio_visual_backend(model.forward_audio_frontend(ia), model.forward_visual_frontend(iv))
                sc.extend(head.forward(out, labels=None).tolist())
            allsc.append(np.array(sc[:len(v)] + [sc[-1] if sc else -9.0] * max(0, len(v) - len(sc))))
    s = np.mean(np.array(allsc), axis=0)
    sm = np.array([s[max(k - 2, 0):k + 3].mean() for k in range(len(s))])
    out = np.full(len(faces), -9.0)
    out[:len(sm)] = sm
    return out


def name_revealed(lines: list[dict], idx: int, name: str | None) -> bool | None:
    if not name:
        return None
    keys = {name, name.replace(" ", "")}
    keys |= {w for w in name.split() if len(w) >= 2}
    keys = {k for k in keys if k and not re.fullmatch(r"[A-Za-z]{1,3}", k)}
    for ln in lines[:idx + 1]:
        if ln.get("status") == "absent":          # a scene that is not in the video was not heard
            continue
        t = (ln.get("text") or "").replace(" ", "")
        if any(k.replace(" ", "") in t for k in keys):
            return True
    return False


# ------------------------------------------------------------- decisions without the models
def judge(info: list[dict], estimated: bool = False) -> str:
    """visible / not_visible / unclear from the face tracks of the decision window
    (``info``: {'speaking_frames', 'frames', 'max_score', …} per track; ``estimated``: the line
    time is a guess)."""
    speaking = [t for t in info if t["speaking_frames"] >= SPEAK_FRAMES]
    if any(t["speaking_frames"] >= VISIBLE_MIN_RATIO * max(t.get("frames") or 0, 1)
           or t["max_score"] >= VISIBLE_MIN_SCORE for t in speaking):
        return "visible"
    if speaking:
        return "unclear"                  # speaking frames, but few of the window and a low best score
    if not info or all(t["speaking_frames"] == 0 and t["max_score"] < 0 for t in info):
        # no face, or faces of which none is talking. At an estimated time neither proves that
        # the speaker is off screen: the line may sit a few seconds from where it is spoken.
        return "unclear" if estimated else "not_visible"
    return "unclear"


def preset(ln: dict, t0: float, t1: float) -> dict | None:
    """The row of a timed line that needs no model, or None when the models have to look."""
    s0 = float(ln["start"])
    dur = max(min(float(ln["end"]) - s0, WIN), 0.3)
    row = {"line_id": ln["id"], "face": "unclear", "n_faces": 0, "best": None, "tracks": [],
           "window": [round(s0, 3), round(s0 + dur, 3)], "timing": ln.get("timing")}
    if ln.get("source"):                  # the script: narration, phone, voice from off screen …
        return {**row, "face": "not_visible", "by": "script", "source": ln["source"]}
    if not (t0 <= s0 < t1 - 0.3):         # a line at the very edge of the window
        return {**row, "window": [round(s0, 3), round(s0, 3)]}
    return None


def signature(lines: list[dict], cuts: list[float], meta: dict) -> str:
    """Hash of everything a row depends on; a part file with another signature is not continued."""
    keys = ("id", "speaker", "text", "start", "end", "timing", "status", "source")
    rows = [[ln.get(k) for k in keys] for ln in lines]
    window = [meta.get(k) for k in ("video", "t0", "t1", "fps", "width", "height")]
    params = [FPS, PRE, POST, WIN, SCORE_THR, IOU_THR, NUM_FAILED, MIN_TRACK, MIN_FACE_PX, CROP_SCALE, SPEAK_FRAMES,
              list(DURATIONS), VISIBLE_MIN_RATIO, VISIBLE_MIN_SCORE]
    blob = json.dumps([rows, list(cuts), window, params], ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def resumable(part: dict | None, sig: str) -> list[dict]:
    """Rows of an interrupted run that still hold: the part file was made from the same dialogue
    lines, cuts, window and constants. Anything else: start over."""
    if not isinstance(part, dict) or part.get("signature") != sig:
        return []
    return [r for r in part.get("lines") or [] if isinstance(r, dict) and r.get("line_id")]


class Analyser:
    """YuNet + LR-ASD on the clip around a line start. Created for the first line that needs
    the models: holds the GPU lock, the two models and the mix until ``close()``."""

    def __init__(self, p):
        import soundfile as sf
        self._stack = contextlib.ExitStack()
        self._stack.enter_context(gpu_lock())
        try:
            self.m = Media(p.meta, p.work)
            mix, self.sr = sf.read(str(p.path("audio") / "mix_16k.wav"), dtype="int16", always_2d=True)
            self.mix = mix[:, 0]
            self.det = face_detector(self.m.width, self.m.height)
            self.model, self.head = _load_lrasd()
        except BaseException:
            self._stack.close()
            raise

    def tracks(self, ln: dict, cuts: list[float]) -> tuple[list[dict], list[float]]:
        """(face tracks of the decision window, [window start, end]) of one line."""
        import python_speech_features
        m, mix, sr, W, H = self.m, self.mix, self.sr, self.m.width, self.m.height
        s0 = float(ln["start"])
        dur = max(min(float(ln["end"]) - s0, WIN), 0.3)
        a, b = max(s0 - PRE, m.t0), min(s0 + POST, m.t1)
        # stay inside the shot that contains the line start
        prev_cut = max([c for c in cuts if c <= s0 + 0.05], default=m.t0)
        next_cut = min([c for c in cuts if c > s0 + 0.05], default=m.t1)
        a, b = max(a, prev_cut), min(b, next_cut)
        if b - a < 0.4:
            a, b = max(s0 - 0.2, m.t0), min(s0 + 0.6, m.t1)
        frames = m.frames(a, b, W, H, fps=FPS)
        dets = []
        for f, fr in enumerate(frames):
            bgr = cv2.cvtColor(fr, cv2.COLOR_RGB2BGR)
            faces = self.det.detect(bgr)
            cur = []
            for fc in (faces if faces is not None else []):
                x, y, w, h = [float(v) for v in fc[:4]]
                if max(w, h) >= MIN_FACE_PX:
                    cur.append({"frame": f, "bbox": [x, y, x + w, y + h], "score": float(fc[-1])})
            dets.append(cur)
        w0, w1 = int((s0 - a) * FPS), int((s0 + dur - a) * FPS)
        info = []
        for tr in track(dets):
            fa, fb = int(tr["frame"][0]), int(tr["frame"][-1]) + 1
            aud = mix[int((a - m.t0 + fa / FPS) * sr):int((a - m.t0 + fb / FPS) * sr)]
            mf = python_speech_features.mfcc(aud, sr, numcep=13, winlen=0.025, winstep=0.010)
            sc = asd_scores(self.model, self.head, crops(frames, tr), mf)
            in_win = [(int(fr), float(s)) for fr, s in zip(tr["frame"], sc) if w0 <= fr < max(w1, w0 + 1)]
            if not in_win:
                continue
            sp = sum(1 for _, s in in_win if s >= 0)
            info.append({"size": round(tr["size"], 1), "speaking_frames": sp, "frames": len(in_win),
                         "mean_score": round(float(np.mean([s for _, s in in_win])), 2),
                         "max_score": round(float(np.max([s for _, s in in_win])), 2),
                         "box": [round(float(v), 1) for v in tr["bbox"][len(tr["bbox"]) // 2]]})
        return info, [round(s0, 3), round(s0 + dur, 3)]

    def close(self) -> None:
        self.model = self.head = self.det = None
        free_gpu()
        self._stack.close()


def run(p, cfg: dict) -> dict:
    if p.path("speaker.json").exists():
        return {"skipped": True}
    from ..util import progress
    meta = p.meta
    lines = (read_json(p.path("dialogue.json"), {}) or {}).get("lines", [])
    cuts = (read_json(p.path("cuts.json"), {}) or {}).get("cuts", [])
    t0, t1 = float(meta.get("t0", 0.0)), float(meta["t1"])
    sig = signature(lines, cuts, meta)
    part = p.path(PART)
    try:
        out = resumable(read_json(part), sig)
    except ValueError:                        # an unreadable part file: start over
        out = []
    done = {r["line_id"] for r in out}
    resumed = len(out)
    if resumed:
        log.info("speaker: continuing an interrupted run after %d finished lines", resumed)
    order = {ln["id"]: i for i, ln in enumerate(lines)}

    def save(path, complete: bool) -> None:
        out.sort(key=lambda r: order.get(r["line_id"], len(order)))
        write_json(path, {"lines": out, "complete": complete, "signature": sig,
                          "params": {"VISIBLE_MIN_RATIO": VISIBLE_MIN_RATIO, "VISIBLE_MIN_SCORE": VISIBLE_MIN_SCORE,
                                     "SPEAK_FRAMES": SPEAK_FRAMES, "WIN": WIN}})

    analyser, no_time, since, last = None, 0, 0, time.time()
    try:
        for i, ln in enumerate(lines):
            progress("speaker", i, len(lines))
            if not has_time(ln):
                no_time += 1
                continue
            if ln["id"] in done:
                continue
            row = preset(ln, t0, t1)
            if row is None:
                if analyser is None:
                    log.info("speaker: YuNet + LR-ASD from line %d of %d ...", i + 1, len(lines))
                    analyser = Analyser(p)
                info, window = analyser.tracks(ln, cuts)
                row = {"line_id": ln["id"], "face": judge(info, timing_estimated(ln)), "n_faces": len(info),
                       "best": max(info, key=lambda t: t["max_score"], default=None), "tracks": info,
                       "window": window, "timing": ln.get("timing")}
            row["name_revealed"] = name_revealed(lines, i - 1, ln.get("speaker"))
            out.append(row)
            done.add(ln["id"])
            since += 1
            if since >= SAVE_EVERY_LINES or time.time() - last >= SAVE_EVERY_S:
                save(part, False)
                since, last = 0, time.time()
    except BaseException:
        if len(out) > resumed:                # keep what is done: the next run continues after it
            save(part, False)
        raise
    finally:
        if analyser is not None:
            analyser.close()
    save(p.path("speaker.json"), True)
    part.unlink(missing_ok=True)
    cnt = {}
    for o in out:
        cnt[o["face"]] = cnt.get(o["face"], 0) + 1
    by_script = sum(1 for o in out if o.get("by") == "script")
    log.info("speaker: %s (%d by the script's off-screen mark, %d lines without a time skipped, %d taken over "
             "from an interrupted run)", cnt, by_script, no_time, resumed)
    return {"faces": cnt, "by_script": by_script, "no_time": no_time, "resumed": resumed}
