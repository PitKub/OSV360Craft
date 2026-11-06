"""Small reusable widgets."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget)


def page(title: str, subtitle: str = "") -> tuple[QWidget, QVBoxLayout]:
    """A scrollable page with a title; returns (page widget, content layout)."""
    outer = QWidget()
    ol = QVBoxLayout(outer)
    ol.setContentsMargins(18, 12, 18, 12)
    t = QLabel(title)
    t.setObjectName("PageTitle")
    ol.addWidget(t)
    if subtitle:
        s = QLabel(subtitle)
        s.setObjectName("Muted")
        s.setWordWrap(True)
        ol.addWidget(s)
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    inner = QWidget()
    lay = QVBoxLayout(inner)
    lay.setContentsMargins(0, 0, 6, 0)
    sa.setWidget(inner)
    ol.addWidget(sa, 1)
    return outer, lay


class KeyValueGrid(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setColumnStretch(1, 1)
        self._labels: dict[str, QLabel] = {}

    def set(self, key: str, value: str, state: str | None = None) -> None:
        if key not in self._labels:
            r = self.grid.rowCount()
            k = QLabel(key)
            k.setObjectName("Muted")
            v = QLabel()
            v.setWordWrap(True)
            v.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.grid.addWidget(k, r, 0, Qt.AlignTop)
            self.grid.addWidget(v, r, 1)
            self._labels[key] = v
        lbl = self._labels[key]
        lbl.setText(value)
        lbl.setObjectName({"ok": "Ok", "warn": "Warn", "bad": "Bad"}.get(state or "", ""))
        lbl.style().unpolish(lbl)
        lbl.style().polish(lbl)


class PathRow(QWidget):
    changed = Signal(str)

    def __init__(self, mode: str = "file", filt: str = "", placeholder: str = "", parent=None) -> None:
        super().__init__(parent)
        self.mode, self.filt = mode, filt
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        b = QPushButton("Browse...")
        b.clicked.connect(self._browse)
        h.addWidget(self.edit, 1)
        h.addWidget(b)
        self.edit.editingFinished.connect(lambda: self.changed.emit(self.edit.text().strip()))

    def _browse(self) -> None:
        if self.mode == "dir":
            p = QFileDialog.getExistingDirectory(self, "Choose folder", self.edit.text())
        else:
            p, _ = QFileDialog.getOpenFileName(self, "Choose file", self.edit.text(), self.filt)
        if p:
            self.edit.setText(p)
            self.changed.emit(p)

    def text(self) -> str:
        return self.edit.text().strip()

    def setText(self, t: str) -> None:
        self.edit.setText(t or "")


class OptionalCombo(QComboBox):
    """A combo whose first entry means 'OpenOSV default' (value None)."""

    def __init__(self, items: list[tuple[str, object]], default_label: str = "OpenOSV default", parent=None) -> None:
        super().__init__(parent)
        self.addItem(default_label, None)
        for label, val in items:
            self.addItem(label, val)

    def value(self):
        return self.currentData()

    def set_value(self, v) -> None:
        i = self.findData(v)
        self.setCurrentIndex(i if i >= 0 else 0)


class OptionalFloat(QWidget):
    """Checkbox 'override' + spin box; unchecked = OpenOSV default (None)."""
    changed = Signal()

    def __init__(self, lo: float, hi: float, step: float, default: float, suffix: str = "", parent=None) -> None:
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        self.cb = QCheckBox("Override")
        self.spin = QDoubleSpinBox()
        self.spin.setRange(lo, hi)
        self.spin.setSingleStep(step)
        self.spin.setDecimals(2)
        self.spin.setValue(default)
        self.spin.setSuffix(suffix)
        self.spin.setEnabled(False)
        self.cb.toggled.connect(self.spin.setEnabled)
        self.cb.toggled.connect(lambda _: self.changed.emit())
        self.spin.valueChanged.connect(lambda _: self.changed.emit())
        h.addWidget(self.cb)
        h.addWidget(self.spin, 1)

    def value(self) -> float | None:
        return self.spin.value() if self.cb.isChecked() else None

    def set_value(self, v: float | None) -> None:
        self.cb.setChecked(v is not None)
        if v is not None:
            self.spin.setValue(v)


def state_label(text: str, state: str) -> QLabel:
    l = QLabel(text)
    l.setObjectName({"ok": "Ok", "warn": "Warn", "bad": "Bad"}.get(state, ""))
    return l


def fmt_seconds(s: float | None) -> str:
    if s is None or s < 0:
        return "-"
    s = int(s)
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"
