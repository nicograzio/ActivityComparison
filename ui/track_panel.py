from pathlib import Path

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFileDialog, QMessageBox, QComboBox, QLineEdit

from ui.map_widget import MapWidget
from core.gpx_loader import load_gpx
from core.fit_loader import load_fit
from core.analyzer import calculate_speed_range, calculate_slope_range


class TrackPanel(QWidget):
    def __init__(self, title):
        super().__init__()
        self.track = None
        layout = QVBoxLayout()
        toolbar = QHBoxLayout()

        self.import_button = QPushButton("Importa FIT / GPX")
        self.import_button.clicked.connect(self.import_file)
        toolbar.addWidget(self.import_button)

        self.file_label = QLabel("File: nessun file caricato")
        toolbar.addWidget(self.file_label)
        toolbar.addStretch()

        toolbar.addWidget(QLabel("Colora per:"))
        self.color_mode = QComboBox()
        self.color_mode.addItems(["Nessuna", "Velocità", "Pendenza"])
        toolbar.addWidget(self.color_mode)

        toolbar.addWidget(QLabel("Min:"))
        self.min_value = QLineEdit()
        self.min_value.setFixedWidth(70)
        toolbar.addWidget(self.min_value)

        toolbar.addWidget(QLabel("Max:"))
        self.max_value = QLineEdit()
        self.max_value.setFixedWidth(70)
        toolbar.addWidget(self.max_value)

        layout.addLayout(toolbar)
        self.map = MapWidget()
        layout.addWidget(self.map)
        self.setLayout(layout)

        self.color_mode.currentTextChanged.connect(self.update_scale)
        self.min_value.editingFinished.connect(self.refresh_color)
        self.max_value.editingFinished.connect(self.refresh_color)

    def refresh_color(self):
        if self.track:
            self.map.draw_track(
                self.track,
                self.color_mode.currentText(),
                float(self.min_value.text() or 0),
                float(self.max_value.text() or 0)
            )

    def update_scale(self):
        if not self.track:
            return

        mode = self.color_mode.currentText()
        if mode == "Velocità":
            minimum, maximum = calculate_speed_range(self.track)
        elif mode == "Pendenza":
            minimum, maximum = calculate_slope_range(self.track)
        else:
            minimum, maximum = 0, 0

        self.min_value.setText(f"{minimum:.1f}")
        self.max_value.setText(f"{maximum:.1f}")
        self.refresh_color()

    def import_file(self):
        filename, _ = QFileDialog.getOpenFileName(self, "Seleziona attività", "", "Attività GPS (*.fit *.gpx)")
        if not filename:
            return

        try:
            ext = Path(filename).suffix.lower()
            self.track = load_gpx(filename) if ext == ".gpx" else load_fit(filename)
            self.file_label.setText(f"File: {Path(filename).name} - Punti: {len(self.track.points)}")
            self.update_scale()
            if self.color_mode.currentText() == "Nessuna":
                self.map.draw_track(self.track)

        except Exception as error:
            QMessageBox.critical(self, "Errore caricamento", str(error))
