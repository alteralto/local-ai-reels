import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BLOCKS = [
    "У каждого горожанина есть невидимый поводок, и длину ему отмерил город.",
    "Королевское общество здравоохранения Британии выяснило: каждый пятый выходит из дома реже, чем хочет, "
    "— боится не найти туалет.",
    "Мы меряем город километрами и станциями метро, а у двоих из пяти больных он кончается у ближайшей уборной.",
    "Генплан без туалетов — это карта с поводком: отправь тому, кто рисует ваш город.",
]


if os.environ.get("REELS_INTEGRATION") != "1" and not os.environ.get("REELS_GSAP"):
    # модульные тесты не ходят в сеть за GSAP: подставляем заглушку (интеграционные берут настоящий)
    _stub = Path(__file__).resolve().parent / "data" / "gsap-stub.js"
    _stub.parent.mkdir(exist_ok=True)
    if not _stub.exists():
        _stub.write_text("window.gsap = window.gsap || {};\n", encoding="utf-8")
    os.environ["REELS_GSAP"] = str(_stub)


def pytest_collection_modifyitems(config, items):
    if os.environ.get("REELS_INTEGRATION") == "1":
        return
    skip = pytest.mark.skip(reason="интеграционный тест: REELS_INTEGRATION=1")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def example_words():
    import json
    return json.loads((ROOT / "example/voice/words.json").read_text(encoding="utf-8"))
