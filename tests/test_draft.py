"""Тесты reels.draft на поддельном OpenAI-совместимом сервере (без сети и без моделей)."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import yaml

from reels import draft as D
from reels.project import load
from reels.scenes import base
from reels.scenes.base import REGISTRY, SceneDoc, SceneOut, esc, scene
from reels.util import ToolError


# --- поддельные шаблоны сцен -------------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def fake_scenes():
    saved = dict(REGISTRY)

    @scene("t_title", SceneDoc("Крупный заголовок.", {"text": "строка, обязательно", "at": "якорь появления"},
                               {"text": "Каждый *пятый*", "at": "каждый пятый"}))
    def t_title(ctx, p):
        ctx.at(p.get("at"))
        return SceneOut(f"<h1>{esc(p['text'])}</h1>")

    @scene("t_count", SceneDoc("Число и пункты.", {"value": "число, обязательно", "items": "[{label, at}]"},
                               {"value": 2000, "items": [{"label": "ямы", "at": "ямы"}]}))
    def t_count(ctx, p):
        for it in p.get("items", []):
            ctx.at(it["at"])
        return SceneOut(f"<b>{float(p['value'])}</b>")

    if "custom" not in REGISTRY:
        scene("custom", SceneDoc("Своя сцена.", {"file": "путь"}))(lambda ctx, p: SceneOut(""))
    yield
    REGISTRY.clear()
    REGISTRY.update(saved)


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for k in ("REELS_LLM_URL", "REELS_LLM_MODEL", "REELS_LLM_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(D, "RETRY_WAIT", 0)


# --- поддельный сервер -------------------------------------------------------------------------------------
class FakeLLM:
    def __init__(self, replies):
        self.replies, self.requests, self.model_calls = list(replies), [], 0
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, obj):
                body = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                outer.model_calls += 1
                self._send({"object": "list", "data": [{"id": "fake-model"}, {"id": "other"}]})

            def do_POST(self):
                req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                req["_auth"] = self.headers.get("Authorization")
                outer.requests.append(req)
                text = outer.replies.pop(0) if len(outer.replies) > 1 else outer.replies[0]
                self._send({"choices": [{"message": {"role": "assistant", "content": text}}]})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/v1"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def llm():
    servers = []

    def make(*replies):
        s = FakeLLM(replies)
        servers.append(s)
        return s
    yield make
    for s in servers:
        s.close()


GOOD = {
    "title": "Город без барьеров",
    "blocks": [
        {"name": "Крючок", "text": "Каждый пятый горожанин выходит из дома реже, чем хочет.",
         "scene": {"type": "t_title", "text": "Каждый *пятый*", "at": "каждый пятый"}},
        {"name": "Факт", "text": "Опрос двух тысяч жителей показал: мешают лестницы, ямы и узкие тротуары.",
         "scene": {"type": "t_count", "value": 2000,
                   "items": [{"label": "лестницы", "at": "лестницы"}, {"label": "ямы", "at": "ямы@end+0.2"}]}},
        {"name": "Поворот", "text": "Больше всего страдают не пожилые люди, а родители с колясками.",
         "scene": {"type": "t_title", "text": "Родители", "at": "родители"}},
        {"name": "Панчлайн", "text": "Город без барьеров нужен всем, а не только меньшинству.",
         "scene": {"type": "t_title", "text": "Нужен *всем*", "at": "50%"}},
    ],
    "source": "Опрос горожан, 2024",
    "endcard": "Город для *всех*",
    "edits": {"linear": "1234", "from-end": "4123", "suspense": "1324"},
    "music_prompts": ["soft piano, calm, 80 bpm, no drums", "warm ambient pad, slow"],
}


def reply(data, think=True):
    head = "<think>Думаю над сценарием: blocks: нет\n</think>\n" if think else ""
    return head + "Вот черновик:\n```yaml\n" + yaml.safe_dump(data, allow_unicode=True, sort_keys=False) + "```\n"


def broken():
    bad = json.loads(json.dumps(GOOD))
    bad["blocks"][1]["scene"] = {"type": "custom", "file": "x.html"}
    bad["blocks"][2]["text"] = "Это сильнее всего бьёт по родителям с колясками."
    bad["blocks"][3]["scene"]["at"] = "барьерам"
    return bad


# --- тесты -------------------------------------------------------------------------------------------------
def test_happy_path_models_discovery(llm, tmp_path):
    s = llm(reply(GOOD))
    path = D.draft("Барьеры в городе", tmp_path / "p", url=s.url)
    assert path == tmp_path / "p" / "reel.yaml"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# черновик от fake-model, проверьте факты и ударения")
    assert s.model_calls == 1 and s.requests[0]["model"] == "fake-model"
    assert s.requests[0]["temperature"] == 0.4 and s.requests[0]["_auth"] is None
    prompt = s.requests[0]["messages"][0]["content"]
    assert "Барьеры в городе" in prompt and "### t_title" in prompt and "### custom" not in prompt
    assert "{{" not in prompt
    p = load(path.parent)
    assert [b.scene["type"] for b in p.blocks] == ["t_title", "t_count", "t_title", "t_title"]
    assert p.cfg["music"]["prompts"] == GOOD["music_prompts"] and p.cfg["voice"]["engine"] == "f5"
    assert p.edits["from-end"] == [4, 1, 2, 3] and p.cfg["theme"] == "blueprint"
    assert "music_prompts" not in yaml.safe_load(text)


def test_env_model_and_key(llm, tmp_path, monkeypatch):
    s = llm(reply(GOOD, think=False))
    monkeypatch.setenv("REELS_LLM_URL", s.url)
    monkeypatch.setenv("REELS_LLM_MODEL", "my-model")
    monkeypatch.setenv("REELS_LLM_KEY", "secret")
    D.draft("тема", tmp_path, facts="Опрос горожан, 2024: каждый пятый...")
    assert s.model_calls == 0 and s.requests[0]["model"] == "my-model"
    assert s.requests[0]["_auth"] == "Bearer secret"
    assert "Опрос горожан, 2024" in s.requests[0]["messages"][0]["content"]


def test_retry_then_ok(llm, tmp_path):
    s = llm(reply(broken()), reply(GOOD))
    path = D.draft("тема", tmp_path, url=s.url, model="m")
    assert path.exists() and len(s.requests) == 2
    msgs = s.requests[1]["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    fix = msgs[2]["content"]
    assert "custom" in fix and "«Это»" in fix and "барьерам" in fix


def test_over_limit_fails_after_retries(llm, tmp_path):
    s = llm(reply(GOOD))
    with pytest.raises(D.DraftError) as e:
        D.draft("тема", tmp_path, url=s.url, model="m", limit=30, retries=1)
    assert len(s.requests) == 2
    assert any("при лимите 30" in p for p in e.value.problems)
    assert (tmp_path / "draft-raw.txt").read_text(encoding="utf-8").startswith("<think>")
    assert not (tmp_path / "reel.yaml").exists()


def test_garbage_and_block_count(llm, tmp_path):
    three = dict(GOOD, blocks=GOOD["blocks"][:3], edits={"linear": "123"})
    s = llm("Не знаю, что ответить.", reply(three))
    with pytest.raises(D.DraftError) as e:
        D.draft("тема", tmp_path, url=s.url, model="m", retries=1)
    assert any("ровно 4" in p for p in e.value.problems)


def test_scene_params_checked_by_template(tmp_path):
    bad = json.loads(json.dumps(GOOD))
    del bad["blocks"][1]["scene"]["value"]
    bad["blocks"][0]["scene"]["type"] = "nope"
    probs = D.validate(bad, tmp_path)
    assert any("nope" in p for p in probs) and any("t_count" in p and "value" in p for p in probs)
    assert D.validate(GOOD | {"music_prompts": None}, tmp_path) == []


def test_extract_yaml_variants():
    assert D.extract_yaml(reply(GOOD))["title"] == GOOD["title"]
    raw = yaml.safe_dump(GOOD, allow_unicode=True, sort_keys=False)
    assert D.extract_yaml("<think>x</think>\n" + raw)["blocks"][0]["name"] == "Крючок"
    with pytest.raises(ValueError):
        D.extract_yaml("просто текст")


def test_catalog_text():
    cat = D.catalog_text()
    assert "### t_count" in cat and "type: t_count" in cat and "`value`" in cat and "### custom" not in cat
    assert "### custom" in D.catalog_text(include_custom=True)


def test_connection_error(tmp_path):
    with pytest.raises(ToolError, match="нет связи"):
        D.draft("тема", tmp_path, url="http://127.0.0.1:9/v1", model="m")
    assert base.REGISTRY is REGISTRY


# --- формат Instagram: крючки A/B, планы, наклейки -----------------------------------------------------------
@pytest.fixture
def stickers():
    """Поддельные наклейки (вместо настоящего шаблона stickers — тесты не зависят от его параметров)."""
    @scene("stickers", SceneDoc("Наклейки: строки текста на плашках.", {"lines": "строки или {text, at}"},
                                {"lines": [{"text": "Что прячет", "at": "что прячет"}, "кабинка?"]}))
    def fake_stickers(ctx, p):
        for ln in p["lines"]:
            if isinstance(ln, dict):
                ctx.at(ln.get("at"))
        return SceneOut("<div></div>")


def st(*lines, at=None):
    shot = {"type": "stickers", "lines": list(lines)}
    return shot | ({"at": at} if at else {})


IG = {
    "format": "question",
    "title": "Что прячет кабинка",
    "blocks": [
        {"role": "hook", "pattern": "question", "name": "Крючок A", "text": "Что прячет обычная городская кабинка?",
         "scene": {"shots": [st({"text": "Что прячет", "at": "что прячет"}, "*кабинка?*"),
                             {"type": "t_title", "text": "WC", "at": "кабинка"}]},
         "sfx": [{"at": "прячет", "sound": "knock"}]},
        {"role": "alt-hook", "pattern": "number", "name": "Крючок B",
         "text": "Каждый пятый горожанин сидит дома из-за туалета.",
         "scene": {"shots": [st("Каждый *пятый*"), {"type": "t_title", "text": "1 из 5", "at": "сидит дома"}]}},
        {"role": "alt-hook", "pattern": "stop", "name": "Крючок C", "text": "Стоп. Город без туалетов держит вас на поводке.",
         "scene": {"shots": [st("Стоп."), st("Город на *поводке*", at="держит")]}},
        {"role": "context", "name": "Контекст",
         "text": "Общественный туалет кажется мелочью, но без него город сжимается до пары кварталов.",
         "scene": {"shots": [st("Мелочь?"), st("Город", "в два квартала", at="город")]}},
        {"role": "value", "name": "Факт",
         "text": "Королевское общество здравоохранения выяснило: каждый пятый выходит из дома реже, чем хочет.",
         "scene": {"shots": [st("RSPH · 2019"), {"type": "t_count", "value": 5,
                                                  "items": [{"label": "дома", "at": "дома"}], "at": "каждый пятый"},
                             st("1 из 5", at="реже")]}},
        {"role": "value", "name": "Факт 2",
         "text": "У людей с болезнями, требующими частого туалета, доля почти вдвое выше.",
         "scene": {"shots": [st("Болезни"), st("*Вдвое* чаще", at="доля")]}},
        {"role": "payoff", "name": "Развязка", "text": "Кабинка прячет свободу: без неё город кончается за углом.",
         "scene": {"shots": [st("Кабинка прячет", "*свободу*"), st("Город — до угла", at="без неё")]}},
        {"role": "cta", "name": "Призыв", "text": "Отправь тому, кто рисует ваш город.",
         "scene": st("Отправь", "градостроителю")},
    ],
    "source": "RSPH, Taking the P, 2019",
    "endcard": "Туалет — тоже *архитектура*",
    "caption": "Что прячет обычная городская кабинка? Ответ — в конце.",
    "hashtags": ["урбанистика", "#город", "туалеты"],
    "music_prompts": ["minimal piano, 80 bpm"],
}


def ig_broken():
    bad = json.loads(json.dumps(IG))
    bad["blocks"][0]["text"] = "Привет! Сегодня расскажу про туалеты."
    bad["blocks"][0]["scene"]["shots"][0]["lines"][0]["at"] = "кабинка"
    bad["blocks"][3]["scene"]["shots"][1]["lines"] = ["Город сжимается до пары кварталов вокруг дома"]
    bad["blocks"][7]["text"] = "Город для всех начинается с кабинки."
    bad["blocks"][7]["scene"] = st("Город для всех")
    return bad


def test_ig_draft_hooks_shots_edits(llm, tmp_path, stickers):
    s = llm(reply(IG))
    path = D.draft("Что прячет городская кабинка", tmp_path / "p", url=s.url, model="m", format="auto", hooks=3)
    prompt = s.requests[0]["messages"][0]["content"]
    assert "### question — вопрос-загадка" in prompt and "### promo — промо продукта" in prompt
    assert "3 разных крючка" in prompt and "### stickers" in prompt and "{{" not in prompt
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# черновик от m, формат question, крючков 3")
    data = yaml.safe_load(text)
    assert list(data["edits"].values()) == ["145678", "245678", "345678"], "монтаж на каждый крючок"
    assert data["captions"] == {"style": "none"}, "текст несут наклейки"
    assert data["limit"] == 72, "озвучка одна на все крючки: лимит с запасом на запасные"
    assert [b["role"] for b in data["blocks"]] == ["hook", "alt-hook", "alt-hook", "context", "value", "value",
                                                   "payoff", "cta"]
    assert data["blocks"][0]["sfx"] == [{"at": "прячет", "sound": "knock"}]
    assert data["blocks"][4]["scene"]["shots"][1]["type"] == "t_count"
    assert data["blocks"][7]["scene"]["type"] == "stickers", "один план — обычная сцена"
    assert "pattern" not in data["blocks"][0] and "caption" not in data and "format" not in data
    p = load(path.parent)
    assert set(p.edits) == set(data["edits"]), "лишних монтажей (linear из DEFAULTS) нет"
    assert list(p.edits.values())[1] == [2, 4, 5, 6, 7, 8] and p.blocks[1].role == "alt-hook"
    assert p.blocks[0].shots and len(p.blocks[0].shots) == 2
    cap = (path.parent / "caption.txt").read_text(encoding="utf-8")
    assert cap.splitlines()[0].startswith("Что прячет") and cap.rstrip().endswith("#урбанистика #город #туалеты")
    from reels.textcheck import check_script
    assert not [i for i in check_script(p) if i.level == "error"]


def test_ig_retry_with_creative_feedback(llm, tmp_path, stickers):
    s = llm(reply(ig_broken()), reply(IG))
    D.draft("тема", tmp_path, url=s.url, model="m", format="question", hooks=3)
    assert len(s.requests) == 2
    assert "Формат задан: **question" in s.requests[0]["messages"][0]["content"]
    fix = s.requests[1]["messages"][2]["content"]
    assert "медленное начало «привет»" in fix
    assert "якорь lines[0].at: «кабинка»" in fix
    assert "текст в кадре «Город сжимается до пары кварталов вокруг дома»" in fix
    assert "нет призыва" in fix and "3 крючка(-ов) первыми" in fix


def test_validate_reel_structure(tmp_path, stickers):
    assert D.validate_reel(IG, tmp_path, format="auto", hooks=3) == []
    two = json.loads(json.dumps(IG))
    del two["blocks"][2]
    probs = D.validate_reel(two, tmp_path, format="auto", hooks=3)
    assert any("крючки: первыми идут 3" in p for p in probs)
    assert D.validate_reel(two, tmp_path, hooks=2) == []
    wrong = json.loads(json.dumps(IG))
    wrong["blocks"][3], wrong["blocks"][6] = wrong["blocks"][6], wrong["blocks"][3]
    assert any("тело ролика по формату question" in p for p in D.validate_reel(wrong, tmp_path, hooks=3))
    nofmt = dict(IG, format="опера")
    assert any("нет формата «опера»" in p for p in D.validate_reel(nofmt, tmp_path, format="auto", hooks=3))
    static = json.loads(json.dumps(IG))
    static["blocks"][4]["scene"] = st("1 из 5")
    static["blocks"][4]["text"] += " Это значит, что город кончается у ближайшей уборной."
    assert any("план 1" in p and "дольше 4" in p for p in D.validate_reel(static, tmp_path, hooks=3))


def test_ig_downgrade_without_subset_edits(tmp_path, stickers):
    data = D.reel_dict(IG, hooks=3, features={"shots": False, "subset_edits": False, "brand": False},
                       brand={"name": "ГРАД"})
    assert [b["role"] for b in data["blocks"]][:2] == ["hook", "context"] and len(data["blocks"]) == 6
    assert data["edits"] == {"main": "123456"} and "brand" not in data
    merged = D.reel_dict(IG, hooks=3, features={"shots": True, "subset_edits": True, "merges_edits": True})
    assert merged["edits"] == {"linear": "145678", "hook-b": "245678", "hook-c": "345678"}
    assert all("shots" not in b["scene"] for b in data["blocks"])
    full = D.reel_dict(IG, hooks=3, brand={"name": "ГРАД"}, features={"shots": True, "subset_edits": True,
                                                                       "brand": True})
    assert full["brand"] == {"name": "ГРАД"}


def test_unknown_format_fails_before_llm(tmp_path):
    with pytest.raises(KeyError, match="нет формата"):
        D.draft("тема", tmp_path, url="http://127.0.0.1:9/v1", model="m", format="опера")
