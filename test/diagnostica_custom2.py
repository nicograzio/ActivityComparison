import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.gpx_loader import load_gpx
from core.strava_analyzer import (
    load_strava_segments,
    find_strava_segments_in_track,
)

segs = load_strava_segments("Strava_Segments")

print("--- DETAIL FOR 29-07-2026 (Pistino Etilico) ---")
track_29 = load_gpx("Examples/Pedalata_pomeridiana_29072026.gpx")
found_29 = find_strava_segments_in_track(segs, track_29)
for res in found_29:
    if res["segment_name"] == "Pistino Etilico":
        print(f"Start idx: {res['start_idx']}, End idx: {res['end_idx']}, Time: {res['time_sec']:.1f}s, Direction: {res['direction']}")
        # Let's inspect the speeds and timestamps
        t0, t1 = res["start_idx"], res["end_idx"]
        pts = track_29.points[t0:t1+1]
        
        # Are there any long pauses? (e.g. dt > 2s with very little distance?)
        for idx in range(len(pts) - 1):
            p1 = pts[idx]
            p2 = pts[idx+1]
            dt = (p2.timestamp - p1.timestamp).total_seconds()
            if dt > 3.0:
                print(f"      Long GAP at track k={t0+idx}: dt={dt}s")
            
        print(f"    Total track points in range: {len(pts)}")
        print(f"    Start time: {pts[0].timestamp}, End time: {pts[-1].timestamp}")

print("\n--- DETAIL FOR 11-07-2026 (MoneLLine) ---")
track_11 = load_gpx("Examples/Pedalata_pomeridiana_11072026.gpx")
found_11 = find_strava_segments_in_track(segs, track_11)
for res in found_11:
    if res["segment_name"] == "MoneLLine":
        print(f"Start idx: {res['start_idx']}, End idx: {res['end_idx']}, Time: {res['time_sec']:.1f}s, Direction: {res['direction']}")
        t0, t1 = res["start_idx"], res["end_idx"]
        pts = track_11.points[t0:t1+1]
        for idx in range(len(pts) - 1):
            p1 = pts[idx]
            p2 = pts[idx+1]
            dt = (p2.timestamp - p1.timestamp).total_seconds()
            if dt > 3.0:
                print(f"      Long GAP at track k={t0+idx}: dt={dt}s")
        print(f"    Total track points in range: {len(pts)}")
        print(f"    Start time: {pts[0].timestamp}, End time: {pts[-1].timestamp}")
