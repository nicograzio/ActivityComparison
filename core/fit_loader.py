from fitparse import FitFile

from core.track import Track, TrackPoint

DEBUG_IMPORT = True


def load_fit(path):
    track = Track(path)

    fit = FitFile(path)

    for index, record in enumerate(fit.get_messages("record")):
        data = {}

        for field in record:
            data[field.name] = field.value

        if DEBUG_IMPORT and index < 10:
            print("=== FIT RAW RECORD", index, "===")
            for key, value in data.items():
                print(key, ":", value)

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

        if DEBUG_IMPORT and index < 10:
            print("=== TRACK POINT", index, "===")
            print(vars(point))

        track.add_point(point)

    print("FIT points loaded:", len(track.points))

    return track
