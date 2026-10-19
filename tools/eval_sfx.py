# -*- coding: utf-8 -*-
"""Compare sound candidates with the human SDH sound lines of the same window (개발 회차만).

Human lines: ``[..]`` lines of the subtitle (효과음, 사람 소리) and ``♬`` lines (음악).
A human sound counts as found when a candidate onset lies within ±TOL s of the cue start.

    .venv\\Scripts\\python.exe tools\\eval_sfx.py work\\gazua_ep1 ..\\data\\sdh3\\sub_RIt0WP6hHQE.ko.srt
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.make_test_script import cues  # noqa: E402

TOL = 1.0


def human_sounds(srt: Path, t0: float, t1: float):
    out = []
    for a, b, rows in cues(srt):
        if not (t0 <= a < t1):
            continue
        for r in rows:
            r = r.strip()
            m = re.match(r"^\[(.+)\]$", r)
            if m and ":" not in r:
                out.append({"t": a, "kind": "sfx", "text": m.group(1)})
            elif r.startswith("♬") and ":" not in r:
                out.append({"t": a, "kind": "music", "text": r})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("work")
    ap.add_argument("srt")
    ap.add_argument("--kind", default="sfx")
    ap.add_argument("--items", action="store_true", help="score items.json (with rec) instead of sfx.json")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    work = Path(a.work)
    meta = json.loads((work / "project.json").read_text(encoding="utf-8"))
    hum = [h for h in human_sounds(Path(a.srt), meta["t0"], meta["t1"]) if h["kind"] == a.kind]
    if a.items:
        cands = [c for c in json.loads((work / "items.json").read_text(encoding="utf-8"))["items"] if c["kind"] == a.kind]
    else:
        src = "sfx.json" if a.kind == "sfx" else "music.json"
        d = json.loads((work / src).read_text(encoding="utf-8"))
        cands = d.get("candidates") or d.get("items") or []
    used = set()
    found = 0
    for h in hum:
        best = None
        for c in cands:
            dt = abs(c["onset"] - h["t"])
            if dt <= TOL and c["id"] not in used and (best is None or dt < best[0]):
                best = (dt, c)
        if best:
            used.add(best[1]["id"])
            found += 1
            c = best[1]
            print(f"  O {h['t']:7.2f} [{h['text']}]  <- {c['id']} {c['onset']:7.2f} {c.get('phrase')} {c.get('rec', '')}")
        else:
            print(f"  X {h['t']:7.2f} [{h['text']}]")
    extra = [c for c in cands if c["id"] not in used]
    print(f"human {len(hum)}  found {found} ({found / max(len(hum), 1):.0%})  candidates {len(cands)}  unmatched candidates {len(extra)}")
    if a.items:
        inc = [c for c in cands if c.get("rec") == "include"]
        inc_hit = [c for c in inc if c["id"] in used]
        print(f"include-recommended {len(inc)}, of which match a human line {len(inc_hit)}; "
              f"human lines matched by an include {len(inc_hit)}/{len(hum)}")


if __name__ == "__main__":
    main()
