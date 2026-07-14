import math
import os
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
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)

        self.zoom_factor = 1.15
        self.track_item = None
        self.tile_items = []
        self.zoom_level = 15
        self.cache_dir = "cache/osm"
        self.current_track = None

        os.makedirs(self.cache_dir, exist_ok=True)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.current_track:
            self.fit_track()

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

    def calculate_zoom(self, min_lat, max_lat, min_lon, max_lon):
        """Calculate initial map zoom from track bounding box."""
        latitude_span = max_lat - min_lat
        longitude_span = max_lon - min_lon
        max_span = max(latitude_span, longitude_span)

        if max_span > 2:
            return 8
        if max_span > 1:
            return 10
        if max_span > 0.5:
            return 12
        if max_span > 0.1:
            return 13
        if max_span > 0.03:
            return 14
        if max_span > 0.01:
            return 15
        return 16

    def add_tile(self, x, y, zoom, px, py):
        cache_file = os.path.join(self.cache_dir, f"{zoom}_{x}_{y}.png")

        try:
            pixmap = QPixmap()

            if os.path.exists(cache_file):
                pixmap.load(cache_file)
            else:
                url = f"https://tile.openstreetmap.org/{zoom}/{x}/{y}.png"
                response = requests.get(
                    url,
                    headers={"User-Agent": "ActivityComparison/1.0"},
                    timeout=10
                )
                response.raise_for_status()

                with open(cache_file, "wb") as file:
                    file.write(response.content)

                pixmap.loadFromData(response.content)

            if pixmap.isNull():
                return

            item = QGraphicsPixmapItem(pixmap)
            item.setPos(px, py)
            item.setZValue(0)
            self.scene.addItem(item)
            self.tile_items.append(item)

        except Exception as error:
            print(f"Errore caricamento tile {x}/{y}: {error}")

    def load_map_area(self, latitude, longitude):
        self.clear_tiles()

        center_x, center_y = self.geo_to_pixel(latitude, longitude, self.zoom_level)
        tile_x = int(center_x // self.TILE_SIZE)
        tile_y = int(center_y // self.TILE_SIZE)

        for x in range(tile_x - 3, tile_x + 4):
            for y in range(tile_y - 3, tile_y + 4):
                self.add_tile(
                    x,
                    y,
                    self.zoom_level,
                    x * self.TILE_SIZE - center_x,
                    y * self.TILE_SIZE - center_y
                )

    def fit_track(self):
        if self.track_item:
            self.fitInView(
                self.track_item.boundingRect().adjusted(-300, -300, 300, 300),
                Qt.AspectRatioMode.KeepAspectRatio
            )

    def draw_track(self, track):
        self.current_track = track
        self.clear_track()

        if len(track.points) < 2:
            return

        min_lat = min(p.latitude for p in track.points)
        max_lat = max(p.latitude for p in track.points)
        min_lon = min(p.longitude for p in track.points)
        max_lon = max(p.longitude for p in track.points)

        self.zoom_level = self.calculate_zoom(
            min_lat,
            max_lat,
            min_lon,
            max_lon
        )

        center_lat = (min_lat + max_lat) / 2
        center_lon = (min_lon + max_lon) / 2

        self.load_map_area(center_lat, center_lon)

        path = QPainterPath()
        origin_x, origin_y = self.geo_to_pixel(center_lat, center_lon, self.zoom_level)

        first = True
        for point in track.points:
            x, y = self.geo_to_pixel(point.latitude, point.longitude, self.zoom_level)

            if first:
                path.moveTo(x - origin_x, y - origin_y)
                first = False
            else:
                path.lineTo(x - origin_x, y - origin_y)

        self.track_item = QGraphicsPathItem(path)
        self.track_item.setPen(QPen(Qt.GlobalColor.blue, 4))
        self.track_item.setZValue(10)
        self.scene.addItem(self.track_item)

        self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-500, -500, 500, 500))

        self.fit_track()
