"""Modulo per l'analisi dei segmenti Strava nelle tracce caricate.

Centralizza il caricamento dei segmenti GPX dalla cartella Strava_Segments
e la loro individuazione all'interno delle tracce dell'utente.
"""

import gpxpy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from core.analyzer import haversine_distance, track_distance_profile
from core.track import Track, TrackPoint


def load_strava_segments(folder_path: str) -> List[dict]:
    """Carica tutti i segmenti GPX dalla cartella Strava_Segments.

    Args:
        folder_path: Percorso della cartella contenente i file GPX dei segmenti Strava.

    Returns:
        Lista di dizionari con ``name`` e ``track`` per ogni segmento Strava.
    """
    segments: List[dict] = []
    folder = Path(folder_path)
    if not folder.is_dir():
        return segments

    for gpx_file in sorted(folder.glob("*.gpx")):
        try:
            with open(gpx_file, "r", encoding="utf-8") as f:
                gpx = gpxpy.parse(f)

            points: List[TrackPoint] = []
            for gpx_track in gpx.tracks:
                for segment in gpx_track.segments:
                    for point in segment.points:
                        points.append(
                            TrackPoint(
                                latitude=point.latitude,
                                longitude=point.longitude,
                                altitude=point.elevation,
                                timestamp=point.time,
                            )
                        )

            if len(points) < 2:
                continue

            segment_name = gpx_file.stem
            segment_track = Track(segment_name)
            segment_track.points = points
            segment_track.invalidate_cache()

            segments.append(
                {
                    "name": segment_name,
                    "track": segment_track,
                    "file_path": str(gpx_file),
                }
            )
        except Exception:
            continue

    return segments


def find_strava_segments_in_track(
    strava_segments: List[dict],
    track: Track,
    distance_threshold_m: float = 15.0,
    min_match_points: int = 5,
) -> List[dict]:
    """Individua i segmenti Strava all'interno di una traccia caricata.

    Per ogni segmento Strava, verifica quanti punti consecutivi sono presenti
    nella traccia entro la soglia di distanza specificata.

    Args:
        strava_segments: Lista di segmenti Strava caricati da ``load_strava_segments``.
        track: Traccia dell'utente in cui cercare i segmenti.
        distance_threshold_m: Distanza massima in metri per considerare un punto matchato.
        min_match_points: Numero minimo di punti matchati consecutivi.

    Returns:
        Lista di dizionari con le informazioni sulle occorrenze trovate.
    """
    if not track or not track.points or len(track.points) < 2:
        return []

    lats_track = track.latitudes
    lons_track = track.longitudes
    profile_track, _ = track_distance_profile(track)

    results: List[dict] = []

    for strava_seg in strava_segments:
        seg_points = strava_seg["track"].points
        n_seg = len(seg_points)

        if n_seg < min_match_points:
            continue

        lats_seg = strava_seg["track"].latitudes
        lons_seg = strava_seg["track"].longitudes

        grid_size_deg = (distance_threshold_m * 2) / 111000.0
        grid_track: Dict[Tuple[int, int], List[int]] = {}
        for idx_t, (lat_t, lon_t) in enumerate(zip(lats_track, lons_track)):
            cell = (int(lat_t / grid_size_deg), int(lon_t / grid_size_deg))
            if cell not in grid_track:
                grid_track[cell] = []
            grid_track[cell].append(idx_t)

        matched_indices: List[int] = []
        for idx_s in range(n_seg):
            lat_s, lon_s = lats_seg[idx_s], lons_seg[idx_s]
            cell_s = (int(lat_s / grid_size_deg), int(lon_s / grid_size_deg))
            min_dist = float("inf")
            best_idx = -1

            for d_lat in (-1, 0, 1):
                for d_lon in (-1, 0, 1):
                    cell = (cell_s[0] + d_lat, cell_s[1] + d_lon)
                    if cell in grid_track:
                        for idx_t in grid_track[cell]:
                            dist = haversine_distance(seg_points[idx_s], track.points[idx_t])
                            if dist < min_dist:
                                min_dist = dist
                                best_idx = idx_t

            if min_dist <= distance_threshold_m and best_idx >= 0:
                matched_indices.append(best_idx)

        if len(matched_indices) < min_match_points:
            continue

        sequences: List[Tuple[int, int]] = []
        seq_start = matched_indices[0]
        seq_end = matched_indices[0]

        for i in range(1, len(matched_indices)):
            if matched_indices[i] == matched_indices[i - 1] + 1:
                seq_end = matched_indices[i]
            else:
                if seq_end - seq_start + 1 >= min_match_points:
                    sequences.append((seq_start, seq_end))
                seq_start = matched_indices[i]
                seq_end = matched_indices[i]

        if seq_end - seq_start + 1 >= min_match_points:
            sequences.append((seq_start, seq_end))

        for seq_start, seq_end in sequences:
            length_m = profile_track[seq_end] - profile_track[seq_start]
            if length_m < 20.0:
                continue

            sub_pts = track.points[seq_start : seq_end + 1]

            time_sec = None
            if sub_pts[0].timestamp and sub_pts[-1].timestamp:
                time_sec = (sub_pts[-1].timestamp - sub_pts[0].timestamp).total_seconds()

            speeds = [p.speed * 3.6 for p in sub_pts if p.speed is not None]
            avg_speed = float(np.mean(speeds)) if speeds else None
            if avg_speed is None and time_sec and time_sec > 0 and length_m > 0:
                avg_speed = (length_m / time_sec) * 3.6

            alts = [p.altitude for p in sub_pts if p.altitude is not None]
            avg_alt = float(np.mean(alts)) if alts else None

            hrs = [p.heart_rate for p in sub_pts if p.heart_rate is not None]
            avg_hr = float(np.mean(hrs)) if hrs else None

            slope = None
            if len(alts) >= 2 and length_m > 0:
                slope = ((alts[-1] - alts[0]) / length_m) * 100.0

            coords = [(p.latitude, p.longitude) for p in sub_pts]

            results.append(
                {
                    "segment_name": strava_seg["name"],
                    "track_name": track.name,
                    "track": track,
                    "start_idx": seq_start,
                    "end_idx": seq_end,
                    "start_dist_m": profile_track[seq_start],
                    "end_dist_m": profile_track[seq_end],
                    "length_m": length_m,
                    "time_sec": time_sec,
                    "avg_speed": avg_speed,
                    "avg_alt": avg_alt,
                    "avg_hr": avg_hr,
                    "slope": slope,
                    "coords": coords,
                }
            )

    return results
    for gpx_file in sorted(folder.glob("*.gpx")):
        try:
            with open(gpx_file, "r", encoding="utf-8") as f:
                gpx = gpxpy.parse(f)

            points: List[TrackPoint] = []
            for gpx_track in gpx.tracks:
                for segment in gpx_track.segments:
                    for point in segment.points:
                        points.append(
                            TrackPoint(
                                latitude=point.latitude,
                                longitude=point.longitude,
                                altitude=point.elevation,
                                timestamp=point.time,
                            )
                        )

            if len(points) < 2:
                continue

            segment_name = gpx_file.stem
            segment_track = Track(segment_name)
            segment_track.points = points
            segment_track.invalidate_cache()

            segments.append(
                {
                    "name": segment_name,
                    "track": segment_track,
                    "file_path": str(gpx_file),
                }
            )
        except Exception:
            continue

    return segments
