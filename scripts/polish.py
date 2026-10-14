"""Оформление ролика поверх build.py: субтитры фразами с подсветкой слова, переходы между блоками,
полоска прогресса, звук перехода. Подключается в build.py сюжета:

    from polish import chunk_captions, POLISH_CSS, polish_js, whoosh_wav
"""
import re

import numpy as np
import scipy.io.wavfile as wavfile


WEAK = {"в", "во", "на", "с", "со", "к", "ко", "у", "о", "об", "и", "а", "но", "до", "из", "за", "по", "от", "не", "без",
        "для", "что", "как", "их", "его", "её", "ее", "это", "the", "a"}


def chunks(words, max_chars=26, max_words=5):
    """Режет слова блока на фразы для субтитров: по пунктуации или по длине строки.
    Фраза не заканчивается предлогом или союзом («…держит их в / городе») — такое слово уходит в следующую."""
    out, cur = [], []
    for w in words:
        cur.append(w)
        text = " ".join(x["w"] for x in cur)
        punct = re.search(r"[.,:;!?—–]$", w["w"])
        if punct and len(text) >= 10 or len(text) >= max_chars or len(cur) >= max_words:
            tail = []
            while not punct and len(cur) > 1 and cur[-1]["w"].lower() in WEAK:
                tail.insert(0, cur.pop())
            out.append(cur)
            cur = tail
    if cur:
        out.append(cur)
    return out


def chunk_captions(words, a, z):
    """HTML субтитров блока [a, z): фразы по 3–5 слов, у каждого слова data-s — секунды от начала блока."""
    block = [w for w in words if a <= w["s"] < z]
    html = []
    for ch in chunks(block):
        spans = " ".join(f'<span data-s="{w["s"] - a:.2f}">{w["w"]}</span>' for w in ch)
        html.append(f'<div class="chunk" data-s="{ch[0]["s"] - a:.2f}">{spans}</div>')
    return "".join(html)


def POLISH_CSS(ink, accent):
    """ink — цвет текста субтитров, accent — цвет текущего слова и полоски прогресса."""
    return f"""
  .cap2 {{ position:absolute; left:60px; right:60px; bottom:260px; height:170px; text-align:center; }}
  .cap2 .chunk {{ position:absolute; left:0; right:0; bottom:0; opacity:0; font-size:64px; font-weight:800;
                 line-height:1.12; letter-spacing:-.015em; color:{ink}; }}
  .cap2 .chunk span {{ display:inline-block; }}
  #prog {{ position:absolute; left:0; top:0; height:10px; width:1080px; background:{accent}; transform-origin:0 50%; z-index:50; }}
"""


def polish_js(accent, ink):
    """JS-функции: capAnim2(S, t, end) — фразы и подсветка; outro(S, t_end) — уход сцены перед склейкой."""
    return f"""
  function capAnim2(S, t, end) {{
    const ch = [...document.querySelectorAll(S+" .cap2 .chunk")];
    ch.forEach((c, i) => {{
      const s = t + parseFloat(c.dataset.s);
      const e = i + 1 < ch.length ? t + parseFloat(ch[i+1].dataset.s) : end;
      tl.fromTo(c, {{opacity:0, y:14}}, {{opacity:1, y:0, duration:.12, ease:"power2.out"}}, s);
      tl.set(c, {{opacity:0}}, e);  // старая фраза исчезает ровно в момент появления новой — без наложения
      const ws = [...c.querySelectorAll("span")];
      ws.forEach((w, j) => {{
        const ws_ = t + parseFloat(w.dataset.s);
        const we = j + 1 < ws.length ? t + parseFloat(ws[j+1].dataset.s) : e;
        tl.fromTo(w, {{color:"{ink}"}}, {{color:"{accent}", duration:.06}}, ws_);
        tl.to(w, {{color:"{ink}", duration:.12}}, we);
      }});
    }});
  }}
  function outro(S, tEnd) {{
    tl.to(S+" > :not(.cap2)", {{opacity:0, y:-24, duration:.16, ease:"power2.in"}}, tEnd - .16);
  }}
"""


def whoosh_wav(path, sr=48000, dur=.38, seed=7):
    """Короткий «вжух»: шум с плавающей полосой частот и мягкой огибающей."""
    rng = np.random.default_rng(seed)
    n = int(sr * dur)
    t = np.arange(n) / sr
    noise = rng.standard_normal(n)
    spec = np.fft.rfft(noise)
    freqs = np.fft.rfftfreq(n, 1 / sr)
    spec *= np.exp(-((freqs - 1800) / 1400) ** 2) + .15 * np.exp(-((freqs - 500) / 400) ** 2)
    x = np.fft.irfft(spec, n)
    env = np.sin(np.pi * np.clip(t / dur, 0, 1)) ** 2 * np.exp(-t * 2)
    x = x * env
    x /= np.abs(x).max()
    x *= .5
    wavfile.write(path, sr, np.stack([x, x], 1).astype(np.float32))
