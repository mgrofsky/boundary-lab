"use strict";

// Add server text with textContent so the browser treats it as text, not HTML.

const token = document.querySelector('meta[name="latch-token"]').getAttribute("content");
let tools = [];
let labels = {};
let live = { on: false, model: null };

function byId(id) {
  return document.getElementById(id);
}

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) {
    node.textContent = String(text);
  }
  if (className) {
    node.className = className;
  }
  return node;
}

function clear(node) {
  while (node.firstChild) {
    node.removeChild(node.firstChild);
  }
}

function money(minor) {
  const whole = Math.floor(minor / 100);
  const cents = String(minor % 100).padStart(2, "0");
  return whole + "." + cents;
}

// Convert dollar amounts such as "60", "60.5", or "60.25" to whole cents.
function toMinor(text) {
  const match = /^(\d{1,9})(?:\.(\d{1,2}))?$/.exec(text.trim());
  if (!match) {
    throw new Error("Enter an amount such as 60 or 60.00.");
  }
  const cents = (match[2] || "").padEnd(2, "0");
  return Number(match[1]) * 100 + Number(cents);
}

async function readResponse(response) {
  let data = null;
  try {
    data = await response.json();
  } catch (error) {
    data = null;
  }
  if (!response.ok) {
    let detail = data && data.detail !== undefined ? data.detail : response.statusText;
    if (typeof detail !== "string") {
      detail = JSON.stringify(detail);
    }
    throw new Error(response.status + " " + detail);
  }
  return data;
}

async function getJSON(path) {
  return readResponse(await fetch(path, { headers: { Accept: "application/json" } }));
}

// fetch keeps its default cors mode, so the browser still sends the real Origin header
// even though the page sets no-referrer.
async function postJSON(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Latch-Token": token },
    body: JSON.stringify(body || {}),
  });
  return readResponse(response);
}

function profile() {
  const checked = document.querySelector('input[name="profile"]:checked');
  return checked ? checked.value : "sam";
}

function log(title, lines, kind) {
  const item = el("li", null, "entry" + (kind ? " " + kind : ""));
  const time = new Date().toLocaleTimeString();
  item.appendChild(el("div", time + "  " + title, "entry-title"));
  for (const line of lines) {
    item.appendChild(el("div", line, "entry-line"));
  }
  const list = byId("log");
  list.insertBefore(item, list.firstChild);
}

function codeKind(code) {
  if (code === "ALLOWED" || code === "APPROVED" || code === "EXECUTED") {
    return "good";
  }
  if (code === "NEEDS_APPROVAL" || code === "ALREADY_RECORDED") {
    return "wait";
  }
  return "bad";
}

function logResult(action, result) {
  const lines = ["result " + result.code + ": " + (result.explanation || "")];
  if (result.operation_id) {
    lines.push("operation " + result.operation_id);
  }
  if (result.replayed) {
    lines.push("Showing the saved record. The tool was not run again.");
  }
  log(labels[profile()] + ", " + action, lines, codeKind(result.code));
}

function logError(action, error) {
  log(action, [error.message], "bad");
}

function renderState(state) {
  byId("run-id").textContent = state.run_id;
  live = state.live || live;
  byId("live-banner").textContent = live.on
    ? "Live model: on (" + live.model + ")"
    : "Live model: off";
  byId("live-form").hidden = !live.on;

  const accounts = byId("accounts");
  clear(accounts);
  for (const [id, account] of Object.entries(state.accounts)) {
    const row = el("tr");
    row.appendChild(el("td", id));
    row.appendChild(el("td", account.owner));
    row.appendChild(el("td", money(account.balance_minor), "number"));
    row.appendChild(el("td", money(account.refundable_minor), "number"));
    accounts.appendChild(row);
  }

  const outbox = byId("outbox");
  clear(outbox);
  if (state.outbox.length === 0) {
    outbox.appendChild(el("li", "nothing sent", "note"));
  }
  for (const message of state.outbox) {
    const item = el("li");
    item.appendChild(el("div", "to " + message.to, "entry-title"));
    item.appendChild(el("div", message.body, "entry-line"));
    outbox.appendChild(item);
  }

  const refunds = byId("refunds");
  clear(refunds);
  const entries = Object.entries(state.refunds);
  if (entries.length === 0) {
    refunds.appendChild(el("li", "no refunds", "note"));
  }
  for (const [op, refund] of entries) {
    refunds.appendChild(el("li", refund.account_id + " " + money(refund.amount_minor) +
      ", operation " + op));
  }
}

async function refresh() {
  try {
    renderState(await getJSON("/api/state"));
  } catch (error) {
    logError("refresh state", error);
  }
}

function renderProfiles(people) {
  const box = byId("profiles");
  clear(box);
  people.forEach((person, index) => {
    labels[person.name] = person.label;
    const label = el("label", null, "choice");
    const input = el("input");
    input.type = "radio";
    input.name = "profile";
    input.value = person.name;
    input.checked = index === 0;
    label.appendChild(input);
    label.appendChild(el("span", person.label));
    box.appendChild(label);
  });
}

function fieldLabel(name) {
  return name.endsWith("_minor") ? "amount in dollars" : name.replace(/_/g, " ");
}

function renderFields() {
  const tool = tools.find((t) => t.name === byId("tool").value);
  const box = byId("fields");
  clear(box);
  if (!tool) {
    return;
  }
  byId("tool-description").textContent = tool.description;
  for (const name of Object.keys(tool.parameters.properties || {})) {
    const id = "field-" + name;
    const label = el("label", fieldLabel(name));
    label.htmlFor = id;
    const input = el(name === "body" ? "textarea" : "input");
    input.id = id;
    input.dataset.name = name;
    if (input.tagName === "INPUT") {
      input.type = "text";
      input.autocomplete = "off";
    }
    if (name.endsWith("_minor")) {
      input.inputMode = "decimal";
      input.placeholder = "60.00";
    }
    box.appendChild(label);
    box.appendChild(input);
  }
}

function renderTools(list) {
  tools = list;
  const select = byId("tool");
  clear(select);
  for (const tool of tools) {
    const option = el("option", tool.name);
    option.value = tool.name;
    select.appendChild(option);
  }
  renderFields();
}

function readArguments() {
  const args = {};
  for (const input of byId("fields").querySelectorAll("[data-name]")) {
    const name = input.dataset.name;
    args[name] = name.endsWith("_minor") ? toMinor(input.value) : input.value;
  }
  return args;
}

async function propose(event) {
  event.preventDefault();
  const tool = byId("tool").value;
  try {
    const result = await postJSON("/api/propose", {
      profile: profile(), tool: tool, arguments: readArguments(),
    });
    byId("operation-id").value = result.operation_id;
    logResult("propose " + tool, result);
  } catch (error) {
    logError("propose " + tool, error);
  }
  await refresh();
}

async function act(path, verb) {
  const op = byId("operation-id").value.trim();
  if (!op) {
    logError(verb, new Error("Click Propose first, or paste an operation ID."));
    return;
  }
  try {
    logResult(verb + " " + op, await postJSON(path, { profile: profile(), operation_id: op }));
  } catch (error) {
    logError(verb + " " + op, error);
  }
  await refresh();
}

async function playScenario(name) {
  try {
    const result = await postJSON("/api/scenario/" + encodeURIComponent(name));
    const lines = [];
    for (const step of result.steps) {
      lines.push("who: " + step.actor);
      lines.push("asked for: " + step.action);
      lines.push("result " + step.code + ": " + step.explanation);
      lines.push("change: " + step.state_change);
      lines.push("");
    }
    const verdict = result.passed ? "matched" : "expected " + result.expected.join(" ");
    lines.push("codes " + result.observed.join(" ") + ", " + verdict);
    log("scenario " + result.title + " (" + result.name + ")", lines,
      result.passed ? "good" : "bad");
  } catch (error) {
    logError("scenario " + name, error);
  }
  await refresh();
}

function renderScenarios(list) {
  const box = byId("scenarios");
  clear(box);
  for (const scenario of list) {
    const button = el("button", scenario.name);
    button.type = "button";
    button.addEventListener("click", () => playScenario(scenario.name));
    box.appendChild(button);
  }
}

async function liveTurn(event) {
  event.preventDefault();
  const text = byId("live-text").value.trim();
  if (!text) {
    return;
  }
  const send = byId("live-send");
  send.disabled = true;
  try {
    const result = await postJSON("/api/live/turn", { profile: profile(), text: text });
    const lines = result.events.map((e) => {
      if (e.event === "assistant") {
        return "assistant: " + e.text;
      }
      if (e.event === "tool") {
        return "tool " + e.tool + " " + e.code + ", operation " + e.operation_id;
      }
      return "limit " + e.code + ", this turn reached the model request limit";
    });
    log(labels[profile()] + ", asked the model: " + text, lines);
    const held = result.events.filter((e) => e.code === "NEEDS_APPROVAL").pop();
    if (held) {
      byId("operation-id").value = held.operation_id;
    }
  } catch (error) {
    logError("live turn", error);
  }
  send.disabled = false;
  await refresh();
}

async function start() {
  byId("tool-form").addEventListener("submit", propose);
  byId("tool").addEventListener("change", renderFields);
  byId("approve").addEventListener("click", () => act("/api/approve", "approve"));
  byId("deny").addEventListener("click", () => act("/api/deny", "deny"));
  byId("run").addEventListener("click", () => act("/api/run", "run"));
  byId("live-form").addEventListener("submit", liveTurn);
  byId("clear-log").addEventListener("click", () => clear(byId("log")));
  try {
    const data = await getJSON("/api/profiles");
    renderProfiles(data.profiles);
    renderTools(data.tools);
    renderScenarios((await getJSON("/api/scenarios")).scenarios);
  } catch (error) {
    logError("load the demo", error);
  }
  await refresh();
}

start();
