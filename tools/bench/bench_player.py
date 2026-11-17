"""Voir `python -m kolnoa_editor.player.bench` (même programme)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from kolnoa_editor.player.bench import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
