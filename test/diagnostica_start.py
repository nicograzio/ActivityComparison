"""Stampa le distanze dall'inizio del segmento BePa UP TRAIL intorno allo start
(identificato via minimo geografico) per FIT e GPX, per capire perche' il FIT
trova uno start piu' tardino."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.fit_loader import load_fit
from core.gpx_loader import load_gpx
from core.strava_analyzer import (
    load_strava_segments,
    _find_best_track_projection,
    _find_last_gate_valley,
    haversine_distance,
    PROJECTION_WINDOW,
    END_PROJECTION_EXTRA_IDX,
    END_PROJECTION_ACCEPT_M,
    END_PROJECTION_EXIT_RISE_M,
)


def dump(label, track, bepa_seg, center):
    p_start_lat = bepa_seg.points[0].latitude
    p_start_lon = bepa_seg.points[0].longitude
    k, r, d = _find_best_track_projection(track, p_start_lat, p_start_lon, center, window=PROJECTION_WINDOW)
    print("=== %s: minimo geografico k=%d r=%.3f d=%.2fm (PROJECTION_WINDOW=%d) ==="
          % (label, k, r, d, PROJECTION_WINDOW))
    for kk in range(k - 30, k + 10):
        if 0 <= kk < len(track.points):
            dd = haversine_distance(track.points[kk], bepa_seg.points[0])
            ts = track.points[kk].timestamp
            print("  k=%4d dist=%.2fm speed=%.1fkmh @ %s" % (
                kk, dd,
                (track.points[kk].speed * 3.6 if track.points[kk].speed else float('nan')),
                ts))
    print("  --- backward gate valley (extra=%d, accept=%.0f, exit=%.0f) ---"
          % (END_PROJECTION_EXTRA_IDX, END_PROJECTION_ACCEPT_M, END_PROJECTION_EXIT_RISE_M))
    for extra, acc, exc in [
        (60, 45.0, 10.0),
        (80, 50.0, 20.0),
        (120, 60.0, 30.0),
    ]:
        kv, rv, dv = _find_last_gate_valley(
            track, p_start_lat, p_start_lon, center,
            max_extra_idx=extra, accept_m=acc, exit_rise_m=exc)
        ts = track.points[kv].timestamp if kv is not None else None
        print("  extra=%d acc=%.0f exit=%.0f -> k=%s r=%.3f d=%.2fm ts=%s"
              % (extra, acc, exc, kv, rv, dv, ts))
    print()


segs = load_strava_segments("Strava_Segments")
bepa_seg = [s for s in segs if s["name"] == "BePa UP TRAIL"][0]["track"]

fit = load_fit("Examples/Pedalata_pomeridiana_11072026.fit")
dump("FIT", fit, bepa_seg, 2095)

gpx = load_gpx("Examples/Pedalata_pomeridiana_11072026.gpx")
dump("GPX", gpx, bepa_seg, 1735)
