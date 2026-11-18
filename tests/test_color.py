"""PR C0 : étalonnage non destructif (modèle, opérations, filtres ffmpeg mesurés sur de vrais pixels, rendu, CLI)."""

import copy
import subprocess

import numpy as np
import pytest

from kolnoa_editor.cli import main
from kolnoa_editor.decision_list import DecisionList, Segment
from kolnoa_editor.ffmpeg import ffmpeg_bin
from kolnoa_editor.grade import _q, grade_filter
from kolnoa_editor.project import Project
from kolnoa_editor.renderer import _video_filter, segment_cache_key
from kolnoa_editor.timeline import (AddGroup, AddTrack, ColorError, ColorGrade, LockedError, Session, SetClipProps, SetColorGrade,
                                    SplitClip, Timeline, TimelineError, add_multicam_clip, execute, from_decision_list,
                                    grade_warnings, op_from_dict, stack_for_clip, to_decision_list, RemoveClip)

WAV = "AUDIO/recorder.wav"
SONY = "MAIRIE/A7IV/20261011_C4837.MP4"
OSMO = "MAIRIE/OP4/DJI_20261011100000_0001_D.MP4"


def tl_one():
    return from_decision_list(DecisionList(title="T", resolution=(640, 360),
                                           segments=[Segment("s001", SONY, 0, 4), Segment("s002", SONY, 4, 8)]))


def tl_multicam():
    tl = tl_one()
    execute(tl, AddTrack({"id": "V2", "kind": "video", "role": "coupe"}))
    execute(tl, AddGroup({"id": "g001", "master": "REC", "name": "G", "angles": [
        {"id": "REC", "media": WAV, "kind": "audio", "status": "reference", "method": "reference", "confidence": 1.0},
        {"id": "A7", "media": SONY, "status": "ok", "method": "audio", "confidence": 0.95, "offset": 12.0}]}))
    execute(tl, add_multicam_clip(tl, "g001", "A7", "V2", 0, 50, group_in=30.0, clip_id="s010"))
    return tl


# --- modèle ---------------------------------------------------------------------
def test_grade_validation_refuses_bad_values():
    for bad in ({"input_profile": "slog"}, {"exposure_ev": 9}, {"lut_strength": 1.5}, {"tint": "a"},
                {"saturation": float("nan")}, {"contrast": True}, {"lut": {"path": ""}}, {"lut": {"path": "a.png"}},
                {"lut": {"path": "a.cube", "rights": "oui"}}, {"lut": {"path": "a.cube", "x": 1}}):
        with pytest.raises(ColorError):
            ColorGrade(**bad)
    assert ColorGrade().neutral and not ColorGrade().active
    assert ColorGrade(exposure_ev=0.5).active and not ColorGrade(exposure_ev=0.5, enabled=False).active


def test_auto_profile_means_unconfirmed_and_nothing_is_assumed():
    g = ColorGrade()
    assert g.input_profile == "auto"
    assert grade_filter([g.to_dict()], lambda p: p) == ""                 # aucun filtre, aucune conversion devinée


# --- opérations -------------------------------------------------------------------
def test_set_color_grade_is_reversible_and_serializable():
    tl = tl_one()
    before = tl.canonical()
    op = SetColorGrade("clip:s001", ColorGrade(exposure_ev=0.5, temperature_k=300).to_dict())
    inv = execute(tl, op)
    assert tl.grades["clip:s001"]["exposure_ev"] == 0.5
    assert execute(tl, op_from_dict(op.to_dict())) is not None            # journalisable
    execute(tl, inv)
    assert tl.canonical() == before and "clip:s001" not in tl.grades


def test_replace_then_undo_restores_the_previous_grade():
    tl = tl_one()
    execute(tl, SetColorGrade("global", ColorGrade(saturation=10).to_dict()))
    inv = execute(tl, SetColorGrade("global", ColorGrade(saturation=-20).to_dict()))
    assert tl.grades["global"]["saturation"] == -20
    execute(tl, inv)
    assert tl.grades["global"]["saturation"] == 10


def test_scope_errors_and_locks():
    tl = tl_one()
    for scope in ("clip:s999", "angle:g001/A7", "nawak", "angle:x", "clip:"):
        with pytest.raises(TimelineError):
            execute(tl, SetColorGrade(scope, ColorGrade(contrast=5).to_dict()))
    with pytest.raises(TimelineError):
        execute(tl, SetColorGrade("global", {"exposure_ev": 99}))
    execute(tl, SetClipProps("s001", {"locked": True}))
    with pytest.raises(LockedError):
        execute(tl, SetColorGrade("clip:s001", ColorGrade(contrast=5).to_dict()))
    assert tl.grades == {}                                                # refus : rien n'a changé


def test_stack_order_is_global_then_angle_then_clip():
    tl = tl_multicam()
    execute(tl, SetColorGrade("clip:s010", ColorGrade(contrast=30).to_dict()))
    execute(tl, SetColorGrade("angle:g001/A7", ColorGrade(exposure_ev=0.7).to_dict()))
    execute(tl, SetColorGrade("global", ColorGrade(saturation=-10).to_dict()))
    stack = stack_for_clip(tl, tl.clip("s010"))
    assert [(g.saturation, g.exposure_ev, g.contrast) for g in stack] == [(-10, 0, 0), (0, 0.7, 0), (0, 0, 30)]
    plain = stack_for_clip(tl, tl.clip("s001"))                            # plan non multicam : global seulement
    assert [g.saturation for g in plain] == [-10]


def test_disabled_and_neutral_grades_are_skipped():
    tl = tl_one()
    execute(tl, SetColorGrade("global", ColorGrade().to_dict()))
    execute(tl, SetColorGrade("clip:s001", ColorGrade(exposure_ev=1, enabled=False).to_dict()))
    assert stack_for_clip(tl, tl.clip("s001")) == []


def test_splitting_a_graded_clip_keeps_the_color_on_both_halves():
    tl = tl_one()
    execute(tl, SetColorGrade("clip:s001", ColorGrade(exposure_ev=0.4).to_dict()))
    execute(tl, SplitClip("s001", 50, "s003"))
    assert tl.clip("s003").parent == "s001"
    assert [g.exposure_ev for g in stack_for_clip(tl, tl.clip("s003"))] == [0.4]
    execute(tl, SetColorGrade("clip:s003", ColorGrade(exposure_ev=-1).to_dict()))   # la 2de moitié peut diverger
    assert [g.exposure_ev for g in stack_for_clip(tl, tl.clip("s003"))] == [-1]
    assert [g.exposure_ev for g in stack_for_clip(tl, tl.clip("s001"))] == [0.4]


def test_removing_a_clip_keeps_its_grade_dormant_so_undo_finds_it_again():
    tl = tl_one()
    execute(tl, SetColorGrade("clip:s002", ColorGrade(exposure_ev=0.3).to_dict()))
    inv = execute(tl, RemoveClip("s002"))
    tl.validate()                                                          # pas d'erreur : réglage dormant
    assert any("orphelin" in w for w in grade_warnings(tl))
    execute(tl, inv)
    assert [g.exposure_ev for g in stack_for_clip(tl, tl.clip("s002"))] == [0.3]
    assert not any("orphelin" in w for w in grade_warnings(tl))


def test_old_timelines_without_grades_still_load_and_roundtrip():
    d = tl_one().to_dict()
    d.pop("grades")
    tl = Timeline.from_dict(d)
    assert tl.grades == {}
    tl2 = Timeline.from_dict(copy.deepcopy(tl.to_dict()))
    assert tl2.canonical() == tl.canonical()


def test_warnings_lut_rights_log_profile_and_mismatch(tmp_path):
    tl = tl_one()
    lut = {"path": "luts/doux.cube", "input_space": "rec709", "output_space": "rec709"}
    execute(tl, SetColorGrade("global", ColorGrade(lut=lut, input_profile="s-log3").to_dict()))
    execute(tl, SetColorGrade("clip:s001", ColorGrade(input_profile="s-log3", exposure_ev=0.2).to_dict()))
    w = " | ".join(grade_warnings(tl, lambda p: False))
    assert "droits d'utilisation" in w and "introuvable" in w and "attend 'rec709'" in w and "restera en LOG" in w
    execute(tl, SetColorGrade("global", ColorGrade(lut={**lut, "rights": "confirmed"}).to_dict()))
    assert "droits d'utilisation" not in " | ".join(grade_warnings(tl))


# --- filtres ffmpeg ----------------------------------------------------------------
def test_filter_path_escaping_handles_windows_paths():
    q = _q(r"C:\Kolnoa\LUTs\it's [x], ok.cube")
    assert "\\" not in q.replace(r"\:", "").replace(r"\'", "").replace(r"\,", "").replace(r"\[", "").replace(r"\]", "")
    assert q.startswith(r"C\:/Kolnoa/LUTs/")


def test_filter_chain_has_a_fixed_order_and_skips_inactive():
    lut = {"path": "a.cube"}
    f = grade_filter([ColorGrade(lut=lut, exposure_ev=1, temperature_k=200, tint=10, contrast=5).to_dict(),
                      ColorGrade().to_dict()], lambda p: p)
    pos = [f.index(k) for k in ("lut3d", "exposure", "colortemperature", "colorchannelmixer", "eq=")]
    assert pos == sorted(pos)
    assert grade_filter(None, lambda p: p) == "" and grade_filter([], lambda p: p) == ""


def pixel(rgb, stack, tmp_path, project=None):
    """Couleur moyenne (R, G, B) d'une image unie passée dans le vrai filtre de rendu."""
    dl = DecisionList(title="T", resolution=(64, 64), fps=25)
    seg = Segment("s001", "x.mp4", 0.0, 1.0, color=stack)
    vf = _video_filter(dl, seg, "final", project)
    out = tmp_path / "o.raw"
    subprocess.run([ffmpeg_bin(), "-y", "-hide_banner", "-v", "error", "-f", "lavfi", "-i",
                    f"color=c=0x{rgb}:s=64x64:r=25:d=1", "-vf", vf, "-frames:v", "1", "-pix_fmt", "rgb24",
                    "-f", "rawvideo", str(out)], check=True)
    return np.fromfile(out, dtype=np.uint8).reshape(-1, 3).mean(0)


def g(**kw):
    return [ColorGrade(**kw).to_dict()]


def test_pixels_neutral_grade_changes_nothing(tmp_path):
    base = pixel("808080", None, tmp_path)
    assert np.allclose(pixel("808080", g(), tmp_path), base, atol=1)


def test_pixels_exposure_brightens_and_darkens(tmp_path):
    base = pixel("606060", None, tmp_path).mean()
    assert pixel("606060", g(exposure_ev=1), tmp_path).mean() > base + 20
    assert pixel("606060", g(exposure_ev=-1), tmp_path).mean() < base - 20


def test_pixels_temperature_warm_is_redder_cool_is_bluer(tmp_path):
    warm = pixel("808080", g(temperature_k=1500), tmp_path)
    cool = pixel("808080", g(temperature_k=-1500), tmp_path)
    assert warm[0] > warm[2] + 8 and cool[2] > cool[0] + 8


def test_pixels_tint_positive_goes_magenta(tmp_path):
    p = pixel("808080", g(tint=80), tmp_path)
    assert p[1] < (p[0] + p[2]) / 2 - 8


def test_pixels_saturation_minus_100_is_gray_and_contrast_moves_away_from_mid(tmp_path):
    p = pixel("c04030", g(saturation=-100), tmp_path)
    assert p.max() - p.min() < 6
    assert pixel("b0b0b0", g(contrast=60), tmp_path).mean() > pixel("b0b0b0", None, tmp_path).mean() + 10


INVERT = "LUT_3D_SIZE 2\n" + "\n".join(f"{1-r} {1-gg} {1-b}" for b in (0, 1) for gg in (0, 1) for r in (0, 1)) + "\n"


def test_pixels_lut_strength_blends_between_original_and_lut(tmp_path):
    project_dir = tmp_path / "mariage"
    (project_dir / "luts").mkdir(parents=True)
    (project_dir / "luts" / "inv.cube").write_text(INVERT)
    project = Project(project_dir)
    lut = {"path": "luts/inv.cube", "rights": "confirmed"}
    full = pixel("c06030", g(lut=lut), tmp_path, project)
    assert abs(full[0] - 0x3f) < 8 and abs(full[2] - 0xcf) < 8            # relatif au projet : image inversée
    half = pixel("c06030", g(lut=lut, lut_strength=0.5), tmp_path, project)
    assert np.allclose(half, (full + pixel("c06030", None, tmp_path)) / 2, atol=6)
    zero = pixel("c06030", g(lut=lut, lut_strength=0.0), tmp_path, project)
    assert np.allclose(zero, pixel("c06030", None, tmp_path), atol=2)      # intensité 0 = pas de LUT


# --- rendu et cache ---------------------------------------------------------------
def test_export_carries_the_effective_stack_and_old_lists_keep_their_shape():
    tl = tl_one()
    execute(tl, SetColorGrade("global", ColorGrade(saturation=-10).to_dict()))
    execute(tl, SetColorGrade("clip:s002", ColorGrade(exposure_ev=0.5).to_dict()))
    dl, warnings = to_decision_list(tl)
    assert [x["saturation"] for x in dl.get("s001").color] == [-10]
    assert [x["saturation"] for x in dl.get("s002").color] == [-10, 0]
    assert [x["exposure_ev"] for x in dl.get("s002").color] == [0.0, 0.5]
    plain, _ = to_decision_list(tl_one())
    assert all("color" not in s for s in plain.to_dict()["segments"])
    again = DecisionList.from_dict(dl.to_dict())
    assert again.get("s002").color == dl.get("s002").color


def test_multicam_export_is_still_refused_even_with_color():
    tl = tl_multicam()
    execute(tl, SetColorGrade("angle:g001/A7", ColorGrade(exposure_ev=0.5).to_dict()))
    with pytest.raises(TimelineError):
        to_decision_list(tl)


def test_cache_key_changes_with_color_and_lut_file_but_not_without(tmp_path):
    src = tmp_path / "a.mp4"
    src.write_bytes(b"x")
    dl = DecisionList(title="T", segments=[Segment("s001", "a.mp4", 0, 2)])
    base = segment_cache_key(dl, dl.get("s001"), "final", src)
    again = segment_cache_key(dl, dl.get("s001"), "final", src)
    assert base == again
    lut = tmp_path / "x.cube"
    lut.write_text(INVERT)
    dl.get("s001").color = g(lut={"path": str(lut)})
    k1 = segment_cache_key(dl, dl.get("s001"), "final", src)
    assert k1 != base
    lut.write_text(INVERT + "# modifiée\n")
    assert segment_cache_key(dl, dl.get("s001"), "final", src) != k1       # fichier LUT modifié : segment invalidé
    dl.get("s001").color = g(exposure_ev=1)
    assert segment_cache_key(dl, dl.get("s001"), "final", src) not in (base, k1)


# --- CLI --------------------------------------------------------------------------
def test_cli_color_set_show_clear_and_undo(mariage, capsys, tmp_path):
    dl = DecisionList(title="T", resolution=(640, 360), segments=[Segment("s001", "MAIRIE/A7IV/20261011_C4837.MP4", 0.0, 4.0)])
    f = tmp_path / "v1.json"
    dl.save(f)
    root = str(mariage)
    assert main(["timeline-import", root, str(f)]) == 0
    assert main(["color-set", root, "clip:s001", "--exposure", "0.5", "--temperature", "300",
                 "--profile", "s-cinetone", "--lut", "luts/doux.cube", "--lut-strength", "0.8"]) == 0
    err = capsys.readouterr().err
    assert "droits d'utilisation" in err                                    # LUT non confirmée : signalé
    assert main(["color-show", root]) == 0
    out = capsys.readouterr()
    assert "clip:s001" in out.out and "expo +0.5 IL" in out.out and "introuvable" in out.err
    grades = Session.open(Project(mariage).timeline_dir).tl.grades
    assert grades["clip:s001"]["lut"]["rights"] == "unknown" and grades["clip:s001"]["lut_strength"] == 0.8
    assert main(["color-set", root, "clip:s001", "--exposure", "-1"]) == 0   # modifie sans perdre le reste
    g1 = Session.open(Project(mariage).timeline_dir).tl.grades["clip:s001"]
    assert g1["exposure_ev"] == -1 and g1["temperature_k"] == 300
    assert main(["color-clear", root, "clip:s001"]) == 0
    assert Session.open(Project(mariage).timeline_dir).tl.grades == {}
    assert main(["timeline-undo", root]) == 0
    assert "clip:s001" in Session.open(Project(mariage).timeline_dir).tl.grades
    assert main(["color-set", root, "clip:s001", "--exposure", "50"]) == 2   # refus propre, pas de traceback
    assert main(["color-set", root, "clip:zzz", "--exposure", "1"]) == 2


def test_end_to_end_render_applies_color_and_never_touches_the_originals(mariage, tmp_path):
    src = mariage / "MAIRIE" / "A7IV" / "20261011_C4837.MP4"
    import hashlib
    digest = lambda: hashlib.sha256(src.read_bytes()).hexdigest()  # noqa: E731
    before = (src.stat().st_size, src.stat().st_mtime_ns, digest())
    dl = DecisionList(title="T", resolution=(640, 360), segments=[Segment("s001", "MAIRIE/A7IV/20261011_C4837.MP4", 0.0, 2.0)])
    f = tmp_path / "v1.json"
    dl.save(f)
    root = str(mariage)
    assert main(["timeline-import", root, str(f)]) == 0

    def chroma_spread():
        assert main(["timeline-render", root]) == 0
        out = sorted(Project(mariage).renders_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime_ns)[-1]
        raw = tmp_path / "f.raw"
        subprocess.run([ffmpeg_bin(), "-y", "-hide_banner", "-v", "error", "-i", str(out), "-frames:v", "1",
                        "-pix_fmt", "rgb24", "-f", "rawvideo", str(raw)], check=True)
        px = np.fromfile(raw, dtype=np.uint8).reshape(-1, 3).astype(int)
        return int(np.abs(px[:, 0] - px[:, 2]).mean())

    colored = chroma_spread()
    assert colored > 5                                                      # mires de couleur : l'image est bien colorée
    assert main(["color-set", root, "global", "--saturation", "-100"]) == 0
    assert chroma_spread() < 3                                              # le rendu est devenu gris
    assert main(["color-clear", root, "global"]) == 0
    assert abs(chroma_spread() - colored) <= 1                              # retirer la couleur redonne l'image d'origine
    assert (src.stat().st_size, src.stat().st_mtime_ns, digest()) == before   # original intact (octet pour octet)


def test_multicam_group_and_color_coexist_and_undo_everything(tmp_path):
    """Non-régression PR #9 : la couleur ne touche ni la synchro des angles ni les plans multicam."""
    tl = tl_multicam()
    snap = tl.canonical()
    multicam_before = [c for c in snap["clips"] if c["group"]] , snap["groups"]
    invs = [execute(tl, SetColorGrade("angle:g001/A7", ColorGrade(exposure_ev=0.5).to_dict())),
            execute(tl, SetColorGrade("clip:s010", ColorGrade(saturation=-20).to_dict())),
            execute(tl, SetColorGrade("global", ColorGrade(contrast=10).to_dict()))]
    after = tl.canonical()
    assert (([c for c in after["clips"] if c["group"]], after["groups"])) == multicam_before   # clips et groupes identiques
    tl.validate()
    for inv in reversed(invs):
        execute(tl, inv)
    assert tl.canonical() == snap
    d = Timeline.from_dict(tl.to_dict())
    assert d.canonical() == snap
