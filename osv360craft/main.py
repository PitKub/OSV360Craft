"""OSV360Craft entry point."""

from __future__ import annotations

import sys


def main() -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QSurfaceFormat
    from PySide6.QtWidgets import QApplication

    from osv360craft import APP_NAME, __version__
    from osv360craft.ui.app_context import AppContext
    from osv360craft.ui.main_window import MainWindow
    from osv360craft.utils.logging import get_logger, setup_logging
    from osv360craft.utils.settings_store import SettingsStore

    setup_logging()
    log = get_logger("osv360craft")
    log.info("%s %s starting (Python %s)", APP_NAME, __version__, sys.version.split()[0])
    fmt = QSurfaceFormat()
    fmt.setVersion(2, 1)
    QSurfaceFormat.setDefaultFormat(fmt)
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("PitKub")
    from PySide6.QtGui import QIcon
    from osv360craft.utils.paths import app_root
    icon = app_root() / "assets" / "OSV360Craft.png"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    ctx = AppContext(SettingsStore())
    win = MainWindow(ctx)
    # Fit the screen (a 1920x1080 laptop at 125-150 % scaling has far less room than 1440x900 suggests).
    scr = app.primaryScreen().availableGeometry()
    win.resize(min(1440, int(scr.width() * 0.92)), min(900, int(scr.height() * 0.9)))
    win.show()
    # Paths given on the command line are queued as if dropped on Batch Stitching.
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if args:
        win.go(2)
        win.batch.add_paths(args)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
