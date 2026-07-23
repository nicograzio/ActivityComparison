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


def calculate_speed_series(track):
    """Build the time/speed series used by the graph widgets.

    Called by:
        - ``ui.main_window.MainWindow._update_graph``

    Args:
        track: Track to convert.

    Returns:
        tuple[list[float], list[float]]: Time samples and speed samples.
    """
    points = getattr(track, "points", [])
    if not points:
        return [], []

    times = [0.0]
    speeds = [0.0]
    first_timestamp = getattr(points[0], "timestamp", None)

    for index in range(1, len(points)):
        previous = points[index - 1]
        current = points[index]

        speed = calculate_point_speed(previous, current)
        speeds.append(0.0 if speed is None else float(speed))

        current_timestamp = getattr(current, "timestamp", None)
        if first_timestamp is not None and current_timestamp is not None:
            try:
                elapsed = (current_timestamp - first_timestamp).total_seconds()
                times.append(float(elapsed) if elapsed >= 0 else float(index))
                continue
            except Exception:
                pass

        times.append(float(index))

    return times, speeds


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
        return None, None

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
    trimmed = Track(track.name)
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