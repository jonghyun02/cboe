# -*- coding: utf-8 -*-
"""Line start error of dialogue.json against the human SDH dialogue cues (개발 회차만).

The test script was made from the same subtitle (tools/make_test_script.py), so line k
of the script is cue k with dialogue text; the cue start is the reference. Lines without a
time in the video (``status`` absent / out_of_range) are counted, not measured.

    .venv\\Scripts\\python.exe tools\\eval_align.py work\\gazua_ep1 ..\\data\\sdh3\\sub_RIt0WP6hHQE.ko.srt
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.make_test_script import cues  # noqa: E402


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    work, srt = Path(sys.argv[1]), Path(sys.argv[2])
    meta = json.loads((work / "project.json").read_text(encoding="utf-8"))
    dia = json.loads((work / "dialogue.json").read_text(encoding="utf-8"))
    lines = dia["lines"]
    status: dict = {}
    for ln in lines:
        status[ln.get("status", "ok")] = status.get(ln.get("status", "ok"), 0) + 1
    print(f"status {status}  extras {len(dia.get('extras', []))}")
    ref = []
    for a, b, rows in cues(srt):
        if not (meta["t0"] <= a < meta["t1"]):
            continue
        txt = [r for r in rows if r.strip() and not r.strip().startswith(("[", "♬"))
               and not (":" in r and r.strip().startswith("("))]
        if txt:
            ref.append(a)
    if len(ref) != len(lines):
        print(f"warning: {len(ref)} reference cues vs {len(lines)} lines")
    pairs = [(ln, r) for ln, r in zip(lines, ref) if ln.get("start") is not None]
    err = np.array([abs(ln["start"] - r) for ln, r in pairs])
    by = {}
    for (ln, _), e in zip(pairs, err):
        by.setdefault(ln.get("timing"), []).append(e)
    for (ln, r), e in zip(pairs, err):
        if e > 1.0:
            print(f"  {ln['id']} {ln['start']:8.2f} ref {r:8.2f} d={ln['start'] - r:+7.2f} {ln.get('timing')} {ln['text'][:24]}")
    print(f"lines {len(err)}  median |err| {np.median(err):.2f}s  <=0.5s {np.mean(err <= 0.5):.0%}  <=1s {np.mean(err <= 1.0):.0%}")
    for k, v in by.items():
        v = np.array(v)
        print(f"  timing={k}: n={len(v)} median {np.median(v):.2f}s <=1s {np.mean(v <= 1.0):.0%}")


if __name__ == "__main__":
    main()
