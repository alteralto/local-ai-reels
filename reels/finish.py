"""Постер в кадр 0, замеры готового ролика, сверка звука Whisper'ом, отчёт report.json / report.md."""
from __future__ import annotations

import importlib.util
import json
import re
import tempfile
from datetime import datetime
from pathlib import Path

from .util import ToolError, ff, run, say, write_json

# ожидания по умолчанию: docs/guide.md, «8. Постер и проверка»
EXPECT = {"width": 1080, "height": 1920, "fps": 30, "lufs": -17.0, "lufs_tol": 2.5, "min_ratio": 0.98}


def poster(src: Path, at: float, dst: Path, codecs: list[tuple[str, list[str]]] | None = None) -> Path:
    """Кадр с секунды at вшивается первым кадром (обложка на площадках); звук копируется как есть.

    Видео перекодируется кодировщиком профиля железа (h264_nvenc / h264_videotoolbox / …); не сработал —
    повтор на libx264. codecs — [(имя, аргументы ffmpeg)], по умолчанию hardware.video_codecs().
    """
    if codecs is None:
        from . import hardware  # noqa: WPS433
        codecs = hardware.video_codecs()
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        png = Path(tmp) / "poster.png"
        run([ff(), "-y", "-v", "error", "-ss", f"{at:.3f}", "-i", src, "-frames:v", "1", png])
        if not png.is_file():
            raise ToolError(f"не удалось взять кадр {at} с из {src}: ролик короче?")
        head = [ff(), "-y", "-v", "error", "-i", src, "-i", png, "-filter_complex",
                "[0:v][1:v]overlay=0:0:enable='eq(n,0)'[v]", "-map", "[v]", "-map", "0:a?"]
        for i, (name, vargs) in enumerate(codecs):
            try:
                run([*head, *vargs, "-c:a", "copy", "-movflags", "+faststart", dst])
                break
            except ToolError as e:
                if i == len(codecs) - 1:
                    raise
                say(f"    ! кодировщик {name} не сработал ({str(e).strip().splitlines()[-1][:160]}) — "
                    f"повторяю на {codecs[i + 1][0]}")
    return dst


def _rate(s: str) -> float:
    if "/" in s:
        a, b = s.split("/", 1)
        return float(a) / float(b) if float(b) else 0.0
    return float(s or 0)


def parse_probe(data: dict) -> dict:
    """JSON ffprobe → {"width", "height", "fps", "duration"}."""
    v = next((s for s in data.get("streams", []) if s.get("codec_type", "video") == "video"), {})
    fps = _rate(v.get("avg_frame_rate") or "") or _rate(v.get("r_frame_rate") or "0")
    dur = float(data.get("format", {}).get("duration") or v.get("duration") or 0)
    return {"width": int(v.get("width", 0)), "height": int(v.get("height", 0)), "fps": round(fps, 3),
            "duration": round(dur, 3)}


def probe(mp4: Path) -> dict:
    """Размер, частота кадров и длительность ролика: {"width", "height", "fps", "duration"}."""
    p = run([ff("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=codec_type,width,height,r_frame_rate,avg_frame_rate:format=duration", "-of", "json", mp4])
    return parse_probe(json.loads(p.stdout))


def parse_ebur128(out: str) -> float:
    """Интегральная громкость (LUFS) из сводки фильтра ebur128."""
    tail = out[out.rfind("Summary:"):] if "Summary:" in out else out
    m = re.search(r"\bI:\s*(-?(?:\d+(?:\.\d+)?|inf))\s*LUFS", tail)
    if not m:
        raise ToolError("ebur128: не найдена строка «I: … LUFS» — в файле нет звука?")
    return float(m.group(1))


def loudness(path: Path) -> float:
    """Интегральная громкость файла по EBU R128, LUFS."""
    p = run([ff(), "-hide_banner", "-nostats", "-i", path, "-af", "ebur128", "-f", "null", "-"])
    return parse_ebur128((p.stdout or "") + (p.stderr or ""))


def verify(mp4: Path, script: str, asr_cfg: dict) -> dict:
    """Whisper по готовому миксу: {"ratio", "heard"} или {"ratio": None, "skipped": причина}."""
    if importlib.util.find_spec("faster_whisper") is None:
        return {"ratio": None, "skipped": "не установлен faster-whisper (pip install faster-whisper)"}
    try:
        from . import asr  # noqa: WPS433 — тяжёлый модуль, грузится только здесь
    except ImportError as e:
        return {"ratio": None, "skipped": f"нет модуля распознавания: {e}"}
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "mix.wav"
        run([ff(), "-y", "-v", "error", "-i", mp4, "-vn", "-ac", "1", "-ar", "16000", wav])
        try:
            heard = asr.transcribe(wav, asr_cfg)
        except Exception as e:  # noqa: BLE001 — нет модели/сети не должно ронять отчёт
            return {"ratio": None, "skipped": f"распознавание не удалось: {str(e).splitlines()[0][:200] if str(e) else type(e).__name__}"}
    return {"ratio": round(float(asr.ratio(script, heard)), 4), "heard": heard}


def evaluate(meas: dict, expect: dict | None = None) -> list[str]:
    """Сравнение замеров с ожиданиями → список проблем по-русски (пусто — всё в норме)."""
    e = {**EXPECT, **(expect or {})}
    probs = []
    if (meas.get("width"), meas.get("height")) != (e["width"], e["height"]):
        probs.append(f"размер {meas.get('width')}×{meas.get('height')}, нужно {e['width']}×{e['height']}")
    if meas.get("fps") is not None and abs(float(meas["fps"]) - float(e["fps"])) > 0.05:
        probs.append(f"{meas['fps']:g} fps, нужно {e['fps']:g}")
    if e.get("duration") and abs(float(meas.get("duration", 0)) - float(e["duration"])) > e.get("duration_tol", 0.5):
        probs.append(f"длительность {meas.get('duration'):.2f} с, ожидалось {float(e['duration']):.2f} с")
    lufs = meas.get("lufs")
    if lufs is None:
        probs.append("нет звука")
    elif abs(lufs - e["lufs"]) > e["lufs_tol"]:
        probs.append(f"громкость {lufs:.1f} LUFS, нужно {e['lufs']:g} ± {e['lufs_tol']:g}")
    r = meas.get("ratio")
    if r is not None and r < e["min_ratio"]:
        probs.append(f"Whisper по миксу: совпадение {r:.0%} < {e['min_ratio']:.0%} — музыка забивает голос?")
    if meas.get("layout_errors"):
        probs.append(f"ошибок вёрстки: {meas['layout_errors']}")
    return probs


def check(mp4: Path, expect: dict | None = None) -> dict:
    """Замеры готового ролика и вердикт: {"ok", "problems", "width", "height", "fps", "duration", "lufs"}."""
    meas = probe(mp4)
    try:
        meas["lufs"] = round(loudness(mp4), 1)
    except ToolError:
        meas["lufs"] = None
    if expect and "ratio" in expect:
        meas["ratio"] = expect["ratio"]
    problems = evaluate(meas, expect)
    return {"file": str(mp4), **meas, "ok": not problems, "problems": problems}


def _cell(v, fmt: str = "{}") -> str:
    return "—" if v is None else fmt.format(v)


def write_report(project, results: list[dict]) -> tuple[Path, Path]:
    """report.json и report.md в project.out. results — по монтажу: результат check() + edit, ratio, layout."""
    out = Path(project.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().isoformat(timespec="seconds")
    jpath = write_json(out / "report.json", {"project": project.slug, "created": stamp, "edits": results})
    lines = [f"# Отчёт: {project.cfg.get('title') or project.slug}", "", f"Собрано {stamp.replace('T', ' ')}.", "",
             "| Монтаж | Файл | Длит., с | Размер | fps | LUFS | Whisper | Вёрстка | Итог |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        lay = r.get("layout_issues")
        if isinstance(lay, list):
            ne = sum(1 for i in lay if i.get("severity") == "error")
            lay = f"{ne} ош. / {len(lay) - ne} пред." if lay else "0"
        ratio = r.get("ratio")
        wh = f"{ratio:.0%}" if isinstance(ratio, (int, float)) else ("пропущено" if r.get("skipped") else "—")
        size = f"{r.get('width')}×{r.get('height')}" if r.get("width") else "—"
        verdict = "OK" if r.get("ok") else "; ".join(r.get("problems") or ["не проверено"])
        lines.append(f"| {r.get('edit', '—')} | {Path(r['file']).name if r.get('file') else '—'} | "
                     f"{_cell(r.get('duration'), '{:.2f}')} | {size} | {_cell(r.get('fps'), '{:g}')} | "
                     f"{_cell(r.get('lufs'), '{:.1f}')} | {wh} | {_cell(lay)} | {verdict} |")
    skipped = {r.get("skipped") for r in results if r.get("skipped")}
    if skipped:
        lines += ["", *[f"Сверка Whisper пропущена: {s}." for s in skipped]]
    lines += ["", "Норма: 1080×1920, fps как в render.fps, громкость около −17 LUFS (±2,5), "
              "Whisper по готовому миксу распознаёт текст почти дословно."]
    mpath = out / "report.md"
    mpath.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return jpath, mpath
