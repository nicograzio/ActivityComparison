"""Modulo per l'importazione di file GPX.

Converte le attività GPX nel modello dati interno ``Track``.
"""

from typing import Dict, Any
import gpxpy
from core.track import Track, TrackPoint


def _get_tag_name(tag: str) -> str:
    """Rimuove eventuale namespace XML e converte in minuscolo."""
    if '}' in tag:
        tag = tag.rsplit('}', 1)[1]
    return tag.lower()


def _extract_extensions(point) -> Dict[str, Any]:
    """Estrae ricorsivamente i dati di estensione da un punto GPX.

    Gestisce i tag per le estensioni GPX:
    - hr / HeartRate
    - cad / Cadence
    - speed
    - atemp / Temperature
    - watemp / WaterTemp
    - depth
    - power
    - course
    """
    ext_data: Dict[str, Any] = {}

    def _traverse(element):
        tag = _get_tag_name(element.tag)
        text = element.text.strip() if element.text else ""

        if text:
            try:
                if tag in ("hr", "heartrate"):
                    ext_data["heart_rate"] = int(float(text))
                elif tag in ("cad", "cadence"):
                    ext_data["cadence"] = int(float(text))
                elif tag == "speed":
                    ext_data["speed"] = float(text)
                elif tag in ("atemp", "temperature"):
                    ext_data["temperature"] = float(text)
                elif tag in ("watemp", "watertemp", "watertemperature"):
                    ext_data["water_temp"] = float(text)
                elif tag == "depth":
                    ext_data["depth"] = float(text)
                elif tag == "power":
                    ext_data["power"] = float(text)
                elif tag == "course":
                    ext_data["course"] = float(text)
            except ValueError:
                pass

        for child in element:
            _traverse(child)

    if point.extensions:
        for ext in point.extensions:
            _traverse(ext)

    return ext_data


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
                ext_data = _extract_extensions(point)

                speed = ext_data.get("speed")
                if speed is None and getattr(point, "speed", None) is not None:
                    speed = point.speed

                points.append(
                    TrackPoint(
                        latitude=point.latitude,
                        longitude=point.longitude,
                        altitude=point.elevation,
                        timestamp=point.time,
                        speed=speed,
                        heart_rate=ext_data.get("heart_rate"),
                        cadence=ext_data.get("cadence"),
                        temperature=ext_data.get("temperature"),
                        water_temp=ext_data.get("water_temp"),
                        depth=ext_data.get("depth"),
                        power=ext_data.get("power"),
                        course=ext_data.get("course"),
                    )
                )

    track.points = points
    track.invalidate_cache()
    return track

