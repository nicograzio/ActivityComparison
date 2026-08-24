"""Pulizia di un file FIT secondo la logica (invertita empiricamente) di Strava.

Strava importa il FIT originale del device e lo elabora: il GPX riesportato
contiene un SOTTOINSIEME dei record 1 Hz del FIT. Confrontando le coppie
FIT/GPX in ``Examples`` è emerso che l'elaborazione consiste di:

1. TAGLIO DELLE PAUSE — vengono scartati i record con timestamp strettamente
   interno a un intervallo di pausa ``(stop, start)`` individuato dagli eventi
   ``timer`` del FIT; il secondo dello ``stop`` e quello dello ``start``
   vengono CONSERVATI (il buco nel GPX è lungo esattamente ``stop -> start``);
2. CODA INIZIALE CONSERVATA — eventuali record precedenti al primo ``start``
   vengono mantenuti (es. Pedalata_pomeridiana_11072026: punto alle 13:53:52,
   primo start alle 13:53:53); i record dopo l'ultimo stop vengono scartati;
3. DEDUPLICAZIONE TIMESTAMP — quando più record condividono lo stesso secondo
   viene tenuto quello CON posizione (es. il record di partenza con solo
   ``distance`` viene scartato); tra quelli con posizione vince l'ultimo;
4. RECORD VUOTI — i record privi di posizione ereditano le coordinate del
   punto precedente (verificato sull'ultimo record della Pedalata_serale);
5. le coordinate/quota passano attraverso senza modifiche (delta mediano
   ~4 cm, dovuto all'arrotondamento dei semicircoli FIT). Nei brevi tratti
   subito dopo una ripartenza Strava ricostruisce invece posizioni proprie
   (tratto quasi fermo, mai oltre qualche decina di metri): non è
   riproducibile dai soli dati FIT ed è privo di impatto sui tempi segmento.

Lo script VALIDA la pulizia confrontando i timestamp/posizioni prodotti con
quelli del GPX di riferimento della stessa attività.

Utilizzo (dalla root del progetto):
    python test/pulisci_fit.py                     # valida tutte le coppie FIT/GPX di Examples/
    python test/pulisci_fit.py <percorso.fit>      # solo report di pulizia (niente confronto)
    python test/pulisci_fit.py <percorso.fit> <percorso.gpx>
    python test/pulisci_fit.py <percorso.fit> -o traccia_pulita.gpx
"""

from __future__ import annotations

import argparse
import sys
from datetime import timezone
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import fitparse
import gpxpy

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_DIR = ROOT / "Examples"


# ---------------------------------------------------------------------------
# Utilità
# ---------------------------------------------------------------------------
def _naive_utc(dt):
    """Normalizza un datetime a naive UTC (i FIT di questi device leggono UTC)."""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distanza approssimata in metri tra due coordinate geografiche."""
    p1, p2 = radians(lat1), radians(lat2)
    a = (
        sin(radians(lat2 - lat1) / 2) ** 2
        + cos(p1) * cos(p2) * sin(radians(lon2 - lon1) / 2) ** 2
    )
    return 2 * 6371000.0 * asin(sqrt(a))


# ---------------------------------------------------------------------------
# Lettura del FIT
# ---------------------------------------------------------------------------
def load_fit_data(path: Path) -> Tuple[List[dict], List[Tuple[object, str]]]:
    """Estrae record e eventi timer dal FIT.

    Returns:
        records: lista di dict ordinati per timestamp
            (``time``, ``lat``, ``lon``, ``ele``, ``hr``, ``speed``, ``acc``,
            ``distance``); ``lat``/``lon`` sono None per i record senza posizione.
        events: lista cronologica (timestamp, tipo) degli eventi ``timer``
            (tipi: ``start``, ``stop``, ``stop_all``, ``marker``, ...).
    """
    fit = fitparse.FitFile(str(path))
    records: List[dict] = []
    events: List[Tuple[object, str]] = []

    semicirc = 180.0 / 2**31
    for msg in fit.messages:
        data = msg.get_values()
        name = msg.name
        if name == "record":
            ts = data.get("timestamp")
            if ts is None:
                continue
            lat = data.get("position_lat")
            lon = data.get("position_long")
            records.append(
                {
                    "time": _naive_utc(ts),
                    "lat": lat * semicirc if lat is not None else None,
                    "lon": lon * semicirc if lon is not None else None,
                    "ele": data.get("enhanced_altitude", data.get("altitude")),
                    "hr": data.get("heart_rate"),
                    "speed": data.get("enhanced_speed", data.get("speed")),
                    "acc": data.get("gps_accuracy"),
                    "distance": data.get("distance"),
                }
            )
        elif name == "event" and str(data.get("event")) == "timer":
            ts = data.get("timestamp")
            if ts is not None:
                events.append((_naive_utc(ts), str(data.get("event_type"))))

    records.sort(key=lambda r: r["time"])
    events.sort(key=lambda e: e[0])
    return records, events


# ---------------------------------------------------------------------------
# Algoritmo di pulizia
# ---------------------------------------------------------------------------
def _pause_intervals(events) -> Tuple[List[Tuple[object, object]], Optional[object]]:
    """Ricostruisce gli intervalli di pausa ``(stop, start_succ)`` e l'istante finale.

    La pausa apre allo ``stop`` (o ``stop_all``) e chiude allo ``start``
    successivo; l'istante finale dell'attività è l'ultimo evento di stop.
    """
    pauses: List[Tuple[object, object]] = []
    stop_t: Optional[object] = None
    last_stop: Optional[object] = None
    for ts, typ in events:
        if typ == "start":
            if stop_t is not None:
                pauses.append((stop_t, ts))
                stop_t = None
        elif typ in ("stop", "stop_all"):
            stop_t = ts
            last_stop = ts
    return pauses, last_stop


def clean_records(records: List[dict], events) -> List[dict]:
    """Applica le regole di pulizia di Strava ai record FIT.

    Regole (vedi docstring del modulo):
      1. scarta i record con ``stop < t < start`` (bordi inclusi conservati);
      2. scarta i record successivi all'ultimo evento di stop;
      3. deduplica i timestamp a favore del record con posizione (l'ultimo).
    """
    pauses, last_stop = _pause_intervals(events)

    def in_pause_gap(t) -> bool:
        return any(a < t < b for a, b in pauses)

    kept: List[dict] = []
    for rec in records:
        t = rec["time"]
        if in_pause_gap(t):
            continue
        if last_stop is not None and t > last_stop:
            continue
        kept.append(rec)

    # Deduplicazione: per uno stesso timestamp tiene l'ULTIMO record con posizione,
    # completandolo con gli eventuali campi presenti solo nel precedente.
    by_ts: Dict[object, dict] = {}
    for rec in kept:
        prev = by_ts.get(rec["time"])
        if prev is None:
            by_ts[rec["time"]] = rec
        elif rec["lat"] is not None or prev["lat"] is None:
            merged = dict(prev)
            merged.update({k: v for k, v in rec.items() if v is not None})
            by_ts[rec["time"]] = merged

    # I record rimasti senza posizione ereditano le coordinate del punto
    # precedente (cosi' fa Strava: es. record vuoto delle 17:56:58 della
    # Pedalata_serale_19082026 -> stesse coordinate del secondo prima).
    filled: List[dict] = []
    last_pos: Optional[Tuple[float, float]] = None
    for t in sorted(by_ts):
        rec = dict(by_ts[t])
        if rec["lat"] is None or rec["lon"] is None:
            if last_pos is not None:
                rec["lat"], rec["lon"] = last_pos
        else:
            last_pos = (rec["lat"], rec["lon"])
        filled.append(rec)
    return filled


# ---------------------------------------------------------------------------
# Confronto con il GPX di riferimento
# ---------------------------------------------------------------------------
def load_gpx_points(path: Path) -> Dict[object, Tuple[float, float, Optional[float]]]:
    """Timestamp GPX (naive UTC) -> (lat, lon, ele)."""
    with open(path, "r", encoding="utf-8") as fh:
        gpx = gpxpy.parse(fh)
    out: Dict[object, Tuple[float, float, Optional[float]]] = {}
    for trk in gpx.tracks:
        for seg in trk.segments:
            for p in seg.points:
                out[_naive_utc(p.time)] = (p.latitude, p.longitude, p.elevation)
    return out


def compare_with_gpx(cleaned: List[dict], gpx_points) -> dict:
    """Statistiche di similarità tra traccia pulita e GPX di riferimento."""
    clean_map = {r["time"]: r for r in cleaned}
    gts = set(gpx_points)
    cts = set(clean_map)
    common = gts & cts

    d_pos: List[float] = []
    d_ele: List[float] = []
    for t in common:
        rec = clean_map[t]
        if rec["lat"] is None or rec["lon"] is None:
            continue
        glat, glon, gele = gpx_points[t]
        d_pos.append(_haversine_m(rec["lat"], rec["lon"], glat, glon))
        if rec["ele"] is not None and gele is not None:
            d_ele.append(abs(rec["ele"] - gele))
    d_pos.sort()

    n_gpx, n_clean = len(gts), len(cts)
    return {
        "gpx_points": n_gpx,
        "clean_points": n_clean,
        "covered": len(common),
        "coverage_pct": 100.0 * len(common) / n_gpx if n_gpx else 0.0,
        "missing_from_fit": sorted(gts - cts),   # punti GPX non riprodotti
        "extra_in_fit": sorted(cts - gts),       # record puliti in più rispetto al GPX
        "d_pos": d_pos,
        "n_pos": len(d_pos),
        "d_ele_max": max(d_ele) if d_ele else None,
    }


def print_validation(stem: str, stats: dict) -> None:
    """Stampa il report di validazione di una coppia."""
    d_pos = stats["d_pos"]
    n = stats["n_pos"]
    print(f"\n=== {stem} ===")
    print(
        f"  punti GPX: {stats['gpx_points']}  |  record FIT puliti: {stats['clean_points']}  |  "
        f"copertura: {stats['covered']}/{stats['gpx_points']} ({stats['coverage_pct']:.2f}%)"
    )
    if stats["missing_from_fit"]:
        ex = [t.strftime("%H:%M:%S") for t in stats["missing_from_fit"][:5]]
        print(f"  timestamp GPX non riprodotti: {len(stats['missing_from_fit'])} {ex}")
    if stats["extra_in_fit"]:
        ex = [t.strftime("%H:%M:%S") for t in stats["extra_in_fit"][:5]]
        print(f"  timestamp extra rispetto al GPX: {len(stats['extra_in_fit'])} {ex}")
    if n:
        over1 = sum(1 for x in d_pos if x > 1.0)
        over10 = sum(1 for x in d_pos if x > 10.0)
        print(
            f"  delta posizione su {n} coppie: mediana {d_pos[n // 2]:.3f} m | "
            f"p95 {d_pos[int(n * 0.95)]:.3f} m | max {d_pos[-1]:.2f} m"
        )
        print(
            f"  coppie con delta > 1 m: {over1} ({100 * over1 / n:.2f}%)"
            f" | > 10 m: {over10} ({100 * over10 / n:.2f}%)"
        )
    if stats["d_ele_max"] is not None:
        print(f"  delta quota massimo: {stats['d_ele_max']:.2f} m")
    verdict = (
        "OK" if stats["coverage_pct"] == 100.0 and not stats["extra_in_fit"] else "DIFF"
    )
    print(f"  esito timestamp: {verdict}")


# ---------------------------------------------------------------------------
# Export GPX della traccia pulita
# ---------------------------------------------------------------------------
def _fmt_num(value: float, decimals: int) -> str:
    """Formatta un numero con un numero fisso di decimali (senza -0)."""
    out = f"{value:.{decimals}f}"
    return "0" if out == "-0" else out


def export_gpx(cleaned: List[dict], path: Path, name: Optional[str] = None) -> None:
    """Scrive la traccia pulita come GPX (un solo trkseg, come l'export Strava).

    Coordinate con 7 decimali, quota con 1 decimale e timestamp UTC con
    suffisso ``Z``; il battito va nell'estensione TrackPointExtension.
    """
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
        ' xmlns:gpxtpx="https://www.garmin.com/xmlschemas/GpxExtensions/v3"'
        ' xmlns="http://www.topografix.com/GPX/1/1"'
        ' xsi:schemaLocation="http://www.topografix.com/GPX/1/1'
        ' http://www.topografix.com/GPX/1/1/gpx.xsd"'
        ' version="1.1" creator="pulisci_fit">',
        " <trk>",
    ]
    if name:
        lines.append(f"  <name>{_xml_escape(name)}</name>")
    lines.append("  <trkseg>")
    for rec in cleaned:
        if rec["lat"] is None or rec["lon"] is None:
            continue
        lines.append(
            f'   <trkpt lat="{_fmt_num(rec["lat"], 7)}" lon="{_fmt_num(rec["lon"], 7)}">'
        )
        if rec["ele"] is not None:
            lines.append(f"    <ele>{_fmt_num(rec['ele'], 1)}</ele>")
        ts = rec["time"].strftime("%Y-%m-%dT%H:%M:%SZ")
        lines.append(f"    <time>{ts}</time>")
        if rec["hr"] is not None:
            lines += [
                "    <extensions>",
                "     <gpxtpx:TrackPointExtension>",
                f"      <gpxtpx:hr>{int(rec['hr'])}</gpxtpx:hr>",
                "     </gpxtpx:TrackPointExtension>",
                "    </extensions>",
            ]
        lines.append("   </trkpt>")
    lines += ["  </trkseg>", " </trk>", "</gpx>", ""]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def _xml_escape(text: str) -> str:
    """Escape minimale dei caratteri XML speciali."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def validate_pair(fit_path: Path, gpx_path: Optional[Path]) -> None:
    """Pulisce un FIT e, se fornito, lo valida contro il GPX di riferimento."""
    records, events = load_fit_data(fit_path)
    cleaned = clean_records(records, events)
    print(
        f"\n{fit_path.name}: record FIT={len(records)}, eventi timer={len(events)}, "
        f"dopo la pulizia={len(cleaned)}"
    )
    if gpx_path is not None:
        stats = compare_with_gpx(cleaned, load_gpx_points(gpx_path))
        print_validation(fit_path.stem, stats)


def find_pairs() -> List[Tuple[Path, Path]]:
    """Cerca in Examples/ le attività presenti sia in FIT che in GPX."""
    stems = {p.stem for p in EXAMPLES_DIR.glob("*.fit")}
    return [
        (EXAMPLES_DIR / f"{s}.fit", EXAMPLES_DIR / f"{s}.gpx")
        for s in sorted(stems)
        if (EXAMPLES_DIR / f"{s}.gpx").is_file()
    ]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Pulizia FIT secondo la logica Strava e validazione contro GPX."
    )
    parser.add_argument("fit", nargs="?", help="Percorso del file FIT da pulire")
    parser.add_argument("gpx", nargs="?", help="GPX di riferimento (stessa attività)")
    parser.add_argument("-o", "--output", type=Path, help="Esporta la traccia pulita in GPX")
    args = parser.parse_args(argv)

    if args.fit is None:
        # Modalità default: valida tutte le coppie disponibili in Examples/
        pairs = find_pairs()
        if not pairs:
            print("Nessuna coppia FIT/GPX trovata in Examples/", file=sys.stderr)
            return 1
        print(f"Coppie FIT/GPX trovate in Examples/: {len(pairs)}")
        for fit_path, gpx_path in pairs:
            validate_pair(fit_path, gpx_path)
        return 0

    fit_path = Path(args.fit)
    if not fit_path.is_file():
        print(f"File non trovato: {fit_path}", file=sys.stderr)
        return 1

    gpx_path = Path(args.gpx) if args.gpx else None
    validate_pair(fit_path, gpx_path)

    if args.output:
        records, events = load_fit_data(fit_path)
        cleaned = clean_records(records, events)
        export_gpx(cleaned, args.output, name=fit_path.stem)
        print(f"\nTraccia pulita esportata: {args.output.resolve()}")

    return 0


if __name__ == "__main__":
    sys.exit(main())



