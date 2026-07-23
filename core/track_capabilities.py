"""Inspect which activity metrics are available in a track.

The UI uses this module to populate the color-mode combo box and to summarize
which data fields are present in the loaded activity.

Called by:
    - ``ui.track_panel.TrackPanel.import_file``
    - ``ui.track_panel.TrackPanel.show_summary``
"""

class TrackCapabilities:
    """Snapshot of the data fields available in a track.

    Called by:
        - ``TrackPanel.import_file`` after loading a GPX/FIT file
    """

    def __init__(self, track):
        """Build a capability snapshot from a track.

        Args:
            track: Track to inspect.
        """
        self.points = len(track.points)
        self.has_position = self._has_position(track)
        self.has_elevation = self._has_elevation(track)
        self.has_timestamp = self._has_timestamp(track)
        self.has_speed = self._has_speed(track)
        self.has_heart_rate = self._has_heart_rate(track)

    @property
    def available_modes(self):
        """List the color modes the UI can offer.

        Called by:
            - ``TrackPanel.import_file`` when populating the combobox

        Returns:
            list[str]: Human readable mode labels.
        """
        modes = ["Nessuna"]

        if self.has_speed:
            modes.append("Velocità")

        if self.has_elevation:
            modes.append("Pendenza")

        if self.has_heart_rate:
            modes.append("Frequenza cardiaca")

        return modes

    @property
    def summary(self):
        """Return the compact capability summary shown in the UI.

        Called by:
            - ``TrackPanel.show_summary``

        Returns:
            dict[str, object]: Dictionary of supported fields.
        """
        return {
            "points": self.points,
            "gps": self.has_position,
            "elevation": self.has_elevation,
            "timestamp": self.has_timestamp,
            "speed": self.has_speed,
            "heart_rate": self.has_heart_rate,
        }

    @staticmethod
    def _has_position(track):
        """Check whether every point has latitude and longitude.

        Returns:
            bool: True if all points have coordinates.
        """
        return all(
            p.latitude is not None and p.longitude is not None
            for p in track.points
        )

    @staticmethod
    def _has_elevation(track):
        """Check whether at least one point has altitude.

        Returns:
            bool: True if altitude is available.
        """
        return any(p.altitude is not None for p in track.points)

    @staticmethod
    def _has_timestamp(track):
        """Check whether at least one point has a timestamp.

        Returns:
            bool: True if a timestamp is available.
        """
        return any(p.timestamp is not None for p in track.points)

    @staticmethod
    def _has_speed(track):
        """Check whether speed can be read or derived for the track.

        Returns:
            bool: True if speed is recorded or derivable.
        """
        # La velocità può essere già presente nel file (FIT/GPX con extension speed)
        # oppure può essere calcolata dalla sequenza GPS quando sono disponibili timestamp.
        has_recorded_speed = any(p.speed is not None for p in track.points)

        if has_recorded_speed:
            return True

        return (
            any(p.timestamp is not None for p in track.points)
            and all(
                p.latitude is not None and p.longitude is not None
                for p in track.points
            )
        )

    @staticmethod
    def _has_heart_rate(track):
        """Check whether at least one point has heart rate.

        Returns:
            bool: True if heart rate is available.
        """
        return any(p.heart_rate is not None for p in track.points)