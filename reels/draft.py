"""Черновик reel.yaml по теме через локальную LLM (OpenAI-совместимый API: LM Studio, Ollama, llama.cpp server).

Два режима:
- формат Instagram (format="auto" или имя из reels.formats): модель выбирает драматургию, пишет несколько
  крючков (A/B), тело ролика, призыв, план кадров (scene.shots) с наклейками; монтажи — по монтажу на крючок;
- классика (format=None): ровно blocks блоков «крючок → факт → поворот → панчлайн», по сцене на блок.

Модель только заполняет готовые шаблоны сцен из библиотеки (REGISTRY): рисовать свои сцены небольшие модели
не умеют (см. local-model-test/). Ответ проверяется; при ошибках модель получает их список и переписывает ответ.

Переменные окружения: REELS_LLM_URL (по умолчанию http://localhost:1234/v1 — LM Studio; Ollama —
http://localhost:11434/v1, llama.cpp — http://localhost:8080/v1), REELS_LLM_MODEL (по умолчанию первая модель
из GET /models), REELS_LLM_KEY (необязательный ключ, уходит как Authorization: Bearer).
"""
from __future__ import annotations

import json
import os
import re
import string
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

from . import formats as F
from . import textcheck as TC
from .project import CONFIG_NAME, DEFAULTS, ConfigError, from_dict, word_count
from .scenes import REGISTRY, SceneCtx, SceneError, Theme, render_scene
from .timing import _words_norm, script_tokens
from .util import ToolError, say

DEFAULT_URL = "http://localhost:1234/v1"
PROMPT_FILE = Path(__file__).with_name("prompts") / "draft.md"
TIMEOUT = 600            # с на один ответ модели
CONNECT_TRIES = 3        # попыток при обрыве связи
RETRY_WAIT = 10          # с между попытками
TEMPERATURE = 0.4
EXCLUDED = ("custom",)   # шаблоны, которые LLM использовать нельзя
# Блок со ссылкой на предыдущий не переживёт перестановку блоков в монтаже.
BAD_STARTS = {"он", "она", "оно", "они", "это", "этот", "эта", "эти", "потом", "тогда", "поэтому", "также"}
LLM_FIELDS = ("title", "blocks", "source", "endcard", "edits", "music_prompts")
IG_FIELDS = ("format", "title", "blocks", "source", "endcard", "caption", "hashtags", "music_prompts")
YAML_KW = dict(allow_unicode=True, sort_keys=False, width=120)
MAX_BLOCKS = 9           # номер блока в монтаже — одна цифра
MAX_SHOTS = 4
SHOT_SLACK = 1.0         # черновик: план до SHOT_MAX + SHOT_SLACK с (оценка по словам грубая)


class DraftError(RuntimeError):
    """Модель не дала годный черновик. problems — список ошибок последнего ответа, raw — путь к ответу."""

    def __init__(self, problems: list[str], raw: Path | None = None):
        self.problems, self.raw = problems, raw
        tail = f"\nОтвет модели: {raw}" if raw else ""
        super().__init__("черновик не прошёл проверку:\n" + "\n".join(f"- {p}" for p in problems) + tail)


# --- каталог сцен ------------------------------------------------------------------------------------------
def catalog_text(include_custom: bool = False) -> str:
    """Каталог шаблонов сцен в Markdown: имя, назначение, параметры, пример в YAML."""
    parts = []
    for name in sorted(REGISTRY):
        if name in EXCLUDED and not include_custom:
            continue
        doc = REGISTRY[name][1]
        lines = [f"### {name}", "", doc.summary.strip(), ""]
        if doc.params:
            lines += ["Параметры:"] + [f"- `{k}` — {v}" for k, v in doc.params.items()] + [""]
        if doc.example:
            ex = {"scene": {"type": name, **{k: v for k, v in doc.example.items() if k != "type"}}}
            lines += ["Пример:", "```yaml", yaml.safe_dump(ex, **YAML_KW).rstrip(), "```", ""]
        parts.append("\n".join(lines))
    return "\n".join(parts).rstrip() + "\n" if parts else "(шаблонов сцен нет)\n"


def project_features() -> dict[str, bool]:
    """Что умеет reel.yaml в этой версии (другие части конвейера растут параллельно): планы scene.shots,
    монтажи из части блоков (A/B-крючки), фирменный стиль brand."""
    def ok(cfg: dict) -> bool:
        try:
            from_dict(cfg, Path("."))
            return True
        except (ConfigError, KeyError, TypeError, ValueError):
            return False
    two = [{"text": "раз два"}, {"text": "три четыре"}]
    return {"shots": ok({"blocks": [{"text": "раз", "scene": {"shots": [{"type": "headline", "lines": ["x"]}]}}],
                         "edits": {"linear": "1"}}),
            "subset_edits": ok({"blocks": two, "edits": {"linear": "12", "short": "1"}}),
            "brand": "brand" in DEFAULTS,
            # project._merge сливает edits с DEFAULTS["edits"] — монтаж linear «1234» тогда добавляется сам
            "merges_edits": _merges_edits()}


def _merges_edits() -> bool:
    """project._merge сливает edits с DEFAULTS["edits"]: тогда к монтажам черновика добавится linear «1234»."""
    default = DEFAULTS.get("edits") or {}
    if not isinstance(default, dict) or not default:
        return False
    n = max((len(str(v)) for v in default.values()), default=1)
    try:
        cfg = from_dict({"blocks": [{"text": f"блок {i}"} for i in range(n)], "edits": {"x-probe": "1"}}, Path("."))
    except (ConfigError, KeyError, TypeError, ValueError):
        return True
    return bool(set(cfg.cfg["edits"]) - {"x-probe"})


# --- запрос ------------------------------------------------------------------------------------------------
def _roles(n: int) -> list[str]:
    if n == 1:
        return ["Крючок"]
    if n == 2:
        return ["Крючок", "Панчлайн"]
    return ["Крючок"] + ["Факт"] * (n - 3) + ["Поворот", "Панчлайн"]


def _default_edits(n: int) -> dict[str, str]:
    order = "".join(str(i) for i in range(1, n + 1))
    edits = {"linear": order}
    if n >= 2:
        edits["from-end"] = order[-1] + order[:-1]
    if n >= 4:
        edits["suspense"] = order[0] + order[2] + order[1] + order[3:]
    return edits


def _facts_text(facts: str) -> str:
    if facts.strip():
        return ("Используй **только** эти факты и цифры, ничего не добавляй от себя. Источник для `source` "
                "возьми отсюда же:\n\n" + facts.strip())
    return ("Фактов не дали. Бери только то, в чём уверен, и только из реального источника. Не выдумывай "
            "статистику, проценты и даты: если точной цифры не знаешь — обойдись без неё.")


def _fill(values: dict[str, str]) -> str:
    text = PROMPT_FILE.read_text(encoding="utf-8")
    for k, v in values.items():
        text = text.replace("{{" + k + "}}", v)
    return re.sub(r"\{\{\w+\}\}", "", text)


def build_prompt(topic: str, *, facts: str = "", blocks: int = 4, limit: int = 60, format: str | None = None,
                 hooks: int = 3) -> str:
    """Текст запроса к модели: шаблон reels/prompts/draft.md + библиотека форматов + каталог сцен + схема ответа.

    format=None — классика (ровно blocks блоков); "auto" — модель выбирает формат; имя — этот формат."""
    if format in (None, "classic"):
        return _classic_prompt(topic, facts=facts, blocks=blocks, limit=limit)
    fmt = None if format == "auto" else F.get(format)
    names = list(F.FORMATS) if fmt is None else [fmt.name]
    choose = ("Выбери **один** формат из библиотеки ниже — тот, что сильнее всего раскрывает тему, и укажи его "
              "имя в поле `format`." if fmt is None else
              f"Формат задан: **{fmt.name} — {fmt.title}**. Поле `format: {fmt.name}`.")
    hook_line = (f"Напиши **{hooks} разных крючка(-ов)** разными приёмами: первый блок `role: hook`, следующие "
                 f"{hooks - 1} — `role: alt-hook`. Из каждого крючка соберётся свой монтаж (A/B-тест): крючок + "
                 "общее тело ролика. Поэтому каждый крючок ведёт в одно и то же тело, а тело не ссылается на "
                 "конкретный крючок." if hooks > 1 else "Напиши один крючок: первый блок `role: hook`.")
    values = {
        "topic": topic.strip(),
        "task": f"{choose}\n\n{hook_line}\n\nПосле крючков — тело ролика по ролям формата, последним — `role: cta`. "
                f"Всего блоков — не больше {MAX_BLOCKS}. В одном монтаже (крючок + тело) не больше **{limit} слов**.",
        "formats": F.describe(names),
        "hooks": F.describe_hooks(),
        "facts": _facts_text(facts),
        "shots": _shots_rules(),
        "catalog": catalog_text(),
        "format": _ig_schema(fmt or F.FORMATS["question"], hooks).rstrip(),
        "answer": _ig_answer_notes(hooks),
        "limit": str(limit),
    }
    return _fill(values)


def _classic_prompt(topic: str, *, facts: str, blocks: int, limit: int) -> str:
    fmt = {"title": "Короткое название",
           "blocks": [{"name": r, "text": f"Текст диктора блока {i}.", "scene": {"type": "<шаблон из каталога>"}}
                      for i, r in enumerate(_roles(blocks), 1)],
           "source": "Организация, исследование, год",
           "endcard": "Вывод с *главным* словом",
           "edits": _default_edits(blocks),
           "music_prompts": ["minimal thoughtful piano, soft synth pulse, calm, 80 bpm, no drums",
                             "ambient cinematic underscore, warm pad, slow, reflective"]}
    roles = " → ".join(r.lower() for r in _roles(blocks))
    values = {
        "topic": topic.strip(),
        "task": f"Классический ролик: ровно {blocks} блока(-ов) по порядку: {roles}. Всего не больше **{limit} слов** "
                f"на все блоки вместе. Блоки монтируются в разном порядке (например, последний идёт первым), поэтому "
                f"каждый блок понятен сам по себе.",
        "formats": "Формат — классика: крючок → факт → поворот → панчлайн. Крючок цепляет в первые 2 секунды, "
                   "панчлайн — короткий сильный вывод.\n",
        "hooks": F.describe_hooks(),
        "facts": _facts_text(facts),
        "shots": "Для каждого блока — **одна** сцена из каталога: `scene: {type: <шаблон>, …параметры}`.",
        "catalog": catalog_text(),
        "format": yaml.safe_dump(fmt, **YAML_KW).rstrip().replace(
            "type: <шаблон из каталога>", "type: <шаблон из каталога>  # и его параметры"),
        "answer": (f"- `title` — короткое название ролика.\n"
                   f"- `blocks` — ровно {blocks} блока(-ов): `name` (роль блока), `text` (текст диктора), `scene`.\n"
                   "- `source` — реальный источник факта: организация, исследование или документ и год.\n"
                   "- `endcard` — финальная карточка, 2–6 слов; главное слово — `*звёздочками*`.\n"
                   "- `edits` — 2–3 монтажа: имя латиницей → порядок номеров блоков, каждый номер ровно один раз.\n"
                   "- `music_prompts` — 2 описания фоновой музыки по-английски: инструменты, настроение, темп."),
        "limit": str(limit),
    }
    return _fill(values)


def _shots_rules() -> str:
    text_scenes = [t for t in ("stickers", "cover", "tag") if t in REGISTRY]
    sticker = (f"Текст в кадре несут шаблоны {', '.join(text_scenes)}: строки по 2–4 слова, не больше "
               f"{F.STICKER_MAX_CHARS} знаков. Текст в кадре ≠ текст диктора: это 2–4 главных слова, а не пересказ."
               if text_scenes else
               "Текст в кадре — заголовки шаблонов: строки по 2–4 слова, не больше "
               f"{F.STICKER_MAX_CHARS} знаков. Текст в кадре ≠ текст диктора.")
    return (f"Каждый блок — это **2–3 плана** (новый план каждые {F.SHOT_SECONDS[0]:g}–{F.SHOT_SECONDS[1]:g} с, "
            f"ни один план не дольше {F.SHOT_MAX:g} с; блок короче 6 слов может быть одним планом):\n\n"
            "```yaml\nscene:\n  shots:\n    - type: <шаблон>      # первый план — с начала блока\n"
            "      …параметры шаблона\n    - type: <шаблон>\n      at: \"слово\"        # момент смены плана: слово "
            "из текста этого блока\n      …параметры\n```\n\n"
            f"{sticker}\n\nНичего важного у краёв: верх {F.SAFE_ZONES['top']} px, низ {F.SAFE_ZONES['bottom']} px и "
            f"правые {F.SAFE_ZONES['right']} px кадра закрывает интерфейс Instagram — параметры top/y там не ставь.")


def _ig_schema(fmt: F.Format, hooks: int) -> str:
    blocks = []
    patterns = list(fmt.hooks) + [h for h in F.HOOKS if h not in fmt.hooks]
    for i in range(hooks):
        blocks.append({"role": "hook" if i == 0 else "alt-hook", "pattern": patterns[i % len(patterns)],
                       "name": f"Крючок {string.ascii_uppercase[i]}", "text": "Текст крючка, до 10 слов.",
                       "scene": {"shots": [{"type": "<шаблон>"}, {"type": "<шаблон>", "at": "<слово из text>"}]}})
    for beat in fmt.body:
        for _ in range(max(1, beat.count[0])):
            blocks.append({"role": beat.role, "name": F.ROLE_TITLES[beat.role],
                           "text": f"Текст диктора, {F._range(beat.words)} слов.",
                           "scene": {"shots": [{"type": "<шаблон>"}, {"type": "<шаблон>", "at": "<слово из text>"}]}})
    blocks[0]["sfx"] = [{"at": "<слово из text>", "sound": "whoosh"}]
    data = {"format": fmt.name, "title": "Короткое название", "blocks": blocks,
            "source": "Организация, исследование, год", "endcard": "Вывод с *главным* словом",
            "caption": "Подпись к посту: первая строка — крючок, дальше 1–2 фразы.",
            "hashtags": ["тема", "город", "наука"],
            "music_prompts": ["minimal thoughtful piano, soft synth pulse, calm, 80 bpm, no drums",
                              "ambient cinematic underscore, warm pad, slow, reflective"]}
    return yaml.safe_dump(data, **YAML_KW).replace("type: <шаблон>", "type: <шаблон из каталога>  # и его параметры")


def _ig_answer_notes(hooks: int) -> str:
    try:
        from .sfx import SOUNDS
        sounds = ", ".join(SOUNDS)
    except ImportError:  # pragma: no cover — numpy есть всегда, но черновик не должен от него зависеть
        sounds = "whoosh, pop, click, knock, bang, stamp, swoosh"
    return "\n".join([
        "- `format` — имя формата из библиотеки.",
        f"- `blocks` — сначала {hooks} крючка(-ов) (`hook`, затем `alt-hook`), потом тело по ролям формата, "
        "последний — `cta`. У каждого блока: `role`, `name` (по-русски), `text` (текст диктора), `scene` (планы).",
        "- `pattern` у крючка — приём из списка крючков (question, claim, number, myth, pov, stop, contrast, list, "
        "secret); у разных крючков — разные приёмы.",
        f"- `sfx` (необязательно) — звук на слове: `[{{at: \"слово\", sound: whoosh}}]`; звуки: {sounds}.",
        "- `source` — реальный источник факта: организация, исследование или документ и год. Он виден в кадре.",
        "- `endcard` — финальная карточка, 2–6 слов, главное слово — `*звёздочками*`.",
        "- `caption` — подпись к посту: первая строка повторяет крючок, дальше 1–2 фразы; `hashtags` — 3–5 тегов "
        "без #.",
        "- `edits` не пиши: монтажи по крючкам соберутся сами.",
        "- `music_prompts` — 2 описания фоновой музыки по-английски: инструменты, настроение, темп, без вокала.",
    ])


def _http(url: str, payload: dict | None = None, key: str | None = None, timeout: float = TIMEOUT) -> dict:
    """GET (payload=None) или POST JSON; повтор при обрыве связи, CONNECT_TRIES раз через RETRY_WAIT с."""
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    data = json.dumps(payload).encode() if payload is not None else None
    for i in range(1, CONNECT_TRIES + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:500]
            raise ToolError(f"LLM {url}: HTTP {e.code}: {body}") from e
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            if i == CONNECT_TRIES:
                raise ToolError(f"нет связи с LLM {url}: {e}. Запустите сервер (LM Studio, Ollama, llama.cpp) "
                                f"или укажите адрес в REELS_LLM_URL") from e
            say(f"  связь с LLM оборвалась ({e}), повтор через {RETRY_WAIT} с…", err=True)
            time.sleep(RETRY_WAIT)
    raise AssertionError("недостижимо")


def first_model(url: str, key: str | None = None) -> str:
    """Первая модель из GET {url}/models."""
    data = _http(url.rstrip("/") + "/models", key=key, timeout=30).get("data") or []
    if not data:
        raise ToolError(f"LLM {url}: нет загруженных моделей — загрузите модель или укажите REELS_LLM_MODEL")
    return str(data[0]["id"])


def chat(url: str, model: str, messages: list[dict], key: str | None = None) -> str:
    """Один ответ модели (choices[0].message.content)."""
    out = _http(url.rstrip("/") + "/chat/completions",
                {"model": model, "messages": messages, "temperature": TEMPERATURE, "stream": False}, key=key)
    try:
        return out["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError, TypeError) as e:
        raise ToolError(f"LLM {url}: неожиданный ответ: {str(out)[:300]}") from e


# --- разбор и проверка ответа ----------------------------------------------------------------------------
def extract_yaml(reply: str) -> dict:
    """YAML из ответа модели: без <think>…</think>, из блока ```yaml (или весь текст). ValueError — не разобрать."""
    text = re.sub(r"<think>.*?(</think>|$)", "", reply, flags=re.S | re.I).strip()
    fences = re.findall(r"```[ \t]*(?:ya?ml)?[ \t]*\n(.*?)```", text, flags=re.S | re.I)
    candidates = [f for f in fences if re.search(r"^\s*blocks\s*:", f, re.M)] or fences or [text]
    try:
        data = yaml.safe_load(candidates[0])
    except yaml.YAMLError as e:
        raise ValueError(f"не удалось разобрать YAML: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("ответ должен быть одним YAML-словарём с полями title, blocks, source, endcard, edits")
    return data


def _anchors(params, path: str = "") -> list[tuple[str, object]]:
    """Все якоря в параметрах сцены: значения ключей at и *_at, в том числе в списках и вложенных словарях."""
    found = []
    if isinstance(params, dict):
        for k, v in params.items():
            p = f"{path}.{k}" if path else str(k)
            if str(k) == "at" or str(k).endswith("_at"):
                found += [(p, x) for x in (v if isinstance(v, list) else [v]) if not isinstance(x, (dict, list))]
            else:
                found += _anchors(v, p)
    elif isinstance(params, list):
        for i, v in enumerate(params):
            found += _anchors(v, f"{path}[{i}]")
    return found


def _anchor_words(at) -> list[str] | None:
    """Слова якоря для поиска в тексте; None — якорь не словесный (число, доля, секунды)."""
    if at is None or isinstance(at, (int, float)):
        return None
    s = re.sub(r"[+-]\d+(\.\d+)?$", "", str(at).strip()).strip()
    s = s[:-4].strip() if s.endswith("@end") else s
    if not s or re.fullmatch(r"\d+(\.\d+)?%?", s):
        return None
    return _words_norm(s)


def _contains(hay: list[str], need: list[str]) -> bool:
    return any(hay[k:k + len(need)] == need for k in range(len(hay) - len(need) + 1))


def _fake_words(text: str) -> list[dict]:
    """Условные таймкоды (0.4 с на слово) — чтобы прогнать шаблон сцены до озвучки."""
    return [{"w": t, "s": round(i * 0.4, 3), "e": round(i * 0.4 + 0.3, 3)} for i, t in enumerate(script_tokens(text))]


def _check_scene(index: int, text: str, scene: dict, where: str, out_dir: Path) -> list[str]:
    """Одна сцена (или один план): шаблон из библиотеки, якоря — слова блока, шаблон рендерится без ошибок."""
    kind = scene.get("type")
    if kind in EXCLUDED:
        return [f"{where}: сцена {kind} запрещена — выбери шаблон из каталога"]
    if kind not in REGISTRY:
        return [f"{where}: нет шаблона сцены «{kind}». Есть: {', '.join(sorted(set(REGISTRY) - set(EXCLUDED)))}"]
    problems = []
    hay = _words_norm(text)
    for path, at in _anchors({k: v for k, v in scene.items() if k != "type"}):
        need = _anchor_words(at)
        if need is not None and not _contains(hay, need):
            problems.append(f"{where}: якорь {path}: «{at}» не найден в тексте блока «{text}» — "
                            f"возьми слова дословно из этого блока")
    if problems:
        return problems
    words = _fake_words(text)
    ctx = SceneCtx(block=index, uid=f"b{index}", start=0.0, length=words[-1]["e"] if words else 1.0,
                   words=words, text=text, theme=Theme(name="blueprint"), project_dir=Path(out_dir))
    try:
        render_scene(ctx, scene)
    except Exception as e:  # noqa: BLE001 — любая ошибка шаблона = неверные параметры для модели
        return [f"{where}: сцена {kind}: {e}"]
    return []


def validate(raw: dict, out_dir: Path, *, blocks: int = 4, limit: int = 60) -> list[str]:
    """Проверка классического черновика: схема reel.yaml, число блоков и слов, сцены из библиотеки, якоря,
    самостоятельность блоков."""
    problems: list[str] = []
    cfg = {k: v for k, v in raw.items() if k != "music_prompts"}
    try:
        project = from_dict(cfg, Path(out_dir))
    except ConfigError as e:
        return [str(e)]
    for key in ("title", "source"):
        if not str(project.cfg.get(key) or "").strip():
            problems.append(f"{key}: поле пустое")
    if len(project.blocks) != blocks:
        problems.append(f"blocks: нужно ровно {blocks} блока(-ов), сейчас {len(project.blocks)}")
    if project.total_words > limit:
        problems.append(f"всего {project.total_words} слов при лимите {limit}: сократи на {project.total_words - limit} "
                        f"(по блокам: {', '.join(str(b.words) for b in project.blocks)})")
    raw_blocks = cfg.get("blocks") or []
    for b in project.blocks:
        where = f"blocks[{b.index}] «{b.name}»"
        given = raw_blocks[b.index - 1] if b.index - 1 < len(raw_blocks) else {}
        if not isinstance(given, dict) or not given.get("scene"):
            problems.append(f"{where}: нет scene — выбери шаблон из каталога")
            continue
        first = _words_norm(b.text)[:1]
        if b.index > 1 and first and first[0] in BAD_STARTS:
            problems.append(f"{where}: начинается с «{b.text.split()[0]}» — блок должен быть понятен без соседних, "
                            f"назови предмет прямо")
        problems += _check_scene(b.index, b.text, b.scene, where, out_dir)
    return problems


# --- проверка черновика в формате Instagram -----------------------------------------------------------------
def _runs(roles: list[str]) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for r in roles:
        if out and out[-1][0] == r:
            out[-1] = (r, out[-1][1] + 1)
        else:
            out.append((r, 1))
    return out


def _body_problems(fmt: F.Format, roles: list[str]) -> list[str]:
    """Роли тела ролика (после крючков) — в порядке формата и в его количествах."""
    runs, k, problems = _runs(roles), 0, []
    for beat in fmt.body:
        n = runs[k][1] if k < len(runs) and runs[k][0] == beat.role else 0
        if n:
            k += 1
        if not beat.count[0] <= n <= beat.count[1]:
            problems.append(f"роль {beat.role}: нужно {F._range(beat.count)} блок(-а) подряд, сейчас {n}")
    if k < len(runs):
        problems.append(f"лишние или переставленные роли: {', '.join(r for r, _ in runs[k:])}")
    if problems:
        want = " → ".join(f"{b.role}×{F._range(b.count)}" for b in fmt.body)
        problems = [f"тело ролика по формату {fmt.name}: {want}; сейчас {' → '.join(roles) or 'пусто'}. "
                    + "; ".join(problems)]
    return problems


def validate_reel(raw: dict, out_dir: Path, *, format: str | None = None, hooks: int = 3,
                  limit: int = 60) -> list[str]:
    """Проверка черновика в формате Instagram: формат и роли, крючки, бюджеты слов, длина монтажей, призыв,
    планы (шаблоны, якоря, длина плана), текст в кадре, безопасные зоны, звуки. Пустой список — годен."""
    problems: list[str] = []
    out_dir = Path(out_dir)
    name = raw.get("format") if format in (None, "auto") else format
    try:
        fmt = F.get(name) if name else None
    except KeyError as e:
        fmt = None
        problems.append(f"format: {e.args[0]}")
    if fmt is None and not problems:
        problems.append(f"format: укажи формат из библиотеки ({', '.join(F.FORMATS)})")
    for key in ("title", "source"):
        if not str(raw.get(key) or "").strip():
            problems.append(f"{key}: поле пустое")
    blocks = raw.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        return problems + ["blocks: нужен список блоков"]
    if len(blocks) > MAX_BLOCKS:
        problems.append(f"blocks: {len(blocks)} блоков — больше {MAX_BLOCKS}: сократи тело или число крючков")
    items = []
    for i, b in enumerate(blocks, 1):
        if not isinstance(b, dict) or not str(b.get("text") or "").strip():
            problems.append(f"blocks[{i}]: нужен словарь с непустым text")
            continue
        role = str(b.get("role") or "").strip().lower()
        if role not in F.ROLES:
            problems.append(f"blocks[{i}]: role «{b.get('role')}» — одна из {', '.join(F.ROLES)}")
        items.append((i, role, " ".join(str(b["text"]).split()), b))
    if len(items) != len(blocks):
        return problems
    roles = [r for _, r, _, _ in items]
    nh = sum(r in ("hook", "alt-hook") for r in roles)
    if roles[:hooks] != ["hook"] + ["alt-hook"] * (hooks - 1) or nh != hooks:
        problems.append(f"крючки: первыми идут {hooks} блок(-а): hook" + (", затем alt-hook" if hooks > 1 else "")
                        + f"; сейчас роли {', '.join(roles)}")
    if roles[-1] != "cta":
        problems.append("последний блок — role: cta (призыв к действию)")
    if fmt is not None and nh == hooks:
        problems += _body_problems(fmt, roles[hooks:])
    hook_items = [it for it in items if it[1] in ("hook", "alt-hook")]
    body = [it for it in items if it[1] not in ("hook", "alt-hook")]
    body_words = sum(word_count(t) for _, _, t, _ in body)
    seen: dict[str, int] = {}
    for i, _role, text, _ in hook_items:
        where = f"blocks[{i}] (крючок)"
        for p in TC.hook_problems(text):
            problems.append(f"{where}: {p}")
        key = " ".join(_words_norm(text))
        if key in seen:
            problems.append(f"{where}: повторяет крючок blocks[{seen[key]}] — нужен другой приём")
        seen.setdefault(key, i)
        total = word_count(text) + body_words
        if total > limit:
            problems.append(f"монтаж с крючком blocks[{i}]: {total} слов при лимите {limit} — сократи тело или крючок")
        sec = TC.seconds(total)
        lo, hi = F.REEL_SECONDS
        if not lo <= sec <= hi:
            problems.append(f"монтаж с крючком blocks[{i}]: ~{sec:.0f} с, нужно {lo:g}–{hi:g} с "
                            f"({F.SECONDS_PER_WORD:g} с на слово)")
    for i, role, text, b in items:
        where = f"blocks[{i}] «{b.get('name') or role}»"
        beat = fmt.beat(role) if fmt else None
        n = word_count(text)
        if beat and not beat.words[0] <= n <= beat.words[1]:
            problems.append(f"{where}: {n} слов, для роли {role} нужно {F._range(beat.words)}")
        if role == "cta" and not TC.has_cta(text, *TC._scene_strings(b.get("scene"))):
            problems.append(f"{where}: нет призыва — повелительный глагол: {', '.join(F.CTA_WORDS[:8])}…")
        problems += _scene_problems(i, text, b, where, out_dir)
    if body:
        i, _, text, _ = body[0]
        first = _words_norm(text)[:1]
        if first and first[0] in BAD_STARTS:
            problems.append(f"blocks[{i}]: начинается с «{text.split()[0]}» — тело идёт после любого из крючков, "
                            f"назови предмет прямо")
    return problems


def _scene_problems(i: int, text: str, b: dict, where: str, out_dir: Path) -> list[str]:
    scene = b.get("scene")
    if isinstance(scene, str):
        scene = {"type": scene}
    if not isinstance(scene, dict) or not scene:
        return [f"{where}: нет scene — планы из каталога: scene: {{shots: [...]}}"]
    shots = TC.shots_of(scene)
    problems: list[str] = []
    if not 1 <= len(shots) <= MAX_SHOTS:
        problems.append(f"{where}: {len(shots)} планов — нужно 1–{MAX_SHOTS} (лучше 2–3)")
    for k, shot in enumerate(shots):
        w = f"{where} план {k + 1}" if len(shots) > 1 else where
        if k and shot.get("at") in (None, "", []) and shot.get("start") in (None, ""):
            problems.append(f"{w}: нет at — слово из текста блока, на котором меняется план")
        problems += _check_scene(i, text, shot, w, out_dir)
    if not problems:
        lengths = TC.shot_lengths(text, scene)
        if len(lengths) > 1 and any(x <= 0.05 for x in lengths):
            problems.append(f"{where}: якоря at планов должны идти по порядку текста, без повторов")
        for k, x in enumerate(lengths, 1):
            if x > F.SHOT_MAX + SHOT_SLACK:
                problems.append(f"{where}: план {k} ~{x:.1f} с — дольше {F.SHOT_MAX:g} с: добавь план или сдвинь at")
    for line in TC.sticker_lines(scene):
        n = len(line.split())
        if len(line) > F.STICKER_MAX_CHARS or n > F.STICKER_MAX_WORDS:
            problems.append(f"{where}: текст в кадре «{line}» — {len(line)} знаков, {n} слов; нужно не больше "
                            f"{F.STICKER_MAX_CHARS} знаков и {F.STICKER_MAX_WORDS} слов в строке")
    problems += [f"{where}: {p}" for p in TC.safe_zone_problems(scene)]
    sfx = b.get("sfx")
    if sfx is not None:
        try:
            from .sfx import SOUNDS
        except ImportError:  # pragma: no cover
            SOUNDS = {}
        if not isinstance(sfx, list):
            problems.append(f"{where}: sfx — список [{{at, sound}}]")
        else:
            hay = _words_norm(text)
            for s in sfx:
                if not isinstance(s, dict):
                    problems.append(f"{where}: sfx — список [{{at, sound}}]")
                    continue
                if SOUNDS and s.get("sound") not in SOUNDS:
                    problems.append(f"{where}: sfx: нет звука «{s.get('sound')}». Есть: {', '.join(SOUNDS)}")
                need = _anchor_words(s.get("at"))
                if need is not None and not _contains(hay, need):
                    problems.append(f"{where}: sfx: якорь «{s.get('at')}» не найден в тексте блока")
    return problems


# --- черновик ----------------------------------------------------------------------------------------------
def _reel(raw: dict, model: str, topic: str, theme: str, limit: int) -> str:
    """Итоговый reel.yaml: поля от модели + голос и музыка по умолчанию + шапка-предупреждение."""
    blocks = []
    for b in raw["blocks"]:
        scene = b["scene"] if isinstance(b["scene"], dict) else {"type": b["scene"]}
        scene = {"type": scene["type"], **{k: v for k, v in scene.items() if k != "type"}}
        blocks.append({"name": b.get("name") or f"Блок {len(blocks) + 1}", "text": " ".join(str(b["text"]).split()),
                       "scene": scene})
    prompts = [str(p).strip() for p in (raw.get("music_prompts") or []) if str(p).strip()][:3]
    out = {"title": raw.get("title", ""), "theme": theme}
    if limit != DEFAULTS["limit"]:
        out["limit"] = limit
    out.update({"blocks": blocks, "source": raw.get("source", ""), "endcard": raw.get("endcard", ""),
                "edits": {k: str(v) for k, v in raw["edits"].items()},
                "voice": {"engine": "f5", "voices": list(DEFAULTS["voice"]["voices"])},
                "music": {"engine": "musicgen", "prompts": prompts or list(DEFAULTS["music"]["prompts"])}})
    head = f"# черновик от {model}, проверьте факты и ударения\n# тема: {' '.join(topic.split())}\n"
    return head + yaml.safe_dump(out, **YAML_KW)


def _scene_out(scene, shots_ok: bool) -> dict:
    shots = [{"type": s["type"], **{k: v for k, v in s.items() if k != "type"}} for s in TC.shots_of(scene)]
    if len(shots) == 1:
        return shots[0]
    if shots_ok:
        return {"shots": shots}
    return shots[0]               # конвейер ещё не умеет планы: оставляем первый план


def reel_dict(raw: dict, *, theme: str = "blueprint", limit: int = 60, hooks: int = 3, brand: dict | None = None,
              features: dict | None = None) -> dict:
    """reel.yaml (словарь) из проверенного ответа в формате Instagram: блоки с ролями и планами, монтаж на каждый
    крючок (крючок + общее тело), субтитры выключены, если текст несут наклейки."""
    feats = features if features is not None else project_features()
    blocks = [b for b in raw["blocks"]]
    if hooks > 1 and not feats.get("subset_edits"):
        say("  ! конвейер пока не умеет монтажи из части блоков — оставляю один крючок", err=True)
        blocks = [blocks[0]] + [b for b in blocks if b.get("role") not in ("hook", "alt-hook")]
        hooks = 1
    out_blocks = []
    for b in blocks:
        ob = {"name": str(b.get("name") or F.ROLE_TITLES.get(b.get("role"), "Блок")), "role": b.get("role"),
              "text": " ".join(str(b["text"]).split()), "scene": _scene_out(b["scene"], bool(feats.get("shots")))}
        if b.get("sfx"):
            ob["sfx"] = [{k: s[k] for k in ("at", "sound") if s.get(k) is not None} for s in b["sfx"]]
        out_blocks.append(ob)
    body = "".join(str(i) for i, b in enumerate(out_blocks, 1) if b["role"] not in ("hook", "alt-hook"))
    if hooks > 1:
        edits = {f"hook-{string.ascii_lowercase[h]}": f"{h + 1}{body}" for h in range(hooks)}
    else:
        edits = {"main": "".join(str(i) for i in range(1, len(out_blocks) + 1))}
    if feats.get("merges_edits"):     # иначе к монтажам добавится linear «1234» из DEFAULTS — первым займём его имя
        edits = {("linear" if k == 0 else name): order for k, (name, order) in enumerate(edits.items())}
    reel_words = max(word_count(b["text"]) for b in out_blocks[:hooks]) + sum(
        word_count(b["text"]) for b in out_blocks[hooks:])
    total = sum(word_count(b["text"]) for b in out_blocks)
    out: dict = {"title": raw.get("title", ""), "theme": theme}
    # limit в reel.yaml считает все блоки (озвучка одна на все крючки): запас — на запасные крючки
    lim = max(limit, reel_words) + (total - reel_words)
    if lim != DEFAULTS["limit"]:
        out["limit"] = lim
    out["blocks"] = out_blocks
    if all(any(s.get("type") in TC.TEXT_SCENES for s in TC.shots_of(b["scene"])) for b in out_blocks):
        out["captions"] = {"style": "none"}    # текст несут наклейки — караоке-субтитры не нужны
    if brand and feats.get("brand"):
        out["brand"] = brand
    prompts = [str(p).strip() for p in (raw.get("music_prompts") or []) if str(p).strip()][:3]
    out.update({"source": raw.get("source", ""), "endcard": raw.get("endcard", ""), "edits": edits,
                "voice": {"engine": "f5", "voices": list(DEFAULTS["voice"]["voices"])},
                "music": {"engine": "musicgen", "prompts": prompts or list(DEFAULTS["music"]["prompts"])}})
    return out


def caption_text(raw: dict) -> str:
    """Подпись к посту: первая строка — крючок, 3–5 хэштегов в конце."""
    cap = " ".join(str(raw.get("caption") or "").split()) or str(raw["blocks"][0]["text"])
    tags = [re.sub(r"[^\w]", "", str(t)) for t in (raw.get("hashtags") or [])]
    tags = [t for t in tags if t][:5]
    return cap + ("\n\n" + " ".join("#" + t for t in tags) if tags else "") + "\n"


def _write(out_dir: Path, text: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / CONFIG_NAME
    if path.exists():
        path.replace(path.with_name(CONFIG_NAME + ".bak"))
    path.write_text(text, encoding="utf-8")
    return path


def draft(topic: str, out_dir: Path, *, facts: str = "", url: str | None = None, model: str | None = None,
          blocks: int = 4, limit: int = 60, retries: int = 3, theme: str = "blueprint", format: str | None = None,
          hooks: int | None = None, brand: dict | None = None) -> Path:
    """Черновик reel.yaml по теме через локальную LLM. Возвращает путь к out_dir/reel.yaml.

    format=None (или "classic") — классика: ровно blocks блоков. format="auto" — модель выбирает формат из
    reels.formats; имя формата — пишет по нему. hooks — сколько крючков (A/B-монтажей), по умолчанию 3.
    brand — фирменный стиль для reel.yaml (если конвейер его поддерживает).

    Ответ проверяется (validate / validate_reel); при ошибках модель получает их список и переписывает ответ —
    до retries раз сверх первой попытки. Не вышло — DraftError, последний ответ сохраняется в out_dir/draft-raw.txt.
    В формате Instagram рядом с reel.yaml пишется caption.txt — подпись к посту с хэштегами.
    """
    out_dir = Path(out_dir)
    ig = format not in (None, "classic")
    if ig:
        fmt = None if format == "auto" else F.get(format)       # KeyError — неизвестный формат, до запроса к LLM
        body_min = F.body_blocks(fmt)[0] if fmt else max(F.body_blocks(f)[0] for f in F.FORMATS.values())
        hooks = max(1, min(hooks or 3, MAX_BLOCKS - body_min))
    url = (url or os.environ.get("REELS_LLM_URL") or DEFAULT_URL).rstrip("/")
    key = os.environ.get("REELS_LLM_KEY") or None
    model = model or os.environ.get("REELS_LLM_MODEL") or first_model(url, key)
    prompt = (build_prompt(topic, facts=facts, limit=limit, format=format, hooks=hooks) if ig
              else build_prompt(topic, facts=facts, blocks=blocks, limit=limit))
    messages = [{"role": "user", "content": prompt}]
    problems: list[str] = []
    reply = ""
    for attempt in range(1, retries + 2):
        say(f"  черновик: {model}, попытка {attempt} из {retries + 1}…")
        reply = chat(url, model, messages, key)
        try:
            if ig:
                raw = {k: v for k, v in extract_yaml(reply).items() if k in IG_FIELDS}
                problems = validate_reel(raw, out_dir, format=format, hooks=hooks, limit=limit)
                if not problems:
                    data = reel_dict(raw, theme=theme, limit=limit, hooks=hooks, brand=brand)
                    try:
                        from_dict(data, out_dir)
                    except ConfigError as e:
                        problems = [f"reel.yaml не собирается: {e}"]
            else:
                raw = {k: v for k, v in extract_yaml(reply).items() if k in LLM_FIELDS}
                if not raw.get("edits") and isinstance(raw.get("blocks"), list):
                    raw["edits"] = _default_edits(len(raw["blocks"]))
                problems = validate({**raw, "theme": theme, "limit": limit}, out_dir, blocks=blocks, limit=limit)
        except (ValueError, TypeError, AttributeError, KeyError) as e:
            problems = [str(e) if isinstance(e, ValueError) else f"неверная структура ответа: {e!r}"]
        if not problems:
            if not ig:
                return _write(out_dir, _reel(raw, model, topic, theme, limit))
            name = F.get(raw["format"] if format == "auto" else format).name
            head = (f"# черновик от {model}, формат {name}, крючков {hooks}: проверьте факты и ударения\n"
                    f"# тема: {' '.join(topic.split())}\n# подпись к посту — caption.txt\n")
            path = _write(out_dir, head + yaml.safe_dump(data, **YAML_KW))
            (out_dir / "caption.txt").write_text(caption_text(raw), encoding="utf-8")
            return path
        for p in problems:
            say(f"    - {p}")
        if ig:
            remind = (f"Напоминаю: {hooks} крючка(-ов) первыми (hook, alt-hook), тело по ролям формата, последний — "
                      f"cta; в монтаже не больше {limit} слов; 2–3 плана на блок, сцены только из каталога, якоря at — "
                      f"слова из текста своего блока.")
        else:
            remind = (f"Напоминаю: ровно {blocks} блока(-ов), не больше {limit} слов, сцены только из каталога, "
                      f"якоря — слова из текста своего блока.")
        messages += [{"role": "assistant", "content": reply},
                     {"role": "user", "content": "В ответе ошибки:\n" + "\n".join(f"- {p}" for p in problems)
                      + f"\n\nИсправь их и верни весь reel.yaml целиком одним блоком ```yaml. {remind}"}]
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / "draft-raw.txt"
    raw_path.write_text(reply, encoding="utf-8")
    raise DraftError(problems, raw_path)


__all__ = ["DraftError", "catalog_text", "build_prompt", "first_model", "chat", "extract_yaml", "validate",
           "validate_reel", "reel_dict", "caption_text", "project_features", "draft"]
