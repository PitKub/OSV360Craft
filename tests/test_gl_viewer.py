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

    QApplication.instance() or QApplication([])
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


@pytest.mark.parametrize("yaw,pitch", [(0, 0), (-90, 0), (90, 0), (-150, 10), (150, -10), (0, 60), (30, -60),
                                        (-90, 35)])
def test_dual_fisheye_projection_points_the_right_way(yaw, pitch):
    """The proxy preview: a synthetic camera frame (KB5 lenses, one turned 1 degree) whose colours
    encode the direction each pixel sees; the viewer centre must show the direction it looks at,
    on either lens and across the seams."""
    np = pytest.importorskip("numpy")
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication

    from fisheye_synth import calibration, expected_colour, render
    from osv360craft.live import fisheye_calib as fc
    from osv360craft.live.panorama_viewer import gl_available, make_viewer

    QApplication.instance() or QApplication([])
    if not gl_available():
        pytest.skip("no OpenGL context available")
    fe = fc.from_probe(calibration(), 3840)
    arr = np.ascontiguousarray(render(fe))
    img = QImage(arr.data, arr.shape[1], arr.shape[0], arr.shape[1] * 4, QImage.Format_RGBA8888).copy()
    v = make_viewer()
    v.resize(200, 200)
    v.show()
    v.set_image(img, fe)
    v.view.yaw, v.view.pitch, v.view.fov = float(yaw), float(pitch), 20.0
    v.update()
    loop = QEventLoop()
    QTimer.singleShot(500, loop.quit)
    loop.exec()
    c = v.grabFramebuffer().pixelColor(100, 100)
    er, eg = expected_colour(yaw, pitch)
    assert abs(c.red() - er) <= 4 and abs(c.green() - eg) <= 4, (c.red(), c.green(), er, eg)
    v.close()


@pytest.mark.parametrize("pitch", [0, 50])
def test_preview_levelling_on_a_tilted_camera(pitch):
    """A camera held rolled 30 deg and pitched 20 deg: with the gyro levelling the preview shows the
    world level - looking straight ahead gives the horizon, looking up 50 deg gives latitude 50."""
    np = pytest.importorskip("numpy")
    import math
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication

    from fisheye_synth import calibration, render
    from osv360craft.live import attitude as at
    from osv360craft.live import fisheye_calib as fc
    from osv360craft.live.panorama_viewer import gl_available, make_viewer

    QApplication.instance() or QApplication([])
    if not gl_available():
        pytest.skip("no OpenGL context available")
    roll, tilt = math.radians(30), math.radians(20)
    w = at.mul(at.quat_matrix(math.cos(tilt / 2), math.sin(tilt / 2), 0, 0),     # pitch about X
               at.quat_matrix(math.cos(roll / 2), 0, math.sin(roll / 2), 0))     # roll about Y (forward)
    fe = fc.from_probe(calibration(), 3840)
    arr = np.ascontiguousarray(render(fe, world_from_body=w))
    img = QImage(arr.data, arr.shape[1], arr.shape[0], arr.shape[1] * 4, QImage.Format_RGBA8888).copy()
    level = at.Attitude([w]).body_from_view(0, "horizon")
    v = make_viewer()
    v.resize(200, 200)
    v.show()
    v.set_image(img, fe, level)
    v.view.pitch, v.view.fov = float(pitch), 20.0
    loop = QEventLoop()
    QTimer.singleShot(500, loop.quit)
    loop.exec()
    c = v.grabFramebuffer().pixelColor(100, 100)
    expected_green = (pitch + 90) / 180 * 255                    # latitude in world
    assert abs(c.green() - expected_green) <= 4, (c.green(), expected_green)
    v.close()
