"""Two-handle range slider used to trim the visible track.

Called by:
    - ``ui.track_panel.TrackPanel``

Emits:
    - ``valuesChanged`` whenever the selected distance interval changes.
"""

from PyQt6.QtWidgets import QWidget, QSizePolicy
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPainter, QPen, QBrush, QColor, QMouseEvent


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
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._minimum = 0
        self._maximum = 1000
        self._lower = 0
        self._upper = 1000
        self.dragging = None
        self.setMinimumHeight(30)
        self.margin = 10 

    def setRange(self, minimum, maximum):
        """Set the allowed range for both handles.

        Called by:
            - ``TrackPanel.import_file``

        Args:
            minimum: Lower bound in slider units.
            maximum: Upper bound in slider units.
        """
        # Ensure minimum < maximum
        if minimum > maximum:
            minimum, maximum = maximum, minimum
        if minimum == maximum:
            maximum = minimum + 1
        self._minimum = minimum
        self._maximum = maximum
        # Clamp current values to the new range
        self._lower = max(self._minimum, min(self._maximum, self._lower))
        self._upper = max(self._minimum, min(self._maximum, self._upper))
        if self._lower > self._upper:
            self._lower, self._upper = self._upper, self._lower
        self.update()

    def setValues(self, lower, upper):
        """Update both handle positions.

        Called by:
            - ``TrackPanel.import_file``

        Args:
            lower: Lower handle position.
            upper: Upper handle position.
        """
        # Clamp values to the allowed range
        lower = max(self._minimum, min(self._maximum, lower))
        upper = max(self._minimum, min(self._maximum, upper))
        # Ensure lower <= upper
        if lower > upper:
            lower, upper = upper, lower
        self._lower = lower
        self._upper = upper
        self.update()

    def _pos(self, value):
        """Convert a slider value to widget coordinates, considerando il margine."""
        larghezza_utile = self.width() - (self.margin * 2)
        if larghezza_utile <= 0 or self._maximum == self._minimum:
            return self.margin
        frazione = (value - self._minimum) / (self._maximum - self._minimum)
        return int(self.margin + (frazione * larghezza_utile))

    def _value(self, x):
        """Convert an x coordinate to slider units, considerando il margine."""
        larghezza_utile = self.width() - (self.margin * 2)
        if larghezza_utile <= 0 or self._maximum == self._minimum:
            return self._minimum
        # Sottrae il margine iniziale e mappa i pixel nell'intervallo corretto
        frazione = (x - self.margin) / larghezza_utile
        frazione = max(0.0, min(1.0, frazione))  # Evita di uscire fuori dai bordi
        return int(self._minimum + frazione * (self._maximum - self._minimum))

    def mousePressEvent(self, a0: QMouseEvent | None):
        """Pick the handle closest to the click position.

        Called by:
            - Qt when the user presses the mouse on the slider.
        """
        assert a0 is not None
        if a0.button() != Qt.MouseButton.LeftButton:
            return
        x = a0.position().x()
        self.dragging = 'lower' if abs(x-self._pos(self._lower)) < abs(x-self._pos(self._upper)) else 'upper'
        self.grabMouse()

    def mouseMoveEvent(self, a0: QMouseEvent | None):
        """Move the active handle and emit the updated interval.

        Called by:
            - Qt while the user drags a slider handle.
        """
        assert a0 is not None
        if not self.dragging:
            return
        value = max(self._minimum, min(self._maximum, self._value(a0.position().x())))
        if self.dragging == 'lower':
            self._lower = min(value, self._upper)
        else:
            self._upper = max(value, self._lower)
        self.valuesChanged.emit(self._lower, self._upper)
        self.update()
        a0.accept()

    def mouseReleaseEvent(self, a0: QMouseEvent | None):
        """Release the currently dragged handle.

        Called by:
            - Qt when the mouse button is released.
        """
        assert a0 is not None
        if a0.button() == Qt.MouseButton.LeftButton and self.dragging is not None:
            self.dragging = None
            self.releaseMouse()
        a0.accept()

    def paintEvent(self, a0):
        """Draw the slider track and its two handles.

        Called by:
            - Qt whenever the widget needs repainting.
        """
        p = QPainter(self)
        y = self.height()//2
        gray = QColor(130, 130, 130)
        p.setPen(QPen(gray, 6))
        p.drawLine(self._pos(self._lower), y, self._pos(self._upper), y)
        p.setPen(QPen(Qt.GlobalColor.darkGray, 4))
        p.drawLine(self.margin, y, self.width()-self.margin, y)
        p.setBrush(QBrush(Qt.GlobalColor.white))
        p.setPen(QPen(gray, 2))
        p.drawEllipse(self._pos(self._lower)-7, y-7, 14, 14)
        p.drawEllipse(self._pos(self._upper)-7, y-7, 14, 14)
