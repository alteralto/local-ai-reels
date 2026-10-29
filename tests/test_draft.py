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
