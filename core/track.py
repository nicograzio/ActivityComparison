"""Modello dati fondamentale per le tracce dell'applicazione.

Rappresenta in memoria le attività caricate da file FIT o GPX e fornisce
proprietà vettoriali NumPy con caching lazy per velocizzare i calcoli analitici.
"""

from dataclasses import dataclass
from typing import List, Optional, Any, Dict
import numpy as np


@dataclass(slots=True)
class WeatherInfo:
    """Informazioni meteo associate a una traccia."""
    condition: Optional[str] = None
    temperature: Optional[float] = None
    wind_speed: Optional[float] = None
    humidity: Optional[int] = None
    source: str = "unknown"

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
    cadence: Optional[int] = None
    temperature: Optional[float] = None
    water_temp: Optional[float] = None
    depth: Optional[float] = None
    power: Optional[float] = None
    course: Optional[float] = None


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
        self.weather_start: Optional[WeatherInfo] = None
        self.weather_end: Optional[WeatherInfo] = None

        # Cache lazy per le rappresentazioni NumPy
        self._np_latitudes: Optional[np.ndarray] = None
        self._np_longitudes: Optional[np.ndarray] = None
        self._np_altitudes: Optional[np.ndarray] = None
        self._np_heart_rates: Optional[np.ndarray] = None
        self._np_speeds: Optional[np.ndarray] = None
        self._np_cadences: Optional[np.ndarray] = None
        self._np_temperatures: Optional[np.ndarray] = None
        self._np_water_temps: Optional[np.ndarray] = None
        self._np_depths: Optional[np.ndarray] = None
        self._np_powers: Optional[np.ndarray] = None
        self._np_courses: Optional[np.ndarray] = None

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
        self._np_cadences = None
        self._np_temperatures = None
        self._np_water_temps = None
        self._np_depths = None
        self._np_powers = None
        self._np_courses = None

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

    @property
    def cadences(self) -> np.ndarray:
        """Array NumPy delle cadenze (float64, np.nan per valori assenti)."""
        if self._np_cadences is None:
            self._np_cadences = np.array(
                [p.cadence if p.cadence is not None else np.nan for p in self.points],
                dtype=np.float64
            )
        return self._np_cadences

    @property
    def temperatures(self) -> np.ndarray:
        """Array NumPy delle temperature (float64, np.nan per valori assenti)."""
        if self._np_temperatures is None:
            self._np_temperatures = np.array(
                [p.temperature if p.temperature is not None else np.nan for p in self.points],
                dtype=np.float64
            )
        return self._np_temperatures

    @property
    def water_temps(self) -> np.ndarray:
        """Array NumPy delle temperature dell'acqua (float64, np.nan per valori assenti)."""
        if self._np_water_temps is None:
            self._np_water_temps = np.array(
                [p.water_temp if p.water_temp is not None else np.nan for p in self.points],
                dtype=np.float64
            )
        return self._np_water_temps

    @property
    def depths(self) -> np.ndarray:
        """Array NumPy delle profondità (float64, np.nan per valori assenti)."""
        if self._np_depths is None:
            self._np_depths = np.array(
                [p.depth if p.depth is not None else np.nan for p in self.points],
                dtype=np.float64
            )
        return self._np_depths

    @property
    def powers(self) -> np.ndarray:
        """Array NumPy delle potenze (float64, np.nan per valori assenti)."""
        if self._np_powers is None:
            self._np_powers = np.array(
                [p.power if p.power is not None else np.nan for p in self.points],
                dtype=np.float64
            )
        return self._np_powers

    @property
    def courses(self) -> np.ndarray:
        """Array NumPy delle direzioni (float64, np.nan per valori assenti)."""
        if self._np_courses is None:
            self._np_courses = np.array(
                [p.course if p.course is not None else np.nan for p in self.points],
                dtype=np.float64
            )
        return self._np_courses
