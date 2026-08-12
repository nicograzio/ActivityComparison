"""ActivityComparison application entry point.

This module is intentionally tiny: it creates the Qt application, instantiates
``MainWindow`` and starts the event loop.

Called by:
    The Python interpreter when launching ``main.py`` directly.

Calls:
    - ``QApplication`` from PyQt6
    - ``MainWindow`` from ``ui.main_window``
"""

import sys
from PyQt6.QtWidgets import QApplication, QSplashScreen
from PyQt6.QtGui import QPixmap, QColor, QPainter
from PyQt6.QtCore import Qt, QTimer
from ui.main_window import MainWindow

def create_splash_screen():
    pixmap = QPixmap(400, 250)
    pixmap.fill(QColor("#2c3e50"))  # Dark elegant background
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    
    # Draw a border
    painter.setPen(QColor("#3498db"))
    painter.drawRect(0, 0, 399, 249)
    
    # Main title
    font = painter.font()
    font.setPointSize(24)
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor("white"))
    painter.drawText(pixmap.rect().adjusted(0, -30, 0, 0), Qt.AlignmentFlag.AlignCenter, "Activity Comparison")
    
    # Subtitle / status
    font.setPointSize(12)
    font.setBold(False)
    painter.setFont(font)
    painter.setPen(QColor("#bdc3c7"))
    painter.drawText(pixmap.rect().adjusted(0, 40, 0, 0), Qt.AlignmentFlag.AlignCenter, "Caricamento componenti in corso...")
    
    painter.end()
    return QSplashScreen(pixmap)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    splash = create_splash_screen()
    splash.show()
    app.processEvents()
    
    window = MainWindow()
    # Must show full screen immediately so native window handle exists for QWebEngine
    # window.showFullScreen()
    
    # Show normal window
    window.show()
    
    def dismiss_splash():
        if splash.isVisible():
            splash.finish(window)

    # Dismiss splash screen when ready or at most after 3 seconds timeout
    window.fullyReady.connect(dismiss_splash)
    QTimer.singleShot(3000, dismiss_splash)
    
    sys.exit(app.exec())
