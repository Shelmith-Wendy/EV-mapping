const URL_PARAMS = new URLSearchParams(window.location.search);
const EMBEDDED = URL_PARAMS.get("embed") === "1";
const TYPE_PARAM = URL_PARAMS.get("type");
const REGION_PARAM = URL_PARAMS.get("region");
const PUBLIC_ROOT = EMBEDDED ? "/app/static/data/public" : "../data/public";
const BASEMAP_STYLE = "https://tiles.openfreemap.org/styles/dark";
const SELECTED = "#5eead4";
const BOUNDARIES_FILE = "kenya_counties_display.geojson";
const STATIONS_FILE = "stations_with_county.json";

const TYPE_META = {
  fast_charger: { label: "Fast charger", short: "Fast", color: "#4ade80", css: "fast" },
  swap_station: { label: "Swap station", short: "Swap", color: "#fb923c", css: "swap" },
  standard_charger: { label: "Standard charger", short: "Standard", color: "#60a5fa", css: "standard" },
};

const app = {
  manifest: null,
  selectedRegionId: "kenya",
  stationType: "all",
  regionData: new Map(),
  panels: [],
};

if (EMBEDDED) document.body.classList.add("is-embed");

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
  const [primarySummary, stations, geojson, secondarySummary = []] = await Promise.all(requests);
  return {
    config,
    primarySummary: primarySummary.map((row) => ({ ...row, region_id: config.region_id })),
    secondarySummary: secondarySummary.map((row) => ({ ...row, region_id: config.region_id })),
    stations: stations.map((station) => ({ ...station, region_id: config.region_id })),
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

function filteredStations(panel) {
  const stationType = app.stationType;
  let stations = panel.bundles.flatMap((bundle) => bundle.stations);
  stations = stations.filter((station) => stationType === "all" || station.station_type === stationType);
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

function fitMapToSelection(panel) {
  if (!panel.map) return;
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
    for (const row of bundle.primarySummary) {
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

function secondaryRowsFor(panel, bundle) {
  if (!panel.selectedPrimary) return [];
  if (panel.selectedPrimary.regionId !== bundle.config.region_id) return [];
  const primaryTier = bundle.config.tiers[0];
  const secondaryTier = bundle.config.tiers[1];
  if (!secondaryTier) return [];
  return sortedRows(
    bundle.secondarySummary.filter(
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
  const primaryRows = sortedRows(bundle.primarySummary, app.stationType);
  const secondaryRows = secondaryRowsFor(panel, bundle);
  updateChartHeaders(panel, bundle, secondaryRows);

  const primaryCanvas = panel.root.querySelector(`[data-primary-canvas="${regionId}"]`);
  const primaryBox = panel.root.querySelector(`[data-primary-box="${regionId}"]`);
  const primaryRef = panel.charts[`primary:${regionId}`];
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

function refreshPanel(panel) {
  const stationTypes = unionStationTypes(panel.bundles.map((bundle) => bundle.config));
  const primaryTier = panel.bundles[0].config.tiers[0];
  const visible = filteredStations(panel);
  if (panel.localStats) fillStats(panel.localStats, visible, primaryTier, stationTypes);
  for (const bundle of panel.bundles) drawRegionCharts(panel, bundle);
  drawBoundaries(panel);
  drawStations(panel);
  fitMapToSelection(panel);
}

function selectPrimary(panel, regionId, primaryTier, name) {
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
    `<div class="charts-stack">${charts}</div>` +
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
    boundaryRenderer: null,
    selectedPrimary: null,
    selectedSecondary: null,
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

function updateBrand(configs) {
  const config = configs[0];
  if (!config) return;
  const source = datasetLabel(config);
  document.getElementById("brand-eyebrow").textContent = source
    ? `${source} · ${config.display_name}`
    : config.display_name;
  document.title = `${config.display_name} EV Map`;
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
      renderTypeToggles(unionStationTypes(selectedConfigs()));
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

  updateBrand(configs);
  renderRegionPicker();
  renderTypeToggles(stationTypes);

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
  const manifest = await fetch(`${PUBLIC_ROOT}/regions.json`, { cache: "no-store" }).then((response) => {
    if (!response.ok) throw new Error(`regions.json HTTP ${response.status}`);
    return response.json();
  });
  app.manifest = manifest;
  app.selectedRegionId = defaultRegionId(manifest.regions);
  await renderDashboard();
}

main().catch((error) => {
  const hint = EMBEDDED
    ? "Streamlit embed failed while starting the map. Hard-refresh http://127.0.0.1:8501/ after restarting Streamlit from <code>app/</code>."
    : "Serve the repo root (not this folder):<br><code>python3 -m http.server 8000</code><br>then open <code>/dashboard/</code><br><br>Run <code>python scripts/run_pipeline.py</code> to build <code>data/public/</code>.";
  document.body.innerHTML = `<p style="padding:40px;font:20px/1.4 IBM Plex Sans,sans-serif;color:#f8fafc;background:#070b14">Could not load dashboard.<br><br>${hint}<br><br><code>${error}</code></p>`;
});
