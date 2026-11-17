"""python -m kolnoa_editor.experimental.memory_proto analyze|anonymize <session_dir|timeline.json> [sortie.json] [options]

LECTURE SEULE sur le projet : aucune écriture dans un dossier de session (refus explicite), aucun écrasement sans --force.
  analyze    imprime le rapport (--json : statistiques brutes ; --hold-s 6 ou --hold-by-kind fichier.json : seuil « respire »)
  anonymize  écrit un export par LISTE BLANCHE, contrôlé avant et après sérialisation
             --deny "mot1,mot2"      termes (noms de clients, lieux…) dont la présence refuse l'export
             --chapters-map f.json   correspondance {texte du chapitre: libellé générique} choisie par le réalisateur
             --force                 autorise l'écrasement du fichier de sortie (jamais d'un fichier de session)"""

import json
import sys
from pathlib import Path

from ...timeline import Timeline
from .analyzer import analyze_timeline, render_report
from .anonymize import ExportRefused, anonymize_timeline, write_export


def _load(src: str) -> Timeline:
    p = Path(src)
    p = p / "current.json" if p.is_dir() else p
    return Timeline.from_dict(json.loads(p.read_text(encoding="utf-8")))


def _opt(argv: list[str], name: str, default=None):
    return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else default


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] not in ("analyze", "anonymize"):
        print(__doc__)
        return 2
    tl = _load(argv[1])
    if argv[0] == "anonymize":
        if len(argv) < 3 or argv[2].startswith("--"):
            print("usage : anonymize <entrée> <sortie.json> [--deny a,b] [--chapters-map f.json] [--force]")
            return 2
        labels = json.loads(Path(_opt(argv, "--chapters-map")).read_text(encoding="utf-8")) if _opt(argv, "--chapters-map") else None
        deny = [t for t in (_opt(argv, "--deny", "") or "").split(",") if t.strip()]
        try:
            out, rep = anonymize_timeline(tl, labels, deny)
            dest = write_export(out, Path(argv[1]), Path(argv[2]), force="--force" in argv, deny=deny,
                                allowed_extra=(labels or {}).values())
        except ExportRefused as exc:
            print(f"REFUSÉ : {exc}")
            return 1
        print(f"écrit : {dest.name} — {rep['n_media']} média(s) renommés ; chapitres : {rep['chapters_generic_kept']} génériques conservés, "
              f"{rep['chapters_replaced_by_section_n']} remplacés par « section-N » (ordre de première apparition), "
              f"{rep['chapters_user_labels']} par vos libellés ; non exporté : {rep['dropped']}.")
        print("À RELIRE par le réalisateur avant tout partage ; ne jamais publier un fichier de tournage réel sans audit.")
        return 0
    hold = json.loads(Path(_opt(argv, "--hold-by-kind")).read_text(encoding="utf-8")) if _opt(argv, "--hold-by-kind") else float(_opt(argv, "--hold-s", 6.0))
    st = analyze_timeline(tl, hold_s=hold)
    print(json.dumps(st, ensure_ascii=False, indent=1) if "--json" in argv else render_report(st))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
