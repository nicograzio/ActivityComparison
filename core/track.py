"""Core track model used throughout the application.

The GUI, loaders and analyzers all operate on this in-memory representation.

Called by:
    - ``core.gpx_loader.load_gpx``
    - ``core.fit_loader.load_fit``
    - ``core.analyzer.trim_track_by_distance``

Consumed by:
    - ``ui.track_panel.TrackPanel``
    - ``ui.main_window.MainWindow``
    - map renderers
    - graph generation helpers
"""

from dataclasses import dataclass


@dataclass
class TrackPoint:
    """Single activity sample.

    Attributes:
        latitude: Latitude in decimal degrees.
        longitude: Longitude in decimal degrees.
        altitude: Optional altitude in meters.
        timestamp: Optional timestamp as returned by the loader.
        speed: Optional speed in meters/second when provided by the source.
        heart_rate: Optional heart-rate sample in bpm.
    """

    latitude: float
    longitude: float
    altitude: float | None = None
    timestamp: object | None = None
    speed: float | None = None
    heart_rate: int | None = None


class Track:
    """Mutable container for the points of one activity.

    Called by:
        - loaders when assembling a parsed file
        - ``core.analyzer.trim_track_by_distance`` when producing a visible subset

    Methods:
        add_point: append a sample to the track.
    """

    def __init__(self, name):
        """Create an empty track container.

        Args:
            name: Display name, usually the source file path.
        """
        self.name = name
        self.points = []

    def add_point(self, point):
        """Append a sample to the track.

        Called by:
            - GPX/FIT loaders
            - ``trim_track_by_distance`` when reconstructing the visible range

        Args:
            point: ``TrackPoint`` to append.

        Returns:
            None.
        """
        self.points.append(point)