"""Modulo per l'analisi dei segmenti Strava nelle tracce caricate.

Implementa l'algoritmo di map-matching con gestione avanzata dei loop di ingresso,
inversioni a U e passaggi multipli all'imbocco del segmento.
"""

from bisect import bisect_left
import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import gpxpy
import numpy as np

from core.analyzer import track_distance_profile, haversine_distance
from core.track import Track, TrackPoint

# =============================================================================
# PARAMETRI DI CONFIGURAZIONE MATCHING SEGMENTI
# =============================================================================
#
# I parametri sono organizzati per CATEGORIA (natura) e per FASE della
# pipeline di map-matching che influenzano:
#
#   FASE 1 - CANDIDATE   (_candidate_track_indices)
#           Per ogni punto del segmento trova, tramite griglia spaziale, i
#           punti di traccia entro la soglia di distanza.
#   FASE 2 - ANCHOR      (_find_occurrences)
#           Raggruppa i candidati dell'imbocco in cluster: ogni cluster e' un
#           potenziale passaggio sul gate d'ingresso (anchor).
#   FASE 3 - WALK        (_walk_forward)
#           Camminata greedy che appare punto-segmento e punto-traccia
#           rispettando il progresso lungo il segmento.
#   FASE 4 - TRIM        (_trim_chain_start)
#           Elimina loop e avvicinamenti spurii prima dell'imbocco reale.
#   FASE 5 - VALIDAZIONE (_find_occurrences, criteri di accettazione)
#           Copertura inizio/fine, densita' e lunghezza dell'occorrenza.
#   FASE 6 - PROIEZIONE  (_find_best_track_projection, _find_gate_valley)
#           Proiezione fine dei gate START/END sui chord della traccia per
#           ricavare i tempi esatti di attraversamento.
#   FASE 7 - SELEZIONE   (filtro finale in find_strava_segments_in_track)
#           Risoluzione delle sovrapposizioni tra occorrenze.
#
# TABELLA RIASSUNTIVA PARAMETRI
# ---------------------------------------------------------------------------
#   FASE  : fase della pipeline influenzata (1..7, vedi sopra)
#   SU    : effetto se il valore AUMENTA
#   GIU   : effetto se il valore DIMINUISCE
#   TEMPO : impatto sul tempo di completamento (+++ forte, ++ medio,
#           + leggero, o = trascurabile); "SU tempo" = piu' lento al
#           crescere del parametro, "GIU tempo" = piu' veloce al crescere.
#
# | Parametro                    | Fase | Natura      | SU aumenta ->                   | GIU diminuisce ->               | Tempo       |
# |------------------------------|------|-------------|---------------------------------|---------------------------------|-------------|
# | DISTANCE_THRESHOLD_M         | 1,4  | geometria   | +tolleranza GPS, + falsi pos.   | +precisione, passaggi persi     | SU tempo ++ |
# | CLUSTER_GAP_IDX              | 2    | clustering  | cluster fusi, meno walk         | piu' cluster, + anchor          | SU tempo +  |
# | ANCHOR_SCAN_RANGE            | 2    | clustering  | + anchor, + passaggi multipli   | 1 solo anchor per cluster       | SU tempo +  |
# | MAX_GAP_RATIO                | 3    | copertura   | attraversa buchi GPS ampi       | catene interrotte, -recall      | SU tempo +  |
# | PROGRESS_RATIO               | 3    | progresso   | tollera deviazioni/detours      | taglia tratti sinuosi           | SU tempo +  |
# | PROGRESS_SLACK_M             | 3    | progresso   | idem (su tratti corti)          | idem                            | o           |
# | TRIM_REF_POINTS              | 4    | trim        | trim imbocco piu' aggressivo    | trim piu' morbido               | o           |
# | TRIM_CHECK_LIMIT             | 4    | trim        | copre avvicinamenti lunghi      | loop lunghi non trimmati        | o           |
# | TRIM_INDEX_GAP               | 4    | trim        | solo salti enormi tagliano      | trim aggressivo (rischio tagli) | o           |
# | START_TOL_RATIO              | 5    | copertura   | accetta inizi incompleti        | scarta occorrenze parziali      | o           |
# | END_TOL_RATIO                | 5    | copertura   | accetta fini incompleti         | scarta occorrenze parziali      | o           |
# | MIN_DENSITY                  | 5    | validazione | scarta occorrenze troncate      | accetta match parziali          | o           |
# | MAX_DENSITY                  | 5    | validazione | accetta occorrenze con loop     | scarta occorrenze lunghe        | o           |
# | END_PROJECTION_EXTRA_IDX     | 6    | proiezione  | valle end cercata piu' a lungo  | uscita gate meno accurata       | SU tempo +  |
# | END_PROJECTION_ACCEPT_M      | 6    | proiezione  | accetta valley lontane          | valley scartate (fallback)      | o           |
# | END_PROJECTION_EXIT_RISE_M   | 6    | proiezione  | scansiona piu' a lungo, robusto | esce al primo rumore (fragile)  | SU tempo +  |
# | START_PROJECTION_EXTRA_IDX   | 6    | proiezione  | copre loop d'ingresso ampi      | start meno accurato             | SU tempo +  |
# | START_PROJECTION_ACCEPT_M    | 6    | proiezione  | accetta valley lontane          | start resta sul chord           | o           |
# | START_PROJECTION_EXIT_RISE_M | 6    | proiezione  | scansiona piu' a lungo, robusto | esce al primo rumore (fragile)  | SU tempo +  |
# | STATIONARY_SPEED_KMH         | 6    | selezione   | + chord ignorati (fermi)        | jitter GPS entra nella valle    | GIU tempo + |
# | OVERLAP_OCCUPANCY_THRESHOLD  | 7    | selezione   | ammette + overlap (doppioni)    | risultati piu' canonici         | o           |
#
# NOTA - costanti interne NON parametrizzate presenti nell'algoritmo:
#   - salto max di 150 indici di traccia per punto segmento (_walk_forward)
#   - early-exit a 10 m di distanza nel matching (_walk_forward)
#   - finestra di proiezione fissa a 0 chord (_find_best_track_projection)
#   - gap di 2 indici nel tie-break a mediana (_median_tie_selection)
#
# ---------------------------------------------------------------------------
# CATEGORIA A - GEOMETRIA DEL MATCHING (FASE 1: generazione candidati)
# ---------------------------------------------------------------------------
# Natura: soglia spaziale assoluta. Influenza quali punti traccia possono
# accoppiarsi ai punti del segmento e la qualita' geografica del matching.
#
# Soglia di vicinanza GPS (metri) per considerare un punto di traccia come
# candidato. Definisce anche la cella della griglia spaziale (FASE 1) e il
# geofence usato dal trim dell'imbocco (FASE 4).
#   - AUMENTA: match piu' tolleranti (GPS rumoroso, campionamento rado), MA
#     piu' falsi positivi e catene che "saltano" su strade vicine.
#   - DIMINUISCE: matching piu' selettivo e preciso, MA rischio di perdere
#     passaggi legittimi (recall in calo).
#   - PERFORMANCE: parametro dominante. Celle di griglia piu' grandi => piu'
#     punti per cella, e _walk_forward esplora piu' candidati per punto: il
#     tempo cresce in modo marcato (quasi quadratico sul numero candidati).
DISTANCE_THRESHOLD_M = 15.0

# ---------------------------------------------------------------------------
# CATEGORIA B - TOLLERANZE DI COPERTURA DELLA CATENA (FASE 3 walk / FASE 5)
# ---------------------------------------------------------------------------
# Natura: rapporti (frazioni dei punti totali del segmento). Influenzano
# quanto una catena puo' essere incompleta ai bordi o durante lo svolgimento
# e restare comunque un'occorrenza valida.

# Rapporto per definire la tolleranza di inizio: la catena puo' iniziare entro
# i primi START_TOL_RATIO * n_seg punti del segmento.
#   - AUMENTA: accetta imbocchi incompleti (piu' recall), MA rischia di
#     validare match parziali/spuri.
#   - DIMINUISCE: richiede la copertura quasi integrale dell'imbocco; i
#     passaggi con inizio oscurato vengono scartati.
#   - PERFORMANCE: trascurabile (solo criterio di accettazione).
START_TOL_RATIO = 0.4

# Rapporto per definire la tolleranza di fine: la catena puo' terminare entro
# gli ultimi END_TOL_RATIO * n_seg punti del segmento.
#   - AUMENTA: accetta fini incompleti (es. uscita dal gate mancante), MA
#     occorrenze potenzialmente troncate.
#   - DIMINUISCE: piu' severo sulla copertura della chiusura del segmento.
#   - PERFORMANCE: trascurabile.
END_TOL_RATIO = 0.4

# Rapporto massimo di gap (punti segmento consecutivi senza candidati) rispetto
# al totale, tollerato durante la camminata in avanti.
#   - AUMENTA: la walk attraversa buchi GPS/tunnel ampi senza interrompersi
#     (piu' recall), MA piu' rischio di accorpare passaggi distinti.
#   - DIMINUISCE: catene interrotte piu' presto: piu' precisione, passaggi
#     persi in caso di dropout del logger.
#   - PERFORMANCE: al crescere la walk prosegue piu' a lungo sui buchi =>
#     tempo leggermente superiore.
MAX_GAP_RATIO = 0.3

# ---------------------------------------------------------------------------
# CATEGORIA C - CLUSTERING DEGLI ANCHOR (FASE 2: individuazione passaggi)
# ---------------------------------------------------------------------------
# Natura: distanze in INDICI di traccia. Influenzano quanti potenziali
# passaggi (anchor) vengono generati all'imbocco e quindi quante volte viene
# lanciata la camminata greedy.

# Distanza minima tra indici di traccia per separare due cluster di candidati
# sull'imbocco: due candidati piu' vicini di cosi' appartengono allo stesso
# passaggio.
#   - AUMENTA: cluster fusi => meno anchor => meno walk tentate; passaggi
#     ravvicinati (giri sul circuito) rischiano di essere visti come uno.
#   - DIMINUISCE: piu' cluster/anchor => piu' passaggi distinti rilevati.
#   - PERFORMANCE: al crescere, meno walk => piu' veloce; al diminuire, piu'
#     walk => piu' lento.
CLUSTER_GAP_IDX = 10

# Raggruppamento degli anchor point iniziali: dopo aver rilevato un nuovo
# cluster, quanti candidati consecutivi aggiungere al pool di anchor da
# tentare.
#   - AUMENTA: piu' punti di partenza provati per cluster => piu' occorrenze
#     multiple trovate sullo stesso gate, MA tentativi spuri in piu'.
#   - DIMINUISCE: si tenta solo il primo candidato di ogni nuovo cluster.
#   - PERFORMANCE: al crescere, piu' walk lanciate => piu' lento.
ANCHOR_SCAN_RANGE = 5

# ---------------------------------------------------------------------------
# CATEGORIA D - COERENZA DEL PROGRESSO (FASE 3: camminata greedy)
# ---------------------------------------------------------------------------
# Natura: soglie di coerenza geometrica. Influenzano la capacita' della walk
# di seguire il segmento anche con deviazioni, senza "scorciatoie" sulla
# traccia.

# Rapporto massimo tra distanza percorsa sulla traccia e distanza sul segmento
# tra due accoppiamenti consecutivi: la traccia non puo' "precedere" troppo il
# segmento.
#   - AUMENTA: tollera deviazioni/detours anche lunghi (piu' robusto), MA la
#     walk puo' agganciare percorsi alternativi vicini.
#   - DIMINUISCE: taglia tratti sinuosi/detour: catene piu' corte, rischio di
#     perdere l'occorrenza su percorsi non esatti.
#   - PERFORMANCE: al crescere, piu' candidati superano il filtro di progresso
#     => walk piu' lenta.
PROGRESS_RATIO = 2.5

# Slack assoluto (metri) sul progresso: tolleranza fissa sommata alla distanza
# del segmento, fondamentale sui tratti molto corti dove il rapporto puro
# sarebbe troppo severo.
#   - AUMENTA: come PROGRESS_RATIO, ma pesa soprattutto sui tratti brevi.
#   - DIMINUISCE: matching piu' rigido sui micro-tratti.
#   - PERFORMANCE: trascurabile/modesto.
PROGRESS_SLACK_M = 50

# ---------------------------------------------------------------------------
# CATEGORIA E - VALIDAZIONE DELL'OCCORRENZA (FASE 5: criteri di accettazione)
# ---------------------------------------------------------------------------
# Natura: soglie di densita' e lunghezza. Influenzano quali catene completate
# vengono promosse a occorrenze vere e proprie.

# Densita' minima: la lunghezza della catena sulla traccia deve essere almeno
# MIN_DENSITY * lunghezza_segmento.
#   - AUMENTA: scarta occorrenze troncate/parziali, MA puo' scartare passaggi
#     legittimi con taglio d'imbocco aggressivo.
#   - DIMINUISCE: accetta match parziali (piu' recall, piu' rumore).
#   - PERFORMANCE: trascurabile (solo confronto numerico).
MIN_DENSITY = 0.5

# Densita' massima: la lunghezza della catena non puo' superare
# MAX_DENSITY * lunghezza_segmento.
#   - AUMENTA: accetta occorrenze con loop/agganci extra prima dell'uscita.
#   - DIMINUISCE: scarta occorrenze "lunghe" (loop non trimmati, deviazioni).
#   - PERFORMANCE: trascurabile.
MAX_DENSITY = 1.5

# ---------------------------------------------------------------------------
# CATEGORIA F - PROIEZIONE FINE DEL GATE END (FASE 6: tempi di attraversamento)
# ---------------------------------------------------------------------------
# Natura: parametri di scansione della valle di distanza gate-traccia in
# avanti dal centro. Influenzano l'accuratezza del tempo di FINE e quanto a
# lungo viene scandita la traccia.

# Indice di scansione per trovare la valle della fine (ridotto da 150):
# finestra, in punti di traccia, oltre la quale la valle non viene piu'
# cercata.
#   - AUMENTA: l'uscita dal geofence viene individuata anche quando il rider
#     resta vicino al gate per molti campioni; MA scansione piu' lunga.
#   - DIMINUISCE: piu' veloce, MA rischio di fermarsi prima dell'uscita vera
#     (tempo di fine inesatto).
#   - PERFORMANCE: il costo e' proporzionale alla finestra => SU tempo +.
END_PROJECTION_EXTRA_IDX = 30

# Distanza massima gate-proiezione per accettare la valle trovata.
#   - AUMENTA: accetta valley anche lontane dal gate (meno fallback).
#   - DIMINUISCE: valley scartate => fallback sul chord della catena (tempo di
#     fine meno raffinato).
#   - PERFORMANCE: trascurabile.
END_PROJECTION_ACCEPT_M = 45.0

# Risalita (metri) dal minimo che chiude la valle: la scansione si ferma quando
# la distanza risale piu' di cosi' rispetto al minimo corrente.
#   - AUMENTA: tollera piu' jitter => la scansione copre tutta la valle e
#     vede il vero minimo (piu' robusto), MA scandisce piu' a lungo.
#   - DIMINUISCE: la scansione si ferma alla prima risalita di rumore,
#     rischiando di chiudere PRIMA del vero minimo (risultato fragile).
#   - PERFORMANCE: al crescere, SU tempo + (scansione piu' lunga).
END_PROJECTION_EXIT_RISE_M = 3.0

# ---------------------------------------------------------------------------
# CATEGORIA G - PROIEZIONE FINE DEL GATE START (FASE 6, simmetrica alla END)
# ---------------------------------------------------------------------------
# Natura: identica alla categoria F ma a scansione ALL'INDIETRO. exit_rise e
# accept sono piu' stretti per catturare l'ingaggio nell'imbocco del segmento
# (punto piu' PRESTO possibile) senza slittamenti temporali.

# Finestra di scansione all'indietro (in punti di traccia) per la valle di
# start.
#   - AUMENTA: copre loop d'ingresso ampi e avvicinamenti lunghi; MA scansione
#     piu' lunga e rischio di arretrare su passaggi precedenti.
#   - DIMINUISCE: start meno accurato se l'ingresso reale e' lontano dal
#     primo punto della catena.
#   - PERFORMANCE: costo proporzionale alla finestra => SU tempo +.
START_PROJECTION_EXTRA_IDX = 120

# Distanza massima gate-proiezione per accettare la valle di start.
#   - AUMENTA: accetta valley lontane; DIMINUISCE: start resta sul chord.
#   - PERFORMANCE: trascurabile.
START_PROJECTION_ACCEPT_M = 45.0

# Risalita che chiude la valle di start (scansione all'indietro).
#   - AUMENTA: tollera piu' jitter => valle completa e vera (piu' robusto),
#     MA scansione piu' lunga; DIMINUISCE: esce al primo rumore (fragile).
#   - PERFORMANCE: al crescere, SU tempo +.
START_PROJECTION_EXIT_RISE_M = 3.0

# ---------------------------------------------------------------------------
# CATEGORIA H - TRIM DEGLI INGRESSI SPURI (FASE 4: pulizia dell'imbocco)
# ---------------------------------------------------------------------------
# Natura: contatori e distanze in INDICI. Influenzano l'eliminazione di loop,
# avvicinamenti e passaggi precedenti all'imbocco reale del segmento.

# Numero di punti di riferimento interno (indicizzato nel segmento) usati per
# punteggiare i punti d'imbocco: un punto catena e' preferito se e' vicino
# sia allo start sia al punto di riferimento (cioe' sta entrando, non
# uscendo).
#   - AUMENTA: riferimento piu' interno nel segmento => trim dell'imbocco piu'
#     aggressivo (taglia piu' avvicinamento).
#   - DIMINUISCE: trim piu' morbido, piu' punti pre-imbocco sopravvivono.
#   - PERFORMANCE: trascurabile.
TRIM_REF_POINTS = 1

# Quanti elementi iniziali della catena analizzare per individuare il punto di
# imbocco vero via punteggio di vicinanza.
#   - AUMENTA: copre avvicinamenti/loop iniziali lunghi.
#   - DIMINUISCE: loop lunghi non vengono piu' trimmati.
#   - PERFORMANCE: trascurabile (loop lineare su pochi elementi).
TRIM_CHECK_LIMIT = 60

# Salto di indici di traccia tra elementi consecutivi della catena che denuncia
# una discontinuita': se il buco e' piu' grande, l'ingresso vero e' DOPO il
# buco (il punto prima apparteneva al passaggio in salita/avvicinamento).
#   - AUMENTA: solo salti enormi tagliano => trim piu' conservativo.
#   - DIMINUISCE: trim aggressivo, MA rischio di tagliare inizi validi.
#   - PERFORMANCE: trascurabile.
TRIM_INDEX_GAP = 20

# ---------------------------------------------------------------------------
# CATEGORIA I - SELEZIONE E POST-PROCESSING (FASE 6 proiezioni / FASE 7 overlap)
# ---------------------------------------------------------------------------
# Natura: soglie decisionali. Influenzano quali chord partecipano alle
# proiezioni e quali occorrenze sopravvivono al filtro finale di overlap.

# Velocita' (km/h) sotto la quale il chord (k, k+1) e' considerato "fermo" e
# viene ignorato nelle proiezioni dei gate (il rider non sta attraversando il
# gate, e' fermo dentro il geofence).
#   - AUMENTA: piu' chord scartati come fermi => valle calcolata solo in
#     movimento; MA esclude anche tratti lenti reali (risalite).
#   - DIMINUISCE: anche i chord fermi (jitter GPS a velocita' ~0) entrano
#     nella valle => start/end potenzialmente sparpagliati.
#   - PERFORMANCE: al crescere, GIU tempo + (meno proiezioni calcolate).
STATIONARY_SPEED_KMH = 0.0

# Quota di punti di traccia gia' occupati da altre occorrenze oltre la quale
# un'occorrenza sovrapposta viene scartata (FASE 7).
#   - AUMENTA: ammette piu' sovrapposizioni => piu' risultati, MA anche
#     passaggi quasi-doppi sullo stesso tratto.
#   - DIMINUISCE: piu' selettivo: vince solo l'occorrenza migliore per ogni
#     tratto di traccia.
#   - PERFORMANCE: trascurabile.
OVERLAP_OCCUPANCY_THRESHOLD = 0.2
#
# =============================================================================

_EARTH_RADIUS_M = 6371000.0
_DEG2RAD = math.pi / 180.0


def _haversine_to_points(
    lat: float, lon: float, lats: np.ndarray, lons: np.ndarray
) -> np.ndarray:
    """Distanza geodesica in metri tra un punto e un array di punti."""
    dlat = (lats - lat) * _DEG2RAD
    dlon = (lons - lon) * _DEG2RAD
    a = (
        np.sin(dlat / 2.0) ** 2
        + np.cos(lat * _DEG2RAD)
        * np.cos(lats * _DEG2RAD)
        * np.sin(dlon / 2.0) ** 2
    )
    np.clip(a, 0.0, 1.0, out=a)
    return _EARTH_RADIUS_M * 2.0 * np.arctan2(np.sqrt(a), np.sqrt(1.0 - a))


def _project_point_on_segment(
    lat_p: float, lon_p: float, lat_a: float, lon_a: float, lat_b: float, lon_b: float
) -> float:
    """Proietta il punto P sul segmento AB e restituisce la frazione r [0, 1]."""
    cos_lat = math.cos(math.radians(lat_a))

    dlat_ab = lat_b - lat_a
    dlon_ab = (lon_b - lon_a) * cos_lat

    dlat_ap = lat_p - lat_a
    dlon_ap = (lon_p - lon_a) * cos_lat

    denom = dlat_ab**2 + dlon_ab**2
    if denom < 1e-15:
        return 0.0

    r = (dlat_ap * dlat_ab + dlon_ap * dlon_ab) / denom
    return max(0.0, min(1.0, r))


def _track_segment_kmh(track: Track, k: int) -> Optional[float]:
    """Velocita' media (km/h) tra due campioni consecutivi di traccia."""
    a = track.points[k]
    b = track.points[k + 1]
    if a.timestamp is None or b.timestamp is None:
        return None
    dt = (b.timestamp - a.timestamp).total_seconds()
    if dt <= 0:
        return None
    return (haversine_distance(a, b) / dt) * 3.6


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distanza geodesica scalare (m): stessa formula di _haversine_to_points."""
    dlat = (lat2 - lat1) * _DEG2RAD
    dlon = (lon2 - lon1) * _DEG2RAD
    a = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(lat1 * _DEG2RAD) * math.cos(lat2 * _DEG2RAD) * math.sin(dlon / 2.0) ** 2
    )
    if a > 1.0:
        a = 1.0
    return _EARTH_RADIUS_M * 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def _chord_moving(track: Track, k: int) -> bool:
    """True se il chord (k, k+1) non indica stazionarieta del rider."""
    speed = _track_segment_kmh(track, k)
    return speed is None or speed >= STATIONARY_SPEED_KMH


def _chord_projection(
    track: Track, k: int, target_lat: float, target_lon: float
) -> Tuple[float, float]:
    """Proiezione del gate sul chord (k, k+1): restituisce (r, distanza_m)."""
    pa = track.points[k]
    pb = track.points[k + 1]
    r = _project_point_on_segment(
        target_lat, target_lon, pa.latitude, pa.longitude, pb.latitude, pb.longitude
    )
    p_lat = pa.latitude + r * (pb.latitude - pa.latitude)
    p_lon = pa.longitude + r * (pb.longitude - pa.longitude)
    return r, _haversine_m(target_lat, target_lon, p_lat, p_lon)

def _find_best_track_projection(
    track: Track,
    target_lat: float,
    target_lon: float,
    center_idx: int,
) -> Tuple[int, float, float]:
    """Cerca il miglior segmento di traccia su cui proiettare un punto del segmento."""
    best_dist = float("inf")
    best_k = center_idx
    best_r = 0.0

    # Finestra di proiezione fissa a 0 (ex PROJECTION_WINDOW=0): si considera
    # solo il chord passato (center_idx); l'eventuale valle fino/oltre e'
    # gestito separatamente da _find_gate_valley.
    start_k = center_idx
    end_k = min(len(track.points) - 2, center_idx)

    for k in range(start_k, end_k + 1):
        if not _chord_moving(track, k):
            continue
        r, d = _chord_projection(track, k, target_lat, target_lon)
        if d < best_dist:
            best_dist = d
            best_k = k
            best_r = r

    return best_k, best_r, best_dist


def _find_gate_valley(
    track: Track,
    target_lat: float,
    target_lon: float,
    center_idx: int,
    max_extra_idx: int,
    accept_m: float,
    exit_rise_m: float,
    backward: bool,
) -> Tuple[Optional[int], float, float]:
    """Trova il valle della distanza gate-traccia partendo dal centro.

    backward=False (gate END): scansione in avanti dal centro e, sul
    plateau di jitter GPS, tie-break sul candidato piu' recente (ultima
    uscita dall'area del gate, geofence-exit). backward=True (gate START):
    scansione all'indietro e tie-break sul piu' antico (primo ingresso).
    La selezione a mediana rende il risultato indipendente dal punto in
    cui parte la scansione e dal campionamento (FIT vs GPX).
    """
    if backward:
        start_k = max(0, center_idx - max_extra_idx)
        end_k = min(len(track.points) - 2, center_idx)
        scan = range(end_k, start_k - 1, -1)
    else:
        start_k = max(0, center_idx)
        end_k = min(len(track.points) - 2, center_idx + max_extra_idx)
        scan = range(start_k, end_k + 1)

    seen: List[Tuple[int, float, float]] = []  # (k, r, d)
    run_min = float("inf")

    for k in scan:
        if not _chord_moving(track, k):
            continue
        r, d = _chord_projection(track, k, target_lat, target_lon)
        seen.append((k, r, d))
        if d < run_min:
            run_min = d
        elif d - run_min > exit_rise_m and run_min <= accept_m:
            break

    if not seen:
        return None, 0.0, float("inf")

    best_d = min(s[2] for s in seen)
    if best_d > accept_m:
        return None, 0.0, float("inf")

    # Tie-break: mediana del run contiguo contenente il minimo (stabile tra
    # formati ed estremi evitati: ne primo ingresso ne ultima uscita netta)
    k_sel, r_sel, d_sel = _median_tie_selection(seen, best_d)
    return k_sel, r_sel, d_sel


def _median_tie_selection(
    seen: List[Tuple[int, float, float]], best_d: float
) -> Tuple[int, float, float]:
    """Sceglie, tra i candidati quasi-equidistanti dal gate, il punto mediano
    del run contiguo di indici che contiene il minimo assoluto."""
    tie = sorted(s for s in seen if s[2] <= best_d)
    k_argmin = min(tie, key=lambda s: s[2])[0]
    groups: List[List[Tuple[int, float, float]]] = [[tie[0]]]
    for prev, item in zip(tie, tie[1:]):
        if item[0] - prev[0] <= 2:
            groups[-1].append(item)
        else:
            groups.append([item])
    chosen = next(g for g in groups if any(s[0] == k_argmin for s in g))
    return chosen[len(chosen) // 2]


def _extract_gpx_points(gpx) -> List[TrackPoint]:
    """Estrae i punti dal GPX: tracks se presenti, altrimenti routes."""
    points: List[TrackPoint] = []
    for gpx_track in gpx.tracks:
        for segment in gpx_track.segments:
            for point in segment.points:
                points.append(
                    TrackPoint(
                        latitude=point.latitude,
                        longitude=point.longitude,
                        altitude=point.elevation,
                        timestamp=point.time,
                    )
                )
    if not points:
        for route in gpx.routes:
            points.extend(
                TrackPoint(
                    latitude=point.latitude,
                    longitude=point.longitude,
                    altitude=point.elevation,
                    timestamp=point.time,
                )
                for point in route.points
            )
    return points


def load_strava_segments(folder_path: str) -> List[dict]:
    """Carica tutti i segmenti GPX dalla cartella Strava_Segments."""
    segments: List[dict] = []
    folder = Path(folder_path)
    if not folder.is_dir():
        return segments

    for gpx_file in sorted(folder.glob("*.gpx")):
        try:
            with open(gpx_file, "r", encoding="utf-8") as f:
                gpx = gpxpy.parse(f)

            points = _extract_gpx_points(gpx)

            if len(points) < 2:
                continue

            segment_name = gpx_file.stem
            segment_track = Track(segment_name)
            segment_track.points = points
            segment_track.invalidate_cache()

            segments.append(
                {
                    "name": segment_name,
                    "track": segment_track,
                    "file_path": str(gpx_file),
                }
            )
        except Exception:
            continue

    return segments


def _candidate_track_indices(
    track: Track,
    segment: Track,
    distance_threshold_m: float,
) -> List[np.ndarray]:
    """Per ogni punto del segmento, individua gli indici di traccia entro la soglia."""
    lats_track = track.latitudes
    lons_track = track.longitudes
    lats_seg = segment.latitudes
    lons_seg = segment.longitudes
    n_seg = len(segment.points)
    n_track = len(track.points)

    grid_size_deg = distance_threshold_m / 111000.0
    grid: Dict[Tuple[int, int], List[int]] = {}
    for i in range(n_track):
        cell = (int(lats_track[i] / grid_size_deg), int(lons_track[i] / grid_size_deg))
        grid.setdefault(cell, []).append(i)

    empty = np.empty(0, dtype=np.int64)
    candidates: List[np.ndarray] = []
    for si in range(n_seg):
        cell = (int(lats_seg[si] / grid_size_deg), int(lons_seg[si] / grid_size_deg))
        pool: List[np.ndarray] = []
        for d_lat in (-1, 0, 1):
            for d_lon in (-1, 0, 1):
                idx = grid.get((cell[0] + d_lat, cell[1] + d_lon))
                if idx:
                    pool.append(np.asarray(idx, dtype=np.int64))
        if not pool:
            candidates.append(empty)
            continue
        track_idx = np.concatenate(pool) if len(pool) > 1 else pool[0]
        dist = _haversine_to_points(
            lats_seg[si], lons_seg[si], lats_track[track_idx], lons_track[track_idx]
        )
        good = track_idx[dist <= distance_threshold_m]
        good.sort()
        candidates.append(good)
    return candidates


def _walk_forward(
    candidates: List[np.ndarray],
    track_profile: List[float],
    segment_profile: List[float],
    track_points: List[TrackPoint],
    segment_points: List[TrackPoint],
    start_track_idx: int,
    max_gap: int,
    progress_ratio: float,
    progress_slack_m: float,
) -> List[Tuple[int, int]]:
    """Camminata greedy in avanti che appaia il segmento alla traccia.

    Restituisce una catena di tuple (indice_segmento, indice_traccia).
    """
    chain: List[Tuple[int, int]] = []
    t = start_track_idx - 1
    prev_seg: Optional[int] = None
    prev_track: Optional[int] = None
    skipped = 0

    for seg_i in range(len(candidates)):
        cand = candidates[seg_i]
        k = bisect_left(cand, t)
        if k >= len(cand):
            skipped += 1
            if skipped > max_gap:
                break
            continue

        matched_track_i: Optional[int] = None
        best_match_dist = float("inf")

        # Cerchiamo il miglior candidato entro una finestra ragionevole
        for j in range(k, len(cand)):
            curr_track_i = int(cand[j])

            # Limite massimo di salto avanti nella traccia per punto segmento
            if prev_track is not None and (curr_track_i - prev_track > 150):
                break

            if prev_seg is not None and prev_track is not None:
                d_seg = segment_profile[seg_i] - segment_profile[prev_seg]
                d_track = track_profile[curr_track_i] - track_profile[prev_track]
                if d_track > max(d_seg * progress_ratio, d_seg + progress_slack_m):
                    continue

            # Calcolo distanza geografica per scegliere il punto piu' vicino
            d_geo = haversine_distance(segment_points[seg_i], track_points[curr_track_i])
            if d_geo < best_match_dist:
                best_match_dist = d_geo
                matched_track_i = curr_track_i

            # Se siamo molto vicini (< 10m), ottimizziamo prendendo il punto
            if d_geo < 10.0:
                break

        if matched_track_i is None:
            skipped += 1
            if skipped > max_gap:
                break
            continue

        chain.append((seg_i, matched_track_i))
        prev_seg = seg_i
        prev_track = matched_track_i
        t = matched_track_i
        skipped = 0

    return chain


def _trim_chain_start(
    chain: List[Tuple[int, int]],
    segment_track: Track,
    track: Track,
    reverse: bool = False,
) -> List[Tuple[int, int]]:
    """Elimina i loop/avvicinamenti iniziali identificando il salto temporale o la discontinuita'
    causata dal passaggio in salita/avvicinamento prima dell'imbocco effettivo.
    """
    if len(chain) < TRIM_REF_POINTS:
        return chain

    # Prendiamo il punto di inizio e un punto di riferimento leggermente avanzato nel segmento
    n_seg_pts = len(segment_track.points)
    p_start = segment_track.points[-1 if reverse else 0]
    # Clamp agli indici validi: valori degeneri del parametro (es. 0 o negativi,
    # esplorati dall'ottimizzatore) non devono produrre un IndexError.
    ref_idx = (
        max(0, min(n_seg_pts - 1, n_seg_pts - int(TRIM_REF_POINTS)))
        if reverse
        else max(0, min(int(TRIM_REF_POINTS), n_seg_pts - 1))
    )
    p_ref = segment_track.points[ref_idx]

    # Cerchiamo se nella catena iniziale c'e' un "gap" o un'inversione di distanza dal punto di riferimento
    best_start_idx = 0
    max_progress_ratio = -1.0

    check_limit = min(TRIM_CHECK_LIMIT, len(chain))

    for i in range(check_limit):
        seg_i, trk_i = chain[i]
        pt_track = track.points[trk_i]

        d_start = haversine_distance(pt_track, p_start)
        d_ref = haversine_distance(pt_track, p_ref)

        # Se il punto e' vicino alla partenza, valutiamo quanto e' proiettato verso l'interno del trail
        if d_start <= DISTANCE_THRESHOLD_M:
            # Punteggio di vicinanza al prosieguo del trail
            score = (DISTANCE_THRESHOLD_M - d_start) + (DISTANCE_THRESHOLD_M - d_ref)

            # Se troviamo un punto che e' sia vicino allo start sia nettamente piu vicino al punto interno,
            # lo preferiamo rispetto ai punti precedenti dove il ciclista si stava allontanando
            if score > max_progress_ratio:
                max_progress_ratio = score
                best_start_idx = i

    # Se c'e' un salto di indice di traccia anomalo tra due elementi vicini della catena iniziale,
    # significa che il primo apparteneva al passaggio in salita e il secondo al passaggio in discesa!
    for i in range(min(TRIM_INDEX_GAP, len(chain) - 1)):
        trk_curr = chain[i][1]
        trk_next = chain[i + 1][1]

        # Se c'e' un "buco" temporale/di indici nella traccia > TRIM_INDEX_GAP punti mentre il segmento e' all'inizio,
        # l'ingresso vero e' dopo il buco!
        if trk_next - trk_curr > TRIM_INDEX_GAP:
            return chain[i + 1 :]

    return chain[best_start_idx:]


def _masked_candidates(
    candidates: List[np.ndarray],
    used: np.ndarray,
    reverse: bool,
) -> List[np.ndarray]:
    """Restituisce i candidati escludendo gli indici usati."""
    order = candidates[::-1] if reverse else candidates
    masked: List[np.ndarray] = []
    for cand in order:
        if len(cand):
            cand = cand[~used[cand]]
        masked.append(cand)
    return masked


def _reversed_profile(profile: List[float]) -> List[float]:
    """Profilo di distanza cumulativa del segmento percorso al contrario."""
    total = profile[-1]
    return [total - value for value in profile[::-1]]


def _find_occurrences(
    track: Track,
    segment_track: Track,
    candidates: List[np.ndarray],
    track_profile: List[float],
    segment_profile: List[float],
    start_tol: int,
    end_tol: int,
    max_gap: int,
    progress_ratio: float = PROGRESS_RATIO,
    progress_slack_m: float = PROGRESS_SLACK_M,
    min_density: float = MIN_DENSITY,
    max_density: float = MAX_DENSITY,
) -> List[Tuple[bool, float, List[Tuple[int, int]]]]:
    """Trova tutte le occorrenze del segmento nella traccia.

    Returns:
        List di (reverse, avg_dist_m, chain)
    """
    n_seg = len(candidates)
    n_track = len(track_profile)
    seg_length = segment_profile[-1]
    used = np.zeros(n_track, dtype=bool)
    occurrences: List[Tuple[bool, float, List[Tuple[int, int]]]] = []

    for reverse in (False, True):
        seg_pts_order = segment_track.points[::-1] if reverse else segment_track.points
        while True:
            masked = _masked_candidates(candidates, used, reverse)
            profile = _reversed_profile(segment_profile) if reverse else segment_profile

            anchor_pool: List[int] = []
            for i in range(min(start_tol + 1, n_seg)):
                arr = masked[i]
                if len(arr) == 0:
                    continue
                anchor_pool.append(int(arr[0]))
                prev = int(arr[0])
                for j in range(1, len(arr)):
                    x = int(arr[j])
                    if x - prev > CLUSTER_GAP_IDX:
                        # Rilevato un nuovo cluster di passaggi sulla traccia
                        for k in range(j, min(j + ANCHOR_SCAN_RANGE, len(arr))):
                            anchor_pool.append(int(arr[k]))
                    prev = x
            anchors = list(dict.fromkeys(anchor_pool))

            found = False
            for s0 in anchors:
                chain = _walk_forward(
                    masked,
                    track_profile,
                    profile,
                    track.points,
                    seg_pts_order,
                    s0,
                    max_gap,
                    progress_ratio,
                    progress_slack_m,
                )
                if not chain:
                    continue

                # Applichiamo il trim sanificato dell'ingresso
                chain = _trim_chain_start(chain, segment_track, track, reverse=reverse)

                seg_start, _ = chain[0]
                seg_end, _ = chain[-1]

                if seg_start > start_tol or seg_end < n_seg - 1 - end_tol:
                    continue
                t0 = min(ti for _, ti in chain)
                t1 = max(ti for _, ti in chain)
                length_m = track_profile[t1] - track_profile[t0]
                if not (min_density * seg_length <= length_m <= max_density * seg_length):
                    continue

                # Calcolo della distanza media geografica della catena per lo scoring
                chain_dists = []
                for s_i, t_i in chain:
                    d = haversine_distance(seg_pts_order[s_i], track.points[t_i])
                    chain_dists.append(d)
                avg_dist_m = float(np.mean(chain_dists)) if chain_dists else float("inf")

                occurrences.append((reverse, avg_dist_m, chain))
                used[t0 : t1 + 1] = True
                found = True
                break
            if not found:
                break

    return occurrences


def _gap_crossing_time(
    track: Track, k_val: int, r_val: float
) -> Optional[float]:
    """Tempo di attraversamento del gate sul chord (k_val, r_val).

    Ogni corda e' trattata come un attraversamento di gate: NON viene mai
    interpolata linearmente nel vuoto. Si assume il bordo del chord piu' vicino
    alla proiezione del gate (r < 0.5 -> ta, r >= 0.5 -> tb), in accordo con il
    comportamento osservato di Strava
    (errore medio ~0.1s su 4 passaggi con buco sul gate; riferimento: BePa UP
    TRAIL su Pedalata_pomeridiana_11072026.gpx, dropout di 10s, r=0.49).
    """
    ta = track.points[k_val].timestamp
    tb = track.points[k_val + 1].timestamp
    if not ta or not tb:
        return None
    # Bordo del buco piu vicino alla proiezione del gate.
    return (ta if r_val < 0.5 else tb).timestamp()


def find_strava_segments_in_track(
    strava_segments: List[dict],
    track: Track,
    distance_threshold_m: float = DISTANCE_THRESHOLD_M,
) -> List[dict]:
    """Individua i segmenti Strava all'interno di una traccia caricata."""
    if not track or not track.points or len(track.points) < 2:
        return []

    track_profile, _ = track_distance_profile(track)
    results: List[dict] = []

    for strava_seg in strava_segments:
        segment_track = strava_seg["track"]
        n_seg = len(segment_track.points)
        candidates = _candidate_track_indices(track, segment_track, distance_threshold_m)
        segment_profile, _ = track_distance_profile(segment_track)

        start_tol = max(2, int(START_TOL_RATIO * n_seg))
        end_tol = max(2, int(END_TOL_RATIO * n_seg))
        max_gap = max(6, int(MAX_GAP_RATIO * n_seg))

        occurrences = _find_occurrences(
            track,
            segment_track,
            candidates,
            track_profile,
            segment_profile,
            start_tol=start_tol,
            end_tol=end_tol,
            max_gap=max_gap,
            progress_ratio=PROGRESS_RATIO,
            progress_slack_m=PROGRESS_SLACK_M,
            min_density=MIN_DENSITY,
            max_density=MAX_DENSITY,
        )

        for reverse, avg_dist_m, chain in occurrences:
            if not reverse:
                seg_start, seg_end = segment_track.points[0], segment_track.points[-1]
            else:
                seg_start, seg_end = segment_track.points[-1], segment_track.points[0]
            # t0_chain e' garantito essere il punto effettivo post-trim e post-salto temporale
            t0_chain = chain[0][1]
            t1_chain = chain[-1][1]

            # PROIEZIONE START (Usa t0_chain)
            k_start, r_start, d_start = _find_best_track_projection(
                track, seg_start.latitude, seg_start.longitude, t0_chain
            )

            # PROIEZIONE END
            k_end, r_end, d_end = _find_best_track_projection(
                track, seg_end.latitude, seg_end.longitude, t1_chain
            )

            # Proiezione END: valle in avanti con selezione deterministica
            # (tie-break a mediana).
            k_valley, r_valley, d_valley = _find_gate_valley(
                track, seg_end.latitude, seg_end.longitude, t1_chain,
                max_extra_idx=END_PROJECTION_EXTRA_IDX,
                accept_m=END_PROJECTION_ACCEPT_M,
                exit_rise_m=END_PROJECTION_EXIT_RISE_M,
                backward=False,
            )
            if k_valley is not None:
                k_end, r_end, d_end = k_valley, r_valley, d_valley

            # Proiezione START: valle all'indietro con i parametri dedicati
            # (finestra di scansione piu corta, accept/rise piu stretti per non
            # arretrare in loop di ingresso o passaggi precedenti del segmento).
            k_last_valley, r_last_valley, d_last_valley = _find_gate_valley(
                track, seg_start.latitude, seg_start.longitude, t0_chain,
                max_extra_idx=START_PROJECTION_EXTRA_IDX,
                accept_m=START_PROJECTION_ACCEPT_M,
                exit_rise_m=START_PROJECTION_EXIT_RISE_M,
                backward=True,
            )
            # Valle autoritativa anche per lo start: la selezione interna e'
            # deterministica, il punto-catena non influenza piu' il risultato.
            if k_last_valley is not None:
                k_start, r_start, d_start = k_last_valley, r_last_valley, d_last_valley

            ts_start = _gap_crossing_time(track, k_start, r_start)
            ts_end = _gap_crossing_time(track, k_end, r_end)

            time_sec = None
            if ts_start is not None and ts_end is not None:
                time_sec = abs(ts_end - ts_start)

            t0 = min(int(k_start), int(k_end))
            t1 = max(int(k_start) + 1, int(k_end) + 1)
            length_m = track_profile[t1] - track_profile[t0]
            sub_pts = track.points[t0 : t1 + 1]

            if time_sec is None and sub_pts[0].timestamp and sub_pts[-1].timestamp:
                time_sec = (sub_pts[-1].timestamp - sub_pts[0].timestamp).total_seconds()

            speeds = [p.speed * 3.6 for p in sub_pts if p.speed is not None]
            avg_speed = float(np.mean(speeds)) if speeds else None
            if avg_speed is None and time_sec and time_sec > 0 and length_m > 0:
                avg_speed = (length_m / time_sec) * 3.6

            alts = [p.altitude for p in sub_pts if p.altitude is not None]

            hrs = [p.heart_rate for p in sub_pts if p.heart_rate is not None]
            avg_hr = float(np.mean(hrs)) if hrs else None

            slope = None
            if len(alts) >= 2 and length_m > 0:
                slope = ((alts[-1] - alts[0]) / length_m) * 100.0

            coords = [(p.latitude, p.longitude) for p in sub_pts]

            results.append(
                {
                    "segment_name": strava_seg["name"],
                    "track_name": track.name,
                    "track": track,
                    "start_idx": t0,
                    "end_idx": t1,
                    "start_dist_m": track_profile[t0],
                    "end_dist_m": track_profile[t1],
                    "length_m": length_m,
                    "time_sec": time_sec,
                    "avg_speed": avg_speed,
                    "avg_hr": avg_hr,
                    "avg_dist_m": avg_dist_m,
                    "slope": slope,
                    "coords": coords,
                    "direction": "reverse" if reverse else "forward",
                    "n_match_points": len(chain),
                    "segment_point_count": n_seg,
                }
            )

    # Ordinamento per qualita' del match: priorita' alla vicinanza geografica (distanza media minore)
    # e secondariamente al numero di punti matchati.
    results.sort(key=lambda x: (x["avg_dist_m"], -x["n_match_points"]))

    final_results = []
    occupied = np.zeros(len(track.points), dtype=bool)

    for res in results:
        t0, t1 = res["start_idx"], res["end_idx"]
        if np.sum(occupied[t0 : t1 + 1]) > OVERLAP_OCCUPANCY_THRESHOLD * (t1 - t0 + 1):
            continue

        final_results.append(res)
        occupied[t0 : t1 + 1] = True

    final_results.sort(key=lambda occ: occ["start_idx"])
    return final_results
