"""Общие мелочи: вывод, запуск команд, ffmpeg, хэши, выбор устройства."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def say(*parts, err: bool = False) -> None:
    print(*parts, file=sys.stderr if err else sys.stdout, flush=True)


class ToolError(RuntimeError):
    """Внешняя программа (ffmpeg, npx, модель) не отработала. Сообщение — что сделать."""


def ff(tool: str = "ffmpeg") -> str:
    """Путь к ffmpeg/ffprobe: папка из FFMPEG_DIR или PATH."""
    d = os.environ.get("FFMPEG_DIR")
    exe = str(Path(d) / tool) if d else (shutil.which(tool) or tool)
    return exe


def run(cmd: list, cwd: Path | None = None, env: dict | None = None, check: bool = True,
        capture: bool = True, timeout: float | None = None) -> subprocess.CompletedProcess:
    """Запуск команды; при ошибке — ToolError с хвостом вывода."""
    try:
        p = subprocess.run([str(c) for c in cmd], cwd=cwd, env={**os.environ, **(env or {})}, text=True,
                           capture_output=capture, timeout=timeout, encoding="utf-8", errors="replace")
    except FileNotFoundError as e:
        raise ToolError(f"не найдена программа {cmd[0]}: установите её или добавьте в PATH") from e
    if check and p.returncode != 0:
        tail = ((p.stdout or "") + (p.stderr or ""))[-2500:]
        raise ToolError(f"{Path(str(cmd[0])).name} завершился с кодом {p.returncode}:\n{tail}")
    return p


def sha(*items) -> str:
    """Хэш входов этапа: строки/числа/словари хэшируются как JSON, пути — по содержимому файла."""
    h = hashlib.sha256()
    for it in items:
        if isinstance(it, Path):
            h.update(str(it.name).encode())
            h.update(it.read_bytes() if it.is_file() else b"<missing>")
        else:
            h.update(json.dumps(it, ensure_ascii=False, sort_keys=True, default=str).encode())
    return h.hexdigest()[:16]


def device(pref: str = "auto") -> str:
    """cuda, если есть видеокарта и torch её видит; иначе cpu. pref=cuda|cpu — без проверки."""
    if pref and pref != "auto":
        return pref
    try:
        import torch  # noqa: WPS433
        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        try:
            import ctranslate2  # noqa: WPS433
            return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except ImportError:
            return "cpu"


def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write_json(path: Path, data) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def wav_duration(path: Path) -> float:
    p = run([ff("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path])
    return float(p.stdout.strip())
