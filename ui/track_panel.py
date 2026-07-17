from pathlib import Path

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFileDialog, QMessageBox, QComboBox, QLineEdit

from ui.map_widget import MapWidget
from core.gpx_loader import load_gpx
from core.fit_loader import load_fit
from core.analyzer import calculate_speed_range, calculate_slope_range
from core.track_capabilities import TrackCapabilities


class TrackPanel(QWidget):
    def __init__(self, title):
        super().__init__()
        self.track = None
        self.capabilities = None

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

        self.info_label = QLabel("")
        layout.addWidget(self.info_label)

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

        if minimum is not None and maximum is not None:
            self.min_value.setText(f"{minimum:.1f}")
            self.max_value.setText(f"{maximum:.1f}")
        else:
            self.min_value.clear()
            self.max_value.clear()

        self.refresh_color()

    def update_available_modes(self):
        self.color_mode.blockSignals(True)
        current = self.color_mode.currentText()
        self.color_mode.clear()
        self.color_mode.addItems(self.capabilities.available_modes)
        if current in self.capabilities.available_modes:
            self.color_mode.setCurrentText(current)
        self.color_mode.blockSignals(False)

    def show_summary(self):
        summary = self.capabilities.summary
        self.info_label.setText(" | ".join(f"{k}: {'✓' if v is True else v}" for k, v in summary.items()))

    def import_file(self):
        filename, _ = QFileDialog.getOpenFileName(self, "Seleziona attività", "", "Attività GPS (*.fit *.gpx)")
        if not filename:
            return

        try:
            ext = Path(filename).suffix.lower()
            self.track = load_gpx(filename) if ext == ".gpx" else load_fit(filename)
            self.capabilities = TrackCapabilities(self.track)

            self.file_label.setText(f"File: {Path(filename).name} - Punti: {len(self.track.points)}")
            self.update_available_modes()
            self.show_summary()
            self.update_scale()

            if self.color_mode.currentText() == "Nessuna":
                self.map.draw_track(self.track)

        except Exception as error:
            QMessageBox.critical(self, "Errore caricamento", str(error))
