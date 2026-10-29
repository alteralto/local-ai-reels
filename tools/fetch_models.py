#!/usr/bin/env python3
"""Скачивание моделей конвейера одной командой.

    python tools/fetch_models.py                    # F5-TTS (русские веса) + голоса Piper в models/
    python tools/fetch_models.py --whisper --musicgen   # плюс Whisper и MusicGen в кэш Hugging Face
    python tools/fetch_models.py --only piper --dir /data/models
    python tools/fetch_models.py --dry-run          # только показать, что и куда будет скачано

Файлы F5 и Piper кладутся туда, где их ждёт конвейер (reels/voice.py):
    <dir>/f5-ru/F5TTS_v1_Base_v2/model_last_inference.safetensors
    <dir>/f5-ru/F5TTS_v1_Base/vocab.txt
    <dir>/piper/ru_RU-<голос>-medium.onnx (+ .onnx.json)
Whisper и MusicGen ложатся в кэш Hugging Face (HF_HOME), откуда их берут faster-whisper и transformers.

Если Hugging Face недоступен, задайте зеркало: HF_ENDPOINT=https://<зеркало> python tools/fetch_models.py
Без huggingface_hub файлы F5 и Piper качаются через urllib: с прогрессом и докачкой (HTTP Range).
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_ENDPOINT = "https://huggingface.co"

F5_REPO = "Misha24-10/F5-TTS_RUSSIAN"
F5_FILES = ("F5TTS_v1_Base_v2/model_last_inference.safetensors", "F5TTS_v1_Base/vocab.txt")

PIPER_REPO = "rhasspy/piper-voices"
PIPER_VOICES = ("denis", "dmitri")

WHISPER_REPO = "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
MUSICGEN_REPO = "facebook/musicgen-small"

GROUPS = ("f5", "piper", "whisper", "musicgen")

LICENSES = {
    "f5": f"{F5_REPO}: CC-BY-NC 4.0 — только некоммерческое использование.",
    "piper": "Голоса Piper ru_RU-denis-medium и ru_RU-dmitri-medium: CC0 — без ограничений.",
    "whisper": f"{WHISPER_REPO}: MIT.",
    "musicgen": f"{MUSICGEN_REPO}: веса под CC-BY-NC 4.0 — только некоммерческое использование.",
}


@dataclass
class FileJob:
    """Один файл из репозитория Hugging Face → конкретный путь на диске."""
    group: str
    repo: str
    path: str          # путь внутри репозитория
    target: Path       # куда положить

    def url(self, endpoint: str | None = None) -> str:
        return hf_url(self.repo, self.path, endpoint)


@dataclass
class SnapshotJob:
    """Весь репозиторий целиком — в кэш Hugging Face (snapshot_download)."""
    group: str
    repo: str
    allow: list[str] = field(default_factory=list)

    def url(self, endpoint: str | None = None) -> str:
        return f"{(endpoint or hf_endpoint()).rstrip('/')}/{self.repo}"


# --- адреса -----------------------------------------------------------------
def hf_endpoint() -> str:
    """Адрес Hugging Face или зеркала из HF_ENDPOINT."""
    return (os.environ.get("HF_ENDPOINT") or DEFAULT_ENDPOINT).rstrip("/")


def hf_url(repo: str, path: str, endpoint: str | None = None) -> str:
    """Прямая ссылка на файл: <endpoint>/<repo>/resolve/main/<path>."""
    return f"{(endpoint or hf_endpoint()).rstrip('/')}/{repo}/resolve/main/{path}"


def piper_path(voice: str, quality: str = "medium") -> str:
    """Путь голоса в rhasspy/piper-voices: ru/ru_RU/<голос>/<качество>/ru_RU-<голос>-<качество>.onnx."""
    return f"ru/ru_RU/{voice}/{quality}/ru_RU-{voice}-{quality}.onnx"


def plan(groups: list[str], root: Path) -> list[FileJob | SnapshotJob]:
    """Список заданий на скачивание для выбранных групп."""
    jobs: list[FileJob | SnapshotJob] = []
    if "f5" in groups:
        jobs += [FileJob("f5", F5_REPO, p, root / "f5-ru" / p) for p in F5_FILES]
    if "piper" in groups:
        for v in PIPER_VOICES:
            base = piper_path(v)
            for p in (base, base + ".json"):
                jobs.append(FileJob("piper", PIPER_REPO, p, root / "piper" / Path(p).name))
    if "whisper" in groups:
        jobs.append(SnapshotJob("whisper", WHISPER_REPO))
    if "musicgen" in groups:
        jobs.append(SnapshotJob("musicgen", MUSICGEN_REPO,
                                ["*.json", "*.safetensors", "*.model", "*.txt"]))
    return jobs


# --- скачивание -------------------------------------------------------------
def _human(n: float) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024 or unit == "ГБ":
            return f"{n:.1f} {unit}" if unit != "Б" else f"{int(n)} {unit}"
        n /= 1024
    return f"{n:.1f} ГБ"


def urllib_download(url: str, target: Path, chunk: int = 1 << 20) -> Path:
    """Скачивание через urllib с прогрессом и докачкой: недокачанный файл лежит рядом как <имя>.part."""
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = {"User-Agent": "local-ai-reels/fetch_models"}
    token = os.environ.get("HF_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if have:
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=60)  # noqa: S310 — адрес собран из констант/HF_ENDPOINT
    except urllib.error.HTTPError as e:
        if e.code == 416 and have:          # файл уже докачан целиком
            part.replace(target)
            return target
        raise
    with resp:
        if have and resp.status != 206:     # сервер не умеет Range — начинаем заново
            have = 0
        total = resp.headers.get("Content-Length")
        total = int(total) + have if total else None
        done = have
        with open(part, "ab" if have else "wb") as f:
            while True:
                buf = resp.read(chunk)
                if not buf:
                    break
                f.write(buf)
                done += len(buf)
                if sys.stderr.isatty():
                    pct = f" {done * 100 // total:3d}%" if total else ""
                    sys.stderr.write(f"\r  {target.name}: {_human(done)}"
                                     f"{' / ' + _human(total) if total else ''}{pct}   ")
                    sys.stderr.flush()
        if sys.stderr.isatty():
            sys.stderr.write("\n")
    if total and done < total:
        raise OSError(f"{target.name}: скачано {done} из {total} байт — запустите команду ещё раз, докачается")
    part.replace(target)
    return target


def hub_download(job: FileJob) -> Path:
    """Скачивание через huggingface_hub (учитывает HF_ENDPOINT и HF_TOKEN), файл копируется в target."""
    from huggingface_hub import hf_hub_download  # noqa: WPS433
    cached = Path(hf_hub_download(repo_id=job.repo, filename=job.path, endpoint=hf_endpoint()))
    job.target.parent.mkdir(parents=True, exist_ok=True)
    tmp = job.target.with_name(job.target.name + ".part")
    shutil.copyfile(cached, tmp)
    tmp.replace(job.target)
    return job.target


def have_hub() -> bool:
    try:
        import huggingface_hub  # noqa: F401, WPS433
    except ImportError:
        return False
    return True


def download_file(job: FileJob) -> Path:
    """Файл через huggingface_hub, если он установлен, иначе через urllib."""
    if have_hub():
        return hub_download(job)
    return urllib_download(job.url(), job.target)


def snapshot(job: SnapshotJob) -> str:
    """Репозиторий целиком в кэш Hugging Face; без huggingface_hub — понятная ошибка."""
    try:
        from huggingface_hub import snapshot_download  # noqa: WPS433
    except ImportError as e:
        raise RuntimeError(f"{job.repo}: нужен huggingface_hub (pip install huggingface_hub) — "
                           "он ставится вместе с faster-whisper и transformers") from e
    return snapshot_download(repo_id=job.repo, allow_patterns=job.allow or None, endpoint=hf_endpoint())


# --- команда ----------------------------------------------------------------
def parse_only(value: str | None, whisper: bool, musicgen: bool) -> list[str]:
    """Группы для скачивания: --only перекрывает остальные флаги; по умолчанию f5 + piper (+ флаги)."""
    if value:
        groups = [g.strip() for g in value.split(",") if g.strip()]
        bad = [g for g in groups if g not in GROUPS]
        if bad:
            raise SystemExit(f"неизвестные группы в --only: {', '.join(bad)}; доступны: {', '.join(GROUPS)}")
        return [g for g in GROUPS if g in groups]
    groups = ["f5", "piper"]
    if whisper:
        groups.append("whisper")
    if musicgen:
        groups.append("musicgen")
    return groups


def print_licenses(groups: list[str]) -> None:
    print("\nЛицензии моделей (подробности — NOTICE.md):")
    for g in groups:
        print(f"  - {LICENSES[g]}")
    if any(g in groups for g in ("f5", "musicgen")):
        print("  Внимание: ролики с озвучкой F5-RU и музыкой MusicGen — только для некоммерческого использования.")


def main(argv: list[str] | None = None,
         file_downloader=None, snapshot_downloader=None) -> int:
    ap = argparse.ArgumentParser(prog="fetch_models", description="Скачать модели для local-ai-reels.")
    ap.add_argument("--dir", default=os.environ.get("TTS_MODELS", "models"),
                    help="папка моделей (по умолчанию TTS_MODELS или ./models)")
    ap.add_argument("--only", help=f"только эти группы через запятую: {','.join(GROUPS)}")
    ap.add_argument("--whisper", action="store_true", help=f"заранее скачать {WHISPER_REPO}")
    ap.add_argument("--musicgen", action="store_true", help=f"заранее скачать {MUSICGEN_REPO}")
    ap.add_argument("--force", action="store_true", help="скачать заново, даже если файл уже есть")
    ap.add_argument("--dry-run", action="store_true", help="только показать адреса и пути, ничего не качать")
    args = ap.parse_args(argv)

    groups = parse_only(args.only, args.whisper, args.musicgen)
    root = Path(args.dir).expanduser()
    jobs = plan(groups, root)
    file_downloader = file_downloader or download_file
    snapshot_downloader = snapshot_downloader or snapshot
    endpoint = hf_endpoint()

    print(f"Папка моделей: {root.resolve()}")
    if endpoint != DEFAULT_ENDPOINT:
        print(f"Зеркало Hugging Face: {endpoint}")
    if args.dry_run:
        print("Режим --dry-run: ничего не скачивается.")

    failed: list[str] = []
    for job in jobs:
        if isinstance(job, FileJob):
            if job.target.is_file() and job.target.stat().st_size > 0 and not args.force:
                print(f"[{job.group}] есть: {job.target}")
                continue
            print(f"[{job.group}] {job.url(endpoint)}\n    -> {job.target}")
            if args.dry_run:
                continue
            try:
                file_downloader(job)
            except Exception as e:  # noqa: BLE001 — продолжаем остальные файлы
                failed.append(f"{job.repo}/{job.path}: {e}")
                print(f"    ошибка: {e}", file=sys.stderr)
        else:
            print(f"[{job.group}] {job.url(endpoint)}\n    -> кэш Hugging Face "
                  f"({os.environ.get('HF_HOME', '~/.cache/huggingface')})")
            if args.dry_run:
                continue
            try:
                snapshot_downloader(job)
            except Exception as e:  # noqa: BLE001
                failed.append(f"{job.repo}: {e}")
                print(f"    ошибка: {e}", file=sys.stderr)

    print_licenses(groups)
    if failed:
        print("\nНе скачалось:", file=sys.stderr)
        for f in failed:
            print(f"  - {f}", file=sys.stderr)
        print("Hugging Face недоступен? Задайте зеркало: HF_ENDPOINT=https://<зеркало> и повторите — "
              "недокачанные файлы докачаются.", file=sys.stderr)
        return 1
    if not args.dry_run:
        print("\nГотово." + ("" if root.resolve() == Path("models").resolve()
                             else f" Укажите путь конвейеру: export TTS_MODELS=\"{root.resolve()}\""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
