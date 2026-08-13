"""Verifica il rilevamento del segmento BePa UP TRAIL nelle tracce di esempio.

Usa l'API pubblica find_strava_segments_in_track (soglia default 25m) per
confermare il fix di rilevamento.
"""
from core.strava_analyzer import (
    load_strava_segments,
    find_strava_segments_in_track,
    _candidate_track_indices,
)
from core.gpx_loader import load_gpx
from core.analyzer import haversine_distances_np
import numpy as np

# Load segments
segments = load_strava_segments('Strava_Segments')
be_pa_seg = [s for s in segments if 'BePa' in s['name']][0]
track = be_pa_seg["track"]

print(f"BePa UP TRAIL segment:")
print(f"  Points: {len(track.points)}")
print(f"  First point: lat={track.points[0].latitude}, lon={track.points[0].longitude}")
print(f"  Last point: lat={track.points[-1].latitude}, lon={track.points[-1].longitude}")

# Segment length (adjacent-point distances via haversine_distances_np)
distances = haversine_distances_np(track.latitudes, track.longitudes)
print(f"\nBePa segment length: {distances.sum():.1f} m")

# Load tracks
track1 = load_gpx('Examples/strava_full.gpx')
track2 = load_gpx('Examples/Pedalata_pomeridiana.gpx')

# --- Candidate check at 15m (diagnostic) ---
for trk, tname in [(track1, 'strava_full.gpx'), (track2, 'Pedalata_pomeridiana.gpx')]:
    cand = _candidate_track_indices(trk, track, 15.0)
    n_with_cand = sum(1 for c in cand if len(c) > 0)
    print(f"\nBePa candidates @15m in {tname}: {n_with_cand}/{len(cand)} segment points have matches")

# --- Public API detection with default 25m threshold ---
print("\n=== find_strava_segments_in_track (default 25m threshold) ===")
results1 = find_strava_segments_in_track(segments, track1)
results2 = find_strava_segments_in_track(segments, track2)

for trk_name, results in [('strava_full.gpx', results1), ('Pedalata_pomeridiana.gpx', results2)]:
    bepa_occs = [r for r in results if r['segment_name'] == 'BePa UP TRAIL']
    print(f"\n{trk_name} — BePa UP TRAIL occurrences: {len(bepa_occs)}")
    for occ in bepa_occs:
        ts = occ['time_sec']
        print(f"  time={ts}s({ts//60:.0f}:{ts%60:02.0f})" if ts else "  time=None",
              f"len={occ['length_m']:.1f}m start_idx={occ['start_idx']} end_idx={occ['end_idx']}",
              f"start_dist={occ['start_dist_m']:.1f}m end_dist={occ['end_dist_m']:.1f}m",
              f"n_match={occ['n_match_points']} dir={occ['direction']}")