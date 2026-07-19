from PyQt6.QtCore import QSize
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel

try:
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
    from matplotlib.figure import Figure
except ImportError:
    FigureCanvasQTAgg = None
    Figure = None


class GraphPanel(QWidget):
    """Activity chart container."""

    def __init__(self):
        super().__init__()
        self._time = []
        self._values = []
        self.setMinimumHeight(220)

        layout = QVBoxLayout(self)

        if FigureCanvasQTAgg and Figure:
            self.figure = Figure(figsize=(8, 3), tight_layout=True)
            self.canvas = FigureCanvasQTAgg(self.figure)
            self.canvas.setMinimumSize(QSize(400, 180))
            self.axis = self.figure.add_subplot(111)
            self.axis.set_xlabel("Tempo")
            self.axis.set_ylabel("Valore")
            layout.addWidget(self.canvas)
        else:
            self.figure = None
            self.canvas = None
            self.axis = None
            layout.addWidget(QLabel("Modulo grafici non disponibile"))

        self.setLayout(layout)

    def set_series(self, time_values, data_values, label="Valore"):
        self._time = list(time_values)
        self._values = list(data_values)

        if not self.axis or not self._time:
            return

        self.axis.clear()
        self.axis.plot(self._time, self._values)
        self.axis.set_xlabel("Tempo")
        self.axis.set_ylabel(label)
        self.figure.tight_layout()
        self.canvas.draw_idle()

    def clear_graph(self):
        self._time.clear()
        self._values.clear()
        if self.axis:
            self.axis.clear()
            self.canvas.draw_idle()
