from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QFileDialog,
)

from ui.map_widget import MapWidget


class TrackPanel(QWidget):

    def __init__(self, title):
        super().__init__()

        self.track = None

        layout = QVBoxLayout()
        toolbar = QHBoxLayout()

        self.import_button = QPushButton("Importa FIT / GPX")
        self.import_button.clicked.connect(self.import_file)
        toolbar.addWidget(self.import_button)

        toolbar.addStretch()

        self.slope_button = QPushButton("Pendenza")
        self.speed_button = QPushButton("Velocità")

        toolbar.addWidget(self.slope_button)
        toolbar.addWidget(self.speed_button)

        layout.addLayout(toolbar)

        self.file_label = QLabel("File: nessun file caricato")
        layout.addWidget(self.file_label)

        self.map = MapWidget()
        layout.addWidget(self.map)

        self.setLayout(layout)

    def import_file(self):
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Seleziona attività",
            "",
            "Attività GPS (*.fit *.gpx)"
        )

        if filename:
            self.file_label.setText(
                f"File: {filename.split('/')[-1]}"
            )
