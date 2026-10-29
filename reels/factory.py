"""Фабрика: список тем → черновики → ролики. Буквально конвейер: python -m reels factory темы.txt --out reels/

Файл тем — UTF-8 текст или YAML.

Текст: по теме на строку; строки с # — комментарии. Факты — после « | » в той же строке или блоком с отступом
под темой (их можно сочетать). Факты вида «@файл» читаются из файла рядом с файлом тем.

    # тема | факты
    Почему в городе нет туалетов | RSPH, 2019: каждый пятый выходит из дома реже, чем хочет
    Как работает светофор
        Первый электрический светофор — Кливленд, 1914.
        Источник: Smithsonian Magazine

YAML: список {topic, facts, slug, theme} (или строк-тем); можно {topics: [...]}.

Для каждой темы: папка <out>/<slug> (slug — транслит темы); нет reel.yaml → черновик через LLM (draft); затем
pipeline.run без вопросов в терминале. Остановки на контрольных точках и ошибки не роняют фабрику: тема получает
статус, фабрика идёт дальше. Повторный запуск продолжает с того же места: готовые этапы берутся из кэша
конвейера, черновики не пишутся заново, подтверждённые (python -m reels approve …) точки пропускаются.
Сводка — <out>/factory.json и <out>/factory.md (обновляется после каждой темы).
"""
from __future__ import annotations

import re
import textwrap
import time
import unicodedata
from datetime import datetime
from pathlib import Path

import yaml

from .project import CONFIG_NAME, ConfigError, load
from .util import ToolError, say, write_json

EXIT_GATE = 3                # как в cli: кто-то ждёт подтверждения
SLUG_MAX = 40
DEFAULT_THEME = "blueprint"
YAML_KEYS = {"topic", "facts", "slug", "theme"}
REPORT_JSON = "factory.json"
REPORT_MD = "factory.md"
MARKS = {"done": "✓", "waiting": "…", "failed": "✗", "pending": "·"}

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
    "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
    # украинские и белорусские буквы
    "і": "i", "ї": "yi", "є": "ye", "ґ": "g", "ў": "u",
}


# --- slug ------------------------------------------------------------------------------------------------------
def slugify(text: str, taken: set[str] | None = None) -> str:
    """Имя папки по теме: транслит кириллицы, латиница в нижнем регистре, цифры и дефисы, не длиннее 40 знаков.

    taken — уже занятые имена: тогда к повтору добавляется -2, -3…, а результат записывается в taken.
    """
    s = "".join(_TRANSLIT.get(c, c) for c in str(text).lower())
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    if len(s) > SLUG_MAX:
        cut = s[:SLUG_MAX]
        if s[SLUG_MAX] != "-" and cut.rfind("-") >= SLUG_MAX // 4:   # не резать слово посередине
            cut = cut[:cut.rfind("-")]
        s = cut.strip("-")
    s = s or "reel"
    if taken is None:
        return s
    out, n = s, 2
    while out in taken:
        tail = f"-{n}"
        out = s[:SLUG_MAX - len(tail)].rstrip("-") + tail
        n += 1
    taken.add(out)
    return out


# --- файл тем --------------------------------------------------------------------------------------------------
def _facts(value, base: Path, where: str) -> str:
    """Факты: строка, список строк (→ пункты) или «@файл» (путь от папки файла тем)."""
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(f"- {str(x).strip()}" for x in value if str(x).strip())
    text = str(value).strip()
    if text.startswith("@"):
        f = Path(text[1:].strip()).expanduser()
        f = f if f.is_absolute() else base / f
        if not f.is_file():
            raise ConfigError(f"{where}: нет файла фактов {f}")
        return f.read_text(encoding="utf-8").strip()
    return text


def _parse_text(text: str, path: Path) -> list[dict]:
    items: list[dict] = []
    for n, line in enumerate(text.splitlines(), 1):
        line = line.rstrip()
        if not line.strip():
            if items and items[-1]["_block"]:
                items[-1]["_block"].append("")     # абзацы внутри блока фактов
            continue
        if line[0] in " \t":
            if not items:
                raise ConfigError(f"{path}:{n}: строка с отступом до первой темы — факты пишут под темой")
            items[-1]["_block"].append(line)
            continue
        if line.startswith("#"):
            continue
        topic, *rest = re.split(r"\s+\|\s+|\s+\|$", line, maxsplit=1)
        topic = re.sub(r"^(?:[-*•]|\d+[.)])\s+", "", topic).strip()   # маркеры списка: «- », «1. »
        if not topic:
            raise ConfigError(f"{path}:{n}: пустая тема")
        items.append({"topic": topic, "_inline": _facts(rest[0] if rest else "", path.parent, f"{path}:{n}"),
                      "_block": [], "_line": n})
    out = []
    for it in items:
        block = textwrap.dedent("\n".join(it["_block"])).strip()
        if block:
            block = _facts(block, path.parent, f"{path}:{it['_line']}") if block.startswith("@") else block
        facts = "\n".join(x for x in (it["_inline"], block) if x)
        out.append({"topic": it["topic"], "facts": facts, "slug": None, "theme": None})
    return out


def _parse_yaml(data, path: Path) -> list[dict]:
    if isinstance(data, dict) and "topics" in data:
        data = data["topics"]
    if not isinstance(data, list):
        raise ConfigError(f"{path}: ожидался список тем: - topic: …")
    out = []
    for i, it in enumerate(data, 1):
        where = f"{path}: тема {i}"
        if isinstance(it, str):
            it = {"topic": it}
        if not isinstance(it, dict):
            raise ConfigError(f"{where}: ожидалась строка или словарь {{topic, facts, slug, theme}}")
        unknown = set(it) - YAML_KEYS
        if unknown:
            raise ConfigError(f"{where}: неизвестные поля {', '.join(sorted(map(str, unknown)))}; "
                              f"есть: {', '.join(sorted(YAML_KEYS))}")
        topic = " ".join(str(it.get("topic") or "").split())
        if not topic:
            raise ConfigError(f"{where}: нужен непустой topic")
        theme = it.get("theme")
        if theme is not None and not isinstance(theme, (str, dict)):
            raise ConfigError(f"{where}: theme — имя темы или словарь {{base, accent}}")
        slug = it.get("slug")
        out.append({"topic": topic, "facts": _facts(it.get("facts"), path.parent, where),
                    "slug": str(slug).strip() if slug not in (None, "") else None, "theme": theme or None})
    return out


def _looks_yaml(text: str):
    """Текст, который на деле YAML-список тем (- topic: …). Иначе None."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    if isinstance(data, dict) and isinstance(data.get("topics"), list):
        return data
    if isinstance(data, list) and data and all(isinstance(x, dict) and "topic" in x for x in data):
        return data
    return None


def parse_topics(path: str | Path) -> list[dict]:
    """Темы из файла: [{topic, facts, slug, theme}] — slug уже назначен и уникален, theme None = по умолчанию."""
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"нет файла тем {path}")
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() in (".yaml", ".yml"):
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise ConfigError(f"{path}: ошибка YAML: {e}") from e
        items = _parse_yaml(data or [], path)
    else:
        data = _looks_yaml(text)
        items = _parse_yaml(data, path) if data is not None else _parse_text(text, path)
    if not items:
        raise ConfigError(f"{path}: нет ни одной темы")
    # сначала заданные вручную slug, потом транслит остальных — ручные имена не сдвигаются
    taken: set[str] = set()
    for it in items:
        if it["slug"]:
            s = slugify(it["slug"])
            if s in taken:
                raise ConfigError(f"{path}: slug «{s}» встречается дважды")
            taken.add(s)
            it["slug"] = s
    for it in items:
        if not it["slug"]:
            it["slug"] = slugify(it["topic"], taken)
    return items


# --- сводка ----------------------------------------------------------------------------------------------------
def _first_line(e: BaseException) -> str:
    """Первая содержательная строка ошибки; «заголовок:» склеивается со следующей строкой."""
    lines = [x.strip() for x in str(e).splitlines() if x.strip()] or [type(e).__name__]
    line = lines[0]
    if line.endswith(":") and len(lines) > 1:
        line = f"{line} {lines[1]}"
    return line[:200]


def _rel(path: Path, base: Path) -> str:
    """Путь от папки фабрики (так работают ссылки в factory.md); снаружи неё — как есть."""
    try:
        return Path(path).resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return str(path)


def _dur(sec: float) -> str:
    sec = int(round(sec))
    return f"{sec} с" if sec < 60 else f"{sec // 60} мин {sec % 60:02d} с"


def _status_text(row: dict) -> str:
    st = row["status"]
    if st == "waiting":
        return f"… ждёт «{row['gate']}»"
    return {"done": "✓ готово", "failed": "✗ ошибка", "pending": "· в очереди"}[st]


def _cell(s: str) -> str:
    return " ".join(str(s).split()).replace("|", "\\|")


def render_md(rows: list[dict], topics: Path) -> str:
    """factory.md: таблица тем, затем что подтвердить и что сломалось."""
    counts = {k: sum(r["status"] == k for r in rows) for k in MARKS}
    head = " · ".join(f"{MARKS[k]} {v}" for k, v in counts.items() if v)
    lines = ["# Фабрика роликов", "",
             f"Темы: `{topics}` · обновлено {datetime.now().isoformat(timespec='seconds')} · {head}", "",
             "| # | Тема | Папка | Статус | Ролики | Время |", "|---|---|---|---|---|---|"]
    for r in rows:
        videos = "<br>".join(f"[{Path(o).name}]({o})" for o in r["outputs"]) or "—"
        lines.append(f"| {r['n']} | {_cell(r['topic'])} | `{r['slug']}` | {_status_text(r)} | {videos} | "
                     f"{_dur(r['seconds']) if r['status'] != 'pending' else '—'} |")
    waiting = [r for r in rows if r["status"] == "waiting"]
    if waiting:
        lines += ["", "## Ждут подтверждения", "",
                  "Посмотрите, подтвердите и запустите фабрику снова тем же вызовом — она продолжит с этого места."]
        for r in waiting:
            lines += ["", f"### {r['slug']} — «{r['gate']}»", "", f"`python -m reels approve {r['dir']} {r['gate']}`"]
            if r.get("summary"):
                lines += ["", "```text", r["summary"].rstrip(), "```"]
    failed = [r for r in rows if r["status"] == "failed"]
    if failed:
        lines += ["", "## Ошибки", ""] + [f"- **{r['slug']}** ({_cell(r['topic'])}): {_cell(r['note'])}"
                                          for r in failed]
    return "\n".join(lines) + "\n"


def _write(rows: list[dict], topics: Path, out: Path) -> Path:
    write_json(out / REPORT_JSON, {"topics": str(topics), "updated": datetime.now().isoformat(timespec="seconds"),
                                   "items": rows})
    md = out / REPORT_MD
    md.write_text(render_md(rows, topics), encoding="utf-8")
    return md


def _print_summary(rows: list[dict], md: Path) -> None:
    counts = ", ".join(f"{MARKS[k]} {sum(r['status'] == k for r in rows)}" for k in ("done", "waiting", "failed"))
    say(f"\n■ фабрика: тем {len(rows)} — {counts}")
    for r in rows:
        if r["status"] == "done":
            note = ", ".join(r["outputs"]) or r["note"]
        elif r["status"] == "waiting":
            note = f"ждёт «{r['gate']}»: python -m reels approve {r['dir']} {r['gate']}"
        else:
            note = r["note"]
        say(f"  {MARKS[r['status']]} {r['slug']:<{SLUG_MAX}} {_dur(r['seconds']):>10}  {note}")
    say(f"  сводка: {md}")


# --- запуск ----------------------------------------------------------------------------------------------------
def run_factory(topics_path: Path, out: Path, *, yes: bool = False, edits: list[str] | None = None,
                theme: str | None = None, url: str | None = None, model: str | None = None,
                qa: str = "error") -> list[dict]:
    """Прогоняет все темы из файла. Возвращает строки сводки:
    {n, topic, slug, dir, status: done|waiting|failed, gate, summary, note, outputs, seconds, drafted}.
    """
    from . import draft as drafting, pipeline   # через модули — чтобы тесты могли подменить draft и run

    topics_path, out = Path(topics_path), Path(out)
    items = parse_topics(topics_path)
    out.mkdir(parents=True, exist_ok=True)
    rows = [{"n": i, "topic": it["topic"], "slug": it["slug"], "dir": str(out / it["slug"]), "status": "pending",
             "gate": None, "summary": "", "note": "", "outputs": [], "seconds": 0.0, "drafted": False}
            for i, it in enumerate(items, 1)]
    llm_down = None              # нет связи с LLM — остальные черновики не пытаемся писать
    _write(rows, topics_path, out)
    for it, row in zip(items, rows):
        d = out / it["slug"]
        say(f"\n■■ [{row['n']}/{len(rows)}] {it['topic']}  →  {d}")
        t0 = time.time()
        try:
            if not (d / CONFIG_NAME).exists():
                if llm_down:
                    raise ToolError(llm_down)
                try:
                    drafting.draft(it["topic"], d, facts=it["facts"], url=url, model=model,
                                   theme=it["theme"] or theme or DEFAULT_THEME)
                except ToolError as e:
                    if str(e).startswith("нет связи с LLM"):
                        llm_down = _first_line(e)
                    raise
                row["drafted"] = True
            project = load(d)
            chosen = edits or list(project.edits)
            pipeline.run(project, yes=yes, edits=edits, qa=qa, interactive=False)
            row["status"] = "done"
            row["outputs"] = [_rel(project.out_mp4(e), out) for e in chosen if project.out_mp4(e).exists()]
        except pipeline.GateWait as g:
            row.update(status="waiting", gate=g.gate, summary=g.summary, note=f"ждёт «{g.gate}»")
            say(f"  … ждёт подтверждения «{g.gate}»: python -m reels approve {d} {g.gate}")
        except (drafting.DraftError, pipeline.StageError, ToolError, ConfigError) as e:
            row.update(status="failed", note=_first_line(e))
            say(f"  ✗ {row['note']}", err=True)
        except Exception as e:  # noqa: BLE001 — одна тема не должна останавливать фабрику
            row.update(status="failed", note=f"{type(e).__name__}: {_first_line(e)}")
            say(f"  ✗ {row['note']}", err=True)
        row["seconds"] = round(time.time() - t0, 1)
        _write(rows, topics_path, out)
    _print_summary(rows, out / REPORT_MD)
    return rows


def cmd_factory(a) -> int:
    """Команда CLI: python -m reels factory <темы> --out <папка> [--yes] [--edit E] [--theme T] [--url] [--model]."""
    if (getattr(a, "jobs", 1) or 1) != 1:
        say("--jobs: пока поддерживается только 1 (рендер и модели занимают всю видеокарту) — темы идут по очереди",
            err=True)
    rows = run_factory(Path(a.topics), Path(a.out), yes=a.yes, edits=a.edit, theme=a.theme, url=a.url,
                       model=a.model, qa=getattr(a, "qa", "error"))
    if any(r["status"] == "failed" for r in rows):
        return 1
    return EXIT_GATE if any(r["status"] == "waiting" for r in rows) else 0


__all__ = ["slugify", "parse_topics", "run_factory", "render_md", "cmd_factory"]
