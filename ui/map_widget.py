import math
import os
import requests

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPen, QPixmap
from PyQt6.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsLineItem, QGraphicsPixmapItem

from core.colorizer import value_to_color


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
        self.track_items = []
        self.tile_items = []
        self.cache_dir = "cache/osm"
        os.makedirs(self.cache_dir, exist_ok=True)

    def wheelEvent(self, event):
        factor = self.zoom_factor if event.angleDelta().y() > 0 else 1 / self.zoom_factor
        self.scale(factor, factor)

    def geo_to_pixel(self, lat, lon, zoom):
        size = self.TILE_SIZE * (2 ** zoom)
        return ((lon + 180) / 360 * size,
                (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * size)

    def clear_track(self):
        for item in self.track_items:
            self.scene.removeItem(item)
        self.track_items.clear()

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
                r = requests.get(
                    f"https://tile.openstreetmap.org/{zoom}/{x}/{y}.png",
                    headers={"User-Agent": "ActivityComparison/1.0"},
                    timeout=10
                )
                r.raise_for_status()
                with open(cache, "wb") as f:
                    f.write(r.content)
                pixmap.loadFromData(r.content)

            if not pixmap.isNull():
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

    def calculate_zoom(self, min_lat, max_lat, min_lon, max_lon):
        span = max(max_lat - min_lat, max_lon - min_lon)
        if span > 2: return 8
        if span > 1: return 10
        if span > 0.5: return 12
        if span > 0.1: return 13
        if span > 0.03: return 14
        if span > 0.005: return 15
        return 16

    def get_segment_value(self, point, color_mode):
        if color_mode == "Velocità":
            speed = getattr(point, "speed", None)
            if speed is None:
                return None
            return speed * 3.6

        if color_mode == "Pendenza":
            return None

        return None

    def draw_track(self, track, color_mode="Nessuna", minimum=None, maximum=None):
        self.clear_track()
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

        ox, oy = self.geo_to_pixel(center_lat, center_lon, self.zoom_level)

        for i in range(1, len(track.points)):
            a = track.points[i - 1]
            b = track.points[i]
            x1, y1 = self.geo_to_pixel(a.latitude, a.longitude, self.zoom_level)
            x2, y2 = self.geo_to_pixel(b.latitude, b.longitude, self.zoom_level)

            color = Qt.GlobalColor.blue
            value = self.get_segment_value(b, color_mode)

            if value is not None and minimum is not None and maximum is not None:
                color = value_to_color(value, minimum, maximum)

            item = QGraphicsLineItem(x1 - ox, y1 - oy, x2 - ox, y2 - oy)
            item.setPen(QPen(color, 4))
            item.setZValue(10)
            self.scene.addItem(item)
            self.track_items.append(item)

        self.fitInView(self.scene.itemsBoundingRect(), Qt.AspectRatioMode.KeepAspectRatio)
