import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from kolnoa_editor.app.bins import BinFolders


def test_bins_never_touch_rushes_and_persist(tmp_path):
    rush = tmp_path / "a.mp4"
    rush.write_bytes(b"x")
    b = BinFolders(tmp_path / "d" / "bins.json")
    f = b.create("Cérémonie")
    b.assign(["a.mp4"], f)
    assert BinFolders(tmp_path / "d" / "bins.json").folder_of("a.mp4") == f
    with pytest.raises(ValueError):
        b.create("cérémonie")
    b.delete(f)
    assert b.folder_of("a.mp4") is None and rush.read_bytes() == b"x"


def test_assign_moves_between_folders(tmp_path):
    b = BinFolders(tmp_path / "bins.json")
    f1, f2 = b.create("A"), b.create("B")
    b.assign(["x.mp4"], f1)
    b.assign(["x.mp4"], f2)
    assert b.folder_of("x.mp4") == f2 and b.folders[0]["media"] == []
    b.assign(["x.mp4"], None)
    assert b.folder_of("x.mp4") is None
