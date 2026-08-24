#!/usr/bin/env python3
"""Ricerca dell'ottimo dei parametri di core/strava_analyzer.py.

Ground truth: Examples/*.gpx (solo GPX: i FIT sono rielaborati da Strava e
usati solo come indice informativo), Strava_Segments/ (segmenti),
test/esempio_tempo_segmenti.txt (tempi ufficiali Strava).

Strategia (default, --method adaptive) — Adaptive Multi-Parameter Batch
Search (AMBS):
  * dead-param PRELIMINARI: i parametri non-sensibili (test min/max) vengono
    esclusi subito dalle mutazioni (riduce il rumore);
  * ogni batch genera N candidati: ~35% muta UN SOLO parametro (affinamento
    locale), il resto muta 2-5 parametri INSIEME (esplorazione interazioni);
  * per ogni parametro il nuovo valore viene campionato da un range dinamico:
    sigma (in passi di griglia) parte ampio e si adatta con la regola 1/5
    (successo > 20% → espande, < 15% → restringe);
  * i parametri mai coinvolti in un miglioramento vengono esclusi;
  * restart da punti casuali per sfuggire agli ottimi locali;
  * stop automatico su convergenza: pazienza (batch consecutivi senza
    miglioramento), finestra (miglioramento cumulativo trascurabile),
    sigma minimo, budget massimo;
  * polish finale: ±1 passo di griglia su ogni parametro.

Strategia alternativa (--method classic): coordinate descent → random search.

Usage (dalla root):
    python test/ottimizza_parametri.py              # media (adaptive)
    python test/ottimizza_parametri.py --quick      # veloce
    python test/ottimizza_parametri.py --deep       # approfondita
    python test/ottimizza_parametri.py --baseline   # solo baseline + dead-param
    python test/ottimizza_parametri.py --export test/params_ottimi.json
    python test/ottimizza_parametri.py --method classic
    python test/ottimizza_parametri.py -q           # output compatto
"""

import argparse
import json
import math
import random
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, cast

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

# ---------------------------------------------------------------------------
# PARAMETRI OGGETTO DELL'OTTIMIZZAZIONE
# ---------------------------------------------------------------------------
PARAM_NAMES: List[str] = [
    "DISTANCE_THRESHOLD_M", "MIN_MATCH_POINTS", "START_TOL_RATIO", "END_TOL_RATIO", "MAX_GAP_RATIO",
    "CLUSTER_GAP_IDX", "PROGRESS_RATIO", "PROGRESS_SLACK_M", "MIN_DENSITY",
    "MAX_DENSITY", "MIN_LENGTH_M", "PROJECTION_WINDOW", "END_PROJECTION_EXTRA_IDX",
    "END_PROJECTION_ACCEPT_M", "END_PROJECTION_EXIT_RISE_M",
    "END_PROJECTION_MIN_IMPROVE_M",
    "START_PROJECTION_EXTRA_IDX",
    "START_PROJECTION_ACCEPT_M",
    "START_PROJECTION_EXIT_RISE_M",
    "START_PROJECTION_MIN_IMPROVE_M",
    "TRIM_REF_POINTS", "TRIM_CHECK_LIMIT", "TRIM_INDEX_GAP", "ANCHOR_SCAN_RANGE",
    "STATIONARY_SPEED_KMH", "OVERLAP_OCCUPANCY_THRESHOLD", "MAX_TOTAL_PASSES",
]

DEFAULTS: Dict[str, float] = {name: getattr(sa, name) for name in PARAM_NAMES}

# Griglie di ricerca. Griglia vuota = parametro congelato (ma verificato come dead).
SEARCH_SPACE: Dict[str, List[float]] = {
    "DISTANCE_THRESHOLD_M": [25.0, 30.0, 35.0, 40.0, 45.0, 50.0, 55.0, 60.0, 70.0],
    "MIN_MATCH_POINTS": [3, 4, 5, 6, 8],
    "START_TOL_RATIO": [0.06, 0.08, 0.10, 0.12, 0.15, 0.18, 0.20, 0.25],
    "END_TOL_RATIO": [0.06, 0.08, 0.10, 0.12, 0.15, 0.18, 0.20, 0.25],
    "MAX_GAP_RATIO": [0.08, 0.10, 0.12, 0.15, 0.20, 0.25, 0.30, 0.35],
    "CLUSTER_GAP_IDX": [20, 30, 40, 50, 60, 80],
    "PROGRESS_RATIO": [1.5, 1.75, 2.0, 2.25, 2.5, 2.75, 3.0, 3.5, 4.0],
    "PROGRESS_SLACK_M": [5.0, 10.0, 15.0, 20.0, 30.0, 40.0, 50.0, 60.0],
    "MIN_DENSITY": [0.30, 0.40, 0.45, 0.50, 0.55, 0.60, 0.70, 0.80],
    "MAX_DENSITY": [1.20, 1.30, 1.40, 1.50, 1.60, 1.70, 1.80, 2.00],
    "MIN_LENGTH_M": [1.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 40.0],
    "PROJECTION_WINDOW": [20, 30, 40, 50, 60, 70, 80, 100],
    "END_PROJECTION_EXTRA_IDX": [30, 40, 50, 60, 70, 80, 100, 120],
    "END_PROJECTION_ACCEPT_M": [25.0, 30.0, 35.0, 40.0, 45.0, 50.0, 55.0, 65.0],
    "END_PROJECTION_EXIT_RISE_M": [3.0, 4.0, 6.0, 8.0, 10.0, 12.0, 15.0, 20.0],
    "END_PROJECTION_MIN_IMPROVE_M": [0.0, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0],
    "START_PROJECTION_EXTRA_IDX": [30, 40, 50, 60, 80, 100, 120],
    "START_PROJECTION_ACCEPT_M": [20.0, 25.0, 30.0, 35.0, 40.0, 45.0, 50.0],
    "START_PROJECTION_EXIT_RISE_M": [1.0, 2.0, 3.0, 4.0, 5.0, 8.0, 10.0],
    "START_PROJECTION_MIN_IMPROVE_M": [0.0, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0],
    "TRIM_REF_POINTS": [2, 3, 4, 5, 6],
    "TRIM_CHECK_LIMIT": [10, 15, 20, 25, 30, 40, 50],
    "TRIM_INDEX_GAP": [5, 8, 10, 12, 15, 20, 25],
    "ANCHOR_SCAN_RANGE": [2, 3, 4, 5, 6, 8],
    "STATIONARY_SPEED_KMH": [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0],
    "OVERLAP_OCCUPANCY_THRESHOLD": [0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80],
    "MAX_TOTAL_PASSES": [4, 6, 8, 10, 12, 16, 20, 24],
}

# Parametri più influenti → testati per primi nel coordinate descent (classic)
CRITICAL_PARAMS = [
    "DISTANCE_THRESHOLD_M", "MIN_MATCH_POINTS", "START_TOL_RATIO", "END_TOL_RATIO", "MAX_GAP_RATIO",
    "PROGRESS_RATIO", "PROGRESS_SLACK_M", "MIN_DENSITY", "MAX_DENSITY",
    "MIN_LENGTH_M", "END_PROJECTION_ACCEPT_M", "END_PROJECTION_EXIT_RISE_M",
    "START_PROJECTION_ACCEPT_M", "START_PROJECTION_EXIT_RISE_M",
    "STATIONARY_SPEED_KMH", "OVERLAP_OCCUPANCY_THRESHOLD",
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
    "MIN_MATCH_POINTS", "CLUSTER_GAP_IDX", "PROJECTION_WINDOW",
    "END_PROJECTION_EXTRA_IDX", "START_PROJECTION_EXTRA_IDX",
    "TRIM_REF_POINTS", "TRIM_CHECK_LIMIT", "TRIM_INDEX_GAP",
    "ANCHOR_SCAN_RANGE", "MAX_TOTAL_PASSES",
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


def evaluate(
    overrides: Dict[str, float],
    activity_tracks: List[Tuple[str, Track]],
    strava: Dict[str, List[Tuple[str, int, str]]],
    segments: List[dict],
) -> Metrics:
    """Calcola il punteggio qualità per la config `overrides` (baseline = {}).

    NOTA: DISTANCE_THRESHOLD_M e MIN_MATCH_POINTS vengono passati esplicitamente
    a find_strava_segments_in_track perché i default della firma sono vincolati
    al momento della definizione della funzione (non leggono il modulo a runtime).
    """
    t0 = time.perf_counter()

    distance_threshold = overrides.get("DISTANCE_THRESHOLD_M", DEFAULTS["DISTANCE_THRESHOLD_M"])
    min_match_points = int(overrides.get("MIN_MATCH_POINTS", DEFAULTS["MIN_MATCH_POINTS"]))

    saved = _apply_overrides(overrides)
    try:
        total_seg_strava = 0
        total_seg_algo = 0
        total_missing = 0
        total_extra = 0
        matched_ok = 0
        matched_small = 0
        matched_off = 0
        total_paired = 0
        sum_abs_delta_s = 0.0
        sum_sq_delta_s = 0.0  # somma delta^2 (per il delta RMS, come confronto_segmenti.py)

        for activity_name, track in activity_tracks:
            found = find_strava_segments_in_track(
                segments, track,
                distance_threshold_m=distance_threshold,
                min_match_points=min_match_points,
            )
            found_norm = [
                (normalize(o["segment_name"]), o["time_sec"], o["segment_name"], o["direction"])
                for o in found
            ]
            strava_list = strava.get(activity_name, [])

            total_seg_strava += len(strava_list)
            total_seg_algo += len(found_norm)

            def min_delta_pairs(name):
                algo_occ = [f for f in found_norm if f[0] == name]
                strava_occ = [(s, t, n) for s, t, n in strava_list if s == name]
                pairs = []
                a_free = list(range(len(algo_occ)))
                s_free = list(range(len(strava_occ)))
                while a_free and s_free:
                    best = None
                    for i in a_free:
                        for j in s_free:
                            if algo_occ[i][1] is None or strava_occ[j][1] is None:
                                continue
                            d = abs(algo_occ[i][1] - strava_occ[j][1])
                            if best is None or d < best[0]:
                                best = (d, i, j)
                    if best is None:
                        break
                    _, i, j = best
                    pairs.append((algo_occ[i], strava_occ[j]))
                    a_free.remove(i)
                    s_free.remove(j)
                return pairs, [algo_occ[i] for i in a_free], [strava_occ[j] for j in s_free]

            for name in {f[0] for f in found_norm} | {s[0] for s in strava_list}:
                pairs, a_left, s_left = min_delta_pairs(name)
                for a, s in pairs:
                    diff = a[1] - s[1]
                    total_paired += 1
                    abs_diff = abs(float(diff))
                    sum_abs_delta_s += abs_diff
                    sum_sq_delta_s += abs_diff * abs_diff
                    if abs(diff) <= 3:
                        matched_ok += 1
                    elif abs(diff) <= 15:
                        matched_small += 1
                    else:
                        matched_off += 1
                total_missing += len(s_left)
                total_extra += len(a_left)

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
    finally:
        _restore(saved)

    return Metrics(
        quality=quality, recall_pct=recall_pct, precision_pct=precision_pct,
        time_acc_pct=time_acc_pct, missing=total_missing, extra=total_extra,
        paired=total_paired, mean_abs_delta=mean_abs_delta, rms_delta=rms_delta,
        matched_ok=matched_ok, matched_small=matched_small, matched_off=matched_off,
        config=dict(DEFAULTS, **overrides), elapsed_s=time.perf_counter() - t0,
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
) -> Tuple[Metrics, int]:
    """Affinamento locale: prova ±1 passo di griglia per ogni parametro.

    Complementare alla ricerca congiunta: garantisce che ogni coordinata
    singola sia all'ottimo locale (almeno rispetto alla griglia).
    """
    grids: Dict[str, List[float]] = {
        name: list(SEARCH_SPACE[name])
        for name in PARAM_NAMES
        if len(SEARCH_SPACE.get(name, [])) > 1
    }
    best = evaluate(base_config, activity_tracks, strava, segments)
    n_evals = 1

    grid_idx: Dict[str, int] = {
        name: _nearest_grid_index(grid, base_config.get(name, DEFAULTS[name]))
        for name, grid in grids.items()
    }

    for rnd in range(1, 4):
        improved = False
        for name, grid in grids.items():
            idx = grid_idx[name]
            for delta in (-1, 1):
                ni = idx + delta
                if ni < 0 or ni >= len(grid):
                    continue
                cand = dict(best.config)
                cand[name] = grid[ni]
                m = evaluate(cand, activity_tracks, strava, segments)
                n_evals += 1
                if m.quality > best.quality + 1e-9:
                    best = m
                    grid_idx[name] = ni
                    improved = True
        if not quiet:
            print(f"      [polish] round {rnd} → {best.short()}")
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
        grid = SEARCH_SPACE.get(name, [])
        if not grid:
            # Parametro senza griglia: campione molto diverso dal default.
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
            if abs(m.quality - baseline.quality) >= 1e-9:
                insensibile = False
                break

        if insensibile:
            if not grid:
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
        "--method", choices=["adaptive", "classic"], default="adaptive",
        help="Motore di ricerca (default: adaptive multi-parametro)",
    )
    parser.add_argument("--export", type=str, default=None, metavar="FILE",
                        help="Esporta i parametri ottimi in JSON (es. test/params_ottimi.json)")
    parser.add_argument("--seed", type=int, default=42, help="Seed per la random search")
    parser.add_argument("-q", "--quiet", action="store_true", help="Output compatto")
    args = parser.parse_args()

    quiet = args.quiet or args.baseline

    # Dimensioni della ricerca in base a --quick/--deep.
    # Pre-inizializziamo entrambe le coppie di variabili per evitare
    # "possibly unbound" dal punto di vista dell'analizzatore statico.
    max_batches, batch_size, patience, restarts = 30, 16, 6, 2
    rounds = n_random = 2
    if args.quick:
        if args.method == "adaptive":
            max_batches, batch_size, patience, restarts = 15, 12, 4, 1
        else:
            rounds, n_random = 1, 15
    elif args.deep:
        if args.method == "adaptive":
            max_batches, batch_size, patience, restarts = 60, 24, 8, 4
        else:
            rounds, n_random = 4, 120
    else:
        if args.method == "adaptive":
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

    if args.method == "adaptive":
        # 3a. Dead-param PRELIMINARI: identifica i parametri non-sensibili da
        # escludere subito dalle mutazioni (evita di sprecare valutazioni).
        print("\nVerifica preliminare parametri non-sensibili...")
        dead_pre = detect_dead_params(activity_tracks, strava, segments, quiet)
        pre_excluded = dead_pre[1]  # non_sensibili
        n_evals += 3 * len(PARAM_NAMES)
        if not quiet and pre_excluded:
            print(f"  {len(pre_excluded)} parametri non-sensibili identificati")

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
            },
            "parametri_non_usati": dead[0],
            "parametri_non_sensibili": dead[1],
            "seed": args.seed,
        }
        export_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nConfigurazione ottima esportata in: {export_path}")


if __name__ == "__main__":
    main()