import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.gpx_loader import load_gpx
from core.strava_analyzer import (
    load_strava_segments,
    find_strava_segments_in_track,
)

segs = load_strava_segments("Strava_Segments")
track_11 = load_gpx("Examples/Pedalata_pomeridiana_11072026.gpx")
found_11 = find_strava_segments_in_track(segs, track_11)

# we want MoneLLine, the second passage
mon_passes = [res for res in found_11 if res["segment_name"] == "MoneLLine"]
if len(mon_passes) >= 2:
    pass2 = mon_passes[1]
    # let's find the occurrences and their chain
    print("MoneLLine second passage properties:")
    print(f"Start: {pass2['start_idx']}, End: {pass2['end_idx']}")
    
    # Let's print the chain of segment index and track index
    # Wait, the find_strava_segments_in_track function doesn't return chain directly, but we can call _find_occurrences to get it.
    import core.strava_analyzer as sa
    track_profile, _ = sa.track_distance_profile(track_11)
    segment_track = [s for s in segs if s["name"] == "MoneLLine"][0]["track"]
    segment_profile, _ = sa.track_distance_profile(segment_track)
    candidates = sa._candidate_track_indices(track_11, segment_track, sa.DISTANCE_THRESHOLD_M)
    end_tol = max(2, int(sa.END_TOL_RATIO * len(segment_track.points)))
    max_gap = max(6, int(sa.MAX_GAP_RATIO * len(segment_track.points)))
    
    occs = sa._find_occurrences(
        track_11,
        segment_track,
        candidates,
        track_profile,
        segment_profile,
        min_match_points=sa.MIN_MATCH_POINTS,
        start_tol=end_tol,
        end_tol=end_tol,
        max_gap=max_gap,
    )
    
    # print the chains
    for idx, occ in enumerate(occs):
        reverse, t0, t1, length_m, avg_dist, chain = occ
        print(f"\nOccurrence {idx+1}: reverse={reverse}, t0={t0}, t1={t1}, len={length_m:.1f}")
        print("Chain elements (showing last 20):")
        for seg_i, trk_i in chain[-20:]:
            dist = sa.haversine_distance(segment_track.points[seg_i], track_11.points[trk_i])
            print(f"  seg_i={seg_i}, trk_i={trk_i}, dist={dist:.2f}m")
