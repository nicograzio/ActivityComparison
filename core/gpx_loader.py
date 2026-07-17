import gpxpy

from core.track import Track, TrackPoint

DEBUG_IMPORT = True


def load_gpx(path):
    track = Track(path)

    with open(path, "r", encoding="utf-8") as file:
        gpx = gpxpy.parse(file)

    index = 0

    for gpx_track in gpx.tracks:
        for segment in gpx_track.segments:
            for point in segment.points:
                if DEBUG_IMPORT and index < 10:
                    print("=== GPX RAW POINT", index, "===")
                    print(vars(point))

                track_point = TrackPoint(
                    latitude=point.latitude,
                    longitude=point.longitude,
                    altitude=point.elevation,
                    timestamp=point.time,
                )

                if DEBUG_IMPORT and index < 10:
                    print("=== TRACK POINT", index, "===")
                    print(vars(track_point))

                track.add_point(track_point)
                index += 1

    print("GPX points loaded:", len(track.points))

    return track
