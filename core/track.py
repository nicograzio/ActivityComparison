from dataclasses import dataclass


@dataclass
class TrackPoint:
    latitude: float
    longitude: float
    altitude: float | None = None
    timestamp: object | None = None
    speed: float | None = None
    heart_rate: int | None = None


class Track:

    def __init__(self, name):
        self.name = name
        self.points = []

    def add_point(self, point):
        self.points.append(point)
