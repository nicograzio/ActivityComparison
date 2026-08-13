"""Analisi dettagliata del match del segmento MoneLLine in entrambe le tracce.

Verifica i tempi calcolati dall'app e li confronta con le aspettative di Strava,
evidenziando scostamenti (soprattutto su pedalata_pomeridiana.gpx).
"""
from core.strava_analyzer import (
    load_strava_segments,
    find_strava_segments_in_track,
    _candidate_track_indices,
    _find_occurrences,
)
from core.gpx_loader import load_gpx
from core.analyzer import track_distance_profile

# Load segments
segments = load_strava_segments('Strava_Segments')
mon_line_seg = [s for s in segments if s['name'] == 'MoneLLine'][0]
track = mon_line_seg["track"]

print(f"MoneLLine segment:")
print(f"  Points: {len(track.points)}")
print(f"  First point: lat={track.points[0].latitude}, lon={track.points[0].longitude}")
print(f"  Last point: lat={track.points[-1].latitude}, lon={track.points[-1].longitude}")
print(f"  First timestamp: {track.points[0].timestamp}")
print(f"  Last timestamp: {track.points[-1].timestamp}")

# Calculate segment length using track_distance_profile
profile, total = track_distance_profile(track)
print(f"\nSegment total distance: {total:.1f} m")
print(f"Segment profile first 5: {profile[:5]}")
print(f"Segment profile last 5: {profile[-5:]}")

mon_profile, _ = track_distance_profile(track)
n_seg = len(track.points)
end_tol = max(2, int(0.10 * n_seg))
max_gap = max(6, int(0.08 * n_seg))


def analyze_track(gpx_path, label):
    """Analizza il match MoneLLine in una traccia e stampa dettagli."""
    trk = load_gpx(gpx_path)
    print(f"\n{'=' * 70}")
    print(f"{label} ({gpx_path}): {len(trk.points)} points")
    print(f"  First point timestamp: {trk.points[0].timestamp}")
    print(f"  Last point timestamp: {trk.points[-1].timestamp}")

    occurrences = find_strava_segments_in_track(segments, trk)
    mon_occs = [o for o in occurrences if o['segment_name'] == 'MoneLLine']

    print(f"\nFound {len(mon_occs)} MoneLLine occurrence(s):")
    for i, occ in enumerate(mon_occs):
        print(f"\n  Occurrence #{i + 1}:")
        print(f"    start_idx: {occ['start_idx']}, end_idx: {occ['end_idx']}")
        print(f"    time_sec: {occ['time_sec']} ({occ['time_sec'] // 60:.0f}:{occ['time_sec'] % 60:02.0f})" if occ['time_sec'] else "    time_sec: None")
        print(f"    length_m: {occ['length_m']:.1f}")
        print(f"    avg_speed: {occ['avg_speed']:.1f} km/h")
        print(f"    n_match_points: {occ['n_match_points']}")
        print(f"    direction: {occ['direction']}")

        if occ['length_m'] > 0 and occ['time_sec'] and occ['time_sec'] > 0:
            expected_speed = (occ['length_m'] / occ['time_sec']) * 3.6
            print(f"    Speed from length/time: {expected_speed:.1f} km/h")

        sub_pts = trk.points[occ['start_idx']:occ['end_idx'] + 1]
        print(f"\n    Matched section timestamps:")
        print(f"    First: {sub_pts[0].timestamp}")
        print(f"    Last: {sub_pts[-1].timestamp}")
        if sub_pts[0].timestamp and sub_pts[-1].timestamp:
            diff = (sub_pts[-1].timestamp - sub_pts[0].timestamp).total_seconds()
            print(f"    Time diff: {diff:.1f} s")

    # Dettaglio catena per capire gli scostamenti
    print(f"\n  Chain detail (threshold=15m, current params):")
    track_profile, _ = track_distance_profile(trk)
    candidates = _candidate_track_indices(trk, track, 15.0)
    occ_list = _find_occurrences(
        candidates, track_profile, mon_profile,
        min_match_points=5, start_tol=end_tol, end_tol=end_tol,
        max_gap=max_gap, progress_ratio=2.5, progress_slack_m=30.0,
    )
    for i, (reverse, t0, t1, length_m, chain) in enumerate(occ_list):
        seg_first = chain[0][0]
        seg_last = chain[-1][0]
        first_tp = trk.points[chain[0][1]]
        last_tp = trk.points[chain[-1][1]]
        ts = ((last_tp.timestamp - first_tp.timestamp).total_seconds()
              if first_tp.timestamp and last_tp.timestamp else None)
        print(f"    Chain #{i + 1}: seg pts {seg_first}..{seg_last} "
              f"(of {n_seg}), track idx {t0}..{t1}, "
              f"matched={len(chain)}, time={ts}s, "
              f"direction={'reverse' if reverse else 'forward'}")


analyze_track('Examples/strava_full.gpx', 'strava_full.gpx')
analyze_track('Examples/Pedalata_pomeridiana.gpx', 'pedalata_pomeridiana.gpx')
