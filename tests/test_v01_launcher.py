"""Lanceur V0.1 : l'autotest de l'EXE (le même que celui de la CI Windows) passe aussi en source, et le projet démo est reproductible."""

import importlib.util
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

SRC = Path(__file__).resolve().parents[1] / "tools" / "winapp" / "kolnoa_editor_app.py"


def _load():
    spec = importlib.util.spec_from_file_location("kolnoa_editor_app", SRC)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_selftest_full_journey():
    assert _load().selftest() == 0


def test_demo_is_reproducible_and_data_dir_is_outside_rushes(tmp_path):
    m = _load()
    a = m.make_demo(tmp_path / "demo")
    assert len(a) == 3 and all(f.is_file() for f in a)
    assert m.make_demo(tmp_path / "demo") == a                      # idempotent
    d = m.data_dir_for(tmp_path / "demo")
    assert tmp_path not in d.parents and d.name.startswith("demo-")


def test_filter_script_option_is_detected_not_assumed():
    from kolnoa_editor.timeline import audio_mix
    a = audio_mix._filter_script_args("g.txt")
    assert a[1] == "g.txt" and a[0] in ("-filter_complex_script", "-/filter_complex")


def test_work_dir_falls_back_when_documents_unusable(tmp_path, monkeypatch):
    """Documents absent/bloqué (OneDrive, droits) : le dossier de travail bascule sur AppData\\Local au lieu d'échouer."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "winapp"))
    import kolnoa_editor_app as app
    home = tmp_path / "u"
    home.mkdir()
    (home / "Documents").write_text("je suis un fichier, pas un dossier")
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("OneDrive", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    rushes = tmp_path / "rushes"
    rushes.mkdir()
    d = app.data_dir_for(rushes)
    assert str(d).startswith(str(tmp_path / "local" / "Kolnoa" / "Projets"))
