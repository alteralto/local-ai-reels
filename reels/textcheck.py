"""Проверка сценария до озвучки: лимит слов, самостоятельность блоков, то, что синтез прочтёт плохо, сцены,
и правила Instagram Reels: крючок, длина, призыв, темп смены кадров, текст в кадре, безопасные зоны.

Ошибки (error) останавливают конвейер, предупреждения (warning) — нет. Правила Instagram — предупреждения:
сценарий, написанный руками, может нарушать их сознательно. Черновик от LLM (reels.draft) проверяется
строже: там эти же правила — повод переписать ответ. Правила и примеры — docs/scripting.md.

Безопасные зоны Instagram (кадр 1080×1920): сверху ~250 px закрывают имя автора и звук, снизу ~420 px —
подпись поста и кнопки, справа ~140 px — колонка «лайк / комментарий / поделиться». Текст и главное
в кадре держат внутри прямоугольника x 60…940, y 250…1500.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import formats as F
from .project import Project

# блок может стоять первым в любом монтаже, поэтому не должен начинаться со ссылки на предыдущий
LINKS = {"он", "она", "оно", "они", "его", "её", "ее", "их", "это", "этот", "эта", "эти", "этого", "этой", "потом",
         "затем", "тогда", "поэтому", "также", "тоже", "ещё", "еще", "здесь", "там", "так", "таким", "такой", "такие",
         "кроме", "однако", "но", "а", "и", "зато", "впрочем", "итак"}

# признаки крючка с «зазором любопытства»: вопрос, число, отрицание, противопоставление, интрига
HOOK_WORDS = {"не", "нет", "ни", "никто", "никогда", "ничего", "но", "а", "хотя", "вместо", "почему", "зачем",
              "как", "что", "кто", "где", "куда", "откуда", "сколько", "какой", "какая", "какие", "стоп", "секрет",
              "ошибка", "ошибку", "правда", "миф", "оказывается", "самом", "деле", "все", "каждый", "каждая",
              "каждое", "только", "даже", "один", "одна", "два", "две", "три", "четыре", "пять", "десять", "сто",
              "тысяча", "тысяч", "миллион", "половина", "треть", "четверть", "пятый", "вдвое", "втрое", "больше",
              "меньше", "было", "стало", "pov", "представьте", "вы", "ваш", "ваша", "ваши", "прячет", "скрывает",
              "запрещено", "нельзя", "опасно"}

# параметры сцен с координатами в кадре (px): y-подобные и x-подобные
Y_KEYS = {"top", "y"}
X_KEYS = {"x", "left"}
# шаблоны, где текст в кадре несёт смысл ролика (наклейки, титры, ярлыки)
TEXT_SCENES = {"stickers", "tag", "cover"}
TEXT_KEYS = ("lines", "text", "title", "title2", "label", "sub", "tags", "items")


@dataclass
class Issue:
    level: str      # error | warning
    where: str      # «блок 2», «сценарий», «монтаж linear»
    msg: str

    def __str__(self) -> str:
        mark = "✗" if self.level == "error" else "!"
        return f"{mark} {self.where}: {self.msg}"


# --- сцены и планы -----------------------------------------------------------------------------------------
def shots_of(scene) -> list[dict]:
    """Планы сцены блока: scene: {shots: [...]} — список планов, иначе сама сцена — один план."""
    if isinstance(scene, dict) and isinstance(scene.get("shots"), list):
        return [s if isinstance(s, dict) else {"type": s} for s in scene["shots"]]
    if isinstance(scene, str):
        return [{"type": scene}]
    return [scene] if isinstance(scene, dict) else []


def _words(text: str) -> list[str]:
    return [w for w in (re.sub(r"[^\w-]", "", x).lower().replace("ё", "е") for x in text.split()) if re.search(r"\w", w)]


def seconds(words: int) -> float:
    """Оценка длительности речи по числу слов (темп диктора ~2,6 слова в секунду)."""
    return words * F.SECONDS_PER_WORD


def shot_lengths(text: str, scene) -> list[float]:
    """Оценка длины каждого плана блока, с: начало плана — его якорь at (слово блока), первый — с начала блока."""
    shots = shots_of(scene)
    words = _words(text)
    total = seconds(len(words))
    if len(shots) <= 1:
        return [total]
    starts: list[float] = []
    for i, s in enumerate(shots):          # как compose.shot_starts: start или at, без якоря — равные доли
        at = s.get("start", s.get("at"))
        at = (at[0] if at else None) if isinstance(at, (list, tuple)) else at
        t = _anchor_time(at, words, total) if i else 0.0
        starts.append(t if t is not None else total * i / len(shots))
    ends = starts[1:] + [total]
    return [max(0.0, e - s) for s, e in zip(starts, ends)]


def _anchor_time(at, words: list[str], total: float) -> float | None:
    if at is None:
        return None
    if isinstance(at, (int, float)):
        return float(at)
    body = re.sub(r"[+-]\d+(\.\d+)?$", "", str(at).strip()).strip()
    end = body.endswith("@end")
    body = body.removesuffix("@end").strip()
    m = re.fullmatch(r"(\d+(\.\d+)?)%", body)
    if m:
        return total * float(m.group(1)) / 100
    need = _words(body)
    for k in range(len(words) - len(need) + 1):
        if need and words[k:k + len(need)] == need:
            return seconds(k + (len(need) if end else 0))
    return None


def sticker_lines(scene) -> list[str]:
    """Строки текста в кадре из наклеек, ярлыков и титров (stickers, tag, cover) — для проверки длины."""
    out: list[str] = []
    for s in shots_of(scene):
        if s.get("type") not in TEXT_SCENES:
            continue
        for k in TEXT_KEYS:
            out += _strings(s.get(k))
    return [re.sub(r"\*", "", x).strip() for x in out if str(x).strip()]


def _strings(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [ln for ln in v.split("\n")]
    if isinstance(v, (int, float)):
        return []
    if isinstance(v, dict):
        return _strings(v.get("text") or v.get("label") or v.get("title"))
    if isinstance(v, list):
        return [x for item in v for x in _strings(item)]
    return []


def safe_zone_problems(scene) -> list[str]:
    """Параметры сцены, которые ставят содержимое под интерфейс Instagram. Best-effort по известным ключам:
    top/y/x/left плана (px кадра; дробь 0…1 — доля кадра) и строк наклеек. Координаты внутри графики
    (точки карты 0…900 и т. п.) не проверяются — у них своя система отсчёта."""
    w, h = F.FRAME
    z = F.SAFE_ZONES
    found: list[str] = []

    def check(params: dict, path: str) -> None:
        for k, v in params.items():
            if k not in Y_KEYS | X_KEYS or isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            px = v * (h if k in Y_KEYS else w) if isinstance(v, float) and 0 < v <= 1 else v
            p = f"{path}{k}"
            if k in Y_KEYS and (px < z["top"] or px > h - z["bottom"]):
                found.append(f"{p}: {v} — по вертикали вне безопасной зоны {z['top']}…{h - z['bottom']} px "
                             f"(сверху и снизу кадр закрывает интерфейс Instagram)")
            elif k in X_KEYS and px > w - z["right"]:
                found.append(f"{p}: {v} — правее {w - z['right']} px текст закроют кнопки Instagram")

    multi = isinstance(scene, dict) and "shots" in scene
    for i, s in enumerate(shots_of(scene)):
        pre = f"shots[{i}]." if multi else ""
        check({k: v for k, v in s.items() if k != "type"}, pre)
        if s.get("type") in TEXT_SCENES and isinstance(s.get("lines"), list):
            for j, ln in enumerate(s["lines"]):
                if isinstance(ln, dict):
                    check(ln, f"{pre}lines[{j}].")
    return found


# --- крючок и призыв ---------------------------------------------------------------------------------------
def slow_opener(text: str) -> str | None:
    """Медленное начало («Привет», «Сегодня я расскажу», «В этом видео»…) — или None."""
    low = " ".join(_words(text))
    return next((o for o in F.SLOW_OPENERS if low == o or low.startswith(o + " ")), None)


def hook_strength(text: str) -> bool:
    """Есть ли в крючке зазор любопытства: вопрос, число, отрицание, противопоставление, обращение к зрителю."""
    if "?" in text or re.search(r"\d", text) or re.search(r"\s[—–-]\s", text):
        return True
    return bool(set(_words(text)) & HOOK_WORDS)


def hook_problems(text: str) -> list[str]:
    """Что не так с крючком (пустой список — крючок годен)."""
    out = []
    n = len(_words(text))
    if n > F.HOOK_MAX_WORDS:
        out.append(f"крючок {n} слов — больше {F.HOOK_MAX_WORDS}: за 2–3 секунды его не дослушают")
    opener = slow_opener(text)
    if opener:
        out.append(f"медленное начало «{opener}» — первая фраза должна сразу цеплять (вопрос, цифра, противоречие)")
    elif not hook_strength(text):
        out.append("слабый крючок: нет вопроса, числа, «не» или противопоставления — нечему удержать палец")
    return out


def has_cta(*texts) -> bool:
    """Есть ли призыв к действию (повелительный глагол или «ссылка в профиле»)."""
    for t in texts:
        if not t:
            continue
        words = set(_words(str(t)))
        low = str(t).lower()
        if words & set(F.CTA_WORDS) or "ссылк" in low or "в профиле" in low or "в шапке" in low:
            return True
    return False


def _scene_strings(scene) -> list[str]:
    out: list[str] = []
    if isinstance(scene, dict):
        for k, v in scene.items():
            out += [v] if isinstance(v, str) and k != "type" else _scene_strings(v)
    elif isinstance(scene, list):
        for v in scene:
            out += _scene_strings(v)
    return out


# --- проверка ----------------------------------------------------------------------------------------------
def check_script(project: Project) -> list[Issue]:
    from .scenes import REGISTRY

    issues: list[Issue] = []
    limit = int(project.cfg["limit"])
    total = project.total_words
    if total > limit:
        issues.append(Issue("error", "сценарий", f"{total} слов при лимите {limit} — сократить на {total - limit}"))
    for b in project.blocks:
        where = f"блок {b.index} «{b.name}»"
        first = re.sub(r"[^\w-]", "", b.text.split()[0]).lower() if b.text.split() else ""
        if b.index > 1 and first in LINKS:
            issues.append(Issue("warning", where, f"начинается с «{b.text.split()[0]}» — в другом монтаже блок может "
                                                  "стоять первым, ссылка на предыдущий потеряется"))
        if re.search(r"\d", b.text):
            issues.append(Issue("warning", where, "цифры в тексте диктора: синтез читает их непредсказуемо — "
                                                  "напишите словами («двое из пяти»)"))
        for abbr in re.findall(r"\b[A-ZА-ЯЁ]{2,}\b", b.text):
            issues.append(Issue("warning", where, f"аббревиатура «{abbr}» — напишите, как читать («пи-ди-эф»)"))
        if re.search(r"[A-Za-z]", b.text) and project.cfg["lang"] == "ru":
            issues.append(Issue("warning", where, "латиница в тексте диктора: русская модель прочтёт её плохо"))
        if b.words > 30:
            issues.append(Issue("warning", where, f"{b.words} слов в одном блоке — длинно для одной сцены"))
        shots = shots_of(b.scene)
        if not shots:
            issues.append(Issue("error", where, "нет сцены"))
        for s in shots:
            kind = s.get("type")
            if kind not in REGISTRY:
                issues.append(Issue("error", where, f"неизвестный тип сцены «{kind}». Есть: {', '.join(sorted(REGISTRY))}"))
        for a in _anchors(b.scene):
            if not _anchor_in_text(a, b.text):
                issues.append(Issue("error", where, f"якорь «{a}» не встречается в тексте блока"))
        issues += _instagram_block(b)
    if not project.cfg["source"]:
        issues.append(Issue("warning", "сценарий", "нет подписи источника (source) — факты без источника вызывают "
                                                  "вопросы"))
    issues += _instagram_edits(project)
    return issues


def _instagram_block(b) -> list[Issue]:
    where = f"блок {b.index} «{b.name}»"
    out = []
    lengths = shot_lengths(b.text, b.scene)
    if len(lengths) > 1:
        for i, x in enumerate(lengths, 1):
            if x > F.SHOT_MAX:
                out.append(Issue("warning", where, f"план {i} держится ~{x:.1f} с (больше {F.SHOT_MAX:g}): статичный "
                                                   "кадр теряет зрителя — добавьте план или сдвиньте якорь at"))
    for line in sticker_lines(b.scene):
        if len(line) > F.STICKER_MAX_CHARS:
            out.append(Issue("warning", where, f"текст в кадре «{line}» — {len(line)} знаков (больше "
                                               f"{F.STICKER_MAX_CHARS}): разбейте на строки по 2–4 слова"))
    for p in safe_zone_problems(b.scene):
        out.append(Issue("warning", where, p))
    return out


def _instagram_edits(project: Project) -> list[Issue]:
    out: list[Issue] = []
    by_index = {b.index: b for b in project.blocks}
    hooks: dict[int, list[str]] = {}
    ctas: dict[int, list[str]] = {}
    lo, hi = F.REEL_SECONDS
    tail = float(project.cfg.get("tail") or 0)
    for name, order in project.edits.items():
        blocks = [by_index[i] for i in order if i in by_index]
        if not blocks:
            continue
        sec = seconds(sum(b.words for b in blocks)) + tail
        if sec < lo or sec > hi:
            out.append(Issue("warning", f"монтаж {name}", f"~{sec:.0f} с — для Reels лучше {lo:g}–{hi:g} с "
                                                          f"(оценка: {F.SECONDS_PER_WORD:g} с на слово)"))
        hooks.setdefault(blocks[0].index, []).append(name)
        ctas.setdefault(blocks[-1].index, []).append(name)
    for i, names in hooks.items():
        b = by_index[i]
        for msg in hook_problems(b.text):
            out.append(Issue("warning", f"блок {i} «{b.name}» (крючок в {', '.join(names)})", msg))
    endcard = project.cfg.get("endcard")
    end = [endcard] if isinstance(endcard, str) else _scene_strings(endcard)
    for i, names in ctas.items():
        b = by_index[i]
        if not has_cta(b.text, *_scene_strings(b.scene), *end):
            out.append(Issue("warning", f"блок {i} «{b.name}» (финал в {', '.join(names)})",
                             "нет призыва к действию: «сохрани», «отправь», «напиши», «заходи…» — в тексте, "
                             "в сцене или на финальной карточке"))
    return out


def _anchors(obj) -> list[str]:
    """Все строковые якоря (at и *_at) в параметрах сцены, вложенные списки и словари тоже (в том числе shots)."""
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "at" or str(k).endswith("_at"):
                out += [x for x in (v if isinstance(v, list) else [v]) if isinstance(x, str)]
            else:
                out += _anchors(v)
    elif isinstance(obj, list):
        for v in obj:
            out += _anchors(v)
    return out


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^\w ]", " ", s.lower().replace("ё", "е").replace("+", "")).split())


def _anchor_in_text(a: str, text: str) -> bool:
    body = re.sub(r"[+-]\d+(\.\d+)?$", "", a.strip()).removesuffix("@end").strip()
    if not body or re.fullmatch(r"\d+(\.\d+)?%?", body):
        return True
    return f" {_norm(body)} " in f" {_norm(text)} "
