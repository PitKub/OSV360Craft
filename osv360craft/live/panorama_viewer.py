"""Interactive 360-degree viewer for equirectangular frames.

OpenGL path: one fragment shader turns every screen pixel into a view ray,
rotates it by the view (yaw, pitch, roll) and samples the equirect texture.
Fallback (no OpenGL, e.g. remote desktop without GPU): the flat equirect is
drawn with QPainter, panned horizontally with wrap-around.

The view orientation here is a viewing aid only. It never changes the
export, except when the user explicitly applies the current heading as the
file's initial viewing direction (a lossless metadata value).

Controls: drag = pan/tilt, wheel = zoom, Alt+drag or right-drag = roll,
double-click = reset, F = flat/sphere toggle, H = horizon guide.
"""

from __future__ import annotations

import math
import struct

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget

from osv360craft.utils.logging import get_logger

log = get_logger(__name__)

try:
    from PySide6.QtOpenGL import (QOpenGLBuffer, QOpenGLShader, QOpenGLShaderProgram, QOpenGLTexture,
                                  QOpenGLVertexArrayObject)
    from PySide6.QtOpenGLWidgets import QOpenGLWidget
    from shiboken6 import VoidPtr
    HAVE_GL = True
except ImportError:  # pragma: no cover
    HAVE_GL = False

VERT = """
attribute vec2 pos;
varying vec2 v;
void main() { v = pos; gl_Position = vec4(pos, 0.0, 1.0); }
"""

FRAG = """
#ifdef GL_ES
precision highp float;
#endif
uniform sampler2D tex;
uniform mat3 rot;
uniform float tanHalf;
uniform float aspect;
uniform int flatMode;
uniform float flatYaw;
uniform int guide;
varying vec2 v;
const float PI = 3.14159265358979;
void main() {
    vec2 tc;
    if (flatMode == 1) {
        tc = vec2(fract(v.x * 0.5 + 0.5 + flatYaw), 0.5 - v.y * 0.5);
    } else {
        vec3 d = normalize(rot * vec3(v.x * tanHalf * aspect, v.y * tanHalf, -1.0));
        float lon = atan(d.x, -d.z);
        float lat = asin(clamp(d.y, -1.0, 1.0));
        tc = vec2(lon / (2.0 * PI) + 0.5, 0.5 - lat / PI);
    }
    vec4 c = texture2D(tex, tc);
    if (guide == 1 && abs(tc.y - 0.5) < 0.0015) c = mix(c, vec4(1.0, 0.8, 0.0, 1.0), 0.8);
    gl_FragColor = vec4(c.rgb, 1.0);
}
"""


def rotation_matrix(yaw: float, pitch: float, roll: float) -> list[float]:
    """Column-major 3x3 for GLSL: R = Ry(yaw) * Rx(pitch) * Rz(roll), degrees."""
    y, p, r = (math.radians(a) for a in (yaw, pitch, roll))
    cy, sy, cp, sp, cr, sr = math.cos(y), math.sin(y), math.cos(p), math.sin(p), math.cos(r), math.sin(r)
    ry = [[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]]
    rx = [[1, 0, 0], [0, cp, -sp], [0, sp, cp]]
    rz = [[cr, -sr, 0], [sr, cr, 0], [0, 0, 1]]

    def mul(a, b):
        return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]

    m = mul(mul(ry, rx), rz)
    return [m[i][j] for j in range(3) for i in range(3)]


class _ViewState:
    def __init__(self) -> None:
        self.yaw = 0.0
        self.pitch = 0.0
        self.roll = 0.0
        self.fov = 90.0
        self.flat = False
        self.guide = False

    def reset(self) -> None:
        self.yaw = self.pitch = self.roll = 0.0
        self.fov = 90.0


class _Interaction:
    viewChanged = None  # placeholder for type checkers

    def _init_interaction(self) -> None:
        self.view = _ViewState()
        self._last: QPointF | None = None
        self._rolling = False
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(False)

    def mousePressEvent(self, e) -> None:
        self._last = e.position()
        self._rolling = e.button() == Qt.RightButton or bool(e.modifiers() & Qt.AltModifier)

    def mouseMoveEvent(self, e) -> None:
        if self._last is None:
            return
        d = e.position() - self._last
        self._last = e.position()
        scale = self.view.fov / max(1, self.height())
        if self._rolling:
            self.view.roll = (self.view.roll + d.x() * 0.3 + 180) % 360 - 180
        elif self.view.flat:
            self.view.yaw = (self.view.yaw - d.x() * 360.0 / max(1, self.width()) + 180) % 360 - 180
        else:
            self.view.yaw = (self.view.yaw - d.x() * scale + 180) % 360 - 180
            self.view.pitch = max(-90.0, min(90.0, self.view.pitch + d.y() * scale))
        self._changed()

    def mouseReleaseEvent(self, e) -> None:
        self._last = None

    def mouseDoubleClickEvent(self, e) -> None:
        self.view.reset()
        self._changed()

    def wheelEvent(self, e) -> None:
        steps = e.angleDelta().y() / 120.0
        self.view.fov = max(20.0, min(140.0, self.view.fov - steps * 5.0))
        self._changed()

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key_F:
            self.view.flat = not self.view.flat
        elif e.key() == Qt.Key_H:
            self.view.guide = not self.view.guide
        else:
            return super().keyPressEvent(e)
        self._changed()

    def set_flat(self, flat: bool) -> None:
        self.view.flat = flat
        self._changed()

    def set_guide(self, on: bool) -> None:
        self.view.guide = on
        self._changed()

    def _changed(self) -> None:
        self.viewChanged.emit(self.view.yaw, self.view.pitch, self.view.roll, self.view.fov)
        self.update()


if HAVE_GL:
    class GLPanoramaViewer(_Interaction, QOpenGLWidget):
        viewChanged = Signal(float, float, float, float)
        glFailed = Signal(str)

        def __init__(self, parent=None) -> None:
            QOpenGLWidget.__init__(self, parent)
            self._init_interaction()
            self._image: QImage | None = None
            self._dirty = False
            self._tex = None
            self._prog = None
            self._vbo = None
            self._vao = None
            self.gl_ok = True

        def set_image(self, img: QImage) -> None:
            self._image = img
            self._dirty = True
            self.update()

        def current_image(self) -> QImage | None:
            return self._image

        def _fail(self, msg: str) -> None:
            if self.gl_ok:
                self.gl_ok = False
                self.gl_error = msg
                log.error(msg)
                self.glFailed.emit(msg)

        def initializeGL(self) -> None:
            try:
                self._prog = QOpenGLShaderProgram(self)
                ok = self._prog.addShaderFromSourceCode(QOpenGLShader.Vertex, VERT)
                ok = ok and self._prog.addShaderFromSourceCode(QOpenGLShader.Fragment, FRAG)
                self._prog.bindAttributeLocation("pos", 0)
                ok = ok and self._prog.link()
                if not ok:
                    raise RuntimeError(self._prog.log())
                self._vao = QOpenGLVertexArrayObject(self)
                self._vao.create()
                self._vao.bind()
                self._vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
                self._vbo.create()
                self._vbo.bind()
                data = struct.pack("8f", -1, -1, 1, -1, -1, 1, 1, 1)
                self._vbo.allocate(data, len(data))
                f = self.context().functions()
                f.glEnableVertexAttribArray(0)
                # PySide6 takes the offset into the bound buffer as a VoidPtr, not an int.
                f.glVertexAttribPointer(0, 2, 0x1406, 0, 0, VoidPtr(0))  # GL_FLOAT, not normalised, tight
                self._vbo.release()
                self._vao.release()
                self.context().aboutToBeDestroyed.connect(self._cleanup)
            except Exception as e:  # report: the page swaps in the software viewer
                self._fail(f"OpenGL viewer could not start: {e}")

        def _cleanup(self) -> None:
            """Free GL objects while the context is still current (avoids warnings on exit)."""
            self.makeCurrent()
            if self._tex is not None:
                self._tex.destroy()
                self._tex = None
            self.doneCurrent()

        def _upload(self) -> None:
            if self._tex is not None:
                self._tex.destroy()
            img = self._image.convertToFormat(QImage.Format_RGBA8888)
            self._tex = QOpenGLTexture(img, QOpenGLTexture.DontGenerateMipMaps)
            self._tex.setMinificationFilter(QOpenGLTexture.Linear)
            self._tex.setMagnificationFilter(QOpenGLTexture.Linear)
            self._tex.setWrapMode(QOpenGLTexture.DirectionS, QOpenGLTexture.Repeat)
            self._tex.setWrapMode(QOpenGLTexture.DirectionT, QOpenGLTexture.ClampToEdge)
            self._dirty = False

        def paintGL(self) -> None:
            try:
                self._paint()
            except Exception as e:
                self._fail(f"OpenGL viewer failed while drawing: {e}")

        def _paint(self) -> None:
            f = self.context().functions()
            f.glClearColor(0.07, 0.07, 0.08, 1.0)
            f.glClear(0x4000)
            if not self.gl_ok or self._image is None:
                return
            if self._dirty:
                self._upload()
            self._prog.bind()
            self._tex.bind(0)
            self._prog.setUniformValue1i(self._prog.uniformLocation("tex"), 0)
            m = rotation_matrix(self.view.yaw, self.view.pitch, self.view.roll)
            from PySide6.QtGui import QMatrix3x3
            self._prog.setUniformValue(self._prog.uniformLocation("rot"), QMatrix3x3(m))
            self._prog.setUniformValue1f(self._prog.uniformLocation("tanHalf"), math.tan(math.radians(self.view.fov) / 2))
            self._prog.setUniformValue1f(self._prog.uniformLocation("aspect"), self.width() / max(1, self.height()))
            self._prog.setUniformValue1i(self._prog.uniformLocation("flatMode"), 1 if self.view.flat else 0)
            self._prog.setUniformValue1f(self._prog.uniformLocation("flatYaw"), self.view.yaw / 360.0)
            self._prog.setUniformValue1i(self._prog.uniformLocation("guide"), 1 if self.view.guide else 0)
            self._vao.bind()
            f.glDrawArrays(0x0005, 0, 4)  # GL_TRIANGLE_STRIP
            self._vao.release()
            self._tex.release()
            self._prog.release()


class SoftwarePanoramaViewer(_Interaction, QWidget):
    """Flat equirect view with horizontal wrap; used when OpenGL is unavailable."""
    viewChanged = Signal(float, float, float, float)

    def __init__(self, parent=None) -> None:
        QWidget.__init__(self, parent)
        self._init_interaction()
        self.view.flat = True
        self._image: QImage | None = None
        self.gl_ok = False

    def set_image(self, img: QImage) -> None:
        self._image = img
        self.update()

    def current_image(self) -> QImage | None:
        return self._image

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(18, 18, 20))
        if self._image is None:
            p.setPen(QColor(150, 150, 150))
            p.drawText(self.rect(), Qt.AlignCenter, "Open an .OSV recording to preview the stitch")
            return
        w = self.width()
        h = min(self.height(), w // 2)
        y0 = (self.height() - h) // 2
        img = self._image.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        shift = int((self.view.yaw / 360.0) * w) % w
        p.drawImage(-shift, y0, img)
        p.drawImage(w - shift, y0, img)
        if self.view.guide:
            p.setPen(QPen(QColor(255, 200, 0), 1))
            p.drawLine(0, y0 + h // 2, w, y0 + h // 2)


def gl_available() -> bool:
    """True when an OpenGL context can really be created (not just imported)."""
    if not HAVE_GL:
        return False
    try:
        from PySide6.QtGui import QGuiApplication, QOffscreenSurface, QOpenGLContext
        if QGuiApplication.platformName() in ("offscreen", "minimal"):
            return False
        ctx = QOpenGLContext()
        if not ctx.create():
            return False
        surf = QOffscreenSurface()
        surf.create()
        ok = ctx.makeCurrent(surf)
        ctx.doneCurrent()
        return bool(ok)
    except Exception:
        return False


def make_viewer(parent=None, prefer_gl: bool = True):
    if prefer_gl and gl_available():
        return GLPanoramaViewer(parent)
    return SoftwarePanoramaViewer(parent)
