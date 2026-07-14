from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QFileDialog,
    QMessageBox,
)

from ui.map_widget import MapWidget
from core.gpx_loader import load_gpx
from core.fit_loader import load_fit


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

        if not filename:
            return

        try:
            extension = Path(filename).suffix.lower()

            if extension == ".gpx":
                self.track = load_gpx(filename)
            elif extension == ".fit":
                self.track = load_fit(filename)
            else:
                raise ValueError("Formato non supportato")

            self.file_label.setText(
                f"File: {Path(filename).name} - Punti: {len(self.track.points)}"
            )

            self.map.draw_track(self.track)

        except Exception as error:
            QMessageBox.critical(
                self,
                "Errore caricamento",
                str(error)
            )
