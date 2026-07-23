"""Coordinate projection helpers for OSM/MapLibre rendering.

This module converts latitude/longitude coordinates into Web Mercator pixel
coordinates.

Called by:
    - map tile and overlay renderers when projecting tracks on the map
"""

import math


# Web Mercator conversion foundation for OpenStreetMap tiles.


def latlon_to_world(latitude, longitude, zoom):
    """Convert a geographic coordinate to world pixel coordinates.

    Called by:
        - map renderers when aligning tiles and overlays

    Args:
        latitude: Latitude in decimal degrees.
        longitude: Longitude in decimal degrees.
        zoom: Zoom level.

    Returns:
        tuple[float, float]: World pixel coordinates.
    """
    scale = 256 * (2 ** zoom)

    x = (longitude + 180.0) / 360.0 * scale
    latitude_rad = math.radians(latitude)
    y = (
        1
        - math.asinh(math.tan(latitude_rad)) / math.pi
    ) / 2 * scale

    return x, y