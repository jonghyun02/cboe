# -*- coding: utf-8 -*-
"""연구 규칙: 시험 데이터의 봉인 구간은 처리하지 않는다.

The two web dramas used for development have sealed test episodes after ``dev_end``, and
볼링블링 is an out-of-source test that is never processed (demo/sdhdemo/common.py). A video
is recognised by the YouTube id in its file name (as given or after resolving links) or by
its exact duration (a renamed copy), so neither the GUI nor ``python -m cboe init``/``run``
can open a project on a sealed part. ``CBOE_RESEARCH_GUARD=0`` turns the check off (for other
people's copies of the tool).
"""
from __future__ import annotations

import os
from pathlib import Path

PROTECTED = {
    "PgaKWtFNI6k": {"title": "로코양", "dev_end": 1127.0, "duration": 3034.221134},       # 7화부터 봉인
    "RIt0WP6hHQE": {"title": "가즈아 도서관", "dev_end": 1579.7, "duration": 3606.569796},  # 6화부터 봉인
    "FGJsSUPXCDs": {"title": "볼링블링", "dev_end": 0.0, "duration": 623.850522},         # 출처 밖 시험: 처리 금지
}
DURATION_TOL = 0.05


def limit(video, duration: float | None = None) -> dict | None:
    """The rule for this video file, or None."""
    if os.environ.get("CBOE_RESEARCH_GUARD", "1") == "0":
        return None
    names = {Path(str(video)).name}
    try:
        names.add(Path(str(video)).resolve().name)
    except OSError:
        pass
    for vid, rule in PROTECTED.items():
        if any(vid in n for n in names) or (duration is not None and abs(float(duration) - rule["duration"]) < DURATION_TOL):
            return {"id": vid, "title": rule["title"], "dev_end": rule["dev_end"]}
    return None


def check_window(video, t0: float, t1: float, duration: float | None = None) -> None:
    """Raise ValueError when [t0, t1) reaches into a sealed part."""
    rule = limit(video, duration)
    if rule is None:
        return
    if rule["dev_end"] <= 0:
        raise ValueError(f"연구 규칙: {rule['title']}은 처리하지 않는 시험 영상입니다")
    if t1 > rule["dev_end"] + 1e-6 or t0 >= rule["dev_end"]:
        m, s = divmod(rule["dev_end"], 60)
        raise ValueError(f"연구 규칙: {rule['title']}은 개발 회차(0~{int(m):02d}:{s:04.1f})까지만 처리합니다 "
                         f"· 끝을 그 안으로 두세요")
