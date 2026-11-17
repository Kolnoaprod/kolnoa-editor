"""Kolnoa Editor — application d'essai Windows (point d'entrée de l'EXE).

  KolnoaEditorTest.exe                      → fenêtre : on choisit les vidéos, puis on essaie le lecteur / la timeline / les mesures
  KolnoaEditorTest.exe vid1.mp4 vid2.mp4    → idem sans boîte de choix
  KolnoaEditorTest.exe --player-worker …    → (interne) une mesure du lecteur dans un processus séparé
  KolnoaEditorTest.exe --selftest           → (CI) vérifie que tout est embarqué et que la fenêtre démarre
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
if (HERE / "bin").is_dir():                                   # ffmpeg/ffprobe embarqués en tête du PATH
    os.environ["PATH"] = str(HERE / "bin") + os.pathsep + os.environ.get("PATH", "")
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))


def selftest() -> int:
    import shutil
    import subprocess
    import tempfile
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        print("ffmpeg/ffprobe non embarqués")
        return 2
    tmp = Path(tempfile.mkdtemp())
    clips = []
    for i in range(2):
        f = tmp / f"c{i}.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=4", "-f", "lavfi", "-i",
                        f"sine=f={300 + 100 * i}:duration=4", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
                        "-shortest", str(f)], check=True)
        clips.append(str(f))
    from PySide6.QtWidgets import QApplication
    from kolnoa_editor.ui import demo_app
    app = QApplication([])
    sess, media, dur = demo_app.build_demo(clips, tmp / "w", make_proxies=True, proxy_seconds=3)
    assert all(m.proxy for m in media.values()), "proxys non créés"
    win = demo_app._make_window(sess, media, dur, tmp / "w")
    win.show()
    for _ in range(40):
        app.processEvents()
    win.mc.player.seek(1.0)
    win.mc.player.play()
    import time
    t0 = time.time()
    while time.time() - t0 < 1.5:
        app.processEvents()
    ok = len(win.mc.images) >= 2
    win.close()
    print("images reçues par angle :", sorted(win.mc.images))
    return 0 if ok else 3


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if "--player-worker" in sys.argv:
        sys.argv.remove("--player-worker")
        from kolnoa_editor.player.bench import main as bench_main
        sys.exit(bench_main(sys.argv[1:]))
    from kolnoa_editor.ui.demo_app import run_gui
    sys.exit(run_gui(sys.argv))
