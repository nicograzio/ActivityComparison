"""Dialog for selecting and comparing Strava segments found in loaded tracks.

Called by:
    - ``ui.main_window.MainWindow`` when user requests Strava segment analysis

Consumes:
    - Segment occurrences produced by ``core.strava_analyzer.find_strava_segments_in_track``
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QBrush
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QTableWidget,
    QTableWidgetItem,
    QPushButton,
    QLabel,
    QHeaderView,
    QSplitter,
    QWidget,
    QMessageBox,
)

from ui.insight_dialog import _format_duration


class StravaSegmentsDialog(QDialog):
    """Dialog showing Strava segments found in loaded tracks.

    Allows selecting up to 2 segments of the same type for detailed comparison.
    """

    comparison_requested = pyqtSignal(list)

    def __init__(self, occurrences: list[dict], parent=None):
        """Create the Strava segments selection dialog.

        Args:
            occurrences: List of Strava segment occurrences found in tracks.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.setWindowTitle("Segmenti Strava Trovati")
        self.setModal(True)
        self.resize(1000, 500)

        self.occurrences = occurrences
        self.selected_rows: list[int] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Header
        header_widget = QWidget()
        header_layout = QVBoxLayout(header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(4)

        title_label = QLabel("<b>Segmenti Strava Trovati</b>")
        header_layout.addWidget(title_label)

        # Count occurrences by segment name
        segment_counts: dict[str, int] = {}
        track_set: set[str] = set()
        for occ in occurrences:
            segment_counts[occ["segment_name"]] = segment_counts.get(occ["segment_name"], 0) + 1
            track_set.add(occ["track_name"])

        summary_text = (
            f"<b>{len(segment_counts)} segmenti Strava</b> trovati in "
            f"<b>{len(track_set)} tracce</b> | "
            f"<b>{len(occurrences)} occorrenze totali</b>"
        )
        summary_label = QLabel(summary_text)
        summary_label.setWordWrap(True)
        header_layout.addWidget(summary_label)

        layout.addWidget(header_widget)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels([
            "Segmento Strava",
            "Traccia",
            "Lunghezza (m)",
            "Tempo",
            "Vel. media (km/h)",
            "Pendenza (%)",
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 200)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.cellClicked.connect(self._on_row_clicked)
        layout.addWidget(self.table, 1)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch(1)

        self.compare_button = QPushButton("Confronto dettagliato")
        self.compare_button.setEnabled(False)
        self.compare_button.clicked.connect(self._on_compare_clicked)
        button_layout.addWidget(self.compare_button)

        close_button = QPushButton("Chiudi")
        close_button.clicked.connect(self.reject)
        button_layout.addWidget(close_button)

        layout.addLayout(button_layout)

        self._populate_table()

    def _populate_table(self):
        """Fill the table with Strava segment occurrences."""
        self.table.setRowCount(len(self.occurrences))

        for row, occ in enumerate(self.occurrences):
            # Segment name
            item_name = QTableWidgetItem(occ["segment_name"])
            item_name.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, 0, item_name)

            # Track name
            item_track = QTableWidgetItem(occ["track_name"])
            item_track.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 1, item_track)

            # Length
            item_len = QTableWidgetItem(f"{occ['length_m']:.0f}")
            item_len.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 2, item_len)

            # Time
            time_str = _format_duration(occ["time_sec"]) if occ["time_sec"] is not None else "N/A"
            item_time = QTableWidgetItem(time_str)
            item_time.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 3, item_time)

            # Avg speed
            speed_str = f"{occ['avg_speed']:.1f}" if occ["avg_speed"] is not None else "N/A"
            item_speed = QTableWidgetItem(speed_str)
            item_speed.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 4, item_speed)

            # Slope
            slope_str = f"{occ['slope']:.1f}" if occ["slope"] is not None else "N/A"
            item_slope = QTableWidgetItem(slope_str)
            item_slope.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 5, item_slope)

    def _on_row_clicked(self, row: int, column: int):
        """Handle row selection toggle."""
        if row < 0 or row >= len(self.occurrences):
            return

        if row in self.selected_rows:
            self.selected_rows.remove(row)
            self.table.selectRow(row)
            self.table.clearSelection()
        else:
            if len(self.selected_rows) >= 2:
                # Deselect the first selected
                old_row = self.selected_rows.pop(0)
                self.table.selectRow(old_row)
                self.table.clearSelection()

            self.selected_rows.append(row)
            self.table.selectRow(row)

        self._update_compare_button()

    def _update_compare_button(self):
        """Enable compare button if 2 segments of same type are selected."""
        if len(self.selected_rows) != 2:
            self.compare_button.setEnabled(False)
            return

        occ1 = self.occurrences[self.selected_rows[0]]
        occ2 = self.occurrences[self.selected_rows[1]]

        # Must be same segment type
        if occ1["segment_name"] == occ2["segment_name"]:
            self.compare_button.setEnabled(True)
        else:
            self.compare_button.setEnabled(False)

    def _on_compare_clicked(self):
        """Emit comparison signal with selected occurrences."""
        if len(self.selected_rows) != 2:
            return

        occ1 = self.occurrences[self.selected_rows[0]]
        occ2 = self.occurrences[self.selected_rows[1]]

        if occ1["segment_name"] != occ2["segment_name"]:
            QMessageBox.warning(
                self,
                "Selezione non valida",
                "Per il confronto dettagliato e' necessario selezionare due occorrenze "
                "dello stesso segmento Strava.",
            )
            return

        self.comparison_requested.emit([occ1, occ2])
        self.accept()
