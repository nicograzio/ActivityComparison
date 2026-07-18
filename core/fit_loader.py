import logging

from fitparse import FitFile

from core.track import Track, TrackPoint


logging.getLogger("fitparse").disabled = True


def load_fit(path):
    track = Track(path)

    fit = FitFile(path)

    for record in fit.get_messages("record"):
        data = {}

        for field in record:
            data[field.name] = field.value

        latitude = data.get("position_lat")
        longitude = data.get("position_long")

        if latitude is None or longitude is None:
            continue

        latitude = latitude * (180 / 2**31)
        longitude = longitude * (180 / 2**31)

        point = TrackPoint(
            latitude=latitude,
            longitude=longitude,
            altitude=data.get("altitude"),
            timestamp=data.get("timestamp"),
            speed=data.get("speed"),
            heart_rate=data.get("heart_rate"),
        )

        track.add_point(point)

    return track