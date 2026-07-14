import math
import requests

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPen, QPainterPath, QPixmap
from PyQt6.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsPathItem, QGraphicsPixmapItem


class MapWidget(QGraphicsView):
    """Interactive map widget with OSM tile foundation."""

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
        self.tile_items = []

        self.setBackgroundBrush(Qt.GlobalColor.lightGray)

    def wheelEvent(self, event):
        if event.angleDelta().y() > 0:
            self.scale(self.zoom_factor, self.zoom_factor)
        else:
            self.scale(1 / self.zoom_factor, 1 / self.zoom_factor)

    def clear_track(self):
        if self.track_item:
            self.scene.removeItem(self.track_item)
            self.track_item = None

    def clear_tiles(self):
        for item in self.tile_items:
            self.scene.removeItem(item)
        self.tile_items.clear()

    def add_osm_tile(self, x, y, zoom, px, py):
        url = f"https://tile.openstreetmap.org/{zoom}/{x}/{y}.png"

        try:
            response = requests.get(url, timeout=5)
            response.raise_for_status()

            pixmap = QPixmap()
            pixmap.loadFromData(response.content)

            item = QGraphicsPixmapItem(pixmap)
            item.setPos(px, py)
            self.scene.addItem(item)
            self.tile_items.append(item)
        except Exception:
            pass

    def load_tiles_for_point(self, latitude, longitude, zoom=15):
        self.clear_tiles()

        n = 2 ** zoom
        x = int((longitude + 180) / 360 * n)
        y = int((1 - math.asinh(math.tan(math.radians(latitude))) / math.pi) / 2 * n)

        self.add_osm_tile(x, y, zoom, -128, -128)

    def draw_track(self, track):
        self.clear_track()

        if len(track.points) < 2:
            return

        self.load_tiles_for_point(
            track.points[0].latitude,
            track.points[0].longitude
        )

        path = QPainterPath()
        points = track.points

        origin_lat = points[0].latitude
        origin_lon = points[0].longitude
        scale = 100000

        path.moveTo(0, 0)

        for point in points[1:]:
            path.lineTo(
                (point.longitude - origin_lon) * scale,
                -(point.latitude - origin_lat) * scale
            )

        self.track_item = QGraphicsPathItem(path)
        self.track_item.setPen(QPen(Qt.GlobalColor.blue, 3))

        self.scene.addItem(self.track_item)
        self.fitInView(
            self.track_item,
            Qt.AspectRatioMode.KeepAspectRatio
        )
