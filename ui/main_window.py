"""Main window orchestration for ActivityComparison.

This module wires together the two activity panels, the comparison control
column, the graph panels and the map synchronization logic.

Called by:
    - ``main.py`` when the application starts

Consumes:
    - ``ui.track_panel.TrackPanel``
    - ``ui.comparison_controls_panel.ComparisonControlsPanel``
    - ``ui.graph_panel.GraphPanel``
"""

import os
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QMainWindow, QWidget, QHBoxLayout, QSplitter, QMessageBox

from ui.comparison_controls_panel import ComparisonControlsPanel
from ui.track_panel import TrackPanel, ScaleMode
from ui.graph_panel import GraphPanel
from ui.insight_dialog import InsightDialog
from ui.strava_segments_dialog import StravaSegmentsDialog
from core.analyzer import calculate_speed_series, track_distance_profile, calculate_track_series, find_common_segments
from core.strava_analyzer import load_strava_segments, find_strava_segments_in_track


class MainWindow(QMainWindow):
    """Top-level GUI controller.

    Responsibilities:
        - create the side-by-side activity layout
        - connect map synchronization
        - keep the graph panels updated
        - coordinate the comparison buttons

    Created by:
        - ``main.py``
    """
    fullyReady = pyqtSignal()



    def __init__(self):
        """Build the full main window layout and connect signals.

        Calls:
            - ``_build_side_splitter``
            - ``_connect_map_sync``

        Side effects:
            Creates the central widgets and connects all UI signals.
        """
        super().__init__()
        self.setWindowTitle("Activity Comparison")
        self._sync_maps_enabled = False
        self._syncing_maps = False
        self._sync_scales_enabled = False
        self._syncing_scales = False
        self._graphs_visible = True
        # Store the original scale settings when sync is enabled for restoration when disabled
        self._left_sync_backup = None
        self._right_sync_backup = None
        self._fullscreen_mode = None
        self._maps_ready_count = 0
        self._common_segments = []
        self._highlight_enabled = False
        self._strava_segments = []
        self._strava_occurrences = []

        central = QWidget()
        main_layout = QHBoxLayout(central)

        self.left_panel = TrackPanel("Activity A")
        self.right_panel = TrackPanel("Activity B")
        self.left_panel.other_panel = self.right_panel
        self.right_panel.other_panel = self.left_panel
        self.controls_panel = ComparisonControlsPanel()
        self.left_graph = GraphPanel()
        self.right_graph = GraphPanel()

        self.left_panel.map.mapReady.connect(self._on_map_ready)
        self.right_panel.map.mapReady.connect(self._on_map_ready)

        self.left_panel.visible_track_changed.connect(
            lambda track, graph=self.left_graph: self._update_graph(graph, track)
        )
        self.left_panel.visible_track_changed.connect(
            lambda _: self._on_visible_track_changed(self.left_panel)
        )
        self.left_panel.activity_loaded.connect(self._check_sync_controls_availability)
        self.left_panel.manual_limits_changed.connect(
            lambda min_v, max_v: self._on_manual_limits_changed(self.left_panel, min_v, max_v)
        )
        self.left_panel.scale_mode_changed.connect(
            lambda mode: self._on_scale_mode_changed(self.left_panel, mode)
        )

        # Connect graph hover signals to map synchronization
        self.left_graph.point_hovered.connect(
            lambda idx, x, y: self._on_graph_point_hovered(self.left_panel, idx, x, y)
        )
        self.left_graph.x_axis_changed.connect(
            lambda _: self.left_panel.refresh_visible_track()
        )

        self.right_panel.visible_track_changed.connect(
            lambda track, graph=self.right_graph: self._update_graph(graph, track)
        )
        self.right_panel.visible_track_changed.connect(
            lambda _: self._on_visible_track_changed(self.right_panel)
        )
        self.right_panel.activity_loaded.connect(self._check_sync_controls_availability)
        self.right_panel.manual_limits_changed.connect(
            lambda min_v, max_v: self._on_manual_limits_changed(self.right_panel, min_v, max_v)
        )
        self.right_panel.scale_mode_changed.connect(
            lambda mode: self._on_scale_mode_changed(self.right_panel, mode)
        )

        # Connect graph hover signals to map synchronization
        self.right_graph.point_hovered.connect(
            lambda idx, x, y: self._on_graph_point_hovered(self.right_panel, idx, x, y)
        )
        self.right_graph.x_axis_changed.connect(
            lambda _: self.right_panel.refresh_visible_track()
        )

        self.left_panel.color_mode.currentTextChanged.connect(
            lambda mode: self._on_color_mode_changed(self.left_panel, mode)
        )
        self.right_panel.color_mode.currentTextChanged.connect(
            lambda mode: self._on_color_mode_changed(self.right_panel, mode)
        )

        self.left_splitter = self._build_side_splitter(self.left_panel, self.left_graph)
        self.right_splitter = self._build_side_splitter(self.right_panel, self.right_graph)

        self.controls_panel.sync_maps_toggled.connect(self._on_sync_maps_toggled)
        self.controls_panel.sync_scales_toggled.connect(self._on_sync_scales_toggled)
        self.controls_panel.highlight_common_segments_toggled.connect(self._on_highlight_common_segments_toggled)
        self.controls_panel.show_segments_insight_requested.connect(self._on_show_segments_insight)
        self.controls_panel.show_strava_segments_requested.connect(self._on_show_strava_segments)
        self.controls_panel.invert_activities_requested.connect(self._invert_activities)
        self.controls_panel.center_traces_requested.connect(self._center_traces)
        self.controls_panel.toggle_graphs_requested.connect(self._toggle_graphs)
        self.controls_panel.left_fullscreen_toggled.connect(self._on_left_fullscreen_toggled)
        self.controls_panel.right_fullscreen_toggled.connect(self._on_right_fullscreen_toggled)

        self._connect_map_sync(self.left_panel, self.right_panel)

        main_layout.addWidget(self.left_splitter, 1)
        main_layout.addWidget(self.controls_panel)
        main_layout.addWidget(self.right_splitter, 1)

        central.setLayout(main_layout)
        self.setCentralWidget(central)

    @staticmethod
    def _build_side_splitter(panel, graph):
        """Create a vertical splitter with one activity panel and one graph.

        Called by:
            - ``__init__`` when building the left and right columns

        Returns:
            QSplitter: Vertical splitter with panel over graph.
        """
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(panel)
        splitter.addWidget(graph)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([650, 300])
        return splitter

    @staticmethod
    def _map_supports_view_sync(panel):
        """Check whether the panel map widget exposes the sync API.

        Called by:
            - ``_connect_map_sync``
            - ``_copy_map_view``
            - ``_on_map_view_changed``

        Returns:
            bool: True when the active map renderer can share its state.
        """
        map_widget = getattr(panel, "map", None)
        return bool(
            map_widget is not None
            # and hasattr(map_widget, "viewChanged") # Folium sync is limited
            and hasattr(map_widget, "get_view_state")
            and hasattr(map_widget, "set_view_state")
        )

    def _connect_map_sync(self, left_panel, right_panel):
        """Connect the viewChanged signal of both maps.

        Called by:
            - ``__init__``

        Side effects:
            Hooks the map widgets so a change in one panel can be reflected in
            the other when the synchronization toggle is active.
        """
        if self._map_supports_view_sync(left_panel):
            left_panel.map.viewChanged.connect(
                lambda state: self._on_map_view_changed(left_panel, state)
            )
        if self._map_supports_view_sync(right_panel):
            right_panel.map.viewChanged.connect(
                lambda state: self._on_map_view_changed(right_panel, state)
            )

    def _on_sync_maps_toggled(self, enabled: bool):
        """Toggle bidirectional map view synchronization.

        Called by:
            - ``ComparisonControlsPanel.sync_maps_toggled``

        Side effects:
            Stores the toggle state and copies the first panel view onto the
            second one when enabled.
        """
        self._sync_maps_enabled = bool(enabled)
        if self._sync_maps_enabled:
            self._copy_map_view(self.left_panel, self.right_panel)

    def _copy_map_view(self, source_panel, target_panel):
        """Copy the view state from one panel to the other.

        Called by:
            - ``_on_sync_maps_toggled``

        Side effects:
            Uses the active renderer API to clone center, zoom, bearing and
            pitch between the two maps.
        """
        if not self._map_supports_view_sync(source_panel) or not self._map_supports_view_sync(target_panel):
            return

        def _apply_state(state):
            if not state:
                return
            self._syncing_maps = True

            def _release_sync(result=None):
                self._syncing_maps = False

            target_panel.map.set_view_state(state, _release_sync)

        get_view_state = getattr(source_panel.map, "get_view_state_async", None)
        if callable(get_view_state):
            get_view_state(_apply_state)
        else:
            state = source_panel.map.get_view_state()
            if not state:
                return
            _apply_state(state)

    def _on_map_view_changed(self, source_panel, state):
        """Mirror a changed map view to the opposite panel.

        Called by:
            - the ``viewChanged`` signal emitted by the active map widgets

        Args:
            source_panel: The panel that produced the state.
            state: Serialized map state.
        """
        if not self._sync_maps_enabled or self._syncing_maps:
            return
        target_panel = self.right_panel if source_panel is self.left_panel else self.left_panel
        if not self._map_supports_view_sync(target_panel):
            return

        target_state = target_panel.map.get_view_state()
        if target_state == state:
            return

        self._syncing_maps = True

        def _release_sync(result=None):
            self._syncing_maps = False

        target_panel.map.set_view_state(state, _release_sync)

    def _on_sync_scales_toggled(self, enabled: bool):
        """Toggle scale synchronization across both activities.

        Called by:
            - ``ComparisonControlsPanel.sync_scales_toggled``

        Side effects:
            - When enabling: takes the color mode from panel A (master),
              calculates absolute min/max across both visible tracks,
              forces both panels to Manual mode with the shared limits.
            - When disabling: restores each panel's previous independent settings.
        """
        self._sync_scales_enabled = bool(enabled)
        self.left_panel.sync_scales_enabled = bool(enabled)
        self.right_panel.sync_scales_enabled = bool(enabled)
        if self._sync_scales_enabled:
            # Both tracks must be loaded to synchronize scales.
            if self.left_panel.track is None or self.right_panel.track is None:
                self._sync_scales_enabled = False
                return

            # Backup current scale mode and limits before synchronization
            self._left_sync_backup = (
                self.left_panel.scale_mode,
                self.left_panel.manual_scale_min,
                self.left_panel.manual_scale_max
            )
            self._right_sync_backup = (
                self.right_panel.scale_mode,
                self.right_panel.manual_scale_min,
                self.right_panel.manual_scale_max
            )

            # When enabling sync, take the color mode from panel A (left, master)
            master_mode = self.left_panel._current_mode()
            self.right_panel.set_color_mode(master_mode)

            # Calculate absolute min/max across both visible tracks
            self._syncing_scales = True
            try:
                mode = master_mode
                ranges = []
                for panel in (self.left_panel, self.right_panel):
                    mini, maxi = panel.get_auto_limits_for_mode(mode)
                    if mini is not None and maxi is not None:
                        ranges.append((mini, maxi))

                if ranges:
                    shared_min = min(r[0] for r in ranges)
                    shared_max = max(r[1] for r in ranges)

                    # Force both panels to Automatic mode with the shared limits
                    self.left_panel.apply_scale_mode(ScaleMode.AUTO, shared_min, shared_max)
                    self.right_panel.apply_scale_mode(ScaleMode.AUTO, shared_min, shared_max)
            finally:
                self._syncing_scales = False
        else:
            # When disabling sync, restore previous settings
            if self._left_sync_backup:
                mode, min_val, max_val = self._left_sync_backup
                self._syncing_scales = True
                try:
                    if mode == ScaleMode.MANUAL and min_val is not None and max_val is not None:
                        self.left_panel.apply_scale_mode(ScaleMode.MANUAL, min_val, max_val)
                    else:
                        self.left_panel.apply_scale_mode(ScaleMode.AUTO)
                finally:
                    self._syncing_scales = False
                self._left_sync_backup = None

            if self._right_sync_backup:
                mode, min_val, max_val = self._right_sync_backup
                self._syncing_scales = True
                try:
                    if mode == ScaleMode.MANUAL and min_val is not None and max_val is not None:
                        self.right_panel.apply_scale_mode(ScaleMode.MANUAL, min_val, max_val)
                    else:
                        self.right_panel.apply_scale_mode(ScaleMode.AUTO)
                finally:
                    self._syncing_scales = False
                self._right_sync_backup = None

    def _on_visible_track_changed(self, source_panel):
        """Handle track visibility changes (trimming)."""
        if self._sync_scales_enabled and not self._syncing_scales:
            self._syncing_scales = True
            try:
                target_panel = self.right_panel if source_panel is self.left_panel else self.left_panel
                if target_panel.scale_mode == ScaleMode.AUTO:
                    target_panel._render_visible_track()
            finally:
                self._syncing_scales = False

        # Clear common segment highlights when the visible track changes
        if self._highlight_enabled:
            self._clear_common_segment_highlights()

    def _on_manual_limits_changed(self, source_panel, minimum, maximum):
        """Sync manual edits if scale synchronization is active."""
        if not self._sync_scales_enabled or self._syncing_scales:
            return

        self._syncing_scales = True
        try:
            target_panel = self.right_panel if source_panel is self.left_panel else self.left_panel
            target_panel.set_manual_scale_limits(minimum, maximum)
        finally:
            self._syncing_scales = False

    def _on_scale_mode_changed(self, panel, mode):
        """Handle scale mode changes on individual panels.
        
        When sync is active and manual limits change, apply them to both panels.
        If switching to auto, disable sync since synchronized mode must be manual.
        """
        if not self._sync_scales_enabled or self._syncing_scales:
            return

        # When synced and user tries to switch to AUTO, disable sync instead
        if mode == ScaleMode.AUTO:
            """
            self._sync_scales_enabled = False
            self.controls_panel.sync_scales_button.blockSignals(True)
            try:
                self.controls_panel.sync_scales_button.setChecked(False)
            finally:
                self.controls_panel.sync_scales_button.blockSignals(False)
            
            Il codice sopra è errato.
            Bisogna che alle tracce venga applicato l'automatico e che venga ricalcolta il min e il max assoluto
            """
            mode = self.left_panel._current_mode()
            ranges = []
            for panel in (self.left_panel, self.right_panel):
                mini, maxi = panel.get_auto_limits_for_mode(mode)
                if mini is not None and maxi is not None:
                    ranges.append((mini, maxi))

            if ranges:
                shared_min = min(r[0] for r in ranges)
                shared_max = max(r[1] for r in ranges)

                # Force both panels to Automatic mode with the shared limits
                self.left_panel.apply_scale_mode(ScaleMode.AUTO, shared_min, shared_max)
                self.right_panel.apply_scale_mode(ScaleMode.AUTO, shared_min, shared_max)
            return

        # If switching to MANUAL, apply the same limits to the other panel
        if mode == ScaleMode.MANUAL:
            target_panel = self.right_panel if panel is self.left_panel else self.left_panel
            self._syncing_scales = True
            try:
                mini, maxi = panel.current_scale_limits()
                target_panel.set_manual_scale_limits(mini, maxi)
            finally:
                self._syncing_scales = False

    def _recalculate_shared_scale(self):
        """Compute and apply absolute min/max across both visible tracks when sync is active."""
        if self._syncing_scales or not self._sync_scales_enabled:
            return

        self._syncing_scales = True
        try:
            mode = self.left_panel._current_mode()
            ranges = []
            for panel in (self.left_panel, self.right_panel):
                mini, maxi = panel.get_auto_limits_for_mode(mode)
                if mini is not None and maxi is not None:
                    ranges.append((mini, maxi))

            if ranges:
                shared_min = min(r[0] for r in ranges)
                shared_max = max(r[1] for r in ranges)

                # Update both panels with the shared limits while staying in MANUAL mode
                self.left_panel.set_manual_scale_limits(shared_min, shared_max)
                self.right_panel.set_manual_scale_limits(shared_min, shared_max)
        finally:
            self._syncing_scales = False

    def _on_color_mode_changed(self, panel, mode):
        """Handle color mode (metric) changes."""
        if not self._sync_scales_enabled or self._syncing_scales:
            return

        if self._highlight_enabled:
            self._clear_common_segment_highlights()

        if panel is self.left_panel:
            # Master A: force right panel to follow
            self._syncing_scales = True
            try:
                self.right_panel.set_color_mode(mode)
                self._recalculate_shared_scale()
            finally:
                self._syncing_scales = False
        else:
            # If right changes, force it back to left's mode if sync is active
            master_mode = self.left_panel._current_mode()
            if mode != master_mode:
                self._syncing_scales = True
                try:
                    self.right_panel.set_color_mode(master_mode)
                finally:
                    self._syncing_scales = False

    def _update_graph(self, graph, track):
        """Update a graph panel with the visible track series.

        Called by:
            - ``TrackPanel.visible_track_changed``

        Args:
            graph: Target ``GraphPanel``.
            track: Visible track to render.

        Side effects:
            Clears the graph when no track is available or redraws the speed
            series when data are present.
        """
        if not track or not getattr(track, "points", None):
            graph.clear_graph()
            return

        # Determine which panel this graph belongs to
        panel = self.left_panel if graph is self.left_graph else self.right_panel
        
        # Get real offsets from the track
        first_ts = None
        start_dist = 0.0
        if panel.track and panel.track.points:
            first_ts = panel.track.points[0].timestamp
            
            # Get the distance offset directly from the trimmed track
            if hasattr(track, 'start_distance_m'):
                start_dist = track.start_distance_m

        x_mode = graph.x_axis_combo.currentText()
        x_values, speeds, altitudes, heart_rates = calculate_track_series(
            track, 
            x_axis_mode=x_mode, 
            first_timestamp=first_ts, 
            start_distance_m=start_dist
        )
        
        if not x_values:
            graph.clear_graph()
            return

        graph.setVisible(self._graphs_visible)
        graph.set_series(x_values, speeds, altitudes, heart_rates, x_mode=x_mode)

    def _invert_activities(self):
        """Swap the left and right activities, graphs and splitters.

        Called by:
            - ``ComparisonControlsPanel.invert_activities_requested``
        """
        self.left_panel, self.right_panel = self.right_panel, self.left_panel
        self.left_graph, self.right_graph = self.right_graph, self.left_graph

        self._reset_splitter(self.left_splitter, self.left_panel, self.left_graph)
        self._reset_splitter(self.right_splitter, self.right_panel, self.right_graph)

    @staticmethod
    def _reset_splitter(splitter, top_widget, bottom_widget):
        """Replace the contents of a splitter with two new widgets.

        Called by:
            - ``_invert_activities``

        Side effects:
            Removes existing child widgets from the splitter and inserts the new
            panel/graph pair.
        """
        while splitter.count():
            widget = splitter.widget(0)
            widget.setParent(None)
        splitter.addWidget(top_widget)
        splitter.addWidget(bottom_widget)
        splitter.setSizes([650, 300])

    def _center_traces(self):
        """Recompute the visible section for both activities.
 
        Called by:
            - ``ComparisonControlsPanel.center_traces_requested``
        """
        self.left_panel.refresh_visible_track()
        self.right_panel.refresh_visible_track()
 
    def _compute_common_segments(self):
        """Compute common segments between the two loaded tracks.

        Returns:
            list[dict]: Common segments or empty list if tracks are missing.
        """
        if self.left_panel.track is None or self.right_panel.track is None:
            return []
        left_visible = self.left_panel._visible_track()
        right_visible = self.right_panel._visible_track()
        if left_visible is None or right_visible is None:
            return []
        return find_common_segments(left_visible, right_visible)

    def _clear_common_segment_highlights(self):
        """Clear highlighted segments from both maps."""
        self._common_segments = []
        self._highlight_enabled = False
        self.left_panel.map.clear_highlighted_segments()
        self.right_panel.map.clear_highlighted_segments()
        self.controls_panel.set_highlight_common_segments_checked(False)
        self.controls_panel.set_segments_insight_enabled(False)

    def _on_highlight_common_segments_toggled(self, enabled: bool):
        """Handle highlight common segments toggle.

        Called by:
            - ``ComparisonControlsPanel.highlight_common_segments_toggled``
        """
        self._highlight_enabled = bool(enabled)
        if not self._highlight_enabled:
            self._clear_common_segment_highlights()
            return

        segments = self._compute_common_segments()
        self._common_segments = segments
        if not segments:
            QMessageBox.information(
                self,
                "Segmenti comuni",
                "Nessun segmento comune trovato con la soglia attuale (15 m).",
            )
            self._clear_common_segment_highlights()
            return

        self.left_panel.map.draw_highlighted_segments(segments)
        self.right_panel.map.draw_highlighted_segments(segments)
        self.controls_panel.set_segments_insight_enabled(True)

    def _on_show_segments_insight(self):
        """Open the insight dialog for the current common segments.

        Called by:
            - ``ComparisonControlsPanel.show_segments_insight_requested``
        """
        if not self._common_segments:
            segments = self._compute_common_segments()
            self._common_segments = segments
            if not segments:
                QMessageBox.information(
                    self,
                    "Segmenti comuni",
                    "Nessun segmento comune disponibile per l'analisi.",
                )
                return

        left_visible = self.left_panel._visible_track()
        right_visible = self.right_panel._visible_track()
        dialog = InsightDialog(
            self._common_segments,
            name_a=self.left_panel.title,
            name_b=self.right_panel.title,
            track_a=left_visible,
            track_b=right_visible,
            parent=self,
        )
        dialog.segment_point_selected.connect(self._on_segment_point_selected)
        dialog.exec()

    def _on_show_strava_segments(self):
        """Open the Strava segments selection dialog.

        Called by:
            - ``ComparisonControlsPanel.show_strava_segments_requested``
        """
        if not self._strava_segments:
            folder_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "..", "Strava_Segments"
            )
            self._strava_segments = load_strava_segments(folder_path)

        if not self._strava_segments:
            QMessageBox.information(
                self,
                "Segmenti Strava",
                "Nessun segmento Strava disponibile. Inserisci i file GPX nella cartella Strava_Segments.",
            )
            return

        left_track = self.left_panel.track
        right_track = self.right_panel.track

        if not left_track and not right_track:
            QMessageBox.information(
                self,
                "Segmenti Strava",
                "Carica almeno una traccia per cercare i segmenti Strava.",
            )
            return

        self._strava_occurrences = []
        if left_track:
            self._strava_occurrences.extend(
                find_strava_segments_in_track(self._strava_segments, left_track)
            )
        if right_track:
            self._strava_occurrences.extend(
                find_strava_segments_in_track(self._strava_segments, right_track)
            )

        if not self._strava_occurrences:
            QMessageBox.information(
                self,
                "Segmenti Strava",
                "Nessun segmento Strava trovato nelle tracce caricate.",
            )
            return

        # Highlight found segments on maps
        self._highlight_strava_segments_on_maps()

        dialog = StravaSegmentsDialog(self._strava_occurrences, parent=self)
        dialog.comparison_requested.connect(self._on_strava_comparison_requested)
        dialog.exec()

    def _on_strava_comparison_requested(self, occurrences: list[dict]):
        """Open detailed comparison dialog for two Strava segment occurrences.

        Called by:
            - ``StravaSegmentsDialog.comparison_requested``
        """
        if len(occurrences) != 2:
            return

        occ1, occ2 = occurrences

        # Create a segment dict compatible with InsightDialog
        segment = {
            "id": occ1["segment_name"],
            "a_start_idx": occ1["start_idx"],
            "a_end_idx": occ1["end_idx"],
            "b_start_idx": occ2["start_idx"],
            "b_end_idx": occ2["end_idx"],
            "a_start_dist_m": occ1["start_dist_m"],
            "a_end_dist_m": occ1["end_dist_m"],
            "b_start_dist_m": occ2["start_dist_m"],
            "b_end_dist_m": occ2["end_dist_m"],
            "length_m": max(occ1["length_m"], occ2["length_m"]),
            "coords_a": occ1.get("coords", []),
            "coords_b": occ2.get("coords", []),
            "time_a_sec": occ1.get("time_sec"),
            "time_b_sec": occ2.get("time_sec"),
            "avg_speed_a": occ1.get("avg_speed"),
            "avg_speed_b": occ2.get("avg_speed"),
            "slope_a": occ1.get("slope"),
            "slope_b": occ2.get("slope"),
        }

        dialog = InsightDialog(
            [segment],
            name_a=occ1["track_name"],
            name_b=occ2["track_name"],
            track_a=occ1["track"],
            track_b=occ2["track"],
            parent=self,
        )
        dialog.segment_point_selected.connect(self._on_segment_point_selected)
        dialog.exec()

    def _highlight_strava_segments_on_maps(self):
        """Draw found Strava segments on both maps."""
        for panel in (self.left_panel, self.right_panel):
            if panel.map:
                panel.map.draw_highlighted_segments([
                    {"coords_a": occ.get("coords", []), "coords_b": []}
                    for occ in self._strava_occurrences
                ])

    def _clear_strava_segment_highlights(self):
        """Remove Strava segment highlights from both maps."""
        self._strava_occurrences = []
        for panel in (self.left_panel, self.right_panel):
            if panel.map:
                panel.map.clear_highlighted_segments()

    def _apply_fullscreen_mode(self, mode):
        """Show one side in fullscreen and hide the other."""
        self._fullscreen_mode = mode
        if mode == "left":
            self.left_splitter.show()
            self.right_splitter.hide()
        elif mode == "right":
            self.left_splitter.hide()
            self.right_splitter.show()
        else:
            self.left_splitter.show()
            self.right_splitter.show()
 
        self.controls_panel.set_fullscreen_state(mode)
 
    def _on_left_fullscreen_toggled(self, enabled: bool):
        """Handle left-side fullscreen requests."""
        if enabled:
            self._apply_fullscreen_mode("left")
        elif self._fullscreen_mode == "left":
            self._apply_fullscreen_mode(None)
 
    def _on_right_fullscreen_toggled(self, enabled: bool):
        """Handle right-side fullscreen requests."""
        if enabled:
            self._apply_fullscreen_mode("right")
        elif self._fullscreen_mode == "right":
            self._apply_fullscreen_mode(None)
 
    def _check_sync_controls_availability(self):
        """Enable sync controls only if both tracks are loaded."""
        both_loaded = self.left_panel.track is not None and self.right_panel.track is not None
        left_loaded = self.left_panel.track is not None
        right_loaded = self.right_panel.track is not None
        self.controls_panel.set_sync_controls_enabled(both_loaded)
        self.controls_panel.set_fullscreen_buttons_enabled(left_loaded, right_loaded)
        self.controls_panel.set_strava_segments_enabled(left_loaded or right_loaded)
        if self._fullscreen_mode == "left" and not left_loaded:
            self._apply_fullscreen_mode(None)
        if self._fullscreen_mode == "right" and not right_loaded:
            self._apply_fullscreen_mode(None)
        self._clear_common_segment_highlights()
        self._clear_strava_segment_highlights()

    def _toggle_graphs(self, visible: bool):
        """Show or hide both graph panels.

        Called by:
            - ``ComparisonControlsPanel.toggle_graphs_requested``

        Args:
            visible: Desired visibility state.
        """
        self._graphs_visible = bool(visible)
        self.left_graph.setVisible(visible)
        self.right_graph.setVisible(visible)
    
    def _on_graph_point_hovered(self, source_panel, point_index, x_value, y_value):
        """Handle graph point hover events and update the corresponding map.
        
        Called by:
            - ``GraphPanel.point_hovered`` signal
        
        Args:
            source_panel: The panel that emitted the hover event
            point_index: Index of the hovered point in the track
            x_value: X value (time) of the hovered point
            y_value: Y value (speed) of the hovered point
        """
        if not hasattr(source_panel, "map") or source_panel.map is None:
            return
        
        # Update the map with the hovered point marker
        source_panel.map.set_hovered_point(point_index)
    def _on_segment_point_selected(self, track, point_index):
        """Handle point selection from the segment detail dialog.

        Called by:
            - ``InsightDialog.segment_point_selected`` signal

        Args:
            track: "A" or "B"
            point_index: Index of the selected point in the original track
        """
        if track == "A":
            panel = self.left_panel
            graph = self.left_graph
        else:
            panel = self.right_panel
            graph = self.right_graph

        if panel and panel.map and panel.track:
            if 0 <= point_index < len(panel.track.points):
                panel.map.set_hovered_point(point_index)
                if graph:
                    graph.set_hovered_point_by_index(point_index)

    def _on_map_ready(self):
        """Track map readiness and emit fullyReady when both maps are loaded."""
        self._maps_ready_count += 1
        if self._maps_ready_count >= 2:
            self.fullyReady.emit()

