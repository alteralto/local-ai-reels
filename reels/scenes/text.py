"""Текстовые шаблоны: headline, stat, quote, list, counter."""
from __future__ import annotations

import re

from .base import SceneCtx, SceneDoc, SceneOut, esc, num, rich, scene
from .common import T, as_lines, fit, fmt_num, kick_js, kicker, plain, reveal, times

BOTTOM_LIST = 1400


def _big_lines(ctx: SceneCtx, lines: list[str], top: float, size: int, cls: str = "l") -> tuple[str, str, float]:
    """Строки .big одна под другой. Возвращает (html, css, низ последней строки)."""
    h, css, y = [], [], top
    lh = round(size * 1.06)
    for i, ln in enumerate(lines):
        h.append(f'<div class="big {cls}{i}"><span>{rich(ln)}</span></div>')
        css.append(f"{ctx.sel} .{cls}{i}{{top:{y:.0f}px;font-size:{size}px;height:{lh + 8}px}}")
        y += lh
    return "".join(h), "\n".join(css), y


@scene("headline", SceneDoc(
    "Крупный заголовок в 1–3 строки с надзаголовком; строки выезжают снизу по якорям. Можно добавить подпись и призыв.",
    {"lines": "список строк (1–3) или строка с \\n; *слово* — акцентом. Обязательно",
     "kicker": "надзаголовок (моно, капслок); необязательно",
     "at": "якорь первой строки или список якорей по строкам; по умолчанию 0.3 с и шаг 0.45 с",
     "sub": "мелкая подпись под заголовком (моно); необязательно",
     "sub_at": "якорь подписи; по умолчанию после последней строки",
     "cta": "призыв в конце (моно, акцент, со стрелкой →); необязательно",
     "cta_at": "якорь призыва; по умолчанию «70%»",
     "cta_always": "показывать призыв, даже если блок не последний в монтаже (по умолчанию — только в последнем)",
     "top": "верх заголовка, px; по умолчанию — по центру зоны 260…1450",
     "size": "наибольший кегль, px; по умолчанию 112 (длинные строки уменьшаются сами)"},
    {"kicker": "генплан · радиусы поводка", "lines": ["Генплан без туалетов —", "*карта с поводком*."],
     "at": ["генплан", "карта с поводком"], "cta": "отправь тому, кто рисует ваш город", "cta_at": "отправь"}))
def headline(ctx: SceneCtx, p: dict) -> SceneOut:
    lines = as_lines(p.get("lines") or p.get("text"))
    if not lines:
        raise ValueError("нужен lines")
    size = min(fit(ln, float(p.get("size", 112))) for ln in lines)
    lh = round(size * 1.06)
    sub, cta = p.get("sub"), p.get("cta")
    if cta and not ctx.last and not p.get("cta_always"):  # в монтаже «с конца» призыв в начале ролика неуместен
        cta = None
    height = lh * len(lines) + (40 + 50 * len(as_lines(sub)) if sub else 0) + (110 if cta else 0)
    top = float(p["top"]) if p.get("top") is not None else max(260, round(820 - height / 2))
    html, css, y = _big_lines(ctx, lines, top, size)
    ts = times(ctx, p.get("at"), len(lines))
    js = kick_js(p) + "".join(reveal(f".l{i}", x) for i, x in enumerate(ts))
    if sub:
        html += f'<div class="sub mono">{rich(sub)}</div>'
        css += f"\n{ctx.sel} .sub{{position:absolute;left:80px;right:80px;top:{y + 40:.0f}px;font-size:34px;line-height:1.4;opacity:.85}}"
        js += f'tl.fromTo(S+" .sub",{{opacity:0,y:16}},{{opacity:.85,y:0,duration:.35}},{T(ctx.at(p.get("sub_at"), ts[-1] + .5))});\n'
        y += 40 + 50 * len(as_lines(sub))
    if cta:
        cs = fit("→ " + str(cta), 36, k=.62, lo=24)
        html += f'<div class="cta mono">→ {esc(cta)}</div>'
        css += (f"\n{ctx.sel} .cta{{position:absolute;left:80px;right:80px;top:{min(y + 60, 1400):.0f}px;"
                f"font-size:{cs}px;color:var(--accent)}}")
        js += (f'tl.fromTo(S+" .cta",{{opacity:0,y:30}},{{opacity:1,y:0,duration:.45,ease:"power3.out"}},'
               f'{T(ctx.at(p.get("cta_at"), ctx.at("70%")))});\n')
    return SceneOut(kicker(p) + html, css, js)


@scene("stat", SceneDoc(
    "Одно крупное число или доля («*1* из 5», «43 %») с подписью; число выезжает по якорю, под ним растёт линия.",
    {"value": "число/текст крупно, *часть* — акцентом. Обязательно",
     "label": "подпись под числом (что это за число); необязательно",
     "kicker": "надзаголовок; необязательно",
     "at": "якорь появления числа; по умолчанию 0.3 с",
     "top": "верх числа, px; по умолчанию — по центру зоны",
     "size": "наибольший кегль числа, px; по умолчанию 240"},
    {"kicker": "RSPH · 2019", "value": "*43* %", "label": "людей с болезнями, требующими\nчастого туалета",
     "at": "двоих из пяти"}))
def stat(ctx: SceneCtx, p: dict) -> SceneOut:
    value = p.get("value")
    if value is None or str(value) == "":
        raise ValueError("нужен value")
    size = fit(value, float(p.get("size", 240)), k=.62, lo=80)
    label = p.get("label")
    ls = fit(label, 46, lo=28) if label else 0
    height = size * 1.1 + 80 + (len(as_lines(label)) * ls * 1.25 if label else 0)
    top = float(p["top"]) if p.get("top") is not None else max(300, round(830 - height / 2))
    t0 = ctx.at(p.get("at"), .3)
    html = f'<div class="big val"><span>{rich(value)}</span></div><div class="rule"></div>'
    rule_y = top + size * 1.08 + 20
    css = (f"{ctx.sel} .val{{top:{top:.0f}px;font-size:{size}px;line-height:1.05;height:{size * 1.12:.0f}px}}\n"
           f"{ctx.sel} .rule{{position:absolute;left:80px;width:920px;top:{rule_y:.0f}px;height:8px;"
           f"background:var(--accent);transform-origin:0 50%}}")
    js = kick_js(p) + reveal(".val", t0, .5)
    js += f'tl.fromTo(S+" .rule",{{scaleX:0}},{{scaleX:1,duration:.6,ease:"power3.out"}},{T(t0 + .25)});\n'
    if label:
        html += f'<div class="label">{rich(label)}</div>'
        css += (f"\n{ctx.sel} .label{{position:absolute;left:80px;right:80px;top:{rule_y + 60:.0f}px;"
                f"font-size:{ls}px;font-weight:700;line-height:1.25}}")
        js += f'tl.fromTo(S+" .label",{{opacity:0,y:20}},{{opacity:1,y:0,duration:.4}},{T(t0 + .45)});\n'
    return SceneOut(kicker(p) + html, css, js)


@scene("quote", SceneDoc(
    "Цитата: крупная кавычка, текст проявляется по словам, ниже автор.",
    {"text": "текст цитаты, *слово* — акцентом. Обязательно",
     "author": "автор или источник; необязательно",
     "kicker": "надзаголовок; необязательно",
     "at": "якорь начала проявления; по умолчанию 0.3 с",
     "duration": "за сколько секунд проявится весь текст; по умолчанию по 0.07 с на слово, не больше 2.5"},
    {"text": "Город кончается там, *где нет туалета*.", "author": "RSPH, 2019", "at": "мы меряем город"}))
def quote(ctx: SceneCtx, p: dict) -> SceneOut:
    text = str(p.get("text") or "")
    if not text.strip():
        raise ValueError("нужен text")
    n = len(plain(text))
    size = int(max(40, min(84, (920 * 760 / (n * .6 * 1.25)) ** .5)))
    text = re.sub(r"\*(.+?)\*", lambda m: " ".join(f"*{x}*" for x in m.group(1).split()), text)
    words = [f'<span class="w">{rich(w)}</span>' for w in text.split()]
    h = (n * .55 * size / 920 + 1) * size * 1.15 + (90 if p.get("author") else 0)
    qt = max(480, round(860 - h / 2))
    t0 = ctx.at(p.get("at"), .3)
    dur = float(p.get("duration", min(2.5, .07 * len(words))))
    html = (f'<div class="qm">“</div><div class="qbox"><div class="qt">{" ".join(words)}</div>'
            + (f'<div class="qa mono">— {esc(p["author"])}</div>' if p.get("author") else "") + "</div>")
    css = (f"{ctx.sel} .qm{{position:absolute;left:66px;top:{qt - 260}px;font-size:300px;font-weight:800;line-height:.8;"
           f"color:var(--accent);height:240px}}\n"
           f"{ctx.sel} .qbox{{position:absolute;left:80px;right:80px;top:{qt}px}}\n"
           f"{ctx.sel} .qt{{font-size:{size}px;font-weight:800;line-height:1.15;letter-spacing:-.02em}}\n"
           f"{ctx.sel} .qt .w{{display:inline-block}}\n"
           f"{ctx.sel} .qa{{margin-top:48px;font-size:34px;opacity:.8}}")
    js = kick_js(p)
    js += f'tl.fromTo(S+" .qm",{{opacity:0,scale:.6,transformOrigin:"0% 30%"}},{{opacity:1,scale:1,duration:.4,ease:"back.out(2)"}},{T(t0 - .2)});\n'
    js += (f'tl.fromTo(S+" .qt .w",{{opacity:.12}},{{opacity:1,duration:.2,stagger:{num(dur / max(1, len(words)))}}},'
           f'{T(t0)});\n')
    if p.get("author"):
        js += f'tl.fromTo(S+" .qa",{{opacity:0,y:16}},{{opacity:.8,y:0,duration:.4}},{T(t0 + dur + .1)});\n'
    return SceneOut(kicker(p) + html, css, js)


@scene("list", SceneDoc(
    "Список из 2–5 пунктов: появляются по одному (номера 01, 02… или галочки).",
    {"items": "список строк или {text, at}; *слово* — акцентом. Обязательно",
     "style": "number | check | dot; по умолчанию number",
     "kicker": "надзаголовок; необязательно",
     "title": "строка заголовка над списком; необязательно",
     "at": "якорь первого пункта (дальше шаг 0.6 с) или список якорей; у пункта свой at важнее"},
    {"kicker": "чем меряют город", "title": "Город меряют:",
     "items": [{"text": "километрами", "at": "километрами"}, {"text": "станциями метро", "at": "станциями"},
               {"text": "*ближайшей уборной*", "at": "уборной"}], "style": "check"}))
def list_(ctx: SceneCtx, p: dict) -> SceneOut:
    raw = p.get("items") or []
    if not raw:
        raise ValueError("нужен items")
    items = [x if isinstance(x, dict) else {"text": x} for x in raw]
    ts = times(ctx, p.get("at"), len(items), .4, .6)
    ts = [ctx.at(it.get("at"), d) for it, d in zip(items, ts)]
    style = p.get("style", "number")
    top = 300
    html, css, js = kicker(p), [], kick_js(p)
    if p.get("title"):
        tsz = fit(p["title"], 96)
        html += f'<div class="big ttl"><span>{rich(p["title"])}</span></div>'
        css.append(f"{ctx.sel} .ttl{{top:260px;font-size:{tsz}px;height:{tsz * 1.12:.0f}px}}")
        js += reveal(".ttl", max(0, min(.3, ts[0] - .3)))
        top = 260 + tsz * 1.1 + 70
    longest = max(len(plain(it.get("text", ""))) for it in items)
    row = min(230, (BOTTOM_LIST - top) / len(items))
    size = int(max(36, min(72, row / 2.6 if longest * .6 * 72 > 800 else row / 1.5, 800 * 2 / (longest * .6))))
    for i, it in enumerate(items):
        mark = {"number": f"{i + 1:02d}", "check": "✓", "dot": "●"}.get(style, f"{i + 1:02d}")
        html += (f'<div class="it it{i}"><div class="mk mono">{mark}</div>'
                 f'<div class="tx">{rich(it.get("text", ""))}</div></div>')
        css.append(f"{ctx.sel} .it{i}{{top:{top + i * row:.0f}px}}")
        js += (f'tl.fromTo(S+" .it{i}",{{opacity:0,x:-40}},{{opacity:1,x:0,duration:.4,ease:"power3.out"}},{T(ts[i])})'
               f'.fromTo(S+" .it{i} .mk",{{scale:0}},{{scale:1,duration:.35,ease:"back.out(3)"}},{T(ts[i] + .05)});\n')
    css.append(f"{ctx.sel} .it{{position:absolute;left:80px;right:80px;display:flex;gap:34px;align-items:baseline}}\n"
               f"{ctx.sel} .it .mk{{flex:0 0 80px;font-size:{max(30, size * .7):.0f}px;color:var(--accent);font-weight:600}}\n"
               f"{ctx.sel} .it .tx{{font-size:{size}px;font-weight:800;line-height:1.1;letter-spacing:-.02em}}")
    return SceneOut(html, "\n".join(css), js)


@scene("counter", SceneDoc(
    "Число, которое «набегает» от from до to (счётчик), с подписью.",
    {"to": "конечное значение. Обязательно",
     "from": "начальное значение; по умолчанию 0",
     "prefix": "текст перед числом («≈», «$»); необязательно",
     "suffix": "текст после числа («%», « км»); необязательно",
     "decimals": "знаков после запятой; по умолчанию 0",
     "label": "подпись под числом; необязательно",
     "kicker": "надзаголовок; необязательно",
     "at": "якорь старта счёта; по умолчанию 0.3 с",
     "duration": "длительность счёта, с; по умолчанию 1.4"},
    {"kicker": "Великобритания · 2019", "from": 0, "to": 20, "suffix": " %",
     "label": "выходят из дома реже, чем хотят", "at": "каждый пятый"}))
def counter(ctx: SceneCtx, p: dict) -> SceneOut:
    if p.get("to") is None:
        raise ValueError("нужен to")
    a, b = float(p.get("from", 0)), float(p["to"])
    d = int(p.get("decimals", 0))
    if not 0 <= d <= 4:
        raise ValueError("decimals: от 0 до 4")
    t0, dur = ctx.at(p.get("at"), .3), float(p.get("duration", 1.4))
    pre, suf = str(p.get("prefix", "")), str(p.get("suffix", ""))
    size = fit(pre + fmt_num(b, d) + suf, 240, k=.62, lo=80)
    ls = fit(p.get("label") or "", 48, lo=28)
    top = max(300, round(830 - (size * 1.1 + (50 + ls * 1.25 * len(as_lines(p.get("label"))) if p.get("label") else 0)) / 2))
    html = (f'<div class="cnt"><span class="pre">{esc(pre)}</span><span class="cv">{fmt_num(a, d)}</span>'
            f'<span class="suf">{esc(suf)}</span></div>')
    css = (f"{ctx.sel} .cnt{{position:absolute;left:80px;right:60px;top:{top}px;font-size:{size}px;font-weight:800;"
           f"line-height:1.05;letter-spacing:-.03em;white-space:nowrap;font-variant-numeric:tabular-nums}}\n"
           f"{ctx.sel} .cnt .cv{{color:var(--accent)}}\n"
           f"{ctx.sel} .cnt .pre,{ctx.sel} .cnt .suf{{white-space:pre}}")
    js = kick_js(p)
    js += f'tl.fromTo(S+" .cnt",{{opacity:0,y:40}},{{opacity:1,y:0,duration:.4,ease:"power3.out"}},{T(t0 - .15)});\n'
    js += ("{const el=document.querySelector(S+\" .cv\"), o={v:" + num(a) + "};\n"
           " const f=()=>{const s=o.v.toFixed(" + str(d) + ").split(\".\");"
           "s[0]=s[0].replace(/\\B(?=(\\d{3})+(?!\\d))/g,\"\\u2009\");el.textContent=s.join(\",\");};\n"
           f" tl.fromTo(o,{{v:{num(a)}}},{{v:{num(b)},duration:{num(dur)},ease:\"power2.out\",onUpdate:f}},{T(t0)}); f();}}\n")
    if p.get("label"):
        html += f'<div class="label">{rich(p["label"])}</div>'
        css += (f"\n{ctx.sel} .label{{position:absolute;left:80px;right:80px;top:{top + size * 1.1 + 50:.0f}px;"
                f"font-size:{ls}px;font-weight:700;line-height:1.25}}")
        js += f'tl.fromTo(S+" .label",{{opacity:0,y:20}},{{opacity:1,y:0,duration:.4}},{T(t0 + .3)});\n'
    return SceneOut(kicker(p) + html, css, js)
