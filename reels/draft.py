"""Черновик reel.yaml по теме через локальную LLM (OpenAI-совместимый API: LM Studio, Ollama, llama.cpp server).

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
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

from .project import CONFIG_NAME, DEFAULTS, ConfigError, from_dict
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
YAML_KW = dict(allow_unicode=True, sort_keys=False, width=120)


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


def build_prompt(topic: str, *, facts: str = "", blocks: int = 4, limit: int = 60) -> str:
    """Текст запроса к модели: шаблон reels/prompts/draft.md + каталог сцен + схема ответа."""
    if facts.strip():
        facts_text = ("Используй **только** эти факты и цифры, ничего не добавляй от себя. Источник для `source` "
                      "возьми отсюда же:\n\n" + facts.strip())
    else:
        facts_text = ("Фактов не дали. Бери только то, в чём уверен, и только из реального источника. Не выдумывай "
                      "статистику, проценты и даты: если точной цифры не знаешь — обойдись без неё.")
    fmt = {"title": "Короткое название",
           "blocks": [{"name": r, "text": f"Текст диктора блока {i}.", "scene": {"type": "<шаблон из каталога>"}}
                      for i, r in enumerate(_roles(blocks), 1)],
           "source": "Организация, исследование, год",
           "endcard": "Вывод с *главным* словом",
           "edits": _default_edits(blocks),
           "music_prompts": ["minimal thoughtful piano, soft synth pulse, calm, 80 bpm, no drums",
                             "ambient cinematic underscore, warm pad, slow, reflective"]}
    values = {"topic": topic.strip(), "blocks": str(blocks), "limit": str(limit),
              "roles": " → ".join(r.lower() for r in _roles(blocks)), "facts": facts_text,
              "catalog": catalog_text(), "format": yaml.safe_dump(fmt, **YAML_KW).rstrip().replace(
                  "type: <шаблон из каталога>", "type: <шаблон из каталога>  # и его параметры")}
    text = PROMPT_FILE.read_text(encoding="utf-8")
    for k, v in values.items():
        text = text.replace("{{" + k + "}}", v)
    return text


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


def validate(raw: dict, out_dir: Path, *, blocks: int = 4, limit: int = 60) -> list[str]:
    """Проверка черновика: схема reel.yaml, число блоков и слов, сцены из библиотеки, якоря, самостоятельность блоков."""
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
        kind = b.scene.get("type")
        if kind in EXCLUDED:
            problems.append(f"{where}: сцена {kind} запрещена — выбери шаблон из каталога")
            continue
        if kind not in REGISTRY:
            problems.append(f"{where}: нет шаблона сцены «{kind}». Есть: {', '.join(sorted(set(REGISTRY) - set(EXCLUDED)))}")
            continue
        hay, bad = _words_norm(b.text), False
        for path, at in _anchors({k: v for k, v in b.scene.items() if k != "type"}):
            need = _anchor_words(at)
            if need is not None and not _contains(hay, need):
                bad = True
                problems.append(f"{where}: якорь {path}: «{at}» не найден в тексте блока «{b.text}» — "
                                f"возьми слова дословно из этого блока")
        if bad:
            continue
        words = _fake_words(b.text)
        ctx = SceneCtx(block=b.index, uid=f"b{b.index}", start=0.0, length=words[-1]["e"] if words else 1.0,
                       words=words, text=b.text, theme=Theme(name="blueprint"), project_dir=Path(out_dir))
        try:
            render_scene(ctx, b.scene)
        except Exception as e:  # noqa: BLE001 — любая ошибка шаблона = неверные параметры для модели
            problems.append(f"{where}: сцена {kind}: {e}")
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


def draft(topic: str, out_dir: Path, *, facts: str = "", url: str | None = None, model: str | None = None,
          blocks: int = 4, limit: int = 60, retries: int = 3, theme: str = "blueprint") -> Path:
    """Черновик reel.yaml по теме через локальную LLM. Возвращает путь к out_dir/reel.yaml.

    Ответ проверяется (validate); при ошибках модель получает их список и переписывает ответ — до retries раз
    сверх первой попытки. Не вышло — DraftError, последний ответ модели сохраняется в out_dir/draft-raw.txt.
    """
    out_dir = Path(out_dir)
    url = (url or os.environ.get("REELS_LLM_URL") or DEFAULT_URL).rstrip("/")
    key = os.environ.get("REELS_LLM_KEY") or None
    model = model or os.environ.get("REELS_LLM_MODEL") or first_model(url, key)
    messages = [{"role": "user", "content": build_prompt(topic, facts=facts, blocks=blocks, limit=limit)}]
    problems: list[str] = []
    reply = ""
    for attempt in range(1, retries + 2):
        say(f"  черновик: {model}, попытка {attempt} из {retries + 1}…")
        reply = chat(url, model, messages, key)
        try:
            raw = {k: v for k, v in extract_yaml(reply).items() if k in LLM_FIELDS}
            if not raw.get("edits") and isinstance(raw.get("blocks"), list):
                raw["edits"] = _default_edits(len(raw["blocks"]))
            problems = validate({**raw, "theme": theme, "limit": limit}, out_dir, blocks=blocks, limit=limit)
        except (ValueError, TypeError, AttributeError, KeyError) as e:
            problems = [str(e) if isinstance(e, ValueError) else f"неверная структура ответа: {e!r}"]
        if not problems:
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / CONFIG_NAME
            if path.exists():
                path.replace(path.with_name(CONFIG_NAME + ".bak"))
            path.write_text(_reel(raw, model, topic, theme, limit), encoding="utf-8")
            return path
        for p in problems:
            say(f"    - {p}")
        messages += [{"role": "assistant", "content": reply},
                     {"role": "user", "content": "В ответе ошибки:\n" + "\n".join(f"- {p}" for p in problems)
                      + f"\n\nИсправь их и верни весь reel.yaml целиком одним блоком ```yaml. Напоминаю: ровно "
                        f"{blocks} блока(-ов), не больше {limit} слов, сцены только из каталога, якоря — слова "
                        f"из текста своего блока."}]
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / "draft-raw.txt"
    raw_path.write_text(reply, encoding="utf-8")
    raise DraftError(problems, raw_path)


__all__ = ["DraftError", "catalog_text", "build_prompt", "first_model", "chat", "extract_yaml", "validate", "draft"]
