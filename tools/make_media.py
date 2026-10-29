"""Пересобирает картинки и GIF для README из готовых роликов: docs/media/*.

    python tools/make_media.py                                   # всё из examples/povodok/out
    python tools/make_media.py --project examples/povodok --log run.log
    python tools/make_media.py --video linear=out/a.mp4 --video from-end=out/b.mp4 --only hero,edits
    python tools/make_media.py --only pipeline,qa                # без роликов

Что получается (docs/media/):
    hero.gif      монтаж linear целиком (или отрывок --hero-start/--hero-length), 320×568, 12 к/с
    edits.gif     первые секунды трёх монтажей рядом — «одна озвучка, много монтажей»
    frames.png    контактный лист из 8 кадров — «кадры до рендера»
    qa.png        настоящие дефекты вёрстки (local-model-test/frames/B.png, C.png) с рамками
    pipeline.svg  схема этапов конвейера с контрольными точками
    terminal.gif  сеанс `python -m reels run examples/povodok --yes`; текст — из --log (настоящий вывод
                  команды) или встроенный образец

Нужны ffmpeg в PATH, Node.js с пакетом playwright (для qa.png и terminal.gif).
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BG, DEEP, INK, ACCENT, ALT = "#123a7a", "#0b2350", "#e8f0ff", "#ffd23f", "#ff6b5b"
EDIT_ORDER = ["linear", "from-end", "suspense"]
ALL = ["pipeline", "qa", "hero", "edits", "frames", "terminal"]


def sh(cmd: list, **kw) -> None:
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)
    if r.returncode:
        sys.exit(f"не отработала команда: {' '.join(map(str, cmd))[:300]}\n{r.stderr[-2000:]}")


def font_file(mono: bool = False) -> str | None:
    names = ["JetBrains Mono:bold", "DejaVu Sans Mono:bold"] if mono else ["DejaVu Sans:bold", "Liberation Sans:bold"]
    for n in names:
        try:
            p = subprocess.run(["fc-match", "-f", "%{file}", n], capture_output=True, text=True).stdout.strip()
            if p and Path(p).exists():
                return p
        except FileNotFoundError:
            break
    return None


def duration(mp4: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(mp4)],
                       capture_output=True, text=True)
    return float(r.stdout.strip() or 0)


def gif(src_filter: str, inputs: list, out: Path, colors: int = 128) -> None:
    """Видео → GIF с общей палитрой (palettegen/paletteuse), без «грязного» дизеринга по всему кадру."""
    graph = (f"{src_filter},split[a][b];[a]palettegen=max_colors={colors}:stats_mode=diff[p];"
             f"[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle")
    sh(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", graph, "-loop", "0", out])


def node(script: str, *args) -> None:
    js = Path(tempfile.mkdtemp()) / "shot.cjs"
    js.write_text(script, encoding="utf-8")
    env = dict(os.environ)
    gnm = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True).stdout.strip()
    if gnm:
        env["NODE_PATH"] = os.pathsep.join(filter(None, [env.get("NODE_PATH", ""), gnm]))
    sh(["node", js, *args], env=env)


SHOT_JS = r"""
const { chromium } = require('playwright');
(async () => {
  const [page_html, out, w, h] = process.argv.slice(2);
  const opts = {};
  if (process.env.HYPERFRAMES_BROWSER_PATH) opts.executablePath = process.env.HYPERFRAMES_BROWSER_PATH;
  let b;
  try { b = await chromium.launch(); } catch (e) { b = await chromium.launch(opts); }
  const p = await b.newPage({ viewport: { width: +w, height: +h }, deviceScaleFactor: 1 });
  await p.goto('file://' + page_html);
  await p.waitForTimeout(300);
  if (out.endsWith('/')) {               // терминал: кадры по состояниям window.frames
    const n = await p.evaluate(() => window.FRAMES.length);
    for (let i = 0; i < n; i++) {
      await p.evaluate(i => window.show(i), i);
      await p.screenshot({ path: out + String(i).padStart(4, '0') + '.png' });
    }
  } else {
    await p.screenshot({ path: out, fullPage: true });
  }
  await b.close();
})().catch(e => { console.error(e); process.exit(1); });
"""


# ---------------------------------------------------------------- pipeline.svg
def make_pipeline(out: Path) -> None:
    W, H = 1200, 440
    nw, nh, gap, x0 = 150, 72, 46, 37
    row1 = [("reel.yaml", "сценарий · сцены", "file"), ("check", "лимит слов, сцены", None),
            ("accent", "RUAccent", None), ("voice", "F5-TTS · Whisper", None),
            ("align", "таймкоды слов", None), ("music", "MusicGen", None)]
    row2 = [("build", "HTML/SVG + GSAP", None), ("snapshot", "кадры · QA вёрстки", None),
            ("render", "HyperFrames", None), ("finish", "постер · −18 LUFS", None),
            ("out/*.mp4", "+ report.md", "file")]
    gates = {"check": "script", "accent": "accents", "voice": "take", "snapshot": "frames"}
    y1, y2 = 118, 262
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
         f'font-family="-apple-system, \'Segoe UI\', Helvetica, Arial, sans-serif">',
         '<defs><pattern id="g" width="24" height="24" patternUnits="userSpaceOnUse">'
         '<path d="M24 0H0V24" fill="none" stroke="#e8f0ff" stroke-opacity=".07"/></pattern>'
         f'<marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
         f'<path d="M0 0L10 5L0 10z" fill="{INK}"/></marker></defs>',
         f'<rect width="{W}" height="{H}" rx="18" fill="{BG}"/><rect width="{W}" height="{H}" rx="18" fill="url(#g)"/>',
         f'<text x="{x0}" y="50" fill="{INK}" font-size="22" font-weight="700">reel.yaml → готовые ролики</text>',
         f'<text x="{W - x0}" y="50" fill="{ACCENT}" font-size="17" text-anchor="end" '
         f'font-family="ui-monospace, Consolas, monospace">python -m reels run &lt;проект&gt;</text>']

    def box(x, y, name, sub, kind):
        stroke = ACCENT if kind else INK
        fill = DEEP if kind else "#1b4a94"
        dash = ' stroke-dasharray="6 5"' if kind else ""
        s.append(f'<rect x="{x}" y="{y}" width="{nw}" height="{nh}" rx="10" fill="{fill}" stroke="{stroke}" '
                 f'stroke-width="2"{dash}/>')
        s.append(f'<text x="{x + nw / 2}" y="{y + 31}" fill="{ACCENT if kind else INK}" font-size="19" '
                 f'font-weight="700" text-anchor="middle" font-family="ui-monospace, Consolas, monospace">'
                 f'{html.escape(name)}</text>')
        s.append(f'<text x="{x + nw / 2}" y="{y + 54}" fill="{INK}" fill-opacity=".75" font-size="13.5" '
                 f'text-anchor="middle">{html.escape(sub)}</text>')

    def gate(cx, y, name):
        s.append(f'<g transform="translate({cx - 58},{y})"><rect width="116" height="26" rx="13" fill="{ACCENT}"/>'
                 f'<path d="M14 13l6-6 6 6-6 6z" fill="{BG}"/>'
                 f'<text x="66" y="18" fill="{BG}" font-size="14" font-weight="700" text-anchor="middle" '
                 f'font-family="ui-monospace, Consolas, monospace">{name}</text></g>')

    xs = [x0 + i * (nw + gap) for i in range(6)]
    for i, (n, sub, kind) in enumerate(row1):
        box(xs[i], y1, n, sub, kind)
        if i:
            s.append(f'<line x1="{xs[i - 1] + nw + 4}" y1="{y1 + nh / 2}" x2="{xs[i] - 4}" y2="{y1 + nh / 2}" '
                     f'stroke="{INK}" stroke-width="2" marker-end="url(#a)"/>')
        if n in gates:
            gate(xs[i] + nw / 2, y1 - 38, gates[n])
    # перевод строки: из music вниз и налево к build
    ym = (y1 + nh + y2) / 2
    s.append(f'<path d="M{xs[5] + nw / 2} {y1 + nh + 4} V{ym} H{xs[0] + nw / 2} V{y2 - 4}" fill="none" '
             f'stroke="{INK}" stroke-width="2" stroke-linejoin="round" marker-end="url(#a)"/>')
    for i, (n, sub, kind) in enumerate(row2):
        box(xs[i], y2, n, sub, kind)
        if i:
            s.append(f'<line x1="{xs[i - 1] + nw + 4}" y1="{y2 + nh / 2}" x2="{xs[i] - 4}" y2="{y2 + nh / 2}" '
                     f'stroke="{INK}" stroke-width="2" marker-end="url(#a)"/>')
        if n in gates:
            gate(xs[i] + nw / 2, y2 + nh + 12, gates[n])
    # монтажи: render повторяется для каждого монтажа
    rx = xs[2]
    s.append(f'<text x="{rx + nw / 2}" y="{y2 + nh + 30}" fill="{INK}" fill-opacity=".75" font-size="13.5" '
             f'text-anchor="middle">× каждый монтаж</text>')
    # легенда
    ly = H - 34
    gate(x0 + 58, ly - 18, "gate")
    s.append(f'<text x="{x0 + 130}" y="{ly}" fill="{INK}" font-size="15">контрольная точка: '
             f'<tspan font-family="ui-monospace, Consolas, monospace" fill="{ACCENT}">reels approve &lt;проект&gt; '
             f'&lt;gate&gt;</tspan> или <tspan font-family="ui-monospace, Consolas, monospace" '
             f'fill="{ACCENT}">--yes</tspan></text>')
    s.append(f'<text x="{W - x0}" y="{ly}" fill="{INK}" fill-opacity=".75" font-size="15" text-anchor="end">'
             f'кэш: этап пропускается, если его входы не менялись</text>')
    s.append("</svg>")
    out.write_text("\n".join(s) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- qa.png
QA_DEFECTS = [
    # кадр, рамка (x, y, w, h) в координатах кадра 540×960, подпись
    ("B.png", (30, 598, 504, 96), "заголовок обрезан краем кадра,<br>строки налезают друг на друга",
     "выход за край кадра · наложение блоков"),
    ("C.png", (32, 340, 422, 66), "крупный текст лежит поверх карты",
     "текст перекрывает графику сцены"),
]


def make_qa(out: Path, frames_dir: Path) -> None:
    cards = []
    for name, (x, y, w, h), label, rule in QA_DEFECTS:
        img = (frames_dir / name).resolve().as_uri()
        ly = y + h + 14 if y < 700 else y - 70
        cards.append(f"""
      <figure>
        <div class="frame"><img src="{img}">
          <div class="box" style="left:{x}px;top:{y}px;width:{w}px;height:{h}px"></div>
          <div class="tag" style="left:{max(x, 24)}px;top:{ly}px">{label}</div>
        </div>
        <figcaption><b>✗</b> {html.escape(rule)}</figcaption>
      </figure>""")
    page = f"""<!doctype html><meta charset="utf-8"><style>
  body{{margin:0;background:{DEEP};font-family:"DejaVu Sans","Segoe UI",sans-serif;color:{INK}}}
  .wrap{{padding:34px 36px 30px;width:max-content}}
  h1{{font-size:30px;margin:0 0 6px;font-weight:800}} h1 em{{color:{ACCENT};font-style:normal}}
  p{{margin:0 0 24px;font-size:18px;opacity:.75}}
  .row{{display:flex;gap:36px}}
  figure{{margin:0}}
  .frame{{position:relative;width:540px;height:960px;border-radius:14px;overflow:hidden;
          box-shadow:0 0 0 2px rgba(232,240,255,.25)}}
  .frame img{{display:block;width:540px;height:960px}}
  .box{{position:absolute;border:5px solid {ALT};border-radius:10px;box-shadow:0 0 0 4000px rgba(5,15,35,.38)}}
  .tag{{position:absolute;background:{ALT};color:#fff;font-weight:700;font-size:21px;line-height:1.25;
        padding:9px 14px;border-radius:9px;max-width:470px}}
  figcaption{{margin-top:14px;font:600 16px "JetBrains Mono","DejaVu Sans Mono",monospace;color:{INK};opacity:.9}}
  figcaption b{{color:{ALT}}}
</style><div class="wrap">
  <h1>Это ловит <em>автопроверка вёрстки</em></h1>
  <p>Настоящие кадры с дефектами: проверка смотрит в DOM кадра до рендера — выход за край, наложения, зона субтитров</p>
  <div class="row">{''.join(cards)}</div>
</div>"""
    tmp = Path(tempfile.mkdtemp())
    (tmp / "qa.html").write_text(page, encoding="utf-8")
    raw = tmp / "qa-full.png"
    node(SHOT_JS, tmp / "qa.html", raw, 1260, 1200)
    # уменьшить до ширины 900 (README всё равно ужимает), 64 цвета хватает
    sh(["ffmpeg", "-y", "-v", "error", "-i", raw, "-vf", "scale=900:-1:flags=lanczos", tmp / "qa-s.png"])
    shutil.copy(tmp / "qa-s.png", out)


# ---------------------------------------------------------------- видео → hero / edits / frames
def make_hero(out: Path, video: Path, start: float, length: float) -> None:
    length = length if length > 0 else duration(video) - start
    gif(f"[0:v]trim=start={start}:duration={length},setpts=PTS-STARTPTS,fps=12,"
        "scale=320:-2:flags=lanczos", ["-i", video], out, colors=128)


def make_edits(out: Path, videos: dict[str, Path], length: float) -> None:
    names = [n for n in EDIT_ORDER if n in videos] + [n for n in videos if n not in EDIT_ORDER]
    names = names[:3]
    fnt = font_file(mono=True)
    inputs, parts = [], []
    for i, n in enumerate(names):
        inputs += ["-t", f"{length}", "-i", videos[n]]
        label = ""
        if fnt:
            label = (f",pad=iw:ih+44:0:44:color={DEEP},drawtext=fontfile='{fnt}':text='{n}':"
                     f"fontcolor={ACCENT}:fontsize=24:x=(w-tw)/2:y=9:y_align=font")
        parts.append(f"[{i}:v]fps=10,scale=240:-2:flags=lanczos{label}[v{i}]")
    sep = "".join(f"[v{i}]" for i in range(len(names)))
    graph = ";".join(parts) + f";{sep}hstack=inputs={len(names)}"
    gif(graph, inputs, out, colors=96)


def make_frames(out: Path, video: Path, n: int = 8) -> None:
    d = duration(video)
    tmp = Path(tempfile.mkdtemp())
    fnt = font_file(mono=True)
    for i in range(n):
        t = round(0.8 + i * (d - 1.6) / (n - 1), 1)
        label = (f",drawbox=x=0:y=0:w=iw:h=40:color={DEEP}@0.85:t=fill,drawtext=fontfile='{fnt}':"
                 f"text='{t:.1f} s':fontcolor={ACCENT}:fontsize=22:x=12:y=9") if fnt else ""
        sh(["ffmpeg", "-y", "-v", "error", "-ss", f"{t}", "-i", video, "-frames:v", "1",
            "-vf", f"scale=270:480:flags=lanczos{label}", tmp / f"f{i:02d}.png"])
    sh(["ffmpeg", "-y", "-v", "error", "-i", tmp / "f%02d.png", "-vf",
        f"tile={n // 2}x2:padding=10:margin=10:color={DEEP}", "-frames:v", "1", out])


# ---------------------------------------------------------------- terminal.gif
SAMPLE_LOG = """\
reels 0.2.0 · проект povodok · 4 блока · 59/60 слов · тема blueprint
монтажи: linear 1234 · from-end 4123 · suspense 1324

✓ check      сценарий 59/60 слов, 4 сцены, источник указан        0.2 с
◆ script     подтверждено (--yes)
– accent     пропуск: voice.engine = file
✓ voice      example/voice/voice.wav · 22.4 с                     0.1 с
✓ align      таймкоды из words.json · границы 4.2 · 11.4 · 17.6   0.3 с
✓ music      example/music/music.wav · −18.0 LUFS                 1.8 с
✓ build      3 монтажа · 4 сцены · 14 фраз субтитров              0.4 с
✓ snapshot   24 кадра · автопроверка вёрстки: замечаний нет        18 с
◆ frames     подтверждено (--yes) · out/contact-linear.png
✓ render     linear · from-end · suspense                          5:06
✓ finish     постер · громкость · сверка текста Whisper            12 с

монтаж     длит.    размер   громкость    текст
linear     24.3 с   7.9 МБ   −18.0 LUFS   100 %
from-end   24.3 с   7.8 МБ   −18.0 LUFS   100 %
suspense   24.3 с   7.9 МБ   −18.0 LUFS   100 %

готово: examples/povodok/out/povodok-linear.mp4 (+2) · отчёт out/report.md
"""


def make_terminal(out: Path, log_text: str, cmd: str) -> None:
    lines = [ln.rstrip() for ln in re.sub(r"\x1b\[[0-9;]*m", "", log_text).splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    # состояния: (сколько символов команды набрано, сколько строк вывода показано, длительность кадра)
    frames = [(0, 0, 0.6)]
    for k in range(1, len(cmd) + 1, 2):
        frames.append((k, 0, 0.06))
    frames.append((len(cmd), 0, 0.5))
    for i in range(1, len(lines) + 1):
        ln = lines[i - 1]
        pause = 0.12 if not ln.strip() else (0.75 if ln.lstrip().startswith(("✓ render", "✓ snapshot")) else 0.32)
        frames.append((len(cmd), i, pause))
    frames.append((len(cmd), len(lines), 3.5))

    def color(ln: str) -> str:
        e = html.escape(ln)
        e = re.sub(r"^(✓)", r'<span class="ok">\1</span>', e)
        e = re.sub(r"^(◆)(\s+\S+)", r'<span class="gate">\1\2</span>', e)
        e = re.sub(r"^(–.*)$", r'<span class="dim">\1</span>', e)
        e = re.sub(r'^(<span class="ok">✓</span>\s+)(\S+)', r'\1<span class="st">\2</span>', e)
        e = re.sub(r"(out/\S+\.(?:mp4|md|png))", r'<span class="path">\1</span>', e)
        e = re.sub(r"^(монтаж\s.*)$", r'<span class="head">\1</span>', e)
        e = re.sub(r"^(готово:)", r'<span class="ok">\1</span>', e)
        return e or "&nbsp;"

    body = [color(ln) for ln in lines]
    rows = max(len(lines) + 3, 14)
    height = 64 + rows * 25 + 30
    page = f"""<!doctype html><meta charset="utf-8"><style>
  html,body{{margin:0;background:#0d1117}}
  .win{{margin:0;width:960px;height:{height}px;background:#0f1d36;font:500 17px/25px "JetBrains Mono","DejaVu Sans Mono",monospace;
        color:#d7e3ff;box-sizing:border-box;overflow:hidden}}
  .bar{{height:38px;background:#0b2350;display:flex;align-items:center;padding:0 16px;gap:8px}}
  .bar i{{width:12px;height:12px;border-radius:50%;display:inline-block}}
  .bar b{{margin-left:14px;font:600 14px "DejaVu Sans",sans-serif;color:#9fb3d9}}
  pre{{margin:0;padding:16px 22px;font:inherit;white-space:pre}}
  .p{{color:{ACCENT}}} .ok{{color:#5bd68a}} .st{{color:#ffffff;font-weight:700}} .gate{{color:{ACCENT};font-weight:700}}
  .dim{{color:#6f84ad}} .path{{color:{ACCENT}}} .head{{color:#9fb3d9;font-weight:700}}
  .cur{{background:#d7e3ff;color:#0f1d36}}
</style><div class="win"><div class="bar"><i style="background:#ff5f57"></i><i style="background:#febc2e"></i>
<i style="background:#28c840"></i><b>local-ai-reels — bash</b></div><pre id="t"></pre></div>
<script>
const CMD = {json.dumps(cmd)}, LINES = {json.dumps(body)};
window.FRAMES = {json.dumps([[c, n] for c, n, _ in frames])};
window.show = i => {{
  const [c, n] = window.FRAMES[i];
  const done = n > 0 || c >= CMD.length && i === window.FRAMES.length - 1;
  let s = '<span class="p">$</span> ' + CMD.slice(0, c).replace(/&/g,'&amp;').replace(/</g,'&lt;');
  if (n === 0) s += '<span class="cur"> </span>';
  s += '\\n' + LINES.slice(0, n).join('\\n');
  if (n === LINES.length) s += '\\n<span class="p">$</span> <span class="cur"> </span>';
  document.getElementById('t').innerHTML = s;
}};
</script>"""
    tmp = Path(tempfile.mkdtemp())
    (tmp / "term.html").write_text(page, encoding="utf-8")
    shots = tmp / "shots"
    shots.mkdir()
    node(SHOT_JS, tmp / "term.html", str(shots) + "/", 960, height)
    concat = tmp / "list.txt"
    with concat.open("w") as f:
        for i, (_, _, dur) in enumerate(frames):
            f.write(f"file '{shots / f'{i:04d}.png'}'\nduration {dur}\n")
        f.write(f"file '{shots / f'{len(frames) - 1:04d}.png'}'\n")
    gif("[0:v]scale=760:-2:flags=lanczos", ["-f", "concat", "-safe", "0", "-i", concat], out, colors=64)


# ---------------------------------------------------------------- main
def find_videos(project: Path) -> dict[str, Path]:
    found = {}
    for mp4 in sorted((project / "out").glob("*.mp4")):
        m = re.match(r".*?-(linear|from-end|suspense|[a-z0-9]+)$", mp4.stem)
        found[m.group(1) if m else mp4.stem] = mp4
    return found


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--project", default="examples/povodok", help="проект, из out/ которого берутся ролики")
    ap.add_argument("--video", action="append", default=[], metavar="EDIT=MP4",
                    help="ролик монтажа явно (можно несколько раз)")
    ap.add_argument("--log", help="настоящий вывод `python -m reels run ...` для terminal.gif")
    ap.add_argument("--cmd", default="python -m reels run examples/povodok --yes")
    ap.add_argument("--out", default="docs/media")
    ap.add_argument("--hero-start", type=float, default=0.0)
    ap.add_argument("--hero-length", type=float, default=0.0, help="длина отрывка, с; 0 — весь ролик")
    ap.add_argument("--edits-length", type=float, default=8.0)
    ap.add_argument("--only", default=",".join(ALL), help="что пересобрать: " + ",".join(ALL))
    a = ap.parse_args(argv)

    out = (ROOT / a.out) if not Path(a.out).is_absolute() else Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    only = [x.strip() for x in a.only.split(",") if x.strip()]
    project = Path(a.project) if Path(a.project).is_absolute() else ROOT / a.project
    videos = find_videos(project)
    for v in a.video:
        k, _, p = v.partition("=")
        videos[k] = Path(p).resolve()
    linear = videos.get("linear") or next(iter(videos.values()), None)

    for what in only:
        target = out / {"pipeline": "pipeline.svg", "qa": "qa.png", "hero": "hero.gif", "edits": "edits.gif",
                        "frames": "frames.png", "terminal": "terminal.gif"}[what]
        if what in ("hero", "edits", "frames") and not linear:
            print(f"  – {what}: нет роликов в {project / 'out'} — сначала python -m reels run {a.project} --yes")
            continue
        if what == "pipeline":
            make_pipeline(target)
        elif what == "qa":
            make_qa(target, ROOT / "local-model-test" / "frames")
        elif what == "hero":
            make_hero(target, linear, a.hero_start, a.hero_length)
        elif what == "edits":
            make_edits(target, videos, a.edits_length)
        elif what == "frames":
            make_frames(target, linear)
        elif what == "terminal":
            log = Path(a.log).read_text(encoding="utf-8") if a.log else SAMPLE_LOG
            make_terminal(target, log, a.cmd)
        print(f"  ✓ {target.relative_to(ROOT) if target.is_relative_to(ROOT) else target}"
              f"  {target.stat().st_size / 1e6:.2f} МБ")


if __name__ == "__main__":
    main()
