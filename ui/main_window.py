from PyQt6.QtWidgets import QMainWindow, QWidget, QHBoxLayout, QSplitter

from ui.comparison_controls_panel import ComparisonControlsPanel
from ui.track_panel import TrackPanel
from ui.graph_panel import GraphPanel


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Activity Comparison")

        central = QWidget()
        main_layout = QHBoxLayout(central)

        self.left_panel = TrackPanel("Activity A")
        self.right_panel = TrackPanel("Activity B")
        self.controls_panel = ComparisonControlsPanel()
        self.graph_panel = GraphPanel()

        self.left_panel.activity_loaded.connect(lambda track: self._update_graph(self.left_panel))
        self.right_panel.activity_loaded.connect(lambda track: self._update_graph(self.right_panel))

        self.controls_panel.sync_maps_toggled.connect(self._on_sync_maps_toggled)
        self.controls_panel.sync_speed_scale_requested.connect(self._sync_speed_scales)
        self.controls_panel.invert_activities_requested.connect(self._invert_activities)
        self.controls_panel.center_traces_requested.connect(self._center_traces)
        self.controls_panel.toggle_graphs_requested.connect(self._toggle_graphs)

        maps_container = QWidget()
        maps_layout = QHBoxLayout(maps_container)
        maps_layout.addWidget(self.left_panel, 1)
        maps_layout.addWidget(self.controls_panel, 0)
        maps_layout.addWidget(self.right_panel, 1)

        splitter = QSplitter()
        splitter.setOrientation(__import__('PyQt6.QtCore', fromlist=['Qt']).Qt.Orientation.Vertical)
        splitter.addWidget(maps_container)
        splitter.addWidget(self.graph_panel)
        splitter.setSizes([700, 250])

        main_layout.addWidget(splitter)
        central.setLayout(main_layout)
        self.setCentralWidget(central)

    def _update_graph(self, panel):
        if not panel.track:
            return

        points = panel.track.points
        if not points:
            return

        times = [
            getattr(point, "timestamp", index)
            for index, point in enumerate(points)
        ]
        speeds = [
            getattr(point, "speed", 0) or 0
            for point in points
        ]

        self.graph_panel.set_series(times, speeds, "Velocità")

    def _on_sync_maps_toggled(self, enabled):
        pass

    def _sync_speed_scales(self):
        pass

    def _invert_activities(self):
        self.left_panel, self.right_panel = self.right_panel, self.left_panel

    def _center_traces(self):
        self.left_panel.refresh_visible_track()
        self.right_panel.refresh_visible_track()

    def _toggle_graphs(self, visible):
        self.graph_panel.setVisible(visible)
