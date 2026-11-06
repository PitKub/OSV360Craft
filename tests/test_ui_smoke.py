"""The whole window builds, every page shows, themes switch (offscreen Qt)."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")


def test_main_window_pages_and_themes(fake_osvtool):
    from PySide6.QtWidgets import QApplication

    from osv360craft.hardware import hardware_report
    from osv360craft.ui.app_context import AppContext
    from osv360craft.ui.main_window import NAV, MainWindow
    from osv360craft.utils.settings_store import SettingsStore

    app = QApplication.instance() or QApplication([])
    s = SettingsStore()
    s.set("benchmark.offer", False)
    ctx = AppContext(s)
    w = MainWindow(ctx, first_run_detect=False)
    rep = hardware_report.build_report(fake_osvtool, test_encoders=False)
    ctx.set_hardware(rep)
    assert ctx.plan is not None
    for i in range(len(NAV)):
        w.go(i)
        app.processEvents()
        assert w.stack.currentIndex() == i
    w.set_theme("light")
    assert s.get("ui.theme") == "light"
    w.toggle_theme()
    assert s.get("ui.theme") == "dark"
    ctx.applyHeading.emit(33.0)
    assert abs(s.get("orientation.initial_yaw") - 33.0) < 1e-6
    w.close()


def test_benchmark_button_reports_the_real_state(fake_osvtool, monkeypatch):
    """Regression: the button said 'osvtool was not found' although detection had found it."""
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

    from osv360craft.hardware import hardware_report
    from osv360craft.ui.app_context import AppContext
    from osv360craft.ui.main_window import MainWindow
    from osv360craft.utils.settings_store import SettingsStore

    seen = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: seen.append("still detecting")))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: (seen.append("not found"), QMessageBox.No)[1]))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (seen.append("file dialog"), ("", ""))[1]))
    app = QApplication.instance() or QApplication([])
    s = SettingsStore()
    s.set("benchmark.offer", False)
    ctx = AppContext(s)
    w = MainWindow(ctx, first_run_detect=False)
    w.analyzer.run_benchmark(False)          # before any detection
    w.analyzer._detecting = True
    ctx.set_hardware(hardware_report.build_report(fake_osvtool, test_encoders=False))
    w.analyzer.run_benchmark(False)          # while a detection is running
    w.analyzer._detecting = False
    w.analyzer.run_benchmark(False)          # found: goes straight to choosing a clip
    ctx.set_hardware(hardware_report.build_report(str(fake_osvtool) + "_missing_dir/osvtool", test_encoders=False))
    if not ctx.hardware.openosv.found:
        w.analyzer.run_benchmark(False)
        assert seen[-1] == "not found"
    assert seen[:3] == ["still detecting", "still detecting", "file dialog"]
    app.processEvents()
    w.close()
