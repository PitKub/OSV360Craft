"""User guide, About and Donate dialogs."""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout)

from osv360craft import APP_NAME, __version__

REPO_URL = "https://github.com/PitKub/OSV360Craft"
DONATION_ADDRESS = "pitkub@gmail.com"  # same voluntary PayPal address as ProxyCraft

GUIDE_HTML = f"""
<h1>{APP_NAME} {__version__}</h1>
<p><b>Stitch original DJI Osmo 360 <code>.OSV</code> recordings into 360-degree equirectangular video, with
the stitching engine of the open-source OpenOSV project.</b> Your original files are never modified.</p>

<h2>Getting started</h2>
<ol>
<li>On first start {APP_NAME} detects your CPU, GPU, memory, OpenOSV and FFmpeg, and tests every video
encoder. The result and the chosen settings are in <b>Hardware Analyzer</b>.</li>
<li>Optionally run the <b>benchmark</b> there with one of your own clips (1-3 minutes on a GPU). It measures
decoding, stitching and encoding on this computer and refines the automatic choices.</li>
<li>Look at a clip in <b>Live Stitching</b>, or go straight to <b>Batch Stitching</b>: add files or folders,
choose a project folder, press <b>Start</b>.</li>
</ol>

<h2>Pure stitch - no quality loss beyond the stitch itself</h2>
<p>The defaults change nothing except what stitching requires:</p>
<ul>
<li><b>Colour is kept</b>: D-Log M stays D-Log M, HLG stays HLG, Rec.709 stays Rec.709.</li>
<li><b>10-bit is kept</b> end to end; OpenOSV works internally in 16-bit or 32-bit floating point.</li>
<li><b>Native resolution</b> (2 x lens height: 7680 x 3840 for 8K recordings, 6000 x 3000 for 6K)
and the <b>source frame rate</b>. Nothing is interpolated.</li>
<li><b>Original audio</b> is copied, not re-encoded.</li>
<li>HEVC 10-bit at <b>CRF 16</b> (visually lossless). Choose <i>Maximum</i> (CRF 10) for archive masters.</li>
</ul>
<p>Two things cannot be avoided: the stitch resamples the two fisheye images into one sphere, and a video
file is compressed. For a completely uncompressed result choose <i>16-bit TIFF image sequence</i>.</p>
<p><b>Why D-Log M takes longer:</b> OpenOSV only writes Rec.709, PQ or HLG straight to video. To keep
D-Log M, {APP_NAME} lets OpenOSV write lossless 16-bit TIFF chunks, encodes them to 10-bit HEVC with
FFmpeg and joins them without re-encoding. Temporary chunks are deleted as it goes; the job needs some
extra free disk space while it runs.</p>

<h2>Horizon, levelling and direction (Stitching Controls)</h2>
<ul>
<li><b>Horizon levelling</b> keeps the horizon level from the camera's gyroscope.</li>
<li><b>Direction lock</b> keeps the first frame's heading, like DJI Studio's direction lock.</li>
<li><b>RockSteady</b> smooths the camera's rotation; <b>RockSteady + horizon</b> also levels it.</li>
<li><b>Off</b> keeps the footage exactly as the camera moved.</li>
<li><b>Initial viewing direction</b>: the direction players open on. It is stored as 360 metadata and is
lossless. In Live Stitching, turn the view and press <i>Use this heading</i>.</li>
</ul>
<p>The gyro corrections happen inside the stitch, so they cost no extra quality.</p>

<h2>Live Stitching</h2>
<p>Open an <code>.OSV</code> (or drop it on the page). Drag to look around, use the mouse wheel to zoom,
right-drag or Alt+drag to roll, double-click to reset. <b>F</b> switches between the sphere and the flat
panorama, <b>H</b> shows a horizon guide.</p>
<p>Every frame shown is a real OpenOSV stitch at the preview size. A paused frame is stitched exactly.
During playback, frames appear as fast as your computer stitches them; the panel shows the real playback,
stitching and decoding speeds. On most computers 8K playback is not real time; the 2K and Performance
previews are faster. The preview size never changes the export.</p>

<h2>Batch Stitching</h2>
<ul>
<li>Folders are searched with subfolders; <code>.LRF</code> proxies, empty files and duplicates are skipped.</li>
<li>Jobs run one at a time by default. <b>Pause after current</b> finishes the running clip;
<b>Stop</b> ends it now and deletes the unfinished file. <b>Retry failed</b> queues failed clips again.
The queue is remembered when you close the app.</li>
<li>Output goes to the project folder: <code>Stitched</code>, <code>Reports</code>, <code>Logs</code>,
<code>Audio</code>, <code>Preview</code>, and <code>Original/sources.json</code> (a list of the originals, which
stay where they are).</li>
<li>A file is only reported as done after it has been <b>validated</b>: resolution and 2:1 aspect, 10-bit,
frame rate, frame count, colour tags, audio and A/V sync, Spherical Video V1 and V2 metadata and a decode
test. The report is in <code>Reports</code>. A file that fails validation keeps its <code>.partial</code>
name only if you asked to keep failed files; otherwise it is deleted.</li>
</ul>

<h2>Processing profiles</h2>
<ul>
<li><b>AUTO</b> (default): fastest working renderer and encoder found on this computer, CRF 16.</li>
<li><b>QUALITY</b>: CRF 12 and the largest preview.</li>
<li><b>PERFORMANCE</b>: CRF 20 and the smallest preview.</li>
</ul>
<p>A quality you choose yourself in Export Settings always wins. If a GPU renderer fails at start-up,
the next one is tried (for example CUDA, then OpenCL, then CPU) and the log says why. Resolution and
quality are never changed silently.</p>

<h2>Requirements</h2>
<ul>
<li>Windows 10 or 11, 64-bit. Microsoft Visual C++ 2015-2022 Redistributable (x64), needed by OpenOSV.</li>
<li>GPU: CUDA on NVIDIA GTX 16 / RTX 20 and newer; OpenCL on AMD and Intel; otherwise the CPU (slow at 8K).</li>
<li>The installer includes OpenOSV and FFmpeg. Without the installer, put OpenOSV's <code>cli</code> folder in
<code>external\\OpenOSV\\cli</code> and FFmpeg in <code>external\\ffmpeg</code>, or set the paths in Settings.</li>
</ul>

<h2>Troubleshooting</h2>
<ul>
<li><b>osvtool could not start</b>: install the Visual C++ Redistributable from
<a href="https://aka.ms/vs/17/release/vc_redist.x64.exe">aka.ms/vs/17/release/vc_redist.x64.exe</a>.</li>
<li><b>An encoder shows "failed test"</b>: the GPU or driver cannot run it; another one is used. Update the GPU driver.</li>
<li><b>An antivirus warning</b> (for example a name ending in <code>!ml</code>): machine-learning checks sometimes flag
new, unsigned apps. Make sure you downloaded OSV360Craft from its GitHub Releases page, check the file on
<a href="https://www.virustotal.com">virustotal.com</a>, and report a false detection to Microsoft at
<a href="https://www.microsoft.com/en-us/wdsi/filesubmission">microsoft.com/wdsi/filesubmission</a>. Never turn
your antivirus off to run an app; on a work computer, ask your IT or security team first.</li>
<li><b>Not enough free space</b>: an 8K render can need tens of GB, more while D-Log M chunks are encoded.</li>
<li><b>Reporting a problem</b>: Logs and Diagnostics &gt; <i>Save diagnostic bundle</i> (no media is included) and
open an issue at <a href="{REPO_URL}/issues">{REPO_URL}/issues</a>.</li>
</ul>

<h2>Privacy</h2>
<p>{APP_NAME} works offline. It sends no media, settings or usage data anywhere. The only network actions are
the links in Help, which open in your browser.</p>
"""


class HelpDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} - User Guide")
        self.resize(880, 720)
        lay = QVBoxLayout(self)
        b = QTextBrowser()
        b.setOpenExternalLinks(True)
        b.setHtml(GUIDE_HTML)
        lay.addWidget(b)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)


def about_dialog(parent) -> None:
    dlg = QDialog(parent)
    dlg.setWindowTitle(f"About {APP_NAME}")
    dlg.resize(560, 360)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(24, 22, 24, 18)
    t = QLabel(f"{APP_NAME} {__version__}")
    t.setObjectName("PageTitle")
    lay.addWidget(t)
    for text in (
        "Stitching, preview and rendering of DJI Osmo 360 .OSV recordings.",
        "Free and open source under the MIT License. Donations are completely optional.",
        "Stitching engine: OpenOSV by The OpenOSV Contributors (Apache License 2.0), github.com/Kemerd/OpenOSV. "
        "Video encoding: FFmpeg. User interface: Qt for Python (PySide6, LGPLv3).",
        "OSV360Craft and OpenOSV are independent projects, not affiliated with or endorsed by DJI. "
        "DJI, Osmo and Osmo 360 are trademarks of SZ DJI Technology Co., Ltd.",
        "Copyright (c) 2026 Piotr Kubicki.",
    ):
        l = QLabel(text)
        l.setWordWrap(True)
        lay.addWidget(l)
    src = QLabel(f"<a href='{REPO_URL}'>Source code on GitHub</a>")
    src.setOpenExternalLinks(True)
    lay.addWidget(src)
    lay.addStretch(1)
    ok = QPushButton("OK")
    ok.clicked.connect(dlg.accept)
    row = QHBoxLayout()
    row.addStretch(1)
    row.addWidget(ok)
    lay.addLayout(row)
    dlg.exec()


def donate_dialog(parent) -> None:
    dlg = QDialog(parent)
    dlg.setWindowTitle(f"Support {APP_NAME}")
    dlg.resize(520, 330)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(26, 24, 26, 20)
    lay.setSpacing(14)
    h = QLabel(f"Enjoying {APP_NAME}?")
    h.setAlignment(Qt.AlignCenter)
    h.setObjectName("PageTitle")
    lay.addWidget(h)
    m = QLabel(f"{APP_NAME} is free to use. If it helps your work and you would like to support its continued "
               "development, you can make a voluntary donation through PayPal. There is no obligation - thank you "
               "for using the app.")
    m.setWordWrap(True)
    m.setAlignment(Qt.AlignCenter)
    lay.addWidget(m)
    a = QLabel(f"<b>PayPal donation address</b><br><span style='font-size:16px;'>{DONATION_ADDRESS}</span>")
    a.setAlignment(Qt.AlignCenter)
    a.setTextInteractionFlags(Qt.TextSelectableByMouse)
    lay.addWidget(a)
    row = QHBoxLayout()
    copy = QPushButton("Copy PayPal address")
    pay = QPushButton("Open PayPal")
    close = QPushButton("Close")

    def do_copy() -> None:
        QApplication.clipboard().setText(DONATION_ADDRESS)
        copy.setText("Address copied")

    copy.clicked.connect(do_copy)
    pay.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://www.paypal.com/")))
    close.clicked.connect(dlg.accept)
    row.addWidget(copy)
    row.addWidget(pay)
    row.addStretch(1)
    row.addWidget(close)
    lay.addLayout(row)
    dlg.exec()
