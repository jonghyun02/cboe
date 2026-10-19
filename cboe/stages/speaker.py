# -*- coding: utf-8 -*-
"""화자 근거 (5.6): for every dialogue line, is the speaking face on screen?

Per line, a clip [start - 0.5 s, start + 2.0 s] at 25 fps:
1. 얼굴 찾기: YuNet (OpenCV FaceDetectorYN, opencv_zoo 2023mar, score >= 0.6) on every frame.
2. 따라가며 묶기: IoU tracking inside each shot (LR-ASD demo values: IoU 0.5, 10 missed
   frames, tracks longer than 10 frames), boxes interpolated over missed frames.
3. 말하는 얼굴: LR-ASD (IJCV 2025, MIT, TalkSet fine-tuned weights) with the repo's
   preprocessing — median-smoothed box, crop scale 0.40, 224 → 112 grey centre crop,
   13 MFCC at 100 Hz from the mix, multi-duration averaging — speaking where the
   ±2-frame smoothed score >= 0.
Decision window: the first second of the line (shorter lines: their length).
face = visible (a track speaks on >= 5 frames), not_visible (no face, or faces that do
not speak on any frame), unclear (a few speaking frames, or silent faces at an estimated
line time).
Also records whether the speaker's name was said in an earlier line (넷플릭스 II.8:
이름은 공개된 뒤에만).
"""
from __future__ import annotations

import math
import re
import sys

import cv2
import numpy as np

from .. import paths
from ..media import Media
from ..util import free_gpu, gpu_lock, log, read_json, timed, write_json

FPS = 25
PRE, POST, WIN = 0.5, 2.0, 1.0
SCORE_THR = 0.6
IOU_THR, NUM_FAILED, MIN_TRACK = 0.5, 10, 10
MIN_FACE_PX = 24
CROP_SCALE = 0.40
SPEAK_FRAMES = 5
DURATIONS = (1, 1, 2, 2, 3)


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
    return model.cuda().eval(), head.cuda().eval()


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
    allsc = []
    with torch.no_grad():
        for d in DURATIONS:
            sc = []
            for i in range(int(math.ceil(length / d))):
                ia = torch.FloatTensor(a[i * d * 100:(i + 1) * d * 100]).unsqueeze(0).cuda()
                iv = torch.FloatTensor(v[i * d * FPS:(i + 1) * d * FPS]).unsqueeze(0).cuda()
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
        t = ln["text"].replace(" ", "")
        if any(k.replace(" ", "") in t for k in keys):
            return True
    return False


def run(p, cfg: dict) -> dict:
    if p.path("speaker.json").exists():
        return {"skipped": True}
    import python_speech_features
    import soundfile as sf
    meta = p.meta
    m = Media(meta, p.work)
    lines = (read_json(p.path("dialogue.json"), {}) or {}).get("lines", [])
    cuts = (read_json(p.path("cuts.json"), {}) or {}).get("cuts", [])
    W, H = m.width, m.height
    det = cv2.FaceDetectorYN.create(str(paths.YUNET), "", (W, H), SCORE_THR, 0.3, 50)
    mix, sr = sf.read(str(p.path("audio") / "mix_16k.wav"), dtype="int16", always_2d=True)
    mix = mix[:, 0]
    out = []
    with gpu_lock(), timed(f"YuNet + LR-ASD on {len(lines)} lines"):
        model, head = _load_lrasd()
        try:
            from ..util import progress
            for i, ln in enumerate(lines):
                progress("speaker", i, len(lines))
                s0 = float(ln["start"])
                if not (m.t0 <= s0 < m.t1 - 0.3):        # a line at the very edge of the window
                    out.append({"line_id": ln["id"], "face": "unclear", "n_faces": 0, "best": None, "tracks": [],
                                "window": [round(s0, 3), round(s0, 3)], "timing": ln.get("timing"),
                                "name_revealed": name_revealed(lines, i - 1, ln.get("speaker"))})
                    continue
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
                    _, faces = det.detect(bgr)
                    cur = []
                    for fc in (faces if faces is not None else []):
                        x, y, w, h = [float(v) for v in fc[:4]]
                        if max(w, h) >= MIN_FACE_PX:
                            cur.append({"frame": f, "bbox": [x, y, x + w, y + h], "score": float(fc[-1])})
                    dets.append(cur)
                tracks = track(dets)
                w0, w1 = int((s0 - a) * FPS), int((s0 + dur - a) * FPS)
                info = []
                for tr in tracks:
                    fa, fb = int(tr["frame"][0]), int(tr["frame"][-1]) + 1
                    aud = mix[int((a - m.t0 + fa / FPS) * sr):int((a - m.t0 + fb / FPS) * sr)]
                    mf = python_speech_features.mfcc(aud, sr, numcep=13, winlen=0.025, winstep=0.010)
                    sc = asd_scores(model, head, crops(frames, tr), mf)
                    in_win = [(int(fr), float(s)) for fr, s in zip(tr["frame"], sc) if w0 <= fr < max(w1, w0 + 1)]
                    if not in_win:
                        continue
                    sp = sum(1 for _, s in in_win if s >= 0)
                    info.append({"size": round(tr["size"], 1), "speaking_frames": sp, "frames": len(in_win),
                                 "mean_score": round(float(np.mean([s for _, s in in_win])), 2),
                                 "max_score": round(float(np.max([s for _, s in in_win])), 2),
                                 "box": [round(float(v), 1) for v in tr["bbox"][len(tr["bbox"]) // 2]]})
                speaking = [t for t in info if t["speaking_frames"] >= SPEAK_FRAMES]
                if speaking:
                    face = "visible"
                elif not info:
                    face = "not_visible"
                elif all(t["speaking_frames"] == 0 and t["max_score"] < 0 for t in info):
                    face = "not_visible"          # faces on screen, none of them is talking
                else:
                    face = "unclear"
                if face == "not_visible" and info and ln.get("timing") not in ("asr", "script"):
                    face = "unclear"              # estimated line time: silent faces prove little
                best = max(info, key=lambda t: t["max_score"], default=None)
                out.append({"line_id": ln["id"], "face": face, "n_faces": len(info),
                            "best": best, "tracks": info, "window": [round(s0, 3), round(s0 + dur, 3)],
                            "name_revealed": name_revealed(lines, i - 1, ln.get("speaker")),
                            "timing": ln.get("timing")})
        finally:
            del model, head
            free_gpu()
    write_json(p.path("speaker.json"), {"lines": out})
    cnt = {}
    for o in out:
        cnt[o["face"]] = cnt.get(o["face"], 0) + 1
    log.info("speaker: %s", cnt)
    return {"faces": cnt}
