"""Reverse geocoding via Nominatim (OpenStreetMap).

Usato per risalire al nome della località (es. "Casalgrande") a partire dalle
coordinate GPS del punto medio di un segmento Strava.

Consumes:
    - ``requests`` (già presente in ``requirements.txt``)
"""

import logging
import threading
import time
from typing import Dict, Optional, Tuple

import requests
from requests.exceptions import RequestException
from urllib3.exceptions import InsecureRequestWarning

# Il fallback SSL (verify=False) è intenzionale per ambienti con proxy che
# usano certificati self-signed. Sopprimiamo il warning di urllib3.
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

log = logging.getLogger(__name__)

_USER_AGENT = (
    "DuoTrack/1.0 "
    "(https://github.com/nicograzio/ActivityComparison; contact: local user)"
)
_NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
_TIMEOUT_S = 10.0
# Nominatim richiede massimo 1 richiesta al secondo.
_MIN_REQUEST_INTERVAL_S = 1.1

# Cache in memoria: (lat approssimata, lon approssimata) -> località o None.
_cache: Dict[Tuple[float, float], Optional[str]] = {}
_cache_lock = threading.Lock()
# Semaforo per rispettare il rate limit di Nominatim tra thread.
_rate_lock = threading.Lock()
_last_request_time = 0.0


def _wait_for_rate_limit() -> None:
    """Attende il tempo necessario per rispettare il rate limit di Nominatim."""
    global _last_request_time
    with _rate_lock:
        now = time.monotonic()
        elapsed = now - _last_request_time
        if elapsed < _MIN_REQUEST_INTERVAL_S:
            time.sleep(_MIN_REQUEST_INTERVAL_S - elapsed)
        _last_request_time = time.monotonic()


def _format_address(address: dict) -> str:
    """Costruisce una stringa di indirizzo leggibile dall'oggetto address.

    Args:
        address: Dizionario ``address`` restituito da Nominatim.

    Returns:
        Stringa formattata (es. "Via Santa Rizza 22b, Casalgrande (RE), Emilia-Romagna").
    """
    parts: list[str] = []

    # Strada + numero civico
    road = address.get("road")
    if road:
        house = address.get("house_number")
        street = f"{road} {house}".strip() if house else road
        parts.append(street)

    # Località più specifica disponibile
    locality = (
        address.get("suburb")
        or address.get("village")
        or address.get("town")
        or address.get("city")
        or address.get("municipality")
        or address.get("hamlet")
        or address.get("neighbourhood")
    )
    if locality:
        parts.append(locality)

    # Provincia/Contea con codice (es. "Reggio nell'Emilia" -> "RE")
    county = address.get("county")
    if county:
        # Usa il codice ISO ufficiale se disponibile (es. "IT-RE" -> "RE")
        iso = address.get("ISO3166-2-lvl6") or ""
        code = iso.split("-")[-1] if "-" in iso else ""
        if code and len(code) <= 3:
            parts.append(f"{county} ({code})")
        else:
            parts.append(county)

    # Regione
    state = address.get("state")
    if state:
        parts.append(state)

    # Paese (solo se non è già implicito)
    country = address.get("country")
    if country and country not in parts:
        parts.append(country)

    return ", ".join(parts)


def _fetch(lat: float, lon: float) -> Optional[str]:
    """Esegue la richiesta HTTP a Nominatim con retry e fallback SSL.

    Returns:
        Indirizzo formattato oppure ``None`` se la richiesta fallisce.
    """
    params = {
        "lat": lat,
        "lon": lon,
        "format": "json",
        "addressdetails": 1,
        "zoom": 18,
    }
    headers = {"User-Agent": _USER_AGENT}

    # Primo tentativo con verifica SSL attiva.
    for attempt, verify in enumerate((True, False)):
        try:
            _wait_for_rate_limit()
            resp = requests.get(
                _NOMINATIM_URL,
                params=params,
                headers=headers,
                timeout=_TIMEOUT_S,
                verify=verify,
            )
            resp.raise_for_status()
            data = resp.json()
            address = data.get("address") or {}
            formatted = _format_address(address)
            if formatted:
                return formatted
            # Fallback: display_name di Nominatim se disponibile
            display_name = data.get("display_name")
            if display_name:
                return display_name
            return None
        except requests.exceptions.SSLError as exc:
            # Errore di verifica SSL: riprova con verify=False al prossimo giro.
            if verify:
                log.warning(
                    "Verifica SSL fallita per Nominatim (tentativo %d): %s", attempt + 1, exc
                )
                continue
            log.error("Errore SSL persistente verso Nominatim: %s", exc)
            return None
        except RequestException as exc:
            log.error("Errore di rete verso Nominatim: %s", exc)
            return None
        except ValueError as exc:
            log.error("Risposta JSON non valida da Nominatim: %s", exc)
            return None
    return None


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

    location = _fetch(lat, lon)

    with _cache_lock:
        _cache[key] = location
    return location