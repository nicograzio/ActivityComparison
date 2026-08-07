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

from PyQt6.QtCore import pyqtSignal, Qt, QSize, QEvent, QUrl
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
    x_axis_mode_changed = pyqtSignal(str)
    manual_limits_changed = pyqtSignal(float, float)
    scale_mode_changed = pyqtSignal(object)

    def __init__(self, title):
        """Create the activity panel.

        Args:
            title: Logical title of the panel.
        """
        super().__init__()
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
            "weather": QLabel()
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
        
        for label in self.icon_labels.values():
            self.summary_layout.addWidget(label)
            label.hide() # Hidden until track loaded
            label.installEventFilter(self)
            label.setMouseTracking(True)

        top_toolbar.addWidget(self.summary_container)
        top_toolbar.addStretch()
        layout.addLayout(top_toolbar)

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
        
        range_layout.addWidget(QLabel("Asse X:"))
        self.x_axis_combo = QComboBox()
        self.x_axis_combo.addItems(["Tempo", "Distanza"])
        self.x_axis_combo.setEnabled(False)
        self.x_axis_combo.currentTextChanged.connect(self._on_x_axis_changed)
        range_layout.addWidget(self.x_axis_combo)
        
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

    def _on_x_axis_changed(self, mode):
        """Handle X axis mode change."""
        self.x_axis_mode_changed.emit(mode)
        self._render_visible_track()

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
        weather_available = self.capabilities.summary["weather"]
        weather_lines = self._weather_lines()
        self._set_icon("weather", "weather", weather_available, "Condizioni Meteo", weather_lines)

    def eventFilter(self, obj, event):
        """Show icon tooltips immediately on hover."""
        if obj in self.icon_labels.values():
            if event.type() in (QEvent.Type.Enter, QEvent.Type.ToolTip):
                tooltip_text = obj.toolTip()
                if tooltip_text:
                    QToolTip.showText(obj.mapToGlobal(obj.rect().bottomLeft()), tooltip_text, obj)
                    return True
            elif event.type() == QEvent.Type.Leave:
                QToolTip.hideText()
                return True
        return super().eventFilter(obj, event)

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
            self.x_axis_combo.setEnabled(True)
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