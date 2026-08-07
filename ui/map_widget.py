"""Folium-based map renderer for activity tracks.

This widget replaces the MapLibre and Raster renderers with a Folium-based one,
integrated via QWebEngineView.
"""

from __future__ import annotations

import json
import os
import io
import tempfile
from pathlib import Path
from typing import Any

import folium
from PyQt6.QtCore import QUrl, QObject, pyqtSignal, pyqtSlot
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from core.analyzer import calculate_point_speed, haversine_distance
from core.colorizer import value_to_color


class _MapViewBridge(QObject):
    viewChanged = pyqtSignal(dict)

    @pyqtSlot("QVariant")
    def onViewChanged(self, state):
        self.viewChanged.emit(state)


class MapWidget(QWidget):
    """Folium-backed track renderer."""

    viewChanged = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ready = False
        self._pending_draw = None
        self._last_view_state: dict[str, Any] = {
            "center": [44.58333, 10.73333],
            "zoom": 14,
        }

        self._bridge = _MapViewBridge()
        self._bridge.viewChanged.connect(self._emit_view_changed)
        self._channel = QWebChannel(self)
        self._channel.registerObject("bridge", self._bridge)

        self._ignore_next_view_change = False

        # Current hovered point for map synchronization
        self._current_hovered_point = None
        self._points_list = []  # Store points for index lookup

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.view = QWebEngineView(self)
        self.view.page().setWebChannel(self._channel)
        layout.addWidget(self.view)

        self.view.loadFinished.connect(self._on_load_finished)

        # Initial empty map
        self._update_map(None)

    def _emit_view_changed(self, state):
        if self._ignore_next_view_change:
            self._ignore_next_view_change = False
            return
        self._last_view_state = state
        self.viewChanged.emit(state)

    def _on_load_finished(self, ok: bool):
        self._ready = bool(ok)
        if self._ready and self._pending_draw is not None:
            track, color_mode, minimum, maximum = self._pending_draw
            self._pending_draw = None
            self.draw_track(track, color_mode, minimum, maximum)

    def _get_html_template(self, m: folium.Map) -> str:
        """Get the HTML content of the folium map with added JS for sync and dynamic drawing."""
        data = io.BytesIO()
        m.save(data, close_file=False)
        html = data.getvalue().decode()

        # Inject script to catch view changes, expose sync API, and draw track dynamically
        sync_script = """
        <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
        <script>
        function setupSync() {
            var maps = [];
            // Folium usually names the map variable 'map_<some_id>'
            // We find it in the global scope
            for (var key in window) {
                if (key.startsWith('map_') && window[key] instanceof L.Map) {
                    var map = window[key];

                    window.leafletMap = map;

                    if (window.qt && window.qt.webChannelTransport) {
                        new QWebChannel(window.qt.webChannelTransport, function(channel) {
                            window.python_bridge = channel.objects.bridge;
                        });
                    }

                    window.__ignoreViewEvent = false;

                    map.on('moveend', function() {
                        if (window.__ignoreViewEvent && window.__ignoreViewEvent > 0) {
                            window.__ignoreViewEvent -= 1;
                            return;
                        }

                        var center = map.getCenter();
                        var state = {
                            center: [center.lat, center.lng],
                            zoom: map.getZoom()
                        };
                        if (window.python_bridge && window.python_bridge.onViewChanged) {
                            window.python_bridge.onViewChanged(state);
                        }
                    });

                    window.getViewState = function() {
                        var center = map.getCenter();
                        return {
                            center: [center.lat, center.lng],
                            zoom: map.getZoom()
                        };
                    };

                    window.setViewState = function(state) {
                        if (state && state.center) {
                            window.__ignoreViewEvent += 1;
                            map.setView(state.center, state.zoom, {animate: false});
                        }
                    };

                    // Dynamic drawing function
                    window.drawTrack = function(points, fitBounds) {
                        // Clear existing track layers
                        if (window.trackLayers) {
                            window.trackLayers.forEach(function(layer) {
                                map.removeLayer(layer);
                            });
                        }
                        window.trackLayers = [];

                        if (!points || points.length < 2) return;

                        // Canvas renderer for high-performance canvas path drawing
                        var canvasRenderer = L.canvas();

                        if (points.length < 2) return;

                        var currentGroup = [[points[0][0], points[0][1]], [points[1][0], points[1][1]]];
                        var currentColor = points[0][2] || 'blue';

                        for (var i = 1; i < points.length - 1; i++) {
                            var nextPoint = points[i + 1];
                            var nextColor = points[i][2] || 'blue';
                            var nextLat = nextPoint[0];
                            var nextLon = nextPoint[1];

                            if (nextColor === currentColor) {
                                currentGroup.push([nextLat, nextLon]);
                            } else {
                                var poly = L.polyline(currentGroup, {
                                    color: currentColor,
                                    weight: 5,
                                    opacity: 0.8,
                                    renderer: canvasRenderer
                                });
                                poly.addTo(map);
                                window.trackLayers.push(poly);

                                currentGroup = [[points[i][0], points[i][1]], [nextLat, nextLon]];
                                currentColor = nextColor;
                            }
                        }

                        if (currentGroup.length >= 2) {
                            var poly = L.polyline(currentGroup, {
                                color: currentColor,
                                weight: 5,
                                opacity: 0.8,
                                renderer: canvasRenderer
                            });
                            poly.addTo(map);
                            window.trackLayers.push(poly);
                        }

                        var startMarker = L.circleMarker([points[0][0], points[0][1]], {
                            radius: 8,
                            fillColor: '#34A853',
                            color: '#ffffff',
                            weight: 2,
                            opacity: 1,
                            fillOpacity: 1
                        }).addTo(map);
                        window.trackLayers.push(startMarker);

                        var endMarker = L.circleMarker([points[points.length - 1][0], points[points.length - 1][1]], {
                            radius: 8,
                            fillColor: '#EA4335',
                            color: '#ffffff',
                            weight: 2,
                            opacity: 1,
                            fillOpacity: 1
                        }).addTo(map);
                        window.trackLayers.push(endMarker);

                        if (fitBounds) {
                            var lats = points.map(function(p) { return p[0]; });
                            var lons = points.map(function(p) { return p[1]; });
                            var minLat = Math.min.apply(null, lats);
                            var maxLat = Math.max.apply(null, lats);
                            var minLon = Math.min.apply(null, lons);
                            var maxLon = Math.max.apply(null, lons);
                            map.fitBounds([[minLat, minLon], [maxLat, maxLon]]);
                        }
                    };

                    // Function to draw/update hovered point marker
                    window.drawHoveredPoint = function(pointData) {
                        // Remove existing hovered marker
                        if (window.hoveredMarker) {
                            map.removeLayer(window.hoveredMarker);
                        }

                        // Add new marker with red circle
                        window.hoveredMarker = L.circleMarker([pointData.lat, pointData.lng], {
                            radius: 8,
                            fillColor: 'red',
                            color: 'darkred',
                            weight: 2,
                            opacity: 1,
                            fillOpacity: 0.8
                        }).addTo(map);
                    };

                    // Function to clear hovered point marker
                    window.clearHoveredPoint = function() {
                        if (window.hoveredMarker) {
                            map.removeLayer(window.hoveredMarker);
                            window.hoveredMarker = null;
                        }
                    };

                    break;
                }
            }
        }
        setTimeout(setupSync, 500);
        </script>
        """
        if "</body>" in html:
            return html.replace("</body>", f"{sync_script}</body>")

        return html + sync_script

    def _update_map(self, track, color_mode: str = "Nessuna", minimum=None, maximum=None):
        # Default center and zoom
        center = [44.58333, 10.73333]
        zoom = 14

        m = folium.Map(location=center, zoom_start=zoom, control_scale=True)

        # Genera l'HTML completo applicando i fix CSS e JS
        content = self._get_html_template(m)

        # Carica l'HTML sbloccando i permessi internet con l'URL di base fittizio
        self.view.setHtml(content, QUrl("http://localhost/"))

    def draw_track(self, track, color_mode: str = "Nessuna", minimum=None, maximum=None):
        """Disegna la traccia sulla mappa Folium/Leaflet.

        Ottimizzato per ridurre il carico di calcolo durante la preparazione dei dati JSON.
        """
        if not self._ready:
            self._pending_draw = (track, color_mode, minimum, maximum)
            return

        points = getattr(track, "points", None) or []
        if not points:
            self.view.page().runJavaScript("if (window.drawTrack) window.drawTrack([], false);")
            self._points_list = []
            return

        self._points_list = points
        points_js = []
        
        # Pre-calcoliamo i valori per la colorazione se necessario
        values = []
        if color_mode != "Nessuna":
            if color_mode == "Velocità":
                for i in range(len(points) - 1):
                    values.append(calculate_point_speed(points[i], points[i+1]))
            elif color_mode == "Pendenza":
                for i in range(len(points) - 1):
                    dist = haversine_distance(points[i], points[i+1])
                    if dist > 0 and points[i].altitude is not None and points[i+1].altitude is not None:
                        values.append(((points[i+1].altitude - points[i].altitude) / dist) * 100)
                    else:
                        values.append(None)
            elif color_mode == "Frequenza cardiaca":
                for i in range(len(points) - 1):
                    hr = points[i+1].heart_rate
                    values.append(float(hr) if hr is not None else None)

        # Costruzione lista JSON ottimizzata
        for i, p in enumerate(points):
            color = "blue"
            if color_mode != "Nessuna" and i < len(values):
                val = values[i]
                color = "#808080"  # Grigio di default per dati mancanti
                if val is not None:
                    color_q = value_to_color(val, minimum or 0, maximum or 0)
                    color = color_q.name()
            
            points_js.append([p.latitude, p.longitude, color])

        js_data = json.dumps(points_js)
        self.view.page().runJavaScript(f"if (window.drawTrack) window.drawTrack({js_data}, true);")

    def get_view_state(self) -> dict[str, Any]:
        return self._last_view_state

    def get_view_state_async(self, callback):
        if not self._ready:
            callback(self._last_view_state)
            return
        self.view.page().runJavaScript(
            "window.getViewState ? window.getViewState() : null;",
            lambda result: callback(result or self._last_view_state),
        )

    def set_view_state(self, state: Any, callback=None):
        if not self._ready:
            if callback:
                callback(None)
            return

        normalized = state
        try:
            normalized = json.loads(json.dumps(state))
        except Exception:
            pass

        if normalized == self._last_view_state:
            if callback:
                callback(None)
            return

        self._ignore_next_view_change = True
        js_state = json.dumps(normalized)

        def _on_set_done(result=None):
            if callback:
                callback(result)

        self.view.page().runJavaScript(
            f"if (window.setViewState) window.setViewState({js_state});",
            _on_set_done,
        )
        self._last_view_state = normalized

    def set_hovered_point(self, point_index: int):
        """Update the hovered point marker on the map.

        Called by:
            - ``MainWindow`` when a point is hovered on the graph

        Args:
            point_index: Index of the hovered point in the track
        """
        if point_index < 0 or point_index >= len(self._points_list):
            self._current_hovered_point = None
            self.view.page().runJavaScript("if (window.clearHoveredPoint) window.clearHoveredPoint();")
            return

        self._current_hovered_point = point_index
        point = self._points_list[point_index]

        # Send point coordinates to map for marker display
        point_data = json.dumps(
            {
                "lat": point.latitude,
                "lng": point.longitude,
                "index": point_index,
            }
        )
        self.view.page().runJavaScript(f"if (window.drawHoveredPoint) window.drawHoveredPoint({point_data});")