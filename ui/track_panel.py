"""Activity panel used for importing, trimming and coloring one track.

Each side of the comparison UI owns one of these panels.

Called by:
    - ``ui.main_window.MainWindow``

Consumes:
    - ``core.gpx_loader.load_gpx``
    - ``core.fit_loader.load_fit``
    - ``core.analyzer`` helpers
    - ``core.track_capabilities.TrackCapabilities``
    - ``ui.range_slider.RangeSlider``
    - map renderer widgets
    - gestione modalità scala Automatica / Manuale
"""

from pathlib import Path
from enum import Enum

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

class ScaleMode(Enum):
    """
    Defines how the color scale is managed.

    AUTO
        The minimum and maximum values are computed automatically
        from the currently visible portion of the track.

    MANUAL
        The user explicitly provides minimum and maximum values.
    """

    AUTO = 0
    MANUAL = 1


class TrackPanel(QWidget):
    """UI and state for a single imported activity.

    Signals:
        activity_loaded: emitted when a new source file is parsed.
        visible_track_changed: emitted after trimming or scale changes.

    Created by:
        - ``MainWindow``
    """

    activity_loaded = pyqtSignal(object)
    visible_track_changed = pyqtSignal(object)
    manual_limits_changed = pyqtSignal(float, float)
    scale_mode_changed = pyqtSignal(object)

    def __init__(self, title):
        """Create the activity panel.

        Args:
            title: Logical title of the panel.
        """
        super().__init__()
        self.track = None
        self.capabilities = None
        self.full_distance_m = 0.0
        self.visible_start_m = 0.0
        self.visible_end_m = 0.0
        #
        # Current scale mode.
        #
        # AUTO
        #     Values are always calculated from the visible track.
        #
        # MANUAL
        #     User-defined values.
        #
        self.scale_mode = ScaleMode.AUTO

        #
        # Manual limits.
        #
        self.manual_scale_min = None
        self.manual_scale_max = None
        self.other_panel = None
        self.sync_scales_enabled = False

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
        self.color_mode.setEnabled(False)
        toolbar.addWidget(self.color_mode)
        self.min_value = QLineEdit()
        self.max_value = QLineEdit()
        self.min_value.setEnabled(False)
        self.max_value.setEnabled(False)
        self.min_value.editingFinished.connect(self._on_scale_limits_edited)
        self.max_value.editingFinished.connect(self._on_scale_limits_edited)
        toolbar.addWidget(self.min_value)
        toolbar.addWidget(self.max_value)
        
        self.scale_mode_button = QPushButton("Automatico")
        self.scale_mode_button.setEnabled(False)
        self.scale_mode_button.setCheckable(True)
        self.scale_mode_button.setChecked(False)
        self.scale_mode_button.setToolTip(
            "Automatico: calcola la scala dalla traccia.\n"
            "Manuale: permette l'inserimento dei valori."
        )

        self.scale_mode_button.toggled.connect(
            self._on_scale_mode_changed
        )
        toolbar.addWidget(self.scale_mode_button)

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
        """Return the current coloring mode text.

        Called by:
            - ``_current_scale_limits``
            - ``_render_visible_track``

        Returns:
            str: Current combo-box entry.
        """
        if self.color_mode.count():
            return self.color_mode.currentText()
        return "Nessuna"

    def _parse_float(self, text):
        """Parse a numeric field used for manual scale limits.

        Called by:
            - ``_manual_scale_limits``

        Args:
            text: Text entered by the user.

        Returns:
            float | None: Parsed value or ``None``.
        """
        text = text.strip().replace(",", ".")
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    
    def _manual_scale_limits(self):
        """Read manual min/max values from the scale fields.

        Called by:
            - ``_current_scale_limits``
            - ``_on_scale_limits_edited``

        Returns:
            tuple[float, float] | None: Valid manual limits or ``None``.
        """
        minimum = self._parse_float(self.min_value.text())
        maximum = self._parse_float(self.max_value.text())
        if minimum is None or maximum is None or minimum >= maximum:
            return None
        return minimum, maximum

    def _current_scale_limits(self, visible_track):
        """Compute the color scale limits for the current mode.

        Called by:
            - ``_render_visible_track``

        Args:
            visible_track: Currently trimmed track.

        Returns:
            tuple[float | None, float | None]: Scale limits.
        """
        if self.scale_mode == ScaleMode.MANUAL:
            if self.manual_scale_min is not None and self.manual_scale_max is not None:
                return self.manual_scale_min, self.manual_scale_max
            manual_limits = self._manual_scale_limits()
            if manual_limits is not None:
                self.manual_scale_min, self.manual_scale_max = manual_limits
                return manual_limits
            # If manual limits are requested but invalid, we don't automatically fall back to auto here,
            # as it might cause confusion with the UI button state.

        # ScaleMode.AUTO with scale synchronization enabled
        if self.sync_scales_enabled and self.other_panel and self.other_panel.track:
            other_visible = self.other_panel._visible_track()
            if other_visible:
                my_min, my_max = self.get_auto_limits_for_mode(self._current_mode(), visible_track)
                other_min, other_max = self.other_panel.get_auto_limits_for_mode(self._current_mode(), other_visible)
                
                minimum = None
                maximum = None
                if my_min is not None and other_min is not None:
                    minimum = min(my_min, other_min)
                elif my_min is not None:
                    minimum = my_min
                elif other_min is not None:
                    minimum = other_min

                if my_max is not None and other_max is not None:
                    maximum = max(my_max, other_max)
                elif my_max is not None:
                    maximum = my_max
                elif other_max is not None:
                    maximum = other_max
                
                return minimum, maximum

        return self.get_auto_limits_for_mode(self._current_mode(), visible_track)

    def get_auto_limits_for_mode(self, mode, visible_track=None):
        """Compute automatic limits for a specific mode.

        Args:
            mode: Color mode string.
            visible_track: Track to analyze (defaults to current visible track).

        Returns:
            tuple[float | None, float | None]: Scale limits.
        """
        if visible_track is None:
            visible_track = self._visible_track()
        if not visible_track:
            return None, None

        if mode == "Velocità":
            return calculate_speed_range(visible_track)
        if mode == "Pendenza":
            return calculate_slope_range(visible_track)
        return None, None

    def set_color_mode(self, mode: str):
        """Select a coloring mode programmatically.

        Called by:
            - ``MainWindow._on_sync_scales_toggled``
            - ``MainWindow._on_color_mode_changed``

        Args:
            mode: Combobox label to select.
        """
        index = self.color_mode.findText(mode)
        if index >= 0 and self.color_mode.currentIndex() != index:
            self.color_mode.setCurrentIndex(index)

    def _visible_track(self):
        """Return the track trimmed to the current slider selection.

        Called by:
            - ``_render_visible_track``
            - ``get_auto_limits_for_mode``

        Returns:
            Track | None: Trimmed track or ``None`` when nothing is loaded.
        """
        if not self.track:
            return None
        start_m = min(self.visible_start_m, self.visible_end_m)
        end_m = max(self.visible_start_m, self.visible_end_m)
        return trim_track_by_distance(self.track, start_m, end_m)

    def current_scale_limits(self):
        """Return the active scale range for the current mode.

        Returns:
            tuple[float | None, float | None]: Manual or automatic limits.
        """
        return self._current_scale_limits(self._visible_track())

    def apply_scale_mode(self, mode: "ScaleMode", manual_min=None, manual_max=None):
        """Programmatically switch scale mode without re-entrant signals.

        Called by:
            - ``MainWindow`` synchronization handlers (``_on_scale_mode_changed``,
              ``_on_manual_limits_changed``, ``_on_sync_scales_toggled``)

        Args:
            mode: Target ``ScaleMode``.
            manual_min: Optional manual lower bound (used when ``mode`` is MANUAL).
            manual_max: Optional manual upper bound (used when ``mode`` is MANUAL).

        Side effects:
            Updates the button state with signals blocked (so this call never
            triggers ``_on_scale_mode_changed`` recursively), then applies the
            same state transition used for user-driven toggles.
        """
        self.scale_mode_button.blockSignals(True)
        try:
            self.scale_mode_button.setChecked(mode == ScaleMode.MANUAL)
        finally:
            self.scale_mode_button.blockSignals(False)
        self._apply_mode_state(mode, manual_min, manual_max)

    def set_manual_scale_limits(self, minimum: float, maximum: float):
        """Apply a manual scale to this panel and update UI.

        Called by:
            - external callers wanting to force manual limits on this panel

        Args:
            minimum: Lower bound.
            maximum: Upper bound.
        """
        if minimum is None or maximum is None or minimum >= maximum:
            return
        self.apply_scale_mode(ScaleMode.MANUAL, minimum, maximum)

    def refresh_visible_track(self):
        """Force a redraw of the visible portion of the track.

        Called by:
            - ``MainWindow._center_traces``
        """
        self._render_visible_track()

    def _render_visible_track(self):
        """Render the currently visible track portion on the map.

        Called by:
            - trim slider updates
            - color-mode changes
            - manual scale edits
            - track import
            - ``refresh_visible_track``

        Side effects:
            Updates the map, scale fields and emits ``visible_track_changed``.
        """
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
        """Handle manual scale field edits.

        Called by:
            - ``editingFinished`` of the min/max fields

        Side effects:
            If not already in Manual mode, switches to it (without re-entrant
            signal cascades) and notifies listeners via ``scale_mode_changed``.
            If already Manual, re-renders and notifies listeners via
            ``manual_limits_changed`` so synchronized panels can follow.
        """
        manual_limits = self._manual_scale_limits()
        if manual_limits is None:
            return

        was_manual = self.scale_mode_button.isChecked()
        self.manual_scale_min, self.manual_scale_max = manual_limits

        if not was_manual:
            self.apply_scale_mode(ScaleMode.MANUAL, *manual_limits)
            self.scale_mode_changed.emit(self.scale_mode)
        else:
            self._render_visible_track()
            self.manual_limits_changed.emit(*manual_limits)

    def update_trim(self, start, end):
        """Apply the slider interval and refresh the rendered track.

        Called by:
            - ``RangeSlider.valuesChanged``

        Args:
            start: Lower distance bound.
            end: Upper distance bound.
        """
        if not self.track:
            return
        self.visible_start_m = float(min(start, end))
        self.visible_end_m = float(max(start, end))
        start_km = self.visible_start_m / 1000
        end_km = self.visible_end_m / 1000
        total_km = self.full_distance_m / 1000
        self.range_label.setText(f"Visualizzazione: {start_km:.2f} km → {end_km:.2f} km / {total_km:.2f} km")
        self._render_visible_track()

    def update_scale(self, *_):
        """Refresh the current rendering after a mode change.

        Called by:
            - ``color_mode.currentTextChanged``
        """
        self._render_visible_track()

    def show_summary(self):
        """Render the capability summary in the info label.

        Called by:
            - ``import_file`` after loading a new track
        """
        summary = self.capabilities.summary
        self.info_label.setText(" | ".join(f"{k}: {'✓' if v is True else v}" for k, v in summary.items()))

    def _apply_mode_state(self, mode: "ScaleMode", manual_min=None, manual_max=None):
        """Apply the internal state, field editability and caption for a mode.

        Called by:
            - ``_on_scale_mode_changed`` (user toggled the button)
            - ``apply_scale_mode`` (programmatic / synchronized change)

        Args:
            mode: Target ``ScaleMode``.
            manual_min: Optional manual lower bound to apply immediately.
            manual_max: Optional manual upper bound to apply immediately.

        Side effects:
            Always enables/disables both min and max fields together, so the
            two fields can never end up in an inconsistent state.
        """
        if mode == ScaleMode.MANUAL:
            self.scale_mode = ScaleMode.MANUAL
            self.scale_mode_button.setText("Manuale")
            self.min_value.setEnabled(True)
            self.max_value.setEnabled(True)
            if manual_min is not None and manual_max is not None:
                self.manual_scale_min = float(manual_min)
                self.manual_scale_max = float(manual_max)
                self.min_value.setText(f"{manual_min:.1f}")
                self.max_value.setText(f"{manual_max:.1f}")
            elif self.manual_scale_min is None:
                # Preserve whatever the fields already show (e.g. values just
                # computed automatically) instead of leaving them blank.
                limits = self._manual_scale_limits()
                if limits:
                    self.manual_scale_min, self.manual_scale_max = limits
        else:
            self.scale_mode = ScaleMode.AUTO
            self.scale_mode_button.setText("Automatico")
            self.min_value.setEnabled(False)
            self.max_value.setEnabled(False)
            if manual_min is not None and manual_max is not None:
                self.min_value.setText(f"{manual_min:.1f}")
                self.max_value.setText(f"{manual_max:.1f}")
            else:
                # When switching to AUTO, populate min/max fields with current auto limits
                visible_track = self._visible_track()
                if visible_track:
                    auto_min, auto_max = self.get_auto_limits_for_mode(self._current_mode(), visible_track)
                    if auto_min is not None and auto_max is not None:
                        self.min_value.setText(f"{auto_min:.1f}")
                        self.max_value.setText(f"{auto_max:.1f}")
            self.manual_scale_min = None
            self.manual_scale_max = None

        self._render_visible_track()

    def _on_scale_mode_changed(self, checked: bool):
        """
        Handle Automatic / Manual scale mode toggled by the user.

        Called by:
            - ``scale_mode_button.toggled``

        Side effects:
            - enables/disables scale edits
            - changes button caption
            - triggers a re-render
            - emits ``scale_mode_changed`` for ``MainWindow`` synchronization
        """
        mode = ScaleMode.MANUAL if checked else ScaleMode.AUTO
        self._apply_mode_state(mode)
        self.scale_mode_changed.emit(self.scale_mode)

    def import_file(self):
        """Open a FIT/GPX file and load it into the panel.

        Called by:
            - import button click

        Side effects:
            - loads a new track
            - updates the map mode dropdown
            - enables the range slider
            - emits ``activity_loaded`` and ``visible_track_changed``
        """
        filename, _ = QFileDialog.getOpenFileName(self, "Seleziona attività", "", "Attività GPS (*.fit *.gpx)")
        if not filename:
            return
        try:
            ext = Path(filename).suffix.lower()
            self.track = load_gpx(filename) if ext == ".gpx" else load_fit(filename)
            self.capabilities = TrackCapabilities(self.track)
            self.file_label.setText(Path(filename).name)
            self.show_summary()
            self.color_mode.setEnabled(True)
            self.scale_mode_button.setEnabled(True)
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
