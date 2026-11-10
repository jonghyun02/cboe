# -*- coding: utf-8 -*-
"""장면 먼저 정렬·줄 상태·대본 대조 (cboe.scenes, cboe.stages.align) on made-up films.

The script is made up by the program: 30 scenes of lines built from a word list of random
syllables. A "film" is the script's lines (some scenes cut, moved, with ad-libs, ...) spoken at
0.13 s per letter; its ASR is those words with their times (optionally with letters changed).
No model, no real script text."""
from __future__ import annotations

import random

import numpy as np
import pytest

from cboe import scenes
from cboe.stages import align
from cboe.state import Project
from cboe.util import read_json, write_json

# syllables that are not vocalisation syllables (align.VOCAL_SYL), so no made-up line is a scream
SYL = [c for c in "가나다라마바사자차카타파거너더러머버서저처커터퍼고노도로모보소조초코토포구누두루무부수주추"
       "투푸기니디리미비시지치키티피개내대래매배새재채태패게네데레메베세제체테페" if c not in align.VOCAL_SYL]
SCENE_GAP, LINE_GAP = 3.0, 0.4


def _vocab(rng, n, lo=2, hi=4):
    out = set()
    while len(out) < n:
        out.add("".join(rng.choice(SYL) for _ in range(rng.randint(lo, hi))))
    return sorted(out)


def make_script(seed=1, n_scenes=30):
    """A version-2 shooting script: scene 7 has one two-letter line, scene 12 no dialogue."""
    rng = random.Random(seed)
    vocab = _vocab(rng, 700)
    sc, lines = [], []
    for k in range(1, n_scenes + 1):
        sid = f"SC{k:03d}"
        sc.append({"id": sid, "number": str(k), "heading": f"{k}. 장소{k} / 낮", "place": f"장소{k}", "time": "낮"})
        n = 0 if k == 12 else 1 if k == 7 else rng.randint(3, 9)
        for _ in range(n):
            text = rng.choice(SYL) + rng.choice(SYL) if k == 7 else \
                " ".join(rng.choice(vocab) for _ in range(rng.randint(2, 7))) + rng.choice([".", "?", "!", ""])
            lines.append({"id": f"L{len(lines) + 1:04d}", "speaker": rng.choice(["가람", "나래", "다솜"]), "text": text,
                          "raw": text, "scene_id": sid, "language": None, "sung": False, "source": None,
                          "delivery": [], "vocal": [], "directions": []})
    return {"version": 2, "format": "screenplay", "timed": False, "characters": ["가람", "나래", "다솜"],
            "scenes": sc, "elements": [], "lines": lines}


def scene_lines(doc, k):
    return [ln for ln in doc["lines"] if ln["scene_id"] == f"SC{k:03d}"]


def speak(film, t=10.0):
    """film: [(uid, text, scene)] in film order -> (asr words, vad, truth {uid: (start, end)}, end)."""
    words, vad, truth, prev = [], [], {}, None
    for uid, text, sc in film:
        if prev is not None and sc != prev:
            t += SCENE_GAP
        prev = sc
        s0 = t
        for w in text.split():
            d = align.SEC_PER_CHAR * max(len(align.norm_chars(w)), 1)
            words.append({"w": w, "s": round(t, 3), "e": round(t + d, 3)})
            t += d + 0.03
        truth[uid] = (round(s0, 3), round(t - 0.03, 3))
        vad.append([round(s0, 3), round(t, 3)])
        t += LINE_GAP
    return words, vad, truth, t + 5.0


def film_of(doc, order=None, cut=(), extra=None):
    order = order or list(range(1, len(doc["scenes"]) + 1))
    out = []
    for k in order:
        if k in cut:
            continue
        for ln in scene_lines(doc, k):
            out.append((ln["id"], ln["text"], k))
            if extra and ln["id"] in extra:
                out.append(extra[ln["id"]] + (k,))
    return out


def garble(words, rate, seed=5):
    """Change each letter of the ASR words with probability ``rate``."""
    rng = random.Random(seed)
    out = []
    for w in words:
        cs = [rng.choice(SYL) if "가" <= c <= "힣" and rng.random() < rate else c for c in w["w"]]
        out.append({**w, "w": "".join(cs)})
    return out


def run(tmp_path, doc, words, vad, t_end, t0=0.0, t1=None):
    write_json(tmp_path / "project.json", {"t0": t0, "t1": t_end if t1 is None else t1, "duration": t_end, "fps": 25.0})
    write_json(tmp_path / "script.json", doc)
    lo, hi = t0, t_end if t1 is None else t1
    write_json(tmp_path / "asr.json", {"words": [w for w in words if lo <= w["s"] and w["e"] <= hi],
                                       "vad": [v for v in vad if lo <= v[0] and v[1] <= hi]})
    for f in ("dialogue.json", "scenes.json"):
        (tmp_path / f).unlink(missing_ok=True)
    res = align.run(Project(tmp_path), {})
    d = read_json(tmp_path / "dialogue.json")
    return d, read_json(tmp_path / "scenes.json")["scenes"], res


def placed(lines, truth, ids=None, tol=1.0):
    """Share of the lines (``ids``, default every line in the film) matched within ``tol`` s of where they are said."""
    ids = list(truth) if ids is None else list(ids)
    by = {ln["id"]: ln for ln in lines}
    ok = [i for i in ids if by[i]["status"] in ("ok", "differs") and abs(by[i]["start"] - truth[i][0]) <= tol]
    return len(ok) / max(len(ids), 1)


@pytest.fixture(scope="module")
def doc():
    return make_script()


def test_film_as_written(tmp_path, doc):
    words, vad, truth, t_end = speak(film_of(doc))
    d, sc, res = run(tmp_path, doc, words, vad, t_end)
    lines = d["lines"]
    assert res["mode"] == "scenes" and len(lines) == len(doc["lines"])
    assert placed(lines, truth) >= 0.99
    assert not [ln for ln in lines if ln["status"] in ("absent", "out_of_range", "differs")]
    assert d["extras"] == []
    by = {s["id"]: s for s in sc}
    assert by["SC012"]["status"] == "unknown" and by["SC012"]["lines"] == []          # no dialogue: between neighbours
    assert by["SC011"]["end"] <= by["SC012"]["start"] <= by["SC012"]["end"] <= by["SC013"]["start"]
    found = [s for s in sc if s["status"] == "found"]
    assert len(found) >= 28 and all(s["start"] is not None and s["score"] is not None for s in found)
    # every line keeps what the script says about it
    for ln, src in zip(lines, doc["lines"]):
        assert (ln["id"], ln["scene_id"], ln["raw"], ln["language"], ln["sung"], ln["source"], ln["delivery"], ln["vocal"]) == \
            (src["id"], src["scene_id"], src["raw"], src["language"], src["sung"], src["source"], src["delivery"], src["vocal"])
        assert ln["status"] != "ok" or ln["asr_text"].split() == src["text"].split()


def test_lines_before_the_first_scene_that_are_not_heard_are_absent(tmp_path, doc):
    """A cover page read as dialogue (no scene) is not placed on a guess (감사 1-03 '내꺼')."""
    cover = [{"id": "L9001", "speaker": "각본", "text": "가나다 라마바"}, {"id": "L9002", "speaker": "제작", "text": "사자"}]
    d2 = {**doc, "lines": [{**c, "raw": c["text"], "scene_id": None} for c in cover] + doc["lines"]}
    words, vad, truth, t_end = speak([("N0", "타파하 코토포 무부수", 0)] + film_of(doc))   # someone speaks first
    d, _, _ = run(tmp_path, d2, words, vad, t_end)
    assert [ln["status"] for ln in d["lines"][:2]] == ["absent", "absent"]
    assert [x["text"] for x in d["extras"]] == ["타파하 코토포 무부수"]
    del truth["N0"]
    assert placed(d["lines"], truth) >= 0.99


def test_cut_scenes_are_absent(tmp_path, doc):
    cut = {4, 11, 19, 25}
    words, vad, truth, t_end = speak(film_of(doc, cut=cut))
    d, sc, _ = run(tmp_path, doc, words, vad, t_end)
    gone = [ln for ln in d["lines"] if ln["id"] not in truth]
    assert len(gone) >= 15
    assert sum(ln["status"] == "absent" for ln in gone) / len(gone) >= 0.95
    assert all(ln["start"] is None and ln["end"] is None for ln in gone if ln["status"] == "absent")
    assert placed(d["lines"], truth) >= 0.95
    by = {s["id"]: s for s in sc}
    assert all(by[f"SC{k:03d}"]["status"] == "absent" for k in cut)


def test_moved_scenes_are_found_where_they_are(tmp_path, doc):
    order = [k for k in range(1, 31) if k not in (15, 16)]
    order[order.index(24) + 1:order.index(24) + 1] = [15, 16]
    words, vad, truth, t_end = speak(film_of(doc, order=order))
    d, sc, _ = run(tmp_path, doc, words, vad, t_end)
    moved = [ln["id"] for k in (15, 16) for ln in scene_lines(doc, k)]
    assert placed(d["lines"], truth, moved) >= 0.9
    assert placed(d["lines"], truth) >= 0.95
    by = {s["id"]: s for s in sc}
    assert by["SC015"]["how"] == "moved" and by["SC016"]["how"] == "moved"
    assert by["SC015"]["start"] > by["SC024"]["start"]


def test_ad_libs_become_extras(tmp_path, doc):
    rng = random.Random(9)
    other = _vocab(rng, 60, 3, 4)                         # words the script never uses
    picks = rng.sample([ln["id"] for ln in doc["lines"] if ln["scene_id"] != "SC007"], 12)
    extra = {i: (f"AD{n:02d}", " ".join(rng.sample(other, 3))) for n, i in enumerate(picks)}
    words, vad, truth, t_end = speak(film_of(doc, extra=extra))
    d, _, _ = run(tmp_path, doc, words, vad, t_end)
    ads = [truth[u] for u, _ in extra.values()]
    covered = sum(any(x["start"] - 0.3 <= (a + b) / 2 <= x["end"] + 0.3 for x in d["extras"]) for a, b in ads)
    assert covered >= 11
    assert len(d["extras"]) <= 14
    script_ids = [ln["id"] for ln in doc["lines"]]
    assert placed(d["lines"], truth, script_ids) >= 0.95
    assert all(x["id"].startswith("X") and x["scene_id"] and x["words"] for x in d["extras"])


def test_partial_window_puts_other_scenes_out_of_range(tmp_path, doc):
    words, vad, truth, t_end = speak(film_of(doc))
    inside = [ln["id"] for k in range(9, 17) for ln in scene_lines(doc, k)]
    t0, t1 = truth[inside[0]][0] - 1.0, truth[inside[-1]][1] + 1.0
    d, sc, _ = run(tmp_path, doc, words, vad, t_end, t0, t1)
    outside = [ln for ln in d["lines"] if ln["id"] not in inside]
    assert sum(ln["status"] == "out_of_range" for ln in outside) / len(outside) >= 0.95
    assert all(t0 - 0.01 <= ln["start"] <= t1 for ln in d["lines"] if ln["start"] is not None)
    assert placed(d["lines"], truth, inside) >= 0.9
    by = {s["id"]: s for s in sc}
    assert by["SC001"]["status"] == by["SC030"]["status"] == "out_of_range"


def test_partial_window_edge_inside_a_scene(tmp_path, doc):
    """review B1: a window that ends one line into a long scene (or starts one line before its
    end) keeps that heard line; the rest of the scene is out of range and nothing is an extra."""
    words, vad, truth, t_end = speak(film_of(doc))
    longs = [k for k in range(2, 30) if len(scene_lines(doc, k)) >= 8][:3]
    assert len(longs) == 3
    for k in longs:
        ls = [ln["id"] for ln in scene_lines(doc, k)]
        for side, t0, t1, inside in (("tail", 0.0, truth[ls[0]][1] + 0.3, ls[:1]),
                                     ("head", truth[ls[-1]][0] - 0.3, None, ls[-1:])):
            w = tmp_path / f"{side}{k}"
            w.mkdir()
            d, sc, _ = run(w, doc, words, vad, t_end, t0, t1)
            by = {ln["id"]: ln for ln in d["lines"]}
            for i in ls:
                if i in inside:
                    assert by[i]["status"] == "ok" and abs(by[i]["start"] - truth[i][0]) <= 0.5, (side, k, by[i])
                else:
                    assert by[i]["status"] == "out_of_range" and by[i]["start"] is None, (side, k, by[i])
            assert d["extras"] == [], (side, k)
            assert next(s for s in sc if s["id"] == f"SC{k:03d}")["status"] == "found"


@pytest.mark.parametrize("cut", [{6}, {8}, {6, 8}])
def test_short_scene_beside_a_cut_scene_is_found(tmp_path, doc, cut):
    """review B1 (감사 1-03): scene 7 is one two-letter line; heard beside a cut scene it was absent
    because the room was reckoned for the cut scene's lines too."""
    words, vad, truth, t_end = speak(film_of(doc, cut=cut))
    d, sc, _ = run(tmp_path, doc, words, vad, t_end)
    ln = next(x for x in d["lines"] if x["scene_id"] == "SC007")
    assert ln["status"] == "ok" and abs(ln["start"] - truth[ln["id"]][0]) <= 0.3
    by = {s["id"]: s for s in sc}
    assert by["SC007"]["status"] == "found"
    assert all(by[f"SC{k:03d}"]["status"] == "absent" and by[f"SC{k:03d}"]["guess"] for k in cut)
    assert d["extras"] == []


def test_scene_in_another_language_is_placed_on_voice(tmp_path, doc):
    """review B1 (brief 7): a whole scene marked foreign (script in Korean, sound in English) cannot
    be found by its letters; it is placed on voice (ok, note foreign), and what is said there is
    its lines', not speech in no script line. Cut, it is absent."""
    rng = random.Random(3)
    eng = ["hello", "there", "my", "friend", "where", "is", "the", "station", "please", "drive", "fast", "now",
           "money", "camera", "road", "home", "tonight", "soldier", "people", "street"]
    d2 = {**doc, "lines": [dict(ln) for ln in doc["lines"]]}
    said = {}
    for ln in d2["lines"]:
        said[ln["id"]] = ln["text"]
        if ln["scene_id"] in ("SC005", "SC020"):
            ln["language"] = "영어"
            said[ln["id"]] = " ".join(rng.choice(eng) for _ in range(len(ln["text"].split()) + 1))
    foreign = {ln["id"] for ln in d2["lines"] if ln["language"]}
    words, vad, truth, t_end = speak([(i, said[i], sc) for i, _, sc in film_of(d2)])
    (tmp_path / "a").mkdir()
    d, sc, _ = run(tmp_path / "a", d2, words, vad, t_end)
    rows = [ln for ln in d["lines"] if ln["id"] in foreign]
    assert len(rows) >= 6
    assert all(r["status"] == "ok" and r["note"] == "foreign" and r["timing"] in ("voice", "even") and "diff" not in r
               for r in rows)
    assert sum(abs(r["start"] - truth[r["id"]][0]) <= 1.0 for r in rows) >= 0.8 * len(rows)
    by = {s["id"]: s for s in sc}
    assert by["SC005"]["status"] == by["SC020"]["status"] == "unknown"
    assert by["SC005"]["note"] == by["SC020"]["note"] == "foreign"
    assert d["extras"] == []
    assert placed(d["lines"], {k: v for k, v in truth.items() if k not in foreign}) >= 0.99
    # the same script on a film without scene 20: its lines are not there
    words, vad, truth, t_end = speak([(i, said[i], sc) for i, _, sc in film_of(d2, cut={20})])
    (tmp_path / "b").mkdir()
    d, sc, _ = run(tmp_path / "b", d2, words, vad, t_end)
    assert all(r["status"] == "absent" for r in d["lines"] if r["scene_id"] == "SC020")
    assert all(r["status"] == "ok" and r["start"] is not None for r in d["lines"] if r["scene_id"] == "SC005")


def test_scene_with_one_line_heard_is_kept(tmp_path, doc):
    """review B1: a long scene too little heard to be found (one short line heard, the rest not)
    kept no line — the heard one became an extra. A line mostly heard keeps the scene (unknown)."""
    us = scenes.units(doc["lines"])
    pick = next((u, i) for u in us if len(u["grams"]) >= align.LONG_SCENE_GRAMS and u["scene_id"] not in ("SC001", "SC030")
                for i in u["idx"] if 4 <= len(align.norm_chars(doc["lines"][i]["text"])) <= 5)
    u, heard_i = pick
    words, vad, truth, t_end = speak(film_of(doc))
    quiet = [doc["lines"][i]["id"] for i in u["idx"] if i != heard_i]
    words = [w for w in words if not any(truth[i][0] - 0.01 <= w["s"] <= truth[i][1] for i in quiet)]
    d, sc, _ = run(tmp_path, doc, words, vad, t_end)
    by = {ln["id"]: ln for ln in d["lines"]}
    h = by[doc["lines"][heard_i]["id"]]
    assert h["status"] == "ok" and abs(h["start"] - truth[h["id"]][0]) <= 0.3
    assert all(by[i]["status"] == "unheard" and by[i]["start"] is not None for i in quiet)
    assert next(s for s in sc if s["id"] == u["scene_id"])["status"] == "unknown"
    assert d["extras"] == []


def test_unheard_lines_stay_near_their_scene(tmp_path, doc):
    """review B1: scenes 9 and 10 are cut and other speech is in their place; the first two lines
    of scene 11 are not heard. With no voice scores (an ASR of the whole film only) they were
    spread from the end of scene 8 on; they stay just before scene 11's first matched line."""
    rng = random.Random(7)
    used = set(" ".join(ln["text"] for ln in doc["lines"]).split())
    other = [w for w in _vocab(rng, 200, 3, 4) if w not in used]
    film = []
    for k in range(1, 31):
        if k in (9, 10):
            film += [(f"N{k}_{n}", " ".join(rng.sample(other, 4)), k) for n in range(10)]
        else:
            film += [(ln["id"], ln["text"], k) for ln in scene_lines(doc, k)]
    words, _, truth, t_end = speak(film)
    s11 = [ln["id"] for ln in scene_lines(doc, 11)]
    quiet = s11[:2]
    words = [w for w in words if not any(truth[i][0] - 0.01 <= w["s"] <= truth[i][1] for i in quiet)]
    d, sc, _ = run(tmp_path, doc, words, [], t_end)
    by = {ln["id"]: ln for ln in d["lines"]}
    first = by[s11[2]]
    assert first["status"] == "ok"
    end8 = max(by[ln["id"]]["end"] for ln in scene_lines(doc, 8))
    assert first["start"] - end8 > 60.0
    for i in quiet:
        assert by[i]["status"] == "unheard" and first["start"] - 30.0 < by[i]["start"] < first["start"]
    letters = sum(len(align.norm_chars(by[i]["text"])) for i in quiet)
    assert by[quiet[0]]["start"] >= first["start"] - (align.SCENE_SLACK + align.SCENE_SEC_PER_CHAR * letters) - 0.1
    rows = {s["id"]: s for s in sc}
    assert rows["SC011"]["start"] == first["start"] and rows["SC011"]["span"][0] == by[quiet[0]]["start"]
    for k in (9, 10):                                   # all their lines absent: so is the scene, with its place
        assert all(by[ln["id"]]["status"] == "absent" for ln in scene_lines(doc, k))
        assert rows[f"SC{k:03d}"]["status"] == "absent" and rows[f"SC{k:03d}"]["guess"]
    # the other speech in their place is kept (one group per stretch without a pause), each under
    # one of the two scenes by its share of their letters (it was None with two scenes in one place)
    there = [x for x in d["extras"] if end8 < x["start"] < first["start"]]
    assert [x["scene_id"] for x in there] == ["SC009", "SC010"]


def test_changed_words_differ_and_one_letter_is_a_hint(tmp_path, doc):
    rng = random.Random(4)
    long_lines = [ln for ln in doc["lines"] if len(ln["text"].split()) >= 4]
    changed, typo = long_lines[0:40:4], long_lines[1:40:8]
    said = {ln["id"]: ln["text"] for ln in doc["lines"]}
    for ln in changed:                                    # one word said differently (no letter in common)
        ws = ln["text"].split()
        ws[1] = "".join(rng.choice([c for c in SYL if c not in ln["text"]]) for _ in range(3))
        said[ln["id"]] = " ".join(ws)
    for ln in typo:                                       # the script has one vowel wrong (ㅏ -> ㅓ)
        ws = said[ln["id"]].split()
        c = ws[2][0]
        k = ord(c) - 0xAC00
        ws[2] = chr(0xAC00 + (k // 588) * 588 + (4 if (k % 588) // 28 != 4 else 0) * 28 + k % 28) + ws[2][1:]
        said[ln["id"]] = " ".join(ws)
    film = [(i, said[i], sc) for i, _, sc in film_of(doc)]
    words, vad, truth, t_end = speak(film)
    d, _, _ = run(tmp_path, doc, words, vad, t_end)
    by = {ln["id"]: ln for ln in d["lines"]}
    for ln in changed:
        r = by[ln["id"]]
        assert r["status"] == "differs", r
        assert any(x["kind"] == "word" and x["script"] == ln["text"].split()[1] and x["at"] == [1, 2] for x in r["diff"])
    for ln in typo:
        r = by[ln["id"]]
        assert r["status"] == "ok" and r["hints"] == ["spelling"]
        assert [x["kind"] for x in r["diff"]] == ["minor"] and r["diff"][0]["spelling"]
    others = [r for i, r in by.items() if i not in {ln["id"] for ln in changed + typo} and i in truth]
    assert sum(r["status"] == "differs" for r in others) <= 1


@pytest.mark.parametrize("rate,place_min,found_min", [(0.05, 0.95, 28), (0.10, 0.9, 27)])
def test_asr_letter_errors(tmp_path, doc, rate, place_min, found_min):
    words, vad, truth, t_end = speak(film_of(doc))
    d, sc, _ = run(tmp_path, doc, garble(words, rate), vad, t_end)
    lines = d["lines"]
    assert placed(lines, truth) >= place_min
    assert sum(s["status"] == "found" for s in sc) >= found_min
    assert sum(ln["status"] == "absent" for ln in lines) / len(lines) <= 0.05
    # one or two wrong letters in a word are ASR noise, not a different line
    assert sum(ln["status"] == "differs" for ln in lines) / len(lines) <= (0.05 if rate < 0.08 else 0.2)
    assert len(d["extras"]) <= 3


# ------------------------------------------------------------------ 장면 없는 대본 (예전과 같게)
def test_script_without_scenes_is_one_scene_as_before(tmp_path, doc):
    flat = {**doc, "format": "lines", "scenes": []}
    film = film_of(doc, cut={4})
    words, vad, truth, t_end = speak(film)
    d, sc, res = run(tmp_path, flat, words, vad, t_end)
    assert res["mode"] == "aligned" and sc == []
    plain = align.align_lines(_P(tmp_path), [dict(ln) for ln in doc["lines"]], words, 0.0, t_end)
    assert [(ln["start"], ln["end"], ln["timing"]) for ln in d["lines"]] == [(ln["start"], ln["end"], ln["timing"]) for ln in plain]
    assert {ln["status"] for ln in d["lines"]} <= {"ok", "unheard"}
    assert all(ln["status"] == "unheard" for ln in d["lines"] if ln["id"] not in truth)


def test_partial_window_without_scenes(tmp_path, doc):
    flat = {**doc, "format": "lines", "scenes": []}
    words, vad, truth, t_end = speak(film_of(doc))
    ids = [ln["id"] for ln in doc["lines"]]
    t1 = truth[ids[len(ids) // 3]][1] + 0.5               # the first third of the film
    d, _, _ = run(tmp_path, flat, words, vad, t_end, 0.0, t1)
    after = [ln for ln in d["lines"] if truth[ln["id"]][0] > t1]
    assert len(after) > 50 and all(ln["status"] == "out_of_range" and ln["start"] is None for ln in after)
    assert all(ln["status"] in ("ok", "differs") for ln in d["lines"] if truth[ln["id"]][1] <= t1)


def test_short_unheard_run_at_a_partial_window_end_stays(tmp_path, doc):
    flat = {**doc, "format": "lines", "scenes": []}
    words, vad, truth, t_end = speak(film_of(doc))
    last2 = [ln["id"] for ln in doc["lines"][-2:]]
    quiet = [w for w in words if not any(truth[i][0] - 0.01 <= w["s"] <= truth[i][1] for i in last2)]
    d, _, _ = run(tmp_path, flat, quiet, vad, t_end + 30.0, 0.0, t_end)     # the video goes on after the window
    by = {ln["id"]: ln for ln in d["lines"]}
    assert [by[i]["status"] for i in last2] == ["unheard", "unheard"]
    assert all(by[i]["timing"] in ("voice", "even") for i in last2)


def test_foreign_line_is_placed_and_never_differs(tmp_path, doc):
    d2 = {**doc, "lines": [dict(ln) for ln in doc["lines"]]}
    target = scene_lines(d2, 5)[1]
    target["language"] = "영어"
    target_ln = next(ln for ln in d2["lines"] if ln["id"] == target["id"])
    target_ln["language"] = "영어"
    said = {ln["id"]: ln["text"] for ln in d2["lines"]}
    said[target["id"]] = "hello there my friend"              # the sound is English, the script Korean
    words, vad, truth, t_end = speak([(i, said[i], sc) for i, _, sc in film_of(d2)])
    d, _, _ = run(tmp_path, d2, words, vad, t_end)
    r = next(ln for ln in d["lines"] if ln["id"] == target["id"])
    assert r["status"] == "ok" and r["note"] == "foreign" and r["timing"] in ("voice", "even")
    assert abs(r["start"] - truth[target["id"]][0]) <= 1.0
    assert "diff" not in r and r["language"] == "영어"


def test_long_line_keeps_its_first_and_last_words(tmp_path):
    """감사 8-m3: a line of 20 s lost the anchors more than 8 s from its middle."""
    rng = random.Random(2)
    vocab = _vocab(rng, 300)
    long = " ".join(rng.choice(vocab) for _ in range(45))
    lines = [{"id": "L1", "speaker": "가", "text": "짧은 말이다"}, {"id": "L2", "speaker": "가", "text": long},
             {"id": "L3", "speaker": "나", "text": "그다음 말이다"}]
    words, vad, truth, _ = speak([(ln["id"], ln["text"], 1) for ln in lines])
    assert truth["L2"][1] - truth["L2"][0] > 16.0
    out = align.align_lines(_P(tmp_path), lines, words, 0.0, 200.0)
    assert out[1]["start"] == pytest.approx(truth["L2"][0], abs=0.2)
    assert out[1]["end"] == pytest.approx(truth["L2"][1], abs=0.3)


def test_extras_leave_out_interjections_and_hint_echoes(tmp_path, doc):
    small = {**doc, "lines": [ln for ln in doc["lines"] if ln["scene_id"] in ("SC001", "SC002", "SC003")],
             "scenes": doc["scenes"][:3]}
    film = film_of(small)
    film.insert(3, ("N1", "네", 1))
    film.insert(6, ("N2", "가람 나래 다솜 가람", 1))       # the hint names repeated
    film.insert(9, ("N3", "타파하 코토포 무부수", 1))       # a real ad-lib
    film.insert(12, ("N4", "아 네", 1))                     # interjections only (review B1)
    film.insert(15, ("N5", "쿄쿄", 1))                      # two letters: kept, marked short
    words, vad, truth, t_end = speak(film)
    d, _, _ = run(tmp_path, small, words, vad, t_end)
    assert [(x["text"], x.get("short", False)) for x in d["extras"]] == [("타파하 코토포 무부수", False), ("쿄쿄", True)]


def test_scenes_without_dialogue_in_a_row_share_the_gap(tmp_path, doc):
    two = {**doc, "lines": [ln for ln in doc["lines"] if ln["scene_id"] != "SC013"]}   # SC012 and SC013 silent
    words, vad, truth, t_end = speak(film_of(two))
    _, sc, _ = run(tmp_path, two, words, vad, t_end)
    row = {r["id"]: r for r in sc}
    gap = [row["SC011"]["end"], row["SC014"]["start"]]
    assert gap[0] < gap[1]
    assert [row["SC012"]["start"], row["SC012"]["end"]] == [row["SC013"]["start"], row["SC013"]["end"]] == gap
    assert row["SC012"]["status"] == row["SC013"]["status"] == "unknown"


def test_asr_noise_shapes():
    assert align.noise_shape(["감사합니다."]) == "greeting"
    assert align.noise_shape(["수고하셨습니다.", "수고하셨습니다."]) == "greeting"
    assert align.noise_shape(["시청해", "주셔서", "감사합니다."]) == "credit"
    assert align.noise_shape(["한글자막", "by", "가나다"]) == "credit"
    assert align.noise_shape(["구독과", "좋아요"]) == "credit"
    assert align.noise_shape(["타파", "타파", "타파", "타파"]) == "repeat"
    assert align.noise_shape(["감사합니다", "가람씨"]) is None             # thanks someone: a real line
    # words said in films: a credit word alone, a call two or three times (asr.hint_echo keeps it too)
    for toks in (["시청", "앞에서", "보자."], ["자막", "좀", "켜", "봐."], ["신문", "구독", "끊었어."],
                 ["엄마!", "엄마!"], ["가자,", "가자!"], ["타파", "타파", "타파"]):
        assert align.noise_shape(toks) is None, toks
    assert align.noise_shape(["자막", "제공:", "가나다"]) == "credit"
    assert align.noise_shape(["좋아요", "구독", "부탁드려요"]) == "credit"
    assert align.noise_shape(["타파하", "코토포"]) is None and align.noise_shape(["."]) is None
    assert align.noise_shape(["baby", "코토포"]) is None                  # 'by' only as a word of its own


class _Scores:
    def __init__(self, **sc):
        self.sc = sc

    def scores(self, a, b):
        return dict(self.sc)


def test_no_voice_under_asr_words():
    w = [{"w": "감사합니다.", "s": 1.0, "e": 1.5}]
    assert align.no_voice(w, _Scores(speech=0.01, music=0.52, vad=0.8)) == {"speech": 0.01, "music": 0.52, "vad": 0.8}
    assert align.no_voice(w, _Scores(speech=0.33, music=0.55, vad=0.8))      # a song's voice under music
    assert align.no_voice(w, _Scores(speech=0.33, music=0.2, vad=0.8)) is None
    assert align.no_voice(w, _Scores(speech=0.5, music=0.6, vad=0.8)) is None  # speech over music
    assert align.no_voice(w, _Scores(vad=0.1)) == {"vad": 0.1}               # no SED files: the VAD
    assert align.no_voice(w, _Scores(vad=0.8)) is None
    assert align.no_voice([{**w[0], "p": 0.2}]) == {"p": 0.2}                 # the ASR's own word probability
    assert align.no_voice([{**w[0], "p": 0.9}]) is None and align.no_voice(w) is None


def test_asr_noise_over_music_is_kept_but_suspect(tmp_path, doc):
    small = {**doc, "lines": [ln for ln in doc["lines"] if ln["scene_id"] in ("SC001", "SC002", "SC003")],
             "scenes": doc["scenes"][:3]}
    film = film_of(small)
    film.insert(3, ("N1", "감사합니다.", 1))                 # over music, nobody speaking
    film.insert(7, ("N2", "감사합니다.", 2))                 # someone says it
    film.insert(10, ("N3", "타파하 코토포 무부수", 2))       # an ad-lib over the same music
    words, vad, truth, t_end = speak(film)
    names = sorted(set(align.VOICE) | set(align.VOCAL_FX) | {"Music"})
    n = int(t_end * 25) + 25
    probs = np.zeros((n, len(names)), np.float16)
    probs[:, names.index("Speech")] = 0.6
    for uid in ("N1", "N3"):
        a, b = (int(x * 25) for x in truth[uid])
        probs[a:b + 1, names.index("Speech")] = 0.02
        probs[a:b + 1, names.index("Music")] = 0.7
    for track in ("mix", "effects"):
        np.savez(tmp_path / f"sed_{track}.npz", probs=probs, classes=np.array(names), t0=0.0, fps=25.0)
    np.savez(tmp_path / "env.npz", speech=np.full(n, -80.0), effects=np.full(n, -80.0), hop=0.04)
    d, _, _ = run(tmp_path, small, words, vad, t_end)
    got = {(x["text"], x["start"]): x for x in d["extras"]}
    assert len(got) == 3
    noise = got[("감사합니다.", truth["N1"][0])]
    assert noise["suspect"] == "greeting" and noise["suspect_by"]["music"] >= 0.5
    assert "suspect" not in got[("감사합니다.", truth["N2"][0])]
    assert "suspect" not in got[("타파하 코토포 무부수", truth["N3"][0])]       # not the shape: kept as it is


def test_voice_scores_are_read_once(tmp_path, doc, monkeypatch):
    """감사 1-09: the score files were read again for every run of unaligned lines."""
    from cboe.stages import sed
    names = sorted(set(align.VOICE) | set(align.VOCAL_FX))
    n = 4000
    for track in ("mix", "effects"):
        np.savez(tmp_path / f"sed_{track}.npz", probs=np.zeros((n, len(names)), np.float16),
                 classes=np.array(names), t0=0.0, fps=25.0)
    np.savez(tmp_path / "env.npz", speech=np.full(n, -80.0), effects=np.full(n, -80.0), hop=0.04)
    calls = []
    real = sed.load
    monkeypatch.setattr(sed, "load", lambda p, track: calls.append(track) or real(p, track))
    flat = {**doc, "format": "lines", "scenes": []}
    words, vad, truth, t_end = speak(film_of(doc))
    quiet = {ln["id"] for ln in doc["lines"][5:80:15]}      # five lines the ASR did not hear, apart
    words = [w for w in words if not any(truth[i][0] - 0.01 <= w["s"] <= truth[i][1] for i in quiet)]
    d, _, _ = run(tmp_path, flat, words, vad, min(t_end, n / 25.0))
    assert sum(ln["status"] == "unheard" for ln in d["lines"]) >= 5
    assert sorted(calls) == ["effects", "mix"]


# ------------------------------------------------------------------ 부품
class _P:
    def __init__(self, tmp):
        self.work = tmp

    def path(self, name):
        return self.work / name


def test_compare_words():
    assert align.compare("우리 집 마당은 조용하다.", ["우리집", "마당은", "시끄럽다"])[0] == \
        [{"kind": "word", "script": "조용하다.", "heard": "시끄럽다", "at": [3, 4]}]
    assert align.compare("너는 무슨 색이야?", ["너는", "어떤", "색이야"])[0][0]["kind"] == "word"
    assert align.compare("자, 가자... 어서!", ["자", "가자", "어서"]) == ([], [])     # spaces and punctuation only
    diff, hints = align.compare("그건 내일 할 거에요.", ["그건", "내일", "할", "거예요"])
    assert hints == ["spelling"] and diff[0]["kind"] == "minor" and diff[0]["spelling"]
    diff, hints = align.compare("가람 씨", ["가럼", "씨"], names=["가람"])               # names are misheard, not misspelt
    assert diff[0]["kind"] == "minor" and hints == [] and "spelling" not in diff[0]
    assert align.compare("3층이야", ["삼층이야"])[0][0]["kind"] == "number"
    assert align.compare("모비 가자", ["모키다", "가자"])[0][0]["kind"] == "word"      # a letter became two
    assert align.compare("아무 말", []) == ([], [])
    # what is shown is whole words on both sides; the kind is judged on the stretch (review B1)
    diff, _ = align.compare("그건 있었을거 같아.", ["그건", "있었을거야."])
    assert [(x["kind"], x["script"], x["heard"], x["at"]) for x in diff] == [("word", "있었을거 같아.", "있었을거야.", [1, 3])]
    diff, _ = align.compare("빨리 기다려야 하잖아요.", ["빨리", "기다려야죠."])
    assert [(x["script"], x["heard"], x["at"]) for x in diff] == [("기다려야 하잖아요.", "기다려야죠.", [1, 3])]


def test_locate_without_order(doc):
    """Scenes are found wherever they are; the chain of scenes in script order is the skeleton."""
    order = list(range(30, 0, -1))                       # the film plays the scenes backwards
    words, _, truth, t_end = speak(film_of(doc, order=order))
    us = scenes.units(doc["lines"])
    loc = scenes.locate(us, words, 0.0, t_end)
    for u, r in zip(us, loc):
        if len(u["grams"]) >= 40:
            first = doc["lines"][u["idx"][0]]["id"]
            assert r["status"] == "found" and abs(r["a"] - truth[first][0]) < 8.0, (u["scene_id"], r)
    assert sum(r.get("how") == "moved" for r in loc) >= 20
