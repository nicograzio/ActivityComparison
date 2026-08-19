import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.strava_analyzer import load_strava_segments, haversine_distance

segs = load_strava_segments("Strava_Segments")
mon_seg = [s for s in segs if s["name"] == "MoneLLine"][0]["track"]

print(f"MoneLLine segment has {len(mon_seg.points)} points.")
for i in [0, 5, 10, 20, len(mon_seg.points)//2, len(mon_seg.points)-1]:
    p = mon_seg.points[i]
    print(f"  Point {i}: {p.latitude:.6f}, {p.longitude:.6f}")
