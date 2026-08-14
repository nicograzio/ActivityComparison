"""DuoTrack application entry point.

This module is intentionally tiny: it creates the Qt application, instantiates
``MainWindow`` and starts the event loop.

Called by:
    The Python interpreter when launching ``main.py`` directly.

Calls:
    - ``QApplication`` from PyQt6
    - ``MainWindow`` from ``ui.main_window``
"""

import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication, QSplashScreen
from PyQt6.QtGui import QPixmap, QColor, QPainter, QFont, QIcon
from PyQt6.QtCore import Qt, QTimer, QRectF
from PyQt6.QtSvg import QSvgRenderer
from ui.main_window import MainWindow

APP_NAME = "DuoTrack"
ASSETS_DIR = Path(__file__).resolve().parent / "assets"
LOGO_PATH = ASSETS_DIR / "logo.svg"


def create_splash_screen():
    """Build the splash screen with the DuoTrack logo and name."""
    pixmap = QPixmap(400, 300)
    pixmap.fill(QColor("#2c3e50"))  # Dark elegant background
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Draw a border
    painter.setPen(QColor("#3498db"))
    painter.drawRect(0, 0, 399, 299)

    # Draw the logo (SVG) centered in the upper area
    renderer = QSvgRenderer(str(LOGO_PATH))
    if renderer.isValid():
        logo_size = 140
        logo_rect = QRectF(
            (pixmap.width() - logo_size) / 2,
            20,
            logo_size,
            logo_size,
        )
        renderer.render(painter, logo_rect)

    # Main title
    font = QFont()
    font.setPointSize(26)
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor("white"))
    painter.drawText(
        pixmap.rect().adjusted(0, 150, 0, 0),
        Qt.AlignmentFlag.AlignCenter,
        APP_NAME,
    )

    # Subtitle / status
    font.setPointSize(12)
    font.setBold(False)
    painter.setFont(font)
    painter.setPen(QColor("#bdc3c7"))
    painter.drawText(
        pixmap.rect().adjusted(0, 210, 0, 0),
        Qt.AlignmentFlag.AlignCenter,
        "Caricamento componenti in corso...",
    )

    painter.end()
    return QSplashScreen(pixmap)


if __name__ == "__main__":
    app = QApplication(sys.argv)

    # Set the application icon
    icon_path = ASSETS_DIR / "icon.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    splash = create_splash_screen()
    splash.show()
    app.processEvents()

    window = MainWindow()
    # Must show full screen immediately so native window handle exists for QWebEngine
    window.showFullScreen()

    # Show normal window
    # window.show()

    def dismiss_splash():
        if splash.isVisible():
            splash.finish(window)

    # Dismiss splash screen when ready or at most after 3 seconds timeout
    window.fullyReady.connect(dismiss_splash)
    QTimer.singleShot(3000, dismiss_splash)

    sys.exit(app.exec())