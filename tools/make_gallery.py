"""Визуальный каталог шаблонов сцен: картинки docs/media/gallery/* из настоящих шаблонов reels/scenes.

    python tools/make_gallery.py                                    # GSAP из REELS_GSAP или reels/vendor/gsap.min.js
    python tools/make_gallery.py --gsap /path/gsap.min.js --fonts-css /path/fonts.css
    python tools/make_gallery.py --out /tmp/gallery --no-gif --theme night

Для каждого шаблона из REGISTRY (кроме custom) берётся его SceneDoc.example, рендерится той же функцией шаблона,
что и в сборке ролика, и кладётся в страницу 1080×1920 с базовым CSS compose (тема → theme_css → _base_css)
и таймлайном GSAP. Якоря примера («каждый пятый») находятся в выдуманной фразе диктора, собранной из самих
якорей; если якорь всё же не нашёлся — время ставится числом. Кадр снимается, когда анимация сцены закончилась
(конец таймлайна), без субтитров и полоски прогресса.

Что получается (--out, по умолчанию docs/media/gallery/):
    <шаблон>.png   кадр шаблона 270×480
    scenes.png     сетка всех шаблонов с подписями
    themes.png     одна сцена (--themes-scene, по умолчанию stat) во всех темах THEMES
    scenes.gif     анимация шаблонов по очереди (без --no-gif)

Нужны Node.js с пакетом playwright (chromium), Pillow, ffmpeg (только для GIF).
--fonts-css — локальный CSS с @font-face (Manrope, IBM Plex Mono): шрифты встраиваются в страницу data:-ссылками;
если файла из url(...) нет рядом с CSS, он ищется по имени во вложенных папках. Без --fonts-css страница берёт
шрифты темы из Google Fonts (нужна сеть).
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from reels import compose, scenes, themes  # noqa: E402
from reels.scenes import SceneCtx, SceneError, esc  # noqa: E402

TILE_W, TILE_H = 270, 480
SKIP = {"custom"}
WORD, GAP = .26, .06                # выдуманный диктор: слово 0.26 с + пауза 0.06 с
FILL = ["смотрите", "вот", "что", "важно", "дальше", "и", "ещё", "тут", "итак", "значит"]
GIF_FPS, GIF_HOLD = 8, 1.2          # кадров в секунду; сколько держать готовый кадр шаблона


# --- выдуманные слова под якоря примера --------------------------------------------------------------
def _anchor_body(v) -> str | None:
    """Фраза якоря без сдвига и @end; None для чисел и процентов."""
    if not isinstance(v, str):
        return None
    s = re.sub(r"[+-]\d+(?:\.\d+)?$", "", v.strip()).strip()
    s = re.sub(r"@end$", "", s).strip()
    if not s or re.fullmatch(r"\d+(\.\d+)?%?", s):
        return None
    return s


def _is_anchor_key(k: str) -> bool:
    return k == "at" or k.endswith("_at")


def anchors_of(example) -> list[str]:
    """Фразы-якоря примера в порядке появления (at, *_at, в том числе внутри items/points)."""
    out: list[str] = []

    def walk(v, key=""):
        if isinstance(v, dict):
            for k, x in v.items():
                walk(x, k)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x, key)
        elif _is_anchor_key(key):
            b = _anchor_body(v)
            if b and b not in out:
                out.append(b)
    walk(example)
    return out


def fake_words(anchors: list[str]) -> list[dict]:
    """Фраза диктора: пара слов-связок, якорь, снова связки… Время — от начала блока."""
    seq, fi = [], 0
    for a in anchors:
        for _ in range(2):
            seq.append(FILL[fi % len(FILL)])
            fi += 1
        seq += a.split()
    seq += ["вот", "так."]
    words, t = [], .2
    for w in seq:
        words.append({"w": w, "s": round(t, 3), "e": round(t + WORD, 3)})
        t += WORD + GAP
    return words


def numeric_anchors(example):
    """Копия примера, где все якоря заменены числами (если фраза так и не нашлась)."""
    n = [0]

    def walk(v, key=""):
        if isinstance(v, dict):
            return {k: walk(x, k) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [walk(x, key) for x in v]
        if _is_anchor_key(key) and _anchor_body(v):
            n[0] += 1
            return .3 + .6 * (n[0] - 1)
        return v
    return walk(example)


def render(name: str, theme) -> tuple[object, list[str]]:
    """SceneOut шаблона по его примеру и список замечаний."""
    _, doc = scenes.REGISTRY[name]
    ex = dict(doc.example)
    words = fake_words(anchors_of(ex))
    notes = []
    for attempt in (ex, None):
        params = attempt if attempt is not None else numeric_anchors(ex)
        ctx = SceneCtx(1, f"g-{name}", 0.0, words[-1]["e"] + .4, words, " ".join(w["w"] for w in words),
                       theme, ROOT)
        try:
            return scenes.render_scene(ctx, {"type": name, **params}), notes
        except SceneError as e:
            if attempt is None:
                raise
            notes.append(f"якорь не нашёлся ({e}) — времена числами")
    raise AssertionError


# --- страница ----------------------------------------------------------------------------------------
def fonts_head(css_path: Path | None, theme) -> str:
    if css_path is None:
        url = getattr(theme, "fonts_url", "")
        return f'<link href="{esc(url)}" rel="stylesheet" />' if url else ""
    css = css_path.read_text(encoding="utf-8")
    base = css_path.parent

    def sub(m):
        u = m.group(1).strip("'\"")
        if re.match(r"^(https?:|data:)", u):
            return m.group(0)
        f = base / u
        if not f.is_file():
            found = next(iter(sorted(base.rglob(Path(u).name))), None)
            if found is None:
                print(f"  шрифт {u} не найден рядом с {css_path}", file=sys.stderr)
                return m.group(0)
            f = found
        mime = {"woff2": "font/woff2", "woff": "font/woff", "ttf": "font/ttf", "otf": "font/otf"}.get(
            f.suffix.lstrip(".").lower(), "application/octet-stream")
        return f"url(data:{mime};base64,{base64.b64encode(f.read_bytes()).decode()})"
    return "<style>" + re.sub(r"url\(([^)]+)\)", sub, css) + "</style>"


def gsap_head(gsap: Path | None) -> str:
    if gsap is None:
        return f'<script src="{compose.GSAP_URL}"></script>'
    return "<script>" + gsap.read_text(encoding="utf-8").replace("</script", "<\\/script") + "</script>"


def scene_page(uid: str, out, theme, head: str) -> str:
    base = compose._base_css(theme, themes.theme_css(theme))
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="UTF-8" />
<meta name="viewport" content="width=1080, height=1920" />
{head}
<style>{base}
{out.css}
</style></head>
<body><div id="root">
  <div id="{uid}" class="scene">
{out.html}
  </div>
</div>
<script>
  const tl = gsap.timeline({{ paused:true }});
  {{ const S='#{uid}', t=0;
    try {{
{out.js}
    }} catch (e) {{ console.error("сцена {uid}:", e); }} }}
  window.__tl = tl;
  tl.seek(0);
</script>
</body></html>
"""


def grid_page(tiles: list[tuple[Path, str]], cols: int, head: str, title: str = "") -> tuple[str, int, int]:
    """Сетка картинок с подписями (рисуется браузером теми же шрифтами). → (html, ширина, высота)."""
    pad, gap, lab = 36, 26, 46
    rows = (len(tiles) + cols - 1) // cols
    th = 64 if title else 0
    w = pad * 2 + cols * TILE_W + (cols - 1) * gap
    h = pad * 2 + th + rows * (TILE_H + lab) + (rows - 1) * gap
    bp = themes.get_theme("blueprint")
    cells = "".join(
        f'<figure><img src="data:image/png;base64,{base64.b64encode(p.read_bytes()).decode()}" />'
        f'<figcaption>{esc(label)}</figcaption></figure>' for p, label in tiles)
    html = f"""<!doctype html><html><head><meta charset="UTF-8" />{head}
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  html, body {{ width:{w}px; height:{h}px; background:{bp.deep}; overflow:hidden; }}
  body {{ padding:{pad}px; font-family:{bp.mono}; color:{bp.ink}; }}
  h1 {{ height:{th}px; font-size:26px; font-weight:600; letter-spacing:.06em; text-transform:uppercase;
        color:{bp.accent}; }}
  .g {{ display:grid; grid-template-columns:repeat({cols}, {TILE_W}px); gap:{gap}px; }}
  figure {{ width:{TILE_W}px; }}
  img {{ display:block; width:{TILE_W}px; height:{TILE_H}px; border-radius:10px;
         box-shadow:0 0 0 1px rgba(232,240,255,.16); }}
  figcaption {{ height:{lab}px; padding-top:12px; text-align:center; font-size:22px; font-weight:600;
                letter-spacing:.04em; }}
</style></head><body>{f"<h1>{esc(title)}</h1>" if title else ""}<div class="g">{cells}</div></body></html>"""
    return html, w, h


# --- браузер -----------------------------------------------------------------------------------------
SHOT_JS = r"""
const { chromium } = require('playwright');
const fs = require('fs');
(async () => {
  const jobs = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const opts = {};
  if (process.env.HYPERFRAMES_BROWSER_PATH) opts.executablePath = process.env.HYPERFRAMES_BROWSER_PATH;
  let b;
  try { b = await chromium.launch(); } catch (e) { b = await chromium.launch(opts); }
  const report = {};
  const launch = async () => { try { return await chromium.launch(); } catch (e) { return await chromium.launch(opts); } };
  for (const j of jobs) {
    // в контейнере Chromium иногда отказывает в снимке («Unable to capture screenshot») — перезапуск и повтор
    for (let attempt = 1; ; attempt++) {
      try { report[j.id] = await runJob(b, j); break; }
      catch (e) {
        if (attempt >= 3) throw e;
        console.error(`${j.id}: ${String(e).split('\n')[0]} — повтор`);
        try { await b.close(); } catch (_) {}
        b = await launch();
      }
    }
  }
  await b.close();
  process.stdout.write(JSON.stringify(report));
})().catch(e => { console.error(e); process.exit(1); });

async function runJob(b, j) {
  {
    const p = await b.newPage({ viewport: { width: j.w, height: j.h }, deviceScaleFactor: 1 });
    const errs = [];
    p.on('pageerror', e => errs.push(String(e)));
    p.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
    await p.goto('file://' + j.html);
    await p.evaluate(async () => {
      for (const f of ['500 40px Manrope', '700 40px Manrope', '800 40px Manrope',
                       '500 40px "IBM Plex Mono"', '600 40px "IBM Plex Mono"']) {
        try { await document.fonts.load(f, 'Аа'); } catch (e) {}
      }
      await document.fonts.ready;
    });
    const seek = x => p.evaluate(x => {
      const tl = window.__tl; if (!tl) return null;
      const d = tl.duration();
      tl.seek(x === 'end' ? d + .05 : Math.min(x, d + .05), false);   // false: onUpdate (счётчик) срабатывает
      return d;
    }, x);
    const r = { errs, frames: [] };
    if (j.shot) {
      r.dur = await seek('end');
      await p.waitForTimeout(30);
      await p.screenshot({ path: j.shot });
      r.overflow = await p.evaluate(() => {
        // элементы сцены, вылезающие за кадр или за поля 80 px, и текст, обрезанный своим блоком
        const out = [];
        for (const el of document.querySelectorAll('.scene *')) {
          const cs = getComputedStyle(el);
          if (cs.opacity === '0' || cs.visibility === 'hidden') continue;
          const r = el.getBoundingClientRect();
          if (!r.width || !r.height || el.closest('svg') && el.tagName !== 'svg') continue;
          const name = el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().replace(/\s+/g, '.') : '');
          if (r.right > 1080.5 || r.left < -.5 || r.bottom > 1920.5 || r.top < -.5)
            out.push(`${name}: за кадром [${Math.round(r.left)},${Math.round(r.top)} → ${Math.round(r.right)},${Math.round(r.bottom)}]`);
          else if (r.bottom > 1450.5)
            out.push(`${name}: ниже 1450 (зона субтитров), низ ${Math.round(r.bottom)}`);
          // по вертикали .big обрезан намеренно (строка выезжает снизу), поэтому смотрим только ширину
          if ((cs.overflow === 'hidden' || cs.overflowX === 'hidden') && el.scrollWidth > el.clientWidth + 2 && el.textContent.trim())
            out.push(`${name}: текст не влез по ширине (${el.scrollWidth} в ${el.clientWidth} px)`);
        }
        return out;
      });
    }
    if (j.seq) {
      r.frames = [];
      const d = await seek(0);
      const n = Math.ceil((d + .1) * j.seq.fps);
      for (let i = 0; i <= n; i++) {
        await seek(Math.min(i / j.seq.fps, d + .05));
        const f = `${j.seq.dir}/${j.id}-${String(i).padStart(4, '0')}.jpg`;
        await p.screenshot({ path: f, type: 'jpeg', quality: 92 });
        r.frames.push(f);
      }
    }
    if (j.plain) await p.screenshot({ path: j.plain });
    await p.close();
    return r;
  }
}
"""


def run_browser(jobs: list[dict], tmp: Path) -> dict:
    js, jf = tmp / "shot.cjs", tmp / f"jobs-{len(list(tmp.glob('jobs-*')))}.json"
    js.write_text(SHOT_JS, encoding="utf-8")
    jf.write_text(json.dumps(jobs, ensure_ascii=False), encoding="utf-8")
    env = dict(os.environ)
    try:
        gnm = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True).stdout.strip()
    except OSError:
        gnm = ""
    if gnm:
        env["NODE_PATH"] = os.pathsep.join(filter(None, [env.get("NODE_PATH", ""), gnm]))
    r = subprocess.run(["node", str(js), str(jf)], capture_output=True, text=True, env=env)
    if r.returncode:
        sys.exit(f"playwright: {r.stderr.strip()[-2000:]}")
    return json.loads(r.stdout)


# --- картинки ----------------------------------------------------------------------------------------
def save_png(img: Image.Image, path: Path, colors: int = 256) -> None:
    """PNG с палитрой: в README картинке не нужен truecolor, а размер меньше в 3–4 раза."""
    img.convert("RGB").quantize(colors=colors, method=Image.Quantize.MEDIANCUT,
                                dither=Image.Dither.FLOYDSTEINBERG).save(path, optimize=True)


def downscale(src: Path, size=(TILE_W, TILE_H)) -> Image.Image:
    return Image.open(src).convert("RGB").resize(size, Image.LANCZOS)


def make_gif(frames: list[list[str]], out: Path, tmp: Path, fps: int, hold: float, size=(TILE_W, TILE_H)) -> None:
    d = tmp / "gif"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir()
    k = 0
    for seq in frames:
        for i, f in enumerate(seq):
            reps = 1 + (round(hold * fps) if i == len(seq) - 1 else 0)
            img = downscale(Path(f), size)
            for _ in range(reps):
                img.save(d / f"{k:05d}.png")
                k += 1
    vf = ("split[a][b];[a]palettegen=max_colors=96:stats_mode=diff[p];"
          "[b][p]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle")
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps), "-i", str(d / "%05d.png"),
                        "-vf", vf, "-loop", "0", str(out)], capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"ffmpeg: {r.stderr.strip()[-1500:]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--gsap", type=Path, default=None,
                    help="локальный gsap.min.js (встраивается в страницу); по умолчанию REELS_GSAP, "
                         "reels/vendor/gsap.min.js или CDN")
    ap.add_argument("--fonts-css", type=Path, default=None,
                    help="CSS с @font-face Manrope / IBM Plex Mono; по умолчанию — Google Fonts темы")
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "media" / "gallery")
    ap.add_argument("--theme", default="blueprint", help="тема кадров шаблонов; по умолчанию blueprint")
    ap.add_argument("--themes-scene", default="stat", help="шаблон для themes.png; по умолчанию stat")
    ap.add_argument("--cols", type=int, default=5, help="колонок в scenes.png; по умолчанию 5")
    ap.add_argument("--only", default="", help="через запятую: какие шаблоны снять (по умолчанию все, кроме custom)")
    ap.add_argument("--no-gif", action="store_true", help="не делать scenes.gif")
    ap.add_argument("--keep", action="store_true", help="не удалять временную папку (страницы и полные кадры)")
    a = ap.parse_args()

    gsap = a.gsap or Path(os.environ.get("REELS_GSAP") or compose.GSAP_LOCAL)
    if not gsap.is_file():
        if a.gsap:
            sys.exit(f"нет файла {a.gsap}")
        print("  локального GSAP нет — страница возьмёт его с CDN (нужна сеть)", file=sys.stderr)
        gsap = None
    if a.fonts_css and not a.fonts_css.is_file():
        sys.exit(f"нет файла {a.fonts_css}")
    names = [n for n in sorted(scenes.REGISTRY) if n not in SKIP]
    if a.only:
        want = [x.strip() for x in a.only.split(",") if x.strip()]
        bad = set(want) - set(names)
        if bad:
            sys.exit(f"нет шаблонов: {', '.join(sorted(bad))}. Есть: {', '.join(names)}")
        names = [n for n in names if n in want]
    theme = themes.get_theme(a.theme)
    a.out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="gallery-"))
    head_cache: dict[str, str] = {}
    gs = gsap_head(gsap)

    def head(th) -> str:
        if th.name not in head_cache:
            head_cache[th.name] = fonts_head(a.fonts_css, th) + "\n" + gs
        return head_cache[th.name]

    # 1. страницы шаблонов (+ themes-scene во всех темах)
    jobs, notes = [], {}
    seq_dir = tmp / "seq"
    seq_dir.mkdir()
    for n in names:
        out, nn = render(n, theme)
        if nn:
            notes[n] = nn
        errs = compose.js_syntax_errors({n: out.js})
        if errs:
            notes.setdefault(n, []).append(f"синтаксическая ошибка JS: {errs[n]}")
        page = tmp / f"{n}.html"
        page.write_text(scene_page(f"g-{n}", out, theme, head(theme)), encoding="utf-8")
        job = {"id": n, "html": str(page), "w": 1080, "h": 1920, "shot": str(tmp / f"{n}.full.png")}
        if not a.no_gif:
            job["seq"] = {"dir": str(seq_dir), "fps": GIF_FPS}
        jobs.append(job)
    ts = a.themes_scene
    if ts not in scenes.REGISTRY or ts in SKIP:
        sys.exit(f"--themes-scene: нет шаблона {ts}")
    for tn in themes.THEMES:
        th = themes.get_theme(tn)
        out, _ = render(ts, th)
        page = tmp / f"theme-{tn}.html"
        page.write_text(scene_page(f"g-{ts}", out, th, head(th)), encoding="utf-8")
        jobs.append({"id": f"theme:{tn}", "html": str(page), "w": 1080, "h": 1920,
                     "shot": str(tmp / f"theme-{tn}.full.png")})
    print(f"  снимаю {len(jobs)} страниц…")
    rep = run_browser(jobs, tmp)

    # 2. плитки 270×480
    tiles, theme_tiles = [], []
    for n in names:
        p = a.out / f"{n}.png"
        save_png(downscale(tmp / f"{n}.full.png"), p)
        tiles.append((p, n))
    for tn in themes.THEMES:
        p = tmp / f"theme-{tn}.png"
        downscale(tmp / f"theme-{tn}.full.png").save(p)
        theme_tiles.append((p, tn))

    # 3. сетки — тот же браузер, те же шрифты
    gh = fonts_head(a.fonts_css, themes.get_theme("blueprint"))
    gjobs = []
    for gid, tl, cols, title in (("scenes", tiles, min(a.cols, len(tiles)), ""),
                                 ("themes", theme_tiles, len(theme_tiles), "")):
        html, w, h = grid_page(tl, cols, gh, title)
        page = tmp / f"grid-{gid}.html"
        page.write_text(html, encoding="utf-8")
        gjobs.append({"id": gid, "html": str(page), "w": w, "h": h, "plain": str(tmp / f"grid-{gid}.png")})
    run_browser(gjobs, tmp)
    for gid in ("scenes", "themes"):
        save_png(Image.open(tmp / f"grid-{gid}.png"), a.out / f"{gid}.png")

    # 4. GIF
    if not a.no_gif:
        make_gif([rep[n]["frames"] for n in names], a.out / "scenes.gif", tmp, GIF_FPS, GIF_HOLD)

    # отчёт
    total = 0
    for f in sorted(a.out.iterdir()):
        if f.suffix in (".png", ".gif"):
            total += f.stat().st_size
            print(f"  {f.relative_to(ROOT) if f.is_relative_to(ROOT) else f}  {f.stat().st_size / 1024:.0f} КБ")
    print(f"  всего {total / 1024 / 1024:.2f} МБ")
    for key, r in rep.items():
        msgs = notes.get(key, []) + [f"ошибка на странице: {e}" for e in r.get("errs", [])] + r.get("overflow", [])
        if r.get("dur") is not None:
            print(f"  {key}: анимация {r['dur']:.2f} с")
        for m in msgs:
            print(f"    ! {m}")
    if a.keep:
        print(f"  временные файлы: {tmp}")
    else:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
