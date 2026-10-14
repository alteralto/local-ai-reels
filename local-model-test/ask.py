"""Запрос к локальной модели в LM Studio: текст + (необязательно) картинки. У модели нет доступа ни к чему, кроме ответа."""
import base64
import json
import sys
import time
import urllib.request

URL = "http://localhost:1234/v1/chat/completions"
MODEL = "qwen/qwen3-vl-4b"


def ask(prompt, images=(), max_tokens=1500, temperature=0.2):
    content = [{"type": "text", "text": prompt}]
    for path in images:
        b64 = base64.b64encode(open(path, "rb").read()).decode()
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
    body = {"model": MODEL, "messages": [{"role": "user", "content": content}],
            "max_tokens": max_tokens, "temperature": temperature}
    req = urllib.request.Request(URL, json.dumps(body).encode(), {"Content-Type": "application/json"})
    t = time.time()
    out = json.load(urllib.request.urlopen(req, timeout=600))
    return out["choices"][0]["message"]["content"], round(time.time() - t, 1), out.get("usage", {})


if __name__ == "__main__":
    QA = ("Это кадр вертикального ролика 1080×1920 (синяя чертёжная графика, крупный заголовок, субтитры внизу). "
          "Проверь вёрстку как строгий редактор. Ищи: текст поверх графики или другого текста, текст, обрезанный "
          "краем кадра, налезающие друг на друга строки, нечитаемые надписи. Полупрозрачные слова в субтитрах — это "
          "норма (подсветка по мере речи). Ответь строго JSON: "
          '{"ok": true/false, "defects": ["что и где"]}')
    for name in sys.argv[1:]:
        text, sec, usage = ask(QA, [name])
        print(f"== {name}  ({sec} с)\n{text}\n", flush=True)
