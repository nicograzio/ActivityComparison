"""Test: Expanded endpoint refinement to handle meanders at start/end."""
import math
import numpy as np
from bisect import bisect_left
from core.strava_analyzer import (
    load_strava_segments,
    _candidate_track_indices,
    _masked_candidates,
    _reversed_profile,
    _walk_forward,
)
from core.analyzer import track_distance_profile
from core.gpx_loader import load_gpx

segments = load_strava_segments('Strava_Segments')
moneline = [s for s in segments if s['name'] == 'MoneLLine'][0]['track']
bepa = [s for s in segments if s['name'] == 'BePa UP TRAIL'][0]['track']
track1 = load_gpx('Examples/strava_full.gpx')
track2 = load_gpx('Examples/Pedalata_pomeridiana.gpx')

def haversine_d(p1, p2):
    r = 6371000.0
    lat1, lon1 = math.radians(p1[0]), math.radians(p1[1])
    lat2, lon2 = math.radians(p2[0]), math.radians(p2[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return r * 2 * math.atan2(math.sqrt(h), math.sqrt(1-h))

def find_occ_expanded_endpoints(cand, track_profile, seg_profile, seg_track, gps_track,
                                start_tol, end_tol, max_gap,
                                progress_ratio=2.5, progress_slack_m=30.0,
                                min_match_points=5, min_length_m=20.0,
                                min_density=0.4, max_density=2.2,
                                max_total_passes=12):
    n_seg = len(cand)
    n_track = len(track_profile)
    used = np.zeros(n_track, dtype=bool)
    occ = []
    seg_start_pt = (seg_track.points[0].latitude, seg_track.points[0].longitude)
    seg_end_pt = (seg_track.points[-1].latitude, seg_track.points[-1].longitude)

    for reverse in (False, True):
        for _ in range(max_total_passes):
            masked = _masked_candidates(cand, used, reverse)
            profile = _reversed_profile(seg_profile) if reverse else seg_profile
            anchor_pool = []
            for i in range(min(start_tol + 1, n_seg)):
                anchor_pool.extend(int(x) for x in masked[i][:10])
            anchors = list(dict.fromkeys(anchor_pool))
            found = False
            for s0 in anchors:
                chain = _walk_forward(masked, track_profile, profile, s0,
                                      max_gap, progress_ratio, progress_slack_m)
                if not chain:
                    continue
                seg_start = chain[0][0]
                seg_end = chain[-1][0]
                if seg_start > start_tol or seg_end < n_seg - 1 - end_tol:
                    continue
                t0_chain = min(ti for _, ti in chain)
                t1_chain = max(ti for _, ti in chain)
                
                # REFINEMENT WITH EXPANDED RANGE
                # Look 200m or 50 points around the detected chain
                search_lo = max(0, t0_chain - 100)
                search_hi = min(n_track - 1, t1_chain + 100)
                
                best_start_dist = float('inf')
                best_start_idx = t0_chain
                for ti in range(search_lo, search_hi + 1):
                    d = haversine_d(seg_start_pt, (gps_track.points[ti].latitude, gps_track.points[ti].longitude))
                    if d < best_start_dist:
                        best_start_dist = d
                        best_start_idx = ti
                
                best_end_dist = float('inf')
                best_end_idx = t1_chain
                for ti in range(search_lo, search_hi + 1):
                    d = haversine_d(seg_end_pt, (gps_track.points[ti].latitude, gps_track.points[ti].longitude))
                    if d < best_end_dist:
                        best_end_dist = d
                        best_end_idx = ti
                
                t0 = min(best_start_idx, best_end_idx)
                t1 = max(best_start_idx, best_end_idx)
                
                length_m = track_profile[t1] - track_profile[t0]
                if length_m < min_length_m or len(chain) < min_match_points:
                    continue
                seg_length = seg_profile[-1]
                if not (min_density * seg_length <= length_m <= max_density * seg_length):
                    continue
                
                occ.append((reverse, t0, t1, length_m, chain, best_start_dist, best_end_dist))
                used[t0_chain:t1_chain+1] = True
                found = True
                break
            if not found:
                break
    return occ

def run_test(gps_track, seg_track, threshold):
    seg_prof, _ = track_distance_profile(seg_track)
    trk_prof, _ = track_distance_profile(gps_track)
    n_seg = len(seg_track.points)
    end_tol = max(2, int(0.10 * n_seg))
    max_gap = max(6, int(0.08 * n_seg))
    cand = _candidate_track_indices(gps_track, seg_track, threshold)
    occ = find_occ_expanded_endpoints(cand, trk_prof, seg_prof, seg_track, gps_track,
                                      end_tol, end_tol, max_gap)
    results = []
    for rv, t0, t1, lm, ch, bd, ed in occ:
        seg0 = ch[0][0]
        ft = gps_track.points[t0]
        lt = gps_track.points[t1]
        ts = ((lt.timestamp - ft.timestamp).total_seconds()
              if ft.timestamp and lt.timestamp else None)
        results.append((seg0, ts, lm, len(ch), t0, t1, bd, ed))
    return results

print("=== Expanded endpoint refinement (25m threshold) ===")
print("(Strava expects: strava_full ML=113/112s, Pedalata ML=125/125s)\n")

for gps_track, tname, seg_track, sname in [
    (track1, 'strava_full', moneline, 'MoneLLine'),
    (track2, 'Pedalata', moneline, 'MoneLLine'),
    (track1, 'strava_full', bepa, 'BePa'),
    (track2, 'Pedalata', bepa, 'BePa'),
]:
    res = run_test(gps_track, seg_track, 25.0)
    print(f"  {tname}/{sname}: {len(res)} occurrences")
    for seg0, ts, lm, nc, t0, t1, bd, ed in res:
        if ts:
            print(f"    seg0={seg0} time={ts:.0f}s({ts//60:.0f}:{ts%60:02.0f}) "
                  f"len={lm:.1f} chain={nc} t0={t0} t1={t1} "
                  f"start_dist={bd:.1f}m end_dist={ed:.1f}m")
        else:
            print(f"    seg0={seg0} (no ts) len={lm:.1f} chain={nc}")
