"""Agente locale basato su Ollama per l'analisi comparativa delle tracce.

Genera un report in linguaggio naturale confrontando due attività GPS
senza inviare dati a servizi esterni: il modello viene eseguito in locale
tramite ``ollama`` e i dati restano sulla macchina dell'utente.

Consumed by:
    - ``ui.insight_dialog`` (bottone "✨ Analisi IA")

Uses:
    - ``core.analyzer.generate_segment_coach_insights`` per il fallback offline
    - ``core.analyzer.track_distance_profile`` per la distanza totale
"""

import json
import logging
import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import requests

from core.analyzer import (
    generate_segment_coach_insights,
    haversine_distance,
    track_distance_profile,
)
from core.track import activity_display_name

log = logging.getLogger(__name__)

# Base URL ed endpoint dell'API locale di Ollama (vedi https://docs.ollama.com/api)
DEFAULT_BASE_URL = os.environ.get("DUOTRACK_OLLAMA_URL", "http://localhost:11434")
DEFAULT_MODEL = os.environ.get("DUOTRACK_OLLAMA_MODEL", "qwen2.5:0.5b")
OLLAMA_CHAT_ENDPOINT = "/api/chat"
OLLAMA_TAGS_ENDPOINT = "/api/tags"


def resolve_base_url() -> str:
    """Base URL del server Ollama da usare per le richieste.

    Ordine:
        1. base URL del motore embedded (``core.ollama_embedded``) se attivo;
        2. fallback storico: ``DUOTRACK_OLLAMA_URL`` oppure ``localhost:11434``.

    Così l'app funziona sia con il motore avviato automaticamente sia con
    un'installazione Ollama gestita dall'utente.
    """
    try:
        from core.ollama_embedded import default_base_url, get_ollama_manager
    except Exception:  # noqa: BLE001 - mai bloccare l'app per il motore IA
        log.debug("Motore Ollama embedded non disponibile", exc_info=True)
    else:
        try:
            url = get_ollama_manager().base_url
            if url:
                return url.rstrip("/")
        except Exception:  # noqa: BLE001 - mai bloccare l'app per il motore IA
            log.debug("Base URL del motore embedded non ottenibile", exc_info=True)
        return default_base_url()
    return DEFAULT_BASE_URL


# Catalogo dei modelli noti scaricabili: nome, dimensione indicativa del
# download (GGUF Q4) e descrizione mostrata nel dialog di download.
KNOWN_DOWNLOADABLE_MODELS: List[Dict[str, Any]] = [
    {"name": "qwen2.5:0.5b", "size_gb": 0.4,
     "desc": "Leggerissimo: gira ovunque ma produce report poveri e incoerenti; solo per test."},
    {"name": "qwen2.5:3b", "size_gb": 2.0,
     "desc": "Buon compromesso: report coerenti anche su macchine modeste (consigliato)."},
    {"name": "llama3.2:3b", "size_gb": 2.0,
     "desc": "Bilanciato tra qualità del report e risorse richieste."},
    {"name": "phi3:mini", "size_gb": 2.3,
     "desc": "Leggero, con buone capacità di ragionamento."},
    {"name": "mistral:7b", "size_gb": 4.4,
     "desc": "Migliore qualità, richiede circa 8 GB di RAM."},
    {"name": "llama3.1:8b", "size_gb": 4.9,
     "desc": "Ottima qualità, richiede circa 8 GB di RAM."},
    {"name": "gemma2:9b", "size_gb": 5.4,
     "desc": "Ottima qualità, richiede circa 10 GB di RAM."},
]

# Modelli noti mostrati come suggerimenti nella UI (la combo è editabile).
KNOWN_LOCAL_MODELS: List[str] = [m["name"] for m in KNOWN_DOWNLOADABLE_MODELS]

# Formato accettato per i nomi dei modelli Ollama: "nome" oppure "nome:tag",
# con lettere, cifre, punto, underscore e trattino (es. "qwen2.5:0.5b",
# "deepseek-r1:1.5b", "llama3.2" — senza tag viene usato :latest).
MODEL_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9._-]+(:[a-zA-Z0-9._-]+)?$")


def is_valid_model_name(name: str) -> bool:
    """True se ``name`` ha il formato di un modello Ollama valido."""
    if not name:
        return False
    return bool(MODEL_NAME_PATTERN.match(name.strip()))


# Nomi popolari suggeriti dall'autocompletamento durante la digitazione:
# catalogo scaricabile + altre taglie/famiglie note su ollama.com.
KNOWN_MODEL_SUGGESTIONS: List[str] = [
    "qwen2.5:0.5b",
    "qwen2.5:1.5b",
    "qwen2.5:3b",
    "qwen2.5:7b",
    "qwen2.5:14b",
    "llama3.2:1b",
    "llama3.2:3b",
    "llama3.1:8b",
    "gemma2:2b",
    "gemma2:9b",
    "mistral:7b",
    "phi3:mini",
    "phi4:mini",
    "deepseek-r1:1.5b",
    "deepseek-r1:7b",
    "deepseek-r1:8b",
]


def get_model_suggestions(installed: Optional[List[str]] = None) -> List[str]:
    """Suggerimenti per l'autocompletamento dei nomi modello.

    Unione senza duplicati (e senza nomi malformati) di: catalogo
    scaricabile, lista statica di suggerimenti popolari e modelli
    realmente installati passati dall'utente.

    Args:
        installed: nomi dei modelli installati sul motore, se noti.
    """
    candidates: List[str] = [m["name"] for m in KNOWN_DOWNLOADABLE_MODELS]
    candidates += KNOWN_MODEL_SUGGESTIONS
    candidates += list(installed or [])
    seen = set()
    ordered: List[str] = []
    for name in candidates:
        if name and name not in seen and is_valid_model_name(name):
            seen.add(name)
            ordered.append(name)
    return ordered

# Differenze di velocità entro questa soglia (%) si considerano parità.
PARITY_TOLERANCE_PCT = 1.0

# Guadagni/perdite di tempo entro questa soglia (s) si considerano equilibrio.
PARITY_TIME_TOL_S = 0.2

SYSTEM_PROMPT = """Sei un coach ciclistico esperto e analista di dati sportivi.
Confronti due attività GPS sui loro SEGMENTI COMUNI e scrivi un report in LINGUA ITALIANA.

REGOLE FONDAMENTALI:
1. L'analisi riguarda SOLO i segmenti elencati in "segmenti_comuni" del JSON: per ognuno confronta
   tempo_a_s con tempo_b_s, vel_a_kmh con vel_b_kmh e, se presenti, fc_media_a con fc_media_b.
2. Usa i delta già calcolati ("delta_tempo_s", "delta_vel_kmh", "delta_vel_pct") e i campi
   "vincitore" ed "esito": NON ricalcolare nulla e NON inventare numeri.
3. I dati in "contesto_tracce" (l'intera uscita: distanza, durata totale, dislivello, meteo) servono
   SOLO come cornice: non usarli mai per decretare un vincitore e non presentarli come dati di segmento.
4. Se "segmenti_comuni" è vuoto, dichiaralo e fermati: non inventare segmenti né consigli.
5. Analizza ESATTAMENTE i segmenti elencati nel messaggio utente: non inventare, non anticipare e
   non menzionare MAI segmenti che non compaiono in quell'elenco. Se l'elenco contiene un solo
   segmento, il report ha una sola voce di confronto.
6. Usa i nomi reali delle attività: "{name_a}" è l'attività A (campi *_a), "{name_b}" è l'attività B (campi *_b).

Significato dei campi: tempo_*_s = tempo sul segmento in secondi; vel_*_kmh = velocità media in km/h;
fc_media_* = frequenza cardiaca media in bpm; pendenza_*_pct = pendenza media in PERCENTO;
dislivello_positivo_m = metri di salita; lunghezza_m = lunghezza del tratto in metri;
km_a / km_b = chilometraggio di inizio del tratto lungo ciascuna traccia.

Struttura del report in Markdown (3 sezioni, nessuna prefazione né riassunto finale):
1. **Confronto per segmento** - per ogni segmento, una riga di riepilogo (chi è stato più veloce,
   tempo, differenza in secondi e percentuale, eventuale FC e pendenza) seguita da un sotto-punto
   "**Andamento**" che descrive i tratti di guadagno/perdita di tempo usando SOLO i dati del campo
   "andamento" (es. "da km 0,0 a 0,4 l'attività A guadagna 2,1 s"), e una breve lettura tecnica
   collegata a pendenza o FC. Se "andamento" è null, ometti il sotto-punto.
2. **Sintesi** - 2-3 frasi: su quali tratti ha vinto l'attività A, su quali la B, quale tendenza emerge.
3. **Consigli pratici** - 2-4 suggerimenti concreti e misurabili, derivati SOLO dai dati dei segmenti
   (puoi riferirti al punto del tratto dove si è perso tempo).

Tono: incoraggiante, professionale, concreto."""


class OllamaUnavailableError(RuntimeError):
    """Ollama non è raggiungibile o non ha risposto come previsto."""


def _rounded(value: Optional[float]) -> Optional[float]:
    """Arrotonda un valore a 2 decimali mantenendo None."""
    if value is None:
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return value


def _weather_to_dict(weather) -> Optional[Dict[str, Any]]:
    """Serializza un oggetto WeatherInfo in un dict JSON-safe."""
    if weather is None:
        return None
    return {
        "condizione": weather.condition,
        "temperatura_c": _rounded(weather.temperature),
        "vento": _rounded(weather.wind_speed),
        "umidita": weather.humidity,
        "fonte": weather.source,
    }


def _isoformat(timestamp: Any) -> Optional[str]:
    """Converte un timestamp in stringa ISO-8601 quando possibile."""
    if timestamp is None:
        return None
    if hasattr(timestamp, "isoformat"):
        try:
            return timestamp.isoformat()
        except Exception:  # noqa: BLE001
            return str(timestamp)
    return str(timestamp)


def _format_duration(seconds: Optional[float]) -> Optional[str]:
    """Formatta una durata in secondi come stringa leggibile (H:MM:SS o M:SS)."""
    if seconds is None:
        return None
    try:
        total = int(round(float(seconds)))
    except (TypeError, ValueError):
        return None
    hours, remainder = divmod(max(total, 0), 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _split_count(length_m: Optional[float]) -> int:
    """Numero di frazioni in cui dividere un segmento per l'analisi d'andamento."""
    if length_m is None:
        return 4
    if length_m >= 3000:
        return 8
    if length_m >= 1000:
        return 6
    return 4


def _side_split_times(
    track, seg: Dict[str, Any], side: str, n_splits: int
) -> Optional[List[float]]:
    """Tempi cumulativi (secondi) ai confini delle frazioni del segmento.

    Usa l'intervallo di indici della traccia (``a_start_idx``/``a_end_idx`` o
    l'equivalente per B) quando disponibile, altrimenti i punti ricampionati
    del segmento. Restituisce una lista di ``n_splits + 1`` tempi (indice 0 =
    partenza, ultimo = arrivo) oppure ``None`` se i timestamp non bastano.
    """
    if track is None:
        return None
    side_lower = side.lower()
    start_idx = seg.get(f"{side_lower}_start_idx")
    end_idx = seg.get(f"{side_lower}_end_idx")
    points = getattr(track, "points", None) or []
    if start_idx is not None and end_idx is not None:
        points = points[int(start_idx):int(end_idx) + 1]
    else:
        points = list(seg.get(f"resampled_points_{side_lower}") or [])
    if len(points) < 2:
        return None

    stamps = [p.timestamp for p in points]
    if stamps[0] is None or stamps[-1] is None:
        return None
    try:
        t0 = stamps[0]
        last_t = 0.0
        elapsed: List[float] = []
        for ts in stamps:
            if ts is not None:
                last_t = (ts - t0).total_seconds()
            elapsed.append(last_t)
    except TypeError:
        return None

    cum = [0.0]
    for prev, cur in zip(points, points[1:]):
        cum.append(cum[-1] + haversine_distance(prev, cur))
    if cum[-1] <= 0:
        return None

    times = [0.0]
    j = 0
    eps = cum[-1] * 1e-9 + 1e-9  # tolleranza: confini esatti non devono slittare
    for k in range(1, n_splits + 1):
        target = cum[-1] * k / n_splits
        while j < len(cum) and cum[j] < target - eps:
            j += 1
        if j >= len(cum):
            j = len(cum) - 1
        times.append(elapsed[j])
    return times


def _segment_progress(
    track_a, track_b, seg: Dict[str, Any], n_splits: int
) -> Optional[List[Dict[str, Any]]]:
    """Analisi deterministica di dove si guadagna/perde tempo nel segmento.

    Divide il tratto in ``n_splits`` frazioni di distanza uguali e calcola, per
    ogni frazione, il guadagno di tempo di un'attività sull'altra dai timestamp.
    Le frazioni consecutive con lo stesso leader vengono fuse in tratti.

    Returns:
        Lista di tratti ``{"da_km", "a_km", "chi", "guadagno_s", "frase"}``
        (``guadagno_s`` positivo = l'attività A guadagna tempo su B), o
        ``None`` se i timestamp non sono sufficienti.
    """
    times_a = _side_split_times(track_a, seg, "A", n_splits)
    times_b = _side_split_times(track_b, seg, "B", n_splits)
    if times_a is None or times_b is None:
        return None

    length_m = seg.get("length_m") or 0
    if length_m <= 0:
        return None

    stretches: List[Dict[str, Any]] = []
    for k in range(1, n_splits + 1):
        # Positivo => in questa frazione A impiega meno tempo di B (A guadagna).
        gain = (times_b[k] - times_b[k - 1]) - (times_a[k] - times_a[k - 1])
        chi = "pari" if abs(gain) <= PARITY_TIME_TOL_S else ("A" if gain > 0 else "B")
        km_start = (k - 1) * length_m / n_splits / 1000.0
        km_end = k * length_m / n_splits / 1000.0
        if stretches and stretches[-1]["chi"] == chi:
            stretches[-1]["a_km"] = _rounded(km_end)
            stretches[-1]["guadagno_s"] = _rounded(
                (stretches[-1]["guadagno_s"] or 0.0) + gain
            )
        else:
            stretches.append({
                "da_km": _rounded(km_start),
                "a_km": _rounded(km_end),
                "chi": chi,
                "guadagno_s": _rounded(gain),
            })

    for st in stretches:
        gain = st["guadagno_s"] or 0.0
        where = f"da {st['da_km']} a {st['a_km']} km"
        if st["chi"] == "pari":
            st["frase"] = f"{where}: tratto in equilibrio ({abs(gain)} s di scarto)."
        elif st["chi"] == "A":
            st["frase"] = f"{where}: l'attività A guadagna {abs(gain)} s sull'attività B."
        else:
            st["frase"] = f"{where}: l'attività B guadagna {abs(gain)} s sull'attività A."
    return stretches


def _track_summary(track, name: str) -> Dict[str, Any]:
    """Statistiche compatte e JSON-safe di una singola traccia."""
    summary: Dict[str, Any] = {"nome": name}

    if track is None or not getattr(track, "points", None):
        summary["nota"] = "traccia non disponibile"
        return summary

    _, total_dist = track_distance_profile(track)
    timestamps = [p.timestamp for p in track.points if p.timestamp is not None]
    duration_sec = None
    if len(timestamps) >= 2:
        try:
            duration_sec = (timestamps[-1] - timestamps[0]).total_seconds()
        except Exception:  # noqa: BLE001
            duration_sec = None

    alts = track.altitudes
    alts_valid = alts[~np.isnan(alts)]
    elev_min = float(np.min(alts_valid)) if alts_valid.size else None
    elev_max = float(np.max(alts_valid)) if alts_valid.size else None

    gain_m = 0.0
    if alts_valid.size >= 2:
        diffs = np.diff(alts_valid)
        gain_m = float(np.sum(diffs[diffs > 0]))

    hrs = track.heart_rates
    hrs_valid = hrs[~np.isnan(hrs)]
    avg_hr = float(np.mean(hrs_valid)) if hrs_valid.size else None

    avg_speed_kmh = None
    if duration_sec and total_dist > 0:
        avg_speed_kmh = (total_dist / 1000.0) / (duration_sec / 3600.0)

    summary.update({
        "dist_km": _rounded(total_dist / 1000.0),
        "durata_s": _rounded(duration_sec),
        "vel_media_kmh": _rounded(avg_speed_kmh),
        "fc_media": _rounded(avg_hr),
        "elev_min_m": _rounded(elev_min),
        "elev_max_m": _rounded(elev_max),
        "dislivello_positivo_m": _rounded(gain_m),
        "inizio": _isoformat(timestamps[0]) if timestamps else None,
        "meteo_inizio": _weather_to_dict(getattr(track, "weather_start", None)),
        "meteo_fine": _weather_to_dict(getattr(track, "weather_end", None)),
    })
    return summary

def build_comparison_snapshot(
    track_a,
    track_b,
    segments: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Costruisce lo snapshot JSON-safe e compatto per il report IA.

    Lo snapshot è centrato sui SEGMENTI COMUNI (l'oggetto reale dell'analisi):
    per ogni segmento sono inclusi i delta già calcolati, un vincitore e una
    frase di esito pronta. Le statistiche delle tracce intere sono incluse
    solo come contesto, sotto ``contesto_tracce``, per non fuorviare il modello.

    Args:
        track_a: Prima traccia (attività A).
        track_b: Seconda traccia (attività B).
        segments: Segmenti comuni prodotti da ``core.analyzer.find_common_segments``.

    Returns:
        Dizionario con i segmenti comuni per primi (chiave ``segmenti_comuni``)
        e le statistiche delle tracce intere come contesto, senza coordinate
        o punti ricampionati (inutili per il report testuale).
    """
    # Nomi "puliti" dell'attività (stem del file, senza percorso né estensione):
    # evitano che nel prompt IA finiscano percorsi completi come
    # "/percorso/Pedalata_pomeridiana_20062026.gpx".
    name_a = activity_display_name(track_a) or "Attività A"
    name_b = activity_display_name(track_b) or "Attività B"

    segment_entries: List[Dict[str, Any]] = []
    for seg in segments:
        spd_a = seg.get("avg_speed_a")
        spd_b = seg.get("avg_speed_b")
        time_a = seg.get("time_a_sec")
        time_b = seg.get("time_b_sec")

        delta_t = None
        if time_a is not None and time_b is not None:
            delta_t = _rounded(time_b - time_a)

        delta_v = None
        delta_v_pct = None
        if spd_a is not None and spd_b is not None:
            delta_v = _rounded(spd_b - spd_a)
            if spd_a:
                delta_v_pct = _rounded((spd_b - spd_a) / spd_a * 100.0)

        # Esito pre-calcolato: diff_pct positivo => A più veloce di B.
        diff_pct: Optional[float] = None
        if spd_a and spd_b:
            diff_pct = _rounded((spd_a - spd_b) / spd_b * 100.0)
        elif time_a and time_b:
            diff_pct = _rounded((time_b - time_a) / time_b * 100.0)
        if diff_pct is None:
            vincitore: Optional[str] = None
        elif abs(diff_pct) <= PARITY_TOLERANCE_PCT:
            vincitore = "pari"
        else:
            vincitore = "A" if diff_pct > 0 else "B"

        esito: Optional[str] = None
        if vincitore == "pari":
            esito = "Tratto in parità: le due attività completano il segmento in tempi equivalenti."
        elif vincitore in ("A", "B"):
            # "A"/"B" escono solo dal ramo `else` del calcolo del vincitore,
            # dove diff_pct è stato verificato non-None: qui è sempre un float.
            assert diff_pct is not None
            win_time = _format_duration(time_a if vincitore == "A" else time_b)
            lose_time = _format_duration(time_b if vincitore == "A" else time_a)
            advantage = abs(diff_pct)
            if win_time and lose_time:
                esito = (
                    f"L'attività {vincitore} completa il tratto in {win_time} "
                    f"contro {lose_time}, circa {advantage}% più veloce."
                )
            else:
                esito = (
                    f"L'attività {vincitore} è risultata circa {advantage}% "
                    "più veloce su questo tratto."
                )

        segment_entries.append({
            "id": seg.get("id"),
            "nome": (
                seg.get("id")
                if isinstance(seg.get("id"), str)
                else f"Segmento {seg.get('id')}"
            ),
            "km_a": _rounded((seg.get("a_start_dist_m") or 0) / 1000.0),
            "km_b": _rounded((seg.get("b_start_dist_m") or 0) / 1000.0),
            "lunghezza_m": _rounded(seg.get("length_m")),
            "tempo_a_s": _rounded(time_a),
            "tempo_b_s": _rounded(time_b),
            "tempo_a_leggibile": _format_duration(time_a),
            "tempo_b_leggibile": _format_duration(time_b),
            "delta_tempo_s": delta_t,
            "vel_a_kmh": _rounded(spd_a),
            "vel_b_kmh": _rounded(spd_b),
            "delta_vel_kmh": delta_v,
            "delta_vel_pct": delta_v_pct,
            "vincitore": vincitore,
            "esito": esito,
            "andamento": _segment_progress(
                track_a, track_b, seg, _split_count(seg.get("length_m"))
            ),
            "pendenza_a_pct": _rounded(seg.get("slope_a")),
            "pendenza_b_pct": _rounded(seg.get("slope_b")),
            "fc_media_a": _rounded(seg.get("avg_hr_a")),
            "fc_media_b": _rounded(seg.get("avg_hr_b")),
            "alt_media_a_m": _rounded(seg.get("avg_alt_a")),
            "alt_media_b_m": _rounded(seg.get("avg_alt_b")),
        })

    snapshot: Dict[str, Any] = {
        "tipo": "confronto_segmenti_comuni",
        "versione": 2,
        "segmenti_comuni": segment_entries,
        "contesto_tracce": {
            "nota": (
                "Statistiche dell'intera uscita per ciascuna attività: usale "
                "solo come contesto, NON per il giudizio sui segmenti."
            ),
            "attivita_a": _track_summary(track_a, name_a),
            "attivita_b": _track_summary(track_b, name_b),
        },
    }
    if name_a == name_b:
        snapshot["nota_stessa_traccia"] = (
            f"I campi *_a e *_b provengono dalla stessa traccia (\"{name_a}\"): "
            "rappresentano occorrenze (passaggi) diverse sullo stesso percorso."
        )
    return snapshot


def _build_user_prompt(snapshot: Dict[str, Any], name_a: str, name_b: str) -> str:
    """Serializza lo snapshot nel messaggio utente del prompt."""
    data = dict(snapshot)
    context = data.get("contesto_tracce")
    if isinstance(context, dict):
        context = dict(context)
        for key, display_name in (("attivita_a", name_a), ("attivita_b", name_b)):
            if isinstance(context.get(key), dict):
                context[key] = dict(context[key])
                context[key]["nome_visualizzato"] = display_name
        data["contesto_tracce"] = context
    else:
        # Compatibilità con snapshot legacy (chiavi attivita_a/attivita_b in cima).
        for key, display_name in (("attivita_a", name_a), ("attivita_b", name_b)):
            if isinstance(data.get(key), dict):
                data[key] = dict(data[key])
                data[key]["nome_visualizzato"] = display_name

    body = json.dumps(data, ensure_ascii=False, indent=2, default=str)

    entries = data.get("segmenti_comuni") or []
    if entries:
        labels: List[str] = []
        for i, e in enumerate(entries, start=1):
            nome = e.get("nome") or f"Segmento {e.get('id', i)}"
            labels.append(f"- {nome} ({_rounded(e.get('lunghezza_m')) or '?'} m)")
        listing = "\n".join(labels)
        count = len(entries)
        plural = "i" if count != 1 else "o"
        seg_list = (
            f"I segmenti da analizzare son{plural} ESATTAMENTE quest{'i' if count != 1 else 'o'} "
            f"{count} (non aggiungerne altri):\n{listing}"
        )
    else:
        seg_list = "Non ci sono segmenti comuni da analizzare."

    return (
        f"Confronta i SEGMENTI COMUNI tra \"{name_a}\" (attività A) e \"{name_b}\" (attività B).\n\n"
        f"{seg_list}\n\n"
        "Istruzioni:\n"
        "- Analizza SOLO i segmenti nell'array \"segmenti_comuni\": una voce di report per ciascuno; "
        "non menzionare MAI segmenti che non sono in questo elenco.\n"
        "- Per ogni segmento confronta i campi *_a con i corrispondenti *_b e usa i delta già "
        "calcolati (delta_tempo_s, delta_vel_kmh, delta_vel_pct), i campi \"vincitore\" ed \"esito\": "
        "non ricalcolare e non inventare numeri.\n"
        "- Descrivi dove si guadagna/perde tempo usando SOLO il campo \"andamento\" (tratti con "
        "frasi già pronte); se \"andamento\" è null, ometti quella parte.\n"
        "- Ignora \"contesto_tracce\" per il giudizio: contiene solo le statistiche dell'intera uscita.\n\n"
        f"Dati di confronto:\n\n```json\n{body}\n```\n\n"
        "Ora scrivi il report richiesto dal ruolo di sistema."
    )


def _post_chat(
    messages: List[Dict[str, Any]],
    model: str,
    base_url: str,
    timeout: float,
    tools: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """POST su ``/api/chat`` gestendo errori di rete e HTTP come ``OllamaUnavailableError``.

    Con ``tools`` abilita il tool-calling dell'agente.
    """
    base_url = str(base_url).rstrip("/")
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        # temperature bassa: report fattuale, meno divagazioni; repeat_penalty
        # contrasta la ripetizione ossessiva tipica dei modelli molto piccoli.
        "options": {
            "temperature": 0.1,
            "num_ctx": 8192,
            "num_predict": 1024,
            "repeat_penalty": 1.1,
        },
    }
    if tools:
        payload["tools"] = tools
    url = base_url + OLLAMA_CHAT_ENDPOINT

    try:
        response = requests.post(url, json=payload, timeout=timeout)
    except requests.exceptions.ConnectionError as exc:
        raise OllamaUnavailableError(
            f"Non riesco a contattare Ollama su {base_url}. "
            "Verifica che sia in esecuzione ('ollama serve')."
        ) from exc
    except requests.exceptions.Timeout as exc:
        raise OllamaUnavailableError(
            f"Ollama su {base_url} non ha risposto entro {timeout:.0f} secondi. "
            "Il modello potrebbe essere lento: riprova o usa un modello più piccolo."
        ) from exc

    if response.status_code != 200:
        raise OllamaUnavailableError(
            f"Ollama ha risposto HTTP {response.status_code}: {response.text[:200]}"
        )

    try:
        return response.json()
    except ValueError as exc:
        raise OllamaUnavailableError("Risposta non valida (JSON) da Ollama.") from exc


def generate_ai_comparison(
    snapshot: Dict[str, Any],
    name_a: str = "Attività A",
    name_b: str = "Attività B",
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: float = 1800.0,
) -> str:
    """Invoca Ollama (POST /api/chat) e restituisce il report testuale.

    Args:
        snapshot: Dati di confronto prodotti da ``build_comparison_snapshot``.
        name_a: Nome visualizzato dell'attività A.
        name_b: Nome visualizzato dell'attività B.
        model: Nome del modello locale (default: ``DEFAULT_MODEL``).
        base_url: Base URL del server Ollama.
        timeout: Timeout in secondi per la richiesta HTTP.

    Raises:
        OllamaUnavailableError: se Ollama non è raggiungibile o risponde male.
    """
    base_url = (base_url or resolve_base_url()).rstrip("/")
    model = model or DEFAULT_MODEL

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(name_a=name_a, name_b=name_b)},
        {"role": "user", "content": _build_user_prompt(snapshot, name_a, name_b)},
    ]
    data = _post_chat(messages, model, base_url, timeout)
    content = data.get("message", {}).get("content")
    if not content:
        raise OllamaUnavailableError("Ollama non ha restituito alcun contenuto.")
    return content.strip()


AGENT_SYSTEM_PROMPT = """Sei un agente esperto di analisi dati sportivi (ciclismo e corsa).
Hai a disposizione STRUMENTI (funzioni) per approfondire il confronto tra due attività GPS.
Regole:
- Scrivi sempre in LINGUA ITALIANA.
- Non inventare numeri: usa esclusivamente i dati ottenuti dagli strumenti o forniti nel messaggio utente.
- Gli strumenti restituiscono JSON. Chiamali quando utile e analizzane il contenuto.
- L'oggetto dell'analisi sono i SEGMENTI COMUNI (array "segmenti_comuni"): per ciascuno confronta i
  campi *_a con i corrispondenti *_b e usa i delta già calcolati ("delta_tempo_s", "delta_vel_kmh",
  "delta_vel_pct") e i campi "vincitore" ed "esito", senza ricalcolare nulla.
- Le statistiche in "contesto_tracce" descrivono l'intera uscita: usale solo come contesto, MAI per
  decretare un vincitore o come se fossero dati di segmento.
- Se "segmenti_comuni" è vuoto, dichiaralo e non inventare segmenti né consigli.
- Analizza ESATTAMENTE i segmenti presenti in "segmenti_comuni": non menzionare mai segmenti assenti.
- Per ogni segmento descrivi anche l'andamento usando SOLO il campo "andamento" (tratti in cui un'
  attività guadagna/perde tempo); se "andamento" è null, ometti quella parte.
- Quando hai le informazioni necessarie, scrivi il report finale in Markdown con le sezioni:
  **Confronto per segmenti** (una voce per segmento, con riga di riepilogo e sotto-punto Andamento),
  **Sintesi**, **Consigli pratici**.
- Per evidenziare un segmento sulla mappa dell'app usa lo strumento highlight_segment.
- Termina il lavoro con il solo testo del report, SENZA chiamare altri strumenti.

Le attività si chiamano "{name_a}" (attività A) e "{name_b}" (attività B)."""


def build_agent_tools() -> List[Dict[str, Any]]:
    """Definisce gli strumenti (tool-calling) esposti all'agente, compatibili con Ollama."""
    return [
        {
            "type": "function",
            "function": {
                "name": "get_comparison_snapshot",
                "description": (
                    "Restituisce lo snapshot completo dei dati di confronto delle due attività "
                    "(distanza, durata, velocità, FC, pendenza, meteo) in formato JSON."
                ),
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_segment_detail",
                "description": (
                    "Restituisce statistiche dettagliate di un segmento comune per l'attività "
                    "indicata (side='A' o side='B'): velocità media e massima, FC media e "
                    "massima, dislivello positivo, numero di campioni."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "segment_id": {
                            "type": "integer",
                            "description": "Identificativo del segmento comune, parte da 1.",
                        },
                        "side": {
                            "type": "string",
                            "enum": ["A", "B"],
                            "description": "Attività di riferimento (A o B).",
                        },
                    },
                    "required": ["segment_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "highlight_segment",
                "description": (
                    "Evidenzia il segmento comune sull'app e porta il pannello dell'attività "
                    "indicata sul tratto visibile corrispondente."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "segment_id": {
                            "type": "integer",
                            "description": "Identificativo del segmento comune, parte da 1.",
                        },
                        "side": {
                            "type": "string",
                            "enum": ["A", "B"],
                            "description": "Pannello da spostare sul segmento.",
                        },
                    },
                    "required": ["segment_id"],
                },
            },
        },
    ]


def _segment_detail(seg: Dict[str, Any], side: str) -> Dict[str, Any]:
    """Statistiche puntuali di un segmento per l'attività scelta (A o B)."""
    pts_key = "resampled_points_a" if side == "A" else "resampled_points_b"
    start_key = "a_start_dist_m" if side == "A" else "b_start_dist_m"
    end_key = "a_end_dist_m" if side == "A" else "b_end_dist_m"
    time_key = "time_a_sec" if side == "A" else "time_b_sec"
    spd_key = "avg_speed_a" if side == "A" else "avg_speed_b"
    hr_key = "avg_hr_a" if side == "A" else "avg_hr_b"

    points = seg.get(pts_key) or []
    speeds = [p.speed * 3.6 for p in points if getattr(p, "speed", None) is not None]
    hrs = [p.heart_rate for p in points if getattr(p, "heart_rate", None) is not None]
    alts = [p.altitude for p in points if getattr(p, "altitude", None) is not None]

    gain = 0.0
    for i in range(1, len(alts)):
        diff = alts[i] - alts[i - 1]
        if diff > 0:
            gain += diff

    start_m = seg.get(start_key)
    end_m = seg.get(end_key)
    return {
        "id": seg.get("id"),
        "side": side,
        "distanza_m": _rounded(seg.get("length_m")),
        "da_km": _rounded(start_m / 1000.0) if start_m is not None else None,
        "a_km": _rounded(end_m / 1000.0) if end_m is not None else None,
        "tempo_s": _rounded(seg.get(time_key)),
        "vel_media_kmh": _rounded(seg.get(spd_key)),
        "vel_max_kmh": _rounded(max(speeds)) if speeds else None,
        "fc_media": _rounded(seg.get(hr_key)),
        "fc_max": _rounded(max(hrs)) if hrs else None,
        "dislivello_positivo_m": _rounded(gain),
        "n_campioni": len(points),
    }


MAX_AGENT_ITERATIONS = 6


def _build_agent_user_prompt(snapshot: Dict[str, Any], name_a: str, name_b: str) -> str:
    """Prompt utente per l'agente: contesto iniziale + invito a usare gli strumenti."""
    data = dict(snapshot)
    context = data.get("contesto_tracce")
    if isinstance(context, dict):
        context = dict(context)
        for key, display_name in (("attivita_a", name_a), ("attivita_b", name_b)):
            if isinstance(context.get(key), dict):
                context[key] = dict(context[key])
                context[key]["nome_visualizzato"] = display_name
        data["contesto_tracce"] = context
    else:
        # Compatibilità con snapshot legacy (chiavi attivita_a/attivita_b in cima).
        for key, display_name in (("attivita_a", name_a), ("attivita_b", name_b)):
            if isinstance(data.get(key), dict):
                data[key] = dict(data[key])
                data[key]["nome_visualizzato"] = display_name
    body = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    return (
        f"Confronta i SEGMENTI COMUNI tra \"{name_a}\" (attività A) e \"{name_b}\" (attività B).\n"
        "Hai a disposizione degli strumenti per approfondire i dati dei segmenti.\n"
        "I dati iniziali di confronto hanno i segmenti comuni in \"segmenti_comuni\"; "
        "\"contesto_tracce\" contiene solo le statistiche dell'intera uscita (non usare per il giudizio):\n"
        f"```json\n{body}\n```"
    )


def run_agentic_comparison(
    snapshot: Dict[str, Any],
    name_a: str,
    name_b: str,
    segments: List[Dict[str, Any]],
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: float = 1800.0,
    on_highlight: Optional[Callable[[int, str], None]] = None,
    max_iterations: int = MAX_AGENT_ITERATIONS,
) -> Tuple[str, List[Dict[str, Any]]]:
    """Esegue il loop agentico di tool-calling e restituisce il report finale.

    Il modello chiama gli strumenti definiti da ``build_agent_tools``; i
    risultati vengono rimandati come messaggi ``role=tool`` fino a quando
    l'assistente non produce una risposta finale senza chiamate.

    Returns:
        (report_finale, transcript) dove transcript è l'elenco delle chiamate
        effettuate (nome, argomenti, risultato) per trasparenza.
    """
    base_url = (base_url or resolve_base_url()).rstrip("/")
    model = model or DEFAULT_MODEL

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": AGENT_SYSTEM_PROMPT.format(name_a=name_a, name_b=name_b)},
        {"role": "user", "content": _build_agent_user_prompt(snapshot, name_a, name_b)},
    ]
    tools = build_agent_tools()
    transcript: List[Dict[str, Any]] = []

    def _dispatch(tool_name: str, args: Dict[str, Any]) -> Any:
        if tool_name == "get_comparison_snapshot":
            return snapshot
        if tool_name == "get_segment_detail":
            seg_id = int(args.get("segment_id", 0))
            side = str(args.get("side", "A")).upper()
            if seg_id < 1 or seg_id > len(segments):
                return {"error": f"segment_id {seg_id} fuori intervallo (1..{len(segments)})"}
            return _segment_detail(segments[seg_id - 1], side)
        if tool_name == "highlight_segment":
            seg_id = int(args.get("segment_id", 0))
            side = str(args.get("side", "A")).upper()
            if on_highlight is not None:
                try:
                    on_highlight(seg_id, side)
                except Exception as exc:  # noqa: BLE001
                    return {"error": f"highlight fallito: {exc}"}
            return {"status": "ok", "segment_id": seg_id, "side": side}
        return {"error": f"strumento sconosciuto: {tool_name}"}

    last_assistant_content = ""
    for _ in range(max_iterations):
        data = _post_chat(messages, model, base_url, timeout, tools=tools)
        message = data.get("message", {}) or {}
        tool_calls = message.get("tool_calls") or []
        content = message.get("content") or ""
        last_assistant_content = content

        if not tool_calls:
            return content.strip(), transcript

        # Aggiunge il messaggio dell'assistente con le chiamate richieste.
        messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})

        for call in tool_calls:
            fn = call.get("function", {}) if isinstance(call, dict) else {}
            tool_name = fn.get("name", "")
            raw_args = fn.get("arguments", {})
            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args)
                except ValueError:
                    args = {}
            elif isinstance(raw_args, dict):
                args = dict(raw_args)
            else:
                args = {}
            result: Any = None
            try:
                result = _dispatch(tool_name, args)
                result_text = json.dumps(result, ensure_ascii=False, default=str)
            except Exception as exc:  # noqa: BLE001
                result = {"error": str(exc)}
                result_text = json.dumps(result, ensure_ascii=False)

            transcript.append({"tool": tool_name, "args": args, "result": result})
            messages.append({"role": "tool", "content": result_text})

    # Limite di iterazioni raggiunto: restituisce l'ultimo contenuto utile.
    return last_assistant_content.strip(), transcript


def check_ollama(base_url: Optional[str] = None, timeout: float = 3.0) -> bool:
    """Verifica rapidamente che il server Ollama sia raggiungibile."""
    base_url = (base_url or resolve_base_url()).rstrip("/")
    try:
        response = requests.get(base_url + OLLAMA_TAGS_ENDPOINT, timeout=timeout)
        return response.status_code == 200
    except requests.exceptions.RequestException:
        return False


def list_local_models(base_url: Optional[str] = None, timeout: float = 5.0) -> List[str]:
    """Elenco dei modelli locali installati (GET /api/tags)."""
    base_url = (base_url or resolve_base_url()).rstrip("/")
    try:
        response = requests.get(base_url + OLLAMA_TAGS_ENDPOINT, timeout=timeout)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as exc:
        raise OllamaUnavailableError(
            f"Non riesco a contattare Ollama su {base_url} per elencare i modelli."
        ) from exc
    return [model.get("name") for model in data.get("models", []) if model.get("name")]


def list_local_models_info(
    base_url: Optional[str] = None, timeout: float = 5.0
) -> List[Dict[str, Any]]:
    """Informazioni ricche dei modelli locali installati (GET /api/tags).

    Ogni elemento contiene ``name``, ``size_bytes`` (dimensione su disco),
    ``parameter_size`` e ``quantization``.

    Raises:
        OllamaUnavailableError: se il server non è raggiungibile o risponde male.
    """
    base_url = (base_url or resolve_base_url()).rstrip("/")
    try:
        response = requests.get(base_url + OLLAMA_TAGS_ENDPOINT, timeout=timeout)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as exc:
        raise OllamaUnavailableError(
            f"Non riesco a contattare Ollama su {base_url} per elencare i modelli."
        ) from exc
    info: List[Dict[str, Any]] = []
    for model in data.get("models", []):
        if not model.get("name"):
            continue
        details = model.get("details") or {}
        info.append({
            "name": model.get("name"),
            "size_bytes": model.get("size"),
            "parameter_size": details.get("parameter_size"),
            "quantization": details.get("quantization_level"),
        })
    return info


_TAG_RE = re.compile(r"<[^>]+>")


def generate_offline_fallback_report(
    segments: List[Dict[str, Any]],
    name_a: str = "Attività A",
    name_b: str = "Attività B",
    error_msg: str = "",
) -> str:
    """Fallback deterministico (senza LLM) usando ``generate_segment_coach_insights``.

    Converte gli insights HTML del coach in testo semplice Markdown-friendly,
    così il dialogo resta leggibile anche quando Ollama non è disponibile.
    """
    insights = generate_segment_coach_insights(segments, name_a, name_b)

    parts: List[str] = []
    if error_msg:
        parts.append(f"⚠️ Ollama non disponibile ({error_msg}).")
        parts.append("Analisi offline del Coach basata su regole deterministiche:")

    plain: List[str] = []
    for item in insights:
        text = item.replace("<br>", "\n")
        text = _TAG_RE.sub("", text)
        plain.append(text)
    parts.append("\n\n".join(plain))
    return "\n\n".join(parts)