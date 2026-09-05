"use strict";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

const state = {
  status: null,
  settings: null,
  selectedEndpoint: null,
  lastLogId: 0,
  logCount: 0,
  logsPaused: false,
  dirty: new Map(),
  memory: null,
  memoryAreasReady: false,
  toastTimer: null,
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  let payload = null;
  try { payload = await response.json(); } catch { payload = null; }
  if (!response.ok) throw new Error(payload?.error || `HTTP ${response.status}`);
  return payload;
}

function showToast(message, isError = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.toggle("is-error", isError);
  toast.classList.add("is-visible");
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => toast.classList.remove("is-visible"), 3200);
}

function formatNumber(value, maximumFractionDigits = 0) {
  return new Intl.NumberFormat("ja-JP", { maximumFractionDigits }).format(Number(value || 0));
}

function formatBytes(value) {
  let bytes = Number(value || 0);
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let unit = 0;
  while (bytes >= 1024 && unit < units.length - 1) {
    bytes /= 1024;
    unit += 1;
  }
  return `${bytes >= 10 || unit === 0 ? bytes.toFixed(0) : bytes.toFixed(1)} ${units[unit]}`;
}

function formatUptime(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds || 0)));
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (days) return `${days}d ${hours}h ${minutes}m`;
  if (hours) return `${hours}h ${minutes}m ${secs}s`;
  return `${minutes}m ${secs}s`;
}

function formatWhen(value) {
  if (!value) return "never";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const delta = Math.max(0, Date.now() - date.getTime());
  if (delta < 1000) return "just now";
  if (delta < 60000) return `${Math.floor(delta / 1000)}s ago`;
  if (delta < 3600000) return `${Math.floor(delta / 60000)}m ago`;
  return date.toLocaleString("ja-JP", { hour12: false });
}

function setConnection(online, label = "ONLINE") {
  const dot = $("#connectionDot");
  dot.classList.remove("is-connecting", "is-online", "is-offline");
  dot.classList.add(online ? "is-online" : "is-offline");
  $("#connectionLabel").textContent = label;
}

async function refreshStatus(showError = false) {
  try {
    const status = await api("/api/status");
    state.status = status;
    setConnection(true, status.healthy ? "HEALTHY" : "ATTENTION");
    $("#serverAddress").textContent = location.origin;
    renderOverview(status);
    syncMemoryAreas(status);
    syncLogEndpointFilter(status);
  } catch (error) {
    setConnection(false, "OFFLINE");
    if (showError) showToast(error.message, true);
  }
}

function renderOverview(status) {
  const metrics = status.metrics || {};
  const rates = status.rates || {};
  $("#metricUptime").textContent = formatUptime(status.uptime_seconds);
  $("#metricVersion").textContent = `version ${status.version}`;
  $("#metricReceived").textContent = formatNumber(metrics.received);
  $("#metricRxRate").textContent = `${formatNumber(rates.received_per_second, 2)} req/s · ${formatBytes(metrics.bytes_received)} received`;
  $("#metricSent").textContent = formatNumber(metrics.sent);
  $("#metricTxRate").textContent = `${formatNumber(rates.sent_per_second, 2)} resp/s · ${formatBytes(metrics.bytes_sent)} sent`;
  $("#metricProblems").textContent = `${formatNumber(metrics.no_response)} / ${formatNumber(metrics.errors)}`;
  const faultCount = Number(metrics.fault_drops || 0) + Number(metrics.fault_corruptions || 0) + Number(metrics.fault_duplicates || 0);
  $("#metricFaults").textContent = `fault events ${formatNumber(faultCount)} · rejected ${formatNumber(metrics.rejected)}`;
  $("#endpointCount").textContent = `${status.running_endpoints}/${status.desired_endpoints}`;
  $("#runtimeConfig").textContent = status.config_source;
  $("#runtimeWeb").textContent = `${status.web.bind}:${status.web.port} · ${status.web.allow_write ? "read/write" : "read only"}`;
  $("#runtimeProcess").textContent = `PID ${status.system.pid} · Python ${status.system.python} · ${status.system.threads} threads`;
  const load = Array.isArray(status.system.load_average) ? ` · load ${status.system.load_average.map((item) => Number(item).toFixed(2)).join(" / ")}` : "";
  $("#runtimeResources").textContent = `${formatBytes(status.system.max_rss_bytes)} max RSS · ${status.system.active_requests} active requests${load}`;
  $("#runtimeMemory").textContent = `${status.memory.words.length} word areas · ${status.memory.bits.length} bit areas · ${formatBytes(status.memory.estimated_bytes)}`;
  $("#logModeSelect").value = status.logging.mode;
  $("#logModeSelect").disabled = !status.web.allow_write;

  const banner = $("#healthBanner");
  banner.className = `health-banner ${status.healthy ? "is-healthy" : "is-warning"}`;
  $("#healthTitle").textContent = status.healthy ? "All requested endpoints are running" : "Runtime needs attention";
  $("#healthDetail").textContent = `${status.running_endpoints} running / ${status.desired_endpoints} expected · ${formatNumber(rates.received_per_second, 2)} incoming requests per second`;
  const healthBadge = $("#runtimeHealthBadge");
  healthBadge.textContent = status.healthy ? "HEALTHY" : "ATTENTION";
  healthBadge.className = `badge ${status.healthy ? "success" : "warning"}`;

  renderWarnings(status.warnings || []);
  renderEndpointTable(status.endpoints || []);
  drawTrafficChart($("#trafficChart"), status.history || []);

  const writeBadge = $("#memoryWriteBadge");
  writeBadge.textContent = status.web.allow_write ? "READ / WRITE" : "READ ONLY";
  writeBadge.className = `badge ${status.web.allow_write ? "success" : "warning"}`;
  $("#memoryCount").max = String(status.web.max_memory_points);
}

function renderWarnings(warnings) {
  const container = $("#warningList");
  container.replaceChildren();
  for (const warning of warnings) {
    const item = document.createElement("div");
    item.className = `warning-item severity-${warning.severity || "info"}`;
    const code = document.createElement("span");
    code.className = "warning-code";
    code.textContent = warning.code || "notice";
    const text = document.createElement("span");
    text.textContent = warning.message;
    item.append(code, text);
    container.appendChild(item);
  }
}

function endpointState(endpoint) {
  if (endpoint.running) return { label: "UP", className: "running" };
  if (endpoint.desired_running) return { label: "ERROR", className: "error" };
  return { label: "STOPPED", className: "stopped" };
}

function renderEndpointTable(endpoints) {
  if (!state.selectedEndpoint && endpoints.length) state.selectedEndpoint = endpoints[0].name;
  const body = $("#endpointTableBody");
  body.replaceChildren();
  for (const endpoint of endpoints) {
    const row = document.createElement("tr");
    row.className = "endpoint-row";
    row.dataset.endpoint = endpoint.name;
    if (state.selectedEndpoint === endpoint.name) row.classList.add("is-selected");
    const status = endpointState(endpoint);
    const address = endpoint.running
      ? `${endpoint.bound_host}:${endpoint.bound_port}/udp`
      : `${endpoint.configured_bind}:${endpoint.configured_port}/udp`;
    row.innerHTML = `
      <td><span class="endpoint-state ${status.className}">${status.label}</span></td>
      <td class="endpoint-name"></td>
      <td><span class="protocol-chip"></span></td>
      <td class="address-text"></td>
      <td class="numeric">${formatNumber(endpoint.rates?.received_per_second, 2)}</td>
      <td class="numeric">${formatNumber(endpoint.metrics?.received)}</td>
      <td class="numeric">${formatNumber(endpoint.average_latency_ms, 2)}</td>
      <td class="numeric">${formatNumber(endpoint.client_count)}</td>
      <td><button type="button" class="button tiny inspect-endpoint">Inspect</button></td>`;
    row.children[1].textContent = endpoint.name;
    row.querySelector(".protocol-chip").textContent = endpoint.protocol;
    row.querySelector(".address-text").textContent = address;
    row.querySelector(".inspect-endpoint").addEventListener("click", (event) => {
      event.stopPropagation();
      selectEndpoint(endpoint.name);
    });
    row.addEventListener("click", () => selectEndpoint(endpoint.name));
    body.appendChild(row);
  }
  if (!endpoints.length) body.innerHTML = '<tr><td colspan="9" class="empty-cell">No endpoints configured.</td></tr>';
  if (state.selectedEndpoint && !endpoints.some((item) => item.name === state.selectedEndpoint)) state.selectedEndpoint = null;
  renderEndpointDetail();
}

function selectEndpoint(name) {
  state.selectedEndpoint = name;
  renderEndpointTable(state.status?.endpoints || []);
}

function renderEndpointDetail() {
  const panel = $("#endpointDetail");
  const endpoint = (state.status?.endpoints || []).find((item) => item.name === state.selectedEndpoint);
  if (!endpoint) {
    panel.className = "endpoint-detail is-empty";
    panel.textContent = "Select an endpoint to inspect its protocol state, last request, clients, and fault settings.";
    return;
  }
  panel.className = "endpoint-detail";
  panel.replaceChildren();

  const header = document.createElement("div");
  header.className = "detail-header";
  const titleWrap = document.createElement("div");
  const kicker = document.createElement("span");
  kicker.className = "panel-kicker";
  kicker.textContent = "ENDPOINT DETAIL";
  const title = document.createElement("h3");
  title.textContent = endpoint.name;
  titleWrap.append(kicker, title);
  const actions = document.createElement("div");
  actions.className = "detail-actions";
  for (const action of endpoint.running ? ["restart", "stop"] : ["start"]) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `button tiny ${action === "stop" ? "danger" : "secondary"}`;
    button.textContent = action[0].toUpperCase() + action.slice(1);
    button.disabled = !state.status.web.allow_write;
    button.addEventListener("click", () => endpointAction(endpoint.name, action));
    actions.appendChild(button);
  }
  const settingsButton = document.createElement("button");
  settingsButton.type = "button";
  settingsButton.className = "button tiny primary";
  settingsButton.textContent = "Settings";
  settingsButton.addEventListener("click", () => activateTab("settings", endpoint.name));
  actions.appendChild(settingsButton);
  header.append(titleWrap, actions);

  const grid = document.createElement("div");
  grid.className = "detail-grid";
  grid.append(
    detailBlock("Activity", [
      ["Last RX", formatWhen(endpoint.last_rx_at)],
      ["Last TX", formatWhen(endpoint.last_tx_at)],
      ["Last client", endpoint.last_remote || "—"],
      ["Last duration", endpoint.last_duration_ms == null ? "—" : `${formatNumber(endpoint.last_duration_ms, 3)} ms`],
      ["Active / peak", `${endpoint.active_requests} / ${endpoint.peak_active_requests}`],
      ["Generation", String(endpoint.generation)],
    ]),
    detailBlock("Counters", [
      ["RX / TX", `${formatNumber(endpoint.metrics.received)} / ${formatNumber(endpoint.metrics.sent)}`],
      ["No response", formatNumber(endpoint.metrics.no_response)],
      ["Errors", formatNumber(endpoint.metrics.errors)],
      ["Faults", `${formatNumber(endpoint.metrics.fault_drops)} drop · ${formatNumber(endpoint.metrics.fault_corruptions)} corrupt · ${formatNumber(endpoint.metrics.fault_duplicates)} duplicate`],
      ["Latency avg / max", `${formatNumber(endpoint.average_latency_ms, 3)} / ${formatNumber(endpoint.max_latency_ms, 3)} ms`],
    ]),
    detailBlock("Last operation", [
      ["Request", endpoint.last_request_summary || "—"],
      ["Response", endpoint.last_response_summary || "—"],
      ["Request ID", endpoint.last_request_id || "—"],
      ["Last error", endpoint.last_error || "none"],
    ], "wide")
  );

  const protocol = document.createElement("div");
  protocol.className = "detail-code-block";
  const protocolTitle = document.createElement("strong");
  protocolTitle.textContent = "Protocol runtime state";
  const pre = document.createElement("pre");
  pre.textContent = JSON.stringify(endpoint.protocol_state || { state: "endpoint stopped" }, null, 2);
  protocol.append(protocolTitle, pre);

  const clients = document.createElement("div");
  clients.className = "detail-clients";
  const clientsTitle = document.createElement("strong");
  clientsTitle.textContent = `Recent clients (${endpoint.client_count})`;
  clients.appendChild(clientsTitle);
  if (endpoint.clients?.length) {
    const table = document.createElement("table");
    table.innerHTML = "<thead><tr><th>Remote</th><th class=\"numeric\">Requests</th><th class=\"numeric\">Bytes</th><th>Last seen</th></tr></thead>";
    const tbody = document.createElement("tbody");
    for (const client of endpoint.clients.slice(0, 12)) {
      const row = document.createElement("tr");
      for (const value of [client.remote, formatNumber(client.requests), formatBytes(client.bytes_received), formatWhen(client.last_seen_at)]) {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.appendChild(cell);
      }
      tbody.appendChild(row);
    }
    table.appendChild(tbody);
    clients.appendChild(table);
  } else {
    const empty = document.createElement("span");
    empty.className = "muted-text";
    empty.textContent = "No clients observed yet.";
    clients.appendChild(empty);
  }
  panel.append(header, grid, protocol, clients);
}

function detailBlock(title, rows, extraClass = "") {
  const block = document.createElement("dl");
  block.className = `detail-block ${extraClass}`.trim();
  const heading = document.createElement("div");
  heading.className = "detail-block-title";
  heading.textContent = title;
  block.appendChild(heading);
  for (const [name, value] of rows) {
    const row = document.createElement("div");
    const dt = document.createElement("dt");
    const dd = document.createElement("dd");
    dt.textContent = name;
    dd.textContent = value;
    row.append(dt, dd);
    block.appendChild(row);
  }
  return block;
}

function drawTrafficChart(canvas, history) {
  if (!canvas) return;
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const width = Math.max(320, rect.width || 640);
  const height = Math.max(170, rect.height || 220);
  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  const values = history.slice(-60);
  const max = Math.max(1, ...values.flatMap((item) => [Number(item.received || 0), Number(item.sent || 0)]));
  const padding = { left: 38, right: 12, top: 14, bottom: 25 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;

  ctx.strokeStyle = "rgba(139,176,211,.14)";
  ctx.fillStyle = "#587087";
  ctx.font = "11px ui-monospace, monospace";
  ctx.lineWidth = 1;
  for (let line = 0; line <= 4; line += 1) {
    const y = padding.top + chartHeight * line / 4;
    ctx.beginPath(); ctx.moveTo(padding.left, y); ctx.lineTo(width - padding.right, y); ctx.stroke();
    const label = Math.round(max * (1 - line / 4));
    ctx.fillText(String(label), 4, y + 4);
  }
  if (!values.length) return;

  const drawSeries = (key, color) => {
    ctx.beginPath();
    values.forEach((item, index) => {
      const x = padding.left + (values.length === 1 ? chartWidth : chartWidth * index / (values.length - 1));
      const y = padding.top + chartHeight - chartHeight * Number(item[key] || 0) / max;
      if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.stroke();
  };
  drawSeries("received", "#42d9ff");
  drawSeries("sent", "#56e39f");
  ctx.fillStyle = "#587087";
  ctx.fillText("60s ago", padding.left, height - 5);
  ctx.textAlign = "right";
  ctx.fillText("now", width - padding.right, height - 5);
  ctx.textAlign = "left";
}

function syncMemoryAreas(status) {
  const storage = $("#memoryStorage").value;
  const areas = storage === "word" ? status.memory.words : status.memory.bits;
  const select = $("#memoryArea");
  const current = select.value;
  const names = areas.map((item) => item.name);
  if (state.memoryAreasReady && names.includes(current) && select.options.length === names.length) return;
  select.replaceChildren();
  for (const area of areas) select.add(new Option(`${area.name} (${formatNumber(area.size)})`, area.name));
  if (names.includes(current)) select.value = current;
  state.memoryAreasReady = true;
}

function syncLogEndpointFilter(status) {
  const select = $("#logEndpointFilter");
  const current = select.value;
  const existing = Array.from(select.options).slice(1).map((option) => option.value);
  const names = status.endpoints.map((item) => item.name);
  if (existing.join("\0") === names.join("\0")) return;
  select.replaceChildren(new Option("All endpoints", ""));
  for (const name of names) select.add(new Option(name, name));
  if (names.includes(current)) select.value = current;
}

function activateTab(name, endpointName = null) {
  for (const item of $$(".tab-button")) item.classList.toggle("is-active", item.dataset.tab === name);
  for (const panel of $$(".tab-panel")) panel.classList.toggle("is-active", panel.id === `tab-${name}`);
  if (name === "memory" && !state.memory) loadMemory();
  if (name === "settings") {
    loadSettings().then(() => {
      if (endpointName) {
        const target = Array.from(document.querySelectorAll("[data-settings-endpoint]")).find((item) => item.dataset.settingsEndpoint === endpointName);
        target?.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    });
  }
}
