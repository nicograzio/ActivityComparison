"""Two-handle range slider used to trim the visible track.

Called by:
    - ``ui.track_panel.TrackPanel``

Emits:
    - ``valuesChanged`` whenever the selected distance interval changes.
"""

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPainter, QPen, QBrush, QColor


class RangeSlider(QWidget):
    """Custom slider with two draggable handles.

    The widget is used to select the visible portion of an activity in meters.
    """

    valuesChanged = pyqtSignal(int, int)

    def __init__(self, parent=None):
        """Initialize the slider with a default range.

        Called by:
            - ``TrackPanel.__init__``
        """
        super().__init__(parent)
        self.minimum = 0
        self.maximum = 1000
        self.lower = 0
        self.upper = 1000
        self.dragging = None
        self.setMinimumHeight(30)

    def setRange(self, minimum, maximum):
        """Set the allowed range for both handles.

        Called by:
            - ``TrackPanel.import_file``

        Args:
            minimum: Lower bound in slider units.
            maximum: Upper bound in slider units.
        """
        self.minimum = minimum
        self.maximum = maximum

    def setValues(self, lower, upper):
        """Update both handle positions.

        Called by:
            - ``TrackPanel.import_file``

        Args:
            lower: Lower handle position.
            upper: Upper handle position.
        """
        self.lower = lower
        self.upper = upper
        self.update()

    def _pos(self, value):
        """Convert a slider value to widget coordinates.

        Called by:
            - ``paintEvent``
            - ``mousePressEvent``
        """
        return int((value-self.minimum)/(self.maximum-self.minimum)*self.width())

    def _value(self, x):
        """Convert an x coordinate to slider units.

        Called by:
            - ``mouseMoveEvent``
        """
        return int(self.minimum + x/max(1,self.width())*(self.maximum-self.minimum))

    def mousePressEvent(self, event):
        """Pick the handle closest to the click position.

        Called by:
            - Qt when the user presses the mouse on the slider.
        """
        x = event.position().x()
        self.dragging = 'lower' if abs(x-self._pos(self.lower)) < abs(x-self._pos(self.upper)) else 'upper'

    def mouseMoveEvent(self, event):
        """Move the active handle and emit the updated interval.

        Called by:
            - Qt while the user drags a slider handle.
        """
        if not self.dragging:
            return
        value = max(self.minimum, min(self.maximum, self._value(event.position().x())))
        if self.dragging == 'lower':
            self.lower = min(value, self.upper)
        else:
            self.upper = max(value, self.lower)
        self.valuesChanged.emit(self.lower, self.upper)
        self.update()

    def mouseReleaseEvent(self, event):
        """Release the currently dragged handle.

        Called by:
            - Qt when the mouse button is released.
        """
        self.dragging = None

    def paintEvent(self, event):
        """Draw the slider track and its two handles.

        Called by:
            - Qt whenever the widget needs repainting.
        """
        p = QPainter(self)
        y = self.height()//2
        gray = QColor(130, 130, 130)
        p.setPen(QPen(gray, 6))
        p.drawLine(self._pos(self.lower), y, self._pos(self.upper), y)
        p.setPen(QPen(Qt.GlobalColor.darkGray, 4))
        p.drawLine(10, y, self.width()-10, y)
        p.setBrush(QBrush(Qt.GlobalColor.white))
        p.setPen(QPen(gray, 2))
        p.drawEllipse(self._pos(self.lower)-7, y-7, 14, 14)
        p.drawEllipse(self._pos(self.upper)-7, y-7, 14, 14)
