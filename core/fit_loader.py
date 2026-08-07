"""Modulo per l'importazione di file FIT.

Converte le attività FIT nel modello dati interno ``Track``.
"""

from fitparse import FitFile
from core.track import Track, TrackPoint


def load_fit(path: str) -> Track:
    """Carica un file FIT e restituisce un'istanza di ``Track``.

    Args:
        path: Percorso del file FIT.

    Returns:
        Track: Istanza popolata con la traccia caricata.
    """
    track = Track(path)
    fit = FitFile(path)
    deg_factor = 180.0 / (2**31)

    points = []
    for record in fit.get_messages("record"):
        # get_values() restituisce direttamente un dizionario senza cicli manuali sui campi
        data = record.get_values()

        latitude = data.get("position_lat")
        longitude = data.get("position_long")

        if latitude is None or longitude is None:
            continue

        latitude *= deg_factor
        longitude *= deg_factor

        altitude = data.get("altitude")
        if altitude is None:
            altitude = data.get("enhanced_altitude")

        point = TrackPoint(
            latitude=latitude,
            longitude=longitude,
            altitude=altitude,
            timestamp=data.get("timestamp"),
            speed=data.get("speed"),
            heart_rate=data.get("heart_rate"),
        )
        points.append(point)

    track.points = points
    track.invalidate_cache()
    return track

