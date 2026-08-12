"""Color scale helpers used by the map renderers.

The UI uses this module to map a normalized numeric value to a QColor.

Called by:
    - ``ui.map_widget.MapWidget``
    - ``ui.vector_map_widget.VectorMapWidget``
"""

from PyQt6.QtGui import QColor


def value_to_color(value, minimum, maximum, mode=None):
    """Map a numeric value to a color gradient.

    Args:
        value: Current value to colorize.
        minimum: Lower bound of the scale.
        maximum: Upper bound of the scale.
        mode: Optional color mode (e.g., "Frequenza cardiaca").

    Returns:
        QColor: A color in the gradient.
    """
    if maximum <= minimum:
        if mode == "Frequenza cardiaca":
            return QColor(255, 255, 255)
        return QColor(255, 255, 0)

    ratio = (value - minimum) / (maximum - minimum)
    ratio = max(0.0, min(1.0, ratio))

    if mode == "Frequenza cardiaca":
        # Gradient from white (255, 255, 255) to red (255, 0, 0)
        # R stays at 255
        # G and B go from 255 down to 0
        gb = int((1 - ratio) * 255)
        return QColor(255, gb, gb)

    # Default green-yellow-red gradient
    if ratio < 0.5:
        r = int(ratio * 2 * 255)
        g = 255
    else:
        r = 255
        g = int((1 - (ratio - 0.5) * 2) * 255)

    return QColor(r, g, 0)
