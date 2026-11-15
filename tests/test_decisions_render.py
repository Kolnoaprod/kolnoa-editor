import pytest

from kolnoa_editor.comments import add_comment, load_comments, to_prompt
from kolnoa_editor.decision_list import DecisionList, DecisionListError, Music, Segment
from kolnoa_editor.probe import probe
from kolnoa_editor.project import Project
from kolnoa_editor.proxies import build_proxies
from kolnoa_editor.renderer import render
from kolnoa_editor.scanner import scan

SONY = "MAIRIE/A7IV/20261011_C4837.MP4"
DRONE = "SOIREE/DRONE/DJI_20261108192622_0015_D.MP4"
CANON = "SOIREE/CANON/A_0009C238A261108_113335HL_CANON.MP4"


def make_dl(**kw):
    return DecisionList(
        title="Test",
        version=1,
        resolution=(1280, 720),
        segments=[
            Segment("s001", SONY, 1.0, 3.0, fade_in=0.3),
            Segment("s002", DRONE, 0.5, 2.5, audio="mute"),
            Segment("s003", CANON, 0.0, 2.0, fade_out=0.5),
        ],
        **kw,
    )


def test_roundtrip_and_duration(tmp_path):
    dl = make_dl(music=Music("musique.mp3", start=1.0, gain_db=-3))
    path = tmp_path / "v.json"
    dl.save(path)
    back = DecisionList.load(path)
    assert back == dl and back.duration == pytest.approx(6.0)


def test_locate_maps_render_time_to_segment():
    dl = make_dl()
    seg, off = dl.locate(2.5)
    assert seg.id == "s002" and off == pytest.approx(0.5)
    assert dl.locate(0.0)[0].id == "s001"
    assert dl.start_of("s003") == pytest.approx(4.0)
    with pytest.raises(DecisionListError):
        dl.locate(99)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.segments.append(Segment("s001", SONY, 0, 1)),            # id en double
        lambda d: d.segments.append(Segment("s9", SONY, 2, 1)),              # fin avant début
        lambda d: d.segments.append(Segment("s9", SONY, 0, 1, audio="x")),   # mode audio
        lambda d: d.segments.append(Segment("s9", SONY, 0, 1, fade_in=2)),   # fondu trop long
        lambda d: d.segments.append(Segment("s9", "../hors.mp4", 0, 1)),     # sort du projet
        lambda d: d.segments.append(Segment("s9", "absent.mp4", 0, 1)),      # fichier absent
    ],
)
def test_validation_rejects_bad_lists(wedding, mutate):
    dl = make_dl()
    mutate(dl)
    with pytest.raises((DecisionListError, ValueError)):
        dl.validate(Project(wedding))


def test_valid_list_passes(wedding):
    make_dl().validate(Project(wedding))


def test_proxies_created_then_skipped(wedding):
    project = Project(wedding)
    files = scan(project)
    created, skipped = build_proxies(project, files, height=360)
    assert created == 3 and skipped == 0  # 3 vidéos, l'audio est ignoré
    assert probe(project.proxy_path(SONY)).height == 360
    created, skipped = build_proxies(project, files, height=360)
    assert created == 0 and skipped == 3


def test_render_proxy_and_final_with_music(wedding):
    project = Project(wedding)
    build_proxies(project, scan(project), height=360)
    dl = make_dl(music=Music("musique.mp3", start=2.0, gain_db=-6, fade_out=1.0), source_gain_db=-10)

    quick = render(project, dl, "proxy")
    info = probe(quick)
    assert info.duration == pytest.approx(6.0, abs=0.25)
    assert (info.width, info.height) == (640, 360)
    assert info.has_audio and info.fps == pytest.approx(25, abs=0.01)

    final = render(project, dl, "final")
    info = probe(final)
    assert info.duration == pytest.approx(6.0, abs=0.25)
    assert (info.width, info.height) == (1280, 720)


def test_render_without_music_keeps_silent_drone_segment(wedding):
    project = Project(wedding)
    out = render(project, make_dl(), "final", out=wedding / ".kolnoa" / "renders" / "nomusic.mp4")
    assert probe(out).has_audio


def test_comments_attach_to_segments(wedding):
    project = Project(wedding)
    dl = make_dl()
    c = add_comment(project, dl, 2.5, "  couper plus tôt  ")
    assert (c.segment_id, c.clip) == ("s002", DRONE)
    assert c.source_time == pytest.approx(1.0)  # début 0.5 + 0.5 dans le segment
    assert c.text == "couper plus tôt"
    add_comment(project, dl, 5.0, "trop long")
    assert [x.id for x in load_comments(project, 1)] == ["c001", "c002"]
    prompt = to_prompt(project, dl)
    assert "segment s002" in prompt and "segment s003" in prompt and "ids des segments" in prompt
