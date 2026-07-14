from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPen, QPainterPath
from PyQt6.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsPathItem


class MapWidget(QGraphicsView):
    """
    Native Qt map widget.

    Prepared for:
    - OpenStreetMap background tiles
    - GPS track rendering
    - segment coloring
    - interactive navigation
    """

    def __init__(self):
        super().__init__()

        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)

        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(
            QGraphicsView.ViewportAnchor.AnchorUnderMouse
        )
        self.setResizeAnchor(
            QGraphicsView.ViewportAnchor.AnchorViewCenter
        )

        self.zoom_factor = 1.15
        self.track_item = None

        self.setBackgroundBrush(Qt.GlobalColor.lightGray)

    def wheelEvent(self, event):
        """Zoom centered on mouse position."""
        if event.angleDelta().y() > 0:
            self.scale(self.zoom_factor, self.zoom_factor)
        else:
            self.scale(1 / self.zoom_factor, 1 / self.zoom_factor)

    def clear_track(self):
        if self.track_item:
            self.scene.removeItem(self.track_item)
            self.track_item = None

    def draw_track(self, track):
        self.clear_track()

        if len(track.points) < 2:
            return

        path = QPainterPath()
        points = track.points

        origin_lat = points[0].latitude
        origin_lon = points[0].longitude

        scale = 100000

        path.moveTo(
            (points[0].longitude - origin_lon) * scale,
            -(points[0].latitude - origin_lat) * scale
        )

        for point in points[1:]:
            path.lineTo(
                (point.longitude - origin_lon) * scale,
                -(point.latitude - origin_lat) * scale
            )

        self.track_item = QGraphicsPathItem(path)
        self.track_item.setPen(
            QPen(Qt.GlobalColor.blue, 3)
        )

        self.scene.addItem(self.track_item)
        self.fitInView(
            self.track_item,
            Qt.AspectRatioMode.KeepAspectRatio
        )

    def mousePressEvent(self, event):
        """Enable map panning with drag."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        super().mousePressEvent(event)
