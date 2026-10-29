"use strict";
/* local-ai-reels — интерфейс. Без сборщика и зависимостей: Tauri (withGlobalTauri) или фикстура для превью. */

const T = window.__TAURI__;
const api = T
  ? {
      invoke: (cmd, args) => T.core.invoke(cmd, args),
      listen: (ev, cb) => T.event.listen(ev, (e) => cb(e.payload)),
      src: (p) => (p ? T.core.convertFileSrc(p) : ""),
    }
  : window.REELS_FIXTURE;

const $ = (id) => document.getElementById(id);
const el = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "class") e.className = v;
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false) e.append(k instanceof Node ? k : String(k));
  return e;
};
const errText = (e) => (typeof e === "string" ? e : e && e.message ? e.message : String(e));

const STAGES = ["check", "accent", "voice", "align", "music", "build", "snapshot", "render", "finish"];
const STAGE_RU = { check: "проверка", accent: "ударения", voice: "голос", align: "таймкоды", music: "музыка",
  build: "сборка", snapshot: "кадры", render: "рендер", finish: "финиш" };
const GATE_RU = { script: "сценарий", accents: "ударения", take: "дубль", frames: "кадры" };
const THEMES = [
  { id: "blueprint", name: "Чертёж", a: "Невидимый", b: "поводок." },
  { id: "night", name: "Ночь", a: "Один", b: "из пяти" },
  { id: "paper", name: "Бумага", a: "Сорок три", b: "процента" },
  { id: "mono", name: "Моно", a: "Город", b: "кончается" },
];
// человеческие шаги режима «Просто»: этапы конвейера → шаг
const STEPS = [
  { name: "Пишем сценарий", stages: ["draft", "check"] },
  { name: "Озвучиваем", stages: ["accent", "voice", "align", "music"] },
  { name: "Собираем кадры", stages: ["build", "snapshot"] },
  { name: "Рендерим", stages: ["render", "finish"] },
  { name: "Готово", stages: [] },
];

const S = {
  info: null, settings: { mode: "simple", concurrency: 1, workspace: null },
  projects: [], current: null, view: null, jobs: [],
  logs: new Map(), logFilter: "", runningStage: new Map(),
  simple: { source: "topic", theme: "blueprint", reels: [], current: null },
};

// ---------- общее ----------
function banner(text) { const b = $("banner"); b.textContent = text || ""; b.hidden = !text; }
async function call(cmd, args) {
  try { return await api.invoke(cmd, args); } catch (e) { banner(errText(e)); throw e; }
}
async function saveSettings(patch) {
  S.settings = await call("set_settings", { settings: { ...S.settings, ...patch } });
}
function setMode(mode) {
  document.body.classList.toggle("expert-mode", mode === "expert");
  $("mode-simple").classList.toggle("on", mode === "simple");
  $("mode-expert").classList.toggle("on", mode === "expert");
  $("simple").hidden = mode !== "simple";
  $("expert").hidden = mode !== "expert";
}
const short = (p) => (p || "").split(/[\\/]/).filter(Boolean).slice(-1)[0] || p;

// ---------- журнал ----------
function pushLog(ev) {
  const arr = S.logs.get(ev.project) || [];
  arr.push(ev);
  if (arr.length > 3000) arr.splice(0, arr.length - 3000);
  S.logs.set(ev.project, arr);
  if (![...$("log-filter").options].some((o) => o.value === ev.project))
    $("log-filter").append(el("option", { value: ev.project }, short(ev.project)));
  if (!S.logFilter || S.logFilter === ev.project) appendLogLine($("log"), ev, !S.logFilter);
  const sr = S.simple.reels.find((r) => r.dir === ev.project);
  if (sr) simpleOnLog(sr, ev);
}
function logLine(ev, withProject) {
  const cls = ev.stream === "stderr" ? "err" : ev.stream === "cmd" ? "cmd" : ev.stage ? "stg" : "";
  return el("div", { class: cls }, withProject ? el("span", { class: "p" }, `[${short(ev.project)}] `) : null, ev.line);
}
function appendLogLine(box, ev, withProject) {
  const stick = box.scrollTop + box.clientHeight >= box.scrollHeight - 30;
  box.append(logLine(ev, withProject));
  while (box.childNodes.length > 3000) box.firstChild.remove();
  if (stick) box.scrollTop = box.scrollHeight;
}
function redrawLog() {
  const box = $("log");
  box.textContent = "";
  const evs = S.logFilter ? S.logs.get(S.logFilter) || [] : [...S.logs.values()].flat();
  for (const ev of evs.slice(-1500)) box.append(logLine(ev, !S.logFilter));
  box.scrollTop = box.scrollHeight;
}

// ---------- очередь ----------
function onJobs(jobs) {
  S.jobs = jobs;
  const run = jobs.filter((j) => j.state === "running").length;
  const q = jobs.length - run;
  $("queue-info").textContent = jobs.length ? `выполняется: ${run}${q ? `, в очереди: ${q}` : ""}` : "";
  renderProjects();
  if (S.view) renderActions();
}
const jobOf = (dir) => S.jobs.find((j) => j.project === dir);

async function onFinished(ev) {
  const sr = S.simple.reels.find((r) => r.dir === ev.project);
  if (sr) await simpleOnFinished(sr, ev);
  S.runningStage.delete(ev.project);
  if (S.settings.mode === "expert") {
    if (ev.outcome === "failed" || ev.outcome === "config") banner(`${short(ev.project)}: ${ev.message}${ev.hint ? " — " + ev.hint : ""}`);
    await refreshProjects();
    if (S.current === ev.project) await openProject(ev.project);
  }
}

// ---------- Эксперт: проекты ----------
function projectBadge(p) {
  const j = jobOf(p.dir);
  if (j) return j.state === "running" ? ["b-run", `в работе: ${STAGE_RU[S.runningStage.get(p.dir)] || "…"}`] : ["b-todo", "в очереди"];
  if (p.error) return ["b-bad", "ошибка"];
  const w = (p.gates || []).find((g) => g.status === "waiting");
  if (w) return ["b-wait", `ждёт: ${GATE_RU[w.gate] || w.gate}`];
  const st = p.stages || [];
  if (st.length && st.every((s) => s.status === "done")) return ["b-ok", "готово"];
  if (st.some((s) => s.status === "done")) return ["b-todo", "частично"];
  return ["b-todo", "не начат"];
}
function renderProjects() {
  const ul = $("projects");
  ul.textContent = "";
  if (!S.projects.length) {
    ul.append(el("li", { class: "muted" }, S.settings.workspace ? "В папке нет проектов с reel.yaml" : "Выберите рабочую папку"));
    return;
  }
  for (const p of S.projects) {
    const [cls, txt] = projectBadge(p);
    const done = (p.stages || []).filter((s) => s.status === "done").length;
    ul.append(el("li", { class: p.dir === S.current ? "on" : "", onclick: () => openProject(p.dir) },
      el("div", { class: "t" }, el("span", {}, p.title || p.slug || short(p.dir)), el("span", { class: `badge ${cls}` }, txt)),
      el("div", { class: "s" }, `${short(p.dir)} · этапов ${done}/${(p.stages || []).length || STAGES.length}`)));
  }
}
async function refreshProjects() {
  if (!S.settings.workspace) { S.projects = []; renderProjects(); return; }
  $("ws-path").textContent = S.settings.workspace;
  try { S.projects = await call("list_projects", { workspace: S.settings.workspace }); } catch { S.projects = []; }
  renderProjects();
}

// ---------- Эксперт: проект ----------
async function openProject(dir) {
  S.current = dir;
  renderProjects();
  try { S.view = await call("open_project", { dir }); } catch { return; }
  renderProject();
}
function renderActions() {
  const j = S.view && jobOf(S.view.status.dir || S.current);
  $("pv-run").disabled = !!j;
  $("pv-yes").disabled = !!j;
  $("pv-stop").disabled = !j;
  $("pv-stop").textContent = j && j.state === "queued" ? "Убрать из очереди" : "Остановить";
  renderStages();
}
function renderStages() {
  const st = S.view.status;
  const box = $("pv-stages");
  box.textContent = "";
  const running = jobOf(S.current) && S.runningStage.get(S.current);
  for (const name of STAGES) {
    const rows = (st.stages || []).filter((r) => r.stage === name);
    let status = rows.length ? (rows.every((r) => r.status === "done") ? "done" : rows.some((r) => r.status === "stale") ? "stale" : "todo") : "todo";
    if (running === name) status = "running";
    const gate = (st.gates || []).find((g) => g.stage === name);
    const edits = rows.filter((r) => r.edit).length;
    box.append(el("div", { class: `st ${status}`, title: rows.map((r) => `${r.key}: ${r.status} ${r.at || ""}`).join("\n") || name },
      STAGE_RU[name], el("small", {}, edits ? `${rows.filter((r) => r.status === "done").length}/${edits} монт.` : name),
      gate && gate.status !== "n/a" ? el("span", { class: `g ${gate.status}`, title: `контрольная точка «${gate.gate}»: ${gate.status}` }) : null));
  }
}
function accentHtml(text) {
  // «+» перед ударной гласной → выделенная гласная
  const out = el("div", { class: "accent-text" });
  const parts = text.split(/\+(.)/u);
  parts.forEach((p, i) => out.append(i % 2 ? el("b", {}, p) : p));
  return out;
}
const pct = (r) => (r === null || r === undefined ? "—" : `${Math.round(r * 100)}%`);
const sec = (d) => (d ? `${d.toFixed(1)} с` : "—");

function renderGate() {
  const v = S.view, box = $("pv-gate");
  const gate = (v.status.gates || []).find((g) => g.status === "waiting");
  box.textContent = "";
  box.hidden = !gate;
  if (!gate) return;
  const f = v.files;
  box.append(el("h2", {}, `Ждёт подтверждения: ${GATE_RU[gate.gate] || gate.gate}`));
  if (gate.gate === "script") {
    box.append(el("p", {}, `Проверьте сценарий: ${f.totalWords} слов${f.limit ? ` из ${f.limit}` : ""}, блоков ${f.blocks.length}. Блоки — ниже, в разделе «Сценарий».`));
  } else if (gate.gate === "accents") {
    if (f.accents) {
      box.append(el("p", { class: "muted small" }, "Ударная гласная выделена. Ошибку правьте в reel.yaml: voice.accents: {\"слово\": \"сл+ово\"}."));
      box.append(accentHtml(f.accents.master));
    } else box.append(el("p", { class: "muted" }, "Нет build/voice/accents.json"));
  } else if (gate.gate === "take") {
    const tb = el("tbody");
    for (const t of f.takes) {
      tb.append(el("tr", { class: t.chosen ? "chosen" : "" },
        el("td", {}, String(t.voice ?? "")), el("td", {}, String(t.seed ?? "")), el("td", {}, pct(t.ratio)), el("td", {}, sec(t.duration)),
        el("td", {}, el("audio", { controls: true, preload: "none", src: api.src(t.path) })),
        el("td", { class: "small", title: t.heard || "" }, (t.heard || "").slice(0, 60) + ((t.heard || "").length > 60 ? "…" : "")),
        el("td", {}, t.chosen ? el("span", { class: "badge b-ok" }, "в master.wav") :
          el("button", { onclick: () => chooseTake(t.file) }, "Выбрать"))));
    }
    box.append(el("table", {}, el("thead", {}, el("tr", {}, ["голос", "seed", "сверка", "длит.", "прослушать", "услышано", ""].map((h) => el("th", {}, h)))), tb));
    if (f.master) box.append(el("p", { class: "small muted" }, "Сейчас в монтаже: ", el("audio", { controls: true, preload: "none", src: api.src(f.master) })));
  } else if (gate.gate === "frames") {
    for (const [edit, out] of Object.entries(v.status.outputs || {})) {
      box.append(el("h3", {}, `Монтаж ${edit}`));
      if (out.frames) box.append(el("img", { class: "sheet", src: api.src(out.frames), alt: `контактный лист ${edit}` }));
      const issues = (f.qa || {})[edit] || [];
      box.append(issues.length
        ? el("ul", { class: "qa" }, issues.map((i) => el("li", {}, el("span", { class: `sev ${i.severity}` }, i.severity), i.t !== null && i.t !== undefined ? `${i.t.toFixed(1)} с · ` : "", `${i.kind}: ${i.text}`)))
        : el("p", { class: "small muted" }, "Автопроверка вёрстки: замечаний нет."));
    }
  }
  box.append(el("div", { class: "row" },
    el("button", { class: "primary", onclick: () => approveGate(gate.gate, true) }, "Подтвердить и продолжить"),
    el("button", { onclick: () => approveGate(gate.gate, false) }, "Только подтвердить")));
}
function mdToNodes(md) {
  const box = el("div");
  for (const line of md.split("\n")) {
    const h = line.match(/^(#{1,3})\s+(.*)/);
    if (h) box.append(el(`h${h[1].length}`, {}, h[2]));
    else if (/^\s*[-*]\s+/.test(line)) box.append(el("div", {}, "• " + line.replace(/^\s*[-*]\s+/, "")));
    else if (line.trim()) box.append(el("div", {}, line));
  }
  return box;
}
function renderProject() {
  const v = S.view, st = v.status, f = v.files;
  $("pv-empty").hidden = true;
  $("pv").hidden = false;
  $("pv-title").textContent = st.title || f.title || st.slug || short(f.dir);
  $("pv-dir").textContent = f.dir;
  const err = st.error || f.yamlError;
  $("pv-error").hidden = !err;
  $("pv-error").textContent = err || "";
  renderActions();
  renderGate();
  $("pv-words").textContent = `${f.totalWords}${f.limit ? ` / ${f.limit}` : ""} слов`;
  const ol = $("pv-blocks");
  ol.textContent = "";
  for (const b of f.blocks)
    ol.append(el("li", {}, el("div", { class: "h" }, el("span", {}, `${b.index}. ${b.name || "блок"} · ${b.scene || "—"}`), el("span", {}, `${b.words} сл.`)), b.text));
  const res = $("pv-results");
  res.textContent = "";
  for (const [edit, out] of Object.entries(st.outputs || {})) {
    if (!out.mp4) continue;
    res.append(el("figure", {}, el("video", { controls: true, preload: "metadata", src: api.src(out.mp4) }),
      el("figcaption", {}, el("span", {}, edit), el("button", { class: "link", onclick: () => call("reveal", { path: out.mp4 }) }, "Показать в папке"))));
  }
  if (!res.childNodes.length) res.append(el("p", { class: "muted" }, "Готовых видео пока нет."));
  $("pv-report-box").hidden = !f.reportText;
  $("pv-report").textContent = "";
  if (f.reportText) $("pv-report").append(mdToNodes(f.reportText));
}
async function chooseTake(file) {
  await call("choose_take", { dir: S.current, file });
  await openProject(S.current);
  banner(`Дубль ${short(file)} теперь в master.wav. Подтвердите «дубль», чтобы продолжить.`);
}
async function approveGate(gate, andRun) {
  const msg = await call("approve", { dir: S.current, gate });
  banner(msg);
  if (andRun) await startRun(false);
  else await openProject(S.current);
}
async function startRun(yes) {
  await call("start_run", { dir: S.current, options: { yes } });
  S.logFilter = S.current;
  $("log-filter").value = S.current;
  redrawLog();
}

// ---------- Просто ----------
function renderThemes() {
  const box = $("themes");
  box.textContent = "";
  for (const t of THEMES)
    box.append(el("button", { class: `theme t-${t.id} ${S.simple.theme === t.id ? "on" : ""}`, onclick: () => { S.simple.theme = t.id; renderThemes(); } },
      el("div", { class: "pv" }, el("span", {}, t.a), el("i", {}, t.b)), el("span", { class: "nm" }, t.name)));
}
function setSource(src) {
  S.simple.source = src;
  $("src-topic").classList.toggle("on", src === "topic");
  $("src-text").classList.toggle("on", src === "text");
  $("topic-box").hidden = src !== "topic";
  $("text-box").hidden = src !== "text";
}
function textStats() {
  const paras = $("own-text").value.split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean);
  const words = $("own-text").value.split(/\s+/).filter((w) => /[\p{L}\p{N}]/u.test(w)).length;
  $("text-stats").textContent = `абзацев: ${paras.length} из 4 · слов: ${words} (до 60)`;
}
async function makeReel() {
  banner("");
  const req = { theme: S.simple.theme };
  if (S.simple.source === "topic") req.topic = $("topic").value.trim();
  else req.text = $("own-text").value;
  let dir;
  try { dir = await api.invoke("simple_make", { request: req }); } catch (e) { showSimpleError(errText(e)); return; }
  const r = { dir, title: req.topic || ($("own-text").value.trim().split(/\s+/).slice(0, 5).join(" ")), step: req.topic ? 0 : 0,
    stage: null, done: false, error: null, hint: null, log: [], outputs: {}, edit: null };
  S.simple.reels.unshift(r);
  S.simple.current = r;
  renderSimple();
}
function showSimpleError(text) {
  const r = { dir: "", title: "Не получилось начать", step: 0, error: text, hint: null, log: [], outputs: {}, failed: true };
  S.simple.current = r;
  renderSimple();
}
function stepOf(stage) { return Math.max(0, STEPS.findIndex((s) => s.stages.includes(stage))); }
function simpleOnLog(r, ev) {
  r.log.push(ev);
  if (ev.stream === "cmd" && ev.line.includes(" draft ")) r.step = 0;
  if (ev.stage) { r.stage = ev.stage.stage; r.step = Math.max(r.step, stepOf(ev.stage.stage)); }
  r.last = ev.line;
  if (S.simple.current === r) {
    renderSimpleProgress(r);
    if (!$("sp-log").hidden) appendLogLine($("sp-log"), ev, false);
  }
}
async function simpleOnFinished(r, ev) {
  if (ev.outcome === "ok") {
    r.done = true;
    r.step = STEPS.length - 1;
    try {
      const v = await api.invoke("open_project", { dir: r.dir });
      r.outputs = v.status.outputs || {};
      r.title = v.status.title || r.title;
    } catch (e) { r.error = errText(e); }
  } else if (ev.outcome === "stopped") {
    r.failed = true; r.error = "Остановлено.";
  } else if (ev.outcome === "gate") {
    r.failed = true; r.error = "Конвейер ждёт подтверждения — откройте проект в режиме «Эксперт».";
  } else {
    r.failed = true; r.error = ev.message; r.hint = ev.hint;
  }
  renderSimple();
}
function renderSimpleProgress(r) {
  $("sp-title").textContent = r.title;
  const ol = $("sp-steps");
  ol.textContent = "";
  STEPS.forEach((s, i) => {
    const cls = r.done || i < r.step ? "done" : i === r.step ? (r.failed ? "fail" : "now") : "";
    ol.append(el("li", { class: cls }, s.name));
  });
  const doneStages = r.stage ? STAGES.indexOf(r.stage) + 1 : 0;
  $("sp-bar").style.width = `${r.done ? 100 : Math.max(4, Math.round((doneStages / (STAGES.length + 1)) * 100))}%`;
  $("sp-line").textContent = r.failed ? "" : r.last || "Запускаем…";
  $("sp-error").hidden = !r.error;
  $("sp-error").textContent = "";
  if (r.error) $("sp-error").append(el("b", {}, r.hint || "Что-то пошло не так"), r.error);
  $("sp-stop").hidden = !!(r.failed || r.done) || !r.dir;
  $("sp-retry").hidden = !r.failed;
}
function renderSimple() {
  const r = S.simple.current;
  $("simple-ws").textContent = S.settings.workspace || (S.info && S.info.defaultWorkspace) || "";
  $("simple-empty").hidden = !!r;
  $("simple-progress").hidden = !r || r.done;
  $("simple-done").hidden = !r || !r.done;
  if (r && !r.done) {
    renderSimpleProgress(r);
    const lg = $("sp-log");
    lg.textContent = "";
    for (const ev of r.log.slice(-500)) lg.append(logLine(ev, false));
  }
  if (r && r.done) {
    $("sd-title").textContent = r.title;
    const edits = Object.entries(r.outputs).filter(([, o]) => o.mp4);
    if (!r.edit || !r.outputs[r.edit]) r.edit = edits.length ? edits[0][0] : null;
    const seg = $("sd-edits");
    seg.textContent = "";
    if (edits.length > 1)
      for (const [e] of edits) seg.append(el("button", { class: e === r.edit ? "on" : "", onclick: () => { r.edit = e; renderSimple(); } }, e));
    const mp4 = r.edit ? r.outputs[r.edit].mp4 : null;
    const v = $("sd-video");
    if (v.dataset.path !== mp4) { v.dataset.path = mp4 || ""; v.src = api.src(mp4); if (r.poster) v.poster = r.poster; }
    $("sd-save").disabled = !mp4;
  }
  const chips = $("simple-list");
  chips.textContent = "";
  if (S.simple.reels.length > 1)
    for (const x of S.simple.reels)
      chips.append(el("button", { class: `chip ${x === r ? "on" : ""}`, onclick: () => { S.simple.current = x; renderSimple(); } },
        `${x.done ? "✓" : x.failed ? "!" : "…"} ${x.title.slice(0, 28)}`));
}

// ---------- запуск ----------
async function init() {
  $("mode-simple").onclick = async () => { setMode("simple"); renderSimple(); await saveSettings({ mode: "simple" }); };
  $("mode-expert").onclick = async () => { setMode("expert"); await saveSettings({ mode: "expert" }); await refreshProjects(); };
  $("concurrency").onchange = (e) => saveSettings({ concurrency: Number(e.target.value) });
  $("ws-pick").onclick = async () => {
    const p = await call("pick_folder", { title: "Папка с проектами роликов" });
    if (p) { await saveSettings({ workspace: p }); await refreshProjects(); }
  };
  $("simple-ws").onclick = async (e) => {
    e.preventDefault();
    const p = await call("pick_folder", { title: "Куда складывать ролики" });
    if (p) { await saveSettings({ workspace: p }); renderSimple(); }
  };
  $("ws-refresh").onclick = refreshProjects;
  $("pv-run").onclick = () => startRun(false);
  $("pv-yes").onclick = () => startRun(true);
  $("pv-stop").onclick = () => call("stop_run", { dir: S.current });
  $("pv-reveal").onclick = () => call("reveal", { path: S.current });
  $("log-filter").onchange = (e) => { S.logFilter = e.target.value; redrawLog(); };
  $("log-clear").onclick = () => { if (S.logFilter) S.logs.delete(S.logFilter); else S.logs.clear(); redrawLog(); };
  $("btn-new").onclick = async () => {
    if (!S.settings.workspace) return banner("Сначала выберите рабочую папку.");
    const sel = $("new-from");
    sel.length = 1;
    for (const ex of await call("list_examples")) sel.append(el("option", { value: ex.dir }, `пример: ${ex.name}`));
    $("dlg-new").showModal();
  };
  $("dlg-new").addEventListener("close", async () => {
    if ($("dlg-new").returnValue !== "ok") return;
    const dir = `${S.settings.workspace}/${$("new-name").value.trim()}`;
    const d = await call("new_project", { dir, from: $("new-from").value || null });
    await refreshProjects();
    await openProject(d);
  });
  $("btn-draft").onclick = () => (S.settings.workspace ? $("dlg-draft").showModal() : banner("Сначала выберите рабочую папку."));
  $("dlg-draft").addEventListener("close", async () => {
    if ($("dlg-draft").returnValue !== "ok") return;
    const topic = $("draft-topic").value.trim();
    const name = $("draft-name").value.trim() || topic.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-|-$/g, "").slice(0, 40);
    await call("draft_project", { topic, dir: `${S.settings.workspace}/${name}` });
    banner(`Черновик «${topic}» пишется — следите за журналом.`);
  });
  $("src-topic").onclick = () => setSource("topic");
  $("src-text").onclick = () => setSource("text");
  $("own-text").oninput = textStats;
  $("make").onclick = makeReel;
  $("sp-stop").onclick = () => S.simple.current && call("stop_run", { dir: S.simple.current.dir });
  $("sp-retry").onclick = () => { S.simple.current = null; renderSimple(); };
  $("sp-log-toggle").onclick = () => { $("sp-log").hidden = !$("sp-log").hidden; renderSimple(); };
  $("sd-again").onclick = () => { S.simple.current = null; renderSimple(); };
  $("sd-open").onclick = () => { const r = S.simple.current; call("reveal", { path: (r.edit && r.outputs[r.edit].mp4) || r.dir }); };
  $("sd-save").onclick = async () => {
    const r = S.simple.current;
    const p = await call("save_video", { src: r.outputs[r.edit].mp4 });
    if (p) banner(`Сохранено: ${p}`);
  };

  await api.listen("run-log", pushLog);
  await api.listen("run-finished", onFinished);
  await api.listen("runs-changed", onJobs);

  try { S.info = await api.invoke("app_info"); } catch (e) { banner(errText(e)); S.info = { settings: S.settings }; }
  S.settings = S.info.settings;
  if (S.info.pythonError) banner(S.info.pythonError);
  $("concurrency").value = String(S.settings.concurrency || 1);
  renderThemes();
  textStats();
  setMode(S.settings.mode);
  renderSimple();
  onJobs(await api.invoke("list_runs"));
  if (S.settings.mode === "expert") await refreshProjects();
  if (window.REELS_FIXTURE && window.REELS_FIXTURE.afterInit) await window.REELS_FIXTURE.afterInit(S, { openProject, renderSimple, setMode, pushLog, setSource });
}
init();
