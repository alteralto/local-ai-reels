"""Командная строка конвейера: python -m reels <команда> …"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

import yaml

from . import __version__
from .project import CONFIG_NAME, ConfigError, load
from .util import ToolError, say

PKG = Path(__file__).resolve().parent
EXIT_GATE = 3


def _projects(paths: list[str]) -> list[Path]:
    out = []
    for p in paths:
        q = Path(p)
        if q.is_file() and q.name == CONFIG_NAME:
            q = q.parent
        if not (q / CONFIG_NAME).exists():
            raise ConfigError(f"{q}: нет {CONFIG_NAME}")
        out.append(q)
    return out


# --- команды ------------------------------------------------------------------------------------------------------
ROLES = ["Крючок", "Факт", "Поворот", "Панчлайн"]


def text_project(paragraphs: list[str], theme: str = "blueprint") -> dict:
    """reel.yaml из своего текста: по блоку на абзац, сцена headline из первых слов блока с якорями на них.
    Для «Просто» в десктопе и для тех, кто не хочет подбирать сцены: всё на экране — из вашего текста."""
    blocks = []
    for i, text in enumerate(paragraphs):
        words = text.split()
        lines, cur = [], []
        for w in words[:7]:  # заголовок — начало блока, не больше трёх строк по ~16 знаков
            if cur and len(" ".join(cur + [w])) > 16:
                lines.append(cur)
                cur = []
                if len(lines) == 3:
                    break
            cur.append(w)
        if cur and len(lines) < 3:
            lines.append(cur)
        clean = [" ".join(x).rstrip(",;:—–-") for x in lines]
        longest = max(range(len(clean)), key=lambda k: len(clean[k]))
        clean[longest] = f"*{clean[longest]}*"
        at = [re.sub(r"[^\w-]", "", x[0]) for x in lines]
        name = ROLES[i] if len(paragraphs) == 4 else f"Блок {i + 1}"
        blocks.append({"name": name, "text": text,
                       "scene": {"type": "headline", "kicker": name.lower(), "lines": clean, "at": at}})
    order = "".join(str(i + 1) for i in range(len(paragraphs)))
    edits = {"linear": order}
    if len(paragraphs) > 1:
        edits["from-end"] = order[-1] + order[:-1]
    title = re.split(r"[.!?:]", paragraphs[0])[0].strip()
    if len(title) > 60:
        title = title[:60].rsplit(" ", 1)[0] + "…"
    return {"title": title, "theme": theme, "blocks": blocks, "edits": edits}


def cmd_new(a) -> int:
    dst = Path(a.dir)
    if (dst / CONFIG_NAME).exists():
        raise ConfigError(f"{dst / CONFIG_NAME} уже есть")
    if a.text:
        raw = sys.stdin.read() if a.text == "-" else Path(a.text).read_text(encoding="utf-8")
        paras = [" ".join(x.split()) for x in re.split(r"\n\s*\n", raw.strip()) if x.strip()]
        if len(paras) == 1:  # абзацы без пустых строк — по строке на блок
            paras = [" ".join(x.split()) for x in raw.strip().splitlines() if x.strip()]
        if not 1 <= len(paras) <= 9:
            raise ConfigError(f"--text: нужно от 1 до 9 абзацев, сейчас {len(paras)}")
        dst.mkdir(parents=True, exist_ok=True)
        cfg = text_project(paras, a.theme or "blueprint")
        (dst / CONFIG_NAME).write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False, width=120),
                                       encoding="utf-8")
        load(dst)  # сразу проверить, что получилось
        say(f"создан {dst / CONFIG_NAME} из вашего текста ({len(paras)} блоков) — "
            f"python -m reels run {dst}")
        return 0
    dst.mkdir(parents=True, exist_ok=True)
    if not a.source:
        shutil.copy(PKG / "templates" / CONFIG_NAME, dst / CONFIG_NAME)
        say(f"создан {dst / CONFIG_NAME} — впишите сценарий и запустите: python -m reels run {dst}")
        return 0
    src = _projects([a.source])[0].resolve()
    cfg = yaml.safe_load((src / CONFIG_NAME).read_text(encoding="utf-8"))
    for sub in ("scenes", "assets"):
        if (src / sub).exists():
            shutil.copytree(src / sub, dst / sub, dirs_exist_ok=True)
    # файлы, на которые ссылается проект-образец снаружи своей папки, копируются в assets/
    for section, key in (("voice", "file"), ("voice", "words"), ("music", "file")):
        rel = (cfg.get(section) or {}).get(key)
        if rel and not Path(rel).is_absolute():
            f = (src / rel).resolve()
            if f.exists() and not f.is_relative_to(src):
                (dst / "assets").mkdir(exist_ok=True)
                name = f.name
                shutil.copy(f, dst / "assets" / name)
                cfg[section][key] = f"assets/{name}"
    (dst / CONFIG_NAME).write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False, width=120),
                                   encoding="utf-8")
    say(f"создан {dst} по образцу {src.name}")
    return 0


def cmd_check(a) -> int:
    from .textcheck import check_script
    bad = 0
    for d in _projects(a.projects):
        p = load(d)
        issues = check_script(p)
        say(f"■ {p.slug}: {p.total_words}/{p.cfg['limit']} слов, блоков {len(p.blocks)}, "
            f"монтажи {', '.join(p.edits)}")
        for b in p.blocks:
            say(f"   {b.index}. {b.name:<10} {b.words:>3} сл.  [{b.scene.get('type')}]")
        for i in issues:
            say("  ", i)
        bad += any(i.level == "error" for i in issues)
    return 1 if bad else 0


def _run_project(d: Path, a, interactive: bool | None) -> tuple[str, str]:
    from . import pipeline
    p = load(d)
    try:
        pipeline.run(p, yes=a.yes, edits=a.edit, from_stage=getattr(a, "from_stage", None),
                     only=getattr(a, "only", None), until=getattr(a, "until", None), force=a.force, qa=a.qa,
                     interactive=interactive)
        return "ok", ""
    except pipeline.GateWait as g:
        say(f"\n── ждёт подтверждения «{g.gate}» ──\n{g.summary}\n"
            f"Подтвердить: python -m reels approve {d} {g.gate}   (или запустить с --yes)")
        return "gate", g.gate


def cmd_run(a) -> int:
    from .pipeline import StageError
    d = _projects([a.project])[0]
    try:
        status, _ = _run_project(d, a, None if not a.no_input else False)
    except (StageError, ToolError, ConfigError) as e:
        say(f"✗ {e}", err=True)
        return 1
    return EXIT_GATE if status == "gate" else 0


def cmd_batch(a) -> int:
    from .pipeline import StageError
    rows = []
    for arg in a.projects:
        d, t0 = Path(arg), time.time()
        try:
            d = _projects([arg])[0]
            status, note = _run_project(d, a, False)
        except (StageError, ToolError, ConfigError) as e:
            status, note = "fail", str(e).splitlines()[0][:100]
        except Exception as e:  # noqa: BLE001 — пакет не должен падать из-за одного проекта
            status, note = "fail", f"{type(e).__name__}: {e}"[:100]
        rows.append((d.name, status, f"{time.time() - t0:.0f} с", note))
    say("\n■ пакет:")
    for name, status, dt, note in rows:
        mark = {"ok": "✓", "gate": "…", "fail": "✗"}[status]
        say(f"  {mark} {name:<28} {dt:>6}  {note}")
    return 1 if any(r[1] == "fail" for r in rows) else (EXIT_GATE if any(r[1] == "gate" for r in rows) else 0)


def cmd_status(a) -> int:
    from .pipeline import status, status_json
    if a.json:  # контракт для приложений (Rust, десктоп): массив объектов, ошибка проекта — в поле error
        out = []
        for d in a.projects:
            try:
                out.append(status_json(load(_projects([d])[0])))
            except ConfigError as e:
                out.append({"dir": str(Path(d).resolve()), "slug": Path(d).name, "title": Path(d).name, "edits": {},
                            "stages": [], "gates": [], "outputs": {}, "report": None, "error": str(e)})
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    for d in _projects(a.projects):
        p = load(d)
        say(f"■ {p.slug}")
        for key, st, at in status(p):
            mark = {"done": "✓", "approved": "✓", "stale": "~", "todo": "·", "waiting": "…", "n/a": "-"}[st]
            say(f"  {mark} {key:<22} {st:<9} {at}")
    return 0


def cmd_approve(a) -> int:
    from .pipeline import StageError, approve
    try:
        say(approve(load(_projects([a.project])[0]), a.gate))
    except StageError as e:
        say(f"✗ {e}", err=True)
        return 1
    return 0


def cmd_clean(a) -> int:
    from .pipeline import clean
    for d in _projects(a.projects):
        clean(load(d))
        say(f"очищено: {d / 'build'}")
    return 0


def cmd_scenes(a) -> int:
    from .scenes import REGISTRY
    if a.name:
        if a.name not in REGISTRY:
            say(f"нет сцены «{a.name}»; есть: {', '.join(sorted(REGISTRY))}", err=True)
            return 1
        _, doc = REGISTRY[a.name]
        say(f"{a.name} — {doc.summary}\n\nпараметры:")
        for k, v in doc.params.items():
            say(f"  {k}: {v}")
        if doc.example:
            say("\nпример:\n" + yaml.safe_dump({"scene": {"type": a.name, **doc.example}}, allow_unicode=True,
                                               sort_keys=False, width=120))
        return 0
    for name, (_, doc) in sorted(REGISTRY.items()):
        say(f"  {name:<10} {doc.summary}")
    try:
        from .themes import THEMES
        say(f"\nтемы: {', '.join(THEMES)}")
    except ImportError:
        pass
    say("\nподробно: python -m reels scenes <имя>")
    return 0


def cmd_draft(a) -> int:
    from .draft import DraftError, draft
    try:
        facts = Path(a.facts).read_text(encoding="utf-8") if a.facts else ""
        brand = yaml.safe_load(Path(a.brand).read_text(encoding="utf-8")) if a.brand else None
        path = draft(a.topic, Path(a.out), facts=facts, url=a.url, model=a.model, limit=a.limit, theme=a.theme,
                     format=a.format, hooks=a.hooks, brand=brand)
    except (DraftError, ToolError, OSError, KeyError) as e:
        say(f"✗ {e}", err=True)
        return 1
    say(f"черновик: {path}\nдальше: проверьте факты и запустите  python -m reels run {a.out}")
    return 0


def cmd_factory(a) -> int:
    from .factory import cmd_factory as run
    return run(a)


def cmd_doctor(a) -> int:
    from .hardware import doctor_json, doctor_report
    project = _projects([a.project])[0] if a.project else None
    if a.json:
        print(json.dumps(doctor_json(project), ensure_ascii=False, indent=1))
    else:
        print(doctor_report(project), end="")
    return 0


def _hw_flags(sp) -> None:
    """--device/--profile/--threads/--allow-slow: ставят REELS_* до запуска конвейера (важнее reel.yaml)."""
    from .project import HW_PROFILES
    g = sp.add_argument_group("железо (docs/hardware.md)")
    g.add_argument("--device", help="устройство torch: auto, cuda, cuda:N, mps, cpu (REELS_DEVICE)")
    g.add_argument("--profile", choices=HW_PROFILES, help="профиль ускорения (REELS_PROFILE)")
    g.add_argument("--threads", type=int, help="потоки CPU; 0 — по числу физических ядер (REELS_THREADS)")
    g.add_argument("--allow-slow", action="store_true",
                   help="F5/Qwen3/MusicGen на CPU без замены на Piper / без пропуска музыки (REELS_ALLOW_SLOW=1)")


def _hw_env(a) -> None:
    import os
    for attr, var in (("device", "REELS_DEVICE"), ("profile", "REELS_PROFILE"), ("threads", "REELS_THREADS")):
        v = getattr(a, attr, None)
        if v is not None:
            os.environ[var] = str(v)
    if getattr(a, "allow_slow", False):
        os.environ["REELS_ALLOW_SLOW"] = "1"


# --- разбор аргументов --------------------------------------------------------------------------------------------
def _run_flags(sp) -> None:
    sp.add_argument("--yes", "-y", action="store_true", help="автопилот: подтверждать контрольные точки")
    sp.add_argument("--edit", "-e", action="append", help="только этот монтаж (можно несколько раз)")
    sp.add_argument("--force", action="store_true", help="пересчитать все этапы, игнорируя кэш")
    sp.add_argument("--qa", choices=["error", "warn"], default="error",
                    help="ошибки автопроверки вёрстки: остановить (error, по умолчанию) или только предупредить")
    _hw_flags(sp)


def parser() -> argparse.ArgumentParser:
    from .pipeline import GATES, STAGES
    ap = argparse.ArgumentParser(prog="reels", description="Конвейер вертикальных роликов на локальных моделях.")
    ap.add_argument("--version", action="version", version=f"local-ai-reels {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="команда")

    sp = sub.add_parser("new", help="создать проект ролика")
    sp.add_argument("dir")
    sp.add_argument("--from", dest="source", help="проект-образец, например examples/povodok-templates")
    sp.add_argument("--text", help="свой текст: файл (или - для stdin), абзац на блок; сцены строятся сами")
    sp.add_argument("--theme", help="тема для --text: blueprint, night, paper, mono")
    sp.set_defaults(fn=cmd_new)

    sp = sub.add_parser("check", help="проверить сценарий: лимит слов, блоки, сцены, якоря")
    sp.add_argument("projects", nargs="+")
    sp.set_defaults(fn=cmd_check)

    sp = sub.add_parser("run", help=f"прогнать конвейер: {' → '.join(STAGES)}")
    sp.add_argument("project")
    _run_flags(sp)
    sp.add_argument("--from", dest="from_stage", choices=STAGES, help="пересчитать начиная с этапа")
    sp.add_argument("--only", choices=STAGES, help="выполнить только один этап")
    sp.add_argument("--until", choices=STAGES, help="остановиться после этапа")
    sp.add_argument("--no-input", action="store_true", help="не спрашивать в терминале, останавливаться на точках")
    sp.set_defaults(fn=cmd_run)

    sp = sub.add_parser("batch", help="прогнать несколько проектов подряд, сводка в конце")
    sp.add_argument("projects", nargs="+")
    _run_flags(sp)
    sp.set_defaults(fn=cmd_batch)

    sp = sub.add_parser("status", help="состояние этапов и контрольных точек")
    sp.add_argument("projects", nargs="+")
    sp.add_argument("--json", action="store_true", help="машиночитаемый вывод для приложений")
    sp.set_defaults(fn=cmd_status)

    sp = sub.add_parser("approve", help="подтвердить контрольную точку")
    sp.add_argument("project")
    sp.add_argument("gate", choices=sorted(GATES.values()))
    sp.set_defaults(fn=cmd_approve)

    sp = sub.add_parser("clean", help="удалить build/ и состояние проекта")
    sp.add_argument("projects", nargs="+")
    sp.set_defaults(fn=cmd_clean)

    sp = sub.add_parser("scenes", help="каталог шаблонов сцен")
    sp.add_argument("name", nargs="?")
    sp.set_defaults(fn=cmd_scenes)

    sp = sub.add_parser("draft", help="черновик reel.yaml по теме через локальную LLM (LM Studio, Ollama)")
    sp.add_argument("topic")
    sp.add_argument("-o", "--out", required=True, help="папка нового проекта")
    sp.add_argument("--facts", help="файл с фактами и источником: модель берёт цифры только оттуда")
    sp.add_argument("--url", help="OpenAI-совместимый API (по умолчанию REELS_LLM_URL или http://localhost:1234/v1)")
    sp.add_argument("--model", help="модель (по умолчанию REELS_LLM_MODEL или первая из /models)")
    sp.add_argument("--limit", type=int, default=60)
    sp.add_argument("--theme", default="blueprint")
    sp.add_argument("--format", default="auto",
                    help="формат ролика: auto | classic | question | myth | list | twist | before-after | promo | news")
    sp.add_argument("--hooks", type=int, default=3, help="сколько вариантов хука (по монтажу на каждый)")
    sp.add_argument("--brand", help="YAML с брендом: логотип, название, партнёры")
    sp.set_defaults(fn=cmd_draft)
    sp = sub.add_parser("factory", help="конвейер по списку тем: черновик → ролики, сводка factory.md")
    sp.add_argument("topics", help="файл тем: по теме на строку (факты после « | » или с отступом) или YAML")
    sp.add_argument("-o", "--out", required=True, help="папка проектов; там же factory.json и factory.md")
    sp.add_argument("--yes", "-y", action="store_true", help="автопилот: подтверждать контрольные точки")
    sp.add_argument("--edit", "-e", action="append", help="только этот монтаж (можно несколько раз)")
    sp.add_argument("--qa", choices=["error", "warn"], default="error",
                    help="ошибки автопроверки вёрстки: остановить (error) или только предупредить")
    sp.add_argument("--theme", help="тема для новых черновиков (по умолчанию blueprint; в YAML — своя у темы)")
    sp.add_argument("--url", help="OpenAI-совместимый API (по умолчанию REELS_LLM_URL или http://localhost:1234/v1)")
    sp.add_argument("--model", help="модель (по умолчанию REELS_LLM_MODEL или первая из /models)")
    sp.add_argument("--jobs", type=int, default=1, help="параллельность; пока поддерживается только 1")
    _hw_flags(sp)
    sp.set_defaults(fn=cmd_factory)

    sp = sub.add_parser("doctor", help="проверить компьютер: видеокарта, профиль ускорения, чего не хватает")
    sp.add_argument("project", nargs="?", help="проект: учесть его движки и раздел hardware")
    sp.add_argument("--json", action="store_true", help="машиночитаемый вывод для приложений")
    sp.set_defaults(fn=cmd_doctor)
    return ap


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    a = parser().parse_args(argv)
    _hw_env(a)
    try:
        return a.fn(a)
    except ConfigError as e:
        say(f"✗ {e}", err=True)
        return 2
    except (ToolError, OSError) as e:  # внешняя программа или файл: понятное сообщение вместо traceback
        say(f"✗ {e}", err=True)
        return 1
    except KeyboardInterrupt:
        say("прервано", err=True)
        return 130
