import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from core.fit_loader import load_fit
from core.gpx_loader import load_gpx
import core.strava_analyzer as sa

def find_strava_segments_in_track_MODIFIED(
    strava_segments,
    track,
    distance_threshold_m = sa.DISTANCE_THRESHOLD_M,
    min_match_points = sa.MIN_MATCH_POINTS,
):
    if not track or not track.points or len(track.points) < 2:
        return []

    track_profile, _ = sa.track_distance_profile(track)
    results = []

    for strava_seg in strava_segments:
        segment_track = strava_seg["track"]
        n_seg = len(segment_track.points)
        if n_seg < min_match_points:
            continue

        candidates = sa._candidate_track_indices(track, segment_track, distance_threshold_m)
        segment_profile, _ = sa.track_distance_profile(segment_track)

        end_tol = max(2, int(sa.END_TOL_RATIO * n_seg))
        max_gap = max(6, int(sa.MAX_GAP_RATIO * n_seg))

        occurrences = sa._find_occurrences(
            track,
            segment_track,
            candidates,
            track_profile,
            segment_profile,
            min_match_points=min_match_points,
            start_tol=end_tol,
            end_tol=end_tol,
            max_gap=max_gap,
            progress_ratio=sa.PROGRESS_RATIO,
            progress_slack_m=sa.PROGRESS_SLACK_M,
            min_length_m=sa.MIN_LENGTH_M,
            min_density=sa.MIN_DENSITY,
            max_density=sa.MAX_DENSITY,
        )

        for reverse, t0_raw, t1_raw, length_m_raw, avg_dist_m, chain in occurrences:
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

            t0_chain = chain[0][1]
            t1_chain = chain[-1][1]

            # PROIEZIONE START
            k_start, r_start, _ = sa._find_best_track_projection(
                track, seg_start_lat, seg_start_lon, t0_chain, window=sa.PROJECTION_WINDOW
            )

            # PROIEZIONE END
            res_end = sa._find_best_track_projection(
                track, seg_end_lat, seg_end_lon, t1_chain, window=sa.PROJECTION_WINDOW
            )
            k_end, r_end, d_end = res_end

            k_valley, r_valley, d_valley = sa._find_first_gate_valley(
                track, seg_end_lat, seg_end_lon, t1_chain,
                max_extra_idx=sa.END_PROJECTION_EXTRA_IDX,
                accept_m=sa.END_PROJECTION_ACCEPT_M,
                exit_rise_m=sa.END_PROJECTION_EXIT_RISE_M,
            )

            # MODIFICA: se k_valley è valido, usalo SEMPRE perché rappresenta la prima uscita reale dal gate!
            if k_valley is not None and d_valley <= sa.END_PROJECTION_ACCEPT_M:
                k_end, r_end = k_valley, r_valley

            def _get_interp_time(k_val: int, r_val: float) -> float:
                ta = track.points[k_val].timestamp
                tb = track.points[k_val + 1].timestamp
                if not ta or not tb:
                    return None
                return ta.timestamp() + r_val * (tb - ta).total_seconds()

            ts_start = _get_interp_time(k_start, r_start)
            ts_end = _get_interp_time(k_end, r_end)

            time_sec = None
            if ts_start is not None and ts_end is not None:
                time_sec = abs(ts_end - ts_start)

            t0 = min(int(k_start), int(k_end))
            t1 = max(int(k_start) + 1, int(k_end) + 1)
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
                    "avg_dist_m": avg_dist_m,
                    "slope": slope,
                    "coords": coords,
                    "direction": "reverse" if reverse else "forward",
                    "n_match_points": len(chain),
                    "segment_point_count": n_seg,
                }
            )

    results.sort(key=lambda x: (x["avg_dist_m"], -x["n_match_points"]))

    final_results = []
    occupied = np.zeros(len(track.points), dtype=bool)

    for res in results:
        t0, t1 = res["start_idx"], res["end_idx"]
        if np.sum(occupied[t0 : t1 + 1]) > sa.OVERLAP_OCCUPANCY_THRESHOLD * (t1 - t0 + 1):
            continue

        final_results.append(res)
        occupied[t0 : t1 + 1] = True

    final_results.sort(key=lambda occ: occ["start_idx"])
    return final_results

# Ora patchiamo la funzione e facciamo girare il confronto!
sa.find_strava_segments_in_track = find_strava_segments_in_track_MODIFIED

import confronto_segmenti
confronto_segmenti.main()
