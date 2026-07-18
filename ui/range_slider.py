from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPainter, QPen, QBrush, QColor


class RangeSlider(QWidget):
    valuesChanged = pyqtSignal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.minimum = 0
        self.maximum = 1000
        self.lower = 0
        self.upper = 1000
        self.dragging = None
        self.setMinimumHeight(30)

    def setRange(self, minimum, maximum):
        self.minimum = minimum
        self.maximum = maximum

    def setValues(self, lower, upper):
        self.lower = lower
        self.upper = upper
        self.update()

    def _pos(self, value):
        return int((value-self.minimum)/(self.maximum-self.minimum)*self.width())

    def _value(self, x):
        return int(self.minimum + x/max(1,self.width())*(self.maximum-self.minimum))

    def mousePressEvent(self, event):
        x = event.position().x()
        self.dragging = 'lower' if abs(x-self._pos(self.lower)) < abs(x-self._pos(self.upper)) else 'upper'

    def mouseMoveEvent(self, event):
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
        self.dragging = None

    def paintEvent(self, event):
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
