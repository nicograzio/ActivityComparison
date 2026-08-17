"""Modulo per l'analisi dei segmenti Strava nelle tracce caricate.

Implementa l'algoritmo di map-matching con gestione avanzata dei loop di ingresso,
inversioni a U e passaggi multipli all'imbocco del segmento.
"""

from bisect import bisect_left
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import gpxpy
import numpy as np

from core.analyzer import track_distance_profile, haversine_distance
from core.track import Track, TrackPoint

# =============================================================================
# PARAMETRI DI CONFIGURAZIONE MATCHING SEGMENTI
# =============================================================================
DISTANCE_THRESHOLD_M = 35.0
MIN_MATCH_POINTS = 5
END_TOL_RATIO = 0.15
MAX_GAP_RATIO = 0.08
CLUSTER_GAP_IDX = 25

PROGRESS_RATIO = 2.5
PROGRESS_SLACK_M = 30.0

MIN_DENSITY = 0.5
MAX_DENSITY = 1.5
MIN_LENGTH_M = 20.0

PROJECTION_WINDOW = 20
END_PROJECTION_EXTRA_IDX = 120
END_PROJECTION_ACCEPT_M = DISTANCE_THRESHOLD_M
END_PROJECTION_EXIT_RISE_M = 12.0
END_PROJECTION_MIN_IMPROVE_M = 5.0
STATIONARY_SPEED_KMH = 2.0
# =============================================================================

_EARTH_RADIUS_M = 6371000.0


def _haversine_to_points(
    lat: float, lon: float, lats: np.ndarray, lons: np.ndarray
) -> np.ndarray:
    """Distanza geodesica in metri tra un punto e un array di punti."""
    deg2rad = np.pi / 180.0
    dlat = (lats - lat) * deg2rad
    dlon = (lons - lon) * deg2rad
    a = (
        np.sin(dlat / 2.0) ** 2
        + np.cos(lat * deg2rad)
        * np.cos(lats * deg2rad)
        * np.sin(dlon / 2.0) ** 2
    )
    np.clip(a, 0.0, 1.0, out=a)
    return _EARTH_RADIUS_M * 2.0 * np.arctan2(np.sqrt(a), np.sqrt(1.0 - a))


def _project_point_on_segment(
    lat_p: float, lon_p: float, lat_a: float, lon_a: float, lat_b: float, lon_b: float
) -> float:
    """Proietta il punto P sul segmento AB e restituisce la frazione r [0, 1]."""
    lat_avg = np.radians(lat_a)
    cos_lat = np.cos(lat_avg)

    dlat_ab = lat_b - lat_a
    dlon_ab = (lon_b - lon_a) * cos_lat

    dlat_ap = lat_p - lat_a
    dlon_ap = (lon_p - lon_a) * cos_lat

    denom = dlat_ab**2 + dlon_ab**2
    if denom < 1e-15:
        return 0.0

    r = (dlat_ap * dlat_ab + dlon_ap * dlon_ab) / denom
    return float(np.clip(r, 0.0, 1.0))


def _track_segment_kmh(track: Track, k: int) -> Optional[float]:
    """Velocità media (km/h) tra due campioni consecutivi di traccia."""
    a = track.points[k]
    b = track.points[k + 1]
    if a.timestamp is None or b.timestamp is None:
        return None
    dt = (b.timestamp - a.timestamp).total_seconds()
    if dt <= 0:
        return None
    dist = haversine_distance(a, b)
    return float((dist / dt) * 3.6)


def _find_best_track_projection(
    track: Track,
    target_lat: float,
    target_lon: float,
    center_idx: int,
    window: int = PROJECTION_WINDOW,
) -> Tuple[int, float, float]:
    """Cerca il miglior segmento di traccia su cui proiettare un punto del segmento."""
    best_dist = float("inf")
    best_k = center_idx
    best_r = 0.0

    start_k = max(0, center_idx - window)
    end_k = min(len(track.points) - 2, center_idx + window)

    for k in range(start_k, end_k + 1):
        speed = _track_segment_kmh(track, k)
        if speed is not None and speed < STATIONARY_SPEED_KMH:
            continue

        pa = track.points[k]
        pb = track.points[k + 1]

        r = _project_point_on_segment(
            target_lat, target_lon, pa.latitude, pa.longitude, pb.latitude, pb.longitude
        )

        p_lat = pa.latitude + r * (pb.latitude - pa.latitude)
        p_lon = pa.longitude + r * (pb.longitude - pa.longitude)

        d = float(_haversine_to_points(target_lat, target_lon, np.array([p_lat]), np.array([p_lon]))[0])

        if d < best_dist:
            best_dist = d
            best_k = k
            best_r = r

    return best_k, best_r, best_dist


def _find_first_gate_valley(
    track: Track,
    target_lat: float,
    target_lon: float,
    center_idx: int,
    max_extra_idx: int,
    accept_m: float,
    exit_rise_m: float,
) -> Tuple[Optional[int], float, float]:
    """Trova il primo avvallamento della distanza gate-traccia in uscita."""
    start_k = max(0, center_idx)
    end_k = min(len(track.points) - 2, center_idx + max_extra_idx)

    best_k: Optional[int] = None
    best_r = 0.0
    best_d = float("inf")

    for k in range(start_k, end_k + 1):
        speed = _track_segment_kmh(track, k)
        if speed is not None and speed < STATIONARY_SPEED_KMH:
            continue

        pa = track.points[k]
        pb = track.points[k + 1]

        r = _project_point_on_segment(
            target_lat, target_lon, pa.latitude, pa.longitude, pb.latitude, pb.longitude
        )
        p_lat = pa.latitude + r * (pb.latitude - pa.latitude)
        p_lon = pa.longitude + r * (pb.longitude - pa.longitude)
        d = float(_haversine_to_points(target_lat, target_lon, np.array([p_lat]), np.array([p_lon]))[0])

        if best_k is None or d < best_d:
            best_k = k
            best_r = r
            best_d = d
        elif d - best_d > exit_rise_m and best_d <= accept_m:
            break

    if best_k is not None and best_d <= accept_m:
        return best_k, best_r, best_d
    return None, 0.0, float("inf")


def load_strava_segments(folder_path: str) -> List[dict]:
    """Carica tutti i segmenti GPX dalla cartella Strava_Segments."""
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

            if not points:
                for route in gpx.routes:
                    points.extend(
                        TrackPoint(
                            latitude=point.latitude,
                            longitude=point.longitude,
                            altitude=point.elevation,
                            timestamp=point.time,
                        )
                        for point in route.points
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


def _candidate_track_indices(
    track: Track,
    segment: Track,
    distance_threshold_m: float,
) -> List[np.ndarray]:
    """Per ogni punto del segmento, individua gli indici di traccia entro la soglia."""
    lats_track = track.latitudes
    lons_track = track.longitudes
    lats_seg = segment.latitudes
    lons_seg = segment.longitudes
    n_seg = len(segment.points)
    n_track = len(track.points)

    grid_size_deg = distance_threshold_m / 111000.0
    grid: Dict[Tuple[int, int], List[int]] = {}
    for i in range(n_track):
        cell = (int(lats_track[i] / grid_size_deg), int(lons_track[i] / grid_size_deg))
        grid.setdefault(cell, []).append(i)

    empty = np.empty(0, dtype=np.int64)
    candidates: List[np.ndarray] = []
    for si in range(n_seg):
        cell = (int(lats_seg[si] / grid_size_deg), int(lons_seg[si] / grid_size_deg))
        pool: List[np.ndarray] = []
        for d_lat in (-1, 0, 1):
            for d_lon in (-1, 0, 1):
                idx = grid.get((cell[0] + d_lat, cell[1] + d_lon))
                if idx:
                    pool.append(np.asarray(idx, dtype=np.int64))
        if not pool:
            candidates.append(empty)
            continue
        track_idx = np.concatenate(pool) if len(pool) > 1 else pool[0]
        dist = _haversine_to_points(
            lats_seg[si], lons_seg[si], lats_track[track_idx], lons_track[track_idx]
        )
        good = track_idx[dist <= distance_threshold_m]
        good.sort()
        candidates.append(good)
    return candidates


def _walk_forward(
    candidates: List[np.ndarray],
    track_profile: List[float],
    segment_profile: List[float],
    start_track_idx: int,
    max_gap: int,
    progress_ratio: float,
    progress_slack_m: float,
) -> List[Tuple[int, int]]:
    """Camminata greedy in avanti che appaia il segmento alla traccia."""
    chain: List[Tuple[int, int]] = []
    t = start_track_idx - 1
    prev_seg: Optional[int] = None
    prev_track: Optional[int] = None
    skipped = 0

    for seg_i in range(len(candidates)):
        cand = candidates[seg_i]
        k = bisect_left(cand, t)
        if k >= len(cand):
            skipped += 1
            if skipped > max_gap:
                break
            continue

        matched = False
        for j in range(k, len(cand)):
            track_i = int(cand[j])
            if prev_seg is not None and prev_track is not None:
                d_seg = segment_profile[seg_i] - segment_profile[prev_seg]
                d_track = track_profile[track_i] - track_profile[prev_track]
                if d_track > max(d_seg * progress_ratio, d_seg + progress_slack_m):
                    continue
            matched = True
            break

        if not matched:
            skipped += 1
            if skipped > max_gap:
                break
            continue

        chain.append((seg_i, track_i))
        prev_seg = seg_i
        prev_track = track_i
        t = track_i
        skipped = 0

    return chain


def _trim_chain_start(
    chain: List[Tuple[int, int]],
    segment_track: Track,
    track: Track,
    reverse: bool = False,
) -> List[Tuple[int, int]]:
    """Elimina i loop/avvicinamenti iniziali identificando il salto temporale o la discontinuità

    causata dal passaggio in salita/avvicinamento prima dell'imbocco effettivo.
    """
    if len(chain) < 4:
        return chain

    # Prendiamo il punto di inizio e un punto di riferimento leggermente avanzato nel segmento (~30-50m)
    p_start = segment_track.points[-1 if reverse else 0]
    ref_idx = max(0, len(segment_track.points) - 4) if reverse else min(4, len(segment_track.points) - 1)
    p_ref = segment_track.points[ref_idx]

    # Cerchiamo se nella catena iniziale c'è un "gap" o un'inversione di distanza dal punto di riferimento
    best_start_idx = 0
    max_progress_ratio = -1.0

    check_limit = min(30, len(chain))

    for i in range(check_limit):
        seg_i, trk_i = chain[i]
        pt_track = track.points[trk_i]

        d_start = haversine_distance(pt_track, p_start)
        d_ref = haversine_distance(pt_track, p_ref)

        # Se il punto è vicino alla partenza, valutiamo quanto è proiettato verso l'interno del trail
        if d_start <= DISTANCE_THRESHOLD_M:
            # Punteggio di vicinanza al prosieguo del trail
            score = (DISTANCE_THRESHOLD_M - d_start) + (DISTANCE_THRESHOLD_M - d_ref)
            
            # Se troviamo un punto che è sia vicino allo start sia nettamente più vicino al punto interno,
            # lo preferiamo rispetto ai punti precedenti dove il ciclista si stava allontanando
            if score > max_progress_ratio:
                max_progress_ratio = score
                best_start_idx = i

    # Se c'è un salto di indice di traccia anomalo tra due elementi vicini della catena iniziale,
    # significa che il primo apparteneva al passaggio in salita e il secondo al passaggio in discesa!
    for i in range(min(15, len(chain) - 1)):
        trk_curr = chain[i][1]
        trk_next = chain[i + 1][1]
        
        # Se c'è un "buco" temporale/di indici nella traccia > 15 punti mentre il segmento è all'inizio,
        # l'ingresso vero è dopo il buco!
        if trk_next - trk_curr > 15:
            return chain[i + 1:]

    return chain[best_start_idx:]


def _masked_candidates(
    candidates: List[np.ndarray],
    used: np.ndarray,
    reverse: bool,
) -> List[np.ndarray]:
    """Restituisce i candidati escludendo gli indici usati."""
    order = candidates[::-1] if reverse else candidates
    masked: List[np.ndarray] = []
    for cand in order:
        if len(cand):
            cand = cand[~used[cand]]
        masked.append(cand)
    return masked


def _reversed_profile(profile: List[float]) -> List[float]:
    """Profilo di distanza cumulativa del segmento percorso al contrario."""
    total = profile[-1]
    return [total - value for value in profile[::-1]]


def _find_occurrences(
    track: Track,
    segment_track: Track,
    candidates: List[np.ndarray],
    track_profile: List[float],
    segment_profile: List[float],
    min_match_points: int = MIN_MATCH_POINTS,
    start_tol: int = 5,
    end_tol: int = 5,
    max_gap: int = 10,
    progress_ratio: float = PROGRESS_RATIO,
    progress_slack_m: float = PROGRESS_SLACK_M,
    min_length_m: float = MIN_LENGTH_M,
    min_density: float = MIN_DENSITY,
    max_density: float = MAX_DENSITY,
    max_total_passes: int = 12,
) -> List[Tuple[bool, int, int, float, List[Tuple[int, int]]]]:
    """Trova tutte le occorrenze del segmento nella traccia."""
    n_seg = len(candidates)
    n_track = len(track_profile)
    seg_length = segment_profile[-1]
    used = np.zeros(n_track, dtype=bool)
    occurrences: List[Tuple[bool, int, int, float, List[Tuple[int, int]]]] = []

    for reverse in (False, True):
        for _ in range(max_total_passes):
            masked = _masked_candidates(candidates, used, reverse)
            profile = _reversed_profile(segment_profile) if reverse else segment_profile

            anchor_pool: List[int] = []
            for i in range(min(start_tol + 1, n_seg)):
                arr = masked[i]
                if len(arr) == 0:
                    continue
                anchor_pool.append(int(arr[0]))
                prev = int(arr[0])
                for j in range(1, len(arr)):
                    x = int(arr[j])
                    if x - prev > CLUSTER_GAP_IDX:
                        for k in range(j, min(j + 5, len(arr))):
                            anchor_pool.append(int(arr[k]))
                    prev = x
            anchors = list(dict.fromkeys(anchor_pool))

            found = False
            for s0 in anchors:
                chain = _walk_forward(
                    masked, track_profile, profile, s0,
                    max_gap, progress_ratio, progress_slack_m,
                )
                if not chain:
                    continue

                # Applichiamo il trim sanificato dell'ingresso
                chain = _trim_chain_start(chain, segment_track, track, reverse=reverse)

                seg_start, _ = chain[0]
                seg_end, _ = chain[-1]

                if seg_start > start_tol or seg_end < n_seg - 1 - end_tol:
                    continue
                t0 = min(ti for _, ti in chain)
                t1 = max(ti for _, ti in chain)
                length_m = track_profile[t1] - track_profile[t0]
                if length_m < min_length_m or len(chain) < min_match_points:
                    continue
                if not (min_density * seg_length <= length_m <= max_density * seg_length):
                    continue
                occurrences.append((reverse, t0, t1, length_m, chain))
                used[t0 : t1 + 1] = True
                found = True
                break
            if not found:
                break

    return occurrences


def find_strava_segments_in_track(
    strava_segments: List[dict],
    track: Track,
    distance_threshold_m: float = DISTANCE_THRESHOLD_M,
    min_match_points: int = MIN_MATCH_POINTS,
) -> List[dict]:
    """Individua i segmenti Strava all'interno di una traccia caricata."""
    if not track or not track.points or len(track.points) < 2:
        return []

    track_profile, _ = track_distance_profile(track)
    results: List[dict] = []

    for strava_seg in strava_segments:
        segment_track = strava_seg["track"]
        n_seg = len(segment_track.points)
        if n_seg < min_match_points:
            continue

        candidates = _candidate_track_indices(track, segment_track, distance_threshold_m)
        segment_profile, _ = track_distance_profile(segment_track)

        end_tol = max(2, int(END_TOL_RATIO * n_seg))
        max_gap = max(6, int(MAX_GAP_RATIO * n_seg))

        occurrences = _find_occurrences(
            track,
            segment_track,
            candidates,
            track_profile,
            segment_profile,
            min_match_points=min_match_points,
            start_tol=end_tol,
            end_tol=end_tol,
            max_gap=max_gap,
            progress_ratio=PROGRESS_RATIO,
            progress_slack_m=PROGRESS_SLACK_M,
            min_length_m=MIN_LENGTH_M,
            min_density=MIN_DENSITY,
            max_density=MAX_DENSITY,
        )

        for reverse, t0_raw, t1_raw, length_m_raw, chain in occurrences:
            if not reverse:
                seg_start_lat = segment_track.points[0].latitude
                seg_start_lon = segment_track.points[0].longitude
                seg_end_lat = segment_track.points[-1].latitude
                seg_end_lon = segment_track.points[-1].longitude
            else:
                seg_start_lat = segment_track.points[-1].latitude
                seg_start_lon = segment_track.points[-1].longitude
                seg_end_lat = segment_track.points[0].latitude
                seg_end_lon = segment_track.points[0].longitude

            # t0_chain è garantito essere il punto effettivo post-trim e post-salto temporale
            t0_chain = chain[0][1]
            t1_chain = chain[-1][1]

            # PROIEZIONE START (Usa t0_chain)
            k_start, r_start, _ = _find_best_track_projection(
                track, seg_start_lat, seg_start_lon, t0_chain, window=PROJECTION_WINDOW
            )

            # PROIEZIONE END
            k_end, r_end, d_end = _find_best_track_projection(
                track, seg_end_lat, seg_end_lon, t1_chain, window=PROJECTION_WINDOW
            )
            k_valley, r_valley, d_valley = _find_first_gate_valley(
                track, seg_end_lat, seg_end_lon, t1_chain,
                max_extra_idx=END_PROJECTION_EXTRA_IDX,
                accept_m=END_PROJECTION_ACCEPT_M,
                exit_rise_m=END_PROJECTION_EXIT_RISE_M,
            )

            if (
                d_valley <= END_PROJECTION_ACCEPT_M
                and d_valley < d_end - END_PROJECTION_MIN_IMPROVE_M
            ):
                k_end, r_end = k_valley, r_valley

            def _get_interp_time(k: int, r: float) -> Optional[float]:
                ta = track.points[k].timestamp
                tb = track.points[k + 1].timestamp
                if not ta or not tb:
                    return None
                return ta.timestamp() + r * (tb - ta).total_seconds()

            ts_start = _get_interp_time(k_start, r_start)
            ts_end = _get_interp_time(k_end, r_end)

            time_sec = None
            if ts_start is not None and ts_end is not None:
                time_sec = abs(ts_end - ts_start)

            t0 = min(k_start, k_end)
            t1 = max(k_start + 1, k_end + 1)
            length_m = track_profile[t1] - track_profile[t0]
            sub_pts = track.points[t0 : t1 + 1]

            if time_sec is None and sub_pts[0].timestamp and sub_pts[-1].timestamp:
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
                    "start_idx": t0,
                    "end_idx": t1,
                    "start_dist_m": track_profile[t0],
                    "end_dist_m": track_profile[t1],
                    "length_m": length_m,
                    "time_sec": time_sec,
                    "avg_speed": avg_speed,
                    "avg_alt": avg_alt,
                    "avg_hr": avg_hr,
                    "slope": slope,
                    "coords": coords,
                    "direction": "reverse" if reverse else "forward",
                    "n_match_points": len(chain),
                    "segment_point_count": n_seg,
                }
            )

    results.sort(key=lambda x: x["n_match_points"], reverse=True)

    final_results = []
    occupied = np.zeros(len(track.points), dtype=bool)

    for res in results:
        t0, t1 = res["start_idx"], res["end_idx"]
        if np.sum(occupied[t0 : t1 + 1]) > 0.5 * (t1 - t0 + 1):
            continue

        final_results.append(res)
        occupied[t0 : t1 + 1] = True

    final_results.sort(key=lambda occ: occ["start_idx"])
    return final_results