"""HyperFrames: lint, snapshot, render; поиск браузера.

Обёртка над `npx --yes hyperframes@<HF_VERSION>` с выключенной телеметрией (как scripts/hf.sh).
Если HyperFrames не смог скачать Chrome (403) — один повтор с уже установленным браузером
(HYPERFRAMES_BROWSER_PATH). Если первый запуск упал с «Navigation timeout» — один повтор как есть.
"""
from __future__ import annotations

import glob
import os
import re
import subprocess
import sys
from pathlib import Path

from .util import ToolError, say

HF_VERSION = "0.8.75"

# где обычно лежит браузер на Chromium (порядок = приоритет)
_BROWSER_GLOBS = [
    # Windows
    r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    r"C:/Program Files/Microsoft/Edge/Application/msedge.exe",
    r"C:/Program Files/Google/Chrome/Application/chrome.exe",
    r"C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    "~/AppData/Local/Google/Chrome/Application/chrome.exe",
    # macOS
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    # Linux
    "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
    "/usr/bin/microsoft-edge", "/snap/bin/chromium", "/opt/google/chrome/chrome",
    # кэши Playwright / Puppeteer / HyperFrames
    "~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome",
    "~/Library/Caches/ms-playwright/chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium",
    "~/AppData/Local/ms-playwright/chromium-*/chrome-win*/chrome.exe",
    "/opt/pw-browsers/chromium-*/chrome-linux*/chrome",
    "/opt/pw-browsers/chromium_headless_shell-*/chrome-headless-shell-linux*/chrome-headless-shell",
    "~/.cache/puppeteer/chrome/*/chrome-*/chrome",
    "~/.cache/puppeteer/chrome-headless-shell/*/chrome-headless-shell-*/chrome-headless-shell",
    "~/.cache/hyperframes/**/chrome-headless-shell",
]

_DOWNLOAD_ERR = re.compile(r"\b403\b|Forbidden|Failed to download|download.*(?:Chrome|browser)|"
                           r"Could not find (?:Chrome|browser|expected browser)|Browser was not found", re.I)
_NAV_TIMEOUT = re.compile(r"Navigation timeout|TimeoutError: Navigation", re.I)


def browser() -> str | None:
    """Браузер, заданный пользователем (HYPERFRAMES_BROWSER_PATH); None — HyperFrames скачает свой."""
    p = os.environ.get("HYPERFRAMES_BROWSER_PATH")
    return p if p else None


def find_browser() -> str | None:
    """Ищет установленный Chrome / Edge / Chromium в обычных местах. None — не нашёлся."""
    for pat in _BROWSER_GLOBS:
        pat = os.path.expanduser(pat)
        hits = sorted(glob.glob(pat, recursive=True), reverse=True) if any(c in pat for c in "*?[") else [pat]
        for h in hits:
            if os.path.isfile(h) and os.access(h, os.X_OK):
                return h
    return None


def _env(browser_path: str | None = None) -> dict:
    env = {**os.environ, "HYPERFRAMES_NO_TELEMETRY": "1", "DO_NOT_TRACK": "1"}
    if os.environ.get("HF_CHROME"):
        env["PRODUCER_HEADLESS_SHELL_PATH"] = os.environ["HF_CHROME"]
    if browser_path:
        env["HYPERFRAMES_BROWSER_PATH"] = browser_path
    return env


def _npx() -> str:
    return "npx.cmd" if sys.platform == "win32" else "npx"


def _exec(args: list, cwd: Path, env: dict, verbose: bool, timeout: float | None) -> tuple[int, str]:
    cmd = [_npx(), "--yes", f"hyperframes@{HF_VERSION}", *[str(a) for a in args]]
    try:
        if not verbose:
            p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=timeout)
            return p.returncode, (p.stdout or "") + (p.stderr or "")
        out = []
        with subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                              encoding="utf-8", errors="replace") as p:
            for line in p.stdout:
                out.append(line)
                print(line, end="", flush=True)
            code = p.wait(timeout=timeout)
        return code, "".join(out)
    except FileNotFoundError as e:
        raise ToolError("не найден npx: установите Node.js 22+ (https://nodejs.org) и откройте терминал заново") from e
    except subprocess.TimeoutExpired as e:
        raise ToolError(f"hyperframes {args[0]}: превышено время ожидания {timeout} с") from e


def hf(args: list, cwd: Path, verbose: bool = False, check: bool = True, timeout: float | None = None) -> str:
    """Запуск `hyperframes <args>` в папке проекта; возвращает вывод. Повторы — при 403 и «Navigation timeout»."""
    cwd = Path(cwd)
    env = _env(browser())
    code, out = _exec(args, cwd, env, verbose, timeout)
    if code != 0 and _NAV_TIMEOUT.search(out):
        say("HyperFrames: «Navigation timeout» — браузер долго стартовал, повторяю…")
        code, out = _exec(args, cwd, env, verbose, timeout)
    if code != 0 and _DOWNLOAD_ERR.search(out) and not browser():
        found = find_browser()
        if found:
            say(f"HyperFrames не смог скачать Chrome — беру установленный браузер: {found}\n"
                f"  (чтобы не искать каждый раз: HYPERFRAMES_BROWSER_PATH=\"{found}\")")
            code, out = _exec(args, cwd, _env(found), verbose, timeout)
    if check and code != 0:
        hint = ""
        if _DOWNLOAD_ERR.search(out):
            hint = ("\nChrome не скачивается: задайте HYPERFRAMES_BROWSER_PATH=<путь к Chrome или Edge> "
                    "(см. «Частые ошибки» в docs/guide.md)")
        raise ToolError(f"hyperframes {args[0]} завершился с кодом {code}:\n{out[-2500:]}{hint}")
    return out


def parse_lint(out: str) -> dict:
    """«N error(s), M warning(s)» → {"errors", "warnings", "output"}."""
    m = re.search(r"(\d+)\s+error\(?s?\)?,\s*(\d+)\s+warning", out)
    errors, warnings = (int(m.group(1)), int(m.group(2))) if m else (len(re.findall(r"^\s*[✗×]", out, re.M)), 0)
    return {"errors": errors, "warnings": warnings, "output": out}


def lint(dir: Path, verbose: bool = False) -> dict:
    """`hyperframes lint`: {"errors": int, "warnings": int, "output": str}. Ошибки lint не бросают исключение."""
    return parse_lint(hf(["lint"], Path(dir), verbose=verbose, check=False))


def snapshot(dir: Path, times: list[float], verbose: bool = False) -> list[Path]:
    """Кадры в моменты times (с) → PNG из dir/snapshots, по порядку времени. Старые кадры удаляются."""
    dir = Path(dir)
    snap = dir / "snapshots"
    if snap.is_dir():
        for f in list(snap.glob("frame-*.png")) + list(snap.glob("contact-sheet.*")):
            f.unlink()
    at = ",".join(f"{t:g}" for t in times)
    hf(["snapshot", "--at", at, "--describe", "false", "--no-end"], dir, verbose=verbose)
    pngs = sorted(snap.glob("frame-*.png"), key=lambda p: int(re.search(r"frame-(\d+)", p.name).group(1)))
    if len(pngs) != len(times):
        raise ToolError(f"hyperframes snapshot: ожидалось {len(times)} кадров, получено {len(pngs)} в {snap}")
    return pngs


def render(dir: Path, out: Path, fps: int = 30, quality: str = "looks", verbose: bool = False) -> Path:
    """Рендер композиции в mp4. out — любой путь (относительный — от текущей папки, не от проекта)."""
    dir, out = Path(dir).resolve(), Path(out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    args = ["render", "-o", out, "--fps", fps, "--quality", quality]
    if not verbose:
        args.append("--quiet")
    log = hf(args, dir, verbose=verbose)
    if not out.is_file() or out.stat().st_size == 0:
        raise ToolError(f"hyperframes render не создал {out}:\n{log[-2500:]}")
    return out
