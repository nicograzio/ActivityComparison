"""Graph panel used to display the activity series below each map.

The widget prefers Matplotlib with the QtAgg canvas and falls back to a
message when the plotting backend is not available.

Called by:
    - ``ui.main_window.MainWindow``

Consumed by:
    - ``MainWindow._update_graph``
"""

from PyQt6.QtCore import QSize, pyqtSignal
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel
from matplotlib.patches import Circle
from matplotlib.lines import Line2D

try:
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
    from matplotlib.figure import Figure
except ImportError:
    FigureCanvasQTAgg = None
    Figure = None


class GraphPanel(QWidget):
    """Render a time series for one activity.

    Created by:
        - ``MainWindow``
    """

    # Signal emitted when user hovers over the graph: (index, x_value, y_value)
    point_hovered = pyqtSignal(int, float, float)

    def __init__(self):
        """Create the graph container and initialize the plotting backend.

        Side effects:
            If Matplotlib is unavailable, a placeholder label is shown.
        """
        super().__init__()
        self._time = []
        self._values = []
        self.setMinimumHeight(220)
        
        # Interactive elements
        self._vertical_line = None
        self._point_marker = None
        self._tooltip = None

        layout = QVBoxLayout(self)

        if FigureCanvasQTAgg and Figure:
            self.figure = Figure(figsize=(8, 3), tight_layout=True)
            self.canvas = FigureCanvasQTAgg(self.figure)
            self.canvas.setMinimumSize(QSize(400, 180))
            self.axis = self.figure.add_subplot(111)
            self.axis.set_xlabel("Tempo")
            self.axis.set_ylabel("Valore")
            layout.addWidget(self.canvas)
            
            # Connect mouse motion event
            self.canvas.mpl_connect('motion_notify_event', self._on_mouse_move)
            self.canvas.mpl_connect('axes_leave_event', self._on_mouse_leave)
        else:
            self.figure = None
            self.canvas = None
            self.axis = None
            layout.addWidget(QLabel("Modulo grafici non disponibile"))

        self.setLayout(layout)

    def set_series(self, time_values, data_values, label="Valore"):
        """Replace the current plot data and redraw the graph.

        Called by:
            - ``MainWindow._update_graph``

        Args:
            time_values: X axis samples.
            data_values: Y axis samples.
            label: Y axis label to display.
        """
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

    def _on_mouse_move(self, event):
        """Handle mouse movement on the graph canvas.
        
        Displays a vertical line, tooltip, and emits a signal with the hovered point.
        """
        if not event.inaxes or not self._time or not self._values:
            return
        
        x_pos = event.xdata
        if x_pos is None:
            return
        
        # Find the closest point in the data
        closest_idx = 0
        min_distance = abs(self._time[0] - x_pos)
        
        for i, t in enumerate(self._time):
            distance = abs(t - x_pos)
            if distance < min_distance:
                min_distance = distance
                closest_idx = i
        
        # Get the value at the closest point
        y_value = self._values[closest_idx]
        x_value = self._time[closest_idx]
        
        # Remove old interactive elements
        if self._vertical_line is not None:
            self._vertical_line.remove()
        if self._point_marker is not None:
            self._point_marker.remove()
        if self._tooltip is not None:
            self._tooltip.remove()
        
        # Draw vertical line
        self._vertical_line = self.axis.axvline(x=x_value, color='red', linestyle='--', linewidth=1.5, alpha=0.7)
        
        # Draw point marker on the trace
        self._point_marker = Circle((x_value, y_value), radius=0.02 * (max(self._values) - min(self._values)), 
                                     color='red', zorder=5)
        self.axis.add_patch(self._point_marker)
        
        # Add tooltip with the value
        y_label = self.axis.get_ylabel()
        tooltip_text = f"{y_label}: {y_value:.2f}"
        self._tooltip = self.axis.text(x_value, y_value, f"  {tooltip_text}", 
                                       fontsize=9, color='red', 
                                       bbox=dict(boxstyle='round,pad=0.3', facecolor='yellow', alpha=0.7),
                                       verticalalignment='center')
        
        self.canvas.draw_idle()
        
        # Emit signal for map synchronization
        self.point_hovered.emit(closest_idx, x_value, y_value)
    
    def _on_mouse_leave(self, event):
        """Handle mouse leaving the graph canvas.
        
        Removes the vertical line, tooltip, and point marker.
        """
        if self._vertical_line is not None:
            self._vertical_line.remove()
            self._vertical_line = None
        if self._point_marker is not None:
            self._point_marker.remove()
            self._point_marker = None
        if self._tooltip is not None:
            self._tooltip.remove()
            self._tooltip = None
        
        self.canvas.draw_idle()

    def clear_graph(self):
        """Clear the graph state and remove the plotted line.

        Called by:
            - ``MainWindow._update_graph`` when no visible track is available
        """
        self._time.clear()
        self._values.clear()
        
        # Clear interactive elements
        if self._vertical_line is not None:
            self._vertical_line.remove()
            self._vertical_line = None
        if self._point_marker is not None:
            self._point_marker.remove()
            self._point_marker = None
        if self._tooltip is not None:
            self._tooltip.remove()
            self._tooltip = None
        
        if self.axis:
            self.axis.clear()
            self.canvas.draw_idle()
