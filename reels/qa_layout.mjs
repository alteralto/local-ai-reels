// Автопроверка вёрстки кадров по DOM: открывает index.html композиции в headless-браузере,
// для каждого момента t прокручивает GSAP-таймлайн и ищет обрезанный, вылезший за кадр и наложенный текст.
// Вход: путь к JSON {html, times, browser, width, height, capTop} в argv[2]. Выход: JSON {issues} в stdout.
// Драйвер: playwright → playwright-core → puppeteer-core → puppeteer (ищется через NODE_PATH).
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const require = createRequire(import.meta.url);
const args = JSON.parse(readFileSync(process.argv[2], "utf-8"));

function loadDriver() {
  for (const name of ["playwright", "playwright-core"]) {
    try { return { kind: "pw", mod: require(name) }; } catch (e) { /* дальше */ }
  }
  for (const name of ["puppeteer-core", "puppeteer"]) {
    try { return { kind: "pp", mod: require(name) }; } catch (e) { /* дальше */ }
  }
  return null;
}

// Код, который выполняется в странице: видимость сцен по data-start/data-duration, seek, замеры.
function measure(o) {
  const { t, W, H, capTop } = o;
  const zones = o.zones || [];
  const hasCaps = !!document.querySelector(".cap2 .chunk");
  const EDGE = 24;
  for (const el of document.querySelectorAll(".clip")) {
    if (el.tagName === "AUDIO" || el.tagName === "VIDEO") continue;
    const s = parseFloat(el.dataset.start || "0"), d = parseFloat(el.dataset.duration || "1e9");
    el.style.display = (s <= t && t < s + d) ? "" : "none";
  }
  const tl = window.__timelines && (window.__timelines.main || Object.values(window.__timelines)[0]);
  if (tl && tl.seek) { tl.seek(t, false); }
  // элементы, которые в момент t едут/появляются: их положение переходное (маска-появление, уход сцены)
  const MOVE = ["x", "y", "xPercent", "yPercent", "scale", "scaleX", "scaleY", "rotation", "left", "top", "opacity", "autoAlpha"];
  const moving = new Set();
  if (tl && tl.getChildren) {
    for (const tw of tl.getChildren(true, true, false)) {
      const st = tw.startTime(), en = st + tw.totalDuration();
      if (!(st <= t && t < en) || !tw.vars || !MOVE.some((k) => k in tw.vars)) continue;
      for (const el of tw.targets()) if (el && el.nodeType === 1) moving.add(el);
    }
  }
  const isMoving = (el) => { for (let e = el; e; e = e.parentElement) if (moving.has(e)) return true; return false; };

  const opacity = (el) => {
    let o = 1;
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (cs.display === "none" || cs.visibility === "hidden") return 0;
      o *= parseFloat(cs.opacity || "1");
    }
    return o;
  };
  const inter = (a, b) => {
    const w = Math.min(a.r, b.r) - Math.max(a.l, b.l), h = Math.min(a.b, b.b) - Math.max(a.t, b.t);
    return w > 0 && h > 0 ? w * h : 0;
  };
  const area = (a) => Math.max(0, a.r - a.l) * Math.max(0, a.b - a.t);
  const rnd = (a) => [Math.round(a.l), Math.round(a.t), Math.round(a.r - a.l), Math.round(a.b - a.t)];

  // текстовые элементы: есть собственный непустой текстовый узел; текст внутри svg — часть графики
  const texts = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const byEl = new Map();
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (!n.textContent.trim()) continue;
    const el = n.parentElement;
    if (!el || el.closest("svg, script, style, head, noscript")) continue;
    if (!byEl.has(el)) byEl.set(el, []);
    byEl.get(el).push(n);
  }
  for (const [el, nodes] of byEl) {
    const op = opacity(el);
    if (op <= 0.05) continue;
    const fs = parseFloat(getComputedStyle(el).fontSize) || 16;
    let box = null, raw = null;
    for (const n of nodes) {
      const r = document.createRange();
      r.selectNodeContents(n);
      for (const q of r.getClientRects()) {
        if (q.width < 1 || q.height < 1) continue;
        // «чернильная» высота строки: середина строки ± 0.45 кегля (content area шрифта выше строки)
        const c = (q.top + q.bottom) / 2, hh = Math.min(q.height / 2, fs * 0.45);
        const ink = { l: q.left, r: q.right, t: c - hh, b: c + hh };
        box = box ? { l: Math.min(box.l, ink.l), r: Math.max(box.r, ink.r), t: Math.min(box.t, ink.t), b: Math.max(box.b, ink.b) } : ink;
        raw = raw ? { l: Math.min(raw.l, q.left), r: Math.max(raw.r, q.right), t: Math.min(raw.t, q.top), b: Math.max(raw.b, q.bottom) } : { l: q.left, r: q.right, t: q.top, b: q.bottom };
      }
    }
    if (!box) continue;
    // предки с overflow ≠ visible: текст целиком за границей — ещё не выехал (маска-появление), это не дефект;
    // частично за границей или шире контейнера — обрезан
    let hidden = false, clipper = null;
    for (let a = el; a && a !== document.body; a = a.parentElement) {
      const cs = getComputedStyle(a);
      const hidX = cs.overflowX !== "visible", hidY = cs.overflowY !== "visible";
      if (!hidX && !hidY) continue;
      const ar = a.getBoundingClientRect(), tol = 2;
      const c = { l: hidX ? ar.left : -1e9, r: hidX ? ar.right : 1e9, t: hidY ? ar.top : -1e9, b: hidY ? ar.bottom : 1e9 };
      if (inter(box, c) <= 0) { hidden = true; break; }
      const scene = a.classList.contains("clip") || a.id === "root";  // у сцены scrollWidth растёт и от графики
      const out = box.l < c.l - tol || box.r > c.r + tol || box.t < c.t - tol || box.b > c.b + tol ||
                  (hidX && !scene && a.scrollWidth > a.clientWidth + tol);
      if (out && !clipper) clipper = a;
    }
    if (hidden) continue;
    const mv = isMoving(el);
    texts.push({ clipper: mv ? null : clipper, moving: mv, el, box, raw, fs, text: nodes.map((n) => n.textContent).join(" ").replace(/\s+/g, " ").trim().slice(0, 80),
                 cap: !!el.closest(".cap2"), chunk: el.closest(".chunk") });
  }

  const issues = [];
  const add = (kind, severity, x, detail) => issues.push({ t, kind, severity: x.moving ? "warning" : severity, text: x.text,
                                                           rect: rnd(x.box), detail: x.moving ? `${detail} (в движении)` : detail });

  for (const x of texts) {
    const b = x.box;
    // 1) за кадром / у самого края
    if (b.l < -1 || b.r > W + 1 || b.t < -1 || b.b > H + 1) {
      add("out_of_frame", "error", x, `текст выходит за кадр ${W}×${H}`);
    } else if (b.l < EDGE || b.r > W - EDGE) {
      add("edge", "warning", x, `текст ближе ${EDGE} px к краю кадра`);
    }
    // 2) обрезка предком с overflow: hidden/clip/auto/scroll (полностью скрытый текст отброшен выше)
    if (x.clipper) {
      const a = x.clipper;
      const who = a.id ? `#${a.id}` : `.${[...a.classList].join(".") || a.tagName.toLowerCase()}`;
      add("clipped", "error", x, `текст обрезан контейнером ${who} (overflow: hidden)`);
    }
    // 5) сцена залезает в зону субтитров (только если субтитры есть)
    if (hasCaps && !x.cap && b.b > capTop.top && b.t < capTop.bottom) {
      add("captions_zone", "error", x, `текст сцены ниже ${capTop.top} px — там субтитры`);
    }
    // 6) интерфейс платформы (Instagram и др.) закроет текст; шапка бренда стоит там намеренно
    if (!x.el.closest("[class*=brand], [id*=brand]")) {
      for (const [l, tp, r, bt, what] of zones) {
        const z = { l, t: tp, r, b: bt };
        const area = Math.max(1, (b.r - b.l) * (b.b - b.t));
        if (inter(b, z) / area > 0.3) { add("platform_zone", "warning", x, `под интерфейсом платформы: ${what}`); break; }
      }
    }
  }

  // 3) наложения текста на текст
  for (let i = 0; i < texts.length; i++) {
    for (let j = i + 1; j < texts.length; j++) {
      const a = texts[i], b = texts[j];
      if (a.el.contains(b.el) || b.el.contains(a.el)) continue;
      if (a.cap && b.cap) continue;  // фразы субтитров сменяют друг друга в одном месте
      const s = inter(a.box, b.box), m = Math.min(area(a.box), area(b.box));
      if (m > 0 && s > 0.15 * m) {
        issues.push({ t, kind: "overlap", severity: a.moving || b.moving ? "warning" : "error", text: `${a.text} / ${b.text}`, rect: rnd(a.box),
                      detail: `надписи наложены (${Math.round(100 * s / m)} % меньшей)`, other: rnd(b.box) });
      }
    }
  }

  // 4) текст поверх графики (svg, img, canvas, video); фон во весь кадр не считается
  const gfx = [];
  for (const g of document.querySelectorAll("svg, img, canvas, video")) {
    if (g.parentElement && g.parentElement.closest("svg")) continue;
    if (opacity(g) <= 0.05) continue;
    const r = g.getBoundingClientRect();
    const gb = { l: Math.max(0, r.left), t: Math.max(0, r.top), r: Math.min(W, r.right), b: Math.min(H, r.bottom) };
    if (area(gb) < 1 || area(gb) > 0.8 * W * H) continue;
    gfx.push({ g, gb });
  }
  for (const x of texts) {
    if (x.cap) continue;
    for (const { g, gb } of gfx) {
      if (g.contains(x.el)) continue;
      const s = inter(x.box, gb);
      if (s > 0.2 * area(x.box)) {
        const who = (g.getAttribute("class") || g.tagName.toLowerCase());
        add("over_graphic", "warning", x, `текст лежит поверх графики <${g.tagName.toLowerCase()} ${who}> (${Math.round(100 * s / area(x.box))} %)`);
        break;
      }
    }
  }
  return issues;
}

async function main() {
  const drv = loadDriver();
  if (!drv) { process.stdout.write(JSON.stringify({ error: "NO_DRIVER" })); process.exit(3); }
  const W = args.width || 1080, H = args.height || 1920;
  const exe = args.browser || undefined;
  const flags = ["--no-sandbox", "--disable-gpu", "--hide-scrollbars", "--allow-file-access-from-files"];
  let browser, page;
  if (drv.kind === "pw") {
    browser = await drv.mod.chromium.launch({ executablePath: exe, headless: true, args: flags });
    page = await browser.newPage({ viewport: { width: W, height: H }, deviceScaleFactor: 1 });
  } else {
    browser = await drv.mod.launch({ executablePath: exe, headless: true, args: flags });
    page = await browser.newPage();
    await page.setViewport({ width: W, height: H, deviceScaleFactor: 1 });
  }
  const pageErrors = [];
  page.on("pageerror", (e) => pageErrors.push(String(e && e.message || e)));
  try {
    await page.goto(pathToFileURL(args.html).href, { waitUntil: "load", timeout: 60000 });
    await page.evaluate(() => document.fonts && document.fonts.ready.then(() => true));
    const hasTl = await page.evaluate(() => !!(window.__timelines && Object.keys(window.__timelines).length));
    let issues = [];
    if (!hasTl) {
      issues.push({ t: 0, kind: "no_timeline", severity: "error", text: "", rect: [0, 0, 0, 0],
                    detail: "в странице нет window.__timelines — скрипт сцены упал или GSAP не загрузился" + (pageErrors.length ? `: ${pageErrors[0]}` : "") });
    }
    for (const t of args.times) {
      issues = issues.concat(await page.evaluate(measure, { t, W, H, capTop: args.capZone || { top: 1450, bottom: 1700 }, zones: args.zones || [] }));
    }
    process.stdout.write(JSON.stringify({ issues, pageErrors, driver: drv.kind }));
  } finally {
    await browser.close();
  }
}

main().catch((e) => { process.stderr.write(String(e && e.stack || e) + "\n"); process.exit(1); });
