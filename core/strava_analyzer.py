"""Modulo per l'analisi dei segmenti Strava nelle tracce caricate.

Centralizza il caricamento dei segmenti GPX dalla cartella ``Strava_Segments``
e la loro individuazione all'interno delle tracce caricate.

L'algoritmo di map-matching è ispirato al classico schema Strava
"aggancio inizio → aggancio fine con controllo direzionale → verifica lineare
dei punti intermedi", ottimizzato per:

- tolleranza GPS (default 15 m) compensata con la distanza di Haversine;
- densità di campionamento diverse tra segmento e traccia (GPX registrati
  da dispositivi con frequenze differenti);
- rumore e piccole derive della traccia (salto di punti con gap tollerati);
- più passaggi dello stesso segmento nella stessa traccia (loop o
  out-and-back), in entrambe le direzioni di percorrenza (``direction``).
"""

from bisect import bisect_left
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import gpxpy
import numpy as np

from core.analyzer import track_distance_profile
from core.track import Track, TrackPoint


_EARTH_RADIUS_M = 6371000.0


def _haversine_to_points(
    lat: float, lon: float, lats: np.ndarray, lons: np.ndarray
) -> np.ndarray:
    """Distanza geodesica in metri tra un punto e un array di punti.

    Args:
        lat: Latitudine del punto di riferimento (gradi decimali).
        lon: Longitudine del punto di riferimento (gradi decimali).
        lats: Array NumPy di latitudini (gradi decimali).
        lons: Array NumPy di longitudini (gradi decimali).

    Returns:
        np.ndarray: Distanze in metri, una per punto di ``lats``/``lons``.
    """
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

            # Fallback: alcuni file GPX memorizzano la geometria nelle rotte.
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
    """Per ogni punto del segmento, gli indici di traccia entro la soglia.

    Usa una griglia spaziale costruita una sola volta sulla traccia per
    limitare la ricerca ai punti vicini (vettorializzata con NumPy).

    Args:
        track: Traccia dell'utente.
        segment: Segmento Strava da cercare.
        distance_threshold_m: Tolleranza GPS in metri.

    Returns:
        Lista di array NumPy ordinati (uno per punto del segmento) con gli
        indici dei punti della traccia entro ``distance_threshold_m`` metri.
    """
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
    """Camminata greedy in avanti che appaia il segmento alla traccia.

    Avanza sulla traccia con indici non decrescenti (consente densità di
    campionamento diverse tra segmento e traccia). Se il salto sul terreno
    risulta fisicamente implausibile, il punto del segmento viene saltato
    (tolleranza ``max_gap``), così piccole derive della traccia non spezzano
    la catena.

    Args:
        candidates: Candidati per ogni punto del segmento.
        track_profile: Profilo di distanza cumulativa della traccia.
        segment_profile: Profilo di distanza cumulativa del segmento.
        start_track_idx: Indice di traccia da cui iniziare.
        max_gap: Numero massimo di punti di segmento consecutivi saltabili.
        progress_ratio: Rapporto massimo (dist. traccia / dist. segmento)
            considerato plausibile tra due punti matchati.
        progress_slack_m: Scarto assoluto di tolleranza in metri.

    Returns:
        Lista di coppie ``(indice_segmento, indice_traccia)`` della catena.
    """
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

        track_i = int(cand[k])
        if prev_seg is not None and prev_track is not None:
            d_seg = segment_profile[seg_i] - segment_profile[prev_seg]
            d_track = track_profile[track_i] - track_profile[prev_track]
            if d_track > max(d_seg * progress_ratio, d_seg + progress_slack_m):
                # Salti sproporzionati (es. rami paralleli) non sono match.
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


def _masked_candidates(
    candidates: List[np.ndarray],
    used: np.ndarray,
    reverse: bool,
) -> List[np.ndarray]:
    """Restituisce i candidati (o invertiti) escludendo gli indici usati.

    Args:
        candidates: Lista degli array candidati per punto del segmento.
        used: Maschera booleana degli indici di traccia già consumati.
        reverse: Se True, inverte l'ordine dei punti del segmento (per
            riconoscere le percorrenze in direzione opposta).

    Returns:
        Lista di array candidati filtrati, nello stesso ordine scelto.
    """
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
    candidates: List[np.ndarray],
    track_profile: List[float],
    segment_profile: List[float],
    min_match_points: int,
    start_tol: int,
    end_tol: int,
    max_gap: int,
    progress_ratio: float = 2.5,
    progress_slack_m: float = 30.0,
    min_length_m: float = 20.0,
    min_density: float = 0.4,
    max_density: float = 2.2,
    max_total_passes: int = 12,
) -> List[Tuple[bool, int, int, float, List[Tuple[int, int]]]]:
    """Trova tutte le occorrenze del segmento nella traccia.

    Per ogni direzione di percorrenza estrae iterativamente una porzione di
    traccia che copre il segmento da inizio a fine; gli indici di traccia
    "consumati" vengono esclusi e la ricerca riparte, così i passaggi
    ripetuti dello stesso segmento vengono tutti rilevati.

    Args:
        candidates: Candidati per punto del segmento.
        track_profile: Profilo di distanza della traccia.
        segment_profile: Profilo di distanza del segmento.
        min_match_points: Numero minimo di punti del segmento appaiati.
        start_tol/end_tol: Tolleranza (in punti di segmento) sulle code.
        max_gap: Massimi punti di segmento consecutivi saltabili.
        progress_ratio/slack: Vincoli sul rapporto di avanzamento.
        min_length_m: Lunghezza minima dell'occorrenza in metri.
        min_density/max_density: Rapporti accettabili (lunghezza occorrenza /
            lunghezza segmento) per scartare passaggi parziali o rami paralleli.
        max_total_passes: Limite di passaggi estratti per direzione.

    Returns:
        Lista di tuple ``(reverse, t0, t1, length_m, chain)``.
    """
    n_seg = len(candidates)
    n_track = len(track_profile)
    seg_length = segment_profile[-1]
    used = np.zeros(n_track, dtype=bool)
    occurrences: List[Tuple[bool, int, int, float, List[Tuple[int, int]]]] = []

    for reverse in (False, True):
        for _ in range(max_total_passes):
            masked = _masked_candidates(candidates, used, reverse)
            profile = _reversed_profile(segment_profile) if reverse else segment_profile

            # Ancore di inizio: primi candidati distinti dei punti d'inizio.
            anchor_pool: List[int] = []
            for i in range(min(start_tol + 1, n_seg)):
                anchor_pool.extend(int(x) for x in masked[i][:10])
            anchors = list(dict.fromkeys(anchor_pool))

            found = False
            for s0 in anchors:
                chain = _walk_forward(
                    masked, track_profile, profile, s0,
                    max_gap, progress_ratio, progress_slack_m,
                )
                if not chain:
                    continue
                seg_start, _ = chain[0]
                seg_end, _ = chain[-1]
                # Copertura completa inizio→fine (controllo lineare).
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
    distance_threshold_m: float = 15.0,
    min_match_points: int = 5,
) -> List[dict]:
    """Individua i segmenti Strava all'interno di una traccia caricata.

    Per ogni segmento verifica se la traccia copre la polilinea del segmento
    da inizio a fine (entro la tolleranza GPS), rispettando la direzione di
    percorrenza, e restituisce tutte le occorrenze trovate (inclusi più
    passaggi dello stesso segmento e percorrenze in direzione opposta).

    Args:
        strava_segments: Lista di segmenti Strava da ``load_strava_segments``.
        track: Traccia dell'utente in cui cercare i segmenti.
        distance_threshold_m: Tolleranza GPS in metri.
        min_match_points: Numero minimo di punti del segmento appaiati.

    Returns:
        Lista di dizionari con le occorrenze trovate (``segment_name``,
        ``track_name``, ``track``, ``start_idx``, ``end_idx``,
        ``start_dist_m``, ``end_dist_m``, ``length_m``, ``time_sec``,
        ``avg_speed``, ``avg_alt``, ``avg_hr``, ``slope``, ``coords``,
        ``direction``, ``n_match_points``).
    """
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

        end_tol = max(2, int(0.10 * n_seg))
        max_gap = max(6, int(0.08 * n_seg))

        occurrences = _find_occurrences(
            candidates,
            track_profile,
            segment_profile,
            min_match_points=min_match_points,
            start_tol=end_tol,
            end_tol=end_tol,
            max_gap=max_gap,
        )

        for reverse, t0, t1, length_m, chain in occurrences:
            sub_pts = track.points[t0 : t1 + 1]

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
                }
            )

    results.sort(key=lambda occ: (occ["segment_name"], occ["start_idx"]))
    return results
