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
def find_common_segments(track_a: Track, track_b: Track, distance_threshold_m: float = 15.0, min_segment_length_m: float = 20.0) -> List[dict]:
    """Individua i segmenti geograficamente comuni tra due tracce.

    Sfrutta un campionamento spaziale e raggruppa i tratti consecutivi che corrono
    a una distanza inferiore a ``distance_threshold_m``.
    """
    if not track_a or not track_b or len(track_a.points) < 2 or len(track_b.points) < 2:
        return []

    lats_a, lons_a = track_a.latitudes, track_a.longitudes
    lats_b, lons_b = track_b.latitudes, track_b.longitudes

    profile_a, total_dist_a = track_distance_profile(track_a)
    profile_b, total_dist_b = track_distance_profile(track_b)

    grid_size_deg = (distance_threshold_m * 2) / 111000.0
    grid_b = {}
    for idx_b, (lat_b, lon_b) in enumerate(zip(lats_b, lons_b)):
        cell = (int(lat_b / grid_size_deg), int(lon_b / grid_size_deg))
        if cell not in grid_b:
            grid_b[cell] = []
        grid_b[cell].append(idx_b)

    is_a_common = np.zeros(len(track_a.points), dtype=bool)
    matched_b_indices = [-1] * len(track_a.points)

    for idx_a, (lat_a, lon_a) in enumerate(zip(lats_a, lons_a)):
        cell_a = (int(lat_a / grid_size_deg), int(lon_a / grid_size_deg))
        min_dist = float("inf")
        best_b_idx = -1

        for d_lat in (-1, 0, 1):
            for d_lon in (-1, 0, 1):
                cell = (cell_a[0] + d_lat, cell_a[1] + d_lon)
                if cell in grid_b:
                    for idx_b in grid_b[cell]:
                        dist = haversine_distance(track_a.points[idx_a], track_b.points[idx_b])
                        if dist < min_dist:
                            min_dist = dist
                            best_b_idx = idx_b

        if min_dist <= distance_threshold_m:
            is_a_common[idx_a] = True
            matched_b_indices[idx_a] = best_b_idx

    segments = []
    in_segment = False
    seg_start_a = 0

    for i in range(len(is_a_common)):
        if is_a_common[i] and not in_segment:
            in_segment = True
            seg_start_a = i
        elif not is_a_common[i] and in_segment:
            in_segment = False
            seg_end_a = i - 1
            length_a = profile_a[seg_end_a] - profile_a[seg_start_a]
            if length_a >= min_segment_length_m:
                segments.append((seg_start_a, seg_end_a))

    if in_segment:
        seg_end_a = len(is_a_common) - 1
        length_a = profile_a[seg_end_a] - profile_a[seg_start_a]
        if length_a >= min_segment_length_m:
            segments.append((seg_start_a, seg_end_a))

    result = []
    for seg_idx, (a_start, a_end) in enumerate(segments, 1):
        b_indices = [matched_b_indices[k] for k in range(a_start, a_end + 1) if matched_b_indices[k] != -1]
        if not b_indices:
            continue
        b_start = min(b_indices)
        b_end = max(b_indices)

        sub_pts_a = track_a.points[a_start:a_end + 1]
        sub_pts_b = track_b.points[b_start:b_end + 1]

        length_a = profile_a[a_end] - profile_a[a_start]
        length_b = profile_b[b_end] - profile_b[b_start]

        speeds_a = [p.speed * 3.6 for p in sub_pts_a if p.speed is not None]
        avg_speed_a = float(np.mean(speeds_a)) if speeds_a else None

        alts_a = [p.altitude for p in sub_pts_a if p.altitude is not None]
        avg_alt_a = float(np.mean(alts_a)) if alts_a else None

        hrs_a = [p.heart_rate for p in sub_pts_a if p.heart_rate is not None]
        avg_hr_a = float(np.mean(hrs_a)) if hrs_a else None

        speeds_b = [p.speed * 3.6 for p in sub_pts_b if p.speed is not None]
        avg_speed_b = float(np.mean(speeds_b)) if speeds_b else None

        alts_b = [p.altitude for p in sub_pts_b if p.altitude is not None]
        avg_alt_b = float(np.mean(alts_b)) if alts_b else None

        hrs_b = [p.heart_rate for p in sub_pts_b if p.heart_rate is not None]
        avg_hr_b = float(np.mean(hrs_b)) if hrs_b else None

        slope_a = None
        if len(alts_a) >= 2 and length_a > 0:
            slope_a = ((alts_a[-1] - alts_a[0]) / length_a) * 100.0

        slope_b = None
        if len(alts_b) >= 2 and length_b > 0:
            slope_b = ((alts_b[-1] - alts_b[0]) / length_b) * 100.0

        time_a_sec = None
        if sub_pts_a[0].timestamp and sub_pts_a[-1].timestamp:
            time_a_sec = (sub_pts_a[-1].timestamp - sub_pts_a[0].timestamp).total_seconds()

        time_b_sec = None
        if sub_pts_b[0].timestamp and sub_pts_b[-1].timestamp:
            time_b_sec = (sub_pts_b[-1].timestamp - sub_pts_b[0].timestamp).total_seconds()

        if avg_speed_a is None and time_a_sec and time_a_sec > 0 and length_a > 0:
            avg_speed_a = (length_a / time_a_sec) * 3.6

        if avg_speed_b is None and time_b_sec and time_b_sec > 0 and length_b > 0:
            avg_speed_b = (length_b / time_b_sec) * 3.6

        coords_a = [(p.latitude, p.longitude) for p in sub_pts_a]
        coords_b = [(p.latitude, p.longitude) for p in sub_pts_b]

        result.append({
            "id": seg_idx,
            "a_start_idx": a_start,
            "a_end_idx": a_end,
            "b_start_idx": b_start,
            "b_end_idx": b_end,
            "a_start_dist_m": profile_a[a_start],
            "a_end_dist_m": profile_a[a_end],
            "b_start_dist_m": profile_b[b_start],
            "b_end_dist_m": profile_b[b_end],
            "length_m": max(length_a, length_b),
            "coords_a": coords_a,
            "coords_b": coords_b,
            "time_a_sec": time_a_sec,
            "time_b_sec": time_b_sec,
            "avg_speed_a": avg_speed_a,
            "avg_speed_b": avg_speed_b,
            "slope_a": slope_a,
            "slope_b": slope_b,
            "avg_alt_a": avg_alt_a,
            "avg_alt_b": avg_alt_b,
            "avg_hr_a": avg_hr_a,
            "avg_hr_b": avg_hr_b,
        })

    return result


def generate_segment_coach_insights(segments: List[dict], name_a: str = "Attività A", name_b: str = "Attività B") -> List[str]:
    """Genera consigli intelligenti del Coach basandosi sulle differenze nei segmenti comuni."""
    if not segments:
        return ["Nessun segmento comune rilevato per elaborare suggerimenti del Coach."]

    insights = []
    total_length = sum(s["length_m"] for s in segments) / 1000.0

    # --- Panoramica generale ---
    insights.append(
        f"<b>Panoramica:</b> Trovati <b>{len(segments)} segmenti comuni</b> per un totale di <b>{total_length:.2f} km</b> di sovrapposizione."
    )

    # --- Confronto velocita ---
    valid_speeds_a = [s["avg_speed_a"] for s in segments if s["avg_speed_a"] is not None]
    valid_speeds_b = [s["avg_speed_b"] for s in segments if s["avg_speed_b"] is not None]

    if valid_speeds_a and valid_speeds_b:
        mean_spd_a = float(np.mean(valid_speeds_a))
        mean_spd_b = float(np.mean(valid_speeds_b))
        diff_spd = mean_spd_b - mean_spd_a
        if abs(diff_spd) >= 0.5:
            faster = name_b if diff_spd > 0 else name_a
            slower = name_a if diff_spd > 0 else name_b
            pct = (abs(diff_spd) / min(mean_spd_a, mean_spd_b)) * 100.0
            insights.append(
                f"🚀 <b>Velocità:</b> In <b>{faster}</b> sei stato più veloce in media del <b>{pct:.1f}%</b> ({abs(diff_spd):.1f} km/h in più) nei tratti comuni rispetto a {slower}."
            )
        else:
            insights.append("⚖️ <b>Ritmo omogeneo:</b> La velocità media complessiva sui tratti comuni è quasi identica tra le due prestazioni.")

    # --- Analisi FC vs Velocita ---
    valid_hrs_a = [s["avg_hr_a"] for s in segments if s["avg_hr_a"] is not None]
    valid_hrs_b = [s["avg_hr_b"] for s in segments if s["avg_hr_b"] is not None]

    if valid_hrs_a and valid_hrs_b and valid_speeds_a and valid_speeds_b:
        mean_hr_a = float(np.mean(valid_hrs_a))
        mean_hr_b = float(np.mean(valid_hrs_b))
        diff_hr = mean_hr_b - mean_hr_a
        diff_spd = mean_spd_b - mean_spd_a

        if diff_spd > 0 and diff_hr <= 0:
            insights.append(f"💡 <b>Efficienza Esemplare:</b> In {name_b} hai viaggiato più veloce con una frequenza cardiaca media uguale o inferiore! Segno di un ottimo stato di forma o gestione energetica.")
        elif diff_spd > 0 and diff_hr > 5:
            insights.append(f"❤️ <b>Sforzo Cardiovascolare:</b> L'aumento di velocità in {name_b} ha richiesto un costo cardiaco medio di +{diff_hr:.0f} bpm. Assicurati di dosare i fuori soglia nei tratti più lunghi.")
        elif diff_spd < 0 and diff_hr > 0:
            insights.append(f"⚠️ <b>Segnale di Affaticamento:</b> In {name_b} la velocità è stata inferiore nonostante una frequenza cardiaca più alta (+{diff_hr:.0f} bpm). Potrebbe indicare stanchezza accumulata o condizioni meteo avverse.")

    # --- Confronto salite ---
    climb_segments = [s for s in segments if (s["slope_a"] and s["slope_a"] > 2.0) or (s["slope_b"] and s["slope_b"] > 2.0)]

    if climb_segments:
        spd_climb_a = [s["avg_speed_a"] for s in climb_segments if s["avg_speed_a"]]
        spd_climb_b = [s["avg_speed_b"] for s in climb_segments if s["avg_speed_b"]]
        if spd_climb_a and spd_climb_b:
            diff_climb = np.mean(spd_climb_b) - np.mean(spd_climb_a)
            if diff_climb > 0.5:
                insights.append(f"⛰️ <b>Prestazione in Salita:</b> Ottimo miglioramento nei tratti in salita in {name_b} (+{diff_climb:.1f} km/h).")
            elif diff_climb < -0.5:
                insights.append(f"⛰️ <b>Consiglio Salita:</b> Nei tratti pendenti {name_a} è risultata più efficace (+{abs(diff_climb):.1f} km/h). Lavora sulla cadenza e sulla gestione del passo in salita.")

    # --- Segmento con maggiore guadagno ---
    if len(segments) > 1 and valid_speeds_a and valid_speeds_b:
        best_seg_b = max(segments, key=lambda s: (s["avg_speed_b"] or 0) - (s["avg_speed_a"] or 0))
        insights.append(
            f"🎯 <b>Miglior Segmento:</b> Nel <b>Segmento {best_seg_b['id']}</b> (km {best_seg_b['a_start_dist_m']/1000:.2f}) hai registrato il massimo guadagno prestazionale!"
        )

    # --- Analisi per segmento ---
    insights.append("<hr>")
    insights.append(f"<b>Analisi dettagliata per segmento</b>")
    for seg in segments:
        seg_id = seg.get("id", "?")
        length_km = seg.get("length_m", 0) / 1000.0
        time_a = seg.get("time_a_sec")
        time_b = seg.get("time_b_sec")
        spd_a = seg.get("avg_speed_a")
        spd_b = seg.get("avg_speed_b")
        hr_a = seg.get("avg_hr_a")
        hr_b = seg.get("avg_hr_b")
        slope_a = seg.get("slope_a")
        slope_b = seg.get("slope_b")

        lines = [f"• <b>Segmento {seg_id}</b> ({length_km:.2f} km)"]

        if time_a is not None and time_b is not None:
            diff = time_b - time_a
            if diff < 0:
                lines.append(f"  - Tempo: hai guadagnato <b>{abs(diff):.0f}s</b> su {name_a}")
            elif diff > 0:
                lines.append(f"  - Tempo: hai perso <b>{diff:.0f}s</b> rispetto a {name_a}")
            else:
                lines.append("  - Tempo: uguale")

        if spd_a is not None and spd_b is not None:
            diff = spd_b - spd_a
            if abs(diff) >= 0.5:
                who = name_b if diff > 0 else name_a
                lines.append(f"  - Velocità: <b>{who}</b> più veloce di {abs(diff):.1f} km/h")
            else:
                lines.append("  - Velocità: sostanzialmente uguale")

        if hr_a is not None and hr_b is not None:
            diff = hr_b - hr_a
            if abs(diff) >= 3:
                who_high = name_b if diff > 0 else name_a
                lines.append(f"  - FC media: <b>{who_high}</b> con battiti più alti di {abs(diff):.0f} bpm")
            else:
                lines.append("  - FC media: simile")

        if slope_a is not None and slope_b is not None:
            diff = slope_b - slope_a
            if abs(diff) >= 0.5:
                who = name_b if diff > 0 else name_a
                lines.append(f"  - Pendenza: in <b>{who}</b> hai affrontato salite più ripide (+{abs(diff):.1f}%)")
            else:
                lines.append("  - Pendenza: analoga")

        insights.append("<br>".join(lines))

    # --- Raccomandazioni finali ---
    insights.append("<hr>")
    if not valid_speeds_a or not valid_speeds_b:
        insights.append("💬 <b>Nota:</b> Dati di velocità incompleti, impossibile generare raccomandazioni avanzate.")
    else:
        if abs(diff_spd) < 0.5:
            insights.append("💬 <b>Raccomandazione:</b> Le due prestazioni sono molto simili. Prova a variare strategia di gara o alimentazione per cercare margini.")
        elif diff_spd > 0:
            insights.append(f"💬 <b>Raccomandazione:</b> {name_b} mostra un passo più aggressivo. Valuta se mantenere questo ritmo per gare più lunghe o se serve più recupero tra tratti veloci.")
        else:
            insights.append(f"💬 <b>Raccomandazione:</b> {name_a} è risultata più veloce. Analizza la distribuzione dello sforzo in {name_b}: forse hai iniziato troppo forte o gestito male i tratti tecnici.")

    return insights

