from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtGui import QPainter, QPen
from PyQt6.QtWidgets import QGraphicsView, QGraphicsScene


class MapWidget(QGraphicsView):
    """
    Native Qt map widget.

    This replaces the previous QtWebEngine/Folium implementation.
    It is designed to support later:
    - OpenStreetMap tiles
    - GPS track rendering
    - segment coloring
    - slope/speed visualization
    """

    def __init__(self):
        super().__init__()

        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)

        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(
            QGraphicsView.ViewportAnchor.AnchorUnderMouse
        )

        self.zoom_factor = 1.15

        self.setBackgroundBrush(Qt.GlobalColor.lightGray)
        self.draw_placeholder()

    def wheelEvent(self, event):
        if event.angleDelta().y() > 0:
            self.scale(self.zoom_factor, self.zoom_factor)
        else:
            self.scale(1 / self.zoom_factor, 1 / self.zoom_factor)

    def draw_placeholder(self):
        pen = QPen(Qt.GlobalColor.gray)
        self.scene.addLine(
            -200,
            0,
            200,
            0,
            pen
        )
        self.scene.addLine(
            0,
            -200,
            0,
            200,
            pen
        )

    def draw_track(self, points):
        """Reserved for GPS polyline rendering."""
        pass
