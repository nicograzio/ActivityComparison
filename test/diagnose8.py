"""Trace the exact walk_forward step-by-step for the 2nd pass."""
import math
import numpy as np
from bisect import bisect_left
from core.strava_analyzer import (
    load_strava_segments,
    _candidate_track_indices,
    _masked_candidates,
    _walk_forward,
    _reversed_profile,
)
from core.analyzer import haversine_distance, track_distance_profile
from core.gpx_loader import load_gpx

segments = load_strava_segments('Strava_Segments')
moneline = [s for s in segments if s['name'] == 'MoneLLine'][0]['track']
track2 = load_gpx('Examples/Pedalata_pomeridiana.gpx')

prof_s, _ = track_distance_profile(moneline)
prof_t, _ = track_distance_profile(track2)
n_seg = len(moneline.points)
end_tol = max(2, int(0.10 * n_seg))
max_gap = max(6, int(0.08 * n_seg))

# Reproduce first occurrence to establish used mask
cand = _candidate_track_indices(track2, moneline, 15.0)
used = np.zeros(len(track2.points), dtype=bool)

# Find first occurrence
masked1 = _masked_candidates(cand, used, False)
chain1 = _walk_forward(masked1, prof_t, prof_s, 1460,
                       max_gap, 2.5, 30.0)
t0_1 = min(ti for _, ti in chain1)
t1_1 = max(ti for _, ti in chain1)
used[t0_1:t1_1 + 1] = True
print(f"First occurrence: t0={t0_1} t1={t1_1} used mask set")
print(f"Chain covers seg {chain1[0][0]}..{chain1[-1][0]} ({len(chain1)} pts)")

# Now mask for second pass
masked2 = _masked_candidates(cand, used, False)

# Find the first seg-0 candidate that would be an anchor
seg0_cands = masked2[0]
print(f"\nSeg 0 masked candidates: {seg0_cands[:10]} (total {len(seg0_cands)})")

# Trace walk from the first seg-0 anchor
anchor = int(seg0_cands[0])
print(f"\nTracing _walk_forward from anchor {anchor}:")
print(f"  start_tol={end_tol}, max_gap={max_gap}")
print(f"  progress_ratio=2.5, progress_slack_m=30.0")

# Manually trace
t = anchor - 1
prev_seg = None
prev_track = None
skipped = 0
for seg_i in range(min(20, n_seg)):
    cand_i = masked2[seg_i]
    k = bisect_left(cand_i, t)
    if k >= len(cand_i):
        skipped += 1
        print(f"  seg {seg_i}: NO candidate >= {t} (cand range {cand_i[0] if len(cand_i)>0 else 'empty'}-{cand_i[-1] if len(cand_i)>0 else 'empty'}, len={len(cand_i)}) -> SKIP #{skipped}")
        if skipped > max_gap:
            print(f"  -> BREAK: skipped={skipped} > max_gap={max_gap}")
            break
        continue

    track_i = int(cand_i[k])
    if prev_seg is not None and prev_track is not None:
        d_seg = prof_s[seg_i] - prof_s[prev_seg]
        d_trk = prof_t[track_i] - prof_t[prev_track]
        limit = max(d_seg * 2.5, d_seg + 30.0)
        passed = d_trk <= limit
        geod = haversine_distance(moneline.points[seg_i], track2.points[track_i])
        prev_geod = haversine_distance(moneline.points[prev_seg], track2.points[prev_track])
        status = "PASS" if passed else "SKIP"
        print(f"  seg {seg_i}: cand={track_i} (cand[{k}], d_geod={geod:.1f}m, prev_geod={prev_geod:.1f}m) "
              f"d_seg={d_seg:.2f} d_trk={d_trk:.1f} limit={limit:.1f} -> {status}")
        if not passed:
            skipped += 1
            print(f"    SKIP #{skipped}")
            if skipped > max_gap:
                print(f"    -> BREAK: skipped={skipped} > max_gap={max_gap}")
                break
            continue

    prev_seg = seg_i
    prev_track = track_i
    t = track_i
    skipped = 0

# Also show what seg points 0-7 look like on the segment
print("\nSegment points 0-7 profile distances:")
for i in range(8):
    print(f"  seg {i}: prof={prof_s[i]:.2f} lat={moneline.points[i].latitude:.6f} lon={moneline.points[i].longitude:.6f}")
