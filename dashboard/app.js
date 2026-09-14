const URL_PARAMS = new URLSearchParams(window.location.search);
const EMBEDDED = URL_PARAMS.get("embed") === "1";
const TYPE_PARAM = URL_PARAMS.get("type");
const VALID_TYPES = new Set(["all", "fast_charger", "swap_station"]);
// In Streamlit, the dashboard is served under /app/static/dashboard/, so use an
// absolute public-data URL. Standalone /dashboard/ still uses the relative path.
const DATA = EMBEDDED ? "/app/static/data/public" : "../data/public";
const KENYA_BOUNDS = [[-5.0, 33.5], [5.6, 42.2]];
const BASEMAP_STYLE = "https://tiles.openfreemap.org/styles/dark";
const FAST = "#4ade80";
const SWAP = "#fb923c";
const SELECTED = "#5eead4";

const state = {
  stationType: VALID_TYPES.has(TYPE_PARAM) ? TYPE_PARAM : "all",
  selectedCounty: null,
  selectedTown: null,
  counties: [],
  towns: [],
  stations: [],
  geojson: null,
  map: null,
  countyLayer: null,
  countyLabelLayer: null,
  stationLayer: null,
  countyRenderer: null,
  countyChart: null,
  townChart: null,
};

if (EMBEDDED) document.body.classList.add("is-embed");

function countOf(row) {
  if (state.stationType === "fast_charger") return row.fast_charger_count;
  if (state.stationType === "swap_station") return row.swap_station_count;
  return row.total_count;
}

function fillColor(count) {
  if (count <= 0) return "#1e293b";
  if (count === 1) return "#134e4a";
  if (count <= 4) return "#0f766e";
  if (count <= 10) return "#14b8a6";
  return "#5eead4";
}

function visibleStations() {
  return state.stations.filter((s) => state.stationType === "all" || s.station_type === state.stationType);
}

function sortedRows(rows) {
  return rows.filter((row) => countOf(row) > 0).sort((a, b) => countOf(b) - countOf(a));
}

function chartHeight(rowCount) {
  return Math.max(160, rowCount * 42);
}

function barColors(labels, baseColor, selectedLabel) {
  if (!selectedLabel) return labels.map(() => baseColor);
  return labels.map((label) => (label === selectedLabel ? baseColor : `${baseColor}66`));
}

function chartOptions(onBarClick) {
  return {
    indexAxis: "y",
    responsive: true,
    maintainAspectRatio: false,
    layout: { padding: { top: 4, right: 8, bottom: 4, left: 0 } },
    onClick(event, elements, chart) {
      if (!elements.length || !onBarClick) return;
      const index = elements[0].index;
      onBarClick(chart.data.labels[index]);
    },
    onHover(event, elements) {
      if (event.native?.target) {
        event.native.target.style.cursor = onBarClick && elements.length ? "pointer" : "default";
      }
    },
    plugins: {
      legend: { display: false },
    },
    scales: {
      x: {
        stacked: true,
        beginAtZero: true,
        grid: { color: "#1e293b" },
        ticks: { color: "#94a3b8", precision: 0, font: { size: 12 } },
      },
      y: {
        stacked: true,
        grid: { display: false },
        ticks: {
          autoSkip: false,
          color: "#f8fafc",
          font: { family: "IBM Plex Sans", size: 13, weight: "500" },
          padding: 6,
        },
      },
    },
  };
}

function stackValue(count) {
  return count > 0 ? count : null;
}

function buildDatasets(labels, rows, selectedLabel) {
  if (state.stationType === "all") {
    return [
      {
        label: "Fast charger",
        data: rows.map((r) => stackValue(r.fast_charger_count)),
        backgroundColor: barColors(labels, FAST, selectedLabel),
        borderRadius: 4,
        minBarLength: 6,
        categoryPercentage: 0.72,
        barPercentage: 0.9,
      },
      {
        label: "Swap station",
        data: rows.map((r) => stackValue(r.swap_station_count)),
        backgroundColor: barColors(labels, SWAP, selectedLabel),
        borderRadius: 4,
        minBarLength: 6,
        categoryPercentage: 0.72,
        barPercentage: 0.9,
      },
    ];
  }
  const color = state.stationType === "fast_charger" ? FAST : SWAP;
  return [{
    label: state.stationType === "fast_charger" ? "Fast charger" : "Swap station",
    data: rows.map(countOf),
    backgroundColor: barColors(labels, color, selectedLabel),
    borderRadius: 4,
    minBarLength: 6,
    categoryPercentage: 0.72,
    barPercentage: 0.9,
  }];
}

function drawBarChart({ canvasId, boxId, chartKey, rows, labelKey, selectedLabel, onBarClick }) {
  const labels = rows.map((row) => row[labelKey]);
  document.getElementById(boxId).style.height = `${chartHeight(rows.length)}px`;

  if (state[chartKey]) state[chartKey].destroy();
  state[chartKey] = new Chart(document.getElementById(canvasId), {
    type: "bar",
    data: { labels, datasets: buildDatasets(labels, rows, selectedLabel) },
    options: chartOptions(onBarClick),
  });
  requestAnimationFrame(() => state[chartKey]?.resize());
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function setHidden(id, hidden) {
  const el = document.getElementById(id);
  if (el) el.hidden = hidden;
}

function updateStats() {
  const visible = filteredStations();
  setText("stat-total", visible.length);
  setText("stat-fast", visible.filter((s) => s.station_type === "fast_charger").length);
  setText("stat-swap", visible.filter((s) => s.station_type === "swap_station").length);
  setText("stat-counties", new Set(visible.map((s) => s.county)).size);
}

function countyTooltipText(county, row) {
  if (state.stationType === "fast_charger") {
    return `<strong>${county}</strong><br>${row.fast_charger_count} fast chargers`;
  }
  if (state.stationType === "swap_station") {
    return `<strong>${county}</strong><br>${row.swap_station_count} swap stations`;
  }
  return (
    `<strong>${county}</strong><br>` +
    `${row.total_count} stations · ${row.fast_charger_count} fast · ${row.swap_station_count} swap`
  );
}

function countyStyle(row, selected) {
  // When a county is selected, mute other fills but keep borders readable.
  if (state.selectedCounty && !selected) {
    return {
      fillColor: "#070b14",
      fillOpacity: 0.72,
      color: "#64748b",
      weight: 1,
      opacity: 0.85,
    };
  }
  return {
    fillColor: fillColor(row ? countOf(row) : 0),
    fillOpacity: selected ? 1 : 0.92,
    color: selected ? SELECTED : "#64748b",
    weight: selected ? 2.5 : 1,
  };
}

function countyLabelIcon(name) {
  return L.divIcon({
    className: "county-label",
    html: `<span>${name}</span>`,
    iconSize: [0, 0],
    iconAnchor: [0, 0],
  });
}

function ringSignedArea(ring) {
  let area = 0;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    area += ring[j][0] * ring[i][1] - ring[i][0] * ring[j][1];
  }
  return area / 2;
}

function ringCentroid(ring) {
  let cx = 0;
  let cy = 0;
  let twiceArea = 0;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const cross = ring[j][0] * ring[i][1] - ring[i][0] * ring[j][1];
    twiceArea += cross;
    cx += (ring[j][0] + ring[i][0]) * cross;
    cy += (ring[j][1] + ring[i][1]) * cross;
  }
  if (Math.abs(twiceArea) < 1e-12) {
    const mid = ring[Math.floor(ring.length / 2)];
    return [mid[0], mid[1]];
  }
  return [cx / (3 * twiceArea), cy / (3 * twiceArea)];
}

function countyLabelPoint(geometry) {
  const polygons = geometry.type === "MultiPolygon"
    ? geometry.coordinates
    : [geometry.coordinates];
  let bestRing = polygons[0][0];
  let bestArea = 0;
  for (const polygon of polygons) {
    const ring = polygon[0];
    const area = Math.abs(ringSignedArea(ring));
    if (area > bestArea) {
      bestArea = area;
      bestRing = ring;
    }
  }
  const [lng, lat] = ringCentroid(bestRing);
  return L.latLng(lat, lng);
}

function drawCountyLabels() {
  if (state.countyLabelLayer) state.countyLabelLayer.remove();
  state.countyLabelLayer = L.layerGroup();
  for (const feature of state.geojson.features) {
    const name = feature.properties.county;
    L.marker(countyLabelPoint(feature.geometry), {
      icon: countyLabelIcon(name),
      interactive: false,
      keyboard: false,
    }).addTo(state.countyLabelLayer);
  }
  state.countyLabelLayer.addTo(state.map);
}

function drawCounties() {
  const byCounty = Object.fromEntries(state.counties.map((row) => [row.county, row]));
  if (!state.countyRenderer) state.countyRenderer = L.canvas({ padding: 0.5 });
  if (state.countyLayer) state.countyLayer.remove();
  state.countyLayer = L.geoJSON(state.geojson, {
    renderer: state.countyRenderer,
    style(feature) {
      const county = feature.properties.county;
      const row = byCounty[county];
      return countyStyle(row, state.selectedCounty === county);
    },
    onEachFeature(feature, layer) {
      const county = feature.properties.county;
      const row = byCounty[county] || {
        total_count: 0, fast_charger_count: 0, swap_station_count: 0,
      };
      layer.bindTooltip(countyTooltipText(county, row), { sticky: true });
      layer.on("click", () => selectCounty(county));
      layer.on("mouseover", () => {
        layer.setStyle({ weight: 2.5, color: "#f8fafc", opacity: 1 });
      });
      layer.on("mouseout", () => {
        if (state.selectedCounty === county) {
          layer.setStyle({ weight: 2.5, color: SELECTED, opacity: 1 });
          return;
        }
        state.countyLayer.resetStyle(layer);
      });
    },
  }).addTo(state.map);
  state.countyLayer.bringToFront();
  drawCountyLabels();
}

function stationPopup(station) {
  const isFast = station.station_type === "fast_charger";
  const mapsLink = station.maps_navigation_url
    ? `<a href="${station.maps_navigation_url}" target="_blank" rel="noopener noreferrer">Open in Google Maps</a>`
    : "";
  return (
    `<strong>${station.name}</strong>` +
    `<span>${isFast ? "Fast charger" : "Swap station"} · ${station.town}, ${station.county}<br>${station.hours}</span>` +
    mapsLink
  );
}

function filteredStations() {
  return visibleStations().filter((station) => {
    if (state.selectedTown) {
      return station.town === state.selectedTown && station.county === state.selectedCounty;
    }
    if (state.selectedCounty) return station.county === state.selectedCounty;
    return true;
  });
}

function fitToStationPoints(stations) {
  if (stations.length === 1) {
    state.map.setView([stations[0].final_latitude, stations[0].final_longitude], 13);
    return;
  }
  const bounds = L.latLngBounds(
    stations.map((station) => [station.final_latitude, station.final_longitude])
  );
  state.map.fitBounds(bounds.pad(0.35));
}

function countyFeatureBounds(county) {
  const feature = state.geojson.features.find((f) => f.properties.county === county);
  if (!feature) return null;
  return L.geoJSON(feature).getBounds();
}

function fitMapToSelection() {
  if (!state.map) return;
  if (state.selectedTown) {
    const stations = filteredStations();
    if (stations.length) {
      fitToStationPoints(stations);
      return;
    }
  }
  if (state.selectedCounty) {
    const bounds = countyFeatureBounds(state.selectedCounty);
    if (bounds) {
      // Edge-to-edge on the county box; muted neighbors hide the bleed.
      state.map.fitBounds(bounds, { animate: false, padding: [10, 10] });
      return;
    }
  }
  // Fit Kenya once the container has a real size (iframe/embed often starts at 0×0).
  state.map.fitBounds(KENYA_BOUNDS, { animate: false, padding: [28, 28] });
}

function syncMapViewport(refit = true) {
  if (!state.map) return;
  state.map.invalidateSize({ animate: false });
  if (refit) fitMapToSelection();
}

function drawStations() {
  if (state.stationLayer) state.stationLayer.remove();
  state.stationLayer = L.featureGroup();
  const visible = filteredStations();
  for (const station of visible) {
    const isFast = station.station_type === "fast_charger";
    L.circleMarker([station.final_latitude, station.final_longitude], {
      radius: 7,
      color: "#070b14",
      weight: 1,
      fillColor: isFast ? FAST : SWAP,
      fillOpacity: 1,
    })
      .bindPopup(stationPopup(station))
      .addTo(state.stationLayer);
  }
  state.stationLayer.addTo(state.map);
  state.stationLayer.bringToFront();
}

function townsForSelection() {
  if (!state.selectedCounty) return [];
  return sortedRows(state.towns.filter((row) => row.county === state.selectedCounty));
}

function updateCountyChartHeader() {
  setHidden("clear-county", !state.selectedCounty);
  setText(
    "county-chart-hint",
    state.selectedCounty ? `${state.selectedCounty} selected` : "Click a county to drill down",
  );
}

function updateTownChartHeader(townRows) {
  const block = document.getElementById("town-chart-block");
  if (block) block.hidden = !state.selectedCounty;
  setHidden("clear-town", !state.selectedTown);
  if (!state.selectedCounty) return;
  setText(
    "town-chart-title",
    state.selectedTown ? `Towns · ${state.selectedTown}` : `Towns · ${state.selectedCounty}`,
  );
  if (!townRows.length) {
    setText("town-chart-hint", "No stations for this filter");
    return;
  }
  setText(
    "town-chart-hint",
    state.selectedTown
      ? "Town selected — use Clear to zoom back out"
      : "Click a town to see its stations",
  );
}

function drawTownChart() {
  const rows = townsForSelection();
  updateTownChartHeader(rows);
  if (!state.selectedCounty) {
    if (state.townChart) {
      state.townChart.destroy();
      state.townChart = null;
    }
    return;
  }
  if (!rows.length) {
    if (state.townChart) {
      state.townChart.destroy();
      state.townChart = null;
    }
    document.getElementById("town-chart-box").style.height = "48px";
    return;
  }
  drawBarChart({
    canvasId: "town-bars",
    boxId: "town-chart-box",
    chartKey: "townChart",
    rows,
    labelKey: "town",
    selectedLabel: state.selectedTown,
    onBarClick: selectTown,
  });
}

function drawCountyChart() {
  updateCountyChartHeader();
  const rows = sortedRows(state.counties);
  drawBarChart({
    canvasId: "county-bars",
    boxId: "county-chart-box",
    chartKey: "countyChart",
    rows,
    labelKey: "county",
    selectedLabel: state.selectedCounty,
    onBarClick: selectCounty,
  });
}

function refreshSelection() {
  updateStats();
  drawCountyChart();
  drawTownChart();
  drawCounties();
  drawStations();
  fitMapToSelection();
}

function selectCounty(county) {
  if (state.selectedCounty === county) {
    state.selectedCounty = null;
    state.selectedTown = null;
  } else {
    state.selectedCounty = county;
    state.selectedTown = null;
  }
  refreshSelection();
}

function selectTown(town) {
  state.selectedTown = state.selectedTown === town ? null : town;
  refreshSelection();
}

function renderMapLayers() {
  drawCounties();
  drawStations();
}

function render() {
  updateStats();
  renderMapLayers();
  drawCountyChart();
  drawTownChart();
}

function syncTypeToggles() {
  document.querySelectorAll("[data-type]").forEach((button) => {
    button.classList.toggle("is-on", button.dataset.type === state.stationType);
  });
}

function bindToggles() {
  syncTypeToggles();
  document.querySelectorAll("[data-type]").forEach((button) => {
    button.addEventListener("click", () => {
      state.stationType = button.dataset.type;
      syncTypeToggles();
      if (state.selectedTown && !filteredStations().length) {
        state.selectedTown = null;
      }
      render();
      fitMapToSelection();
    });
  });
  document.getElementById("clear-county")?.addEventListener("click", () => {
    state.selectedCounty = null;
    state.selectedTown = null;
    refreshSelection();
  });
  document.getElementById("clear-town")?.addEventListener("click", () => {
    state.selectedTown = null;
    refreshSelection();
  });
}

async function main() {
  const [counties, towns, stations, geojson] = await Promise.all([
    fetch(`${DATA}/county_summary.json`).then((r) => r.json()),
    fetch(`${DATA}/town_summary.json`).then((r) => r.json()),
    fetch(`${DATA}/stations_with_county.json`).then((r) => r.json()),
    fetch(`${DATA}/kenya_counties_display.geojson`).then((r) => r.json()),
  ]);
  Object.assign(state, { counties, towns, stations, geojson });

  const mapEl = document.getElementById("map");
  state.map = L.map(mapEl, { zoomControl: true, attributionControl: true });
  // MapLibre basemap needs a real center/zoom during onAdd; fit Kenya first.
  state.map.fitBounds(KENYA_BOUNDS, { animate: false, padding: [28, 28] });
  const basemap = L.maplibreGL({ style: BASEMAP_STYLE }).addTo(state.map);
  state.map.attributionControl.setPrefix(false);
  state.map.attributionControl.addAttribution(
    '<a href="https://openfreemap.org/" target="_blank" rel="noopener noreferrer">OpenFreeMap</a> © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a>'
  );

  bindToggles();
  updateStats();
  drawCountyChart();
  drawTownChart();

  new ResizeObserver(() => {
    syncMapViewport(!state.selectedCounty && !state.selectedTown);
  }).observe(mapEl);

  const paintMapLayers = () => {
    renderMapLayers();
    syncMapViewport(true);
    requestAnimationFrame(() => syncMapViewport(true));
    setTimeout(() => syncMapViewport(true), 150);
  };

  const glMap = basemap.getMaplibreMap?.();
  if (glMap?.loaded?.()) {
    paintMapLayers();
  } else if (glMap?.once) {
    glMap.once("load", paintMapLayers);
  } else {
    // Basemap GL not ready yet (common in a zero-size iframe); still draw overlays.
    paintMapLayers();
    setTimeout(paintMapLayers, 200);
  }
}

main().catch((error) => {
  const hint = EMBEDDED
    ? "Streamlit embed failed while starting the map. Hard-refresh http://127.0.0.1:8501/ after restarting Streamlit from <code>app/</code>."
    : "Serve the repo root (not this folder):<br><code>python3 -m http.server 8000</code><br>then open <code>/dashboard/</code><br><br>Run <code>python scripts/run_pipeline.py</code> to build <code>data/public/</code>.";
  document.body.innerHTML = `<p style="padding:40px;font:20px/1.4 IBM Plex Sans,sans-serif;color:#f8fafc;background:#070b14">Could not load dashboard.<br><br>${hint}<br><br><code>${error}</code></p>`;
});
