"""Panneaux latéraux : Effets & transitions (honnête : seul ce qui est réellement rendu est actif) et Retouche par prompt."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QPlainTextEdit, QPushButton, QSlider, QVBoxLayout, QWidget)

from ..timeline.model import TimelineError
from ..timeline.ops import SetClipProps, Transaction

SOON = "bientôt"


def _title(text: str) -> QLabel:
    t = QLabel(text)
    t.setObjectName("panelTitle")
    return t


def _soon_row(name: str, hint: str) -> QWidget:
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 2, 0, 2)
    lab = QLabel(name)
    lab.setToolTip(hint)
    badge = QLabel(SOON)
    badge.setObjectName("badge")
    h.addWidget(lab, 1)
    h.addWidget(badge)
    lab.setEnabled(False)
    return w


class EffectsPanel(QWidget):
    """Vitesse (réellement rendue à l'export) ; transitions, stabilisation, étalonnage : annoncés « bientôt », pas simulés."""
    speedRequested = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.target = QLabel("Sélectionnez un plan sur la timeline.")
        self.target.setObjectName("zone")
        self.target.setWordWrap(True)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(50, 200)                      # centièmes : 0,50× … 2,00×
        self.slider.setValue(100)
        self.value = QLabel("1,00×")
        self.value.setObjectName("timecode")
        self.slider.valueChanged.connect(lambda v: self.value.setText(f"{v / 100:.2f}×".replace(".", ",")))
        self.apply_btn = QPushButton("Appliquer la vitesse")
        self.apply_btn.setObjectName("primary")
        self.apply_btn.clicked.connect(lambda: self.speedRequested.emit(self.slider.value() / 100))
        self.reset_btn = QPushButton("Vitesse normale")
        self.reset_btn.setObjectName("chip")
        self.reset_btn.clicked.connect(lambda: self.speedRequested.emit(1.0))
        self.badge = QLabel("rendu à l'export")
        self.badge.setObjectName("badgeOk")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(self.target)
        row = QHBoxLayout()
        row.addWidget(_title("Vitesse"))
        row.addWidget(self.badge)
        row.addStretch(1)
        row.addWidget(self.value)
        lay.addLayout(row)
        lay.addWidget(self.slider)
        lay.addWidget(QLabel("0,5× = ralenti · 2× = accéléré. Le son suit le plan."))
        b = QHBoxLayout()
        b.addWidget(self.reset_btn)
        b.addStretch(1)
        b.addWidget(self.apply_btn)
        lay.addLayout(b)
        lay.addSpacing(8)
        lay.addWidget(_title("Transitions"))
        for n, h in (("Fondu enchaîné", "Pas encore rendu à l'export : l'afficher ici serait trompeur."),
                     ("Fondu au noir", "Idem."), ("Balayage / glissé", "Idem.")):
            lay.addWidget(_soon_row(n, h))
        lay.addSpacing(8)
        lay.addWidget(_title("Image"))
        for n, h in (("Stabilisation", "Disponible dans FFmpeg (vidstab) : lot suivant."),
                     ("Étalonnage / LUT", "Lot suivant."), ("Recadrage / zoom", "Lot suivant.")):
            lay.addWidget(_soon_row(n, h))
        lay.addStretch(1)

    def set_target(self, text: str, enabled: bool, speed: float | None = None) -> None:
        self.target.setText(text)
        self.apply_btn.setEnabled(enabled)
        self.reset_btn.setEnabled(enabled)
        if speed:
            self.slider.setValue(round(speed * 100))


def speed_transaction(tl, clip_ids, speed: float, rush_len=lambda m: None) -> Transaction:
    """SetClipProps(extra.speed) sur chaque plan vidéo choisi. Refuse hors 0,5×–2×, audio multicam « group », ou dépassement du rush."""
    if not 0.5 <= speed <= 2.0:
        raise TimelineError("vitesse hors plage (0,5× à 2×)")
    ops = []
    for cid in clip_ids:
        c = tl.clip(cid)
        if tl.track(c.track).kind != "video":
            continue
        if speed != 1.0 and c.audio == "group":
            raise TimelineError(f"{cid} : vitesse sur un plan multicam à audio maître pas encore supportée")
        n = rush_len(c.media)
        if n is not None and c.src_in + c.duration / tl.fps * speed > n + 1e-6:
            raise TimelineError(f"{cid} : à {speed:.2f}× le plan dépasserait la fin du rush")
        extra = dict(c.extra or {})
        if speed == 1.0:
            extra.pop("speed", None)
        else:
            extra["speed"] = speed
        ops.append(SetClipProps(cid, {"extra": extra}))
    if not ops:
        raise TimelineError("aucun plan vidéo sélectionné")
    return Transaction(ops, f"vitesse {speed:.2f}× sur {len(ops)} plan(s)")


class RetouchePanel(QWidget):
    """Retouche du montage par prompt : on voit la portée (sélection / zone I-O / tête de lecture), on écrit, on applique."""
    submitted = Signal(str)

    SUGGESTIONS = ("raccourcis ce plan", "accélère le rythme", "remplace par un plan de coupe", "monte-moi cette séquence")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scope = QLabel("Portée : tête de lecture")
        self.scope.setObjectName("badgeOk")
        self.edit = QPlainTextEdit()
        self.edit.setPlaceholderText("Décrivez la retouche : « raccourcis ce plan de 2 s », « mets un plan de coupe ici »…\nCtrl+Entrée pour appliquer.")
        self.edit.setFixedHeight(96)
        self.go = QPushButton("Appliquer à la portée")
        self.go.setObjectName("primary")
        self.go.clicked.connect(self._submit)
        self.history = QListWidget()
        self.history.setToolTip("Double-clic : remettre la consigne dans le champ")
        self.history.itemDoubleClicked.connect(lambda it: self.edit.setPlainText(it.data(Qt.UserRole)))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(self.scope, 0, Qt.AlignLeft)
        lay.addWidget(self.edit)
        lay.addWidget(self.go)
        lay.addWidget(_title("Idées"))
        for s in self.SUGGESTIONS:
            b = QPushButton(s)
            b.setObjectName("chip")
            b.clicked.connect(lambda _=False, s=s: (self.edit.setPlainText(s), self.edit.setFocus()))
            lay.addWidget(b)
        lay.addWidget(_title("Historique"))
        lay.addWidget(self.history, 1)
        self.edit.installEventFilter(self)

    def eventFilter(self, obj, ev):
        from PySide6.QtCore import QEvent
        if obj is self.edit and ev.type() == QEvent.KeyPress and ev.key() in (Qt.Key_Return, Qt.Key_Enter) and ev.modifiers() & Qt.ControlModifier:
            self._submit()
            return True
        return super().eventFilter(obj, ev)

    def _submit(self) -> None:
        t = self.edit.toPlainText().strip()
        if t:
            self.submitted.emit(t)

    def record(self, text: str, ok: bool) -> None:
        from PySide6.QtWidgets import QListWidgetItem
        it = QListWidgetItem(("✓ " if ok else "✗ ") + text.replace("\n", " "))
        it.setData(Qt.UserRole, text)
        self.history.insertItem(0, it)
        if ok:
            self.edit.clear()

    def set_scope(self, text: str) -> None:
        self.scope.setText("Portée : " + text)
