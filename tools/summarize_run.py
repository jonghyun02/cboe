# -*- coding: utf-8 -*-
"""One-screen summary of a work folder: stage times, candidates, recommendations, agent, LLM use.

    .venv\\Scripts\\python.exe tools\\summarize_run.py work\\gazua_dev
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


def load(p: Path, default=None):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    w = Path(sys.argv[1])
    meta = load(w / "project.json", {})
    print(f"# {meta.get('title')}  [{meta.get('t0')}, {meta.get('t1')}) s  ({(meta.get('t1', 0) - meta.get('t0', 0)) / 60:.1f} min)")
    rl = load(w / "run_log.json", {})
    print("stage seconds: " + ", ".join(f"{k} {v.get('seconds')}" for k, v in rl.items() if isinstance(v, dict) and "seconds" in v))
    items = (load(w / "items.json", {}) or {}).get("items", [])
    c = Counter((it["kind"], it["rec"]) for it in items)
    for kind in ("sfx", "music", "speaker"):
        tot = sum(v for (k, _), v in c.items() if k == kind)
        print(f"{kind:8s} {tot:4d}  include {c[(kind, 'include')]:3d}  exclude {c[(kind, 'exclude')]:3d}  check {c[(kind, 'check')]:3d}")
    ag = load(w / "agent_done.json", {})
    if ag:
        print(f"agent: ran {ag.get('ran')} (total results {ag.get('total')}), left {len(ag.get('left', []))}, tool calls {ag.get('tool_calls')}")
    agent_items = [it for it in items if it.get("agent")]
    if agent_items:
        tools = Counter(t for it in agent_items for t in (it["agent"].get("tools") or []))
        print("agent tool use per item: " + ", ".join(f"{k} {v}" for k, v in tools.most_common()))
    logp = w / "llm_log.jsonl"
    if logp.exists():
        rows = [json.loads(x) for x in logp.read_text(encoding="utf-8").splitlines() if x.strip()]
        by = {}
        for r in rows:
            tag = r.get("tag", "").split(":")[0]
            b = by.setdefault(tag, {"n": 0, "ok": 0, "sec": 0.0, "cost": 0.0})
            b["n"] += 1
            b["ok"] += 1 if r.get("ok") else 0
            b["sec"] += r.get("seconds") or 0
            b["cost"] += r.get("cost_usd") or 0
        for tag, b in by.items():
            print(f"llm {tag:7s} calls {b['n']:4d} ok {b['ok']:4d}  model time {b['sec']:7.0f}s  list-price ${b['cost']:.2f}")
    d = load(w / "draft.json", {})
    if d:
        print(f"draft: {d.get('summary')}")


if __name__ == "__main__":
    main()
