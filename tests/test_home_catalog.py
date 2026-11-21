import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from kolnoa_editor.app.catalog import ProjectCatalog
from kolnoa_editor.app.controller import ProjectController
from kolnoa_editor.app.media_manifest import MediaManifest


def test_new_project_is_empty_openable_and_listed(tmp_path):
    cat = ProjectCatalog(tmp_path / "base")
    p = cat.create("Mariage Sarah & David")
    assert p.suffix == ".klnprj" and p.is_file()
    m = MediaManifest(p)
    ctl = ProjectController.open(m.root, m.data_dir)           # le même chemin que le lanceur
    assert not ctl.rushes and ctl.tl.title
    e = cat.entries()
    assert [x["name"] for x in e] == [p.stem] and e[0]["rushes"] == 0
    with pytest.raises(ValueError):
        cat.create("mariage sarah & david")                   # nom déjà pris : jamais d'écrasement
    with pytest.raises(ValueError):
        cat.create("   ")


def test_history_orders_forgets_and_never_deletes(tmp_path):
    cat = ProjectCatalog(tmp_path / "base")
    a, b = cat.create("A"), cat.create("B")
    cat.touch(a)
    assert [x["name"] for x in cat.entries()][0] == "A"
    cat.forget(a)
    assert (tmp_path / "base" / "A").is_dir()                  # rien n'est supprimé du disque
    rush_dir = tmp_path / "rushes"
    rush_dir.mkdir()
    cat.touch(rush_dir)
    assert {x["kind"] for x in cat.entries()} >= {"dossier", "projet"}
    cat.touch(tmp_path / "disparu.klnprj")                      # entrée morte : ignorée, pas d'erreur
    assert all(x["name"] != "disparu" for x in cat.entries())


def test_home_dialog_creates_and_returns_path(tmp_path):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from kolnoa_editor.ui.home import HomeDialog
    d = HomeDialog(ProjectCatalog(tmp_path / "base"))
    d.name.setText("Test")
    d.create_new()
    assert d.result_path and d.result_path.name == "Test.klnprj"
