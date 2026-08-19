import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.gpx_loader import load_gpx
from core.strava_analyzer import (
    load_strava_segments,
    find_strava_segments_in_track,
)

segs = load_strava_segments("Strava_Segments")
track_29 = load_gpx("Examples/Pedalata_pomeridiana_29072026.gpx")

# we want Pistino Etilico
pe_passes = [res for res in find_strava_segments_in_track(segs, track_29) if res["segment_name"] == "Pistino Etilico"]
for idx, pass2 in enumerate(pe_passes):
    print(f"Pistino Etilico passage {idx+1}:")
    print(f"Start: {pass2['start_idx']}, End: {pass2['end_idx']}, Time: {pass2['time_sec']:.1f}s")
    
    import core.strava_analyzer as sa
    track_profile, _ = sa.track_distance_profile(track_29)
    segment_track = [s for s in segs if s["name"] == "Pistino Etilico"][0]["track"]
    segment_profile, _ = sa.track_distance_profile(segment_track)
    candidates = sa._candidate_track_indices(track_29, segment_track, sa.DISTANCE_THRESHOLD_M)
    end_tol = max(2, int(sa.END_TOL_RATIO * len(segment_track.points)))
    max_gap = max(6, int(sa.MAX_GAP_RATIO * len(segment_track.points)))
    
    occs = sa._find_occurrences(
        track_29,
        segment_track,
        candidates,
        track_profile,
        segment_profile,
        min_match_points=sa.MIN_MATCH_POINTS,
        start_tol=end_tol,
        end_tol=end_tol,
        max_gap=max_gap,
    )
    
    for o_idx, occ in enumerate(occs):
        reverse, t0, t1, length_m, avg_dist, chain = occ
        print(f"Occurrence {o_idx+1}: reverse={reverse}, t0={t0}, t1={t1}, len={length_m:.1f}")
        print("First 10 chain elements:")
        for seg_i, trk_i in chain[:10]:
            dist = sa.haversine_distance(segment_track.points[seg_i], track_29.points[trk_i])
            print(f"  seg_i={seg_i}, trk_i={trk_i}, dist={dist:.2f}m")
        print("Last 10 chain elements:")
        for seg_i, trk_i in chain[-10:]:
            dist = sa.haversine_distance(segment_track.points[seg_i], track_29.points[trk_i])
            print(f"  seg_i={seg_i}, trk_i={trk_i}, dist={dist:.2f}m")
            
        # Let's check around start_idx (t0) and end_idx (t1) of the projection
        print("Distanze intorno a t0 (start_idx) della proiezione:")
        for k in range(t0 - 15, t0 + 15):
            if 0 <= k < len(track_29.points):
                d = sa.haversine_distance(track_29.points[k], segment_track.points[0])
                print(f"  k={k}: dist_to_start={d:.2f}m @ {track_29.points[k].timestamp}")
                
        print("Distanze intorno a t1 (end_idx) della proiezione:")
        for k in range(t1 - 15, t1 + 15):
            if 0 <= k < len(track_29.points):
                d = sa.haversine_distance(track_29.points[k], segment_track.points[-1])
                print(f"  k={k}: dist_to_end={d:.2f}m @ {track_29.points[k].timestamp}")
