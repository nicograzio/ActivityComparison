from PyQt6.QtGui import QColor


def value_to_color(value, minimum, maximum):
    if maximum <= minimum:
        return QColor(160, 160, 160)

    ratio = (value - minimum) / (maximum - minimum)
    ratio = max(0.0, min(1.0, ratio))

    intensity = int(235 - ratio * 140)
    intensity = max(60, min(235, intensity))
    return QColor(intensity, intensity, intensity)