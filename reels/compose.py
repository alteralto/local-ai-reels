"""Сборка проекта HyperFrames для одного монтажа — обобщение scripts/build.py + scripts/polish.py.

Всё, что в build.py было зашито (тексты, цвета, тайминги, SVG), берётся из reel.yaml (project.cfg),
таймкодов этапа align ({"cuts", "words"}) и библиотеки шаблонов сцен (reels/scenes).

Слои кадра (снизу вверх): сцены блоков (дорожка 0; блок может состоять из нескольких кадров — scene: {shots: [...]})
→ фирменная шапка блока (brand) → финальная карточка (1) → подпись источника → полоска прогресса → зерно (grain).
Звук: куски мастер-файла (2), музыка (3), вжух на склейках (5), звуковые акценты sfx (6+). Все <audio> обязаны
иметь id — HyperFrames молча выбрасывает звук без id.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import mixdown, scenes, sfx
from . import timing as tm
from .project import ConfigError, Project, sfx_cues
from .scenes import SceneCtx, SceneError, Theme, esc, num, rich
from .util import cache_dir, say

GSAP_VERSION = "3.14.2"
GSAP_URL = f"https://cdn.jsdelivr.net/npm/gsap@{GSAP_VERSION}/dist/gsap.min.js"
# локальная копия GSAP (без сети): путь в REELS_GSAP или reels/vendor/gsap.min.js — кладётся рядом с index.html
GSAP_LOCAL = Path(__file__).parent / "vendor" / "gsap.min.js"
WHOOSH_DUR, WHOOSH_VOL, WHOOSH_LEAD = .38, .35, .14   # вжух начинается чуть раньше склейки
NO_CARD_TAIL = .5        # без текста финальной карточки — короткий хвост на последнем кадре
POSTER_LEAD = .35        # постер «auto»: за столько секунд до конца первого блока
SFX_TRACK = 6            # первая дорожка звуковых акцентов; пересекающиеся акценты уходят на 7, 8, …
GRAIN_FPS = 12           # зерно «дрожит» 12 раз в секунду, как плёнка
# переходы между кадрами блока: сколько секунд предыдущий кадр ещё виден под новым
TRANSITION_OVERLAP = {"cut": 0.0, "slam": 0.0, "wipe": .25, "fade": .3}

# предлоги и союзы, которыми фраза субтитров не заканчивается («…держит их в / городе»)
WEAK = {"в", "во", "на", "с", "со", "к", "ко", "у", "о", "об", "и", "а", "но", "до", "из", "за", "по", "от", "не", "без",
        "для", "что", "как", "их", "его", "её", "ее", "это", "the", "a"}


# --- тема -------------------------------------------------------------------
def _vars(theme: Theme) -> str:
    """CSS-переменные темы (запасной вариант, если в reels.themes нет theme_css)."""
    return (f":root {{ --bg:{theme.bg}; --deep:{theme.deep}; --ink:{theme.ink}; --accent:{theme.accent}; "
            f"--alt:{theme.alt}; --muted:{theme.muted}; --font:{theme.font}; --mono:{theme.mono}; }}")


def _fallback_theme(spec) -> Theme:
    if isinstance(spec, dict):
        names = {f.name for f in dataclasses.fields(Theme)} - {"name"}
        return Theme(str(spec.get("base") or "blueprint"), **{k: v for k, v in spec.items() if k in names})
    return Theme(str(spec or "blueprint"))


def resolve_theme(project: Project) -> tuple[Theme, str]:
    """Тема проекта и её блок :root с переменными. Без модуля reels.themes — тема по умолчанию из scenes.base."""
    spec = project.cfg.get("theme", "blueprint")
    try:
        from . import themes
    except ImportError:
        themes = None
    if themes is not None and hasattr(themes, "get_theme"):
        th = themes.get_theme(spec)
        return th, (themes.theme_css(th) if hasattr(themes, "theme_css") else _vars(th))
    th = _fallback_theme(spec)
    return th, _vars(th)


def _rgba(color: str, a: float) -> str:
    """Цвет с прозрачностью: #rgb/#rrggbb → rgba(); иначе color-mix (Chrome его понимает)."""
    c = str(color).strip()
    m = re.fullmatch(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", c)
    if m:
        h = m.group(1)
        if len(h) == 3:
            h = "".join(x * 2 for x in h)
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        return f"rgba({r},{g},{b},{a:g})"
    return f"color-mix(in srgb, {c} {a * 100:g}%, transparent)"


# --- субтитры (из polish.py) -------------------------------------------------
def chunks(words: list[dict], max_chars: int = 26, max_words: int = 5) -> list[list[dict]]:
    """Режет слова блока на фразы субтитров: по пунктуации или по длине строки.
    Фраза не заканчивается предлогом или союзом — такое слово уходит в следующую."""
    out, cur = [], []
    for w in words:
        cur.append(w)
        text = " ".join(x["w"] for x in cur)
        punct = re.search(r"[.,:;!?—–]$", w["w"])
        if punct and len(text) >= 10 or len(text) >= max_chars or len(cur) >= max_words:
            tail = []
            while not punct and len(cur) > 1 and cur[-1]["w"].lower() in WEAK:
                tail.insert(0, cur.pop())
            out.append(cur)
            cur = tail
    if cur:
        out.append(cur)
    if len(out) > 1:  # «город.» на 0,3 с отдельной фразой не успевают прочитать — клеим к предыдущей
        last, prev = " ".join(x["w"] for x in out[-1]), " ".join(x["w"] for x in out[-2])
        if len(last) < 10 and len(prev) + 1 + len(last) <= max_chars + 10 and len(out[-2]) + len(out[-1]) <= max_words:
            out[-2:] = [out[-2] + out[-1]]
    return out


def captions_html(words: list[dict], max_chars: int = 26, max_words: int = 5) -> str:
    """HTML субтитров блока. words — слова блока, время от начала блока; data-s — секунды от начала блока."""
    html = []
    for ch in chunks(words, max_chars, max_words):
        # после предлога или союза — неразрывный пробел: «у» не останется в конце строки
        spans = "".join(f'<span data-s="{w["s"]:.2f}">{esc(w["w"])}</span>'
                        + ("" if j == len(ch) - 1 else ("\u00a0" if w["w"].lower() in WEAK else " "))
                        for j, w in enumerate(ch))
        html.append(f'<div class="chunk" data-s="{ch[0]["s"]:.2f}">{spans}</div>')
    return "".join(html)


def _runtime_js(theme: Theme) -> str:
    """capAnim2(S, t, end) — фразы и подсветка текущего слова; outro(S, tEnd) — уход сцены перед склейкой."""
    ink, acc = json.dumps(theme.ink), json.dumps(theme.accent)
    return f"""
  function capAnim2(S, t, end) {{
    const ch = [...document.querySelectorAll(S+" .cap2 .chunk")];
    ch.forEach((c, i) => {{
      const s = t + parseFloat(c.dataset.s);
      const e = i + 1 < ch.length ? t + parseFloat(ch[i+1].dataset.s) : end;
      tl.fromTo(c, {{opacity:0, y:14}}, {{opacity:1, y:0, duration:.12, ease:"power2.out"}}, s);
      tl.set(c, {{opacity:0}}, e);  // старая фраза исчезает ровно в момент появления новой
      const ws = [...c.querySelectorAll("span")];
      ws.forEach((w, j) => {{
        const a = t + parseFloat(w.dataset.s);
        const b = j + 1 < ws.length ? t + parseFloat(ws[j+1].dataset.s) : e;
        tl.fromTo(w, {{color:{ink}}}, {{color:{acc}, duration:.06}}, a);
        tl.to(w, {{color:{ink}, duration:.12}}, b);
      }});
    }});
  }}
  function outro(S, tEnd) {{
    tl.to(S+" > :not(.cap2):not(.brand)", {{opacity:0, y:-24, duration:.16, ease:"power2.in"}}, tEnd - .16);
  }}"""


# --- CSS ----------------------------------------------------------------------
def _base_css(theme: Theme, vars_css: str) -> str:
    """Базовые стили — контракт с шаблонами сцен (.scene, .mono, .kicker, .big, em, переменные темы)."""
    line = _rgba(theme.ink, .06)
    grid = (f"background-image: linear-gradient({line} 1px, transparent 1px), "
            f"linear-gradient(90deg, {line} 1px, transparent 1px); background-size: 40px 40px;"
            if getattr(theme, "grid", True) else "")
    return f"""
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  {vars_css}
  html, body {{ width:1080px; height:1920px; overflow:hidden; background:var(--bg); }}
  #root {{ position:relative; width:1080px; height:1920px; font-family:var(--font); color:var(--ink); }}
  .scene {{ position:absolute; inset:0; overflow:hidden; background-color:var(--bg); {grid} }}
  .mono {{ font-family:var(--mono); letter-spacing:.04em; }}
  .kicker {{ position:absolute; left:80px; right:80px; top:150px; font-size:28px; text-transform:uppercase;
            color:var(--accent); line-height:1.35; font-family:var(--mono); }}
  .big {{ position:absolute; left:80px; right:60px; font-weight:800; font-size:96px; line-height:1.02;
         letter-spacing:-.03em; overflow:hidden; }}
  .big span {{ display:inline-block; }}
  #root em {{ font-style:normal; color:var(--accent); }}
  .src {{ position:absolute; left:80px; right:80px; bottom:110px; font-size:21px; opacity:.6; }}
  #endcard .t {{ position:absolute; left:80px; right:80px; top:780px; font-size:76px; font-weight:800;
                letter-spacing:-.02em; line-height:1.08; }}
  .cap2 {{ position:absolute; left:60px; right:60px; bottom:260px; height:170px; text-align:center; }}
  .cap2 .chunk {{ position:absolute; left:0; right:0; bottom:0; opacity:0; font-size:64px; font-weight:800;
                 line-height:1.12; letter-spacing:-.015em; color:var(--ink); }}
  .cap2 .chunk span {{ display:inline-block; }}
  #prog {{ position:absolute; left:0; top:0; height:10px; width:1080px; background:var(--accent);
          transform-origin:0 50%; z-index:50; }}
  .shot {{ position:absolute; inset:0; overflow:hidden; background-color:var(--bg); {grid} }}
  .shot > .flash {{ position:absolute; inset:0; background:#fff; opacity:0; pointer-events:none; z-index:30; }}
  .brand {{ position:absolute; left:40px; right:40px; top:34px; height:118px; z-index:40; display:flex;
           align-items:center; gap:18px; pointer-events:none; color:var(--ink); }}
  .brand img {{ width:56px; height:56px; object-fit:contain; flex:none; }}
  .brand .bn {{ font-family:var(--font); font-size:20px; font-weight:800; line-height:1.18; letter-spacing:.03em;
               text-transform:uppercase; }}
  .brand .rule {{ position:absolute; left:0; right:0; bottom:2px; height:2px; background:currentColor; opacity:.75; }}
  #root .scene.has-brand .kicker {{ top:186px; }}
  .partners {{ position:absolute; left:40px; right:40px; bottom:90px; z-index:40; display:flex; flex-wrap:wrap;
              justify-content:center; align-items:center; gap:14px 22px; font-family:var(--font); font-size:30px;
              font-weight:800; letter-spacing:.02em; text-transform:uppercase; color:var(--ink); }}
  .partners img {{ height:64px; width:auto; object-fit:contain; }}
  .partners .x {{ opacity:.6; font-weight:500; }}
  #grain {{ position:absolute; inset:0; z-index:70; pointer-events:none; background-image:url(grain.png);
           background-size:512px 512px; background-repeat:repeat; mix-blend-mode:overlay; }}
"""


# --- проверка JS сцен ---------------------------------------------------------
_CHECK_JS = ("const s=JSON.parse(require('fs').readFileSync(0,'utf8'));const r={};"
             "for(const k in s){try{new Function('tl','S','t',s[k])}catch(e){r[k]=String(e&&e.message||e)}}"
             "process.stdout.write(JSON.stringify(r))")


def js_syntax_errors(snippets: dict[str, str]) -> dict[str, str]:
    """Синтаксические ошибки JS сцен (через node, если он есть): {uid: сообщение}.
    try/catch в index.html ловит только ошибки выполнения, а синтаксическая ломает весь скрипт."""
    node = shutil.which("node")
    if not node or not snippets:
        return {}
    try:
        p = subprocess.run([node, "-e", _CHECK_JS], input=json.dumps(snippets), capture_output=True, text=True,
                           encoding="utf-8", timeout=30)
        return json.loads(p.stdout or "{}") if p.returncode == 0 else {}
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {}


def _gsap_file() -> Path | None:
    """Локальная копия GSAP, чтобы рендер не зависел от CDN (за прокси и без сети он недоступен).

    Порядок: REELS_GSAP → reels/vendor/gsap.min.js → кэш → npm pack gsap@<версия> → прямое скачивание.
    Не вышло — None: страница возьмёт GSAP с CDN.
    """
    if os.environ.get("REELS_GSAP"):
        p = Path(os.environ["REELS_GSAP"])
        return p if p.is_file() else None
    if GSAP_LOCAL.is_file():
        return GSAP_LOCAL
    cached = cache_dir() / f"gsap-{GSAP_VERSION}" / "gsap.min.js"
    if cached.is_file():
        return cached
    cached.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            npm = shutil.which("npm") or shutil.which("npm.cmd")
            if not npm:
                raise OSError("нет npm")
            subprocess.run([npm, "pack", f"gsap@{GSAP_VERSION}", "--pack-destination", tmp, "--silent"],
                           check=True, capture_output=True, timeout=120)
            tgz = next(Path(tmp).glob("gsap-*.tgz"))
            with tarfile.open(tgz) as t:
                data = t.extractfile("package/dist/gsap.min.js").read()
        cached.write_bytes(data)
        return cached
    except (OSError, subprocess.SubprocessError, StopIteration, KeyError, tarfile.TarError, AttributeError):
        pass
    try:
        with urllib.request.urlopen(GSAP_URL, timeout=30) as r:
            cached.write_bytes(r.read())
        return cached
    except OSError:
        say(f"! GSAP {GSAP_VERSION} не удалось сохранить локально — страница возьмёт его с CDN", err=True)
        return None


# --- раскладка ----------------------------------------------------------------
def _endcard(project: Project) -> str:
    """Текст финальной карточки (строка); у карточки-сцены (словарь) — пустая строка."""
    card = project.cfg.get("endcard")
    return "" if isinstance(card, dict) else str(card or "").strip()


def _endcard_scene(project: Project) -> dict | None:
    """Финальная карточка-сцена: {type, ...} или {shots: [...]} — рендерится как блок с uid "end"."""
    card = project.cfg.get("endcard")
    return card if isinstance(card, dict) and card else None


def _layout(project: Project, order: list[int], timing: dict):
    """(раскладка, общая длительность, пауза, хвост, есть ли финальная карточка)."""
    gap, tail = float(project.cfg["gap"]), float(project.cfg["tail"])
    card = bool(_endcard(project) or _endcard_scene(project)) and tail > 0
    if not card:
        tail = min(tail, NO_CARD_TAIL) if tail > 0 else NO_CARD_TAIL
    placements, total = tm.place(list(order), timing["cuts"], gap, tail)
    return placements, total, gap, tail, card


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", s.lower()).strip("-.") or "reel"


def _karaoke(project: Project) -> bool:
    """Субтитры-караоке внизу кадра (captions.style: karaoke) или без них (none — текст несут сами сцены)."""
    return str((project.cfg.get("captions") or {}).get("style", "karaoke")) != "none"


# --- кадры блока (scene: {shots: [...]}) ---------------------------------------
def shot_starts(spec: dict, words: list[dict], length: float, where: str = "") -> list[float]:
    """Начала кадров от начала блока, с. Первый кадр — с 0; следующий — с якоря start/at (timing.anchor),
    без якоря — равными долями блока. Кадр длится до начала следующего, последний — до конца блока."""
    shots = spec.get("shots") or []
    out: list[float] = []
    for k, sh in enumerate(shots):
        if k == 0:
            out.append(0.0)
            continue
        at = sh.get("start", sh.get("at"))
        if isinstance(at, (list, tuple)):
            at = at[0] if at else None
        try:
            s = tm.anchor(words, at, length * k / len(shots))
        except ValueError as e:
            raise SceneError(f"{where}кадр {k + 1}: {e}") from e
        s = round(min(max(s, 0.0), max(length - .1, 0.0)), 3)
        if s <= out[-1]:
            raise SceneError(f"{where}кадр {k + 1} начинается в {s:.2f} с — не позже предыдущего ({out[-1]:.2f} с); "
                             f"проверьте якорь at")
        out.append(s)
    return out


@dataclass
class _Unit:
    """Отрисованный блок (или финальная карточка): HTML содержимого, CSS, JS шаблонов, управляющий JS, файлы, звуки."""
    html: str
    css: list[str] = field(default_factory=list)
    js: list[tuple[str, str]] = field(default_factory=list)     # (uid сцены/кадра, JS шаблона)
    ctl: str = ""                                               # видимость кадров и переходы (JS блока)
    files: dict = field(default_factory=dict)
    cues: list[tuple[float, dict]] = field(default_factory=list)  # (время от начала блока, {sound, volume})
    shots: list[tuple[float, float]] = field(default_factory=list)  # кадры: (начало, конец) от начала блока


def _cue_times(cues: list[dict], words: list[dict], default: float, where: str) -> list[tuple[float, dict]]:
    out = []
    for c in cues:
        try:
            out.append((round(tm.anchor(words, c.get("at"), default), 3), c))
        except ValueError as e:
            raise SceneError(f"{where}звук {c.get('sound')}: {e}") from e
    return out


def _render_unit(ctx: SceneCtx, spec: dict, extra_sfx: list[dict], where: str) -> _Unit:
    """Сцена блока: одна (как раньше) или несколько кадров со своими uid ("b2s1", "b2s2", …)."""
    try:
        own_sfx = sfx_cues(spec.get("sfx"), f"{where.strip(': ')}.sfx")
    except ConfigError as e:
        raise SceneError(str(e)) from e
    cues = _cue_times(list(extra_sfx) + own_sfx, ctx.words, 0.0, where)
    shots = spec.get("shots") if isinstance(spec.get("shots"), list) else None
    if not shots:
        out = scenes.render_scene(ctx, {k: v for k, v in spec.items() if k != "sfx"})
        return _Unit(out.html, [out.css], [(ctx.uid, out.js)], "", dict(out.files), cues, [(0.0, ctx.length)])
    starts = shot_starts(spec, ctx.words, ctx.length, where)
    ends = starts[1:] + [None]
    unit = _Unit("", cues=cues)
    html, ctl = [], []
    for k, sh in enumerate(shots):
        uid = f"{ctx.uid}s{k + 1}"
        sctx = dataclasses.replace(ctx, uid=uid, shot_start=starts[k], shot_end=ends[k])
        params = {x: v for x, v in sh.items() if x not in ("transition", "sfx", "start", "brand")}
        out = scenes.render_scene(sctx, params)
        tr = str(sh.get("transition", "cut"))
        flash = '<div class="flash"></div>' if tr == "slam" else ""
        html.append(f'    <div id="{uid}" class="shot">\n{out.html}{flash}\n    </div>')
        unit.css.append(out.css)
        unit.js.append((uid, out.js))
        unit.files.update(out.files)
        unit.cues += _cue_times(sh.get("sfx") or [], ctx.words, starts[k], where)
        unit.shots.append((starts[k], ends[k] if ends[k] is not None else ctx.length))
        sel, t0 = f"#{uid}", f"t+{num(starts[k])}"
        if k > 0:
            prev = f"#{ctx.uid}s{k}"
            unit.css.append(f"{sel} {{ visibility:hidden; }}")
            ctl.append(f'    tl.set("{sel}", {{visibility:"visible"}}, {t0});')
            if tr == "slam":
                ctl.append(f'    tl.fromTo("{sel}", {{scale:1.08}}, {{scale:1, duration:.3, ease:"power3.out"}}, {t0});')
                ctl.append(f'    tl.fromTo("{sel} > .flash", {{opacity:.75}}, {{opacity:0, duration:.2, ease:"power1.out"}}, '
                           f'{t0});')
            elif tr == "wipe":
                ctl.append(f'    tl.fromTo("{sel}", {{clipPath:"inset(0% 100% 0% 0%)"}}, '
                           f'{{clipPath:"inset(0% 0% 0% 0%)", duration:.25, ease:"power2.inOut"}}, {t0});')
            elif tr == "fade":
                ctl.append(f'    tl.fromTo("{sel}", {{opacity:0}}, {{opacity:1, duration:.3, ease:"power1.out"}}, {t0});')
            ctl.append(f'    tl.set("{prev}", {{visibility:"hidden"}}, t+{num(starts[k] + TRANSITION_OVERLAP[tr])});')
        if sh.get("brand") is False:   # шапка спрятана на время кадра
            ctl.append(f'    tl.set(S+" > .brand", {{visibility:"hidden"}}, {t0});')
            if ends[k] is not None:
                ctl.append(f'    tl.set(S+" > .brand", {{visibility:"visible"}}, t+{num(ends[k])});')
    unit.html = "\n".join(html)
    unit.ctl = "\n".join(ctl)
    return unit


# --- фирменный стиль ------------------------------------------------------------
def _asset(project: Project, src, name: str, files: dict) -> str:
    """Файл проекта → имя в папке монтажа (копируется при сборке). Нет файла — ConfigError."""
    path = project.path(str(src))
    if path is None or not path.is_file():
        raise ConfigError(f"нет файла {src}")
    fname = f"{name}{path.suffix.lower()}"
    files[fname] = path
    return fname


def _brand_html(project: Project, files: dict) -> str:
    """Шапка: логотип 56 px + название капслоком в 2–3 строки + тонкая линия под ними (y≈150)."""
    b = project.cfg.get("brand") or {}
    if not b.get("header", True) or not (b.get("logo") or b.get("name")):
        return ""
    logo = f'<img src="{esc(_asset(project, b["logo"], "brand-logo", files))}" alt="" />' if b.get("logo") else ""
    name = f'<div class="bn">{esc(str(b.get("name") or "").strip()).replace(chr(10), "<br>")}</div>' \
        if b.get("name") else ""
    rule = '<div class="rule"></div>' if b.get("rule", True) else ""
    return f'\n    <div class="brand">{logo}{name}{rule}</div>'


def _partners_html(project: Project, files: dict) -> str:
    """Ряд партнёров внизу финальной карточки: имена капслоком и/или логотипы через «×»."""
    items = []
    for i, x in enumerate((project.cfg.get("brand") or {}).get("partners") or [], 1):
        if isinstance(x, dict) and x.get("logo"):
            items.append(f'<img src="{esc(_asset(project, x["logo"], f"partner-{i}", files))}" '
                         f'alt="{esc(x.get("name") or "")}" />')
        else:
            items.append(f'<span>{esc(x.get("name", "") if isinstance(x, dict) else x)}</span>')
    if not items:
        return ""
    return '\n    <div class="partners">' + '<span class="x">×</span>'.join(items) + "</div>"


def grain_png(path: Path, size: int = 512, seed: int = 23) -> Path:
    """Текстура зерна: серый шум size×size (среднее 128) — PNG без PIL (numpy + zlib)."""
    rng = np.random.default_rng(seed)
    img = np.clip(128 + rng.normal(0, 46, (size, size)), 0, 255).astype(np.uint8)
    raw = b"".join(b"\x00" + row.tobytes() for row in img)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (len(data).to_bytes(4, "big") + kind + data
                + (zlib.crc32(kind + data) & 0xFFFFFFFF).to_bytes(4, "big"))

    ihdr = size.to_bytes(4, "big") * 2 + bytes([8, 0, 0, 0, 0])
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9))
                     + chunk(b"IEND", b""))
    return path


def _safe_name(name: str) -> str:
    p = Path(str(name))
    if p.is_absolute() or ".." in p.parts or not p.parts:
        raise SceneError(f"файл сцены «{name}»: имя должно быть относительным, без «..»")
    return p.as_posix()


# --- сборка -------------------------------------------------------------------
def build_edit(project: Project, edit: str, order: list[int], timing: dict) -> Path:
    """Пишет проект HyperFrames монтажа в project.edit_dir(edit): index.html, voice.wav, music.wav, whoosh.wav,
    звуки sfx-*.wav, файлы сцен и логотипы, grain.png, hyperframes.json, meta.json, package.json.
    timing = {"cuts": [N+1], "words": [слова сценария с s/e]}."""
    if not project.master_wav.exists():
        raise FileNotFoundError(f"нет мастер-файла озвучки {project.master_wav}: сначала этап voice")
    cuts, words = timing["cuts"], timing["words"]
    if len(cuts) != len(project.blocks) + 1:
        raise ValueError(f"timing: границ {len(cuts)}, а блоков {len(project.blocks)} — нужно N+1, пересоберите align")
    cfg = project.cfg
    theme, vars_css = resolve_theme(project)
    placements, total, gap, tail, card = _layout(project, order, timing)
    cap = cfg.get("captions") or {}
    max_chars, max_words = int(cap.get("max_chars", 26)), int(cap.get("max_words", 5))
    karaoke = _karaoke(project)
    files: dict[str, Path] = {}
    brand_html = _brand_html(project, files)

    units, scene_html, audio_html, cue_list = [], [], [], []
    for i, p in enumerate(placements):
        b, uid = p.block, f"b{p.block}"
        block = project.blocks[b - 1]
        bw = tm.block_words(words, cuts, b)
        ctx = SceneCtx(b, uid, p.start, p.length, bw, block.text, theme, project.dir,
                       position=i, count=len(placements))
        unit = _render_unit(ctx, block.scene, getattr(block, "sfx", []), f"блок {b}: ")
        units.append((p, uid, unit))
        cue_list += [(p.start + at, c, uid) for at, c in unit.cues]
        last_open = i == len(placements) - 1 and not card       # без карточки последняя сцена держится до конца
        dur = total - p.start if last_open else p.length + gap
        head = brand_html if getattr(block, "brand", True) else ""
        caps = f'\n    <div class="cap2">{captions_html(bw, max_chars, max_words)}</div>' if karaoke else ""
        scene_html.append(
            f'\n  <div id="{uid}" class="scene clip{" has-brand" if head else ""}" data-start="{num(p.start)}" '
            f'data-duration="{num(dur)}" data-track-index="0">\n{unit.html}{caps}{head}\n  </div>')
        if i > 0:  # звук перехода на склейке
            audio_html.append(
                f'<audio id="sfx-wh{b}" class="clip" src="whoosh.wav" data-start="{num(max(p.start - WHOOSH_LEAD, 0))}" '
                f'data-duration="{num(WHOOSH_DUR)}" data-volume="{num(WHOOSH_VOL)}" data-track-index="5"></audio>')
        audio_html.append(
            f'<audio id="vo{b}" class="clip" src="voice.wav" data-start="{num(p.start)}" data-duration="{num(p.length)}" '
            f'data-media-start="{num(p.media_start)}" data-volume="1" data-track-index="2"></audio>')

    card_scene = _endcard_scene(project) if card else None
    card_unit = None
    if card_scene:
        ctx = SceneCtx(0, "end", total - tail, tail, [], "", theme, project.dir,
                       position=len(placements), count=len(placements) + 1)
        card_unit = _render_unit(ctx, card_scene, [], "финальная карточка: ")
        cue_list += [(total - tail + at, c, "end") for at, c in card_unit.cues]

    snippets = {uid: js for _, _, u in units for uid, js in u.js if js.strip()}
    if card_unit:
        snippets.update({uid: js for uid, js in card_unit.js if js.strip()})
    errors = js_syntax_errors(snippets)

    def scene_js(uid: str, js: str, start: float) -> str:
        if uid in errors:
            say(f"сцена {uid}: синтаксическая ошибка в JS ({errors[uid]}) — анимация сцены отключена", err=True)
            js = "/* JS сцены отключён: синтаксическая ошибка */"
        return (f"  {{ const S='#{uid}', t={num(start)};\n    try {{\n{js}\n    }} catch (e) {{ "
                f"console.error(\"сцена {uid}:\", e); }}")

    anim_js = []
    for i, (p, uid, unit) in enumerate(units):
        end = p.start + p.length
        last_open = i == len(units) - 1 and not card
        last = i == len(units) - 1
        # перед финальной карточкой сцена уходит к концу речи: карточка непрозрачна и закрыла бы уход
        out_t = total if last_open else (end if last and card else end + gap)
        tail_js = (f"    capAnim2(S, t, {num(end)}); " if karaoke else "    ") + f"outro(S, {num(out_t)}); }}"
        if len(unit.js) == 1 and not unit.ctl:   # одна сцена — как раньше
            anim_js.append(scene_js(uid, unit.js[0][1], p.start) + "\n" + tail_js)
        else:
            anim_js += [scene_js(sid, js, p.start) + " }" for sid, js in unit.js]
            anim_js.append(f"  {{ const S='#{uid}', t={num(p.start)};\n{unit.ctl}\n{tail_js}")
    if card:
        audio_html.append(
            f'<audio id="sfx-whend" class="clip" src="whoosh.wav" data-start="{num(max(total - tail - WHOOSH_LEAD, 0))}" '
            f'data-duration="{num(WHOOSH_DUR)}" data-volume="{num(WHOOSH_VOL)}" data-track-index="5"></audio>')

    # звуковые акценты: каждый звук пишется один раз, теги — на свободные дорожки 6, 7, …
    sounds: dict[str, float] = {}
    track_end: list[float] = []
    for n, (at, c, uid) in enumerate(sorted(cue_list, key=lambda x: x[0]), 1):
        name = c["sound"]
        if name not in sounds:
            sounds[name] = sfx.duration(sfx.make(name, project.edit_dir(edit) / f"sfx-{name}.wav"))
        dur, at = sounds[name], max(at, 0.0)
        tr = next((k for k, e in enumerate(track_end) if e <= at), len(track_end))
        if tr == len(track_end):
            track_end.append(0.0)
        track_end[tr] = at + dur
        audio_html.append(
            f'<audio id="sfx-{uid}-{n}-{name}" class="clip" src="sfx-{name}.wav" data-start="{num(at)}" '
            f'data-duration="{num(dur)}" data-volume="{num(c.get("volume", .6))}" '
            f'data-track-index="{SFX_TRACK + tr}"></audio>')

    mus = cfg.get("music") or {}
    has_music = mus.get("engine") != "none" and project.music_wav.exists()
    duck = has_music and bool(mus.get("duck", True))
    if has_music:
        # с приглушением громкость и затухания уже вписаны в music.wav монтажа (mixdown.duck_music)
        vol, fades = (1, "") if duck else (mus.get("volume", .24), ' data-fade-in="0.6" data-fade-out="1.6"')
        audio_html.append(
            f'<audio id="music" class="clip" src="music.wav" data-start="0" data-duration="{num(total)}" '
            f'data-volume="{num(vol)}"{fades} data-track-index="3"></audio>')

    endcard_html = endcard_js = ""
    if card:
        partners = _partners_html(project, files)
        if card_unit:
            endcard_html = (f'\n  <div id="end" class="scene clip{" has-brand" if brand_html else ""}" '
                            f'data-start="{num(total - tail)}" data-duration="{num(tail)}" data-track-index="1">\n'
                            f'{card_unit.html}{brand_html}{partners}\n  </div>')
            endcard_js = "\n".join([scene_js(sid, js, total - tail) + " }" for sid, js in card_unit.js]
                                   + ([f"  {{ const S='#end', t={num(total - tail)};\n{card_unit.ctl}\n  }}"]
                                      if card_unit.ctl else [])) + "\n"
        else:
            endcard_html = (f'\n  <div id="endcard" class="scene clip{" has-brand" if brand_html else ""}" '
                            f'data-start="{num(total - tail)}" data-duration="{num(tail)}" '
                            f'data-track-index="1">\n    <div class="t">{rich(_endcard(project))}</div>'
                            f'{brand_html}{partners}\n  </div>')
            endcard_js = (f'  tl.fromTo("#endcard .t", {{opacity:0, y:30}}, {{opacity:1, y:0, duration:.4}}, '
                          f'{num(total - tail + .05)});\n')
        if partners:
            sel = "#end" if card_unit else "#endcard"
            endcard_js += (f'  tl.fromTo("{sel} .partners", {{opacity:0, y:20}}, {{opacity:1, y:0, duration:.35}}, '
                           f'{num(total - tail + .25)});\n')
    grain = float(getattr(theme, "grain", 0) or 0)
    grain_html = f'\n  <div id="grain" style="opacity:{num(min(grain, 1))}"></div>' if grain > 0 else ""
    if grain > 0:
        steps = max(int(total * GRAIN_FPS), 1)
        endcard_js += (f'  tl.fromTo("#grain", {{backgroundPosition:"0px 0px"}}, {{backgroundPosition:"{steps * 173}px '
                       f'{steps * 311}px", duration:{num(total)}, ease:"steps({steps})"}}, 0);\n')
    nl = "\n"
    src = str(cfg.get("source") or "").strip()
    # подпись источника — буквально (без *акцентов*: в названиях бывают звёздочки, «Taking the P***»)
    src_html = f'\n  <div class="src mono">{esc(src).replace(nl, "<br>")}</div>' if src else ""
    fonts = getattr(theme, "fonts_url", "")
    fonts_html = f'<link href="{esc(fonts)}" rel="stylesheet" />\n' if fonts else ""
    all_units = [u for _, _, u in units] + ([card_unit] if card_unit else [])
    scene_css = "\n".join(c for u in all_units for c in u.css if c.strip())
    for u in all_units:
        files.update({_safe_name(k): v for k, v in u.files.items()})
    gsap_local = _gsap_file()

    html = f"""<!doctype html>
<html lang="{esc(cfg.get("lang") or "ru")}">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=1080, height=1920" />
{fonts_html}<script src="{"gsap.min.js" if gsap_local else GSAP_URL}"></script>
<style>{_base_css(theme, vars_css)}
{scene_css}
</style>
</head>
<body>
<div id="root" data-composition-id="main" data-start="0" data-duration="{num(total)}" data-width="1080" data-height="1920">
  <div id="prog"></div>{''.join(scene_html)}{endcard_html}{src_html}{grain_html}
  {(nl + '  ').join(audio_html)}
</div>
<script>
  window.__timelines = window.__timelines || {{}};
  const tl = gsap.timeline({{ paused:true }});
{_runtime_js(theme)}
  tl.fromTo("#prog", {{scaleX:0}}, {{scaleX:1, duration:{num(total)}, ease:"none"}}, 0);
{nl.join(anim_js)}
{endcard_js}  window.__timelines["main"] = tl;
  tl.seek(0);
</script>
</body>
</html>
"""
    d = project.edit_dir(edit)
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.html").write_text(html, encoding="utf-8")
    shutil.copyfile(project.master_wav, d / "voice.wav")
    if duck:
        mixdown.duck_music(project.music_wav, placements, total, d / "music.wav",
                           base_db=float(mus.get("duck_base_db", -3.0)), duck_db=float(mus.get("duck_db", -15.0)))
    elif has_music:
        shutil.copyfile(project.music_wav, d / "music.wav")
    elif (d / "music.wav").exists():
        (d / "music.wav").unlink()
    sfx.whoosh(d / "whoosh.wav", dur=WHOOSH_DUR)
    for fname, srcp in files.items():   # файлы сцен (SceneOut.files) и логотипы — рядом с index.html
        srcp = Path(srcp)
        srcp = srcp if srcp.is_absolute() else project.dir / srcp
        if not srcp.is_file():
            raise FileNotFoundError(f"нет файла {srcp} (нужен как {fname})")
        (d / fname).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(srcp, d / fname)
    if grain > 0:
        grain_png(d / "grain.png")
    if gsap_local:
        shutil.copyfile(gsap_local, d / "gsap.min.js")
    name = _slug(f"{project.slug}-{edit}")
    meta = {"hyperframes.json": {"$schema": "https://hyperframes.heygen.com/schema/hyperframes.json",
                                 "paths": {"blocks": "compositions", "components": "compositions/components",
                                           "assets": "assets"},
                                 "media": {"autoProxy": True}},
            "meta.json": {"id": name, "name": name},
            "package.json": {"name": name, "private": True, "type": "module"}}
    for fname, data in meta.items():
        (d / fname).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return d


# --- кадры для проверки и постер ---------------------------------------------
def snapshot_times(project: Project, order: list[int], timing: dict) -> list[float]:
    """Кадры для просмотра: по одному на блок в порядке монтажа — когда содержимое уже появилось
    (start + max(0.75·длины, длина − 0.6), до ухода сцены), плюс середина финальной карточки. Округление до 0.1."""
    placements, total, _, tail, card = _layout(project, order, timing)
    out = [round(p.start + max(.75 * p.length, p.length - .6), 1) for p in placements]
    for p in placements:   # блок из нескольких кадров: каждый кадр незадолго до смены
        spec = project.blocks[p.block - 1].scene
        if isinstance(spec.get("shots"), list) and len(spec["shots"]) > 1:
            try:
                st = shot_starts(spec, tm.block_words(timing["words"], timing["cuts"], p.block), p.length)
            except SceneError:
                continue
            out += [round(p.start + max(a + .7 * (b - a), b - .35), 1) for a, b in zip(st, st[1:])]
    if card:
        out.append(round(total - tail / 2, 1))
    out.append(round(poster_time(project, order, timing), 1))  # обложку проверяем тоже
    return sorted(set(out))


def poster_time(project: Project, order: list[int], timing: dict) -> float:
    """Секунда монтажа для постера (кадр 0 обложки). cfg["poster"]: число | {at: число} | "auto".

    "auto" — конец первого блока монтажа минус 0.35 с: крючок уже целиком на экране (заголовок и графика
    доанимированы, на субтитрах — последняя фраза), а уход сцены перед склейкой ещё не начался.
    """
    spec = project.cfg.get("poster", "auto")
    if isinstance(spec, dict):
        spec = spec.get("at", "auto")
    placements, total, *_ = _layout(project, order, timing)
    if spec is None or spec == "auto":
        first = placements[0]
        t = max(first.start + first.length - POSTER_LEAD, .1)
        cap = project.cfg.get("captions") or {}
        bw = tm.block_words(timing["words"], timing["cuts"], first.block)
        for ch in chunks(bw, int(cap.get("max_chars", 26)), int(cap.get("max_words", 5))) if _karaoke(project) else []:
            s = first.start + ch[0]["s"]
            if s - .05 < t < s + .3:      # фраза только проявляется — берём кадр до неё
                t = max(s - .1, .1)
        return round(t, 2)
    return round(min(max(float(spec), 0.0), total), 2)


__all__ = ["build_edit", "snapshot_times", "poster_time", "chunks", "captions_html", "resolve_theme",
           "js_syntax_errors", "shot_starts", "grain_png", "WEAK"]
