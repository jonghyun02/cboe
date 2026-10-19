# -*- coding: utf-8 -*-
"""Make a test 대본 (``이름: 대사`` text) from a human SDH subtitle track.

There is no production script for the test web dramas, so the dialogue text of the
human SDH track stands in for it: sound lines ([..]) and music lines (♬) are dropped,
``(이름 / 정보)`` prefixes give the speaker. The human track names a speaker only on
some cues, so a name is filled in only between two cues that carry the same name
(no other name in between); every other line keeps an unknown speaker.

    .venv\\Scripts\\python.exe tools\\make_test_script.py <sdh.srt> <out.txt> --start 0 --end 290.15
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cboe.script import TIME_RE, _secs  # noqa: E402


def cues(path: Path):
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").strip()):
        rows = block.strip().splitlines()
        tl = next((i for i, r in enumerate(rows) if TIME_RE.search(r)), None)
        if tl is None:
            continue
        g = TIME_RE.search(rows[tl]).groups()
        yield _secs(*g[:4]), _secs(*g[4:]), rows[tl + 1:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("srt")
    ap.add_argument("out")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float, default=1e9)
    a = ap.parse_args()
    lines = []
    for t0, t1, rows in cues(Path(a.srt)):
        if not (a.start <= t0 < a.end):
            continue
        speaker, texts = None, []
        for r in rows:
            r = r.strip()
            if not r or r.startswith("[") or r.startswith("♬") or ":" in r and r.startswith("("):
                continue
            m = re.match(r"^\(([^()]+)\)\s*(.*)$", r)
            if m:
                speaker = m.group(1).split("/")[0].strip()
                r = m.group(2).strip()
            if r:
                texts.append(r)
        if texts:
            lines.append({"t": t0, "speaker": speaker, "text": " ".join(texts)})
    # fill names only between two cues with the same name
    named = [i for i, ln in enumerate(lines) if ln["speaker"]]
    for i, j in zip(named, named[1:]):
        if lines[i]["speaker"] == lines[j]["speaker"]:
            for k in range(i + 1, j):
                lines[k]["speaker"] = lines[i]["speaker"]
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"{ln['speaker']}: {ln['text']}" if ln["speaker"] else ln["text"] for ln in lines)
    out.write_text("# test script from human SDH dialogue lines\n" + body + "\n", encoding="utf-8")
    n_named = sum(1 for ln in lines if ln["speaker"])
    print(f"{out}: {len(lines)} lines, {n_named} with a speaker")


if __name__ == "__main__":
    main()
