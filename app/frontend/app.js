const $ = (id) => document.getElementById(id);
const state = { config: null, mode: "a2a", scenario: null, running: false, started: 0, ended: 0, runId: "", traceId: "", responseIds: [], raw: [], texts: { coordinator: "", analyst: "" }, tools: new Map(), eventCount: 0, phase: "", terminal: false, timer: null };
const icon = (name) => `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const escapeHTML = (text) => String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const clock = (ms) => `${String(Math.floor(ms / 60000)).padStart(2, "0")}:${String(Math.floor(ms / 1000) % 60).padStart(2, "0")}`;
const pretty = (obj) => typeof obj === "string" ? obj : JSON.stringify(obj, null, 2);

// Model HTML is always escaped first; only these generated formatting tags survive.
export function markdown(text) {
  const blocks = [];
  let source = escapeHTML(text).replace(/```[^\n]*\n([\s\S]*?)(?:```|$)/g, (_, code) => `\n@@BLOCK${blocks.push(`<pre><code>${code.trimEnd()}</code></pre>`) - 1}@@\n`);
  const inline = (line) => {
    const codes = [];
    return line.replace(/`([^`]+)`/g, (_, code) => `@@CODE${codes.push(`<code>${code}</code>`) - 1}@@`)
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, label, url) => `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/__(.+?)__/g, "<strong>$1</strong>")
      .replace(/(^|[\s(])\*([^*\n]+)\*(?=$|[\s.,!?;)])/g, "$1<em>$2</em>")
      .replace(/@@CODE(\d+)@@/g, (_, index) => codes[Number(index)] || "");
  };
  const lines = source.split("\n");
  let html = "", list = null;
  const closeList = () => { if (list) { html += `</${list}>`; list = null; } };
  const tableCells = (line) => line.trim().replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^@@BLOCK\d+@@$/.test(line.trim())) { closeList(); html += blocks[Number(line.trim().match(/\d+/)[0])] || ""; continue; }
    if (i + 1 < lines.length && line.includes("|") && /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(lines[i + 1]) && lines[i + 1].includes("---")) {
      closeList();
      html += `<div class="table-wrap"><table><thead><tr>${tableCells(line).map((cell) => `<th>${inline(cell)}</th>`).join("")}</tr></thead><tbody>`;
      i += 2;
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) {
        html += `<tr>${tableCells(lines[i]).map((cell) => `<td>${inline(cell)}</td>`).join("")}</tr>`; i++;
      }
      html += "</tbody></table></div>"; i--; continue;
    }
    if (!line.trim()) { closeList(); continue; }
    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading) { closeList(); html += `<h${heading[1].length}>${inline(heading[2])}</h${heading[1].length}>`; continue; }
    const bullet = line.match(/^\s*([-*+]|\d+\.)\s+(.+)$/);
    if (bullet) { const type = /^\d/.test(bullet[1]) ? "ol" : "ul"; if (list !== type) { closeList(); list = type; html += `<${type}>`; } html += `<li>${inline(bullet[2])}</li>`; continue; }
    closeList();
    if (/^([-*_])\1{2,}$/.test(line.trim())) html += "<hr>";
    else if (line.startsWith("&gt; ")) html += `<blockquote>${inline(line.slice(5))}</blockquote>`;
    else html += `<p>${inline(line)}</p>`;
  }
  closeList();
  return html;
}

function toast(message) { $("toast").textContent = message; $("toast").classList.add("visible"); setTimeout(() => $("toast").classList.remove("visible"), 2300); }
async function copy(text) { try { await navigator.clipboard.writeText(text); toast("Copied to clipboard"); } catch { toast("Clipboard unavailable in this browser"); } }
function setLink(id, url) { const node = $(id); if (url && /^https:\/\//.test(url)) node.href = url; else node.removeAttribute("href"); }

function chooseScenario(scenario) {
  state.scenario = scenario.id; $("prompt").value = scenario.prompt;
  $("run-button").disabled = state.running || !state.config || !scenario.prompt.trim();
  document.querySelectorAll(".scenario-card").forEach((card) => { const selected = card.dataset.scenario === scenario.id; card.classList.toggle("selected", selected); card.setAttribute("aria-pressed", selected); });
}

function setMode(mode) {
  if (state.running) return;
  state.mode = mode;
  document.querySelectorAll("[data-mode]").forEach((button) => { const selected = button.dataset.mode === mode; button.classList.toggle("selected", selected); button.setAttribute("aria-pressed", selected); });
  $("edge-label").textContent = mode === "a2a" ? "via A2A" : "via backend";
  $("mode-explainer").textContent = mode === "a2a" ? "Foundry-native A2A · Coordinator delegates directly to the Analyst." : "Backend relay · Coordinator brief → Analyst → final recommendation.";
}

async function health() {
  $("connection-label").textContent = "Checking agents";
  try {
    const response = await fetch("/api/health");
    if (!response.ok) throw new Error(`Health check HTTP ${response.status}`);
    const data = await response.json(), ok = data.status === "ok";
    $("connection").className = `connection ${ok ? "ok" : "error"}`;
    $("connection-label").textContent = ok ? "Agents connected" : state.config?.configured ? "Foundry unavailable" : "Setup required";
    $("connection").title = Object.entries(data.agents || {}).map(([name, agent]) => `${name}: ${agent.message}`).join("\n") || data.status;
  } catch (error) {
    $("connection").className = "connection error"; $("connection-label").textContent = "Connection unavailable"; $("connection").title = error.message;
  }
}

function setAgent(agent, status, detail = "") {
  if (!["coordinator", "analyst"].includes(agent)) return;
  const labels = { idle: "Standby", thinking: "Thinking", calling_tool: "Using tools", waiting: "Waiting", done: "Complete", error: "Unavailable" };
  $(`agent-${agent}`).dataset.status = status;
  $(`status-${agent}`).textContent = labels[status] || status;
  if (detail) { $(`detail-${agent}`).textContent = detail; $(`detail-${agent}`).title = detail; }
}

function addTimeline({ title, agent = "system", type = "clock", detail = "", data = null, running = false, extraClass = "" }) {
  $("timeline-empty")?.remove();
  const entry = document.createElement("article");
  entry.className = `timeline-card ${running ? "running" : ""} ${extraClass}`;
  entry.dataset.started = Date.now();
  const agentName = { coordinator: "COORDINATOR", analyst: "LOGISTICS ANALYST", system: "CONTROL TOWER" }[agent] || "CONTROL TOWER";
  const time = new Date().toLocaleTimeString([], { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
  entry.innerHTML = `<span class="timeline-marker">${icon(type)}</span><div class="timeline-card-head"><span class="timeline-agent">${agentName}</span><span>${time}</span></div><h4>${escapeHTML(title)}</h4>${detail ? `<p>${escapeHTML(detail)}</p>` : ""}${data !== null ? `<details><summary>Inspect details</summary><pre>${escapeHTML(pretty(data))}</pre></details>` : ""}<span class="timeline-duration ${running ? "live" : ""}">${running ? "Running · 0s" : ""}</span>`;
  const timeline = $("timeline"), nearBottom = timeline.scrollHeight - timeline.scrollTop - timeline.clientHeight < 90;
  timeline.append(entry); state.eventCount++; $("event-count").textContent = String(state.eventCount).padStart(2, "0");
  if (nearBottom || state.eventCount < 5) timeline.scrollTop = timeline.scrollHeight;
  return entry;
}

function toolInfo(data) {
  const names = { file_search: ["Searching Caldova policies (File Search)", "file"], a2a: ["Delegating to Logistics Analyst via A2A", "link"], web_search: ["Searching the web", "globe"], mcp: [`MCP: ${data.name}`, "link"], function: [`ERP: ${data.name}`, "box"], code_interpreter: ["Running code analysis", "code"] };
  return names[data.tool_type] || [data.name || "Using a tool", "spark"];
}

function toolChips(agent, type, status) {
  document.querySelectorAll(`#agent-${agent} [data-tool]`).forEach((chip) => {
    if (!chip.dataset.tool.split(" ").includes(type)) return;
    chip.classList.toggle("active", status === "active");
    if (status === "completed") chip.classList.add("completed");
  });
}

function renderText(agent) {
  const text = state.texts[agent];
  if (agent === "analyst") {
    $("analyst-report").hidden = !text;
    $("analyst-text").innerHTML = markdown(text);
  } else if (state.phase !== "brief") {
    $("recommendation-empty").hidden = !!text;
    $("recommendation-text").hidden = !text;
    $("recommendation-text").innerHTML = markdown(text);
    $("copy-recommendation").disabled = !text;
  }
}

function updateRaw() {
  $("raw-count").textContent = state.raw.length;
  if (!$("debug-drawer").hidden) $("debug-content").textContent = state.raw.map((item) => JSON.stringify(item, null, 2)).join("\n\n");
}

function receive(type, data) {
  switch (type) {
    case "raw":
      state.raw.push(data); if (state.raw.length > 300) state.raw.shift(); updateRaw(); break;
    case "run.started":
      state.runId = data.run_id; state.traceId = data.trace_id;
      $("run-id").textContent = data.run_id; $("trace-id").textContent = data.trace_id; $("copy-trace").disabled = !data.trace_id;
      $("flow-state").innerHTML = '<span class="status-dot"></span>Agents at work'; $("flow-state").classList.add("active");
      $("recommendation-state").textContent = "Connecting the dots";
      addTimeline({ title: "Disruption received", detail: `${state.mode === "a2a" ? "Foundry-native A2A" : "Backend-orchestrated"} response started.`, type: "spark" }); break;
    case "agent.status":
      setAgent(data.agent, data.status, data.detail);
      if (data.agent === "coordinator" && data.detail?.startsWith("Preparing delegation brief")) state.phase = "brief";
      if (data.agent === "coordinator" && data.detail?.startsWith("Synthesizing final")) { state.phase = "final"; state.texts.coordinator = ""; renderText("coordinator"); $("handoff-edge").classList.remove("active"); }
      if (data.status === "thinking") {
        $("flow-state").innerHTML = `<span class="status-dot"></span>${data.agent === "analyst" ? "Analyst investigating" : "Coordinator thinking"}`;
        if (data.agent === "analyst" && state.mode === "a2a") $("timeline-state").textContent = "A2A delegation in progress";
      }
      break;
    case "tool.started": {
      const [title, typeIcon] = toolInfo(data), key = `${data.agent}:${data.call_id}`;
      if (state.tools.has(key)) break;
      const entry = addTimeline({ title, agent: data.agent, type: typeIcon, data: data.arguments ?? {}, running: true });
      state.tools.set(key, { entry, data }); toolChips(data.agent, data.tool_type, "active"); break;
    }
    case "tool.completed": {
      const key = `${data.agent}:${data.call_id}`, record = state.tools.get(key);
      if (record) {
        record.entry.classList.remove("running"); record.entry.classList.toggle("error", data.status === "error");
        const duration = Math.max(0, Date.now() - Number(record.entry.dataset.started));
        record.entry.querySelector(".timeline-duration").className = "timeline-duration";
        record.entry.querySelector(".timeline-duration").textContent = `${data.status === "error" ? "Error" : "Completed"} · ${(duration / 1000).toFixed(1)}s`;
        const details = record.entry.querySelector("details");
        if (details) details.querySelector("pre").textContent = pretty({ arguments: record.data.arguments, output: data.output_preview, status: data.status });
        record.done = true;
      }
      const stillActive = [...state.tools.values()].some((tool) => !tool.done && tool.data.agent === data.agent && tool.data.tool_type === data.tool_type);
      toolChips(data.agent, data.tool_type, stillActive ? "active" : "completed");
      if (data.tool_type === "a2a") { $("handoff-edge").classList.remove("active"); $("timeline-state").textContent = "Delegation returned"; }
      break;
    }
    case "handoff":
      $("handoff-edge").classList.add("active"); $("edge-label").textContent = data.via === "a2a" ? "via A2A" : "via backend";
      addTimeline({ title: data.to === "analyst" ? `Analyst engaged · via ${data.via === "a2a" ? "A2A" : "backend"}` : "Analysis returned to Coordinator", agent: data.from, type: "link", detail: data.via === "a2a" ? "The Analyst is working remotely. Its report arrives when delegation completes." : data.message_preview, extraClass: "handoff" });
      break;
    case "text.delta":
      if (!["coordinator", "analyst"].includes(data.agent)) break;
      state.texts[data.agent] += data.delta || ""; renderText(data.agent);
      if (data.agent === "coordinator" && state.phase !== "brief") { $("recommendation-state").textContent = "Writing the recommendation"; $("recommendation-text").classList.add("streaming-cursor"); }
      break;
    case "agent.message":
      if (!["coordinator", "analyst"].includes(data.agent)) break;
      if (data.text) state.texts[data.agent] = data.text;
      if (data.response_id && !state.responseIds.includes(data.response_id)) state.responseIds.push(data.response_id);
      renderText(data.agent);
      if (data.agent === "analyst") { addTimeline({ title: "Analyst report ready", agent: "analyst", type: "check", detail: "Operational findings are ready for the Coordinator." }); $("handoff-edge").classList.remove("active"); }
      else if (state.phase !== "brief") $("recommendation-text").classList.remove("streaming-cursor");
      break;
    case "run.completed":
      state.terminal = true; state.texts.coordinator = data.final_text || state.texts.coordinator; state.phase = "final"; renderText("coordinator");
      state.responseIds = [...new Set([...state.responseIds, ...(data.response_ids || [])])];
      $("response-ids").textContent = state.responseIds.length ? `Responses: ${state.responseIds.join(" · ")}` : "No response IDs returned.";
      $("response-ids").title = state.responseIds.join("\n");
      $("run-metrics").textContent = `${(data.duration_ms / 1000).toFixed(1)}s / ${Number(data.usage?.total_tokens || 0).toLocaleString()} tokens`;
      $("run-metrics").title = pretty(data.usage || {});
      $("recommendation-state").textContent = "Recommendation ready"; $("flow-state").innerHTML = '<span class="status-dot"></span>Response complete';
      $("timeline-state").textContent = "A coordinated response, complete"; $("timeline-dot").style.background = "#0E7C66"; $("answer-timing").textContent = `Delivered in ${(data.duration_ms / 1000).toFixed(1)}s`;
      addTimeline({ title: "A clear way forward", type: "check", detail: "Recommendation complete. Every step is traceable." });
      finish(); break;
    case "run.error":
      state.terminal = true; showError(data.error || "The Foundry run failed."); break;
  }
}

function finish() {
  state.running = false; state.ended = Date.now(); clearInterval(state.timer);
  $("elapsed").textContent = clock(state.ended - state.started);
  $("run-button").disabled = !state.config || !$("prompt").value.trim();
  $("run-button").innerHTML = `${icon("spark")}<span>Find a way forward</span>${icon("arrow")}`;
  $("prompt").disabled = false; document.querySelectorAll("[data-mode],.scenario-card").forEach((node) => node.disabled = false);
  $("flow-state").classList.remove("active"); $("handoff-edge").classList.remove("active"); $("recommendation-text").classList.remove("streaming-cursor"); $("recommendation-text").setAttribute("aria-busy", "false");
  document.querySelectorAll(".tool-chips .active").forEach((chip) => chip.classList.remove("active"));
  for (const record of state.tools.values()) if (!record.done) { record.entry.classList.remove("running"); const counter = record.entry.querySelector(".timeline-duration"); counter.classList.remove("live"); counter.textContent = "Run ended"; }
}

function showError(message) {
  $("run-error").hidden = false; $("run-error").textContent = message;
  $("recommendation-empty").hidden = true; $("recommendation-state").textContent = "Unable to complete";
  $("flow-state").innerHTML = '<span class="status-dot"></span>Connection needs attention'; $("timeline-state").textContent = "Run stopped · details below";
  $("timeline-dot").style.background = "#C50F1F";
  for (const agent of ["coordinator", "analyst"]) if (!["done", "idle", "error"].includes($(`agent-${agent}`).dataset.status)) setAgent(agent, "error", "Run ended before a response was received");
  addTimeline({ title: "Foundry request unavailable", type: "alert", detail: message, extraClass: "error" });
  finish();
}

function reset() {
  state.started = Date.now(); state.ended = 0; state.runId = ""; state.traceId = ""; state.responseIds = []; state.raw = []; state.texts = { coordinator: "", analyst: "" }; state.tools.clear(); state.eventCount = 0; state.phase = ""; state.terminal = false;
  $("timeline").replaceChildren(); $("event-count").textContent = "00"; $("elapsed").textContent = "00:00"; $("timeline-state").textContent = "Connecting to Microsoft Foundry"; $("timeline-dot").style.background = "#0F6CBD";
  $("recommendation-empty").hidden = false; $("recommendation-empty").querySelector("h3").textContent = "Connecting the dots.";
  $("recommendation-empty").querySelector("p").innerHTML = "The agents are working through your disruption.<br>Follow their progress in the live trail.";
  $("recommendation-text").hidden = true; $("recommendation-text").innerHTML = ""; $("recommendation-text").setAttribute("aria-busy", "true");
  $("analyst-report").hidden = true; $("analyst-report").open = false; $("analyst-text").innerHTML = ""; $("run-error").hidden = true;
  $("run-id").textContent = "Starting…"; $("trace-id").textContent = "Awaiting trace"; $("run-metrics").textContent = "— / —"; $("response-ids").textContent = "Response IDs appear after a run."; $("copy-trace").disabled = true; $("copy-recommendation").disabled = true; $("answer-timing").textContent = "Human judgment. Agent-powered.";
  for (const agent of ["coordinator", "analyst"]) setAgent(agent, "idle", agent === "coordinator" ? "Policies → decisions" : "Signals → understanding");
  document.querySelectorAll(".tool-chips>span").forEach((chip) => chip.classList.remove("active", "completed")); updateRaw();
}

export async function consumeSSE(response, onEvent) {
  if (!response.body) throw new Error("This browser cannot read streaming responses.");
  const reader = response.body.getReader(), decoder = new TextDecoder();
  let buffer = "";
  const frame = (part) => {
    let name = "message"; const data = [];
    for (const line of part.split("\n")) {
      if (line.startsWith(":")) continue;
      if (line.startsWith("event:")) name = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
    }
    if (data.length) onEvent(name, JSON.parse(data.join("\n")));
  };
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
      buffer = buffer.replace(/\r\n/g, "\n");
      let boundary;
      while ((boundary = buffer.indexOf("\n\n")) !== -1) { frame(buffer.slice(0, boundary)); buffer = buffer.slice(boundary + 2); }
      if (done) { if (buffer.trim()) frame(buffer); break; }
    }
  } finally { reader.releaseLock(); }
}

async function run() {
  const message = $("prompt").value.trim(); if (!message || state.running) return;
  reset(); state.running = true; $("run-button").disabled = true; $("run-button").innerHTML = `${icon("clock")}<span>Agents are working…</span>`;
  $("prompt").disabled = true; document.querySelectorAll("[data-mode],.scenario-card").forEach((node) => node.disabled = true);
  state.timer = setInterval(() => {
    $("elapsed").textContent = clock(Date.now() - state.started);
    document.querySelectorAll(".timeline-card.running").forEach((entry) => { entry.querySelector(".timeline-duration").textContent = `Running · ${Math.floor((Date.now() - Number(entry.dataset.started)) / 1000)}s`; });
  }, 500);
  try {
    const response = await fetch("/api/run", { method: "POST", headers: { "Content-Type": "application/json", "Accept": "text/event-stream" }, body: JSON.stringify({ message, scenario_id: state.scenario, mode: state.mode }) });
    if (!response.ok) throw new Error(`The backend returned HTTP ${response.status}. ${await response.text()}`);
    await consumeSSE(response, receive);
    if (!state.terminal) throw new Error("The agent stream disconnected before completing. Check Foundry connectivity and try again.");
  } catch (error) { if (!state.terminal) { state.terminal = true; showError(error.message); } }
}

async function init() {
  document.querySelectorAll("[data-mode]").forEach((button) => button.addEventListener("click", () => setMode(button.dataset.mode)));
  $("run-button").addEventListener("click", run); $("connection").addEventListener("click", health);
  $("prompt").addEventListener("input", () => { state.scenario = null; document.querySelectorAll(".scenario-card").forEach((card) => { card.classList.remove("selected"); card.setAttribute("aria-pressed", "false"); }); $("run-button").disabled = !state.config || !$("prompt").value.trim(); });
  $("prompt").addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); run(); } });
  $("copy-trace").addEventListener("click", () => copy(state.traceId));
  $("copy-recommendation").addEventListener("click", () => copy(state.texts.coordinator));
  $("copy-debug").addEventListener("click", () => copy(JSON.stringify(state.raw, null, 2)));
  $("debug-toggle").addEventListener("click", () => { const open = $("debug-drawer").hidden; $("debug-drawer").hidden = !open; $("debug-toggle").setAttribute("aria-expanded", open); updateRaw(); });
  try {
    const response = await fetch("/api/config"); if (!response.ok) throw new Error(`Configuration HTTP ${response.status}`);
    state.config = await response.json();
    for (const scenario of state.config.scenarios || []) {
      const card = document.createElement("button"); card.className = "scenario-card"; card.dataset.scenario = scenario.id; card.setAttribute("aria-pressed", "false");
      card.innerHTML = `<span class="scenario-selected"></span><span class="scenario-tag">${escapeHTML(scenario.tag || "SCENARIO")}</span><span class="scenario-title">${escapeHTML(scenario.title)}${icon("arrow")}</span><span class="scenario-subtitle">${escapeHTML(scenario.subtitle || "")}</span>`;
      card.addEventListener("click", () => chooseScenario(scenario)); $("scenarios").append(card);
    }
    if (state.config.scenarios?.length) chooseScenario(state.config.scenarios[0]);
    $("coordinator-model").textContent = state.config.agents.coordinator.model; $("analyst-model").textContent = state.config.agents.analyst.model;
    $("agent-coordinator").title = `${state.config.agents.coordinator.name} · versions ${pretty(state.config.agents.coordinator.versions)}`;
    $("agent-analyst").title = `${state.config.agents.analyst.name} · version ${state.config.agents.analyst.version}`;
    setLink("foundry-link", state.config.links.foundry_portal); setLink("trace-link", state.config.links.foundry_portal); setLink("insights-link", state.config.links.app_insights);
    $("trace-link").title = state.config.links.foundry_traces_hint; $("run-button").disabled = !$("prompt").value.trim();
    await health();
  } catch (error) {
    $("connection").className = "connection error"; $("connection-label").textContent = "Backend unavailable";
    $("run-error").hidden = false; $("run-error").textContent = `Could not load configuration: ${error.message}`;
  }
}
init();
