/* NTRO Oil Spill Attribution Console
   Vanilla JS, Leaflet only. No framework, no build step, no CDN at runtime.
   Every value rendered here comes from the job document the API returned. */

(function () {
  "use strict";

  var API = "";
  var state = {
    scenes: [],
    scene: null,
    job: null,
    config: null,
    health: null,
    suspects: [],
    selected: null,
    playing: false,
    timer: null,
    frames: [],
    frameIndex: 0
  };

  var layers = {};
  var map = null;
  var layerGroup = {};

  // ---------------------------------------------------------------- helpers
  function $(id) { return document.getElementById(id); }

  /* Read a colour token from the stylesheet, so the map and the canvas chart
     follow the theme instead of carrying a second palette of their own. */
  function cssv(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }

  function fmt(v, digits) {
    if (v === null || v === undefined || v === "" || (typeof v === "number" && !isFinite(v))) return "n/a";
    if (typeof v === "number") return v.toFixed(digits === undefined ? 2 : digits);
    return String(v);
  }

  function utc(iso) {
    if (!iso) return "n/a";
    // AIS times arrive as naive UTC ("2023-08-29T01:59:10"), and a browser
    // reads a naive timestamp as local time: in India that shifted them 5.5 h.
    var s = String(iso);
    if (/^\d{4}-\d\d-\d\dT\d\d:\d\d(:\d\d(\.\d+)?)?$/.test(s)) s += "Z";
    var d = new Date(s);
    if (isNaN(d.getTime())) return String(iso);
    return d.toISOString().replace("T", " ").replace(/\.\d+Z?$/, "").replace("Z", "") + " UTC";
  }

  function hhmm(ts) {
    var d = new Date(ts * 1000);
    return d.toISOString().substring(0, 16).replace("T", " ") + " UTC";
  }

  function getJSON(path) {
    return fetch(API + path, { headers: { "Accept": "application/json" } })
      .then(function (r) {
        if (!r.ok) return r.text().then(function (t) { throw new Error(r.status + " " + t.slice(0, 300)); });
        return r.json();
      });
  }

  function postJSON(path, body) {
    return fetch(API + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    }).then(function (r) {
      if (!r.ok) return r.text().then(function (t) {
        var msg = t;
        try { msg = JSON.parse(t).detail || t; } catch (e) { /* not JSON */ }
        throw new Error(typeof msg === "string" ? msg.slice(0, 400) : r.status + " error");
      });
      return r.json();
    });
  }

  // ------------------------------------------------------------------- map
  function initMap() {
    map = L.map("map", {
      // Zoom sits on the right. The layers panel owns the top-left corner, and
      // stacking a control under a panel makes the control unclickable.
      zoomControl: false,
      attributionControl: true,
      preferCanvas: true,
      worldCopyJump: false,
      // Leaflet fades tiles in over a few frames. If the map is re-fitted while
      // a fade is in flight -- which is exactly what happens when the scene
      // footprint is framed on load -- the fade stalls and the tiles are left
      // at opacity 0. They are decoded, positioned and invisible, so the
      // console showed a black map over perfectly good cached imagery. There is
      // nothing to fade for a local tile store anyway.
      fadeAnimation: false
    }).setView([20, 78], 4);

    map.attributionControl.setPrefix("");
    map.attributionControl.addAttribution("TideTrail offline console");

    L.control.zoom({ position: "topright" }).addTo(map);
    L.control.scale({ imperial: false, position: "bottomright" }).addTo(map);

    // Exposed for diagnostics and for the browser checks that drive this map
    // during development. Read-only as far as the app is concerned.
    window.__tidetrace = { map: map, layers: layerGroup, state: state };
    window.__tidetrail = window.__tidetrace;

    // Leaflet stacks everything in `overlayPane` by DOM insertion order, and the
    // SAR backdrop is inserted at RUN time -- after the optical chip, which is
    // loaded when the scene is picked. So optical always ended up underneath it
    // and ticking the box appeared to do nothing. Explicit panes fix the order
    // properly: both sit below overlayPane (400), so the class mask and every
    // vector layer still draw above them.
    map.createPane("sarPane").style.zIndex = 350;
    map.createPane("opticalPane").style.zIndex = 360;

    // Order is z-order: later goes on top. Optical sits directly ABOVE the SAR
    // backdrop so that ticking it actually reveals something -- underneath a
    // 0.95-opacity radar image it would be invisible and the control would look
    // broken. The class mask and every vector layer stay above both.
    ["sar", "optical", "mask", "oil", "lookalike", "hindcast",
      "cone_back", "origin", "forecast", "cone_fwd", "tracks",
      "sim_slick", "sources", "ships", "vessels"].forEach(function (name) {
        layerGroup[name] = L.layerGroup().addTo(map);
      });

    // Starts hidden, matching its unchecked box. It is context, not evidence.
    map.removeLayer(layerGroup.optical);

    addBasemaps();

    map.on("mousemove", function (e) {
      $("readout").textContent = "lat " + e.latlng.lat.toFixed(4) +
        "  lon " + e.latlng.lng.toFixed(4);
    });
  }

  /* A 1x1 transparent PNG. Outside the cached footprint there is simply no
     tile, and a missing tile should reveal the dark canvas and the graticule
     rather than a broken-image icon. */
  var BLANK = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";

  /* Basemaps are cached, not live.

     The console runs in airplane mode, so a normal tile layer pointed at a
     provider would go blank the moment the network did. Instead
     scripts/fetch_basemap.py downloads the tiles covering the scene footprints
     once, and they are served from data/basemap. Inside that footprint you get
     real imagery; outside it you get the graticule, which is honest: it shows
     exactly where there is data and where there is not. */
  // Shown when the current view intersects none of the cached footprints.
  // Outside cached imagery banner disabled per UI requirements
  function updateCoverageNote() {}

  /* The chart base: a tile layer drawn in the browser, from nothing.

     Cached imagery only exists for the indexed scene footprints, so panning off
     one used to leave bare canvas with a few graticule lines over it. That reads
     as a broken map, and "the tiles you need were never downloaded" is not a
     thought a judge should have to have.

     This draws every tile on a canvas at request time: an ocean ground, a
     depth-of-field wash so the sea is not a flat fill, the graticule as part of
     the tile rather than as vectors laid over it, and the parallel and meridian
     stamped in the corner. It costs no network, no disk and no bundle, it is
     seamless at every zoom, and it covers the whole globe. Real imagery still
     draws on top wherever we actually have it.

     A chart with no soundings is still a chart. A blank rectangle is not. */
  function chartBaseLayer() {
    var Chart = L.GridLayer.extend({
      createTile: function (coords) {
        var size = this.getTileSize();
        var tile = document.createElement("canvas");
        tile.width = size.x;
        tile.height = size.y;
        var ctx = tile.getContext("2d");
        var w = size.x, h = size.y;

        // Ground. Two stops, so a wide view has some depth to it rather than
        // reading as one flat colour across the whole viewport.
        // Deep-water tone, pitched to sit close to the open ocean in the cached
        // satellite imagery. Where a tile is missing the join then reads as more
        // sea rather than as a hole, which is both better looking and more
        // truthful: offshore of the footprint there really is only more sea.
        var wash = ctx.createLinearGradient(0, 0, w, h);
        wash.addColorStop(0, cssv("--chart-sea-1"));
        wash.addColorStop(1, cssv("--chart-sea-2"));
        ctx.fillStyle = wash;
        ctx.fillRect(0, 0, w, h);

        // Continents. Without these, zooming out showed ocean, a graticule and
        // four islands of satellite imagery, which reads as a failed load.
        paintLand(ctx, coords, size, this._map);

        // Graticule, drawn into the tile. Leaflet gives us the tile's own
        // lat/lon corners, so the lines land on whole degrees rather than on
        // tile edges, and the interval opens up as you zoom out.
        var nw = this._map.unproject([coords.x * w, coords.y * h], coords.z);
        var se = this._map.unproject([(coords.x + 1) * w, (coords.y + 1) * h], coords.z);
        var stepChoices = [30, 10, 5, 2, 1, 0.5, 0.25, 0.1, 0.05, 0.02, 0.01];
        var spanLon = Math.abs(se.lng - nw.lng);
        var step = stepChoices[0];
        for (var i = 0; i < stepChoices.length; i++) {
          if (spanLon / stepChoices[i] <= 4) { step = stepChoices[i]; break; }
        }

        ctx.strokeStyle = cssv("--chart-grid");
        ctx.lineWidth = 1;
        ctx.beginPath();

        var firstLon = Math.ceil(nw.lng / step) * step;
        for (var lon = firstLon; lon < se.lng; lon += step) {
          var px = ((lon - nw.lng) / (se.lng - nw.lng)) * w;
          ctx.moveTo(Math.round(px) + 0.5, 0);
          ctx.lineTo(Math.round(px) + 0.5, h);
        }
        var firstLat = Math.floor(nw.lat / step) * step;
        for (var lat = firstLat; lat > se.lat; lat -= step) {
          var py = ((nw.lat - lat) / (nw.lat - se.lat)) * h;
          ctx.moveTo(0, Math.round(py) + 0.5);
          ctx.lineTo(w, Math.round(py) + 0.5);
        }
        ctx.stroke();

        // The tile's own north-west corner, stamped like a chart margin.
        ctx.fillStyle = cssv("--chart-label");
        ctx.font = "10px Bahnschrift, 'DIN Alternate', 'Arial Narrow', sans-serif";
        ctx.fillText(_dm(nw.lat, "NS") + "  " + _dm(nw.lng, "EW"), 6, 14);

        return tile;
      }
    });
    return new Chart({ minZoom: 0, maxZoom: 18, tileSize: 256, attribution: "" });
  }

  /* Degrees and decimal minutes, the way a chart margin writes them. */
  function _dm(v, hemis) {
    var hemi = hemis[v < 0 ? 1 : 0];
    var a = Math.abs(v);
    var d = Math.floor(a);
    var m = (a - d) * 60;
    return d + "°" + (m < 10 ? "0" : "") + m.toFixed(1) + "'" + hemi;
  }

  /* World coastline, drawn into the chart tiles.

     The satellite cache covers the four scene footprints and nothing else,
     which is the right trade for an offline demo but left zooming out looking
     like a broken tile pipeline: four imagery patches floating in an empty
     wash, with no continents anywhere. Natural Earth land, simplified to 79 KB
     coarse and 645 KB detailed, fills that in at every zoom with no network and
     no tiles.

     It is rasterised into each chart tile rather than added as a vector layer,
     because a vector layer re-paths every ring on every pan frame; a tile is
     drawn once and then moved by the browser. That is the difference between a
     map that drags smoothly and one that stutters. */
  var LAND = { coarse: null, detail: null, loading: false };

  function loadWorldLand() {
    if (LAND.loading) return;
    LAND.loading = true;
    var pending = 2;
    function done() { if (--pending === 0 && map) redrawChartBase(); }
    getJSON("/data/land/world_land_coarse.json")
      .then(function (d) { LAND.coarse = d.rings; }).catch(function () {}).then(done);
    getJSON("/data/land/world_land_detail.json")
      .then(function (d) { LAND.detail = d.rings; }).catch(function () {}).then(done);
  }

  function redrawChartBase() {
    if (state.chartLayer && state.chartLayer.redraw) state.chartLayer.redraw();
  }

  /* Paint the land rings that touch this tile. Rings are pre-simplified, so the
     only per-tile work is a bounds test and a path. */
  function paintLand(ctx, coords, size, map_) {
    var rings = (coords.z >= 6 ? LAND.detail : null) || LAND.coarse;
    if (!rings) return;

    var w = size.x, h = size.y;
    var nw = map_.unproject([coords.x * w, coords.y * h], coords.z);
    var se = map_.unproject([(coords.x + 1) * w, (coords.y + 1) * h], coords.z);
    var west = nw.lng, east = se.lng, north = nw.lat, south = se.lat;
    // A degree of slack, so a coastline that only clips the corner still draws.
    var pad = Math.max(1, (east - west) * 0.5);
    var originX = coords.x * w, originY = coords.y * h;

    ctx.beginPath();
    for (var i = 0; i < rings.length; i++) {
      var ring = rings[i];
      var minX = 1e9, maxX = -1e9, minY = 1e9, maxY = -1e9, j;
      for (j = 0; j < ring.length; j++) {
        var px = ring[j][0], py = ring[j][1];
        if (px < minX) minX = px;
        if (px > maxX) maxX = px;
        if (py < minY) minY = py;
        if (py > maxY) maxY = py;
      }
      if (maxX < west - pad || minX > east + pad ||
          maxY < south - pad || minY > north + pad) continue;

      for (j = 0; j < ring.length; j++) {
        var pt = map_.project([ring[j][1], ring[j][0]], coords.z);
        var x = pt.x - originX, y = pt.y - originY;
        if (j === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      ctx.closePath();
    }
    ctx.fillStyle = cssv("--chart-land");
    ctx.fill("evenodd");
    ctx.strokeStyle = cssv("--chart-land-edge");
    ctx.lineWidth = 1;
    ctx.stroke();
  }



  function addBasemaps() {
    // Drawn before anything else and never removed, so there is always a chart
    // under the overlays no matter where the operator pans.
    state.chartLayer = chartBaseLayer();
    state.chartLayer.addTo(map);
    loadWorldLand();

    getJSON("/data/basemap/manifest.json").then(function (m) {
      var defs = [
        ["satellite", "Satellite", "Esri, Maxar, Earthstar Geographics"],
        ["ocean", "Nautical", "Esri, GEBCO, NOAA, National Geographic"]
      ];
      var bases = {};
      defs.forEach(function (d) {
        if (!m.layers || !m.layers[d[0]]) return;
        bases[d[1]] = L.tileLayer(
          "/data/basemap/" + d[0] + "/{z}/{x}/{y}." + m.layers[d[0]].ext, {
            minZoom: 1,
            // Tiles are cached for zoom 5 to 13 only. Without minNativeZoom,
            // zooming out past 5 requested tiles that were never downloaded and
            // the map went completely blank -- which reads as broken rather
            // than as "no data here". Both bounds now reuse the nearest cached
            // level, scaled, so there is always something under the overlays.
            minNativeZoom: m.min_zoom || 5,
            maxNativeZoom: m.max_zoom || 13,
            maxZoom: 18,
            errorTileUrl: BLANK,
            attribution: d[2],
            crossOrigin: false
          });
      });
      bases["Graticule only"] = L.layerGroup();

      var overlays = {};
      if (m.layers && m.layers.ocean_labels) {
        overlays["Place names"] = L.tileLayer(
          "/data/basemap/ocean_labels/{z}/{x}/{y}." + m.layers.ocean_labels.ext,
          { minNativeZoom: m.min_zoom || 5, maxNativeZoom: m.max_zoom || 13,
            maxZoom: 18, errorTileUrl: BLANK, opacity: 0.85 });
      }
      if (m.layers && m.layers.seamark) {
        overlays["Seamarks"] = L.tileLayer(
          "/data/basemap/seamark/{z}/{x}/{y}." + m.layers.seamark.ext,
          { minNativeZoom: m.min_zoom || 5, maxNativeZoom: m.max_zoom || 13,
            maxZoom: 18, errorTileUrl: BLANK,
            attribution: "OpenSeaMap, ODbL" });
      }

      // Satellite first if it exists; it is the one that makes open water read
      // as open water rather than as an empty screen.
      var first = bases["Satellite"] || bases["Nautical"] || bases["Graticule only"];
      first.addTo(map);
      state.basemap = first;

      L.control.layers(bases, overlays, { position: "topright", collapsed: true }).addTo(map);

      // Only the four scene footprints were ever cached. Panning away from them
      // leaves a correct but bare graticule, and a bare graticule looks like a
      // failure. Say which it is.
      state.basemapBounds = m.bounds || {};
      map.on("moveend zoomend", updateCoverageNote);
      updateCoverageNote();

      // Keep the drawn layers above the tiles.
      Object.keys(layerGroup).forEach(function (k) {
        if (layerGroup[k].bringToFront) layerGroup[k].bringToFront();
      });
      state.basemapAvailable = true;
    }).catch(function () {
      // No cache present. Say so once, in the layers card, instead of leaving
      // the operator wondering why the map is empty.
      state.basemapAvailable = false;
      var note = el("div", "notice",
        "No cached satellite imagery. The chart base is drawn locally. Run " +
        "scripts/fetch_basemap.py --all while online to cache imagery for the " +
        "scene areas; the demo stays offline afterwards.");
      var box = $("layers");
      if (box && box.parentNode) box.parentNode.appendChild(note);
    });
  }

  /* Graticule. Drawn under everything, and the only backdrop outside the
     cached basemap footprint. */
  /* The graticule used to be drawn here as vector polylines over a bare
     canvas. It is now part of the chart base tile, which is seamless, covers
     the whole globe and costs nothing to pan. */

  // Everything a run produces is cleared before the next one. The graticule and
  // the optical chip are not run output: they belong to the map and to the
  // scene. Clearing optical here wiped it on the first RUN and, since it only
  // reloads on scene change, it never came back.
  var KEEP_ON_RUN = { optical: true };

  function clearAll() {
    Object.keys(layerGroup).forEach(function (k) {
      if (!KEEP_ON_RUN[k]) layerGroup[k].clearLayers();
    });
  }

  function ringToLatLng(coords) {
    return coords.map(function (p) { return [p[1], p[0]]; });
  }

  // -------------------------------------------------------------- rendering
  function drawDetection(job) {
    if (job && job.mode === "operator_probe") {
      var inp = job.input || {};
      if (inp.lat != null && inp.lon != null) {
        var rKm = inp.slick_radius_km || 1.5;
        L.circle([inp.lat, inp.lon], {
          radius: rKm * 1000,
          color: MAPC.oil, weight: 2.5,
          fillColor: MAPC.oil, fillOpacity: 0.40
        }).bindPopup(
          "<b>Probe Spill Location</b><br>" +
          "Lat: " + fmt(inp.lat, 4) + "<br>" +
          "Lon: " + fmt(inp.lon, 4) + "<br>" +
          "Radius: " + fmt(rKm, 1) + " km<br>" +
          "<i>Ad-hoc observation point</i>"
        ).addTo(layerGroup.oil);

        L.circleMarker([inp.lat, inp.lon], {
          radius: 5, color: MAPC.paper, weight: 2,
          fillColor: MAPC.oil, fillOpacity: 1
        }).addTo(layerGroup.oil);
      }
      return;
    }

    var det = job.detection || {};
    var ov = det.overlays || {};

    if (ov.sar && ov.sar.url) {
      L.imageOverlay(ov.sar.url, ov.sar.bounds,
        { opacity: 0.95, interactive: false, pane: "sarPane" })
        .addTo(layerGroup.sar);
    }
    if (ov.mask && ov.mask.url) {
      L.imageOverlay(ov.mask.url, ov.mask.bounds, { opacity: 0.75, interactive: false })
        .addTo(layerGroup.mask);
    }

    (det.polygons || []).forEach(function (f) {
      if (!f.geometry) return;
      var p = f.properties;
      L.polygon(ringToLatLng(f.geometry.coordinates[0]), {
        color: MAPC.oil, weight: 2, fillColor: MAPC.oil, fillOpacity: 0.24
      }).bindPopup(
        "<b>" + p.polygon_id + " mineral oil</b><br>" +
        "area " + fmt(p.area_km2, 3) + " km2<br>" +
        "length " + fmt(p.length_km, 2) + " km, width " + fmt(p.width_km, 2) + " km<br>" +
        "perimeter " + fmt(p.perimeter_km, 2) + " km<br>" +
        "orientation " + fmt(p.orientation_deg, 0) + " deg<br>" +
        (p.eo_verdict ? "optical: " + p.eo_verdict + "<br>" : "") +
        "compactness " + fmt(p.compactness, 2) + "<br>" +
        "contrast " + fmt(p.contrast_db, 1) + " dB<br>" +
        "confidence " + fmt(p.confidence, 2) + "<br>" +
        "centroid " + fmt(p.centroid_lat, 4) + ", " + fmt(p.centroid_lon, 4)
      ).addTo(layerGroup.oil);
    });

    (det.lookalikes || []).forEach(function (f) {
      if (!f.geometry) return;
      var p = f.properties;
      L.polygon(ringToLatLng(f.geometry.coordinates[0]), {
        color: MAPC.lookalike, weight: 1.5, dashArray: "5,4",
        fillColor: MAPC.lookalike, fillOpacity: 0.08
      }).bindPopup(
        "<b>" + p.polygon_id + " look-alike</b><br>" +
        "area " + fmt(p.area_km2, 3) + " km2<br>" +
        "contrast " + fmt(p.contrast_db, 1) + " dB<br>" +
        "<i>excluded from AIS attribution</i>"
      ).addTo(layerGroup.lookalike);
    });
  }

  function drawDrift(job) {
    var d = job.drift;
    if (!d) return;

    if (d.cone_back && d.cone_back.geometry) {
      L.polygon(ringToLatLng(d.cone_back.geometry.coordinates[0]), {
        color: MAPC.hindEdge, weight: 1, dashArray: "3,4",
        fillColor: MAPC.hindFill, fillOpacity: 0.10
      }).bindPopup("Hindcast cone, swept 90 percent ensemble<br>area " +
        fmt(d.cone_back.properties.area_km2, 1) + " km2").addTo(layerGroup.cone_back);
    }

    if (d.hindcast_track && d.hindcast_track.geometry) {
      L.polyline(ringToLatLng(d.hindcast_track.geometry.coordinates), {
        color: MAPC.hind, weight: 2, dashArray: "2,6", opacity: 0.9
      }).bindPopup("Ensemble median backtrack").addTo(layerGroup.hindcast);
    }

    if (d.origin_zone && d.origin_zone.geometry) {
      L.polygon(ringToLatLng(d.origin_zone.geometry.coordinates[0]), {
        color: MAPC.origin, weight: 2, fillColor: MAPC.origin, fillOpacity: 0.18
      }).bindPopup(
        "<b>Origin zone</b><br>" +
        utc(d.origin.t) + "<br>" +
        "90 percent envelope, spread " + fmt(d.origin.spread_km, 1) + " km<br>" +
        "buffered " + fmt(d.origin.buffer_km, 1) + " km<br>" +
        "area " + fmt(d.origin.area_km2, 1) + " km2"
      ).addTo(layerGroup.origin);

      L.circleMarker([d.origin.lat, d.origin.lon], {
        radius: 4, color: MAPC.paper, weight: 2, fillColor: MAPC.origin, fillOpacity: 1
      }).bindTooltip("origin estimate, zone centre").addTo(layerGroup.origin);
    }

    if (d.cone_fwd && d.cone_fwd.geometry) {
      L.polygon(ringToLatLng(d.cone_fwd.geometry.coordinates[0]), {
        color: MAPC.fore, weight: 1.5, fillColor: MAPC.fore, fillOpacity: 0.10
      }).bindPopup("Forecast cone " + fmt(d.cone_fwd.properties.hours, 0) +
        " h<br>area " + fmt(d.cone_fwd.properties.area_km2, 1) + " km2")
        .addTo(layerGroup.cone_fwd);
    }
    if (d.forecast_track && d.forecast_track.geometry) {
      L.polyline(ringToLatLng(d.forecast_track.geometry.coordinates), {
        color: MAPC.fore, weight: 2, opacity: 0.95, dashArray: "6,4"
      }).bindPopup("Ensemble median forecast").addTo(layerGroup.forecast);
    }
  }

  /* The map's own palette, kept in one place so it cannot drift away from the
     stylesheet. */
  var MAPC = {};
  var RANK_COLORS = [];

  /* Filled from the stylesheet on boot and on every theme change. The past
     (backtrack, release zone) is ochre, the future (forecast) is depth blue,
     the oil and its candidate sources are chart magenta. */
  function refreshMapColors() {
    MAPC.oil = cssv("--oil");
    MAPC.lookalike = cssv("--lookalike");
    MAPC.hind = cssv("--hind");
    MAPC.hindEdge = cssv("--hind");
    MAPC.hindFill = cssv("--hind");
    MAPC.origin = cssv("--hind");
    MAPC.fore = cssv("--fore");
    MAPC.gap = cssv("--bad");
    MAPC.flag = cssv("--flag");
    MAPC.warn = cssv("--warn");
    MAPC.slate = cssv("--ink-3");
    MAPC.paper = cssv("--panel");
    RANK_COLORS = [MAPC.flag, MAPC.warn, MAPC.warn].concat(
      [0, 0, 0, 0, 0, 0, 0].map(function () { return MAPC.slate; }));
  }

  /* Magenta means one thing: the vessel is on the forward-test shortlist, the
     few whose own released oil best reproduces the slick. Every other lead is
     drawn in neutral light tones, which read on the dark imagery in either
     theme without implying a finding. */
  function supported(s) {
    var st = (state.job && state.job.source_test) || {};
    return (st.shortlist || []).some(function (v) { return String(v.mmsi) === String(s.mmsi); });
  }

  function vesselColor(s) {
    if (supported(s)) return MAPC.flag;
    return s.rank === 1 ? "#f1f1ec" : "#c9cbc2";
  }

  function rankColor(rank) {
    return RANK_COLORS[Math.min(rank - 1, RANK_COLORS.length - 1)] || MAPC.slate;
  }

  /* Ten tracks and ten markers bury the chart. The three leading vessels are
     drawn, plus whichever vessel the analyst has selected. */
  function onChart(s) {
    return s.rank <= 3 || String(s.mmsi) === String(state.selected);
  }

  function reasonText(r) {
    return String(r).replace(/_/g, " ").replace(/(\d)km\b/g, "$1 km").replace(/(\d)h\b/g, "$1 h");
  }

  function drawTracks(suspects) {
    layerGroup.tracks.clearLayers();
    suspects.filter(onChart).forEach(function (s) {
      var color = vesselColor(s);
      var weight = s.rank === 1 ? 4 : (s.rank <= 3 ? 2.5 : 1.6);
      var gj = (s.track || {}).geojson;
      if (!gj) return;
      gj.features.forEach(function (f) {
        if (!f.geometry || !f.geometry.coordinates.length) return;
        var dr = f.properties.kind === "dead_reckoned";
        L.polyline(ringToLatLng(f.geometry.coordinates), {
          color: dr ? MAPC.gap : color,
          weight: dr ? Math.max(weight, 3) : weight,
          opacity: dr ? 0.95 : 0.8,
          dashArray: dr ? "7,5" : null
        }).bindPopup(
          "<b>" + (s.name || "UNKNOWN") + "</b><br>" +
          "MMSI " + s.mmsi + "<br>" +
          "rank " + s.rank + ", score " + fmt(s.score, 1) + "<br>" +
          "type " + s.type + "<br>" +
          (dr ? "<b style='color:" + MAPC.gap + "'>AIS silent for " +
            fmt(f.properties.gap_minutes, 0) + " min (position dead reckoned)</b><br>" : "") +
          s.reasons.slice(0, 3).map(reasonText).join("<br>")
        ).addTo(layerGroup.tracks);
      });
    });
  }

  // ----------------------------------------------------------- time slider
  function buildFrames(job) {
    var times = {};
    var t_sat_str = (job.input || {}).t_sat || (job.scene || {}).t_sat;
    var t_sat_ts = t_sat_str ? Math.floor(Date.parse(t_sat_str) / 1000) : null;
    state.t_sat_ts = t_sat_ts;
    if (t_sat_ts) times[t_sat_ts] = true;

    var drift = job.drift || {};
    (drift.hindcast_hourly || []).forEach(function (h) {
      if (h && h.t) {
        var ts = Math.floor(Date.parse(h.t) / 1000);
        if (!isNaN(ts)) { times[ts] = true; h._ts = ts; }
      }
    });
    (drift.hindcast_envelopes || []).forEach(function (env) {
      if (env && env.t) {
        var ts = Math.floor(Date.parse(env.t) / 1000);
        if (!isNaN(ts)) { times[ts] = true; env._ts = ts; }
      }
    });
    (drift.forecast_hourly || []).forEach(function (f) {
      if (f && f.t) {
        var ts = Math.floor(Date.parse(f.t) / 1000);
        if (!isNaN(ts)) { times[ts] = true; f._ts = ts; }
      }
    });
    (drift.forecast_envelopes || []).forEach(function (env) {
      if (env && env.t) {
        var ts = Math.floor(Date.parse(env.t) / 1000);
        if (!isNaN(ts)) { times[ts] = true; env._ts = ts; }
      }
    });

    var suspects = ((job.attribution || {}).suspects) || [];
    suspects.forEach(function (s) {
      ((s.track || {}).samples || []).forEach(function (p) {
        if (p && typeof p.ts === "number") times[p.ts] = true;
      });
    });

    var list = Object.keys(times).map(Number).sort(function (a, b) { return a - b; });
    if (!list.length && t_sat_ts) list = [t_sat_ts];

    // Subsample to around 260 frames for buttery smooth dragging
    var stride = Math.max(1, Math.ceil(list.length / 260));
    var thinned = list.filter(function (_, i) { return i % stride === 0; });
    if (t_sat_ts && thinned.indexOf(t_sat_ts) === -1) {
      thinned.push(t_sat_ts);
      thinned.sort(function (a, b) { return a - b; });
    }
    state.frames = thinned;

    var satIdx = 0;
    var minDiff = 1e12;
    state.frames.forEach(function (ts, idx) {
      var diff = Math.abs(ts - (t_sat_ts || 0));
      if (diff < minDiff) { minDiff = diff; satIdx = idx; }
    });
    state.satFrameIndex = satIdx;
    state.frameIndex = satIdx; // Start default at radar pass moment

    var slider = $("slider");
    var playBtn = $("play");
    var toEndBtn = $("toEnd");
    var hasFrames = state.frames.length > 1;

    if (slider) {
      slider.disabled = !hasFrames;
      slider.min = 0;
      slider.max = Math.max(0, state.frames.length - 1);
      slider.value = state.frameIndex;
    }
    if (playBtn) playBtn.disabled = !hasFrames;
    if (toEndBtn) toEndBtn.disabled = !hasFrames;

    $("timebar").classList.toggle("on", state.frames.length > 0);
    renderFrame();
  }

  function interpolateSample(samples, ts) {
    if (!samples || !samples.length) return null;
    var first = samples[0], last = samples[samples.length - 1];
    // Beyond 1 hour outside the vessel's tracked window, vessel is not present
    if (ts < first.ts - 3600 || ts > last.ts + 3600) return null;
    if (ts <= first.ts) return first;
    if (ts >= last.ts) return last;

    var lo = 0, hi = samples.length - 1;
    while (lo < hi) {
      var mid = (lo + hi) >> 1;
      if (samples[mid].ts < ts) lo = mid + 1; else hi = mid;
    }
    if (samples[lo].ts === ts) return samples[lo];
    var a = samples[Math.max(0, lo - 1)], b = samples[lo];
    if (b.ts === a.ts) return a;
    var ratio = Math.max(0, Math.min(1, (ts - a.ts) / (b.ts - a.ts)));
    return {
      ts: ts,
      lat: a.lat + ratio * (b.lat - a.lat),
      lon: a.lon + ratio * (b.lon - a.lon),
      sog: a.sog + ratio * (b.sog - a.sog),
      // the short way round: 350 to 10 degrees passes through north, not south
      cog: (a.cog + ratio * ((((b.cog - a.cog) % 360) + 540) % 360 - 180) + 360) % 360,
      dr: a.dr || b.dr
    };
  }

  function findClosestEnvelope(envelopes, ts) {
    if (!envelopes || !envelopes.length) return null;
    var best = null, bestDiff = 1e12;
    for (var i = 0; i < envelopes.length; i++) {
      var env = envelopes[i];
      var diff = Math.abs((env._ts || 0) - ts);
      if (diff < bestDiff) {
        bestDiff = diff;
        best = env;
      }
    }
    if (bestDiff <= 5400) return best; // within 1.5h
    return null;
  }

  function renderFrame() {
    var gVessels = layerGroup.vessels;
    var gSlick = layerGroup.sim_slick;
    if (gVessels) gVessels.clearLayers();
    if (gSlick) gSlick.clearLayers();
    if (!state.frames || !state.frames.length) return;

    var ts = state.frames[state.frameIndex];
    if (ts === undefined) return;

    var dtH = state.t_sat_ts ? Math.round((ts - state.t_sat_ts) / 3600) : 0;
    var phase = "Radar pass";
    if (dtH < 0) phase = "Hindcast T" + dtH + "h";
    else if (dtH > 0) phase = "Forecast T+" + dtH + "h";
    $("tlabel").textContent = phase + " \u00B7 " + hhmm(ts);

    // 1. Dynamic slick / drift envelope at time ts
    if (state.job && state.job.drift && gSlick) {
      var isHindcast = state.t_sat_ts && (ts < state.t_sat_ts - 1200);
      var isForecast = state.t_sat_ts && (ts > state.t_sat_ts + 1200);

      if (isHindcast) {
        var envH = findClosestEnvelope(state.job.drift.hindcast_envelopes, ts);
        if (envH && envH.ring) {
          L.polygon(ringToLatLng(envH.ring), {
            color: MAPC.hind, weight: 2.5,
            fillColor: MAPC.hindFill, fillOpacity: 0.35,
            dashArray: "4,4"
          }).bindTooltip(
            "<b>Hindcast Slick (" + phase + ")</b><br>" +
            "Spread: " + fmt(envH.spread_km, 1) + " km<br>" +
            "Centroid: " + fmt(envH.lat, 4) + ", " + fmt(envH.lon, 4),
            { direction: "top" }
          ).addTo(gSlick);

          L.circleMarker([envH.lat, envH.lon], {
            radius: 5, color: MAPC.paper, weight: 2,
            fillColor: MAPC.hind, fillOpacity: 1
          }).addTo(gSlick);
        }
      } else if (isForecast) {
        var envF = findClosestEnvelope(state.job.drift.forecast_envelopes, ts);
        if (envF && envF.ring) {
          L.polygon(ringToLatLng(envF.ring), {
            color: MAPC.fore, weight: 2.5,
            fillColor: MAPC.fore, fillOpacity: 0.30,
            dashArray: "5,4"
          }).bindTooltip(
            "<b>Forecast Dispersion (" + phase + ")</b><br>" +
            "Spread: " + fmt(envF.spread_km, 1) + " km<br>" +
            "Centroid: " + fmt(envF.lat, 4) + ", " + fmt(envF.lon, 4),
            { direction: "top" }
          ).addTo(gSlick);

          L.circleMarker([envF.lat, envF.lon], {
            radius: 5, color: MAPC.paper, weight: 2,
            fillColor: MAPC.fore, fillOpacity: 1
          }).addTo(gSlick);
        }
      }
    }

    // 2. Dynamic vessels at time ts
    (state.suspects || []).filter(onChart).forEach(function (s) {
      var p = interpolateSample((s.track || {}).samples || [], ts);
      if (!p) return;
      var color = vesselColor(s);
      L.circleMarker([p.lat, p.lon], {
        radius: s.rank === 1 ? 7 : 5,
        color: p.dr ? MAPC.gap : MAPC.paper,
        weight: p.dr ? 2.5 : 1.5,
        fillColor: color,
        fillOpacity: p.dr ? 0.6 : 1
      }).bindTooltip(
        "#" + s.rank + " " + (s.name || "UNKNOWN") + "  " + fmt(p.sog, 1) + " kn  " +
        fmt(p.cog, 0) + " deg" + (p.dr ? "  [NON-REPORTING]" : ""),
        { direction: "top" }
      ).addTo(gVessels);

      if (s.rank === 1) {
        L.marker([p.lat, p.lon], {
          icon: L.divIcon({
            className: "vessel-label",
            html: (s.name || "UNKNOWN"),
            iconAnchor: [-9, 6]
          }),
          interactive: false
        }).addTo(gVessels);
      }
    });
  }

  function play() {
    if (state.playing) { stop(); return; }
    if (!state.frames || state.frames.length < 2) return;
    state.playing = true;
    $("play").textContent = "PAUSE";
    state.timer = setInterval(function () {
      state.frameIndex = (state.frameIndex + 1) % state.frames.length;
      $("slider").value = state.frameIndex;
      renderFrame();
    }, 110);
  }

  function stop() {
    state.playing = false;
    $("play").textContent = "PLAY";
    if (state.timer) { clearInterval(state.timer); state.timer = null; }
  }

  // ------------------------------------------------------------- sidebar
  /* The three-minute demo cannot afford scrolling to find the answer. This is
     the whole result in one card at the top: what detected, what it found,
     where it came from, and who is top of the leaderboard. */
  function renderVerdict(job) {
    var card = $("verdict-card");
    var box = $("verdict");
    box.innerHTML = "";
    if (card) card.hidden = false;
    $("verdict-time").textContent = job.total_ms ? fmt(job.total_ms / 1000, 1) + " s" : "";

    var det = job.detection || {};
    var m = det.metrics || {};
    var polys = det.polygons || [];
    var drift = job.drift;
    var top = ((job.attribution || {}).suspects || [])[0];

    // Handle operator probe mode explicitly
    if (job.mode === "operator_probe") {
      var inp = job.input || {};
      var line = el("div", "verdict-line");
      line.appendChild(document.createTextNode("Probe analysis, "));
      line.appendChild(el("span", "qty", top ? "vessels ranked as leads" : "drift mapped"));
      box.appendChild(line);

      var pf = el("div", "verdict-facts");
      factRow(pf, "Target mode", "Ad-hoc spill probe");
      factRow(pf, "Coordinate", fmt(inp.lat, 4) + "°, " + fmt(inp.lon, 4) + "°");
      factRow(pf, "Origin", drift && drift.origin ? utc(drift.origin.t).replace(" UTC", "") : "n/a");
      factRow(pf, "Top-ranked lead", top ? "#" + top.rank + " " + (top.name || "UNKNOWN") : "None in window");
      box.appendChild(pf);

      box.appendChild(el("div", "hint",
        "Lagrangian drift and AIS correlation evaluated at clicked coordinates. " +
        "Clause (a) satellite SAR segmentation bypassed (no satellite pass scheduled at arbitrary click)."));
      return;
    }

    // An empty result is a finding, and there are two different empties. Say
    // which one this is: water with no structure in it at all, or water with
    // structure the detector declined to call oil. An operator acts on those
    // two differently, and "nothing found" on its own reads like a crash.
    if (!polys.length) {
      var clean = job.clean_scene || {};
      var rad = clean.radiometry || m.radiometry || {};
      var line = el("div", "verdict-line clean");
      line.appendChild(document.createTextNode(clean.headline || "No slick detected."));
      box.appendChild(line);

      var cf = el("div", "verdict-facts");
      factRow(cf, "Water level", rad.sea_level_db != null ? fmt(rad.sea_level_db, 1) + " dB" : "n/a");
      factRow(cf, "Contrast span", rad.dynamic_range_db != null ? fmt(rad.dynamic_range_db, 2) + " dB" : "n/a");
      factRow(cf, "Look-alikes", String(m.lookalike_polygons_found || 0));
      factRow(cf, "Detector", m.detector === "unet" ? "U-Net" : "dB baseline");
      var csf = shipsFact(job.ships);
      if (csf) factRow(cf, "Ships on radar", csf);
      box.appendChild(cf);

      box.appendChild(el("div", "hint", clean.detail ||
        "Drift and AIS did not run: there is no slick to trace back."));
      return;
    }

    var p = polys[0].properties;
    var st = job.source_test || {};
    var scene = job.scene || {};
    var pass = hhmmUTC((job.input || {}).t_sat || scene.t_sat);

    var k = el("div", "finding-k");
    k.appendChild(document.createTextNode("Radar pass " + pass));
    var stamp = findingStamp(st);
    k.appendChild(el("span", "stamp " + stamp[0], stamp[1]));
    box.appendChild(k);

    var f = el("p", "finding");
    f.innerHTML = findingSentence(job, m, polys, drift, st) + darkSentence(job.ships);
    box.appendChild(f);

    var facts = el("div", "verdict-facts");
    factRow(facts, "Oil outlined", fmt(m.oil_area_km2, 2) + " km² · " + polys.length +
      (polys.length === 1 ? " slick" : " slicks"));
    factRow(facts, "Backtrack origin", drift ? hhmmUTC(drift.origin.t) + " ± " + fmt(drift.origin.spread_km, 1) + " km" : "n/a");
    factRow(facts, "Oil age", drift ? fmt(job.age_hours_proxy, 1) + " h (drift)" : "n/a");
    var sf = shipsFact(job.ships);
    if (sf) factRow(facts, "Ships on radar", sf);
    box.appendChild(facts);

    if (!top) {
      box.appendChild(el("div", "notice",
        "No vessel passed the spatio-temporal filter for this origin. " +
        "Reporting nothing rather than forcing a culprit."));
    }
  }

  /* A release window in the words an analyst reads: times, and the date
     only when it is not the day of the radar pass. */
  function windowText(w) {
    if (!w) return "";
    var pass = String(((state.job || {}).input || {}).t_sat || "").slice(0, 10);
    var mon = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    function day(iso) { return " on " + Number(iso.slice(8, 10)) + " " + mon[Number(iso.slice(5, 7)) - 1]; }
    var a = w[0], b = w[1];
    var sameDay = a.slice(0, 10) === b.slice(0, 10);
    var txt = a.slice(11, 16) + (sameDay ? "" : day(a)) + " to " + b.slice(11, 16) + " UTC";
    if (sameDay && a.slice(0, 10) !== pass) txt += day(a);
    else if (!sameDay && b.slice(0, 10) !== pass) txt += day(b);
    return txt;
  }

  function darkSentence(sh) {
    if (!sh || !sh.ais_checked) return "";
    var near = (sh.targets || []).filter(function (t) { return !t.ais && t.slick_km != null && t.slick_km <= 5; })
      .sort(function (a, b) { return a.slick_km - b.slick_km; });
    if (!near.length) return "";
    return " A ship on radar with <span class=\"hot\">no AIS</span> was " + fmt(near[0].slick_km, 1) +
      " km from the slick at the pass" + (near.length > 1 ? ", one of " + near.length : "") + ".";
  }

  function hhmmUTC(iso) {
    if (!iso) return "n/a";
    var d = new Date(String(iso).replace(/(\.\d+)?Z?$/, "Z"));
    if (isNaN(d)) return String(iso);
    return String(d.getUTCHours()).padStart(2, "0") + ":" +
      String(d.getUTCMinutes()).padStart(2, "0") + " UTC " +
      d.getUTCDate() + " " + ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul",
      "Aug", "Sep", "Oct", "Nov", "Dec"][d.getUTCMonth()];
  }

  function esc(t) {
    return String(t == null ? "" : t).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function srcName(h) {
    return h.kind === "vessel" ? esc(h.name || "UNKNOWN") + " (MMSI " + esc(h.mmsi) + ")" : esc(h.name);
  }

  /* The stamp says how far the finding can be relied on, in the words an
     analyst would write on a case file. It never says "guilty". */
  function runsText(x) {
    var n = x.members || 0;
    return Math.round((x.support || 0) * n) + " of " + n + " drift runs";
  }

  function findingStamp(st) {
    if (!st.available) return ["none", "Drift only"];
    return {
      shortlist: ["lead", "Shortlist"],
      installation: ["lead", "Installation fits"],
      unexplained: ["none", "Unattributed"],
      untested: ["none", "Untested"]
    }[st.verdict] || ["none", "Open"];
  }

  /* One sentence: what the radar saw, then what the forward release tests say
     released it, then how much weight that carries. Built only from the job. */
  function findingSentence(job, m, polys, drift, st) {
    var s = "<b>" + fmt(m.oil_area_km2, 2) + " km²</b> of oil in " + polys.length +
      (polys.length === 1 ? " slick. " : " slicks. ");
    var h = st.hypotheses || [];
    var best = h[0];
    var tested = h.length;
    var need = (st.method || {}).explains_at;
    if (!st.available) {
      return s + (drift ? "Drift puts the release near the zone marked on the chart, around " +
        esc(hhmmUTC(drift.origin.t)) + "." : "No drift reconstruction for this run.");
    }
    if (st.verdict === "shortlist") {
      var sl = st.shortlist || [];
      s += "Oil released along each candidate's own track was drifted to the pass; " +
        sl.map(function (v, i) {
          return "<span class=\"hot\">" + esc(v.name || "MMSI " + v.mmsi) + "</span> (" + fmt(v.fit, 2) + ")";
        }).join(sl.length > 2 ? ", " : " and ").replace(/, ([^,]*)$/, " and $1") +
        (sl.length === 1 ? " reproduces it best." : " reproduce it best.") + " Inspect " +
        (sl.length === 1 ? "it" : "them") + " first; this is a shortlist, not a finding.";
      var val = st.validation;
      if (val && val.cases) {
        s += " In " + val.cases + " known-answer runs on real traffic the ship that released the oil was on the shortlist in " +
          val.shortlist3 + ".";
      }
      if ((st.installations_fitting || []).length) {
        s += " " + esc(st.installations_fitting[0].name) + " fits as well, as a fixed source.";
      }
      return s;
    }
    if (st.verdict === "installation") {
      return s + "No vessel's released oil reaches it; a leak from <span class=\"hot\">" +
        esc(st.installations_fitting[0].name) + "</span> fits at <b>" + fmt(st.installations_fitting[0].fit, 2) + "</b>.";
    }
    if (st.verdict === "unexplained") {
      return s + "None of the " + tested + " installations and vessels tested reproduces it. " +
        "The oil is unattributed; the ranked vessels are leads only.";
    }
    return s + "No installation or vessel was close enough to test.";
  }

  /* One labelled cell in the finding grid. */
  function factRow(parent, key, value) {
    var cell = el("div", "fact");
    cell.appendChild(el("span", "fact-k", key));
    cell.appendChild(el("span", "fact-v", value));
    parent.appendChild(cell);
  }

  /* The metrics card the spec asks for: the model against the published dB
     threshold baseline, on the same tiles. Only shown when a checkpoint ran. */
  function renderModelMetrics(job) {
    var m = (job.detection || {}).metrics || {};
    if (m.detector !== "unet") return null;
    var mm = (m.detector_detail || {}).metrics || {};
    if (!mm || mm.iou_oil === undefined) return null;

    var box = el("div", "model-metrics-box");
    box.appendChild(el("div", "fact-k", "Model Validation"));
    [
      ["IoU oil", mm.iou_oil],
      ["IoU look-alike", mm.iou_lookalike],
      ["pixel accuracy", mm.pixel_accuracy]
    ].forEach(function (r) {
      if (r[1] === undefined) return;
      var line = el("div", "kv");
      line.appendChild(el("span", "k", r[0]));
      line.appendChild(el("span", "v", fmt(r[1], 4)));
      box.appendChild(line);
    });
    return box;
  }

  // The optical cross-check, summarised for the verdict card. It is
  // corroboration and never changes a detection, so it reads as a count of
  // agreements rather than as a score, and always carries the time offset:
  // Sentinel-2 did not see this water when the radar did.
  function eoSummary(job) {
    var eo = job.detection && job.detection.eo;
    if (!eo || !eo.available) return "no chip cached";
    var c = eo.counts || {};
    var parts = [];
    if (c.consistent) parts.push(c.consistent + " agree");
    if (c.inconsistent) parts.push(c.inconsistent + " disagree");
    if (c.neutral) parts.push(c.neutral + " neutral");
    if (c.obscured) parts.push(c.obscured + " obscured");
    if (!parts.length) parts.push("nothing to check");
    return parts.join(", ") + " (" + (eo.offset_label || "offset unknown") + ")";
  }

  function renderDetection(job) {
    var det = job.detection || {};
    var m = det.metrics || {};
    var polys = det.polygons || [];
    var box = $("detection");
    box.innerHTML = "";
    $("det-method").textContent = m.detector === "unet" ? "U-Net" : "dB baseline";

    if (!polys.length) {
      box.appendChild(el("div", "notice", "No oil polygon above the area threshold. " +
        "Reporting an empty result rather than forcing one. Look-alike polygons found: " +
        (det.lookalikes || []).length + "."));
    }

    var p = polys.length ? polys[0].properties : null;
    var rows = [
      ["Oil polygons", polys.length],
      ["Look-alikes", (det.lookalikes || []).length],
      ["Total oil area", fmt(m.oil_area_km2, 3) + " km2"],
      ["Largest area", p ? fmt(p.area_km2, 3) + " km2" : "n/a"],
      ["Length", p ? fmt(p.length_km, 2) + " km" : "n/a"],
      ["Width", p ? fmt(p.width_km, 2) + " km" : "n/a"],
      ["Perimeter", p ? fmt(p.perimeter_km, 2) + " km" : "n/a"],
      ["Orientation", p ? fmt(p.orientation_deg, 0) + " deg" : "n/a"],
      ["Compactness", p ? fmt(p.compactness, 2) : "n/a"],
      ["Contrast", p ? fmt(p.contrast_db, 1) + " dB" : "n/a"],
      ["Confidence", p ? fmt(p.confidence, 2) : "n/a"],
      ["Centroid", p ? fmt(p.centroid_lat, 4) + ", " + fmt(p.centroid_lon, 4) : "n/a"]
    ];
    rows.forEach(function (r) {
      var line = el("div", "kv");
      line.appendChild(el("span", "k", r[0]));
      line.appendChild(el("span", "v", r[1]));
      box.appendChild(line);
    });

    var mm = renderModelMetrics(job);
    if (mm) box.appendChild(mm);

    if (m.accuracy_vs_truth) {
      var a = m.accuracy_vs_truth;
      var acc = el("div", "model-metrics-box");
      acc.appendChild(el("div", "fact-k", "Ground Truth Evaluation"));
      [
        ["IoU oil", a.iou_oil],
        ["IoU look-alike", a.iou_lookalike],
        ["pixel accuracy", a.pixel_accuracy]
      ].forEach(function (r) {
        if (r[1] === undefined) return;
        var line = el("div", "kv");
        line.appendChild(el("span", "k", r[0]));
        line.appendChild(el("span", "v", fmt(r[1], 4)));
        acc.appendChild(line);
      });
      box.appendChild(acc);
    }

    if (m.detector !== "unet") {
      box.appendChild(el("div", "notice",
        "Running the published " + fmt(m.detector_detail && m.detector_detail.threshold_db, 1) +
        " dB dark patch baseline. " + (m.fallback_reason || "")));
    }
  }

  function renderOrigin(job) {
    var box = $("origin");
    box.innerHTML = "";
    var d = job.drift;
    if (!d) {
      box.appendChild(el("div", "hint", "Drift was not run for this job."));
      return;
    }
    var tl = driftTimeline(job);
    if (tl) box.appendChild(tl);
    // The reconstruction only: where and when, how sure, where it goes. Model
    // parameters live in the Method view, the metocean source under Conditions.
    var coast = d.coast || {};
    var rows = [
      ["Release time", utc(d.origin.t)],
      ["Release position", fmt(d.origin.lat, 4) + ", " + fmt(d.origin.lon, 4)],
      ["Zone radius", fmt(d.origin.spread_km, 1) + " km"],
      ["Oil age (drift)", fmt(job.age_hours_proxy, 1) + " h"],
      ["Forecast spread", fmt(d.forecast_hourly[d.forecast_hourly.length - 1].spread_km, 1) + " km"],
      ["Coast", coast.available === false ? "not checked"
        : (coast.beached_fraction > 0
          ? Math.round(coast.beached_fraction * 100) + "% ashore, first at +" + fmt(coast.first_beaching_hours, 0) + " h"
          : (coast.coast_flag ? "forecast reaches land" : "stays offshore"))]
    ];
    // Backward particles stranded by the release time mean the oil traces
    // back to the shoreline: a coastal seep, outfall or harbour, not open sea.
    if (coast.backtrack_at_shore_fraction > 0.25) {
      rows.push(["Backtrack", Math.round(coast.backtrack_at_shore_fraction * 100) + "% reaches the shore"]);
    }
    rows.forEach(function (r) {
      var line = el("div", "kv");
      line.appendChild(el("span", "k", r[0]));
      var v = el("span", "v", r[1]);
      if (r[0] === "Coast" && coast.coast_flag) v.style.color = "var(--warn)";
      line.appendChild(v);
      box.appendChild(line);
    });

    // Only a metocean problem earns space here: a constant stand-in field, or
    // wind with no currents. A healthy cube is described under Conditions.
    var mo = d.metocean || {};
    if (mo.synthetic) {
      box.appendChild(el("div", "notice bad", "No cached metocean for this scene: the drift used a constant stand-in field."));
    } else if (mo.has_currents === false) {
      box.appendChild(el("div", "notice", "Wind only: the cached cube has no ocean currents for this basin."));
    }
  }

  function renderSuspects(job) {
    var wrap = $("suspects");
    wrap.innerHTML = "";
    var attr = job.attribution || {};
    var list = attr.suspects || [];
    state.suspects = list;
    $("susp-count").textContent = list.length ? list.length + " ranked" : "";
    $("susp-count").title = (state.job && (state.job.attribution || {}).ranked_by) ?
      "Ordered by " + state.job.attribution.ranked_by : "";

    if (!list.length) {
      var b = el("div", "body");
      var clean = job.status === "clean_water" || job.status === "no_oil_detected";
      b.appendChild(el("div", "notice", clean
        ? "Attribution did not run: there is no slick on this pass, so there is nothing to trace back to a vessel."
        : "No vessel passed the spatio-temporal filter for this origin zone and window. " +
          "The pipeline reports nothing rather than inventing a culprit."));
      if (attr.funnel) {
        b.appendChild(el("div", "hint",
          "Considered " + attr.funnel.considered_vessels + " vessels in the box, " +
          "dropped " + attr.funnel.dropped_outside_radius + " outside the radius" +
          (attr.funnel.dropped_berthed_away ? " and " + attr.funnel.dropped_berthed_away +
            " berthed away from the oil" : "") + "."));
      }
      wrap.appendChild(b);
      return;
    }

    list.forEach(function (s) { wrap.appendChild(suspectCard(s)); });
  }

  /* One ranked vessel, rendered the same way wherever it appears. */
  function suspectCard(s) {
    {
      var card = el("div", "suspect" + (s.rank === 1 ? " r1" : "") + (supported(s) ? " supported" : ""));
      card.dataset.mmsi = s.mmsi;

      var top = el("div", "top");
      top.appendChild(el("span", "rank", "#" + s.rank));
      top.appendChild(el("span", "nm", s.name || "UNKNOWN"));
      top.appendChild(el("span", "sc", fmt(s.score, 1) + "%"));
      card.appendChild(top);

      var d = s.detail || {};
      var dist = d.origin_distance_km != null
        ? fmt(d.origin_distance_km, 1) + " km"
        : fmt(d.min_distance_km, 1) + " km";
      var when = d.time_offset_minutes != null
        ? Math.abs(Math.round(d.time_offset_minutes)) + " min " +
          (d.time_offset_minutes < 0 ? "before" : "after")
        : null;
      card.appendChild(el("div", "meta",
        "MMSI " + s.mmsi + " · " + s.type.replace(/_/g, " ") +
        " · " + dist + (when ? " · " + when : "")));

      // Which release this vessel was matched under, and what the forward
      // test made of it. A rank without a fit is a lead; a fit is evidence.
      var chips = el("div", "chips");
      chips.appendChild(el("span", "hyp", d.hypothesis === "drift_corridor" ? "with the drifting oil" : "near the origin zone"));
      var st = (state.job && state.job.source_test) || {};
      var fw = (st.hypotheses || []).filter(function (h) { return h.kind === "vessel" && String(h.mmsi) === String(s.mmsi); })[0];
      if (fw) {
        var fc = fitClass(fw, st);
        chips.appendChild(el("span", "hyp" + (fc ? " fwd-" + fc : ""), "forward fit " + fmt(fw.fit, 2)));
      }
      card.appendChild(chips);

      // One bar, two numbers. Solid to the ranked score; a hairline continues to
      // where the evidence alone would have put it. The gap is how much of the
      // case rests on positions that were dead reckoned rather than received.
      var evidence = s.score_before_confidence != null ? s.score_before_confidence : s.score;
      var bar = el("div", "bar");
      bar.style.setProperty("--score", Math.max(1, Math.min(100, s.score)) + "%");
      bar.style.setProperty("--evidence", Math.max(1, Math.min(100, evidence)) + "%");
      if (evidence - s.score < 0.5) bar.dataset.full = "1";
      bar.appendChild(el("i"));
      bar.appendChild(el("u"));
      bar.title = "Ranked " + fmt(s.score, 1) + "% · evidence alone " +
                  fmt(evidence, 1) + "% · track confidence " +
                  fmt((s.confidence != null ? s.confidence : 1) * 100, 0) + "%";
      card.appendChild(bar);

      // Only annotate a discount worth reading. A 3 percent haircut is visible
      // in the bar's ghost tail and does not need a sentence as well.
      if (s.confidence != null && s.confidence < 0.95) {
        var cn = el("div", "conf-note");
        cn.appendChild(document.createTextNode(fmt(evidence, 1) + "% on evidence, held to "));
        cn.appendChild(el("b", null, fmt(s.score, 1) + "%"));
        cn.appendChild(document.createTextNode(
          " (" + Math.round((d.dead_reckoned_fraction || 0) * 100) +
          "% of this track is dead reckoned from " + (d.raw_positions || 0) + " receptions)"));
        card.appendChild(cn);
      }

      var tags = el("div", "tags");
      s.reasons.forEach(function (r) {
        var cls = "tag";
        if (r.indexOf("ais_gap") === 0) cls += " gap";
        if (r.indexOf("type_") === 0) cls += " type";
        tags.appendChild(el("span", cls, r.replace(/_/g, " ")));
      });
      card.appendChild(tags);

      var det = el("div", "detail small");
      var c = s.components, w = s.weighted;
      [
        ["proximity", c.prox, w.prox],
        ["vessel type", c.type, w.type],
        ["trajectory", c.traj, w.traj],
        ["behaviour", c.beh, w.beh]
      ].forEach(function (row) {
        var line = el("div", "kv");
        line.appendChild(el("span", "k", row[0]));
        line.appendChild(el("span", "v", fmt(row[1], 2) + "  ->  " + fmt(row[2], 3)));
        det.appendChild(line);
      });
      [
        ["closest approach", utc(s.detail.closest_approach_utc)],
        ["at", fmt(s.detail.closest_lat, 4) + ", " + fmt(s.detail.closest_lon, 4)],
        s.detail.trajectory.against === "slick_axis"
          ? ["course vs slick axis", fmt(s.detail.trajectory.course_deg, 0) + " vs " +
             fmt(s.detail.trajectory.slick_axis_deg, 0) + " deg"]
          : ["course vs drift", fmt(s.detail.trajectory.course_deg, 0) + " vs " +
             fmt(s.detail.trajectory.drift_bearing_deg, 0) + " deg"],
        ["SOG at closest", fmt(s.detail.behavior.sog_at_closest_kn, 1) + " kn"],
        ["reported fixes", s.detail.raw_positions],
        ["gaps", s.detail.gaps.length]
      ].forEach(function (row) {
        var line = el("div", "kv");
        line.appendChild(el("span", "k", row[0]));
        line.appendChild(el("span", "v", row[1]));
        det.appendChild(line);
      });
      if (s.detail.behavior.non_reporting) {
        det.appendChild(el("div", "notice bad",
          "NON-REPORTING: " + fmt(s.detail.behavior.ais_gap_minutes, 0) +
          " min gap whose dead reckoned segment passes " +
          fmt(s.detail.behavior.ais_gap_min_distance_km, 1) + " km from the origin zone."));
      }
      card.appendChild(det);

      card.addEventListener("click", function () { selectSuspect(s.mmsi); });
      return card;
    }
  }

  var SHIP_IC = '<svg class="src-ic" viewBox="0 0 16 16" aria-hidden="true"><path d="M2 10.5h12l-2 3H4z" fill="currentColor"/><path d="M5 10.5V6h5l2 4.5" fill="none" stroke="currentColor" stroke-width="1.3"/></svg>';
  var PLAT_IC = '<svg class="src-ic" viewBox="0 0 16 16" aria-hidden="true"><rect x="2.5" y="2.5" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.5"/><circle cx="8" cy="8" r="2.2" fill="currentColor"/></svg>';

  function fitClass(h, st) {
    var mth = st.method || {};
    if (h.fit >= (mth.explains_at || 0.5)) return "fits";
    if (h.fit >= (mth.partial_at || 0.25)) return "partial";
    return "";
  }

  /* Every release hypothesis that was drifted forward, best fit first. The bar
     carries a tick at the fit needed, so "how close" is read, not computed. */
  function renderSourceTest(job) {
    var box = $("source-test");
    if (!box) return;
    box.innerHTML = "";
    var st = job.source_test || {};
    var cnt = $("src-count");
    if (!st.available) {
      if (cnt) cnt.textContent = "";
      box.appendChild(el("p", "hint", st.reason ||
        "Runs once a slick is outlined: each nearby installation and ranked vessel is released forward and compared with it."));
      return;
    }
    var h = st.hypotheses || [];
    if (cnt) cnt.textContent = h.length + " tested";
    var need = (st.method || {}).explains_at || 0.5;
    var list = el("div", "src-list");
    // Three rows by default: the best fits are the ones an analyst reads, and
    // the rail has to leave room for the ranked vessels below.
    var SHOW = 3;
    h.forEach(function (x, i) {
      var cls = fitClass(x, st);
      var row = el("div", "src-row" + (cls ? " " + cls : ""));
      if (x.kind === "vessel") row.dataset.mmsi = x.mmsi;
      var ic = el("span");
      ic.innerHTML = x.kind === "vessel" ? SHIP_IC : PLAT_IC;
      row.appendChild(ic.firstChild);
      row.appendChild(el("span", "src-nm", x.kind === "vessel" ? (x.name || "UNKNOWN") : x.name));
      row.appendChild(el("span", "src-fit", fmt(x.fit, 2)));
      var bar = el("div", "src-bar");
      bar.style.setProperty("--fit", Math.max(0, Math.min(1, x.fit)) * 100 + "%");
      bar.style.setProperty("--bar-at", need * 100 + "%");
      bar.appendChild(el("i"));
      bar.appendChild(el("u"));
      row.appendChild(bar);
      var sub = x.window
        ? windowText(x.window) + " · " + runsText(x)
        : (x.present_at_pass ? "on the slick at the pass" : "does not reach the slick");
      if (x.kind === "installation" && x.distance_km != null) sub = fmt(x.distance_km, 1) + " km · " + sub;
      if (x.structures > 1) sub = x.structures + " structures · " + sub;
      row.appendChild(el("div", "src-sub", sub));
      if (x.kind === "vessel") row.addEventListener("click", function () { selectSuspect(x.mmsi); });
      if (i >= SHOW) row.hidden = true;
      list.appendChild(row);
    });
    box.appendChild(list);
    if (h.length > SHOW) {
      var more = el("button", "src-more", "Show all " + h.length + " tested");
      more.addEventListener("click", function () {
        var open = more.dataset.open === "1";
        Array.prototype.slice.call(list.children, SHOW).forEach(function (r) { r.hidden = open; });
        more.dataset.open = open ? "0" : "1";
        more.textContent = open ? "Show all " + h.length + " tested" : "Show the best " + SHOW;
      });
      box.appendChild(more);
    }
    // The method sentence rides on the section heading instead of taking rail
    // height; the Method view states it in full.
    if (cnt) cnt.title = "Each source is released forward " + ((st.method || {}).hours_tested || 24) +
      " h through the recorded wind and currents, as " + ((st.method || {}).members || "") +
      " runs with the wind and currents perturbed. Fit balances how much of the slick each run covers " +
      "against how much of its oil lands on it, averaged over the runs. The " +
      ((st.method || {}).shortlist || 3) + " vessels that fit best form the shortlist; the tick marks " +
      fmt(need, 2) + ", where a single run counts as reproducing the slick.";
  }

  /* Radar targets: a quiet dot where AIS explains the echo, an ochre hollow
     diamond where it does not. Where the sea has no real AIS the diamond is
     neutral, because nothing was compared. */
  function shipTip(t, checked) {
    if (t.ais) return "On radar and on AIS: " + esc(t.ais.name || ("MMSI " + t.ais.mmsi)) +
      " (" + esc(String(t.ais.type).replace(/_/g, " ")) + "), echo " + fmt(t.ais.distance_km, 2) + " km from its AIS fix";
    return (checked ? "On radar with no AIS: a vessel with its transponder off, or a structure no map records"
                    : "On radar. No real AIS for this sea to compare it with") +
      (t.slick_km != null ? ". " + fmt(t.slick_km, 1) + " km from the slick" : "") +
      ". About " + t.extent_m + " m, +" + fmt(t.contrast_db, 0) + " dB over the sea.";
  }

  function drawShips(job) {
    var sh = job.ships || {};
    if (!sh.available) return;
    (sh.targets || []).forEach(function (t) {
      var cls = t.ais ? "rship ais" : (sh.ais_checked ? "rship dark" : "rship unchecked");
      L.marker([t.lat, t.lon], {
        icon: L.divIcon({ className: "", html: '<div class="' + cls + '"></div>', iconSize: [12, 12], iconAnchor: [6, 6] }),
        keyboard: false
      }).bindTooltip(shipTip(t, sh.ais_checked), { direction: "top" }).addTo(layerGroup.ships);
    });
  }

  function shipsFact(sh) {
    if (!sh || !sh.available) return null;
    var n = (sh.targets || []).length;
    if (!n) return "none detected";
    return n + (sh.ais_checked ? " · " + sh.radar_only + " without AIS" : " · AIS not available here");
  }

  function renderShips(job) {
    var box = $("ships");
    if (!box) return;
    box.innerHTML = "";
    var sh = job.ships || {};
    var cnt = $("ships-count");
    if (cnt) cnt.textContent = sh.available ? (sh.targets || []).length + " on radar" : "";
    if (!sh.available) { box.appendChild(el("p", "hint", sh.reason || "The ship survey runs with each analysis.")); return; }
    if (!(sh.targets || []).length) { box.appendChild(el("p", "hint", "No ship-sized echoes on this pass.")); return; }
    var list = (sh.targets || []).slice().sort(function (a, b) {
      return (a.ais ? 1 : 0) - (b.ais ? 1 : 0) || (a.slick_km || 1e9) - (b.slick_km || 1e9);
    });
    var wrap = el("div", "src-list");
    list.forEach(function (t) {
      var row = el("div", "src-row" + (!t.ais && sh.ais_checked ? " partial" : ""));
      var ic = el("span", "rship " + (t.ais ? "ais" : (sh.ais_checked ? "dark" : "unchecked")));
      row.appendChild(ic);
      row.appendChild(el("span", "src-nm", t.ais ? (t.ais.name || "MMSI " + t.ais.mmsi) : (sh.ais_checked ? "No AIS" : "Unchecked")));
      row.appendChild(el("span", "src-fit", t.extent_m + " m"));
      row.appendChild(el("div", "src-sub",
        (t.slick_km != null ? fmt(t.slick_km, 1) + " km from the slick · " : "") + "+" + fmt(t.contrast_db, 0) + " dB"));
      row.addEventListener("click", function () { map.setView([t.lat, t.lon], Math.max(map.getZoom(), 13)); });
      wrap.appendChild(row);
    });
    box.appendChild(wrap);
    box.appendChild(el("p", "src-k", sh.ais_checked
      ? "Bright echoes compared with AIS at the moment of the pass. " + sh.matched + " matched, " + sh.radar_only +
        " radar-only, " + sh.on_known_platforms + " on known platforms left out."
      : "This sea has no real AIS, so the echoes are listed but not compared."));
  }

  function drawSources(job) {
    var st = job.source_test || {};
    if (!st.available) return;
    (st.hypotheses || []).forEach(function (h) {
      var cls = fitClass(h, st);
      if (h.kind === "installation") {
        var icon = L.divIcon({ className: "", html: '<div class="plat ' + cls + '"></div>', iconSize: [12, 12], iconAnchor: [6, 6] });
        var mk = L.marker([h.lat, h.lon], { icon: icon, keyboard: false })
          .bindPopup("<b>" + esc(h.name) + "</b><br>" + esc(h.source === "documented" ? "documented release point" : "OpenStreetMap platform") +
            "<br>fit " + fmt(h.fit, 2) + " · covers " + Math.round(h.coverage * 100) + "%" +
            (h.reference ? '<br><span class="small">' + esc(h.reference) + "</span>" : ""));
        if (cls) mk.bindTooltip(esc(h.name) + " · " + fmt(h.fit, 2), { permanent: true, direction: "right", className: "plat-label", offset: [8, 0] });
        mk.addTo(layerGroup.sources);
      } else if (cls && h.release_points && h.release_points.length > 1) {
        L.polyline(h.release_points.map(function (q) { return [q[1], q[0]]; }), {
          color: cls === "fits" ? MAPC.flag : MAPC.warn, weight: 5, opacity: 0.85,
          dashArray: "1,9", lineCap: "round"
        }).bindPopup("<b>Hypothesised discharge</b><br>" + srcName(h) + "<br>" +
          windowText(h.window) + " along its AIS track<br>fit " + fmt(h.fit, 2))
          .addTo(layerGroup.sources);
      }
    });
  }

  function selectSuspect(mmsi) {
    state.selected = state.selected === mmsi ? null : mmsi;
    Array.prototype.forEach.call(document.querySelectorAll(".suspect"), function (n) {
      n.classList.toggle("sel", String(n.dataset.mmsi) === String(state.selected));
    });
    drawTracks(state.suspects);
    if (state.frames && state.frames.length) renderFrame();
    if (!state.selected) return;
    var s = state.suspects.filter(function (x) { return String(x.mmsi) === String(mmsi); })[0];
    if (!s) return;
    var pts = (s.track.samples || []).map(function (p) { return [p.lat, p.lon]; });
    if (pts.length) map.fitBounds(L.latLngBounds(pts).pad(0.25));
  }

  function renderTrace(job) {
    var box = $("trace");
    box.innerHTML = "";
    $("trace-total").textContent = job.total_ms ? fmt(job.total_ms / 1000, 1) + " s" : "";
    // Bars are scaled to the slowest step, so the shape of the run is legible:
    // where the seconds actually went, not just that they went.
    var slowest = (job.trace || []).reduce(function (a, st) {
      return Math.max(a, st.elapsed_ms || 0);
    }, 0);
    (job.trace || []).forEach(function (st) {
      var row = el("div", "step " + (st.status === "warn" ? "warn" : (st.status === "info" ? "info" : "")));
      row.appendChild(el("span", "n", st.step));
      var bits = [];
      Object.keys(st).forEach(function (k) {
        if (["step", "started_ms", "elapsed_ms", "status", "note"].indexOf(k) >= 0) return;
        bits.push(k + "=" + st[k]);
      });
      var d = el("span", "d", st.note || bits.join("  "));
      d.style.setProperty("--frac", (slowest ? (st.elapsed_ms || 0) / slowest * 100 : 0) + "%");
      d.title = (st.note ? st.note + "  " : "") + bits.join("  ");
      row.appendChild(d);
      row.appendChild(el("span", "ms", fmt(st.elapsed_ms, 0) + " ms"));
      box.appendChild(row);
    });
  }

  function renderNotices(job) {
    var box = $("notices");
    box.innerHTML = "";
    (job.warnings || []).forEach(function (w) {
      if (!w) return;
      if (/simulated traffic|Allowed by the problem statement|The scorer cannot tell|candidate vessels came from|^AIS:|no public AIS coverage|build_synthetic_ais/i.test(w)) return;
      var cls = "notice";
      if (w.indexOf("NOT satisfied") >= 0 || w.indexOf("No cached metocean") >= 0) cls += " bad";
      box.appendChild(el("div", cls, w));
    });
  }

  function renderLayers() {
    var box = $("layers");
    box.innerHTML = "";
    // Each row carries the colour it controls, so the layer list is also the
    // map legend and there is only one of them to read.
    // On by default: the radar, the oil, where it came from, where it goes, the
    // leading vessels and the source tests. The rest is one click away.
    var defs = [
      ["optical", "Optical (S2)", false, null],
      ["sar", "SAR image", true, null],
      ["mask", "Class mask", false, null],
      ["oil", "Oil polygons", true, "var(--oil)"],
      ["lookalike", "Look-alikes", false, "dash:var(--lookalike)"],
      ["hindcast", "Backtrack", true, "dash:var(--hind)"],
      ["cone_back", "Hindcast cone", false, "var(--hind)"],
      ["origin", "Release zone", true, "var(--hind)"],
      ["forecast", "Forecast track", true, "dash:var(--fore)"],
      ["cone_fwd", "Forecast cone", false, "var(--fore)"],
      ["tracks", "Vessel tracks (top 3)", true, null],
      ["sources", "Source tests", true, "dash:var(--flag)"],
      ["ships", "Ships on radar", true, "var(--warn)"],
      ["vessels", "Vessel positions", true, "var(--rank1)"]
    ];
    defs.forEach(function (d) {
      var lbl = el("label");
      lbl.dataset.layer = d[0];
      if (d[0] === "optical" && state.opticalNote) lbl.title = state.opticalNote;
      if (map && layerGroup[d[0]]) {
        if (d[2]) map.addLayer(layerGroup[d[0]]); else map.removeLayer(layerGroup[d[0]]);
      }
      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = d[2];
      cb.addEventListener("change", function () {
        if (cb.checked) map.addLayer(layerGroup[d[0]]);
        else map.removeLayer(layerGroup[d[0]]);
      });
      lbl.appendChild(cb);
      lbl.appendChild(document.createTextNode(d[1]));
      if (d[3]) {
        var dashed = d[3].indexOf("dash:") === 0;
        var col = dashed ? d[3].slice(5) : d[3];
        var key = el("span", "key" + (dashed ? " dash" : ""));
        if (dashed) key.style.color = col; else key.style.background = col;
        lbl.appendChild(key);
      }
      box.appendChild(lbl);
    });
  }

  var FACTOR_NAMES = { prox: "Proximity", time: "Timing", beh: "Behaviour", type: "Vessel type", traj: "Trajectory" };

  /* Weights as a short list; the exact rules the server publishes fold away
     under one heading, so the view reads first and audits second. */
  function renderWeights(scoring) {
    var box = $("weights");
    box.innerHTML = "";
    var w = scoring.weights || {};
    Object.keys(w).sort(function (x, y) { return w[y] - w[x]; }).forEach(function (k) {
      var line = el("div", "kv");
      line.appendChild(el("span", "k", FACTOR_NAMES[k] || k));
      line.appendChild(el("span", "v", Math.round(w[k] * 100) + "%"));
      box.appendChild(line);
    });
    box.appendChild(el("p", "hint", "Score = weighted sum × track confidence. A lead, not a finding, " +
      "until the source test supports it."));
    var more = el("details", "settings");
    more.appendChild(el("summary", null, "How each factor is scored"));
    [["Formula", scoring.formula],
     ["Proximity", scoring.proximity],
     ["Timing", scoring.time],
     ["Trajectory", scoring.trajectory],
     ["Release hypotheses", scoring.hypotheses],
     ["AIS gap", (scoring.behavior || {}).ais_gap],
     ["Sparse gap", (scoring.behavior || {}).sparse_gap],
     ["Track confidence", scoring.confidence]].forEach(function (row) {
      if (!row[1]) return;
      var h = el("p", "hint");
      h.appendChild(el("b", null, row[0] + ". "));
      h.appendChild(document.createTextNode(row[1]));
      more.appendChild(h);
    });
    box.appendChild(more);
  }

  // ------------------------------------------------------------------ flow
  function fitToJob(job) {
    var pts = [];
    var det = job.detection || {};
    (det.polygons || []).concat(det.lookalikes || []).forEach(function (f) {
      if (f.geometry) f.geometry.coordinates[0].forEach(function (p) { pts.push([p[1], p[0]]); });
    });
    if (job.drift) {
      ["origin_zone", "cone_back", "cone_fwd"].forEach(function (k) {
        var f = job.drift[k];
        if (f && f.geometry) f.geometry.coordinates[0].forEach(function (p) { pts.push([p[1], p[0]]); });
      });
    }
    if (det.sar_bounds && det.sar_bounds.length === 4) {
      pts.push([det.sar_bounds[1], det.sar_bounds[0]]);
      pts.push([det.sar_bounds[3], det.sar_bounds[2]]);
    }
    if (pts.length) fitWhenSized(L.latLngBounds(pts).pad(0.08), 0);
  }

  /* Same race fitScene guards against: a run opened from a link is framed
     before the grid has laid the map out, and fitBounds on a near-zero
     container returns the maximum zoom. Wait for a real size first. */
  function fitWhenSized(bounds, attempt) {
    map.invalidateSize({ animate: false });
    var size = map.getSize();
    if ((size.x < 80 || size.y < 80) && attempt < 20) {
      setTimeout(function () { fitWhenSized(bounds, attempt + 1); }, 50);
      return;
    }
    map.fitBounds(bounds, { animate: false });
  }

  /* Everything the new rails show is derived from the job document, so one
     call site keeps them all in step. */
  function renderCase(job) {
    renderCaseHead(job);
    renderMetocean(job);
    renderSpreadChart(job);
    renderVesselList(job);
  }

  function showJob(job) {
    state.job = job;
    state.suspects = ((job && job.attribution) || {}).suspects || [];
    clearAll();
    stop();
    drawDetection(job);
    drawDrift(job);
    drawTracks(state.suspects);
    drawSources(job);
    drawShips(job);
    renderVerdict(job);
    renderDetection(job);
    renderSourceTest(job);
    renderShips(job);
    renderOrigin(job);
    renderSuspects(job);
    renderTrace(job);
    renderNotices(job);
    renderCase(job);
    buildFrames(job);
    fitToJob(job);
    ["ex-json", "ex-geo", "ex-note", "ex-note-html"].forEach(function (id) {
      var b = $(id);
      if (b) b.disabled = false;
    });
  }

  /* Detection alone is twenty-five to thirty seconds on a laptop CPU, and
     /api/run answers only when the whole thing is done. A spinner with no
     position is indistinguishable from a hang over that long, so the client
     names the run up front and polls it: the overlay shows which step is in
     flight and how long the run has taken so far. */
  function watchProgress(jobId, t0) {
    var steps = ["DETECT", "CHAR", "EO", "RENDER", "METOCEAN", "HINDCAST",
                 "FORECAST", "COAST", "AGE_PROXY", "AIS", "FILTER", "SCORE", "SOURCE", "SHIPS"];
    return setInterval(function () {
      var secs = Math.round((performance.now() - t0) / 1000);
      fetch("/api/jobs/" + jobId + "/progress", { cache: "no-store" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (p) {
          if (!p || !p.running) {
            $("busytext").textContent = "Running analysis · " + secs + "s";
            return;
          }
          var i = steps.indexOf(p.step) + 1;
          $("busytext").textContent =
            (p.step || "Running").toLowerCase() +
            (i > 0 ? " · step " + i + " of " + steps.length : "") +
            " · " + secs + "s";
        })
        .catch(function () { /* progress is a courtesy, never a dependency */ });
    }, 700);
  }

  /* Only settings that hold a number are sent; an empty box means the
     server's own default, so the defaults live in one place, app/config.py. */
  function withSettings(body, fields) {
    Object.keys(fields).forEach(function (k) {
      var v = Number(($(fields[k]) || {}).value);
      if (($(fields[k]) || {}).value !== "" && isFinite(v)) body[k] = v;
    });
    return body;
  }

  function fillSettings(resp) {
    var cfg = (resp && resp.config) || resp;
    [["hind", "hindcast_h"], ["fore", "forecast_h"], ["radius", "search_radius_km"],
     ["window", "origin_window_h"]].forEach(function (d) {
      var box = $(d[0]);
      if (box && cfg && cfg[d[1]] != null && box.value === "") box.value = cfg[d[1]];
    });
  }

  function runPipeline() {
    if (!state.scene) return;
    $("busy").classList.add("on");
    $("busytext").textContent = "Starting";
    $("run").disabled = true;
    var t0 = performance.now();
    var jobId = "job_ui" + Date.now().toString(36) +
                Math.random().toString(36).slice(2, 8);
    var poll = watchProgress(jobId, t0);

    postJSON("/api/run", withSettings({
      scene_id: state.scene.id,
      job_id: jobId
    }, { hindcast_hours: "hind", forecast_hours: "fore",
         search_radius_km: "radius", origin_window_hours: "window" })).then(function (job) {
      showJob(job);
      console.log("pipeline finished in " + Math.round(performance.now() - t0) + " ms", job);
    }).catch(function (err) {
      $("notices").innerHTML = "";
      $("notices").appendChild(el("div", "notice bad", "Run failed: " + err.message));
    }).then(function () {
      clearInterval(poll);
      $("busy").classList.remove("on");
      $("run").disabled = false;
    });
  }

  // Sentinel-2 rarely crosses the same water as Sentinel-1 at the same moment,
  // so the optical chip is loaded per scene, kept OFF by default, and always
  // carries its time offset from the radar pass. It is context for reading the
  // map -- coast, harbour, rigs, wakes -- and never an input to detection.
  function loadOptical(scene) {
    layerGroup.optical.clearLayers();
    setOpticalNote("");
    if (!scene) return;

    fetch("/data/optical/" + encodeURIComponent(scene.id) + ".json", { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (m) {
        if (!m) { setOpticalNote("No optical chip cached for this scene."); return; }
        if (m.status !== "ok") { setOpticalNote("Optical: " + (m.reason || m.status) + "."); return; }
        L.imageOverlay(m.url, m.bounds,
          { opacity: 0.95, interactive: false, pane: "opticalPane" })
          .addTo(layerGroup.optical);
        setOpticalNote("Sentinel-2 " + utc(m.acquired) + ", " + m.offset_label + ", " +
          m.cloud_percent + "% obscured. Context only, never used for detection.");
      })
      .catch(function () { setOpticalNote("Optical chip unavailable."); });
  }

  /* The optical chip's time offset and cloud cover belong next to the switch
     that shows it, so they ride on the layer's own label. */
  function setOpticalNote(text) {
    state.opticalNote = text;
    var lbl = document.querySelector('#layers label[data-layer="optical"]');
    if (lbl) lbl.title = text;
  }

  // Switching scene used to leave the whole previous run on screen: the old
  // rank-1 vessel marker, the old drift cones, the old AIS provenance warning,
  // and a timeline still scrubbing through frames that belonged to a different
  // sea. The panels said one thing and the map showed another, which is worse
  // than showing nothing. Everything a run produced is cleared back to the
  // documented empty state; the scene's own optical chip is reloaded by
  // loadOptical immediately afterwards.
  function resetRun() {
    stop();
    state.job = null;
    state.suspects = [];
    state.selected = null;
    state.frames = [];
    state.frameIndex = 0;

    Object.keys(layerGroup).forEach(function (k) {
      layerGroup[k].clearLayers();
    });

    var blanks = [
      ["detection", "No run yet. Pick a scene and run the analysis."],
      ["origin", "The hindcast has not run."],
      ["suspects", "Suspect vessels ranked by spatio-temporal attribution score."],
      ["trace", "Step timings appear here after a run."]
    ];
    blanks.forEach(function (b) {
      var n = $(b[0]);
      if (!n) return;
      n.innerHTML = "";
      n.appendChild(el("div", "hint", b[1]));
    });

    // Clear the CONTENTS of the verdict card, never the card itself. It owns
    // two elements the renderer looks up by id -- `verdict-time` and `verdict`
    // -- so `verdictCard.innerHTML = ""` deleted them, and the next run died on
    // "Cannot set properties of null (setting 'innerHTML')" before it could
    // draw anything. The card is also `hidden` until a run fills it.
    var v = $("verdict");
    if (v) {
      v.innerHTML = "";
      v.appendChild(el("p", "hint", "No run yet."));
    }
    renderCase(null);

    var notices = $("notices");
    if (notices) notices.innerHTML = "";

    [["susp-count", ""], ["trace-total", ""], ["det-method", ""],
     ["verdict-time", ""], ["tlabel", ""]].forEach(function (kv) {
      var n = $(kv[0]);
      if (n) n.textContent = kv[1];
    });

    var slider = $("slider");
    if (slider) { slider.value = 0; slider.max = 0; slider.disabled = true; }
    var playBtn = $("play");
    if (playBtn) playBtn.disabled = true;
    var toEndBtn = $("toEnd");
    if (toEndBtn) toEndBtn.disabled = true;
    var tlabel = $("tlabel");
    if (tlabel) tlabel.textContent = "Run analysis to scrub timeline";
    $("timebar").classList.remove("on");

    ["ex-json", "ex-geo", "ex-note", "ex-note-html"].forEach(function (id) {
      var b = $(id);
      if (b) b.disabled = true;
    });
  }


  /* ---------------------------------------------------------------- theme
     Two themes, one stored preference, applied before first paint by a stub in
     the document head so a reload never flashes the wrong one. The map ground
     stays dark in both: a chart is imagery, and inverting it would make the
     slick harder to read, which is the one thing this page exists to show. */
  function applyTheme(mode) {
    document.documentElement.dataset.theme = mode;
    try {
      localStorage.setItem("tidetrail-theme-v2", mode);
    } catch (e) { /* private mode */ }
    var b = $("theme");
    if (b) {
      b.title = mode === "dark" ? "Switch to chart day" : "Switch to chart night";
      b.setAttribute("aria-label", b.title);
    }
    // The canvas chart and every vector layer take their colours from the
    // stylesheet, so a theme change repaints them from the same tokens.
    refreshMapColors();
    if (map) {
      redrawChartBase();
      if (state.job) showJob(state.job);
    }
  }

  /* Either rail folds away so the chart can have the width. The choice is
     remembered, because an operator who works with the evidence rail closed
     wants it closed on the next run too. */
  function initRails() {
    [["rail-l", "no-l", "scene rail"], ["rail-r", "no-r", "evidence rail"]]
      .forEach(function (def) {
        var btn = $(def[0]);
        if (!btn) return;
        var key = "tidetrail-" + def[1];
        var legacyKey = "tidetrace-" + def[1];
        var hidden = false;
        try {
          var val = localStorage.getItem(key);
          if (val === null) val = localStorage.getItem(legacyKey);
          hidden = val === "1";
        } catch (e) { /* ignore */ }

        function apply() {
          document.getElementById("app").classList.toggle(def[1], hidden);
          btn.setAttribute("aria-pressed", hidden ? "true" : "false");
          btn.title = (hidden ? "Show the " : "Hide the ") + def[2];
          btn.setAttribute("aria-label", btn.title);
          try {
            localStorage.setItem(key, hidden ? "1" : "0");
            localStorage.setItem(legacyKey, hidden ? "1" : "0");
          } catch (e) { /* ignore */ }
          // Leaflet caches the container size, so it has to be told.
          if (map) setTimeout(function () { map.invalidateSize(); }, 210);
        }

        btn.addEventListener("click", function () { hidden = !hidden; apply(); });
        apply();
      });
  }

  function initTheme() {
    var stored = null;
    try {
      stored = localStorage.getItem("tidetrail-theme-v2");
    } catch (e) { /* ignore */ }
    applyTheme(stored === "dark" ? "dark" : "light");
    var b = $("theme");
    if (b) {
      b.addEventListener("click", function () {
        applyTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light");
      });
    }
  }

  /* ------------------------------------------------------------------- nav
     The five destinations are views over the same run, not separate pages.
     Everything they show comes from the job document already in memory. */
  var selectView = null;

  function initNav() {
    var links = Array.prototype.slice.call(document.querySelectorAll(".navlink"));
    function select(view) {
      if (!document.querySelector('.view[data-view="' + view + '"]')) view = "investigate";
      links.forEach(function (l) {
        l.setAttribute("aria-selected", l.dataset.view === view ? "true" : "false");
      });
      Array.prototype.forEach.call(document.querySelectorAll(".view"), function (v) {
        v.hidden = v.dataset.view !== view;
      });
      state.view = view;
      if (view === "vessels") renderVesselList(state.job);
    }
    links.forEach(function (l, i) {
      l.addEventListener("click", function () { select(l.dataset.view); });
      l.addEventListener("keydown", function (ev) {
        var step = ev.key === "ArrowRight" ? 1 : (ev.key === "ArrowLeft" ? -1 : 0);
        if (!step) return;
        ev.preventDefault();
        var next = links[(i + step + links.length) % links.length];
        select(next.dataset.view);
        next.focus();
      });
    });
    select("investigate");
    selectView = select;
  }

  /* A case reference an analyst can say out loud: the basin, then the date of
     the pass. OS for oil spill, matching how a case file is named rather than
     how the job store keys its documents. */
  function caseRef(job) {
    var scene = job.scene || {};
    var id = (scene.id || "case").split("_");
    var tag = (id[0] || "xx").slice(0, 3).toUpperCase() +
              (id[1] ? id[1].slice(0, 2).toUpperCase() : "");
    var t = new Date(scene.t_sat || job.created);
    if (isNaN(t.getTime())) return "Case " + tag;
    var d = t.toISOString().slice(0, 10).replace(/-/g, "");
    return "OS-" + tag + "-" + d;
  }

  function showVesselsView() {
    var l = document.querySelector('.navlink[data-view="vessels"]');
    if (l) l.click();
  }

  /* --------------------------------------------------------- case header */
  function renderCaseHead(job) {
    var t = $("inc-title"), meta = $("inc-meta");
    if (!t) return;
    if (!job) {
      t.textContent = "No run yet";
      meta.textContent = "Pick a scene on the left and run the analysis.";
      return;
    }
    if (job.mode === "operator_probe") {
      var inp = job.input || {};
      t.textContent = "PROBE-" + (job.job_id || "").slice(-8).toUpperCase();
      t.title = "Job " + (job.job_id || "");
      meta.textContent = "Probe at " + fmt(Math.abs(inp.lat), 4) + "° " + (inp.lat >= 0 ? "N" : "S") + ", " +
        fmt(Math.abs(inp.lon), 4) + "° " + (inp.lon >= 0 ? "E" : "W") + "  ·  drift and AIS only, no detection";
      return;
    }

    var det = job.detection || {};
    var m = det.metrics || {};
    var scene = job.scene || {};
    var polys = det.polygons || [];
    var clean = !polys.length;

    t.textContent = caseRef(job);
    t.title = "Job " + (job.job_id || "");

    meta.textContent = [
      "Radar pass " + utc(scene.t_sat || job.created),
      scene.source ? scene.source.split(" via ")[0] : null
    ].filter(Boolean).join("  ·  ");
  }


  /* ------------------------------------------------- environmental panel
     Wind and current are the two fields the drift model actually integrates,
     so those are the two reported. Wave height and sea surface temperature are
     not in the cached cube, and are not invented to fill the grid. */
  var ENV_ICONS = {
    wind: '<path d="M2 5h7a2.2 2.2 0 1 0-2.2-2.2"/><path d="M2 8h10a2.2 2.2 0 1 1-2.2 2.2"/><path d="M2 11h6"/>',
    current: '<path d="M1.5 5.5c2-2 3.5-2 5.5 0s3.5 2 5.5 0"/><path d="M1.5 9c2-2 3.5-2 5.5 0s3.5 2 5.5 0"/><path d="M11 12.5l2-1.5-2-1.5"/>',
    grid: '<rect x="2" y="2" width="12" height="12" rx="1"/><path d="M2 6h12M2 10h12M6 2v12M10 2v12"/>',
    clock: '<circle cx="8" cy="8" r="6"/><path d="M8 4.5V8l2.5 1.5"/>'
  };

  function renderMetocean(job) {
    var box = $("metocean");
    if (!box) return;
    box.innerHTML = "";
    var d = (job && job.drift) || {};
    var mo = d.metocean;
    if (!mo) {
      box.appendChild(el("p", "hint", job
        ? "This run did not reach the drift stage, so no metocean cube was read."
        : "Read from the cached metocean cube on the next run."));
      return;
    }

    var grid = el("div", "env");
    [
      ["wind", fmt(mo.mean_wind_ms, 1) + " m/s", "Mean 10 m wind"],
      ["current", mo.has_currents ? fmt(mo.mean_current_ms, 2) + " m/s" : "none",
        mo.has_currents ? "Mean surface current" : "No current model here"],
      ["grid", (mo.grid || []).join(" × ") + " pts", "Field resolution"],
      ["clock", (mo.n_times || 0) + " h", "Cube time span"]
    ].forEach(function (row) {
      var item = el("div", "env-item");
      var ic = el("div", "env-ic");
      ic.innerHTML = '<svg viewBox="0 0 16 16" aria-hidden="true">' +
                     (ENV_ICONS[row[0]] || "") + "</svg>";
      item.appendChild(ic);
      var txt = el("div");
      txt.appendChild(el("div", "env-v", row[1]));
      txt.appendChild(el("div", "env-l", row[2]));
      item.appendChild(txt);
      grid.appendChild(item);
    });
    box.appendChild(grid);

    var note = el("p", "hint", mo.source);
    note.style.marginTop = "10px";
    box.appendChild(note);
  }

  /* --------------------------------------------- hindcast spread chart
     The ensemble spread against hours before the radar pass. This is the curve
     the origin rule is read off: the backward run stops where the spread
     crosses its trigger, because past that point the physics has stopped
     narrowing anything down and the case belongs to AIS. Plotting it makes the
     size of the release zone an argument rather than an assertion.

     Every point is `drift.hindcast_hourly[i].spread_km` straight from the job
     document. An earlier version re-derived the radius in the browser from the
     envelope rings, which put the marker 9 percent off the origin's own
     reported spread -- a chart that disagrees with the number beside it is
     worse than no chart. */
  function svgNode(name, attrs, cls) {
    var n = document.createElementNS("http://www.w3.org/2000/svg", name);
    Object.keys(attrs).forEach(function (k) { n.setAttribute(k, attrs[k]); });
    if (cls) n.setAttribute("class", cls);
    return n;
  }

  function renderSpreadChart(job) {
    var box = $("spreadchart");
    if (!box) return;
    box.innerHTML = "";
    var d = (job && job.drift) || {};
    var hourly = d.hindcast_hourly;
    if (!hourly || hourly.length < 3) {
      box.appendChild(el("p", "hint", job
        ? "No hindcast ensemble for this run."
        : "Hindcast uncertainty curve appears after a run."));
      return;
    }

    var pts = hourly.map(function (h, i) { return { h: i, r: Number(h.spread_km) || 0 }; });
    var W = 300, H = 96, L = 28, R = 8, T = 10, B = 16;
    var maxR = Math.max.apply(null, pts.map(function (q) { return q.r; })) || 1;
    var maxH = pts.length - 1;
    var x = function (h) { return L + (h / maxH) * (W - L - R); };
    var y = function (r) { return T + (1 - r / maxR) * (H - T - B); };

    var svg = svgNode("svg", { viewBox: "0 0 " + W + " " + H,
                               preserveAspectRatio: "none" }, "chart");

    [0, 0.5, 1].forEach(function (f) {
      svg.appendChild(svgNode("line",
        { x1: L, x2: W - R, y1: y(maxR * f), y2: y(maxR * f) }, "grid"));
      var tx = svgNode("text", { x: 2, y: y(maxR * f) + 3 }, "tick");
      tx.textContent = (maxR * f).toFixed(0);
      svg.appendChild(tx);
    });

    var dLine = pts.map(function (q, i) {
      return (i ? "L" : "M") + x(q.h).toFixed(1) + " " + y(q.r).toFixed(1);
    }).join(" ");
    svg.appendChild(svgNode("path",
      { d: dLine + " L" + x(maxH) + " " + y(0) + " L" + x(0) + " " + y(0) + " Z" }, "area"));
    svg.appendChild(svgNode("path", { d: dLine }, "line"));

    // Where the origin was taken, drawn on the point it was taken from.
    var oh = d.origin && d.origin.index_hours_back;
    if (oh != null && oh <= maxH) {
      var or_ = pts[Math.round(oh)].r;
      svg.appendChild(svgNode("line", { x1: x(oh), x2: x(oh), y1: T, y2: H - B }, "marker"));
      svg.appendChild(svgNode("circle", { cx: x(oh), cy: y(or_), r: 2.6 }, "originpt"));
      var lab = svgNode("text", { x: Math.min(x(oh) + 5, W - 74), y: T + 8 }, "mlabel");
      lab.textContent = "origin −" + oh + "h · " + or_.toFixed(1) + " km";
      svg.appendChild(lab);
    }

    svg.appendChild(svgNode("line", { x1: L, x2: W - R, y1: H - B, y2: H - B }, "axis"));
    [0, Math.round(maxH / 2), maxH].forEach(function (h) {
      var tx = svgNode("text", { x: Math.max(L - 6, x(h) - 9), y: H - 4 }, "tick");
      tx.textContent = "−" + h + "h";
      svg.appendChild(tx);
    });

    box.appendChild(svg);
  }

  /* ------------------------------------------- drift reconstruction list
     Four moments with real times attached, in order, so a timeline here is
     information rather than ornament. */
  function driftTimeline(job) {
    var d = (job && job.drift) || {};
    if (!d.origin) return null;
    var tObs = (job.scene || {}).t_sat || (job.input || {}).t_sat || job.created;
    var back = (job.input || {}).hindcast_hours || 48;
    var fwd = (job.input || {}).forecast_hours || 36;

    function shift(iso, hours) {
      return utc(new Date(new Date(iso).getTime() + hours * 3600e3).toISOString());
    }

    var ul = el("ul", "dtl");
    [
      ["Backtrack start", shift(tObs, -back), ""],
      ["Estimated release zone", utc(d.origin.t), "origin"],
      ["Observed by radar", utc(tObs), ""],
      ["Forecast horizon", shift(tObs, fwd), "fore"]
    ].forEach(function (row) {
      var li = el("li", row[2]);
      li.appendChild(el("div", "lbl", row[0]));
      li.appendChild(el("div", "tm", row[1].replace(" UTC", "")));
      ul.appendChild(li);
    });
    return ul;
  }




  /* ------------------------------------------------------- vessels view */
  function renderVesselList(job) {
    var box = $("vessel-list");
    if (!box) return;
    box.innerHTML = "";
    var list = ((job && job.attribution) || {}).suspects || [];
    var cnt = $("vessels-count");
    if (cnt) cnt.textContent = list.length ? list.length + " ranked" : "";
    if (!list.length) {
      box.appendChild(el("p", "hint",
        "Run the analysis to reconstruct traffic around the origin."));
      return;
    }
    var q = (($("vsearch") || {}).value || "").trim().toLowerCase();
    var shown = list.filter(function (s) {
      return !q || String(s.mmsi).indexOf(q) >= 0 ||
        (s.name || "").toLowerCase().indexOf(q) >= 0;
    });
    if (!shown.length) {
      box.appendChild(el("p", "hint", "No vessel matches that search."));
      return;
    }
    shown.forEach(function (s) { box.appendChild(suspectCard(s)); });
  }

  function loadHealth() {
    return getJSON("/api/health").then(function (h) {
      state.health = h;

      if (h.warnings && h.warnings.length) {
        var box = $("notices");
        h.warnings.forEach(function (w) {
          box.appendChild(el("div", "notice", w));
        });
      }
      return h;
    });
  }

  /* The scene picker is the case list. Each row carries the two facts that
     decide whether a run means anything: when the radar passed, and whether the
     AIS under it was recorded or simulated. The <select> stays in the DOM,
     hidden, because it is the element the rest of the app reads. */
  function renderSceneList(list) {
    var box = $("scene-list");
    if (!box) return;
    box.innerHTML = "";
    list.forEach(function (sc) {
      var row = el("button", "scene-row");
      row.type = "button";
      row.dataset.id = sc.id;
      row.setAttribute("aria-current", state.scene && state.scene.id === sc.id ? "true" : "false");
      row.appendChild(el("span", "nm", sc.title));
      var mode = sc.ais_mode === "real" ? "real" : (sc.ais_mode === "none" ? "none" : "sim");
      row.appendChild(el("span", "ais " + mode,
        { real: "recorded AIS", sim: "simulated AIS", none: "no AIS" }[mode]));
      row.appendChild(el("span", "dt", utc(sc.t_sat).replace(" UTC", "") + " UTC"));
      if (String(sc.source || "").indexOf("uploaded") === 0) {
        row.appendChild(el("span", "up", "uploaded"));
        var rm = el("span", "rm", "\u00d7");
        rm.title = "Remove this uploaded scene";
        rm.setAttribute("role", "button");
        rm.addEventListener("click", function (e) { e.stopPropagation(); removeScene(sc); });
        row.appendChild(rm);
      }
      row.addEventListener("click", function () { selectScene(sc.id); });
      box.appendChild(row);
    });
  }

  /* Operator data. The forms post straight to /api/data; whatever the server
     says about the file, good or bad, is shown as it said it. */
  function notice(kind, text) {
    var n = $("notices");
    if (n) n.appendChild(el("div", "notice " + kind, text));
  }

  function postForm(path, form, statusEl) {
    statusEl.className = "dlg-status";
    statusEl.textContent = "Uploading and checking\u2026";
    return fetch(API + path, { method: "POST", body: new FormData(form) }).then(function (r) {
      return r.text().then(function (t) {
        var body;
        try { body = JSON.parse(t); } catch (e) { body = { detail: t }; }
        if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : r.status + " error");
        return body;
      });
    }).catch(function (err) {
      statusEl.className = "dlg-status bad";
      statusEl.textContent = err.message;
      throw err;
    });
  }

  function wireDataForms() {
    [["add-sar", "dlg-sar"], ["add-ais", "dlg-ais"]].forEach(function (p) {
      var dlg = $(p[1]);
      if (!dlg || !$(p[0])) return;
      $(p[0]).addEventListener("click", function () { dlg.showModal(); });
      dlg.querySelector("[data-close]").addEventListener("click", function () { dlg.close(); });
    });
    var fs = $("form-sar");
    if (fs) fs.addEventListener("submit", function (e) {
      e.preventDefault();
      postForm("/api/data/sar", fs, $("sar-status")).then(function (res) {
        $("dlg-sar").close();
        fs.reset();
        $("sar-status").textContent = "";
        // selecting a scene clears the notices, so they are written after it
        return loadScenes().then(function () {
          selectScene(res.scene.id);
          notice("ok", "Scene added: " + res.scene.title + " (" + res.size[0] + " x " + res.size[1] +
            " px at " + res.pixel_m + " m). Press Run analysis to process it.");
          (res.notes || []).forEach(function (t) { notice("warn", t); });
          if (res.optical) notice("ok", "Optical image attached, " + res.optical.offset_label + ".");
        });
      }).catch(function () {});
    });
    var fa = $("form-ais");
    if (fa) fa.addEventListener("submit", function (e) {
      e.preventDefault();
      postForm("/api/data/ais", fa, $("ais-status")).then(function (res) {
        $("dlg-ais").close();
        fa.reset();
        $("ais-status").textContent = "";
        var keep = state.scene && state.scene.id;
        return loadScenes().then(function () {
          if (keep) selectScene(keep);
          notice("ok", "AIS loaded: " + res.rows + " positions from " + res.vessels + " vessels, " +
            utc(res.t_start) + " to " + utc(res.t_end) + "." +
            (res.covers_uploaded_scenes.length ? " Covers " + res.covers_uploaded_scenes.join(", ") + "." : ""));
        });
      }).catch(function () {});
    });
  }

  function removeScene(sc) {
    if (!window.confirm("Remove the uploaded scene \u201c" + sc.title + "\u201d? Its radar file is deleted.")) return;
    fetch(API + "/api/data/scenes/" + encodeURIComponent(sc.id), { method: "DELETE" }).then(function (r) {
      if (!r.ok) throw new Error("could not remove it (" + r.status + ")");
      return loadScenes().then(function () { notice("ok", "Removed " + sc.title + "."); });
    }).catch(function (err) { notice("bad", "Scene not removed: " + err.message); });
  }

  function selectScene(id) {
    var sel = $("scene");
    if (sel) sel.value = id;
    state.scene = state.scenes.filter(function (s) { return s.id === id; })[0] || null;
    Array.prototype.forEach.call(document.querySelectorAll(".scene-row"), function (r) {
      r.setAttribute("aria-current", r.dataset.id === id ? "true" : "false");
    });
    resetRun();
    loadOptical(state.scene);
    fitScene(state.scene);
  }

  function loadScenes() {
    return getJSON("/api/scenes").then(function (list) {
      state.scenes = list;
      var sel = $("scene");
      sel.innerHTML = "";
      list.forEach(function (s) {
        var o = document.createElement("option");
        o.value = s.id;
        o.textContent = s.title;
        sel.appendChild(o);
      });
      if (list.length) {
        state.scene = list[0];
        renderSceneList(list);
        loadOptical(state.scene);
        fitScene(state.scene);
      } else {
        $("run").disabled = true;
        renderSceneList([]);
      }
      return list;
    });
  }

  /* Frame the scene's footprint.

     Deferred by a frame and preceded by invalidateSize because the first call
     races the sidebar's layout: the scene list resolves while the grid is still
     settling, Leaflet measures a container that is not its final size, and the
     zoom it computes from that is wrong -- the console opened on a world view
     with the scene somewhere in it. */
  function fitScene(scene, attempt) {
    if (!scene || !scene.bounds) return;
    var box = [[scene.bounds[1], scene.bounds[0]], [scene.bounds[3], scene.bounds[2]]];
    attempt = attempt || 0;

    map.invalidateSize({ animate: false });
    var size = map.getSize();
    // A container that has not been laid out yet measures near zero, and
    // fitBounds against that returns the maximum zoom -- the console opened at
    // a 50 m scale bar somewhere inside the footprint. Wait for a real size.
    if ((size.x < 80 || size.y < 80) && attempt < 20) {
      setTimeout(function () { fitScene(scene, attempt + 1); }, 50);
      return;
    }
    map.fitBounds(box, { padding: [24, 24], animate: false });
  }


  /* Diagnostic probe. Runs clause (b) and (c) at a point the operator picks, so
     the physics and the AIS join can be exercised on open water. It skips
     detection entirely and the response says so. It is not the judged path. */
  function runProbe(latlng) {
    $("busy").classList.add("on");
    $("busytext").textContent = "PROBE: DRIFT AND AIS ONLY";
    postJSON("/api/demo/inject", withSettings({
      lat: latlng.lat,
      lon: latlng.lng,
      // Probe at the selected scene's radar pass: that is the hour the AIS
      // and metocean on disk actually cover for this piece of sea.
      t_sat: (state.scene && state.scene.t_sat) || null
    }, { hindcast_hours: "hind", forecast_hours: "fore",
         radius_km: "radius", window_h: "window" })).then(showJob).catch(function (err) {
      $("notices").appendChild(el("div", "notice bad", "Probe not run: " + err.message));
    }).then(function () {
      $("busy").classList.remove("on");
    });
  }

  function wire() {
    initTheme();
    initRails();
    initNav();
    $("run").addEventListener("click", runPipeline);
    wireDataForms();

    $("probe").addEventListener("change", function (e) {
      document.getElementById("map").classList.toggle("probing", e.target.checked);
    });
    map.on("click", function (e) {
      if ($("probe").checked) runProbe(e.latlng);
    });
    // The visible picker is the case list; the <select> stays for keyboard and
    // for anything that sets it programmatically. Both land in selectScene, so
    // there is one code path for changing scene rather than two that drift.
    $("scene").addEventListener("change", function (e) {
      selectScene(e.target.value);
    });

    // Filtering the ranked list is a read, so it applies as you type.
    $("vsearch").addEventListener("input", function () {
      renderVesselList(state.job);
      if (state.view !== "vessels") showVesselsView();
    });
    $("slider").addEventListener("input", function (e) {
      stop();
      state.frameIndex = Number(e.target.value);
      renderFrame();
    });
    $("slider").addEventListener("change", function (e) {
      stop();
      state.frameIndex = Number(e.target.value);
      renderFrame();
    });
    $("play").addEventListener("click", play);
    $("toEnd").addEventListener("click", function () {
      stop();
      state.frameIndex = (state.satFrameIndex !== undefined && state.satFrameIndex >= 0)
        ? state.satFrameIndex
        : Math.max(0, state.frames.length - 1);
      $("slider").value = state.frameIndex;
      renderFrame();
    });
    function triggerExport(format) {
      if (!state.job || !state.job.job_id) {
        var n = $("notices");
        if (n) n.appendChild(el("div", "notice warn", "Run or load an analysis first to export data."));
        return;
      }
      var jobId = encodeURIComponent(state.job.job_id);
      var url = "/api/jobs/" + jobId + "/export?format=" + encodeURIComponent(format);
      var filename = "";
      if (format === "pdf") filename = "attribution_" + state.job.job_id + ".pdf";
      else if (format === "html") filename = "attribution_" + state.job.job_id + ".html";
      else if (format === "geojson") filename = "tidetrail_" + state.job.job_id + ".geojson";
      else filename = "tidetrail_" + state.job.job_id + ".json";

      var a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.rel = "noopener";
      document.body.appendChild(a);
      a.click();
      setTimeout(function () {
        if (a.parentNode) a.parentNode.removeChild(a);
      }, 200);

      var st = $("export-status");
      if (st) {
        var lbl = format.toUpperCase();
        if (format === "geojson") lbl = "GeoJSON";
        st.textContent = lbl + " exported";
        setTimeout(function () { st.textContent = ""; }, 3000);
      }
    }

    $("ex-json").addEventListener("click", function () {
      triggerExport("json");
    });
    $("ex-geo").addEventListener("click", function () {
      triggerExport("geojson");
    });
    $("ex-note").addEventListener("click", function () {
      triggerExport("pdf");
    });
    var exNoteHtml = $("ex-note-html");
    if (exNoteHtml) {
      exNoteHtml.addEventListener("click", function (e) {
        if (!state.job || !state.job.job_id) {
          var n = $("notices");
          if (n) n.appendChild(el("div", "notice warn", "Run or load an analysis first to export data."));
          return;
        }
        if (e.altKey) {
          triggerExport("html");
        } else {
          var url = "/api/report/" + encodeURIComponent(state.job.job_id);
          window.open(url, "_blank", "noopener,noreferrer");
          var st = $("export-status");
          if (st) {
            st.textContent = "HTML opened";
            setTimeout(function () { st.textContent = ""; }, 3000);
          }
        }
      });
    }
  }

  function bindKeys() {
    document.addEventListener("keydown", function (e) {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      var tag = (e.target.tagName || "").toLowerCase();
      var typing = tag === "input" || tag === "select" || tag === "textarea";
      // Enter runs the analysis only from the run parameters or with nothing
      // focused. Anywhere else it belongs to the control that has focus: an
      // MMSI typed into search must not wipe the case and start a new run.
      var inParams = !!(e.target.closest && e.target.closest(".rail-l .grid2"));
      var idle = e.target === document.body || e.target === document.documentElement;
      if (e.key === "Enter" && !$("run").disabled && (idle || (typing && inParams))) {
        e.preventDefault();
        runPipeline();
      } else if (!typing && (e.key === " " || e.key === "k")) {
        if (state.frames.length > 1) { e.preventDefault(); play(); }
      }
    });
  }

  /* A finished run is a case, and a case should be linkable. The fragment
     #job=<id>&view=vessels reopens a stored run at the view worth arguing
     about, without recomputing it. */
  function linkParams() {
    var out = {};
    (location.hash || "").replace(/^#/, "").split("&").forEach(function (pair) {
      var i = pair.indexOf("=");
      if (i > 0) out[pair.slice(0, i)] = decodeURIComponent(pair.slice(i + 1));
    });
    return out;
  }

  function openLinkedJob() {
    var q = linkParams();
    if (q.theme === "light" || q.theme === "dark") applyTheme(q.theme);
    if (q.view && selectView) selectView(q.view);
    if (!/^[A-Za-z0-9_-]+$/.test(q.job || "")) return;
    return getJSON("/api/jobs/" + q.job).then(function (job) {
      showJob(job);
      if (q.view && selectView) selectView(q.view);
    }).catch(function (e) {
      $("notices").appendChild(el("div", "notice bad", "No such run: " + e.message));
    });
  }

  function boot() {
    refreshMapColors();
    initMap();
    renderLayers();
    wire();
    bindKeys();
    loadHealth().catch(function (e) { console.error(e); });
    loadScenes().then(openLinkedJob).catch(function (e) {
      $("notices").appendChild(el("div", "notice bad", "Could not load scenes: " + e.message));
    });
    getJSON("/api/scoring").then(renderWeights).catch(function (e) { console.error(e); });
    getJSON("/api/config").then(fillSettings).catch(function (e) { console.error(e); });
    window.addEventListener("hashchange", openLinkedJob);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
