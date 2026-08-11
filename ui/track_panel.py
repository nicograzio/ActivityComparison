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

from PyQt6.QtCore import pyqtSignal, Qt, QSize, QEvent, QUrl, QObject
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFileDialog, QMessageBox, QComboBox, QLineEdit, QFrame, QGraphicsColorizeEffect, QToolTip, QSizePolicy
from PyQt6.QtGui import QPixmap, QColor
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply

from ui.map_widget import MapWidget

from ui.range_slider import RangeSlider
from core.gpx_loader import load_gpx
from core.fit_loader import load_fit
from core.weather_loader import (
    pick_datapoints,
    as_utc,
    build_weather_url,
    parse_weather_response,
)
from core.analyzer import (
    calculate_speed_range,
    calculate_slope_range,
    track_distance_profile,
    trim_track_by_distance,
)
from core.track_capabilities import TrackCapabilities

def _format_time_duration(seconds):
    """Format a duration in seconds to HH:MM:SS, MM:SS, or SS format.
    
    Args:
        seconds: Duration in seconds (int or float).
    
    Returns:
        str: Formatted time string.
    """
    if seconds is None:
        return "0"
    
    total_seconds = int(round(seconds))
    
    if total_seconds < 60:
        # Less than 1 minute: SS format
        return f"{total_seconds}s"
    elif total_seconds < 3600:
        # Less than 1 hour: MM:SS format
        minutes = total_seconds // 60
        secs = total_seconds % 60
        return f"{minutes}:{secs:02d}"
    else:
        # 1 hour or more: HH:MM:SS format
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        secs = total_seconds % 60
        return f"{hours}:{minutes:02d}:{secs:02d}"

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
    other_panel: "TrackPanel | None"

    def __init__(self, title):
        """Create the activity panel.

        Args:
            title: Logical title of the panel.
        """
        super().__init__()
        self.title = title
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
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

        # Stato per il recupero meteo asincrono via QNetworkAccessManager.
        self.weather_nam = QNetworkAccessManager(self)
        self.weather_token = 0
        self.weather_active = False
        self.weather_outstanding = 0
        self.weather_requests = []
        self.weather_start_result = None
        self.weather_end_result = None


        # Pre-create icon labels to avoid adding them to layout multiple times
        self.icon_labels = {
            "gps": QLabel(),
            "heart_rate": QLabel(),
            "elevation": QLabel(),
            "speed": QLabel(),
            "weather": QLabel(),
            "info": QLabel()
        }

        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # First toolbar: Import and Summary
        top_toolbar = QHBoxLayout()
        self.import_button = QPushButton("Importa FIT / GPX")
        self.import_button.clicked.connect(self.import_file)
        top_toolbar.addWidget(self.import_button)

        self.file_label = QLabel("Nessun attività caricata")
        self.file_label.setStyleSheet("font-weight: bold;")
        top_toolbar.addWidget(self.file_label)

        # Summary icons container
        self.summary_container = QWidget()
        self.summary_layout = QHBoxLayout(self.summary_container)
        self.summary_layout.setContentsMargins(5, 0, 5, 0)
        self.summary_layout.setSpacing(10)
        
        for key, label in self.icon_labels.items():
            if key == "info":
                continue
            self.summary_layout.addWidget(label)
            label.hide() # Hidden until track loaded
            label.installEventFilter(self)
            label.setMouseTracking(True)
        
        self.info_button = self.icon_labels["info"]
        self.info_button.hide()
        self.info_button.installEventFilter(self)
        self.info_button.setMouseTracking(True)

        top_toolbar.addWidget(self.summary_container)
        top_toolbar.addStretch()
        top_toolbar.addWidget(self.info_button)
        layout.addLayout(top_toolbar)

        pixmap = QPixmap("assets/icons/info.png")
        if not pixmap.isNull():
            self.info_button.setPixmap(pixmap.scaled(24, 24, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            self.info_button.setText("ℹ")
        # (Rimosso blocco precedente che applicava l'effetto colore qui, ora gestito in show_summary)
        self.info_button.setToolTip("Visualizza informazioni dettagliate dell'attività")


        # Second toolbar: Scale controls
        scale_toolbar = QHBoxLayout()
        scale_toolbar.addWidget(QLabel("Colora per:"))
        self.color_mode = QComboBox()
        self.color_mode.setEnabled(False)
        self.color_mode.currentTextChanged.connect(self.update_scale)
        scale_toolbar.addWidget(self.color_mode)

        scale_toolbar.addStretch()
        scale_toolbar.addWidget(QLabel("Min:"))
        self.min_value = QLineEdit()
        self.max_value = QLineEdit()
        self.min_value.setFixedWidth(60)
        self.max_value.setFixedWidth(60)
        self.min_value.setEnabled(False)
        self.max_value.setEnabled(False)
        self.min_value.editingFinished.connect(self._on_scale_limits_edited)
        self.max_value.editingFinished.connect(self._on_scale_limits_edited)
        scale_toolbar.addWidget(self.min_value)
        scale_toolbar.addWidget(QLabel("Max:"))
        scale_toolbar.addWidget(self.max_value)
        
        self.scale_mode_button = QPushButton("Automatico")
        self.scale_mode_button.setEnabled(False)
        self.scale_mode_button.setCheckable(True)
        self.scale_mode_button.setChecked(False)
        self.scale_mode_button.setToolTip(
            "Automatico: calcola la scala dalla traccia.\n"
            "Manuale: permette l'inserimento dei valori."
        )
        self.scale_mode_button.toggled.connect(self._on_scale_mode_changed)
        scale_toolbar.addWidget(self.scale_mode_button)

        layout.addLayout(scale_toolbar)

        self.map = MapWidget()
        self.map.setMinimumHeight(400)
        layout.addWidget(self.map, stretch=1)

        range_layout = QHBoxLayout()
        self.range_label = QLabel("Nessuna attività caricata")
        self.range_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.range_label.setMinimumWidth(280)
        range_layout.addWidget(self.range_label)
        range_layout.addStretch()

        layout.addLayout(range_layout)
        
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

    def _current_scale_limits(self, visible_track) -> tuple[float | None, float | None]:
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
            if other_visible is not None:
                my_limits = self.get_auto_limits_for_mode(self._current_mode(), visible_track)
                other_limits = self.other_panel.get_auto_limits_for_mode(self._current_mode(), other_visible)
                if my_limits is None or other_limits is None:
                    return None, None
                my_min, my_max = my_limits
                other_min, other_max = other_limits # type: ignore
                
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

    def get_auto_limits_for_mode(self, mode, visible_track=None) -> tuple[float | None, float | None]:
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

         distances, _ = track_distance_profile(self.track)
         start_point = 0
         for i, distance in enumerate(distances):
             if distance >= self.visible_start_m:
                 start_point = i if distance == self.visible_start_m or i == 0 else i - 1
                 break

         end_point = len(distances) - 1
         for i, distance in enumerate(distances):
             if distance > self.visible_end_m:
                 end_point = max(0, i - 1)
                 break

         # Calculate time values
         time_start_str = "0"
         time_end_str = "0"
         time_total_str = "0"
         
         if self.track.points:
             # Get first and last timestamps from the full track
             first_timestamp = self.track.points[0].timestamp
             last_timestamp = self.track.points[-1].timestamp
             
             # Get timestamps at the visible range boundaries
             start_timestamp = None
             end_timestamp = None
             
             if start_point < len(self.track.points):
                 start_timestamp = self.track.points[start_point].timestamp
             if end_point < len(self.track.points):
                 end_timestamp = self.track.points[end_point].timestamp
             
             # Calculate total duration
             if first_timestamp is not None and last_timestamp is not None:
                 try:
                     total_seconds = (last_timestamp - first_timestamp).total_seconds()
                     time_total_str = _format_time_duration(total_seconds)
                 except Exception:
                     time_total_str = "0"
             
             # Calculate visible range start time
             if first_timestamp is not None and start_timestamp is not None:
                 try:
                     start_seconds = (start_timestamp - first_timestamp).total_seconds()
                     time_start_str = _format_time_duration(start_seconds)
                 except Exception:
                     time_start_str = "0"
             
             # Calculate visible range end time
             if first_timestamp is not None and end_timestamp is not None:
                 try:
                     end_seconds = (end_timestamp - first_timestamp).total_seconds()
                     time_end_str = _format_time_duration(end_seconds)
                 except Exception:
                     time_end_str = "0"

         self.range_label.setText(
             f"Visualizzazione: {start_km:.2f} km → {end_km:.2f} km / {total_km:.2f} km | "
             f"{time_start_str} → {time_end_str} / {time_total_str} | "
             f"da punto {start_point + 1} a punto {end_point + 1}"
         )
         self._render_visible_track()

    def update_scale(self, *_):
        """Refresh the current rendering after a mode change.

        Called by:
            - ``color_mode.currentTextChanged``
        """
        self._render_visible_track()

    def _set_icon(self, key, icon_name, available, tooltip_title, tooltip_lines, color=None):
        """Helper to set up an icon with color effect and rich text tooltip.

        Args:
            key: Chiave dell'etichetta in ``self.icon_labels``.
            icon_name: Nome del file PNG in ``assets/icons``.
            available: Se True il colore predefinito è verde, altrimenti rosso.
            tooltip_title: Titolo in grassetto del tooltip.
            tooltip_lines: Righe ``(etichetta, valore)`` della tabella del tooltip.
            color: ``QColor`` opzionale che sovrascrive il colore predefinito
                (usato ad es. per lo stato "recupero meteo in corso").
        """
        label = self.icon_labels[key]
        pixmap = QPixmap(f"assets/icons/{icon_name}.png")
        if not pixmap.isNull():
            label.setPixmap(pixmap.scaled(24, 24, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            label.setText("?")

        # Apply color effect
        effect = QGraphicsColorizeEffect(label)
        if color is None:
            color = QColor("green" if available else "red")
        effect.setColor(color)
        label.setGraphicsEffect(effect)

        # Build rich text tooltip
        rows = "".join(f"<tr><td style='padding-right: 10px;'>{line[0]}</td><td>{line[1]}</td></tr>" for line in tooltip_lines)
        tooltip_html = (
            f"<div style='font-family: sans-serif;'>"
            f"<b>{tooltip_title}</b>"
            f"<table style='margin-top: 5px; border-collapse: collapse;'>"
            f"{rows}"
            f"</table>"
            f"</div>"
        )
        label.setToolTip(tooltip_html)
        label.show()

    def show_summary(self):
        """Render the capability summary in the top toolbar with icons and tooltips.

        Called by:
            - ``import_file`` after loading a new track
        """
        assert self.capabilities is not None
        summary = self.capabilities.summary
        stats = self.capabilities.stats

        # GPS
        gps_available = summary["gps"]
        gps_lines = [("Stato:", "Presente" if gps_available else "Assente"),
                     ("Punti:", str(summary['points']))]
        self._set_icon("gps", "gps", gps_available, "Punti GPS", gps_lines)

        # Heart Rate
        hr_available = summary["heart_rate"]
        hr_lines = []
        if hr_available:
            s = stats["heart_rate"]
            hr_lines = [("Min:", f"{s['min']:.0f} bpm"),
                        ("Max:", f"{s['max']:.0f} bpm"),
                        ("Media:", f"{s['avg']:.0f} bpm")]
        else:
            hr_lines = [("Stato:", "Assente")]
        self._set_icon("heart_rate", "heart-rate", hr_available, "Frequenza Cardiaca", hr_lines)

        # Elevation
        elev_available = summary["elevation"]
        elev_lines = []
        if elev_available:
            e = stats["elevation"]
            sl = stats["slope"]
            elev_lines = [("Alt. Min:", f"{e['min']:.1f} m"),
                          ("Alt. Max:", f"{e['max']:.1f} m"),
                          ("Pend. Min:", f"{sl['min']:.1f} %"),
                          ("Pend. Max:", f"{sl['max']:.1f} %")]
        else:
            elev_lines = [("Stato:", "Assente")]
        self._set_icon("elevation", "elevation", elev_available, "Altitudine e Pendenza", elev_lines)

        # Speed
        speed_available = summary["speed"]
        speed_lines = []
        if speed_available:
            s = stats["speed"]
            speed_lines = [("Min:", f"{s['min']:.1f} km/h"),
                           ("Max:", f"{s['max']:.1f} km/h"),
                           ("Media:", f"{s['avg']:.1f} km/h")]
        else:
            speed_lines = [("Stato:", "Assente")]
        self._set_icon("speed", "speed", speed_available, "Velocità", speed_lines)

        # Weather
        weather_available = summary["weather"]
        weather_lines = self._weather_lines()
        self._set_icon("weather", "weather", weather_available, "Condizioni Meteo", weather_lines)

        # Info
        # Determina il colore basato sullo sfondo
        bg_color = self.palette().color(self.backgroundRole())
        is_dark_bg = bg_color.lightness() < 128
        info_color = QColor("white" if is_dark_bg else "black")
        
        self._set_icon("info", "info", True, "Informazioni", [], color=info_color)

    def _weather_lines(self):
        """Costruisce le righe del tooltip meteo mostrando inizio e fine attività."""
        lines = []
        if self.track is None or (self.track.weather_start is None and self.track.weather_end is None):
            return [("Stato:", "Assente")]

        def info_block(label, weather):
            if weather is None:
                return [(label, "Non disponibile")]
            block = [(label, weather.condition or "Non disponibile")]
            if weather.temperature is not None:
                block.append(("Temperatura:", f"{weather.temperature:.0f}°C"))
            if weather.wind_speed is not None:
                block.append(("Vento:", f"{weather.wind_speed:.1f} m/s"))
            if weather.humidity is not None:
                block.append(("Umidità:", f"{weather.humidity}%"))
            return block

        lines.extend(info_block("Inizio:", self.track.weather_start))
        lines.extend(info_block("Fine:", self.track.weather_end))
        return lines

    def _start_weather_fetch(self):
        """Avvia (in modo asincrono) il recupero del meteo esterno.

        Usa ``QNetworkAccessManager`` (networking Qt integrato) invece di
        thread + socket bloccanti, che interferivano con la ``QWebEngineView``
        della mappa. Qualsiasi fetch precedente non ancora terminato viene
        annullato (gestione sicura di import multipli).
        """
        if self.track is None:
            return

        start_pt, end_pt = pick_datapoints(self.track)
        if start_pt is None and end_pt is None:
            # Nessun punto con timestamp+coordinate: niente da interpellare.
            self._cancel_weather_fetch()
            self._update_weather_icon()
            return

        # Annulla eventuali richieste in corso e avvia un nuovo "ciclo".
        self.weather_token += 1
        self._abort_weather_requests()
        self.weather_active = True
        self.weather_outstanding = 0
        self.weather_start_result = None
        self.weather_end_result = None
        self._set_weather_pending()

        requested = 0
        if start_pt is not None:
            dt = as_utc(start_pt.timestamp)
            if dt is not None:
                self._weather_request(start_pt.latitude, start_pt.longitude, dt, is_start=True)
                requested += 1
        if end_pt is not None and end_pt is not start_pt:
            dt = as_utc(end_pt.timestamp)
            if dt is not None:
                self._weather_request(end_pt.latitude, end_pt.longitude, dt, is_start=False)
                requested += 1

        if requested == 0:
            self.weather_active = False
            self._update_weather_icon()

    def _show_track_info(self):
        """Show a dialog with detailed track information."""
        if not self.track:
            return
        
        from PyQt6.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
            QTableWidget, QTableWidgetItem, QHeaderView, QSpinBox, QLabel
        )
        from core.analyzer import calculate_point_speed, calculate_slope_range, track_distance_profile

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Informazioni: {self.title}")
        dlg.resize(700, 600)
        layout = QVBoxLayout(dlg)

        tabs = QTabWidget()
        layout.addWidget(tabs)

        # ============================================================
        # TAB 1: Informazioni Traccia (track-level)
        # ============================================================
        tab_track = QWidget()
        track_layout = QVBoxLayout(tab_track)

        track_table = QTableWidget(0, 2)
        track_table.setHorizontalHeaderLabels(["Metrica", "Valore"])
        header = track_table.horizontalHeader()
        assert header is not None
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setStretchLastSection(True)
        track_layout.addWidget(track_table)

        def add_track_row(key, val):
            row = track_table.rowCount()
            track_table.insertRow(row)
            item_key = QTableWidgetItem(key)
            item_key.setFlags(item_key.flags() & ~Qt.ItemFlag.ItemIsEditable)
            track_table.setItem(row, 0, item_key)
            item_val = QTableWidgetItem(val)
            item_val.setFlags(item_val.flags() & ~Qt.ItemFlag.ItemIsEditable)
            track_table.setItem(row, 1, item_val)

        # File info
        add_track_row("Nome File", self.track.name.split('/')[-1])
        add_track_row("Percorso File", self.track.name)
        add_track_row("N. Punti Totali", str(len(self.track.points)))

        # Durata
        if self.track.points and self.track.points[0].timestamp and self.track.points[-1].timestamp:
            try:
                total_seconds = (self.track.points[-1].timestamp - self.track.points[0].timestamp).total_seconds()
                hours = int(total_seconds // 3600)
                minutes = int((total_seconds % 3600) // 60)
                seconds = int(total_seconds % 60)
                add_track_row("Durata Totale", f"{hours:02d}:{minutes:02d}:{seconds:02d}")
            except Exception:
                pass

        # Distanza
        _, total_distance = track_distance_profile(self.track)
        add_track_row("Distanza Totale (m)", f"{total_distance:.2f}")
        add_track_row("Distanza Totale (km)", f"{total_distance/1000:.3f}")

        # Timestamp inizio/fine
        if self.track.points:
            first_ts = self.track.points[0].timestamp
            last_ts = self.track.points[-1].timestamp
            add_track_row("Timestamp Inizio", str(first_ts) if first_ts else "N/A")
            add_track_row("Timestamp Fine", str(last_ts) if last_ts else "N/A")

        # Statistiche da capabilities
        assert self.capabilities is not None
        stats = self.capabilities.stats

        # Altitudine
        if self.capabilities.summary["elevation"]:
            s = stats["elevation"]
            add_track_row("Altitudine Min (m)", f"{s['min']:.1f}")
            add_track_row("Altitudine Max (m)", f"{s['max']:.1f}")
            if s['min'] is not None and s['max'] is not None:
                add_track_row("Escursione Altimetrica (m)", f"{s['max'] - s['min']:.1f}")

        # Pendenza
        if self.capabilities.summary["elevation"]:
            sl = stats["slope"]
            add_track_row("Pendenza Min (%)", f"{sl['min']:.2f}")
            add_track_row("Pendenza Max (%)", f"{sl['max']:.2f}")

        # Velocità
        if self.capabilities.summary["speed"]:
            s = stats["speed"]
            add_track_row("Velocità Min (km/h)", f"{s['min']:.2f}")
            add_track_row("Velocità Max (km/h)", f"{s['max']:.2f}")
            add_track_row("Velocità Media (km/h)", f"{s['avg']:.2f}")

        # Frequenza Cardiaca
        if self.capabilities.summary["heart_rate"]:
            s = stats["heart_rate"]
            add_track_row("FC Min (bpm)", f"{s['min']:.0f}")
            add_track_row("FC Max (bpm)", f"{s['max']:.0f}")
            add_track_row("FC Media (bpm)", f"{s['avg']:.0f}")

        # Meteo
        if self.track.weather_start or self.track.weather_end:
            ws = self.track.weather_start
            we = self.track.weather_end
            if ws:
                add_track_row("Meteo Inizio - Condizione", ws.condition or "N/A")
                add_track_row("Meteo Inizio - Temperatura", f"{ws.temperature:.1f}°C" if ws.temperature is not None else "N/A")
                add_track_row("Meteo Inizio - Vento", f"{ws.wind_speed:.1f} m/s" if ws.wind_speed is not None else "N/A")
                add_track_row("Meteo Inizio - Umidità", f"{ws.humidity}%" if ws.humidity is not None else "N/A")
            if we:
                add_track_row("Meteo Fine - Condizione", we.condition or "N/A")
                add_track_row("Meteo Fine - Temperatura", f"{we.temperature:.1f}°C" if we.temperature is not None else "N/A")
                add_track_row("Meteo Fine - Vento", f"{we.wind_speed:.1f} m/s" if we.wind_speed is not None else "N/A")
                add_track_row("Meteo Fine - Umidità", f"{we.humidity}%" if we.humidity is not None else "N/A")

        # Metadati FIT
        if hasattr(self.track, 'sessions') and self.track.sessions:
            add_track_row("N. Sessioni FIT", str(len(self.track.sessions)))
        if hasattr(self.track, 'laps') and self.track.laps:
            add_track_row("N. Lap FIT", str(len(self.track.laps)))
        if hasattr(self.track, 'device_infos') and self.track.device_infos:
            add_track_row("N. Device Info", str(len(self.track.device_infos)))

        track_layout.addWidget(track_table)
        tabs.addTab(tab_track, "Dati Traccia")

        # ============================================================
        # TAB 2: Informazioni Punto (point-level)
        # ============================================================
        tab_point = QWidget()
        point_layout = QVBoxLayout(tab_point)

        selector_layout = QHBoxLayout()
        selector_layout.addWidget(QLabel("Seleziona Punto:"))
        point_spin = QSpinBox()
        point_spin.setRange(1, max(1, len(self.track.points)))
        point_spin.setValue(1)
        selector_layout.addWidget(point_spin)
        selector_layout.addStretch()
        point_layout.addLayout(selector_layout)

        point_table = QTableWidget(0, 2)
        point_table.setHorizontalHeaderLabels(["Campo", "Valore"])
        header = point_table.horizontalHeader()
        assert header is not None
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setStretchLastSection(True)
        point_layout.addWidget(point_table)

        def format_value(val):
            if val is None:
                return "N/A"
            if isinstance(val, float):
                return f"{val:.4f}"
            return str(val)

        def update_point_info(index):
            point_table.setRowCount(0)
            assert self.track is not None and self.track.points is not None
            if not self.track.points or index < 1 or index > len(self.track.points):
                return

            pt = self.track.points[index - 1]

            # Coordinate
            point_table.insertRow(point_table.rowCount())
            item_idx = QTableWidgetItem("Indice Punto")
            item_idx.setFlags(item_idx.flags() & ~Qt.ItemFlag.ItemIsEditable)
            point_table.setItem(point_table.rowCount()-1, 0, item_idx)
            item_val = QTableWidgetItem(str(index))
            item_val.setFlags(item_val.flags() & ~Qt.ItemFlag.ItemIsEditable)
            point_table.setItem(point_table.rowCount()-1, 1, item_val)

            point_table.insertRow(point_table.rowCount())
            item_lat = QTableWidgetItem("Latitudine")
            item_lat.setFlags(item_lat.flags() & ~Qt.ItemFlag.ItemIsEditable)
            point_table.setItem(point_table.rowCount()-1, 0, item_lat)
            item_val = QTableWidgetItem(format_value(pt.latitude))
            item_val.setFlags(item_val.flags() & ~Qt.ItemFlag.ItemIsEditable)
            point_table.setItem(point_table.rowCount()-1, 1, item_val)

            point_table.insertRow(point_table.rowCount())
            item_lon = QTableWidgetItem("Longitudine")
            item_lon.setFlags(item_lon.flags() & ~Qt.ItemFlag.ItemIsEditable)
            point_table.setItem(point_table.rowCount()-1, 0, item_lon)
            item_val = QTableWidgetItem(format_value(pt.longitude))
            item_val.setFlags(item_val.flags() & ~Qt.ItemFlag.ItemIsEditable)
            point_table.setItem(point_table.rowCount()-1, 1, item_val)

            # Altitudine
            point_table.insertRow(point_table.rowCount())
            item_alt = QTableWidgetItem("Altitudine (m)")
            item_alt.setFlags(item_alt.flags() & ~Qt.ItemFlag.ItemIsEditable)
            point_table.setItem(point_table.rowCount()-1, 0, item_alt)
            item_val = QTableWidgetItem(format_value(pt.altitude))
            item_val.setFlags(item_val.flags() & ~Qt.ItemFlag.ItemIsEditable)
            point_table.setItem(point_table.rowCount()-1, 1, item_val)

            # Timestamp
            point_table.insertRow(point_table.rowCount())
            item_ts = QTableWidgetItem("Timestamp")
            item_ts.setFlags(item_ts.flags() & ~Qt.ItemFlag.ItemIsEditable)
            point_table.setItem(point_table.rowCount()-1, 0, item_ts)
            item_val = QTableWidgetItem(str(pt.timestamp) if pt.timestamp else "N/A")
            item_val.setFlags(item_val.flags() & ~Qt.ItemFlag.ItemIsEditable)
            point_table.setItem(point_table.rowCount()-1, 1, item_val)

            # Distanza cumulativa
            if pt.distance is not None:
                point_table.insertRow(point_table.rowCount())
                item_dist = QTableWidgetItem("Distanza (m)")
                item_dist.setFlags(item_dist.flags() & ~Qt.ItemFlag.ItemIsEditable)
                point_table.setItem(point_table.rowCount()-1, 0, item_dist)
                item_val = QTableWidgetItem(f"{pt.distance:.2f}")
                item_val.setFlags(item_val.flags() & ~Qt.ItemFlag.ItemIsEditable)
                point_table.setItem(point_table.rowCount()-1, 1, item_val)

            # Velocità raw
            if pt.speed is not None:
                point_table.insertRow(point_table.rowCount())
                item_spd = QTableWidgetItem("Velocità Raw (m/s)")
                item_spd.setFlags(item_spd.flags() & ~Qt.ItemFlag.ItemIsEditable)
                point_table.setItem(point_table.rowCount()-1, 0, item_spd)
                item_val = QTableWidgetItem(f"{pt.speed:.2f}")
                item_val.setFlags(item_val.flags() & ~Qt.ItemFlag.ItemIsEditable)
                point_table.setItem(point_table.rowCount()-1, 1, item_val)

            # Velocità calcolata (distanza/tempo)
            if index > 1:
                assert self.track is not None and self.track.points is not None
                prev_pt = self.track.points[index - 2]
                calc_speed = calculate_point_speed(prev_pt, pt)
                if calc_speed is not None:
                    point_table.insertRow(point_table.rowCount())
                    item_calc = QTableWidgetItem("Velocità Calcolata (km/h)")
                    item_calc.setFlags(item_calc.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    point_table.setItem(point_table.rowCount()-1, 0, item_calc)
                    item_val = QTableWidgetItem(f"{calc_speed:.2f}")
                    item_val.setFlags(item_val.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    point_table.setItem(point_table.rowCount()-1, 1, item_val)

            # Frequenza Cardiaca
            if pt.heart_rate is not None:
                point_table.insertRow(point_table.rowCount())
                item_hr = QTableWidgetItem("Frequenza Cardiaca (bpm)")
                item_hr.setFlags(item_hr.flags() & ~Qt.ItemFlag.ItemIsEditable)
                point_table.setItem(point_table.rowCount()-1, 0, item_hr)
                item_val = QTableWidgetItem(str(pt.heart_rate))
                item_val.setFlags(item_val.flags() & ~Qt.ItemFlag.ItemIsEditable)
                point_table.setItem(point_table.rowCount()-1, 1, item_val)

            # Cadenza
            if pt.cadence is not None:
                point_table.insertRow(point_table.rowCount())
                item_cad = QTableWidgetItem("Cadenza (rpm)")
                item_cad.setFlags(item_cad.flags() & ~Qt.ItemFlag.ItemIsEditable)
                point_table.setItem(point_table.rowCount()-1, 0, item_cad)
                item_val = QTableWidgetItem(str(pt.cadence))
                item_val.setFlags(item_val.flags() & ~Qt.ItemFlag.ItemIsEditable)
                point_table.setItem(point_table.rowCount()-1, 1, item_val)

            # Temperatura
            if pt.temperature is not None:
                point_table.insertRow(point_table.rowCount())
                item_temp = QTableWidgetItem("Temperatura (°C)")
                item_temp.setFlags(item_temp.flags() & ~Qt.ItemFlag.ItemIsEditable)
                point_table.setItem(point_table.rowCount()-1, 0, item_temp)
                item_val = QTableWidgetItem(f"{pt.temperature:.1f}")
                item_val.setFlags(item_val.flags() & ~Qt.ItemFlag.ItemIsEditable)
                point_table.setItem(point_table.rowCount()-1, 1, item_val)

            # Temperatura acqua
            if pt.water_temp is not None:
                point_table.insertRow(point_table.rowCount())
                item_wt = QTableWidgetItem("Temperatura Acqua (°C)")
                item_wt.setFlags(item_wt.flags() & ~Qt.ItemFlag.ItemIsEditable)
                point_table.setItem(point_table.rowCount()-1, 0, item_wt)
                item_val = QTableWidgetItem(f"{pt.water_temp:.1f}")
                item_val.setFlags(item_val.flags() & ~Qt.ItemFlag.ItemIsEditable)
                point_table.setItem(point_table.rowCount()-1, 1, item_val)

            # Potenza
            if pt.power is not None:
                point_table.insertRow(point_table.rowCount())
                item_pwr = QTableWidgetItem("Potenza (W)")
                item_pwr.setFlags(item_pwr.flags() & ~Qt.ItemFlag.ItemIsEditable)
                point_table.setItem(point_table.rowCount()-1, 0, item_pwr)
                item_val = QTableWidgetItem(str(pt.power))
                item_val.setFlags(item_val.flags() & ~Qt.ItemFlag.ItemIsEditable)
                point_table.setItem(point_table.rowCount()-1, 1, item_val)

            # Corso/heading
            if pt.course is not None:
                point_table.insertRow(point_table.rowCount())
                item_crs = QTableWidgetItem("Corso (°)")
                item_crs.setFlags(item_crs.flags() & ~Qt.ItemFlag.ItemIsEditable)
                point_table.setItem(point_table.rowCount()-1, 0, item_crs)
                item_val = QTableWidgetItem(f"{pt.course:.1f}")
                item_val.setFlags(item_val.flags() & ~Qt.ItemFlag.ItemIsEditable)
                point_table.setItem(point_table.rowCount()-1, 1, item_val)

            # Calorie
            if pt.calories is not None:
                point_table.insertRow(point_table.rowCount())
                item_cal = QTableWidgetItem("Calorie")
                item_cal.setFlags(item_cal.flags() & ~Qt.ItemFlag.ItemIsEditable)
                point_table.setItem(point_table.rowCount()-1, 0, item_cal)
                item_val = QTableWidgetItem(f"{pt.calories:.1f}")
                item_val.setFlags(item_val.flags() & ~Qt.ItemFlag.ItemIsEditable)
                point_table.setItem(point_table.rowCount()-1, 1, item_val)

            # Pendenza raw (grade)
            if pt.grade is not None:
                point_table.insertRow(point_table.rowCount())
                item_gr = QTableWidgetItem("Pendenza Raw (%)")
                item_gr.setFlags(item_gr.flags() & ~Qt.ItemFlag.ItemIsEditable)
                point_table.setItem(point_table.rowCount()-1, 0, item_gr)
                item_val = QTableWidgetItem(f"{pt.grade:.2f}")
                item_val.setFlags(item_val.flags() & ~Qt.ItemFlag.ItemIsEditable)
                point_table.setItem(point_table.rowCount()-1, 1, item_val)

            # Pendenza calcolata (da altitudine)
            if index > 1:
                assert self.track is not None and self.track.points is not None
                prev_pt = self.track.points[index - 2]
                if prev_pt.altitude is not None and pt.altitude is not None:
                    from core.analyzer import haversine_distance
                    dist = haversine_distance(prev_pt, pt)
                    if dist > 5.0:
                        slope = ((pt.altitude - prev_pt.altitude) / dist) * 100.0
                        point_table.insertRow(point_table.rowCount())
                        item_sl = QTableWidgetItem("Pendenza Calcolata (%)")
                        item_sl.setFlags(item_sl.flags() & ~Qt.ItemFlag.ItemIsEditable)
                        point_table.setItem(point_table.rowCount()-1, 0, item_sl)
                        item_val = QTableWidgetItem(f"{slope:.2f}")
                        item_val.setFlags(item_val.flags() & ~Qt.ItemFlag.ItemIsEditable)
                        point_table.setItem(point_table.rowCount()-1, 1, item_val)

            # Accuratezza GPS
            if pt.gps_accuracy is not None:
                point_table.insertRow(point_table.rowCount())
                item_gps = QTableWidgetItem("Accuratezza GPS (m)")
                item_gps.setFlags(item_gps.flags() & ~Qt.ItemFlag.ItemIsEditable)
                point_table.setItem(point_table.rowCount()-1, 0, item_gps)
                item_val = QTableWidgetItem(f"{pt.gps_accuracy:.1f}")
                item_val.setFlags(item_val.flags() & ~Qt.ItemFlag.ItemIsEditable)
                point_table.setItem(point_table.rowCount()-1, 1, item_val)

            # Dati extra
            if hasattr(pt, 'extra_data') and pt.extra_data:
                for k, v in pt.extra_data.items():
                    point_table.insertRow(point_table.rowCount())
                    item_extra = QTableWidgetItem(f"Extra: {k}")
                    item_extra.setFlags(item_extra.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    point_table.setItem(point_table.rowCount()-1, 0, item_extra)
                    item_val = QTableWidgetItem(format_value(v))
                    item_val.setFlags(item_val.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    point_table.setItem(point_table.rowCount()-1, 1, item_val)

        update_point_info(1)
        point_spin.valueChanged.connect(update_point_info)

        tabs.addTab(tab_point, "Dati Punto")

        layout.addWidget(tabs)
        dlg.exec()

    def _abort_weather_requests(self):
        """Annulla e libera tutte le richieste meteo ancora in corso."""
        for reply in self.weather_requests:
            reply.abort()
        self.weather_requests = []
        self.weather_outstanding = 0

    def _cancel_weather_fetch(self):
        """Annulla un eventuale recupero meteo in corso."""
        self.weather_token += 1
        self._abort_weather_requests()
        self.weather_active = False

    def _weather_request(self, lat, lon, dt_utc, is_start):
        """Avvia una singola richiesta HTTP asincrona verso Open-Meteo."""
        url = QUrl(build_weather_url(lat, lon, dt_utc))
        req = QNetworkRequest(url)
        # Timeout duro di sicurezza: l'icona non resterà mai gialla oltre ~15s.
        req.setTransferTimeout(15000)
        reply = self.weather_nam.get(req)
        assert reply is not None
        reply.setProperty("is_start", is_start)
        reply.setProperty("dt_utc", dt_utc)
        reply.setProperty("token", self.weather_token)
        reply.finished.connect(lambda: self._on_weather_reply(reply))
        self.weather_requests.append(reply)
        self.weather_outstanding += 1

    def _on_weather_reply(self, reply):
        """Gestisce il completamento di una risposta meteo."""
        if reply in self.weather_requests:
            self.weather_requests.remove(reply)

        # Risposta di una fetch superata (nuovo import): ignora.
        if reply.property("token") != self.weather_token:
            reply.deleteLater()
            return

        self.weather_outstanding -= 1
        is_start = reply.property("is_start")
        dt_utc = reply.property("dt_utc")
        info = None
        if reply.error() == QNetworkReply.NetworkError.NoError:
            info = parse_weather_response(reply.readAll().data(), dt_utc)
        reply.deleteLater()

        if is_start:
            self.weather_start_result = info
        else:
            self.weather_end_result = info

        if self.weather_outstanding <= 0:
            self._finish_weather_fetch()

    def _finish_weather_fetch(self):
        """Applica il meteo recuperato (o il fallimento) e aggiorna l'icona."""
        self.weather_active = False
        if self.track is None:
            self._update_weather_icon()
            return
        ws = self.weather_start_result
        we = self.weather_end_result
        if ws is None and we is None:
            # Nessun dato recuperabile: icona rossa con "Assente".
            self._update_weather_icon()
            return
        self.track.weather_start = ws
        self.track.weather_end = we or ws
        self.capabilities = TrackCapabilities(self.track)
        self._update_weather_icon()

    def _set_weather_pending(self):
        """Mostra l'icona meteo gialla con l'indicazione \"recupero in corso\"."""
        label = self.icon_labels["weather"]
        pixmap = QPixmap("assets/icons/weather.png")
        if not pixmap.isNull():
            label.setPixmap(pixmap.scaled(24, 24, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            label.setText("?")

        effect = QGraphicsColorizeEffect(label)
        effect.setColor(QColor("yellow"))
        label.setGraphicsEffect(effect)
        label.setToolTip("Recupero dei dati meteo in corso...")
        label.show()

    def _update_weather_icon(self):
        """Aggiorna solo l'icona meteo con i dati correnti della traccia."""
        assert self.capabilities is not None
        weather_available = self.capabilities.summary["weather"]
        weather_lines = self._weather_lines()
        self._set_icon("weather", "weather", weather_available, "Condizioni Meteo", weather_lines)

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        """Show icon tooltips immediately on hover."""
        obj = a0
        event = a1
        # Type assertions for Pylance
        if not isinstance(obj, QWidget) or not isinstance(event, QEvent):
            return super().eventFilter(a0, a1)
        if obj in self.icon_labels.values():
            if event.type() in (QEvent.Type.Enter, QEvent.Type.ToolTip):
                tooltip_text = obj.toolTip()
                if tooltip_text:
                    QToolTip.showText(obj.mapToGlobal(obj.rect().bottomLeft()), tooltip_text, obj)
                    return True
            elif event.type() == QEvent.Type.Leave:
                QToolTip.hideText()
                return True
            elif event.type() == QEvent.Type.MouseButtonPress and obj == self.info_button:
                self._show_track_info()
                return True
        return super().eventFilter(a0, a1)

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
            self.scale_mode_button.setChecked(True)
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
            self.scale_mode_button.setChecked(False)
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
        self.min_value.clearFocus()
        self.max_value.clearFocus()
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
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Seleziona attività",
            "",
            "Attività GPS (*.fit *.gpx)",
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if not filename:
            return
        try:
            # Annulla eventuale recupero meteo ancora in corso dal file precedente.
            self._cancel_weather_fetch()
            ext = Path(filename).suffix.lower()
            self.track = load_gpx(filename) if ext == ".gpx" else load_fit(filename)
            self.capabilities = TrackCapabilities(self.track)
            self.file_label.setText(f"  {Path(filename).name}")
            self.show_summary()
            # Se il file non fornisce dati meteo, li recupera da API esterna in background.
            if not self.capabilities.summary["weather"]:
                self._start_weather_fetch()
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
            self.map._fit_next_draw = True
            self.update_trim(0, slider_max)
            self._render_visible_track()
            self.activity_loaded.emit(self.track)
        except Exception as error:
            QMessageBox.critical(self, "Errore caricamento", str(error))