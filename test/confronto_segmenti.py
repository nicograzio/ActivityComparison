"""Confronto tra i segmenti rilevati dall'algoritmo dell'app nei file Examples
e quelli rilevati da Strava (test/esempio_tempo_segmenti.txt).

Usage (dalla root del progetto):
    python test/confronto_segmenti.py
"""

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
SCORE_W_TIME = 0.30       # peso: precisione temporale (delta medio vs Strava)
TIME_TOLERANCE_S = 15.0   # delta (s) oltre il quale l'accuratezza temporale è 0


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
    """Formatta secondi nel formato M:SS (o H:MM:SS)."""
    if sec is None:
        return "n/d"
    sec = int(round(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def main() -> None:
    segs = load_strava_segments(str(SEGMENTS_DIR))
    print(f"Segmenti Strava caricati: {[s['name'] for s in segs]}\n")

    strava = parse_strava_txt(STRAVA_TXT)

    activity_files = sorted(
        list(EXAMPLES_DIR.glob("*.gpx")) + list(EXAMPLES_DIR.glob("*.fit"))
    )
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
    sum_abs_delta_s = 0.0

    print("=" * 78)
    for activity_file in activity_files:
        if activity_file.suffix.lower() == ".fit":
            track = load_fit(str(activity_file))
        else:
            track = load_gpx(str(activity_file))
        found = find_strava_segments_in_track(segs, track)

        found_norm = [  # (nome_norm, time_sec, nome_reale, direzione, len_m, start_km)
            (normalize(o["segment_name"]), o["time_sec"], o["segment_name"], o["direction"],
             o["length_m"], o["start_dist_m"] / 1000.0)
            for o in found
        ]
        strava_list = strava.get(activity_file.name, [])  # (nome_norm, sec, nome_reale)

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
                total_paired += 1
                sum_abs_delta_s += abs(float(diff))
                if abs(diff) <= 3:
                    marker = "OK"
                    matched_ok += 1
                elif abs(diff) <= 15:
                    marker = "~"
                    matched_small += 1
                else:
                    marker = "X"
                    matched_off += 1
                diff_line.append((a[2], diff, a[1], ssec, a[3], marker, a[4], a[5]))
            for s in s_left:
                missing.append((s[2], s[1]))
            for a in a_left:
                extra.append(a)

        total_missing += len(missing)
        total_extra += len(extra)

        has_diff = bool(missing) or bool(extra) or any(d[5] != "OK" for d in diff_line)
        if has_diff:
            activities_diff += 1
        # Attività senza scostamenti: tutti i delta accoppiati = 0 o < 1s
        if diff_line and all(abs(d[1]) < 1 for d in diff_line):
            activities_no_delta += 1
        status = "OK" if not has_diff else "DIFF"

        print(f"\n### {activity_file.name}  [{status}]")
        print(f"    Strava:    {', '.join(f'{n} {fmt_time(s)}' for _, s, n in strava_list) or '(nessuno)'}")
        print(f"    Algoritmo: {', '.join(f'{n} {fmt_time(s)} ({d})' for _, s, n, d, _l, _k in found_norm) or '(nessuno)'}")

        if diff_line:
            print("    Tempi (algo vs Strava):")
            for name, diff, talgo, tstrava, _dname, marker, len_m, start_km in diff_line:
                print(f"      - {name:<24} algo={fmt_time(talgo):>8}  Strava={fmt_time(tstrava):>8}  "
                      f"Δ={diff:+.0f}s {marker}  [{len_m/1000:.2f} km alla distanza {start_km:.1f} km]")
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
    time_acc_pct = (
        max(0.0, 100.0 * (1.0 - mean_abs_delta / TIME_TOLERANCE_S))
        if total_paired > 0
        else 0.0
    )
    quality_score = (
        SCORE_W_RECALL * recall_pct
        + SCORE_W_PRECISION * precision_pct
        + SCORE_W_TIME * time_acc_pct
    )

    print("\nRIEPILOGO")
    print(f"  Attività confrontate: {len(activity_files)} (con differenze: {activities_diff})")
    print(f"  Attività senza scostamenti (delta = 0 o < 1s): {activities_no_delta}")
    print(f"  Passaggi Strava totali: {total_seg_strava} | Passaggi algoritmo: {total_seg_algo}")
    print(f"  Percorsi mancanti: {total_missing} | Percorsi extra: {total_extra}")
    print(f"  Tempi entro +/-3s:  {matched_ok}")
    print(f"  Tempi entro +/-15s: {matched_small}")
    print(f"  Tempi oltre +/-15s: {matched_off}")
    print(f"\n  Punteggio qualità algoritmo: {quality_score:.1f}/100")
    print(f"    Recall passaggi Strava:    {recall_pct:5.1f}%  ({total_seg_strava - total_missing}/{total_seg_strava})")
    print(f"    Precisione (no extra):     {precision_pct:5.1f}%")
    print(f"    Accuratezza tempo:         {time_acc_pct:5.1f}%  (delta medio {mean_abs_delta:.2f}s)")


if __name__ == "__main__":
    main()