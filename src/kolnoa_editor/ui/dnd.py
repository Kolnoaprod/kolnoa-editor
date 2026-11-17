"""Glisser-déposer du chutier vers la timeline : format du message (sans logique de montage).

Deux sortes de charge : `{"sub": "b001"}` (un plan du chutier) ou `{"media": "RUSH/a.mp4", "length": 12.5, "src_in": 0.0,
"kind": "video"}` (un rush brut, durée en secondes). La charge ne contient ni chemin absolu ni donnée client : seulement des références."""

from __future__ import annotations

import json

from PySide6.QtCore import QMimeData

MIME = "application/x-kolnoa-bin"


def encode(payload: dict) -> QMimeData:
    m = QMimeData()
    m.setData(MIME, json.dumps(payload).encode("utf-8"))
    return m


def decode(mime) -> dict | None:
    """Charge valide ou None (un glisser étranger, ou abîmé, est ignoré sans exception)."""
    if mime is None or not mime.hasFormat(MIME):
        return None
    try:
        d = json.loads(bytes(mime.data(MIME)).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if isinstance(d, dict) and (isinstance(d.get("sub"), str) or (isinstance(d.get("media"), str) and isinstance(d.get("length"), (int, float)))):
        return d
    return None
