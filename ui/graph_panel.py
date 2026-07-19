from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel


class GraphPanel(QWidget):
    """Placeholder container for activity charts.

    The widget is intentionally kept empty in this commit. The chart engine,
    data series and cursor interaction will be added in following commits.
    """

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Grafici attività"))
        self.setLayout(layout)
