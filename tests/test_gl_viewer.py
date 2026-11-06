"""The OpenGL 360 viewer really draws (needs a display with OpenGL; skipped otherwise).

Regression: on Windows the viewer stayed black because PySide6 rejected an int
offset in glVertexAttribPointer; the failure was silent. Run locally with e.g.
  Xvfb :99 &  DISPLAY=:99 QT_QPA_PLATFORM=xcb python -m pytest tests/test_gl_viewer.py
"""

import os
import subprocess

import pytest

from conftest import HAVE_FFMPEG

if os.environ.get("QT_QPA_PLATFORM", "") == "offscreen" or not (os.environ.get("DISPLAY") or os.name == "nt"):
    pytest.skip("needs a real display with OpenGL", allow_module_level=True)


def test_gl_viewer_draws_equirect(tmp_path):
    if not HAVE_FFMPEG:
        pytest.skip("ffmpeg needed")
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication

    from osv360craft.live.panorama_viewer import GLPanoramaViewer, gl_available, make_viewer

    app = QApplication.instance() or QApplication([])
    if not gl_available():
        pytest.skip("no OpenGL context available")
    png = tmp_path / "eq.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1024x512", "-frames:v", "1",
                    "-pix_fmt", "rgb48be", str(png)], check=True)
    v = make_viewer()
    assert isinstance(v, GLPanoramaViewer)
    failures = []
    v.glFailed.connect(failures.append)
    v.resize(640, 360)
    v.show()
    v.set_image(QImage(str(png)))
    loop = QEventLoop()
    QTimer.singleShot(1200, loop.quit)
    loop.exec()
    fb = v.grabFramebuffer()
    colours = {fb.pixelColor(x, y).name() for x in range(0, 640, 29) for y in range(0, 360, 29)}
    assert not failures and v.gl_ok, failures
    assert len(colours) > 5, "the viewer drew nothing"
    v.close()
