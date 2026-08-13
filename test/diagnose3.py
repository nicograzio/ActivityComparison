"""Test: does marking only chain indices (not full range) fix the 2nd pass?"""
import math
import numpy as np
from bisect import bisect_left
from core.strava_analyzer import (
    load_strava_segments,
    find_strava_segments_in_track,
    _candidate_track_indices,
    _masked_candidates,
    _walk_forward,
    _reversed_profile,
)
from core.analyzer import track_distance_profile
from core.gpx_loader import load_gpx

segments = load_strava_segments('Strava_Segments')
track2 = load_gpx('Examples/Pedalata_pomeridiana.gpx')
moneline = [s for s in segments if s['name'] == 'MoneLLine'][0]['track']


def _find_occurrences_v2(candidates, track_profile, segment_profile,
                         min_match_points=5, start_tol=16, end_tol=16,
                         max_gap=13, progress_ratio=2.5, progress_slack_m=30.0,
                         min_length_m=20.0, min_density=0.4, max_density=2.2,
                         max_total_passes=12, mark_only_chain=False):
    n_seg = len(candidates)
    n_track = len(track_profile)
    used = np.zeros(n_track, dtype=bool)
    occurrences = []
    for reverse in (False, True):
        for _ in range(max_total_passes):
            masked = _masked_candidates(candidates, used, reverse)
            profile = _reversed_profile(segment_profile) if reverse else segment_profile
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
                seg_start, _ = chain[0]
                seg_end, _ = chain[-1]
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
                    chain_indices = np.array([ti for _, ti in chain])
                    used[chain_indices] = True
                else:
                    used[t0:t1 + 1] = True
                found = True
                break
            if not found:
                break
    return occurrences


def get_seg_params(t):
    n_seg = len(moneline.points)
    end_tol = max(2, int(0.10 * n_seg))
    max_gap = max(6, int(0.08 * n_seg))
    return end_tol, max_gap


prof_s, _ = track_distance_profile(moneline)
prof_t, _ = track_distance_profile(track2)
end_tol, max_gap = get_seg_params(track2)

# Current approach (15m, mark full range)
cand15 = _candidate_track_indices(track2, moneline, 15.0)
occ_cur = _find_occurrences_v2(cand15, prof_t, prof_s,
    min_match_points=5, start_tol=end_tol, end_tol=end_tol, max_gap=max_gap)
print("CURRENT (15m, mark full range):")
for rv, t0, t1, lm, ch in occ_cur:
    seg0 = ch[0][0]
    ft = track2.points[ch[0][1]]
    lt = track2.points[ch[-1][1]]
    ts = (lt.timestamp - ft.timestamp).total_seconds()
    print(f"  t0={t0} t1={t1} seg_start={seg0} chain={len(ch)} "
          f"len={lm:.1f} time={ts}s")

# v2: 15m, mark only chain indices
occ_v2a = _find_occurrences_v2(cand15, prof_t, prof_s,
    min_match_points=5, start_tol=end_tol, end_tol=end_tol, max_gap=max_gap,
    mark_only_chain=True)
print("\nV2 (15m, mark only chain indices):")
for rv, t0, t1, lm, ch in occ_v2a:
    seg0 = ch[0][0]
    ft = track2.points[ch[0][1]]
    lt = track2.points[ch[-1][1]]
    ts = (lt.timestamp - ft.timestamp).total_seconds()
    print(f"  t0={t0} t1={t1} seg_start={seg0} chain={len(ch)} "
          f"len={lm:.1f} time={ts}s")

# v3: 25m, mark only chain indices
cand25 = _candidate_track_indices(track2, moneline, 25.0)
occ_v3 = _find_occurrences_v2(cand25, prof_t, prof_s,
    min_match_points=5, start_tol=end_tol, end_tol=end_tol, max_gap=max_gap,
    mark_only_chain=True)
print("\nV3 (25m, mark only chain indices):")
for rv, t0, t1, lm, ch in occ_v3:
    seg0 = ch[0][0]
    ft = track2.points[ch[0][1]]
    lt = track2.points[ch[-1][1]]
    ts = (lt.timestamp - ft.timestamp).total_seconds()
    print(f"  t0={t0} t1={t1} seg_start={seg0} chain={len(ch)} "
          f"len={lm:.1f} time={ts}s")

# v4: 25m, mark full range (original behavior with 25m)
occ_v4 = _find_occurrences_v2(cand25, prof_t, prof_s,
    min_match_points=5, start_tol=end_tol, end_tol=end_tol, max_gap=max_gap,
    mark_only_chain=False)
print("\nV4 (25m, mark full range - same as original with 25m):")
for rv, t0, t1, lm, ch in occ_v4:
    seg0 = ch[0][0]
    ft = track2.points[ch[0][1]]
    lt = track2.points[ch[-1][1]]
    ts = (lt.timestamp - ft.timestamp).total_seconds()
    print(f"  t0={t0} t1={t1} seg_start={seg0} chain={len(ch)} "
          f"len={lm:.1f} time={ts}s")
