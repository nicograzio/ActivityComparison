class TrackCapabilities:
    def __init__(self, track):
        self.points = len(track.points)
        self.has_position = self._has_position(track)
        self.has_elevation = self._has_elevation(track)
        self.has_timestamp = self._has_timestamp(track)
        self.has_speed = self._has_speed(track)
        self.has_heart_rate = self._has_heart_rate(track)

    @property
    def available_modes(self):
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
        return all(
            p.latitude is not None and p.longitude is not None
            for p in track.points
        )

    @staticmethod
    def _has_elevation(track):
        return any(p.altitude is not None for p in track.points)

    @staticmethod
    def _has_timestamp(track):
        return any(p.timestamp is not None for p in track.points)

    @staticmethod
    def _has_speed(track):
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
        return any(p.heart_rate is not None for p in track.points)
