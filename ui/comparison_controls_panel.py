from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QPushButton, QSizePolicy


class ComparisonControlsPanel(QWidget):
    sync_maps_toggled = pyqtSignal(bool)
    sync_speed_scale_requested = pyqtSignal()

    def __init__(self):
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
        button = QPushButton(text)
        button.setObjectName(label)
        button.setProperty("class", "comparisonControlButton")
        button.setToolTip(tooltip)
        button.setCheckable(checkable)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFixedSize(42, 42)
        return button
