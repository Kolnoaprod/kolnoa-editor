"""Choix d'angle sur de VRAIES vidéos (ffmpeg) : flou, sombre, équivalents, non synchronisé ; continuité ; cache ; parcours manuel → consigne → réouverture → export."""

import shutil

import pytest

from kolnoa_editor.app import ProjectController
from kolnoa_editor.auto import Moment, StyleParams
from kolnoa_editor.auto.visual import VisualParams
from kolnoa_editor.probe import probe
from kolnoa_editor.timeline import Selection
from test_multicam_auto import event_audio, info, make_cam

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg absent")


def project(tmp_path, cams):
    """cams : [(nom, début de l'événement, vf, horodatage)]"""
    root = tmp_path / "m"
    audio = event_audio(30, 3)
    rushes = []
    for name, t0, vf, stamp in cams:
        rel = f"CEREMONIE/{name}/C.MP4"
        make_cam(root / rel, audio, t0, 30, None, vf)
        rushes.append(info(rel, stamp, 30 - t0, "CEREMONIE", name, [Moment(0.0, 30.0 - t0)]))
    c = ProjectController.open(root, tmp_path / "d")
    c.add_rushes(rushes)
    return c


def run(c, **kw):
    return c.build_with_multicam(style=StyleParams(shot_min=2, shot_max=4), analyzer=lambda r, p: r.moments, **kw)


def angle_runs(c):
    return [(x.angle, x.start, x.end) for x in c.tl.clips_on("V1")]


def test_blurry_dark_and_sharp_angles(tmp_path):
    c = project(tmp_path, [("A", 0, "boxblur=14:5", "2026-10-11T10:00:00"),          # maître, flou
                           ("B", 3, None, "2026-10-11T10:00:03"),                       # net, démarre 3 s plus tard
                           ("C", 0, "eq=brightness=-0.75", "2026-10-11T10:00:00")])     # très sombre
    rep = run(c)
    g = c.tl.groups[0]
    byname = {a.id: a.media.split("/")[1] for a in g.angles}
    used = [byname[x.angle] for x in c.tl.clips_on("V1")]
    assert "C" not in used                                                           # l'angle sombre n'est jamais retenu
    assert used[0] == "A" and used[-1] == "B"                                        # avant 3 s seul le maître couvre ; ensuite le net
    assert len(used) <= 3                                                            # continuité : pas de coupes à intervalle fixe
    why = c.tl.clips_on("V1")[-1].extra["angle_choice"]
    assert why["analyse"] == "visuelle" and "meilleure" in why["motif"] and len(why["scores"]) >= 2
    a = rep["groups"][0]["analysis"]
    assert a["frames"] > 20 and a["seconds"] >= 0                                    # coût mesuré et rapporté
    assert rep["groups"][0]["skipped"] == []


def test_equivalent_angles_make_no_cuts(tmp_path):
    c = project(tmp_path, [("A", 0, None, "2026-10-11T10:00:00"), ("B", 0, None, "2026-10-11T10:00:00")])
    run(c)
    v = c.tl.clips_on("V1")
    assert len(v) == 1 and v[0].angle == c.tl.groups[0].master                        # aucune alternance gratuite


def test_cache_and_switch_off(tmp_path):
    c = project(tmp_path, [("A", 0, "boxblur=14:5", "2026-10-11T10:00:00"), ("B", 0, None, "2026-10-11T10:00:00")])
    r1 = run(c)
    c.undo()
    r2 = run(c)
    assert r1["groups"][0]["analysis"]["cache_hits"] == 0 and r2["groups"][0]["analysis"]["cache_hits"] == 2     # 2e passe : tout vient du cache
    c.undo()
    r3 = run(c, visual=VisualParams(enabled=False))
    assert r3["groups"][0]["analysis"] is None
    v = c.tl.clips_on("V1")
    assert len(v) == 1 and v[0].angle == c.tl.groups[0].master and v[0].extra["angle_choice"]["analyse"] == "repli"


def test_auto_then_manual_angle_then_directive_then_reopen_and_export(tmp_path):
    c = project(tmp_path, [("A", 0, "boxblur=14:5", "2026-10-11T10:00:00"), ("B", 0, None, "2026-10-11T10:00:00")])
    run(c)
    g = c.tl.groups[0]
    sharp = next(a.id for a in g.angles if "/B/" in a.media)
    master = next(a.id for a in g.angles if a.id != sharp)                            # on impose l'angle FLOU, contre le choix du logiciel
    first = c.tl.clips_on("V1")[0]
    assert first.angle == sharp                                                       # le net est choisi d'office
    from kolnoa_editor.timeline import Transaction
    from kolnoa_editor.timeline.ops import switch_angle
    c.session.do(Transaction([switch_angle(c.tl, first.id, master)], "angle"), "angle")        # le réalisateur impose le maître
    assert c.tl.clip(first.id).angle == master and c.session.state_of(first.id) == "manuel"
    before_sync = {a.id: (a.offset, a.status) for a in c.tl.groups[0].angles}
    c.command("raccourcis de 3 secondes", Selection.of_clips(c.tl, [first.id]))      # consigne ciblée sur le plan retouché à la main : refusée ou limitée ?
    assert c.tl.clip(first.id).angle == master                                         # la correction manuelle d'angle survit
    assert {a.id: (a.offset, a.status) for a in c.tl.groups[0].angles} == before_sync  # la synchro n'a pas bougé
    c.save("v")
    again = ProjectController.open(tmp_path / "m", tmp_path / "d")
    assert again.tl.canonical() == c.tl.canonical() and again.session.provenance() == c.session.provenance()
    out = tmp_path / "f.mp4"
    again.export(out)
    i = probe(out)
    assert abs(i.duration - max(x.end for x in again.tl.clips_on("V1")) / again.tl.fps) < 0.2 and i.has_audio
