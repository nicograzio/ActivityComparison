"""Fallback raster map renderer based on OpenStreetMap tiles.

Used when the MapLibre/WebEngine renderer is not available. It draws the track
as a colored polyline on top of a tile-based map.

Called by:
    - ``ui.track_panel.TrackPanel`` when the preferred renderer cannot load

Consumes:
    - ``core.analyzer.calculate_point_speed``
    - ``core.analyzer.haversine_distance``
    - ``core.colorizer.value_to_color``
"""

import math
import os
import requests

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPen, QPixmap, QPainterPath, QPainter
from PyQt6.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsPathItem, QGraphicsPixmapItem

from core.colorizer import value_to_color
from core.analyzer import calculate_point_speed


class MapWidget(QGraphicsView):
    """Raster activity map with OSM tiles and a track polyline."""

    TILE_SIZE = 256
    viewChanged = pyqtSignal(dict)

    def __init__(self):
        """Create the graphics scene, tile cache and interaction settings.

        Called by:
            - ``TrackPanel`` as a fallback renderer
        """
        super().__init__()
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.zoom_factor = 1.15
        self.zoom_level = 15
        self.track_items = []
        self.tile_items = []
        self.cache_dir = "cache/osm"
        self.show_points = False
        os.makedirs(self.cache_dir, exist_ok=True)

    def _emit_view_changed(self):
        """Emit the current camera state to listeners."""
        self.viewChanged.emit(self.get_view_state())

    def get_view_state(self):
        """Return the current scene center and zoom state.

        Called by:
            - ``MainWindow._copy_map_view``
            - ``MainWindow._on_map_view_changed``

        Returns:
            dict: Camera state with center, scale and zoom.
        """
        center = self.mapToScene(self.viewport().rect().center())
        transform = self.transform()
        return {
            "center": [round(center.x(), 3), round(center.y(), 3)],
            "scale": round(transform.m11(), 4),
            "zoom": self.zoom_level,
        }

    def set_view_state(self, state):
        """Apply a saved camera state to the raster map.

        Called by:
            - ``MainWindow._copy_map_view``
            - ``MainWindow._on_map_view_changed``
        """
        if not isinstance(state, dict):
            return
        center = state.get("center")
        scale = state.get("scale")
        if not isinstance(center, (list, tuple)) or len(center) != 2:
            return
        try:
            cx = float(center[0])
            cy = float(center[1])
            scale = float(scale) if scale is not None else self.transform().m11()
        except Exception:
            return

        self.resetTransform()
        self.scale(scale, scale)
        self.centerOn(cx, cy)
        self._emit_view_changed()

    def wheelEvent(self, event):
        """Zoom in or out around the mouse position.

        Called by:
            - Qt mouse wheel events
        """
        factor = self.zoom_factor if event.angleDelta().y() > 0 else 1 / self.zoom_factor
        self.scale(factor, factor)
        self._emit_view_changed()

    def mouseReleaseEvent(self, event):
        """Notify listeners after a drag pan completes."""
        super().mouseReleaseEvent(event)
        self._emit_view_changed()

    def geo_to_pixel(self, lat, lon, zoom):
        """Project latitude/longitude to OSM tile pixels.

        Called by:
            - tile loading and track drawing routines
        """
        size = self.TILE_SIZE * (2 ** zoom)
        return ((lon + 180) / 360 * size,
                (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * size)

    def clear_track(self):
        """Remove all rendered track items from the scene."""
        for item in self.track_items:
            self.scene.removeItem(item)
        self.track_items.clear()

    def clear_tiles(self):
        """Remove all tile items from the scene."""
        for item in self.tile_items:
            self.scene.removeItem(item)
        self.tile_items.clear()

    def add_tile(self, x, y, zoom, px, py):
        """Load a single OSM tile and place it in the scene.

        Called by:
            - ``load_map_area``
        """
        try:
            cache = os.path.join(self.cache_dir, f"{zoom}_{x}_{y}.png")
            pixmap = QPixmap()
            if os.path.exists(cache):
                pixmap.load(cache)
            else:
                r = requests.get(f"https://tile.openstreetmap.org/{zoom}/{x}/{y}.png", headers={"User-Agent": "ActivityComparison/1.0"}, timeout=10)
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
        except Exception:
            pass

    def load_map_area(self, lat, lon):
        """Load a 7x7 tile area around a geographic center."""
        self.clear_tiles()
        cx, cy = self.geo_to_pixel(lat, lon, self.zoom_level)
        tx, ty = int(cx / self.TILE_SIZE), int(cy / self.TILE_SIZE)
        for x in range(tx - 3, tx + 4):
            for y in range(ty - 3, ty + 4):
                self.add_tile(x, y, self.zoom_level, x * self.TILE_SIZE - cx, y * self.TILE_SIZE - cy)

    def calculate_zoom(self, min_lat, max_lat, min_lon, max_lon):
        """Choose a zoom level from the track bounding box size."""
        span = max(max_lat - min_lat, max_lon - min_lon)
        if span > 2: return 8
        if span > 1: return 10
        if span > 0.5: return 12
        if span > 0.1: return 13
        if span > 0.03: return 14
        if span > 0.005: return 15
        return 16

    def get_segment_value(self, previous, point, color_mode):
        """Return the color metric for a track segment."""
        if color_mode == "Velocità":
            return calculate_point_speed(previous, point)
        return None

    def draw_track(self, track, color_mode="Nessuna", minimum=None, maximum=None):
        """Render a track as colored line segments over the tile scene.

        Called by:
            - ``TrackPanel._render_visible_track``
        """
        old_transform = self.transform()
        old_center = self.mapToScene(self.viewport().rect().center())
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
            previous = track.points[i - 1]
            point = track.points[i]
            x1, y1 = self.geo_to_pixel(previous.latitude, previous.longitude, self.zoom_level)
            x2, y2 = self.geo_to_pixel(point.latitude, point.longitude, self.zoom_level)

            path = QPainterPath()
            path.moveTo(x1 - ox, y1 - oy)
            path.lineTo(x2 - ox, y2 - oy)

            color = Qt.GlobalColor.gray
            if color_mode == "Velocità":
                value = self.get_segment_value(previous, point, color_mode)
                if value is not None:
                    color = value_to_color(value, minimum, maximum)

            item = QGraphicsPathItem(path)
            item.setPen(QPen(color, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            item.setZValue(10)
            self.scene.addItem(item)
            self.track_items.append(item)

        self.setTransform(old_transform)
        self.centerOn(old_center)
        self._emit_view_changed()