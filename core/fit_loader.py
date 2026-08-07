"""Modulo per l'importazione di file FIT.

Converte le attività FIT nel modello dati interno ``Track``.
"""

from fitparse import FitFile
from core.track import Track, TrackPoint, WeatherInfo


# Mappa dei valori dell'enum FIT WeatherCondition -> descrizione in italiano.
_WEATHER_CONDITIONS = {
    0: "Sereno",
    1: "Parzialmente nuvoloso",
    2: "Prevalentemente nuvoloso",
    3: "Pioggia",
    4: "Neve",
    5: "Ventoso",
    6: "Temporali",
    7: "Miscuglio invernale",
    8: "Nebbia",
    11: "Nebbioso",
    12: "Grandine",
    13: "Rovesci sparsi",
    14: "Temporali sparsi",
    15: "Precipitazione sconosciuta",
    16: "Pioggia leggera",
    17: "Pioggia intensa",
    18: "Neve leggera",
    19: "Neve intensa",
    20: "Pioggia/Neve leggera",
    21: "Pioggia/Neve intensa",
    22: "Nuvoloso",
    23: "Prevalentemente sereno",
    24: "Caldo",
}


def _parse_weather_info(data: dict) -> WeatherInfo:
    """Converte un record meteo FIT in un'istanza di ``WeatherInfo``."""
    condition_code = data.get("weather_condition")
    condition = _WEATHER_CONDITIONS.get(condition_code) if condition_code is not None else None

    return WeatherInfo(
        condition=condition,
        temperature=data.get("temperature"),
        wind_speed=data.get("wind_speed"),
        humidity=data.get("humidity"),
        source="fit",
    )


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

    # Estrazione delle informazioni meteo dal file FIT.
    # I record meteo possono comparire con o senza timestamp; usiamo il timestamp
    # per associarli all'inizio e alla fine dell'attività. Quando manca un
    # timestamp, assumiamo che i meteo si riferiscano all'attività intera e
    # li assegniamo sia all'inizio che alla fine.
    weather_records = []
    try:
        for record in fit.get_messages("weather"):
            data = record.get_values()
            weather_records.append(data)
    except Exception:
        weather_records = []

    weather_start = None
    weather_end = None

    if weather_records:
        weather_by_time = {}
        untimed = []
        has_timestamps = any(rec.get("timestamp") is not None for rec in weather_records)
        for rec in weather_records:
            ts = rec.get("timestamp")
            if ts is not None:
                weather_by_time.setdefault(ts, _parse_weather_info(rec))
            else:
                untimed.append(_parse_weather_info(rec))

        if has_timestamps and weather_by_time:
            start_ts = points[0].timestamp if points else None
            end_ts = points[-1].timestamp if points else None

            def closest(target_ts):
                if target_ts is None:
                    return None
                best = None
                for ts in weather_by_time:
                    if best is None or abs((ts - target_ts).total_seconds()) < abs((best - target_ts).total_seconds()):
                        best = ts
                return weather_by_time.get(best)

            weather_start = closest(start_ts)
            weather_end = closest(end_ts)
        elif untimed:
            # I dati meteo coprono l'intera attività
            weather_start = untimed[0]
            weather_end = untimed[0]
        else:
            weather_start = next(iter(weather_by_time.values()))
            weather_end = weather_start

    track.weather_start = weather_start
    track.weather_end = weather_end

    return track

