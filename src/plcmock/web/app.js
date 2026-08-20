async function loadMemory() {
  const storage = $("#memoryStorage").value;
  const area = $("#memoryArea").value;
  const start = Math.max(0, Number.parseInt($("#memoryStart").value, 10) || 0);
  const count = Math.max(1, Number.parseInt($("#memoryCount").value, 10) || 32);
  if (!area) return showToast("Memory area is not available.", true);
  try {
    const payload = await api(`/api/memory?storage=${encodeURIComponent(storage)}&area=${encodeURIComponent(area)}&start=${start}&count=${count}`);
    state.memory = payload;
    state.dirty.clear();
    renderMemory(payload);
    updateDirtyState();
  } catch (error) {
    showToast(error.message, true);
  }
}

function renderMemory(payload) {
  const body = $("#memoryTableBody");
  body.replaceChildren();
  payload.values.forEach((value, offset) => {
    const address = payload.start + offset;
    const row = document.createElement("tr");
    const addressCell = document.createElement("td");
    addressCell.textContent = `${payload.area}${address}`;
    const decimalCell = document.createElement("td");
    decimalCell.className = "numeric";
    decimalCell.textContent = String(value);
    const hexCell = document.createElement("td");
    hexCell.textContent = payload.storage === "word" ? `0x${Number(value).toString(16).toUpperCase().padStart(4, "0")}` : (value ? "ON" : "OFF");
    const editorCell = document.createElement("td");

    if (payload.storage === "word") {
      const input = document.createElement("input");
      input.type = "text";
      input.value = String(value);
      input.dataset.address = String(address);
      input.dataset.original = String(value);
      input.setAttribute("aria-label", `${payload.area}${address} value`);
      input.addEventListener("input", () => markWordDirty(input));
      editorCell.appendChild(input);
    } else {
      const label = document.createElement("label");
      label.className = "bit-editor";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.checked = Boolean(value);
      input.dataset.address = String(address);
      input.dataset.original = value ? "1" : "0";
      input.addEventListener("change", () => markBitDirty(input));
      const text = document.createElement("span");
      text.textContent = input.checked ? "ON" : "OFF";
      input.addEventListener("change", () => { text.textContent = input.checked ? "ON" : "OFF"; });
      label.append(input, text);
      editorCell.appendChild(label);
    }
    row.append(addressCell, decimalCell, hexCell, editorCell);
    body.appendChild(row);
  });
  $("#memorySummary").textContent = `${payload.storage.toUpperCase()} ${payload.area} · ${payload.start} … ${payload.start + payload.count - 1} · ${payload.count} points`;
}

function parseWordInput(value) {
  const text = value.trim();
  if (!text) throw new Error("Value cannot be empty.");
  if (!/^(?:0x[0-9a-f]+|[0-9]+)$/i.test(text)) {
    throw new Error("Word value must be 0..65535 or 0x0000..0xFFFF.");
  }
  const number = /^0x/i.test(text) ? Number.parseInt(text.slice(2), 16) : Number.parseInt(text, 10);
  if (!Number.isInteger(number) || number < 0 || number > 0xffff) throw new Error("Word value must be 0..65535 or 0x0000..0xFFFF.");
  return number;
}

function markWordDirty(input) {
  const address = Number(input.dataset.address);
  try {
    const value = parseWordInput(input.value);
    if (value === Number(input.dataset.original)) {
      state.dirty.delete(address);
      input.classList.remove("is-dirty");
    } else {
      state.dirty.set(address, value);
      input.classList.add("is-dirty");
    }
  } catch {
    state.dirty.set(address, input.value);
    input.classList.add("is-dirty");
  }
  updateDirtyState();
}

function markBitDirty(input) {
  const address = Number(input.dataset.address);
  const value = input.checked ? 1 : 0;
  if (value === Number(input.dataset.original)) state.dirty.delete(address);
  else state.dirty.set(address, value);
  input.closest("tr").classList.toggle("is-dirty", state.dirty.has(address));
  updateDirtyState();
}

function updateDirtyState() {
  const count = state.dirty.size;
  const writable = Boolean(state.status?.web.allow_write);
  $("#dirtySummary").textContent = count ? `${count} pending change${count === 1 ? "" : "s"}` : "No pending changes";
  $("#writeMemoryButton").disabled = !writable || count === 0;
}

async function writeMemory() {
  if (!state.memory || !state.dirty.size) return;
  const items = [];
  try {
    for (const [address, rawValue] of state.dirty.entries()) {
      items.push({ address, value: state.memory.storage === "word" ? parseWordInput(String(rawValue)) : Number(rawValue) });
    }
  } catch (error) {
    return showToast(error.message, true);
  }
  try {
    await api("/api/memory", {
      method: "PUT",
      body: JSON.stringify({ storage: state.memory.storage, area: state.memory.area, items }),
    });
    showToast(`${items.length} memory cell${items.length === 1 ? "" : "s"} written.`);
    await loadMemory();
  } catch (error) {
    showToast(error.message, true);
  }
}

function resetLogs() {
  state.lastLogId = 0;
  state.logCount = 0;
  const consoleElement = $("#logConsole");
  consoleElement.replaceChildren();
  const empty = document.createElement("div");
  empty.className = "log-empty";
  empty.textContent = "Waiting for matching records…";
  consoleElement.appendChild(empty);
  updateLogFooter();
}

async function refreshLogs() {
  if (state.logsPaused) return;
  const endpoint = $("#logEndpointFilter").value;
  const level = $("#logLevelFilter").value;
  const search = $("#logSearchFilter").value.trim();
  const params = new URLSearchParams({ after: String(state.lastLogId), limit: "250", level });
  if (endpoint) params.set("endpoint", endpoint);
  if (search) params.set("search", search);
  try {
    const payload = await api(`/api/logs?${params}`);
    if (payload.records.length) appendLogs(payload.records);
    state.lastLogId = Math.max(state.lastLogId, Number(payload.next_after || payload.latest_id || 0));
    $("#logBufferLabel").textContent = `buffer ${formatNumber(payload.capacity)} · latest #${formatNumber(payload.latest_id)}`;
  } catch {
    // Status polling owns the visible online/offline indicator.
  }
}

function appendLogs(records) {
  const consoleElement = $("#logConsole");
  consoleElement.querySelector(".log-empty")?.remove();
  for (const record of records) {
    const line = document.createElement("div");
    line.className = `log-line level-${record.level}`;
    const time = document.createElement("span");
    time.className = "log-time";
    const date = new Date(record.timestamp);
    time.textContent = Number.isNaN(date.getTime()) ? "--:--:--" : date.toLocaleTimeString("ja-JP", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit", fractionalSecondDigits: 3 });
    const level = document.createElement("span");
    level.className = "log-level";
    level.textContent = record.level;
    const endpoint = document.createElement("span");
    endpoint.className = "log-endpoint";
    endpoint.textContent = record.endpoint || record.logger;
    const message = document.createElement("span");
    message.className = "log-message";
    const request = record.request_id ? `[${record.request_id}] ` : "";
    message.textContent = `${request}${record.message}`;
    line.append(time, level, endpoint, message);
    consoleElement.appendChild(line);
    state.logCount += 1;
  }
  while (consoleElement.children.length > 600) {
    consoleElement.firstElementChild?.remove();
    state.logCount = Math.max(0, state.logCount - 1);
  }
  if ($("#autoScrollToggle").checked) consoleElement.scrollTop = consoleElement.scrollHeight;
  updateLogFooter();
}

function updateLogFooter() {
  $("#logCountLabel").textContent = `${formatNumber(state.logCount)} records shown${state.logsPaused ? " · paused" : ""}`;
}

async function clearLogs() {
  try {
    await api("/api/logs/clear", { method: "POST", body: "{}" });
    resetLogs();
    showToast("Dashboard log buffer cleared.");
  } catch (error) {
    showToast(error.message, true);
  }
}

async function changeLogMode() {
  const mode = $("#logModeSelect").value;
  try {
    const response = await api("/api/logging", { method: "POST", body: JSON.stringify({ mode }) });
    showToast(`Logging switched to ${response.logging.mode}.`);
    await refreshStatus();
  } catch (error) {
    showToast(error.message, true);
  }
}

function initTabs() {
  for (const button of $$(".tab-button")) {
    button.addEventListener("click", () => {
      for (const item of $$(".tab-button")) item.classList.toggle("is-active", item === button);
      for (const panel of $$(".tab-panel")) panel.classList.toggle("is-active", panel.id === `tab-${button.dataset.tab}`);
      if (button.dataset.tab === "memory" && !state.memory) loadMemory();
    });
  }
}

function initEvents() {
  $("#refreshStatusButton").addEventListener("click", () => refreshStatus(true));
  $("#logModeSelect").addEventListener("change", changeLogMode);
  $("#memoryStorage").addEventListener("change", () => {
    state.memoryAreasReady = false;
    if (state.status) syncMemoryAreas(state.status);
    state.memory = null;
    state.dirty.clear();
    updateDirtyState();
    loadMemory();
  });
  $("#loadMemoryButton").addEventListener("click", loadMemory);
  $("#writeMemoryButton").addEventListener("click", writeMemory);
  $("#pauseLogsButton").addEventListener("click", () => {
    state.logsPaused = !state.logsPaused;
    $("#pauseLogsButton").textContent = state.logsPaused ? "Resume" : "Pause";
    updateLogFooter();
  });
  $("#clearLogsButton").addEventListener("click", clearLogs);
  for (const selector of ["#logEndpointFilter", "#logLevelFilter"]) {
    $(selector).addEventListener("change", resetLogs);
  }
  let searchTimer;
  $("#logSearchFilter").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(resetLogs, 250);
  });
}

async function boot() {
  initTabs();
  initEvents();
  await refreshStatus(true);
  if (state.status) {
    state.memoryAreasReady = false;
    syncMemoryAreas(state.status);
  }
  await refreshLogs();
  setInterval(() => refreshStatus(false), 2000);
  setInterval(refreshLogs, 750);
}

document.addEventListener("DOMContentLoaded", boot);
