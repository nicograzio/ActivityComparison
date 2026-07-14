import math
import os
import requests

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPen, QPainterPath, QPixmap
from PyQt6.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsPathItem, QGraphicsPixmapItem


class MapWidget(QGraphicsView):
    TILE_SIZE = 256

    def __init__(self):
        super().__init__()
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)

        self.zoom_factor = 1.15
        self.zoom_level = 15
        self.track_item = None
        self.tile_items = []
        self.current_track = None
        self.cache_dir = "cache/osm"
        os.makedirs(self.cache_dir, exist_ok=True)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.track_item:
            self.fit_track()

    def wheelEvent(self, event):
        factor = self.zoom_factor if event.angleDelta().y() > 0 else 1 / self.zoom_factor
        self.scale(factor, factor)

    def geo_to_pixel(self, lat, lon, zoom):
        size = self.TILE_SIZE * (2 ** zoom)
        return (
            (lon + 180) / 360 * size,
            (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * size
        )

    def calculate_zoom(self, min_lat, max_lat, min_lon, max_lon):
        span = max(max_lat - min_lat, max_lon - min_lon)
        if span > 2:
            return 8
        if span > 1:
            return 10
        if span > 0.5:
            return 12
        if span > 0.1:
            return 13
        if span > 0.03:
            return 14
        if span > 0.005:
            return 15
        return 16

    def clear_tiles(self):
        for item in self.tile_items:
            self.scene.removeItem(item)
        self.tile_items.clear()

    def add_tile(self, x, y, zoom, px, py):
        try:
            cache = os.path.join(self.cache_dir, f"{zoom}_{x}_{y}.png")
            pixmap = QPixmap()
            if os.path.exists(cache):
                pixmap.load(cache)
            else:
                url = f"https://tile.openstreetmap.org/{zoom}/{x}/{y}.png"
                r = requests.get(url, headers={"User-Agent": "ActivityComparison/1.0"}, timeout=10)
                r.raise_for_status()
                with open(cache, "wb") as f:
                    f.write(r.content)
                pixmap.loadFromData(r.content)

            if pixmap.isNull():
                return

            item = QGraphicsPixmapItem(pixmap)
            item.setPos(px, py)
            item.setZValue(0)
            self.scene.addItem(item)
            self.tile_items.append(item)
        except Exception as e:
            print("Tile error:", e)

    def load_map_area(self, lat, lon):
        self.clear_tiles()
        cx, cy = self.geo_to_pixel(lat, lon, self.zoom_level)
        tx, ty = int(cx / self.TILE_SIZE), int(cy / self.TILE_SIZE)
        for x in range(tx - 3, tx + 4):
            for y in range(ty - 3, ty + 4):
                self.add_tile(x, y, self.zoom_level, x * self.TILE_SIZE - cx, y * self.TILE_SIZE - cy)

    def fit_track(self):
        if self.track_item:
            self.fitInView(self.track_item, Qt.AspectRatioMode.KeepAspectRatio)

    def draw_track(self, track):
        self.current_track = track
        if len(track.points) < 2:
            return

        min_lat = min(p.latitude for p in track.points)
        max_lat = max(p.latitude for p in track.points)
        min_lon = min(p.longitude for p in track.points)
        max_lon = max(p.longitude for p in track.points)

        self.zoom_level = max(10, min(16, self.calculate_zoom(min_lat, max_lat, min_lon, max_lon)))

        center_lat = (min_lat + max_lat) / 2
        center_lon = (min_lon + max_lon) / 2
        self.load_map_area(center_lat, center_lon)

        origin_x, origin_y = self.geo_to_pixel(center_lat, center_lon, self.zoom_level)
        path = QPainterPath()

        first = True
        for p in track.points:
            x, y = self.geo_to_pixel(p.latitude, p.longitude, self.zoom_level)
            if first:
                path.moveTo(x - origin_x, y - origin_y)
                first = False
            else:
                path.lineTo(x - origin_x, y - origin_y)

        self.track_item = QGraphicsPathItem(path)
        self.track_item.setPen(QPen(Qt.GlobalColor.blue, 4))
        self.track_item.setZValue(10)
        self.scene.addItem(self.track_item)
        self.fit_track()
