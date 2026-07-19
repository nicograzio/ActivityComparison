from PyQt6.QtWidgets import QWidget, QVBoxLayout

try:
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
    from matplotlib.figure import Figure
except ImportError:
    FigureCanvasQTAgg = None
    Figure = None


class GraphPanel(QWidget):
    """Base activity graph widget.

    First implementation:
    - time axis support
    - single selectable data series
    - ready for future cursor and multi-series extensions
    """

    def __init__(self):
        super().__init__()

        self._time = []
        self._values = []

        layout = QVBoxLayout(self)

        if FigureCanvasQTAgg and Figure:
            self.figure = Figure(figsize=(5, 2))
            self.canvas = FigureCanvasQTAgg(self.figure)
            self.axis = self.figure.add_subplot(111)
            self.axis.set_xlabel("Tempo")
            self.axis.set_ylabel("Valore")
            layout.addWidget(self.canvas)
        else:
            self.figure = None
            self.canvas = None
            self.axis = None

        self.setLayout(layout)

    def set_series(self, time_values, data_values, label="Valore"):
        self._time = list(time_values)
        self._values = list(data_values)

        if not self.axis:
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
