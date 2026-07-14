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

    # FIT stores speed in m/s. Ignore invalid values.
    if isinstance(speed, (int, float)) and speed >= 0:
        return speed * 3.6

    time_a = getattr(previous, "timestamp", None)
    time_b = getattr(current, "timestamp", None)

    if time_a is None or time_b is None:
        return None

    try:
        seconds = (time_b - time_a).total_seconds()
    except Exception:
        return None

    if seconds <= 0:
        return None

    distance = haversine_distance(previous, current)

    if distance <= 0:
        return None

    return (distance / seconds) * 3.6


def calculate_speed_range(track):
    values = []

    for i in range(1, len(track.points)):
        speed = calculate_point_speed(track.points[i - 1], track.points[i])
        if speed is not None:
            values.append(speed)

    if not values:
        return 0.0, 0.0

    return min(values), max(values)


def calculate_slope_range(track):
    values = []

    for i in range(1, len(track.points)):
        previous = track.points[i - 1]
        current = track.points[i]

        distance = haversine_distance(previous, current)
        previous_alt = getattr(previous, "altitude", 0) or 0
        current_alt = getattr(current, "altitude", 0) or 0

        if distance > 0:
            values.append(((current_alt - previous_alt) / distance) * 100)

    if not values:
        return 0.0, 0.0

    return min(values), max(values)
