"""Kolnoa — Test de performance : petite fenêtre Windows, zéro commande.

Double-clic → choisir 3 vidéos → « Lancer le test ». Le programme fait 3 tours complets (Qt et web, copies légères puis vidéos
originales), affiche la progression et écrit REPORT.md. Il ne modifie JAMAIS les vidéos choisies.
"""

from __future__ import annotations

import sys

if "--selftest" in sys.argv:                            # contrôle de l'EXE (CI) : tout est-il bien embarqué ?
    import shutil
    import bench_run as _br
    _br.use_bundled_ffmpeg()
    import PySide6.QtMultimedia, bench_qt  # noqa: E401,F401
    ok = shutil.which("ffmpeg") and shutil.which("ffprobe") and (_br.HERE / "bench_web.html").is_file()
    sys.exit(0 if ok else 2)

if "--qt-worker" in sys.argv:                           # l'EXE se relance lui-même pour mesurer le prototype Qt
    sys.argv.remove("--qt-worker")
    import bench_qt
    bench_qt.main()
    sys.exit(0)

import os
import queue
import threading
import time
import traceback
from pathlib import Path

import bench_run as br

ROUNDS = 3
VIDEO_TYPES = [("Vidéos", "*.mp4 *.mov *.mxf *.mkv *.avi *.m4v *.MP4 *.MOV *.MXF"), ("Tous les fichiers", "*.*")]


def hide_child_consoles() -> None:
    """Windows : pas de fenêtre noire qui clignote à chaque ffmpeg lancé par le programme."""
    if sys.platform != "win32":
        return
    import subprocess
    orig = subprocess.Popen.__init__

    def init(self, *a, **kw):
        kw["creationflags"] = kw.get("creationflags", 0) | 0x08000000      # CREATE_NO_WINDOW
        orig(self, *a, **kw)
    subprocess.Popen.__init__ = init


def default_out_dir() -> Path:
    docs = Path(os.path.expanduser("~")) / "Documents"
    return (docs if docs.is_dir() else Path.home()) / "Kolnoa_Benchmark" / time.strftime("%Y-%m-%d_%H%M%S")


def open_path(p: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(str(p))                        # noqa: S606 — ouvre avec le programme par défaut
        else:
            import subprocess
            subprocess.Popen(["xdg-open", str(p)])
    except Exception:
        pass


def run_job(files, out: Path, q: queue.Queue, cancel: threading.Event) -> None:
    """Thread de travail : parle à l'interface uniquement via la file `q`."""
    try:
        br.use_bundled_ffmpeg()
        res = br.run_pipeline(files, out, rounds=ROUNDS, no_audio=False,
                              log=lambda m: q.put(("log", m)),
                              progress=lambda d, t, l: q.put(("progress", d, t, l)),
                              cancel=cancel.is_set)
        report = br.write_outputs(res, out)
        q.put(("done", str(report)))
    except br.Cancelled:
        q.put(("cancelled",))
    except Exception as e:                              # message lisible + trace dans un fichier
        out.mkdir(parents=True, exist_ok=True)
        (out / "erreur.txt").write_text(traceback.format_exc(), encoding="utf-8")
        q.put(("error", f"{e}\n\nDétails : {out / 'erreur.txt'}"))


def main() -> None:
    hide_child_consoles()
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("Kolnoa — Test de performance")
    root.geometry("640x480")
    files: list[str] = []
    q: queue.Queue = queue.Queue()
    cancel = threading.Event()
    state = {"out": None, "report": None, "running": False}

    frm = ttk.Frame(root, padding=14)
    frm.pack(fill="both", expand=True)
    ttk.Label(frm, text="Test de performance de l'éditeur", font=("Segoe UI", 15, "bold")).pack(anchor="w")
    ttk.Label(frm, justify="left", wraplength=600,
              text="1. Choisis 3 vidéos (les 3 caméras).  2. Clique sur « Lancer le test ».  3. Attends la fin sans toucher à rien : "
                   "des fenêtres vont s'ouvrir et se fermer toutes seules. Tes vidéos ne sont jamais modifiées.").pack(anchor="w", pady=(4, 10))
    lst = tk.Listbox(frm, height=3, activestyle="none")
    lst.pack(fill="x")
    row = ttk.Frame(frm)
    row.pack(fill="x", pady=8)
    b_pick = ttk.Button(row, text="Choisir 3 vidéos…")
    b_pick.pack(side="left")
    b_run = ttk.Button(row, text="Lancer le test", state="disabled")
    b_run.pack(side="left", padx=8)
    b_cancel = ttk.Button(row, text="Annuler", state="disabled")
    b_cancel.pack(side="left")
    label = ttk.Label(frm, text="")
    label.pack(anchor="w")
    bar = ttk.Progressbar(frm, maximum=100)
    bar.pack(fill="x", pady=4)
    log = tk.Text(frm, height=10, state="disabled", wrap="word")
    log.pack(fill="both", expand=True)
    row2 = ttk.Frame(frm)
    row2.pack(fill="x", pady=(8, 0))
    b_rep = ttk.Button(row2, text="Ouvrir le rapport", state="disabled")
    b_rep.pack(side="left")
    b_dir = ttk.Button(row2, text="Ouvrir le dossier", state="disabled")
    b_dir.pack(side="left", padx=8)

    def say(msg: str) -> None:
        log.configure(state="normal")
        log.insert("end", msg + "\n")
        log.see("end")
        log.configure(state="disabled")

    def refresh() -> None:
        b_run.configure(state="normal" if len(files) == 3 and not state["running"] else "disabled")
        b_pick.configure(state="disabled" if state["running"] else "normal")
        b_cancel.configure(state="normal" if state["running"] else "disabled")

    def pick() -> None:
        sel = filedialog.askopenfilenames(title="Choisis exactement 3 vidéos", filetypes=VIDEO_TYPES)
        if not sel:
            return
        if len(sel) != 3:
            messagebox.showwarning("Kolnoa", f"Il faut choisir exactement 3 vidéos (tu en as choisi {len(sel)}).")
            return
        files[:] = list(sel)
        lst.delete(0, "end")
        for f in files:
            lst.insert("end", Path(f).name)
        refresh()

    def start() -> None:
        state.update(out=default_out_dir(), running=True, report=None)
        cancel.clear()
        bar["value"] = 0
        label.configure(text="Préparation (les copies légères prennent quelques minutes la première fois)…")
        b_rep.configure(state="disabled")
        b_dir.configure(state="disabled")
        refresh()
        threading.Thread(target=run_job, args=(list(files), state["out"], q, cancel), daemon=True).start()

    def stop() -> None:
        cancel.set()
        label.configure(text="Annulation en cours…")

    def poll() -> None:
        try:
            while True:
                m = q.get_nowait()
                if m[0] == "log":
                    say(m[1])
                elif m[0] == "progress":
                    _, d, t, l = m
                    bar["value"] = 100 * d / max(1, t)
                    label.configure(text=f"{l}  ({d}/{t})" if d < t else l)
                else:
                    state["running"] = False
                    b_dir.configure(state="normal")
                    if m[0] == "done":
                        state["report"] = Path(m[1])
                        bar["value"] = 100
                        label.configure(text="Terminé. Envoie le fichier REPORT.md à Claudio.")
                        b_rep.configure(state="normal")
                        say(f"Rapport : {m[1]}")
                        open_path(state["out"])
                    elif m[0] == "cancelled":
                        label.configure(text="Annulé.")
                    else:
                        label.configure(text="Une erreur est survenue.")
                        say(m[1])
                        messagebox.showerror("Kolnoa", m[1])
                    refresh()
        except queue.Empty:
            pass
        root.after(200, poll)

    b_pick.configure(command=pick)
    b_run.configure(command=start)
    b_cancel.configure(command=stop)
    b_rep.configure(command=lambda: state["report"] and open_path(state["report"]))
    b_dir.configure(command=lambda: state["out"] and open_path(state["out"]))
    root.protocol("WM_DELETE_WINDOW", lambda: (cancel.set(), root.destroy()))
    poll()
    root.mainloop()


if __name__ == "__main__":
    main()
