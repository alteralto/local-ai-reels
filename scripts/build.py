"""Пример сюжета «Поводок»: вертикальная композиция HyperFrames из ОДНОГО мастер-файла озвучки.

python build.py <порядок> <папка>     например: python build.py 1234 reel-linear
Порядок блоков меняет только раскладку кусков мастер-аудио на таймлайне (data-media-start).
Под свой сюжет менять CUTS, SCRIPT, SCENES (SVG и текст сцен), ANIM (GSAP), подпись источника и финальную карточку.
Рядом должны лежать captext.py, voice/voice.wav, voice/words.json и (необязательно) music/music.wav.
Субтитры фразами, переходы между блоками и полоска прогресса — из polish.py (лежит рядом).
"""
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).parent
ORDER = [int(c) for c in sys.argv[1]]
OUT = HERE / sys.argv[2]

CUTS = [0.0, 4.2, 11.45, 17.6, 22.4]   # середины пауз по пословным таймкодам Whisper
GAP, TAIL = 0.12, 1.5
from captext import aligned  # noqa: E402
from polish import chunk_captions, POLISH_CSS, polish_js, whoosh_wav  # noqa: E402
INK, ACCENT = "#e8f0ff", "#ffd23f"
SCRIPT = ("У каждого горожанина есть невидимый поводок, и длину ему отмерил город. "
          "Королевское общество здравоохранения Британии выяснило: каждый пятый выходит из дома реже, чем хочет, — боится не найти туалет. "
          "Мы меряем город километрами и станциями метро, а у двоих из пяти больных он кончается у ближайшей уборной. "
          "Генплан без туалетов — это карта с поводком: отправь тому, кто рисует ваш город.")
words = aligned(json.loads((HERE / "voice/words.json").read_text(encoding="utf-8")), SCRIPT)

place, t = {}, 0.0
for b in ORDER:
    length = CUTS[b] - CUTS[b - 1]
    place[b] = (round(t, 3), round(length, 3))
    t += length + GAP
TOTAL = round(t - GAP + TAIL, 2)


# квартальная сетка: улицы через 100 и пара диагоналей — общая подложка карт
GRID = "".join(f'<line x1="{x}" y1="0" x2="{x}" y2="900"/><line x1="0" y1="{x}" x2="900" y2="{x}"/>' for x in range(50, 900, 100)) + \
    '<line x1="0" y1="820" x2="900" y2="120"/><line x1="0" y1="160" x2="640" y2="900"/>'
WC = [(250, 250), (650, 300), (450, 650), (180, 720)]
METRO = "M40 520 C 220 470, 330 380, 450 420 S 700 560, 860 380"
STATIONS = [(40, 520), (250, 445), (450, 420), (650, 500), (860, 380)]


def person(x, cls=""):
    return f'<g class="fig {cls}" transform="translate({x},0)"><circle cx="40" cy="40" r="28"/><path d="M0 160 Q0 82 40 82 Q80 82 80 160 Z"/></g>'


SCENES = {
    1: f"""
      <div class="kicker mono">город · радиус свободы</div>
      <svg class="map m1" viewBox="0 0 900 900">
        <g class="grid">{GRID}</g>
        <circle class="radius" cx="250" cy="250" r="0"/>
        <line class="leash" x1="250" y1="250" x2="470" y2="430"/>
        <g class="wc"><rect x="226" y="226" width="48" height="48" rx="8"/><text x="250" y="259" text-anchor="middle">WC</text></g>
        <circle class="me" cx="470" cy="430" r="18"/>
      </svg>
      <div class="big b1a"><span>Невидимый</span></div>
      <div class="big b1b"><span><em>поводок</em>.</span></div>""",
    2: f"""
      <div class="kicker mono">RSPH · Taking the P*** · май 2019</div>
      <div class="big stat"><span><em>1</em> из 5</span></div>
      <svg class="people" viewBox="0 0 560 220">
        {person(0)}{person(115)}{person(230)}{person(345)}{person(460, "odd")}
        <path class="house" d="M440 200 V95 L500 40 L560 95 V200 Z"/>
      </svg>
      <div class="note mono">выходит из дома реже, чем хочет —<br>боится не найти туалет</div>""",
    3: f"""
      <div class="kicker mono">у людей с болезнями, требующими частого туалета, — 43 %</div>
      <svg class="map m3" viewBox="0 0 900 900">
        <defs><mask id="reach"><rect width="900" height="900" fill="white"/>
          {''.join(f'<circle class="hole" cx="{x}" cy="{y}" r="0" fill="black"/>' for x, y in WC)}</mask></defs>
        <g class="grid">{GRID}</g>
        <path class="metro" d="{METRO}"/>
        {''.join(f'<circle class="stn" cx="{x}" cy="{y}" r="14"/>' for x, y in STATIONS)}
        <rect class="dim" width="900" height="900" mask="url(#reach)"/>
        {''.join(f'<circle class="ring" cx="{x}" cy="{y}" r="0"/>' for x, y in WC)}
        {''.join(f'<g class="wc"><rect x="{x-22}" y="{y-22}" width="44" height="44" rx="8"/><text x="{x}" y="{y+9}" text-anchor="middle">WC</text></g>' for x, y in WC)}
      </svg>
      <div class="big b3"><span><em>2 из 5</em>: город = круги</span></div>""",
    4: f"""
      <div class="kicker mono">генплан · радиусы поводка</div>
      <svg class="map m4" viewBox="0 0 900 900">
        <g class="grid">{GRID}</g>
        <rect class="zone z1" x="60" y="60" width="280" height="280"/><rect class="zone z2" x="560" y="60" width="280" height="380"/>
        <rect class="zone z3" x="60" y="460" width="380" height="380"/><rect class="zone z4" x="560" y="560" width="280" height="280"/>
        {''.join(f'<circle class="lr" cx="{x}" cy="{y}" r="150"/>' for x, y in WC)}
      </svg>
      <div class="big b4a"><span>Генплан без туалетов —</span></div>
      <div class="big b4b"><span><em>карта с поводком</em>.</span></div>
      <div class="cta mono">→ отправь тому, кто рисует ваш город</div>""",
}

ANIM = {
    1: """
      tl.fromTo(S+" .kicker",{opacity:0,y:-20},{opacity:1,y:0,duration:.35},t+.05)
        .fromTo(S+" .grid",{opacity:0},{opacity:1,duration:.6},t+.05)
        .fromTo(S+" .wc",{opacity:0,scale:.4,transformOrigin:"250px 250px"},{opacity:1,scale:1,duration:.35,ease:"back.out(3)"},t+.4)
        .fromTo(S+" .me",{opacity:0},{opacity:1,duration:.3},t+.6)
        .fromTo(S+" .b1a span",{yPercent:110},{yPercent:0,duration:.45,ease:"expo.out"},t+1.5)
        .fromTo(S+" .leash",{strokeDashoffset:290},{strokeDashoffset:0,duration:.5,ease:"power2.out"},t+2.1)
        .fromTo(S+" .b1b span",{yPercent:110},{yPercent:0,duration:.45,ease:"expo.out"},t+2.15)
        .fromTo(S+" .radius",{attr:{r:0}},{attr:{r:290},duration:.9,ease:"power2.out"},t+2.7)
        .to(S+" .me",{attr:{cx:520,cy:470},duration:.25,yoyo:true,repeat:1,ease:"power1.inOut"},t+3.5);""",
    2: """
      tl.fromTo(S+" .kicker",{opacity:0,y:-20},{opacity:1,y:0,duration:.35},t+.05)
        .fromTo(S+" .fig",{opacity:0,y:30},{opacity:1,y:0,duration:.35,stagger:.08,ease:"power3.out"},t+.3)
        .fromTo(S+" .stat span",{yPercent:110},{yPercent:0,duration:.5,ease:"expo.out"},t+3.1)
        .to(S+" .odd",{fill:"#ffd23f",duration:.25},t+3.2)
        .fromTo(S+" .note",{opacity:0},{opacity:1,duration:.35},t+3.6)
        .fromTo(S+" .house",{strokeDashoffset:700},{strokeDashoffset:0,duration:.6,ease:"power2.inOut"},t+5.8)
        .to(S+" .odd",{opacity:.55,duration:.4},t+6.2);""",
    3: """
      tl.fromTo(S+" .kicker",{opacity:0,y:-20},{opacity:1,y:0,duration:.35},t+.05)
        .fromTo(S+" .grid",{opacity:0},{opacity:1,duration:.5},t+.05)
        .fromTo(S+" .metro",{strokeDashoffset:1000},{strokeDashoffset:0,duration:1.2,ease:"power1.inOut"},t+1.1)
        .fromTo(S+" .stn",{opacity:0,scale:0,transformOrigin:"center"},{opacity:1,scale:1,duration:.25,stagger:.12,ease:"back.out(3)"},t+1.9)
        .fromTo(S+" .wc",{opacity:0},{opacity:1,duration:.25,stagger:.08},t+3.0)
        .fromTo(S+" .hole",{attr:{r:900}},{attr:{r:130},duration:1.2,ease:"power2.inOut"},t+3.4)
        .fromTo(S+" .ring",{attr:{r:900},opacity:0},{attr:{r:130},opacity:1,duration:1.2,ease:"power2.inOut"},t+3.4)
        .fromTo(S+" .dim",{opacity:0},{opacity:.82,duration:.6},t+3.4)
        .fromTo(S+" .b3 span",{yPercent:110},{yPercent:0,duration:.5,ease:"expo.out"},t+4.4);""",
    4: """
      tl.fromTo(S+" .kicker",{opacity:0,y:-20},{opacity:1,y:0,duration:.35},t+.05)
        .fromTo(S+" .grid",{opacity:0},{opacity:1,duration:.4},t+.05)
        .fromTo(S+" .zone",{opacity:0,scale:.92,transformOrigin:"center"},{opacity:1,scale:1,duration:.4,stagger:.07},t+.15)
        .fromTo(S+" .b4a span",{yPercent:110},{yPercent:0,duration:.45,ease:"expo.out"},t+.3)
        .fromTo(S+" .lr",{opacity:0,attr:{r:0}},{opacity:1,attr:{r:150},duration:.6,stagger:.1,ease:"back.out(1.6)"},t+1.5)
        .fromTo(S+" .b4b span",{yPercent:110},{yPercent:0,duration:.45,ease:"expo.out"},t+1.85)
        .fromTo(S+" .cta",{opacity:0,y:30},{opacity:1,y:0,duration:.45,ease:"power3.out"},t+3.0);""",
}

scene_html, anim_js, audio_html = [], [], []
for b in ORDER:
    start, length = place[b]
    scene_html.append(f"""
    <div id="b{b}" class="scene clip" data-start="{start}" data-duration="{length + GAP:.3f}" data-track-index="0">
      {SCENES[b]}
      <div class="cap2">{chunk_captions(words, CUTS[b - 1], CUTS[b])}</div>
    </div>""")
    anim_js.append(f"  {{ const S='#b{b}', t={start};{ANIM[b]}\n    capAnim2(S,t,{start + length:.3f}); outro(S,{start + length + GAP:.3f}); }}")
    if start > 0:  # звук перехода на склейке
        audio_html.append(f'<audio id="sfx-wh{b}" class="clip" src="whoosh.wav" data-start="{max(start - .14, 0):.3f}" '
                          f'data-duration="0.38" data-volume="0.35" data-track-index="5"></audio>')
    audio_html.append(f'<audio id="vo{b}" class="clip" src="voice.wav" data-start="{start}" data-duration="{length:.3f}" '
                      f'data-media-start="{CUTS[b - 1]}" data-volume="1" data-track-index="2"></audio>')

music = (f'<audio id="music" class="clip" src="music.wav" data-start="0" data-duration="{TOTAL}" data-volume="0.24" '
         f'data-fade-in="0.6" data-fade-out="1.6" data-track-index="3"></audio>') if (HERE / "music/music.wav").exists() else ""

html = f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=1080, height=1920" />
<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@500;700;800&family=IBM+Plex+Mono:wght@500;600&display=block" rel="stylesheet" />
<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  html, body {{ width:1080px; height:1920px; overflow:hidden; background:#123a7a; }}
  :root {{ --blue:#123a7a; --deep:#0b2350; --line:#e8f0ff; --y:#ffd23f; }}
  #root {{ position:relative; width:1080px; height:1920px; font-family:Manrope,"Segoe UI",sans-serif; color:var(--line); }}
  /* синька: сетка в тон фона */
  .scene {{ position:absolute; inset:0; overflow:hidden; background-color:var(--blue);
           background-image: linear-gradient(rgba(232,240,255,.06) 1px, transparent 1px), linear-gradient(90deg, rgba(232,240,255,.06) 1px, transparent 1px);
           background-size: 40px 40px; }}
  .mono {{ font-family:"IBM Plex Mono",Consolas,monospace; letter-spacing:.04em; }}
  .kicker {{ position:absolute; left:80px; right:80px; top:150px; font-size:28px; text-transform:uppercase; color:var(--y); line-height:1.35; }}
  .big {{ position:absolute; left:80px; right:60px; font-weight:800; font-size:96px; line-height:1.02; letter-spacing:-.03em; overflow:hidden; }}
  .big span {{ display:inline-block; }} .big em {{ font-style:normal; color:var(--y); }}
  .map {{ position:absolute; left:90px; width:900px; height:900px; overflow:visible; }}
  .map .grid line {{ stroke:var(--line); stroke-opacity:.22; stroke-width:3; }}
  .map .wc rect {{ fill:var(--line); }} .map .wc text {{ font:700 22px "IBM Plex Mono",monospace; fill:var(--blue); }}
  .m1 {{ top:520px; }}
  .m1 .radius {{ fill:rgba(255,210,63,.10); stroke:var(--y); stroke-width:4; stroke-dasharray:14 10; }}
  .m1 .leash {{ stroke:var(--y); stroke-width:6; stroke-dasharray:290; }}
  .m1 .me {{ fill:var(--y); }}
  .b1a {{ top:260px; }} .b1b {{ top:360px; }}
  .stat {{ top:300px; font-size:220px; }}
  .people {{ position:absolute; left:120px; top:640px; width:840px; height:330px; overflow:visible; }}
  .people .fig {{ fill:var(--line); }}
  .people .house {{ fill:none; stroke:var(--y); stroke-width:7; stroke-dasharray:700; stroke-linejoin:round; }}
  .note {{ position:absolute; left:80px; right:80px; top:1060px; font-size:36px; line-height:1.4; }}
  .m3 {{ top:300px; }}
  .m3 .metro {{ fill:none; stroke:#ff6b5b; stroke-width:12; stroke-dasharray:1000; stroke-linecap:round; }}
  .m3 .stn {{ fill:var(--line); stroke:#ff6b5b; stroke-width:6; }}
  .m3 .dim {{ fill:var(--deep); }}
  .m3 .ring {{ fill:none; stroke:var(--y); stroke-width:5; }}
  .b3 {{ top:1240px; font-size:84px; }}
  .m4 {{ top:280px; }}
  .m4 .zone {{ fill:rgba(232,240,255,.10); stroke:var(--line); stroke-width:3; }}
  .m4 .lr {{ fill:rgba(255,210,63,.12); stroke:var(--y); stroke-width:4; stroke-dasharray:12 9; }}
  .b4a {{ top:1210px; font-size:72px; }} .b4b {{ top:1290px; font-size:72px; }}
  .cta {{ position:absolute; left:80px; right:80px; top:1395px; font-size:32px; color:var(--y); }}
  .src {{ position:absolute; left:80px; right:80px; bottom:110px; font-size:21px; opacity:.6; }}
  #endcard .t {{ position:absolute; left:80px; right:80px; top:780px; font-size:76px; font-weight:800; letter-spacing:-.02em; line-height:1.08; }}
  #endcard .t em {{ font-style:normal; color:var(--y); }}
{POLISH_CSS(INK, ACCENT)}
</style>
</head>
<body>
<div id="root" data-composition-id="main" data-start="0" data-duration="{TOTAL}" data-width="1080" data-height="1920">
  <div id="prog"></div>
{''.join(scene_html)}
  <div id="endcard" class="scene clip" data-start="{TOTAL - TAIL:.3f}" data-duration="{TAIL}" data-track-index="1">
    <div class="t">Город кончается там,<br><em>где нет туалета.</em></div>
  </div>
  <div class="src mono">источник: RSPH, «Taking the P***: the decline of the great British public toilet», 2019</div>
  {''.join(audio_html)}
  {music}
</div>
<script>
  window.__timelines = window.__timelines || {{}};
  const tl = gsap.timeline({{ paused:true }});
{polish_js(ACCENT, INK)}
  tl.fromTo("#prog", {{scaleX:0}}, {{scaleX:1, duration:{TOTAL}, ease:"none"}}, 0);
{chr(10).join(anim_js)}
  tl.fromTo("#endcard .t", {{opacity:0, y:30}}, {{opacity:1, y:0, duration:.4}}, {TOTAL - TAIL + .05:.2f});
  window.__timelines["main"] = tl;
  tl.seek(0);
</script>
</body>
</html>
"""

OUT.mkdir(parents=True, exist_ok=True)
(OUT / "index.html").write_text(html, encoding="utf-8")
for src in ("voice/voice.wav",) + (("music/music.wav",) if music else ()):
    shutil.copy(HERE / src, OUT / Path(src).name)
whoosh_wav(str(OUT / "whoosh.wav"))
PROJECT = {"hyperframes.json": {"$schema": "https://hyperframes.heygen.com/schema/hyperframes.json",
                                "paths": {"blocks": "compositions", "components": "compositions/components", "assets": "assets"},
                                "media": {"autoProxy": True}},
           "meta.json": {"id": OUT.name, "name": OUT.name},
           "package.json": {"name": OUT.name, "private": True, "type": "module"}}
for name, data in PROJECT.items():
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
print(OUT, "total", TOTAL, "order", ORDER, "music" if music else "no music")
