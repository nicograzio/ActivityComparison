import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.gpx_loader import load_gpx
from core.analyzer import haversine_distance

track = load_gpx("Examples/Pedalata_pomeridiana_11072026.gpx")
p1 = track.points[1734]
p2 = track.points[1735]
dist = haversine_distance(p1, p2)
dt = (p2.timestamp - p1.timestamp).total_seconds()
speed = (dist / dt) * 3.6
print(f"k=1734 to 1735: dist={dist:.2f}m, dt={dt:.1f}s, speed={speed:.2f} kmh")
