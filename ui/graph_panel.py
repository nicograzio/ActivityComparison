"""Graph panel used to display the activity series below each map.

The widget uses PyQtGraph for high-performance, modern and interactive plotting.

Called by:
    - ``ui.main_window.MainWindow``

Consumed by:
    - ``MainWindow._update_graph``
"""

from PyQt6.QtCore import QSize, pyqtSignal, Qt
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel
import pyqtgraph as pg
import numpy as np

# Configure PyQtGraph for a modern look
pg.setConfigOption('antialias', True)
pg.setConfigOption('background', '#1e1e1e')  # Dark background to match modern UI
pg.setConfigOption('foreground', '#dcdcdc')  # Light foreground for text/axes


class GraphPanel(QWidget):
    """Render a time series for one activity using PyQtGraph.

    Created by:
        - ``MainWindow``
    """

    # Signal emitted when user hovers over the graph: (index, x_value, y_value)
    point_hovered = pyqtSignal(int, float, float)

    def __init__(self):
        """Create the graph container and initialize the PyQtGraph widget."""
        super().__init__()
        self._time = np.array([])
        self._values = np.array([])
        self.setMinimumHeight(220)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Create PlotWidget
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setMinimumSize(QSize(400, 180))
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.plot_widget.getAxis('bottom').setLabel("Tempo")
        self.plot_widget.getAxis('left').setLabel("Valore")
        
        # Style the plot
        self.plot_item = self.plot_widget.getPlotItem()
        self.curve = self.plot_item.plot(pen=pg.mkPen(color='#3498db', width=2))
        
        # Interactive elements: Crosshair
        self.v_line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen(color='r', style=Qt.PenStyle.DashLine, width=1.5))
        self.h_line = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen(color='r', style=Qt.PenStyle.DashLine, width=1.5))
        self.v_line.hide()
        self.h_line.hide()
        self.plot_item.addItem(self.v_line, ignoreBounds=True)
        self.plot_item.addItem(self.h_line, ignoreBounds=True)

        # Point marker
        self.point_marker = pg.ScatterPlotItem(size=10, pen=pg.mkPen('r'), brush=pg.mkBrush('r'))
        self.plot_item.addItem(self.point_marker)
        
        # Tooltip-like label
        self.label = pg.TextItem(anchor=(0, 1), color='#f1c40f', fill=(30, 30, 30, 200))
        self.label.hide()
        self.plot_item.addItem(self.label)

        layout.addWidget(self.plot_widget)
        self.setLayout(layout)

        # Connect mouse motion event
        self.proxy = pg.SignalProxy(self.plot_item.scene().sigMouseMoved, rateLimit=60, slot=self._on_mouse_move)

    def set_series(self, time_values, data_values, label="Valore"):
        """Replace the current plot data and redraw the graph.

        Called by:
            - ``MainWindow._update_graph``

        Args:
            time_values: X axis samples.
            data_values: Y axis samples.
            label: Y axis label to display.
        """
        self._time = np.array(time_values)
        self._values = np.array(data_values)

        if len(self._time) == 0:
            self.clear_graph()
            return

        self.curve.setData(self._time, self._values)
        self.plot_item.getAxis('left').setLabel(label)
        
        # Reset view to data
        self.plot_item.autoRange()

    def _on_mouse_move(self, evt):
        """Handle mouse movement on the graph canvas."""
        pos = evt[0]
        if self.plot_item.sceneBoundingRect().contains(pos):
            mouse_point = self.plot_item.vb.mapSceneToView(pos)
            x_pos = mouse_point.x()
            
            if len(self._time) == 0:
                return

            # Find the closest point in the data using numpy for speed
            idx = np.abs(self._time - x_pos).argmin()
            
            x_value = self._time[idx]
            y_value = self._values[idx]
            
            # Update crosshair and marker
            self.v_line.setPos(x_value)
            self.h_line.setPos(y_value)
            self.v_line.show()
            self.h_line.show()
            
            self.point_marker.setData([x_value], [y_value])
            
            # Update label
            y_label = self.plot_item.getAxis('left').labelText
            self.label.setText(f"{y_label}: {y_value:.2f}")
            self.label.setPos(x_value, y_value)
            self.label.show()
            
            # Emit signal for map synchronization
            self.point_hovered.emit(int(idx), float(x_value), float(y_value))
        else:
            self._hide_interactive_elements()

    def _hide_interactive_elements(self):
        """Hide crosshair, marker and label."""
        self.v_line.hide()
        self.h_line.hide()
        self.point_marker.setData([], [])
        self.label.hide()

    def leaveEvent(self, event):
        """Override leaveEvent to hide crosshair when mouse leaves the widget."""
        self._hide_interactive_elements()
        super().leaveEvent(event)

    def clear_graph(self):
        """Clear the graph state and remove the plotted line.

        Called by:
            - ``MainWindow._update_graph`` when no visible track is available
        """
        self._time = np.array([])
        self._values = np.array([])
        self.curve.setData([], [])
        self._hide_interactive_elements()
