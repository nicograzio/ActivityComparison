import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.gpx_loader import load_gpx
from core.strava_analyzer import (
    load_strava_segments,
    find_strava_segments_in_track,
    haversine_distance,
)

segs = load_strava_segments("Strava_Segments")

print("--- DIAGNOSTICA 11-07-2026 (MoneLLine) ---")
track_11 = load_gpx("Examples/Pedalata_pomeridiana_11072026.gpx")
found_11 = find_strava_segments_in_track(segs, track_11)
for res in found_11:
    if res["segment_name"] == "MoneLLine":
        print(f"Start idx: {res['start_idx']}, End idx: {res['end_idx']}, Time: {res['time_sec']:.1f}s, Direction: {res['direction']}, Dist: {res['start_dist_m']:.1f} to {res['end_dist_m']:.1f}")
        # let's look at the actual coordinates and distances from the actual segment start / end
        s_track = res["track"]
        # segment start
        mon_seg = [s for s in segs if s["name"] == "MoneLLine"][0]["track"]
        p_start_seg = mon_seg.points[0]
        p_end_seg = mon_seg.points[-1]
        
        # print some points around start_idx
        print("  Around Start:")
        for k in range(res['start_idx'] - 5, res['start_idx'] + 6):
            if 0 <= k < len(s_track.points):
                p = s_track.points[k]
                d_start = haversine_distance(p, p_start_seg)
                print(f"    k={k}: dist_to_start_seg={d_start:.2f}m @ {p.timestamp}")
        
        print("  Around End:")
        for k in range(res['end_idx'] - 5, res['end_idx'] + 6):
            if 0 <= k < len(s_track.points):
                p = s_track.points[k]
                d_end = haversine_distance(p, p_end_seg)
                print(f"    k={k}: dist_to_end_seg={d_end:.2f}m @ {p.timestamp}")

print("\n--- DIAGNOSTICA 29-07-2026 (Pistino Etilico) ---")
track_29 = load_gpx("Examples/Pedalata_pomeridiana_29072026.gpx")
found_29 = find_strava_segments_in_track(segs, track_29)
for res in found_29:
    if res["segment_name"] == "Pistino Etilico":
        print(f"Start idx: {res['start_idx']}, End idx: {res['end_idx']}, Time: {res['time_sec']:.1f}s, Direction: {res['direction']}, Dist: {res['start_dist_m']:.1f} to {res['end_dist_m']:.1f}")
        s_track = res["track"]
        pe_seg = [s for s in segs if s["name"] == "Pistino Etilico"][0]["track"]
        p_start_seg = pe_seg.points[0]
        p_end_seg = pe_seg.points[-1]
        
        print("  Around Start:")
        for k in range(res['start_idx'] - 5, res['start_idx'] + 6):
            if 0 <= k < len(s_track.points):
                p = s_track.points[k]
                d_start = haversine_distance(p, p_start_seg)
                print(f"    k={k}: dist_to_start_seg={d_start:.2f}m @ {p.timestamp}")
        
        print("  Around End:")
        for k in range(res['end_idx'] - 5, res['end_idx'] + 6):
            if 0 <= k < len(s_track.points):
                p = s_track.points[k]
                d_end = haversine_distance(p, p_end_seg)
                print(f"    k={k}: dist_to_end_seg={d_end:.2f}m @ {p.timestamp}")
