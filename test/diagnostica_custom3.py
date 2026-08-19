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

print("--- ANALISI PASSI 11-07-2026 (MoneLLine) ---")
track_11 = load_gpx("Examples/Pedalata_pomeridiana_11072026.gpx")
mon_seg = [s for s in segs if s["name"] == "MoneLLine"][0]["track"]

# Per ogni punto della traccia tra 2670 e 2830, vediamo le distanze dai vari punti del segmento.
# Vediamo in particolare l'inizio (0) e la fine (-1).
for k in range(2670, 2835):
    p = track_11.points[k]
    d_start = haversine_distance(p, mon_seg.points[0])
    d_end = haversine_distance(p, mon_seg.points[-1])
    print(f"k={k}: coord={p.latitude:.6f},{p.longitude:.6f} dist_start={d_start:.1f} dist_end={d_end:.1f} speed={p.speed*3.6 if p.speed else 0.0:.1f} elevation={p.altitude if p.altitude else 0.0:.1f} @ {p.timestamp}")
