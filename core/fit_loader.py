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

    Legge tutti i possibili dati e metadati contenuti nel file FIT
    (punti traccia con tutte le metriche, sessioni, lap, device_info, meteo, ecc.).

    Args:
        path: Percorso del file FIT.

    Returns:
        Track: Istanza popolata con la traccia caricata e i relativi dati.
    """
    track = Track(path)
    fit = FitFile(path)
    deg_factor = 180.0 / (2**31)

    known_point_keys = {
        "position_lat", "position_long",
        "altitude", "enhanced_altitude",
        "speed", "enhanced_speed",
        "timestamp",
        "heart_rate",
        "cadence", "fractional_cadence",
        "temperature",
        "water_temp", "water_temperature",
        "depth",
        "power",
        "course", "heading",
        "distance",
        "calories",
        "grade", "slope",
        "gps_accuracy", "pos_accuracy",
    }

    points = []
    weather_records = []

    for msg in fit.messages:
        msg_name = msg.name
        data = msg.get_values()

        # Memorizzazione di tutte le tipologie di messaggio nei metadati della traccia
        if msg_name not in track.fit_metadata:
            track.fit_metadata[msg_name] = []
        track.fit_metadata[msg_name].append(data)

        # Assegnazione ai campi dedicati della traccia
        if msg_name == "session":
            track.sessions.append(data)
        elif msg_name == "lap":
            track.laps.append(data)
        elif msg_name == "device_info":
            track.device_infos.append(data)
        elif msg_name == "event":
            track.events.append(data)
        elif msg_name == "file_id" and track.file_id is None:
            track.file_id = data
        elif msg_name == "weather":
            weather_records.append(data)
        elif msg_name == "record":
            latitude = data.get("position_lat")
            longitude = data.get("position_long")

            if latitude is None or longitude is None:
                continue

            latitude *= deg_factor
            longitude *= deg_factor

            altitude = data.get("altitude")
            if altitude is None:
                altitude = data.get("enhanced_altitude")

            speed = data.get("speed")
            if speed is None:
                speed = data.get("enhanced_speed")

            cadence = data.get("cadence")
            frac_cadence = data.get("fractional_cadence")
            if cadence is not None and frac_cadence is not None:
                cadence = cadence + frac_cadence

            water_temp = data.get("water_temp")
            if water_temp is None:
                water_temp = data.get("water_temperature")

            course = data.get("course")
            if course is None:
                course = data.get("heading")

            grade = data.get("grade")
            if grade is None:
                grade = data.get("slope")

            gps_acc = data.get("gps_accuracy")
            if gps_acc is None:
                gps_acc = data.get("pos_accuracy")

            extra_data = {k: v for k, v in data.items() if k not in known_point_keys}

            point = TrackPoint(
                latitude=latitude,
                longitude=longitude,
                altitude=altitude,
                timestamp=data.get("timestamp"),
                speed=speed,
                heart_rate=data.get("heart_rate"),
                cadence=cadence,
                temperature=data.get("temperature"),
                water_temp=water_temp,
                depth=data.get("depth"),
                power=data.get("power"),
                course=course,
                distance=data.get("distance"),
                calories=data.get("calories"),
                grade=grade,
                gps_accuracy=gps_acc,
                extra_data=extra_data if extra_data else None,
            )
            points.append(point)

    track.points = points
    track.invalidate_cache()

    # Estrazione delle informazioni meteo dal file FIT.
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
            weather_start = untimed[0]
            weather_end = untimed[0]
        else:
            weather_start = next(iter(weather_by_time.values()))
            weather_end = weather_start

    track.weather_start = weather_start
    track.weather_end = weather_end

    return track

