"""Test the final fix combination: 25m + Haversine progress + full-range mask."""
import math
import numpy as np
from bisect import bisect_left
from core.strava_analyzer import (
    load_strava_segments,
    _candidate_track_indices,
    _masked_candidates,
    _reversed_profile,
)
from core.analyzer import track_distance_profile
from core.gpx_loader import load_gpx

EARTH_RADIUS_M = 6371000.0
segments = load_strava_segments('Strava_Segments')
moneline = [s for s in segments if s['name'] == 'MoneLLine'][0]['track']
bepa = [s for s in segments if s['name'] == 'BePa UP TRAIL'][0]['track']

_track_lat = None
_track_lon = None


def _walk_forward_haversine(masked, track_profile, segment_profile,
                            start_track_idx, max_gap, progress_ratio,
                            progress_slack_m, track_lat, track_lon):
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
            d_track = float(np.sqrt(
                (track_lat[track_i] - track_lat[prev_track]) ** 2 +
                (track_lon[track_i] - track_lon[prev_track]) ** 2
            )) * 111000.0  # rough deg->m
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


def _find_occurrences_fixed(cand, track_profile, segment_profile, track,
                            min_match_points=5, start_tol=16, end_tol=16,
                            max_gap=13, progress_ratio=3.0, progress_slack_m=40.0,
                            min_length_m=20.0, min_density=0.4, max_density=2.2,
                            max_total_passes=12):
    n_seg = len(cand)
    n_track = len(track_profile)
    used = np.zeros(n_track, dtype=bool)
    occ = []
    track_lat = track.latitudes
    track_lon = track.longitudes
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
                chain = _walk_forward_haversine(
                    masked, track_profile, profile, s0,
                    max_gap, progress_ratio, progress_slack_m,
                    track_lat, track_lon)
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
                used[t0:t1 + 1] = True  # full-range masking
                found = True
                break
            if not found:
                break
    return occ


def haversine(a, b):
    return math.radians  # placeholder, not used


for track, tname, seg, seg_name in [
    (load_gpx('Examples/strava_full.gpx'), 'strava_full', moneline, 'MoneLLine'),
    (load_gpx('Examples/Pedalata_pomeridiana.gpx'), 'Pedalata', moneline, 'MoneLLine'),
]:
    prof_s, _ = track_distance_profile(seg)
    prof_t, _ = track_distance_profile(track)
    n_seg = len(seg.points)
    end_tol = max(2, int(0.10 * n_seg))
    max_gap = max(6, int(0.08 * n_seg))
    cand = _candidate_track_indices(track, seg, 25.0)
    occ = _find_occurrences_fixed(cand, prof_t, prof_s, track,
        min_match_points=5, start_tol=end_tol, end_tol=end_tol, max_gap=max_gap)
    print(f"\n=== {tname} / {seg_name} (25m, Haversine progress, full-range mask) ===")
    for rv, t0, t1, lm, ch in occ:
        seg0 = ch[0][0]
        ft = track.points[ch[0][1]]
        lt = track.points[ch[-1][1]]
        ts = ((lt.timestamp - ft.timestamp).total_seconds()
              if ft.timestamp and lt.timestamp else None)
        print(f"  t0={t0} t1={t1} seg_start={seg0} chain={len(ch)} "
              f"len={lm:.1f} time={ts}s ({(ts//60):.0f}:{ts%60:02.0f})" if ts else "")

# BePa test
for track, tname in [
    (load_gpx('Examples/strava_full.gpx'), 'strava_full'),
    (load_gpx('Examples/Pedalata_pomeridiana.gpx'), 'Pedalata'),
]:
    prof_bs, _ = track_distance_profile(bepa)
    prof_t, _ = track_distance_profile(track)
    n_seg = len(bepa.points)
    end_tol = max(2, int(0.10 * n_seg))
    max_gap = max(6, int(0.08 * n_seg))
    cand = _candidate_track_indices(track, bepa, 25.0)
    occ = _find_occurrences_fixed(cand, prof_t, prof_bs, track,
        min_match_points=5, start_tol=end_tol, end_tol=end_tol, max_gap=max_gap)
    print(f"\n=== BePa in {tname} (25m, Haversine progress, full-range mask) ===")
    print(f"  {len(occ)} occurrences found")
    for rv, t0, t1, lm, ch in occ:
        ft = track.points[ch[0][1]]
        lt = track.points[ch[-1][1]]
        ts = ((lt.timestamp - ft.timestamp).total_seconds()
              if ft.timestamp and lt.timestamp else None)
        print(f"  t0={t0} t1={t1} seg_start={ch[0][0]} seg_end={ch[-1][0]} "
              f"len={lm:.1f} time={ts}s" if ts else "  (no timestamps)")
