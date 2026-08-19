import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.gpx_loader import load_gpx
from core.strava_analyzer import (
    load_strava_segments,
    find_strava_segments_in_track,
)

segs = load_strava_segments("Strava_Segments")
mon_seg = [s for s in segs if "MoneLLine" in s["name"]][0]
track = load_gpx("Examples/Pedalata_pomeridiana.gpx")

found = find_strava_segments_in_track([mon_seg], track)

for i, res in enumerate(found):
    print(f"Passaggio {i+1}:")
    t0 = res['start_idx']
    t1 = res['end_idx']
    p_start = track.points[t0]
    p_end = track.points[t1]
    print(f"  Start k={t0}: {p_start.latitude}, {p_start.longitude} @ {p_start.timestamp}")
    print(f"  End   k={t1}: {p_end.latitude}, {p_end.longitude} @ {p_end.timestamp}")
    print(f"  Duration: {res['time_sec']:.1f}s")
