const STORAGE_KEY = "wildfireMvpStateV2";

const state = {
  config: null,
  map: null,
  layerData: {},
  layerMeta: [],
  layerObjects: {},
  overlayState: {
    roads: true,
    shelters: true,
    water: true,
    staging: true,
    fireline: true,
    closures: true
  },
  startPoint: null,
  selectedTarget: null,
  nearbyItems: null,
  routeCandidates: [],
  routeLayers: [],
  highlightedRouteId: null,
  blockedSegmentIds: new Set(),
  lastRouteAnalysis: null,
  bootstrapped: false
};

const ROUTE_COLORS = ["#10b981", "#f59e0b", "#fb7185"];

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let message = response.statusText;
    try {
      const payload = await response.json();
      message = payload.detail || payload.message || message;
    } catch (_) {}
    throw new Error(message);
  }
  return response.json();
}

function byId(id) {
  return document.getElementById(id);
}

function setText(id, text) {
  const node = byId(id);
  if (node) node.textContent = text;
}

function formatMeters(value) {
  return `${Math.round(value)}m`;
}

function formatMinutes(value) {
  return `${Number(value).toFixed(1)}분`;
}

function formatLatLng(point) {
  return `${point.lat.toFixed(5)}, ${point.lng.toFixed(5)}`;
}

function formatTimestamp(value) {
  if (!value) return "시간 없음";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ko-KR", { hour12: false });
}

function severityMeta(severity) {
  return {
    ok: { text: "양호", className: "status-ready" },
    caution: { text: "주의", className: "status-pending" },
    danger: { text: "위험", className: "status-danger" }
  }[severity] || { text: severity, className: "status-pending" };
}

function layerLabel(layerName) {
  return {
    fireline: "화선",
    closures: "통제선",
    shelters: "대피소",
    water: "수자원",
    staging: "집결지",
    roads: "도로/임도"
  }[layerName] || layerName;
}

function layerSourceLabel(source) {
  return {
    sample: "샘플",
    uploaded: "업로드",
    remote: "원격"
  }[source] || source;
}

function loadPersistedState() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
  } catch (_) {
    return null;
  }
}

function persistState() {
  if (!state.bootstrapped) return;
  const payload = {
    overlayState: state.overlayState,
    startPoint: state.startPoint,
    selectedTarget: state.selectedTarget,
    blockedSegmentIds: Array.from(state.blockedSegmentIds),
    goalLayer: byId("goalLayerSelect")?.value,
    nightMode: byId("nightModeCheckbox")?.checked
  };
  localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
}

function hydrateFromStorage() {
  const saved = loadPersistedState();
  state.startPoint = saved?.startPoint || { ...state.config.defaultCenter };
  state.selectedTarget = saved?.selectedTarget || null;
  state.blockedSegmentIds = new Set(saved?.blockedSegmentIds || []);
  state.overlayState = {
    ...state.overlayState,
    ...(saved?.overlayState || {})
  };
  if (saved?.goalLayer) {
    byId("goalLayerSelect").value = saved.goalLayer;
  }
  if (typeof saved?.nightMode === "boolean") {
    byId("nightModeCheckbox").checked = saved.nightMode;
  }
}

async function init() {
  state.config = await fetchJson("/api/config");
  hydrateFromStorage();
  state.layerMeta = state.config.layers || [];
  buildLayerToggles(state.layerMeta);
  renderSourceCatalog(state.config.sources);
  renderSelectedTarget();
  renderStartPoint();
  renderBlockedSegments();
  await initMap();
  await loadAllLayers();
  await refreshNearby();
  renderLayerBadges();
  renderBriefing();
  setText("syncStatus", `준비 완료 · 업로드 형식 ${state.config.acceptedUploadFormats.join(", ")}`);
  state.bootstrapped = true;
  persistState();
  registerServiceWorker();
}

async function refreshLayerMeta() {
  const payload = await fetchJson("/api/layers");
  state.layerMeta = payload.layers || [];
  buildLayerToggles(state.layerMeta);
  renderLayerBadges();
}

async function initMap() {
  state.map = L.map("map", {
    zoomControl: true,
    preferCanvas: true
  }).setView([state.startPoint.lat, state.startPoint.lng], state.config.defaultZoom);

  L.tileLayer(state.config.tile.url, {
    attribution: state.config.tile.attribution,
    maxZoom: 19
  }).addTo(state.map);

  state.config.wmsLayers.forEach((item) => {
    const wmsLayer = L.tileLayer.wms(item.url, {
      layers: item.layers,
      format: item.format || "image/png",
      transparent: item.transparent !== false,
      opacity: 0.45
    });
    state.layerObjects[item.id] = wmsLayer;
  });

  state.map.on("click", (event) => {
    state.startPoint = { lat: event.latlng.lat, lng: event.latlng.lng };
    renderStartPoint();
    renderStartMarker();
    refreshNearby();
    renderBriefing();
    persistState();
  });

  renderStartMarker();
  bindDelegatedActions();
}

function buildLayerToggles(layers) {
  const root = byId("layerToggles");
  root.innerHTML = "";
  layers.forEach((layer) => {
    const row = document.createElement("div");
    row.className = "toggle-row";
    row.innerHTML = `
      <label>
        <input type="checkbox" data-layer-toggle="${layer.name}" ${state.overlayState[layer.name] ? "checked" : ""} />
        <div class="toggle-copy">
          <span>${layer.title}</span>
          <span class="muted small">${layerSourceLabel(layer.source)} · ${layer.updated_at ? formatTimestamp(layer.updated_at) : "시간 없음"}</span>
        </div>
      </label>
      <span class="muted small">${layer.feature_count}</span>
    `;
    root.appendChild(row);
  });
}

function renderLayerBadges() {
  const badges = byId("layerBadges");
  badges.innerHTML = "";
  state.layerMeta.forEach((item) => {
    const badge = document.createElement("div");
    badge.className = "badge";
    badge.textContent = `${item.title} ${item.feature_count} · ${layerSourceLabel(item.source)}`;
    badges.appendChild(badge);
  });
  const blockedBadge = document.createElement("div");
  blockedBadge.className = "badge";
  blockedBadge.textContent = `수동 차단 ${state.blockedSegmentIds.size}`;
  badges.appendChild(blockedBadge);
}

function renderSourceCatalog(items) {
  const root = byId("sourceCatalog");
  root.innerHTML = "";
  items.forEach((item) => {
    const statusClass = {
      ready: "status-ready",
      cataloged: "status-ready",
      pending_key: "status-pending",
      pending_config: "status-pending",
      manual_upload: "status-manual"
    }[item.status] || "status-pending";
    const statusText = {
      ready: "연계 준비",
      cataloged: "카탈로그 완료",
      pending_key: "키 필요",
      pending_config: "설정 필요",
      manual_upload: "수동 반영"
    }[item.status] || item.status;

    const card = document.createElement("div");
    card.className = "catalog-card";
    card.innerHTML = `
      <div class="catalog-head">
        <div>
          <div class="catalog-title">${item.name}</div>
          <div class="muted small">${item.purpose}</div>
        </div>
        <span class="status-pill ${statusClass}">${statusText}</span>
      </div>
    `;
    root.appendChild(card);
  });
}

async function loadLayer(name) {
  const payload = await fetchJson(`/api/layers/${name}`);
  state.layerData[name] = payload;
  createOrUpdateLayer(name, payload);
}

async function loadAllLayers() {
  for (const name of ["roads", "shelters", "water", "staging", "fireline", "closures"]) {
    await loadLayer(name);
  }
  syncLayerVisibility();
  renderLayerBadges();
}

function roadFeatureId(feature) {
  return String(feature?.properties?.id || "");
}

function isBlockedRoad(featureOrId) {
  const id = typeof featureOrId === "string" ? featureOrId : roadFeatureId(featureOrId);
  return state.blockedSegmentIds.has(id);
}

function getLineStyle(name, feature) {
  if (name === "roads") {
    return isBlockedRoad(feature)
      ? { color: "#e11d48", weight: 5, opacity: 0.95, dashArray: "10 8" }
      : { color: "#7dd3fc", weight: 3, opacity: 0.75 };
  }
  if (name === "fireline") {
    return { color: "#ef4444", weight: 3, fillColor: "#ef4444", fillOpacity: 0.22 };
  }
  if (name === "closures") {
    return { color: "#fb923c", weight: 4, dashArray: "8 8", opacity: 0.95 };
  }
  return { color: "#7dd3fc", weight: 3, opacity: 0.75 };
}

function createOrUpdateLayer(name, payload) {
  if (state.layerObjects[name]) {
    state.map.removeLayer(state.layerObjects[name]);
  }

  const pointStyleByLayer = {
    shelters: { radius: 8, color: "#60a5fa", fillColor: "#60a5fa", fillOpacity: 0.92, weight: 2 },
    water: { radius: 8, color: "#22d3ee", fillColor: "#22d3ee", fillOpacity: 0.92, weight: 2 },
    staging: { radius: 8, color: "#34d399", fillColor: "#34d399", fillOpacity: 0.92, weight: 2 }
  };

  const layer = L.geoJSON(payload, {
    style: (feature) => getLineStyle(name, feature),
    pointToLayer: (feature, latlng) => {
      const style = pointStyleByLayer[name];
      return style ? L.circleMarker(latlng, style) : L.marker(latlng);
    },
    onEachFeature: (feature, leafletLayer) => {
      const props = feature.properties || {};
      const lines = [
        `<strong>${props.name || layerLabel(name)}</strong>`,
        props.address ? `<div>${props.address}</div>` : "",
        props.capacity ? `<div>수용: ${props.capacity}</div>` : "",
        props.kind ? `<div>유형: ${props.kind}</div>` : "",
        props.id ? `<div class="muted small">ID: ${props.id}</div>` : ""
      ].filter(Boolean);

      if (["shelters", "water", "staging"].includes(name)) {
        lines.push(`<a href="#" class="popup-button" data-target-layer="${name}" data-target-id="${props.id}" data-target-name="${props.name || ""}">이곳을 목표로 지정</a>`);
      }

      if (name === "roads") {
        lines.push(`<a href="#" class="popup-button secondary-link" data-toggle-block-id="${props.id}" data-toggle-block-name="${props.name || props.id}">${isBlockedRoad(feature) ? "차단 해제" : "차단 후보로 표시"}</a>`);
      }

      leafletLayer.bindPopup(lines.join(""));
    }
  });

  state.layerObjects[name] = layer;
  if (state.overlayState[name]) {
    layer.addTo(state.map);
  }
}

function syncLayerVisibility() {
  Object.entries(state.layerObjects).forEach(([name, layer]) => {
    if (!layer || ["startMarker", "routeTargetMarker"].includes(name)) return;
    if (state.overlayState[name]) {
      if (!state.map.hasLayer(layer)) layer.addTo(state.map);
    } else if (state.map.hasLayer(layer)) {
      state.map.removeLayer(layer);
    }
  });
}

function renderStartMarker() {
  if (state.layerObjects.startMarker) {
    state.map.removeLayer(state.layerObjects.startMarker);
  }
  state.layerObjects.startMarker = L.circleMarker([state.startPoint.lat, state.startPoint.lng], {
    radius: 9,
    color: "#f97316",
    fillColor: "#f97316",
    fillOpacity: 0.95,
    weight: 2
  }).bindPopup("시작점");
  state.layerObjects.startMarker.addTo(state.map);
}

function renderStartPoint() {
  byId("startPointText").innerHTML = `
    <div>좌표: ${formatLatLng(state.startPoint)}</div>
    <div class="muted small">지도 클릭 또는 현위치 버튼으로 변경</div>
  `;
}

function renderSelectedTarget() {
  if (!state.selectedTarget) {
    byId("selectedTargetCard").textContent = "지도 또는 인접 자원 목록에서 특정 목표를 지정할 수 있습니다.";
    return;
  }
  byId("selectedTargetCard").innerHTML = `
    <div><strong>${state.selectedTarget.name || state.selectedTarget.id}</strong></div>
    <div class="muted small">유형: ${layerLabel(state.selectedTarget.layer)}</div>
  `;
}

function roadNameById(roadId) {
  const feature = (state.layerData.roads?.features || []).find((item) => String(item.properties?.id) === String(roadId));
  return feature?.properties?.name || roadId;
}

function renderBlockedSegments() {
  const root = byId("blockedSegmentList");
  const ids = Array.from(state.blockedSegmentIds);
  if (!ids.length) {
    root.classList.add("empty");
    root.textContent = "차단 후보가 없습니다.";
    return;
  }
  root.classList.remove("empty");
  root.innerHTML = ids.map((id) => `
    <span class="block-chip">
      <span>${roadNameById(id)}</span>
      <button class="secondary tiny" data-toggle-block-id="${id}" data-toggle-block-name="${roadNameById(id)}">해제</button>
    </span>
  `).join("");
}

function toggleBlockedSegment(segmentId, name) {
  if (!segmentId) return;
  if (state.blockedSegmentIds.has(segmentId)) {
    state.blockedSegmentIds.delete(segmentId);
    setText("syncStatus", `${name || segmentId} 차단 해제`);
  } else {
    state.blockedSegmentIds.add(segmentId);
    setText("syncStatus", `${name || segmentId} 차단 후보 반영`);
  }
  renderBlockedSegments();
  renderLayerBadges();
  invalidateRoutes(`${name || segmentId} 반영 후 경로 재계산이 필요합니다.`);
  if (state.layerData.roads) {
    createOrUpdateLayer("roads", state.layerData.roads);
    syncLayerVisibility();
  }
  persistState();
}

function bindDelegatedActions() {
  document.body.addEventListener("click", (event) => {
    const targetAction = event.target.closest("[data-target-layer]");
    if (targetAction) {
      event.preventDefault();
      state.selectedTarget = {
        layer: targetAction.dataset.targetLayer,
        id: targetAction.dataset.targetId,
        name: targetAction.dataset.targetName
      };
      byId("goalLayerSelect").value = state.selectedTarget.layer;
      renderSelectedTarget();
      renderBriefing();
      state.map.closePopup();
      persistState();
      return;
    }

    const blockAction = event.target.closest("[data-toggle-block-id]");
    if (blockAction) {
      event.preventDefault();
      toggleBlockedSegment(blockAction.dataset.toggleBlockId, blockAction.dataset.toggleBlockName || blockAction.dataset.toggleBlockId);
      state.map.closePopup();
      return;
    }
  });
}

async function refreshNearby() {
  try {
    const payload = await fetchJson(`/api/nearby?lat=${state.startPoint.lat}&lng=${state.startPoint.lng}`);
    state.nearbyItems = payload.items;
    renderNearby(payload.items);
    renderBriefing();
  } catch (error) {
    byId("nearbyList").textContent = `인접 자원 조회 실패: ${error.message}`;
  }
}

function renderNearby(items) {
  const root = byId("nearbyList");
  root.innerHTML = "";
  const groups = [
    ["shelters", "대피소"],
    ["water", "수자원"],
    ["staging", "대기/집결지"]
  ];

  groups.forEach(([key, title]) => {
    const groupItems = items[key] || [];
    const card = document.createElement("div");
    card.className = "nearby-card";
    const body = groupItems.length
      ? groupItems.map((item) => `
        <div class="small" style="margin-top:0.45rem">
          <div class="chip-row">
            <strong>${item.name || item.id}</strong>
            <button class="secondary tiny" data-target-layer="${key}" data-target-id="${item.id}" data-target-name="${item.name || ""}">목표 지정</button>
          </div>
          <div class="muted">${formatMeters(item.distance_m)} · ${item.address || "-"}</div>
        </div>
      `).join("")
      : `<div class="muted small">없음</div>`;

    card.innerHTML = `
      <div class="nearby-head">
        <div class="nearby-title">${title}</div>
      </div>
      ${body}
    `;
    root.appendChild(card);
  });
}

async function calculateRoutes() {
  try {
    setText("syncStatus", "경로 계산 중…");
    const payload = {
      start: state.startPoint,
      goal_layer: byId("goalLayerSelect").value,
      night_mode: byId("nightModeCheckbox").checked,
      max_candidates: 3,
      blocked_segment_ids: Array.from(state.blockedSegmentIds)
    };
    if (state.selectedTarget && state.selectedTarget.layer === payload.goal_layer) {
      payload.goal_id = state.selectedTarget.id;
    }
    const result = await fetchJson("/api/route", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(payload)
    });
    state.routeCandidates = result.routes || [];
    state.lastRouteAnalysis = result.analysis || null;
    state.highlightedRouteId = state.routeCandidates[0] ? state.routeCandidates[0].id : null;
    renderRoutes();
    drawRouteCandidates();
    renderBriefing();
    setText("syncStatus", `경로 ${state.routeCandidates.length}개 계산 완료`);
  } catch (error) {
    setText("syncStatus", `경로 계산 실패: ${error.message}`);
    byId("routeList").innerHTML = `<div class="route-card">${error.message}</div>`;
    byId("briefingCard").textContent = `경로 계산 실패: ${error.message}`;
  }
}

function activeRoute() {
  return state.routeCandidates.find((item) => item.id === state.highlightedRouteId) || state.routeCandidates[0] || null;
}

function invalidateRoutes(message = "기존 경로를 초기화했습니다.") {
  state.routeCandidates = [];
  state.highlightedRouteId = null;
  state.lastRouteAnalysis = null;
  state.routeLayers.forEach((layer) => state.map && state.map.removeLayer(layer));
  state.routeLayers = [];
  if (state.layerObjects.routeTargetMarker) {
    state.map.removeLayer(state.layerObjects.routeTargetMarker);
    delete state.layerObjects.routeTargetMarker;
  }
  renderRoutes();
  renderBriefing();
  setText("syncStatus", message);
}

function renderBriefing(analysis = null) {
  const effectiveAnalysis = analysis || state.lastRouteAnalysis;
  const route = activeRoute();
  const root = byId("briefingCard");
  if (!route) {
    const nearestShelter = state.nearbyItems?.shelters?.[0];
    const nearestWater = state.nearbyItems?.water?.[0];
    root.innerHTML = `
      <div><strong>시작점</strong> ${formatLatLng(state.startPoint)}</div>
      <div class="muted small" style="margin-top:0.4rem">최근접 대피소: ${nearestShelter ? nearestShelter.name : "없음"}</div>
      <div class="muted small">최근접 수자원: ${nearestWater ? nearestWater.name : "없음"}</div>
      <div class="muted small">수동 차단 도로: ${state.blockedSegmentIds.size}개</div>
    `;
    return;
  }
  const nearestWater = state.nearbyItems?.water?.[0];
  const severity = severityMeta(route.severity);
  root.innerHTML = `
    <div class="route-head">
      <div>
        <div><strong>${route.target.name || route.target.feature_id}</strong></div>
        <div class="muted small">${route.reason}</div>
      </div>
      <span class="status-pill ${severity.className}">${severity.text}</span>
    </div>
    <div class="metric-row">
      <span class="metric-chip">총거리 ${formatMeters(route.distance_m)}</span>
      <span class="metric-chip">예상 ${formatMinutes(route.eta_min)}</span>
      <span class="metric-chip">최소 이격 ${formatMeters(route.min_clearance_m)}</span>
      <span class="metric-chip">수동 차단 ${state.blockedSegmentIds.size}</span>
    </div>
    <div class="muted small" style="margin-top:0.5rem">시작점 ${formatLatLng(state.startPoint)}</div>
    <div class="muted small">최근접 수자원 ${nearestWater ? nearestWater.name : "없음"}</div>
    ${effectiveAnalysis ? `<div class="muted small">검토 목표 ${effectiveAnalysis.targets_considered}개 · 시작점 네트워크 이격 ${formatMeters(effectiveAnalysis.start_snap_distance_m)}</div>` : ""}
  `;
}

function renderRoutes() {
  const root = byId("routeList");
  if (!state.routeCandidates.length) {
    root.textContent = "후보 경로가 없습니다.";
    return;
  }
  root.innerHTML = "";
  state.routeCandidates.forEach((route, index) => {
    const severity = severityMeta(route.severity);
    const card = document.createElement("div");
    card.className = `route-card ${route.id === state.highlightedRouteId || (!state.highlightedRouteId && index === 0) ? "active" : ""}`;
    card.dataset.routeId = route.id;
    card.innerHTML = `
      <div class="route-head">
        <div>
          <div class="route-title route-color-${(index % 3) + 1}">후보 ${index + 1}</div>
          <div class="muted small">${route.target.name || route.target.feature_id}</div>
          <div class="muted small">${route.reason}</div>
        </div>
        <div>
          <div class="status-pill ${severity.className}">${severity.text}</div>
          <div class="muted small" style="margin-top:0.35rem; text-align:right;">점수 ${route.score}</div>
        </div>
      </div>
      <div class="metric-row">
        <span class="metric-chip">총거리 ${formatMeters(route.distance_m)}</span>
        <span class="metric-chip">망상거리 ${formatMeters(route.network_distance_m)}</span>
        <span class="metric-chip">접속 ${formatMeters(route.connector_distance_m)}</span>
        <span class="metric-chip">예상 ${formatMinutes(route.eta_min)}</span>
        <span class="metric-chip">화선 중첩 ${formatMeters(route.hazard_overlap_m)}</span>
        <span class="metric-chip">최소 이격 ${formatMeters(route.min_clearance_m)}</span>
      </div>
      ${route.warnings.length ? `<ul class="warning-list">${route.warnings.map((item) => `<li>${item}</li>`).join("")}</ul>` : ""}
      <details class="segment-details">
        <summary>구간 ${route.segments.length}개 보기</summary>
        <div class="segment-list">
          ${route.segments.map((segment) => `
            <div class="segment-row">
              <div>
                <div><strong>${segment.name || segment.id}</strong></div>
                <div class="muted small">${formatMeters(segment.distance_m)} · ${formatMinutes(segment.travel_min)} · ${segment.segment_type}</div>
              </div>
              <button class="secondary tiny" data-toggle-block-id="${segment.id}" data-toggle-block-name="${segment.name || segment.id}">${state.blockedSegmentIds.has(segment.id) ? "해제" : "차단"}</button>
            </div>
          `).join("")}
        </div>
      </details>
    `;
    card.addEventListener("click", () => {
      state.highlightedRouteId = route.id;
      drawRouteCandidates();
      renderRoutes();
      renderBriefing();
    });
    root.appendChild(card);
  });
}

function drawRouteCandidates() {
  state.routeLayers.forEach((layer) => state.map.removeLayer(layer));
  state.routeLayers = [];
  if (state.layerObjects.routeTargetMarker) {
    state.map.removeLayer(state.layerObjects.routeTargetMarker);
    delete state.layerObjects.routeTargetMarker;
  }

  state.routeCandidates.forEach((route, index) => {
    const isActive = route.id === state.highlightedRouteId || (!state.highlightedRouteId && index === 0);
    const layer = L.geoJSON(route.geometry, {
      style: {
        color: ROUTE_COLORS[index] || ROUTE_COLORS[0],
        weight: isActive ? 7 : 4,
        opacity: isActive ? 0.95 : 0.55
      }
    }).addTo(state.map);
    state.routeLayers.push(layer);
    if (isActive) {
      state.map.fitBounds(layer.getBounds(), { padding: [40, 40] });
      const point = route.target?.geometry?.coordinates;
      if (point && Array.isArray(point) && point.length >= 2) {
        state.layerObjects.routeTargetMarker = L.circleMarker([point[1], point[0]], {
          radius: 8,
          color: ROUTE_COLORS[index] || ROUTE_COLORS[0],
          fillColor: ROUTE_COLORS[index] || ROUTE_COLORS[0],
          fillOpacity: 0.95,
          weight: 2
        }).bindPopup(route.target.name || route.target.feature_id);
        state.layerObjects.routeTargetMarker.addTo(state.map);
      }
    }
  });
}

async function uploadLayer(layerName, inputId) {
  const input = byId(inputId);
  if (!input.files || !input.files.length) {
    setText("syncStatus", "업로드할 파일을 선택하세요.");
    return;
  }
  const form = new FormData();
  form.append("file", input.files[0]);

  try {
    const result = await fetchJson(`/api/incidents/${layerName}`, {
      method: "POST",
      body: form
    });
    await loadLayer(layerName);
    await refreshLayerMeta();
    syncLayerVisibility();
    invalidateRoutes(`${result.message} · 경로를 다시 계산하세요.`);
    input.value = "";
  } catch (error) {
    setText("syncStatus", `업로드 실패: ${error.message}`);
  }
}

async function resetDemo() {
  await fetchJson("/api/reset-demo", { method: "POST" });
  await loadLayer("fireline");
  await loadLayer("closures");
  await refreshLayerMeta();
  syncLayerVisibility();
  invalidateRoutes("샘플 데이터로 복구했습니다. 경로를 다시 계산하세요.");
}

function registerServiceWorker() {
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
}

function bindEvents() {
  byId("routeBtn").addEventListener("click", calculateRoutes);

  byId("useMapCenterBtn").addEventListener("click", () => {
    const center = state.map.getCenter();
    state.startPoint = { lat: center.lat, lng: center.lng };
    renderStartPoint();
    renderStartMarker();
    refreshNearby();
    renderBriefing();
    persistState();
  });

  byId("useGeolocationBtn").addEventListener("click", () => {
    if (!navigator.geolocation) {
      setText("syncStatus", "브라우저 위치 기능을 사용할 수 없습니다.");
      return;
    }
    navigator.geolocation.getCurrentPosition((position) => {
      state.startPoint = {
        lat: position.coords.latitude,
        lng: position.coords.longitude
      };
      state.map.setView([state.startPoint.lat, state.startPoint.lng], Math.max(state.map.getZoom(), 14));
      renderStartPoint();
      renderStartMarker();
      refreshNearby();
      renderBriefing();
      setText("syncStatus", "현위치를 반영했습니다.");
      persistState();
    }, () => {
      setText("syncStatus", "현위치를 가져오지 못했습니다.");
    }, { enableHighAccuracy: true, timeout: 8000 });
  });

  byId("clearTargetBtn").addEventListener("click", () => {
    state.selectedTarget = null;
    renderSelectedTarget();
    renderBriefing();
    persistState();
  });

  byId("clearBlockedSegmentsBtn").addEventListener("click", () => {
    state.blockedSegmentIds.clear();
    renderBlockedSegments();
    renderLayerBadges();
    invalidateRoutes("차단 후보 초기화 후 경로 재계산이 필요합니다.");
    if (state.layerData.roads) {
      createOrUpdateLayer("roads", state.layerData.roads);
      syncLayerVisibility();
    }
    persistState();
  });

  byId("uploadFirelineBtn").addEventListener("click", () => uploadLayer("fireline", "firelineFile"));
  byId("uploadClosuresBtn").addEventListener("click", () => uploadLayer("closures", "closuresFile"));
  byId("resetDemoBtn").addEventListener("click", resetDemo);

  byId("goalLayerSelect").addEventListener("change", persistState);
  byId("nightModeCheckbox").addEventListener("change", persistState);
  byId("layerToggles").addEventListener("change", (event) => {
    const layerName = event.target.dataset.layerToggle;
    if (!layerName) return;
    state.overlayState[layerName] = event.target.checked;
    syncLayerVisibility();
    persistState();
  });
}

window.addEventListener("DOMContentLoaded", async () => {
  bindEvents();
  try {
    await init();
  } catch (error) {
    setText("syncStatus", `초기화 실패: ${error.message}`);
  }
});
