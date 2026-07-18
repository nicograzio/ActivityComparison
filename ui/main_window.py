from PyQt6.QtWidgets import QMainWindow, QWidget, QHBoxLayout

from ui.comparison_controls_panel import ComparisonControlsPanel
from ui.track_panel import TrackPanel


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()

        self.setWindowTitle("Activity Comparison")

        central = QWidget()
        layout = QHBoxLayout()

        self.left_panel = TrackPanel("Activity A")
        self.right_panel = TrackPanel("Activity B")
        self.controls_panel = ComparisonControlsPanel()

        layout.addWidget(self.left_panel)
        layout.addWidget(self.controls_panel)
        layout.addWidget(self.right_panel)

        central.setLayout(layout)
        self.setCentralWidget(central)
