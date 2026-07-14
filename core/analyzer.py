import math


def haversine_distance(a, b):
    radius = 6371000
    lat1 = math.radians(a.latitude)
    lat2 = math.radians(b.latitude)
    dlat = math.radians(b.latitude - a.latitude)
    dlon = math.radians(b.longitude - a.longitude)

    value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def calculate_point_speed(previous, current):
    speed = getattr(current, "speed", None)
    if speed is not None:
        return speed * 3.6

    time_a = getattr(previous, "timestamp", None)
    time_b = getattr(current, "timestamp", None)

    if time_a is None or time_b is None:
        return None

    seconds = (time_b - time_a).total_seconds()
    if seconds <= 0:
        return None

    return (haversine_distance(previous, current) / seconds) * 3.6


def calculate_speed_range(track):
    values = []
    points = track.points

    for i in range(1, len(points)):
        speed = calculate_point_speed(points[i - 1], points[i])
        if speed is not None:
            values.append(speed)

    if not values:
        return 0.0, 0.0

    return min(values), max(values)


def calculate_slope_range(track):
    slopes = []

    points = track.points
    for i in range(1, len(points)):
        previous = points[i - 1]
        current = points[i]

        distance = haversine_distance(previous, current)
        elevation_diff = getattr(current, "elevation", 0) - getattr(previous, "elevation", 0)

        if distance > 0:
            slopes.append((elevation_diff / distance) * 100)

    if not slopes:
        return 0.0, 0.0

    return min(slopes), max(slopes)
