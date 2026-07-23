"""MapLibre bridge foundation.

This is the first abstraction layer for the vector renderer. It keeps the
track-to-GeoJSON conversion logic isolated from the web view widget.

Called by:
    - future MapLibre-facing widgets

Current status:
    The module is mostly a compatibility foundation while the vector renderer
    is being introduced incrementally.
"""

import json


class MapLibreBridge:
    """Bridge between activity data and a vector map frontend.

    Methods in this bridge are intentionally small and serializable so they can
    be reused by the web-view renderer or future vector renderers.
    """

    def __init__(self):
        """Initialize the bridge in a not-ready state."""
        self.ready = False
        self.layers = {}

    def set_ready(self, value=True):
        """Mark the bridge as ready for rendering calls.

        Called by:
            - potential renderer bootstrap code
        """
        self.ready = value

    def track_geojson(self, points):
        """Create a GeoJSON LineString from a list of track points.

        Called by:
            - ``set_track_layer``

        Args:
            points: Iterable of points with latitude/longitude attributes.

        Returns:
            dict: GeoJSON feature.
        """
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
        """Store the current track layer and serialize it as JSON.

        Called by:
            - future vector-renderer integration code

        Returns:
            str: Serialized track layer.
        """
        self.layers["track"] = self.track_geojson(points)
        return json.dumps(self.layers["track"])