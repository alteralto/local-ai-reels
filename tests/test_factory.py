"""Тесты reels.factory: файл тем, slug, прогон с подменёнными draft и pipeline.run (без LLM и рендера)."""
from __future__ import annotations

import argparse
import json

import pytest
import yaml

from reels import draft as D, factory as F, pipeline
from reels.project import CONFIG_NAME, ConfigError
from reels.util import ToolError


# --- файл тем --------------------------------------------------------------------------------------------------
def test_parse_text(tmp_path):
    (tmp_path / "facts.txt").write_text("Факт из файла.\n", encoding="utf-8")
    f = tmp_path / "topics.txt"
    f.write_text(
        "﻿# список тем\n"
        "Почему в городе нет туалетов | RSPH, 2019: каждый пятый выходит из дома реже\n"
        "\n"
        "Как работает светофор\n"
        "    Первый электрический светофор — Кливленд, 1914.\n"
        "\n"
        "    # не комментарий, а факт\n"
        "    Источник: Smithsonian\n"
        "- Тема из маркированного списка | @facts.txt\n"
        "2. Нумерованная тема\n"
        "# Закомментированная тема\n", encoding="utf-8")
    items = F.parse_topics(f)
    assert [i["topic"] for i in items] == ["Почему в городе нет туалетов", "Как работает светофор",
                                           "Тема из маркированного списка", "Нумерованная тема"]
    assert items[0]["facts"] == "RSPH, 2019: каждый пятый выходит из дома реже"
    assert items[1]["facts"] == ("Первый электрический светофор — Кливленд, 1914.\n\n# не комментарий, а факт\n"
                                 "Источник: Smithsonian")
    assert items[2]["facts"] == "Факт из файла."
    assert items[3]["facts"] == ""
    assert items[0]["slug"] == "pochemu-v-gorode-net-tualetov"
    assert all(i["theme"] is None for i in items)


def test_parse_text_inline_and_block_facts(tmp_path):
    f = tmp_path / "t.txt"
    f.write_text("Тема | коротко\n  подробно\n", encoding="utf-8")
    assert F.parse_topics(f)[0]["facts"] == "коротко\nподробно"


def test_parse_text_errors(tmp_path):
    f = tmp_path / "t.txt"
    f.write_text("   факт без темы\nТема\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="отступом"):
        F.parse_topics(f)
    f.write_text("# только комментарии\n\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="нет ни одной темы"):
        F.parse_topics(f)
    with pytest.raises(ConfigError, match="нет файла тем"):
        F.parse_topics(tmp_path / "missing.txt")


def test_parse_yaml(tmp_path):
    f = tmp_path / "topics.yaml"
    f.write_text(yaml.safe_dump([
        {"topic": "Поводок горожанина", "facts": "RSPH 2019", "slug": "povodok", "theme": "chalk"},
        "Тема строкой",
        {"topic": "Факты списком", "facts": ["первый", "второй"], "theme": {"base": "blueprint", "accent": "#f00"}},
    ], allow_unicode=True), encoding="utf-8")
    items = F.parse_topics(f)
    assert items[0] == {"topic": "Поводок горожанина", "facts": "RSPH 2019", "slug": "povodok", "theme": "chalk"}
    assert items[1] == {"topic": "Тема строкой", "facts": "", "slug": "tema-strokoy", "theme": None}
    assert items[2]["facts"] == "- первый\n- второй"
    assert items[2]["theme"] == {"base": "blueprint", "accent": "#f00"}


def test_parse_yaml_in_txt_and_topics_key(tmp_path):
    f = tmp_path / "topics.txt"
    f.write_text("- topic: Первая\n- topic: Вторая\n  facts: факт\n", encoding="utf-8")
    assert [(i["topic"], i["facts"]) for i in F.parse_topics(f)] == [("Первая", ""), ("Вторая", "факт")]
    g = tmp_path / "t.yml"
    g.write_text("topics:\n  - topic: Одна\n", encoding="utf-8")
    assert F.parse_topics(g)[0]["slug"] == "odna"


def test_parse_yaml_errors(tmp_path):
    f = tmp_path / "t.yaml"
    f.write_text("- topic: Тема\n  fact: опечатка\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="неизвестные поля fact"):
        F.parse_topics(f)
    f.write_text("- topic: ''\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="topic"):
        F.parse_topics(f)
    f.write_text("- {topic: А, slug: same}\n- {topic: Б, slug: same}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="дважды"):
        F.parse_topics(f)


# --- slug ------------------------------------------------------------------------------------------------------
def test_slugify_cyrillic():
    assert F.slugify("Щука, ёж и Хрущёв — съезд!") == "shchuka-ezh-i-khrushchev-sezd"
    assert F.slugify("Юла, Цапля, Жук, Чай, Шум, Эхо, Йод") == "yula-tsaplya-zhuk-chay-shum-ekho-yod"
    assert F.slugify("Мышь Фу") == "mysh-fu"
    assert F.slugify("Café Déjà vu 2024") == "cafe-deja-vu-2024"
    assert F.slugify("Їжак і ґанок") == "yizhak-i-ganok"
    assert F.slugify("?!…") == "reel"


def test_slugify_length():
    s = F.slugify("Почему в Москве нет общественных туалетов и что с этим делать")
    assert len(s) <= 40 and s == "pochemu-v-moskve-net-obshchestvennykh"
    long_word = F.slugify("Дезоксирибонуклеиновая" * 3)
    assert len(long_word) == 40 and "-" not in long_word


def test_slugify_unique():
    taken: set[str] = set()
    assert [F.slugify("Тема", taken) for _ in range(3)] == ["tema", "tema-2", "tema-3"]
    taken = {"a" * 40}
    s = F.slugify("a" * 50, taken)
    assert s == "a" * 38 + "-2" and len(s) == 40


def test_parse_topics_unique_slugs(tmp_path):
    f = tmp_path / "t.yaml"
    f.write_text("- Светофор\n- Светофор\n- {topic: Другое, slug: svetofor}\n", encoding="utf-8")
    # заданный вручную slug остаётся за своей темой, повторы получают -2, -3
    assert [i["slug"] for i in F.parse_topics(f)] == ["svetofor-2", "svetofor-3", "svetofor"]


# --- прогон ----------------------------------------------------------------------------------------------------
class Fake:
    """Подмена draft и pipeline.run: тема с «сломан» — DraftError, проект в waiting_gates — GateWait."""

    def __init__(self):
        self.drafts: list[tuple[str, dict]] = []
        self.runs: list[str] = []
        self.waiting_gates: dict[str, str] = {}
        self.fail_stage: set[str] = set()

    def draft(self, topic, out_dir, *, facts="", url=None, model=None, theme="blueprint", **kw):
        self.drafts.append((topic, {"facts": facts, "url": url, "model": model, "theme": theme}))
        if "сломан" in topic.lower():
            raise D.DraftError(["blocks: нужно ровно 4 блока(-ов), сейчас 2"], out_dir / "draft-raw.txt")
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / CONFIG_NAME
        path.write_text(yaml.safe_dump({"title": topic, "theme": theme, "blocks": ["Раз два три.", "Четыре пять."],
                                        "edits": {"linear": "12", "from-end": "21"}}, allow_unicode=True),
                        encoding="utf-8")
        return path

    def run(self, project, *, yes=False, edits=None, qa="error", interactive=None, **kw):
        assert interactive is False
        self.runs.append(project.slug)
        if project.slug in self.fail_stage:
            raise pipeline.StageError("в сценарии 2 ошибок — исправьте reel.yaml\nподробности")
        if project.slug in self.waiting_gates:
            raise pipeline.GateWait(self.waiting_gates[project.slug], "Сценарий:\n  1. Блок 1: Раз два три.", project)
        for e in (edits or project.edits):
            mp4 = project.out_mp4(e)
            mp4.parent.mkdir(parents=True, exist_ok=True)
            mp4.write_bytes(b"mp4")
        return {}


@pytest.fixture
def fake(monkeypatch):
    f = Fake()
    monkeypatch.setattr(D, "draft", f.draft)
    monkeypatch.setattr(pipeline, "run", f.run)
    return f


@pytest.fixture
def topics(tmp_path):
    f = tmp_path / "topics.txt"
    f.write_text("Готовая тема | факт один\nТема у ворот\nСломанная тема\n", encoding="utf-8")
    return f


def test_run_factory_statuses(tmp_path, topics, fake, capsys):
    out = tmp_path / "out"
    fake.waiting_gates["tema-u-vorot"] = "script"
    rows = F.run_factory(topics, out, theme="chalk", url="http://llm:1/v1", model="m")
    assert [(r["slug"], r["status"]) for r in rows] == [
        ("gotovaya-tema", "done"), ("tema-u-vorot", "waiting"), ("slomannaya-tema", "failed")]
    done, wait, bad = rows
    assert done["outputs"] == ["gotovaya-tema/out/gotovaya-tema-linear.mp4",
                               "gotovaya-tema/out/gotovaya-tema-from-end.mp4"]
    assert done["drafted"] and wait["drafted"] and not bad["drafted"]
    assert wait["gate"] == "script" and "Раз два три" in wait["summary"]
    assert bad["note"].startswith("черновик не прошёл проверку: - blocks: нужно ровно 4")
    assert fake.drafts[0] == ("Готовая тема", {"facts": "факт один", "url": "http://llm:1/v1", "model": "m",
                                               "theme": "chalk"})
    assert fake.runs == ["gotovaya-tema", "tema-u-vorot"]

    data = json.loads((out / "factory.json").read_text(encoding="utf-8"))
    assert [r["status"] for r in data["items"]] == ["done", "waiting", "failed"]
    md = (out / "factory.md").read_text(encoding="utf-8")
    assert "| 1 | Готовая тема | `gotovaya-tema` | ✓ готово | [gotovaya-tema-linear.mp4]" \
           "(gotovaya-tema/out/gotovaya-tema-linear.mp4)<br>" in md
    assert "| 2 | Тема у ворот | `tema-u-vorot` | … ждёт «script» | — |" in md
    assert "| 3 | Сломанная тема | `slomannaya-tema` | ✗ ошибка | — |" in md
    assert f"python -m reels approve {out / 'tema-u-vorot'} script" in md
    assert "## Ошибки" in md and "blocks: нужно ровно 4" in md
    printed = capsys.readouterr().out
    assert "■ фабрика: тем 3 — ✓ 1, … 1, ✗ 1" in printed and "gotovaya-tema/out/gotovaya-tema-linear.mp4" in printed


def test_run_factory_resumes(tmp_path, topics, fake):
    out = tmp_path / "out"
    fake.waiting_gates["tema-u-vorot"] = "script"
    F.run_factory(topics, out)
    assert len(fake.drafts) == 3
    # человек подтвердил точку, а сломанную тему поправили (модель на этот раз справится)
    fake.waiting_gates.clear()
    fake.drafts.clear()
    fake.runs.clear()
    topics.write_text("Готовая тема | факт один\nТема у ворот\nПочиненная тема\n", encoding="utf-8")
    rows = F.run_factory(topics, out)
    assert [r["status"] for r in rows] == ["done", "done", "done"]
    assert [t for t, _ in fake.drafts] == ["Починенная тема"]        # черновики с reel.yaml не пишутся заново
    assert fake.runs == ["gotovaya-tema", "tema-u-vorot", "pochinennaya-tema"]
    assert [r["drafted"] for r in rows] == [False, False, True]


def test_run_factory_reuses_existing_project_and_theme_priority(tmp_path, fake):
    f = tmp_path / "t.yaml"
    f.write_text("- {topic: Своя тема, theme: chalk}\n- {topic: Общая тема}\n- {topic: Готовый, slug: ready}\n",
                 encoding="utf-8")
    out = tmp_path / "out"
    (out / "ready").mkdir(parents=True)
    (out / "ready" / CONFIG_NAME).write_text("blocks: [Один.]\nedits: {linear: '1'}\n", encoding="utf-8")
    rows = F.run_factory(f, out, theme="paper", edits=["linear"])
    assert [t["theme"] for _, t in fake.drafts] == ["chalk", "paper"]
    assert rows[2]["status"] == "done" and not rows[2]["drafted"]
    assert rows[2]["outputs"] == ["ready/out/ready-linear.mp4"]
    rows = F.run_factory(f, tmp_path / "out2")
    assert [t["theme"] for _, t in fake.drafts[2:]] == ["chalk", "blueprint", "blueprint"]


def test_run_factory_stage_error_and_bad_yaml(tmp_path, fake):
    f = tmp_path / "t.txt"
    f.write_text("Первая\nВторая\n", encoding="utf-8")
    out = tmp_path / "out"
    fake.fail_stage.add("pervaya")
    (out / "vtoraya").mkdir(parents=True)
    (out / "vtoraya" / CONFIG_NAME).write_text("blocks: [Один.]\nbogus: 1\n", encoding="utf-8")
    rows = F.run_factory(f, out)
    assert [r["status"] for r in rows] == ["failed", "failed"]
    assert rows[0]["note"] == "в сценарии 2 ошибок — исправьте reel.yaml"
    assert "неизвестные поля: bogus" in rows[1]["note"]


def test_run_factory_llm_down_skips_other_drafts(tmp_path, fake, monkeypatch):
    calls = []

    def down(topic, out_dir, **kw):
        calls.append(topic)
        raise ToolError("нет связи с LLM http://localhost:1234/v1: refused. Запустите сервер")

    monkeypatch.setattr(D, "draft", down)
    f = tmp_path / "t.txt"
    f.write_text("Раз\nДва\nТри\n", encoding="utf-8")
    rows = F.run_factory(f, tmp_path / "out")
    assert calls == ["Раз"]
    assert all(r["status"] == "failed" and r["note"].startswith("нет связи с LLM") for r in rows)


def test_cmd_factory_exit_codes(tmp_path, topics, fake, capsys):
    a = argparse.Namespace(topics=str(topics), out=str(tmp_path / "out"), yes=True, edit=None, theme=None, url=None,
                           model=None, qa="warn", jobs=2)
    assert F.cmd_factory(a) == 1                         # сломанная тема
    assert "--jobs" in capsys.readouterr().err
    topics.write_text("Готовая тема\nТема у ворот\n", encoding="utf-8")
    fake.waiting_gates["tema-u-vorot"] = "take"
    assert F.cmd_factory(a) == F.EXIT_GATE
    fake.waiting_gates.clear()
    assert F.cmd_factory(a) == 0
