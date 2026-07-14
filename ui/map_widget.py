from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPen, QPainterPath
from PyQt6.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsPathItem


class MapWidget(QGraphicsView):

    def __init__(self):
        super().__init__()

        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)

        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(
            QGraphicsView.ViewportAnchor.AnchorUnderMouse
        )

        self.zoom_factor = 1.15
        self.track_item = None

        self.setBackgroundBrush(Qt.GlobalColor.lightGray)

    def wheelEvent(self, event):
        if event.angleDelta().y() > 0:
            self.scale(self.zoom_factor, self.zoom_factor)
        else:
            self.scale(1 / self.zoom_factor, 1 / self.zoom_factor)

    def draw_track(self, track):
        if self.track_item:
            self.scene.removeItem(self.track_item)

        if len(track.points) < 2:
            return

        path = QPainterPath()

        points = track.points

        origin_lat = points[0].latitude
        origin_lon = points[0].longitude

        scale = 100000

        first_x = (points[0].longitude - origin_lon) * scale
        first_y = -(points[0].latitude - origin_lat) * scale

        path.moveTo(first_x, first_y)

        for point in points[1:]:
            x = (point.longitude - origin_lon) * scale
            y = -(point.latitude - origin_lat) * scale
            path.lineTo(x, y)

        self.track_item = QGraphicsPathItem(path)
        self.track_item.setPen(
            QPen(Qt.GlobalColor.blue, 2)
        )

        self.scene.addItem(self.track_item)
        self.fitInView(
            self.track_item,
            Qt.AspectRatioMode.KeepAspectRatio
        )
