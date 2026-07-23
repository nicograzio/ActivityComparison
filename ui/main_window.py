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

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMainWindow, QWidget, QHBoxLayout, QSplitter

from ui.comparison_controls_panel import ComparisonControlsPanel
from ui.track_panel import TrackPanel
from ui.graph_panel import GraphPanel
from core.analyzer import calculate_speed_series


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
        self._graphs_visible = True

        central = QWidget()
        main_layout = QHBoxLayout(central)

        self.left_panel = TrackPanel("Activity A")
        self.right_panel = TrackPanel("Activity B")
        self.controls_panel = ComparisonControlsPanel()
        self.left_graph = GraphPanel()
        self.right_graph = GraphPanel()

        self.left_panel.visible_track_changed.connect(
            lambda track, graph=self.left_graph: self._update_graph(graph, track)
        )
        self.right_panel.visible_track_changed.connect(
            lambda track, graph=self.right_graph: self._update_graph(graph, track)
        )

        self.left_splitter = self._build_side_splitter(self.left_panel, self.left_graph)
        self.right_splitter = self._build_side_splitter(self.right_panel, self.right_graph)

        self.controls_panel.sync_maps_toggled.connect(self._on_sync_maps_toggled)
        self.controls_panel.sync_speed_scale_requested.connect(self._sync_speed_scales)
        self.controls_panel.invert_activities_requested.connect(self._invert_activities)
        self.controls_panel.center_traces_requested.connect(self._center_traces)
        self.controls_panel.toggle_graphs_requested.connect(self._toggle_graphs)

        self._connect_map_sync(self.left_panel, self.right_panel)

        maps_splitter = QSplitter(Qt.Orientation.Horizontal)
        maps_splitter.addWidget(self.left_splitter)
        maps_splitter.addWidget(self.controls_panel)
        maps_splitter.addWidget(self.right_splitter)
        maps_splitter.setStretchFactor(0, 1)
        maps_splitter.setStretchFactor(1, 0)
        maps_splitter.setStretchFactor(2, 1)

        main_layout.addWidget(maps_splitter)
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
            and hasattr(map_widget, "viewChanged")
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
        state = source_panel.map.get_view_state()
        if not state:
            return
        self._syncing_maps = True
        try:
            target_panel.map.set_view_state(state)
        finally:
            self._syncing_maps = False

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
        self._syncing_maps = True
        try:
            target_panel.map.set_view_state(state)
        finally:
            self._syncing_maps = False

    def _sync_speed_scales(self):
        """Apply a shared speed scale across both activities.

        Called by:
            - ``ComparisonControlsPanel.sync_speed_scale_requested``

        Side effects:
            Reads the current visible range from both panels, computes a shared
            minimum/maximum and re-renders both tracks.
        """
        ranges = []
        for panel in (self.left_panel, self.right_panel):
            minimum, maximum = panel.visible_speed_range()
            if minimum is not None and maximum is not None:
                ranges.append((minimum, maximum))
        if not ranges:
            return
        shared_min = min(value for value, _ in ranges)
        shared_max = max(value for _, value in ranges)
        for panel in (self.left_panel, self.right_panel):
            panel.set_color_mode("Velocità")
            panel.set_speed_scale_limits(shared_min, shared_max)

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

        times, speeds = calculate_speed_series(track)
        if not times or not speeds:
            graph.clear_graph()
            return

        graph.setVisible(True)
        graph.set_series(times, speeds, "Velocità")

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