"""Central comparison controls shown between the two activity panels.

This panel emits user intentions only; ``MainWindow`` owns the actual
comparison logic.

Called by:
    - ``ui.main_window.MainWindow``

Signals emitted:
    - sync_maps_toggled
    - sync_scales_toggled
    - invert_activities_requested
    - center_traces_requested
    - toggle_graphs_requested
    - left_fullscreen_toggled
    - right_fullscreen_toggled
    - highlight_common_segments_toggled
    - show_segments_insight_requested
    - show_strava_segments_requested
"""

import os
from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QPushButton, QSizePolicy


class ComparisonControlsPanel(QWidget):
    """Vertical stack of square buttons for the comparison view."""

    sync_maps_toggled = pyqtSignal(bool)
    sync_scales_toggled = pyqtSignal(bool)
    invert_activities_requested = pyqtSignal()
    center_traces_requested = pyqtSignal()
    toggle_graphs_requested = pyqtSignal(bool)
    left_fullscreen_toggled = pyqtSignal(bool)
    right_fullscreen_toggled = pyqtSignal(bool)
    highlight_common_segments_toggled = pyqtSignal(bool)
    show_segments_insight_requested = pyqtSignal()
    show_strava_segments_requested = pyqtSignal()

    def __init__(self):
        """Create the control column and wire button signals.

        Called by:
            - ``MainWindow``
        """
        super().__init__()
        self.setObjectName("comparisonControlsPanel")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        self._sync_controls_enabled = False
        self._fullscreen_mode = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(12)
        layout.addStretch(1)

        self.sync_maps_button = self._build_button(
            "🔗",
            "Sincronizza mappe",
            "Sincronizza posizione e zoom delle due mappe",
            checkable=True,
        )
        self.sync_maps_button.toggled.connect(self.sync_maps_toggled.emit)
        layout.addWidget(self.sync_maps_button, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.sync_scales_button = self._build_button(
            "⚖️",
            "Sincronizza scale",
            "Sincronizza la scala dei colori tra le due attività",
            checkable=True,
        )
        self.sync_scales_button.toggled.connect(self.sync_scales_toggled.emit)
        layout.addWidget(self.sync_scales_button, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.highlight_common_segments_button = self._build_button(
            "🎯",
            "Evidenzia segmenti comuni",
            "Evidenzia sulla mappa i segmenti comuni tra le due tracce",
            checkable=True,
        )
        self.highlight_common_segments_button.toggled.connect(self.highlight_common_segments_toggled.emit)
        layout.addWidget(self.highlight_common_segments_button, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.show_segments_insight_button = self._build_button(
            "🤖",
            "Mostra analisi segmenti",
            "Apri la finestra con il confronto dettagliato dei segmenti comuni",
            checkable=False,
        )
        self.show_segments_insight_button.clicked.connect(self.show_segments_insight_requested.emit)
        layout.addWidget(self.show_segments_insight_button, alignment=Qt.AlignmentFlag.AlignHCenter)

        strava_icon_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "assets", "icons", "strava.png"
        )
        self.show_strava_segments_button = QPushButton()
        self.show_strava_segments_button.setObjectName("Segmenti Strava")
        self.show_strava_segments_button.setProperty("class", "comparisonControlButton")
        self.show_strava_segments_button.setToolTip("Trova segmenti Strava e confrontali")
        self.show_strava_segments_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.show_strava_segments_button.setFixedSize(42, 42)
        strava_icon = QIcon(strava_icon_path)
        if not strava_icon.isNull():
            self.show_strava_segments_button.setIcon(strava_icon)
            self.show_strava_segments_button.setIconSize(QSize(32, 32))
        else:
            self.show_strava_segments_button.setText("🏃")
        self.show_strava_segments_button.clicked.connect(self.show_strava_segments_requested.emit)
        layout.addWidget(self.show_strava_segments_button, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.left_fullscreen_button = self._build_button(
            "➡️",
            "Schermo intero sinistra",
            "Nascondi il pannello di destra e porta a schermo intero la traccia sinistra",
            checkable=True,
        )
        self.left_fullscreen_button.toggled.connect(self.left_fullscreen_toggled.emit)
        layout.addWidget(self.left_fullscreen_button, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.right_fullscreen_button = self._build_button(
            "⬅️",
            "Schermo intero destra",
            "Nascondi il pannello di sinistra e porta a schermo intero la traccia destra",
            checkable=True,
        )
        self.right_fullscreen_button.toggled.connect(self.right_fullscreen_toggled.emit)
        layout.addWidget(self.right_fullscreen_button, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.invert_button = self._build_button(
            "🔄",
            "Inverti attività A/B",
            "Scambia le due attività tra i pannelli",
            checkable=False,
        )
        self.invert_button.clicked.connect(self.invert_activities_requested.emit)
        layout.addWidget(self.invert_button, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.center_button = self._build_button(
            "📍",
            "Centra entrambe sulle tracce",
            "Porta entrambe le mappe a inquadrare le rispettive tracce",
            checkable=False,
        )
        self.center_button.clicked.connect(self.center_traces_requested.emit)
        layout.addWidget(self.center_button, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.graphs_button = self._build_button(
            "📊",
            "Mostra / Nascondi grafici",
            "Mostra o nasconde il pannello dei grafici sotto le mappe",
            checkable=True,
        )
        self.graphs_button.toggled.connect(self.toggle_graphs_requested.emit)
        self.graphs_button.setChecked(True)
        layout.addWidget(self.graphs_button, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.set_sync_controls_enabled(False)
        self.set_fullscreen_buttons_enabled(False, False)
        self.set_fullscreen_state(None)

        layout.addStretch(1)

        self.setStyleSheet(
            """
            QWidget#comparisonControlsPanel {
                background: transparent;
            }
            QPushButton.comparisonControlButton {
                min-width: 42px;
                max-width: 42px;
                min-height: 42px;
                max-height: 42px;
                border-radius: 8px;
                border: 1px solid #707070;
                background: #2d2d2d;
                color: #f0f0f0;
                font-size: 18px;
                font-weight: 700;
            }
            QPushButton.comparisonControlButton:hover {
                background: #3b3b3b;
            }
            QPushButton.comparisonControlButton:checked {
                background: #5a5a5a;
                border-color: #a0a0a0;
            }
            """
        )

    def set_sync_controls_enabled(self, enabled: bool):
        """Enable or disable comparison and synchronization controls.

        Called by:
            - ``MainWindow`` when tracks are loaded/unloaded
        """
        self._sync_controls_enabled = enabled
        self.sync_maps_button.setEnabled(enabled and self._fullscreen_mode is None)
        self.sync_scales_button.setEnabled(enabled and self._fullscreen_mode is None)
        self.highlight_common_segments_button.setEnabled(enabled and self._fullscreen_mode is None)
        self.invert_button.setEnabled(enabled and self._fullscreen_mode is None)
        self.center_button.setEnabled(enabled and self._fullscreen_mode is None)

    def set_segments_insight_enabled(self, enabled: bool):
        """Enable or disable the segments insight button.

        Called by:
            - ``MainWindow`` when common segments are available or cleared.
        """
        self.show_segments_insight_button.setEnabled(
            enabled and self._sync_controls_enabled and self._fullscreen_mode is None
        )

    def set_strava_segments_enabled(self, enabled: bool):
        """Enable or disable the Strava segments button.

        Called by:
            ``MainWindow`` when at least one track is loaded.
        """
        self.show_strava_segments_button.setEnabled(
            enabled and self._fullscreen_mode is None
        )

    def set_highlight_common_segments_checked(self, checked: bool):
        """Programmatically check or uncheck the highlight toggle.

        Called by:
            - ``MainWindow`` to reflect the current highlight state without
              emitting the toggled signal again.
        """
        self.highlight_common_segments_button.blockSignals(True)
        try:
            self.highlight_common_segments_button.setChecked(checked)
        finally:
            self.highlight_common_segments_button.blockSignals(False)

    def set_fullscreen_buttons_enabled(self, left_enabled: bool, right_enabled: bool):
        """Enable or disable the fullscreen controls for each side."""
        self.left_fullscreen_button.setEnabled(left_enabled)
        self.right_fullscreen_button.setEnabled(right_enabled)

    def set_fullscreen_state(self, mode):
        """Set fullscreen mode and disable unrelated controls."""
        self._fullscreen_mode = mode
        is_fullscreen = mode is not None

        self.sync_maps_button.setEnabled(self._sync_controls_enabled and not is_fullscreen)
        self.sync_scales_button.setEnabled(self._sync_controls_enabled and not is_fullscreen)
        self.highlight_common_segments_button.setEnabled(self._sync_controls_enabled and not is_fullscreen)
        self.show_segments_insight_button.setEnabled(self._sync_controls_enabled and not is_fullscreen)
        self.show_strava_segments_button.setEnabled(not is_fullscreen)
        self.invert_button.setEnabled(self._sync_controls_enabled and not is_fullscreen)
        self.center_button.setEnabled(self._sync_controls_enabled and not is_fullscreen)

        self.left_fullscreen_button.blockSignals(True)
        try:
            self.left_fullscreen_button.setChecked(mode == "left")
        finally:
            self.left_fullscreen_button.blockSignals(False)

        self.right_fullscreen_button.blockSignals(True)
        try:
            self.right_fullscreen_button.setChecked(mode == "right")
        finally:
            self.right_fullscreen_button.blockSignals(False)

    def _build_button(self, text: str, label: str, tooltip: str, checkable: bool) -> QPushButton:
        """Create one square button used in the comparison column.

        Called by:
            - ``__init__`` for each button

        Returns:
            QPushButton: Configured control button.
        """
        button = QPushButton(text)
        button.setObjectName(label)
        button.setProperty("class", "comparisonControlButton")
        button.setToolTip(tooltip)
        button.setCheckable(checkable)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFixedSize(42, 42)
        return button