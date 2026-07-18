from PyQt6.QtGui import QColor


def value_to_color(value, minimum, maximum):
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
