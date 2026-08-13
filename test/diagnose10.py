"""Test hybrid approach: Haversine progress check + density guard against stitching."""
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

segments = load_strava_segments('Strava_Segments')
moneline = [s for s in segments if s['name'] == 'MoneLLine'][0]['track']
bepa = [s for s in segments if s['name'] == 'BePa UP TRAIL'][0]['track']
track1 = load_gpx('Examples/strava_full.gpx')
track2 = load_gpx('Examples/Pedalata_pomeridiana.gpx')


def walk_forward_hav(masked, track_lat, track_lon, segment_profile,
                     start_track_idx, max_gap=13, progress_ratio=2.5,
                     progress_slack_m=40.0):
    """Progress check uses Haversine distance between matched track points."""
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
            d_hav = _haversine_to_points(track_lat[prev_track], track_lon[prev_track],
                                         np.array([track_lat[track_i]]),
                                         np.array([track_lon[track_i]]))[0]
            if d_hav > max(d_seg * progress_ratio, d_seg + progress_slack_m):
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


def find_occ(cand, track_profile, segment_profile, track,
             start_tol, end_tol, max_gap,
             progress_ratio=2.5, progress_slack_m=40.0,
             min_match_points=5, min_length_m=20.0,
             min_density=0.4, max_density=1.8,
             max_total_passes=12):
    n_seg = len(cand)
    n_track = len(track_profile)
    used = np.zeros(n_track, dtype=bool)
    occ = []
    for reverse in (False, True):
        for _ in range(max_total_passes):
            masked = _masked_candidates(cand, used, reverse)
            profile = _reversed_profile(segment_profile) if reverse else segment_profile
            anchor_pool = []
            for i in range(min(start_tol + 1, n_seg)):
                anchor_pool.extend(int(x) for x in masked[i][:10])
            anchors = list(dict.fromkeys(anchor_pool))
            found = False
            for s0 in anchors:
                chain = walk_forward_hav(masked, track.latitudes, track.longitudes,
                                         profile, s0, max_gap, progress_ratio, progress_slack_m)
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
                occ.append((reverse, t0, t1, length_m, chain))
                used[t0:t1 + 1] = True
                found = True
                break
            if not found:
                break
    return occ


def run_test(track, seg, threshold, slack=40.0, ratio=2.5, max_density=1.8):
    prof_s, _ = track_distance_profile(seg)
    prof_t, _ = track_distance_profile(track)
    n_seg = len(seg.points)
    end_tol = max(2, int(0.10 * n_seg))
    max_gap = max(6, int(0.08 * n_seg))
    cand = _candidate_track_indices(track, seg, threshold)
    occ = find_occ(cand, prof_t, prof_s, track, end_tol, end_tol, max_gap,
                   progress_ratio=ratio, progress_slack_m=slack,
                   max_density=max_density)
    results = []
    for rv, t0, t1, lm, ch in occ:
        seg0 = ch[0][0]
        ft = track.points[ch[0][1]]
        lt = track.points[ch[-1][1]]
        ts = ((lt.timestamp - ft.timestamp).total_seconds()
              if ft.timestamp and lt.timestamp else None)
        results.append((seg0, ts, lm, len(ch), t0, t1))
    return results


# Test various max_density with Haversine + 25m + full-range mask
for md in [1.5, 1.8, 2.0, 2.2]:
    print(f"\n=== 25m + Haversine + full-mask + max_density={md} ===")
    for track, tname, seg, sname in [
        (track1, 'strava', moneline, 'MoneLLine'),
        (track2, 'Pedalata', moneline, 'MoneLLine'),
        (track1, 'strava', bepa, 'BePa'),
        (track2, 'Pedalata', bepa, 'BePa'),
    ]:
        res = run_test(track, seg, 25.0, slack=40.0, ratio=2.5, max_density=md)
        print(f"  {tname}/{sname}: {len(res)} occ")
        for seg0, ts, lm, nc, t0, t1 in res:
            if ts:
                print(f"    seg0={seg0} time={ts:.0f}s len={lm:.1f} chain={nc} "
                      f"t0={t0} t1={t1}")
            else:
                print(f"    seg0={seg0} (no ts) len={lm:.1f} chain={nc}")
