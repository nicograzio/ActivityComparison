from pathlib import Path

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFileDialog, QMessageBox, QComboBox, QLineEdit

try:
    from ui.vector_map_widget import VectorMapWidget as MapWidget
except Exception:
    from ui.map_widget import MapWidget

from ui.range_slider import RangeSlider
from core.gpx_loader import load_gpx
from core.fit_loader import load_fit
from core.analyzer import calculate_speed_range, calculate_slope_range
from core.track_capabilities import TrackCapabilities


class TrackPanel(QWidget):
    def __init__(self, title):
        super().__init__()
        self.track = None
        self.capabilities = None
        self.full_distance = 0.0

        layout = QVBoxLayout(self)
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
        self.min_value = QLineEdit(); self.max_value = QLineEdit()
        toolbar.addWidget(self.min_value); toolbar.addWidget(self.max_value)
        layout.addLayout(toolbar)

        self.info_label = QLabel("")
        layout.addWidget(self.info_label)
        self.map = MapWidget()
        layout.addWidget(self.map)

        self.range_label = QLabel("Nessuna attività caricata")
        layout.addWidget(self.range_label)
        self.range_slider = RangeSlider()
        self.range_slider.valuesChanged.connect(self.update_trim)
        layout.addWidget(self.range_slider)

        self.color_mode.currentTextChanged.connect(self.update_scale)

    def update_trim(self, start, end):
        if not self.track:
            return
        start_km = self.full_distance * start / 1000
        end_km = self.full_distance * end / 1000
        self.range_label.setText(f"Visualizzazione: {start_km:.2f} km → {end_km:.2f} km / {self.full_distance:.2f} km")

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
        self.map.draw_track(self.track, mode, minimum, maximum)

    def import_file(self):
        filename, _ = QFileDialog.getOpenFileName(self, "Seleziona attività", "", "Attività GPS (*.fit *.gpx)")
        if not filename:
            return
        try:
            ext = Path(filename).suffix.lower()
            self.track = load_gpx(filename) if ext == ".gpx" else load_fit(filename)
            self.capabilities = TrackCapabilities(self.track)
            self.file_label.setText(Path(filename).name)
            self.full_distance = getattr(self.track, "distance", 0) / 1000
            self.range_slider.setValues(0, 1000)
            self.update_trim(0, 1000)
            self.map.draw_track(self.track)
        except Exception as error:
            QMessageBox.critical(self, "Errore caricamento", str(error))
