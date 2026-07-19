from PyQt6.QtWidgets import QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QSplitter

from ui.comparison_controls_panel import ComparisonControlsPanel
from ui.track_panel import TrackPanel
from ui.graph_panel import GraphPanel
from core.analyzer import calculate_point_speed


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Activity Comparison")

        central = QWidget()
        main_layout = QHBoxLayout(central)

        self.left_panel = TrackPanel("Activity A")
        self.right_panel = TrackPanel("Activity B")
        self.controls_panel = ComparisonControlsPanel()
        self.left_graph = GraphPanel()
        self.right_graph = GraphPanel()

        self.left_panel.activity_loaded.connect(lambda track: self._update_graph(self.left_graph, self.left_panel.track))
        self.right_panel.activity_loaded.connect(lambda track: self._update_graph(self.right_graph, self.right_panel.track))
        self.left_panel.visible_track_changed.connect(lambda track: self._update_graph(self.left_graph, track))
        self.right_panel.visible_track_changed.connect(lambda track: self._update_graph(self.right_graph, track))

        left_container = QWidget()
        left_layout = QVBoxLayout(left_container)
        left_layout.addWidget(self.left_panel, 3)
        left_layout.addWidget(self.left_graph, 2)

        right_container = QWidget()
        right_layout = QVBoxLayout(right_container)
        right_layout.addWidget(self.right_panel, 3)
        right_layout.addWidget(self.right_graph, 2)

        self.controls_panel.sync_maps_toggled.connect(self._on_sync_maps_toggled)
        self.controls_panel.sync_speed_scale_requested.connect(self._sync_speed_scales)
        self.controls_panel.invert_activities_requested.connect(self._invert_activities)
        self.controls_panel.center_traces_requested.connect(self._center_traces)
        self.controls_panel.toggle_graphs_requested.connect(self._toggle_graphs)

        maps_splitter = QSplitter()
        maps_splitter.addWidget(left_container)
        maps_splitter.addWidget(self.controls_panel)
        maps_splitter.addWidget(right_container)
        maps_splitter.setStretchFactor(0, 1)
        maps_splitter.setStretchFactor(1, 0)
        maps_splitter.setStretchFactor(2, 1)

        main_layout.addWidget(maps_splitter)
        central.setLayout(main_layout)
        self.setCentralWidget(central)

    def _update_graph(self, graph, track):
        if not track or not getattr(track, "points", None):
            graph.clear_graph()
            return

        points = track.points
        if len(points) < 2:
            graph.clear_graph()
            return

        times = []
        speeds = []

        for index, point in enumerate(points):
            times.append(float(index))
            if index == 0:
                speeds.append(0.0)
                continue

            speed = calculate_point_speed(points[index - 1], point)
            speeds.append(0.0 if speed is None else speed)

        graph.setVisible(True)
        graph.set_series(times, speeds, "Velocità")

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
        self.left_graph.setVisible(visible)
        self.right_graph.setVisible(visible)
