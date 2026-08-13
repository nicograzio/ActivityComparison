"""Test: 25m threshold + cumulative track distance + various slack values."""
import math
import numpy as np
from core.strava_analyzer import (
    load_strava_segments,
    _candidate_track_indices,
    _find_occurrences,
    _masked_candidates,
    _walk_forward,
    _reversed_profile,
)
from core.analyzer import track_distance_profile
from core.gpx_loader import load_gpx

segments = load_strava_segments('Strava_Segments')
moneline = [s for s in segments if s['name'] == 'MoneLLine'][0]['track']
bepa = [s for s in segments if s['name'] == 'BePa UP TRAIL'][0]['track']
track1 = load_gpx('Examples/strava_full.gpx')
track2 = load_gpx('Examples/Pedalata_pomeridiana.gpx')


def find_occurrences_v2(cand, track_profile, segment_profile,
                        start_tol, end_tol, max_gap,
                        progress_ratio=2.5, progress_slack_m=30.0,
                        min_match_points=5, min_length_m=20.0,
                        min_density=0.4, max_density=2.2,
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
                occ.append((reverse, t0, t1, length_m, chain))
                used[t0:t1 + 1] = True
                found = True
                break
            if not found:
                break
    return occ


def run_test(track, seg, seg_name, threshold, slack, ratio=2.5):
    prof_s, _ = track_distance_profile(seg)
    prof_t, _ = track_distance_profile(track)
    n_seg = len(seg.points)
    end_tol = max(2, int(0.10 * n_seg))
    max_gap = max(6, int(0.08 * n_seg))
    cand = _candidate_track_indices(track, seg, threshold)
    occ = find_occurrences_v2(cand, prof_t, prof_s, end_tol, end_tol, max_gap,
                              progress_ratio=ratio, progress_slack_m=slack)
    results = []
    for rv, t0, t1, lm, ch in occ:
        seg0 = ch[0][0]
        ft = track.points[ch[0][1]]
        lt = track.points[ch[-1][1]]
        ts = ((lt.timestamp - ft.timestamp).total_seconds()
              if ft.timestamp and lt.timestamp else None)
        results.append((seg0, ts, lm, len(ch), t0, t1))
    return results


print("=== BePa with 25m, slack=30, ratio=2.5 (original params, 25m) ===")
for track, tname in [(track1, 'strava_full'), (track2, 'Pedalata')]:
    res = run_test(track, bepa, 'BePa', 25.0, 30.0, 2.5)
    print(f"  {tname}: {len(res)} occurrences")
    for seg0, ts, lm, nc, t0, t1 in res:
        print(f"    seg_start={seg0} time={ts}s len={lm:.1f} chain={nc} t0={t0} t1={t1}")

print("\n=== MoneLLine strava_full, 25m, slack=30 ===")
res = run_test(track1, moneline, 'MoneLLine', 25.0, 30.0, 2.5)
for seg0, ts, lm, nc, t0, t1 in res:
    print(f"  seg_start={seg0} time={ts}s({ts//60:.0f}:{ts%60:02.0f}) len={lm:.1f} chain={nc}")

print("\n=== MoneLLine Pedalata, 25m, slack=30 ===")
res = run_test(track2, moneline, 'MoneLLine', 25.0, 30.0, 2.5)
for seg0, ts, lm, nc, t0, t1 in res:
    print(f"  seg_start={seg0} time={ts}s({ts//60:.0f}:{ts%60:02.0f}) len={lm:.1f} chain={nc}")

print("\n=== MoneLLine Pedalata, 25m, slack=50, ratio=2.0 ===")
res = run_test(track2, moneline, 'MoneLLine', 25.0, 50.0, 2.0)
for seg0, ts, lm, nc, t0, t1 in res:
    print(f"  seg_start={seg0} time={ts}s({ts//60:.0f}:{ts%60:02.0f}) len={lm:.1f} chain={nc}")

print("\n=== MoneLLine Pedalata, 25m, slack=60, ratio=2.0 ===")
res = run_test(track2, moneline, 'MoneLLine', 25.0, 60.0, 2.0)
for seg0, ts, lm, nc, t0, t1 in res:
    print(f"  seg_start={seg0} time={ts}s({ts//60:.0f}:{ts%60:02.0f}) len={lm:.1f} chain={nc}")

print("\n=== MoneLLine strava_full, 25m, slack=60, ratio=2.0 ===")
res = run_test(track1, moneline, 'MoneLLine', 25.0, 60.0, 2.0)
for seg0, ts, lm, nc, t0, t1 in res:
    print(f"  seg_start={seg0} time={ts}s({ts//60:.0f}:{ts%60:02.0f}) len={lm:.1f} chain={nc}")

print("\n=== BePa strava_full, 25m, slack=60, ratio=2.0 ===")
res = run_test(track1, bepa, 'BePa', 25.0, 60.0, 2.0)
for seg0, ts, lm, nc, t0, t1 in res:
    print(f"  seg_start={seg0} time={ts}s len={lm:.1f} chain={nc}")
