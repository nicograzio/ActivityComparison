"""Test modified progress check using Haversine distance."""
import math
import numpy as np
from bisect import bisect_left
from core.strava_analyzer import (
    load_strava_segments,
    _candidate_track_indices,
    _masked_candidates,
    _reversed_profile,
    _haversine_to_points,
)
from core.analyzer import track_distance_profile
from core.gpx_loader import load_gpx

EARTH_RADIUS_M = 6371000.0
segments = load_strava_segments('Strava_Segments')
track2 = load_gpx('Examples/Pedalata_pomeridiana.gpx')
moneline = [s for s in segments if s['name'] == 'MoneLLine'][0]['track']


def haversine_np(lat1, lon1, lat2, lon2):
    """Distanza haversine vettoriale tra due array di punti."""
    r = EARTH_RADIUS_M
    la1, la2 = np.radians(lat1), np.radians(lat2)
    dlat = np.radians(lat2 - lat1)
    dlon = np.radians(lon2 - lon1)
    h = np.sin(dlat / 2) ** 2 + np.cos(la1) * np.cos(la2) * np.sin(dlon / 2) ** 2
    return r * 2 * np.arctan2(np.sqrt(h), np.sqrt(1 - h))


def walk_forward_haversine(masked, track_lat, track_lon, segment_profile,
                           start_track_idx, max_gap=13, progress_ratio=2.5,
                           progress_slack_m=30.0):
    """Version of _walk_forward using Haversine distance for progress check."""
    chain = []
    t = start_track_idx - 1
    prev_seg = None
    prev_track = None
    skipped = 0
    for seg_i in range(len(masked)):
        cand = masked[seg_i]
        k = bisect_left(cand, t)
        if k >= len(cand):
            skipped += 1
            if skipped > max_gap:
                break
            continue
        track_i = int(cand[k])
        if prev_seg is not None and prev_track is not None:
            d_seg = segment_profile[seg_i] - segment_profile[prev_seg]
            # Haversine distance between matched track points
            d_track = haversine_np(
                track_lat[prev_track], track_lon[prev_track],
                track_lat[track_i], track_lon[track_i]
            )
            if d_track > max(d_seg * progress_ratio, d_seg + progress_slack_m):
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


def find_occurrences_v2(cand, track_profile, segment_profile, track_lat, track_lon,
                        min_match_points=5, start_tol=16, end_tol=16,
                        max_gap=13, progress_ratio=2.5, progress_slack_m=30.0,
                        min_length_m=20.0, min_density=0.4, max_density=2.2,
                        use_haversine=False, mark_only_chain=False):
    n_seg = len(cand)
    n_track = len(track_profile)
    used = np.zeros(n_track, dtype=bool)
    occurrences = []
    for reverse in (False, True):
        for _ in range(12):
            masked = _masked_candidates(cand, used, reverse)
            profile = _reversed_profile(segment_profile) if reverse else segment_profile
            anchor_pool = []
            for i in range(min(start_tol + 1, n_seg)):
                anchor_pool.extend(int(x) for x in masked[i][:10])
            anchors = list(dict.fromkeys(anchor_pool))
            found = False
            for s0 in anchors:
                if use_haversine:
                    chain = walk_forward_haversine(
                        masked, track_lat, track_lon, profile, s0,
                        max_gap, progress_ratio, progress_slack_m)
                else:
                    from core.strava_analyzer import _walk_forward
                    chain = _walk_forward(masked, track_profile, profile, s0,
                                          max_gap, progress_ratio, progress_slack_m)
                if not chain:
                    continue
                seg_start = chain[0][0]
                seg_end = chain[-1][0]
                if seg_start > start_tol or seg_end < n_seg - 1 - end_tol:
                    continue
                t0 = min(ti for _, ti in chain)
                t1 = max(ti for _, ti in chain)
                length_m = track_profile[t1] - track_profile[t0]
                if length_m < min_length_m or len(chain) < min_match_points:
                    continue
                seg_length = segment_profile[-1]
                if not (min_density * seg_length <= length_m <= max_density * seg_length):
                    continue
                occurrences.append((reverse, t0, t1, length_m, chain))
                if mark_only_chain:
                    used[np.array([ti for _, ti in chain])] = True
                else:
                    used[t0:t1 + 1] = True
                found = True
                break
            if not found:
                break
    return occurrences


prof_s, _ = track_distance_profile(moneline)
prof_t, _ = track_distance_profile(track2)
track_lat = track2.latitudes
track_lon = track2.longitudes

end_tol = max(2, int(0.10 * len(moneline.points)))
max_gap = max(6, int(0.08 * len(moneline.points)))

cand15 = _candidate_track_indices(track2, moneline, 15.0)
cand25 = _candidate_track_indices(track2, moneline, 25.0)

print("=== MoneLLine 2nd pass analysis (Pedalata_pomeridiana) ===")
print(f"end_tol={end_tol} max_gap={max_gap}")

# Current: 15m, cumulative track distance progress check
occ1 = find_occurrences_v2(cand15, prof_t, prof_s, track_lat, track_lon,
    end_tol, end_tol, max_gap, use_haversine=False)
print(f"\nOriginal (15m, cumdist progress):")
for rv, t0, t1, lm, ch in occ1:
    ft = track2.points[ch[0][1]]
    lt = track2.points[ch[-1][1]]
    ts = (lt.timestamp - ft.timestamp).total_seconds()
    print(f"  t0={t0} t1={t1} seg0={ch[0][0]} chain={len(ch)} len={lm:.1f} time={ts}s")

# Fixed: 25m, Haversine progress check
occ2 = find_occurrences_v2(cand25, prof_t, prof_s, track_lat, track_lon,
    end_tol, end_tol, max_gap, use_haversine=True, mark_only_chain=True)
print(f"\nFixed (25m, haversine progress, mark chain only):")
for rv, t0, t1, lm, ch in occ2:
    ft = track2.points[ch[0][1]]
    lt = track2.points[ch[-1][1]]
    ts = (lt.timestamp - ft.timestamp).total_seconds()
    print(f"  t0={t0} t1={t1} seg0={ch[0][0]} chain={len(ch)} len={lm:.1f} time={ts}s")

# Also test strava_full with the fix
track1 = load_gpx('Examples/strava_full.gpx')
prof_t1, _ = track_distance_profile(track1)
lat1 = track1.latitudes
lon1 = track1.longitudes
cand1_25 = _candidate_track_indices(track1, moneline, 25.0)
occ3 = find_occurrences_v2(cand1_25, prof_t1, prof_s, lat1, lon1,
    end_tol, end_tol, max_gap, use_haversine=True, mark_only_chain=True)
print(f"\nFixed - strava_full (25m, haversine progress):")
for rv, t0, t1, lm, ch in occ3:
    ft = track1.points[ch[0][1]]
    lt = track1.points[ch[-1][1]]
    ts = (lt.timestamp - ft.timestamp).total_seconds()
    print(f"  t0={t0} t1={t1} seg0={ch[0][0]} chain={len(ch)} len={lm:.1f} time={ts}s")

# Test BePa with 25m + haversine
bepa = [s for s in segments if s['name'] == 'BePa UP TRAIL'][0]['track']
prof_bs, _ = track_distance_profile(bepa)
b_end_tol = max(2, int(0.10 * len(bepa.points)))
b_max_gap = max(6, int(0.08 * len(bepa.points)))
for track, tname in [(track1, 'strava_full'), (track2, 'Pedalata')]:
    c = _candidate_track_indices(track, bepa, 25.0)
    pt, _ = track_distance_profile(track)
    occ = find_occurrences_v2(c, pt, prof_bs, track.latitudes, track.longitudes,
        b_end_tol, b_end_tol, b_max_gap, use_haversine=True, mark_only_chain=True)
    print(f"\nBePa in {tname} (25m, haversine): {len(occ)} occurrences")
    for rv, t0, t1, lm, ch in occ:
        ft = track.points[ch[0][1]]
        lt = track.points[ch[-1][1]]
        ts = (lt.timestamp - ft.timestamp).total_seconds()
        print(f"  t0={t0} t1={t1} seg0={ch[0][0]} seg_end={ch[-1][0]} "
              f"len={lm:.1f} time={ts}s")
