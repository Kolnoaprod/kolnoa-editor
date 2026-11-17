"""python -m kolnoa_editor.experimental.memory_proto analyze|anonymize <session_dir|timeline.json> [sortie]

LECTURE SEULE sur le projet. `anonymize` écrit un nouveau fichier JSON (jamais dans le dossier de session) ;
`analyze` imprime le rapport (ou écrit le JSON de statistiques avec --json)."""

import json
import sys
from pathlib import Path

from ...timeline import Timeline
from .analyzer import analyze_timeline, render_report
from .anonymize import anonymize_timeline


def _load(src: str) -> Timeline:
    p = Path(src)
    p = p / "current.json" if p.is_dir() else p
    return Timeline.from_dict(json.loads(p.read_text(encoding="utf-8")))


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] not in ("analyze", "anonymize"):
        print(__doc__)
        return 2
    tl = _load(argv[1])
    if argv[0] == "anonymize":
        if len(argv) < 3:
            print("usage : anonymize <entrée> <sortie.json>")
            return 2
        out, rep = anonymize_timeline(tl)
        Path(argv[2]).write_text(json.dumps(out.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{rep['n_media']} média(s) renommés ; textes de chapitres CONSERVÉS, à relire : {rep['chapters_kept_for_review']}")
        return 0
    st = analyze_timeline(tl)
    print(json.dumps(st, ensure_ascii=False, indent=1) if "--json" in argv else render_report(st))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
