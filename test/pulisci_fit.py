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
   ~4 cm, dovuto all'arrotondamento dei semicircoli FIT). Quando il device
   riacquisisce male dopo una pausa la traccia FIT riparte con un offset di
   decine di metri che non decade; Strava lo respinge e fa "strisciare" la
   traccia lungo la linea reale. Questo comportamento NON è riproducibile
   dai soli dati del FIT (la stream offsettata è internamente coerente:
   vedi ``reconstruct_positions``) ed è lasciato invariato.

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
# Ricostruzione delle posizioni nei tratti "fuori traiettoria" (stile Strava)
# ---------------------------------------------------------------------------
# Fenomeno (verificato su Pedalata_serale_19082026 17:04-17:10): dopo una
# pausa il ricevitore riacquisisce con soluzione OFFSETTATA di decine di
# metri e vi resta incollato per minuti. L'export Strava respinge l'offset e
# comprime la traccia (~28% della lunghezza FIT) lungo la linea reale.
#
# PERCHE' NON E' RIPRODUCIBILE DAI SOLI DATI FIT (esperimenti, vedi git):
#   * la stream offsettata è INTERNAMENTE COERENTE: passo GPS ~ incremento
#     del campo ``distance``, nessun salto punto-pointo, quindi nessuna firma
#     locale la distingue da una pedalata lenta legittima;
#   * i punti Strava corrono lungo la linea reale, decine di metri lontano
#     da qualunque fix FIT: il miglior filtro locale provato (EMA/MA/shrink/
#     chase) lascia RMS 20-26 m e max 30 m sulle due finestre peggiori;
#   * servirebbe un riferimento esterno (map-matching o GPX noto), che qui
#     non si vuole usare per costruire il "FIT equivalente".
#
# La macchina sotto (rilevatore + ponte strisciante) resta disponibile come
# base per un futuro snap su geometria nota ma è DISATTIVATA di default
# (parametro ``reconstruct`` / flag CLI ``--reconstruct``): attivarla senza
# riferimento esterno altererebbe dati legittimi senza avvicinarsi a Strava.

JUMP_THRESHOLD_M = 12.0      # passo oltre cui una fix è considerata fuori traiettoria
STEP_DT_MAX_S = 3.0          # il salto anomalo avviene tra fix CONSECUTIVE (non tra pause)
CUT_SPEED_MAX_MPS = 2.0      # attraversamento di pausa plausibile fino a questa velocita'
PAUSE_DRIFT_M = 8.0          # deriva minima dello stream durante una pausa per aprire
PAUSE_SPREAD_MAX_M = 30.0    # durante la deriva le fix restano ammassate (max raggio)
HEAL_SPEED_MPS = 2.0         # velocita' oltre cui lo stream post-ripartenza e' fidato
MIN_ANOMALY_S = 15.0         # durata minima dell'anomalia per attivare la ricostruzione
CLOSE_THRESHOLD_M = 20.0     # gap sotto cui lo stream FIT e' di nuovo considerato fidato
CHASE_SPEED_MPS = 0.6        # velocita' massima di "strisciamento" del ponte
CHASE_DT_CAP_S = 5.0         # secondo massimo di avanzamento per fix (i buchi non contano)
TAIL_BLEND_PTS = 6           # punti finali fusi con le fix reali al ricongiungimento



def _detect_open_times(records: List[dict], events) -> set:
    """Istanti (su record GREZZI) in cui aprire una ricostruzione di posizione.

    Due firme di errore grossolano:

    1. TELETRASPORTO: dopo una pausa la traccia salta oltre ``JUMP_THRESHOLD_M``
       tra due fix, a velocita' implicita superiore a ``CUT_SPEED_MAX_MPS``;
    2. DERIVA IN PAUSA: le fix registrate DURANTE una pausa restano ammassate
       (raggio ``PAUSE_SPREAD_MAX_M``) ma traslate di oltre ``PAUSE_DRIFT_M``
       rispetto alla posizione di stop, con velocita' irrisorie: il ricevitore
       ha perso l'aggancio ed e' riacquisito su una soluzione offsettata
       (Pedalata_serale_19082026 17:04-17:10). La traccia resta quindi
       affidabile solo da qualche punto successivo: si apre una ricostruzione
       dal secondo di stop e il ponte striscia finche' lo stream non guarisce.

    Gli spostamenti plausibili compiuti durante la pausa (es. passeggiata:
    ~100 m a passo umano, presenti anche nell'export Strava) NON aprono
    ricostruzioni: la velocita' media durante la pausa li tradisce.
    """
    out: set = set()
    pauses, _ = _pause_intervals(events)
    if not pauses:
        return out

    # --- firma 1: teletrasporti attraverso le pause -------------------------
    for i in range(1, len(records)):
        a, b = records[i - 1], records[i]
        if a["lat"] is None or b["lat"] is None:
            continue
        dt = (b["time"] - a["time"]).total_seconds()
        if dt <= STEP_DT_MAX_S:
            continue
        if not any(pa < b["time"] and pb > a["time"] for pa, pb in pauses):
            continue
        d = _haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])
        if d / max(dt, 1.0) > CUT_SPEED_MAX_MPS and d >= JUMP_THRESHOLD_M:
            out.add(b["time"])

    # --- firma 2: deriva del GPS durante la pausa ---------------------------
    for pa, pb in pauses:
        during = [
            r for r in records
            if pa <= r["time"] <= pb and r["lat"] is not None
        ]
        if len(during) < 2:
            continue
        pre = next((r for r in reversed(records) if r["time"] <= pa and r["lat"] is not None), None)
        if pre is None:
            continue
        center_lat = sum(r["lat"] for r in during) / len(during)
        center_lon = sum(r["lon"] for r in during) / len(during)
        spread = max(
            _haversine_m(r["lat"], r["lon"], center_lat, center_lon) for r in during
        )
        drift = _haversine_m(pre["lat"], pre["lon"], center_lat, center_lon)
        if drift >= PAUSE_DRIFT_M and spread <= PAUSE_SPREAD_MAX_M:
            # il ricevitore ha riacquisito su una soluzione offsettata: la
            # traccia non e' affidabile dalla RIPARTENZA (resume) finche' lo
            # stream non torna a correre sano (v. HEAL_SPEED_MPS)
            out.add(pb)
    return out


def reconstruct_positions(cleaned: List[dict], open_times: Optional[set] = None) -> List[dict]:
    """Sostituisce le posizioni dei tratti derivati con un ponte "strisciante".

    Opera sui record GIÀ puliti (pause tagliate, dedup applicata);
    ``open_times`` contiene gli istanti di apertura rilevati sui record
    grezzi (vedi ``_detect_open_times``). Dall'istante di apertura la
    posizione ricostruita avanza verso lo stream FIT:

    * del passo indicato dal campo ``distance`` del FIT (che riflette il
      movimento reale anche quando il GPS è offsettato), limitato a
      ``CHASE_SPEED_MPS`` al secondo;
    * nella direzione del punto FIT corrente.

    L'anomalia si chiude quando, dopo almeno ``MIN_ANOMALY_S``, lo stream
    torna entro ``CLOSE_THRESHOLD_M`` dal ponte; gli ultimi punti prima della
    chiusura vengono fusi linearmente col tornare ai dati reali (continuità).
    Se lo stream non riconverge, i dati restano invariati.
    """
    out = [dict(r) for r in cleaned]
    n = len(out)
    if not open_times:
        return out

    def dist(a, b):
        return _haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])

    def move_towards(src, dst, max_step):
        d = dist(src, dst)
        if d <= max_step:
            return {"lat": dst["lat"], "lon": dst["lon"]}
        f = max_step / d
        return {"lat": src["lat"] + f * (dst["lat"] - src["lat"]),
                "lon": src["lon"] + f * (dst["lon"] - src["lon"])}

    def bridge_step(rec, dt, prev_dist):
        """Passo del ponte: incremento `distance` FIT (movimento reale),
        limitato a CHASE_SPEED_MPS al secondo."""
        d = rec.get("distance")
        inc = None
        if d is not None and prev_dist is not None:
            inc = max(0.0, d - prev_dist)
        cap = CHASE_SPEED_MPS * min(dt, CHASE_DT_CAP_S)
        return inc if inc is not None else cap, d

    i = 1
    while i < n:
        prev, cur = out[i - 1], out[i]
        if cur["lat"] is None or prev["lat"] is None or cur["time"] not in open_times:
            i += 1
            continue

        # --- anomalia aperta su `cur`: insegui lo stream finché non riconverge ---
        state = {"lat": prev["lat"], "lon": prev["lon"]}
        prev_time = prev["time"]
        prev_dist = prev.get("distance")
        close_idx = None
        j = i
        while j < n:
            rec = out[j]
            if rec["lat"] is None:
                j += 1
                continue
            gap = dist(state, rec)
            elapsed = (rec["time"] - prev["time"]).total_seconds()
            if j > i and gap <= CLOSE_THRESHOLD_M and elapsed >= MIN_ANOMALY_S:
                close_idx = j
                break
            dt = min(max((rec["time"] - prev_time).total_seconds(), 1.0), CHASE_DT_CAP_S)
            steplen, pd = bridge_step(rec, dt, prev_dist)
            state = move_towards(state, rec, steplen)
            prev_time = rec["time"]
            prev_dist = pd if pd is not None else prev_dist
            j += 1

        if close_idx is None:
            # nessuna riconvergenza: lascia i dati originali
            i += 1
            continue

        # riscrive i punti interni con la traiettoria del ponte, fondendo gli
        # ultimi TAIL_BLEND punti con le posizioni reali per evitare scarti
        state = {"lat": prev["lat"], "lon": prev["lon"]}
        prev_time = prev["time"]
        prev_dist = prev.get("distance")
        first_idx = i
        while first_idx < close_idx and out[first_idx]["lat"] is None:
            first_idx += 1
        n_blend = min(TAIL_BLEND_PTS, max(0, close_idx - first_idx))
        for k in range(first_idx, close_idx):
            rec = out[k]
            dt = min(max((rec["time"] - prev_time).total_seconds(), 1.0), CHASE_DT_CAP_S)
            steplen, pd = bridge_step(rec, dt, prev_dist)
            state = move_towards(state, rec, steplen)
            prev_time = rec["time"]
            prev_dist = pd if pd is not None else prev_dist
            w = 1.0 if n_blend == 0 else min(1.0, (k - (close_idx - n_blend) + 1) / n_blend)
            if w < 1.0 and rec["lat"] is not None:
                rec["lat"] = state["lat"] + w * (rec["lat"] - state["lat"])
                rec["lon"] = state["lon"] + w * (rec["lon"] - state["lon"])
            else:
                rec["lat"] = state["lat"]
                rec["lon"] = state["lon"]
            rec["reconstructed"] = True
        i = close_idx + 1
    return out


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
def clean_fit(path: Path, reconstruct: bool = False) -> List[dict]:
    """Pipeline completa: lettura FIT -> pulizia (-> ricostruzione, opt-in)."""
    records, events = load_fit_data(path)
    cleaned = clean_records(records, events)
    if reconstruct:
        cleaned = reconstruct_positions(cleaned, _detect_open_times(records, events))
    return cleaned


def validate_pair(fit_path: Path, gpx_path: Optional[Path], reconstruct: bool = False) -> None:
    """Pulisce un FIT e, se fornito, lo valida contro il GPX di riferimento."""
    records, events = load_fit_data(fit_path)
    cleaned = clean_records(records, events)
    if reconstruct:
        cleaned = reconstruct_positions(cleaned, _detect_open_times(records, events))
    n_reco = sum(1 for r in cleaned if r.get("reconstructed"))
    print(
        f"\n{fit_path.name}: record FIT={len(records)}, eventi timer={len(events)}, "
        f"dopo la pulizia={len(cleaned)}, ricostruiti={n_reco}"
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
    parser.add_argument(
        "--reconstruct", action="store_true",
        help="Sperimentale: attiva la ricostruzione delle posizioni post-pausa "
             "(richiede un riferimento esterno per essere fedele a Strava)",
    )
    args = parser.parse_args(argv)
    reco = args.reconstruct

    if args.fit is None:
        # Modalità default: valida tutte le coppie disponibili in Examples/
        pairs = find_pairs()
        if not pairs:
            print("Nessuna coppia FIT/GPX trovata in Examples/", file=sys.stderr)
            return 1
        print(f"Coppie FIT/GPX trovate in Examples/: {len(pairs)}")
        for fit_path, gpx_path in pairs:
            validate_pair(fit_path, gpx_path, reconstruct=reco)
        return 0

    fit_path = Path(args.fit)
    if not fit_path.is_file():
        print(f"File non trovato: {fit_path}", file=sys.stderr)
        return 1

    gpx_path = Path(args.gpx) if args.gpx else None
    validate_pair(fit_path, gpx_path, reconstruct=reco)

    if args.output:
        cleaned = clean_fit(fit_path, reconstruct=reco)
        export_gpx(cleaned, args.output, name=fit_path.stem)
        print(f"\nTraccia pulita esportata: {args.output.resolve()}")

    return 0


if __name__ == "__main__":
    sys.exit(main())



