/* global maplibregl */

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

function removeLayerSource(id) {
  if (map.getLayer(id)) map.removeLayer(id);
  if (map.getSource(id)) map.removeSource(id);
}

function clearTrack() {
  ['track-casing','track-line','track-points'].forEach(removeLayerSource);
  map.easeTo({center: DEFAULT_CENTER, zoom: DEFAULT_ZOOM, duration:0});
}

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

window.appMap={clearTrack,setTrack};

map.on('error', e => console.log('MapLibre error', e));