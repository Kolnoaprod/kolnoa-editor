"""Kolnoa Editor V0.1 — point d'entrée de l'EXE portable (éditeur complet : importer → monter → retoucher → enregistrer → exporter).

  KolnoaEditor.exe                  → choisit le dossier de rushes puis ouvre la fenêtre
  KolnoaEditor.exe <dossier>        → ouvre ce dossier de rushes
  KolnoaEditor.exe --demo <dossier> → crée 3 rushes synthétiques dans <dossier> (parcours de test reproductible) puis l'ouvre
  KolnoaEditor.exe --selftest       → (CI) parcours complet sans fenêtre : import, montage, consigne, enregistrement, réouverture, export MP4
Les rushes ne sont JAMAIS modifiés. Le projet (journal, proxys, rendus) vit dans Documents\\Kolnoa_Projets\\<dossier>-<id>."""

from __future__ import annotations

import hashlib
import os
import logging
import threading
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


def make_demo(folder: Path, n: int = 3, seconds: int = 8, *, cancel=None) -> list[Path]:
    """Rushes synthétiques (image de test + son) : permet de juger le parcours sans aucun fichier client."""
    from kolnoa_editor.ffmpeg import ffmpeg_bin, run
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for i in range(n):
        f = folder / f"rush_demo_{i + 1}.mp4"
        if not f.exists():
            run([ffmpeg_bin(), "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=25:duration={seconds}",
                            "-f", "lavfi", "-i", f"sine=f={300 + 150 * i}:duration={seconds}", "-c:v", "libx264", "-preset", "ultrafast",
                            "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(f)], cancel=cancel)
        out.append(f)
    return out


def selftest() -> int:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from shutil import which
    from time import monotonic, sleep

    from PySide6.QtWidgets import QApplication

    from kolnoa_editor.app import ProjectController
    from kolnoa_editor.probe import probe
    from kolnoa_editor.timeline import Selection
    from kolnoa_editor.ui.app_window import AppWindow

    if not (which("ffmpeg") and which("ffprobe")):
        raise RuntimeError("FFmpeg et FFprobe sont nécessaires à l'autotest.")
    app = QApplication.instance() or QApplication([])

    def wait_for(condition, label):
        end = monotonic() + 120
        while not condition():
            app.processEvents()
            if monotonic() >= end:
                raise TimeoutError(label)
            sleep(0.01)

    def wait_operation(win):
        wait_for(lambda: not win._operation.running, "Délai de l’opération dépassé")
        assert win._operation.bar.value() == 100, win.status.text()
        win._operation.close()

    with tempfile.TemporaryDirectory() as directory:
        tmp = Path(directory)
        root, data = tmp / "demo", tmp / "data"
        for i, path in enumerate(make_demo(root)):
            # Des prises successives d'une caméra ne doivent pas se chevaucher dans la démo.
            stamp = path.stat().st_mtime + 20 * i
            os.utime(path, (stamp, stamp))
        ctl = ProjectController.open(root, data)
        win = AppWindow(ctl)
        win.show()
        try:
            win.import_rushes()
            wait_operation(win)
            assert len(ctl.rushes) == 3, "import"
            assert win.run_prompt("Monte-moi cette séquence"), "lancement du montage"
            wait_operation(win)
            assert ctl.tl.clips, "montage vide"
            video = ctl.tl.clips_on("V1")
            selection = Selection.of_clips(ctl.tl, [clip.id for clip in video[: max(2, len(video) // 2)]])
            ctl.command("raccourcis cette partie de 2 secondes", selection)
            ctl.save("selftest")
            reopened = ProjectController.open(root, data)
            assert reopened.tl.canonical() == ctl.tl.canonical(), "réouverture"
            win.export(str(tmp / "film.mp4"), "1080p")
            wait_operation(win)
            info = probe(tmp / "film.mp4")
            assert info.duration > 1 and info.width == 1920 and info.has_audio, f"export {info}"
            win.tlv.set_playhead(25)
            win._request_preview()
            wait_for(lambda: win.preview.pixmap() is not None and not win.preview.pixmap().isNull(), "Aperçu indisponible")
        finally:
            win.close()
        message = f"Autotest OK — {len(reopened.tl.clips)} clips, export {info.width}x{info.height} {info.duration:.1f}s, aperçu OK"
        logging.info(message)
        print(message)
    return 0


def main(argv: list[str]) -> int:
    if "--selftest" in argv:
        return selftest()
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

    from kolnoa_editor import __version__
    from kolnoa_editor.app import ProjectController
    from kolnoa_editor.app.instance import InstanceLock, engine_dir
    from kolnoa_editor.app.media_manifest import MediaManifest
    from kolnoa_editor.ffmpeg import Cancelled
    from kolnoa_editor.ui.activation import ActivationServer, notify_running_instance
    from kolnoa_editor.ui.app_window import AppWindow
    from kolnoa_editor.ui.operation import OperationDialog, OperationWorker

    app = QApplication.instance() or QApplication(argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Kolnoa Editor")
    app.setApplicationVersion(__version__)
    lock = InstanceLock(engine_dir())
    if not lock.acquire():
        if notify_running_instance():
            return 0
        QMessageBox.warning(None, "Kolnoa Editor", "Une instance est déjà ouverte, mais elle ne répond pas. Consultez le journal de démarrage.")
        return 1

    active_window = None

    def show_window():
        if active_window is not None:
            if active_window.isMinimized():
                active_window.showNormal()
            else:
                active_window.show()
            active_window.raise_()
            active_window.activateWindow()

    try:
        activation = ActivationServer()
        def wake():
            logging.info("Réveil demandé par une autre instance")
            show_window()

        activation.show_requested.connect(wake)
        if not activation.ok:
            logging.error("Le canal de réveil de l'instance n'a pas pu être ouvert.")
        args = [arg for arg in argv[1:] if not arg.startswith("--")]
        demo = "--demo" in argv and bool(args)
        if args:
            root = Path(args[0])
            if not demo and not root.is_dir() and not (root.is_file() and root.suffix.lower() == ".klnprj"):
                raise ValueError(f"Dossier de rushes ou projet introuvable : {root}")
        else:
            picker = QFileDialog(None, "Kolnoa Editor — Dossier de rushes")
            picker.setFileMode(QFileDialog.Directory)
            active_window = picker
            show_window()
            if not picker.exec():
                return 0
            root = Path(picker.selectedFiles()[0])

        def open_project(cancel, progress):
            if demo:
                progress("Création des rushes de démonstration", str(root), None, None, None)
                make_demo(root, cancel=cancel)
            cancel.raise_if_cancelled()
            if not demo and root.suffix.lower() == ".klnprj" and root.is_file():
                manifest = MediaManifest(root)
                if manifest.root is None or manifest.data_dir is None:
                    raise ValueError("Ce fichier ne référence pas un projet Kolnoa.")
                return ProjectController.open(manifest.root, manifest.data_dir), manifest
            ctl = ProjectController.open(root, data_dir_for(root))
            if not ctl.rushes:
                ctl.import_rushes(cancel=cancel, on_progress=progress)
            return ctl, None

        worker = OperationWorker(open_project)
        dialog = OperationDialog("Kolnoa Editor — Ouverture du projet", worker)
        active_window = dialog

        def opened():
            if worker.error is None or isinstance(worker.error, Cancelled):
                dialog.accept()
            else:
                dialog.finish(f"Ouverture impossible : {worker.error}", False)

        worker.finished.connect(opened)
        worker.start()
        show_window()
        dialog.exec()
        if worker.error is not None:
            return 0 if isinstance(worker.error, Cancelled) else 1
        ctl, manifest = worker.result
        active_window = AppWindow(ctl, manifest=manifest)
        show_window()
        app.setQuitOnLastWindowClosed(True)
        logging.info("Fenêtre principale affichée")
        return app.exec()
    finally:
        lock.release()


def bootstrap(argv: list[str]) -> int:
    from logging.handlers import RotatingFileHandler
    from kolnoa_editor.app.instance import engine_dir

    debug = "--diagnostic" in argv
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    path = engine_dir() / "startup.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        logger.addHandler(RotatingFileHandler(path, maxBytes=2_000_000, backupCount=2, encoding="utf-8"))
        logger.handlers[-1].setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    except OSError:
        logging.exception("Journal indisponible : %s", path)

    def report(exc_type, value, traceback):
        logging.error("Exception non interceptée", exc_info=(exc_type, value, traceback))

    sys.excepthook = report
    threading.excepthook = lambda args: report(args.exc_type, args.exc_value, args.exc_traceback)
    logging.info("Kolnoa Editor 0.1.1, Windows portable : lancement (pid=%s, diagnostic=%s)", os.getpid(), debug)
    if debug:
        from shutil import which
        logging.debug("Python %s. FFmpeg : %s. FFprobe : %s", sys.version, which("ffmpeg"), which("ffprobe"))
    try:
        return main(argv)
    except Exception:
        logging.exception("Échec du démarrage")
        if "--selftest" not in argv:
            message = f"Impossible de démarrer. Journal :\n{path}"
            try:
                from PySide6.QtWidgets import QApplication, QMessageBox
                app = QApplication.instance() or QApplication([])
                QMessageBox.critical(None, "Kolnoa Editor", message)
            except ImportError:
                if sys.platform == "win32":
                    import ctypes
                    ctypes.windll.user32.MessageBoxW(None, message, "Kolnoa Editor", 0x10)
        return 1


if __name__ == "__main__":
    sys.exit(bootstrap(sys.argv))
