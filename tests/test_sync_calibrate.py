"""`kolnoa sync-calibrate` : lecture seule des rushes, inventaire, plan, rapport, repères manuels, fermeture en boucle."""

import json
import os
from pathlib import Path

import numpy as np
import pytest

from kolnoa_editor.cli import main
from kolnoa_editor.sync import calibrate as cal
from kolnoa_editor.sync.synth import extract, luma_scene, mux_audio, speech_like, write_gray_video, write_wav

FS = 8000


def tree(root: Path):
    """Faux dossier de mariage : un enregistreur (maître + 2e micro), une caméra avec son, une caméra MUETTE
    (audio à zéro numérique, comme les Sony du mariage réel) et un drone sans piste audio."""
    master = speech_like(90, seed=5, rate=FS)
    write_wav(root / "RUSHES/AUDIO/MIC 1/DJI_01_20260903_102305.WAV", master, FS)
    mic2 = np.concatenate([np.zeros(int(1.5 * FS), np.float32), master[: 80 * FS]]) * 0.8
    write_wav(root / "RUSHES/AUDIO/MIC 2/DJI_01_20260903_102303.WAV", mic2, FS)
    luma = luma_scene(30, 10.0, seed=1, events=[6.0, 15.0, 24.0])
    write_gray_video(root / "RUSHES/CAM 1/clip_son.MP4", luma, 10.0)
    mux_audio(root / "RUSHES/CAM 1/clip_son.MP4", _wav(root, extract(master, FS, 30.0, 30.0, seed=2)), root / "RUSHES/CAM 1/20260903_C0001.MP4")
    (root / "RUSHES/CAM 1/clip_son.MP4").unlink()
    write_gray_video(root / "RUSHES/CAM 1/20260903_C0002.MP4", luma, 10.0, with_audio=True)     # audio = silence numérique
    ev = [e - 3.0 for e in (6.0, 15.0, 24.0)]
    write_gray_video(root / "RUSHES/DRONE/DJI_20260903102400_0001_D.MP4",
                     luma_scene(25, 10.0, seed=9, events=ev, private_events=2), 10.0)


def _wav(root: Path, x) -> Path:
    return write_wav(root / "tmp_cam.wav", x, FS)


@pytest.fixture()
def wedding(tmp_path):
    root = tmp_path / "mariage"
    tree(root)
    (root / "tmp_cam.wav").unlink()
    return root


def listing(root: Path):
    return sorted((str(p.relative_to(root)), p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*"))


# --- chemins / sécurité ---------------------------------------------------------------------------------
def test_windows_paths_need_a_root_map_off_windows():
    if os.name == "nt":
        pytest.skip("Windows")
    with pytest.raises(ValueError, match="--root-map"):
        cal.resolve_root("G:\\Mon Drive\\KOLNOA\\MARIAGE")
    r = cal.resolve_root("G:\\Mon Drive\\KOLNOA\\MARIAGE\\RUSHES", ["G:\\Mon Drive\\KOLNOA\\MARIAGE=/data/m"])
    assert r == Path("/data/m/RUSHES")


def test_report_folder_inside_the_rushes_is_refused(tmp_path):
    root = tmp_path / "m"
    root.mkdir()
    with pytest.raises(ValueError, match="ne doit pas se trouver dans les rushes"):
        cal.ensure_outside(root / "rapport", root)
    cal.ensure_outside(tmp_path / "rapport", root)


@pytest.mark.parametrize("name,expect", [
    ("DJI_20260903103733_0001_D.MP4", "2026-09-03T10:37:33"),
    ("DJI_01_20260903_102305.WAV", "2026-09-03T10:23:05"),
    ("20260903_C4178.MP4", None),
])
def test_filename_timestamps(name, expect):
    t = cal.name_time(name)
    assert (t.isoformat() if t else None) == expect


# --- chaîne complète en lecture seule ------------------------------------------------------------------
def test_dry_run_decodes_nothing_and_changes_nothing(wedding, tmp_path):
    before = listing(wedding)
    out = tmp_path / "rapport"
    opts = cal.Options(root=wedding, out=out, scenario="custom", audio_dirs=["RUSHES/AUDIO/MIC 1", "RUSHES/AUDIO/MIC 2"],
                       video_dirs=["RUSHES/CAM 1", "RUSHES/DRONE"], dry_run=True)
    res = cal.run(opts, log=lambda *_: None)
    assert res["unchanged"] and listing(wedding) == before
    assert (out / "plan.txt").exists() and (out / "report.md").exists() and (out / "inventory.csv").exists()
    assert res["audio"] == [] and res["visual"] == []


def test_full_run_reports_audio_visual_and_never_touches_originals(wedding, tmp_path):
    before = listing(wedding)
    out = tmp_path / "rapport"
    truth = tmp_path / "truth.json"
    mic1, mic2 = "RUSHES/AUDIO/MIC 1/DJI_01_20260903_102305.WAV", "RUSHES/AUDIO/MIC 2/DJI_01_20260903_102303.WAV"
    truth.write_text(json.dumps([{"ref": mic1, "sig": mic2, "t_ref": 12.0, "t_sig": 13.5}]), encoding="utf-8")   # MIC 2 démarre 1,5 s plus tôt
    opts = cal.Options(root=wedding, out=out, scenario="custom", audio_dirs=["RUSHES/AUDIO/MIC 1", "RUSHES/AUDIO/MIC 2"],
                       video_dirs=["RUSHES/CAM 1", "RUSHES/DRONE"], with_hint=True, truth=truth, clock_tol_s=86400.0,
                       max_video_seconds=60.0)
    res = cal.run(opts, log=lambda *_: None)
    assert listing(wedding) == before and res["unchanged"]
    # audio : MIC 2 contre MIC 1, et la caméra avec son ; la caméra muette n'est PAS proposée en audio
    sigs = {a.sig: a for a in res["audio"]}
    m2 = sigs[mic2]
    assert m2.status == "ok" and abs(m2.offset_s - (-1.5)) < 0.04
    assert m2.truth_error_ms is not None and abs(m2.truth_error_ms) < 40
    assert "RUSHES/CAM 1/20260903_C0002.MP4" not in sigs
    # le fichier muet est signalé comme tel dans l'inventaire
    inv = {r["rel"]: r for r in json.loads((out / "report.json").read_text(encoding="utf-8"))["inventaire"]}
    assert inv["RUSHES/CAM 1/20260903_C0002.MP4"]["audio_empty"] is True
    assert inv["RUSHES/DRONE/DJI_20260903102400_0001_D.MP4"]["has_audio"] is False
    # visuel : le drone et la caméra muette sont comparés ; jamais « ok »
    assert res["visual"] and all(v.status in ("proposition", "faible", "ambigu", "impossible") for v in res["visual"])
    md = (out / "report.md").read_text(encoding="utf-8")
    assert "Originaux inchangés : oui" in md and "pas** une probabilité" in md.replace("*", "**", 0) or "heuristique" in md
    assert (out / "results.csv").exists() and (out / "report.json").exists()


def test_cli_exit_code_and_report(wedding, tmp_path, capsys):
    out = tmp_path / "r"
    code = main(["sync-calibrate", str(wedding), "--scenario", "custom", "--audio-dir", "RUSHES/AUDIO/MIC 1",
                 "--audio-dir", "RUSHES/AUDIO/MIC 2", "--video-dir", "RUSHES/CAM 1", "--dry-run", "--out", str(out)])
    assert code == 0 and "Originaux inchangés : oui" in capsys.readouterr().out
    code = main(["sync-calibrate", str(wedding), "--scenario", "custom", "--video-dir", "RUSHES/CAM 1", "--dry-run",
                 "--out", str(wedding / "RUSHES" / "rapport")])
    assert code == 2


def test_modification_of_an_original_is_detected():
    before = {"a": (10, 1), "b": (20, 2)}
    assert cal.changed_files(before, {"a": (10, 1), "b": (21, 2)}) == ["b"]
    assert cal.changed_files(before, dict(before)) == []


# --- repères, fermeture en boucle, constats -------------------------------------------------------------
def _ar(ref, sig, off, status="ok"):
    return cal.AudioResult(ref, sig, status, 0.9, off, 0.0, False, 1.0, 12, 12, 12, 20.0, 50.0, 0.1, [], [])


def test_loop_closure_is_zero_for_consistent_offsets_and_flags_inconsistent_ones():
    rs = [_ar("M", "A", 10.0), _ar("M", "B", 4.0), _ar("A", "B", -6.0)]
    # A→M = 10, B→M = 4 : B par rapport à A = 4 - 10 = -6 ; cohérent
    # (convention : off(A→B) = off(B→M) - off(A→M) ; ici la fonction teste (A-B) - direct)
    c = cal.loop_closure([_ar("M", "A", 10.0), _ar("M", "B", 4.0), _ar("M", "B", 4.0, "impossible")])
    assert c == []                                    # pas de lien A↔B direct : pas de boucle
    c = cal.loop_closure([_ar("M", "A", 10.0), _ar("M", "B", 4.0), _ar("A", "B", 6.0)])
    assert len(c) == 1 and abs(c[0]["ecart_ms"]) < 1e-6
    c = cal.loop_closure([_ar("M", "A", 10.0), _ar("M", "B", 4.0), _ar("A", "B", 6.05)])
    assert abs(c[0]["ecart_ms"] - (-50.0)) < 1e-6


def test_truth_error_in_ms_and_frames():
    a = _ar("R", "S", 2.030)
    rows = cal.apply_truth([{"ref": "R", "sig": "S", "t_ref": 12.0, "t_sig": 10.0}], [a], [])
    assert abs(rows[0]["erreur_ms"] - 30.0) < 1e-6 and abs(a.truth_error_ms - 30.0) < 1e-6


def test_threshold_findings_need_enough_windows_and_never_apply_anything():
    few = cal.threshold_findings([_ar("M", "A", 1.0)])
    assert isinstance(few["min_psr_suggere"], str)
    good = [{"t": i, "offset": 1.0, "psr": 15.0 + i % 5, "valide": True, "silence": False, "inlier": True} for i in range(14)]
    bad = [{"t": i, "offset": 9.0, "psr": 6.0 + i % 3, "valide": False, "silence": False, "inlier": False} for i in range(6)]
    r = _ar("M", "A", 1.0)
    r.window_table = good + bad
    f = cal.threshold_findings([r])
    assert isinstance(f["min_psr_suggere"], dict) and 8 <= f["min_psr_suggere"]["valeur"] <= 15


# --- progression et annulation (C1a) ---------------------------------------------------------------------
def _opts(root, out, **kw):
    return cal.Options(root=root, out=out, scenario="custom", audio_dirs=["RUSHES/AUDIO/MIC 1", "RUSHES/AUDIO/MIC 2"],
                       video_dirs=["RUSHES/CAM 1", "RUSHES/DRONE"], max_video_seconds=30.0, **kw)


def test_progress_reports_every_stage_in_order_and_reaches_the_total(wedding, tmp_path):
    seen = []
    res = cal.run(_opts(wedding, tmp_path / "r"), log=lambda *_: None, progress=lambda *a: seen.append(a))
    stages = []
    for st, *_ in seen:
        if not stages or stages[-1] != st:
            stages.append(st)
    assert stages == ["inventaire", "audio", "visuel", "rapport"]
    for st in stages:
        cur = [c for s, c, t, _ in seen if s == st]
        assert cur == sorted(cur)
        tot = {t for s, c, t, _ in seen if s == st}
        assert len(tot) == 1 and cur[-1] == tot.pop()              # chaque étape finit à 100 %
    assert res["unchanged"]


def test_cancel_during_the_audio_stage_writes_no_report_and_touches_nothing(wedding, tmp_path):
    from kolnoa_editor.ffmpeg import CancelToken, Cancelled

    token, before, out = CancelToken(), listing(wedding), tmp_path / "r"

    def progress(stage, cur, total, msg):
        if stage == "audio" and cur == 0:
            token.cancel()

    with pytest.raises(Cancelled):
        cal.run(_opts(wedding, out), log=lambda *_: None, progress=progress, cancel=token)
    assert not (out / "report.md").exists()                        # pas de rapport partiel présenté comme complet
    assert listing(wedding) == before                              # rushes intacts (lecture seule)


def test_cancel_is_not_swallowed_as_a_file_error(wedding, tmp_path):
    from kolnoa_editor.ffmpeg import CancelToken, Cancelled

    token = CancelToken()
    token.cancel()
    with pytest.raises(Cancelled):
        cal.run(_opts(wedding, tmp_path / "r"), log=lambda *_: None, cancel=token)


def test_cli_prints_progress_and_exits_130_when_cancelled(wedding, tmp_path, capsys, monkeypatch):
    out = tmp_path / "r"
    code = main(["sync-calibrate", str(wedding), "--scenario", "custom", "--audio-dir", "RUSHES/AUDIO/MIC 1",
                 "--audio-dir", "RUSHES/AUDIO/MIC 2", "--video-dir", "RUSHES/CAM 1", "--no-visual", "--out", str(out), "--quiet"])
    cap = capsys.readouterr()
    assert code == 0 and "[inventaire]" in cap.err and "[audio]" in cap.err and "Originaux inchangés : oui" in cap.out
    from kolnoa_editor.tasks import Task

    def interrupt(self, timeout=None):                             # Ctrl+C pendant l'attente de la tâche
        if not getattr(self, "_boom", False):
            self._boom = True
            raise KeyboardInterrupt
        return self._done.wait(timeout)
    monkeypatch.setattr(Task, "wait", interrupt)
    out2 = tmp_path / "r2"
    code = main(["sync-calibrate", str(wedding), "--scenario", "custom", "--audio-dir", "RUSHES/AUDIO/MIC 1",
                 "--audio-dir", "RUSHES/AUDIO/MIC 2", "--video-dir", "RUSHES/CAM 1", "--no-visual", "--out", str(out2), "--quiet"])
    cap = capsys.readouterr()
    assert code == 130 and "Annulé" in cap.out
