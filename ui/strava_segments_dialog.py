"""Dialog for selecting and comparing Strava segments found in loaded tracks.

Called by:
    - ``ui.main_window.MainWindow`` when user requests Strava segment analysis

Consumes:
    - Segment occurrences produced by ``core.strava_analyzer.find_strava_segments_in_track``
    - Original Strava segments from ``core.strava_analyzer.load_strava_segments``
    - Reverse geocoding from ``core.geocoder.reverse_geocode``
"""

import html
import os

from PyQt6.QtCore import Qt, pyqtSignal, QThread
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QTableWidget,
    QTableWidgetItem,
    QPushButton,
    QLabel,
    QHeaderView,
    QScrollArea,
    QWidget,
    QMessageBox,
)

from core.analyzer import track_distance_profile
from core.geocoder import reverse_geocode
from ui.insight_dialog import _format_duration


class _GeocodeWorker(QThread):
    """Thread asincrono per il reverse geocoding dei segmenti.

    Esegue ``core.geocoder.reverse_geocode`` per ogni punto medio del segmento
    fuori dal thread della UI, evitando blocchi durante le chiamate HTTP.
    """

    geocoded = pyqtSignal(str, str)  # segment_name, location ("" se non trovata)

    def __init__(self, points: list, parent=None):
        """Crea il worker.

        Args:
            points: Lista di tuple ``(segment_name, lat, lon)``.
            parent: Widget parent opzionale.
        """
        super().__init__(parent)
        self._points = points

    def run(self):
        """Esegue il geocoding e notifica ogni risultato via segnale."""
        for name, lat, lon in self._points:
            if self.isInterruptionRequested():
                break
            loc = reverse_geocode(lat, lon)
            self.geocoded.emit(name, loc or "")


class StravaSegmentsDialog(QDialog):
    """Dialog showing Strava segments found in loaded tracks.

    Groups occurrences by segment, showing one header (name, distance, number
    of points, geographic location) and one table per segment. Allows
    selecting up to 2 occurrences of the same segment for detailed comparison.
    """

    comparison_requested = pyqtSignal(list)

    def __init__(self, occurrences: list[dict], strava_segments: list[dict] | None = None, parent=None):
        """Create the Strava segments selection dialog.

        Args:
            occurrences: List of Strava segment occurrences found in tracks.
            strava_segments: Optional original Strava segments (for real
                segment distance/points and geocoding midpoint).
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.setWindowTitle("Segmenti Strava Trovati")
        self.setModal(False)
        self.resize(1050, 650)

        self.occurrences = occurrences
        self.strava_segments = strava_segments or []
        self._segment_by_name = {s["name"]: s for s in self.strava_segments}
        self._selected: list[tuple[str, dict]] = []
        self._updating = True
        self._location_labels: dict[str, QLabel] = {}
        self._segment_info: dict[str, tuple[str, str]] = {}
        self._table_occurrences: dict[QTableWidget, list[dict]] = {}
        self._geocode_worker: _GeocodeWorker | None = None

        # Raggruppa le occorrenze per segmento (già ordinate per nome).
        self._groups: dict[str, list[dict]] = {}
        for occ in occurrences:
            self._groups.setdefault(occ["segment_name"], []).append(occ)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Header riepilogativo
        segment_counts = len(self._groups)
        track_set = {occ["track_name"] for occ in occurrences}
        summary_text = (
            f"<b>{segment_counts} segmenti Strava</b> trovati in "
            f"<b>{len(track_set)} tracce</b> | "
            f"<b>{len(occurrences)} occorrenze totali</b>"
        )
        summary_label = QLabel(summary_text)
        summary_label.setWordWrap(True)
        layout.addWidget(summary_label)

        # Area scrollabile con una sezione per segmento
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        container = QWidget()
        self._sections_layout = QVBoxLayout(container)
        self._sections_layout.setContentsMargins(0, 0, 0, 0)
        self._sections_layout.setSpacing(8)

        for segment_name, group_occurrences in self._groups.items():
            self._build_segment_section(segment_name, group_occurrences)

        self._sections_layout.addStretch(1)
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

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

        self._update_compare_button()
        self._start_geocoding()
        # Costruzione completata: ora le modifiche utente sono attive.
        self._updating = False

    # ------------------------------------------------------------------ UI

    def _build_segment_header_text(self, segment_name: str, position_text: str) -> str:
        """Costruisce l'HTML per l'intestazione di un segmento su una singola riga.

        Combina nome, distanza, numero di punti e posizione in un'unica riga
        per ottimizzare lo spazio verticale.
        """
        dist_str, points_str = self._segment_info.get(segment_name, ("N/A", "N/A"))
        return (
            f"<span style='font-size:13px; font-weight:bold;'>"
            f"{self._escape(segment_name)}</span>"
            f"<span style='font-size:11px; color:#E0E0E0;'>"
            f"  |  Distanza: <b>{dist_str}</b>  |  Punti: <b>{points_str}</b>"
            f"  |  Posizione: {position_text}</span>"
        )

    def _build_segment_section(self, segment_name: str, occurrences: list[dict]):
        """Costruisce l'intestazione e la tabella per un segmento."""
        seg = self._segment_by_name.get(segment_name)
        if seg is not None:
            seg_track = seg["track"]
            seg_points = len(seg_track.points)
            profile, _ = track_distance_profile(seg_track)
            seg_length = profile[-1] if profile else 0.0
        else:
            seg_points = occurrences[0].get("segment_point_count") or 0
            seg_length = occurrences[0].get("length_m", 0.0)

        # Intestazione segmento: nome, distanza, punti e posizione su una singola riga
        dist_str = self._format_distance(seg_length)
        points_str = str(seg_points)
        self._segment_info[segment_name] = (dist_str, points_str)
        header = QLabel(self._build_segment_header_text(segment_name, "<b>caricamento…</b>"))
        header.setWordWrap(True)
        self._sections_layout.addWidget(header)
        self._location_labels[segment_name] = header

        # Tabella delle occorrenze
        table = QTableWidget()
        table.setColumnCount(10)
        table.setHorizontalHeaderLabels([
            "Sel.",
            "File",
            "Tempo",
            "% Match",
            "Lunghezza (m)",
            "Vel. media (km/h)",
            "Pendenza (%)",
            "Direzione",
            "FC media (bpm)",
            "Punti match",
        ])
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        header_view = table.horizontalHeader()
        header_view.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(0, 40)
        table.cellClicked.connect(self._on_cell_clicked)
        table.itemChanged.connect(self._on_item_changed)
        self._sections_layout.addWidget(table)

        self._table_occurrences[table] = list(occurrences)
        self._populate_table(table, occurrences)

        self._sections_layout.addSpacing(16)

    def _populate_table(self, table: QTableWidget, occurrences: list[dict]):
        """Riempe una tabella con le occorrenze di un segmento."""
        table.setRowCount(len(occurrences))

        for row, occ in enumerate(occurrences):
            # Checkbox
            check_item = QTableWidgetItem()
            check_item.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
            )
            check_item.setCheckState(Qt.CheckState.Unchecked)
            check_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            check_item.setToolTip("Seleziona per il confronto (max 2 per segmento)")
            table.setItem(row, 0, check_item)

            # File
            raw_name = occ.get("track_name", "")
            file_name = os.path.basename(str(raw_name).replace("\\", "/")) or raw_name
            item_file = QTableWidgetItem(file_name)
            item_file.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_file.setToolTip(str(raw_name))
            table.setItem(row, 1, item_file)

            # Tempo
            time_str = _format_duration(occ.get("time_sec")) if occ.get("time_sec") is not None else "N/A"
            item_time = QTableWidgetItem(time_str)
            item_time.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(row, 2, item_time)

            # % Match
            n_match = occ.get("n_match_points")
            n_total = occ.get("segment_point_count")
            if n_match is not None and n_total:
                match_pct = (n_match / n_total) * 100.0
                item_match = QTableWidgetItem(f"{match_pct:.1f}%")
            else:
                item_match = QTableWidgetItem("N/A")
            item_match.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(row, 3, item_match)

            # Lunghezza
            item_len = QTableWidgetItem(f"{occ.get('length_m', 0):.0f}")
            item_len.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(row, 4, item_len)

            # Velocità media
            speed = occ.get("avg_speed")
            item_speed = QTableWidgetItem(f"{speed:.1f}" if speed is not None else "N/A")
            item_speed.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(row, 5, item_speed)

            # Pendenza
            slope = occ.get("slope")
            item_slope = QTableWidgetItem(f"{slope:.1f}" if slope is not None else "N/A")
            item_slope.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(row, 6, item_slope)

            # Direzione
            direction = "Inversa" if occ.get("direction") == "reverse" else "Diretta"
            item_dir = QTableWidgetItem(direction)
            item_dir.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(row, 7, item_dir)

            # FC media
            hr = occ.get("avg_hr")
            item_hr = QTableWidgetItem(f"{hr:.0f}" if hr is not None else "N/A")
            item_hr.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(row, 8, item_hr)

            # Punti match
            item_n = QTableWidgetItem(str(n_match) if n_match is not None else "N/A")
            item_n.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(row, 9, item_n)

    # ------------------------------------------------------------ Selezione

    def _on_cell_clicked(self, row: int, column: int):
        """Seleziona/deseleziona un'occorrenza cliccando sulla riga."""
        if column == 0:
            # La checkbox è gestita da _on_item_changed.
            return
        table = self.sender()
        if not isinstance(table, QTableWidget):
            return
        occurrences = self._table_occurrences.get(table)
        if not occurrences or row < 0 or row >= len(occurrences):
            return
        self._toggle_occurrence(occurrences[row])

    def _on_item_changed(self, item: QTableWidgetItem):
        """Seleziona/deseleziona un'occorrenza tramite la checkbox."""
        if self._updating or item.column() != 0:
            return
        table = item.tableWidget()
        if table is None:
            return
        occurrences = self._table_occurrences.get(table)
        if not occurrences or item.row() < 0 or item.row() >= len(occurrences):
            return
        checked = item.checkState() == Qt.CheckState.Checked
        self._toggle_occurrence(occurrences[item.row()], checked=checked)

    def _toggle_occurrence(self, occ: dict, checked: bool | None = None):
        """Applica la logica di selezione (max 2, stesso segmento)."""
        if checked is None:
            checked = not self._is_selected(occ)

        if checked:
            # Se già selezionata, nessuna modifica.
            if self._is_selected(occ):
                return
            seg_name = occ["segment_name"]
            # Se la selezione corrente è di un segmento diverso, resetta tutto.
            if self._selected and self._selected[0][0] != seg_name:
                self._selected.clear()
            if len(self._selected) >= 2:
                self._selected.pop(0)
            self._selected.append((seg_name, occ))
        else:
            self._selected = [s for s in self._selected if s[1] is not occ]

        self._refresh_checkboxes()
        self._update_compare_button()

    def _is_selected(self, occ: dict) -> bool:
        """True se l'occorrenza è già selezionata."""
        return any(occ is s for _, s in self._selected)

    def _refresh_checkboxes(self):
        """Sincronizza checkbox e righe selezionate con lo stato corrente."""
        self._updating = True
        try:
            for table, occurrences in self._table_occurrences.items():
                selected_rows = []
                for row, occ in enumerate(occurrences):
                    is_sel = self._is_selected(occ)
                    item = table.item(row, 0)
                    if item is not None:
                        item.setCheckState(
                            Qt.CheckState.Checked if is_sel else Qt.CheckState.Unchecked
                        )
                    if is_sel:
                        selected_rows.append(row)
                table.clearSelection()
                for row in selected_rows:
                    table.selectRow(row)
        finally:
            self._updating = False

    def _update_compare_button(self):
        """Abilita il confronto solo con 2 occorrenze dello stesso segmento."""
        enabled = (
            len(self._selected) == 2
            and self._selected[0][0] == self._selected[1][0]
        )
        self.compare_button.setEnabled(enabled)

    # ------------------------------------------------------------- Geocoding

    def _segment_midpoint(self, segment_name: str):
        """Restituisce (lat, lon) del punto medio del segmento, se disponibile."""
        seg = self._segment_by_name.get(segment_name)
        if seg is not None:
            pts = seg["track"].points
            if pts:
                mid = pts[len(pts) // 2]
                return mid.latitude, mid.longitude
        occurrences = self._groups.get(segment_name)
        if occurrences:
            coords = occurrences[0].get("coords") or []
            if coords:
                mid = coords[len(coords) // 2]
                return mid[0], mid[1]
        return None

    def _start_geocoding(self):
        """Avvia il reverse geocoding asincrono per ogni segmento."""
        points = []
        for segment_name in self._groups:
            mid = self._segment_midpoint(segment_name)
            if mid:
                points.append((segment_name, mid[0], mid[1]))
        if not points:
            return

        self._geocode_worker = _GeocodeWorker(points, parent=self)
        self._geocode_worker.geocoded.connect(self._on_geocoded)
        self._geocode_worker.start()

    def _on_geocoded(self, segment_name: str, location: str):
        """Aggiorna l'etichetta della posizione per un segmento."""
        label = self._location_labels.get(segment_name)
        if label is None:
            return
        if location:
            pos_text = f"<b>{location}</b>"
        else:
            mid = self._segment_midpoint(segment_name)
            if mid:
                pos_text = f"{mid[0]:.4f}, {mid[1]:.4f}"
            else:
                pos_text = "N/D"
        label.setText(self._build_segment_header_text(segment_name, pos_text))

    def closeEvent(self, event):
        """Interrompe il worker di geocoding alla chiusura del dialog."""
        if self._geocode_worker is not None and self._geocode_worker.isRunning():
            self._geocode_worker.requestInterruption()
            self._geocode_worker.wait(1500)
        super().closeEvent(event)

    # ------------------------------------------------------------ Confronto

    def _on_compare_clicked(self):
        """Emette il segnale di confronto con le occorrenze selezionate."""
        if len(self._selected) != 2:
            return

        seg_name1, occ1 = self._selected[0]
        seg_name2, occ2 = self._selected[1]

        if seg_name1 != seg_name2:
            QMessageBox.warning(
                self,
                "Selezione non valida",
                "Per il confronto dettagliato e' necessario selezionare due occorrenze "
                "dello stesso segmento Strava.",
            )
            return

        self.comparison_requested.emit([occ1, occ2])
        self.accept()

    # ------------------------------------------------------------- Utilities

    @staticmethod
    def _format_distance(meters: float) -> str:
        """Formatta una distanza in metri come km o m a seconda della grandezza."""
        if meters is None:
            return "N/A"
        if meters >= 1000:
            return f"{meters / 1000:.2f} km"
        return f"{meters:.0f} m"

    @staticmethod
    def _escape(text: str) -> str:
        """Escape HTML minimale per i testi delle intestazioni."""
        return html.escape(str(text), quote=False)
