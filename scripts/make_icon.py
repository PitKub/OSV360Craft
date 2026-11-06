"""Draws the OSV360Craft icon (assets/OSV360Craft.png and .ico). Run: python scripts/make_icon.py"""
import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPen, QGuiApplication

OUT = Path(__file__).resolve().parents[1] / "assets"


def draw(size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 256.0
    g = QLinearGradient(0, 0, size, size)
    g.setColorAt(0, QColor("#1f6feb"))
    g.setColorAt(1, QColor("#0b1f3a"))
    p.setBrush(g)
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(8 * s, 8 * s, 240 * s, 240 * s), 48 * s, 48 * s)
    c = QPointF(128 * s, 118 * s)
    pen = QPen(QColor(255, 255, 255, 235), 9 * s)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(c, 72 * s, 72 * s)                 # sphere
    pen.setWidthF(6 * s)
    p.setPen(pen)
    p.drawEllipse(c, 30 * s, 72 * s)                 # meridian
    p.drawLine(QPointF(56 * s, 118 * s), QPointF(200 * s, 118 * s))  # equator / horizon
    pen.setColor(QColor("#ffb020"))
    pen.setWidthF(9 * s)
    p.setPen(pen)
    p.drawArc(QRectF(36 * s, 26 * s, 184 * s, 184 * s), 200 * 16, 140 * 16)  # orbit arc
    p.setPen(QColor(255, 255, 255))
    f = QFont("Arial", max(6, int(30 * s)))
    f.setBold(True)
    p.setFont(f)
    p.drawText(QRectF(0, 196 * s, size, 50 * s), Qt.AlignCenter, "360")
    p.end()
    return img


if __name__ == "__main__":
    app = QGuiApplication(sys.argv)
    OUT.mkdir(exist_ok=True)
    draw(256).save(str(OUT / "OSV360Craft.png"))
    ok = draw(256).save(str(OUT / "OSV360Craft.ico"), "ICO")
    print("ico written" if ok else "ICO writer unavailable; convert the PNG with any tool")
