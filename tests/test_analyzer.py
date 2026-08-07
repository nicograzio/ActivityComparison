import unittest

import numpy as np

from core.analyzer import calculate_slope_range
from core.track import Track, TrackPoint


class TestAnalyzer(unittest.TestCase):
    def test_calculate_slope_range_handles_edge_smoothing(self):
        """La smoothing non deve introdurre pendenze spurie sui bordi della traccia."""
        track = Track("synthetic")
        for i in range(12):
            track.add_point(
                TrackPoint(
                    latitude=0.0 + i * 0.0001,
                    longitude=0.0,
                    altitude=100.0 + float(i),
                )
            )

        slope_min, slope_max = calculate_slope_range(track)

        self.assertIsNotNone(slope_min)
        self.assertIsNotNone(slope_max)
        self.assertGreater(slope_min, 0.0)
        self.assertGreater(slope_max, slope_min)
        self.assertLess(slope_max, 20.0)


if __name__ == "__main__":
    unittest.main()
