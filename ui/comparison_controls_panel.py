"""Central comparison controls shown between the two activity panels.

This panel emits user intentions only; ``MainWindow`` owns the actual
comparison logic.

Called by:
    - ``ui.main_window.MainWindow``

Signals emitted:
    - sync_maps_toggled
    - sync_speed_scale_requested
    - invert_activities_requested
    - center_traces_requested
    - toggle_graphs_requested
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QPushButton, QSizePolicy


class ComparisonControlsPanel(QWidget):
    """Vertical stack of square buttons for the comparison view."""

    sync_maps_toggled = pyqtSignal(bool)
    sync_speed_scale_requested = pyqtSignal()
    invert_activities_requested = pyqtSignal()
    center_traces_requested = pyqtSignal()
    toggle_graphs_requested = pyqtSignal(bool)

    def __init__(self):
        """Create the control column and wire button signals.

        Called by:
            - ``MainWindow``
        """
        super().__init__()
        self.setObjectName("comparisonControlsPanel")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

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

        self.sync_speed_button = self._build_button(
            "⚖️",
            "Scala velocità comune",
            "Applica alla due attività la stessa scala velocità",
            checkable=False,
        )
        self.sync_speed_button.clicked.connect(self.sync_speed_scale_requested.emit)
        layout.addWidget(self.sync_speed_button, alignment=Qt.AlignmentFlag.AlignHCenter)

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
        layout.addWidget(self.graphs_button, alignment=Qt.AlignmentFlag.AlignHCenter)

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