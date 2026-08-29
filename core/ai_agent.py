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

from core.analyzer import generate_segment_coach_insights, track_distance_profile

log = logging.getLogger(__name__)

# Base URL ed endpoint dell'API locale di Ollama (vedi https://docs.ollama.com/api)
DEFAULT_BASE_URL = os.environ.get("DUOTRACK_OLLAMA_URL", "http://localhost:11434")
DEFAULT_MODEL = os.environ.get("DUOTRACK_OLLAMA_MODEL", "qwen2.5:0.5b")
OLLAMA_CHAT_ENDPOINT = "/api/chat"
OLLAMA_TAGS_ENDPOINT = "/api/tags"

# Modelli noti mostrati come suggerimenti nella UI (la combo è editabile).
KNOWN_LOCAL_MODELS: List[str] = [
    "qwen2.5:0.5b",
    "llama3.2:3b",
    "llama3.1:8b",
    "mistral:7b",
    "gemma2:9b",
    "phi3:mini",
]

SYSTEM_PROMPT = """Sei un coach ciclistico esperto e analista di dati sportivi.
Confronti due attività GPS effettuate sullo stesso percorso (o con tratti comuni) e scrivi un report in LINGUA ITALIANA.
Usa SOLO i dati forniti: non inventare numeri, tempi o sensazioni.
Struttura il report in Markdown con queste sezioni:
1. **Panoramica** - un paragrafo introduttivo che sintetizza il confronto.
2. **Confronto per segmento** - per ogni segmento comune, una o due frasi con tempo, velocità ed eventuale frequenza cardiaca o pendenza.
3. **Punti di forza** - per ciascuna attività, dove è risultata migliore dell'altra.
4. **Aree di miglioramento** - i punti più deboli di ciascuna attività.
5. **Consigli pratici** - da 2 a 4 suggerimenti allenanti concreti e misurabili.

Tono: incoraggiante, professionale, concreto. Niente prefazioni né riassunti superflui.
Riferisciti alle attività con i loro nomi ("{name_a}" per l'attività A, "{name_b}" per l'attività B)."""


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

    Args:
        track_a: Prima traccia (attività A).
        track_b: Seconda traccia (attività B).
        segments: Segmenti comuni prodotti da ``core.analyzer.find_common_segments``.

    Returns:
        Dizionario con statistiche per traccia e per segmento, senza
        coordinate o punti ricampionati (inutili per il report testuale).
    """
    name_a = getattr(track_a, "name", "Attività A") if track_a else "Attività A"
    name_b = getattr(track_b, "name", "Attività B") if track_b else "Attività B"

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

        segment_entries.append({
            "id": seg.get("id"),
            "km_a": _rounded((seg.get("a_start_dist_m") or 0) / 1000.0),
            "km_b": _rounded((seg.get("b_start_dist_m") or 0) / 1000.0),
            "lunghezza_m": _rounded(seg.get("length_m")),
            "tempo_a_s": _rounded(time_a),
            "tempo_b_s": _rounded(time_b),
            "delta_tempo_s": delta_t,
            "vel_a_kmh": _rounded(spd_a),
            "vel_b_kmh": _rounded(spd_b),
            "delta_vel_kmh": delta_v,
            "delta_vel_pct": delta_v_pct,
            "pendenza_a_pct": _rounded(seg.get("slope_a")),
            "pendenza_b_pct": _rounded(seg.get("slope_b")),
            "fc_media_a": _rounded(seg.get("avg_hr_a")),
            "fc_media_b": _rounded(seg.get("avg_hr_b")),
            "alt_media_a_m": _rounded(seg.get("avg_alt_a")),
            "alt_media_b_m": _rounded(seg.get("avg_alt_b")),
        })

    return {
        "tipo": "confronto_attivita",
        "versione": 1,
        "attivita_a": _track_summary(track_a, name_a),
        "attivita_b": _track_summary(track_b, name_b),
        "segmenti_comuni": segment_entries,
    }


def _build_user_prompt(snapshot: Dict[str, Any], name_a: str, name_b: str) -> str:
    """Serializza lo snapshot nel messaggio utente del prompt."""
    data = dict(snapshot)
    for key, display_name in (("attivita_a", name_a), ("attivita_b", name_b)):
        if isinstance(data.get(key), dict):
            data[key] = dict(data[key])
            data[key]["nome_visualizzato"] = display_name

    body = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    return (
        "Ecco i dati di confronto tra le due attività:\n\n"
        f"```json\n{body}\n```\n\n"
        "Scrivi il report di allenamento richiesto dal ruolo di sistema."
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
        "options": {"temperature": 0.4, "num_ctx": 8192},
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
    base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
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
- Quando hai le informazioni necessarie, scrivi il report finale in Markdown con le sezioni:
  **Panoramica**, **Confronto per segmenti**, **Punti di forza**, **Aree di miglioramento**, **Consigli pratici**.
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
    for key, display_name in (("attivita_a", name_a), ("attivita_b", name_b)):
        if isinstance(data.get(key), dict):
            data[key] = dict(data[key])
            data[key]["nome_visualizzato"] = display_name
    body = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    return (
        f"Confronta le due attività \"{name_a}\" (attività A) e \"{name_b}\" (attività B).\n"
        "Hai a disposizione degli strumenti per approfondire i dati.\n"
        "Dati iniziali di confronto:\n"
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
    base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
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
    base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
    try:
        response = requests.get(base_url + OLLAMA_TAGS_ENDPOINT, timeout=timeout)
        return response.status_code == 200
    except requests.exceptions.RequestException:
        return False


def list_local_models(base_url: Optional[str] = None, timeout: float = 5.0) -> List[str]:
    """Elenco dei modelli locali installati (GET /api/tags)."""
    base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
    try:
        response = requests.get(base_url + OLLAMA_TAGS_ENDPOINT, timeout=timeout)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as exc:
        raise OllamaUnavailableError(
            f"Non riesco a contattare Ollama su {base_url} per elencare i modelli."
        ) from exc
    return [model.get("name") for model in data.get("models", []) if model.get("name")]


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