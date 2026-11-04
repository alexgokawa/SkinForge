"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const base = (p) => String(p || "").split(/[\\/]/).pop();

let S = null;            // server state
let sel = null;          // selected slot key
let mode = "paint";
let previewNone = false;  // preview only: checkerboard instead of paint (shows what is transparent)
const slotColor = (k) => (S.project.slot_colors || {})[k] || null;      // this template's own base colour
const paintOf = (k) => slotColor(k) || S.project.base_color;
let pickTarget = null;   // {key, ovr} or {icon}
let artBust = 0;

// ---------------------------------------------------------------- api
async function api(path, body, raw) {
  const opt = body === undefined ? {} : { method: "POST", body: raw ? body : JSON.stringify(body), headers: raw ? {} : { "Content-Type": "application/json" } };
  const r = await fetch(path, opt);
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.error) throw new Error(j.error || r.statusText);
  return j;
}
function toast(msg, err) {
  const t = $("#toast");
  t.textContent = msg; t.className = "toast" + (err ? " err" : ""); t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), err ? 6000 : 2600);
}
async function guard(fn) { try { return await fn(); } catch (e) { toast(e.message, true); logUi("error shown: " + e.message); } }
// errors in the window go to SkinForge's log file too (Mod tab > Problems)
function logUi(msg) { try { fetch("/api/log", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ msg: String(msg).slice(0, 4000) }) }); } catch (_) {} }
window.addEventListener("error", (e) => logUi(`JS error: ${e.message} at ${(e.filename || "").split("/").pop()}:${e.lineno}:${e.colno}${e.error && e.error.stack ? "\n" + e.error.stack : ""}`));
window.addEventListener("unhandledrejection", (e) => logUi(`JS promise: ${e.reason && (e.reason.stack || e.reason.message) || e.reason}`));

async function refresh() {
  S = await api("/api/state");
  if (!opened) opened = new Set(S.ui_open || []);
  const all = slots();
  if (!sel || !all.find((s) => s.key === sel)) sel = all.length ? all[0].key : null;
  render();
  if (!S.busy && (S.ai_check || (S.official || {}).busy)) {   // background AI template check: the list shrinks as it goes
    clearTimeout(refresh._a);
    refresh._a = setTimeout(() => refresh().catch(() => {}), 4000);
  }
  if (S.busy) {   // game files still being read (app start): check again shortly
    clearTimeout(refresh._t);
    refresh._t = setTimeout(() => refresh().then(() => { if (!S.busy) fit(); }).catch(() => {}), 1200);
  }
}
const slots = () => (S?.trailers || []).flatMap((t) => t.slots);
const slotOf = (k) => slots().find((s) => s.key === k);
const cfgOf = (k) => S.project.slots[k] || {};
const trailerOf = (k) => S.trailers.find((t) => t.slots.some((s) => s.key === k));

// ---------------------------------------------------------------- render
function render() {
  $("#ver").textContent = "v" + S.version;
  $("#projName").textContent = base(S.project_dir) || "Untitled";
  renderGame(); renderSkins(); renderSources(); renderTrailers(); renderStage(); renderSlotTab(); renderPaintTab(); renderModTab(); renderBuild(); renderRecent();
}

function renderRecent() {
  $("#recentList").innerHTML = (S.recent || []).filter((r) => r !== S.project_file).map((r) =>
    `<button class="recent" data-path="${esc(r)}">${esc(base(r.replace(/[\\/]project\.json$/i, "")))}<small>${esc(r)}</small></button>`).join("") || `<div class="menu-label">-</div>`;
}

function renderGame() {
  $$("#gameSeg [data-game]").forEach((b) => b.classList.toggle("on", b.dataset.game === (S.project.game || "ats")));
}

function renderSkins() {
  const p = S.project, live = p.skins.filter((x) => !x.deleted);
  $("#skinList").innerHTML = live.map((x) => `<div class="src skin ${x.id === p.active_skin ? "sel" : ""}" data-skin="${esc(x.id)}" title="Click to edit this skin">
    <span class="dot ${p.skin_counts[x.id] ? "ok" : "none"}"></span><span class="name">${esc(x.name || "Skin")}</span>
    <span class="cnt">${p.skin_counts[x.id] || 0} slot${p.skin_counts[x.id] === 1 ? "" : "s"}</span></div>`).join("");
  $("#skinDel").disabled = live.length < 2;
}

function renderSources() {
  const el = $("#sources");
  if (S.busy) { el.innerHTML = `<div class="src"><span class="spin"></span><span class="name">${esc(S.busy)}</span></div>`; return; }
  if (!S.sources.length) { el.innerHTML = `<div class="src"><span class="dot none"></span><span class="name">No pack selected</span></div>`; return; }
  el.innerHTML = S.sources.map((s, i) => `
    <div class="src" title="${esc(s.path)}"><span class="dot ${s.ok ? "ok" : "bad"}"></span><span class="name">${s.kind === "game" ? "SCS game files · " : ""}${esc(base(s.path) || "scan")}</span>
    ${s.path ? `<button class="x" data-rm="${i}" title="Remove">✕</button>` : ""}</div>
    ${s.ok ? "" : `<div class="src-err">${esc(s.error)}</div>`}`).join("");
  renderOfficial();
}
function officialNote(s) {
  const o = S.official || {}, f = (o.slots || {})[s.key], pick = (o.picks || {})[s.key];
  if (!o.dir) return `<p class="hint" style="margin:0">Have a folder of paint template PNGs for SCS's trucks and trailers (the community sets on Trucky: truckymods.io, 'truck and trailer templates')? Point <b>Template folder…</b> (Sources) at that folder and the Template view and Export use that sheet wherever it matches this model.</p>`;
  const choose = `<div class="row gap"><button class="small ghost" data-opick="${esc(s.key)}" title="Pick the sheet for this part from your template folder yourself">Choose sheet…</button>${pick !== undefined ? `<button class="small ghost" data-opickauto="${esc(s.key)}" title="Let SkinForge match the sheet again">Automatic</button>` : ""}</div>`;
  if (pick === "") return `<p class="hint" style="margin:0">You chose SkinForge's own template for this part.</p>${choose}`;
  if (pick) return `<p class="hint" style="margin:0">Your chosen sheet: <b>${esc(pick)}</b></p>${choose}`;
  if (!f) return `<p class="hint" style="margin:0">${o.busy ? "Looking for this part's sheet in your template folder…" : "No sheet in your template folder matched this part automatically - SkinForge's drawn template is used. Pick one yourself with Choose sheet…"}</p>${choose}`;
  return `<p class="hint" style="margin:0">Template folder sheet: <b>${esc(f)}</b> (same layout as the game's model).</p>
    <label class="check"><input type="checkbox" id="officialOff" ${o.off ? "checked" : ""}>Use SkinForge's labelled template instead (FRONT / ROOF / LEFT SIDE…)</label>${choose}`;
}
// "Choose sheet…": every PNG of the vehicle's template-folder folders as thumbnails; one click picks it
async function openSheetPick(key) {
  const s = slotOf(key);
  const r = await api(`/api/official/candidates?key=${encodeURIComponent(key)}`);
  $("#oPickTitle").textContent = `Template sheet · ${s ? s.label : key}`;
  $("#oPickNote").textContent = r.items.length ? `${r.items.length} sheet${r.items.length > 1 ? "s" : ""}` +
    (r.auto ? ` · automatic match: ${r.auto}` : " · no automatic match") : "No PNGs found in your template folder";
  $("#oPickGrid").innerHTML = r.items.map((x) => `<button class="opick-item${x.rel === r.pick || (r.pick == null && x.rel === r.auto) ? " on" : ""}" data-orel="${esc(x.rel)}" title="${esc(x.rel)} · ${x.w}×${x.h}">
      <img loading="lazy" src="/api/official/thumb?rel=${encodeURIComponent(x.rel)}&h=140" alt="">
      <span>${esc(x.rel)}</span><small>${x.w}×${x.h}</small></button>`).join("");
  $("#oPick").dataset.key = key;
  $("#oPick").hidden = false;
}
function renderOfficial() {
  const o = S.official || {}, el = $("#officialSrc");
  if (!el) return;
  el.innerHTML = o.dir ? `<div class="src" title="${esc(o.dir)}"><span class="dot ${o.found ? "ok" : "bad"}"></span><span class="name">Template folder · ${esc(base(o.dir))}${o.busy ? " · matching…" : ""}</span><button class="x" id="reloadOfficial" title="Reload the folder (after adding, replacing or removing templates in it)">↻</button><button class="x" id="rmOfficial" title="Stop using these">✕</button></div>
    ${o.found ? "" : `<div class="src-err">Folder not found</div>`}` : "";
}

// texture size of one truck / trailer / car: its own choice (Slot tab), else the project's
function vresOf(tid) { return +((S.project.res_by_vehicle || {})[tid]) || +S.project.resolution || 4096; }
// an SCS slot's texture size (scanned at the project size) at its vehicle's size
function texSizeOf(s) {
  const res = +S.project.resolution || 4096, f = vresOf(s.trailer) / res;
  const [w, h] = s.tex_size || [res, res];
  return [Math.max(4, Math.round(w * f)), Math.max(4, Math.round(h * f))];
}
function vehicleWord(t) { return t.kind.startsWith("scs_ai") ? "AI vehicle" : t.kind === "scs_car" ? "car" : t.kind === "scs" || t.kind === "mod3d" || t.kind === "trailer" ? "trailer" : "truck"; }
// finish / colour lock of one truck / trailer / car: its own choice, else the mod's (Paint job tab)
function vfinOf(tid) { const v = (S.project.finish_by_vehicle || {})[tid]; return v ? finishesOf({ finish: v }) : finishesOf(S.project); }
function vlockOf(tid) { const v = (S.project.lock_by_vehicle || {})[tid]; return v === undefined || v === null ? !!S.project.color_locked : !!v; }
// a template that is its own paint job (author template, a truck's cab sheet): finish, colour lock and
// AI traffic can be set for it alone; SCS / mod trailer parts share one paint job per trailer
function ownJob(s) { return s.kind === "trailer" || s.kind === "truck" || (s.kind === "scs" && (!!s.cab || !!s.body)); }
function sfinOf(s, t) { const v = (S.project.finish_by_slot || {})[s.key]; return v ? finishesOf({ finish: v }) : vfinOf(t.id); }
function slockOf(s, t) {
  const v = (S.project.lock_by_slot || {})[s.key];
  if (v !== undefined && v !== null) return v;
  const lv = (S.project.lock_by_vehicle || {})[t.id];
  return lv !== undefined && lv !== null ? lv : !!S.project.color_locked;
}
function strafficOf(s, t) { return (S.project.traffic_by_slot || {})[s.key] || trafficOf(t.id); }
function slotPaintGroup(t, s) {
  if (!ownJob(s)) return vehiclePaintGroup(t);
  const ownF = !!(S.project.finish_by_slot || {})[s.key], sl = (S.project.lock_by_slot || {})[s.key];
  const ownL = sl !== undefined && sl !== null, fins = sfinOf(s, t), w = vehicleWord(t);
  const what = s.kind === "scs" ? (s.cab ? "cab" : "body") : "template";
  const vt = (S.project.traffic_by_slot || {})[s.key], cabAi = s.kind === "scs" && t.kind === "scs_truck";
  const tv = strafficOf(s, t), own = tv !== "ai", ai = tv !== "own";
  return `<div class="group"><h2>This ${what} · paint job</h2>
    <div class="field"><span>Finish${ownF ? ` · own · <button class="link" data-sfinreset="${esc(s.key)}">same as the ${w}</button>` : ` · same as the ${w}`}</span><div class="seg">
      ${[["gloss", "Gloss"], ["metallic", "Metallic"], ["matte", "Matte"]].map(([v, l]) => `<button data-sfin="${v}" class="${fins.includes(v) ? "on" : ""}">${l}</button>`).join("")}</div></div>
    <label class="field"><span>Paint colour in the shop</span><select data-slock="${esc(s.key)}">
      ${[["", `Same as the ${w} (${slockOf({ key: "" }, t) ? "locked" : "players can change it"})`], ["0", "Players can change it"], ["1", "Locked"]].map(([v, n]) => `<option value="${v}" ${(ownL ? (sl ? "1" : "0") : "") === v ? "selected" : ""}>${n}</option>`).join("")}</select></label>
    ${cabAi ? `<div class="field"><span>Use this ${what} as${vt ? ` · own · <button class="link" data-strafficreset="${esc(s.key)}">same as the ${w}</button>` : ` · same as the ${w}`}</span></div>
    <label class="check"><input type="checkbox" data-straffic="own" ${own ? "checked" : ""}> Ownable ${w} (paint job in the shop)</label>
    <label class="check"><input type="checkbox" data-straffic="ai" ${ai ? "checked" : ""}> AI traffic (${t.kind === "scs" ? "SCS's AI trucks pull this body with this livery" : "SCS's AI " + esc(t.name) + " with this livery"})</label>` : ""}
    <small class="hint" style="margin:0">Only for ${esc(s.label)}.${s.body ? ` A body with its own setting here becomes its <b>own paint job</b> in the trailer shop (only offered on that body); the other bodies keep sharing one.` : ""} The paint colour itself: <b>Custom</b> above the preview. Settings for the whole ${w} are below.</small></div>
    ${vehiclePaintGroup(t)}`;
}
function vehiclePaintGroup(t) {
  const w = vehicleWord(t), ownF = !!(S.project.finish_by_vehicle || {})[t.id], lv = (S.project.lock_by_vehicle || {})[t.id];
  const ownL = lv !== undefined && lv !== null, fins = vfinOf(t.id);
  return `<div class="group"><h2>This ${w} · paint job</h2>
    <div class="field"><span>Finish${ownF ? ` · own · <button class="link" data-vfinreset="${esc(t.id)}">same as the skin</button>` : " · same as the skin"}</span><div class="seg">
      ${[["gloss", "Gloss"], ["metallic", "Metallic"], ["matte", "Matte"]].map(([v, l]) => `<button data-vfin="${v}" data-vt="${esc(t.id)}" class="${fins.includes(v) ? "on" : ""}">${l}</button>`).join("")}</div></div>
    <label class="field"><span>Paint colour in the shop</span><select data-vlock="${esc(t.id)}">
      ${[["", `Same as the skin (${S.project.color_locked ? "locked" : "players can change it"})`], ["0", "Players can change it"], ["1", "Locked"]].map(([v, n]) => `<option value="${v}" ${(ownL ? (lv ? "1" : "0") : "") === v ? "selected" : ""}>${n}</option>`).join("")}</select></label>
    <small class="hint" style="margin:0">Only for ${esc(t.name)} - e.g. a matte, locked truck with gloss trailers you can recolour. The Paint job tab is the default for everything else. Settings here are for the skin you're editing.</small></div>${trafficGroup(t)}`;
}
// SCS trucks: the cab art as an ownable paint job and/or on SCS's AI version of the truck
function trafficOf(tid) { return (S.project.traffic_by_vehicle || {})[tid] || "own"; }
function trafficGroup(t) {
  if (t.kind !== "scs_truck") return "";   // SCS trailers: the AI traffic sheet in their list is the AI livery
  const v = trafficOf(t.id), own = v !== "ai", ai = v !== "own", tr = t.kind === "scs", w = tr ? "trailer" : "truck";
  return `<div class="group"><h2>This ${w} · use the skin as</h2>
    <label class="check"><input type="checkbox" data-traffic="own" data-vt="${esc(t.id)}" ${own ? "checked" : ""}> Ownable ${w} (paint job in the ${w} shop)</label>
    <label class="check"><input type="checkbox" data-traffic="ai" data-vt="${esc(t.id)}" ${ai ? "checked" : ""}> AI traffic (SCS's AI ${tr ? "trucks pull this trailer" : esc(t.name) + " drives around"} with this livery)</label>
    <small class="hint" style="margin:0">${tr ? `AI traffic: SCS's traffic trailers use their own company-livery layout, so they get their own sheet - <b>AI traffic sheet</b> in this trailer's list (shown once AI traffic is ticked). Paint it like SCS's company liveries: top half = both sides, bottom = front wall and rear doors. Every traffic length uses that one sheet.` : `AI traffic: SkinForge carries your art over onto SCS's AI version of this truck when building (matched in 3D - you only paint the normal templates). Each cab you painted becomes an AI truck in traffic that pulls trailers.`} Parts the AI model has that your templates don't cover take the paint colour.</small></div>`;
}
// "· 8K · matte · locked" in a vehicle's list header when it has its own settings
function vehicleTags(t) {
  const P = S.project, out = [];
  if ((P.res_by_vehicle || {})[t.id]) out.push(vresOf(t.id) / 1024 + "K");
  if ((P.finish_by_vehicle || {})[t.id]) out.push(vfinOf(t.id).join("+"));
  const lv = (P.lock_by_vehicle || {})[t.id];
  if (lv !== undefined && lv !== null) out.push(lv ? "locked" : "colour free");
  const tv = (P.traffic_by_vehicle || {})[t.id];
  if (tv && t.kind === "scs_truck") out.push(tv === "ai" ? "AI traffic only" : "+ AI traffic");
  const sh = t.slots.find((x) => x.kind === "ai_sheet");
  if (sh && cfgOf(sh.key).art) out.push("+ AI traffic");
  return out.length ? ` · <b class="vtag">${esc(out.join(" · "))}</b>` : "";
}
function vresNote(t) {
  const own = +((S.project.res_by_vehicle || {})[t.id]) || 0, res = +S.project.resolution || 4096;
  return `<p class="hint" style="margin:0">Texture size: <b>${vresOf(t.id) / 1024}K</b> for ${esc(t.name)}${own ? " (its own size)" : " (same as the mod)"} · change it with 2K / 4K / 8K above the preview. Mix sizes in one mod, e.g. an 8K truck with 4K trailers.</p>`;
}
function tmplSizeNote(scs, tid) {
  const res = tid ? vresOf(tid) : +S.project.resolution || 4096, want = +S.project.template_size || 0;
  const k = (want || res) / 1024 + "K";
  const sizes = scs ? `Cabs and main sheets at ${k}, small parts in proportion.` : `Long side ${want || res} px.`;
  return want && want !== res ? `${sizes} This ${tid ? "vehicle" : "mod"} is still built at ${res / 1024}K - set its Texture size to ${k} too if you want ${k} in game.` : sizes;
}

function thumbUrl(key, ovr = -1, size = 96) {
  return `/api/preview?key=${encodeURIComponent(key)}&ovr=${ovr}&size=${size}&mode=clean&color=${encodeURIComponent(paintOf(key))}&b=${artBust}`;
}

function renderTrailers() {
  const el = $("#trailers");
  if (!S.trailers.length) { el.innerHTML = S.sources.some((s) => s.ok) ? `<p class="hint">No trailers with template paint jobs found in this pack.</p>` : ""; return; }
  const kinds = { trailer: "Trailers", truck: "Trucks", logo: "Logos & yard signs", scs: "SCS trailers", scs_truck: "SCS trucks", mod3d: "Mod trailers · templates from 3D models", mod3d_truck: "Mod trucks · templates from 3D models", scs_car: "Road Trip cars", scs_ai: "AI traffic · cars, vans, work trucks, buses", scs_ai_truck: "AI traffic · semi trucks", scs_ai_trailer: "AI traffic · trailers", scs_escort: "Escort vehicles · special transport" };
  // one collapsible section per category (SCS trucks, AI traffic, ...), closed by default,
  // remembered per project like the vehicle groups (ids "kind:<kind>")
  const cats = [];
  for (const t of S.trailers) {
    let c = cats.find((x) => x.kind === t.kind);
    if (!c) cats.push(c = { kind: t.kind, list: [] });
    c.list.push(t);
  }
  el.innerHTML = cats.map((c) => {
    const id = "kind:" + c.kind, isOpen = !!(opened && opened.has(id));
    const painted = c.list.reduce((a, t) => a + t.slots.filter((s) => cfgOf(s.key).art).length, 0);
    const loaded = c.list.filter((t) => !t.lazy).length;
    const sub = `${c.list.length} ${c.list.length === 1 ? "item" : "items"}${loaded < c.list.length ? ` · ${loaded} loaded` : ""}${painted ? ` · <b class="vtag">${painted} painted</b>` : ""}`;
    return `<div class="cat ${isOpen ? "" : "closed"}"><div class="cat-h" data-toggle="${esc(id)}"><span class="chev">${isOpen ? "▾" : "▸"}</span><h2>${kinds[c.kind] || c.kind}</h2><small>${sub}</small></div>
      ${isOpen ? `<div class="cat-b">${c.list.map(groupHtml).join("")}</div>` : ""}</div>`;
  }).join("");
}

function bodiesOnly(t) { return !!(S.project.bodies_only || {})[t.id] && t.slots.some((x) => x.body); }
function aiOn(t) {   // AI traffic ticked for this trailer or one of its bodies (this skin)
  const ts = S.project.traffic_by_slot || {};
  return trafficOf(t.id) !== "own" || t.slots.some((x) => ts[x.key] && ts[x.key] !== "own");
}
function shownSlots(t) {   // "Bodies only": parts are left out (they take the paint colour) unless they have art
  let out = bodiesOnly(t) ? t.slots.filter((x) => x.body || x.kind === "ai_sheet" || cfgOf(x.key).art) : t.slots;
  return out;
}
function groupHtml(t) {
  {
    const done = t.slots.filter((s) => cfgOf(s.key).art).length;
    const open = t.lazy ? false : !!(opened && opened.has(t.id));
    const head = "";
    if (t.lazy || t.error) return `${head}<div class="trailer closed">
      <div class="trailer-h"><span class="chev">▸</span><div class="t"><b>${esc(t.name)}</b><small>${t.error ? esc(t.error) : "not loaded · Load reads its models once"}</small></div>
        ${t.lazy ? `<button class="small" data-loadtruck="${esc(t.id)}" data-vk="${t.kind === "scs" ? "trailer" : t.kind === "scs_car" ? "car" : t.kind.startsWith("scs_ai") || t.kind === "scs_escort" ? "ai" : "truck"}" title="Read this ${t.kind === "scs" ? "trailer's bodies and parts" : t.kind === "scs_car" ? "car's body" : t.kind.startsWith("scs_ai") ? "AI vehicle's body" : "truck's cabs and parts"} from its 3D models (the first time can take a minute)">Load</button>` : ""}</div></div>`;
    return `${head}<div class="trailer ${open ? "" : "closed"}">
      <div class="trailer-h" data-toggle="${esc(t.id)}"><span class="chev">${open ? "▾" : "▸"}</span><div class="t"><b>${esc(t.name)}</b><small>${t.id === "player_logos" ? "your own company's logo · replaces the game's logos 1-" + t.slots.length : t.id === "yard_signs" ? "texture replacement · " + t.slots.length + " sign" + (t.slots.length > 1 ? "s" : "") + " · load above the yard mods" : t.kind === "logo" ? "texture replacement · 6 trucks" : t.kind === "scs" || t.kind === "mod3d" ? esc(t.id) + " · " + t.slots.length + " parts · one paint job" + (t.hidden_parts ? ` · ${t.hidden_parts} empty part${t.hidden_parts > 1 ? "s" : ""} hidden (paint colour)` : "") : t.kind === "scs_car" ? esc(t.id) + " · one body sheet" : t.kind === "scs_ai_trailer" ? "AI trailer · one sheet · each skin adds a trailer to traffic" : t.kind === "scs_escort" ? "escort · left / right / roof / front / rear · each skin adds an escort vehicle" : t.kind.startsWith("scs_ai") ? "AI traffic · one sheet · each skin adds a vehicle to traffic" : t.kind === "scs_truck" || t.kind === "mod3d_truck" ? esc(t.id) + " · " + t.slots.filter((x) => x.cab).length + " cabs + " + t.slots.filter((x) => !x.cab).length + " parts" : esc(t.id) + " · " + t.slots.length + " template" + (t.slots.length > 1 ? "s" : "")}${t.kind === "logo" ? "" : vehicleTags(t)}</small></div>
        <span class="acts"><span class="count ${done === t.slots.length ? "done" : ""}">${done}/${t.slots.length}</span>
        ${t.id === "ruda_logos" ? `<button class="small ghost" id="importLogos" title="Take the logos from an existing logo mod (.scs)">Import…</button>` : ""}
        ${t.slots.some((x) => x.body) && t.slots.some((x) => !x.body) ? `<button class="small ghost ${bodiesOnly(t) ? "on" : ""}" data-bodies="${esc(t.id)}" title="${bodiesOnly(t) ? "Showing only the body sheets - click to show every part again" : "Show only the body sheets (parts you don't paint take the paint colour)"}">${bodiesOnly(t) ? "Bodies only ✓" : "Bodies only"}</button>` : ""}
        ${t.kind !== "logo" || t.slots.some((x) => x.has_template) ? `<button class="small ghost" data-tmpl="${esc(t.id)}" title="Choose which painting templates to export">Templates</button>` : ""}</span></div>
      ${!open ? "" : shownSlots(t).map((s) => {
        const has = !!cfgOf(s.key).art;
        return `<div class="slot ${s.key === sel ? "sel" : ""}" data-key="${esc(s.key)}">
          <img loading="lazy" src="${has ? thumbUrl(s.key) : thumbUrl(s.key, -1, 96).replace("mode=clean", "mode=template")}" alt="">
          <div class="meta"><b>${esc(s.label)}</b><small>${esc(uniqNames(s).join(", "))}</small></div>
          <span class="dot ${has ? "ok" : cabState(s) === "broken" ? "part" : "none"}" title="${has ? "Artwork assigned" : "No artwork yet"}"></span></div>`;
      }).join("")}
    </div>`;
  }
}

function partName(o) {
  const tex = (o.tex || "").split("/").pop().replace(/\.(dds|tobj)$/, "");
  return tex || o.parts[0]?.split(".")[0] || "part " + o.index;
}
function inheritedPart(t, o) {
  if (!o.tex) return null;
  for (const x of t.slots) for (const xo of x.overrides) {
    const r = ((cfgOf(x.key).overrides) || {})[xo.index];
    if (r && xo.tex === o.tex) return r;
  }
  return null;
}
let picking = false;
let pickBusy = false;
const OPS = { v: "Flip vertical", h: "Flip horizontal", r: "Rotate 180°" };
function flipsGroup(s, t, cfg, logo) {
  if (logo || !s.has_template || s.kind === "scs" || s.kind === "ai_sheet") return "";
  const marks = cfg.flips || [];
  const others = t.slots.filter((x) => x.key !== s.key && x.has_template);
  return `<div class="group"><h2>Mirrored pieces</h2>
    <p class="hint" style="margin:0">For pieces the template lays out upside down or mirrored (e.g. the W900 right side). Mark them, paint them upright on a separate sheet, and SkinForge flips them back when building.</p>
    ${marks.map((m, i) => `<div class="flip-row"><span class="n">${i + 1}</span>
      <select data-flipop="${i}">${Object.entries(OPS).map(([k, l]) => `<option value="${k}" ${m.op === k ? "selected" : ""}>${l}</option>`).join("")}</select>
      <button class="small danger" data-flipdel="${i}" title="Remove this mark">✕</button></div>`).join("")}
    <div class="row gap"><button class="small ${picking ? "primary" : ""}" id="pickBtn">${picking ? "Done marking (Esc)" : "+ Mark mirrored pieces"}</button></div>
    ${marks.length ? `
      ${cfg.upright ? `<div class="artcard"><img src="/api/preview?key=${encodeURIComponent(s.key)}&mode=upright&size=128&color=none&b=${artBust}" alt=""><div class="m"><b>Upright sheet</b><small>${esc(base(cfg.upright))}</small></div>
        <div class="row gap"><button class="small" data-pick="-2">Replace</button><button class="small danger" data-clear="-2">Remove</button></div></div>`
      : `<div class="dropzone" data-pick="-2"><b>Drop your upright sheet</b><br>Paint over the <i>…_upright.png</i> that Templates export writes for this ${s.kind === "truck" ? "cab" : "template"}</div>`}
      ${cfg.art ? `<div><button class="small" id="extractUpright" title="Cut the marked pieces out of your current artwork, turn them upright and use that as the upright sheet">Make upright sheet from my artwork</button></div>` : ""}
      <p class="hint" style="margin:0">Use the <b>Upright</b> view to check it. Preview and Clean show the result in the game layout.</p>
      ${others.length ? `<div><button class="small" id="copyFlips">Copy marks to the other ${others.length} ${s.kind === "truck" ? "cabs" : "templates"}</button></div>` : ""}` : ""}
  </div>`;
}
function cabSelection(s) {
  const c = cfgOf(s.key).cabins;
  if (Array.isArray(c)) return { list: c, how: "custom" };
  if (s.fix_cabins) return { list: s.fix_cabins, how: "fixed" };
  return { list: s.cabin_units || [], how: "template" };
}
function cabState(s) {
  if (s.kind !== "truck") return "ok";
  const sel = cabSelection(s);
  if (!sel.list.length) return "broken";
  return sel.how === "fixed" ? "fixed" : "ok";
}
function appliesTo(s, t) {
  if (s.kind !== "truck" || !(t.cabins || []).length) return uniqNames(s);
  const names = Object.fromEntries(t.cabins.map((c) => [c.unit, c.name]));
  const cabs = cabSelection(s).list.map((u) => names[u] || u);
  const other = s.targets.filter((x) => !x.unit.endsWith(".cabin")).map((x) => x.name);
  return [...new Set([...cabs, ...other])];
}
function cabPicker(s, t, cfg) {
  const sel = cabSelection(s), on = new Set(sel.list);
  const ordered = [...t.cabins.filter((c) => on.has(c.unit)), ...t.cabins.filter((c) => !on.has(c.unit))];
  const note = { template: "as the author's template", fixed: "SkinForge's correction", custom: "your choice" }[sel.how];
  return `<div class="group"><h2>Cabs this skin goes on</h2>
    <p class="hint" style="margin:0">${on.size} of ${t.cabins.length} cabs · ${note}${sel.how === "custom" ? ` · <button class="link" data-cabreset="1">reset</button>` : ""}</p>
    <div class="share" id="cabList">${ordered.map((c) => `<label class="check"><input type="checkbox" data-cab="${esc(c.unit)}" ${on.has(c.unit) ? "checked" : ""}>${esc(c.name)}</label>`).join("")}</div></div>`;
}
function finishesOf(p) {
  const f = String(p.finish || "both").toLowerCase();
  const out = f === "both" ? ["gloss", "metallic"] : f.split("+").filter((x) => ["gloss", "metallic", "matte"].includes(x));
  return out.length ? out : ["gloss", "metallic"];
}
// video memory the skin's textures take in game: DXT5 = 1 byte per pixel, + 1/3 for the mipmaps
function vramNote(p) {
  const res = +p.resolution || 4096;
  let bytes = 0, n = 0;
  for (const t of S.trailers) for (const s of t.slots) {
    if (!cfgOf(s.key).art || s.kind === "logo") continue;
    const [w, h] = s.kind === "scs" ? texSizeOf(s) : [vresOf(s.trailer), vresOf(s.trailer)];
    bytes += w * h * 4 / 3; n++;
  }
  const mb = (b) => b >= 1024 * 1024 * 1024 ? (b / 1024 / 1024 / 1024).toFixed(1) + " GB" : Math.round(b / 1024 / 1024) + " MB";
  const per = mb(res * res * 4 / 3);
  const now = n ? `This skin: ${n} texture${n > 1 ? "s" : ""}, about ${mb(bytes)} of video memory in game.` : `Each full ${res} × ${res} texture takes about ${per} of video memory in game.`;
  if (res < 8192) return `<small class="hint">${now}</small>`;
  return `<div class="warnlist"><span class="warn">8K: four times the video memory of 4K - about ${per} per full texture (4K: ${mb(4096 * 4096 * 4 / 3)}).${n ? " " + now : ""}
    On an 8 GB card keep 8K for one or two hero trucks, not a whole fleet or traffic; too many can stutter or blur when the game runs out of VRAM. Builds also take longer.</span></div>`;
}
function uniqNames(s) { return [...new Set(s.targets.map((x) => x.name))]; }
// expanded vehicle groups: closed by default, remembered per project (the app
// window's browser storage is lost on every start - new port each time)
let opened = null;
function setOpen(id, on) {
  if (on) opened.add(id); else opened.delete(id);
  api("/api/ui/open", { id, open: on }).catch(() => {});
}

// ---- stage (zoom / pan)
const view = { z: 1, x: 0, y: 0, fitted: true };
function renderStage() {
  const s = slotOf(sel);
  $("#emptyStage").hidden = !!s;
  $("#previewImg").hidden = !s;
  $("#stageTitle").textContent = s ? `${trailerOf(sel).name} - ${s.label}` : "";
  $("#stageTitle").title = $("#stageTitle").textContent;
  const hasFlips = !!(s && (cfgOf(sel).flips || []).length);
  $("#uprightBtn").hidden = !hasFlips;
  if (!hasFlips && mode === "upright") mode = "paint";
  $("#dropHint").hidden = !s || mode === "template" || (mode === "upright" ? !!cfgOf(sel).upright : !!cfgOf(sel).art);
  $("#pickHint").hidden = !picking;
  $("#viewport").classList.toggle("picking", picking);
  $("#previewColor").value = s ? paintOf(sel) : S.project.base_color;
  const pm = previewNone ? "none" : s && slotColor(sel) ? "custom" : "default";
  const sw = $("#paintSeg [data-paint=custom]");
  sw.title = s && s.kind === "scs" ? "This trailer's own paint colour (all its parts share one paint job). Default = the Paint job tab colour."
    : "This template's own paint colour - overrides the Paint job tab colour for this template. Default = the Paint job tab colour.";
  $$("#paintSeg [data-paint]").forEach((b) => b.classList.toggle("on", b.dataset.paint === pm));
  $$("#modeSeg button").forEach((b) => b.classList.toggle("on", b.dataset.mode === mode));
  const zs = $("#sizeSeg"), vt = s && s.kind !== "logo" ? trailerOf(sel) : null;
  zs.hidden = !vt;
  if (vt) {
    const cur = vresOf(vt.id), own = !!(S.project.res_by_vehicle || {})[vt.id];
    $$("#sizeSeg button").forEach((b) => {
      b.classList.toggle("on", +b.dataset.vsize === cur);
      b.title = `Build ${vt.name} at ${+b.dataset.vsize / 1024}K` + (+b.dataset.vsize === +S.project.resolution ? " (the mod's size)" : "") +
        (own && +b.dataset.vsize === cur ? " - its own size; click the mod's size to follow the mod again" : "");
    });
  }
  if (!s) return;
  const off = !(S.official || {}).off ? ((S.official || {}).slots || {})[sel] || "" : "";
  const url = `/api/preview?key=${encodeURIComponent(sel)}&mode=${mode}&color=${encodeURIComponent(previewNone ? "none" : paintOf(sel))}&b=${artBust}&o=${encodeURIComponent(off)}`;
  const img = $("#previewImg"), vp = $("#viewport"), msg = $("#stageMsg");
  if (img.dataset.src !== url) {
    img.dataset.src = url;
    // another slot's picture must never stand in for this one: grey it out until ours arrives
    const otherSlot = img.dataset.key !== sel;
    clearTimeout(renderStage.slow);
    msg.hidden = true; msg.classList.remove("err");
    if (otherSlot && img.getAttribute("src")) vp.classList.add("stale");
    renderStage.slow = setTimeout(() => {
      if (img.dataset.src !== url) return;
      msg.textContent = s.kind === "scs" ? "Drawing this template from the 3D model…" : "Loading the template…";
      msg.hidden = false;
    }, 400);
    const pre = new Image();
    renderStage.pre = pre;   // keep a reference, or Chromium may collect it before onload fires
    pre.fetchPriority = "high";
    pre.onload = () => {
      if (img.dataset.src !== url) return;
      clearTimeout(renderStage.slow);
      msg.hidden = true; vp.classList.remove("stale");
      const shapeChanged = pre.naturalWidth !== img.naturalWidth || pre.naturalHeight !== img.naturalHeight;
      img.src = url; img.dataset.key = sel;
      if (shapeChanged || view.fitted) img.decode().then(fit).catch(() => {});
    };
    pre.onerror = async () => {
      if (img.dataset.src !== url) return;
      clearTimeout(renderStage.slow);
      let why = "";
      try { why = (await (await fetch(url)).json()).error || ""; } catch (e) { /* not JSON */ }
      if (img.dataset.src !== url) return;
      vp.classList.add("stale");
      msg.textContent = `Couldn't draw this template${why ? ": " + why : ""} - Mod tab > Problems > Save error report`;
      msg.classList.add("err"); msg.hidden = false;
    };
    pre.src = url;
    if (!img.getAttribute("src")) { img.src = url; img.dataset.key = sel; }
  }
}
function applyView() { $("#previewImg").style.transform = `translate(${view.x}px,${view.y}px) scale(${view.z})`; }
function fit() {
  const vp = $("#viewport").getBoundingClientRect(), img = $("#previewImg");
  const nw = img.naturalWidth || 1024, nh = img.naturalHeight || 1024;
  view.z = Math.min((vp.width - 40) / nw, (vp.height - 40) / nh);
  view.x = (vp.width - nw * view.z) / 2; view.y = (vp.height - nh * view.z) / 2; view.fitted = true; applyView();
}

// ---- slot tab
function renderSlotTab() {
  const el = $("#tab-slot"), s = slotOf(sel);
  if (!s) { el.innerHTML = `<p class="hint">Load a trailer pack to start.</p>`; return; }
  const cfg = cfgOf(sel), t = trailerOf(sel);
  const scs = s.kind === "scs";
  const siblings = t.slots.filter((x) => x.key !== sel && (x.kind === "ai_sheet") === (s.kind === "ai_sheet") && (!scs || String(x.tex_size) === String(s.tex_size)));
  const aisheet = s.kind === "ai_sheet";
  const logo = s.kind === "logo", sign = s.trailer === "yard_signs", plogo = s.trailer === "player_logos", aiv = s.kind === "ai" || s.kind === "ai_trailer" || s.kind === "ai_escort" || s.kind === "ai_sheet";
  el.innerHTML = `
    ${s.messy ? `<p class="warn" style="margin:0"><b>Overlapping layout.</b> SCS never made liveries for this AI model (it only gets a flat colour), so its texture layout puts several parts on top of each other: the same art lands on more than one part. A colour, a pattern or a logo in a clean area works; a detailed livery won't line up.</p>` : ""}
    ${s.kind === "ai_escort" ? `<p class="hint" style="margin:0">Escort vehicle (special transport): SkinForge lays the body out again (like the escort templates skin authors make by hand): LEFT SIDE, RIGHT SIDE, ROOF / HOOD, FRONT and REAR each get their own space, so what you paint is exactly what shows - lettering reads right on both sides. The grey "inside" box is the bed / inner panels: a colour is enough. Each skin adds this escort, with your livery, to the escort vehicles that lead and follow oversize loads. Transparent areas take the paint colour.</p>` : s.kind === "ai_trailer" ? `<p class="hint" style="margin:0">AI trailer: the whole trailer on one sheet, drawn from SCS's traffic trailer model. Each skin adds one more of these trailers to traffic (AI trucks pull it) with your livery as its paint job. Transparent areas take the paint colour.</p>` : aiv && !aisheet ? `<p class="hint" style="margin:0">AI traffic: the whole body of this vehicle on one sheet, drawn from its 3D model. Each skin adds one more of these vehicles to the game's traffic with your livery. Transparent areas take the paint colour. Left and right usually share the side area (one reads mirrored). Lights, glass and chrome stay SCS's own; far away (lowest detail) the vehicle shows SCS's plain version.</p>` : ""}
    <div><h3>${esc(t.name)} - ${esc(s.label)}</h3><small class="hint" style="margin:0">${esc(s.trailer)} · ${esc(s.token)}${scs ? ` · ${texSizeOf(s)[0]} × ${texSizeOf(s)[1]}` : ""}</small></div>
    ${scs ? `<p class="hint" style="margin:0">${s.cab && t.kind === "scs_car" ? `Body sheet drawn from the car's 3D model - the whole car on one sheet. Becomes one paint job in the car's paint shop (Road Trip).` : s.cab ? `Cab sheet drawn from the 3D model (parts that use the cab texture are on it too). This skin becomes one paint job per cab you paint; the truck's separate parts go with every one of them. Paint only parts and it goes on every cab.` : `Template drawn from the 3D model. All parts of this ${t.kind === "scs_truck" || t.kind === "mod3d_truck" ? "truck" : "trailer"} go into <b>one paint job</b>; parts you leave empty take the paint colour.`}</p>` : ""}
    <div class="field"><span>${logo ? "Replaces the texture on" : "Applies to"}</span><div class="chips">${appliesTo(s, t).map((n) => `<span class="chip">${esc(n)}</span>`).join("") || '<span class="chip">no cab</span>'}</div></div>
    ${logo ? "" : `<div class="field"><span>Paint colour</span><div class="colorrow"><span class="chip" style="background:${esc(paintOf(sel))};width:28px">&nbsp;</span>
      <small style="margin:0">${slotColor(sel) ? `own colour ${esc(slotColor(sel))}${scs ? ` (whole ${vehicleWord(t)})` : ""} · <button class="link" data-paint="default">use Paint job colour</button>` : "Paint job colour · pick <b>Custom</b> above the preview to give this " + (scs ? vehicleWord(t) : "template") + " its own"}</small></div></div>`}
    ${cabState(s) === "broken" ? `<div class="warn">The author's template points at ${s.unmatched.map(esc).join(", ")}, which doesn't exist, so a skin here won't show on any cab. Tick the right cabs below.</div>` : ""}
    ${cabState(s) === "fixed" ? `<p class="hint" style="margin:0">Fixed: the author's template points at cab names that don't exist${(s.unmatched || []).length ? ` (${s.unmatched.map(esc).join(", ")})` : ""}. SkinForge sends this skin to the cabs ticked below instead.</p>` : ""}
    ${s.kind === "truck" && (t.cabins || []).length ? cabPicker(s, t, cfg) : ""}
    ${flipsGroup(s, t, cfg, logo)}
    <div class="group"><h2>Artwork</h2>
      ${cfg.art ? `<div class="artcard"><img src="${thumbUrl(sel, -1, 128)}" alt=""><div class="m"><b>${esc(base(cfg.art))}</b><small id="artInfo">…</small></div>
        <div class="row gap"><button class="small" data-pick="-1">Replace</button><button class="small danger" data-clear="-1">Remove</button></div></div>
        <div class="warnlist" id="artWarn"></div>`
      : `<div class="dropzone" data-pick="-1"><b>Drop an image or click to choose</b><br>PNG with transparency · ${scs ? `${texSizeOf(s)[0]} × ${texSizeOf(s)[1]} (same shape as the template)` : sign ? `the whole sign · ${s.tex_size ? s.tex_size[0] + " × " + s.tex_size[1] : "same size as the pack's"} (transparent = see-through on the sign)` : plogo ? `logo on a transparent square${s.tex_size ? " · the game's logo is " + s.tex_size[0] + " × " + s.tex_size[1] : ""}` : logo ? "logo on a transparent square (2048 × 2048 ideal)" : s.has_template ? "same shape as the template (e.g. 4096 × 4096 or 4096 × 2048)" : "power-of-two size"}</div>`}
    </div>
    ${aisheet ? (() => { const lens = ((s.label.match(/\(([^)]*)\)/) || [])[1] || "").split(",").map((x) => parseInt(x)).filter((x) => x);
        const on = (S.project.ai_lengths || {})[t.id]; const has = (L) => !on || on.includes(L);
        return lens.length ? `<div class="field"><span>Traffic lengths that use this sheet</span>${lens.map((L) => `<label class="check"><input type="checkbox" data-ailen="${L}" ${has(L) ? "checked" : ""}> ${L}'</label>`).join("")}</div>` : ""; })() : ""}
    ${aisheet ? `<p class="hint" style="margin:0"><b>AI traffic livery.</b> Paint this sheet and SCS's AI trucks pull ${esc(t.name)}s with it - leave it empty for no AI traffic. SCS's traffic ${esc(t.name)}s all use one company sheet like this (it is how SCS's own company liveries - and SneaK's Voltison trailer - work): the <b>top half is the side</b>, shown on <b>both sides</b> - SkinForge re-maps SCS's traffic models so <b>every length shows the whole band</b>, edge to edge (shorter trailers squeeze it). Fill the band right down to its bottom edge. Bottom row: rear doors, front wall and the roof (small square). Every ticked traffic length uses this one sheet. Transparent = the paint colour.</p>` : ""}
    ${sign ? `<p class="hint" style="margin:0">Replaces <b>${esc(s.template_dds || "")}</b> from the yard mod's pack. Load this mod <b>above</b> ${esc((s.targets[0] || [])[1] || "the sign pack")} in the mod manager. Template view: a PNG in your template folder whose name contains this sign's name (e.g. <i>msdsign</i> or <i>Manchester SD sign</i>), else the pack's own template, else what the pack shows now. After adding one, click ↻ on the Template folder line.</p>` : plogo ? `<p class="hint" style="margin:0">Pick it in the game when you create your company (or later in the company settings). It <b>replaces</b> the game's logo_${esc(s.token.slice(6))}: nothing is added, and only one company-logo mod can be active - load this mod <b>above</b> any other logo mod.${s.has_template ? " Template view shows the game's logo you replace." : " The game files couldn't be read, so the logo is built at your image's size (power of two, max 512)."}</p>` : logo ? `<p class="hint" style="margin:0">Jon's trucks show this when you pick the matching <b>Your Logo</b> option in the truck shop. Load this mod <b>above</b> the Jon Ruda truck mods.</p>` : ""}
    ${siblings.length ? `<div class="group"><h2>${sign ? "Use this artwork for other signs too" : logo ? "Use this logo for other slots too" : scs ? "Use this artwork for other parts too" : (s.kind === "truck" ? "Use this artwork for other cabs too" : "Use this artwork for other lengths too")}</h2>
      <p class="hint" style="margin:0">${sign ? "Same art on several signs, or a different one per sign." : logo ? "One logo everywhere, or a different one per option - your call." : s.kind === "truck" ? "Only where two cabs share the same template layout - compare the Template view first." : "Handy when two body types share one layout (Jon does this for dry van + reefer of the same length). Check the preview of each."}</p>
      <div class="share">${siblings.map((x) => `<label class="check"><input type="checkbox" value="${esc(x.key)}">${esc(x.label)}${cfgOf(x.key).art ? ' <span class="chip">has art</span>' : ""}</label>`).join("")}</div>
      <div><button class="small" id="shareBtn" ${cfg.art ? "" : "disabled"}>Copy artwork to checked</button></div></div>` : ""}
    ${s.overrides.length ? `<div class="group"><h2>Separate parts</h2>
      <p class="hint" style="margin:0">These parts use their own texture. Art set once is reused by every ${s.kind === "truck" ? "cab" : "slot"} of this ${s.kind === "truck" ? "truck" : "trailer"} that has the same part. Empty = plain paint colour.</p>
      ${s.overrides.map((o) => { const r = (cfg.overrides || {})[o.index]; const inh = r ? null : inheritedPart(t, o); return `
        <div class="artcard">${r ? `<img src="${thumbUrl(sel, o.index, 128)}" alt="">` : ""}<div class="m"><b>${esc(partName(o))}</b><small>${o.parts.length} part variant${o.parts.length > 1 ? "s" : ""}${r ? " · " + esc(base(r)) : inh ? " · shared: " + esc(base(inh)) : " · paint colour"}</small></div>
        <div class="row gap"><button class="small" data-pick="${o.index}">${r ? "Replace" : "Add"}</button>${r ? `<button class="small danger" data-clear="${o.index}">✕</button>` : ""}</div></div>`; }).join("")}</div>` : ""}
    ${logo ? "" : vresNote(t)}
    ${logo || aiv ? "" : slotPaintGroup(t, s)}
    ${logo && !s.has_template ? "" : `<div class="group"><h2>Template</h2>
      ${logo ? "" : `<label class="field"><span>Template size</span><select data-k="template_size">
        ${[["", `Same as texture size (${(+S.project.resolution || 4096) / 1024}K)`], ["2048", "2K · 2048"], ["4096", "4K · 4096"], ["8192", "8K · 8192"]].map(([v, n]) => `<option value="${v}" ${String(S.project.template_size || "") === v ? "selected" : ""}>${n}</option>`).join("")}</select></label>`}
      ${scs ? officialNote(s) : ""}
      <div class="row gap"><button class="small" data-tmplone="${esc(s.key)}">Export this template</button><button class="small ghost" data-tmpl="${esc(t.id)}">Choose templates…</button></div>
      <p class="hint" style="margin:0">${scs ? `Saves every part's template (drawn from the game's models) as PNG into Documents\\SkinForge\\Templates (one folder per game and vehicle, shared by all projects) - paint over it. ${tmplSizeNote(true, t.id)}` : `Saves the author's template for every ${s.kind === "truck" ? "cab" : "length"} as PNG into Documents\\SkinForge\\Templates (one folder per game and vehicle, shared by all projects) - paint over it. ${tmplSizeNote(false, t.id)}`}</p>
      <label class="check"><input type="checkbox" data-k="export_guides" ${S.project.export_guides ? "checked" : ""}>Also export guide overlays (line art on transparent, for a locked top layer)</label></div>`}`;
  if (cfg.art) guard(async () => {
    const r = await api("/api/art/info", { key: sel });
    if (!r.info) return;
    $("#artInfo") && ($("#artInfo").textContent = `${r.info.w} × ${r.info.h}`);
    $("#artWarn") && ($("#artWarn").innerHTML = r.info.warnings.map((w) => `<div class="warn">${esc(w)}</div>`).join(""));
  });
}

// ---- paint job tab
function renderPaintTab() {
  const p = S.project, m = p.metallic, fins = finishesOf(p), met = fins.includes("metallic");
  $("#tab-paint").innerHTML = `
    <label class="field"><span>Skin name (shown in the shop) · ${esc(p.skins.filter((x) => !x.deleted).length)} skin${p.skins.filter((x) => !x.deleted).length > 1 ? "s" : ""} in this mod</span><input type="text" data-k="paint_name" value="${esc(p.paint_name)}">
      <small>Finish, price and colour below apply to every skin. Each skin has its own name, artwork and shop icon (Mod tab).</small></label>
    <div class="grid2">
      <label class="field"><span>Price</span><input type="number" min="0" step="50" data-k="price" data-num value="${esc(p.price)}"></label>
      <label class="field"><span>Texture size</span><select data-k="resolution" data-num>
        ${[[2048, "2K"], [4096, "4K"], [8192, "8K"]].map(([r, n]) => `<option value="${r}" ${+p.resolution === r ? "selected" : ""}>${n} · ${r} × ${r}</option>`).join("")}</select></label>
    </div>
    <small class="hint" style="margin-top:-8px">Used for every skin, trucks and trailers - unless a truck / trailer has its own size (Slot tab). 2K uses a quarter of the VRAM of 4K; 4K keeps small lettering sharp; 8K is for close-up screenshots.</small>
    ${vramNote(p)}
    ${S.trailers.some((t) => t.kind === "truck") ? `<div class="field"><span>Trucks: UV set</span><div class="seg" id="uvSeg">
      ${[["normal", "Normal"], ["both", "Normal + Alt UV"]].map(([v, l]) => `<button data-uv="${v}" class="${(p.truck_uvset || "normal") === v ? "on" : ""}">${l}</button>`).join("")}</div>
      <small>Jon ships every truck template twice. "Normal" matches his default and your MS2 truck skins; add Alt UV only if a cab shows the skin misplaced.</small></div>` : ""}
    <div class="field"><span>Finish · pick one or more</span><div class="seg" id="finishSeg">
      ${[["gloss", "Gloss"], ["metallic", "Metallic"], ["matte", "Matte"]].map(([v, l]) => `<button data-fin="${v}" class="${fins.includes(v) ? "on" : ""}">${l}</button>`).join("")}</div>
      <small>Each finish is its own entry in the paint shop (Gloss + Metallic is how Jon ships his skins). <b>This skin only</b> - every skin keeps its own finish. Default for every truck / trailer in this skin - one can have its own (Slot tab).${fins.includes("matte") ? " Matte: flat, no shine - it swaps the model's base texture for a flat one, the way MS2's matte skins do." : ""}</small></div>
    <div class="field"><span>Default paint colour</span><div class="colorrow"><input type="color" data-k="base_color" value="${esc(p.base_color)}">
      <label class="check"><input type="checkbox" data-k="color_locked" ${p.color_locked ? "checked" : ""}>Lock colour (players can't change it)</label><small class="hint" style="margin:0">This skin only. Default for every truck / trailer - one can have its own lock and finish (Slot tab).</small></div>
      <small>Shows through every transparent pixel of your artwork. Templates with their own colour (<b>Custom</b> above the preview) keep theirs.</small></div>
    <div class="group" ${met ? "" : "hidden"}><h2>Metallic</h2>
      <div class="grid2">
        <label class="field"><span>Flake colour</span><input type="color" data-m="flake_color" value="${esc(m.flake_color)}"></label>
        <label class="field"><span>Flip colour</span><input type="color" data-m="flip_color" value="${esc(m.flip_color)}"></label>
        <label class="field"><span>Flip strength</span><input type="number" step="0.05" min="0" data-m="flip_strength" value="${m.flip_strength}"></label>
        <label class="field"><span>Flake density</span><input type="number" step="0.1" min="0" data-m="flake_density" value="${m.flake_density}"></label>
        <label class="field"><span>Flake shininess</span><input type="number" step="1" min="0" data-m="flake_shininess" value="${m.flake_shininess}"></label>
        <label class="field"><span>Clearcoat rolloff</span><input type="number" step="0.1" min="0" data-m="flake_clearcoat_rolloff" value="${m.flake_clearcoat_rolloff}"></label>
      </div></div>`;
}

// ---- mod tab
function renderModTab() {
  const p = S.project, locked = Object.keys(p.built || {}).length > 0;
  $("#tab-mod").innerHTML = `
    <label class="field"><span>Mod name (Mod Manager)</span><input type="text" data-k="mod_name" value="${esc(p.mod_name)}"><small>Also the .scs file name.</small></label>
    <div class="grid2">
      <label class="field"><span>Author</span><input type="text" data-k="author" value="${esc(p.author)}"></label>
      <label class="field"><span>Version</span><input type="text" data-k="mod_version" value="${esc(p.mod_version)}"></label>
    </div>
    <label class="field"><span>Unit code</span><input type="text" maxlength="6" data-k="code" value="${esc(p.code)}" ${locked ? "disabled" : ""}>
      <small>${locked ? "Locked: saved games reference these paint jobs. Every slot keeps its ID on every rebuild." : "1-6 letters/digits, unique to this mod. Paint jobs become " + esc(p.code) + "01, " + esc(p.code) + "02 …"}</small></label>
    <label class="field"><span>AI traffic · how often your vehicles show up</span><select data-k="ai_spawn" data-num>
      ${[[0.3, "Less than SCS's own (0.3x)"], [1, "Like one of SCS's own (1x)"], [3, "Often (3x)"], [10, "A lot (10x - for testing)"]].map(([v, n]) => `<option value="${v}" ${(+p.ai_spawn || 1) === v ? "selected" : ""}>${n}</option>`).join("")}</select>
      <small>AI trucks and AI trailers with your skin, compared with the SCS truck / trailer they are copied from. There are dozens of SCS trucks and trailers in traffic, so at 1x you meet yours now and then - pick "A lot" to check a build quickly, then set it back before you share the mod.</small></label>
    <label class="field"><span>Description</span><textarea data-k="description" placeholder="Shown in the Mod Manager">${esc(p.description)}</textarea></label>
    <div class="field"><span>Output folder</span><div class="row gap"><input type="text" data-k="output_dir" value="${esc(p.output_dir)}" style="flex:1"><button class="small" id="browseOut">…</button></div>
      ${Object.entries(S.mod_dirs).map(([g, d]) => `<button class="link" data-out="${esc(d)}">Use ${g} mod folder</button>`).join(" ")}</div>
    <div class="group"><h2>Icons</h2><div class="icons">
      ${[["icon", `Shop icon · ${esc(p.paint_name)}`, "256 × 64"], ["mod_icon", "Mod Manager image", "276 × 162"]].map(([w, l, d]) => `
      <div class="iconbox" data-icon="${w}">
        <img src="/api/icon_preview?which=${w}&b=${artBust}&s=${encodeURIComponent(p.active_skin)}&n=${encodeURIComponent(w === "icon" ? p.paint_name : p.mod_name)}&c=${encodeURIComponent(p.base_color)}" alt="">
        <b>${l}</b><br>${d} · ${p[w] ? "custom" : "auto"}
        ${p[w] ? `<br><button class="link" data-iconclear="${w}">Use auto</button>` : ""}
      </div>`).join("")}</div>
      <small class="hint" style="margin:0">Click or drop an image. Left on auto, a simple name badge is generated.</small></div>
    <label class="check"><input type="checkbox" data-k="ask_overwrite" ${S.project.ask_overwrite !== false ? "checked" : ""}>Ask before replacing this project's own mod file (a mod from another project is always asked about)</label>
    <div class="group"><h2>Problems?</h2>
      <div class="row gap"><button class="small" id="saveReport">Save error report</button><button class="small ghost" id="openLog">Open log folder</button></div>
      <small class="hint" style="margin:0">SkinForge logs what it does (game files read, Load, builds, errors) in its log file. The error report is one zip with the log, the settings and this project file - no artwork. Send it along when something goes wrong.</small></div>`;
}

// ---- build box
function renderBuild() {
  const b = S.build, el = $("#buildBox");
  const withArt = Object.entries(S.project.slots).filter(([, c]) => c.art);
  const nl = withArt.filter(([k]) => k.startsWith("ruda_logos/") || k.startsWith("yard_signs/") || k.startsWith("player_logos/")).length;
  const liveSkins = S.project.skins.filter((x) => !x.deleted && S.project.skin_counts[x.id]);
  const ns = liveSkins.reduce((a, x) => a + S.project.skin_counts[x.id], 0), n = ns + nl;
  const what = [ns ? (liveSkins.length > 1 ? `${liveSkins.length} skins` : `${ns} slot${ns === 1 ? "" : "s"}`) : "", nl ? `${nl} logo${nl === 1 ? "" : "s"}/sign${nl === 1 ? "" : "s"}` : ""].filter(Boolean).join(" + ") || "nothing yet";
  const out = (S.project.output_dir || "") + "\\" + (S.project.mod_name || "") + ".scs";
  let html = `<button class="primary" id="buildBtn" ${b.running || !n ? "disabled" : ""}>${b.running ? '<span class="spin"></span>Building…' : `Build mod · ${what}`}</button>
    <div class="build-out">→ ${esc(out)}</div>
    ${S.out_exists && !b.running && !b.result ? `<div class="warn">${esc(S.project.mod_name || "")}.scs already exists - building replaces it. New mod? Change the Mod name first.</div>` : ""}`;
  if (b.running) html += `<div class="log">${esc(b.log[b.log.length - 1] || "")}</div>`;
  if (b.error) html += `<div class="build-err">${esc(b.error)}</div>`;
  if (b.result) {
    const r = b.result;
    html += `<div><span class="build-ok">Built</span> <span class="hint">${(r.size / 1048576).toFixed(1)} MB · ${r.units.length} items · ${r.seconds}s</span></div>
      ${r.warnings.map((w) => `<div class="warn">${esc(w)}</div>`).join("")}
      <div class="row gap"><button class="small" data-open="${esc(r.path)}">Show in folder</button></div>
      <p class="hint" style="margin:0">Start the game after building - it only scans the mod folder at launch.</p>`;
  }
  el.innerHTML = html;
  $("#buildTop").disabled = b.running || !n;
}

// ---------------------------------------------------------------- actions
async function setSetting(obj) {
  $("#savedState").textContent = "Saving…";
  await guard(async () => {
    const r = await api("/api/settings", obj); S.project = r.project; S.out_exists = r.out_exists;
    if (r.project_dir && r.project_dir !== S.project_dir) { S.project_dir = r.project_dir; S.project_file = r.project_file; S.recent = r.recent; renderRecent(); toast(`Project folder renamed: ${r.project_dir}`); }
  });
  $("#savedState").textContent = "Saved";
}
async function uploadArt(file, key, ovr = -1) {
  if (!file) return;
  toast(`Importing ${file.name}…`);
  await guard(async () => {
    const r = await api(`/api/art?key=${encodeURIComponent(key)}&ovr=${ovr}&filename=${encodeURIComponent(file.name)}`, await file.arrayBuffer(), true);
    S.project = r.project; artBust++;
    if (r.info.warnings.length) toast(r.info.warnings[0], true); else toast(`${file.name} assigned`);
    render();
  });
}
async function uploadIcon(file, which) {
  await guard(async () => { const r = await api(`/api/icon?which=${which}`, await file.arrayBuffer(), true); S.project = r.project; artBust++; render(); toast("Icon set"); });
}
async function dialog(kind, filter, title, initial) {
  const r = await api("/api/dialog", { kind, filter, title, initial: initial || "" });
  if (r.supported) return r.path;
  return prompt(title + "\n(paste the full path)", initial || "") || "";
}
let polling = false;
async function startBuild() {
  await guard(async () => {
    // a forgotten mod name would build right on top of another mod: ask first
    const c = await api("/api/build/check", {});
    if (c.exists && c.ask) {
      const when = c.mtime ? new Date(c.mtime * 1000).toLocaleString() : "";
      const msg = c.ours
        ? `"${base(c.path)}" already exists (built ${when || "earlier"}) - your previous build of this mod.\n\n` +
          `Building replaces it with the new version. If you meant to make a NEW mod, cancel and change the Mod name in the Mod tab first.\n\n` +
          `Replace it?\n\n(You can turn this question off for this project in the Mod tab.)`
        : `"${base(c.path)}" already exists${when ? " (built " + when + ")" : ""} and was NOT made from this project` +
          (c.other_project ? ` - it was built from another project (${base(c.other_project)}), maybe the one this project was copied from.`
            : c.codes && c.codes.length ? ` - it holds paint jobs with unit code ${c.codes.join(", ")}, this project uses ${S.project.code}.` : ".") +
          `\n\nBuilding now replaces that mod. To keep it, cancel and give this mod its own name in the Mod tab (Mod name).\n\nReplace it?`;
      if (!window.confirm(msg)) { logUi(`Build cancelled: would overwrite ${c.path}`); document.querySelector("[data-tab=mod]")?.click(); toast("Build cancelled - change the Mod name in the Mod tab"); return; }
      logUi(`Build: user chose to overwrite ${c.path} (codes ${(c.codes || []).join(",")})`);
    }
    await api("/api/build", {});
    S.build = { running: true, log: [], result: null, error: null }; renderBuild();
    if (polling) return; polling = true;
    while (true) {
      await new Promise((r) => setTimeout(r, 400));
      S.build = await api("/api/build"); renderBuild();
      if (!S.build.running) break;
    }
    polling = false;
    await refresh();
    if (S.build.error) toast(S.build.error, true); else toast("Mod built");
  });
}

// ---------------------------------------------------------------- events
document.addEventListener("click", async (e) => {
  const tg = e.target.closest("[data-toggle]");
  if (tg && !e.target.closest("button")) {
    const id = tg.dataset.toggle;
    setOpen(id, !opened.has(id)); renderTrailers();
    return;
  }
  const t = e.target.closest("button, .slot, .dropzone, .iconbox, [data-pick], [data-skin]");
  if (!$("#projMenu").hidden && !e.target.closest(".proj")) $("#projMenu").hidden = true;
  if (!t) return;
  if (t.matches(".slot")) { sel = t.dataset.key; picking = false; render(); fit(); return; }
  if (t.id === "projBtn") { $("#projMenu").hidden = !$("#projMenu").hidden; return; }
  if (t.dataset.act === "new") {
    $("#projMenu").hidden = true;
    const name = prompt("New project - name of the new skin (paint job name).\n\nStarts empty: no artwork, new mod name and unit code. Your current project stays saved and is under Recent.", "");
    if (name) await guard(async () => { S = await api("/api/project/new", { name }); sel = null; opened = null; artBust++; await refresh(); fit(); });
    return;
  }
  if (t.dataset.act === "open") {
    $("#projMenu").hidden = true;
    const p = await dialog("open", "project", "Open SkinForge project (project.json)", S.project_dir);
    if (p) await guard(async () => { await api("/api/project/open", { path: p }); sel = null; opened = null; artBust++; await refresh(); fit(); });
    return;
  }
  if (t.dataset.act === "rename") {
    $("#projMenu").hidden = true;
    const name = prompt("Rename project (also renames its folder in Documents\\SkinForge):", base(S.project_dir) || "");
    if (name) await guard(async () => { S = await api("/api/project/rename", { name }); render(); toast(`Project folder: ${S.project_dir}`); });
    return;
  }
  if (t.dataset.act === "folder") { $("#projMenu").hidden = true; api("/api/open", { path: S.project_dir }); return; }
  if (t.matches(".recent")) { $("#projMenu").hidden = true; await guard(async () => { await api("/api/project/open", { path: t.dataset.path }); sel = null; opened = null; artBust++; await refresh(); fit(); }); return; }
  if (t.id === "findSrc") {
    await guard(async () => {
      const r = await api("/api/find_sources");
      if (!r.found.length) return toast("No pack with template paint jobs found in your mod folders", true);
      S = await api("/api/sources", { sources: r.found }); render(); fit();
      toast(`Loaded ${r.found.length} pack${r.found.length > 1 ? "s" : ""}: ${r.found.map(base).join(", ")}`);
    });
    return;
  }
  if (t.id === "browseSrc") {
    const p = await dialog("open", "scs", "Choose the Open Definition .scs", Object.values(S.mod_dirs)[0]);
    if (p) await guard(async () => { S = await api("/api/sources", { sources: [...S.project.sources, p] }); render(); fit(); });
    return;
  }
  if (t.matches("[data-skin]") || t.closest("[data-skin]")) {
    const id = (t.closest("[data-skin]") || t).dataset.skin;
    if (id === S.project.active_skin) return;
    await guard(async () => { S = await api("/api/skins", { action: "select", id }); artBust++; render(); });
    return;
  }
  if (t.id === "skinAdd" || t.id === "skinDup") {
    const dup = t.id === "skinDup";
    const name = prompt(dup ? `Name of the copy of "${S.project.paint_name}":` : "Name of the new skin (shown in the shop):", dup ? S.project.paint_name + " 2" : "");
    if (name === null) return;
    await guard(async () => { S = await api("/api/skins", { action: dup ? "duplicate" : "add", name }); artBust++; render(); fit(); toast(`Editing skin: ${S.project.paint_name}`); });
    return;
  }
  if (t.id === "skinRen") {
    const name = prompt("Skin name (shown in the shop):", S.project.paint_name);
    if (name) await guard(async () => { S = await api("/api/skins", { action: "rename", id: S.project.active_skin, name }); render(); });
    return;
  }
  if (t.id === "skinDel") {
    if (!confirm(`Delete the skin "${S.project.paint_name}" and its artwork assignments?\n\nIf it was built before, its paint jobs stay in the mod as plain colour so saved games still load.`)) return;
    await guard(async () => { S = await api("/api/skins", { action: "delete", id: S.project.active_skin }); artBust++; render(); fit();
      toast(S.kept ? "Skin removed - kept as plain colour paint jobs for your saves" : "Skin deleted"); });
    return;
  }
  if (t.dataset.game) {
    if (t.dataset.game === (S.project.game || "ats")) return;
    await guard(async () => { S = await api("/api/game", { game: t.dataset.game }); sel = null; artBust++; await refresh(); fit();
      toast(`${t.dataset.game === "ets2" ? "ETS2" : "ATS"} project: its own packs, game files and mod folder`); });
    return;
  }
  if (t.dataset.loadtruck) {
    const isTrailer = t.dataset.vk === "trailer", isCar = t.dataset.vk === "car", isAi = t.dataset.vk === "ai", id = t.dataset.loadtruck;
    toast(`Reading the ${isTrailer ? "trailer" : isCar ? "car" : isAi ? "AI vehicle" : "truck"}'s models… (the first time can take a minute)`);
    t.disabled = true; t.textContent = "Loading…";
    await guard(async () => {
      S = await api(isTrailer ? "/api/scs/trailer" : isCar ? "/api/scs/car" : isAi ? "/api/scs/ai" : "/api/scs/truck", { id });
      setOpen(id, true); render();
      if (isAi) {
        const tr = S.trailers.find((x) => x.id === id && (x.kind.startsWith("scs_ai") || x.kind === "scs_escort"));
        toast(tr && tr.slots.length ? `${tr.name}: body sheet ready` : (tr && tr.error) || "No usable template for this AI model (SCS never laid it out for liveries, parts overlap) - left out of the list", !(tr && tr.slots.length));
        return;
      }
      if (isCar) {
        const tr = S.trailers.find((x) => x.id === id && x.kind === "scs_car");
        toast(tr && tr.slots.length ? `${tr.name}: body sheet ready` : (tr && tr.error) || "Nothing paintable on this car - left out of the list", !(tr && tr.slots.length));
        return;
      }
      if (isTrailer) {
        const tr = S.trailers.find((x) => x.id === id && x.kind === "scs");
        toast(tr && tr.slots.length ? `${tr.name}: ${tr.slots.length} parts` : (tr && tr.error) || "Nothing paintable on this trailer - left out of the list", !(tr && tr.slots.length));
        return;
      }
      const tr = S.trailers.find((x) => x.id === id && (x.kind === "scs_truck" || x.kind === "mod3d_truck"));
      toast(tr && tr.slots.length ? `${tr.name}: ${tr.slots.filter((x) => x.cab).length} cabs, ${tr.slots.filter((x) => !x.cab).length} parts` : (tr && tr.error) || "No paintable cab found for this truck - left out of the list", !(tr && tr.slots.length));
    });
    return;
  }
  if (t.id === "browseOfficial") {
    const p = await dialog("dir", null, "Pick your folder of paint templates (PNG, one folder per truck / trailer, e.g. 'ATS templates')", (S.official || {}).dir || "");
    if (p) await guard(async () => { S = await api("/api/official", { path: p }); render(); toast("Matching the template folder's sheets to the loaded trucks and trailers…"); refresh(); });
    return;
  }
  if (t.id === "reloadOfficial") {
    await guard(async () => { S = await api("/api/official", { reload: true }); artBust++; render(); toast("Reading the template folder again…"); refresh(); });
    return;
  }
  if (t.id === "rmOfficial") {
    await guard(async () => { S = await api("/api/official", { path: "" }); render(); });
    return;
  }
  if (t.id === "browseGame") {
    const p = await dialog("dir", null, "Pick your game install folder (or an extracted def + base_vehicle folder)", "");
    if (p) await guard(async () => {
      toast("Reading the game's trailers…");
      S = await api("/api/sources", { sources: [...S.project.sources.filter((x) => x !== p), p] }); render(); fit();
      const g = S.sources.find((x) => x.path === p);
      if (g && !g.ok) toast(g.error, true); else toast(`Game files loaded: ${S.trailers.filter((x) => x.kind === "scs").length} SCS trailers, ${S.trailers.filter((x) => x.kind === "scs_truck").length} SCS trucks - press Load on the ones you want to paint`);
    });
    return;
  }
  if (t.dataset.rm !== undefined) {
    const src = S.project.sources.filter((_, i) => i !== +t.dataset.rm);
    await guard(async () => { S = await api("/api/sources", { sources: src }); render(); });
    return;
  }
  if (t.dataset.bodies) {
    const tr = S.trailers.find((x) => x.id === t.dataset.bodies);
    await setSetting({ bodies_only: { [tr.id]: !bodiesOnly(tr) } });
    if (bodiesOnly(tr) && !shownSlots(tr).some((x) => x.key === sel) && trailerOf(sel) && trailerOf(sel).id === tr.id) sel = shownSlots(tr)[0].key;
    render();
    return;
  }
  if (t.dataset.tmpl) {   // pick which templates of this vehicle to export
    const tr = S.trailers.find((x) => x.id === t.dataset.tmpl), list = shownSlots(tr).filter((x) => x.has_template !== false);
    $("#tmplPickTitle").textContent = `Export templates · ${tr.name}`;
    $("#tmplPickList").innerHTML = list.map((x) => `<label class="check"><input type="checkbox" value="${esc(x.key)}" ${x.key === sel || list.length <= 3 ? "checked" : ""}> ${esc(x.label)}</label>`).join("");
    $("#tmplPick").hidden = false;
    return;
  }
  if (t.id === "tmplPickAll" || t.id === "tmplPickNone") { $$("#tmplPickList input").forEach((x) => { x.checked = t.id === "tmplPickAll"; }); return; }
  if (t.id === "tmplPickCancel") { $("#tmplPick").hidden = true; return; }
  if (t.dataset && t.dataset.opick) { await guard(() => openSheetPick(t.dataset.opick)); return; }
  if (t.dataset && t.dataset.opickauto) {
    await guard(async () => { S = await api("/api/official/pick", { key: t.dataset.opickauto, rel: null }); artBust++; render(); });
    return;
  }
  const oi = t.closest && t.closest(".opick-item");
  if (oi || t.id === "oPickOwn") {
    const key = $("#oPick").dataset.key;
    $("#oPick").hidden = true;
    await guard(async () => { S = await api("/api/official/pick", { key, rel: oi ? oi.dataset.orel : "" }); artBust++; render(); });
    toast(oi ? `Using ${oi.dataset.orel}` : "Using SkinForge's own template");
    return;
  }
  if (t.id === "oPickAuto") {
    const key = $("#oPick").dataset.key;
    $("#oPick").hidden = true;
    await guard(async () => { S = await api("/api/official/pick", { key, rel: null }); artBust++; render(); });
    return;
  }
  if (t.id === "oPickCancel" || t.id === "oPick") { $("#oPick").hidden = true; return; }
  if (t.id === "tmplPickGo" || t.dataset.tmplone) {
    const keys = t.dataset.tmplone ? [t.dataset.tmplone] : $$("#tmplPickList input").filter((x) => x.checked).map((x) => x.value);
    if (!keys.length) { toast("Tick at least one template"); return; }
    $("#tmplPick").hidden = true;
    toast("Exporting templates…");
    await guard(async () => { const r = await api("/api/templates", { keys }); toast(`${r.written.length} template${r.written.length === 1 ? "" : "s"} written to ${r.dir}`); });
    return;
  }
  if (t.id === "importLogos") {
    const p = await dialog("open", "scs", "Pick an existing logo mod (.scs) to import from", Object.values(S.mod_dirs)[0]);
    if (p) await guard(async () => { const r = await api("/api/logos/import", { path: p }); S.project = r.project; artBust++; render(); toast(`Imported ${r.imported.length} logos`); });
    return;
  }
  if (t.dataset.pick !== undefined) { pickTarget = { key: sel, ovr: +t.dataset.pick }; $("#fileInput").value = ""; $("#fileInput").click(); return; }
  if (t.dataset.clear !== undefined) { await guard(async () => { const r = await api("/api/art/clear", { key: sel, ovr: +t.dataset.clear }); S.project = r.project; artBust++; render(); }); return; }
  if (t.id === "shareBtn") {
    const to = $$(".share input:checked").map((i) => i.value);
    if (!to.length) return toast("Tick the slots to copy to");
    await guard(async () => { const r = await api("/api/art/share", { from: sel, to }); S.project = r.project; artBust++; render(); toast(`Copied to ${to.length} slot${to.length > 1 ? "s" : ""}`); });
    return;
  }
  if (t.dataset.uv) { await setSetting({ truck_uvset: t.dataset.uv }); renderPaintTab(); return; }
  if (t.dataset.vfin) {
    const tid = t.dataset.vt, cur = new Set(vfinOf(tid)), f = t.dataset.vfin;
    if (cur.has(f)) { if (cur.size === 1) { toast("Keep at least one finish"); return; } cur.delete(f); } else cur.add(f);
    const order = ["gloss", "metallic", "matte"].filter((x) => cur.has(x)).join("+");
    const same = order === finishesOf(S.project).join("+");
    await setSetting({ finish_by_vehicle: { [tid]: same ? "" : order } }); renderSlotTab(); renderTrailers(); renderBuild(); return;
  }
  if (t.dataset.sfin) {
    const s = slotOf(sel), tr = trailerOf(sel), cur = new Set(sfinOf(s, tr)), f = t.dataset.sfin;
    if (cur.has(f)) { if (cur.size === 1) { toast("Keep at least one finish"); return; } cur.delete(f); } else cur.add(f);
    const order = ["gloss", "metallic", "matte"].filter((x) => cur.has(x)).join("+");
    const same = order === vfinOf(tr.id).join("+");
    await setSetting({ finish_by_slot: { [sel]: same ? "" : order } }); renderSlotTab(); renderTrailers(); renderBuild(); return;
  }
  if (t.dataset.sfinreset) { await setSetting({ finish_by_slot: { [t.dataset.sfinreset]: "" } }); renderSlotTab(); renderTrailers(); renderBuild(); return; }
  if (t.dataset.strafficreset) { await setSetting({ traffic_by_slot: { [t.dataset.strafficreset]: "" } }); renderSlotTab(); renderTrailers(); renderBuild(); return; }
  if (t.dataset.vfinreset) { await setSetting({ finish_by_vehicle: { [t.dataset.vfinreset]: "" } }); renderSlotTab(); renderTrailers(); renderBuild(); return; }
  if (t.dataset.fin) {
    const cur = new Set(finishesOf(S.project)), f = t.dataset.fin;
    if (cur.has(f)) { if (cur.size === 1) { toast("Keep at least one finish"); return; } cur.delete(f); } else cur.add(f);
    const order = ["gloss", "metallic", "matte"].filter((x) => cur.has(x));
    await setSetting({ finish: order.join("+") }); renderPaintTab(); renderBuild(); return;
  }
  if (t.dataset.out) { await setSetting({ output_dir: t.dataset.out }); renderModTab(); renderBuild(); return; }
  if (t.id === "browseOut") { const p = await dialog("dir", null, "Where should the .scs go?", S.project.output_dir); if (p) { await setSetting({ output_dir: p }); renderModTab(); renderBuild(); } return; }
  if (t.id === "pickBtn") { picking = !picking; if (picking && mode === "template") mode = "paint"; renderStage(); renderSlotTab(); return; }
  if (t.dataset.flipdel !== undefined) { await guard(async () => { const r = await api("/api/flips", { key: sel, action: "remove", index: +t.dataset.flipdel }); S.project = r.project; artBust++; renderStage(); renderSlotTab(); }); return; }
  if (t.id === "extractUpright") {
    toast("Cutting the pieces out of your artwork…");
    await guard(async () => {
      const r = await api("/api/flips", { key: sel, action: "extract" });
      S.project = r.project; artBust++;
      toast("Upright sheet made - also saved as " + r.file.split(/[\\/]/).pop() + " in the project's art folder");
      renderStage(); renderSlotTab(); renderTrailers();
    });
    return;
  }
  if (t.id === "copyFlips") {
    const tr = trailerOf(sel), to = tr.slots.filter((x) => x.key !== sel && x.has_template).map((x) => x.key);
    await guard(async () => {
      const r = await api("/api/flips", { key: sel, action: "copy", to });
      S.project = r.project; artBust++;
      const got = Object.values(r.report).filter((n) => n > 0).length;
      toast(got ? `New marks added on ${got} of ${to.length} - existing marks were kept. Check each cab for pieces without an outline.`
                : "Nothing new to add - the other cabs already have these pieces marked, or have them elsewhere");
      renderTrailers();
    });
    return;
  }
  if (t.dataset.cabreset) { await guard(async () => { const r = await api("/api/slot/cabins", { key: sel, cabins: null }); S.project = r.project; renderSlotTab(); renderTrailers(); }); return; }
  if (t.dataset.iconclear) { await guard(async () => { const r = await api(`/api/icon/clear?which=${t.dataset.iconclear}`, {}); S.project = r.project; artBust++; renderModTab(); }); return; }
  if (t.dataset.icon) { pickTarget = { icon: t.dataset.icon }; $("#fileInput").value = ""; $("#fileInput").click(); return; }
  if (t.dataset.open) { api("/api/open", { path: t.dataset.open, select: true }); return; }
  if (t.id === "buildBtn" || t.id === "buildTop") { startBuild(); return; }
  if (t.dataset.link) { api("/api/link", { name: t.dataset.link }); return; }
  if (t.id === "openLog") { await guard(() => api("/api/log/open", {})); return; }
  if (t.id === "saveReport") { await guard(async () => { const r = await api("/api/log/report", {}); toast(`Error report saved: ${r.path}`); }); return; }
  if (t.dataset.vsize) {
    const vt = trailerOf(sel);
    if (!vt) return;
    const v = +t.dataset.vsize, follow = v === (+S.project.resolution || 4096);
    await setSetting({ res_by_vehicle: { [vt.id]: follow ? 0 : v } });
    artBust++; renderStage(); renderSlotTab(); renderPaintTab(); renderTrailers(); renderBuild();
    toast(`${vt.name}: ${v / 1024}K textures${follow ? " (same as the mod)" : ""}${v === 8192 ? " - four times the video memory of 4K" : ""}`);
    return;
  }
  if (t.dataset.paint === "default") {
    previewNone = false;
    if (slotColor(sel)) await guard(async () => { const r = await api("/api/slot/color", { key: sel, color: null }); S.project = r.project; toast("Back to the Paint job colour"); renderTrailers(); renderSlotTab(); });
    renderStage(); return;
  }
  if (t.dataset.paint === "none") { previewNone = !previewNone; renderStage(); return; }
  if (t.dataset.mode) { mode = t.dataset.mode; renderStage(); return; }
  if (t.id === "zoomFit") { fit(); return; }
  if (t.id === "zoom100") { const vp = $("#viewport").getBoundingClientRect(), im = $("#previewImg"); view.z = 1; view.x = (vp.width - (im.naturalWidth || 1024)) / 2; view.y = (vp.height - (im.naturalHeight || 1024)) / 2; view.fitted = false; applyView(); return; }
  if (t.closest("#tabs")) { $$("#tabs button").forEach((b) => b.classList.toggle("on", b === t)); $$(".tab-body").forEach((b) => (b.hidden = b.id !== "tab-" + t.dataset.tab)); return; }
});

$("#fileInput").addEventListener("change", (e) => {
  const f = e.target.files[0]; if (!f || !pickTarget) return;
  if (pickTarget.icon) uploadIcon(f, pickTarget.icon); else uploadArt(f, pickTarget.key, pickTarget.ovr);
});

document.addEventListener("change", (e) => {
  const t = e.target;
  if (t.id === "officialOff") {
    guard(async () => { S = await api("/api/official", { off: t.checked }); render(); });
    return;
  }
  if (t.dataset.k) {
    let v = t.type === "checkbox" ? t.checked : t.value;
    if (t.dataset.num !== undefined) v = +v;
    setSetting({ [t.dataset.k]: v }).then(() => {
      if (["paint_name"].includes(t.dataset.k)) { $("#projName").textContent = base(S.project_dir); renderSkins(); renderModTab(); }
      if (["base_color"].includes(t.dataset.k)) { renderStage(); renderTrailers(); }
      if (["code"].includes(t.dataset.k)) renderModTab();
      if (["mod_name", "output_dir"].includes(t.dataset.k)) renderBuild();
      if (t.dataset.k === "template_size") { renderSlotTab(); return; }
      if (t.dataset.k === "resolution") {   // SCS part sizes follow it: reload the list, show the VRAM note
        artBust++;
        return refresh().then(() => toast(`Texture size: ${v} × ${v}`));
      }
      renderBuild();
    });
  } else if (t.dataset.traffic) {
    const tid = t.dataset.vt, cur = trafficOf(tid);
    let own = cur !== "ai", ai = cur !== "own";
    if (t.dataset.traffic === "own") own = t.checked; else ai = t.checked;
    if (!own && !ai) { own = true; toast("Pick at least one - the skin has to go somewhere", true); }
    setSetting({ traffic_by_vehicle: { [tid]: own && ai ? "both" : ai ? "ai" : "own" } }).then(() => { renderSlotTab(); renderTrailers(); renderBuild(); });
  } else if (t.dataset.ailen) {
    const tr = trailerOf(sel), all = $$("[data-ailen]").map((x) => +x.dataset.ailen);
    const on = $$("[data-ailen]").filter((x) => x.checked).map((x) => +x.dataset.ailen);
    setSetting({ ai_lengths: { [tr.id]: on.length === all.length ? null : on } }).then(() => { renderSlotTab(); renderTrailers(); renderBuild(); });
  } else if (t.dataset.straffic) {
    const s = slotOf(sel), tr = trailerOf(sel), cur = strafficOf(s, tr);
    let own = cur !== "ai", ai = cur !== "own";
    if (t.dataset.straffic === "own") own = t.checked; else ai = t.checked;
    if (!own && !ai) { own = true; toast("Pick at least one - the skin has to go somewhere", true); }
    const v = own && ai ? "both" : ai ? "ai" : "own";
    setSetting({ traffic_by_slot: { [sel]: v === trafficOf(tr.id) ? "" : v } }).then(() => { renderSlotTab(); renderTrailers(); renderBuild(); });
  } else if (t.dataset.slock) {
    const v = t.value === "" ? null : t.value === "1";
    setSetting({ lock_by_slot: { [t.dataset.slock]: v } }).then(() => { renderSlotTab(); renderTrailers(); });
  } else if (t.dataset.vlock) {
    const v = t.value === "" ? null : t.value === "1";
    setSetting({ lock_by_vehicle: { [t.dataset.vlock]: v } }).then(() => { renderSlotTab(); renderTrailers(); });
  } else if (t.dataset.vres) {
    const tid = t.dataset.vres, v = +t.value;
    setSetting({ res_by_vehicle: { [tid]: v } }).then(() => {
      artBust++; renderSlotTab(); renderPaintTab(); renderTrailers(); renderBuild();
      toast(`${(S.trailers.find((x) => x.id === tid) || {}).name || tid}: ${v ? v / 1024 + "K textures" : "same size as the mod"}`);
    });
  } else if (t.dataset.m) {
    setSetting({ metallic: { [t.dataset.m]: t.type === "color" ? t.value : +t.value } });
  } else if (t.id === "previewColor") {
    previewNone = false;
    guard(async () => { const r = await api("/api/slot/color", { key: sel, color: t.value }); S.project = r.project;
      toast(slotOf(sel).kind === "scs" ? "Paint colour set for this trailer's paint job" : "Paint colour set for this template"); renderStage(); renderTrailers(); renderSlotTab(); });
  }
  else if (t.dataset.flipop !== undefined) {
    guard(async () => { const r = await api("/api/flips", { key: sel, action: "op", index: +t.dataset.flipop, op: t.value }); S.project = r.project; artBust++; renderStage(); renderSlotTab(); });
  }
  else if (t.dataset.cab !== undefined) {
    const cabs = $$("#cabList input[data-cab]").filter((i) => i.checked).map((i) => i.dataset.cab);
    guard(async () => { const r = await api("/api/slot/cabins", { key: sel, cabins: cabs }); S.project = r.project; renderSlotTab(); renderTrailers(); });
  }
});

// drag & drop: onto the stage, a slot row, a dropzone or an icon box
document.addEventListener("dragover", (e) => {
  e.preventDefault();
  const z = e.target.closest(".viewport, .slot, .dropzone, .iconbox");
  $$(".dragover").forEach((x) => x !== z && x.classList.remove("dragover"));
  if (z) z.classList.add("dragover");
});
document.addEventListener("dragleave", (e) => { if (!e.relatedTarget) $$(".dragover").forEach((x) => x.classList.remove("dragover")); });
document.addEventListener("drop", (e) => {
  e.preventDefault();
  $$(".dragover").forEach((x) => x.classList.remove("dragover"));
  const f = e.dataTransfer.files[0]; if (!f) return;
  const z = e.target.closest(".viewport, .slot, .dropzone, .iconbox");
  if (!z) return;
  if (z.matches(".iconbox")) return uploadIcon(f, z.dataset.icon);
  if (z.matches(".slot")) { sel = z.dataset.key; return uploadArt(f, sel); }
  if (z.matches(".dropzone")) return uploadArt(f, sel, +(z.dataset.pick ?? -1));
  if (sel) uploadArt(f, sel, mode === "upright" ? -2 : -1);
});

// zoom / pan
const vpEl = $("#viewport");
vpEl.addEventListener("wheel", (e) => {
  e.preventDefault();
  const r = vpEl.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
  const nz = Math.min(8, Math.max(0.1, view.z * (e.deltaY < 0 ? 1.15 : 1 / 1.15)));
  view.x = mx - ((mx - view.x) * nz) / view.z; view.y = my - ((my - view.y) * nz) / view.z; view.z = nz; view.fitted = false; applyView();
}, { passive: false });
let pan = null;
let downAt = null;
let boxEl = null;
function normAt(e) {
  const r = $("#previewImg").getBoundingClientRect();
  return { x: (e.clientX - r.left) / r.width, y: (e.clientY - r.top) / r.height };
}
vpEl.addEventListener("pointerdown", (e) => {
  downAt = { x: e.clientX, y: e.clientY };
  vpEl.setPointerCapture(e.pointerId);
  if (picking) return;   // in mark mode a drag draws a box instead of panning
  pan = { x: e.clientX - view.x, y: e.clientY - view.y }; vpEl.classList.add("panning");
});
vpEl.addEventListener("pointermove", (e) => {
  if (!picking || !downAt) return;
  if (Math.hypot(e.clientX - downAt.x, e.clientY - downAt.y) <= 5) return;
  if (!boxEl) { boxEl = document.createElement("div"); boxEl.className = "pick-box"; document.body.appendChild(boxEl); }
  Object.assign(boxEl.style, { left: Math.min(e.clientX, downAt.x) + "px", top: Math.min(e.clientY, downAt.y) + "px",
    width: Math.abs(e.clientX - downAt.x) + "px", height: Math.abs(e.clientY - downAt.y) + "px" });
});
vpEl.addEventListener("pointerup", async (e) => {
  const start = downAt; downAt = null;
  if (boxEl) { boxEl.remove(); boxEl = null; }
  if (!picking || !start) return;
  const dragged = Math.hypot(e.clientX - start.x, e.clientY - start.y) > 5;
  const a = normAt(e), b = normAt({ clientX: start.x, clientY: start.y });
  const clamp = (v) => Math.min(1, Math.max(0, v));
  let body;
  if (dragged) body = { key: sel, action: "add", op: "v", box: [clamp(b.x), clamp(b.y), clamp(a.x), clamp(a.y)] };
  else {
    if (a.x < 0 || a.y < 0 || a.x > 1 || a.y > 1) return;
    body = { key: sel, action: "add", op: "v", x: a.x, y: a.y };
  }
  if (pickBusy) return;
  pickBusy = true;
  toast("Finding the piece…");
  try {
    await guard(async () => {
      const res = await api("/api/flips", body);
      S.project = res.project; artBust++; toast("Piece marked - next one, or Esc when done");
    });
  } finally { pickBusy = false; }
  renderStage(); renderSlotTab(); renderTrailers();
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && picking) { picking = false; renderStage(); renderSlotTab(); } });
vpEl.addEventListener("pointermove", (e) => { if (!pan || picking) return; view.x = e.clientX - pan.x; view.y = e.clientY - pan.y; view.fitted = false; applyView(); });
vpEl.addEventListener("pointerup", () => { pan = null; vpEl.classList.remove("panning"); });
vpEl.addEventListener("pointercancel", () => { pan = null; downAt = null; if (boxEl) { boxEl.remove(); boxEl = null; } });
window.addEventListener("resize", () => { if (view.fitted) fit(); });
document.addEventListener("keydown", (e) => {
  if (e.target.matches("input, textarea, select")) return;
  if (e.key === "f" || e.key === "F") fit();
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    const all = slots(), i = all.findIndex((s) => s.key === sel);
    const n = all[Math.min(all.length - 1, Math.max(0, i + (e.key === "ArrowDown" ? 1 : -1)))];
    if (n) { sel = n.key; render(); e.preventDefault(); }
  }
});

// keep-alive + close detection
setInterval(() => fetch("/api/ping").catch(() => {}), 2000);
window.addEventListener("pagehide", () => navigator.sendBeacon("/api/bye"));

refresh().then(fit).catch((e) => toast(e.message, true));

// ---------------------------------------------------------------- colour popover
// The app window's native colour box (Windows) has no hex field and no eyedropper: every
// <input type="color"> opens this instead - hex box (paste #1a2b3c / 1a2b3c), screen eyedropper,
// recent colours, and the system picker as a fallback. Applying fires the input's normal "change".
const colorPop = { input: null, native: false };
// recent colours live in SkinForge's settings.json (S.recent_colors), not browser storage: the window
// gets a new port on every start, which empties localStorage
function recentColors() { return (S && S.recent_colors) || []; }
function rememberColor(c) {
  if (S) S.recent_colors = [c, ...recentColors().filter((x) => x !== c)].slice(0, 6);
  api("/api/colors", { color: c }).then((r) => { if (S && r.recent_colors) S.recent_colors = r.recent_colors; }).catch(() => {});
}
function normHex(v) {
  const m = String(v || "").trim().replace(/^#/, "").match(/^([0-9a-f]{3}|[0-9a-f]{6})$/i);
  if (!m) return null;
  const h = m[1].length === 3 ? m[1].split("").map((c) => c + c).join("") : m[1];
  return "#" + h.toLowerCase();
}
function openColorPop(input) {
  colorPop.input = input;
  let el = $("#colorPop");
  if (!el) {
    el = document.createElement("div");
    el.id = "colorPop"; el.className = "color-pop";
    document.body.appendChild(el);
  }
  const cur = input.value || "#9a9a9a";
  el.innerHTML = `<div class="row gap"><span class="cp-sw" id="cpSw" style="background:${cur}"></span>
      <input id="cpHex" type="text" spellcheck="false" value="${cur}" maxlength="7" placeholder="#rrggbb"></div>
    <div class="row gap">${"EyeDropper" in window ? `<button class="small" id="cpDrop" title="Pick a colour from anywhere on the screen">Eyedropper</button>` : ""}
      <button class="small ghost" id="cpSys">System picker…</button></div>
    ${recentColors().length ? `<div class="cp-label">Recent</div><div class="cp-recent">${recentColors().map((c) => `<button class="cp-r" data-cp="${c}" title="${c}" style="background:${c}"></button>`).join("")}</div>` : ""}
    <div class="row gap" style="justify-content:flex-end"><button class="small ghost" id="cpCancel">Cancel</button><button class="small" id="cpOk">Use colour</button></div>`;
  const r = input.getBoundingClientRect();
  el.hidden = false;
  el.style.left = Math.max(8, Math.min(window.innerWidth - 260, r.left)) + "px";
  el.style.top = (r.bottom + 6 + 210 > window.innerHeight ? Math.max(8, r.top - 216) : r.bottom + 6) + "px";
  const hex = $("#cpHex");
  hex.focus(); hex.select();
  hex.addEventListener("input", () => { const h = normHex(hex.value); hex.classList.toggle("bad", !h); if (h) $("#cpSw").style.background = h; });
  hex.addEventListener("keydown", (e) => { if (e.key === "Enter") applyColorPop(); if (e.key === "Escape") closeColorPop(); });
}
function closeColorPop() { const el = $("#colorPop"); if (el) el.hidden = true; colorPop.input = null; }
function applyColorPop(val) {
  const inp = colorPop.input, h = normHex(val || $("#cpHex").value);
  if (!inp) return;
  if (!h) { toast("Not a colour - use #rrggbb, e.g. #1a2b3c", true); return; }
  closeColorPop();
  rememberColor(h);
  inp.value = h;
  inp.dispatchEvent(new Event("input", { bubbles: true }));
  inp.dispatchEvent(new Event("change", { bubbles: true }));
}
document.addEventListener("click", async (e) => {
  const inp = e.target.closest && e.target.closest('input[type="color"]');
  if (inp) {
    if (colorPop.native) { colorPop.native = false; return; }   // "System picker…": let it open
    e.preventDefault(); e.stopPropagation();
    openColorPop(inp);
    return;
  }
  const pop = $("#colorPop");
  if (!pop || pop.hidden) return;
  if (!e.target.closest("#colorPop")) { closeColorPop(); return; }
  const id = e.target.id;
  if (id === "cpCancel") closeColorPop();
  else if (id === "cpOk") applyColorPop();
  else if (e.target.dataset.cp) applyColorPop(e.target.dataset.cp);
  else if (id === "cpDrop") {
    try { const r = await new window.EyeDropper().open(); $("#cpHex").value = r.sRGBHex; $("#cpSw").style.background = r.sRGBHex; }
    catch (err) { /* cancelled with Esc */ }
  } else if (id === "cpSys") {
    const inp2 = colorPop.input; closeColorPop();
    colorPop.native = true;
    try { inp2.showPicker(); colorPop.native = false; } catch (err) { inp2.click(); }
  }
}, true);
