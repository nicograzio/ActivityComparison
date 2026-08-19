"""Verifica coerenza FIT vs GPX per BePa UP TRAIL (stessa attivita').
Mostra i tempi proiettati prima e dopo le modifiche alla proiezione."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.fit_loader import load_fit
from core.gpx_loader import load_gpx
from core.strava_analyzer import (
    load_strava_segments,
    find_strava_segments_in_track,
    _candidate_track_indices,
    _find_occurrences,
    _find_first_gate_valley,
    _find_last_gate_valley,
    _find_best_track_projection,
    DISTANCE_THRESHOLD_M,
    END_TOL_RATIO,
    MAX_GAP_RATIO,
    PROJECTION_WINDOW,
    END_PROJECTION_EXTRA_IDX,
    END_PROJECTION_ACCEPT_M,
    END_PROJECTION_EXIT_RISE_M,
    track_distance_profile,
    haversine_distance,
)


def analyze(label, track, segs, bepa_seg):
    n_track = len(track.points)
    found = find_strava_segments_in_track(segs, track)
    bepa_res = [r for r in found if r["segment_name"] == "BePa UP TRAIL"]
    print("=== %s: %d punti ===" % (label, n_track))
    if not bepa_res:
        print("  BePa NOT found!")
        print()
        return
    r = bepa_res[0]
    print("  Risultato finale: start=%d, end=%d, time=%.1fs" % (
        r["start_idx"], r["end_idx"], r["time_sec"]))

    track_profile, _ = track_distance_profile(track)
    candidates = _candidate_track_indices(track, bepa_seg, DISTANCE_THRESHOLD_M)
    segment_profile, _ = track_distance_profile(bepa_seg)
    n_seg = len(bepa_seg.points)
    end_tol = max(2, int(END_TOL_RATIO * n_seg))
    max_gap = max(6, int(MAX_GAP_RATIO * n_seg))
    occs = _find_occurrences(
        track, bepa_seg, candidates, track_profile, segment_profile,
        min_match_points=4, start_tol=end_tol, end_tol=end_tol, max_gap=max_gap)

    seg_start_lat = bepa_seg.points[0].latitude
    seg_start_lon = bepa_seg.points[0].longitude
    seg_end_lat = bepa_seg.points[-1].latitude
    seg_end_lon = bepa_seg.points[-1].longitude

    for occ in occs:
        reverse, t0, t1, length_m, avg_dist, chain = occ
        t0_chain = chain[0][1]
        t1_chain = chain[-1][1]
        print("  Occ: t0=%d, t1=%d, t0_chain=%d, t1_chain=%d" % (t0, t1, t0_chain, t1_chain))

        k_start, r_start, d_start = _find_best_track_projection(
            track, seg_start_lat, seg_start_lon, t0_chain, window=PROJECTION_WINDOW)
        k_end, r_end, d_end = _find_best_track_projection(
            track, seg_end_lat, seg_end_lon, t1_chain, window=PROJECTION_WINDOW)
        kv_e, rv_e, dv_e = _find_first_gate_valley(
            track, seg_end_lat, seg_end_lon, t1_chain,
            max_extra_idx=END_PROJECTION_EXTRA_IDX, accept_m=END_PROJECTION_ACCEPT_M,
            exit_rise_m=END_PROJECTION_EXIT_RISE_M)
        kv_s, rv_s, dv_s = _find_last_gate_valley(
            track, seg_start_lat, seg_start_lon, t0_chain,
            max_extra_idx=END_PROJECTION_EXTRA_IDX, accept_m=END_PROJECTION_ACCEPT_M,
            exit_rise_m=END_PROJECTION_EXIT_RISE_M)

        def it(k, r):
            ta = track.points[k].timestamp
            tb = track.points[k + 1].timestamp
            if not ta or not tb:
                return None
            return ta.timestamp() + r * (tb - ta).total_seconds()

        start_use_k, start_use_r = k_start, r_start
        if kv_s is not None and dv_s <= END_PROJECTION_ACCEPT_M:
            if d_start is None or d_start >= dv_s:
                start_use_k, start_use_r = kv_s, rv_s
        end_use_k, end_use_r = k_end, r_end
        if kv_e is not None and dv_e <= END_PROJECTION_ACCEPT_M:
            end_use_k, end_use_r = kv_e, rv_e

        ts_s = it(start_use_k, start_use_r)
        ts_e = it(end_use_k, end_use_r)
        t = abs(ts_e - ts_s) if ts_s and ts_e else None
        print("    START best: k=%d r=%.3f d=%.2fm ts=%s" % (k_start, r_start, d_start, it(k_start, r_start)))
        print("    START valley: k=%s r=%.3f d=%.2fm ts=%s" % (kv_s, rv_s, dv_s, it(kv_s, rv_s)))
        print("    END best: k=%d r=%.3f d=%.2fm ts=%s" % (k_end, r_end, d_end, it(k_end, r_end)))
        print("    END valley: k=%s r=%.3f d=%.2fm ts=%s" % (kv_e, rv_e, dv_e, it(kv_e, rv_e)))
        print("    USED start k=%d ts=%s ; end k=%d ts=%s" % (start_use_k, ts_s, end_use_k, ts_e))
        if t is not None:
            print("    => TIME sec = %.1f" % t)
    print()


segs = load_strava_segments("Strava_Segments")
bepa_seg = [s for s in segs if s["name"] == "BePa UP TRAIL"][0]["track"]

fit = load_fit("Examples/Pedalata_pomeridiana_11072026.fit")
analyze("FIT", fit, segs, bepa_seg)

gpx = load_gpx("Examples/Pedalata_pomeridiana_11072026.gpx")
analyze("GPX", gpx, segs, bepa_seg)
