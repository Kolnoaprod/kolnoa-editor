"""Régressions : cache d'analyse visuelle et erreurs du décodeur."""
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from kolnoa_editor.auto.visual import AnalysisStats, VisualParams, analyze_angle
from kolnoa_editor.ffmpeg import FFmpegError


def test_same_filename_different_cameras_never_share_visual_cache(tmp_path, monkeypatch):
    """Deux caméras peuvent avoir le même nom, taille et date de fichier."""
    a = tmp_path / "camera_A" / "C0001.MP4"
    b = tmp_path / "camera_B" / "C0001.MP4"
    a.parent.mkdir()
    b.parent.mkdir()
    a.write_bytes(b"same-size")
    b.write_bytes(b"same-size")
    os.utime(b, ns=(a.stat().st_atime_ns, a.stat().st_mtime_ns))
    params = VisualParams(width=16, height=16)
    calls = []

    def fake_run(cmd, *, binary, **kwargs):
        assert binary
        source = Path(cmd[cmd.index("-i") + 1])
        calls.append(source)
        gray = 60 if source == a else 180
        return SimpleNamespace(returncode=0, stdout=np.full((16, 16), gray, dtype=np.uint8).tobytes(), stderr=b"")

    monkeypatch.setattr("kolnoa_editor.auto.visual.run", fake_run)
    stats = AnalysisStats()
    cache = tmp_path / "cache"
    first = analyze_angle(a, 0, 1, params, cache, stats)
    second = analyze_angle(b, 0, 1, params, cache, stats)
    again = analyze_angle(a, 0, 1, params, cache, stats)

    assert len(calls) == 2
    assert stats.cache_hits == 1
    assert first[0].mean == again[0].mean == 60
    assert second[0].mean == 180
    assert len(list(cache.glob("*.json"))) == 2


def test_ffmpeg_error_is_not_cached_as_empty_analysis(tmp_path, monkeypatch):
    source = tmp_path / "bad.mp4"
    source.write_bytes(b"not a video")
    def fake_run(*args, **kwargs):
        raise FFmpegError("Invalid data")

    monkeypatch.setattr("kolnoa_editor.auto.visual.run", fake_run)
    cache = tmp_path / "cache"
    with pytest.raises(FFmpegError, match="Invalid data"):
        analyze_angle(source, 0, 1, VisualParams(), cache)
    assert not cache.exists() or not list(cache.glob("*.json"))
