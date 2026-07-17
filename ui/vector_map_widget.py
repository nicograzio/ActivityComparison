"""
Vector map renderer foundation.

Initial container for migrating the application from raster OSM tiles to a
vector map renderer based on MapLibre.
"""

from PyQt6.QtWebEngineWidgets import QWebEngineView


class VectorMapWidget(QWebEngineView):
    """Base MapLibre container.

    The legacy raster MapWidget remains active until the vector renderer is
    fully integrated. Track layers will be migrated using GeoJSON sources.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHtml(self._base_html())

    def _base_html(self):
        return """
        <!doctype html>
        <html>
        <head>
            <meta charset='utf-8'>
            <style>
                html, body, #map {
                    margin: 0;
                    width: 100%;
                    height: 100%;
                    overflow: hidden;
                }
            </style>
        </head>
        <body>
            <div id='map'></div>
            <script>
                // MapLibre initialization will be added in the next commit.
            </script>
        </body>
        </html>
        """
