from PyQt6.QtWidgets import QMainWindow, QWidget, QHBoxLayout

from ui.track_panel import TrackPanel


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()

        self.setWindowTitle("Activity Comparison")

        central = QWidget()
        layout = QHBoxLayout()

        layout.addWidget(TrackPanel("Activity A"))
        layout.addWidget(TrackPanel("Activity B"))

        central.setLayout(layout)
        self.setCentralWidget(central)
