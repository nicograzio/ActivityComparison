from core.strava_analyzer import load_strava_segments, find_strava_segments_in_track
from core.track import Track
from core.gpx_loader import load_gpx

# Load segments
segments = load_strava_segments('Strava_Segments')
print(f'Loaded {len(segments)} segments')
for s in segments:
    print(f'  Segment: {s["name"]}, points: {len(s["track"].points)}')

# Load tracks
track1 = load_gpx('Examples/strava_full.gpx')
track2 = load_gpx('Examples/Pedalata_pomeridiana.gpx')

print(f'\nTrack 1 (strava_full.gpx): {len(track1.points)} points')
print(f'Track 2 (Pedalata_pomeridiana.gpx): {len(track2.points)} points')

# Check timestamps
print(f'\nTrack 1 first timestamp: {track1.points[0].timestamp}')
print(f'Track 1 last timestamp: {track1.points[-1].timestamp}')
print(f'Track 2 first timestamp: {track2.points[0].timestamp}')
print(f'Track 2 last timestamp: {track2.points[-1].timestamp}')

# Find MoneLLine segment in both tracks
print('\n--- Finding MoneLLine in strava_full.gpx ---')
occurrences = find_strava_segments_in_track(segments, track1)
for occ in occurrences:
    if occ['segment_name'] == 'MoneLLine':
        print(f'Found: start_idx={occ["start_idx"]}, end_idx={occ["end_idx"]}')
        print(f'  time_sec: {occ["time_sec"]}')
        print(f'  length_m: {occ["length_m"]}')
        print(f'  avg_speed: {occ["avg_speed"]}')

print('\n--- Finding MoneLLine in Pedalata_pomeridiana.gpx ---')
occurrences = find_strava_segments_in_track(segments, track2)
for occ in occurrences:
    if occ['segment_name'] == 'MoneLLine':
        print(f'Found: start_idx={occ["start_idx"]}, end_idx={occ["end_idx"]}')
        print(f'  time_sec: {occ["time_sec"]}')
        print(f'  length_m: {occ["length_m"]}')
        print(f'  avg_speed: {occ["avg_speed"]}')

# Find BePa UP TRAIL segment
be_pa_seg = [s for s in segments if 'BePa' in s['name']]
print(f'\nBePa segments: {be_pa_seg}')
if be_pa_seg:
    print(f'Finding BePa UP TRAIL in strava_full.gpx...')
    occ = find_strava_segments_in_track(be_pa_seg, track1)
    for o in occ:
        print(f'  Found: segment={o["segment_name"]}, time_sec={o["time_sec"]}')
    
    print(f'Finding BePa UP TRAIL in Pedalata_pomeridiana.gpx...')
    occ = find_strava_segments_in_track(be_pa_seg, track2)
    for o in occ:
        print(f'  Found: segment={o["segment_name"]}, time_sec={o["time_sec"]}')