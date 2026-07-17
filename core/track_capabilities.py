class TrackCapabilities:
    def __init__(self, track):
        self.points = len(track.points)
        self.has_position = self._has_position(track)
        self.has_elevation = self._has_elevation(track)
        self.has_timestamp = self._has_timestamp(track)
        self.has_speed = self._has_speed(track)
        self.has_heart_rate = self._has_heart_rate(track)

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
        return any(p.speed is not None for p in track.points)

    @staticmethod
    def _has_heart_rate(track):
        return any(p.heart_rate is not None for p in track.points)
