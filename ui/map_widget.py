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
from PyQt6.QtCore import QUrl, pyqtSignal
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from core.analyzer import calculate_point_speed, haversine_distance
from core.colorizer import value_to_color


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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.view = QWebEngineView(self)
        layout.addWidget(self.view)

        self.view.loadFinished.connect(self._on_load_finished)
        
        # Initial empty map
        self._update_map(None)

    def _emit_view_changed(self, state):
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
        <script>
        function setupSync() {
            var maps = [];
            // Folium usually names the map variable 'map_<some_id>'
            // We find it in the global scope
            for (var key in window) {
                if (key.startsWith('map_') && window[key] instanceof L.Map) {
                    var map = window[key];
                    
                    window.leafletMap = map;
                    
                    map.on('moveend', function() {
                        var center = map.getCenter();
                        var state = {
                            center: [center.lat, center.lng],
                            zoom: map.getZoom()
                        };
                        if (window.python_bridge) {
                            // If we had a QWebChannel, we'd use it here.
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

                        var currentGroup = [[points[0][0], points[0][1]]];
                        var currentColor = points[0][2] || 'blue';

                        for (var i = 1; i < points.length; i++) {
                            var p = points[i];
                            var lat = p[0];
                            var lon = p[1];
                            var color = p[2] || 'blue';

                            if (color === currentColor) {
                                currentGroup.push([lat, lon]);
                            } else {
                                var poly = L.polyline(currentGroup, {
                                    color: currentColor,
                                    weight: 5,
                                    opacity: 0.8,
                                    renderer: canvasRenderer
                                });
                                poly.addTo(map);
                                window.trackLayers.push(poly);

                                currentGroup = [[points[i-1][0], points[i-1][1]], [lat, lon]];
                                currentColor = color;
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
        if not self._ready:
            self._pending_draw = (track, color_mode, minimum, maximum)
            return

        points = getattr(track, "points", None) or []
        if not points:
            self.view.page().runJavaScript("if (window.drawTrack) window.drawTrack([], false);")
            return

        points_js = []
        for i in range(len(points)):
            p = points[i]
            color = "blue"
            if color_mode != "Nessuna" and i < len(points) - 1:
                p1 = p
                p2 = points[i+1]
                val = None
                if color_mode == "Velocità":
                    val = calculate_point_speed(p1, p2)
                elif color_mode == "Pendenza":
                    dist = haversine_distance(p1, p2)
                    if dist > 0 and p1.altitude is not None and p2.altitude is not None:
                        val = ((p2.altitude - p1.altitude) / dist) * 100
                
                color = "#808080"
                if val is not None:
                    color_q = value_to_color(val, minimum or 0, maximum or 0)
                    color = color_q.name()
            
            points_js.append([p.latitude, p.longitude, color])

        js_data = json.dumps(points_js)
        # We always fit bounds when drawing the track to make sure the view matches
        self.view.page().runJavaScript(f"if (window.drawTrack) window.drawTrack({js_data}, true);")

    def get_view_state(self) -> dict[str, Any]:
        return self._last_view_state

    def set_view_state(self, state: Any):
        if not self._ready:
            return
        
        js_state = json.dumps(state)
        self.view.page().runJavaScript(f"if (window.setViewState) window.setViewState({js_state});")
        self._last_view_state = state
