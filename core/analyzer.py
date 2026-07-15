import math

DEBUG_SPEED = True


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
    calculated = None

    if isinstance(speed, (int, float)) and speed >= 0:
        calculated = speed * 3.6
    else:
        time_a = getattr(previous, "timestamp", None)
        time_b = getattr(current, "timestamp", None)

        if time_a is not None and time_b is not None:
            try:
                seconds = (time_b - time_a).total_seconds()
                if seconds > 0:
                    distance = haversine_distance(previous, current)
                    if distance > 0:
                        calculated = (distance / seconds) * 3.6
            except Exception as error:
                if DEBUG_SPEED:
                    print("Speed calculation error:", error)

    if DEBUG_SPEED:
        print(
            "SPEED DEBUG |",
            "raw=", speed,
            "time=", getattr(current, "timestamp", None),
            "calculated_kmh=", calculated
        )

    return calculated


def calculate_speed_range(track):
    values = []

    print("=== SPEED RANGE DEBUG ===")
    print("Track points:", len(track.points))

    for i in range(1, len(track.points)):
        speed = calculate_point_speed(track.points[i - 1], track.points[i])
        if speed is not None:
            values.append(speed)

    print("Valid speed values:", len(values))

    if not values:
        print("No speed values found")
        return 0.0, 0.0

    minimum = min(values)
    maximum = max(values)

    print("Speed min km/h:", minimum)
    print("Speed max km/h:", maximum)
    print("First values:", values[:10])

    return minimum, maximum


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
