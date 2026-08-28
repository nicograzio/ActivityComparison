#!/usr/bin/env python3
"""Ricerca dell'ottimo dei parametri di core/strava_analyzer.py.

Ground truth: Examples/*.gpx (solo GPX: i FIT sono rielaborati da Strava e
usati solo come indice informativo), Strava_Segments/ (segmenti),
test/esempio_tempo_segmenti.txt (tempi ufficiali Strava).

Strategia (default, --method refine) — ricerca continua coarse-to-fine:
  * ogni parametro ha un INTERVALLO CONTINUO (PARAM_BOUNDS): nessun elenco
    predefinito di valori, quindi l'ottimo è raggiungibile anche quando non
    coincide con alcun valore scelto "a mano";
  * fase A1 sweep 1-D: K valori per parametro sparsi su TUTTO il range →
    profilo di sensibilità ed esclusione dei parametri non sensibili;
  * fase A2 discesa greedy: applica in sequenza i migliori punti dello sweep,
    tenendo solo quelli che migliorano davvero (interazioni sequenziali);
  * fase A3 esplorazione congiunta (Latin Hypercube): configurazioni che
    variano tutti i parametri attivi insieme;
  * fase B affinamento iterativo: attorno al best corrente ogni parametro ha
    un intervallo che si RESTRINGE ad ogni round (fattore adattivo sul tasso
    di successo) e i nuovi valori sono campionati UNIFORMEMENTE dentro
    l'intervallo: si affina proprio dove si vedono i miglioramenti;
  * stop quando ogni intervallo scende sotto la risoluzione minima (gli interi
    convergono al valore esatto) oppure su pazienza/budget;
  * polish finale continuo: ±passo di risoluzione per ogni parametro.

Strategie alternative (su griglie discrete, per confronto):
    --method adaptive  AMBS: batch evolutivi con sigma dinamico in passi di griglia
    --method classic   coordinate descent + random search

Usage (dalla root):
    python test/ottimizza_parametri.py              # media (refine, default)
    python test/ottimizza_parametri.py --quick      # veloce
    python test/ottimizza_parametri.py --deep       # approfondita
    python test/ottimizza_parametri.py --baseline   # solo baseline + dead-param
    python test/ottimizza_parametri.py --export test/params_ottimi.json
    python test/ottimizza_parametri.py --method adaptive   # vecchio motore
    python test/ottimizza_parametri.py --method classic
    python test/ottimizza_parametri.py -q           # output compatto
    python test/ottimizza_parametri.py -d           # log dettagliato di ogni operazione
"""

import argparse
import json
import os
import math
import random
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import core.strava_analyzer as sa  # noqa: E402
from core.gpx_loader import load_gpx  # noqa: E402
from core.strava_analyzer import (  # noqa: E402
    find_strava_segments_in_track,
    load_strava_segments,
)
from core.track import Track  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_DIR = ROOT / "Examples"
SEGMENTS_DIR = ROOT / "Strava_Segments"
STRAVA_TXT = ROOT / "test" / "esempio_tempo_segmenti.txt"

# Pesi qualità (identici a test/confronto_segmenti.py)
SCORE_W_RECALL = 0.35
SCORE_W_PRECISION = 0.35
SCORE_W_TIME = 0.30
TIME_TOLERANCE_S = 15.0
_DETAILED: bool = False

# ---------------------------------------------------------------------------
# PARAMETRI OGGETTO DELL'OTTIMIZZAZIONE
# ---------------------------------------------------------------------------
PARAM_NAMES: List[str] = [
    "DISTANCE_THRESHOLD_M", "START_TOL_RATIO", "END_TOL_RATIO", "MAX_GAP_RATIO",
    "CLUSTER_GAP_IDX", "PROGRESS_RATIO", "PROGRESS_SLACK_M", "MIN_DENSITY",
    "MAX_DENSITY", "MIN_LENGTH_M", "END_PROJECTION_EXTRA_IDX",
    "END_PROJECTION_ACCEPT_M", "END_PROJECTION_EXIT_RISE_M",
    "START_PROJECTION_EXTRA_IDX",
    "START_PROJECTION_ACCEPT_M",
    "START_PROJECTION_EXIT_RISE_M",
    "TRIM_REF_POINTS", "TRIM_CHECK_LIMIT", "TRIM_INDEX_GAP", "ANCHOR_SCAN_RANGE",
    "STATIONARY_SPEED_KMH", "OVERLAP_OCCUPANCY_THRESHOLD", #"MAX_TOTAL_PASSES",
    "HARD_ACCEPT_M",
]

DEFAULTS: Dict[str, float] = {name: getattr(sa, name) for name in PARAM_NAMES}

# Griglie di ricerca. Griglia vuota = parametro congelato (ma verificato come dead).
SEARCH_SPACE: Dict[str, List[float]] = {
    "DISTANCE_THRESHOLD_M": [25.0, 30.0, 35.0, 40.0, 45.0, 50.0, 55.0, 60.0, 70.0],
    "START_TOL_RATIO": [0.06, 0.08, 0.10, 0.12, 0.15, 0.18, 0.20, 0.25],
    "END_TOL_RATIO": [0.06, 0.08, 0.10, 0.12, 0.15, 0.18, 0.20, 0.25],
    "MAX_GAP_RATIO": [0.08, 0.10, 0.12, 0.15, 0.20, 0.25, 0.30, 0.35],
    "CLUSTER_GAP_IDX": [20, 30, 40, 50, 60, 80],
    "PROGRESS_RATIO": [1.5, 1.75, 2.0, 2.25, 2.5, 2.75, 3.0, 3.5, 4.0],
    "PROGRESS_SLACK_M": [5.0, 10.0, 15.0, 20.0, 30.0, 40.0, 50.0, 60.0],
    "MIN_DENSITY": [0.30, 0.40, 0.45, 0.50, 0.55, 0.60, 0.70, 0.80],
    "MAX_DENSITY": [1.20, 1.30, 1.40, 1.50, 1.60, 1.70, 1.80, 2.00],
    "MIN_LENGTH_M": [1.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 40.0],
    "END_PROJECTION_EXTRA_IDX": [30, 40, 50, 60, 70, 80, 100, 120],
    "END_PROJECTION_ACCEPT_M": [25.0, 30.0, 35.0, 40.0, 45.0, 50.0, 55.0, 65.0],
    "END_PROJECTION_EXIT_RISE_M": [3.0, 4.0, 6.0, 8.0, 10.0, 12.0, 15.0, 20.0],
    "START_PROJECTION_EXTRA_IDX": [30, 40, 50, 60, 80, 100, 120],
    "START_PROJECTION_ACCEPT_M": [20.0, 25.0, 30.0, 35.0, 40.0, 45.0, 50.0],
    "START_PROJECTION_EXIT_RISE_M": [1.0, 2.0, 3.0, 4.0, 5.0, 8.0, 10.0],
    "TRIM_REF_POINTS": [2, 3, 4, 5, 6],
    "TRIM_CHECK_LIMIT": [10, 15, 20, 25, 30, 40, 50],
    "TRIM_INDEX_GAP": [5, 8, 10, 12, 15, 20, 25],
    "ANCHOR_SCAN_RANGE": [2, 3, 4, 5, 6, 8],
    "STATIONARY_SPEED_KMH": [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0],
    "OVERLAP_OCCUPANCY_THRESHOLD": [0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80],
    #"MAX_TOTAL_PASSES": [4, 6, 8, 10, 12, 16, 20, 24],
    "HARD_ACCEPT_M": [25.0, 30.0, 35.0, 40.0, 45.0, 55.0, 70.0, 90.0],
}

# Range continui di ricerca (--method refine). A differenza delle griglie
# sopra, qui ogni parametro può assumere QUALSIASI valore nell'intervallo:
# la ricerca parte ampia e poi affina intorno ai valori che danno
# miglioramento, così l'ottimo viene raggiunto anche se non coincide con
# alcun valore scelto a mano. Gli estremi derivano dalle griglie storiche
# (±~10% della larghezza) con estensioni mirate dove aveva senso
# (es. STATIONARY_SPEED_KMH: l'ottimo precedente era sul bordo della griglia).
# Nota: alcuni default storici del modulo restano volutamente FUORI dal range
# (es. START_TOL_RATIO 0.5, *_EXTRA_IDX 1500,
# TRIM_CHECK_LIMIT 1800): sono valori che disattivano le rispettive
# funzionalità, non candidati plausibili per l'ottimo.
PARAM_BOUNDS: Dict[str, Tuple[float, float]] = {
    "DISTANCE_THRESHOLD_M": (10.0, 100.0),
    "START_TOL_RATIO": (0.0, 0.5),
    "END_TOL_RATIO": (0.0, 0.5),
    "MAX_GAP_RATIO": (0.0, 0.6),
    "CLUSTER_GAP_IDX": (0.0, 100.0),
    "PROGRESS_RATIO": (0.0, 5.0),
    "PROGRESS_SLACK_M": (0.0, 75.0),
    "MIN_DENSITY": (0.1, 0.9),
    "MAX_DENSITY": (1.0, 2.5),
    "MIN_LENGTH_M": (0.0, 44.0),
    "END_PROJECTION_EXTRA_IDX": (20.0, 150.0),
    "END_PROJECTION_ACCEPT_M": (0.0, 90.0),
    "END_PROJECTION_EXIT_RISE_M": (0.0, 30.0),
    "START_PROJECTION_EXTRA_IDX": (20.0, 150.0),
    "START_PROJECTION_ACCEPT_M": (0.0, 90.0),
    "START_PROJECTION_EXIT_RISE_M": (0.0, 30.0),
    # NB: lower bound 1 (e non 0): con 0 punti di riferimento il trim perde di
    # significato e la ricerca continuativa genererebbe configurazioni degeneri.
    "TRIM_REF_POINTS": (1.0, 25.0),
    "TRIM_CHECK_LIMIT": (0.0, 75.0),
    "TRIM_INDEX_GAP": (0.0,30.0),
    "ANCHOR_SCAN_RANGE": (0.0, 20.0),
    "STATIONARY_SPEED_KMH": (0.0, 4.0),
    "OVERLAP_OCCUPANCY_THRESHOLD": (0.1, 0.85),
    #"MAX_TOTAL_PASSES": (2.0, 5.0),
    # Tetto rigido di accettazione (m) per le valli di proiezione start/end
    "HARD_ACCEPT_M": (18.0, 99.0),
}


def param_min_step(name: str) -> float:
    """Risoluzione minima significativa per il parametro `name`.

    Gli interi hanno passo 1 (la ricerca converge al valore esatto); i float
    hanno una risoluzione pari a ~0.4% della larghezza del range, quindi
    l'affinamento può scendere fino a quella precisione.
    """
    lo, hi = PARAM_BOUNDS[name]
    if name in INT_PARAMS:
        return 1.0
    return max((hi - lo) * 0.004, 1e-4)


def _clip_bound(name: str, value: float) -> float:
    """Clip ai bounds di `name`; arrotonda a intero per i parametri interi."""
    lo, hi = PARAM_BOUNDS[name]
    v = min(hi, max(lo, float(value)))
    return float(round(v)) if name in INT_PARAMS else v


def _fix_density_constraint(cfg: Dict[str, float]) -> None:
    """Garantisce MIN_DENSITY < MAX_DENSITY aggiustando la coppia sul posto.

    Viene chiamata PRIMA di ogni valutazione: correggere qui non costa
    nessuna valutazione sprecata.
    """
    lo = cfg.get("MIN_DENSITY")
    hi = cfg.get("MAX_DENSITY")
    if lo is None or hi is None:
        return
    if float(lo) >= float(hi) - 0.02:
        mid = (float(lo) + float(hi)) / 2.0
        cfg["MIN_DENSITY"] = mid - 0.01
        cfg["MAX_DENSITY"] = mid + 0.01


# Parametri più influenti → testati per primi nel coordinate descent (classic)
CRITICAL_PARAMS = [
    "DISTANCE_THRESHOLD_M", "START_TOL_RATIO", "END_TOL_RATIO", "MAX_GAP_RATIO",
    "PROGRESS_RATIO", "PROGRESS_SLACK_M", "MIN_DENSITY", "MAX_DENSITY",
    "MIN_LENGTH_M", "END_PROJECTION_ACCEPT_M", "END_PROJECTION_EXIT_RISE_M",
    "START_PROJECTION_ACCEPT_M", "START_PROJECTION_EXIT_RISE_M",
    "STATIONARY_SPEED_KMH", "OVERLAP_OCCUPANCY_THRESHOLD",
    "HARD_ACCEPT_M",
]


# ---------------------------------------------------------------------------
# MODELLI DATI
# ---------------------------------------------------------------------------
@dataclass
class Metrics:
    quality: float = 0.0
    recall_pct: float = 0.0
    precision_pct: float = 0.0
    time_acc_pct: float = 0.0
    missing: int = 0
    extra: int = 0
    paired: int = 0
    mean_abs_delta: float = 0.0
    rms_delta: float = 0.0
    matched_ok: int = 0
    matched_small: int = 0
    matched_off: int = 0
    config: Dict[str, float] = field(default_factory=dict)
    elapsed_s: float = 0.0

    def short(self) -> str:
        return (
            f"Q={self.quality:5.1f}  rec={self.recall_pct:5.1f}%  "
            f"prec={self.precision_pct:5.1f}%  time={self.time_acc_pct:5.1f}%  "
            f"(ΔRMS={self.rms_delta:.2f}s, mancanti={self.missing}, extra={self.extra})"
        )


# ---------------------------------------------------------------------------
# UTILITÀ CONFRONTO (identiche a confronto_segmenti.py)
# ---------------------------------------------------------------------------
def normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def parse_strava_txt(path: Path) -> Dict[str, List[Tuple[str, int, str]]]:
    """Legge test/esempio_tempo_segmenti.txt → {nome_traccia: [(nome_norm, sec, nome)]}."""
    result: Dict[str, List[Tuple[str, int, str]]] = {}
    current: Optional[str] = None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.endswith(":") and not line.startswith("-"):
            current = line[:-1].strip()
            result.setdefault(current, [])
            continue
        m = re.match(r"-\s+(.+?)\s+(\d{1,2}:\d{2}(?::\d{2})?)$", line)
        if m and current is not None:
            name = m.group(1).strip()
            raw = m.group(2)
            parts = [int(p) for p in raw.split(":")]
            secs = parts[-1] + parts[-2] * 60 + (parts[0] * 3600 if len(parts) == 3 else 0)
            result[current].append((normalize(name), secs, name))
    return result


def fmt_time(sec) -> str:
    if sec is None:
        return "n/d"
    sec = int(round(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


# ---------------------------------------------------------------------------
# CARICAMENTO DATI (una volta sola, poi riusati in ogni valutazione)
# ---------------------------------------------------------------------------
def load_activity_tracks() -> List[Tuple[str, Track]]:
    """Carica SOLO i .gpx di Examples/: il punteggio dell'ottimizzazione deve
    coincidere con quello di test/confronto_segmenti.py, calcolato sui soli
    GPX (i .fit sono rielaborati da Strava e usati solo come indice)."""
    loaded = []
    for f in sorted(EXAMPLES_DIR.glob("*.gpx")):
        loaded.append((f.name, load_gpx(str(f))))
    return loaded


# ---------------------------------------------------------------------------
# VALUTAZIONE DI UNA CONFIGURAZIONE
# ---------------------------------------------------------------------------
# Parametri che nel codice vengono usati come interi (range/min/max/indici)
INT_PARAMS = {
    "CLUSTER_GAP_IDX",
    "END_PROJECTION_EXTRA_IDX", "START_PROJECTION_EXTRA_IDX",
    "TRIM_REF_POINTS", "TRIM_CHECK_LIMIT", "TRIM_INDEX_GAP",
    "ANCHOR_SCAN_RANGE", #"MAX_TOTAL_PASSES",
}


def _apply_overrides(overrides: Dict[str, float]) -> Dict[str, float]:
    """Applica i valori al modulo core.strava_analyzer; restituisce i precedenti.

    I parametri che il codice usa come interi (range/min/max) vengono
    convertiti a int per evitare TypeError ('float' cannot be interpreted).
    """
    saved = {}
    for name, value in overrides.items():
        if not hasattr(sa, name):
            continue
        saved[name] = getattr(sa, name)
        if name in INT_PARAMS:
            value = int(value)
        setattr(sa, name, value)
    return saved


def _restore(saved: Dict[str, float]) -> None:
    for name, value in saved.items():
        setattr(sa, name, value)


# ---------------------------------------------------------------------------
# VALUTAZIONE PARALLELA (riuso dei worker tra una valutazione e l'altra)
# ---------------------------------------------------------------------------
# Ogni evaluate() valuta 15 tracce indipendenti: il costo va quasi tutto in
# find_strava_segments_in_track. Le tracce, i segmenti e la ground-truth non
# cambiano mai tra una valutazione e l'altra: i worker li caricano UNA volta
# nel loro processo (initializer) e ogni evaluate vi spedisce solo la config
# (piccola) + l'indice della traccia. I worker restituiscono i contatori
# leggeri per traccia, che il main aggrega. È in fallback sequenziale quello
# originale se il process pool non è disponibile.
_W_ACTIVITY: Optional[List[Tuple[str, Track]]] = None
_W_SEGMENTS: Optional[List[dict]] = None
_W_STRAVA: Optional[Dict[str, List[Tuple[str, int, str]]]] = None
_pool: Optional[ProcessPoolExecutor] = None


def _init_optim_worker() -> None:
    """Carica i dati nel processo worker (eseguito una volta per processo)."""
    global _W_ACTIVITY, _W_SEGMENTS, _W_STRAVA
    _W_ACTIVITY = load_activity_tracks()
    _W_SEGMENTS = load_strava_segments(str(SEGMENTS_DIR))
    _W_STRAVA = parse_strava_txt(STRAVA_TXT)


def _score_track(
    track: Track,
    strava_list: Sequence[Tuple[str, Optional[float], str]],
    distance: float,
    segments: List[dict],
) -> Dict[str, float]:
    """Calcola i contatori di qualità per UNA traccia (aggregati dal main).

    Applica find_strava_segments_in_track e il confronto greedy per ogni
    segmento, restituendo solo i contatori (niente oggetti, è serializzabile).
    """
    found = find_strava_segments_in_track(
        segments, track,
        distance_threshold_m=distance,
    )
    found_norm = [
        (normalize(o["segment_name"]), o["time_sec"], o["segment_name"], o["direction"])
        for o in found
    ]
    n_algo = len(found_norm)
    n_strava = len(strava_list)

    found_by_name: Dict[str, list] = {}
    for f in found_norm:
        found_by_name.setdefault(f[0], []).append(f)
    strava_by_name: Dict[str, list] = {}
    for s in strava_list:
        strava_by_name.setdefault(s[0], []).append(s)

    missing = extra = paired = 0
    matched_ok = matched_small = matched_off = 0
    sum_abs = 0.0
    sum_sq = 0.0

    # Ordine di prima comparsa: deterministico (niente iterazione di un set)
    names = list(dict.fromkeys(
        list(found_by_name.keys()) + list(strava_by_name.keys())
    ))
    for name in names:
        algo_occ = found_by_name.get(name, ())
        strava_occ = strava_by_name.get(name, ())
        a_free = list(range(len(algo_occ)))
        s_free = list(range(len(strava_occ)))
        while a_free and s_free:
            best = None
            for i in a_free:
                t_algo = algo_occ[i][1]
                if t_algo is None:
                    continue
                for j in s_free:
                    t_str = strava_occ[j][1]
                    if t_str is None:
                        continue
                    d = abs(t_algo - t_str)
                    if best is None or d < best[0]:
                        best = (d, i, j)
            if best is None:
                break
            _, i, j = best
            diff = algo_occ[i][1] - strava_occ[j][1]
            abs_diff = abs(float(diff))
            paired += 1
            sum_abs += abs_diff
            sum_sq += abs_diff * abs_diff
            if abs(diff) <= 3:
                matched_ok += 1
            elif abs(diff) <= 15:
                matched_small += 1
            else:
                matched_off += 1
            a_free.remove(i)
            s_free.remove(j)
        missing += len(s_free)
        extra += len(a_free)

    return {
        "n_algo": n_algo,
        "n_strava": n_strava,
        "missing": missing,
        "extra": extra,
        "paired": paired,
        "matched_ok": matched_ok,
        "matched_small": matched_small,
        "matched_off": matched_off,
        "sum_abs": sum_abs,
        "sum_sq": sum_sq,
    }


def _eval_task(payload) -> Dict[str, float]:
    """Entry point del worker: (config completa, indice traccia) → contatori.

    Applica la config COMPLETA (default + overrides + vincolo densità) al
    proprio modulo core.strava_analyzer prima di calcolare: ogni task parte da
    uno stato pulito e NON accumula i parametri dei task precedenti.
    """
    config, index = payload
    _fix_density_constraint(config)
    _apply_overrides(config)

    # I dati del worker vengono caricati UNA volta nel processo dall'initializer
    # (_init_optim_worker), quindi in condizioni normali non sono mai None. Ci
    # protegge comunque (e fa capire a Pylance che non serve pedice su None)
    # una verifica difensiva: se mancano, li carichiamo al volo.
    activity = _W_ACTIVITY
    segments = _W_SEGMENTS
    strava = _W_STRAVA
    if activity is None or segments is None or strava is None:
        _init_optim_worker()
        activity = _W_ACTIVITY
        segments = _W_SEGMENTS
        strava = _W_STRAVA
    assert activity is not None and segments is not None and strava is not None

    name, track = activity[index]
    distance = float(config.get("DISTANCE_THRESHOLD_M", DEFAULTS["DISTANCE_THRESHOLD_M"]))
    strava_list = strava.get(name, [])
    return _score_track(track, strava_list, distance, segments)


def _evaluate_counters(
    overrides: Dict[str, float],
    activity_tracks: List[Tuple[str, Track]],
    strava: Dict[str, List[Tuple[str, int, str]]],
    segments: List[dict],
    distance_threshold: float,
) -> List[dict]:
    """Contatori di qualità per ogni traccia: in parallelo sul pool persistente,
    o in fallback sequenziale (stesso codice, nel processo principale)."""
    n = len(activity_tracks)
    if n == 0:
        return []
    pool = _get_pool()
    if pool is not None:
        try:
            payloads = [(overrides, i) for i in range(n)]
            return list(pool.map(_eval_task, payloads))
        except Exception:
            pass  # il pool può fallire al primo uso → ripiega sul sequenziale
    # Fallback sequenziale.in-process, con overrides applicati/ripristinati
    saved = _apply_overrides(overrides)
    counters = []
    try:
        for name, track in activity_tracks:
            strava_list = strava.get(name, [])
            counters.append(
                _score_track(track, strava_list, distance_threshold, segments)
            )
    finally:
        _restore(saved)
    return counters


def _get_pool() -> Optional[ProcessPoolExecutor]:
    """Crea (su richiesta) il pool persistente di worker; None se non possibile."""
    global _pool
    if _pool is None:
        # Un worker per CPU disponibile; le tracce indipendenti si dividono tra loro.
        workers = os.cpu_count() or 1
        try:
            _pool = ProcessPoolExecutor(max_workers=workers, initializer=_init_optim_worker)
        except (OSError, PermissionError, ImportError):
            _pool = None
    return _pool


def evaluate(
    overrides: Dict[str, float],
    activity_tracks: List[Tuple[str, Track]],
    strava: Dict[str, List[Tuple[str, int, str]]],
    segments: List[dict],
) -> Metrics:
    """Calcola il punteggio qualità per la config `overrides` (baseline = {}).

    La config COMPLETA (default + overrides + vincolo densità) viene applicata
    ai worker del pool: ogni valutazione parte da uno stato pulito, quindi il
    risultato non dipende dall'ordine/accumulo delle valutazioni precedenti.

    NOTA: DISTANCE_THRESHOLD_M viene passato esplicitamente a
    find_strava_segments_in_track perché il default della firma è vincolato
    al momento della definizione della funzione (non legge il modulo a runtime).
    """
    t0 = time.perf_counter()

    full_cfg = dict(DEFAULTS)
    full_cfg.update(overrides)
    _fix_density_constraint(full_cfg)

    distance_threshold = float(full_cfg["DISTANCE_THRESHOLD_M"])
    if _DETAILED:
        # Configurazione completa per debugging: identica a quella applicata
        # ai worker del pool (stessa chiave `full_cfg`).
        print(
            f"    [evaluate] valutazione config={full_cfg} "
            f"(dist={distance_threshold})"
        )

    # Aggregazione dei contatori per traccia (in parallelo o in fallback sequenziale)
    counters = _evaluate_counters(
        full_cfg, activity_tracks, strava, segments,
        distance_threshold,
    )

    total_seg_strava = sum(c["n_strava"] for c in counters)
    total_seg_algo = sum(c["n_algo"] for c in counters)
    total_missing = sum(c["missing"] for c in counters)
    total_extra = sum(c["extra"] for c in counters)
    matched_ok = sum(c["matched_ok"] for c in counters)
    matched_small = sum(c["matched_small"] for c in counters)
    matched_off = sum(c["matched_off"] for c in counters)
    total_paired = sum(c["paired"] for c in counters)
    sum_abs_delta_s = sum(c["sum_abs"] for c in counters)
    sum_sq_delta_s = sum(c["sum_sq"] for c in counters)

    recall_pct = (
        100.0 * (total_seg_strava - total_missing) / total_seg_strava
        if total_seg_strava > 0 else 0.0
    )
    precision_pct = (
        100.0 * (total_seg_algo - total_extra) / total_seg_algo
        if total_seg_algo > 0 else 0.0
    )
    mean_abs_delta = sum_abs_delta_s / total_paired if total_paired > 0 else 0.0
    # Accuratezza temporale sul delta RMS: formula identica a quella di
    # test/confronto_segmenti.py (penalizza quadraticamente gli scostamenti
    # grandi; scosti opposti non si compensano).
    rms_delta = math.sqrt(sum_sq_delta_s / total_paired) if total_paired > 0 else 0.0
    time_acc_pct = (
        max(0.0, 100.0 * (1.0 - rms_delta / TIME_TOLERANCE_S))
        if total_paired > 0 else 0.0
    )
    quality = (
        SCORE_W_RECALL * recall_pct
        + SCORE_W_PRECISION * precision_pct
        + SCORE_W_TIME * time_acc_pct
    )
    if _DETAILED:
        print(
            f"    [evaluate] risultato: Q={quality:6.2f} rec={recall_pct:5.1f}% "
            f"prec={precision_pct:5.1f}% time={time_acc_pct:5.1f}% "
            f"paired={total_paired} missing={total_missing} extra={total_extra} "
            f"delta_rms={rms_delta:.2f}s"
        )

    return Metrics(
        quality=quality, recall_pct=recall_pct, precision_pct=precision_pct,
        time_acc_pct=time_acc_pct, missing=total_missing, extra=total_extra,
        paired=total_paired, mean_abs_delta=mean_abs_delta, rms_delta=rms_delta,
        matched_ok=matched_ok, matched_small=matched_small, matched_off=matched_off,
        config=full_cfg, elapsed_s=time.perf_counter() - t0,
    )


def describe_diff(baseline: Metrics, best: Metrics) -> Dict[str, Tuple[float, float]]:
    """Differenza (base→ottimo) per ogni parametro modificato."""
    diff = {}
    for name in PARAM_NAMES:
        b = baseline.config.get(name)
        v = best.config.get(name)
        if b is not None and v is not None and abs(float(b) - float(v)) > 1e-12:
            diff[name] = (float(b), float(v))
    return diff


# ---------------------------------------------------------------------------
# MOTORI DI RICERCA — CLASSICI (coordinate descent + random search)
# ---------------------------------------------------------------------------
def coordinate_descent(
    base_config: Dict[str, float],
    activity_tracks: List[Tuple[str, Track]],
    strava: Dict[str, List[Tuple[str, int, str]]],
    segments: List[dict],
    rounds: int,
    params_order: List[str],
    quiet: bool,
) -> Metrics:
    """Greedy one-at-a-time: per ogni parametro prova tutte le griglie."""
    best = evaluate(base_config, activity_tracks, strava, segments)
    if not quiet:
        print(f"    [coord. descent] baseline → {best.short()}")
    if _DETAILED:
        print("    [coord. descent] inizio discesa greedy")

    current = dict(base_config)
    for rnd in range(1, rounds + 1):
        improved_once = False
        for name in params_order:
            grid = SEARCH_SPACE.get(name, [])
            if len(grid) <= 1:
                continue
            for value in grid:
                if abs(float(value) - float(current.get(name, 0))) < 1e-12:
                    continue
                candidate = dict(current)
                candidate[name] = value
                m = evaluate(candidate, activity_tracks, strava, segments)
                if not quiet:
                    print(
                        f"      rnd{rnd} {name:<28} = {value!s:<6} → Q={m.quality:6.1f} "
                        f"(miglior Q={best.quality:6.1f})"
                    )
                if m.quality > best.quality + 1e-9:
                    best = m
                    current = candidate
                    improved_once = True
                    if _DETAILED:
                        print(f"      [coord. descent] >>> nuovo best Q={m.quality:6.1f} per {name}={value!s}")
            if not quiet and rnd == 1:
                print()
        if not improved_once:
            if not quiet:
                print(f"    [coord. descent] nessun miglioramento nel round {rnd}; stop.")
            break
        if not quiet:
            print(f"    [coord. descent] fine round {rnd} → {best.short()}")
    return best


def random_search(
    base_config: Dict[str, float],
    activity_tracks: List[Tuple[str, Track]],
    strava: Dict[str, List[Tuple[str, int, str]]],
    segments: List[dict],
    n_iter: int,
    seed: int,
    quiet: bool,
) -> Metrics:
    """Esplorazione stocastica riproducibile su tutte le griglie."""
    rng = random.Random(seed)
    best = evaluate(base_config, activity_tracks, strava, segments)
    if _DETAILED:
        print(f"    [random search] inizio ({n_iter} iterazioni, seed={seed})")

    searchable = [n for n, g in SEARCH_SPACE.items() if len(g) > 1]
    for it in range(1, n_iter + 1):
        candidate = dict(base_config)
        for name in searchable:
            candidate[name] = rng.choice(SEARCH_SPACE[name])
        m = evaluate(candidate, activity_tracks, strava, segments)
        if not quiet:
            marker = " *" if m.quality > best.quality + 1e-9 else ""
            print(f"      random {it:>3}/{n_iter} → Q={m.quality:6.1f}{marker}")
        if m.quality > best.quality + 1e-9:
            best = m
            if _DETAILED:
                print(f"      [random search] >>> nuovo best Q={m.quality:6.1f}")
    return best


# ---------------------------------------------------------------------------
# MOTORE DI RICERCA — ADAPTIVE MULTI-PARAMETER BATCH SEARCH (AMBS)
# ---------------------------------------------------------------------------
def _nearest_grid_index(grid: List[float], value: float) -> int:
    """Indice nella griglia del valore più vicino a `value`."""
    return min(range(len(grid)), key=lambda i: abs(float(grid[i]) - float(value)))


def adaptive_multiparam_search(
    base_config: Dict[str, float],
    activity_tracks: List[Tuple[str, Track]],
    strava: Dict[str, List[Tuple[str, int, str]]],
    segments: List[dict],
    max_batches: int,
    batch_size: int,
    patience: int,
    excluded_params: Optional[List[str]] = None,
    min_mutate: int = 2,
    max_mutate: int = 5,
    single_frac: float = 0.35,
    window: int = 5,
    window_eps: float = 0.05,
    restarts: int = 1,
    seed: int = 42,
    quiet: bool = False,
) -> Tuple[Metrics, Dict[str, object]]:
    """Ricerca evolutiva adattiva multi-parametro (range dinamico + stop su convergenza).

    - Ogni batch genera `batch_size` candidati.
    - ~`single_frac` (default 35%) dei candidati muta UN SOLO parametro
      (potere di affinamento locale del coordinate descent), il resto muta
      `min_mutate`..`max_mutate` parametri INSIEME (esplorazione delle
      interazioni, richiesta chiave: non uno alla volta).
    - Ogni parametro ha un proprio sigma (raggio in passi di griglia) che
      parte ampio e si adatta con la regola 1/5 sulle finestre recenti:
      tasso di successo (su ultimi batch) > 20% → sigma ×1.25;
      < 15% → sigma ×0.75.
    - `excluded_params` (tipicamente i parametri non-sensibili rilevati dal
      dead-param test) vengono esclusi SUBITO dalle mutazioni.
    - I parametri mai coinvolti in un miglioramento dopo un warmup vengono
      esclusi dalle mutazioni successive (riduce il rumore).
    - `restarts` (default 1): se > 1, la ricerca riparte da punti casuali
      diversi per sfuggire agli ottimi locali; il migliore viene conservato.
    - Stop automatico: pazienza (batch consecutivi senza miglioramento),
      finestra (miglioramento cumulativo < window_eps), sigma minimo, budget.

    Returns:
        (best, info) dove info contiene diagnostica: motivo stop, batch,
        valutazioni, sigma finali, parametri esclusi/attivi.
    """
    rng = random.Random(seed)

    # Griglie utilizzabili (parametri con più di un valore)
    grids: Dict[str, List[float]] = {
        name: list(SEARCH_SPACE[name])
        for name in PARAM_NAMES
        if len(SEARCH_SPACE.get(name, [])) > 1
    }

    # Parametri già noti come non-sensibili (dal dead-param test) → esclusi subito
    excluded_set = set(excluded_params or []) & set(grids.keys())

    best = evaluate(base_config, activity_tracks, strava, segments)
    n_evals = 1
    best_config = dict(base_config)
    all_improved = 0
    all_batches = 0
    stop_reason = "budget massimo raggiunto"
    final_active: set = set()
    final_excluded: set = excluded_set
    sigma: Dict[str, float] = {}

    if not quiet:
        print(
            f"    [adaptive] batch={batch_size}, max_batches={max_batches}, "
            f"mutazioni={min_mutate}-{max_mutate} multi + 1 singole ({single_frac:.0%}), "
            f"pazienza={patience}, restarts={restarts}, window={window}, "
            f"window_eps={window_eps}, seed={seed}"
        )
        print(f"    [adaptive] baseline → {best.short()}")
    if _DETAILED:
        print("    [adaptive] inizio ricerca adattiva")

    def _idx_to_config(cand_idx: Dict[str, int], base: Dict[str, float]) -> Dict[str, float]:
        cfg = dict(base)
        for name, idx in cand_idx.items():
            cfg[name] = grids[name][idx]
        return cfg

    for restart in range(1, restarts + 1):
        # Punto di partenza: baseline (restart 1) o casuale (restart > 1)
        if restart == 1:
            start_config = dict(base_config)
        else:
            start_config = dict(base_config)
            for name in grids:
                if name in excluded_set:
                    continue
                start_config[name] = rng.choice(grids[name])
            if not quiet:
                print(f"    [adaptive] restart {restart}: punto di partenza casuale")

        # Stato per parametro: indice corrente nella griglia e sigma (in passi)
        grid_idx: Dict[str, int] = {}
        sigma: Dict[str, float] = {}
        for name, grid in grids.items():
            grid_idx[name] = _nearest_grid_index(grid, start_config.get(name, DEFAULTS[name]))
            sigma[name] = max(1.0, len(grid) / 2.0)

        active = set(grids.keys()) - excluded_set
        rates_window: Dict[str, List[float]] = {name: [] for name in grids}
        param_hits: Dict[str, int] = {}
        param_mut_total: Dict[str, int] = {}

        improved_hist: List[bool] = []
        batch_deltas: List[float] = []
        consec_no_improve = 0
        batch_count = 0
        restart_best = evaluate(start_config, activity_tracks, strava, segments)
        n_evals += 1
        restart_best_idx = dict(grid_idx)

        def _make_candidate(
            base_idx: Dict[str, int],
            single: bool,
        ) -> Tuple[Dict[str, int], List[str]]:
            """Genera un candidato: 1 parametro oppure 2-5 insieme (range dinamico)."""
            cand = dict(base_idx)
            pool = [n for n in active if sigma[n] >= 1.0]
            if not pool:
                return cand, []
            if single:
                k = 1
            else:
                k = rng.randint(min_mutate, max_mutate)
                if k > len(pool):
                    k = len(pool)
            chosen = rng.sample(pool, k)
            for name in chosen:
                grid = grids[name]
                s = int(sigma[name])
                lo = max(0, base_idx[name] - s)
                hi = min(len(grid) - 1, base_idx[name] + s)
                cand[name] = rng.randint(lo, hi)
            return cand, chosen

        for b in range(1, max_batches + 1):
            q_before = restart_best.quality
            batch_hits: Dict[str, int] = {}
            batch_muts: Dict[str, int] = {}

            for it in range(batch_size):
                single = (it / batch_size) < single_frac
                cand_idx, chosen = _make_candidate(restart_best_idx, single=single)
                if not chosen:
                    continue
                cfg = _idx_to_config(cand_idx, start_config)
                m = evaluate(cfg, activity_tracks, strava, segments)
                n_evals += 1
                for name in chosen:
                    batch_muts[name] = batch_muts.get(name, 0) + 1
                    param_mut_total[name] = param_mut_total.get(name, 0) + 1
                if m.quality > restart_best.quality + 1e-9:
                    restart_best = m
                    restart_best_idx = cand_idx
                    if _DETAILED:
                        print(f"      [adaptive] batch {b} it {it+1}: nuovo best Q={m.quality:6.1f}")
                    for name in chosen:
                        batch_hits[name] = batch_hits.get(name, 0) + 1
                        param_hits[name] = param_hits.get(name, 0) + 1

            delta = restart_best.quality - q_before
            batch_deltas.append(delta)
            improved_hist.append(delta > 1e-9)
            batch_count = b

            # --- Adattamento sigma per parametro (regola 1/5 su finestra recente) ---
            for name in list(active):
                muts = batch_muts.get(name, 0)
                rate = batch_hits.get(name, 0) / muts if muts > 0 else 0.0
                rates_window[name].append(rate)
                rates_window[name] = rates_window[name][-5:]
                if not rates_window[name]:
                    continue
                smooth = sum(rates_window[name]) / len(rates_window[name])
                if smooth > 0.2:
                    scale = 1.25
                elif smooth < 0.15:
                    scale = 0.75
                else:
                    scale = 1.0
                sigma[name] = max(1.0, min(len(grids[name]) - 1, sigma[name] * scale))
                if (
                    smooth == 0.0
                    and sigma[name] <= 1.0
                    and param_mut_total.get(name, 0) >= batch_size
                ):
                    active.discard(name)
                    if not quiet:
                        print(f"      [adaptive] escluso {name} (mai in un miglioramento)")

            if not quiet:
                avg_sigma = sum(sigma.values()) / len(sigma) if sigma else 0.0
                marker = "▲" if delta > 1e-9 else "·"
                print(
                    f"      restart{restart} batch {b:>2}/{max_batches}  "
                    f"Q={restart_best.quality:6.1f}  {marker}  Δ=+{delta:.2f}  "
                    f"σ_medio={avg_sigma:4.1f}  attivi={len(active)}/{len(grids)}"
                )

            # --- Criteri di stop automatico ---
            if delta <= 1e-9:
                consec_no_improve += 1
            else:
                consec_no_improve = 0

            if consec_no_improve >= patience:
                stop_reason = f"pazienza: {patience} batch consecutivi senza miglioramento"
                break
            if (
                len(batch_deltas) >= window
                and sum(batch_deltas[-window:]) < window_eps
            ):
                stop_reason = (
                    f"finestra: miglioramento cumulativo < {window_eps:.2f} "
                    f"sugli ultimi {window} batch"
                )
                break
            if all(sigma[n] <= 1.0 for n in active) and delta <= 1e-9:
                stop_reason = "sigma minimo raggiunto senza miglioramento"
                break

        all_batches += batch_count
        all_improved += sum(1 for f in improved_hist if f)
        final_active = active
        final_excluded = excluded_set | (set(grids) - active)

        # Conserva il migliore tra i restart
        if restart_best.quality > best.quality + 1e-9:
            best = restart_best
            best_config = _idx_to_config(restart_best_idx, start_config)
            if not quiet:
                print(f"    [adaptive] restart {restart}: nuovo miglioramento → {best.short()}")
            if _DETAILED and restart_best.quality > best.quality + 1e-9:
                print(f"    [adaptive] restart {restart}: nuovo best globale Q={best.quality:6.1f}")

    return best, {
        "method": "adaptive",
        "stop_reason": stop_reason,
        "batches": all_batches,
        "batch_size": batch_size,
        "n_evals": n_evals,
        "improved_batches": all_improved,
        "sigma_final": {name: float(sigma[name]) for name in grids},
        "attivi": sorted(final_active),
        "esclusi": sorted(final_excluded),
    }


def polish_pass(
    base_config: Dict[str, float],
    activity_tracks: List[Tuple[str, Track]],
    strava: Dict[str, List[Tuple[str, int, str]]],
    segments: List[dict],
    quiet: bool = False,
    max_rounds: int = 3,
) -> Tuple[Metrics, int]:
    """Affinamento locale continuo: prova ±passo di risoluzione per ogni
    parametro (e multipli, con granularità decrescente round dopo round),
    sempre dentro i suoi bounds.

    A differenza della versione a griglia, il passo è continuo (~0.4% del
    range; 1 unità per gli interi), quindi verifica l'ottimalità locale anche
    in punti che non appartengono a nessun elenco predefinito di valori.
    """
    best = evaluate(base_config, activity_tracks, strava, segments)
    n_evals = 1
    current = dict(best.config)

    for rnd in range(1, max_rounds + 1):
        improved = False
        for name in PARAM_NAMES:
            if name not in PARAM_BOUNDS:
                continue
            base_step = param_min_step(name)
            step = max(base_step, base_step * 2.0 ** (1 - rnd))
            for sgn in (-1.0, 1.0):
                cur_val = float(current.get(name, DEFAULTS[name]))
                v = _clip_bound(name, cur_val + sgn * step)
                if abs(v - cur_val) < 1e-12:
                    continue
                cand = dict(current)
                cand[name] = v
                m = evaluate(cand, activity_tracks, strava, segments)
                n_evals += 1
                if m.quality > best.quality + 1e-9:
                    best = m
                    current = dict(m.config)
                    improved = True
                    if _DETAILED:
                        print(f"      [polish] >>> nuovo best Q={m.quality:6.1f} (round {rnd})")
        if not quiet:
            print(f"      [polish] round {rnd} → {best.short()}")
        elif _DETAILED:
            print(f"      [polish] round {rnd}: nessun miglioramento")
        if not improved:
            break
    return best, n_evals


# ---------------------------------------------------------------------------
# DETECTION PARAMETRI "MORTI"
# ---------------------------------------------------------------------------
def detect_dead_params(
    activity_tracks: List[Tuple[str, Track]],
    strava: Dict[str, List[Tuple[str, int, str]]],
    segments: List[dict],
    quiet: bool,
) -> Tuple[List[str], List[str]]:
    """Distingue i parametri in due categorie.

    Returns:
        (non_usati, non_sensibili):
          - non_usati: parametri la cui griglia è vuota → il codice non li legge MAI
          - non_sensibili: parametri che su QUESTO dataset non modificano il
            punteggio provando il min e il max della griglia (potrebbero essere
            usati dal codice ma i dati non ne evidenziano l'effetto).
    """
    non_usati: List[str] = []
    non_sensibili: List[str] = []
    baseline = evaluate({}, activity_tracks, strava, segments)
    for name in PARAM_NAMES:
        if name in PARAM_BOUNDS:
            # Range continuo: proviamo gli estremi (più 1 medio se ha senso)
            blo, bhi = PARAM_BOUNDS[name]
            samples = [float(blo), float(bhi)]
            if bhi - blo > 2.0 * param_min_step(name):
                samples.insert(1, (blo + bhi) / 2.0)
        else:
            grid = SEARCH_SPACE.get(name, [])
            if not grid:
                # Parametro senza range né griglia: campione molto diverso dal default.
                base_val = DEFAULTS[name]
                sample = base_val * 2 + 1 if isinstance(base_val, float) else base_val + 3
                samples = [float(sample)]
            else:
                # Proviamo il minimo e il massimo della griglia (più 1 medio se c'è)
                samples = [float(grid[0]), float(grid[-1])]
                if len(grid) > 2:
                    samples.append(float(grid[len(grid) // 2]))

        insensibile = True
        for sample in samples:
            m = evaluate({name: sample}, activity_tracks, strava, segments)
            if _DETAILED:
                tag = "SENSIBILE" if abs(m.quality - baseline.quality) >= 1e-9 else "insensibile"
                print(f"      [dead-params] {name} prova {sample} → Q={m.quality:6.2f} ({tag})")
            if abs(m.quality - baseline.quality) >= 1e-9:
                insensibile = False
                break

        if insensibile:
            # "non usato" = né range continuo né griglia (il codice non lo legge);
            # con i range definiti per tutti i parametri, resta un caso limite.
            if name not in PARAM_BOUNDS and not SEARCH_SPACE.get(name):
                non_usati.append(name)
            else:
                non_sensibili.append(name)
    return non_usati, non_sensibili


# ---------------------------------------------------------------------------
# REPORT
# ---------------------------------------------------------------------------
def print_report(
    baseline: Metrics,
    best: Metrics,
    dead: Tuple[List[str], List[str]],
    elapsed: float,
    n_evals: int,
    conv: Optional[Dict[str, object]] = None,
) -> None:
    # `dead` ora è (non_usati, non_sensibili)
    non_usati, non_sensibili = dead
    print("\n" + "=" * 78)
    print("REPORT OTTIMIZZAZIONE PARAMETRI core/strava_analyzer.py")
    print("=" * 78)
    print("\nBaseline (parametri attuali):")
    print(f"  {baseline.short()}")
    print("\nOttimo trovato:")
    print(f"  {best.short()}")

    diff = describe_diff(baseline, best)
    if diff:
        print("\nParametri modificati (baseline → ottimo):")
        for name, (b, v) in diff.items():
            print(f"  {name:<28} {b!s:<24} → {v!s}")
    else:
        print("\nNessun parametro modificato: la baseline è già l'ottimo locale trovato.")

    print("\nConfigurazione ottima completa:")
    for name in PARAM_NAMES:
        marker = "  ← modificato" if name in diff else ""
        print(f"  {name:<28} = {best.config[name]!s:<10}{marker}")

    if conv:
        print("\nConvergenza ricerca:")
        print(f"  Metodo: {conv.get('method', '?')}")
        print(f"  Motivo stop: {conv.get('stop_reason', '?')}")
        print(
            f"  Batch eseguiti: {conv.get('batches', '?')} "
            f"(di cui {conv.get('improved_batches', '?')} migliorativi; "
            f"candidati/batch: {conv.get('batch_size', '?')})"
        )
        esclusi: List[str] = cast(List[str], conv.get("esclusi", []))
        if esclusi:
            print("  Parametri esclusi dalla mutazione (mai in un miglioramento):")
            for name in esclusi:
                print(f"    · {name}")
        attivi: List[str] = cast(List[str], conv.get("attivi", []))
        if attivi:
            print(f"  Parametri attivi alla convergenza ({len(attivi)}):")
            print(f"    {' '.join(attivi)}")

    if non_usati:
        print("\nParametri NON USATI dal codice (griglia vuota, mai letti a runtime):")
        for name in non_usati:
            print(f"  ⚠ {name}")
        print("  → Definirli in core/strava_analyzer.py non ha effetto. Verifica il codice.")
    if non_sensibili:
        print("\nParametri non sensibili su questo dataset (nessuna variazione di punteggio):")
        for name in non_sensibili:
            print(f"  · {name}")
        print("  → Sono applicati, ma i dati attuali non evidenziano il loro effetto.")
    if not non_usati and not non_sensibili:
        print("\nNessun parametro morto/sordo rilevato.")

    print(f"\nTempo totale ottimizzazione: {elapsed:.1f} s")
    print(f"Valutazioni totali effettuate: ~{n_evals}")

    print("\nMetriche dettagliate:")
    for label, m in (("Baseline", baseline), ("Ottimo", best)):
        print(f"  {label}: recall={m.recall_pct:.1f}%  precision={m.precision_pct:.1f}%  "
              f"time_acc={m.time_acc_pct:.1f}%  ΔRMS={m.rms_delta:.2f}s  Δmedio={m.mean_abs_delta:.2f}s  "
              f"mancanti={m.missing}  extra={m.extra}  "
              f"entro3s={m.matched_ok}  entro15s={m.matched_small}  oltre15s={m.matched_off}")


# ---------------------------------------------------------------------------
# MOTORE DI RICERCA — CONTINUA COARSE-TO-FINE (refine, default)
# ---------------------------------------------------------------------------
def _linspace(lo: float, hi: float, k: int) -> List[float]:
    """K valori equispaziati (estremi inclusi) su [lo, hi]."""
    lo_f, hi_f = float(lo), float(hi)
    if k <= 1:
        return [lo_f]
    step = (hi_f - lo_f) / (k - 1)
    return [lo_f + i * step for i in range(k)]


def refine_search(
    base_config: Dict[str, float],
    activity_tracks: List[Tuple[str, Track]],
    strava: Dict[str, List[Tuple[str, int, str]]],
    segments: List[dict],
    sweep_points: int = 5,
    n_explore: int = 30,
    max_rounds: int = 12,
    batch_size: int = 14,
    patience: int = 4,
    min_mutate: int = 2,
    max_mutate: int = 5,
    single_frac: float = 0.35,
    seed: int = 42,
    quiet: bool = False,
) -> Tuple[Metrics, Dict[str, object]]:
    """Ricerca continua coarse-to-fine sui range continui di PARAM_BOUNDS.

    Fasi:
      A1 sweep 1-D      : `sweep_points` valori per parametro su TUTTO il range
                          (nessun valore prestabilito); i parametri mai
                          sensibili vengono esclusi dalle fasi successive;
      A2 greedy continua: applica in sequenza il miglior punto di sweep di
                          ogni parametro, conservandolo solo se migliora;
      A3 esplorazione   : `n_explore` configurazioni Latin Hypercube che
                          variano TUTTI i parametri attivi insieme;
      B  affinamento    : intervalli centrati sul best corrente, ristretti ad
                          ogni round con fattore adattivo; i candidati mutano
                          UN parametro (~single_frac) o 2..max_mutate insieme,
                          campionando UNIFORMEMENTE dentro l'intervallo →
                          l'ottimo tra due valori qualsiasi è raggiungibile.
                          Stop quando ogni intervallo ≤ risoluzione minima
                          (interi: passo 1), oppure su pazienza/budget.

    Returns:
        (best, info): info contiene la diagnostica completa della ricerca.
    """
    rng = random.Random(seed)

    bounds: Dict[str, Tuple[float, float]] = {n: PARAM_BOUNDS[n] for n in PARAM_NAMES}
    frozen = {
        name for name, (blo, bhi) in bounds.items()
        if bhi - blo <= param_min_step(name)
    }
    excluded: set = set(frozen)
    n_evals = 0

    start = dict(base_config)
    best = evaluate(start, activity_tracks, strava, segments)
    n_evals += 1
    q_ref = best.quality

    if not quiet:
        fuori = [
            name for name in PARAM_NAMES
            if not (bounds[name][0] - 1e-12 <= float(DEFAULTS[name]) <= bounds[name][1] + 1e-12)
        ]
        print(
            f"    [refine] sweep={sweep_points}pt/param, explore={n_explore}, "
            f"rounds<={max_rounds}, batch={batch_size}, pazienza={patience}, seed={seed}"
        )
        if fuori:
            print(
                f"    [refine] ⚠ default fuori dai range (non esplorati): "
                f"{', '.join(sorted(fuori))}"
            )
        print(f"    [refine] baseline → {best.short()}")

    # --- Fase A1: sweep 1-D su tutto il range ---
    sweep_best: Dict[str, Tuple[float, float]] = {}
    sweep_delta: Dict[str, float] = {}
    if not quiet:
        print("    [refine] fase A1: sweep 1-D...")
    for name in sorted(set(bounds) - frozen):
        blo, bhi = bounds[name]
        step = param_min_step(name)
        cur = float(start.get(name, DEFAULTS[name]))
        v_best = cur
        q_best = q_ref
        delta_max = 0.0
        for p in _linspace(blo, bhi, sweep_points):
            pv = _clip_bound(name, p)
            if abs(pv - cur) < max(step / 2.0, 1e-12):
                continue
            m = evaluate({name: pv}, activity_tracks, strava, segments)
            n_evals += 1
            if m.quality > q_best + 1e-9:
                q_best = m.quality
                v_best = pv
            delta_max = max(delta_max, abs(m.quality - q_ref))
        sweep_best[name] = (v_best, q_best)
        sweep_delta[name] = delta_max
        if delta_max < 1e-9:
            excluded.add(name)
        if not quiet:
            # Diagnostica: chiarisce che si tiene il MIGLIOR punto dello sweep,
            # non l'ultimo testato (la scansione spesso mostra Q decrescenti).
            if q_best > q_ref + 1e-9:
                esito = (
                    f"→ scelto {name}={v_best:.4g} (Q={q_best:.2f}) — "
                    f"il migliore tra i punti testati (baseline Q={q_ref:.2f})"
                )
            else:
                esito = (
                    f"→ tenuto baseline {name}={cur:.4g} (Q={q_ref:.2f}): "
                    f"nessun punto testato migliora"
                )
            print(
                f"    [refine]   A1 {name:<28} Δmax={sweep_delta[name]:6.2f}  {esito}"
            )
    if not quiet:
        sensibili = sorted(set(sweep_best) - excluded, key=lambda n: -sweep_delta[n])
        top = ", ".join(f"{n} ({sweep_delta[n]:.2f})" for n in sensibili[:5])
        print(
            f"    [refine] fase A1: {len(excluded)} parametri non sensibili esclusi; "
            f"top sensibilità: {top}"
        )

    # --- Fase A2: discesa greedy continua sui migliori punti dello sweep ---
    if not quiet:
        print("    [refine] fase A2: discesa greedy...")
    for name in sorted(set(sweep_best) - excluded, key=lambda n: -sweep_delta[n]):
        v = sweep_best[name][0]
        if abs(v - float(start.get(name, DEFAULTS[name]))) < 1e-12:
            continue
        cand = dict(start)
        cand[name] = v
        _fix_density_constraint(cand)
        m = evaluate(cand, activity_tracks, strava, segments)
        n_evals += 1
        if m.quality > best.quality + 1e-9:
            best = m
            start = cand
            if _DETAILED:
                print(f"      [refine] >>> nuovo best Q={m.quality:6.1f} (greedy fase A2)")

    # --- Fase A3: esplorazione congiunta (Latin Hypercube) ---
    lhs_names = sorted(set(bounds) - excluded)
    if n_explore > 0 and lhs_names:
        if not quiet:
            print(f"    [refine] fase A3: esplorazione LHS ({n_explore} configurazioni)...")
        perms = {name: rng.sample(range(n_explore), n_explore) for name in lhs_names}
        for i in range(n_explore):
            row: Dict[str, float] = {}
            for name in lhs_names:
                blo, bhi = bounds[name]
                row[name] = _clip_bound(
                    name, blo + (perms[name][i] + rng.random()) / n_explore * (bhi - blo)
                )
            cand = dict(start)
            cand.update(row)
            _fix_density_constraint(cand)
            m = evaluate(cand, activity_tracks, strava, segments)
            n_evals += 1
            if m.quality > best.quality + 1e-9:
                best = m
                if not quiet:
                    print(f"      [refine] LHS {i + 1}/{n_explore}: nuovo best → {best.short()}")
                elif _DETAILED:
                    print(f"      [refine] >>> LHS {i + 1}/{n_explore}: nuovo best Q={m.quality:6.1f}")

    # --- Fase B: affinamento iterativo (zoom verso i miglioramenti) ---
    centers: Dict[str, float] = {
        name: float(best.config.get(name, DEFAULTS[name])) for name in lhs_names
    }
    widths = {name: bounds[name][1] - bounds[name][0] for name in centers}
    span_tot = dict(widths)
    min_w = {name: param_min_step(name) for name in centers}
    rates_hist: Dict[str, List[float]] = {name: [] for name in centers}
    mut_tot: Dict[str, int] = {name: 0 for name in centers}
    hit_tot: Dict[str, int] = {name: 0 for name in centers}

    stop_reason = "budget round raggiunto"
    rounds_done = 0
    improved_rounds = 0
    consec_no = 0

    if not quiet:
        print(f"    [refine] fase B: affinamento iterativo su {len(centers)} parametri")

    for rnd in range(1, max_rounds + 1):
        rounds_done = rnd
        q_before = best.quality
        ref_cfg = dict(best.config)
        batch_hits: Dict[str, int] = {}
        batch_muts: Dict[str, int] = {}

        for _ in range(batch_size):
            pool = sorted(centers)
            if not pool:
                break
            single = rng.random() < single_frac
            k = 1 if single else min(len(pool), rng.randint(min_mutate, max_mutate))
            chosen = rng.sample(pool, k)
            cand = dict(ref_cfg)
            for name in chosen:
                c = centers[name]
                half = widths[name] / 2.0
                clo = max(bounds[name][0], c - half)
                chi = min(bounds[name][1], c + half)
                cand[name] = _clip_bound(name, rng.uniform(clo, chi))
            _fix_density_constraint(cand)
            mutated = [
                name for name in PARAM_NAMES
                if abs(float(cand.get(name, DEFAULTS[name]))
                       - float(ref_cfg.get(name, DEFAULTS[name]))) > 1e-12
            ]
            if not mutated:
                continue
            m = evaluate(cand, activity_tracks, strava, segments)
            n_evals += 1
            for name in mutated:
                batch_muts[name] = batch_muts.get(name, 0) + 1
                mut_tot[name] = mut_tot.get(name, 0) + 1
            if m.quality > best.quality + 1e-9:
                best = m
                for name in mutated:
                    batch_hits[name] = batch_hits.get(name, 0) + 1
                    hit_tot[name] = hit_tot.get(name, 0) + 1
                if _DETAILED:
                    print(f"      [refine] >>> nuovo best Q={m.quality:6.1f} (fase B)")

        # Restringimento adattivo degli intervalli + recentro sul best corrente
        drops: List[str] = []
        for name in list(centers):
            muts = batch_muts.get(name, 0)
            rate = batch_hits.get(name, 0) / muts if muts > 0 else 0.0
            hist = rates_hist[name]
            hist.append(rate)
            rates_hist[name] = hist[-3:]
            smooth = sum(rates_hist[name]) / len(rates_hist[name])
            if smooth > 0.20:
                factor = 0.85  # successo alto: restringe lentamente
            elif smooth < 0.08:
                factor = 0.35  # nessun successo: collassa rapidamente
            else:
                factor = 0.60
            widths[name] = max(min_w[name], widths[name] * factor)
            centers[name] = float(best.config.get(name, DEFAULTS[name]))
            if rnd >= 3 and mut_tot[name] >= batch_size and hit_tot[name] == 0:
                drops.append(name)
        for name in drops:
            if not quiet:
                print(f"      [refine] escluso {name} (mai in un miglioramento)")
            centers.pop(name, None)
            widths.pop(name, None)
            rates_hist.pop(name, None)

        delta = best.quality - q_before
        if delta > 1e-9:
            improved_rounds += 1
            consec_no = 0
        else:
            consec_no += 1

        if not quiet:
            avg_rel = (
                sum(widths[n] / span_tot[n] for n in widths) / len(widths) if widths else 0.0
            )
            marker = "▲" if delta > 1e-9 else "·"
            print(
                f"      round {rnd:>2}/{max_rounds}  Q={best.quality:6.2f}  {marker}  "
                f"Δ=+{delta:.3f}  larghezza_rel media={avg_rel:6.1%}  attivi={len(centers)}"
            )

        # Criteri di stop
        if not centers:
            stop_reason = "nessun parametro attivo residuo"
            break
        if all(widths[name] <= min_w[name] * 1.001 for name in widths):
            stop_reason = "risoluzione minima raggiunta su tutti i parametri"
            break
        if consec_no >= patience:
            stop_reason = f"pazienza: {patience} round consecutivi senza miglioramento"
            break

    return best, {
        "method": "refine",
        "stop_reason": stop_reason,
        "batches": rounds_done,
        "improved_batches": improved_rounds,
        "batch_size": batch_size,
        "n_evals": n_evals,
        "sweep_points": sweep_points,
        "larghezze_finali": {name: widths[name] / span_tot[name] for name in widths},
        "attivi": sorted(centers),
        "esclusi": sorted(excluded | (set(bounds) - set(centers))),
    }

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--quick", action="store_true", help="Ottimizzazione veloce")
    parser.add_argument("--deep", action="store_true", help="Ottimizzazione approfondita")
    parser.add_argument("--baseline", action="store_true", help="Solo valutazione baseline + dead-param")
    parser.add_argument(
        "--method", choices=["refine", "adaptive", "classic"], default="refine",
        help="Motore di ricerca (default: refine, continua coarse-to-fine)",
    )
    parser.add_argument("--export", type=str, default=None, metavar="FILE",
                        help="Esporta i parametri ottimi in JSON (es. test/params_ottimi.json)")
    parser.add_argument("--seed", type=int, default=42, help="Seed per la random search")
    parser.add_argument("-q", "--quiet", action="store_true", help="Output compatto")
    parser.add_argument("-d", "--detailed", action="store_true", help="Log dettagliato su ogni operazione")
    args = parser.parse_args()

    quiet = args.quiet or args.baseline
    global _DETAILED
    _DETAILED = args.detailed

    # Dimensioni della ricerca in base a --quick/--deep.
    # Pre-inizializziamo tutte le variabili per evitare "possibly unbound"
    # dal punto di vista dell'analizzatore statico.
    max_batches, batch_size, patience, restarts = 30, 16, 6, 2
    rounds = n_random = 2
    sweep_points, n_explore, max_rounds = 5, 30, 12
    if args.quick:
        if args.method == "refine":
            sweep_points, n_explore, max_rounds, batch_size, patience = 3, 12, 8, 10, 3
        elif args.method == "adaptive":
            max_batches, batch_size, patience, restarts = 15, 12, 4, 1
        else:
            rounds, n_random = 1, 15
    elif args.deep:
        if args.method == "refine":
            sweep_points, n_explore, max_rounds, batch_size, patience = 7, 60, 18, 20, 6
        elif args.method == "adaptive":
            max_batches, batch_size, patience, restarts = 60, 24, 8, 4
        else:
            rounds, n_random = 4, 120
    else:
        if args.method == "refine":
            sweep_points, n_explore, max_rounds, batch_size, patience = 5, 30, 12, 14, 4
        elif args.method == "adaptive":
            max_batches, batch_size, patience, restarts = 30, 16, 6, 2
        else:
            rounds, n_random = 2, 40

    # --- 1. Caricamento dati (una volta sola) ---
    print("Caricamento segmenti Strava...")
    segments = load_strava_segments(str(SEGMENTS_DIR))
    print(f"  Segmenti: {[s['name'] for s in segments]}")

    print("Caricamento tracce attività...")
    activity_tracks = load_activity_tracks()
    names = ", ".join(n for n, _ in activity_tracks[:3])
    print(f"  Tracce: {len(activity_tracks)} ({names}{'...' if len(activity_tracks) > 3 else ''})")

    print("Lettura ground truth Strava...")
    strava = parse_strava_txt(STRAVA_TXT)
    n_seg = sum(len(v) for v in strava.values())
    print(f"  Passaggi riferimento: {n_seg}")

    n_evals = 0

    # --- 2. Baseline ---
    print("\nValutazione baseline...")
    baseline = evaluate({}, activity_tracks, strava, segments)
    n_evals += 1
    print(f"  {baseline.short()}")
    print(f"  Tempo singola valutazione: {baseline.elapsed_s:.1f} s")

    if args.baseline:
        dead = detect_dead_params(activity_tracks, strava, segments, quiet)
        n_evals += 3 * len(PARAM_NAMES)
        print_report(baseline, baseline, dead, 0.0, n_evals)
        return

    # --- 3. Ricerca ---
    print("\n" + "=" * 78)
    print("RICERCA OTTIMO PARAMETRI")
    print("=" * 78)

    t_start = time.perf_counter()
    conv: Optional[Dict[str, object]] = None

    if args.method == "refine":
        # Ricerca continua coarse-to-fine: sweep 1-D → greedy → LHS → zoom.
        print("\n--- Ricerca continua coarse-to-fine (sweep 1-D → greedy → LHS → affinamento) ---")
        best, conv = refine_search(
            DEFAULTS,
            activity_tracks,
            strava,
            segments,
            sweep_points=sweep_points,
            n_explore=n_explore,
            max_rounds=max_rounds,
            batch_size=batch_size,
            patience=patience,
            seed=args.seed,
            quiet=quiet,
        )
        n_evals += int(cast(int, conv["n_evals"]))

        # Polish locale continuo (±passo di risoluzione)
        print("\n--- Polish locale continuo (±passo di risoluzione) ---")
        best, n_polish = polish_pass(best.config, activity_tracks, strava, segments, quiet)
        n_evals += n_polish
    elif args.method == "adaptive":
        # 3a. Dead-param PRELIMINARI: identifica i parametri non-sensibili da
        # escludere subito dalle mutazioni (evita di sprecare valutazioni).
        print("\nVerifica preliminare parametri non-sensibili...")
        dead_pre = detect_dead_params(activity_tracks, strava, segments, quiet)
        pre_excluded = dead_pre[1]  # non_sensibili
        n_evals += 3 * len(PARAM_NAMES)
        if not quiet and pre_excluded:
            print(f"  {len(pre_excluded)} parametri non-sensibili identificati")
        if _DETAILED and pre_excluded:
            print(f"    [main] parametri non-sensibili (prerequisito): {sorted(pre_excluded)}")

        # 3b. Adaptive multi-parameter batch search (con range dinamico e stop)
        print("\n--- Ricerca adattiva multi-parametro (AMBS) ---")
        best, conv = adaptive_multiparam_search(
            DEFAULTS,
            activity_tracks,
            strava,
            segments,
            max_batches=max_batches,
            batch_size=batch_size,
            patience=patience,
            excluded_params=pre_excluded,
            restarts=restarts,
            seed=args.seed,
            quiet=quiet,
        )
        n_evals += int(cast(int, conv["n_evals"]))

        # 3c. Polish locale
        print("\n--- Polish locale (±1 passo di griglia) ---")
        best, n_polish = polish_pass(best.config, activity_tracks, strava, segments, quiet)
        n_evals += n_polish
    else:
        # 3a. Coordinate descent (metodo classico, un parametro alla volta)
        order = CRITICAL_PARAMS + [p for p in PARAM_NAMES if p not in CRITICAL_PARAMS]
        print(f"\n--- Coordinate descent ({rounds} round) ---")
        best = coordinate_descent(
            DEFAULTS, activity_tracks, strava, segments, rounds, order, quiet
        )
        n_evals += sum(
            1 for name in order for _ in SEARCH_SPACE.get(name, [])
            if len(SEARCH_SPACE.get(name, [])) > 1
        )

        # 3b. Random search (metodo classico)
        print(f"\n--- Random search ({n_random} iterazioni, seed={args.seed}) ---")
        best = random_search(
            best.config, activity_tracks, strava, segments, n_random, args.seed, quiet
        )
        n_evals += n_random

    t_elapsed = time.perf_counter() - t_start

    # --- 4. Dead params ---
    print("\nVerifica parametri 'morti'...")
    dead = detect_dead_params(activity_tracks, strava, segments, quiet)
    n_evals += 3 * len(PARAM_NAMES)

    # --- 5. Report ---
    print_report(baseline, best, dead, t_elapsed, n_evals, conv=conv)

    # --- 6. Export JSON ---
    if args.export:
        export_path = Path(args.export)
        if not export_path.is_absolute():
            export_path = ROOT / export_path
        payload = {
            "generato": datetime.now().isoformat(timespec="seconds"),
            "metodo": args.method,
            "baseline": {
                "quality": baseline.quality,
                "recall_pct": baseline.recall_pct,
                "precision_pct": baseline.precision_pct,
                "time_acc_pct": baseline.time_acc_pct,
                "rms_delta_s": baseline.rms_delta,
                "mean_abs_delta_s": baseline.mean_abs_delta,
                "missing": baseline.missing,
                "extra": baseline.extra,
                "config": baseline.config,
            },
            "ottimo": {
                "quality": best.quality,
                "recall_pct": best.recall_pct,
                "precision_pct": best.precision_pct,
                "time_acc_pct": best.time_acc_pct,
                "rms_delta_s": best.rms_delta,
                "mean_abs_delta_s": best.mean_abs_delta,
                "missing": best.missing,
                "extra": best.extra,
                "config": best.config,
            },
            "convergenza": {
                "stop_reason": conv.get("stop_reason") if conv else None,
                "batches": conv.get("batches") if conv else None,
                "improved_batches": conv.get("improved_batches") if conv else None,
                "batch_size": conv.get("batch_size") if conv else None,
                "parametri_esclusi": conv.get("esclusi") if conv else [],
                "parametri_attivi": conv.get("attivi") if conv else [],
                "larghezze_intervalli_finali": conv.get("larghezze_finali") if conv else None,
                "punti_sweep": conv.get("sweep_points") if conv else None,
            },
            "parametri_non_usati": dead[0],
            "parametri_non_sensibili": dead[1],
            "seed": args.seed,
        }
        export_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nConfigurazione ottima esportata in: {export_path}")


if __name__ == "__main__":
    main()