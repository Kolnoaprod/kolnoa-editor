"""Kolnoa Editor V0.1 — point d'entrée de l'EXE portable (éditeur complet : importer → monter → retoucher → enregistrer → exporter).

  KolnoaEditor.exe                  → choisit le dossier de rushes puis ouvre la fenêtre
  KolnoaEditor.exe <dossier>        → ouvre ce dossier de rushes
  KolnoaEditor.exe --demo <dossier> → crée 3 rushes synthétiques dans <dossier> (parcours de test reproductible) puis l'ouvre
  KolnoaEditor.exe --selftest       → (CI) parcours complet sans fenêtre : import, montage, consigne, enregistrement, réouverture, export MP4
Les rushes ne sont JAMAIS modifiés. Le projet (journal, proxys, rendus) vit dans Documents\\Kolnoa_Projets\\<dossier>-<id>."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
if (HERE / "bin").is_dir():
    os.environ["PATH"] = str(HERE / "bin") + os.pathsep + os.environ.get("PATH", "")
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))


def data_dir_for(root: Path) -> Path:
    base = Path(os.environ.get("USERPROFILE") or Path.home()) / "Documents" / "Kolnoa_Projets"
    ident = hashlib.sha1(str(root.resolve()).lower().encode()).hexdigest()[:8]
    return base / f"{root.name or 'projet'}-{ident}"


def make_demo(folder: Path, n: int = 3, seconds: int = 8) -> list[Path]:
    """Rushes synthétiques (image de test + son) : permet de juger le parcours sans aucun fichier client."""
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for i in range(n):
        f = folder / f"rush_demo_{i + 1}.mp4"
        if not f.exists():
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=25:duration={seconds}",
                            "-f", "lavfi", "-i", f"sine=f={300 + 150 * i}:duration={seconds}", "-c:v", "libx264", "-preset", "ultrafast",
                            "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(f)], check=True)
        out.append(f)
    return out


def selftest() -> int:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    import shutil
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        print("ffmpeg/ffprobe non embarqués")
        return 2
    from kolnoa_editor.app import ProjectController
    from kolnoa_editor.auto import StyleParams
    from kolnoa_editor.probe import probe
    tmp = Path(tempfile.mkdtemp())
    root, data = tmp / "demo", tmp / "data"
    make_demo(root)
    c = ProjectController.open(root, data)
    assert c.import_rushes() == 3, "import"
    c.build("Monte-moi cette séquence", None, StyleParams(shot_min=1, shot_max=3))
    assert c.tl.clips, "montage vide"
    from kolnoa_editor.timeline import Selection
    v = c.tl.clips_on("V1")
    c.command("raccourcis cette partie de 2 secondes", Selection.of_clips(c.tl, [x.id for x in v[: max(2, len(v) // 2)]]))
    c.save("selftest")
    c2 = ProjectController.open(root, data)
    assert len(c2.tl.clips) == len(c.tl.clips), "réouverture"
    rep = c2.export(tmp / "film.mp4", resolution="1080p")
    info = probe(rep.output)
    assert info.duration > 1 and info.width == 1920, f"export {info}"
    from PySide6.QtWidgets import QApplication
    from kolnoa_editor.ui.app_window import AppWindow
    app = QApplication.instance() or QApplication([])
    win = AppWindow(c2, player_factory=lambda ctl, g: None)
    win.show()
    win.tlv.set_playhead(25)
    win._update_preview()
    for _ in range(20):
        app.processEvents()
    ok = win.preview.pixmap() is not None and not win.preview.pixmap().isNull()
    win.close()
    print(f"parcours OK — {len(c2.tl.clips)} plans, export {info.width}x{info.height} {info.duration:.1f}s, aperçu {'OK' if ok else 'ABSENT'}")
    return 0 if ok else 3


def main(argv: list[str]) -> int:
    if "--selftest" in argv:
        return selftest()
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
    app = QApplication.instance() or QApplication(argv)
    app.setQuitOnLastWindowClosed(False)                     # le moteur survit à la fermeture de la fenêtre ; « Quitter » (icône) l'arrête
    from kolnoa_editor.app import ProjectController
    from kolnoa_editor.app.engine import EngineAlreadyRunning, ResidentEngine
    from kolnoa_editor.ui.activation import ActivationServer, notify_running_instance
    from kolnoa_editor.ui.app_window import AppWindow
    from kolnoa_editor.ui.tray import TrayController
    engine = ResidentEngine()
    try:
        engine.start()
    except EngineAlreadyRunning:
        notify_running_instance()                            # réveille la fenêtre de l'instance existante, puis on s'efface
        return 0
    args = [a for a in argv[1:] if not a.startswith("--")]
    minimized = "--minimized" in argv
    if "--demo" in argv and args:
        make_demo(Path(args[0]))
    folder = args[0] if args and Path(args[0]).is_dir() else ""
    if not folder and engine.settings.last_project and Path(engine.settings.last_project).is_dir():
        folder = engine.settings.last_project
    if not folder:
        if minimized:                                        # démarrage de session sans projet : icône seule
            folder = ""
        else:
            folder = QFileDialog.getExistingDirectory(None, "Dossier de rushes du projet")
            if not folder:
                engine.shutdown()
                return 0
    win = None
    tray = None
    if folder:
        root = Path(folder)
        try:
            ctl = ProjectController.open(root, data_dir_for(root))
            if not ctl.rushes:
                ctl.import_rushes()
        except Exception as e:                               # noqa: BLE001 — message clair plutôt qu'une fenêtre qui disparaît
            QMessageBox.critical(None, "Kolnoa Editor", f"Impossible d'ouvrir le projet :\n{e}")
            engine.shutdown()
            return 1
        engine.attach_project(ctl)
        win = AppWindow(ctl)
        win.engine = engine
    else:
        from PySide6.QtWidgets import QLabel
        win = QLabel("Kolnoa Editor tourne en arrière-plan.\nOuvrez un dossier de rushes en relançant l'application.")
    tray = TrayController(app, engine, win)
    if hasattr(win, "tray"):
        win.tray = tray
    act = ActivationServer()
    act.show_requested.connect(tray.show_window)
    if not (minimized and tray.available):
        win.show()
    code = app.exec()
    engine.shutdown()
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
