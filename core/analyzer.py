"""Core numerical helpers for activity comparison.

This module centralizes distance, speed, slope, distance profiles and track
trimming so that UI widgets only orchestrate the flow.

Called by:
    - ``ui.track_panel.TrackPanel`` for rendering and trim updates
    - ``ui.main_window.MainWindow`` for graph generation and scale sync
    - map renderers for segment coloring

Consumes:
    - ``core.track.Track`` and ``core.track.TrackPoint``
"""

import math
from datetime import timedelta

from core.track import Track, TrackPoint


def haversine_distance(a, b):
    """Return the geodesic distance between two points in meters.

    Called by:
        - ``calculate_point_speed``
        - ``calculate_slope_range``
        - ``track_distance_profile``
        - ``trim_track_by_distance``
        - map renderers when evaluating segment values

    Args:
        a: First point with ``latitude`` and ``longitude``.
        b: Second point with ``latitude`` and ``longitude``.

    Returns:
        float: Distance in meters.
    """
    radius = 6371000
    lat1 = math.radians(a.latitude)
    lat2 = math.radians(b.latitude)
    dlat = math.radians(b.latitude - a.latitude)
    dlon = math.radians(b.longitude - a.longitude)

    value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def calculate_point_speed(previous, current):
    """Calculate the speed of a segment in km/h.

    If the source already provides speed and it is non-negative, that value is
    reused. Otherwise speed is derived from distance and timestamp delta.

    Called by:
        - ``calculate_speed_series``
        - ``calculate_speed_range``
        - ``ui.map_widget.MapWidget.get_segment_value``
        - ``ui.vector_map_widget.VectorMapWidget._segment_value``

    Args:
        previous: Previous track point.
        current: Current track point.

    Returns:
        float | None: Speed in km/h, or ``None`` when it cannot be computed.
    """
    speed = getattr(current, "speed", None)

    if isinstance(speed, (int, float)) and speed >= 0:
        return speed * 3.6

    time_a = getattr(previous, "timestamp", None)
    time_b = getattr(current, "timestamp", None)

    if time_a is None or time_b is None:
        return None

    try:
        seconds = (time_b - time_a).total_seconds()
        if seconds <= 0:
            return None

        distance = haversine_distance(previous, current)
        if distance <= 0:
            return None

        return (distance / seconds) * 3.6
    except Exception:
        return None


def calculate_track_series(track, x_axis_mode="Tempo", first_timestamp=None, start_distance_m=0.0):
    """Build the series data for the graph widget.

    Args:
        track: Track to convert.
        x_axis_mode: "Tempo" or "Distanza".
        first_timestamp: Original start timestamp of the full track to calculate real time offset.
        start_distance_m: Original start distance offset in meters.

    Returns:
        tuple[list[float], list[float], list[float], list[float]]: X-axis samples, speeds, altitudes, and heart rates.
    """
    points = getattr(track, "points", [])
    if not points:
        return [], [], [], []

    x_values = []
    speeds = []
    altitudes = []
    heart_rates = []

    # Calculate distance profile for the current segment to handle relative distances
    segment_distances, _ = track_distance_profile(track)

    for index in range(len(points)):
        current = points[index]
        
        # Speed calculation
        if index == 0:
            if len(points) > 1:
                speed = calculate_point_speed(points[0], points[1])
            else:
                speed = getattr(current, "speed", 0.0)
                if speed is not None: speed *= 3.6
        else:
            speed = calculate_point_speed(points[index-1], current)
        
        speeds.append(float(speed) if speed is not None else 0.0)

        # Altitude
        alt = getattr(current, "altitude", None)
        altitudes.append(float(alt) if alt is not None else None)

        # Heart rate
        hr = getattr(current, "heart_rate", None)
        heart_rates.append(int(hr) if hr is not None else None)

        # X-axis value calculation
        if x_axis_mode == "Distanza":
            # Real distance = offset + distance within this segment
            x_values.append((start_distance_m + segment_distances[index]) / 1000.0)
        else:
            # Time calculation
            current_timestamp = getattr(current, "timestamp", None)
            if first_timestamp is not None and current_timestamp is not None:
                try:
                    elapsed = (current_timestamp - first_timestamp).total_seconds()
                    x_values.append(float(elapsed))
                except Exception:
                    x_values.append(float(index))
            else:
                x_values.append(float(index))

    # Handle missing/None values gracefully for altitude and heart rate by forward/backward filling
    # checking if they are present at all.
    has_altitude = any(alt is not None for alt in altitudes)
    cleaned_altitudes = []
    if has_altitude:
        last_valid_alt = 0.0
        for alt in altitudes:
            if alt is not None:
                last_valid_alt = alt
                break
        for alt in altitudes:
            if alt is not None:
                last_valid_alt = alt
            cleaned_altitudes.append(last_valid_alt)
    else:
        cleaned_altitudes = [0.0] * len(points)

    has_heart_rate = any(hr is not None for hr in heart_rates)
    cleaned_heart_rates = []
    if has_heart_rate:
        last_valid_hr = 0.0
        for hr in heart_rates:
            if hr is not None:
                last_valid_hr = float(hr)
                break
        for hr in heart_rates:
            if hr is not None:
                last_valid_hr = float(hr)
            cleaned_heart_rates.append(last_valid_hr)
    else:
        cleaned_heart_rates = [0.0] * len(points)

    return x_values, speeds, cleaned_altitudes, cleaned_heart_rates


def calculate_speed_series(track):
    """Build the time/speed series used by the graph widgets (legacy wrapper)."""
    first_ts = track.points[0].timestamp if track.points else None
    x_val, spd, _, _ = calculate_track_series(track, x_axis_mode="Tempo", first_timestamp=first_ts)
    return x_val, spd


def calculate_speed_range(track):
    """Return the min/max speed of a track in km/h.

    Called by:
        - ``ui.track_panel.TrackPanel._current_scale_limits``
        - ``ui.track_panel.TrackPanel.visible_speed_range``

    Args:
        track: Track to inspect.

    Returns:
        tuple[float | None, float | None]: Minimum and maximum speed.
    """
    values = []

    for i in range(1, len(track.points)):
        speed = calculate_point_speed(track.points[i - 1], track.points[i])
        if speed is not None:
            values.append(speed)

    if not values:
        return None, None

    return min(values), max(values)


def calculate_slope_range(track):
    """Return the min/max slope percentage for a track.

    Called by:
        - ``ui.track_panel.TrackPanel._current_scale_limits``

    Args:
        track: Track to inspect.

    Returns:
        tuple[float | None, float | None]: Minimum and maximum slope.
    """
    points = getattr(track, "points", [])
    if len(points) < 2:
        return None, None

    # Apply a moving average window to smooth altitude data and reduce noise
    window_size = 11
    altitudes = [getattr(p, "altitude", 0) or 0 for p in points]
    smoothed_altitudes = []
    for i in range(len(altitudes)):
        start = max(0, i - window_size // 2)
        end = min(len(altitudes), i + window_size // 2 + 1)
        window = altitudes[start:end]
        smoothed_altitudes.append(sum(window) / len(window))

    values = []
    for i in range(1, len(points)):
        previous = points[i - 1]
        current = points[i]

        distance = haversine_distance(previous, current)
        # Use a minimum distance threshold to avoid extreme slope values from GPS noise
        if distance > 5.0:
            previous_alt = smoothed_altitudes[i - 1]
            current_alt = smoothed_altitudes[i]
            values.append(((current_alt - previous_alt) / distance) * 100)

    if not values:
        # Fallback if all segments are too short
        return 0.0, 0.0

    return min(values), max(values)


def track_distance_profile(track):
    """Return cumulative distance samples and the total distance.

    Called by:
        - ``ui.track_panel.TrackPanel.import_file``
        - ``trim_track_by_distance``

    Args:
        track: Track to analyze.

    Returns:
        tuple[list[float], float]: Cumulative distance samples and total meters.
    """
    distances = [0.0]
    total = 0.0

    for i in range(1, len(track.points)):
        segment = haversine_distance(track.points[i - 1], track.points[i])
        if segment > 0:
            total += segment
        distances.append(total)

    return distances, total


def _interpolate_number(start, end, fraction):
    """Interpolate a scalar value between two endpoints.

    Called by:
        - ``_interpolate_point``

    Returns:
        interpolated value or ``None``.
    """
    if start is None or end is None:
        return None
    try:
        return start + (end - start) * fraction
    except Exception:
        return None


def _interpolate_timestamp(start, end, fraction):
    """Interpolate a timestamp between two endpoints.

    Called by:
        - ``_interpolate_point``

    Returns:
        interpolated timestamp or ``None``.
    """
    if start is None or end is None:
        return None
    try:
        delta = end - start
        if isinstance(delta, timedelta):
            return start + delta * fraction
    except Exception:
        pass
    return None


def _interpolate_point(previous, current, fraction):
    """Create a synthetic point between two samples.

    Called by:
        - ``trim_track_by_distance`` when the trim boundary cuts a segment

    Args:
        previous: First endpoint.
        current: Second endpoint.
        fraction: Fraction of the segment where the synthetic point lies.

    Returns:
        TrackPoint: interpolated point.
    """
    altitude = _interpolate_number(previous.altitude, current.altitude, fraction)
    speed = _interpolate_number(previous.speed, current.speed, fraction)
    heart_rate = _interpolate_number(previous.heart_rate, current.heart_rate, fraction)
    timestamp = _interpolate_timestamp(previous.timestamp, current.timestamp, fraction)

    if heart_rate is not None:
        heart_rate = int(round(heart_rate))

    return TrackPoint(
        latitude=previous.latitude + (current.latitude - previous.latitude) * fraction,
        longitude=previous.longitude + (current.longitude - previous.longitude) * fraction,
        altitude=altitude,
        timestamp=timestamp,
        speed=speed,
        heart_rate=heart_rate,
    )


def trim_track_by_distance(track, start_distance_m, end_distance_m):
    """Return a new track trimmed to a distance interval.

    Called by:
        - ``ui.track_panel.TrackPanel._visible_track``

    Args:
        track: Original track.
        start_distance_m: Start of the visible interval, in meters.
        end_distance_m: End of the visible interval, in meters.

    Returns:
        Track: Trimmed track.
    """
    trimmed = Track(track.name, start_distance_m=start_distance_m)
    points = track.points

    if len(points) < 2:
        for point in points:
            trimmed.add_point(point)
        return trimmed

    profile, total_distance = track_distance_profile(track)
    if total_distance <= 0:
        for point in points:
            trimmed.add_point(point)
        return trimmed

    start_distance_m = max(0.0, min(start_distance_m, total_distance))
    end_distance_m = max(start_distance_m, min(end_distance_m, total_distance))

    if start_distance_m <= 0 and end_distance_m >= total_distance:
        for point in points:
            trimmed.add_point(point)
        return trimmed

    def append_point(point):
        if not trimmed.points:
            trimmed.add_point(point)
            return
        last = trimmed.points[-1]
        if (
            last.latitude != point.latitude
            or last.longitude != point.longitude
            or last.altitude != point.altitude
            or last.timestamp != point.timestamp
            or last.speed != point.speed
            or last.heart_rate != point.heart_rate
        ):
            trimmed.add_point(point)

    for i in range(1, len(points)):
        previous = points[i - 1]
        current = points[i]
        start_segment = profile[i - 1]
        end_segment = profile[i]

        if end_segment < start_distance_m:
            continue
        if start_segment > end_distance_m:
            break

        segment_distance = end_segment - start_segment
        if segment_distance <= 0:
            continue

        if start_segment < start_distance_m <= end_segment:
            fraction = (start_distance_m - start_segment) / segment_distance
            append_point(_interpolate_point(previous, current, fraction))
        elif start_distance_m <= start_segment and not trimmed.points:
            append_point(previous)

        if end_segment <= end_distance_m:
            append_point(current)
        else:
            fraction = (end_distance_m - start_segment) / segment_distance
            append_point(_interpolate_point(previous, current, fraction))
            break

    if not trimmed.points:
        append_point(points[0])

    return trimmed
