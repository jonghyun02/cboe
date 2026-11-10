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
    words, vad, truth, t_end = speak(film)
    d, _, _ = run(tmp_path, small, words, vad, t_end)
    texts = [x["text"] for x in d["extras"]]
    assert texts == ["타파하 코토포 무부수"]


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
