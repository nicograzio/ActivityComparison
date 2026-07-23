"""Tile downloading and in-memory caching for the fallback map renderer.

The fallback OSM renderer relies on this helper to retrieve tiles on demand.

Called by:
    - ``ui.map_widget.MapWidget`` when a raster map is active
"""

import requests


class TileLoader:
    """Download OSM tiles and reuse them from an in-memory cache.

    Called by:
        - the fallback map renderer when a tile is missing
    """

    def __init__(self):
        """Create an empty tile cache.

        Returns:
            None.
        """
        self.cache = {}

    def get_tile(self, x, y, zoom):
        """Return the PNG bytes of a single OSM tile.

        Called by:
            - the fallback map renderer when it needs one tile

        Args:
            x: Tile X index.
            y: Tile Y index.
            zoom: Zoom level.

        Returns:
            bytes: PNG tile content.
        """
        key = (x, y, zoom)

        if key in self.cache:
            return self.cache[key]

        url = (
            f"https://tile.openstreetmap.org/"
            f"{zoom}/{x}/{y}.png"
        )

        response = requests.get(url, timeout=10)
        response.raise_for_status()

        self.cache[key] = response.content

        return response.content