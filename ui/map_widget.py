import os
import tempfile

import folium
from PyQt6.QtWebEngineWidgets import QWebEngineView


class MapWidget(QWebEngineView):

    def __init__(self):
        super().__init__()
        self.load_map()

    def load_map(self):
        map_view = folium.Map(
            location=[44.646, 10.925],
            zoom_start=13
        )

        file = tempfile.NamedTemporaryFile(
            suffix=".html",
            delete=False
        )

        map_view.save(file.name)

        with open(file.name, "r", encoding="utf-8") as html:
            self.setHtml(html.read())

        os.unlink(file.name)
