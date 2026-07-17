"""
MapLibre vector map bridge foundation.

This module contains the first abstraction layer for the future vector
renderer. The existing QGraphicsView raster renderer remains active while
MapLibre integration is introduced incrementally.
"""

import json


class MapLibreBridge:
    """Bridge between activity data and a vector map frontend."""

    def __init__(self):
        self.ready = False
        self.layers = {}

    def set_ready(self, value=True):
        self.ready = value

    def track_geojson(self, points):
        """Create a GeoJSON LineString from track points."""
        return {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [
                    [p.longitude, p.latitude] for p in points
                ],
            },
        }

    def set_track_layer(self, points):
        self.layers["track"] = self.track_geojson(points)
        return json.dumps(self.layers["track"])
