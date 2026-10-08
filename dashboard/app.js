const URL_PARAMS = new URLSearchParams(window.location.search);
const TYPE_PARAM = URL_PARAMS.get("type");
const REGION_PARAM = URL_PARAMS.get("region");
const MANUFACTURER_PARAM = URL_PARAMS.get("manufacturer");
const MANUFACTURER_ALL = "all";
const PUBLIC_ROOT = "../data/public";
const BASEMAP_STYLE = "https://tiles.openfreemap.org/styles/dark";
const SELECTED = "#5eead4";
const BOUNDARIES_FILE = "kenya_counties_display.geojson";
const STATIONS_FILE = "stations_with_county.json";
const CANDIDATES_FILE = "tvs_candidates.json";

const TYPE_META = {
  fast_charger: { label: "Fast charger", short: "Fast", color: "#4ade80", css: "fast" },
  swap_station: { label: "Swap station", short: "Swap", color: "#fb923c", css: "swap" },
  standard_charger: { label: "Standard charger", short: "Standard", color: "#60a5fa", css: "standard" },
};

const app = {
  manifest: null,
  selectedRegionId: "kenya",
  stationType: "all",
  manufacturer: MANUFACTURER_ALL,
  showCandidates: false,
  regionData: new Map(),
  panels: [],
};

const retrievedLabels = new Map();

function titleCaseWords(value) {
  return String(value).replace(/_/g, " ").replace(/\b\w/g, (ch) => ch.toUpperCase());
}

function tierTitle(tier, { plural = false } = {}) {
  const irregular = {
    county: ["County", "Counties"],
    town: ["Town", "Towns"],
    district: ["District", "Districts"],
  };
  if (irregular[tier]) return irregular[tier][plural ? 1 : 0];
  const base = titleCaseWords(tier);
  return plural ? `${base}s` : base;
}

function typeMeta(stationType) {
  return TYPE_META[stationType] || {
    label: titleCaseWords(stationType),
    short: titleCaseWords(stationType),
    color: "#94a3b8",
    css: "muted",
  };
}

function selectedConfig() {
  return app.manifest.regions.find((region) => region.region_id === app.selectedRegionId)
    || app.manifest.regions[0];
}

function selectedConfigs() {
  const config = selectedConfig();
  return config ? [config] : [];
}

function unionStationTypes(configs) {
  const types = new Set();
  for (const config of configs) {
    for (const stationType of config.station_types) types.add(stationType);
  }
  return [...types].sort();
}

function datasetLabel(config) {
  return config.dataset_source_label || config.display_name || null;
}

function defaultRegionId(regions) {
  if (REGION_PARAM && regions.some((region) => region.region_id === REGION_PARAM)) {
    return REGION_PARAM;
  }
  if (regions.some((region) => region.region_id === "kenya")) return "kenya";
  return regions[0]?.region_id;
}

function countOf(row, stationType) {
  if (stationType === "all") return row.total_count || 0;
  return row[`${stationType}_count`] || 0;
}

function emptyCountRow(stationTypes) {
  const row = { total_count: 0 };
  for (const stationType of stationTypes) row[`${stationType}_count`] = 0;
  return row;
}

function fillColor(count) {
  if (count <= 0) return "#1e293b";
  if (count === 1) return "#134e4a";
  if (count <= 4) return "#0f766e";
  if (count <= 10) return "#14b8a6";
  return "#5eead4";
}

function sortedRows(rows, stationType) {
  return rows
    .filter((row) => countOf(row, stationType) > 0)
    .sort((a, b) => countOf(b, stationType) - countOf(a, stationType));
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
      onBarClick(elements[0].index);
    },
    onHover(event, elements) {
      if (event.native?.target) {
        event.native.target.style.cursor = onBarClick && elements.length ? "pointer" : "default";
      }
    },
    plugins: { legend: { display: false } },
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

function buildDatasets(ids, rows, selectedId, stationType, stationTypes) {
  if (stationType === "all") {
    return stationTypes.map((typeName) => {
      const meta = typeMeta(typeName);
      return {
        label: meta.label,
        data: rows.map((row) => stackValue(row[`${typeName}_count`] || 0)),
        backgroundColor: barColors(ids, meta.color, selectedId),
        borderRadius: 4,
        minBarLength: 6,
        categoryPercentage: 0.72,
        barPercentage: 0.9,
      };
    });
  }
  const meta = typeMeta(stationType);
  return [{
    label: meta.label,
    data: rows.map((row) => countOf(row, stationType)),
    backgroundColor: barColors(ids, meta.color, selectedId),
    borderRadius: 4,
    minBarLength: 6,
    categoryPercentage: 0.72,
    barPercentage: 0.9,
  }];
}

function drawBarChart({ canvas, box, chartRef, rows, labelKey, selectedLabel, onBarClick, stationType, stationTypes }) {
  const ids = rows.map((row) => row[labelKey]);
  const labels = rows.map((row) => unitDisplayName(row, labelKey));
  box.style.height = `${chartHeight(rows.length)}px`;
  if (chartRef.chart) chartRef.chart.destroy();
  chartRef.chart = new Chart(canvas, {
    type: "bar",
    data: {
      labels,
      datasets: buildDatasets(ids, rows, selectedLabel, stationType, stationTypes),
    },
    options: chartOptions((index) => onBarClick(ids[index])),
  });
  requestAnimationFrame(() => chartRef.chart?.resize());
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

function unitLabelPoint(geometry) {
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

function unitDisplayName(rowOrProps, tier, nativeName = null) {
  const native = nativeName ?? rowOrProps?.[tier];
  if (!rowOrProps) return native || "";
  return rowOrProps[`${tier}_en`] || native || "";
}

function bilingualHeading(english, chinese) {
  if (!chinese || chinese === english) return `<strong>${english}</strong>`;
  return `<strong>${english}</strong><span class="unit-native">${chinese}</span>`;
}

function unitLabelIcon(name) {
  return L.divIcon({
    className: "county-label",
    html: `<span>${name}</span>`,
    iconSize: [0, 0],
    iconAnchor: [0, 0],
  });
}

function jitteredPositions(stations) {
  const groups = new Map();
  for (const station of stations) {
    const key = `${station.final_latitude},${station.final_longitude}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(station);
  }
  const positions = new Map();
  for (const group of groups.values()) {
    if (group.length === 1) {
      positions.set(group[0], [group[0].final_latitude, group[0].final_longitude]);
      continue;
    }
    // Keep the ring small (~5m). Taipei districts are tight; the old
    // 0.00018*n formula pushed edge stacks ~160m outside the polygon.
    const radius = Math.min(0.00002 * Math.sqrt(group.length), 0.000045);
    for (let index = 0; index < group.length; index += 1) {
      const angle = (2 * Math.PI * index) / group.length;
      positions.set(group[index], [
        group[index].final_latitude + radius * Math.cos(angle),
        group[index].final_longitude + radius * Math.sin(angle),
      ]);
    }
  }
  return positions;
}

function lookupUnitProp(bundle, tier, nativeName) {
  if (!nativeName) return null;
  return bundle.primarySummary.find((row) => row[tier] === nativeName)
    || bundle.geojson.features.find((feature) => feature.properties[tier] === nativeName)?.properties
    || null;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch]));
}

function stationPopup(station, bundle) {
  const tiers = bundle.config.tiers;
  const primaryTier = tiers[0];
  const native = station[primaryTier];
  const props = lookupUnitProp(bundle, primaryTier, native);
  const english = unitDisplayName(props || station, primaryTier, native);
  const meta = typeMeta(station.station_type);
  const mapsLink = station.maps_navigation_url
    ? `<a href="${station.maps_navigation_url}" target="_blank" rel="noopener noreferrer">Open in Google Maps</a>`
    : "";
  const placeLine = english ? `${meta.label} · ${english}` : meta.label;
  const nativeLine = native && native !== english ? `<span class="unit-native">${native}</span>` : "";
  const hoursLine = station.hours ? `<span>${station.hours}</span>` : "";
  return `<strong>${station.name}</strong><span>${placeLine}</span>${nativeLine}${hoursLine}${mapsLink}`;
}

function candidatePopup(station, bundle) {
  const primaryTier = bundle.config.tiers[0];
  const native = station[primaryTier];
  const props = lookupUnitProp(bundle, primaryTier, native);
  const english = unitDisplayName(props || station, primaryTier, native);
  const locality = station.locality || station.name;
  const place = english && english !== locality ? `${english}` : "";
  const nativeLine = native && native !== english && native !== locality
    ? `<span class="unit-native">${escapeHtml(native)}</span>`
    : "";
  const mapsLink = station.maps_navigation_url
    ? `<a href="${station.maps_navigation_url}" target="_blank" rel="noopener noreferrer">Open in Google Maps</a>`
    : "";
  const placeLine = place ? `<span>${escapeHtml(place)}</span>` : "";
  return `<strong>${escapeHtml(locality)}</strong><span>TVS candidate</span>${placeLine}${nativeLine}${mapsLink}`;
}

function unitTooltipText(english, chinese, row, stationType, stationTypes) {
  const heading = bilingualHeading(english, chinese);
  if (stationType !== "all") {
    const meta = typeMeta(stationType);
    return `${heading}<br>${countOf(row, stationType)} ${meta.label.toLowerCase()}s`;
  }
  const parts = stationTypes
    .map((typeName) => `${row[`${typeName}_count`] || 0} ${typeMeta(typeName).short.toLowerCase()}`)
    .join(" · ");
  return `${heading}<br>${row.total_count || 0} stations · ${parts}`;
}

function unitStyle(row, selected, hasSelection, stationType) {
  if (hasSelection && !selected) {
    return {
      fillColor: "#070b14",
      fillOpacity: 0.72,
      color: "#64748b",
      weight: 1,
      opacity: 0.85,
    };
  }
  return {
    fillColor: fillColor(row ? countOf(row, stationType) : 0),
    fillOpacity: selected ? 1 : 0.92,
    color: selected ? SELECTED : "#64748b",
    weight: selected ? 2.5 : 1,
  };
}

function statsHtml(stationTypes, tierPlural) {
  const typeStats = stationTypes.map((typeName) => {
    const meta = typeMeta(typeName);
    return (
      `<article class="stat stat-${meta.css}">` +
      `<p class="stat-label">${meta.short}</p>` +
      `<p class="stat-value" data-stat="${typeName}">—</p>` +
      `</article>`
    );
  }).join("");
  return (
    `<article class="stat">` +
    `<p class="stat-label">Stations</p>` +
    `<p class="stat-value" data-stat="total">—</p>` +
    `</article>` +
    typeStats +
    `<article class="stat">` +
    `<p class="stat-label">${tierPlural}</p>` +
    `<p class="stat-value" data-stat="units">—</p>` +
    `</article>`
  );
}

function fillStats(root, stations, primaryTier, stationTypes) {
  if (!root) return;
  root.querySelector('[data-stat="total"]').textContent = String(stations.length);
  for (const typeName of stationTypes) {
    const el = root.querySelector(`[data-stat="${typeName}"]`);
    if (el) el.textContent = String(stations.filter((s) => s.station_type === typeName).length);
  }
  const units = new Set(
    stations.map((station) => `${station.region_id}:${station[primaryTier]}`).filter((key) => !key.endsWith(":undefined") && !key.endsWith(":null") && !key.endsWith(":"))
  );
  root.querySelector('[data-stat="units"]').textContent = String(units.size);
}

function legendHtml(stationTypes) {
  const typeItems = stationTypes.map((typeName) => {
    const meta = typeMeta(typeName);
    return `<li><span class="swatch swatch-${meta.css}"></span> ${meta.label}</li>`;
  }).join("");
  return `<li><span class="swatch swatch-zero"></span> No stations</li>${typeItems}`;
}

async function loadRegionBundle(config) {
  const base = `${PUBLIC_ROOT}/${config.data_path}`;
  const primaryTier = config.tiers[0];
  const secondaryTier = config.tiers[1];
  const fetchJson = (url) => fetch(url, { cache: "no-store" }).then((response) => {
    if (!response.ok) throw new Error(`${url} HTTP ${response.status}`);
    return response.json();
  });
  const requests = [
    fetchJson(`${base}/${primaryTier}_summary.json`),
    fetchJson(`${base}/${STATIONS_FILE}`),
    fetchJson(`${base}/${BOUNDARIES_FILE}`),
  ];
  if (secondaryTier) {
    requests.push(fetchJson(`${base}/${secondaryTier}_summary.json`));
  }
  const candidateRequest = config.region_id === "taipei"
    ? fetch(`${base}/${CANDIDATES_FILE}`, { cache: "no-store" }).then((response) => {
      if (response.status === 404) return [];
      if (!response.ok) throw new Error(`${CANDIDATES_FILE} HTTP ${response.status}`);
      return response.json();
    })
    : Promise.resolve([]);
  const loaded = await Promise.all(requests);
  const primarySummary = loaded[0];
  const stations = loaded[1];
  const geojson = loaded[2];
  const secondarySummary = secondaryTier ? loaded[3] : [];
  const candidates = await candidateRequest;
  return {
    config,
    primarySummary: primarySummary.map((row) => ({ ...row, region_id: config.region_id })),
    secondarySummary: secondarySummary.map((row) => ({ ...row, region_id: config.region_id })),
    stations: stations.map((station) => ({ ...station, region_id: config.region_id })),
    candidates: candidates.map((station) => ({ ...station, region_id: config.region_id })),
    geojson: {
      type: "FeatureCollection",
      features: geojson.features.map((feature) => ({
        ...feature,
        properties: { ...feature.properties, region_id: config.region_id },
      })),
    },
  };
}

function destroyPanels() {
  for (const panel of app.panels) {
    for (const chart of Object.values(panel.charts)) {
      chart.chart?.destroy();
    }
    panel.map?.remove();
    panel.resizeObserver?.disconnect();
  }
  app.panels = [];
  document.getElementById("workspace").innerHTML = "";
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function matchesManufacturer(station) {
  if (!app.manufacturer || app.manufacturer === MANUFACTURER_ALL) return true;
  return station.operator === app.manufacturer;
}

function operatorsForSelection() {
  const config = selectedConfig();
  const bundle = config && app.regionData.get(config.region_id);
  if (!bundle) return [];
  const names = new Set();
  for (const station of bundle.stations) {
    if (app.stationType !== "all" && station.station_type !== app.stationType) continue;
    const operator = typeof station.operator === "string" ? station.operator.trim() : "";
    if (operator) names.add(operator);
  }
  return [...names].sort((a, b) => a.localeCompare(b));
}

function applyManufacturerChoice(value) {
  const operators = operatorsForSelection();
  if (operators.length < 2 || !value || value === MANUFACTURER_ALL || !operators.includes(value)) {
    app.manufacturer = MANUFACTURER_ALL;
    return;
  }
  app.manufacturer = value;
}

function summaryFromStations(bundle, stations, tiers) {
  const stationTypes = bundle.config.station_types;
  const byKey = new Map();
  const sourceRows = tiers.length === 1 ? bundle.primarySummary : bundle.secondarySummary;
  for (const row of sourceRows) {
    const fresh = emptyCountRow(stationTypes);
    for (const tier of tiers) fresh[tier] = row[tier];
    const enKey = `${tiers[0]}_en`;
    if (row[enKey]) fresh[enKey] = row[enKey];
    fresh.region_id = row.region_id;
    byKey.set(tiers.map((tier) => row[tier]).join(":"), fresh);
  }
  for (const station of stations) {
    if (!matchesManufacturer(station)) continue;
    const row = byKey.get(tiers.map((tier) => station[tier]).join(":"));
    if (!row) continue;
    row.total_count += 1;
    const column = `${station.station_type}_count`;
    if (Object.prototype.hasOwnProperty.call(row, column)) row[column] += 1;
  }
  return [...byKey.values()];
}

function filteredPrimarySummary(bundle) {
  if (!app.manufacturer || app.manufacturer === MANUFACTURER_ALL) return bundle.primarySummary;
  return summaryFromStations(bundle, bundle.stations, [bundle.config.tiers[0]]);
}

function filteredSecondarySummary(bundle) {
  if (!app.manufacturer || app.manufacturer === MANUFACTURER_ALL) return bundle.secondarySummary;
  const secondaryTier = bundle.config.tiers[1];
  if (!secondaryTier) return [];
  return summaryFromStations(bundle, bundle.stations, [bundle.config.tiers[0], secondaryTier]);
}

function filteredStations(panel) {
  const stationType = app.stationType;
  let stations = panel.bundles.flatMap((bundle) => bundle.stations);
  stations = stations.filter((station) => stationType === "all" || station.station_type === stationType);
  stations = stations.filter((station) => matchesManufacturer(station));
  if (panel.selectedSecondary) {
    const { regionId, primary, secondary, secondaryTier } = panel.selectedSecondary;
    const primaryTier = panel.bundles.find((bundle) => bundle.config.region_id === regionId).config.tiers[0];
    return stations.filter(
      (station) =>
        station.region_id === regionId &&
        station[primaryTier] === primary &&
        station[secondaryTier] === secondary
    );
  }
  if (panel.selectedPrimary) {
    const { regionId, primary, primaryTier } = panel.selectedPrimary;
    return stations.filter(
      (station) => station.region_id === regionId && station[primaryTier] === primary
    );
  }
  return stations;
}

function unionBounds(bundles) {
  let bounds = null;
  for (const bundle of bundles) {
    const [[south, west], [north, east]] = bundle.config.bounds;
    const next = L.latLngBounds([south, west], [north, east]);
    bounds = bounds ? bounds.extend(next) : next;
  }
  return bounds;
}

function featureBounds(panel, regionId, primaryTier, primaryName) {
  for (const bundle of panel.bundles) {
    if (bundle.config.region_id !== regionId) continue;
    const feature = bundle.geojson.features.find(
      (item) => item.properties[primaryTier] === primaryName
    );
    if (feature) return L.geoJSON(feature).getBounds();
  }
  return null;
}

function fitToStationPoints(map, stations) {
  if (stations.length === 1) {
    map.setView([stations[0].final_latitude, stations[0].final_longitude], 13);
    return;
  }
  const bounds = L.latLngBounds(
    stations.map((station) => [station.final_latitude, station.final_longitude])
  );
  map.fitBounds(bounds.pad(0.35));
}

function haversineKm(origin, target) {
  const toRad = (degrees) => degrees * Math.PI / 180;
  const [lat1, lng1] = origin;
  const [lat2, lng2] = target;
  const dLat = toRad(lat2 - lat1);
  const dLng = toRad(lng2 - lng1);
  const h = Math.sin(dLat / 2) ** 2
    + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLng / 2) ** 2;
  return 6371 * 2 * Math.asin(Math.sqrt(h));
}

function candidatePoint(station) {
  return [station.final_latitude, station.final_longitude];
}

function candidatePinName(station) {
  return station.locality || station.name || "Candidate";
}

function formatKm(km) {
  return `${km.toFixed(1)} km`;
}

function spacingRows(panel) {
  const pool = panel.bundles.flatMap((bundle) => bundle.candidates || []);
  const rows = [];
  for (const candidate of filteredCandidates(panel)) {
    let nearest = null;
    let distanceKm = Infinity;
    const origin = candidatePoint(candidate);
    for (const other of pool) {
      if (other.station_id === candidate.station_id) continue;
      const distance = haversineKm(origin, candidatePoint(other));
      if (distance < distanceKm) {
        distanceKm = distance;
        nearest = other;
      }
    }
    if (nearest) rows.push({ candidate, nearest, distanceKm });
  }
  rows.sort((a, b) => a.distanceKm - b.distanceKm || candidatePinName(a.candidate).localeCompare(candidatePinName(b.candidate)));
  return rows;
}

function focusedSpacing(panel) {
  if (!panel.focusedCandidateId || !app.showCandidates) return null;
  return spacingRows(panel).find((row) => row.candidate.station_id === panel.focusedCandidateId) || null;
}

function fitMapToSelection(panel) {
  if (!panel.map) return;
  const focused = focusedSpacing(panel);
  if (focused) {
    const bounds = L.latLngBounds([
      candidatePoint(focused.candidate),
      candidatePoint(focused.nearest),
    ]);
    panel.map.fitBounds(bounds, { animate: false, padding: [56, 56], maxZoom: 14 });
    return;
  }
  if (panel.selectedSecondary) {
    const stations = filteredStations(panel);
    if (stations.length) {
      fitToStationPoints(panel.map, stations);
      return;
    }
  }
  if (panel.selectedPrimary) {
    const { regionId, primary, primaryTier } = panel.selectedPrimary;
    const bounds = featureBounds(panel, regionId, primaryTier, primary);
    if (bounds) {
      panel.map.fitBounds(bounds, { animate: false, padding: [10, 10] });
      return;
    }
  }
  const bounds = unionBounds(panel.bundles);
  if (bounds) panel.map.fitBounds(bounds, { animate: false, padding: [28, 28] });
}

function syncMapViewport(panel, refit = true) {
  if (!panel.map) return;
  panel.map.invalidateSize({ animate: false });
  if (refit) fitMapToSelection(panel);
}

function drawBoundaries(panel) {
  const stationType = app.stationType;
  const byKey = new Map();
  for (const bundle of panel.bundles) {
    const primaryTier = bundle.config.tiers[0];
    for (const row of filteredPrimarySummary(bundle)) {
      byKey.set(`${bundle.config.region_id}:${row[primaryTier]}`, { row, primaryTier, bundle });
    }
  }
  if (!panel.boundaryRenderer) panel.boundaryRenderer = L.canvas({ padding: 0.5 });
  if (panel.boundaryLayer) panel.boundaryLayer.remove();
  if (panel.labelLayer) panel.labelLayer.remove();
  panel.labelLayer = L.layerGroup();

  const merged = {
    type: "FeatureCollection",
    features: panel.bundles.flatMap((bundle) => bundle.geojson.features),
  };

  panel.boundaryLayer = L.geoJSON(merged, {
    renderer: panel.boundaryRenderer,
    style(feature) {
      const regionId = feature.properties.region_id;
      const bundle = panel.bundles.find((item) => item.config.region_id === regionId);
      const primaryTier = bundle.config.tiers[0];
      const name = feature.properties[primaryTier];
      const entry = byKey.get(`${regionId}:${name}`);
      const selected =
        panel.selectedPrimary?.regionId === regionId &&
        panel.selectedPrimary?.primary === name;
      return unitStyle(entry?.row, selected, Boolean(panel.selectedPrimary), stationType);
    },
    onEachFeature(feature, layer) {
      const regionId = feature.properties.region_id;
      const bundle = panel.bundles.find((item) => item.config.region_id === regionId);
      const primaryTier = bundle.config.tiers[0];
      const stationTypes = bundle.config.station_types;
      const name = feature.properties[primaryTier];
      const entry = byKey.get(`${regionId}:${name}`);
      const row = entry?.row || emptyCountRow(stationTypes);
      const english = unitDisplayName(entry?.row || feature.properties, primaryTier, name);
      layer.bindTooltip(
        unitTooltipText(english, name, row, stationType, stationTypes),
        { sticky: true, className: "unit-tooltip" },
      );
      layer.on("click", () => selectPrimary(panel, regionId, primaryTier, name));
      layer.on("mouseover", () => {
        layer.setStyle({ weight: 2.5, color: "#f8fafc", opacity: 1 });
      });
      layer.on("mouseout", () => {
        if (panel.selectedPrimary?.regionId === regionId && panel.selectedPrimary?.primary === name) {
          layer.setStyle({ weight: 2.5, color: SELECTED, opacity: 1 });
          return;
        }
        panel.boundaryLayer.resetStyle(layer);
      });
      L.marker(unitLabelPoint(feature.geometry), {
        icon: unitLabelIcon(english),
        interactive: false,
        keyboard: false,
      }).addTo(panel.labelLayer);
    },
  }).addTo(panel.map);
  panel.boundaryLayer.bringToFront();
  panel.labelLayer.addTo(panel.map);
}

function drawStations(panel) {
  if (panel.stationLayer) panel.stationLayer.remove();
  panel.stationLayer = L.featureGroup();
  const visible = filteredStations(panel);
  const positions = jitteredPositions(visible);
  for (const station of visible) {
    const bundle = panel.bundles.find((item) => item.config.region_id === station.region_id);
    const meta = typeMeta(station.station_type);
    const latLng = positions.get(station);
    L.circleMarker(latLng, {
      radius: 7,
      color: "#070b14",
      weight: 1,
      fillColor: meta.color,
      fillOpacity: 1,
    })
      .bindPopup(stationPopup(station, bundle))
      .addTo(panel.stationLayer);
  }
  panel.stationLayer.addTo(panel.map);
  panel.stationLayer.bringToFront();
}

function filteredCandidates(panel) {
  if (!app.showCandidates) return [];
  if (app.stationType !== "all" && app.stationType !== "swap_station") return [];
  let rows = panel.bundles.flatMap((bundle) => bundle.candidates || []);
  if (panel.selectedPrimary) {
    const { regionId, primary, primaryTier } = panel.selectedPrimary;
    rows = rows.filter((row) => row.region_id === regionId && row[primaryTier] === primary);
  }
  return rows;
}

function candidatesToDraw(panel) {
  const visible = filteredCandidates(panel);
  const focused = focusedSpacing(panel);
  if (!focused) return visible;
  if (visible.some((station) => station.station_id === focused.nearest.station_id)) return visible;
  return [...visible, focused.nearest];
}

function spacingPair(panel) {
  const focused = focusedSpacing(panel);
  if (!focused) return null;
  return new Set([focused.candidate.station_id, focused.nearest.station_id]);
}

function drawCandidates(panel) {
  if (panel.candidateLayer) panel.candidateLayer.remove();
  panel.candidateLayer = L.featureGroup();
  const positions = jitteredPositions(candidatesToDraw(panel));
  const pair = spacingPair(panel);
  for (const station of positions.keys()) {
    const bundle = panel.bundles.find((item) => item.config.region_id === station.region_id);
    const inPair = pair?.has(station.station_id);
    L.circleMarker(positions.get(station), {
      radius: inPair ? 11 : 8,
      color: inPair ? "#f8fafc" : "#fee2e2",
      weight: inPair ? 3 : 2,
      fillColor: "#ef4444",
      fillOpacity: pair && !inPair ? 0.35 : 1,
    })
      .bindPopup(candidatePopup(station, bundle))
      .addTo(panel.candidateLayer);
  }
  panel.candidateLayer.addTo(panel.map);
  panel.candidateLayer.bringToFront();
}

function drawSpacingLink(panel) {
  if (panel.spacingLinkLayer) panel.spacingLinkLayer.remove();
  panel.spacingLinkLayer = null;
  const focused = focusedSpacing(panel);
  if (!focused || !panel.map) return;
  const positions = jitteredPositions(candidatesToDraw(panel));
  const start = positions.get(focused.candidate);
  const end = positions.get(focused.nearest);
  if (!start || !end) return;
  const line = L.polyline([start, end], {
    color: "#f8fafc",
    weight: 3,
    opacity: 1,
    dashArray: "7 7",
    interactive: false,
  });
  panel.spacingLinkLayer = L.layerGroup([
    L.polyline([start, end], {
      color: "#070b14",
      weight: 7,
      opacity: 0.85,
      interactive: false,
    }),
    line,
  ]).addTo(panel.map);
  if (panel.candidateLayer) panel.candidateLayer.bringToFront();
}

function refreshLegend(panel) {
  const legend = panel.root.querySelector(".legend");
  if (!legend) return;
  const stationTypes = unionStationTypes(panel.bundles.map((bundle) => bundle.config));
  const typeAllows = app.stationType === "all" || app.stationType === "swap_station";
  const showCandidates = typeAllows && app.showCandidates
    && panel.bundles.some((bundle) => bundle.candidates?.length);
  const candidateItem = showCandidates
    ? `<li><span class="swatch swatch-candidate"></span> TVS candidate</li>`
    : "";
  legend.innerHTML = legendHtml(stationTypes) + candidateItem;
}

function secondaryRowsFor(panel, bundle) {
  if (!panel.selectedPrimary) return [];
  if (panel.selectedPrimary.regionId !== bundle.config.region_id) return [];
  const primaryTier = bundle.config.tiers[0];
  const secondaryTier = bundle.config.tiers[1];
  if (!secondaryTier) return [];
  return sortedRows(
    filteredSecondarySummary(bundle).filter(
      (row) => row[primaryTier] === panel.selectedPrimary.primary
    ),
    app.stationType
  );
}

function updateChartHeaders(panel, bundle, secondaryRows) {
  const regionId = bundle.config.region_id;
  const primaryTier = bundle.config.tiers[0];
  const secondaryTier = bundle.config.tiers[1];
  const clearPrimary = panel.root.querySelector(`[data-clear-primary="${regionId}"]`);
  const primaryHint = panel.root.querySelector(`[data-primary-hint="${regionId}"]`);
  const selectedHere =
    panel.selectedPrimary?.regionId === regionId ? panel.selectedPrimary.primary : null;

  if (clearPrimary) clearPrimary.hidden = !selectedHere;
  if (primaryHint) {
    const selectedRow = selectedHere
      ? bundle.primarySummary.find((row) => row[primaryTier] === selectedHere)
      : null;
    const selectedLabel = selectedHere
      ? unitDisplayName(selectedRow, primaryTier, selectedHere)
      : null;
    primaryHint.textContent = selectedLabel
      ? `${selectedLabel} selected`
      : `Click a ${primaryTier} to drill down`;
  }

  if (!secondaryTier) return;
  const secondaryBlock = panel.root.querySelector(`[data-secondary-block="${regionId}"]`);
  const clearSecondary = panel.root.querySelector(`[data-clear-secondary="${regionId}"]`);
  const secondaryTitle = panel.root.querySelector(`[data-secondary-title="${regionId}"]`);
  const secondaryHint = panel.root.querySelector(`[data-secondary-hint="${regionId}"]`);
  if (secondaryBlock) secondaryBlock.hidden = !selectedHere;
  if (clearSecondary) clearSecondary.hidden = !panel.selectedSecondary || panel.selectedSecondary.regionId !== regionId;
  if (!selectedHere) return;
  const selectedTown =
    panel.selectedSecondary?.regionId === regionId ? panel.selectedSecondary.secondary : null;
  const selectedPrimaryLabel = unitDisplayName(
    bundle.primarySummary.find((row) => row[primaryTier] === selectedHere),
    primaryTier,
    selectedHere,
  );
  if (secondaryTitle) {
    secondaryTitle.textContent = selectedTown
      ? `${tierTitle(secondaryTier, { plural: true })} · ${selectedTown}`
      : `${tierTitle(secondaryTier, { plural: true })} · ${selectedPrimaryLabel}`;
  }
  if (!secondaryRows.length) {
    if (secondaryHint) secondaryHint.textContent = "No stations for this filter";
    return;
  }
  if (secondaryHint) {
    secondaryHint.textContent = selectedTown
      ? `${tierTitle(secondaryTier)} selected — use Clear to zoom back out`
      : `Click a ${secondaryTier} to see its stations`;
  }
}

function drawRegionCharts(panel, bundle) {
  const regionId = bundle.config.region_id;
  const primaryTier = bundle.config.tiers[0];
  const secondaryTier = bundle.config.tiers[1];
  const stationTypes = bundle.config.station_types;
  const selectedPrimary =
    panel.selectedPrimary?.regionId === regionId ? panel.selectedPrimary.primary : null;
  const primaryRows = sortedRows(filteredPrimarySummary(bundle), app.stationType);
  const secondaryRows = secondaryRowsFor(panel, bundle);
  updateChartHeaders(panel, bundle, secondaryRows);

  const primaryCanvas = panel.root.querySelector(`[data-primary-canvas="${regionId}"]`);
  const primaryBox = panel.root.querySelector(`[data-primary-box="${regionId}"]`);
  const primaryRef = panel.charts[`primary:${regionId}`];
  const primaryHint = panel.root.querySelector(`[data-primary-hint="${regionId}"]`);
  if (!primaryRows.length) {
    primaryRef.chart?.destroy();
    primaryRef.chart = null;
    if (primaryBox) primaryBox.style.height = "48px";
    if (primaryHint) primaryHint.textContent = "No stations for this filter";
    const secondaryRef = panel.charts[`secondary:${regionId}`];
    secondaryRef?.chart?.destroy();
    if (secondaryRef) secondaryRef.chart = null;
    return;
  }
  drawBarChart({
    canvas: primaryCanvas,
    box: primaryBox,
    chartRef: primaryRef,
    rows: primaryRows,
    labelKey: primaryTier,
    selectedLabel: selectedPrimary,
    onBarClick: (name) => selectPrimary(panel, regionId, primaryTier, name),
    stationType: app.stationType,
    stationTypes,
  });

  if (!secondaryTier) return;
  const secondaryRef = panel.charts[`secondary:${regionId}`];
  const secondaryCanvas = panel.root.querySelector(`[data-secondary-canvas="${regionId}"]`);
  const secondaryBox = panel.root.querySelector(`[data-secondary-box="${regionId}"]`);
  if (!selectedPrimary) {
    secondaryRef.chart?.destroy();
    secondaryRef.chart = null;
    return;
  }
  if (!secondaryRows.length) {
    secondaryRef.chart?.destroy();
    secondaryRef.chart = null;
    secondaryBox.style.height = "48px";
    return;
  }
  const selectedSecondary =
    panel.selectedSecondary?.regionId === regionId ? panel.selectedSecondary.secondary : null;
  drawBarChart({
    canvas: secondaryCanvas,
    box: secondaryBox,
    chartRef: secondaryRef,
    rows: secondaryRows,
    labelKey: secondaryTier,
    selectedLabel: selectedSecondary,
    onBarClick: (name) => selectSecondary(panel, regionId, primaryTier, selectedPrimary, secondaryTier, name),
    stationType: app.stationType,
    stationTypes,
  });
}

function renderCandidateSpacing(panel) {
  const block = panel.root.querySelector("[data-candidate-spacing]");
  if (!block) return;
  const rows = spacingRows(panel);
  const show = rows.length > 0;
  block.hidden = !show;
  if (!show) {
    panel.focusedCandidateId = null;
    return;
  }
  if (panel.focusedCandidateId && !rows.some((row) => row.candidate.station_id === panel.focusedCandidateId)) {
    panel.focusedCandidateId = null;
  }
  const hint = block.querySelector("[data-spacing-hint]");
  if (hint) hint.textContent = `Closest pair ${formatKm(rows[0].distanceKm)}`;
  const list = block.querySelector("[data-spacing-list]");
  list.innerHTML = rows.map((row) => {
    const on = row.candidate.station_id === panel.focusedCandidateId;
    return (
      `<li>` +
      `<button type="button" class="spacing-row${on ? " is-on" : ""}" data-spacing-id="${escapeHtml(row.candidate.station_id)}" aria-pressed="${on}">` +
      `<span class="spacing-name">${escapeHtml(candidatePinName(row.candidate))}</span>` +
      `<span class="spacing-km">${formatKm(row.distanceKm)}</span>` +
      `<span class="spacing-near">from ${escapeHtml(candidatePinName(row.nearest))}</span>` +
      `</button></li>`
    );
  }).join("");
  list.querySelectorAll("[data-spacing-id]").forEach((button) => {
    button.addEventListener("click", () => {
      const id = button.dataset.spacingId;
      panel.focusedCandidateId = panel.focusedCandidateId === id ? null : id;
      refreshPanel(panel);
    });
  });
}

function refreshPanel(panel) {
  const stationTypes = unionStationTypes(panel.bundles.map((bundle) => bundle.config));
  const primaryTier = panel.bundles[0].config.tiers[0];
  const visible = filteredStations(panel);
  if (panel.localStats) fillStats(panel.localStats, visible, primaryTier, stationTypes);
  for (const bundle of panel.bundles) drawRegionCharts(panel, bundle);
  drawBoundaries(panel);
  drawStations(panel);
  drawCandidates(panel);
  drawSpacingLink(panel);
  refreshLegend(panel);
  renderCandidateSpacing(panel);
  fitMapToSelection(panel);
}

function selectPrimary(panel, regionId, primaryTier, name) {
  panel.focusedCandidateId = null;
  if (panel.selectedPrimary?.regionId === regionId && panel.selectedPrimary?.primary === name) {
    panel.selectedPrimary = null;
    panel.selectedSecondary = null;
  } else {
    panel.selectedPrimary = { regionId, primary: name, primaryTier };
    panel.selectedSecondary = null;
  }
  refreshPanel(panel);
  updateGlobalStats();
}

function selectSecondary(panel, regionId, primaryTier, primary, secondaryTier, name) {
  panel.focusedCandidateId = null;
  if (panel.selectedSecondary?.regionId === regionId && panel.selectedSecondary?.secondary === name) {
    panel.selectedSecondary = null;
  } else {
    panel.selectedSecondary = { regionId, primary, secondary: name, secondaryTier };
    panel.selectedPrimary = { regionId, primary, primaryTier };
  }
  refreshPanel(panel);
  updateGlobalStats();
}

function bindPanelControls(panel) {
  for (const bundle of panel.bundles) {
    const regionId = bundle.config.region_id;
    panel.root.querySelector(`[data-clear-primary="${regionId}"]`)?.addEventListener("click", () => {
      if (panel.selectedPrimary?.regionId === regionId) {
        panel.selectedPrimary = null;
        panel.selectedSecondary = null;
        refreshPanel(panel);
        updateGlobalStats();
      }
    });
    panel.root.querySelector(`[data-clear-secondary="${regionId}"]`)?.addEventListener("click", () => {
      if (panel.selectedSecondary?.regionId === regionId) {
        panel.selectedSecondary = null;
        refreshPanel(panel);
        updateGlobalStats();
      }
    });
  }
}

function regionChartsHtml(bundle, { showRegionTitle }) {
  const regionId = bundle.config.region_id;
  const primaryTier = bundle.config.tiers[0];
  const secondaryTier = bundle.config.tiers[1];
  const title = showRegionTitle
    ? `<h2 class="region-charts-title">${bundle.config.display_name}</h2>`
    : "";
  const secondary = secondaryTier
    ? (
      `<section class="chart-block" data-secondary-block="${regionId}" aria-label="Stations by ${secondaryTier}" hidden>` +
      `<div class="chart-block-head">` +
      `<h2 data-secondary-title="${regionId}">${tierTitle(secondaryTier, { plural: true })}</h2>` +
      `<p class="chart-hint" data-secondary-hint="${regionId}"></p>` +
      `<button type="button" class="chart-clear" data-clear-secondary="${regionId}" hidden>Clear</button>` +
      `</div>` +
      `<div class="chart-scroll"><div data-secondary-box="${regionId}"><canvas data-secondary-canvas="${regionId}"></canvas></div></div>` +
      `</section>`
    )
    : "";
  return (
    `<div class="region-charts" data-region-charts="${regionId}">` +
    title +
    `<section class="chart-block" aria-label="Stations by ${primaryTier}">` +
    `<div class="chart-block-head">` +
    `<h2>${tierTitle(primaryTier, { plural: true })}</h2>` +
    `<p class="chart-hint" data-primary-hint="${regionId}">Click a ${primaryTier} to drill down</p>` +
    `<button type="button" class="chart-clear" data-clear-primary="${regionId}" hidden>Clear</button>` +
    `</div>` +
    `<div class="chart-scroll"><div data-primary-box="${regionId}"><canvas data-primary-canvas="${regionId}"></canvas></div></div>` +
    `</section>` +
    secondary +
    `</div>`
  );
}

function buildPanelElement(panelSpecs, { showLocalStats, showRegionTitles }) {
  const configs = panelSpecs.regions;
  const stationTypes = unionStationTypes(configs);
  const primaryTier = configs[0].tiers[0];
  const titleNames = configs.map((config) => config.display_name).join(" · ");
  const localStats = showLocalStats
    ? `<section class="panel-stats stats" aria-label="${titleNames} totals">${statsHtml(stationTypes, tierTitle(primaryTier, { plural: true }))}</section>`
    : "";
  const charts = configs.map((config) => {
    const bundle = app.regionData.get(config.region_id);
    return regionChartsHtml(bundle, { showRegionTitle: showRegionTitles });
  }).join("");
  const section = document.createElement("section");
  section.className = "region-panel";
  section.dataset.cluster = panelSpecs.cluster;
  section.innerHTML =
    `${showLocalStats ? `<header class="panel-head"><h2 class="panel-title">${titleNames}</h2>${localStats}</header>` : ""}` +
    `<main class="stage">` +
    `<div class="map" aria-label="${titleNames} station map"></div>` +
    `<aside class="side-panel">` +
    `<div class="charts-stack">` +
    `<section class="spacing-block" data-candidate-spacing hidden>` +
    `<div class="chart-block-head">` +
    `<h2>Candidate spacing</h2>` +
    `<p class="chart-hint" data-spacing-hint></p>` +
    `</div>` +
    `<div class="spacing-scroll"><ul class="spacing-list" data-spacing-list></ul></div>` +
    `</section>` +
    `${charts}</div>` +
    `<ul class="legend">${legendHtml(stationTypes)}</ul>` +
    `</aside>` +
    `</main>`;
  return section;
}

function mountPanel(panelSpecs, options) {
  const workspace = document.getElementById("workspace");
  const root = buildPanelElement(panelSpecs, options);
  workspace.appendChild(root);
  const bundles = panelSpecs.regions.map((config) => app.regionData.get(config.region_id));
  const charts = {};
  for (const bundle of bundles) {
    charts[`primary:${bundle.config.region_id}`] = { chart: null };
    if (bundle.config.tiers[1]) charts[`secondary:${bundle.config.region_id}`] = { chart: null };
  }
  const panel = {
    root,
    bundles,
    charts,
    map: null,
    boundaryLayer: null,
    labelLayer: null,
    stationLayer: null,
    candidateLayer: null,
    spacingLinkLayer: null,
    boundaryRenderer: null,
    selectedPrimary: null,
    selectedSecondary: null,
    focusedCandidateId: null,
    localStats: root.querySelector(".panel-stats"),
    resizeObserver: null,
  };

  const mapEl = root.querySelector(".map");
  panel.map = L.map(mapEl, { zoomControl: true, attributionControl: true });
  const bounds = unionBounds(bundles);
  panel.map.fitBounds(bounds, { animate: false, padding: [28, 28] });
  const basemap = L.maplibreGL({ style: BASEMAP_STYLE }).addTo(panel.map);
  panel.map.attributionControl.setPrefix(false);
  panel.map.attributionControl.addAttribution(
    '<a href="https://openfreemap.org/" target="_blank" rel="noopener noreferrer">OpenFreeMap</a> © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a>'
  );

  bindPanelControls(panel);
  app.panels.push(panel);

  const paint = () => {
    refreshPanel(panel);
    syncMapViewport(panel, true);
    requestAnimationFrame(() => syncMapViewport(panel, true));
    setTimeout(() => syncMapViewport(panel, true), 150);
  };

  panel.resizeObserver = new ResizeObserver(() => {
    syncMapViewport(panel, !panel.selectedPrimary && !panel.selectedSecondary);
  });
  panel.resizeObserver.observe(mapEl);

  const glMap = basemap.getMaplibreMap?.();
  if (glMap?.loaded?.()) paint();
  else if (glMap?.once) glMap.once("load", paint);
  else {
    paint();
    setTimeout(paint, 200);
  }
}

function formatRetrievedAt(value) {
  const date = new Date(value);
  if (!value || Number.isNaN(date.getTime())) return "";
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const day = String(date.getUTCDate()).padStart(2, "0");
  const hours = String(date.getUTCHours()).padStart(2, "0");
  const minutes = String(date.getUTCMinutes()).padStart(2, "0");
  return `Updated ${months[date.getUTCMonth()]} ${day}, ${date.getUTCFullYear()} · ${hours}:${minutes} UTC`;
}

async function retrievedLabel(regionId) {
  if (retrievedLabels.has(regionId)) return retrievedLabels.get(regionId);
  let label = "";
  try {
    const response = await fetch(`${PUBLIC_ROOT}/${regionId}/manifest.json`, { cache: "no-store" });
    if (response.ok) {
      const manifest = await response.json();
      label = formatRetrievedAt(manifest.retrieved_at);
    }
  } catch {
    label = "";
  }
  retrievedLabels.set(regionId, label);
  return label;
}

async function updateBrand(configs) {
  const config = configs[0];
  const updated = document.getElementById("brand-updated");
  if (!config) {
    updated.hidden = true;
    updated.textContent = "";
    return;
  }
  const source = datasetLabel(config);
  document.getElementById("brand-eyebrow").textContent = source
    ? `${source} · ${config.display_name}`
    : config.display_name;
  document.title = `${config.display_name} EV Map`;
  const label = await retrievedLabel(config.region_id);
  updated.hidden = !label;
  updated.textContent = label;
}

function updateGlobalStats() {
  const root = document.getElementById("global-stats");
  const configs = selectedConfigs();
  if (configs.length === 0) {
    root.hidden = true;
    root.innerHTML = "";
    return;
  }
  root.hidden = false;
  const stationTypes = unionStationTypes(configs);
  const primaryTier = configs[0].tiers[0];
  root.innerHTML = statsHtml(stationTypes, tierTitle(primaryTier, { plural: true }));
  const stations = app.panels.flatMap((panel) => filteredStations(panel));
  fillStats(root, stations, primaryTier, stationTypes);
}

function renderCandidateToggle() {
  const root = document.getElementById("candidate-toggle");
  const available = selectedConfigs().some((config) => app.regionData.get(config.region_id)?.candidates?.length);
  root.hidden = !available;
  if (!available) {
    app.showCandidates = false;
    root.innerHTML = "";
    return;
  }
  const on = app.showCandidates ? " is-on" : "";
  root.innerHTML =
    `<button class="toggle${on}" data-candidates="tvs" aria-pressed="${app.showCandidates}">TVS candidates</button>`;
  root.querySelector("button").addEventListener("click", () => {
    app.showCandidates = !app.showCandidates;
    renderCandidateToggle();
    for (const panel of app.panels) refreshPanel(panel);
  });
}

function renderTypeToggles(stationTypes) {
  const root = document.getElementById("type-toggles");
  if (!stationTypes.includes(app.stationType) && app.stationType !== "all") {
    app.stationType = "all";
  }
  const buttons = [
    `<button class="toggle${app.stationType === "all" ? " is-on" : ""}" data-type="all">All</button>`,
    ...stationTypes.map((typeName) => {
      const meta = typeMeta(typeName);
      const on = app.stationType === typeName ? " is-on" : "";
      return `<button class="toggle${on}" data-type="${typeName}">${meta.label}</button>`;
    }),
  ];
  root.innerHTML = buttons.join("");
  root.querySelectorAll("[data-type]").forEach((button) => {
    button.addEventListener("click", () => {
      app.stationType = button.dataset.type;
      applyManufacturerChoice(app.manufacturer);
      renderTypeToggles(unionStationTypes(selectedConfigs()));
      renderManufacturerSelect();
      for (const panel of app.panels) {
        if (panel.selectedSecondary && !filteredStations(panel).length) {
          panel.selectedSecondary = null;
        }
        refreshPanel(panel);
      }
      updateGlobalStats();
    });
  });
}

function orderedRegions() {
  return [...app.manifest.regions].sort((a, b) => {
    if (a.region_id === "kenya") return -1;
    if (b.region_id === "kenya") return 1;
    return a.display_name.localeCompare(b.display_name);
  });
}

function renderManufacturerSelect() {
  const root = document.getElementById("manufacturer-picker");
  applyManufacturerChoice(app.manufacturer);
  const operators = operatorsForSelection();
  if (operators.length < 2) {
    root.hidden = true;
    root.innerHTML = "";
    return;
  }
  const options = [
    `<option value="${MANUFACTURER_ALL}"${app.manufacturer === MANUFACTURER_ALL ? " selected" : ""}>All manufacturers</option>`,
    ...operators.map((name) => (
      `<option value="${escapeHtml(name)}"${app.manufacturer === name ? " selected" : ""}>${escapeHtml(name)}</option>`
    )),
  ];
  root.hidden = false;
  root.innerHTML =
    `<label class="region-select-label" for="manufacturer-select">Manufacturer</label>` +
    `<select id="manufacturer-select" class="region-select" aria-label="Manufacturer">${options.join("")}</select>`;
  root.querySelector("#manufacturer-select").addEventListener("change", (event) => {
    applyManufacturerChoice(event.target.value);
    renderManufacturerSelect();
    for (const panel of app.panels) refreshPanel(panel);
    updateGlobalStats();
  });
}

function renderRegionPicker() {
  const root = document.getElementById("region-picker");
  const options = orderedRegions().map((region) => {
    const selected = region.region_id === app.selectedRegionId ? " selected" : "";
    return `<option value="${region.region_id}"${selected}>${region.display_name}</option>`;
  }).join("");
  root.innerHTML =
    `<label class="region-select-label" for="region-select">Region</label>` +
    `<select id="region-select" class="region-select" aria-label="Region">${options}</select>`;
  root.querySelector("#region-select").addEventListener("change", async (event) => {
    app.selectedRegionId = event.target.value;
    await renderDashboard();
  });
}

async function ensureRegionData(configs) {
  const missing = configs.filter((config) => !app.regionData.has(config.region_id));
  const loaded = await Promise.all(missing.map(loadRegionBundle));
  for (const bundle of loaded) app.regionData.set(bundle.config.region_id, bundle);
}

async function renderDashboard() {
  const configs = selectedConfigs();
  await ensureRegionData(configs);
  const stationTypes = unionStationTypes(configs);

  await updateBrand(configs);
  renderRegionPicker();
  renderTypeToggles(stationTypes);
  renderManufacturerSelect();
  renderCandidateToggle();

  destroyPanels();
  const workspace = document.getElementById("workspace");
  workspace.classList.add("is-merge");
  workspace.classList.remove("is-grid");

  mountPanel(
    { cluster: configs[0].cluster, regions: configs },
    { showLocalStats: false, showRegionTitles: false }
  );
  updateGlobalStats();
}

async function main() {
  if (TYPE_PARAM && TYPE_META[TYPE_PARAM]) app.stationType = TYPE_PARAM;
  if (MANUFACTURER_PARAM) app.manufacturer = MANUFACTURER_PARAM;
  const manifest = await fetch(`${PUBLIC_ROOT}/regions.json`, { cache: "no-store" }).then((response) => {
    if (!response.ok) throw new Error(`regions.json HTTP ${response.status}`);
    return response.json();
  });
  app.manifest = manifest;
  app.selectedRegionId = defaultRegionId(manifest.regions);
  await renderDashboard();
}

main().catch((error) => {
  document.body.innerHTML =
    `<p style="padding:40px;font:20px/1.4 IBM Plex Sans,sans-serif;color:#f8fafc;background:#070b14">` +
    `Could not load dashboard.<br><br>` +
    `Open <code>/dashboard/</code> from a server rooted at the repo, or open the Streamlit app. ` +
    `This page reads <code>../data/public</code>.<br><br>` +
    `<code>${error}</code></p>`;
});
