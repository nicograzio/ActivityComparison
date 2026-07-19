from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMainWindow, QWidget, QHBoxLayout, QSplitter

from ui.comparison_controls_panel import ComparisonControlsPanel
from ui.track_panel import TrackPanel
from ui.graph_panel import GraphPanel
from core.analyzer import calculate_speed_series


class MainWindow(QMainWindow):
    def __init__(self):
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
        map_widget = getattr(panel, "map", None)
        return bool(
            map_widget is not None
            and hasattr(map_widget, "viewChanged")
            and hasattr(map_widget, "get_view_state")
            and hasattr(map_widget, "set_view_state")
        )

    def _connect_map_sync(self, left_panel, right_panel):
        if self._map_supports_view_sync(left_panel):
            left_panel.map.viewChanged.connect(
                lambda state: self._on_map_view_changed(left_panel, state)
            )
        if self._map_supports_view_sync(right_panel):
            right_panel.map.viewChanged.connect(
                lambda state: self._on_map_view_changed(right_panel, state)
            )

    def _on_sync_maps_toggled(self, enabled: bool):
        self._sync_maps_enabled = bool(enabled)
        if self._sync_maps_enabled:
            self._copy_map_view(self.left_panel, self.right_panel)

    def _copy_map_view(self, source_panel, target_panel):
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
        self.left_panel, self.right_panel = self.right_panel, self.left_panel
        self.left_graph, self.right_graph = self.right_graph, self.left_graph

        self._reset_splitter(self.left_splitter, self.left_panel, self.left_graph)
        self._reset_splitter(self.right_splitter, self.right_panel, self.right_graph)

    @staticmethod
    def _reset_splitter(splitter, top_widget, bottom_widget):
        while splitter.count():
            widget = splitter.widget(0)
            widget.setParent(None)
        splitter.addWidget(top_widget)
        splitter.addWidget(bottom_widget)
        splitter.setSizes([650, 300])

    def _center_traces(self):
        self.left_panel.refresh_visible_track()
        self.right_panel.refresh_visible_track()

    def _toggle_graphs(self, visible: bool):
        self._graphs_visible = bool(visible)
        self.left_graph.setVisible(visible)
        self.right_graph.setVisible(visible)
