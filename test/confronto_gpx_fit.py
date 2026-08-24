"""Confronto puntuale tra i punti GPX e FIT con lo stesso timestamp.

Carica i punti del file GPX e i record del file FIT della stessa attività,
li abbina tramite timestamp identico e per ogni coppia riporta le differenze
principali (posizione, quota, battito). Per ogni punto FIT indica inoltre
se in quell'istante è presente un messaggio ``event`` nel file FIT.

Utilizzo:
    python test/confronto_gpx_fit.py [percorso.gpx] [percorso.fit]
    python test/confronto_gpx_fit.py -o report.csv <percorso.gpx> <percorso.fit>

Se i percorsi non sono indicati viene usata la coppia d'esempio
``Examples/Pedalata_serale_19082026.{gpx,fit}``.

Output:
    - Riepilogo in console: coppie abbinate, record rimasti fuori, eventi FIT
      (con indicazione di coincidenza o meno) e statistiche delle differenze.
    - Report CSV con una riga per OGNI record FIT, ordinata cronologicamente:
      i record privi di punto GPX simultaneo compaiono così esattamente nella
      loro posizione tra i punti GPX adiacenti (colonna ``posizione_tra_gpx``
      del tipo ``tra HH:MM:SSZ e HH:MM:SSZ``); la colonna ``evento_fit``
      riporta l'eventuale messaggio event presente in quell'istante.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import math
import statistics
import sys
from collections import Counter
from datetime import timezone
from pathlib import Path

import fitparse
import gpxpy

EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "Examples"
DEFAULT_GPX = EXAMPLES_DIR / "Pedalata_serale_19082026.gpx"
DEFAULT_FIT = EXAMPLES_DIR / "Pedalata_serale_19082026.fit"

# Fattore di conversione delle coordinate FIT (semicircles -> gradi decimali).
SEMICIRCLES_TO_DEG = 180.0 / (2**31)


# ---------------------------------------------------------------------------
# Utilità
# ---------------------------------------------------------------------------
def _to_utc(dt):
    """Normalizza un datetime in UTC (i FIT hanno tipicamente datetime naive)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso_utc(dt) -> str:
    """Formatta un datetime UTC come stringa ISO 8601 con suffisso Z."""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distanza approssimata in metri tra due coordinate geografiche."""
    raggio = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * raggio * math.asin(math.sqrt(a))


def _num(value, decimals: int = 2) -> str:
    """Formatta un numero opzionale, restituendo '-' se assente."""
    if value is None:
        return "-"
    return f"{value:.{decimals}f}"


# ---------------------------------------------------------------------------
# Lettura dei file
# ---------------------------------------------------------------------------
def load_gpx_points(path: Path) -> list[dict]:
    """Estrae i punti GPX: tempo, coordinate, quota ed estensioni numeriche."""
    with open(path, "r", encoding="utf-8") as fh:
        gpx = gpxpy.parse(fh)

    # Tag delle estensioni TrackPoint da convertire in numero.
    tag_numerici = {"hr", "cad", "atemp", "watemp", "depth", "power", "speed", "course"}

    def _estrai_estensioni(elemento, dati: dict) -> None:
        tag = elemento.tag.rsplit("}", 1)[-1].lower()
        testo = (elemento.text or "").strip()
        if testo and tag in tag_numerici:
            try:
                dati.setdefault(tag, float(testo))
            except ValueError:
                pass
        for figlio in elemento:
            _estrai_estensioni(figlio, dati)

    punti = []
    for trk in gpx.tracks:
        for segmento in trk.segments:
            for p in segmento.points:
                estensioni: dict = {}
                for ext in p.extensions or []:
                    _estrai_estensioni(ext, estensioni)
                hr = estensioni.get("hr")
                punti.append(
                    {
                        "time": _to_utc(p.time),
                        "lat": p.latitude,
                        "lon": p.longitude,
                        "ele": p.elevation,
                        "hr": int(hr) if hr is not None else None,
                    }
                )
    return punti


def load_fit_data(path: Path) -> tuple[list[dict], list[dict]]:
    """Estrae i record e i messaggi ``event`` dal file FIT.

    Returns:
        Tupla ``(records, events)``: i record (uno per punto campionato) e
        gli eventi con timestamp, tipo e trigger decodificati.
    """
    fit = fitparse.FitFile(str(path))
    records: list[dict] = []
    events: list[dict] = []

    for msg in fit.messages:
        valori = msg.get_values()

        if msg.name == "record":
            lat = valori.get("position_lat")
            lon = valori.get("position_long")
            alt = valori.get("enhanced_altitude")
            if alt is None:
                alt = valori.get("altitude")
            speed = valori.get("enhanced_speed")
            if speed is None:
                speed = valori.get("speed")
            records.append(
                {
                    "time": _to_utc(valori.get("timestamp")),
                    "lat": lat * SEMICIRCLES_TO_DEG if lat is not None else None,
                    "lon": lon * SEMICIRCLES_TO_DEG if lon is not None else None,
                    "ele": alt,
                    "hr": valori.get("heart_rate"),
                    "speed": speed,
                    "distance": valori.get("distance"),
                    "gps_accuracy": valori.get("gps_accuracy"),
                }
            )
        elif msg.name == "event":
            events.append(
                {
                    "time": _to_utc(valori.get("timestamp")),
                    "event": valori.get("event"),
                    "event_type": valori.get("event_type"),
                    "timer_trigger": valori.get("timer_trigger"),
                }
            )

    return records, events


# ---------------------------------------------------------------------------
# Abbinamento per timestamp
# ---------------------------------------------------------------------------
def abbina_punti(
    gpx_points: list[dict], fit_records: list[dict]
) -> tuple[list[tuple[dict, dict | None]], list[dict], int]:
    """Associa ogni record FIT al punto GPX con lo stesso timestamp.

    Nessun record FIT viene scartato: i record privi di corrispondente GPX
    vengono restituiti comunque, con ``None`` al posto del punto GPX, così
    che nel report risultino inseriti tra i punti GPX adiacenti.

    Returns:
        ``(righe, solo_gpx, doppioni_fit)``: ``righe`` è la lista ordinata
        cronologicamente di tuple ``(record_fit, punto_gpx_o_None)``;
        ``solo_gpx`` contiene i punti GPX senza record FIT contemporaneo e
        ``doppioni_fit`` il numero di timestamp FIT ripetuti nel file.
    """
    conteggi_fit = Counter(
        rec["time"] for rec in fit_records if rec["time"] is not None
    )
    doppioni_fit = sum(n - 1 for n in conteggi_fit.values() if n > 1)

    gpx_per_tempo = {p["time"]: p for p in gpx_points if p["time"] is not None}

    righe = [(rec, gpx_per_tempo.get(rec["time"])) for rec in fit_records]
    righe.sort(key=lambda rg: (rg[0]["time"] is None, rg[0]["time"]))

    tempi_fit = set(conteggi_fit)
    solo_gpx = [
        p for p in gpx_points
        if p["time"] is not None and p["time"] not in tempi_fit
    ]
    return righe, solo_gpx, doppioni_fit


def descrivi_evento(ev: dict) -> str:
    """Restituisce una descrizione compatta di un messaggio event FIT."""
    parti = [str(ev[k]) for k in ("event", "event_type") if ev.get(k) is not None]
    if ev.get("timer_trigger") is not None:
        parti.append(f"trigger={ev['timer_trigger']}")
    return "/".join(parti) if parti else "sconosciuto"


# ---------------------------------------------------------------------------
# Reportistica
# ---------------------------------------------------------------------------
def stampa_riepilogo(
    gpx_path: Path,
    fit_path: Path,
    gpx_points: list[dict],
    records: list[dict],
    events: list[dict],
    n_abbinati: int,
    n_solo_fit: int,
    solo_gpx: list,
    doppioni_fit: int,
    delta_pos: list,
    delta_ele: list,
    delta_hr: list,
    tempi_coppie: set,
) -> None:
    """Stampa su console il riepilogo del confronto puntuale."""
    linea = "=" * 78
    print(linea)
    print("CONFRONTO PUNTUALE GPX <-> FIT (tutti i record FIT, abbinati o meno)")
    print(linea)
    print(f"GPX: {gpx_path.name} ({len(gpx_points)} punti)")
    print(f"FIT: {fit_path.name} ({len(records)} record, {len(events)} eventi)")

    print(f"\nRecord FIT con punto GPX allo stesso istante: {n_abbinati}")
    print(
        f"Record FIT senza punto GPX contemporaneo: {n_solo_fit} "
        "(nel CSV compaiono inseriti tra i punti GPX adiacenti)"
    )
    print(f"Punti GPX senza record FIT contemporaneo: {len(solo_gpx)}")
    if doppioni_fit:
        print(f"Timestamp FIT duplicati nel file: {doppioni_fit}")

    # --- Messaggi event ----------------------------------------------------
    print("\n" + "-" * 78)
    n_coincidenti = sum(1 for ev in events if ev["time"] in tempi_coppie)
    print(
        f"MESSAGGI EVENT NEL FIT: {len(events)} "
        f"(coincidenti con un punto abbinato: {n_coincidenti})"
    )
    conteggi = Counter((ev["event"], ev["event_type"]) for ev in events)
    ordinati = sorted(conteggi.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1])))
    for (evento, tipo), n in ordinati:
        print(f"  {evento}/{tipo}: {n}")

    print("\nElenco cronologico (✓ = istante presente come punto abbinato GPX/FIT):")
    for ev in sorted(events, key=lambda e: (e["time"] is None, e["time"])):
        marca = "✓" if ev["time"] in tempi_coppie else " "
        quando = _iso_utc(ev["time"]) if ev["time"] else "senza timestamp"
        print(f"  {marca} {quando}  {descrivi_evento(ev)}")

    # --- Statistiche delle differenze ---------------------------------------
    print("\n" + "-" * 78)
    print("DIFFERENZE SUI PUNTI ABBINATI")
    if delta_pos:
        print(
            f"  Posizione : media {_num(statistics.mean(delta_pos))} m | "
            f"max {_num(max(delta_pos))} m | <1 m: {sum(d < 1 for d in delta_pos)} | "
            f"<5 m: {sum(d < 5 for d in delta_pos)} "
            f"(su {len(delta_pos)} coppie con coordinate)"
        )
    else:
        print("  Posizione : nessuna coppia confrontabile")
    if delta_ele:
        assolute = [abs(d) for d in delta_ele]
        print(
            f"  Quota     : |Δ| media {_num(statistics.mean(assolute))} m | "
            f"|Δ| max {_num(max(assolute))} m (su {len(delta_ele)} coppie)"
        )
    if delta_hr:
        assolute_hr = [abs(d) for d in delta_hr]
        print(
            f"  Battito   : |Δ| media {_num(statistics.mean(assolute_hr), 1)} bpm | "
            f"|Δ| max {max(assolute_hr):.0f} bpm (su {len(delta_hr)} coppie)"
        )


def _breve(t, riferimento) -> str:
    """Timestamp compatto: solo l'ora quando ricade nello stesso giorno."""
    if t.date() == riferimento.date():
        return t.strftime("%H:%M:%SZ")
    return _iso_utc(t)


def scrivi_csv(
    percorso: Path,
    righe: list,
    eventi_indice: dict,
    tempi_gpx: list,
) -> None:
    """Scrive il report CSV con una riga per OGNI record FIT.

    Le righe sono ordinate cronologicamente: i record privi di punto GPX
    simultaneo risultano quindi inseriti tra i punti GPX adiacenti, con la
    colonna ``posizione_tra_gpx`` che esplicita l'intervallo di appartenenza.
    """
    intestazione = [
        "timestamp_utc",
        "tipo",
        "posizione_tra_gpx",
        "gpx_lat", "gpx_lon", "gpx_ele_m", "gpx_hr",
        "fit_lat", "fit_lon", "fit_ele_m", "fit_hr",
        "fit_speed_ms", "fit_distance_m", "fit_gps_accuracy_m",
        "delta_posizione_m", "delta_quota_m", "delta_battito_bpm",
        "evento_fit",
    ]
    with open(percorso, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(intestazione)
        for rec, gpx_p in righe:
            t = rec["time"]
            if gpx_p is not None:
                tipo, posizione = "gpx+fit", ""
            elif t is not None:
                tipo = "solo_fit"
                i = bisect.bisect_left(tempi_gpx, t)
                prec = tempi_gpx[i - 1] if i > 0 else None
                succ = tempi_gpx[i] if i < len(tempi_gpx) else None
                if prec is not None and succ is not None:
                    posizione = f"tra {_breve(prec, t)} e {_breve(succ, t)}"
                elif succ is not None:
                    posizione = f"prima di {_breve(succ, t)}"
                elif prec is not None:
                    posizione = f"dopo {_breve(prec, t)}"
                else:
                    posizione = ""
            else:
                tipo, posizione = "solo_fit", ""

            if gpx_p is not None:
                delta_pos = (
                    _haversine_m(gpx_p["lat"], gpx_p["lon"], rec["lat"], rec["lon"])
                    if None not in (gpx_p["lat"], gpx_p["lon"], rec["lat"], rec["lon"])
                    else None
                )
                delta_ele = (
                    rec["ele"] - gpx_p["ele"]
                    if None not in (gpx_p["ele"], rec["ele"])
                    else None
                )
                delta_hr = (
                    rec["hr"] - gpx_p["hr"]
                    if None not in (gpx_p["hr"], rec["hr"])
                    else None
                )
            else:
                delta_pos = delta_ele = delta_hr = None

            eventi = eventi_indice.get(t, [])
            gpx_lat = (
                f"{gpx_p['lat']:.7f}" if gpx_p and gpx_p["lat"] is not None else ""
            )
            gpx_lon = (
                f"{gpx_p['lon']:.7f}" if gpx_p and gpx_p["lon"] is not None else ""
            )
            fit_lat = f"{rec['lat']:.7f}" if rec["lat"] is not None else ""
            fit_lon = f"{rec['lon']:.7f}" if rec["lon"] is not None else ""
            writer.writerow(
                [
                    _iso_utc(t) if t else "",
                    tipo,
                    posizione,
                    gpx_lat, gpx_lon,
                    _num(gpx_p["ele"], 1) if gpx_p else "",
                    "" if gpx_p is None or gpx_p["hr"] is None else gpx_p["hr"],
                    fit_lat, fit_lon,
                    _num(rec["ele"], 1),
                    "" if rec["hr"] is None else rec["hr"],
                    _num(rec["speed"], 3),
                    _num(rec["distance"], 1),
                    _num(rec["gps_accuracy"], 1),
                    _num(delta_pos),
                    _num(delta_ele),
                    "" if delta_hr is None else f"{delta_hr:.0f}",
                    " | ".join(eventi),
                ]
            )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    """Punto di ingresso dello script di confronto puntuale."""
    parser = argparse.ArgumentParser(
        description="Confronto dei punti GPX e FIT con timestamp identico."
    )
    parser.add_argument(
        "gpx", nargs="?", default=str(DEFAULT_GPX), help="Percorso del file GPX"
    )
    parser.add_argument(
        "fit", nargs="?", default=str(DEFAULT_FIT), help="Percorso del file FIT"
    )
    parser.add_argument(
        "-o", "--output", type=Path, default=None,
        help="Percorso del report CSV (default: confronto_punti_<nome>.csv nella cwd)",
    )
    args = parser.parse_args(argv)

    gpx_path = Path(args.gpx)
    fit_path = Path(args.fit)
    for percorso in (gpx_path, fit_path):
        if not percorso.is_file():
            print(f"File non trovato: {percorso}", file=sys.stderr)
            return 1

    gpx_points = load_gpx_points(gpx_path)
    records, events = load_fit_data(fit_path)
    righe, solo_gpx, doppioni_fit = abbina_punti(gpx_points, records)
    n_abbinati = sum(1 for _, gpx_p in righe if gpx_p is not None)
    n_solo_fit = len(righe) - n_abbinati

    # Istanti dei record abbinati (per marcare gli eventi coincidenti).
    tempi_coppie = {
        rec["time"] for rec, gpx_p in righe
        if gpx_p is not None and rec["time"] is not None
    }

    # Timestamp GPX ordinati: servono a collocare i record FIT solitari.
    tempi_gpx = sorted(p["time"] for p in gpx_points if p["time"] is not None)

    # Indice di TUTTI gli eventi per timestamp: più eventi possono condividere
    # lo stesso istante; usato per popolare la colonna evento_fit del CSV.
    eventi_indice: dict = {}
    for ev in events:
        if ev["time"] is not None:
            eventi_indice.setdefault(ev["time"], []).append(descrivi_evento(ev))

    # Differenze sulle coppie abbinate (dove entrambi i valori sono presenti).
    delta_pos: list[float] = []
    delta_ele: list[float] = []
    delta_hr: list[float] = []
    for rec, gpx_p in righe:
        if gpx_p is None:
            continue
        if None not in (gpx_p["lat"], gpx_p["lon"], rec["lat"], rec["lon"]):
            delta_pos.append(
                _haversine_m(gpx_p["lat"], gpx_p["lon"], rec["lat"], rec["lon"])
            )
        if None not in (gpx_p["ele"], rec["ele"]):
            delta_ele.append(rec["ele"] - gpx_p["ele"])
        if None not in (gpx_p["hr"], rec["hr"]):
            delta_hr.append(rec["hr"] - gpx_p["hr"])

    stampa_riepilogo(
        gpx_path, fit_path, gpx_points, records, events,
        n_abbinati, n_solo_fit, solo_gpx, doppioni_fit,
        delta_pos, delta_ele, delta_hr, tempi_coppie,
    )

    percorso_csv = args.output or Path(f"confronto_punti_{gpx_path.stem}.csv")
    scrivi_csv(percorso_csv, righe, eventi_indice, tempi_gpx)
    print("\n" + "-" * 78)
    print(f"Report dettagliato CSV: {percorso_csv.resolve()} ({len(righe)} righe)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
