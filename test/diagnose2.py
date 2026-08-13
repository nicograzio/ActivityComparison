"""Focused test to confirm fix hypotheses."""
import math
import numpy as np
from core.strava_analyzer import (
    load_strava_segments,
    _candidate_track_indices,
    _find_occurrences,
)
from core.analyzer import track_distance_profile
from core.gpx_loader import load_gpx

EARTH_RADIUS_M = 6371000.0


def haversine(a_lat, a_lon, b_lat, b_lon):
    r = EARTH_RADIUS_M
    la1, la2 = math.radians(a_lat), math.radians(b_lat)
    dlat = math.radians(b_lat - a_lat)
    dlon = math.radians(b_lon - a_lon)
    h = math.sin(dlat / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlon / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(h), math.sqrt(1 - h))


segments = load_strava_segments('Strava_Segments')
track1 = load_gpx('Examples/strava_full.gpx')
track2 = load_gpx('Examples/Pedalata_pomeridiana.gpx')
bepa = [s for s in segments if s['name'] == 'BePa UP TRAIL'][0]['track']
moneline = [s for s in segments if s['name'] == 'MoneLLine'][0]['track']

# ---- TEST 1: BePa with threshold 25m ----
print("TEST 1: BePa with distance_threshold_m=25.0")
for track, tname in [(track1, 'strava_full.gpx'), (track2, 'Pedalata_pomeridiana.gpx')]:
    prof_s, _ = track_distance_profile(bepa)
    prof_t, _ = track_distance_profile(track)
    cand = _candidate_track_indices(track, bepa, 25.0)
    n_wc = sum(1 for c in cand if len(c) > 0)
    end_tol = max(2, int(0.10 * len(bepa.points)))
    max_gap = max(6, int(0.08 * len(bepa.points)))
    occ = _find_occurrences(cand, prof_t, prof_s, min_match_points=5,
                            start_tol=end_tol, end_tol=end_tol, max_gap=max_gap,
                            progress_ratio=2.5, progress_slack_m=30.0)
    print(f"  {tname}: {n_wc}/712 have candidates, {len(occ)} occurrences")
    for o in occ:
        rv, t0, t1, lm, ch = o
        print(f"    reverse={rv} t0={t0} t1={t1} len={lm:.1f}m chain={len(ch)} "
              f"seg_start={ch[0][0]} seg_end={ch[-1][0]}")

# ---- TEST 2: MoneLLine seg points 0-9 candidate distances ----
print("\nTEST 2: MoneLLine seg points 0-9 candidates in Pedalata (15m threshold)")
cand = _candidate_track_indices(track2, moneline, 15.0)
for si in range(10):
    c = cand[si]
    print(f"  seg pt {si} ({moneline.points[si].latitude:.6f},"
          f"{moneline.points[si].longitude:.6f}): {len(c)} candidates")
    for ti in c[:6]:
        d = haversine(moneline.points[si].latitude, moneline.points[si].longitude,
                      track2.points[ti].latitude, track2.points[ti].longitude)
        region = ("1st-pass(1460-1583)" if 1400 <= ti <= 1600
                  else ("2nd-pass(2686+)" if ti >= 2600 else f"idx={ti}"))
        print(f"    track idx {ti} dist={d:.1f}m {region}")

# ---- TEST 3: MoneLLine with threshold 25m ----
print("\nTEST 3: MoneLLine with 25m threshold")
profs, _ = track_distance_profile(moneline)
for track, tname in [(track1, 'strava_full.gpx'), (track2, 'Pedalata_pomeridiana.gpx')]:
    prof_t, _ = track_distance_profile(track)
    cand = _candidate_track_indices(track, moneline, 25.0)
    n_wc = sum(1 for c in cand if len(c) > 0)
    end_tol = max(2, int(0.10 * len(moneline.points)))
    max_gap = max(6, int(0.08 * len(moneline.points)))
    occ = _find_occurrences(cand, prof_t, profs, min_match_points=5,
                            start_tol=end_tol, end_tol=end_tol, max_gap=max_gap,
                            progress_ratio=2.5, progress_slack_m=30.0)
    print(f"  {tname}: {n_wc}/169 have candidates, {len(occ)} occurrences")
    for o in occ:
        rv, t0, t1, lm, ch = o
        seg_first = ch[0][0]
        first_tp = track.points[ch[0][1]]
        last_tp = track.points[ch[-1][1]]
        ts = ((last_tp.timestamp - first_tp.timestamp).total_seconds()
              if first_tp.timestamp and last_tp.timestamp else None)
        print(f"    reverse={rv} t0={t0} t1={t1} len={lm:.1f}m "
              f"chain={len(ch)} seg_start={seg_first} time={ts}s")

# ---- TEST 4: seg 0 candidate distances to second pass area ----
print("\nTEST 4: MoneLLine seg pt 0 nearest track dists in 2nd-pass area")
cand25 = _candidate_track_indices(track2, moneline, 25.0)
for si in range(8):
    c = cand25[si]
    near_2nd = [ti for ti in c if ti >= 2600]
    dists = [haversine(moneline.points[si].latitude, moneline.points[si].longitude,
                       track2.points[ti].latitude, track2.points[ti].longitude)
             for ti in near_2nd]
    print(f"  seg pt {si}: {len(c)} total cand, {len(near_2nd)} near 2nd pass "
          f"min_dist={min(dists) if dists else 'N/A'}")
