"""Автопроверка вёрстки кадров по DOM и контактный лист.

Небольшие локальные модели не видят обрезанный и наложенный текст на кадрах (local-model-test/README.md),
поэтому кадры проверяются по геометрии: qa_layout.mjs открывает index.html в headless-браузере, ставит
GSAP-таймлайн на момент t и сравнивает прямоугольники надписей с кадром, контейнерами, графикой и зоной субтитров.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import render as hfr
from .util import ToolError, ff, run, say

SCRIPT = Path(__file__).with_name("qa_layout.mjs")
DRIVER = "playwright-core@1.49.1"
CAPTIONS_ZONE = {"top": 1450, "bottom": 1700}   # субтитры .cap2: снизу 260 px, высота 170 px

KINDS = {
    "out_of_frame": "за кадром",
    "edge": "у края кадра",
    "clipped": "обрезан",
    "overlap": "наложение",
    "over_graphic": "поверх графики",
    "captions_zone": "в зоне субтитров",
    "no_timeline": "нет таймлайна",
}


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(base) / "local-ai-reels" / "qa"


def _node_path() -> str:
    """NODE_PATH: свой кэш с playwright-core, уже заданный NODE_PATH, глобальные пакеты npm."""
    parts = [str(cache_dir() / "node_modules")]
    if os.environ.get("NODE_PATH"):
        parts.append(os.environ["NODE_PATH"])
    try:
        npm = "npm.cmd" if os.name == "nt" else "npm"
        g = subprocess.run([npm, "root", "-g"], capture_output=True, text=True, timeout=30).stdout.strip()
        if g:
            parts.append(g)
    except (OSError, subprocess.SubprocessError):
        pass
    return os.pathsep.join(parts)


def _install_driver() -> None:
    d = cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    say(f"Проверка вёрстки: ставлю {DRIVER} в {d} (один раз)…")
    npm = "npm.cmd" if os.name == "nt" else "npm"
    run([npm, "i", "--no-audit", "--no-fund", "--prefix", d, DRIVER],
        env={"PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1"}, timeout=600)


def layout_check(dir: Path, times: list[float], timeout: float = 300) -> list[dict]:
    """Проверка вёрстки index.html в моменты times. Возвращает [{t, kind, severity, text, rect, detail}]."""
    html = (Path(dir) / "index.html").resolve()
    if not html.is_file():
        raise ToolError(f"нет {html}")
    exe = hfr.browser() or hfr.find_browser()
    with tempfile.TemporaryDirectory() as tmp:
        arg = Path(tmp) / "args.json"
        arg.write_text(json.dumps({"html": str(html), "times": [float(t) for t in times], "browser": exe,
                                   "width": 1080, "height": 1920, "capZone": CAPTIONS_ZONE}), encoding="utf-8")
        env = {**os.environ, "NODE_PATH": _node_path()}
        cmd = ["node", str(SCRIPT), str(arg)]
        for attempt in range(2):
            try:
                p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   env=env, timeout=timeout)
            except FileNotFoundError as e:
                raise ToolError("не найден node: установите Node.js 22+") from e
            if p.returncode == 3 and attempt == 0:      # нет ни playwright, ни puppeteer
                _install_driver()
                continue
            break
    if p.returncode != 0:
        err = (p.stderr or p.stdout or "")[-2000:]
        hint = ""
        if "Executable doesn't exist" in err or "executablePath" in err or "Could not find" in err:
            hint = "\nНе найден браузер: задайте HYPERFRAMES_BROWSER_PATH=<путь к Chrome или Edge>"
        raise ToolError(f"проверка вёрстки упала (код {p.returncode}):\n{err}{hint}")
    return json.loads(p.stdout)["issues"]


def errors(issues: list[dict]) -> list[dict]:
    return [i for i in issues if i.get("severity") == "error"]


def format_issues(issues: list[dict]) -> str:
    """Список замечаний по-русски, по одному на строку; пусто — «Вёрстка: замечаний нет»."""
    if not issues:
        return "Вёрстка: замечаний нет"
    ne = len(errors(issues))
    lines = [f"Вёрстка: ошибок {ne}, предупреждений {len(issues) - ne}"]
    for i in sorted(issues, key=lambda x: (x.get("t", 0), x.get("severity") != "error")):
        mark = "✗" if i.get("severity") == "error" else "!"
        x, y, w, h = (i.get("rect") or [0, 0, 0, 0])[:4]
        kind = KINDS.get(i.get("kind"), i.get("kind"))
        lines.append(f"  {mark} {i.get('t', 0):6.2f} с  {kind}: «{i.get('text', '')}» — {i.get('detail', '')} "
                     f"[x={x} y={y} {w}×{h}]")
    return "\n".join(lines)


def contact_sheet(pngs: list[Path], out: Path, cols: int = 4, width: int = 270,
                  labels: list[str] | None = None) -> Path:
    """Склеивает кадры в один лист (ffmpeg scale + tile). labels — подписи (например, «8.0 с»); без шрифта — без них."""
    pngs = [Path(p) for p in pngs]
    if not pngs:
        raise ToolError("contact_sheet: нет кадров")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cols = max(1, min(cols, len(pngs)))
    rows = -(-len(pngs) // cols)
    n = len(pngs)
    inputs = []
    for p in pngs:
        inputs += ["-i", p]

    def graph(with_text: bool) -> str:
        parts = []
        for k in range(n):
            f = f"[{k}:v]scale={width}:-2,setsar=1"
            if with_text and labels:
                txt = str(labels[k]).replace("\\", "").replace(":", "\\:").replace("'", "")
                f += (f",drawtext=text='{txt}':x=8:y=8:fontsize={max(14, width // 12)}:fontcolor=white"
                      f":box=1:boxcolor=black@0.6:boxborderw=4")
            parts.append(f"{f}[v{k}]")
        if n == 1:
            return parts[0].rsplit("[", 1)[0] + "[out]"
        cells = ["+".join(["w0"] * (k % cols)) or "0" for k in range(n)]
        cells = [f"{x}_{'+'.join(['h0'] * (k // cols)) or '0'}" for k, x in enumerate(cells)]
        pads = "".join(f"[v{k}]" for k in range(n))
        fill = ":fill=black" if rows * cols > n else ""
        return ";".join(parts) + f";{pads}xstack=inputs={n}:layout={'|'.join(cells)}{fill}[out]"

    for with_text in ([True, False] if labels else [False]):
        try:
            run([ff(), "-y", "-v", "error", *inputs, "-filter_complex", graph(with_text), "-map", "[out]",
                 "-frames:v", "1", "-update", "1", out])
            return out
        except ToolError:
            if not with_text:
                raise
    return out
