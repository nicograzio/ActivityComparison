"""Graph panel used to display the activity series below each map.

The widget uses PyQtGraph for high-performance, modern and interactive plotting.

Called by:
    - ``ui.main_window.MainWindow``

Consumed by:
    - ``MainWindow._update_graph``
"""

from typing import cast
from PyQt6.QtCore import QSize, pyqtSignal, Qt, QPoint, QPointF
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QCheckBox, QComboBox
import pyqtgraph as pg
import numpy as np

# Configure PyQtGraph for a modern look
pg.setConfigOption('antialias', True)
pg.setConfigOption('background', '#1e1e1e')  # Dark background to match modern UI
pg.setConfigOption('foreground', '#dcdcdc')  # Light foreground for text/axes


def format_time_axis(seconds):
    """Format seconds into HH:MM:SS, MM:SS or SS."""
    seconds = int(round(seconds))
    if seconds < 0:
        return f"-{format_time_axis(-seconds)}"
    
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    elif minutes > 0:
        return f"{minutes:02d}:{secs:02d}"
    else:
        return f"{secs:02d}s"


class TimeAxisItem(pg.AxisItem):
    """Custom axis item to display formatted time."""
    def tickStrings(self, values, scale, spacing):
        return [format_time_axis(value) for value in values]


class GraphPanel(QWidget):
    """Render time/distance series for one activity using PyQtGraph.

    Created by:
        - ``MainWindow``
    """

    # Signal emitted when user hovers over the graph: (index, x_value, y_value)
    point_hovered = pyqtSignal(int, float, float)
    # Signal emitted when the X axis mode combo changes: ("Tempo" or "Distanza")
    x_axis_changed = pyqtSignal(str)

    def __init__(self):
        """Create the graph container and initialize the PyQtGraph widget."""
        super().__init__()
        self._time = np.array([])
        self._speeds = np.array([])
        self._altitudes = np.array([])
        self._heart_rates = np.array([])
        self._x_mode = "Tempo"
        
        self._has_altitude = False
        self._has_speed = False
        self._has_hr = False
        
        self.setMinimumHeight(240)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # Legend / Series Toggles
        self.legend_widget = QWidget()
        self.legend_layout = QHBoxLayout(self.legend_widget)
        self.legend_layout.setContentsMargins(10, 2, 10, 2)
        self.legend_layout.setSpacing(15)
        self.legend_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)

        # Altitude style (grey background filled line)
        self.cb_altitude = QCheckBox("Altitudine")
        self.cb_altitude.setChecked(True)
        self.cb_altitude.setStyleSheet("""
            QCheckBox { color: #888888; font-weight: bold; background-color: transparent; }
            QCheckBox::indicator { width: 12px; height: 12px; border: 1px solid #555555; background-color: #888888; }
            QCheckBox::indicator:unchecked { background-color: transparent; }
        """)

        # Speed style (blue line)
        self.cb_speed = QCheckBox("Velocità")
        self.cb_speed.setChecked(True)
        self.cb_speed.setStyleSheet("""
            QCheckBox { color: #3498db; font-weight: bold; background-color: transparent; }
            QCheckBox::indicator { width: 12px; height: 12px; border: 1px solid #555555; background-color: #3498db; }
            QCheckBox::indicator:unchecked { background-color: transparent; }
        """)

        # Heart rate style (red line)
        self.cb_hr = QCheckBox("Cardio")
        self.cb_hr.setChecked(True)
        self.cb_hr.setStyleSheet("""
            QCheckBox { color: #e74c3c; font-weight: bold; background-color: transparent; }
            QCheckBox::indicator { width: 12px; height: 12px; border: 1px solid #555555; background-color: #e74c3c; }
            QCheckBox::indicator:unchecked { background-color: transparent; }
        """)

        self.legend_layout.addWidget(self.cb_altitude)
        self.legend_layout.addWidget(self.cb_speed)
        self.legend_layout.addWidget(self.cb_hr)

        # Asse X: combobox spostata sulla destra del grafico (senza etichetta).
        # Vivendo dentro il pannello del grafico, viene nascosto automaticamente
        # insieme ad esso quando si nascondono i grafici.
        self.legend_layout.addStretch(1)
        self.x_axis_combo = QComboBox()
        self.x_axis_combo.addItems(["Tempo", "Distanza"])
        self.x_axis_combo.setEnabled(False)
        self.x_axis_combo.setToolTip("Scegli cosa mostrare sull'asse X del grafico")
        self.x_axis_combo.currentTextChanged.connect(self.x_axis_changed.emit)
        self.legend_layout.addWidget(self.x_axis_combo)

        layout.addWidget(self.legend_widget)
        self.legend_widget.hide() # Hide until data is loaded

        # Connect slots
        self.cb_altitude.stateChanged.connect(self._update_visibility)
        self.cb_speed.stateChanged.connect(self._update_visibility)
        self.cb_hr.stateChanged.connect(self._update_visibility)

        # Create PlotWidget with custom X axis
        self.time_axis = TimeAxisItem(orientation='bottom')
        self.dist_axis = pg.AxisItem(orientation='bottom')
        
        self.plot_widget = pg.PlotWidget(axisItems={'bottom': self.time_axis})
        self.plot_widget.setMouseTracking(True)
        self.plot_widget.setMinimumSize(QSize(400, 180))
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        
        self.plot_item = cast(pg.PlotItem, self.plot_widget.getPlotItem())
        assert self.plot_item is not None, "PlotItem could not be created"
        
        # We use separate ViewBoxes for multiple Y axes
        # Speed is the main ViewBox
        self.vb_speed = cast(pg.ViewBox, self.plot_item.vb)
        assert self.vb_speed is not None, "Speed ViewBox could not be obtained"
        
        # Extra ViewBox for Heart Rate
        self.vb_hr = pg.ViewBox()
        scene = self.plot_item.scene()
        assert scene is not None, "Scene could not be obtained"
        scene.addItem(self.vb_hr)
        
        # Extra ViewBox for Altitude
        self.vb_alt = pg.ViewBox()
        scene.addItem(self.vb_alt)
        
        # Add axes to the layout
        # Speed Axis (Left 1)
        self.ax_speed = self.plot_item.getAxis('left')
        assert self.ax_speed is not None, "Speed axis could not be obtained"
        self.ax_speed.setLabel('Velocità', color='#3498db', units='km/h')
        self.ax_speed.setPen('#3498db')
        
        # Altitude Axis: use the default right axis from PlotItem for the first right-side axis.
        self.ax_alt = self.plot_item.getAxis('right')
        assert self.ax_alt is not None, "Altitude axis could not be obtained"
        self.ax_alt.setLabel('Altitudine', color='#888888', units='m')
        self.ax_alt.setPen('#888888')
        self.ax_alt.linkToView(self.vb_alt)
        self.ax_alt.setVisible(True)
        
        # Heart Rate Axis: add a second right-side axis in a new column.
        self.ax_hr = pg.AxisItem('right')
        plot_item_layout = self.plot_item.layout
        assert plot_item_layout is not None, "PlotItem layout could not be obtained"
        plot_item_layout.addItem(self.ax_hr, 2, 3)
        self.ax_hr.setLabel('Cardio', color='#e74c3c', units='bpm')
        self.ax_hr.setPen('#e74c3c')
        self.ax_hr.linkToView(self.vb_hr)
        self.ax_hr.setVisible(True)
 
        # Link X axes of all ViewBoxes
        self.vb_hr.setXLink(self.vb_speed)
        self.vb_alt.setXLink(self.vb_speed)

        # Synchronize ViewBox resizing
        def update_views():
            vb_speed_rect = self.vb_speed.sceneBoundingRect()
            self.vb_hr.setGeometry(vb_speed_rect)
            self.vb_alt.setGeometry(vb_speed_rect)
        self.vb_speed.sigResized.connect(update_views)

        # Style the curves
        self.curve_speed = pg.PlotDataItem(pen=pg.mkPen(color='#3498db', width=2))
        self.vb_speed.addItem(self.curve_speed)
        
        self.curve_hr = pg.PlotDataItem(pen=pg.mkPen(color='#e74c3c', width=2))
        self.vb_hr.addItem(self.curve_hr)
        
        self.curve_alt = pg.PlotDataItem(pen=pg.mkPen(color='#888888', width=1.5))
        self.vb_alt.addItem(self.curve_alt)
        
        # Interactive elements: Crosshair vertical line
        self.v_line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen(color='#f1c40f', style=Qt.PenStyle.DashLine, width=1.5))
        self.v_line.hide()
        self.plot_item.addItem(self.v_line, ignoreBounds=True)

        # Point markers for each series
        self.marker_speed = pg.ScatterPlotItem(size=10, pen=pg.mkPen('#3498db'), brush=pg.mkBrush('#3498db'))
        self.vb_speed.addItem(self.marker_speed)
        
        self.marker_hr = pg.ScatterPlotItem(size=10, pen=pg.mkPen('#e74c3c'), brush=pg.mkBrush('#e74c3c'))
        self.vb_hr.addItem(self.marker_hr)
        
        self.marker_alt = pg.ScatterPlotItem(size=10, pen=pg.mkPen('#888888'), brush=pg.mkBrush('#888888'))
        self.vb_alt.addItem(self.marker_alt)
        
        # Tooltip-like label
        self.label = pg.TextItem(anchor=(0, 1), color='#f1c40f', fill=(30, 30, 30, 220))
        self.label.hide()
        self.plot_item.addItem(self.label)

        layout.addWidget(self.plot_widget)
        self.setLayout(layout)


        # Connect mouse motion event
        plot_scene = self.plot_item.scene()
        assert plot_scene is not None, "Scene could not be obtained for SignalProxy"
        self.proxy = pg.SignalProxy(plot_scene.sigMouseMoved, rateLimit=60, slot=self._on_mouse_move)
        self.vb_hr.setVisible(True)  # Ensure HR axis is visible

    def _update_visibility(self):
        """Update visibility of graph series based on checkbox toggles."""
        show_alt = self.cb_altitude.isChecked() and self._has_altitude
        show_speed = self.cb_speed.isChecked() and self._has_speed
        show_hr = self.cb_hr.isChecked() and self._has_hr

        self.curve_alt.setVisible(show_alt)
        self.ax_alt.setVisible(show_alt)
        
        self.curve_speed.setVisible(show_speed)
        self.ax_speed.setVisible(show_speed)
        
        self.curve_hr.setVisible(show_hr)
        self.ax_hr.setVisible(show_hr)
        
        # Force autoRange for all active viewboxes
        if show_speed: self.vb_speed.autoRange()
        if show_alt: self.vb_alt.autoRange()
        if show_hr: self.vb_hr.autoRange()
        
        self._update_limits()

    def _update_limits(self):
        """Set limits to prevent zooming out beyond data bounds."""
        if len(self._time) == 0:
            return
            
        x_min, x_max = np.min(self._time), np.max(self._time)
        
        # Buffer for X limits
        x_range = x_max - x_min
        self.vb_speed.setLimits(xMin=x_min - x_range*0.02, xMax=x_max + x_range*0.02)
        
        # Y limits for each series
        if self._has_speed:
            y_min, y_max = np.min(self._speeds), np.max(self._speeds)
            y_range = max(y_max - y_min, 1.0)
            self.vb_speed.setLimits(yMin=y_min - y_range*0.1, yMax=y_max + y_range*0.1)
            
        if self._has_altitude:
            y_min, y_max = np.min(self._altitudes), np.max(self._altitudes)
            y_range = max(y_max - y_min, 1.0)
            self.vb_alt.setLimits(yMin=y_min - y_range*0.1, yMax=y_max + y_range*0.1)
            
        if self._has_hr:
            y_min, y_max = np.min(self._heart_rates), np.max(self._heart_rates)
            y_range = max(y_max - y_min, 1.0)
            self.vb_hr.setLimits(yMin=y_min - y_range*0.1, yMax=y_max + y_range*0.1)

    def set_series(self, x_values, speeds, altitudes, heart_rates, x_mode="Tempo"):
        """Replace the current plot data and redraw the graph.

        Called by:
            - ``MainWindow._update_graph``

        Args:
            x_values: X axis samples.
            speeds: Speed Y axis samples.
            altitudes: Altitude Y axis samples.
            heart_rates: Heart rate Y axis samples.
            x_mode: "Tempo" or "Distanza".
        """
        self._time = np.array(x_values)
        self._speeds = np.array(speeds)
        self._altitudes = np.array(altitudes)
        self._heart_rates = np.array(heart_rates)
        self._x_mode = x_mode

        if len(self._time) == 0:
            self.clear_graph()
            return

        # Determine data availability
        self._has_speed = len(self._speeds) > 0 and any(s != 0.0 for s in self._speeds)
        self._has_altitude = len(self._altitudes) > 0 and any(a != 0.0 for a in self._altitudes)
        self._has_hr = len(self._heart_rates) > 0 and any(h != 0.0 for h in self._heart_rates)

        # Show legend and checkboxes accordingly
        self.legend_widget.show()
        self.cb_altitude.setVisible(self._has_altitude)
        self.cb_speed.setVisible(self._has_speed)
        self.cb_hr.setVisible(self._has_hr)

        # Enable and sync the X axis combo with the current mode
        self.x_axis_combo.setEnabled(True)
        if self.x_axis_combo.currentText() != x_mode:
            self.x_axis_combo.blockSignals(True)
            self.x_axis_combo.setCurrentText(x_mode)
            self.x_axis_combo.blockSignals(False)

        # Update X axis type and label
        if x_mode == "Tempo":
            self.plot_item.setAxisItems({'bottom': self.time_axis})
            self.plot_widget.getAxis('bottom').setLabel("Tempo")
        else:
            self.plot_item.setAxisItems({'bottom': self.dist_axis})
            self.plot_widget.getAxis('bottom').setLabel("Distanza", units="km")

        # Set curve data
        self.curve_speed.setData(self._time, self._speeds)
        self.curve_hr.setData(self._time, self._heart_rates)
        self.curve_alt.setData(self._time, self._altitudes)
        
        if self._has_altitude:
            min_alt = np.nanmin(self._altitudes)
            self.curve_alt.setFillLevel(min_alt)
            self.curve_alt.setFillBrush(pg.mkBrush(128, 128, 128, 60))

        # Align ranges: we want them all to "start at the same base"
        # In PyQtGraph with multiple ViewBoxes, each has its own range.
        # To align them visually so they all start at the bottom, we can
        # set the Y-range for each to [min_val, max_val + padding].
        # But autoRange does this automatically for each ViewBox.
        # The user said "i due min devono essere portati allo stesso livello",
        # which is exactly what happens when each ViewBox auto-scales to its data.
        
        # Set visibility and trigger autoRange
        self._update_visibility()
        
        # Reset view to data
        self.vb_speed.autoRange()
        self.vb_alt.autoRange()
        self.vb_hr.autoRange()

        # Connect Y-axis scaling if user wants them linked
        # When vb_speed Y range changes, we want to scale others proportionally
        self.vb_speed.sigYRangeChanged.connect(self._sync_y_ranges)

    def _sync_y_ranges(self):
        """Synchronize Y ranges of all ViewBoxes proportionally."""
        if len(self._time) == 0 or not self.vb_speed.viewRange():
            return
            
        # Disable signals to avoid recursion
        self.vb_speed.blockSignals(True)
        self.vb_alt.blockSignals(True)
        self.vb_hr.blockSignals(True)
        
        try:
            # Current Y range for speed (normalized 0-1)
            s_min, s_max = self.vb_speed.viewRange()[1]
            data_s_min, data_s_max = np.min(self._speeds), np.max(self._speeds)
            data_s_range = max(data_s_max - data_s_min, 1.0)
            
            rel_min = (s_min - data_s_min) / data_s_range
            rel_max = (s_max - data_s_min) / data_s_range
            
            if self._has_altitude:
                data_a_min, data_a_max = np.min(self._altitudes), np.max(self._altitudes)
                data_a_range = max(data_a_max - data_a_min, 1.0)
                a_min = data_a_min + rel_min * data_a_range
                a_max = data_a_min + rel_max * data_a_range
                self.vb_alt.setYRange(a_min, a_max, padding=0)
                
            if self._has_hr:
                data_h_min, data_h_max = np.min(self._heart_rates), np.max(self._heart_rates)
                data_h_range = max(data_h_max - data_h_min, 1.0)
                h_min = data_h_min + rel_min * data_h_range
                h_max = data_h_min + rel_max * data_h_range
                self.vb_hr.setYRange(h_min, h_max, padding=0)
        finally:
            self.vb_speed.blockSignals(False)
            self.vb_alt.blockSignals(False)
            self.vb_hr.blockSignals(False)

    def _on_mouse_move(self, evt):
        """Handle mouse movement on the graph canvas."""
        pos = evt[0]
        plot_item_rect = self.plot_item.sceneBoundingRect()
        if plot_item_rect.contains(pos):
            mouse_point = self.vb_speed.mapSceneToView(pos)
            x_pos = mouse_point.x()
            
            if len(self._time) == 0:
                return

            # Find closest point index in the X-axis data
            idx = np.abs(self._time - x_pos).argmin()
            x_value = self._time[idx]
            
            # Show the vertical crosshair line
            self.v_line.setPos(x_value)
            self.v_line.show()
            
            lines = []
            
            # Determine visibility/presence
            show_alt = self.cb_altitude.isChecked() and self._has_altitude
            show_speed = self.cb_speed.isChecked() and self._has_speed
            show_hr = self.cb_hr.isChecked() and self._has_hr

            # Update markers and tooltip
            if show_alt:
                y_alt = self._altitudes[idx]
                self.marker_alt.setData([x_value], [y_alt])
                self.marker_alt.show()
                lines.append(f"Alt: {y_alt:.1f} m")
            else:
                self.marker_alt.hide()

            if show_speed:
                y_spd = self._speeds[idx]
                self.marker_speed.setData([x_value], [y_spd])
                self.marker_speed.show()
                lines.append(f"Vel: {y_spd:.1f} km/h")
            else:
                self.marker_speed.hide()


            if show_hr:
                y_hr = self._heart_rates[idx]
                self.marker_hr.setData([x_value], [y_hr])
                self.marker_hr.show()
                lines.append(f"Cardio: {int(y_hr)} bpm")
            else:
                self.marker_hr.hide()

            if lines:
                x_formatted = format_time_axis(x_value) if self._x_mode == "Tempo" else f"{x_value:.2f} km"
                self.label.setText(f"X: {x_formatted}\n" + "\n".join(lines))
                # Position label: center vertically around cursor (half above, half below).
                view_rect = self.vb_speed.sceneBoundingRect()
                top_y = view_rect.top()
                bottom_y = view_rect.bottom()

                # Determine horizontal and vertical placement using scene coordinates
                label_width = self.label.boundingRect().width()
                label_height = self.label.boundingRect().height()

                # Horizontal: place on the left of mouse if near right edge
                if pos.x() + label_width + 12 > view_rect.right():
                    label_x_scene = pos.x() - 12
                    anchor_x = 1  # Right edge of label at pos.x() - 12
                else:
                    label_x_scene = pos.x() + 12
                    anchor_x = 0  # Left edge of label at pos.x() + 12

                # Vertical: normally center around cursor
                label_y_scene = pos.y()
                anchor_y = 0.5  # Vertical center of label at pos.y()

                # Adjust vertical if out of bounds
                if pos.y() - label_height / 2 < top_y:
                    # Out of top -> place below arrow
                    label_y_scene = pos.y() + 12
                    anchor_y = 0  # Top edge of label at pos.y() + 12
                elif pos.y() + label_height / 2 > bottom_y:
                    # Out of bottom -> place above arrow
                    label_y_scene = pos.y() - 12
                    anchor_y = 1  # Bottom edge of label at pos.y() - 12

                self.label.setAnchor((anchor_x, anchor_y))

                # Convert scene pixel coords to plot data coords for TextItem positioning
                scene_point = QPointF(label_x_scene, label_y_scene)
                view_point = self.vb_speed.mapSceneToView(scene_point)

                self.label.setPos(view_point.x(), view_point.y())
                self.label.show()
            else:
                self.label.hide()
            
            # Emit signal for map synchronization
            y_val_emit = self._speeds[idx] if self._has_speed else (self._altitudes[idx] if self._has_altitude else 0.0)
            self.point_hovered.emit(int(idx), float(x_value), float(y_val_emit))
        else:
            self._hide_interactive_elements()

    def _hide_interactive_elements(self):
        """Hide crosshair, markers and label."""
        self.v_line.hide()
        self.marker_alt.hide()
        self.marker_speed.hide()
        self.marker_hr.hide()
        self.label.hide()

    def leaveEvent(self, a0):
        """Override leaveEvent to hide crosshair when mouse leaves the widget."""
        self._hide_interactive_elements()
        super().leaveEvent(a0)

    def set_hovered_point_by_index(self, point_index: int):
        """Show the hovered point marker by data index.

        Called by:
            - ``MainWindow`` when selecting a point from the segment detail dialog

        Args:
            point_index: Index of the point in the track data arrays.
        """
        if point_index < 0 or point_index >= len(self._time):
            self._hide_interactive_elements()
            return

        x_value = self._time[point_index]

        # Show the vertical crosshair line
        self.v_line.setPos(x_value)
        self.v_line.show()

        show_alt = self.cb_altitude.isChecked() and self._has_altitude
        show_speed = self.cb_speed.isChecked() and self._has_speed
        show_hr = self.cb_hr.isChecked() and self._has_hr

        lines = []

        if show_alt:
            y_alt = self._altitudes[point_index]
            self.marker_alt.setData([x_value], [y_alt])
            self.marker_alt.show()
            lines.append(f"Alt: {y_alt:.1f} m")
        else:
            self.marker_alt.hide()

        if show_speed:
            y_spd = self._speeds[point_index]
            self.marker_speed.setData([x_value], [y_spd])
            self.marker_speed.show()
            lines.append(f"Vel: {y_spd:.1f} km/h")
        else:
            self.marker_speed.hide()

        if show_hr:
            y_hr = self._heart_rates[point_index]
            self.marker_hr.setData([x_value], [y_hr])
            self.marker_hr.show()
            lines.append(f"Cardio: {int(y_hr)} bpm")
        else:
            self.marker_hr.hide()

        if lines:
            x_formatted = format_time_axis(x_value) if self._x_mode == "Tempo" else f"{x_value:.2f} km"
            self.label.setText(f"X: {x_formatted}\n" + "\n".join(lines))
            self.label.show()
        else:
            self.label.hide()

        # Emit signal for map synchronization
        y_val_emit = self._speeds[point_index] if self._has_speed else (self._altitudes[point_index] if self._has_altitude else 0.0)
        self.point_hovered.emit(point_index, float(x_value), float(y_val_emit))

    def clear_graph(self):
        """Clear the graph state and remove the plotted lines."""
        self._time = np.array([])
        self._speeds = np.array([])
        self._altitudes = np.array([])
        self._heart_rates = np.array([])
        self._has_altitude = False
        self._has_speed = False
        self._has_hr = False
        
        self.curve_alt.setData([], [])
        self.curve_speed.setData([], [])
        self.curve_hr.setData([], [])
        
        self.legend_widget.hide()
        self.cb_altitude.setVisible(False)
        self.cb_speed.setVisible(False)
        self.cb_hr.setVisible(False)
        self.x_axis_combo.setEnabled(False)
        
        self._hide_interactive_elements()