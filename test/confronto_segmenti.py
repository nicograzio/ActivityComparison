"""Confronto tra i segmenti rilevati dall'algoritmo dell'app nei file Examples
e quelli rilevati da Strava (test/esempio_tempo_segmenti.txt).

Il PUNTEGGIO QUALITA' dell'algoritmo (recall/precisione/accuratezza temporale)
è calcolato SOLO sui file .gpx: Strava importa i .fit del device e li
rielabora, quindi la traccia riesportata può differire leggermente
dall'originale (vedi test/pulisci_fit.py). I file .fit vengono comunque
analizzati e mostrati SOLO come indice informativo; per le attività presenti
in entrambi i formati (FIT+GPX della stessa pedalata) è riportata la sezione
delle discrepanze di formato.

Usage (dalla root del progetto):
    python test/confronto_segmenti.py
"""

import math
import re
import sys
from pathlib import Path

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


def main() -> None:
    segs = load_strava_segments(str(SEGMENTS_DIR))
    print(f"Segmenti Strava caricati: {[s['name'] for s in segs]}\n")

    strava = parse_strava_txt(STRAVA_TXT)

    # Il punteggio qualità usa solo i GPX; i FIT sono indice informativo.
    gpx_files = sorted(EXAMPLES_DIR.glob("*.gpx"))
    fit_files = sorted(EXAMPLES_DIR.glob("*.fit"))
    activity_files = gpx_files + fit_files

    activities_diff = 0
    activities_no_delta = 0  # attività con tutti i delta accoppiati = 0 o < 1s
    total_seg_strava = 0
    total_seg_algo = 0
    total_missing = 0
    total_extra = 0
    matched_ok = 0
    matched_small = 0
    matched_off = 0
    total_paired = 0
    sum_abs_delta_s = 0.0   # somma |delta| (per il delta medio, solo informativo)
    sum_sq_delta_s = 0.0    # somma delta^2 (per il delta RMS usato nell'accuratezza)
    max_abs_delta_s = 0.0   # peggior scostamento assoluto osservato
    worst_rows = []  # (|delta|, attività, segmento, delta, t_algo, t_strava) per la classifica
    fmt_times = {}  # (stem, estensione) -> [(nome_norm, time_sec), ...] per invarianza formato

    print("=" * 78)
    for activity_file in activity_files:
        is_gpx = activity_file.suffix.lower() == ".gpx"
        if not is_gpx:
            track = load_fit(str(activity_file))
        else:
            track = load_gpx(str(activity_file))
        found = find_strava_segments_in_track(segs, track)

        found_norm = [  # (nome_norm, time_sec, nome_reale, direzione, len_m, start_km)
            (normalize(o["segment_name"]), o["time_sec"], o["segment_name"], o["direction"],
             o["length_m"], o["start_dist_m"] / 1000.0)
            for o in found
        ]
        fmt_times[(activity_file.stem, activity_file.suffix.lower())] = [
            (f[0], f[1]) for f in found_norm
        ]
        strava_list = strava.get(activity_file.name, [])  # (nome_norm, sec, nome_reale)

        if is_gpx:  # solo i GPX entrano nel computo di qualità
            total_seg_strava += len(strava_list)
            total_seg_algo += len(found_norm)

        def min_delta_pairs(name):
            """Accoppia le occorrenze algoritmo/Strava dello stesso segmento
            minimizzando le differenze di tempo (greedy ad ogni passo prende
            la coppia con delta minimo assoluto)."""
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

        diff_line = []  # (nome_algo, delta_s, t_algo, t_strava, direzione, marcatore, len_km, pos_km)
        missing = []
        extra = []
        for name in {f[0] for f in found_norm} | {s[0] for s in strava_list}:
            pairs, a_left, s_left = min_delta_pairs(name)
            for a, s in pairs:
                ssec = s[1]
                diff = a[1] - ssec
                abs_diff = abs(float(diff))
                if is_gpx:  # statistiche di qualità solo sui GPX
                    total_paired += 1
                    sum_abs_delta_s += abs_diff
                    sum_sq_delta_s += abs_diff * abs_diff
                    max_abs_delta_s = max(max_abs_delta_s, abs_diff)
                    worst_rows.append((abs_diff, activity_file.name, a[2], float(diff), float(a[1]), float(ssec)))
                if abs(diff) <= 3:
                    marker = "OK"
                    if is_gpx:
                        matched_ok += 1
                elif abs(diff) <= 15:
                    marker = "~"
                    if is_gpx:
                        matched_small += 1
                else:
                    marker = "X"
                    if is_gpx:
                        matched_off += 1
                diff_line.append((a[2], diff, a[1], ssec, a[3], marker, a[4], a[5]))
            for s in s_left:
                missing.append((s[2], s[1]))
            for a in a_left:
                extra.append(a)

        if is_gpx:
            total_missing += len(missing)
            total_extra += len(extra)

        has_diff = bool(missing) or bool(extra) or any(d[5] != "OK" for d in diff_line)
        if has_diff and is_gpx:
            activities_diff += 1
        # Attività senza scostamenti: tutti i delta accoppiati = 0 o < 1s
        if is_gpx and diff_line and all(abs(d[1]) < 1 for d in diff_line):
            activities_no_delta += 1
        status = ("OK" if not has_diff else "DIFF") if is_gpx else "INDICE"

        print(f"\n### {activity_file.name}  [{status}]")
        if not is_gpx:
            print("    (file FIT: analizzato solo come indice, non entra nel punteggio)")
        print(f"    Strava:    {', '.join(f'{n} {fmt_time(s)}' for _, s, n in strava_list) or '(nessuno)'}")
        print(f"    Algoritmo: {', '.join(f'{n} {fmt_time(s)} ({d})' for _, s, n, d, _l, _k in found_norm) or '(nessuno)'}")

        if diff_line:
            print("    Tempi (algo vs Strava):")
            for name, diff, talgo, tstrava, _dname, marker, len_m, start_km in diff_line:
                print(f"      - {name:<24} algo={fmt_time(talgo):>12}  Strava={fmt_time(tstrava):>12}  "
                      f"Δ={diff:+9.3f}s {marker}  [{len_m/1000:.2f} km alla distanza {start_km:.1f} km]")
        if missing:
            print(f"    MANCANTI per l'algoritmo: {[f'{n} {fmt_time(t)}' for n, t in missing]}")
        if extra:
            print(f"    EXTRA (solo algo): {[f'{n} {fmt_time(t)} ({d}, {l/1000:.2f} km @ {k:.1f} km)' for _, t, n, d, l, k in extra]}  # passaggi rilevati ma senza tempo nel file Strava")

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
        f"  Attività GPX (base del punteggio): {len(gpx_files)} "
        f"(con differenze: {activities_diff})"
    )
    print(f"  Attività senza scostamenti (delta < 1s): {activities_no_delta}")
    print(
        f"  Attività FIT (solo indice informativo): {len(fit_files)} "
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


if __name__ == "__main__":
    main()