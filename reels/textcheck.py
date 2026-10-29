"""Проверка сценария до озвучки: лимит слов, самостоятельность блоков, то, что синтез прочтёт плохо, сцены.

Ошибки (error) останавливают конвейер, предупреждения (warning) — нет.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .project import Project

# блок может стоять первым в любом монтаже, поэтому не должен начинаться со ссылки на предыдущий
LINKS = {"он", "она", "оно", "они", "его", "её", "ее", "их", "это", "этот", "эта", "эти", "этого", "этой", "потом",
         "затем", "тогда", "поэтому", "также", "тоже", "ещё", "еще", "здесь", "там", "так", "таким", "такой", "такие",
         "кроме", "однако", "но", "а", "и", "зато", "впрочем", "итак"}


@dataclass
class Issue:
    level: str      # error | warning
    where: str      # «блок 2», «сценарий», «монтаж linear»
    msg: str

    def __str__(self) -> str:
        mark = "✗" if self.level == "error" else "!"
        return f"{mark} {self.where}: {self.msg}"


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
        kind = b.scene.get("type")
        if kind not in REGISTRY:
            issues.append(Issue("error", where, f"неизвестный тип сцены «{kind}». Есть: {', '.join(sorted(REGISTRY))}"))
        anchors = _anchors(b.scene)
        for a in anchors:
            if not _anchor_in_text(a, b.text):
                issues.append(Issue("error", where, f"якорь «{a}» не встречается в тексте блока"))
    if not project.cfg["source"]:
        issues.append(Issue("warning", "сценарий", "нет подписи источника (source) — факты без источника вызывают "
                                                  "вопросы"))
    return issues


def _anchors(obj) -> list[str]:
    """Все строковые якоря (at и *_at) в параметрах сцены, вложенные списки и словари тоже."""
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
