"""Сборка проекта HyperFrames для одного монтажа — обобщение scripts/build.py + scripts/polish.py.

Всё, что в build.py было зашито (тексты, цвета, тайминги, SVG), берётся из reel.yaml (project.cfg),
таймкодов этапа align ({"cuts", "words"}) и библиотеки шаблонов сцен (reels/scenes).

Слои кадра (снизу вверх): сцены блоков (дорожка 0) → финальная карточка (1) → подпись источника → полоска прогресса.
Звук: куски мастер-файла (2), музыка (3), вжух на склейках (5). Все <audio> обязаны иметь id —
HyperFrames молча выбрасывает звук без id.
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
from pathlib import Path

from . import mixdown, scenes, sfx
from . import timing as tm
from .project import Project
from .scenes import SceneCtx, Theme, esc, num, rich
from .util import cache_dir, say

GSAP_VERSION = "3.14.2"
GSAP_URL = f"https://cdn.jsdelivr.net/npm/gsap@{GSAP_VERSION}/dist/gsap.min.js"
# локальная копия GSAP (без сети): путь в REELS_GSAP или reels/vendor/gsap.min.js — кладётся рядом с index.html
GSAP_LOCAL = Path(__file__).parent / "vendor" / "gsap.min.js"
WHOOSH_DUR, WHOOSH_VOL, WHOOSH_LEAD = .38, .35, .14   # вжух начинается чуть раньше склейки
NO_CARD_TAIL = .5        # без текста финальной карточки — короткий хвост на последнем кадре
POSTER_LEAD = .35        # постер «auto»: за столько секунд до конца первого блока

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
    return out


def captions_html(words: list[dict], max_chars: int = 26, max_words: int = 5) -> str:
    """HTML субтитров блока. words — слова блока, время от начала блока; data-s — секунды от начала блока."""
    html = []
    for ch in chunks(words, max_chars, max_words):
        spans = " ".join(f'<span data-s="{w["s"]:.2f}">{esc(w["w"])}</span>' for w in ch)
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
    tl.to(S+" > :not(.cap2)", {{opacity:0, y:-24, duration:.16, ease:"power2.in"}}, tEnd - .16);
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
    return str(project.cfg.get("endcard") or "").strip()


def _layout(project: Project, order: list[int], timing: dict):
    """(раскладка, общая длительность, пауза, хвост, есть ли финальная карточка)."""
    gap, tail = float(project.cfg["gap"]), float(project.cfg["tail"])
    card = bool(_endcard(project)) and tail > 0
    if not card:
        tail = min(tail, NO_CARD_TAIL) if tail > 0 else NO_CARD_TAIL
    placements, total = tm.place(list(order), timing["cuts"], gap, tail)
    return placements, total, gap, tail, card


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", s.lower()).strip("-.") or "reel"


# --- сборка -------------------------------------------------------------------
def build_edit(project: Project, edit: str, order: list[int], timing: dict) -> Path:
    """Пишет проект HyperFrames монтажа в project.edit_dir(edit): index.html, voice.wav, music.wav, whoosh.wav,
    hyperframes.json, meta.json, package.json. timing = {"cuts": [N+1], "words": [слова сценария с s/e]}."""
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

    outs, scene_html, audio_html = [], [], []
    for i, p in enumerate(placements):
        b, uid = p.block, f"b{p.block}"
        block = project.blocks[b - 1]
        bw = tm.block_words(words, cuts, b)
        ctx = SceneCtx(b, uid, p.start, p.length, bw, block.text, theme, project.dir)
        out = scenes.render_scene(ctx, block.scene)
        outs.append((p, uid, out))
        last_open = i == len(placements) - 1 and not card       # без карточки последняя сцена держится до конца
        dur = total - p.start if last_open else p.length + gap
        scene_html.append(
            f'\n  <div id="{uid}" class="scene clip" data-start="{num(p.start)}" data-duration="{num(dur)}" '
            f'data-track-index="0">\n{out.html}\n    <div class="cap2">{captions_html(bw, max_chars, max_words)}</div>\n'
            f'  </div>')
        if i > 0:  # звук перехода на склейке
            audio_html.append(
                f'<audio id="sfx-wh{b}" class="clip" src="whoosh.wav" data-start="{num(max(p.start - WHOOSH_LEAD, 0))}" '
                f'data-duration="{num(WHOOSH_DUR)}" data-volume="{num(WHOOSH_VOL)}" data-track-index="5"></audio>')
        audio_html.append(
            f'<audio id="vo{b}" class="clip" src="voice.wav" data-start="{num(p.start)}" data-duration="{num(p.length)}" '
            f'data-media-start="{num(p.media_start)}" data-volume="1" data-track-index="2"></audio>')

    errors = js_syntax_errors({uid: out.js for _, uid, out in outs if out.js.strip()})
    anim_js = []
    for i, (p, uid, out) in enumerate(outs):
        js = out.js
        if uid in errors:
            say(f"сцена {uid}: синтаксическая ошибка в JS ({errors[uid]}) — анимация сцены отключена", err=True)
            js = "/* JS сцены отключён: синтаксическая ошибка */"
        end = p.start + p.length
        last_open = i == len(outs) - 1 and not card
        anim_js.append(
            f"  {{ const S='#{uid}', t={num(p.start)};\n    try {{\n{js}\n    }} catch (e) {{ console.error(\"сцена {uid}:\", e); }}\n"
            f"    capAnim2(S, t, {num(end)}); outro(S, {num(total if last_open else end + gap)}); }}")

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
        endcard_html = (f'\n  <div id="endcard" class="scene clip" data-start="{num(total - tail)}" data-duration="{num(tail)}" '
                        f'data-track-index="1">\n    <div class="t">{rich(_endcard(project))}</div>\n  </div>')
        endcard_js = (f'  tl.fromTo("#endcard .t", {{opacity:0, y:30}}, {{opacity:1, y:0, duration:.4}}, '
                      f'{num(total - tail + .05)});\n')
    nl = "\n"
    src = str(cfg.get("source") or "").strip()
    # подпись источника — буквально (без *акцентов*: в названиях бывают звёздочки, «Taking the P***»)
    src_html = f'\n  <div class="src mono">{esc(src).replace(nl, "<br>")}</div>' if src else ""
    fonts = getattr(theme, "fonts_url", "")
    fonts_html = f'<link href="{esc(fonts)}" rel="stylesheet" />\n' if fonts else ""
    scene_css = "\n".join(out.css for _, _, out in outs if out.css.strip())
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
  <div id="prog"></div>{''.join(scene_html)}{endcard_html}{src_html}
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
    if card:
        out.append(round(total - tail / 2, 1))
    return out


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
        return round(max(first.start + first.length - POSTER_LEAD, .1), 1)
    return round(min(max(float(spec), 0.0), total), 2)


__all__ = ["build_edit", "snapshot_times", "poster_time", "chunks", "captions_html", "resolve_theme",
           "js_syntax_errors", "WEAK"]
