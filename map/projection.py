import math


# Web Mercator conversion foundation for OpenStreetMap tiles.


def latlon_to_world(latitude, longitude, zoom):
    scale = 256 * (2 ** zoom)

    x = (longitude + 180.0) / 360.0 * scale

    latitude_rad = math.radians(latitude)
    y = (
        1
        - math.asinh(math.tan(latitude_rad)) / math.pi
    ) / 2 * scale

    return x, y
