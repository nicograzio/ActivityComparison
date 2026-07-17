/* global maplibregl */

const map = new maplibregl.Map({
  container: 'map',
  style: 'https://tiles.openfreemap.org/styles/liberty',
  center: [10.0, 44.5],
  zoom: 7,
  pitch: 0,
  bearing: 0,
  attributionControl: true,
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
  removeIfExists('track-points-layer', 'layer');
  removeIfExists('track-points', 'source');
  removeIfExists('track-line-layer', 'layer');
  removeIfExists('track-line', 'source');
}

function setTrack(payload) {
  clearTrack();

  if (!payload || !payload.geojson) {
    return;
  }

  map.addSource('track-line', {
    type: 'geojson',
    data: payload.geojson,
  });

  map.addLayer({
    id: 'track-line-layer',
    type: 'line',
    source: 'track-line',
    paint: {
      'line-color': ['get', 'color'],
      'line-width': ['coalesce', ['get', 'width'], 4],
      'line-opacity': 0.96,
    },
    layout: {
      'line-cap': 'round',
      'line-join': 'round',
    },
  });

  if (payload.points && payload.points.length) {
    map.addSource('track-points', {
      type: 'geojson',
      data: {
        type: 'FeatureCollection',
        features: payload.points,
      },
    });

    map.addLayer({
      id: 'track-points-layer',
      type: 'circle',
      source: 'track-points',
      paint: {
        'circle-radius': 2.5,
        'circle-color': '#ffffff',
        'circle-stroke-width': 1,
        'circle-stroke-color': '#111111',
        'circle-opacity': 0.85,
      },
    });
  }

  if (payload.bounds && payload.bounds.length === 2) {
    map.fitBounds(payload.bounds, {
      padding: 44,
      duration: 0,
      maxZoom: payload.maxZoom || 17,
    });
  }
}

window.appMap = {
  clearTrack,
  setTrack,
};

window.__mapReady = false;

map.on('load', () => {
  window.__mapReady = true;
  if (window.__pendingTrackPayload) {
    setTrack(window.__pendingTrackPayload);
    window.__pendingTrackPayload = null;
  }
});
