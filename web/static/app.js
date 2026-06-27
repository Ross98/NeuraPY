"use strict";
const $ = (id) => document.getElementById(id);
let schema = null;

async function init() {
  schema = await (await fetch("/api/schema")).json();
  const sel = $("frameType");
  for (const [type, def] of Object.entries(schema.frames)) {
    const opt = document.createElement("option");
    opt.value = type; opt.textContent = def.label || type;
    sel.appendChild(opt);
  }
  sel.addEventListener("change", renderFields);
  $("buildBtn").addEventListener("click", onBuild);
  $("sendBtn").addEventListener("click", onSend);
  $("sendBuildBtn").addEventListener("click", async () => { await onBuild(); await onSend(); });
  renderFields();
  startSSE();
  $("status").textContent = "live"; $("status").classList.add("ok");
}

function renderFields() {
  const t = $("frameType").value;
  const def = schema.frames[t];
  const root = $("fields"); root.innerHTML = "";
  if (!def) return;
  // Direction badge above the fields (camera_to_robot / robot_to_camera)
  const dir = def.direction;
  if (dir) {
    const badge = document.createElement("div");
    badge.className = "dir-badge " + (dir === "camera_to_robot" ? "dir-out" : "dir-in");
    badge.textContent = dir === "camera_to_robot"
      ? "→ camera → robot (you build & send)"
      : "→ robot → camera (received only; build for simulation)";
    root.appendChild(badge);
  }
  for (const f of def.fields) {
    const lbl = document.createElement("label");
    lbl.innerHTML = `<span>${f.name}${f.unit ? " ("+f.unit+")" : ""}</span>`;
    let inp;
    if (f.type === "bytes") {
      inp = document.createElement("input"); inp.type = "text";
      inp.placeholder = "hex (e.g. 01020304)";
      if (f.length) {
        inp.maxLength = f.length * 2;
        lbl.querySelector("span").textContent += ` [${f.length}B]`;
      }
    } else if (f.type.startsWith && f.type.startsWith("enum{")) {
      inp = document.createElement("select");
      const opts = f.type.slice(5, -1).split(",").map(s => s.trim());
      const labels = f.labels || opts;
      for (let i = 0; i < opts.length; i++) {
        const o = document.createElement("option");
        o.value = opts[i]; o.textContent = labels[i] || opts[i];
        inp.appendChild(o);
      }
    } else if (f.type.startsWith("list[")) {
      inp = document.createElement("input"); inp.type = "text";
      inp.placeholder = "逗号或空格分隔";
    } else { inp = document.createElement("input"); inp.type = "text"; }
    inp.id = "f_" + f.name;
    if (f.default !== undefined) {
      inp.value = f.type === "bytes" && typeof f.default === "string"
        ? f.default
        : JSON.stringify(f.default);
    }
    lbl.appendChild(inp); root.appendChild(lbl);
  }
  updateSendButtonForDirection();
}

function updateSendButtonForDirection() {
  const t = $("frameType").value;
  const def = schema.frames[t];
  const btn = $("sendBtn"); const btnBuild = $("sendBuildBtn");
  if (!def) return;
  // robot_to_camera frames can't be sent to a fake_camera (they'd be
  // junk — the camera doesn't issue motion frames in this direction).
  const sendable = def.direction !== "robot_to_camera";
  [btn, btnBuild].forEach(b => { if (b) { b.disabled = !sendable; b.title = sendable ? "" : "this frame direction is robot→camera; build only, don't send"; }});
}

function collectFields() {
  const t = $("frameType").value;
  const def = schema.frames[t]; const out = {};
  for (const f of def.fields) {
    const raw = $("f_" + f.name).value.trim();
    if (f.type === "int") out[f.name] = parseInt(raw, 10);
    else if (f.type === "float") out[f.name] = parseFloat(raw);
    else if (f.type === "bytes") {
      // Accept either "01020304" or "01 02 03 04"; convert to int list
      // so the backend (which expects bytes/lists) accepts it uniformly.
      const hex = raw.replace(/\s+/g, "");
      if (hex.length % 2 !== 0) throw new Error(`${f.name}: odd hex length`);
      out[f.name] = Array.from({length: hex.length / 2}, (_, i) =>
        parseInt(hex.slice(i * 2, i * 2 + 2), 16));
    }
    else if (f.type.startsWith("list[")) out[f.name] = raw.split(/[,\s]+/).filter(Boolean).map(Number);
    else out[f.name] = raw;
  }
  return out;
}

async function onBuild() {
  const t = $("frameType").value; let fields;
  try { fields = collectFields(); }
  catch (e) { toast(e.message); return; }
  const r = await fetch("/api/build", {method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({type: t, fields})});
  const j = await r.json();
  if (!r.ok) { toast(j.error || "build failed"); return; }
  $("hexOut").value = j.hex; showFrame(j.hex, j.parsed);
}

async function onSend() {
  const hex = $("hexOut").value.trim().replace(/\s+/g, "");
  const r = await fetch("/api/send", {method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({target: $("sendTarget").value, hex})});
  const j = await r.json(); if (!r.ok) toast(j.error || "send failed");
}

function startSSE() {
  const es = new EventSource("/api/stream");
  es.onmessage = (e) => {
    const ev = JSON.parse(e.data);
    if (ev.kind === "frame_in" || ev.kind === "frame_out") {
      addLog(ev); showFrame(ev.data.raw_hex, ev.data.parsed);
    } else if (ev.kind === "state") updateStateBar(ev.data);
    else if (ev.kind === "error") toast(ev.data.msg);
    else if (ev.kind === "log") addLog({ts: ev.ts, kind: "log", src: ev.src, data: ev.data});
  };
  es.onerror = () => { $("status").textContent = "offline"; $("status").classList.remove("ok"); };
}

function showFrame(hex, parsed) {
  const bytes = []; const s = hex.replace(/\s+/g, "");
  for (let i = 0; i < s.length; i += 2) bytes.push(parseInt(s.slice(i, i+2), 16));
  const grid = $("hexGrid"); grid.innerHTML = "";
  for (const b of bytes) {
    const c = document.createElement("div"); c.className = "hex-cell";
    c.textContent = b.toString(16).padStart(2, "0").toUpperCase();
    grid.appendChild(c);
  }
  const lines = [];
  lines.push("type: " + (parsed && parsed.type));
  if (parsed && parsed.fields) {
    if (parsed.type === "motion_or_status") {
      // Backend returned both layouts; show each with a label.
      lines.push("  (motion interpretation):");
      if (parsed.fields.motion) {
        for (const [k, v] of Object.entries(parsed.fields.motion)) {
          lines.push(`    ${k}: ${formatVal(v)}`);
        }
      }
      lines.push("  (status interpretation):");
      if (parsed.fields.status) {
        for (const [k, v] of Object.entries(parsed.fields.status)) {
          lines.push(`    ${k}: ${formatVal(v)}`);
        }
      }
    } else {
      for (const [k, v] of Object.entries(parsed.fields)) lines.push(`  ${k}: ${formatVal(v)}`);
    }
  }
  $("parsed").textContent = lines.join("\n");
}

function formatVal(v) {
  if (v instanceof Uint8Array || v instanceof ArrayBuffer) {
    return `<bytes ${v.byteLength || v.length}B>`;
  }
  if (Array.isArray(v) && v.length > 0 && typeof v[0] === "number" && v.length <= 32) {
    // Heuristic: short numeric arrays might be bytes; show as hex if so.
    const allByte = v.every(x => Number.isInteger(x) && x >= 0 && x <= 255);
    if (allByte && v.length >= 4) return "[" + v.map(x => x.toString(16).padStart(2, "0")).join(" ") + "]";
    return JSON.stringify(v);
  }
  return JSON.stringify(v);
}

function addLog(ev) {
  const li = document.createElement("li");
  const dir = ev.kind === "frame_in" ? "in" : ev.kind === "frame_out" ? "out" :
              ev.kind === "error" ? "err" : "log";
  li.className = dir;
  const t = new Date(ev.ts * 1000).toLocaleTimeString();
  li.textContent = `${t}  ${ev.kind}  ${ev.src}  ${ev.data && ev.data.parsed ? ev.data.parsed.type : (ev.data && ev.data.msg || "")}`;
  const log = $("log"); log.insertBefore(li, log.firstChild);
  while (log.children.length > 200) log.removeChild(log.lastChild);
}

function updateStateBar(d) {
  const j = (d.joints_rad || []).map(v => v.toFixed(2)).join(" ");
  const t = (d.tcp || []).map(v => v.toFixed(1)).join(" ");
  $("state").textContent = `STATE  J=${j}  TCP=${t}  ${d.is_moving ? "moving" : "idle"}`;
}

function toast(msg) {
  const d = document.createElement("div"); d.className = "toast"; d.textContent = msg;
  $("toasts").appendChild(d); setTimeout(() => d.remove(), 3000);
}

init();
