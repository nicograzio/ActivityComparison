/* global maplibregl */

maplibregl.prewarm();

const map = new maplibregl.Map({
  container: 'map',
  style: 'https://tiles.openfreemap.org/styles/liberty',
  center: [10.0, 44.5],
  zoom: 7,
  pitch: 0,
  bearing: 0,
  attributionControl: true,
  antialias: true,
  maxZoom: 20
});

map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');

function removeIfExists(id, kind) {
  if (map.getLayer(id)) {
    map.removeLayer(id);
  }
  if (kind === 'source' && map.getSource(id)) {
    map.removeSource(id);
  }
}

function clearTrack() {
  ['track-casing-layer','track-line-layer','track-points-layer'].forEach(id => removeIfExists(id, 'layer'));
  ['track-casing','track-line','track-points'].forEach(id => removeIfExists(id, 'source'));
}

function setTrack(payload) {
  clearTrack();
  if (!payload || !payload.geojson) return;

  map.addSource('track-line', {
    type: 'geojson',
    data: payload.geojson,
    lineMetrics: true
  });

  // Outer halo improves readability over detailed vector maps
  map.addLayer({
    id: 'track-casing-layer',
    type: 'line',
    source: 'track-line',
    paint: {
      'line-color': '#000000',
      'line-width': 8,
      'line-opacity': 0.35,
      'line-blur': 1
    },
    layout: {
      'line-cap': 'round',
      'line-join': 'round'
    }
  });

  map.addLayer({
    id: 'track-line-layer',
    type: 'line',
    source: 'track-line',
    paint: {
      'line-color': ['get', 'color'],
      'line-width': 4.5,
      'line-opacity': 1
    },
    layout: {
      'line-cap': 'round',
      'line-join': 'round'
    }
  });

  if (payload.points && payload.points.length) {
    map.addSource('track-points', {
      type: 'geojson',
      data: {
        type: 'FeatureCollection',
        features: payload.points
      }
    });

    map.addLayer({
      id: 'track-points-layer',
      type: 'circle',
      source: 'track-points',
      paint: {
        'circle-radius': 2.2,
        'circle-color': '#ffffff',
        'circle-stroke-width': 1,
        'circle-stroke-color': '#111111'
      }
    });
  }

  if (payload.bounds && payload.bounds.length === 2) {
    map.fitBounds(payload.bounds, {
      padding: 60,
      duration: 0,
      maxZoom: 18
    });
  }
}

window.appMap = {
  clearTrack,
  setTrack
};

map.on('load', () => {
  window.__mapReady = true;
});
