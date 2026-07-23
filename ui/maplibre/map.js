/* global maplibregl */

/**
 * Local MapLibre renderer entry point.
 *
 * Public API exposed to Python through ``window.appMap``:
 *   - clearTrack()
 *   - setTrack(payload)
 *   - getViewState()
 *   - setViewState(state)
 *
 * Called by:
 *   - ``ui.vector_map_widget.VectorMapWidget``
 */
maplibregl.prewarm();

const DEFAULT_CENTER = [10.73333, 44.58333]; // Casalgrande
const DEFAULT_ZOOM = 14;

const map = new maplibregl.Map({
  container: 'map',
  style: 'https://tiles.openfreemap.org/styles/liberty',
  center: DEFAULT_CENTER,
  zoom: DEFAULT_ZOOM,
  minZoom: 2,
  maxZoom: 22,
  antialias: true,
  attributionControl: true
});

map.addControl(new maplibregl.NavigationControl({showCompass:false}), 'top-right');

/**
 * Remove a layer/source pair if it exists.
 *
 * Called by:
 *   - clearTrack()
 */
function removeLayerSource(id) {
  if (map.getLayer(id)) map.removeLayer(id);
  if (map.getSource(id)) map.removeSource(id);
}

/**
 * Remove the current track and restore the default camera.
 *
 * Called by:
 *   - VectorMapWidget.clear_track()
 */
function clearTrack() {
  ['track-casing','track-line','track-points'].forEach(removeLayerSource);
  map.easeTo({center: DEFAULT_CENTER, zoom: DEFAULT_ZOOM, duration:0});
}

/**
 * Render a colored GeoJSON track on the map.
 *
 * Called by:
 *   - VectorMapWidget._push_track_payload()
 */
function setTrack(payload) {
  clearTrack();
  if (!payload || !payload.geojson) return;

  map.addSource('track-line', {type:'geojson', data:payload.geojson});

  map.addLayer({
    id:'track-casing',
    type:'line',
    source:'track-line',
    paint:{'line-color':'#000000','line-width':9,'line-opacity':0.35},
    layout:{'line-cap':'round','line-join':'round'}
  });

  map.addLayer({
    id:'track-line',
    type:'line',
    source:'track-line',
    paint:{'line-color':['get','color'],'line-width':5},
    layout:{'line-cap':'round','line-join':'round'}
  });

  if (payload.bounds) {
    map.fitBounds(payload.bounds,{padding:60,maxZoom:18,duration:0});
  }
}

/**
 * Read the current map camera state.
 *
 * Called by:
 *   - VectorMapWidget._poll_view_state()
 *
 * Returns:
 *   Plain serializable camera state.
 */
function getViewState() {
  const center = map.getCenter();
  return {
    center: [center.lng, center.lat],
    zoom: map.getZoom(),
    bearing: map.getBearing(),
    pitch: map.getPitch()
  };
}

/**
 * Apply a camera state without animation.
 *
 * Called by:
 *   - VectorMapWidget.set_view_state()
 */
function setViewState(state) {
  if (!state) return;
  const options = {duration:0};
  if (Array.isArray(state.center) && state.center.length === 2) {
    options.center = state.center;
  }
  if (typeof state.zoom === 'number') {
    options.zoom = state.zoom;
  }
  if (typeof state.bearing === 'number') {
    options.bearing = state.bearing;
  }
  if (typeof state.pitch === 'number') {
    options.pitch = state.pitch;
  }
  map.jumpTo(options);
}

window.appMap={clearTrack,setTrack,getViewState,setViewState};

map.on('error', e => console.log('MapLibre error', e));