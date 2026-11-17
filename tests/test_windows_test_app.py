"""Application d'essai Windows : construction de la session d'essai, fenêtre, série de mesures (processus séparés), rapport sans noms de fichiers."""

import json
import os
import shutil
import subprocess
import time

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg absent")

from PySide6.QtWidgets import QApplication

from kolnoa_editor.player import suite
from kolnoa_editor.ui import demo_app


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    d = tmp_path_factory.mktemp("rushes_client_secret")
    out = []
    for i in range(3):
        f = d / f"MARIAGE_DUPONT_cam{i}.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=5", "-f", "lavfi", "-i",
                        "sine=f=300:duration=5", "-c:v", "libx264", "-preset", "ultrafast", "-g", "12", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(f)], check=True)
        out.append(str(f))
    return out


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_build_demo_makes_a_valid_multicam_session_with_proxies_and_never_touches_the_rushes(clips, tmp_path):
    before = [(os.path.getsize(c), os.path.getmtime(c)) for c in clips]
    sess, media, dur = demo_app.build_demo(clips, tmp_path / "w", make_proxies=True, proxy_seconds=3)
    assert list(media) == ["CAM1", "CAM2", "CAM3"] and all(m.proxy and os.path.isfile(m.proxy) for m in media.values())
    sess.tl.validate()
    c = sess.tl.clips_on("V1")[0]
    assert c.group == "g001" and c.angle == "CAM1" and len(sess.tl.bin) == 3
    assert [(os.path.getsize(x), os.path.getmtime(x)) for x in clips] == before
    assert not any(str(tmp_path / "w") in c for c in clips)


def test_window_starts_plays_and_all_angles_receive_frames(clips, tmp_path, qapp):
    sess, media, dur = demo_app.build_demo(clips, tmp_path / "w", make_proxies=False)
    win = demo_app._make_window(sess, media, dur, tmp_path / "w")
    win.show()
    win.mc.player.seek(1.0)
    win.mc.player.play()
    t0 = time.time()
    while time.time() - t0 < 2.0:
        qapp.processEvents()
    assert set(win.mc.images) == set(media)
    win.tlv.grab()
    win.close()


def test_measure_suite_runs_in_separate_processes_and_report_has_no_file_names(clips, tmp_path):
    res = suite.run_suite(clips[:2], tmp_path / "m", log=lambda s: None, configs=[("logiciel", "")], modes=("seek",))
    r = res["mesures"]["logiciel"]["seek"]
    assert "resultat" in r and set(r["resultat"]) == {"rush 1", "rush 2"} and r["resultat"]["rush 1"]["n"] > 0
    md = (tmp_path / "m" / "MESURES.md").read_text(encoding="utf-8")
    js = (tmp_path / "m" / "mesures.json").read_text(encoding="utf-8")
    for text in (md, js):
        assert "DUPONT" not in text and "rushes_client" not in text           # dépôt public : aucun nom de fichier ni de client
    assert json.loads(js)["verdict_gpu"] and "Mesures du lecteur" in md


def test_suite_reports_worker_failure_instead_of_crashing(tmp_path):
    res = suite.run_suite([str(tmp_path / "absent.mp4")], tmp_path / "m", log=lambda s: None, configs=[("logiciel", "")], modes=("seek",))
    r = res["mesures"]["logiciel"]["seek"]
    assert "erreur" in r or r["resultat"]            # pas d'exception ; soit une erreur déclarée, soit un résultat vide


def test_verdict_wording(tmp_path):
    base = {"nvidia_smi": True, "mesures": {"logiciel": {"seek": {"gpu": {"dec_max_pct": 0}}}, "gpu (cuda)": {"seek": {"gpu": {"dec_max_pct": 40}}}}}
    assert "ACTIF" in suite.verdict(base)
    base["mesures"]["gpu (cuda)"]["seek"]["gpu"]["dec_max_pct"] = 1
    assert "PAS de preuve" in suite.verdict(base)
    assert "nvidia-smi absent" in suite.verdict({"nvidia_smi": False, "mesures": {}})
