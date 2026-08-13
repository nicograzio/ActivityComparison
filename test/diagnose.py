"""Diagnostic script per capire problemi di match segmenti Strava."""
import math
import numpy as np
from core.strava_analyzer import (
    load_strava_segments,
    find_strava_segments_in_track,
    _candidate_track_indices,
    _find_occurrences,
)
from core.analyzer import haversine_distance, track_distance_profile
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

print("=" * 70)
print("BEPA UP TRAIL DIAGNOSTIC")
print("=" * 70)

seg_profile, seg_total = track_distance_profile(bepa)
print(f"BePa points: {len(bepa.points)}")
print(f"BePa total length: {seg_total:.1f} m")
print(f"BePa first: ({bepa.points[0].latitude}, {bepa.points[0].longitude})")
print(f"BePa last:  ({bepa.points[-1].latitude}, {bepa.points[-1].longitude})")

for track, tname in [(track1, 'strava_full.gpx'), (track2, 'Pedalata_pomeridiana.gpx')]:
    print(f"\n--- BePa in {tname} ---")
    candidates = _candidate_track_indices(track, bepa, distance_threshold_m=15.0)
    n_with_cand = sum(1 for c in candidates if len(c) > 0)
    print(f"  Segment points with >=1 candidate: {n_with_cand}/{len(bepa.points)}")
    n_cand_per = [len(c) for c in candidates]
    print(f"  Candidati: min={min(n_cand_per)}, max={max(n_cand_per)}, "
          f"media={np.mean(n_cand_per):.1f}")

    track_profile, track_total = track_distance_profile(track)

    end_tol = max(2, int(0.10 * len(bepa.points)))
    max_gap = max(6, int(0.08 * len(bepa.points)))
    print(f"  end_tol={end_tol}, start_tol={end_tol}, max_gap={max_gap}")

    occ = _find_occurrences(
        candidates, track_profile, seg_profile,
        min_match_points=5, start_tol=end_tol, end_tol=end_tol,
        max_gap=max_gap, progress_ratio=2.5, progress_slack_m=30.0,
        min_length_m=20.0, min_density=0.4, max_density=2.2,
    )
    print(f"  Occurrences found: {len(occ)}")
    for o in occ:
        reverse, t0, t1, length_m, chain = o
        print(f"    reverse={reverse}, t0={t0}, t1={t1}, length={length_m:.1f}, "
              f"chain_len={len(chain)}, coverage={chain[-1][0]+1}/{len(bepa.points)}")

    seg_start = bepa.points[0]
    seg_end = bepa.points[-1]
    dists_start = [haversine(seg_start.latitude, seg_start.longitude,
                             tp.latitude, tp.longitude) for tp in track.points]
    dists_end = [haversine(seg_end.latitude, seg_end.longitude,
                           tp.latitude, tp.longitude) for tp in track.points]
    idx_start = int(np.argmin(dists_start))
    idx_end = int(np.argmin(dists_end))
    print(f"  Closest track pt to BePa start (idx {idx_start}): dist={dists_start[idx_start]:.1f}m")
    print(f"  Closest track pt to BePa end (idx {idx_end}): dist={dists_end[idx_end]:.1f}m")

    print(f"  Start idx {idx_start} vs End idx {idx_end} -> "
          f"{'START before END (forward)' if idx_start < idx_end else 'END before START (reverse)'}")

    if idx_start < idx_end:
        d_track = track_profile[idx_end] - track_profile[idx_start]
    else:
        d_track = track_profile[idx_start] - track_profile[idx_end]
    density_ratio = d_track / seg_total if seg_total > 0 else 0
    print(f"  Track dist between start/end match: {d_track:.1f} m")
    print(f"  Density ratio (track/segment): {density_ratio:.2f} "
          f"{'(OK)' if 0.4 <= density_ratio <= 2.2 else '(FAIL!)'}")

    # Try with much looser thresholds to see if geometry matches at all
    for thr in [30.0, 50.0, 80.0]:
        for mr in [1.5, 3.0, 5.0]:
            for mn_den in [0.2]:
                occ2 = _find_occurrences(
                    _candidate_track_indices(track, bepa, distance_threshold_m=thr),
                    track_profile, seg_profile,
                    min_match_points=5, start_tol=end_tol, end_tol=end_tol,
                    max_gap=max_gap, progress_ratio=mr, progress_slack_m=50.0,
                    min_length_m=20.0, min_density=mn_den, max_density=3.0,
                )
                if occ2:
                    print(f"  [thr={thr}, ratio={mr}] Occurrences found: {len(occ2)}")
                    for o2 in occ2:
                        rv, t0b, t1b, lb, ch = o2
                        print(f"    reverse={rv}, t0={t0b}, t1={t1b}, len={lb:.1f}, "
                              f"chain={len(ch)}, seg_cov={ch[-1][0]+1}/{len(bepa.points)}")

print("\n" + "=" * 70)
print("MONELLINE TIMING DIAGNOSTIC")
print("=" * 70)

mon_profile, mon_total = track_distance_profile(moneline)
print(f"MoneLLine points: {len(moneline.points)}")
print(f"MoneLLine total length: {mon_total:.1f} m")

for track, tname in [(track1, 'strava_full.gpx'), (track2, 'Pedalata_pomeridiana.gpx')]:
    print(f"\n--- MoneLLine in {tname} ---")
    occs = find_strava_segments_in_track(segments, track)
    for occ in occs:
        if occ['segment_name'] != 'MoneLLine':
            continue
        t0, t1 = occ['start_idx'], occ['end_idx']
        print(f"  Occurrence: t0={t0}, t1={t1}, time={occ['time_sec']}s, "
              f"length={occ['length_m']:.1f}m, n_match={occ['n_match_points']}")

        candidates = _candidate_track_indices(track, moneline, 15.0)
        track_profile, _ = track_distance_profile(track)
        end_tol = max(2, int(0.10 * len(moneline.points)))
        max_gap = max(6, int(0.08 * len(moneline.points)))
        occ_list = _find_occurrences(
            candidates, track_profile, mon_profile,
            min_match_points=5, start_tol=end_tol, end_tol=end_tol,
            max_gap=max_gap, progress_ratio=2.5, progress_slack_m=30.0,
        )
        for reverse, c0, c1, clen, chain in occ_list:
            if c0 == t0 and c1 == t1:
                seg_first = chain[0][0]
                seg_last = chain[-1][0]
                print(f"    Chain: seg points {seg_first}..{seg_last} "
                      f"(need 0..{len(moneline.points)-1-end_tol})")
                first_tp = track.points[chain[0][1]]
                last_tp = track.points[chain[-1][1]]
                if first_tp.timestamp and last_tp.timestamp:
                    direct_time = (last_tp.timestamp - first_tp.timestamp).total_seconds()
                    print(f"    Direct time (chain first->last): {direct_time:.1f}s")
                    slice_time = (track.points[t1].timestamp - track.points[t0].timestamp).total_seconds()
                    print(f"    Slice time (min->max idx): {slice_time:.1f}s")
                for i in range(1, len(chain)):
                    dseg = mon_profile[chain[i][0] - 0] - mon_profile[chain[i - 1][0]]
                    dtrk = track_profile[chain[i][1]] - track_profile[chain[i - 1][1]]
                    if dseg > 0.001 and dtrk > dseg * 2.5:
                        print(f"    PROGRESS VIOLATION at chain[{i}]: "
                              f"dseg={dseg:.1f} dtrk={dtrk:.1f} ratio={dtrk/dseg:.2f}")
