"""Confronto della solidita' del match BePa UP TRAIL in tutte le Examples."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.gpx_loader import load_gpx
from core.strava_analyzer import load_strava_segments, find_strava_segments_in_track

segs = load_strava_segments("Strava_Segments")
print(f"{'File':<38} {'tempo':>8} {'match_pt':>10} {'len_m':>7} {'dir':<8} {'posizione':>9}")
for name in sorted(Path("Examples").glob("*.gpx")):
    track = load_gpx(str(name))
    res = find_strava_segments_in_track(segs, track)
    for r in res:
        if r["segment_name"] != "BePa UP TRAIL":
            continue
        t = r["time_sec"]
        m, s = int(t // 60), int(round(t % 60)) if t else 0
        print(f"{name.name:<42} {m:>2}:{s:02d}    {r['n_match_points']:>4}/{r['segment_point_count']:<3} "
              f"{r['length_m']:>6.0f} m  {r['direction']:<8} @{r['start_dist_m']/1000:.1f} km")