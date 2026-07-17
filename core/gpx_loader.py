import gpxpy

from core.track import Track, TrackPoint


def load_gpx(path):
    track = Track(path)

    with open(path, "r", encoding="utf-8") as file:
        gpx = gpxpy.parse(file)

    for gpx_track in gpx.tracks:
        for segment in gpx_track.segments:
            for point in segment.points:
                track.add_point(
                    TrackPoint(
                        latitude=point.latitude,
                        longitude=point.longitude,
                        altitude=point.elevation,
                        timestamp=point.time,
                    )
                )

    return track
