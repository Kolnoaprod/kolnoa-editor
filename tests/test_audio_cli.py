"""Chaîne audio en ligne de commande avec de vrais fichiers : proposer (sans rien appliquer), choisir, mixer, mesurer."""

import json

import numpy as np

from kolnoa_editor.cli import main
from kolnoa_editor.timeline import Session
from tests.test_audio_mix import tone, write_wav
from tests.test_multicam_cli import shoot  # noqa: F401  (fixture : enregistreur, caméra avec son, drone muet)


def test_propose_never_applies_then_master_then_mix(shoot, capsys, tmp_path):  # noqa: F811
    root, project = shoot
    assert main(["multicam-sync", root, "g001"]) == 0
    capsys.readouterr()
    history = (project.timeline_dir / "history.jsonl").read_text()
    assert main(["audio-propose", root, "g001", "40", "70"]) == 0
    out = capsys.readouterr().out
    assert "CAM" in out and "intelligibilité" in out and "DRONE" not in out          # le drone n'a pas d'audio : jamais candidat
    assert (project.timeline_dir / "history.jsonl").read_text() == history              # proposer n'écrit rien
    assert main(["audio-master", root, "g001", "40", "70", "CAM", "--reason", "proposition validée"]) == 0
    tl = Session.open(project.timeline_dir).tl
    assert [(r.start, r.end, r.source, r.reason) for r in tl.masters] == [(40.0, 70.0, "CAM", "proposition validée")]
    assert main(["audio-master", root, "g001", "50", "55", "DRONE"]) == 2               # drone muet : refusé
    assert "pas d'audio" in capsys.readouterr().err
    assert main(["audio-master", root, "g001", "40", "70", "--clear"]) == 0
    assert Session.open(project.timeline_dir).tl.masters == []
    assert main(["audio-master", root, "g001", "40", "70"]) == 2                        # ni source ni --clear
    wav = tmp_path / "mix.wav"
    assert main(["audio-mix", root, str(wav), "--lufs", "-23"]) == 0
    text = capsys.readouterr().out
    assert wav.is_file() and "Mixage écrit" in text and "LUFS" in text
    assert main(["audio-mix", root, str(wav)]) == 2                                      # n'écrase pas sans --force
    assert Session.open(project.timeline_dir).tl.audio_settings.target_lufs is None      # la session n'a pas été modifiée


def test_audio_measure_is_read_only_and_writes_json(tmp_path, capsys):
    f = write_wav(tmp_path / "t.wav", tone(440, 3.0, 0.3))
    before = f.read_bytes()
    out = tmp_path / "m.json"
    assert main(["audio-measure", str(f), "--out", str(out)]) == 0
    rows = json.loads(out.read_text())
    assert rows[0]["channels"] == 1 and rows[0]["clip_ratio"] == 0.0 and np.isclose(rows[0]["duration_s"], 3.0, atol=0.01)
    assert "LUFS" in capsys.readouterr().out and f.read_bytes() == before
    assert main(["audio-measure", str(tmp_path / "absent.wav")]) == 2
