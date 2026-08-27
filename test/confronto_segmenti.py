"""Confronto tra i segmenti rilevati dall'algoritmo dell'app nei file Examples
e quelli rilevati da Strava (test/esempio_tempo_segmenti.txt).

Il PUNTEGGIO QUALITA' dell'algoritmo (recall/precisione/accuratezza temporale)
è calcolato SOLO sui file .gpx: Strava importa i .fit del device e li
rielabora, quindi la traccia riesportata può differire leggermente
dall'originale (vedi test/pulisci_fit.py). I file .fit vengono comunque
analizzati e mostrati SOLO come indice informativo; per le attività presenti
in entrambi i formati (FIT+GPX della stessa pedalata) è riportata la sezione
delle discrepanze di formato.

I file vengono analizzati in parallelo (un processo per file, fino al numero
di core disponibili); l'output resta identico all'esecuzione sequenziale.
A fine esecuzione è stampato il tempo totale impiegato.

Usage (dalla root del progetto):
    python test/confronto_segmenti.py
"""

import math
import os
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.fit_loader import load_fit  # noqa: E402
from core.gpx_loader import load_gpx  # noqa: E402
from core.strava_analyzer import (  # noqa: E402
    find_strava_segments_in_track,
    load_strava_segments,
)

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_DIR = ROOT / "Examples"
SEGMENTS_DIR = ROOT / "Strava_Segments"
STRAVA_TXT = ROOT / "test" / "esempio_tempo_segmenti.txt"

# Pesi per il punteggio qualità (ottimizzazione di core/strava_analyzer.py)
SCORE_W_RECALL = 0.35     # peso: quota passaggi Strava rilevati dall'algoritmo
SCORE_W_PRECISION = 0.35  # peso: quota passaggi algoritmo senza extra spuri
SCORE_W_TIME = 0.30       # peso: precisione temporale (accuratezza su delta RMS vs Strava)
TIME_TOLERANCE_S = 15.0   # delta RMS (s) oltre il quale l'accuratezza temporale è 0

# Dati condivisi con i worker del process pool (inizializzati da _init_worker)
_SEGMENTS: Optional[List[dict]] = None
_STRAVA: Optional[dict] = None


def normalize(name: str) -> str:
    """Normalizza il nome di un segmento per il confronto (case/separatori)."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def parse_strava_txt(path: Path) -> dict:
    """Legge il file testuale con i risultati ufficiali Strava.

    Formato atteso:
        NomeFile.gpx:
        - NomeSegmento MM:SS
        - NomeSegmento H:MM:SS

    Returns:
        dict {nome_traccia: [(nome_normalizzato, tempo_sec, nome_originale)]}
    """
    result = {}
    current = None
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


def fmt_time(sec):
    """Formatta i secondi nel formato M:SS.mmm (o H:MM:SS.mmm) con 3 decimali."""
    if sec is None:
        return "n/d"
    tot_ms = int(round(float(sec) * 1000))
    h, rem_ms = divmod(tot_ms, 3600_000)
    m, rem_s = divmod(rem_ms, 60_000)
    s, ms = divmod(rem_s, 1000)
    if h:
        return f"{h}:{m:02d}:{s:02d}.{ms:03d}"
    return f"{m}:{s:02d}.{ms:03d}"
def _pair_occurrences(name, found_by_name, strava_by_name):
    """Accoppia le occorrenze algoritmo/Strava dello stesso segmento
    minimizzando le differenze di tempo (greedy ad ogni passo prende
    la coppia con delta minimo assoluto).

    Returns:
        (pairs, algo_non_accoppiate, strava_non_accoppiate)
    """
    algo_occ = found_by_name.get(name, ())
    strava_occ = strava_by_name.get(name, ())
    pairs = []
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
        pairs.append((algo_occ[i], strava_occ[j]))
        a_free.remove(i)
        s_free.remove(j)
    return pairs, [algo_occ[i] for i in a_free], [strava_occ[j] for j in s_free]


def _init_worker(segments_dir: str, strava_txt: str) -> None:
    """Inizializza i dati condivisi nel processo worker (una volta per processo)."""
    global _SEGMENTS, _STRAVA
    _SEGMENTS = load_strava_segments(segments_dir)
    _STRAVA = parse_strava_txt(Path(strava_txt))


def _analyze_file(activity_file: Path) -> dict:
    """Carica una traccia, individua i segmenti e calcola il confronto con Strava.

    Restituisce un dict con i dati leggeri per la stampa e le statistiche
    di qualità (niente oggetti Track, così il risultato è serializzabile).
    """
    t0 = time.perf_counter()
    is_gpx = activity_file.suffix.lower() == ".gpx"
    track = load_gpx(str(activity_file)) if is_gpx else load_fit(str(activity_file))
    # Narrowing per il type checker: le globali sono caricate da _init_worker
    # (initializer del process pool) o dal fallback sequenziale in main().
    segments = _SEGMENTS if _SEGMENTS is not None else load_strava_segments(str(SEGMENTS_DIR))
    strava_ref = _STRAVA if _STRAVA is not None else parse_strava_txt(STRAVA_TXT)
    found = find_strava_segments_in_track(segments, track)

    found_norm = [  # (nome_norm, time_sec, nome_reale, direzione, len_m, start_km)
        (normalize(o["segment_name"]), o["time_sec"], o["segment_name"], o["direction"],
         o["length_m"], o["start_dist_m"] / 1000.0)
        for o in found
    ]
    strava_list = strava_ref.get(activity_file.name, [])  # (nome_norm, sec, nome_reale)

    # Occorrenze raggruppate per segmento (evita di rifiltrare le liste ad ogni nome)
    found_by_name = {}
    for f in found_norm:
        found_by_name.setdefault(f[0], []).append(f)
    strava_by_name = {}
    for s in strava_list:
        strava_by_name.setdefault(s[0], []).append(s)

    diff_line = []  # (nome_algo, delta_s, t_algo, t_strava, direzione, marcatore, len_m, pos_km)
    missing = []
    extra = []
    worst_rows = []  # (|delta|, attività, segmento, delta, t_algo, t_strava) per la classifica
    paired = 0
    sum_abs_delta = 0.0
    sum_sq_delta = 0.0
    max_abs_delta = 0.0
    matched_ok = matched_small = matched_off = 0
    n_missing = n_extra = 0

    # Nomi nell'ordine di prima comparsa: output deterministico (un set avrebbe
    # ordinamento dipendente dall'hash randomizzato delle stringhe)
    names = list(dict.fromkeys(
        [f[0] for f in found_norm] + [s[0] for s in strava_by_name]
    ))
    for name in names:
        pairs, a_left, s_left = _pair_occurrences(name, found_by_name, strava_by_name)
        for a, s in pairs:
            ssec = s[1]
            diff = a[1] - ssec
            abs_diff = abs(float(diff))
            if is_gpx:  # statistiche di qualità solo sui GPX
                paired += 1
                sum_abs_delta += abs_diff
                sum_sq_delta += abs_diff * abs_diff
                max_abs_delta = max(max_abs_delta, abs_diff)
                worst_rows.append((abs_diff, activity_file.name, a[2], float(diff),
                                   float(a[1]), float(ssec)))
                if abs(diff) <= 3:
                    marker = "OK"
                    matched_ok += 1
                elif abs(diff) <= 15:
                    marker = "~"
                    matched_small += 1
                else:
                    marker = "X"
                    matched_off += 1
            else:
                marker = "OK" if abs(diff) <= 3 else ("~" if abs(diff) <= 15 else "X")
            diff_line.append((a[2], diff, a[1], ssec, a[3], marker, a[4], a[5]))
        for s in s_left:
            missing.append((s[2], s[1]))
            if is_gpx:
                n_missing += 1
        for a in a_left:
            extra.append(a)
            if is_gpx:
                n_extra += 1

    return {
        "name": activity_file.name,
        "stem": activity_file.stem,
        "ext": activity_file.suffix.lower(),
        "is_gpx": is_gpx,
        "strava_list": strava_list,
        "found_norm": found_norm,
        "diff_line": diff_line,
        "missing": missing,
        "extra": extra,
        "has_diff": bool(missing) or bool(extra) or any(d[5] != "OK" for d in diff_line),
        "no_delta": bool(is_gpx and diff_line and all(abs(d[1]) < 1 for d in diff_line)),
        "n_strava": len(strava_list) if is_gpx else 0,
        "n_algo": len(found_norm) if is_gpx else 0,
        "n_missing": n_missing,
        "n_extra": n_extra,
        "matched_ok": matched_ok,
        "matched_small": matched_small,
        "matched_off": matched_off,
        "paired": paired,
        "sum_abs_delta": sum_abs_delta,
        "sum_sq_delta": sum_sq_delta,
        "max_abs_delta": max_abs_delta,
        "worst_rows": worst_rows,
        "fmt_times": [(f[0], f[1]) for f in found_norm],
        "elapsed": time.perf_counter() - t0,
    }


def main() -> None:
    t_start = time.perf_counter()
    segs = load_strava_segments(str(SEGMENTS_DIR))
    print(f"Segmenti Strava caricati: {[s['name'] for s in segs]}\n")

    # Il punteggio qualità usa solo i GPX; i FIT sono indice informativo.
    gpx_files = sorted(EXAMPLES_DIR.glob("*.gpx"))
    fit_files = sorted(EXAMPLES_DIR.glob("*.fit"))
    activity_files = gpx_files + fit_files

    # Analisi parallela: ogni file è indipendente, un worker per CPU disponibile.
    # In ambienti che non supportano i process pool si ripiega sul sequenziale.
    results = []
    n_workers = min(len(activity_files), os.cpu_count() or 1)
    if n_workers > 1:
        try:
            with ProcessPoolExecutor(
                max_workers=n_workers,
                initializer=_init_worker,
                initargs=(str(SEGMENTS_DIR), str(STRAVA_TXT)),
            ) as pool:
                results = list(pool.map(_analyze_file, activity_files, chunksize=1))
        except (OSError, PermissionError, ImportError):
            results = []
    if not results:
        _init_worker(str(SEGMENTS_DIR), str(STRAVA_TXT))
        results = [_analyze_file(p) for p in activity_files]

    # --- Aggregazione delle statistiche di qualità (solo GPX) ---
    total_seg_strava = sum(r["n_strava"] for r in results)
    total_seg_algo = sum(r["n_algo"] for r in results)
    total_missing = sum(r["n_missing"] for r in results)
    total_extra = sum(r["n_extra"] for r in results)
    matched_ok = sum(r["matched_ok"] for r in results)
    matched_small = sum(r["matched_small"] for r in results)
    matched_off = sum(r["matched_off"] for r in results)
    total_paired = sum(r["paired"] for r in results)
    sum_abs_delta_s = sum(r["sum_abs_delta"] for r in results)
    sum_sq_delta_s = sum(r["sum_sq_delta"] for r in results)
    max_abs_delta_s = max((r["max_abs_delta"] for r in results), default=0.0)
    worst_rows = [row for r in results for row in r["worst_rows"]]
    activities_diff = sum(1 for r in results if r["is_gpx"] and r["has_diff"])
    activities_no_delta = sum(1 for r in results if r["no_delta"])
    fmt_times = {(r["stem"], r["ext"]): r["fmt_times"] for r in results}
    gpx_count = len(gpx_files)
    fit_count = len(fit_files)

    print("=" * 78)
    for r in results:
        is_gpx = r["is_gpx"]
        status = ("OK" if not r["has_diff"] else "DIFF") if is_gpx else "INDICE"
        print(f"\n### {r['name']}  [{status}]")
        if not is_gpx:
            print("    (file FIT: analizzato solo come indice, non entra nel punteggio)")
        print("    Strava:    " + ", ".join(
            f"{n} {fmt_time(s)}" for _, s, n in r["strava_list"]) or "(nessuno)")
        print("    Algoritmo: " + ", ".join(
            f"{n} {fmt_time(s)} ({d})" for _, s, n, d, _l, _k in r["found_norm"]) or "(nessuno)")

        if r["diff_line"]:
            print("    Tempi (algo vs Strava):")
            for name, diff, talgo, tstrava, _dname, marker, len_m, start_km in r["diff_line"]:
                print(f"      - {name:<24} algo={fmt_time(talgo):>12}  Strava={fmt_time(tstrava):>12}  "
                      f"Δ={diff:+9.3f}s {marker}  [{len_m/1000:.2f} km alla distanza {start_km:.1f} km]")
        if r["missing"]:
            print(f"    MANCANTI per l'algoritmo: {[f'{n} {fmt_time(t)}' for n, t in r['missing']]}")
        if r["extra"]:
            print(f"    EXTRA (solo algo): {[f'{n} {fmt_time(t)} ({d}, {l/1000:.2f} km @ {k:.1f} km)' for _, t, n, d, l, k in r['extra']]}  # passaggi rilevati ma senza tempo nel file Strava")

    print("\n" + "=" * 78)

    # --- Punteggio qualità algoritmo (per ottimizzare strava_analyzer.py) ---
    recall_pct = (
        100.0 * (total_seg_strava - total_missing) / total_seg_strava
        if total_seg_strava > 0
        else 0.0
    )
    precision_pct = (
        100.0 * (total_seg_algo - total_extra) / total_seg_algo
        if total_seg_algo > 0
        else 0.0
    )
    mean_abs_delta = sum_abs_delta_s / total_paired if total_paired > 0 else 0.0
    # Accuratezza temporale basata sul delta RMS (radice della media dei quadrati):
    # a differenza della media semplice, penalizza in modo quadratico gli scostamenti
    # grandi — anche quando la maggioranza dei passaggi è accurata. Scosti positivi e
    # negativi non si compensano e gli outlier non vengono diluiti dalla media.
    rms_delta = math.sqrt(sum_sq_delta_s / total_paired) if total_paired > 0 else 0.0
    time_acc_pct = (
        max(0.0, 100.0 * (1.0 - rms_delta / TIME_TOLERANCE_S))
        if total_paired > 0
        else 0.0
    )
    quality_score = (
        SCORE_W_RECALL * recall_pct
        + SCORE_W_PRECISION * precision_pct
        + SCORE_W_TIME * time_acc_pct
    )

    # --- Raggruppamento per attività comuni (stessa pedalata in FIT e GPX) ---
    fmt_stems = {}
    for stem, ext in fmt_times:
        fmt_stems.setdefault(stem, []).append(ext)
    paired_stems = sorted(s for s, e in fmt_stems.items() if len(e) >= 2)
    n_paired_fit = len([s for s in paired_stems if (s, ".fit") in fmt_times])
    fit_only_stems = sorted(
        s for (s, e) in fmt_times if e == ".fit" and s not in paired_stems
    )

    print("\nRIEPILOGO")
    print(
        f"  Attività GPX (base del punteggio): {gpx_count} "
        f"(con differenze: {activities_diff})"
    )
    print(f"  Attività senza scostamenti (delta < 1s): {activities_no_delta}")
    print(
        f"  Attività FIT (solo indice informativo): {fit_count} "
        f"| con coppia GPX: {n_paired_fit} | senza coppia: {len(fit_only_stems)}"
    )
    print(
        f"  Passaggi Strava totali (solo GPX): {total_seg_strava} | "
        f"Passaggi algoritmo (solo GPX): {total_seg_algo}"
    )
    print(f"  Percorsi mancanti: {total_missing} | Percorsi extra: {total_extra}")
    print(f"  Tempi entro +/-3s:  {matched_ok}")
    print(f"  Tempi entro +/-15s: {matched_small}")
    print(f"  Tempi oltre +/-15s: {matched_off}")
    print(f"\n  Punteggio qualità algoritmo (calcolato sui soli GPX): {quality_score:.3f}/100")
    print(f"    Recall passaggi Strava:    {recall_pct:7.3f}%  ({total_seg_strava - total_missing}/{total_seg_strava})")
    print(f"    Precisione (no extra):     {precision_pct:7.3f}%")
    print(
        f"    Accuratezza tempo:         {time_acc_pct:7.3f}%  "
        f"(delta RMS {rms_delta:.3f}s, medio {mean_abs_delta:.3f}s, max {max_abs_delta_s:.3f}s)"
    )

    # --- Classifica degli scostamenti piu' alti (solo GPX) ---
    # Utile per concentrare il miglioramento dell'algoritmo sugli outlier:
    # il delta RMS e' dominato dai casi piu' lontani da Strava.
    if total_paired > 0:
        n_over_1 = sum(1 for r in worst_rows if r[0] > 1)
        n_over_3 = matched_small + matched_off
        print(
            f"\n  SCOSTAMENTI ALTI: {n_over_1}/{len(worst_rows)} oltre 1s "
            f"| {n_over_3} oltre 3s — top 10 per |delta|:"
        )
        for ad, act, sname, dff, talgo_v, tstr_v in sorted(worst_rows, key=lambda r: -r[0])[:10]:
            print(f"    - {sname:<24} {act:<38} Δ={dff:+9.3f}s  "
                  f"algo={fmt_time(talgo_v):>12}  Strava={fmt_time(tstr_v):>12}")

    # --- Discrepanze di formato: stessa pedalata registrata in FIT e GPX ---
    # Solo indice informativo: NON entra nel punteggio qualità.
    if paired_stems:
        print("\nDISCREPANZE FORMATO (FIT vs GPX della stessa pedalata; fuori dal punteggio)")
        worst = 0.0
        for stem in paired_stems:
            la = sorted(fmt_times[(stem, ".fit")], key=lambda x: x[1])
            lb = sorted(fmt_times[(stem, ".gpx")], key=lambda x: x[1])
            if len(la) != len(lb):
                print(f"  {stem}: occorrenze diverse fit={len(la)} gpx={len(lb)}")
                continue
            deltas = [abs(x[1] - y[1]) for x, y in zip(la, lb)] or [0.0]
            worst = max(worst, max(deltas))
            print(f"  {stem}: delta medio {sum(deltas)/len(deltas):9.3f}s  max {max(deltas):9.3f}s")
        print(f"  Peggior delta formato: {worst:.3f}s")

    # --- Attività FIT senza coppia GPX: solo elenco dei passaggi trovati ---
    if fit_only_stems:
        print("\nPASSAGGI TROVATI NEI FIT SENZA COPPIA GPX (solo indice)")
        for stem in fit_only_stems:
            lst = sorted(fmt_times[(stem, ".fit")], key=lambda x: x[1])
            print(f"  {stem}.fit: " + (", ".join(f"{n} {fmt_time(t)}" for n, t in lst) or "(nessuno)"))

    # --- Tempo di esecuzione ---
    elapsed = time.perf_counter() - t_start
    slowest = max(results, key=lambda r: r["elapsed"])
    print(
        f"\nTempo di esecuzione: {elapsed:.2f}s "
        f"({len(activity_files)} file in parallelo su {n_workers} processi; "
        f"file più lento: {slowest['name']} {slowest['elapsed']:.2f}s)"
    )


if __name__ == "__main__":
    main()
