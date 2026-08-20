"use strict";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

const state = {
  status: null,
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
  if (!response.ok) {
    throw new Error(payload?.error || `HTTP ${response.status}`);
  }
  return payload;
}

function showToast(message, isError = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.toggle("is-error", isError);
  toast.classList.add("is-visible");
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => toast.classList.remove("is-visible"), 2800);
}

function formatNumber(value) {
  return new Intl.NumberFormat("ja-JP").format(Number(value || 0));
}

function formatBytes(value) {
  let bytes = Number(value || 0);
  const units = ["B", "KiB", "MiB", "GiB"];
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
    setConnection(true);
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
  $("#metricUptime").textContent = formatUptime(status.uptime_seconds);
  $("#metricVersion").textContent = `version ${status.version}`;
  $("#metricReceived").textContent = formatNumber(metrics.received);
  $("#metricBytesIn").textContent = `${formatBytes(metrics.bytes_received)} received`;
  $("#metricSent").textContent = formatNumber(metrics.sent);
  $("#metricBytesOut").textContent = `${formatBytes(metrics.bytes_sent)} sent`;
  $("#metricProblems").textContent = `${formatNumber(metrics.no_response)} / ${formatNumber(metrics.errors)}`;
  const faultCount = Number(metrics.fault_drops || 0) + Number(metrics.fault_corruptions || 0) + Number(metrics.fault_duplicates || 0);
  $("#metricFaults").textContent = `fault events ${formatNumber(faultCount)}`;
  $("#endpointCount").textContent = String(status.endpoints.length);
  $("#runtimeConfig").textContent = status.config_source;
  $("#runtimeWeb").textContent = `${status.web.bind}:${status.web.port} · ${status.web.allow_write ? "read/write" : "read only"}`;
  $("#runtimeLogging").textContent = `${status.logging.mode} · ${status.logging.traffic} traffic · ${status.logging.memory} memory`;
  $("#runtimeMemory").textContent = `${status.memory.words.length} word areas · ${status.memory.bits.length} bit areas`;
  $("#logModeSelect").value = status.logging.mode;

  const body = $("#endpointTableBody");
  body.replaceChildren();
  for (const endpoint of status.endpoints) {
    const row = document.createElement("tr");
    const address = endpoint.running ? `${endpoint.bound_host}:${endpoint.bound_port}/udp` : `${endpoint.configured_bind}:${endpoint.configured_port}/udp`;
    row.innerHTML = `
      <td><span class="endpoint-state ${endpoint.running ? "running" : ""}">${endpoint.running ? "UP" : "DOWN"}</span></td>
      <td class="endpoint-name"></td>
      <td><span class="protocol-chip"></span></td>
      <td class="address-text"></td>
      <td class="numeric">${formatNumber(endpoint.metrics.received)}</td>
      <td class="numeric">${formatNumber(endpoint.metrics.sent)}</td>
      <td class="numeric">${formatNumber(endpoint.metrics.errors)}</td>`;
    row.children[1].textContent = endpoint.name;
    row.querySelector(".protocol-chip").textContent = endpoint.protocol;
    row.querySelector(".address-text").textContent = address;
    body.appendChild(row);
  }
  if (!status.endpoints.length) {
    body.innerHTML = '<tr><td colspan="7" class="empty-cell">No endpoints configured.</td></tr>';
  }

  const writeBadge = $("#memoryWriteBadge");
  writeBadge.textContent = status.web.allow_write ? "READ / WRITE" : "READ ONLY";
  writeBadge.className = `badge ${status.web.allow_write ? "success" : "warning"}`;
  $("#memoryCount").max = String(status.web.max_memory_points);
  $("#writeNotice").querySelector("strong").textContent = status.web.allow_write ? "Development interface" : "Read-only interface";
}

function syncMemoryAreas(status) {
  const storage = $("#memoryStorage").value;
  const areas = storage === "word" ? status.memory.words : status.memory.bits;
  const select = $("#memoryArea");
  const current = select.value;
  const names = areas.map((item) => item.name);
  if (state.memoryAreasReady && names.includes(current) && select.options.length === names.length) return;
  select.replaceChildren();
  for (const area of areas) {
    const option = document.createElement("option");
    option.value = area.name;
    option.textContent = `${area.name} (${formatNumber(area.size)})`;
    select.appendChild(option);
  }
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
