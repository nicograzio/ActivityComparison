"""GPX import helpers.

This module turns a GPX file into the internal ``Track`` model used by the
rest of the application.

Called by:
    - ``ui.track_panel.TrackPanel.import_file``
"""

import gpxpy

from core.track import Track, TrackPoint


def load_gpx(path):
    """Load a GPX file and return a ``Track`` instance.

    Called by:
        - ``TrackPanel.import_file`` when the chosen file has a ``.gpx``
          extension.

    Args:
        path: Path to the GPX file.

    Returns:
        Track: Parsed activity.
    """
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