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
  for (const f of def.fields) {
    const lbl = document.createElement("label");
    lbl.innerHTML = `<span>${f.name}${f.unit ? " ("+f.unit+")" : ""}</span>`;
    let inp;
    if (f.type.startsWith && f.type.startsWith("enum{")) {
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
    if (f.default !== undefined) inp.value = JSON.stringify(f.default);
    lbl.appendChild(inp); root.appendChild(lbl);
  }
}

function collectFields() {
  const t = $("frameType").value;
  const def = schema.frames[t]; const out = {};
  for (const f of def.fields) {
    const v = $("f_" + f.name).value.trim();
    if (f.type === "int") out[f.name] = parseInt(v, 10);
    else if (f.type === "float") out[f.name] = parseFloat(v);
    else if (f.type.startsWith("list[")) out[f.name] = v.split(/[,\s]+/).filter(Boolean).map(Number);
    else out[f.name] = v;
  }
  return out;
}

async function onBuild() {
  const t = $("frameType").value; const fields = collectFields();
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
  let txt = "type: " + (parsed && parsed.type) + "\n";
  if (parsed && parsed.fields) for (const [k, v] of Object.entries(parsed.fields)) txt += `  ${k}: ${JSON.stringify(v)}\n`;
  $("parsed").textContent = txt;
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
