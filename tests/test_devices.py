# -*- coding: utf-8 -*-
"""Device choice and the pieces that let the stages run on an Intel GPU (no GPU needed here)."""
from __future__ import annotations

import pytest

from cboe import util
from cboe.stages import asr


def test_device_env_override(monkeypatch):
    monkeypatch.setenv("CBOE_DEVICE", "cpu")
    assert util.device() == "cpu"
    monkeypatch.setenv("CBOE_DEVICE", "xpu")
    assert util.device() == "xpu"


def test_asr_backend_follows_device(monkeypatch):
    made = []
    monkeypatch.setattr(asr, "FasterWhisper", lambda dev: made.append(("fw", dev)) or "fw")
    monkeypatch.setattr(asr, "OVWhisper", lambda dev: made.append(("ov", dev)) or "ov")
    monkeypatch.delenv("CBOE_ASR_BACKEND", raising=False)
    assert [asr.load_model(d) for d in ("cuda", "cpu", "xpu")] == ["fw", "fw", "ov"]
    monkeypatch.setenv("CBOE_ASR_BACKEND", "faster-whisper")
    assert asr.load_model("xpu") == "fw"


def _w(word, s, e):
    return {"word": " " + word, "start": s, "end": e, "n": asr._nchars(word)}


def test_fw_segment_fixes_first_word_after_pause():
    """Word times as OpenVINO gave them for a window of the test clip (words replaced): the first
    word of the second timestamp segment ran over a 2.6 s pause; faster-whisper shortens it to
    twice the median word length and splits it from the next long word."""
    words = [_w("오늘", 0.0, 0.38), _w("아침", 0.38, 1.14), _w("학교에서", 1.14, 1.68), _w("친구를", 1.68, 1.98),
             _w("만나", 1.98, 2.28), _w("같이", 2.28, 2.74), _w("점심을", 2.74, 3.16), _w("먹었어요", 3.16, 3.48),
             _w("그리고는", 3.48, 6.10), _w("돌아왔다", 6.10, 8.00)]
    segs = [{"start": 0.0, "end": 3.8, "n": asr._nchars("오늘 아침 학교에서 친구를 만나 같이 점심을 먹었어요")},
            {"start": 3.8, "end": 8.14, "n": asr._nchars("그리고는 돌아왔다")}]
    out = asr.fw_segment_fixes(words, segs, 8.5)
    assert [w["word"] for w in out] == [w["word"] for w in words]
    assert out[:8] == words[:8]                              # no pause before the first segment
    med = (0.42 + 0.46) / 2                                  # median word length; max = 2 x median
    assert out[8]["end"] == pytest.approx(8.0 - 2 * med) and out[9]["start"] == out[8]["end"]
    assert out[8]["start"] == pytest.approx(out[8]["end"] - 2 * med)
    assert out[9]["end"] == 8.0


def test_fw_segment_fixes_long_word_after_sentence_end():
    words = [_w("정말?", 0.0, 0.4), _w("그건", 0.4, 0.6), _w("아니", 0.6, 0.9), _w("알겠어.", 0.9, 1.3),
             _w("그러면", 1.3, 3.0), _w("가자", 3.0, 3.3)]
    out = asr.fw_segment_fixes(words, [], 3.5)
    med = (0.3 + 0.4) / 2
    assert out[4]["start"] == pytest.approx(3.0 - 2 * med) and out[4]["end"] == 3.0
    assert [w["start"] for w in out[:4]] == [w["start"] for w in words[:4]]
    assert words[4]["start"] == 1.3                       # the input is not changed


def test_fw_segment_fixes_without_segments_keeps_times():
    words = [_w("정말?", 0.1, 0.5)]
    assert asr.fw_segment_fixes(words, [], 1.0) == words
    assert asr.fw_segment_fixes([], [], 1.0) == []
