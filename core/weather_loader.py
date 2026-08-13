"""Recupero delle condizioni meteo di un'attività passata tramite API esterna.

Quando un file (FIT/GPX) non contiene informazioni meteo, questo modulo interroga
il database meteorologico storico di Open-Meteo (gratuito, senza chiave API)
usando coordinate GPS e timestamp del primo e dell'ultimo punto della traccia.

In caso di errore di rete, dati assenti o date non coperte dal servizio,
restituisce ``None`` senza generare eccezioni: la UI mostrerà l'icona rossa.

Consumes:
    - ``core.track.Track``, ``core.track.WeatherInfo``
"""

import json
from datetime import datetime, timezone
from typing import Optional, Tuple, List
from urllib.parse import urlencode

from core.track import Track, TrackPoint, WeatherInfo



# Descrizione italiana dei WMO Weather Codes (Open-Meteo).
_WMO_CODES = {
    0: "Sereno",
    1: "Prevalentemente sereno",
    2: "Parzialmente nuvoloso",
    3: "Nuvoloso",
    45: "Nebbia",
    48: "Nebbia con brina",
    51: "Pioviggine leggera",
    53: "Pioviggine",
    55: "Pioviggine intensa",
    56: "Pioviggine ghiacciata leggera",
    57: "Pioviggine ghiacciata intensa",
    61: "Pioggia debole",
    63: "Pioggia",
    65: "Pioggia intensa",
    66: "Pioggia ghiacciata leggera",
    67: "Pioggia ghiacciata intensa",
    71: "Neve debole",
    73: "Neve",
    75: "Neve intensa",
    77: "Granelli di neve",
    80: "Rovesci leggeri",
    81: "Rovesci",
    82: "Rovesci violenti",
    85: "Rovesci di neve leggeri",
    86: "Rovesci di neve intensi",
    95: "Temporale",
    96: "Temporale con grandine leggera",
    99: "Temporale con grandine intensa",
}

def as_utc(dt) -> Optional[datetime]:
    """Normalizza un timestamp a un ``datetime`` timezone-aware in UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        # Si assume il timestamp naive come UTC (tipico dei file FIT).
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def pick_datapoints(track: Track) -> Tuple[Optional[TrackPoint], Optional[TrackPoint]]:
    """Restituisce il primo e l'ultimo punto con timestamp e coordinate valide."""
    start_pt = None
    end_pt = None
    for p in track.points:
        if (p.timestamp is not None
                and p.latitude is not None and p.longitude is not None):
            start_pt = p
            break
    for p in reversed(track.points):
        if (p.timestamp is not None
                and p.latitude is not None and p.longitude is not None):
            end_pt = p
            break
    return start_pt, end_pt


def build_weather_url(lat: float, lon: float, dt_utc: datetime) -> str:
    """Costruisce l'URL di Open-Meteo Archive per il dato punto/tempo in UTC."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": dt_utc.date().isoformat(),
        "end_date": dt_utc.date().isoformat(),
        "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m,weathercode",
        "timezone": "UTC",
    }
    return "https://archive-api.open-meteo.com/v1/archive?" + urlencode(params)


def _parse_iso_time(value: str) -> Optional[datetime]:
    """Converte una stringa tempo ISO (es. '2024-05-01T10:00') in datetime UTC."""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _condition_from_code(code) -> Optional[str]:
    if code is None:
        return None
    return _WMO_CODES.get(int(code))


def parse_weather_response(payload, dt_utc: datetime) -> Optional[WeatherInfo]:
    """Interpreta la risposta JSON di Open-Meteo e produce il ``WeatherInfo``.

    Args:
        payload: Body della risposta (str o bytes) in formato JSON.
        dt_utc: Timestamp UTC per selezionare lo slot orario più vicino.

    Returns:
        ``WeatherInfo`` con i dati più vicini all'ora richiesta, oppure ``None``
        se la risposta è malformata o priva di dati.
    """
    try:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        data = json.loads(payload)
        hourly = data.get("hourly", {})
        times = hourly.get("time") or []
        if not times:
            return None

        target = dt_utc.replace(minute=0, second=0, microsecond=0)
        parsed: List[Optional[datetime]] = [_parse_iso_time(t) for t in times]
        best_idx = 0
        best_diff = None
        for i, t in enumerate(parsed):
            if t is None:
                continue
            diff = abs((t - target).total_seconds())
            if best_diff is None or diff < best_diff:
                best_diff = diff
                best_idx = i

        def value(key):
            arr = hourly.get(key)
            if arr is None or best_idx >= len(arr):
                return None
            return arr[best_idx]

        return WeatherInfo(
            condition=_condition_from_code(value("weathercode")),
            temperature=value("temperature_2m"),
            wind_speed=value("wind_speed_10m"),
            humidity=value("relative_humidity_2m"),
            source="open-meteo",
        )
    except Exception:
        return None


def fetch_weather_for_track(track: Track) -> Tuple[Optional[WeatherInfo], Optional[WeatherInfo]]:
    """(Bloccante, per test/uso non-Qt) Recupera il meteo inizio/fine via Open-Meteo.

    Nota: nell'applicazione Qt il recupero avviene in modo asincrono con
    ``QNetworkAccessManager``; questa funzione è mantenuta solo per utili
    test/strumenti a riga di comando.
    """
    import requests

    start_pt, end_pt = pick_datapoints(track)
    weather_start = None
    weather_end = None
    for pt, is_start in ((start_pt, True), (end_pt, False)):
        if pt is None:
            continue
        if is_start and start_pt is end_pt:
            continue
        dt_utc = as_utc(pt.timestamp)
        if dt_utc is None:
            continue
        try:
            url = build_weather_url(pt.latitude, pt.longitude, dt_utc)
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            info = parse_weather_response(resp.text, dt_utc)
        except Exception:
            info = None
        if is_start:
            weather_start = info
        else:
            weather_end = info
    return weather_start, weather_end


