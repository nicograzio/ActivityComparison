from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import QVBoxLayout, QWidget

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
except Exception as exc:  # pragma: no cover
    QWebEngineView = None  # type: ignore[assignment]
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None

from core.analyzer import calculate_point_speed, haversine_distance
from core.colorizer import value_to_color

if QWebEngineView is None:
    raise ImportError("PyQt6.QtWebEngineWidgets is required for the vector map renderer") from _IMPORT_ERROR


class VectorMapWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.show_points = False
        self._ready = False
        self._pending_payload: dict[str, Any] | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.view = QWebEngineView(self)
        self.view.setZoomFactor(1.0)
        self.view.settings().setAttribute(
            self.view.settings().WebAttribute.ShowScrollBars,
            False
        )
        layout.addWidget(self.view)

        index_path = Path(__file__).resolve().parent / "maplibre" / "index.html"
        self.view.setUrl(QUrl.fromLocalFile(str(index_path)))
        self.view.loadFinished.connect(self._on_load_finished)

    def _on_load_finished(self, ok: bool):
        self._ready = bool(ok)
        if self._ready and self._pending_payload is not None:
            self._push_track_payload(self._pending_payload)
            self._pending_payload = None

    def _run_js(self, script: str):
        self.view.page().runJavaScript(script)

    def _push_track_payload(self, payload: dict[str, Any]):
        self._run_js(f"window.appMap && window.appMap.setTrack({json.dumps(payload, ensure_ascii=False)});")

    def clear_track(self):
        if not self._ready:
            self._pending_payload = None
            return
        self._run_js("window.appMap && window.appMap.clearTrack();")

    @staticmethod
    def _qcolor_to_hex(color: Any) -> str:
        try:
            return color.name()
        except Exception:
            return "#808080"

    @staticmethod
    def _segment_slope(previous: Any, current: Any):
        distance = haversine_distance(previous, current)
        if distance <= 0:
            return None
        previous_alt = getattr(previous, "altitude", None)
        current_alt = getattr(current, "altitude", None)
        if previous_alt is None or current_alt is None:
            return None
        return ((current_alt - previous_alt) / distance) * 100

    def _segment_value(self, previous: Any, current: Any, color_mode: str):
        if color_mode == "Velocità":
            return calculate_point_speed(previous, current)
        if color_mode == "Pendenza":
            return self._segment_slope(previous, current)
        return None

    def draw_track(self, track, color_mode: str = "Nessuna", minimum=None, maximum=None):
        points = getattr(track, "points", None) or []
        if len(points) < 2:
            self.clear_track()
            return

        line_features = []
        point_features = []
        latitudes = []
        longitudes = []

        for index in range(1, len(points)):
            previous = points[index - 1]
            current = points[index]
            if any(getattr(p, "latitude", None) is None or getattr(p, "longitude", None) is None for p in (previous, current)):
                continue

            latitudes.extend([previous.latitude, current.latitude])
            longitudes.extend([previous.longitude, current.longitude])

            value = self._segment_value(previous, current, color_mode)
            color = "#808080"
            if color_mode in ("Velocità", "Pendenza") and value is not None:
                color = self._qcolor_to_hex(value_to_color(value, minimum or 0, maximum or 0))

            line_features.append({
                "type": "Feature",
                "properties": {"color": color},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[previous.longitude, previous.latitude], [current.longitude, current.latitude]]
                }
            })

        payload = {
            "geojson": {"type": "FeatureCollection", "features": line_features},
            "points": point_features,
            "bounds": [[min(longitudes), min(latitudes)], [max(longitudes), max(latitudes)]],
            "maxZoom": 18,
        }

        if not self._ready:
            self._pending_payload = payload
            return
        self._push_track_payload(payload)
