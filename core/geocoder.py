"""Reverse geocoding via Nominatim (OpenStreetMap).

Usato per risalire al nome della località (es. "Casalgrande") a partire dalle
coordinate GPS del punto medio di un segmento Strava.

Consumes:
    - ``requests`` (già presente in ``requirements.txt``)
"""

import threading
from typing import Dict, Optional, Tuple

import requests

_USER_AGENT = (
    "DuoTrack/1.0 "
    "(https://github.com/nicograzio/ActivityComparison; contact: local user)"
)
_NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
_TIMEOUT_S = 10.0

# Cache in memoria: (lat approssimata, lon approssimata) -> località o None.
_cache: Dict[Tuple[float, float], Optional[str]] = {}
_cache_lock = threading.Lock()


def reverse_geocode(lat: float, lon: float) -> Optional[str]:
    """Restituisce il nome della località per il punto (``lat``, ``lon``).

    Interroga Nominatim/OpenStreetMap e memorizza il risultato in una cache
    in memoria per evitare chiamate ripetute per lo stesso punto approssimato.

    Args:
        lat: Latitudine in gradi decimali.
        lon: Longitudine in gradi decimali.

    Returns:
        Nome della località (es. "Casalgrande") oppure ``None`` se la rete
        fallisce oppure la risposta non contiene una località riconoscibile.
    """
    key = (round(lat, 5), round(lon, 5))
    with _cache_lock:
        if key in _cache:
            return _cache[key]

    try:
        resp = requests.get(
            _NOMINATIM_URL,
            params={
                "lat": lat,
                "lon": lon,
                "format": "json",
                "addressdetails": 1,
                "zoom": 14,
            },
            headers={"User-Agent": _USER_AGENT},
            timeout=_TIMEOUT_S,
        )
        resp.raise_for_status()
        data = resp.json()
        address = data.get("address") or {}
        location = (
            address.get("city")
            or address.get("town")
            or address.get("village")
            or address.get("municipality")
            or address.get("suburb")
            or address.get("hamlet")
            or address.get("neighbourhood")
            or address.get("county")
            or address.get("state")
            or address.get("country")
        )
    except Exception:
        location = None

    with _cache_lock:
        _cache[key] = location
    return location