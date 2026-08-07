"""Modulo per l'importazione di file GPX.

Converte le attività GPX nel modello dati interno ``Track``.
"""

import gpxpy
from core.track import Track, TrackPoint


def load_gpx(path: str) -> Track:
    """Carica un file GPX e restituisce un'istanza di ``Track``.

    Args:
        path: Percorso del file GPX.

    Returns:
        Track: Istanza popolata con la traccia caricata.
    """
    track = Track(path)

    with open(path, "r", encoding="utf-8") as file:
        gpx = gpxpy.parse(file)

    points = []
    for gpx_track in gpx.tracks:
        for segment in gpx_track.segments:
            for point in segment.points:
                points.append(
                    TrackPoint(
                        latitude=point.latitude,
                        longitude=point.longitude,
                        altitude=point.elevation,
                        timestamp=point.time,
                    )
                )

    track.points = points
    track.invalidate_cache()
    return track
