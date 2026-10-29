#!/usr/bin/env python3
"""Поднимает версию пакета перед релизом.

    python tools/bump_version.py 0.3.0 [--date 2026-11-01] [--dry-run]

Меняет три места и ничего не коммитит:
  * pyproject.toml — `version = "..."` в секции [project];
  * reels/__init__.py — `__version__ = "..."`;
  * CHANGELOG.md — содержимое «## [Unreleased]» уходит в «## [X.Y.Z] — дата», над ним остаётся пустой
    «## [Unreleased]», ссылки сравнения внизу файла обновляются.

Дальше: проверить diff, закоммитить, `git tag vX.Y.Z && git push --tags` — релиз соберёт .github/workflows/release.yml.
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
INIT = ROOT / "reels" / "__init__.py"
CHANGELOG = ROOT / "CHANGELOG.md"

SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:(a|b|rc)(0|[1-9]\d*))?$")
PROJECT_SECTION = re.compile(r"^\[project\]\s*$", re.M)
NEXT_SECTION = re.compile(r"^\[", re.M)
PROJECT_VERSION = re.compile(r'^version\s*=\s*"([^"]+)"', re.M)
INIT_VERSION = re.compile(r'^(__version__\s*=\s*["\'])([^"\']+)(["\'])', re.M)
UNRELEASED = re.compile(r"^## \[Unreleased\][^\n]*\n", re.M)
UNRELEASED_LINK = re.compile(r"^\[Unreleased\]:\s*(\S+?)/compare/(\S+?)\.\.\.HEAD\s*$", re.M)


class BumpError(Exception):
    pass


def _key(v: str) -> tuple:
    m = SEMVER.match(v)
    if not m:
        raise BumpError(f"версия «{v}» не в формате X.Y.Z (или X.Y.ZrcN)")
    major, minor, patch, pre, pre_n = m.groups()
    # финальный релиз старше любого пре-релиза той же версии
    return int(major), int(minor), int(patch), (pre or "~"), int(pre_n or 0)


def bump_pyproject(text: str, new: str) -> tuple[str, str]:
    sec = PROJECT_SECTION.search(text)
    end = NEXT_SECTION.search(text, sec.end()) if sec else None
    m = PROJECT_VERSION.search(text, sec.end(), end.start() if end else len(text)) if sec else None
    if not m:
        raise BumpError("в pyproject.toml нет version = \"...\" в секции [project]")
    return text[:m.start(1)] + new + text[m.end(1):], m.group(1)


def bump_init(text: str, new: str) -> tuple[str, str]:
    m = INIT_VERSION.search(text)
    if not m:
        raise BumpError("в reels/__init__.py нет __version__ = \"...\"")
    return text[:m.start(2)] + new + text[m.end(2):], m.group(2)


def bump_changelog(text: str, new: str, old: str, date: str) -> str:
    if re.search(rf"^## \[{re.escape(new)}\]", text, re.M):
        raise BumpError(f"в CHANGELOG.md уже есть раздел [{new}]")
    m = UNRELEASED.search(text)
    if not m:
        raise BumpError("в CHANGELOG.md нет раздела «## [Unreleased]»")
    nxt = re.compile(r"^## \[", re.M).search(text, m.end())
    body = text[m.end():nxt.start() if nxt else len(text)]
    if not body.strip():
        raise BumpError("раздел [Unreleased] пуст — нечего выпускать; опишите изменения в CHANGELOG.md")
    text = text[:m.end()] + f"\n## [{new}] — {date}\n" + text[m.end():]
    # ссылки сравнения в стиле Keep a Changelog: [Unreleased] → vNEW...HEAD, [NEW] → vOLD...vNEW
    link = UNRELEASED_LINK.search(text)
    if link:
        base, prev = link.group(1), link.group(2)
        repl = f"[Unreleased]: {base}/compare/v{new}...HEAD\n[{new}]: {base}/compare/{prev}...v{new}"
        text = text[:link.start()] + repl + text[link.end():]
    elif old:
        print("CHANGELOG.md: ссылки [Unreleased]: .../compare/...HEAD не нашлось — ссылки не обновлены",
              file=sys.stderr)
    return text


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("version", help="новая версия X.Y.Z (без v)")
    ap.add_argument("--date", default=dt.date.today().isoformat(), help="дата релиза, по умолчанию сегодня")
    ap.add_argument("--dry-run", action="store_true", help="только показать, что изменится")
    ap.add_argument("--allow-same", action="store_true", help="не ругаться, если версия не растёт")
    a = ap.parse_args(argv)

    new = a.version.removeprefix("v")
    try:
        _key(new)
        dt.date.fromisoformat(a.date)
    except (ValueError, BumpError) as e:
        print(f"ошибка: {e}", file=sys.stderr)
        return 2

    try:
        py_new, old = bump_pyproject(PYPROJECT.read_text(encoding="utf-8"), new)
        init_new, init_old = bump_init(INIT.read_text(encoding="utf-8"), new)
        if init_old != old:
            raise BumpError(f"версии расходятся: pyproject.toml {old}, reels/__init__.py {init_old}")
        if not a.allow_same and _key(new) <= _key(old):
            raise BumpError(f"новая версия {new} не больше текущей {old}")
        cl_new = bump_changelog(CHANGELOG.read_text(encoding="utf-8"), new, old, a.date)
    except BumpError as e:
        print(f"ошибка: {e}", file=sys.stderr)
        return 1

    changes = [(PYPROJECT, py_new), (INIT, init_new), (CHANGELOG, cl_new)]
    for path, text in changes:
        rel = path.relative_to(ROOT)
        if a.dry_run:
            print(f"[dry-run] {rel}")
        else:
            path.write_text(text, encoding="utf-8", newline="\n")
            print(f"обновлён {rel}")
    print(f"{old} → {new}" + (" (dry-run, файлы не тронуты)" if a.dry_run else ""))
    if not a.dry_run:
        print(f"дальше: git commit -am 'release {new}' && git tag v{new} && git push && git push --tags")
    return 0


if __name__ == "__main__":
    sys.exit(main())
