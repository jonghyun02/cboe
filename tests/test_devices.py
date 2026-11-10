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


class _Stop(Exception):
    pass


def _loader(calls, name, cached=True, result=None):
    """A fake Hugging Face loader: records its calls; without the files in the cache it raises
    OSError when it may not download (what transformers / huggingface_hub do)."""
    def load(*a, **k):
        calls.append((name, a[:1], dict(k)))
        if k.get("local_files_only") and not cached:
            raise OSError("not in the cache")
        if result is None:
            raise _Stop
        return result
    return load


def test_model_stages_open_the_cached_model_without_asking_huggingface(monkeypatch):
    import sys
    import types

    import numpy as np

    from cboe.stages import music
    calls = []
    assert util.local_first(_loader(calls, "x", result="m"), "repo/x", revision="r") == "m"
    assert calls == [("x", ("repo/x",), {"local_files_only": True, "revision": "r"})]
    calls.clear()
    assert util.local_first(_loader(calls, "x", cached=False, result="m"), "repo/x") == "m"
    assert [k for _, _, k in calls] == [{"local_files_only": True}, {}]          # not there: then it may fetch
    # music (AST), faster-whisper and the OpenVINO Whisper: the first call asks for local files only
    calls.clear()
    fake_tf = types.SimpleNamespace(ASTFeatureExtractor=types.SimpleNamespace(from_pretrained=_loader(calls, "ast_fe")),
                                    ASTForAudioClassification=types.SimpleNamespace(
                                        from_pretrained=_loader(calls, "ast")))
    monkeypatch.setitem(sys.modules, "transformers", fake_tf)
    with pytest.raises(_Stop):
        music._ast_windows(np.zeros(16000, dtype=np.float32), 16000, [0.0])
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=_loader(calls, "fw")))
    with pytest.raises(_Stop):
        asr.FasterWhisper("cpu")
    import huggingface_hub
    monkeypatch.setitem(sys.modules, "openvino_genai", types.SimpleNamespace())
    monkeypatch.setattr(huggingface_hub, "snapshot_download", _loader(calls, "ov"))
    with pytest.raises(_Stop):
        asr.OVWhisper("gpu")
    assert [(n, k.get("local_files_only")) for n, _, k in calls] == [("ast_fe", True), ("fw", True), ("ov", True)]
    assert calls[2][2]["revision"] == asr.paths.WHISPER_OV_REVISION


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
