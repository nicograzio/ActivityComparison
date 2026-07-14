import requests


class TileLoader:

    def __init__(self):
        self.cache = {}

    def get_tile(self, x, y, zoom):
        key = (x, y, zoom)

        if key in self.cache:
            return self.cache[key]

        url = (
            f"https://tile.openstreetmap.org/"
            f"{zoom}/{x}/{y}.png"
        )

        response = requests.get(url, timeout=10)
        response.raise_for_status()

        self.cache[key] = response.content

        return response.content
