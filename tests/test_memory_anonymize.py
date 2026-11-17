"""Anonymisation par LISTE BLANCHE : données sensibles injectées dans chaque champ, contrôle du JSON final, session protégée."""

import dataclasses
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from kolnoa_editor.experimental.memory_proto import analyze_timeline
from kolnoa_editor.experimental.memory_proto import __main__ as cli
from kolnoa_editor.experimental.memory_proto.anonymize import (FIELD_POLICY, _CLASSES, ExportRefused, anonymize_timeline,
                                                              export_timeline, lint_export, safe_output_path, write_export)
from kolnoa_editor.timeline import Angle, AudioStrip, Clip, Crossfade, Marker, Session, Timeline, Track
from kolnoa_editor.timeline.model import MasterRange, MulticamGroup, Subclip

SECRETS = ["Dupont", "Léa", "Neuilly", "Clients", "Rabbin", "C0042", "alliances", "Zoé", "Secret", "Montmartre", "regard"]


def sensitive_timeline() -> Timeline:
    """Une timeline dont CHAQUE champ texte libre porte un secret."""
    angles = [Angle(id="Cam Dupont A7", media="Clients/Dupont/Neuilly/C0042.MP4", status="ok", method="audio", offset=0.0,
                    note="Secret : caméra de Zoé"),
              Angle(id="Drone Montmartre", media="Clients/Dupont/DRONE/D01.MP4", status="ok", method="audio", offset=1.0,
                    has_audio=False)]
    tl = Timeline(
        title="Mariage Dupont — Léa & Zoé", fps=25,
        music={"path": "Z:/Clients/Dupont/musique.mp3", "start": 0, "gain_db": -3, "fade_out": 2, "licence": "Secret Records",
               "artiste": "Zoé"},
        tracks=[Track("V1 Dupont", "video", "main"), Track("Cutaways Neuilly", "video", "Rabbin"), Track("A1 Léa", "audio", "voix Secret")],
        clips=[Clip(id="s001", track="V1 Dupont", media="Clients/Dupont/Neuilly/C0042.MP4", start=0, duration=100, group="g001",
                    angle="Cam Dupont A7", label="Léa regard", note="garder le regard", extra={"lut": "Secret.cube"},
                    strips=[AudioStrip(src=0, name="Micro Rabbin")]),
               Clip(id="s002", track="V1 Dupont", media="Clients/Dupont/DRONE/D01.MP4", start=100, duration=100, src_in=10.0,
                    group="g001", angle="Drone Montmartre", parent="s001", link="lien-Dupont"),
               Clip(id="s003", track="Cutaways Neuilly", media="Clients/alliances.MP4", start=20, duration=25)],
        markers=[Marker("m001", 0, "chapter", "Mairie de Neuilly"), Marker("m002", 100, "chapter", "Cérémonie Dupont"),
                 Marker("m003", 100, "note", "Secret Montmartre")],
        groups=[MulticamGroup(id="g001", master="Cam Dupont A7", name="Groupe Léa", angles=angles)],
        masters=[MasterRange(id="r001", group="g001", start=0.0, end=5.0, source="Cam Dupont A7", reason="Secret : bruit")],
        crossfades=[Crossfade("x001", "s001", "s002", 0.5)],
        bin=[Subclip(id="b001", media="Clients/alliances.MP4", src_in=0.0, src_out=2.0, name="alliances Léa", tags=["Secret"], note="Zoé")])
    return tl


def test_policy_covers_every_field_of_every_exported_dataclass():
    """Un champ ajouté au modèle SANS décision d'export fait échouer ce test : la confidentialité n'est jamais supposée."""
    for name, cls in _CLASSES.items():
        assert {f.name for f in dataclasses.fields(cls)} == set(FIELD_POLICY[name]), f"{name}: champs sans décision d'export"


def test_no_secret_survives_in_the_exported_json_and_lint_passes():
    tl = sensitive_timeline()
    tl.validate()
    doc, report = export_timeline(tl)
    assert lint_export(doc, deny=SECRETS) == []
    blob = json.dumps(doc, ensure_ascii=False)
    for secret in SECRETS:
        assert secret.lower() not in blob.lower(), secret
    assert report["chapters_replaced_by_section_n"] == 2 and report["dropped"]["bin"] == 1
    assert report["dropped"]["music_keys"] == 2 and report["dropped"]["clip_texts"] >= 3


def test_exported_document_is_a_valid_timeline_with_identical_statistics():
    tl = sensitive_timeline()
    anon, _ = anonymize_timeline(tl, deny=SECRETS)
    a, b = analyze_timeline(tl), analyze_timeline(anon)
    for st in (a, b):
        st.pop("fingerprint")
        for s in st["sections"]:
            s["kind"] = "x"                                                  # seuls les libellés de section changent
    assert a == b
    assert tl.title.startswith("Mariage Dupont")                              # l'original n'est pas modifié


def test_chapter_text_is_sanitized_generic_kept_user_labels_applied():
    tl = sensitive_timeline()
    tl.markers.append(Marker("m004", 150, "chapter", "Danses"))
    doc, _ = export_timeline(tl)
    assert [m["text"] for m in doc["markers"] if m["kind"] == "chapter"] == ["section-1", "section-2", "danses"]
    doc2, rep = export_timeline(tl, {"Mairie de Neuilly": "mairie"})
    assert [m["text"] for m in doc2["markers"] if m["kind"] == "chapter"][0] == "mairie" and rep["chapters_user_labels"] == 1


def test_lint_rejects_free_text_unknown_keys_and_denied_terms_in_every_position():
    doc, _ = export_timeline(sensitive_timeline())
    for mutate in (lambda d: d["tracks"][0].__setitem__("id", "V1 Dupont"),
                   lambda d: d["clips"][0].__setitem__("label", "x y"),
                   lambda d: d["clips"][0]["strips"][0].__setitem__("name", "Micro"),
                   lambda d: d["music"].__setitem__("artiste", "Zoé"),
                   lambda d: d["groups"][0].__setitem__("name", "Groupe Léa"),
                   lambda d: d["groups"][0]["angles"][0].__setitem__("note", "mot"),
                   lambda d: d["markers"][0].__setitem__("text", "Mairie de Neuilly"),
                   lambda d: d["masters"][0].__setitem__("reason", "pourquoi"),
                   lambda d: d.__setitem__("champ_futur", "valeur"),
                   lambda d: d["clips"][0]["extra"].__setitem__("lut", "Secret.cube")):
        bad = json.loads(json.dumps(doc))
        mutate(bad)
        assert lint_export(bad), mutate
    assert lint_export(doc, deny=["rush-001"]) != []                          # le terme interdit est détecté même dans un identifiant
    assert lint_export(doc, deny=["LEA"]) == []                               # insensible à la casse et aux accents, absent ici


def test_anonymize_refuses_when_a_denied_term_would_remain_as_user_label():
    with pytest.raises(ExportRefused):
        anonymize_timeline(sensitive_timeline(), chapter_labels={"Mairie de Neuilly": "Neuilly"}, deny=["Neuilly"])


# --- protection des fichiers de session et des chemins de sortie ---------------------------------------------------------

def snapshot(dirpath: Path) -> dict[str, tuple[str, int]]:
    return {str(p.relative_to(dirpath)): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
            for p in sorted(dirpath.rglob("*")) if p.is_file()}


def make_session(tmp_path) -> Path:
    from tests.test_memory_proto import base, clip
    tl = base([clip("c001", "V1", 0, 100)], [Marker("m001", 0, "chapter", "mairie")])
    Session.create(tmp_path / "sess", tl)
    return tmp_path / "sess"


def test_output_inside_session_or_over_session_files_is_refused_and_session_stays_identical(tmp_path):
    sess = make_session(tmp_path)
    before = snapshot(sess)
    for target in (sess / "export.json", sess / "current.json", sess / "sub" / "x.json", sess / "base.json", sess / "history.jsonl"):
        with pytest.raises(ExportRefused):
            safe_output_path(sess, target)
    assert cli.main(["anonymize", str(sess), str(sess / "export.json")]) == 1
    assert cli.main(["analyze", str(sess)]) == 0
    assert snapshot(sess) == before                                          # octets ET dates de modification inchangés
    assert not (sess / "export.json").exists() and not list(sess.glob("*.part"))


def test_output_next_to_a_session_file_or_in_another_session_is_refused(tmp_path):
    sess = make_session(tmp_path)
    other = tmp_path / "other"
    Session.create(other, Timeline.from_dict(json.loads((sess / "current.json").read_text(encoding="utf-8"))))
    with pytest.raises(ExportRefused):
        safe_output_path(sess / "current.json", sess.parent / "other" / "anon.json")      # dossier d'une autre session
    with pytest.raises(ExportRefused):
        safe_output_path(sess / "current.json", sess / "anon.json")                         # fichier source donné directement
    with pytest.raises(ExportRefused):
        safe_output_path(sess / "current.json", sess / "current.json")


def test_existing_output_is_never_overwritten_without_force_and_symlinks_are_refused(tmp_path):
    sess = make_session(tmp_path)
    out = tmp_path / "anon.json"
    assert cli.main(["anonymize", str(sess), str(out)]) == 0
    first = out.read_bytes()
    out.write_bytes(first + b" ")                                            # le réalisateur a modifié le fichier
    assert cli.main(["anonymize", str(sess), str(out)]) == 1 and out.read_bytes() == first + b" "
    assert cli.main(["anonymize", str(sess), str(out), "--force"]) == 0 and out.read_bytes() == first
    link = tmp_path / "lien.json"
    os.symlink(out, link)
    assert cli.main(["anonymize", str(sess), str(link), "--force"]) == 1


def test_cli_end_to_end_refuses_on_denied_term_and_writes_nothing(tmp_path, capsys):
    tl = sensitive_timeline()
    src = tmp_path / "timeline.json"
    src.write_text(json.dumps(tl.to_dict(), ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "anon.json"
    mapping = tmp_path / "map.json"
    mapping.write_text(json.dumps({"Mairie de Neuilly": "Neuilly"}), encoding="utf-8")
    assert cli.main(["anonymize", str(src), str(out), "--chapters-map", str(mapping), "--deny", "Neuilly"]) == 1
    assert not out.exists() and "REFUSÉ" in capsys.readouterr().out
    assert cli.main(["anonymize", str(src), str(out), "--deny", ",".join(SECRETS)]) == 0
    text = out.read_text(encoding="utf-8")
    assert all(s.lower() not in text.lower() for s in SECRETS)
    printed = capsys.readouterr().out
    assert all(s not in printed for s in SECRETS)                            # le rapport imprimé ne contient aucun nom d'origine
    assert src.read_bytes() == json.dumps(tl.to_dict(), ensure_ascii=False).encode("utf-8")   # la source n'a pas bougé


def test_write_is_atomic_and_relints_the_final_text(tmp_path, monkeypatch):
    anon, _ = anonymize_timeline(sensitive_timeline())
    out = tmp_path / "a.json"
    with pytest.raises(ExportRefused):
        write_export(anon, tmp_path / "timeline.json", out, deny=["rush-001"])      # contrôle du texte final
    assert not out.exists() and not list(tmp_path.glob("*.part"))


# --- aucun fichier de session ni export réel dans le dépôt public ---------------------------------------------------------

def tracked_files() -> list[str]:
    root = Path(__file__).resolve().parents[1]
    r = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True)
    return r.stdout.splitlines() if r.returncode == 0 else []


def test_repository_contains_no_session_files_or_unaudited_anonymized_exports():
    files = tracked_files()
    assert [f for f in files if Path(f).name in ("base.json", "history.jsonl", "current.json", "library.json")] == []
    fixtures = [f for f in files if f.startswith("tests/data/anonymized/") and f.endswith(".json")]
    if fixtures:                                                              # une fixture réelle exige un audit écrit ET un lint propre
        assert "tests/data/anonymized/AUDIT.md" in files
        root = Path(__file__).resolve().parents[1]
        for f in fixtures:
            assert lint_export(json.loads((root / f).read_text(encoding="utf-8"))) == [], f
