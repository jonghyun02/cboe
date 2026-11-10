# -*- coding: utf-8 -*-
"""Work-folder state shared by the CLI and the review server.

The pipeline writes ``items.json`` (candidates with evidence and a recommendation)
and ``dialogue.json`` once. The writer's choices live in ``review.json``. Every
time a choice changes, ``Project.recompute()`` merges both, runs placement
(끼워 넣기) and saves ``draft.json`` — "고칠 때마다 배치를 다시 계산".

``review.json`` (every section may be missing; an older file opens as it is)::

    items     {item id: {decision, phrase, short, name, fp, at}}       후보 결정 (as before)
    placement {item id: {action "move", t, fp}}                        배치 고치기 (as before)
    lines     {line id: {fp, ctx, at, include, text, text_from, name, language, sung,
                         start, end, breaks, keep, show_name}}         대사 줄 결정
                         (show_name: only a line without a speaker item, e.g. one brought back)
    added     {"A0001": {kind "line"|"sfx"|"music", start, end, text (line) | phrase (sound),
                         name, language, sung, show_name, extra, extra_fp, at}}   작가가 넣은 것
    extras    {extra id: {fp, action "ignore", at}}                    무시하기로 한 '대본에 없는 말'
    people    {name: {mode "name"|"generic", before, reveal "auto"|"line"|"never",
                      reveal_line, reveal_fp, reveal_ctx, at}}         인물별 표시

A line decision is about the line with the same fingerprint (``line_fp``: speaker, text and the
place among lines of the same speaker and text) and context (``ctx``: the script lines around it
and how many lines share its words): line ids are sequence numbers that change when the script is
read again, so a stored decision never lands on another line (as for items). Among lines of the
same words the one with the same neighbours is the line; when that cannot be told the decision
stays aside under ``fp:<fingerprint>`` rather than land on a line the writer never touched.
An extra that becomes a line keeps its fingerprint in the added line (``extra_fp``).

``compose()`` merges everything into what placement, the review server and the export check use
(the dialogue lines with the writer's choices, ``effective_lines``; the items with the
recommendations that lean on another item worked out again, ``effective_items``):

- Lines with a time (ok · differs · unheard) are in by default — an unheard line is not left out
  (감사 1-03); absent / out_of_range lines have no time and are out until the writer gives them a
  start and an end. A line the writer takes out is out, and so is its speaker item.
- A line whose text changes gets new word times: the ASR word times when the writer picks what was
  heard, else the line span shared out by letters (the words of the script stay in
  ``script_text``). Every line says where it comes from (``origin``), why it is out
  (``excluded``) and whether its time is a guess (``estimated``: not when the writer fixed both
  start and end).
- '같은 화자가 이어 말함' (감사 3-07) is worked out again from the merged lines: the line before
  (in film order, in the subtitles) is of the same person and the same scene and source, and that
  person was identified there (the name shown, or no name needed: a visible face, an earlier
  run) — no time limit. The writer's own decisions are never changed.
- '앞 자막에서 이미 알림' (감사 6-m2, 9-x1): a sound or music item that ``follows`` a root is
  'already told' only when the root really goes out (final include and placed); otherwise it is
  확인 필요. The writer's own decisions are never changed.
- Labels come from ``cboe.people`` every time (never stored): generic labels before the name is
  told, numbers per scene, and the people whose name is shown before it is told.
"""
from __future__ import annotations

import math
import re
import threading
import time
from pathlib import Path

from .util import read_json, write_json

FINALS = ("include", "exclude", "check")
LINE_STATUSES = ("ok", "differs", "unheard", "absent", "out_of_range")
NO_TIME = ("absent", "out_of_range")          # no time in the video: out unless the writer gives one
ESTIMATED = ("voice", "even")                 # dialogue.json ``timing`` of a guessed time
EXCLUDED_KO = {"absent": "영화에 없음", "out_of_range": "처리 구간 밖", "writer": "작가가 뺌", "no_time": "시각 없음"}
ORIGIN_KO = {"script": "대본", "heard": "들린 말", "writer": "작가"}
TEXT_FROM = ("script", "heard", "writer")
LINE_FIELDS = ("include", "text", "text_from", "name", "language", "sung", "start", "end", "breaks", "keep",
               "show_name")
ADDED_KINDS = ("line", "sfx", "music")
CONTINUES = "같은 화자가 이어 말함"            # the reason of a speaker item whose name a run carries


class ReviewError(ValueError):
    """A writer's change that cannot be applied (the server answers 400 with this Korean text)."""


class Conflict(ReviewError):
    """A change that clashes with the state of the work folder (answered as 409)."""


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _norm(text) -> str:
    return " ".join(str(text or "").split())


def _line_base(ln: dict) -> str:
    return f"line@{ln.get('speaker') or ''}:{_norm(ln.get('text'))}"


def line_fp(ln: dict, k: int = 0) -> str:
    """What a line decision is about: speaker, text and ``k`` — the place of this line among the
    lines of the same speaker and text (a line said twice is two lines). The decision also keeps
    the line's context (``_line_index``), so that a line of the same words added or taken out in
    front of it, when the script is read again, does not move the decision onto another line."""
    return f"{_line_base(ln)}#{k}"


def _split_fp(fp) -> tuple[str, int]:
    base, sep, k = str(fp or "").rpartition("#")
    return (base, int(k)) if sep and k.isdigit() else (str(fp or ""), 0)


def _line_index(lines: list[dict]) -> dict:
    """What line decisions are matched against: for every line id its fingerprint (``line_fp``)
    and its context ``{"prev", "next", "n"}`` — the speaker and words of the two script lines
    before it (nearest first) and of the two after it ("" past either end), and how many lines
    have its speaker and words."""
    bases = [_line_base(ln) for ln in lines]
    count: dict = {}
    for b in bases:
        count[b] = count.get(b, 0) + 1

    def at(j):
        return bases[j] if 0 <= j < len(bases) else ""
    seen: dict = {}
    fps, ctx, by_base = {}, {}, {}
    for i, ln in enumerate(lines):
        b, lid = bases[i], ln.get("id")
        k = seen.get(b, 0)
        seen[b] = k + 1
        fps[lid] = f"{b}#{k}"
        ctx[lid] = {"prev": [at(i - 1), at(i - 2)], "next": [at(i + 1), at(i + 2)], "n": count[b]}
        by_base.setdefault(b, []).append(lid)
    return {"fps": fps, "ctx": ctx, "by_base": by_base}


def _ctx_score(was: dict, now: dict) -> int:
    """How much of the context ``was`` (stored with a decision) the line has ``now``: 2 for each
    nearest neighbour, 1 for each next one. The end of the script ("") is no evidence: lines are
    added at either end too."""
    score = 0
    for side in ("prev", "next"):
        a, b = list(was.get(side) or []), list(now.get(side) or [])
        for j, w in enumerate((2, 1)):
            if j < len(a) and j < len(b) and a[j] and a[j] == b[j]:
                score += w
    return score


def _pick_line(fp, ctx, index: dict) -> tuple[str | None, int]:
    """(line id, how sure) of the line a decision stored with ``fp`` and ``ctx`` is about; (None, 0)
    when it cannot be told. A line whose words are unique, then and now, is that line. Among lines
    of the same words the one with the same lines around it wins; when that does not settle it,
    the place among them (``#k``) counts only if their number has not changed — else the decision
    is about none of them (it stays aside, under ``fp:``). A decision of an older version (no
    context) goes by the place, as before."""
    base, _ = _split_fp(fp)
    cands = index["by_base"].get(base) or []
    if not cands:
        return None, 0
    by_k = next((c for c in cands if index["fps"][c] == fp), None)
    if not isinstance(ctx, dict):
        return by_k, (1 if by_k else 0)
    n = ctx.get("n")
    if len(cands) == 1 and n == 1:
        return cands[0], 9
    score = {c: _ctx_score(ctx, index["ctx"][c]) for c in cands}
    top = max(score.values())
    best = [c for c in cands if score[c] == top]
    if top > 0 and len(best) == 1:
        return best[0], top
    if by_k in best and n == len(cands):
        return by_k, top
    return None, 0


def extra_fp(x: dict) -> str:
    """What a decision on a '대본에 없는 말' is about: where it starts and what was heard."""
    return f"extra@{float(_num(x.get('start')) or 0.0):.1f}:{_norm(x.get('text'))}"


def even_words(toks: list[str], start: float, end: float) -> list[dict]:
    """Word times for ``toks`` over start..end, shared out by their width (II.2 units)."""
    from .textutil import units
    if not toks:
        return []
    w = [max(units(t), 0.5) for t in toks]
    total, t, out = sum(w), float(start), []
    for tok, x in zip(toks, w):
        d = (float(end) - float(start)) * x / total
        out.append({"w": tok, "s": round(t, 3), "e": round(t + d, 3)})
        t += d
    return out


def _mapped(words: list[dict], s0: float, e0: float, s1: float, e1: float) -> list[dict]:
    """``words`` moved from the span s0..e0 onto s1..e1 (a writer's new start / end)."""
    span = (e0 - s0) or 1.0

    def f(t):
        return round(s1 + (float(t) - s0) * (e1 - s1) / span, 3)
    return [dict(w, s=f(w["s"]), e=f(w["e"])) for w in words]


def heard_words(toks: list[str], asr: list[dict], start, end):
    """The ASR words that say ``toks`` (the text the writer took from what was heard) near
    start..end, one per token; None when they cannot be found."""
    if not toks or start is None or end is None or not asr:
        return None
    lo, hi = float(start) - 2.0, float(end) + 2.0
    near = [w for w in asr if isinstance(w, dict) and _num(w.get("s")) is not None and _num(w.get("e")) is not None
            and lo <= float(w["s"]) <= hi and str(w.get("w") or "").strip()]
    flat = []                                   # (token, s, e): an ASR word with spaces is shared out
    for w in near:
        sub = str(w["w"]).split()
        flat += [(x["w"], x["s"], x["e"]) for x in even_words(sub, float(w["s"]), float(w["e"]))]
    n = len(toks)
    for i in range(len(flat) - n + 1):
        if [t for t, _, _ in flat[i:i + n]] == toks:
            return [{"w": t, "s": round(float(s), 3), "e": round(float(e), 3)} for t, s, e in flat[i:i + n]]
    return None


class Project:
    def __init__(self, work):
        self.work = Path(work)
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ files
    def path(self, name: str) -> Path:
        return self.work / name

    @property
    def meta(self) -> dict:
        return read_json(self.path("project.json"), {}) or {}

    def dialogue(self) -> list[dict]:
        return (read_json(self.path("dialogue.json"), {}) or {}).get("lines", [])

    def extras(self) -> list[dict]:
        """dialogue.json ``extras``: speech the ASR heard that is in no script line."""
        return (read_json(self.path("dialogue.json"), {}) or {}).get("extras", []) or []

    def scenes(self) -> list[dict]:
        return (read_json(self.path("scenes.json"), {}) or {}).get("scenes", []) or []

    def items(self) -> list[dict]:
        return (read_json(self.path("items.json"), {}) or {}).get("items", [])

    def cuts(self) -> list[float]:
        return (read_json(self.path("cuts.json"), {}) or {}).get("cuts", [])

    def characters(self, review: dict | None = None) -> list[str]:
        """Names for the writer to pick: the script's, the speakers of the lines, then the names the
        writer gave (lines, items, added lines)."""
        script = read_json(self.path("script.json"), {}) or {}
        review = review if review is not None else self.review()
        names = list(script.get("characters", []))
        given = [ln.get("speaker") for ln in self.dialogue()]
        given += [d.get("name") for sec in ("lines", "items", "added") for d in review.get(sec, {}).values()
                  if isinstance(d, dict)]
        for n in given:
            if n and n not in names:
                names.append(n)
        return names

    def review(self) -> dict:
        r = read_json(self.path("review.json"), {}) or {}
        for sec in ("items", "placement", "lines", "added", "extras", "people"):
            if not isinstance(r.get(sec), dict):
                r[sec] = {}
        return r

    def save_review(self, review: dict) -> None:
        write_json(self.path("review.json"), review)

    # ------------------------------------------------------------- decisions
    @staticmethod
    def fingerprint(it: dict) -> str:
        """What a decision was made about: kind and onset of a sound, line id and text of a
        speaker line. Item ids are sequence numbers and change when a stage is re-run, so a
        stored decision only applies to the item with the same fingerprint."""
        if it.get("kind") == "speaker":
            sp = it.get("speaker") or {}
            return f"speaker@{sp.get('line_id')}:{it.get('phrase', '')}"
        return f"{it.get('kind')}@{float(it.get('onset', 0.0)):.2f}"

    def _decisions(self, review: dict, items: list[dict], section: str) -> dict[str, dict]:
        """Decisions of ``review[section]`` keyed by the current item id (fingerprint-checked)."""
        stored = review.get(section, {})
        by_fp = {d["fp"]: d for d in stored.values() if isinstance(d, dict) and d.get("fp")}
        out = {}
        for it in items:
            fp = self.fingerprint(it)
            d = stored.get(it["id"])
            if d and d.get("fp") and d["fp"] != fp:
                d = None                      # the id now names another candidate
            d = d or by_fp.get(fp)
            if d:
                out[it["id"]] = d
        return out

    def _merge_items(self, review: dict, items: list[dict], lines: dict) -> list[dict]:
        """Items with the stored decisions merged in (``final``, ``decided``, ``phrase``/``short``,
        ``placement``; a speaker item's ``speaker.name`` = the line's name). ``lines``: the
        merged lines by id (their ``name`` is the writer's line name, else the name stored on the
        item, else the script's)."""
        dec = self._decisions(review, items, "items")
        plc = self._decisions(review, items, "placement")
        out = []
        for it in items:
            it = dict(it)
            d = dec.get(it["id"], {})
            it["decided"] = d.get("decision") in ("include", "exclude")
            it["final"] = d.get("decision") if it["decided"] else it.get("rec", "check")
            if d.get("phrase"):
                it["phrase"] = d["phrase"]
                it["short"] = d.get("short") or d["phrase"]
            if it["kind"] == "speaker":
                sp = dict(it.get("speaker") or {})
                line = lines.get(sp.get("line_id"), {})
                sp["stage_name"] = sp.get("name")
                sp["name"] = line.get("name") if line else (d.get("name") or sp.get("name"))
                it["speaker"] = sp
                if line and not line.get("include"):
                    it["line_excluded"] = True          # out of the subtitles and of the counts
            pl = plc.get(it["id"])
            if pl:
                it["placement"] = pl
            out.append(it)
        return out

    def effective_items(self, review: dict | None = None, draft: dict | None = None) -> list[dict]:
        """Items with the writer's choices merged in (module doc).

        Adds ``final`` (include / exclude / check), ``decided`` (bool), and for the
        current text ``phrase``/``short``; speaker items get ``name``. A recommendation worked out
        again here keeps the stage's in ``rec_stage``. With ``draft`` an item whose root is in
        ``no_room`` is no longer 'already told'. Speaker items of lines that are out carry
        ``line_excluded``.
        """
        return self.compose(review, draft)["items"]

    def effective_lines(self, review: dict | None = None, draft: dict | None = None) -> list[dict]:
        """The dialogue lines with the writer's choices, and the lines the writer added, in film
        order (module doc; the keys are listed in ``compose``)."""
        return self.compose(review, draft)["lines"]

    def set_decision(self, item_id: str, **fields) -> dict:
        """Store a writer decision. ``fields``: decision, phrase, short, name, scope.

        A speaker item's ``name`` is the name of its line (``set_speaker``): ``scope`` ``line``
        (default), ``speaker`` (= ``person``: every line of the same person — by person, not by
        the label shown, 감사 3-m2), ``voice`` (same voice cluster, when lines carry ``voice``),
        and the scopes of ``cboe.people.SCOPES``.
        """
        with self._lock:
            review = self.review()
            eff = {it["id"]: it for it in self.effective_items(review)}
            if item_id not in eff:
                raise KeyError(item_id)
            scope = fields.pop("scope", "line") or "line"
            it = eff[item_id]
            changed = [item_id]
            if it["kind"] == "speaker" and "name" in fields:
                name = fields.pop("name")
                lid = (it.get("speaker") or {}).get("line_id")
                done = self._set_names(review, lid, name, scope)
                by_line = {(o.get("speaker") or {}).get("line_id"): o["id"] for o in eff.values() if o["kind"] == "speaker"}
                changed = list(dict.fromkeys([item_id] + [by_line[x] for x in done if x in by_line]))
                if not fields:
                    self.save_review(review)
                    return {"changed": changed, "lines": done}
            stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
            self._normalise(review, "items")              # decisions under today's ids first
            cur = dict(review["items"].get(item_id, {}))
            for k, v in fields.items():
                if v is None:
                    cur.pop(k, None)
                else:
                    cur[k] = v
            cur["at"] = stamp
            cur["fp"] = self.fingerprint(it)
            review["items"][item_id] = cur
            self.save_review(review)
            return {"changed": changed}

    def _normalise(self, review: dict, section: str) -> None:
        """Re-key ``review[section]`` by today's item ids, matched by fingerprint, so that a write
        under an id never lands on the decision of another candidate that used to have it.
        Decisions whose candidate is gone are kept under ``fp:<fingerprint>`` and come back
        when the candidate does."""
        items = self.items()
        stored = review.get(section, {})
        current = self._decisions(review, items, section)
        new, used = {}, set()
        for it in items:
            d = current.get(it["id"])
            if d:
                d = dict(d)
                d["fp"] = self.fingerprint(it)
                new[it["id"]] = d
                used.add(d["fp"])
        for d in stored.values():
            if isinstance(d, dict) and d.get("fp") and d["fp"] not in used:
                new[f"fp:{d['fp']}"] = d
        review[section] = new

    def set_placement(self, item_id: str, action: str | None, t: float | None = None) -> None:
        """Writer's fix for a '배치 확인 필요' item: move (to time t) or clear."""
        with self._lock:
            review = self.review()
            it = next((x for x in self.items() if x["id"] == item_id), None)
            self._normalise(review, "placement")
            review["placement"].pop(item_id, None)
            if action not in (None, "clear"):
                review["placement"][item_id] = {"action": action, "t": t,
                                                "fp": self.fingerprint(it) if it else None}
            self.save_review(review)

    # -------------------------------------------------------- line decisions
    def _window(self) -> tuple[float, float]:
        meta = self.meta
        t0 = _num(meta.get("t0")) or 0.0
        t1 = _num(meta.get("t1"))
        if t1 is None:
            t1 = _num(meta.get("duration"))
        return t0, (t1 if t1 is not None else float("inf"))

    def _span(self, start, end) -> tuple[float, float]:
        """A start and an end the writer gives: finite, start < end, inside the processed part."""
        s, e = _num(start), _num(end)
        if s is None or e is None:
            raise ReviewError("시작과 끝은 둘 다 초 단위 숫자여야 합니다")
        t0, t1 = self._window()
        if not (t0 <= s < e <= t1):
            raise ReviewError(f"시작은 끝보다 앞이고 처리 구간({t0:.1f}~{t1:.1f}초) 안이어야 합니다")
        return round(s, 3), round(e, 3)

    def _normalise_lines(self, review: dict, dlg: list[dict]) -> dict:
        """Re-key ``review["lines"]`` by today's line ids (by fingerprint); decisions whose line is
        gone (or that cannot be told apart from another line of the same words) stay under
        ``fp:<fingerprint>``. Every decision kept with a line gets the line's fingerprint and context
        of today. Returns the ``_line_index`` (``fps``, ``ctx``)."""
        cur, index = self._line_decisions(review, dlg)
        new, used = {}, {id(d) for d in cur.values()}
        for lid, d in cur.items():
            new[lid] = dict(d, fp=index["fps"][lid], ctx=index["ctx"][lid])
        for d in (review.get("lines") or {}).values():
            if isinstance(d, dict) and d.get("fp") and id(d) not in used:
                key, k = f"fp:{d['fp']}", 2
                while key in new:
                    key, k = f"fp:{d['fp']}~{k}", k + 1
                new[key] = d
        review["lines"] = new
        return index

    def _line_fields(self, ln: dict, cur: dict, fields: dict) -> dict:
        """``cur`` (a stored line decision) with ``fields`` applied and checked (None = back to the
        script / the default)."""
        cur = dict(cur)
        f = dict(fields)
        if "text_from" in f or "text" in f:
            src = f.pop("text_from", None)
            text = _norm(f.pop("text", None)) or None
            if src not in (None,) + TEXT_FROM:
                raise ReviewError(f"글의 출처는 {', '.join(TEXT_FROM)} 가운데 하나입니다")
            if src == "heard":
                text = _norm(ln.get("asr_text"))
                if not text:
                    raise Conflict("이 줄에서 들린 말이 없어 고를 수 없습니다")
            if src == "script" or (src is None and text is None) or text == _norm(ln.get("text")):
                cur.pop("text", None)
                cur.pop("text_from", None)
                if src == "script":
                    cur["text_from"] = "script"            # the writer chose the script's words
            else:
                cur["text"], cur["text_from"] = text, src or "writer"
        for k in ("include", "sung", "keep", "show_name"):
            if k in f:
                v = f.pop(k)
                if v is None:
                    cur.pop(k, None)
                elif isinstance(v, bool):
                    cur[k] = v
                else:
                    raise ReviewError(f"{k} 는 참·거짓이어야 합니다")
        for k in ("name", "language"):
            if k in f:
                v = f.pop(k)
                if v is None:
                    cur.pop(k, None)
                else:
                    cur[k] = _norm(v)                      # language "": spoken in Korean (no label)
                    if k == "name" and not cur[k]:
                        cur.pop(k)
        if "start" in f or "end" in f:
            s, e = f.pop("start", cur.get("start")), f.pop("end", cur.get("end"))
            if s is None and e is None:
                cur.pop("start", None)
                cur.pop("end", None)
            else:
                s = s if s is not None else ln.get("start")
                e = e if e is not None else ln.get("end")
                cur["start"], cur["end"] = self._span(s, e)
        if "breaks" in f:
            v = f.pop("breaks")
            if not v:
                cur.pop("breaks", None)
            else:
                n = len((_norm(cur.get("text")) or _norm(ln.get("text"))).split())
                try:
                    b = sorted({int(x) for x in v})
                except (TypeError, ValueError):
                    raise ReviewError("끊을 자리는 어절 번호 목록입니다") from None
                if any(x <= 0 or x >= n for x in b):
                    raise ReviewError(f"끊을 자리는 1~{n - 1} 사이 어절 번호입니다 (이 줄은 {n}어절)")
                cur["breaks"] = b
        if f:
            raise ReviewError(f"모르는 칸입니다: {', '.join(sorted(f))}")
        if cur.get("include") is True and (ln.get("status") in NO_TIME or ln.get("start") is None) \
                and not ("start" in cur and "end" in cur):
            raise Conflict("시각이 없는 줄입니다 · 시작과 끝을 정해야 넣을 수 있습니다")
        return cur

    def set_line(self, line_id: str, **fields) -> dict:
        """Store the writer's decision on one dialogue line (``LINE_FIELDS``; module doc).
        ``text_from`` "heard" takes the words heard over the line (``asr_text``), "script" goes back
        to the script; a ``text`` of the writer's own is "writer". KeyError for an unknown line,
        ReviewError / Conflict for a value that cannot be used."""
        with self._lock:
            review = self.review()
            dlg = self.dialogue()
            ln = next((x for x in dlg if x.get("id") == line_id), None)
            if ln is None:
                raise KeyError(line_id)
            if "show_name" in fields and any(it.get("kind") == "speaker" and (it.get("speaker") or {}).get("line_id")
                                             == line_id for it in self.items()):
                raise ReviewError("화자 항목이 있는 줄은 화자 항목에서 이름 표시를 정합니다")
            index = self._normalise_lines(review, dlg)
            cur = self._line_fields(ln, {k: v for k, v in review["lines"].get(line_id, {}).items()
                                         if k in LINE_FIELDS}, fields)
            if "name" in fields:
                self._drop_item_names(review, [line_id])
            self._store_line(review, line_id, index, cur)
            self.save_review(review)
            return {"line": line_id, "decision": cur}

    def _store_line(self, review: dict, line_id: str, index: dict, cur: dict) -> None:
        """Keep ``cur`` (the fields of a line decision) under ``line_id`` with the line's fingerprint
        and context (``_line_index``), or drop the decision when nothing is left."""
        if cur:
            review["lines"][line_id] = dict(cur, fp=index["fps"][line_id], ctx=index["ctx"][line_id],
                                            at=time.strftime("%Y-%m-%dT%H:%M:%S"))
        else:
            review["lines"].pop(line_id, None)

    def set_lines(self, ids: list[str], include: bool | None) -> dict:
        """Take several lines in or out at once (None: back to the default). Nothing is stored when
        one id is unknown (KeyError) or a line without a time would be taken in (Conflict)."""
        with self._lock:
            review = self.review()
            dlg = self.dialogue()
            by = {x.get("id"): x for x in dlg}
            ids = list(dict.fromkeys(ids))
            missing = [i for i in ids if i not in by]
            if missing:
                raise KeyError(", ".join(missing[:5]))
            index = self._normalise_lines(review, dlg)
            new, bad = {}, []
            for i in ids:
                try:
                    new[i] = self._line_fields(by[i], {k: v for k, v in review["lines"].get(i, {}).items()
                                                      if k in LINE_FIELDS}, {"include": include})
                except Conflict:
                    bad.append(i)
            if bad:
                raise Conflict(f"시각이 없는 줄은 시작과 끝을 정해야 넣을 수 있습니다: {', '.join(bad[:5])}")
            for i, cur in new.items():
                self._store_line(review, i, index, cur)
            self.save_review(review)
            return {"lines": ids}

    def _drop_item_names(self, review: dict, line_ids) -> None:
        """A name stored on a speaker item by an older version gives way to the line's name."""
        want = set(line_ids)
        items = {it["id"]: it for it in self.items() if it.get("kind") == "speaker"
                 and (it.get("speaker") or {}).get("line_id") in want}
        for iid, d in list((review.get("items") or {}).items()):
            if iid in items and isinstance(d, dict) and "name" in d:
                review["items"][iid] = {k: v for k, v in d.items() if k != "name"}

    def _set_names(self, review: dict, line_id: str, name, scope="line") -> list[str]:
        """Give the lines of ``scope`` (``cboe.people.SCOPES``, by person) the name ``name`` (None:
        back to the script's). Changes ``review``; returns the line ids."""
        from . import people as people_mod
        comp = self.compose(review)
        order = [{"id": ln["id"], "person": ln["name"], "scene_id": ln.get("scene_id"), "voice": ln.get("voice")}
                 for ln in comp["lines"]]
        targets = people_mod.scope_lines(order, line_id, scope)
        dlg = self.dialogue()
        index = self._normalise_lines(review, dlg)
        by = {x.get("id"): x for x in dlg}
        for lid in targets:
            if lid in by:
                cur = self._line_fields(by[lid], {k: v for k, v in review["lines"].get(lid, {}).items()
                                                  if k in LINE_FIELDS}, {"name": name})
                self._store_line(review, lid, index, cur)
            elif lid in (review.get("added") or {}):
                a = dict(review["added"][lid])
                if name:
                    a["name"] = _norm(name)
                else:
                    a.pop("name", None)
                review["added"][lid] = a
        self._drop_item_names(review, targets)
        return targets

    def set_speaker(self, line_id: str, name, scope: str = "line") -> dict:
        """The name of a line and, by ``scope``, of other lines of the same person: line ·
        from_here · before · scene · person (older: speaker) · voice (감사 3-10, 3-m2)."""
        with self._lock:
            review = self.review()
            known = {x.get("id") for x in self.dialogue()} | set(review.get("added") or {})
            if line_id not in known:
                raise KeyError(line_id)
            done = self._set_names(review, line_id, name, scope)
            self.save_review(review)
            return {"lines": done}

    # ------------------------------------------------------------ additions
    def _added_fields(self, kind: str, cur: dict, fields: dict) -> dict:
        from . import style
        a = dict(cur, kind=kind)
        f = dict(fields)
        if "start" in f or "end" in f:
            a["start"], a["end"] = self._span(f.pop("start", a.get("start")), f.pop("end", a.get("end")))
        for k in ("name", "language"):
            if k in f:
                v = f.pop(k)
                if v is None or (k == "name" and not _norm(v)):
                    a.pop(k, None)
                else:
                    a[k] = _norm(v)
        for k in ("sung", "show_name", "keep"):
            if k in f:
                v = f.pop(k)
                if v is None:
                    a.pop(k, None)
                elif isinstance(v, bool):
                    a[k] = v
                else:
                    raise ReviewError(f"{k} 는 참·거짓이어야 합니다")
        if "text" in f:
            a["text"] = _norm(f.pop("text"))
        if "phrase" in f:
            a["phrase"] = style.bracket(f.pop("phrase"))      # a sound or a note is a label: [일어로 말한다]
        if "breaks" in f:
            v = f.pop("breaks") or []
            n = len(_norm(a.get("text")).split())
            if any(not isinstance(x, int) or x <= 0 or x >= n for x in v):
                raise ReviewError(f"끊을 자리는 1~{n - 1} 사이 어절 번호입니다")
            a["breaks"] = sorted(set(v))
        if f:
            raise ReviewError(f"모르는 칸입니다: {', '.join(sorted(f))}")
        if a.get("start") is None or a.get("end") is None:
            raise ReviewError("시작과 끝이 필요합니다")
        if kind == "line" and not a.get("text"):
            raise ReviewError("넣을 대사 글이 필요합니다")
        if kind != "line" and not a.get("phrase"):
            raise ReviewError("넣을 문구가 필요합니다 (예: [음악이 멈춘다])")
        return a

    @staticmethod
    def _new_id(review: dict) -> str:
        nums = [int(k[1:]) for k in (review.get("added") or {}) if re.fullmatch(r"A\d+", k)]
        return f"A{max(nums, default=0) + 1:04d}"

    def add(self, kind: str, **fields) -> dict:
        """Something the writer adds: a line (kind "line": start, end, text, name, language, sung,
        show_name) or a sound or any other note (kind "sfx" | "music": start, end, phrase)."""
        if kind not in ADDED_KINDS:
            raise ReviewError(f"넣을 수 있는 것은 {', '.join(ADDED_KINDS)} 입니다")
        with self._lock:
            review = self.review()
            aid = self._new_id(review)
            review["added"][aid] = dict(self._added_fields(kind, {}, fields), at=time.strftime("%Y-%m-%dT%H:%M:%S"))
            self.save_review(review)
            return {"id": aid}

    def update_added(self, aid: str, **fields) -> dict:
        with self._lock:
            review = self.review()
            cur = (review.get("added") or {}).get(aid)
            if not isinstance(cur, dict):
                raise KeyError(aid)
            review["added"][aid] = dict(self._added_fields(cur.get("kind"), cur, fields),
                                        at=time.strftime("%Y-%m-%dT%H:%M:%S"))
            self.save_review(review)
            return {"id": aid}

    def delete_added(self, aid: str) -> dict:
        with self._lock:
            review = self.review()
            if aid not in (review.get("added") or {}):
                raise KeyError(aid)
            review["added"].pop(aid)
            self.save_review(review)
            return {"id": aid}

    def set_extra(self, extra_id: str, action: str, **fields) -> dict:
        """A '대본에 없는 말' (dialogue.json extras): "line" makes it a line the writer added (its
        heard words and times; ``fields`` may give text, name, language, sung, start, end),
        "ignore" sets it aside (and takes back a line made from it), "clear" undoes both."""
        with self._lock:
            review = self.review()
            x = next((e for e in self.extras() if e.get("id") == extra_id), None)
            if x is None:
                raise KeyError(extra_id)
            fp = extra_fp(x)
            made = [k for k, a in (review.get("added") or {}).items() if isinstance(a, dict) and a.get("extra_fp") == fp]
            for k, d in list(review["extras"].items()):
                if isinstance(d, dict) and d.get("fp") == fp:
                    review["extras"].pop(k)
            out = {"extra": extra_id, "added": None}
            if action == "line":
                if made:
                    raise Conflict(f"이미 대사로 넣었습니다 ({made[0]})")
                base = {"start": x.get("start"), "end": x.get("end"), "text": x.get("text")}
                base.update(fields)
                a = self._added_fields("line", {"extra": extra_id, "extra_fp": fp, "scene_id": x.get("scene_id")}, base)
                aid = self._new_id(review)
                review["added"][aid] = dict(a, at=time.strftime("%Y-%m-%dT%H:%M:%S"))
                out["added"] = aid
            elif action in ("ignore", "clear"):
                for k in made:
                    review["added"].pop(k)
                if action == "ignore":
                    review["extras"][extra_id] = {"fp": fp, "action": "ignore", "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
            else:
                raise ReviewError("할 일은 line · ignore · clear 가운데 하나입니다")
            self.save_review(review)
            return out

    # --------------------------------------------------------------- people
    def _person_fields(self, cur: dict, fields: dict, lines_by_id: dict) -> dict:
        from . import people as people_mod
        d = dict(cur)
        f = dict(fields)
        if "mode" in f:
            m = f.pop("mode")
            if m is None:
                d.pop("mode", None)
                d.pop("before", None)
            elif m in people_mod.MODES:
                d["mode"] = m
            else:
                raise ReviewError("표시 방식은 name(이름 그대로) 또는 generic(일반 표시) 입니다")
        if "before" in f:
            b = _norm(f.pop("before")).strip("[] ")
            if b:
                d["before"] = b
            else:
                d.pop("before", None)
        if "reveal" in f or "reveal_line" in f:
            r = f.pop("reveal", None) or ("line" if f.get("reveal_line") else "auto")
            lid = f.pop("reveal_line", None)
            if r not in people_mod.REVEALS:
                raise ReviewError("이름으로 바뀌는 줄은 auto · line · never 가운데 하나입니다")
            for k in ("reveal", "reveal_line", "reveal_fp", "reveal_ctx"):
                d.pop(k, None)
            if r == "line":
                if lid not in lines_by_id:
                    raise ReviewError(f"없는 줄입니다: {lid}")
                d.update(reveal="line", reveal_line=lid)
                if lines_by_id[lid].get("fp"):                  # a script line: found again by fingerprint
                    d.update(reveal_fp=lines_by_id[lid]["fp"], reveal_ctx=lines_by_id[lid].get("fp_ctx"))
            elif r == "never":
                d["reveal"] = "never"
        if f:
            raise ReviewError(f"모르는 칸입니다: {', '.join(sorted(f))}")
        if d.get("mode") == "generic" and not d.get("before"):
            raise ReviewError("일반 표시(예: 남자, 여자)를 적어야 합니다")
        return d

    def set_people(self, names: list[str], **fields) -> dict:
        """How people are labelled (module doc, ``cboe.people``): ``mode`` "name" | "generic" | None,
        ``before`` (the generic label), ``reveal`` "auto" | "line" | "never" and ``reveal_line``.
        Several people at once (role names: ``mode="name"``); nothing is stored when one is
        unknown (KeyError)."""
        with self._lock:
            review = self.review()
            comp = self.compose(review)
            known = {p["name"] for p in comp["people"]}
            names = list(dict.fromkeys(names))
            missing = [n for n in names if n not in known]
            if missing:
                raise KeyError(", ".join(missing[:5]))
            by = {ln["id"]: ln for ln in comp["lines"]}
            new = {n: self._person_fields(review["people"].get(n, {}), fields, by) for n in names}
            stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
            for n, d in new.items():
                d = {k: v for k, v in d.items() if k != "at"}
                if d:
                    review["people"][n] = dict(d, at=stamp)
                else:
                    review["people"].pop(n, None)
            self.save_review(review)
            return {"people": names}

    # ------------------------------------------------------------- composing
    def _line_decisions(self, review: dict, lines: list[dict]) -> tuple[dict, dict]:
        """({line id: decision}, ``_line_index(lines)``) — a stored decision applies to the line its
        fingerprint and context point to (``_pick_line``), at most one decision per line: the
        surest first, then the one stored under that line's id. A decision without a fingerprint
        goes by the id."""
        index = _line_index(lines)
        claims = []
        for key, d in (review.get("lines") or {}).items():
            if not isinstance(d, dict):
                continue
            if not d.get("fp"):
                if key in index["fps"]:
                    claims.append((9, True, key, key, d))
                continue
            lid, sure = _pick_line(d["fp"], d.get("ctx"), index)
            if lid:
                claims.append((sure, key == lid, key, lid, d))
        out, used = {}, set()
        for _sure, _own, key, lid, d in sorted(claims, key=lambda c: (-c[0], not c[1], c[2])):
            if lid not in out and key not in used:
                out[lid] = d
                used.add(key)
        return out, index

    def _line(self, ln: dict, idx: int, d: dict, item_name, asr) -> dict:
        """One dialogue line with the writer's decision ``d`` (module doc)."""
        status = ln.get("status") or "ok"                    # an older dialogue.json: every line is 'ok'
        s0, e0 = _num(ln.get("start")), _num(ln.get("end"))
        ps, pe = _num(d.get("start")), _num(d.get("end"))
        start = ps if ps is not None else s0
        end = pe if pe is not None else e0
        pinned = ps is not None and pe is not None
        timed = start is not None and end is not None and end >= start
        estimated = (ln.get("timing") in ESTIMATED or status == "unheard") and not pinned
        default = status not in NO_TIME and s0 is not None and e0 is not None
        include = d["include"] if isinstance(d.get("include"), bool) else default
        if include and not timed:
            include, excluded = False, (status if status in NO_TIME else "no_time")
        elif not include:
            excluded = "writer" if d.get("include") is False else (status if status in NO_TIME else "no_time")
        else:
            excluded = None
        script_text = _norm(ln.get("text"))
        text = _norm(d.get("text")) or script_text
        text_from = d.get("text_from") if d.get("text_from") in TEXT_FROM else ("writer" if d.get("text") else "script")
        if text == script_text:
            text_from = "script" if text_from == "writer" else text_from
        toks = text.split()
        words = ln.get("words") if isinstance(ln.get("words"), list) else []
        retext, moved = text != script_text, (start, end) != (s0, e0)
        if timed and (retext or moved):
            same = len(words) == len(toks) and all(isinstance(w, dict) and _num(w.get("s")) is not None
                                                   and _num(w.get("e")) is not None for w in words)
            if not retext and same and s0 is not None and e0 is not None and e0 > s0:
                words = _mapped(words, s0, e0, start, end)       # the writer moved the line: its words go with it
            else:
                hw = heard_words(toks, asr(), s0 if s0 is not None else start, e0 if e0 is not None else end) \
                    if text_from == "heard" else None
                if hw and not pinned:                            # what was heard, where it was heard
                    words, start, end = hw, hw[0]["s"], max(w["e"] for w in hw)
                elif hw:
                    words = _mapped(hw, hw[0]["s"], max(w["e"] for w in hw), start, end)
                else:                                            # the line span shared out by letters
                    words = even_words(toks, start, end)
        out = dict(ln)
        out.update(idx=idx, origin="script", status=status, script_text=script_text, text=text, text_from=text_from,
                   start=start, end=end, pinned=pinned, estimated=bool(estimated), include=include,
                   excluded=excluded, decided=isinstance(d.get("include"), bool), words=words,
                   name=_norm(d.get("name")) or item_name or ln.get("speaker") or None,
                   breaks=list(d.get("breaks") or []), keep=bool(d.get("keep")), show_name_set=d.get("show_name"))
        if "language" in d:
            out["language"] = d["language"] or None
        if "sung" in d:
            out["sung"] = bool(d["sung"])
        out["decision"] = {k: d[k] for k in LINE_FIELDS if k in d}
        return out

    def _added(self, aid: str, a: dict, extras_by_fp: dict) -> dict:
        """A line or a sound the writer added, as a line (kind "line") or an item."""
        start, end = _num(a.get("start")), _num(a.get("end"))
        if a.get("kind") == "line":
            text = _norm(a.get("text"))
            x = extras_by_fp.get(a.get("extra_fp")) or {}
            xw = [w for w in x.get("words") or [] if isinstance(w, dict)]
            toks = text.split()
            if x and _norm(x.get("text")) == text and len(xw) == len(toks) and start == _num(x.get("start")):
                words = [{"w": t, "s": w["s"], "e": w["e"]} for t, w in zip(toks, xw)]
            else:
                words = even_words(toks, start, end) if start is not None and end is not None else []
            return {"id": aid, "idx": None, "origin": "heard" if a.get("extra") else "writer", "status": "ok",
                    "timing": "writer", "speaker": None, "text": text, "script_text": None, "text_from": "writer"
                    if not a.get("extra") else "heard", "start": start, "end": end, "pinned": True,
                    "estimated": False, "include": True, "excluded": None, "decided": True, "words": words,
                    "name": _norm(a.get("name")) or None, "language": a.get("language") or None,
                    "sung": bool(a.get("sung")), "scene_id": a.get("scene_id") or x.get("scene_id"),
                    "breaks": list(a.get("breaks") or []), "keep": bool(a.get("keep")),
                    "show_name_set": a.get("show_name"), "extra": a.get("extra"), "decision": {}}
        phrase = _norm(a.get("phrase")) or None
        return {"id": aid, "kind": a.get("kind"), "origin": "writer", "onset": start, "t0": start,
                "t1": end if end is not None else start, "phrase": phrase, "short": phrase, "rec": "include",
                "final": "include", "decided": True, "reason": "작가가 넣음", "evidence": [], "phrases": [],
                "flags": [], "added": True}

    def compose(self, review: dict | None = None, draft: dict | None = None) -> dict:
        """Everything merged (module doc): ``{"items", "lines", "extras", "added", "people",
        "scenes"}``. ``draft``: the placement result of this composition — with it, items whose
        root went to ``no_room`` are 확인 필요 again and lines carry their cells.

        A line: the dialogue.json keys plus ``idx`` (script order; None for an added line),
        ``seq`` (film order), ``origin`` script | heard | writer, ``status`` (ok when missing),
        ``text`` (what is shown) / ``script_text`` / ``text_from``, ``start`` ``end`` ``pinned``
        ``estimated`` ``include`` ``excluded`` (absent | out_of_range | writer | no_time)
        ``decided`` ``name`` (who speaks: the person) ``label_name`` (what the label shows)
        ``name_early`` ``show_name`` ``speaker_item`` ``breaks`` ``keep`` ``decision``."""
        from . import people as people_mod
        from .stages import recommend
        review = review if review is not None else self.review()
        dj = read_json(self.path("dialogue.json"), {}) or {}
        dlg = [ln for ln in dj.get("lines", []) if isinstance(ln, dict) and ln.get("id")]
        extras = [x for x in dj.get("extras", []) or [] if isinstance(x, dict)]
        items = self.items()
        script = read_json(self.path("script.json"), {}) or {}
        scenes = self.scenes()
        cache: dict = {}

        def asr():
            if "w" not in cache:
                cache["w"] = (read_json(self.path("asr.json"), {}) or {}).get("words", []) or []
            return cache["w"]

        item_dec = self._decisions(review, items, "items")
        sp_of = {(it.get("speaker") or {}).get("line_id"): it for it in items if it.get("kind") == "speaker"}
        line_dec, lindex = self._line_decisions(review, dlg)
        lines = []
        for idx, ln in enumerate(dlg):
            it = sp_of.get(ln["id"])
            iname = _norm(item_dec.get(it["id"], {}).get("name")) if it else None
            e = self._line(ln, idx, line_dec.get(ln["id"], {}), iname or None, asr)
            e["fp"], e["fp_ctx"] = lindex["fps"][ln["id"]], lindex["ctx"][ln["id"]]
            e["speaker_item"] = it["id"] if it else None
            lines.append(e)
        # what the writer added; extras that became a line or were set aside
        x_fps = {extra_fp(x): x for x in extras}
        added_lines, added_items, added_rows = [], [], []
        for aid, a in sorted((review.get("added") or {}).items()):
            if not isinstance(a, dict) or a.get("kind") not in ADDED_KINDS:
                continue
            obj = self._added(aid, a, x_fps)
            (added_lines if a["kind"] == "line" else added_items).append(obj)
            added_rows.append(dict(a, id=aid))
        lines += added_lines
        _film_order(lines)
        _scene_of_added(lines)
        by_line = {ln["id"]: ln for ln in lines}
        eff = self._merge_items(review, items, by_line) + added_items
        _speaker_runs(lines, eff, recommend.decide_speaker)
        # labels: generic before the name is told, numbers per scene (cboe.people)
        shown = [ln for ln in lines if ln["include"]]
        pdec = {k: v for k, v in (review.get("people") or {}).items() if isinstance(v, dict)}
        for name, dd in pdec.items():                       # a reveal line chosen by the writer, by fingerprint
            if dd.get("reveal") == "line" and dd.get("reveal_fp"):
                hit, _ = _pick_line(dd["reveal_fp"], dd.get("reveal_ctx"), lindex)
                pdec[name] = dict(dd, reveal_line=hit)          # None: that line cannot be told any more
        disp = people_mod.display([{"id": ln["id"], "person": ln["name"], "scene_id": ln.get("scene_id"),
                                    "show": ln["show_name"], "text": ln["text"]} for ln in shown], pdec)
        early_lines = {p["early"]["line"] for p in disp["people"].values() if p["early"]}
        for ln in lines:
            ln["label_name"] = disp["labels"].get(ln["id"], ln["name"]) if ln["include"] else ln["name"]
            ln["generic"] = bool(disp["generic"].get(ln["id"]))
            ln["name_early"] = ln["id"] in early_lines
        _followers(eff, None)                              # roots taken as placed; finish() knows better
        # extras: open, made a line, or set aside
        x_dec = review.get("extras") or {}
        made = {a["extra_fp"]: a["id"] for a in added_rows if a.get("kind") == "line" and a.get("extra_fp")}
        ignored = {d.get("fp") for d in x_dec.values() if isinstance(d, dict) and d.get("action") == "ignore"}
        ex_rows = []
        for x in extras:
            fp = extra_fp(x)
            row = {k: v for k, v in x.items() if k != "words"}
            row.update(fp=fp, added=made.get(fp), state="line" if fp in made else "ignored" if fp in ignored else "open")
            ex_rows.append(row)
        # people: the table, what was found, the writer's decisions, line counts
        names = [ln["name"] for ln in lines if ln.get("name")]
        count: dict = {}
        for ln in shown:
            for n in people_mod.parts(ln["name"]):
                count[n] = count.get(n, 0) + 1
        prow = []
        for p in people_mod.table(script.get("people") or (), names):
            info = disp["people"].get(p["name"]) or {}
            told = info.get("told")
            prow.append(dict(p, lines=count.get(p["name"], 0), told=told, from_line=info.get("from_line"),
                             early=info.get("early"), mode=info.get("mode"), before=info.get("before"),
                             decision=pdec.get(p["name"])))
        comp = {"items": eff, "lines": lines, "extras": ex_rows, "added": added_rows, "people": prow,
                "scenes": scenes, "review": review}
        return self.finish(comp, draft) if draft is not None else comp

    def finish(self, comp: dict, draft: dict) -> dict:
        """``comp`` after placement: an item whose root went to ``no_room`` is 확인 필요 again, and
        every line carries its ``cells`` and ``no_room`` (a guessed line squeezed out, 2-m1)."""
        _followers(comp["items"], draft)
        self._attach(comp, draft)
        return comp

    @staticmethod
    def _attach(comp: dict, draft: dict) -> None:
        cells: dict = {}
        for c in draft.get("cells") or []:
            for r in c.get("refs") or []:
                cells.setdefault(r, []).append(c["id"])
        nr = {e.get("line"): e for e in draft.get("no_room") or [] if e.get("line")}
        for ln in comp["lines"]:
            ln["cells"] = cells.get(ln["id"], [])
            ln["no_room"] = nr.get(ln["id"])

    # -------------------------------------------------------------- placement
    def placement_inputs(self, review: dict | None = None, comp: dict | None = None) -> tuple[list[dict], list[dict]]:
        """(lines, sounds) for ``placement.place``: the lines that are in, with the label to show,
        and the included sounds (a sound the script writes down carries ``estimated``)."""
        comp = comp or self.compose(review)
        lines = []
        for ln in comp["lines"]:
            if not ln["include"]:
                continue
            pl = dict(ln)
            pl["name"] = ln.get("label_name")
            pl["person"] = ln.get("name")
            if not ln["estimated"]:                         # the writer fixed the time: not a guess any more
                if pl.get("status") in ("unheard",) + NO_TIME:
                    pl["status"] = "ok"
                if pl.get("timing") in ESTIMATED:
                    pl["timing"] = "writer"
            for k in ("breaks", "keep"):
                if not pl.get(k):
                    pl.pop(k, None)
            lines.append(pl)
        sounds = []
        for it in comp["items"]:
            if it["kind"] in ("sfx", "music") and it["final"] == "include":
                pl = it.get("placement") or {}
                if pl.get("action") == "exclude":
                    continue
                s = {"id": it["id"], "kind": it["kind"], "onset": it["onset"],
                     "t1": it.get("t1", it["onset"]), "phrase": it.get("phrase"),
                     "short": it.get("short") or it.get("phrase"),
                     "source_line": it.get("source_line"),
                     "move_to": pl.get("t") if pl.get("action") == "move" else None}
                if it.get("estimated"):
                    s["estimated"] = True
                sounds.append(s)
        return lines, sounds

    def recompute(self, comp: dict | None = None) -> dict:
        from . import placement, style
        with self._lock:
            lines, sounds = self.placement_inputs(comp=comp)
            meta = self.meta
            # the limits of this work folder's profile (project.json "profile": adult | children)
            draft = placement.place(lines, sounds, self.cuts(), float(meta.get("fps") or 25.0),
                                    window=(meta.get("t0", 0.0), meta.get("t1")),
                                    settings=style.for_project(meta).settings())
            write_json(self.path("draft.json"), draft)
            return draft

    def draft(self) -> dict:
        d = read_json(self.path("draft.json"))
        return d if d is not None else self.recompute()

    # ----------------------------------------------------------------- export
    def export_check(self, comp: dict | None = None, draft: dict | None = None) -> dict:
        """What is left before exporting. ``guide`` is the result of the independent guide check
        (``cboe.check.run``: clause by clause, must / should) on the current draft, with the
        draft's frame rate, the shot changes and the profile of this work folder; ``flags`` lists
        the cells placement marked (timing_estimated, overlap, flash).

        ``lines`` counts the dialogue lines still to look at: ``differs_open`` (the script and what
        was heard differ and the writer has not chosen), ``unheard`` (in, at a guessed time),
        ``absent`` / ``out_of_range`` (out: not in the film / outside the processed part),
        ``writer_excluded``, ``no_room`` (no cell: a guessed line squeezed between two others).
        ``extras_open``: '대본에 없는 말' not decided yet. ``names_early``: people whose name is
        shown before it is told (``{"person", "line", "told"}``; told None = never called)."""
        from . import check, style
        draft = draft if draft is not None else self.draft()
        profile = style.for_project(self.meta).profile
        if draft.get("notation") != style.NOTATION or draft.get("profile") != profile:
            comp, draft = None, self.recompute()  # written with an older notation ('(이름)') or another profile
        comp = comp if comp is not None else self.compose(draft=draft)
        eff = [it for it in comp["items"] if not it.get("line_excluded")]
        guide = check.run(draft, cuts=self.cuts(), profile=profile)
        flags: dict = {}
        for c in draft.get("cells", []):
            for name in c.get("flags") or []:
                flags.setdefault(name, []).append(c["id"])
        sounds = [it for it in eff if it["kind"] in ("sfx", "music")]
        # sounds left at 확인 필요 are not captioned; speaker lines left at 확인 필요 are still
        # exported, only without a name, so they are reported apart
        pending = [it["id"] for it in sounds if it["final"] == "check"]
        check_speaker = [it["id"] for it in eff if it["kind"] == "speaker" and it["final"] == "check"]
        undecided_ex = [it["id"] for it in sounds if it["final"] == "exclude" and not it["decided"]]
        viol = [c for c in draft.get("cells", []) if (c.get("check") or {}).get("violations")]
        # an included sound whose caption is still the placeholder '[소리]' must be named first
        unnamed = [it["id"] for it in eff if it["kind"] == "sfx" and it["final"] == "include"
                   and (it.get("phrase") or "").strip() in ("[소리]", "")]
        inl = [ln for ln in comp["lines"] if ln["include"]]
        lines = {"differs_open": [ln["id"] for ln in inl if ln["status"] == "differs"
                                  and not {"text", "text_from"} & set(ln.get("decision") or {})],
                 "unheard": [ln["id"] for ln in inl if ln["status"] == "unheard" and ln["estimated"]],
                 "absent": [ln["id"] for ln in comp["lines"] if ln["excluded"] == "absent"],
                 "out_of_range": [ln["id"] for ln in comp["lines"] if ln["excluded"] == "out_of_range"],
                 "writer_excluded": [ln["id"] for ln in comp["lines"] if ln["excluded"] == "writer"],
                 "no_room": [e for e in draft.get("no_room", []) if e.get("line")]}
        early = [{"person": p["name"], "line": p["early"]["line"], "told": p["early"]["told"]}
                 for p in comp["people"] if p.get("early")]
        return {"check": pending, "check_speaker": check_speaker,
                "no_room": [e for e in draft.get("no_room", []) if not e.get("line")],
                "exclude_recommended": undecided_ex, "unnamed": unnamed,
                "violations": [{"cell": c["id"], "violations": c["check"]["violations"]} for c in viol],
                "guide": guide, "flags": flags, "profile": profile, "lines": lines,
                "extras_open": [x["id"] for x in comp["extras"] if x["state"] == "open"], "names_early": early}

    def export(self, fmt: str = "srt", report: bool = False):
        """Write the draft as ``fmt`` (srt | vtt | ttml) into the work folder's ``export``.
        Returns the path, or with ``report=True`` the whole result of ``export.write``
        (``{"path", "format", "cells", "check"}``: the guide check of what was written)."""
        import re

        from . import export as ex
        draft = self.recompute()
        fmt = str(fmt or "srt").lower().lstrip(".")
        title = re.sub(r'[\\/:*?"<>|]+', "_", self.meta.get("title") or self.work.name).strip() or "export"
        out = self.path("export") / f"{title}.{fmt}"
        res = ex.write(draft["cells"], out, fmt, fps=draft.get("fps"), cuts=self.cuts(),
                       profile=draft.get("profile"), title=self.meta.get("title") or self.work.name)
        return res if report else res["path"]


# --------------------------------------------------------------------- what is worked out again
def _film_order(lines: list[dict]) -> None:
    """Sort ``lines`` into film order and number them (``seq``): by start; a script line without a
    time right after the script line before it; an added line after a script line of the same start."""
    last = float("-inf")
    for ln in sorted((x for x in lines if x.get("idx") is not None), key=lambda x: x["idx"]):
        if ln.get("start") is not None:
            last = float(ln["start"])
        ln["_k"] = (last, 0, ln["idx"], "")
    for ln in lines:
        if ln.get("idx") is None:
            ln["_k"] = (float(ln["start"]) if ln.get("start") is not None else float("-inf"), 1, 0, ln["id"])
    lines.sort(key=lambda x: x["_k"])
    for i, ln in enumerate(lines):
        ln["seq"] = i
        ln.pop("_k", None)


def _scene_of_added(lines: list[dict]) -> None:
    """An added line without a scene takes the scene of the line before it in the film."""
    scene = None
    for ln in lines:
        if ln.get("idx") is None and not ln.get("scene_id"):
            ln["scene_id"] = scene
        elif ln.get("scene_id"):
            scene = ln["scene_id"]


def _speaker_runs(lines: list[dict], eff: list[dict], decide) -> None:
    """``show_name`` of every line, and '같은 화자가 이어 말함' worked out again (module doc, 감사 3-07).
    ``decide`` is ``recommend.decide_speaker``. A line the writer added, or one the writer brought
    back, has no speaker item: its name is shown unless the writer said not to (``show_name``)."""
    by_id = {it["id"]: it for it in eff}
    last = None
    for ln in lines:
        if not ln["include"]:
            ln["show_name"] = False
            continue
        person = ln.get("name")
        it = by_id.get(ln.get("speaker_item")) if ln.get("speaker_item") else None
        cont = bool(last is not None and person and last["person"] == person and last["ident"]
                    and last["scene"] == ln.get("scene_id") and last["source"] == ln.get("source"))
        if it is None:
            # a line the writer added or brought back (no speaker item: it had no time); any other
            # line without one is shown without a name, as before
            want = ln.get("show_name_set")
            default = ln.get("idx") is None or ln.get("status") in NO_TIME
            ln["show_name"] = bool(person) and (want if isinstance(want, bool) else default)
            ident, root = ln["show_name"], ln["id"]
        else:
            sp = it["speaker"]
            if not it["decided"]:
                rec, reason = it.get("rec", "check"), it.get("reason")
                s2 = dict(it, speaker=dict(sp, estimated=ln["estimated"]))
                stage_cont = bool(it.get("follows")) or str(reason or "").startswith(CONTINUES)
                if stage_cont or bool(sp.get("stage_name")) != bool(sp.get("name")):
                    rec, reason, _ = decide(s2, None)        # the stage's run, or a name the script did not give
                follows = None
                if cont and decide(s2, last)[0] == "exclude" and sp.get("face") == "not_visible":
                    rec, follows = "exclude", last["root"]
                    reason = f"{CONTINUES} · " + ("앞 줄에 이름 표시" if last["shown"] else "앞 줄에서 이미 식별됨")
                if (rec, reason, follows) != (it.get("rec"), it.get("reason"), it.get("follows")):
                    it["rec_stage"] = it.get("rec_stage", it.get("rec"))
                    it["rec"], it["reason"] = rec, reason
                    if follows:
                        it["follows"] = follows
                    else:
                        it.pop("follows", None)
                it["final"] = rec
            ln["show_name"] = it["final"] == "include" and bool(person)
            ident = bool(person) and it["final"] in ("include", "exclude")
            root = last["root"] if cont and it["final"] == "exclude" and it.get("follows") else it["id"]
        last = {"person": person, "scene": ln.get("scene_id"), "source": ln.get("source"), "ident": ident,
                "shown": ln["show_name"], "root": root}


def _followers(eff: list[dict], draft: dict | None) -> None:
    """'앞 자막에서 이미 알림' worked out again (module doc, 감사 6-m2 · 9-x1): an item that ``follows``
    a root is excluded as told only when the root goes out (final include, not in ``no_room``);
    otherwise it is 확인 필요. Items the writer decided are left alone."""
    nr = {e.get("item") for e in (draft or {}).get("no_room") or []}
    by_id = {it["id"]: it for it in eff}
    rows = sorted((it for it in eff if it.get("kind") in ("sfx", "music") and it.get("follows")),
                  key=lambda it: float(it.get("onset") or 0.0))
    for it in rows:
        if it["decided"]:
            continue
        root = by_id.get(it["follows"])
        told = bool(root) and root.get("final") == "include" and root["id"] not in nr \
            and (root.get("placement") or {}).get("action") != "exclude"
        rec, reason = it.get("rec"), it.get("reason")
        if told and rec != "exclude":
            rec = "exclude"
            reason = "같은 분위기 음악이 이어짐 · 앞 자막에서 이미 알림" if it["kind"] == "music" else "앞 자막에서 이미 알림"
        elif not told and (rec != "check" or "이미 알림" in str(reason or "")):
            rec = "check"
            reason = (f"앞 후보({root['id']})가 자막에 나가지 않음 · 확인 필요" if root else "앞 후보가 없음 · 확인 필요")
        if (rec, reason) != (it.get("rec"), it.get("reason")):
            it["rec_stage"] = it.get("rec_stage", it.get("rec"))
            it["rec"], it["reason"] = rec, reason
        it["final"] = it["rec"]
