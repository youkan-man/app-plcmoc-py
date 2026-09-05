"use strict";

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
    hexCell.textContent = payload.storage === "word"
      ? `0x${Number(value).toString(16).toUpperCase().padStart(4, "0")}`
      : (value ? "ON" : "OFF");
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
  if (!/^(?:0x[0-9a-f]+|[0-9]+)$/i.test(text)) throw new Error("Word value must be 0..65535 or 0x0000..0xFFFF.");
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
      items.push({
        address,
        value: state.memory.storage === "word" ? parseWordInput(String(rawValue)) : Number(rawValue),
      });
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
    time.textContent = Number.isNaN(date.getTime()) ? "--:--:--" : date.toLocaleTimeString("ja-JP", {
      hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit", fractionalSecondDigits: 3,
    });
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
    await Promise.all([refreshStatus(), loadSettings(false)]);
  } catch (error) {
    showToast(error.message, true);
    if (state.status) $("#logModeSelect").value = state.status.logging.mode;
  }
}

async function loadSettings(showError = true) {
  try {
    const settings = await api("/api/settings");
    state.settings = settings;
    renderSettings(settings);
    return settings;
  } catch (error) {
    if (showError) showToast(error.message, true);
    return null;
  }
}

function renderSettings(settings) {
  for (const item of document.querySelectorAll('datalist[id^="suggestions-"]')) item.remove();
  const notice = $("#settingsAccessNotice");
  notice.className = `health-banner ${settings.writable ? "is-warning" : "is-healthy"}`;
  notice.querySelector("strong").textContent = settings.writable ? "Runtime configuration is writable" : "Read-only dashboard";
  notice.querySelector("span:last-child").textContent = settings.writable
    ? "Applying endpoint settings validates the new protocol and restarts only that UDP endpoint. Export YAML to persist the current runtime configuration."
    : "Start without --no-web-write to enable endpoint, logging, and memory changes.";

  renderLoggingSettings(settings);
  $("#resetMetricsButton").disabled = !settings.writable;
  $("#applyLoggingButton").disabled = !settings.writable;

  const list = $("#endpointSettingsList");
  list.replaceChildren();
  for (const endpoint of settings.endpoints) list.appendChild(createEndpointSettingsCard(endpoint, settings.writable, settings.protocol_suggestions));
  if (!settings.endpoints.length) {
    const empty = document.createElement("article");
    empty.className = "panel loading-card";
    empty.textContent = "No endpoints configured.";
    list.appendChild(empty);
  }
}

function renderLoggingSettings(settings) {
  fillSelect($("#settingsLogMode"), settings.logging.modes, settings.logging.mode);
  fillSelect($("#settingsLogLevel"), settings.logging.levels, settings.logging.level);
  fillSelect($("#settingsTrafficLog"), settings.logging.traffic_modes, settings.logging.traffic);
  fillSelect($("#settingsMemoryLog"), settings.logging.memory_modes, settings.logging.memory);
  $("#settingsMaxHex").value = String(settings.logging.max_hex_bytes);
  for (const element of [$("#settingsLogMode"), $("#settingsLogLevel"), $("#settingsTrafficLog"), $("#settingsMemoryLog"), $("#settingsMaxHex")]) element.disabled = !settings.writable;
}

function fillSelect(select, values, selected) {
  const current = Array.from(select.options).map((item) => item.value);
  if (current.join("\0") !== values.join("\0")) {
    select.replaceChildren();
    for (const value of values) select.add(new Option(value, value));
  }
  select.value = selected;
}

function createEndpointSettingsCard(endpoint, writable, protocolSuggestions) {
  const card = document.createElement("article");
  card.className = "panel endpoint-settings-card";
  card.dataset.settingsEndpoint = endpoint.name;
  const status = endpoint.running ? "running" : endpoint.desired_running ? "error" : "stopped";
  const statusLabel = endpoint.running ? "RUNNING" : endpoint.desired_running ? "DOWN" : "STOPPED";

  const header = document.createElement("div");
  header.className = "settings-card-header";
  const titleWrap = document.createElement("div");
  const titleLine = document.createElement("div");
  titleLine.className = "settings-title-line";
  const title = document.createElement("h3");
  title.textContent = endpoint.name;
  const stateBadge = document.createElement("span");
  stateBadge.className = `endpoint-state ${status}`;
  stateBadge.textContent = statusLabel;
  titleLine.append(title, stateBadge);
  const subtitle = document.createElement("span");
  subtitle.className = "settings-subtitle";
  subtitle.textContent = `${endpoint.config.protocol} · generation ${endpoint.generation} · ${formatNumber(endpoint.rates.received_per_second, 2)} req/s`;
  titleWrap.append(titleLine, subtitle);
  const badges = document.createElement("div");
  if (endpoint.changed_from_startup) {
    const changed = document.createElement("span");
    changed.className = "badge warning";
    changed.textContent = "MODIFIED";
    badges.appendChild(changed);
  }
  if (endpoint.last_error) {
    const error = document.createElement("span");
    error.className = "badge danger";
    error.textContent = "ERROR";
    error.title = endpoint.last_error;
    badges.appendChild(error);
  }
  header.append(titleWrap, badges);

  const body = document.createElement("div");
  body.className = "settings-card-body";
  const connection = settingsSection("Endpoint", "Binding and protocol changes restart this endpoint.");
  connection.content.classList.add("settings-form", "endpoint-basic-form");
  connection.content.append(
    labeledControl("Desired state", checkboxControl("setting-running", endpoint.config.running)),
    labeledControl("Bind address", textControl("setting-bind", endpoint.config.bind)),
    labeledControl("UDP port", numberControl("setting-port", endpoint.config.port, 0, 65535)),
    labeledControl("Protocol", textControl("setting-protocol", endpoint.config.protocol, protocolSuggestions)),
  );

  const options = settingsSection("Protocol options", "Common fields are guided; the complete JSON remains editable below.");
  const optionFields = document.createElement("div");
  optionFields.className = "schema-fields";
  const grouped = groupSchema(endpoint.option_schema || []);
  for (const [group, fields] of Object.entries(grouped)) {
    const fieldset = document.createElement("fieldset");
    const legend = document.createElement("legend");
    legend.textContent = group;
    fieldset.appendChild(legend);
    const grid = document.createElement("div");
    grid.className = "settings-form schema-grid";
    for (const schema of fields) grid.appendChild(createSchemaField(schema, endpoint.config.options));
    fieldset.appendChild(grid);
    optionFields.appendChild(fieldset);
  }
  if (!endpoint.option_schema?.length) {
    const hint = document.createElement("p");
    hint.className = "muted-text";
    hint.textContent = "This custom protocol has no guided schema. Use the advanced JSON editor.";
    optionFields.appendChild(hint);
  }
  const advanced = document.createElement("details");
  advanced.className = "advanced-options";
  const summary = document.createElement("summary");
  summary.textContent = "Advanced options JSON";
  const textarea = document.createElement("textarea");
  textarea.className = "setting-options-json code-input";
  textarea.spellcheck = false;
  textarea.value = JSON.stringify(endpoint.config.options || {}, null, 2);
  advanced.append(summary, textarea);
  options.content.append(optionFields, advanced);

  const faults = settingsSection("Fault injection", "Rates are percentages. A seed makes random fault sequences reproducible.");
  faults.content.classList.add("settings-form", "fault-form");
  const fault = endpoint.config.faults || {};
  faults.content.append(
    labeledControl("Drop %", numberControl("fault-drop", Number(fault.drop_rate || 0) * 100, 0, 100, 0.01)),
    labeledControl("Duplicate %", numberControl("fault-duplicate", Number(fault.duplicate_rate || 0) * 100, 0, 100, 0.01)),
    labeledControl("Corrupt %", numberControl("fault-corrupt", Number(fault.corrupt_rate || 0) * 100, 0, 100, 0.01)),
    labeledControl("Delay min ms", numberControl("fault-delay-min", fault.delay_ms?.min || 0, 0, 3600000, 0.1)),
    labeledControl("Delay max ms", numberControl("fault-delay-max", fault.delay_ms?.max || 0, 0, 3600000, 0.1)),
    labeledControl("Seed", textControl("fault-seed", fault.seed == null ? "" : String(fault.seed))),
  );

  body.append(connection.section, options.section, faults.section);

  const footer = document.createElement("div");
  footer.className = "settings-card-footer";
  const primary = document.createElement("div");
  primary.className = "button-group";
  const apply = actionButton("Apply & restart", "primary", () => applyEndpointCard(card, endpoint.name));
  const toggle = actionButton(endpoint.running ? "Stop" : "Start", endpoint.running ? "danger" : "accent", () => endpointAction(endpoint.name, endpoint.running ? "stop" : "start"));
  const restart = actionButton("Restart", "secondary", () => endpointAction(endpoint.name, "restart"));
  primary.append(apply, toggle, restart);
  const secondary = document.createElement("div");
  secondary.className = "button-group";
  const reset = actionButton("Restore startup", "secondary", () => endpointAction(endpoint.name, "reset"));
  const resetStats = actionButton("Reset metrics", "secondary", () => endpointAction(endpoint.name, "reset-metrics"));
  secondary.append(reset, resetStats);
  footer.append(primary, secondary);

  for (const control of cardControls(body, footer)) control.disabled = !writable;
  card.append(header, body, footer);
  return card;
}

function cardControls(body, footer) {
  return [...body.querySelectorAll("input, select, textarea, button"), ...footer.querySelectorAll("button")];
}

function settingsSection(title, description) {
  const section = document.createElement("section");
  section.className = "settings-section";
  const heading = document.createElement("div");
  heading.className = "settings-section-heading";
  const strong = document.createElement("strong");
  strong.textContent = title;
  const span = document.createElement("span");
  span.textContent = description;
  heading.append(strong, span);
  const content = document.createElement("div");
  section.append(heading, content);
  return { section, content };
}

function labeledControl(labelText, control) {
  const label = document.createElement("label");
  const span = document.createElement("span");
  span.textContent = labelText;
  label.append(span, control);
  return label;
}

function textControl(className, value, suggestions = []) {
  const input = document.createElement("input");
  input.type = "text";
  input.className = className;
  input.value = value ?? "";
  if (suggestions.length) {
    const listId = `suggestions-${className}-${Math.random().toString(36).slice(2)}`;
    input.setAttribute("list", listId);
    const list = document.createElement("datalist");
    list.id = listId;
    for (const item of suggestions) list.appendChild(new Option(item, item));
    document.body.appendChild(list);
  }
  return input;
}

function numberControl(className, value, min, max, step = 1) {
  const input = document.createElement("input");
  input.type = "number";
  input.className = className;
  input.value = String(value ?? 0);
  input.min = String(min);
  input.max = String(max);
  input.step = String(step);
  return input;
}

function checkboxControl(className, checked) {
  const wrap = document.createElement("span");
  wrap.className = "switch-control";
  const input = document.createElement("input");
  input.type = "checkbox";
  input.className = className;
  input.checked = Boolean(checked);
  const slider = document.createElement("span");
  slider.className = "switch-track";
  wrap.append(input, slider);
  return wrap;
}

function groupSchema(schema) {
  return schema.reduce((result, field) => {
    const key = field.group || "Protocol";
    (result[key] ||= []).push(field);
    return result;
  }, {});
}

function createSchemaField(schema, options) {
  const label = document.createElement("label");
  label.className = "schema-field";
  label.dataset.path = schema.path;
  label.dataset.type = schema.type;
  label.dataset.nullable = schema.nullable ? "true" : "false";
  const title = document.createElement("span");
  title.textContent = schema.label;
  label.appendChild(title);
  const current = getPath(options, schema.path, schema.default);

  if (schema.type === "multi") {
    const group = document.createElement("span");
    group.className = "choice-group";
    for (const choice of schema.choices) {
      const item = document.createElement("label");
      item.className = "choice-item";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.value = choice;
      input.checked = Array.isArray(current) && current.includes(choice);
      const text = document.createElement("span");
      text.textContent = choice;
      item.append(input, text);
      group.appendChild(item);
    }
    label.appendChild(group);
  } else if (schema.type === "boolean") {
    label.appendChild(checkboxControl("", Boolean(current)));
  } else if (schema.type === "select") {
    const select = document.createElement("select");
    for (const choice of schema.choices) select.add(new Option(choice, choice));
    select.value = current;
    label.appendChild(select);
  } else if (schema.type === "integer") {
    label.appendChild(numberControl("", current, schema.min ?? -2147483648, schema.max ?? 2147483647));
  } else {
    const input = document.createElement("input");
    input.type = "text";
    input.value = Array.isArray(current) ? current.join(", ") : (current ?? "");
    label.appendChild(input);
  }
  return label;
}

function getPath(object, path, fallback) {
  let current = object;
  for (const part of path.split(".")) {
    if (!current || typeof current !== "object" || !(part in current)) return fallback;
    current = current[part];
  }
  return current;
}

function setPath(object, path, value) {
  const parts = path.split(".");
  let current = object;
  for (const part of parts.slice(0, -1)) {
    if (!current[part] || typeof current[part] !== "object" || Array.isArray(current[part])) current[part] = {};
    current = current[part];
  }
  current[parts[parts.length - 1]] = value;
}

function collectSchemaOptions(card, baseOptions) {
  let options;
  try {
    options = JSON.parse(card.querySelector(".setting-options-json").value || "{}");
  } catch (error) {
    throw new Error(`Options JSON is invalid: ${error.message}`);
  }
  if (!options || typeof options !== "object" || Array.isArray(options)) throw new Error("Options JSON must be an object.");
  for (const field of card.querySelectorAll(".schema-field")) {
    const type = field.dataset.type;
    let value;
    if (type === "multi") value = Array.from(field.querySelectorAll('input[type="checkbox"]:checked')).map((item) => item.value);
    else if (type === "boolean") value = field.querySelector('input[type="checkbox"]').checked;
    else if (type === "integer") {
      value = Number.parseInt(field.querySelector("input").value, 10);
      if (!Number.isInteger(value)) throw new Error(`${field.querySelector("span").textContent} must be an integer.`);
    } else if (type === "select") value = field.querySelector("select").value;
    else if (type === "integer-list") {
      value = splitList(field.querySelector("input").value).map((item) => {
        const number = /^0x/i.test(item) ? Number.parseInt(item.slice(2), 16) : Number.parseInt(item, 10);
        if (!Number.isInteger(number)) throw new Error(`${item} is not an integer.`);
        return number;
      });
    } else if (type === "string-list") value = splitList(field.querySelector("input").value);
    else value = field.querySelector("input").value;
    setPath(options, field.dataset.path, value);
  }
  return options;
}

function splitList(value) {
  return value.split(/[\s,]+/).map((item) => item.trim()).filter(Boolean);
}

function percent(card, selector, label) {
  const value = Number(card.querySelector(selector).value);
  if (!Number.isFinite(value) || value < 0 || value > 100) throw new Error(`${label} must be 0..100.`);
  return value / 100;
}

function nonNegative(card, selector, label) {
  const value = Number(card.querySelector(selector).value);
  if (!Number.isFinite(value) || value < 0) throw new Error(`${label} must be non-negative.`);
  return value;
}

function collectEndpointCard(card) {
  const delayMin = nonNegative(card, ".fault-delay-min", "Delay min");
  const delayMax = nonNegative(card, ".fault-delay-max", "Delay max");
  if (delayMax < delayMin) throw new Error("Delay max must be greater than or equal to delay min.");
  const seedText = card.querySelector(".fault-seed").value.trim();
  let seed = null;
  if (seedText) {
    seed = Number.parseInt(seedText, 10);
    if (!Number.isInteger(seed)) throw new Error("Fault seed must be an integer or empty.");
  }
  const port = Number.parseInt(card.querySelector(".setting-port").value, 10);
  if (!Number.isInteger(port) || port < 0 || port > 65535) throw new Error("UDP port must be 0..65535.");
  return {
    running: card.querySelector(".setting-running").checked,
    bind: card.querySelector(".setting-bind").value.trim(),
    port,
    protocol: card.querySelector(".setting-protocol").value.trim(),
    options: collectSchemaOptions(card, {}),
    faults: {
      seed,
      drop_rate: percent(card, ".fault-drop", "Drop rate"),
      duplicate_rate: percent(card, ".fault-duplicate", "Duplicate rate"),
      corrupt_rate: percent(card, ".fault-corrupt", "Corrupt rate"),
      delay_ms: { min: delayMin, max: delayMax },
    },
  };
}

async function applyEndpointCard(card, name) {
  let payload;
  try { payload = collectEndpointCard(card); }
  catch (error) { return showToast(error.message, true); }
  const button = card.querySelector(".button.primary");
  await withBusy(button, "Applying…", async () => {
    const result = await api(`/api/endpoints/${encodeURIComponent(name)}`, { method: "PUT", body: JSON.stringify(payload) });
    state.settings = result.settings;
    renderSettings(result.settings);
    await refreshStatus();
    showToast(`${name} configuration applied.`);
  });
}

async function endpointAction(name, action) {
  const label = `${action} ${name}`;
  try {
    const result = await api(`/api/endpoints/${encodeURIComponent(name)}/action`, {
      method: "POST",
      body: JSON.stringify({ action }),
    });
    state.settings = result.settings;
    renderSettings(result.settings);
    await refreshStatus();
    showToast(`${label} completed.`);
  } catch (error) {
    showToast(`${label}: ${error.message}`, true);
  }
}

async function applyLogging() {
  const button = $("#applyLoggingButton");
  await withBusy(button, "Applying…", async () => {
    const payload = {
      mode: $("#settingsLogMode").value,
      level: $("#settingsLogLevel").value,
      traffic: $("#settingsTrafficLog").value,
      memory: $("#settingsMemoryLog").value,
      max_hex_bytes: Number.parseInt($("#settingsMaxHex").value, 10),
    };
    const result = await api("/api/logging", { method: "POST", body: JSON.stringify(payload) });
    showToast(`Logging applied: ${result.logging.mode}/${result.logging.level}.`);
    await Promise.all([refreshStatus(), loadSettings(false)]);
  });
}

async function resetMetrics() {
  if (!confirm("Reset all endpoint counters and recent-client telemetry?")) return;
  try {
    await api("/api/metrics/reset", { method: "POST", body: "{}" });
    await Promise.all([refreshStatus(), loadSettings(false)]);
    showToast("All endpoint counters were reset.");
  } catch (error) {
    showToast(error.message, true);
  }
}

async function withBusy(button, label, operation) {
  const old = button?.textContent;
  if (button) { button.disabled = true; button.textContent = label; }
  try { await operation(); }
  catch (error) { showToast(error.message, true); }
  finally { if (button) { button.disabled = false; button.textContent = old; } }
}

function actionButton(label, style, action) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `button ${style}`;
  button.textContent = label;
  button.addEventListener("click", action);
  return button;
}

function initTabs() {
  for (const button of $$(".tab-button")) button.addEventListener("click", () => activateTab(button.dataset.tab));
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
  for (const selector of ["#logEndpointFilter", "#logLevelFilter"]) $(selector).addEventListener("change", resetLogs);
  let searchTimer;
  $("#logSearchFilter").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(resetLogs, 250);
  });
  $("#reloadSettingsButton").addEventListener("click", () => loadSettings(true));
  $("#applyLoggingButton").addEventListener("click", applyLogging);
  $("#resetMetricsButton").addEventListener("click", resetMetrics);
  $("#settingsLogMode").addEventListener("change", () => {
    const presets = {
      quiet: ["WARNING", "off", "off"],
      normal: ["INFO", "summary", "off"],
      debug: ["DEBUG", "summary", "write"],
      trace: ["TRACE", "hex", "all"],
    };
    const preset = presets[$("#settingsLogMode").value];
    if (preset) {
      $("#settingsLogLevel").value = preset[0];
      $("#settingsTrafficLog").value = preset[1];
      $("#settingsMemoryLog").value = preset[2];
    }
  });
  window.addEventListener("resize", () => {
    if (state.status) drawTrafficChart($("#trafficChart"), state.status.history || []);
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
  await Promise.all([refreshLogs(), loadSettings(false)]);
  setInterval(() => refreshStatus(false), 2000);
  setInterval(refreshLogs, 750);
}

document.addEventListener("DOMContentLoaded", boot);
