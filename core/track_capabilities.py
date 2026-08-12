"""Ispeziona ed estrae le capacità e le statistiche disponibili in una traccia.

Fornisce alla UI le modalità di colorazione disponibili e le statistiche generali
sulle metriche (altitudine, pendenza, frequenza cardiaca, velocità).
"""

from typing import Dict, List, Any, Optional
import numpy as np

from core.track import Track
from core.analyzer import calculate_point_speed, calculate_slope_range


class TrackCapabilities:
    """Istantanea dei campi e delle metriche disponibili in una traccia."""

    def __init__(self, track: Track) -> None:
        """Costruisce un'istantanea delle capacità a partire da una traccia.

        Args:
            track: Istanza di ``Track`` da ispezionare.
        """
        self.points: int = len(track.points)
        self.has_position: bool = self._has_position(track)
        self.has_elevation: bool = self._has_elevation(track)
        self.has_timestamp: bool = self._has_timestamp(track)
        self.has_speed: bool = self._has_speed(track)
        self.has_heart_rate: bool = self._has_heart_rate(track)
        self.has_weather: bool = self._has_weather(track)

        # Statistiche sintetiche per i tooltip della UI
        self.stats: Dict[str, Dict[str, Optional[float]]] = self._calculate_stats(track)

    def _calculate_stats(self, track: Track) -> Dict[str, Dict[str, Optional[float]]]:
        """Calcola le statistiche di min, max e media per le metriche con NumPy."""
        stats: Dict[str, Dict[str, Optional[float]]] = {
            "elevation": {"min": None, "max": None},
            "slope": {"min": None, "max": None},
            "heart_rate": {"min": None, "max": None, "avg": None},
            "speed": {"min": None, "max": None, "avg": None},
        }

        if not track.points:
            return stats

        # 1. Altitudine (usando l'array NumPy della traccia)
        alts = track.altitudes
        valid_alts = alts[~np.isnan(alts)]
        if len(valid_alts) > 0:
            stats["elevation"]["min"] = float(np.min(valid_alts))
            stats["elevation"]["max"] = float(np.max(valid_alts))

        # 2. Pendenza (utilizzando la funzione del modulo analyzer)
        slope_min, slope_max = calculate_slope_range(track)
        if slope_min is not None and slope_max is not None:
            stats["slope"]["min"] = slope_min
            stats["slope"]["max"] = slope_max

        # 3. Frequenza cardiaca
        hrs = track.heart_rates
        valid_hrs = hrs[~np.isnan(hrs)]
        if len(valid_hrs) > 0:
            stats["heart_rate"]["min"] = float(np.min(valid_hrs))
            stats["heart_rate"]["max"] = float(np.max(valid_hrs))
            stats["heart_rate"]["avg"] = float(np.mean(valid_hrs))

        # 4. Velocità
        speeds = []
        for i in range(1, len(track.points)):
            s = calculate_point_speed(track.points[i - 1], track.points[i])
            if s is not None:
                speeds.append(s)

        if speeds:
            stats["speed"]["min"] = float(min(speeds))
            stats["speed"]["max"] = float(max(speeds))
            stats["speed"]["avg"] = float(sum(speeds) / len(speeds))

        return stats

    @property
    def available_modes(self) -> List[str]:
        """Elenco delle modalità di visualizzazione/colorazione disponibili per la UI."""
        modes = ["Nessuna"]

        if self.has_speed:
            modes.append("Velocità")

        if self.has_elevation:
            modes.append("Pendenza")
            modes.append("Altitudine")

        if self.has_heart_rate:
            modes.append("Frequenza cardiaca")

        return modes

    @property
    def summary(self) -> Dict[str, Any]:
        """Sommario compatto delle capacità per l'interfaccia utente."""
        return {
            "points": self.points,
            "gps": self.has_position,
            "elevation": self.has_elevation,
            "timestamp": self.has_timestamp,
            "speed": self.has_speed,
            "heart_rate": self.has_heart_rate,
            "weather": self.has_weather,
        }

    @staticmethod
    def _has_position(track: Track) -> bool:
        """Verifica se la traccia ha coordinate GPS valide per tutti i punti."""
        return len(track.points) > 0 and all(
            p.latitude is not None and p.longitude is not None for p in track.points
        )

    @staticmethod
    def _has_elevation(track: Track) -> bool:
        """Verifica se almeno un punto ha l'altitudine."""
        return any(p.altitude is not None for p in track.points)

    @staticmethod
    def _has_timestamp(track: Track) -> bool:
        """Verifica se almeno un punto ha il timestamp."""
        return any(p.timestamp is not None for p in track.points)

    @staticmethod
    def _has_speed(track: Track) -> bool:
        """Verifica se la velocità è presente o calcolabile."""
        if any(p.speed is not None for p in track.points):
            return True

        return any(p.timestamp is not None for p in track.points) and all(
            p.latitude is not None and p.longitude is not None for p in track.points
        )

    @staticmethod
    def _has_heart_rate(track: Track) -> bool:
        """Verifica se almeno un punto ha la frequenza cardiaca."""
        return any(p.heart_rate is not None for p in track.points)

    @staticmethod
    def _has_weather(track: Track) -> bool:
        """Verifica se la traccia dispone di informazioni meteo (inizio o fine)."""
        return track.weather_start is not None or track.weather_end is not None
