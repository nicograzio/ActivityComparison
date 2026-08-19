import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.gpx_loader import load_gpx
from core.strava_analyzer import (
    load_strava_segments,
    find_strava_segments_in_track,
    track_distance_profile
)

segs = load_strava_segments("Strava_Segments")
mon_seg = [s for s in segs if "MoneLLine" in s["name"]][0]
track = load_gpx("Examples/Pedalata_pomeridiana.gpx")

found = find_strava_segments_in_track([mon_seg], track)

for i, res in enumerate(found):
    print(f"Passaggio {i+1}:")
    print(f"  Start idx: {res['start_idx']}, End idx: {res['end_idx']}")
    print(f"  Time: {res['time_sec']:.1f}s, Speed: {res['avg_speed']:.1f} kmh")
    print(f"  Start Dist: {res['start_dist_m']:.1f}m, End Dist: {res['end_dist_m']:.1f}m")
    # Vediamo i primi punti della traccia nel match
    t0 = res['start_idx']
    for k in range(t0, t0 + 10):
        if k >= len(track.points): break
        p = track.points[k]
        print(f"    k={k}: {p.latitude}, {p.longitude} @ {p.timestamp}")
