"""Parcours utilisateur COMPLET sur de vrais médias : importer → Monte-moi → consigne → retouche manuelle → enregistrer → rouvrir → MP4 vidéo + audio.
Le MP4 est relu : durée = timeline, image et son présents, un plan de coupe de V2 visible à l'image."""

import os
import shutil
import subprocess

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from kolnoa_editor.app import ProjectController
from kolnoa_editor.auto import StyleParams
from kolnoa_editor.probe import probe
from kolnoa_editor.timeline import Selection, SetClipProps

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg absent")


def gen(path, color, freq, dur):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c={color}:s=320x180:r=25:d={dur}",
                    "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={dur}", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(path)], check=True)


def center_color(path, t):
    r = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1", "-vf", "scale=1:1,format=rgb24",
                        "-f", "rawvideo", "-"], capture_output=True, check=True)
    return tuple(r.stdout[:3])


def mean_volume(path):
    r = subprocess.run(["ffmpeg", "-i", str(path), "-vn", "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True)
    for line in r.stderr.splitlines():
        if "mean_volume" in line:
            return float(line.split("mean_volume:")[1].split("dB")[0])
    return None


def test_full_user_journey(tmp_path):
    root = tmp_path / "mariage"
    gen(root / "MAIRIE" / "A7IV" / "C0001.MP4", "red", 440, 12)
    gen(root / "MAIRIE" / "A7IV" / "C0002.MP4", "green", 660, 12)
    gen(root / "AMBIANCE" / "DRONE" / "C0003.MP4", "blue", 880, 8)
    c = ProjectController.open(root, tmp_path / "data")
    assert c.import_rushes() == 3
    st = StyleParams(shot_min=1.5, shot_max=3.0)
    rep = c.build("Monte-moi cette séquence", None, st)
    assert rep.shots >= 6
    v = c.tl.clips_on("V1")
    # consigne ciblée sur une zone, puis retouche manuelle ailleurs
    r = c.command("raccourcis cette partie de 2 secondes", Selection.of_clips(c.tl, [x.id for x in v[1:4]]))
    assert r.frames_delta < 0
    v = c.tl.clips_on("V1")
    c.session.do(SetClipProps(v[0].id, {"note": "gardé"}))
    c.command("remplace par un plan de coupe", Selection.of_clips(c.tl, [v[2].id]))
    cut = c.tl.clips_on("V2")
    assert cut
    c.save("avant-export")
    again = ProjectController.open(root, tmp_path / "data")                  # réouverture : même timeline, même provenance
    assert again.tl.canonical() == c.tl.canonical() and again.session.provenance() == c.session.provenance()
    out = tmp_path / "film.mp4"
    rep = again.export(out)
    expected = max(x.end for x in again.tl.clips_on("V1")) / again.tl.fps
    info = probe(out)
    assert abs(info.duration - expected) < 0.15 and info.has_audio
    assert mean_volume(out) is not None and mean_volume(out) > -50          # du son, pas du silence
    mid = (cut[0].start + cut[0].duration / 2) / again.tl.fps
    px = center_color(out, mid)
    want = {"C0001": (255, 0, 0), "C0002": (0, 128, 0), "C0003": (0, 0, 255)}[cut[0].media.split("/")[-1][:5]]
    assert all(abs(a - b) < 70 for a, b in zip(px, want)), (cut[0].media, px)     # le plan de coupe (V2) est bien à l'image
    assert again.tl.clip(v[0].id).note == "gardé" and again.session.state_of(v[0].id) == "manuel"
    assert not any("erreur" in w.lower() for w in rep.warnings)


def test_project_takes_rush_format_and_refuses_fps_change_later(tmp_path):
    root = tmp_path / "m"
    p = root / "A" / "C1.MP4"
    p.parent.mkdir(parents=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=640x360:r=50:d=4", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=4", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(p)], check=True)
    c = ProjectController.open(root, tmp_path / "d")
    assert c.tl.fps == 25
    c.import_rushes()
    assert c.tl.fps == 50 and tuple(c.tl.resolution) == (640, 360)          # cadence et taille repris des rushes
    c.build("Monte-moi cette séquence", None, StyleParams(shot_min=1, shot_max=2))
    from kolnoa_editor.timeline import TimelineError
    with pytest.raises(TimelineError):
        c.apply_format({"fps": 25})
    assert Selection and abs(probe(c.export(tmp_path / "f.mp4", resolution="1080p").output).fps - 50) < 0.5
    assert ProjectController.open(root, tmp_path / "d").tl.fps == 50
