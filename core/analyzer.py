def calculate_speed_range(track):
    values = [p.speed for p in track.points if getattr(p, "speed", None) is not None]

    if not values:
        return 0.0, 0.0

    return min(values) * 3.6, max(values) * 3.6


def calculate_slope_range(track):
    slopes = []

    points = track.points
    for i in range(1, len(points)):
        previous = points[i - 1]
        current = points[i]

        distance = getattr(current, "distance", None)
        elevation_diff = getattr(current, "elevation", 0) - getattr(previous, "elevation", 0)

        if distance and distance > 0:
            slopes.append((elevation_diff / distance) * 100)

    if not slopes:
        return 0.0, 0.0

    return min(slopes), max(slopes)
