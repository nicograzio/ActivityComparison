from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFileDialog, QMessageBox, QComboBox, QLineEdit

try:
    from ui.vector_map_widget import VectorMapWidget as MapWidget
except Exception:
    from ui.map_widget import MapWidget

from ui.range_slider import RangeSlider
from core.gpx_loader import load_gpx
from core.fit_loader import load_fit
from core.analyzer import (
    calculate_speed_range,
    calculate_slope_range,
    track_distance_profile,
    trim_track_by_distance,
)
from core.track_capabilities import TrackCapabilities


class TrackPanel(QWidget):
    activity_loaded = pyqtSignal(object)
    visible_track_changed = pyqtSignal(object)

    def __init__(self, title):
        super().__init__()
        self.track = None
        self.capabilities = None
        self.full_distance_m = 0.0
        self.visible_start_m = 0.0
        self.visible_end_m = 0.0
        self.scale_mode = "auto"
        self.manual_scale_min = None
        self.manual_scale_max = None

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
        self.min_value = QLineEdit()
        self.max_value = QLineEdit()
        self.min_value.editingFinished.connect(self._on_scale_limits_edited)
        self.max_value.editingFinished.connect(self._on_scale_limits_edited)
        toolbar.addWidget(self.min_value)
        toolbar.addWidget(self.max_value)
        layout.addLayout(toolbar)

        self.info_label = QLabel("")
        layout.addWidget(self.info_label)
        self.map = MapWidget()
        layout.addWidget(self.map)

        self.range_label = QLabel("Nessuna attività caricata")
        layout.addWidget(self.range_label)
        self.range_slider = RangeSlider()
        self.range_slider.setEnabled(False)
        self.range_slider.valuesChanged.connect(self.update_trim)
        layout.addWidget(self.range_slider)

        self.color_mode.currentTextChanged.connect(self.update_scale)

    def _current_mode(self):
        if self.color_mode.count():
            return self.color_mode.currentText()
        return "Nessuna"

    def _parse_float(self, text):
        text = text.strip().replace(",", ".")
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    
    def _manual_scale_limits(self):
        minimum = self._parse_float(self.min_value.text())
        maximum = self._parse_float(self.max_value.text())
        if minimum is None or maximum is None or minimum >= maximum:
            return None
        return minimum, maximum

    def _current_scale_limits(self, visible_track):
        if self.scale_mode == "manual":
            if self.manual_scale_min is not None and self.manual_scale_max is not None:
                return self.manual_scale_min, self.manual_scale_max
            manual_limits = self._manual_scale_limits()
            if manual_limits is not None:
                self.manual_scale_min, self.manual_scale_max = manual_limits
                return manual_limits
            self.scale_mode = "auto"

        mode = self._current_mode()
        if mode == "Velocità":
            return calculate_speed_range(visible_track)
        if mode == "Pendenza":
            return calculate_slope_range(visible_track)
        return None, None

    def set_color_mode(self, mode: str):
        index = self.color_mode.findText(mode)
        if index >= 0 and self.color_mode.currentIndex() != index:
            self.color_mode.setCurrentIndex(index)

    def _visible_track(self):
        if not self.track:
            return None
        start_m = min(self.visible_start_m, self.visible_end_m)
        end_m = max(self.visible_start_m, self.visible_end_m)
        return trim_track_by_distance(self.track, start_m, end_m)

    def visible_speed_range(self):
        visible_track = self._visible_track()
        if not visible_track:
            return None, None
        return calculate_speed_range(visible_track)

    def current_scale_limits(self):
        if self.manual_scale_min is not None and self.manual_scale_max is not None:
            return self.manual_scale_min, self.manual_scale_max
        return self.visible_speed_range()

    def set_speed_scale_limits(self, minimum, maximum):
        if minimum is None or maximum is None or minimum >= maximum:
            return
        self.scale_mode = "manual"
        self.manual_scale_min = float(minimum)
        self.manual_scale_max = float(maximum)
        self.min_value.setText(f"{minimum:.1f}")
        self.max_value.setText(f"{maximum:.1f}")
        self._render_visible_track()

    def refresh_visible_track(self):
        self._render_visible_track()

    def _render_visible_track(self):
        if not self.track:
            return
        visible_track = self._visible_track()
        if not visible_track:
            return
        minimum, maximum = self._current_scale_limits(visible_track)
        if minimum is not None and maximum is not None:
            self.min_value.setText(f"{minimum:.1f}")
            self.max_value.setText(f"{maximum:.1f}")
        else:
            self.min_value.clear()
            self.max_value.clear()
        self.map.draw_track(visible_track, self._current_mode(), minimum, maximum)
        self.visible_track_changed.emit(visible_track)

    def _on_scale_limits_edited(self):
        manual_limits = self._manual_scale_limits()
        if manual_limits is None:
            self.scale_mode = "auto"
            self.manual_scale_min = None
            self.manual_scale_max = None
        else:
            self.scale_mode = "manual"
            self.manual_scale_min, self.manual_scale_max = manual_limits
        self._render_visible_track()

    def update_trim(self, start, end):
        if not self.track:
            return
        start_km = self.visible_start_m / 1000
        end_km = self.visible_end_m / 1000
        total_km = self.full_distance_m / 1000
        self.range_label.setText(f"Visualizzazione: {start_km:.2f} km → {end_km:.2f} km / {total_km:.2f} km")
        self.visible_start_m = float(min(start, end))
        self.visible_end_m = float(max(start, end))
        self._render_visible_track()

    def update_scale(self, *_):
        self._render_visible_track()

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
            self.file_label.setText(Path(filename).name)
            self.show_summary()
            self.color_mode.clear()
            self.color_mode.addItems(self.capabilities.available_modes)
            self.color_mode.setCurrentText("Velocità" if "Velocità" in self.capabilities.available_modes else self.capabilities.available_modes[0])
            _, self.full_distance_m = track_distance_profile(self.track)
            slider_max = max(1, int(round(self.full_distance_m)))
            self.range_slider.setEnabled(True)
            self.range_slider.setRange(0, slider_max)
            self.range_slider.setValues(0, slider_max)
            self.visible_start_m = 0
            self.visible_end_m = self.full_distance_m
            self.update_trim(0, slider_max)
            self._render_visible_track()
            self.activity_loaded.emit(self.track)
        except Exception as error:
            QMessageBox.critical(self, "Errore caricamento", str(error))
