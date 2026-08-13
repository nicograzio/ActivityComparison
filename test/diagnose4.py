"""Trace why 2nd pass starts at seg 6/7 instead of seg 0."""
import numpy as np
from bisect import bisect_left
from core.strava_analyzer import (
    load_strava_segments,
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

prof_s, _ = track_distance_profile(moneline)
prof_t, _ = track_distance_profile(track2)
n_seg = len(moneline.points)
end_tol = max(2, int(0.10 * n_seg))
max_gap = max(6, int(0.08 * n_seg))
print(f"n_seg={n_seg} end_tol={end_tol} max_gap={max_gap}")

# First, find the first occurrence (forward) to establish used mask
cand = _candidate_track_indices(track2, moneline, 15.0)
used = np.zeros(len(track2.points), dtype=bool)

# Find first occurrence manually
def find_one_occurrence(cand, used, reverse, prof_t, prof_s,
                        start_tol, end_tol, max_gap, progress_ratio=2.5,
                        progress_slack_m=30.0, min_match_points=5,
                        min_length_m=20.0, min_density=0.4, max_density=2.2):
    n_seg = len(cand)
    masked = _masked_candidates(cand, used, reverse)
    profile = _reversed_profile(prof_s) if reverse else prof_s
    anchor_pool = []
    for i in range(min(start_tol + 1, n_seg)):
        anchor_pool.extend(int(x) for x in masked[i][:10])
    anchors = list(dict.fromkeys(anchor_pool))
    print(f"  Anchors (from seg 0..{start_tol}): {len(anchors)} unique")
    for s0 in anchors[:10]:
        chain = _walk_forward(masked, prof_t, profile, s0,
                              max_gap, progress_ratio, progress_slack_m)
        if not chain:
            continue
        seg_start = chain[0][0]
        seg_end = chain[-1][0]
        if seg_start > start_tol or seg_end < n_seg - 1 - end_tol:
            print(f"  Anchor {s0}: chain seg {seg_start}..{seg_end} -> COVERAGE FAIL")
            continue
        t0 = min(ti for _, ti in chain)
        t1 = max(ti for _, ti in chain)
        length_m = prof_t[t1] - prof_t[t0]
        if length_m < min_length_m or len(chain) < min_match_points:
            print(f"  Anchor {s0}: chain seg {seg_start}..{seg_end} -> LENGTH/MATCH FAIL (len={length_m:.1f})")
            continue
        seg_length = prof_s[-1]
        if not (min_density * seg_length <= length_m <= max_density * seg_length):
            print(f"  Anchor {s0}: chain seg {seg_start}..{seg_end} -> DENSITY FAIL (ratio={length_m/seg_length:.2f})")
            continue
        print(f"  Anchor {s0}: SUCCESS seg {seg_start}..{seg_end} t0={t0} t1={t1} len={length_m:.1f}")
        return chain, t0, t1
    print(f"  No anchor worked (tried {len(anchors[:10])}+ anchors)")
    return None, None, None

# First pass
print("=== FIRST PASS (forward) ===")
chain1, t0_1, t1_1 = find_one_occurrence(cand, used, False, prof_t, prof_s,
    end_tol, end_tol, max_gap)
if chain1:
    # Mark used (full range)
    used[t0_1:t1_1 + 1] = True
    print(f"  Marked used[{t0_1}:{t1_1+1}] = True")

# Second pass
print("\n=== SECOND PASS (forward) ===")
# Show what masked looks like for seg 0
masked = _masked_candidates(cand, used, False)
print(f"  masked[0] (seg 0): {masked[0][:10]} (len={len(masked[0])})")
print(f"  masked[1] (seg 1): {masked[1][:10]} (len={len(masked[1])})")
print(f"  masked[6] (seg 6): {masked[6][:10]} (len={len(masked[6])})")
print(f"  masked[7] (seg 7): {masked[7][:10]} (len={len(masked[7])})")

# Check if seg 0 has any candidates at all
for si in range(10):
    cm = masked[si]
    print(f"  masked[{si}]: len={len(cm)} idx_range={cm[0] if len(cm)>0 else 'N/A'}-{cm[-1] if len(cm)>0 else 'N/A'}")

chain2, t0_2, t1_2 = find_one_occurrence(cand, used, False, prof_t, prof_s,
    end_tol, end_tol, max_gap)

if chain2:
    seg_start = chain2[0][0]
    seg_end = chain2[-1][0]
    print(f"\n  Second pass: seg_start={seg_start}, seg_end={seg_end}")
    print(f"  Skipped seg points 0..{seg_start-1} at the beginning")
    # Show why: trace walk from seg 0's first candidate
    first_cand = masked[0]
    print(f"\n  Tracing walk from first seg-0 candidate...")
    for anchor in first_cand[:3]:
        chain = _walk_forward(masked, prof_t, prof_s, anchor,
                              max_gap, 2.5, 30.0)
        print(f"    Anchor {anchor}: chain_len={len(chain)} "
              f"seg_first={chain[0][0] if chain else 'N/A'} "
              f"seg_last={chain[-1][0] if chain else 'N/A'}")
        if chain and len(chain) < n_seg:
            # Find where it breaks
            seg_covered = set(si for si, _ in chain)
            breaks = []
            prev_seg = chain[0][0]
            for i in range(1, len(chain)):
                if chain[i][0] != prev_seg + 1:
                    # Check what was skipped
                    for skipped_si in range(prev_seg + 1, chain[i][0]):
                        breaks.append(f"seg {skipped_si} skipped (cand={len(masked[skipped_si])})")
                prev_seg = chain[i][0]
            for b in breaks[:5]:
                print(f"      {b}")
            if not breaks:
                print(f"      (no internal skips, chain just ended at seg {chain[-1][0]})")
