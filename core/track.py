"""Modello dati fondamentale per le tracce dell'applicazione.

Rappresenta in memoria le attività caricate da file FIT o GPX e fornisce
proprietà vettoriali NumPy con caching lazy per velocizzare i calcoli analitici.
"""

from dataclasses import dataclass
from typing import List, Optional, Any
import numpy as np


@dataclass(slots=True)
class TrackPoint:
    """Singolo campione di un'attività GPS.

    Attributi:
        latitude: Latitudine in gradi decimali.
        longitude: Longitudine in gradi decimali.
        altitude: Altitudine opzionale in metri.
        timestamp: Timestamp opzionale della rilevazione.
        speed: Velocità opzionale in m/s fornita dal dispositivo.
        heart_rate: Frequenza cardiaca opzionale in bpm.
    """

    latitude: float
    longitude: float
    altitude: Optional[float] = None
    timestamp: Optional[Any] = None
    speed: Optional[float] = None
    heart_rate: Optional[int] = None


class Track:
    """Contenitore per i punti di un'attività.

    Offre l'accesso vettorializzato tramite NumPy alle varie metriche per calcoli ad alte prestazioni.
    """

    def __init__(self, name: str, start_distance_m: float = 0.0) -> None:
        """Inizializza un contenitore traccia vuoto.

        Args:
            name: Nome visualizzato (solitamente il percorso del file).
            start_distance_m: Offset di distanza in metri dall'inizio della traccia completa.
        """
        self.name: str = name
        self.points: List[TrackPoint] = []
        self.start_distance_m: float = start_distance_m

        # Cache lazy per le rappresentazioni NumPy
        self._np_latitudes: Optional[np.ndarray] = None
        self._np_longitudes: Optional[np.ndarray] = None
        self._np_altitudes: Optional[np.ndarray] = None
        self._np_heart_rates: Optional[np.ndarray] = None
        self._np_speeds: Optional[np.ndarray] = None

    def add_point(self, point: TrackPoint) -> None:
        """Aggiunge un punto alla traccia e invalida la cache.

        Args:
            point: Istanza di ``TrackPoint`` da inserire.
        """
        self.points.append(point)
        self.invalidate_cache()

    def invalidate_cache(self) -> None:
        """Invalida la cache degli array vettoriali."""
        self._np_latitudes = None
        self._np_longitudes = None
        self._np_altitudes = None
        self._np_heart_rates = None
        self._np_speeds = None

    @property
    def latitudes(self) -> np.ndarray:
        """Array NumPy delle latitudini (float64)."""
        if self._np_latitudes is None:
            self._np_latitudes = np.array([p.latitude for p in self.points], dtype=np.float64)
        return self._np_latitudes

    @property
    def longitudes(self) -> np.ndarray:
        """Array NumPy delle longitudini (float64)."""
        if self._np_longitudes is None:
            self._np_longitudes = np.array([p.longitude for p in self.points], dtype=np.float64)
        return self._np_longitudes

    @property
    def altitudes(self) -> np.ndarray:
        """Array NumPy delle altitudini (float64, np.nan per valori assenti)."""
        if self._np_altitudes is None:
            self._np_altitudes = np.array(
                [p.altitude if p.altitude is not None else np.nan for p in self.points],
                dtype=np.float64
            )
        return self._np_altitudes

    @property
    def heart_rates(self) -> np.ndarray:
        """Array NumPy delle frequenze cardiache (float64, np.nan per valori assenti)."""
        if self._np_heart_rates is None:
            self._np_heart_rates = np.array(
                [p.heart_rate if p.heart_rate is not None else np.nan for p in self.points],
                dtype=np.float64
            )
        return self._np_heart_rates

    @property
    def speeds(self) -> np.ndarray:
        """Array NumPy delle velocità in m/s (float64, np.nan per valori assenti)."""
        if self._np_speeds is None:
            self._np_speeds = np.array(
                [p.speed if p.speed is not None else np.nan for p in self.points],
                dtype=np.float64
            )
        return self._np_speeds
