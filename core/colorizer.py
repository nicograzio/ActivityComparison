"""Color scale helpers used by the map renderers.

The UI uses this module to map a normalized numeric value to a QColor.

Called by:
    - ``ui.map_widget.MapWidget``
    - ``ui.vector_map_widget.VectorMapWidget``
"""

from PyQt6.QtGui import QColor


def value_to_color(value, minimum, maximum):
    """Map a numeric value to a green-yellow-red gradient.

    Called by:
        - map renderers when drawing speed or slope segments

    Args:
        value: Current value to colorize.
        minimum: Lower bound of the scale.
        maximum: Upper bound of the scale.

    Returns:
        QColor: A color in the gradient.
    """
    if maximum <= minimum:
        return QColor(255, 255, 0)

    ratio = (value - minimum) / (maximum - minimum)
    ratio = max(0.0, min(1.0, ratio))

    if ratio < 0.5:
        r = int(ratio * 2 * 255)
        g = 255
    else:
        r = 255
        g = int((1 - (ratio - 0.5) * 2) * 255)

    return QColor(r, g, 0)