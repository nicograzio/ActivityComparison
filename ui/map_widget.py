import math
import requests

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPen, QPainterPath, QPixmap
from PyQt6.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsPathItem, QGraphicsPixmapItem


class MapWidget(QGraphicsView):
    """Interactive GPS map with OpenStreetMap background."""

    TILE_SIZE = 256

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
        self.zoom_level = 15

    def wheelEvent(self, event):
        factor = self.zoom_factor if event.angleDelta().y() > 0 else 1 / self.zoom_factor
        self.scale(factor, factor)

    def clear_track(self):
        if self.track_item:
            self.scene.removeItem(self.track_item)
            self.track_item = None

    def clear_tiles(self):
        for item in self.tile_items:
            self.scene.removeItem(item)
        self.tile_items.clear()

    def geo_to_pixel(self, lat, lon, zoom):
        size = self.TILE_SIZE * (2 ** zoom)
        x = (lon + 180) / 360 * size
        y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * size
        return x, y

    def add_tile(self, x, y, zoom, px, py):
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

    def load_map_area(self, latitude, longitude):
        self.clear_tiles()

        zoom = self.zoom_level
        center_x, center_y = self.geo_to_pixel(latitude, longitude, zoom)

        tile_x = int(center_x // self.TILE_SIZE)
        tile_y = int(center_y // self.TILE_SIZE)

        for x in range(tile_x - 2, tile_x + 3):
            for y in range(tile_y - 2, tile_y + 3):
                self.add_tile(
                    x,
                    y,
                    zoom,
                    x * self.TILE_SIZE - center_x,
                    y * self.TILE_SIZE - center_y
                )

    def draw_track(self, track):
        self.clear_track()

        if len(track.points) < 2:
            return

        first = track.points[0]
        self.load_map_area(first.latitude, first.longitude)

        path = QPainterPath()
        zoom = self.zoom_level

        origin_x, origin_y = self.geo_to_pixel(
            first.latitude,
            first.longitude,
            zoom
        )

        path.moveTo(0, 0)

        for point in track.points[1:]:
            x, y = self.geo_to_pixel(
                point.latitude,
                point.longitude,
                zoom
            )
            path.lineTo(x - origin_x, y - origin_y)

        self.track_item = QGraphicsPathItem(path)
        self.track_item.setPen(QPen(Qt.GlobalColor.blue, 4))
        self.scene.addItem(self.track_item)

        self.fitInView(
            self.track_item,
            Qt.AspectRatioMode.KeepAspectRatio
        )
