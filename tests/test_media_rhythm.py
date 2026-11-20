from pathlib import Path

import numpy as np
import pytest

from kolnoa_editor.app.media_manifest import MediaManifest
from kolnoa_editor.auto.rhythm import estimate_rhythm, beat_markers
from kolnoa_editor.ffmpeg import CancelToken, Cancelled
from kolnoa_editor.timeline import Timeline


def test_import_references_from_multiple_folders_and_restores_categories(tmp_path, monkeypatch):
    camera = tmp_path / "Caméra"
    music = tmp_path / "Musique"
    camera.mkdir()
    music.mkdir()
    (camera / "clip.MOV").write_bytes(b"video")
    (camera / "photo.jpg").write_bytes(b"photo")
    (music / "titre.wav").write_bytes(b"audio")
    sub = camera / "suite"
    sub.mkdir()
    (sub / "autre.mp4").write_bytes(b"video")
    from types import SimpleNamespace
    monkeypatch.setattr("kolnoa_editor.app.media_manifest.probe", lambda *a, **k: SimpleNamespace(duration=4, has_audio=True))
    path = tmp_path / "Projet.klnprj"
    manifest = MediaManifest(path)
    assert {r.kind for r in manifest.import_paths([camera, music])} == {"video", "audio", "image"}
    second = MediaManifest(tmp_path / "Musical.klnprj")
    assert [r.kind for r in second.import_paths([music], music=True)] == ["music"]
    assert [Path(r.path).name for r in manifest.import_paths([camera], recursive=True)] == ["autre.mp4"]
    assert MediaManifest(path).media == manifest.media
    assert (camera / "clip.MOV").read_bytes() == b"video"


def test_cancelled_import_does_not_publish(tmp_path):
    source = tmp_path / "photo.png"
    source.write_bytes(b"image")
    file = tmp_path / "Projet.klnprj"
    token = CancelToken()
    with pytest.raises(Cancelled):
        MediaManifest(file).import_paths([source], cancel=token, progress=lambda *args: token.cancel())
    assert not file.exists()


def test_detects_regular_beats_and_stays_uncertain_on_silence():
    signal = np.zeros(1000)
    signal[10::50] = 1
    rhythm = estimate_rhythm(signal)
    assert rhythm.bpm == 120 and len(rhythm.beats) >= 19
    assert estimate_rhythm(np.zeros(1000)).bpm is None
    tx = beat_markers(Timeline(title="Test", fps=25), rhythm, 50, 75)
    assert len(tx.ops) == 2 and all(50 <= op.marker["frame"] < 75 for op in tx.ops)


def write_click_track(wav):
    import wave

    rate = 8000
    signal = np.zeros(rate * 12, dtype="<i2")
    for i in range(1000, len(signal) - 120, rate // 2):
        signal[i:i + 120] = np.tile(np.array([18000, -18000], dtype="<i2"), 60)
    with wave.open(str(wav), "wb") as stream:
        stream.setparams((1, 2, rate, 0, "NONE", ""))
        stream.writeframes(signal.tobytes())


def test_measures_a_real_wave_file(tmp_path):
    from kolnoa_editor.auto.rhythm import analyze_music

    wav = tmp_path / "pulsations.wav"
    write_click_track(wav)
    rhythm = analyze_music(wav)
    assert rhythm.bpm == pytest.approx(120, abs=1)
    assert len(rhythm.beats) >= 20


def test_import_and_place_music_via_workers(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from time import monotonic, sleep

    from PySide6.QtWidgets import QApplication
    from kolnoa_editor.app import ProjectController
    from kolnoa_editor.timeline import AddClip
    from kolnoa_editor.ui.app_window import AppWindow

    def wait(win):
        limit = monotonic() + 10
        while win._operation.running and monotonic() < limit:
            QApplication.processEvents()
            sleep(.01)
        assert not win._operation.running and win._operation.bar.value() == 100, win.status.text()

    root = tmp_path / "rushes"
    root.mkdir()
    wav = tmp_path / "pulsations.wav"
    write_click_track(wav)
    app = QApplication.instance() or QApplication([])
    ctl = ProjectController.open(root, tmp_path / "data")
    ctl.session.do(AddClip(dict(id="v1", media="rush.mp4", track="V1", start=0, duration=150, src_in=0)))
    win = AppWindow(ctl)
    win.import_references([wav], music=True)
    wait(win)
    win.analyze_music_reference(win.media_manifest.media[0], place=True)
    wait(win)
    assert ctl.tl.clips_on("A2") and len(ctl.tl.markers) >= 8
    assert ProjectController.open(root, tmp_path / "data").tl.canonical() == ctl.tl.canonical()
    ctl.undo()
    assert not ctl.tl.clips_on("A2") and not ctl.tl.markers
    win.close()


def test_five_independent_sequences_share_a_project_manifest(tmp_path):
    from kolnoa_editor.app import ProjectController
    from kolnoa_editor.timeline import AddClip

    ctl = ProjectController.open(tmp_path, tmp_path / "data")
    path = tmp_path / "Mariage.klnprj"
    manifest = MediaManifest(path)
    entries = [manifest.create_sequence(ctl, title) for title in
               ["Film Houppa", "Film Réception", "Teaser Houppa", "Teaser Réception", "Clip global"]]
    first = manifest.open_sequence(ctl, entries[1].id)
    teaser = manifest.open_sequence(ctl, entries[3].id)
    for session, length in [(first, 150), (teaser, 50)]:
        session.do(AddClip(dict(id="v1", media="A7IV/rush.mp4", track="V1", start=0, duration=length, src_in=0)))
    teaser.undo()
    recovered = MediaManifest(path)
    assert recovered.active_sequence == entries[3].id
    again = ProjectController.open(recovered.root, recovered.data_dir)
    assert recovered.open_sequence(again, entries[1].id).tl.clips[0].duration == 150
    assert not recovered.open_sequence(again, entries[3].id).tl.clips
    assert len(recovered.sequences) == 5
