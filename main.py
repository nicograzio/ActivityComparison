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

from PyQt6.QtWidgets import QApplication

from ui.main_window import MainWindow


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.showFullScreen()
    sys.exit(app.exec())