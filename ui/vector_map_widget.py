"""
Vector map renderer foundation.

This module is the starting point for replacing raster OSM tiles with a
vector based renderer. The existing MapWidget remains untouched until the
new renderer is fully integrated.
"""

from PyQt6.QtWidgets import QGraphicsView, QGraphicsScene
from PyQt6.QtGui import QPainter


class VectorMapWidget(QGraphicsView):
    """Base class for the future vector map implementation."""

    def __init__(self):
        super().__init__()
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.track_layer = []

    def clear_map(self):
        self.scene.clear()
        self.track_layer.clear()

    def draw_track(self, track):
        """Placeholder for vector track rendering.

        The existing renderer is intentionally kept active until vector tile
        support is completed.
        """
        pass
