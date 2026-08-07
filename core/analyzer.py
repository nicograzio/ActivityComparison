"""Modulo per l'analisi numerica delle tracce e calcolo delle metriche.

Centralizza i calcoli di distanza (Haversine), velocità, pendenza e la gestione
dei profili per grafici e mappe, sfruttando NumPy per le prestazioni.
"""

import math
from datetime import timedelta
from typing import List, Tuple, Optional, Any
import numpy as np

from core.track import Track, TrackPoint


def haversine_distance(a: Any, b: Any) -> float:
    """Calcola la distanza geodesica in metri tra due punti GPS singoli.

    Args:
        a: Primo punto con attributi ``latitude`` e ``longitude``.
        b: Secondo punto con attributi ``latitude`` e ``longitude``.

    Returns:
        float: Distanza in metri.
    """
    radius = 6371000.0
    lat1 = math.radians(a.latitude)
    lat2 = math.radians(b.latitude)
    dlat = math.radians(b.latitude - a.latitude)
    dlon = math.radians(b.longitude - a.longitude)

    val = math.sin(dlat / 2.0) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2
    val = max(0.0, min(1.0, val))
    return radius * 2.0 * math.atan2(math.sqrt(val), math.sqrt(1.0 - val))


def haversine_distances_np(lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    """Calcola in modo vettoriale le distanze (in metri) tra punti adiacenti.

    Args:
        lats: Array NumPy delle latitudini in gradi decimali.
        lons: Array NumPy delle longitudini in gradi decimali.

    Returns:
        np.ndarray: Array di lunghezza N-1 con le distanze dei segmenti.
    """
    if len(lats) < 2:
        return np.array([], dtype=np.float64)

    radius = 6371000.0
    lats_rad = np.radians(lats)
    lons_rad = np.radians(lons)

    dlat = lats_rad[1:] - lats_rad[:-1]
    dlon = lons_rad[1:] - lons_rad[:-1]

    a = np.sin(dlat / 2.0) ** 2 + np.cos(lats_rad[:-1]) * np.cos(lats_rad[1:]) * np.sin(dlon / 2.0) ** 2
    np.clip(a, 0.0, 1.0, out=a)
    return radius * 2.0 * np.arctan2(np.sqrt(a), np.sqrt(1.0 - a))


def calculate_point_speed(previous: TrackPoint, current: TrackPoint) -> Optional[float]:
    """Calcola la velocità di un segmento in km/h.

    Se la velocità è già disponibile sul punto corrente, viene riutilizzata (* 3.6).
    Altrimenti viene derivata dalla distanza e dalla differenza di tempo.

    Args:
        previous: Punto precedente della traccia.
        current: Punto corrente della traccia.

    Returns:
        Optional[float]: Velocità in km/h oppure ``None`` se non calcolabile.
    """
    speed = getattr(current, "speed", None)

    if isinstance(speed, (int, float)) and speed >= 0:
        return float(speed * 3.6)

    time_a = getattr(previous, "timestamp", None)
    time_b = getattr(current, "timestamp", None)

    if time_a is None or time_b is None:
        return None

    try:
        seconds = (time_b - time_a).total_seconds()
        if seconds <= 0:
            return None

        dist = haversine_distance(previous, current)
        if dist <= 0:
            return None

        return float((dist / seconds) * 3.6)
    except Exception:
        return None


def track_distance_profile(track: Track) -> Tuple[List[float], float]:
    """Restituisce le distanze cumulative dei punti e la distanza totale in metri.

    Args:
        track: Traccia da analizzare.

    Returns:
        Tuple[List[float], float]: Campioni di distanza cumulativa e totale in metri.
    """
    points = getattr(track, "points", [])
    if len(points) < 2:
        return [0.0] * len(points), 0.0

    segment_distances = haversine_distances_np(track.latitudes, track.longitudes)
    cum_distances = np.zeros(len(points), dtype=np.float64)
    cum_distances[1:] = np.cumsum(segment_distances)

    total_distance = float(cum_distances[-1])
    return cum_distances.tolist(), total_distance



def calculate_track_series(
    track: Track,
    x_axis_mode: str = "Tempo",
    first_timestamp: Optional[Any] = None,
    start_distance_m: float = 0.0,
) -> Tuple[List[float], List[float], List[float], List[float]]:
    """Genera le serie di dati per i grafici della UI (asse X, velocità, altitudine, frequenza cardiaca).

    Args:
        track: Traccia da convertire.
        x_axis_mode: Modalità asse X ("Tempo" o "Distanza").
        first_timestamp: Timestamp di inizio della traccia completa.
        start_distance_m: Offset iniziale di distanza in metri.

    Returns:
        Tuple[List[float], List[float], List[float], List[float]]:
            Valori asse X, velocità (km/h), altitudini (m), frequenze cardiache (bpm).
    """
    points = getattr(track, "points", [])
    if not points:
        return [], [], [], []

    n_points = len(points)

    # 1. Calcolo Velocità per ciascun punto
    speeds = np.zeros(n_points, dtype=np.float64)
    if n_points > 1:
        spd0 = calculate_point_speed(points[0], points[1])
        speeds[0] = spd0 if spd0 is not None else 0.0

        for i in range(1, n_points):
            spd = calculate_point_speed(points[i - 1], points[i])
            speeds[i] = spd if spd is not None else 0.0
    else:
        spd0 = getattr(points[0], "speed", 0.0)
        speeds[0] = (spd0 * 3.6) if spd0 is not None else 0.0

    # 2. Estrazione ed eventuale riempimento di Altitudine e Frequenza Cardiaca
    alt_arr = track.altitudes
    hr_arr = track.heart_rates

    cleaned_altitudes = _fill_missing_values(alt_arr)
    cleaned_heart_rates = _fill_missing_values(hr_arr)

    # 3. Calcolo dell'asse X
    if x_axis_mode == "Distanza":
        segment_distances, _ = track_distance_profile(track)
        x_values = [(start_distance_m + dist) / 1000.0 for dist in segment_distances]
    else:
        x_values = []
        for index in range(n_points):
            ts = getattr(points[index], "timestamp", None)
            if first_timestamp is not None and ts is not None:
                try:
                    elapsed = (ts - first_timestamp).total_seconds()
                    x_values.append(float(elapsed))
                except Exception:
                    x_values.append(float(index))
            else:
                x_values.append(float(index))

    return x_values, speeds.tolist(), cleaned_altitudes.tolist(), cleaned_heart_rates.tolist()


def _fill_missing_values(arr: np.ndarray) -> np.ndarray:
    """Helper interno per il riempimento di valori NaN (forward & backward fill)."""
    valid_mask = ~np.isnan(arr)
    if not np.any(valid_mask):
        return np.zeros(len(arr), dtype=np.float64)

    out = arr.copy()
    valid_indices = np.where(valid_mask)[0]

    # Riempimento iniziale (backward fill)
    first_valid_idx = valid_indices[0]
    out[:first_valid_idx] = out[first_valid_idx]

    # Riempimento in avanti (forward fill)
    for i in range(first_valid_idx + 1, len(out)):
        if np.isnan(out[i]):
            out[i] = out[i - 1]

    return out


def calculate_speed_series(track: Track) -> Tuple[List[float], List[float]]:
    """Costruisce le serie di tempo/velocità per il widget del grafico."""
    first_ts = track.points[0].timestamp if track.points else None
    x_val, spd, _, _ = calculate_track_series(track, x_axis_mode="Tempo", first_timestamp=first_ts)
    return x_val, spd


def calculate_speed_range(track: Track) -> Tuple[Optional[float], Optional[float]]:
    """Restituisce la velocità minima e massima di una traccia in km/h."""
    points = getattr(track, "points", [])
    if len(points) < 2:
        return None, None

    speeds = []
    for i in range(1, len(points)):
        spd = calculate_point_speed(points[i - 1], points[i])
        if spd is not None:
            speeds.append(spd)

    if not speeds:
        return None, None

    return float(min(speeds)), float(max(speeds))


def calculate_slope_range(track: Track) -> Tuple[Optional[float], Optional[float]]:
    """Restituisce la pendenza minima e massima in percentuale."""
    points = getattr(track, "points", [])
    if len(points) < 2:
        return None, None

    altitudes = _fill_missing_values(track.altitudes)

    # Media mobile su finestra di 11 campioni per smussare il rumore altimetrico del GPS.
    # Usiamo padding sui bordi per evitare che l'algoritmo consideri valori esterni pari a zero.
    window_size = 11
    if len(altitudes) < window_size:
        window = np.ones(len(altitudes), dtype=np.float64) / float(len(altitudes))
        smoothed_altitudes = np.convolve(altitudes, window, mode='same')
    else:
        pad_size = window_size // 2
        padded_altitudes = np.pad(altitudes, pad_size, mode='edge')
        window = np.ones(window_size, dtype=np.float64) / float(window_size)
        smoothed_altitudes = np.convolve(padded_altitudes, window, mode='valid')

    # Distanze dei segmenti
    distances = haversine_distances_np(track.latitudes, track.longitudes)

    # Maschera per considerare solo segmenti con distanza sufficiente (> 5.0m)
    valid_mask = distances > 5.0
    if not np.any(valid_mask):
        return 0.0, 0.0

    delta_alt = smoothed_altitudes[1:] - smoothed_altitudes[:-1]
    slopes = (delta_alt[valid_mask] / distances[valid_mask]) * 100.0

    if len(slopes) == 0:
        return 0.0, 0.0

    return float(np.min(slopes)), float(np.max(slopes))


def _interpolate_number(start: Optional[float], end: Optional[float], fraction: float) -> Optional[float]:
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
