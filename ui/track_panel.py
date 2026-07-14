from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton

from ui.map_widget import MapWidget


class TrackPanel(QWidget):

    def __init__(self, title):
        super().__init__()

        layout = QVBoxLayout()
        toolbar = QHBoxLayout()

        self.import_button = QPushButton("Importa FIT / GPX")
        toolbar.addWidget(self.import_button)

        toolbar.addStretch()

        self.slope_button = QPushButton("Pendenza")
        self.speed_button = QPushButton("Velocità")

        toolbar.addWidget(self.slope_button)
        toolbar.addWidget(self.speed_button)

        layout.addLayout(toolbar)

        self.map = MapWidget()
        layout.addWidget(self.map)

        self.setLayout(layout)
