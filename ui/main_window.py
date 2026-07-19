from PyQt6.QtWidgets import QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QSplitter

from ui.comparison_controls_panel import ComparisonControlsPanel
from ui.track_panel import TrackPanel
from ui.graph_panel import GraphPanel


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
        self.graph_panel = GraphPanel()

        self.controls_panel.sync_maps_toggled.connect(self._on_sync_maps_toggled)
        self.controls_panel.sync_speed_scale_requested.connect(self._sync_speed_scales)
        self.controls_panel.invert_activities_requested.connect(self._invert_activities)
        self.controls_panel.center_traces_requested.connect(self._center_traces)
        self.controls_panel.toggle_graphs_requested.connect(self._toggle_graphs)

        self._connect_map_sync(self.left_panel, self.right_panel)

        maps_splitter = QSplitter()
        maps_container = QWidget()
        maps_layout = QHBoxLayout(maps_container)
        maps_layout.addWidget(self.left_panel, 1)
        maps_layout.addWidget(self.controls_panel, 0)
        maps_layout.addWidget(self.right_panel, 1)
        maps_container.setLayout(maps_layout)

        vertical_splitter = QSplitter()
        vertical_splitter.setOrientation(__import__('PyQt6.QtCore', fromlist=['Qt']).Qt.Orientation.Vertical)
        vertical_splitter.addWidget(maps_container)
        vertical_splitter.addWidget(self.graph_panel)
        vertical_splitter.setSizes([700, 250])

        main_layout.addWidget(vertical_splitter)

        central.setLayout(main_layout)
        self.setCentralWidget(central)

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
            left_panel.map.viewChanged.connect(lambda state: self._on_map_view_changed(left_panel, state))
        if self._map_supports_view_sync(right_panel):
            right_panel.map.viewChanged.connect(lambda state: self._on_map_view_changed(right_panel, state))

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
        if self._map_supports_view_sync(target_panel):
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

    def _invert_activities(self):
        self.left_panel, self.right_panel = self.right_panel, self.left_panel

    def _center_traces(self):
        self.left_panel.refresh_visible_track()
        self.right_panel.refresh_visible_track()

    def _toggle_graphs(self, visible: bool):
        self._graphs_visible = bool(visible)
        self.graph_panel.setVisible(visible)
