"""Медиа-сцены: файлы проекта попадают в SceneOut.files, размеры картинок читаются без Pillow, видео — клип HyperFrames."""
import re
import struct
from pathlib import Path

import pytest

from reels import compose
from reels.scenes import REGISTRY, SceneCtx, SceneError, render_scene
from reels.scenes.media import image_size
from reels.textcheck import _anchors
from reels.themes import get_theme

ROOT = Path(__file__).resolve().parent.parent
SHOT = "app/desktop/screenshot.png"          # 1360×860
CLIP = "local-model-test/results/qwen3-vl-8b/reel-fixed.mp4"


def ctx(start: float = 10.0, project_dir: Path = ROOT, params: dict | None = None) -> SceneCtx:
    extra = [re.sub(r"@end|[+-]\d+(\.\d+)?$", "", a) for a in _anchors(params or {})]
    text = " ".join(["Вступление", "этапы", "дубли", "подтвердить", "готов", *extra, "конец."])
    words = [{"w": w, "s": round(i * .5, 2), "e": round(i * .5 + .4, 2)} for i, w in enumerate(text.split())]
    return SceneCtx(3, "b3", start, words[-1]["e"], words, text, get_theme("blueprint"), project_dir)


def test_image_size_headers(tmp_path):
    assert tuple(image_size(ROOT / SHOT)) == (1360, 860)
    gif = tmp_path / "a.gif"
    gif.write_bytes(b"GIF89a" + struct.pack("<HH", 33, 44) + b"\0" * 20)
    assert tuple(image_size(gif)) == (33, 44)
    svg = tmp_path / "a.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 480"></svg>')
    assert tuple(image_size(svg)) == (640, 480)
    jpg = tmp_path / "a.jpg"   # SOI, APP0, SOF0 (высота 120, ширина 90)
    jpg.write_bytes(b"\xff\xd8\xff\xe0\x00\x04ab\xff\xc0\x00\x0b\x08\x00\x78\x00\x5a\x01\x01\x11\x00\xff\xd9")
    assert tuple(image_size(jpg)) == (90, 120)


@pytest.mark.parametrize("name", ["image", "screen", "video"])
def test_files_are_copied_and_referenced(name):
    params = {"type": name, **REGISTRY[name][1].example}
    out = render_scene(ctx(params=params), params)
    assert len(out.files) == 1
    (fname, src), = out.files.items()
    assert Path(src).is_file() and fname.startswith("b3-")
    assert f'src="{fname}"' in out.html
    assert not compose.js_syntax_errors({"b3": out.js})


@pytest.mark.parametrize("name", ["image", "screen", "video"])
def test_missing_or_wrong_file(name):
    with pytest.raises(SceneError, match="нет файла"):
        render_scene(ctx(), {"type": name, "src": "nope/missing.png" if name != "video" else "nope/missing.mp4"})
    with pytest.raises(SceneError, match="нужен файл"):
        render_scene(ctx(), {"type": name, "src": "README.md"})


def test_bad_frame_and_enter():
    with pytest.raises(SceneError, match="frame"):
        render_scene(ctx(), {"type": "image", "src": SHOT, "frame": "tv"})
    with pytest.raises(SceneError, match="enter"):
        render_scene(ctx(), {"type": "image", "src": SHOT, "frame": "card", "enter": "spin"})


def test_screen_focus_cursor_highlight():
    p = {"type": "screen", "src": SHOT, "w": 920, "h": 860,
         "focus": [{"at": "этапы", "region": [300, 120, 600, 70], "zoom": 2},
                   {"at": "подтвердить", "region": [320, 430, 420, 70], "zoom": 9}],
         "cursor": [{"at": "дубли", "x": 400, "y": 300}, {"at": "подтвердить", "x": 445, "y": 465, "click": True}],
         "highlight": [{"at": "дубли", "region": [326, 264, 990, 46], "until": "готов", "dim": True}]}
    out = render_scene(ctx(), p)
    # обычный масштаб — скриншот закрывает окно 920×860: max(920/1360, 860/860) = 1; zoom ограничен 3
    assert "scale:2,duration" in out.js and "scale:3,duration" in out.js and "scale:9" not in out.js
    assert out.html.count('class="rp ') == 1 and 'class="cur"' in out.html and 'class="hl hl0"' in out.html
    # курсор приходит к точке в момент якоря: «подтвердить» — 4-е слово, 1.5 с
    m = re.search(r'tl\.fromTo\(S\+" \.cur",\{x:400,y:300\},\{x:445,y:465,duration:([\d.]+)[^)]*\},t\+([\d.]+)\)', out.js)
    assert m and float(m.group(1)) + float(m.group(2)) == pytest.approx(1.5)
    assert 'S+" .rp1"' in out.js and "t+1.5)" in out.js
    # главный объект в зоне 40…1040 × 160…1450
    left, top = (float(x) for x in re.search(r"\.med\{position:absolute;left:(-?\d+)px;top:(-?\d+)px", out.css).groups())
    assert 40 <= left and 160 <= top and top + 860 <= 1450


def test_tall_screenshot_scrolls_without_gaps(tmp_path):
    tall = tmp_path / "tall.png"   # 400×3000: только заголовок PNG, размеры читаются из IHDR
    tall.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + struct.pack(">II", 400, 3000) + b"\x08\x06\0\0\0")
    out = render_scene(ctx(project_dir=tmp_path), {"type": "screen", "src": "tall.png",
                                                    "focus": [{"at": "этапы", "region": [0, 2900, 400, 100], "zoom": 1}]})
    vw = 920.0
    s0 = vw / 400
    vh = float(re.search(r"\.vp\{position:relative;width:\d+px;height:(\d+)px", out.css).group(1))
    assert vh <= 1450 - 160
    y = float(re.search(r"\{x:[-\d.]+,y:([-\d.]+),scale:[\d.]+,duration", out.js).group(1))
    assert y == pytest.approx(vh - 3000 * s0, abs=1)        # прокрутка до самого низа, не дальше


def test_video_clip_attributes():
    out = render_scene(ctx(start=12.5), {"type": "video", "src": CLIP, "media_start": 1, "speed": 2})
    v = re.search(r"<video [^>]+>", out.html).group(0)
    assert 'id="b3-video"' in v and 'class="clip"' in v
    assert 'data-start="12.5"' in v                        # абсолютное время монтажа, а не от начала сцены
    assert 'data-media-start="1"' in v and 'data-playback-rate="2"' in v and " muted" in v
    loud = render_scene(ctx(), {"type": "video", "src": CLIP, "muted": False, "volume": .3})
    assert 'data-has-audio="true"' in loud.html and 'data-volume="0.3"' in loud.html and " muted" not in loud.html


def test_image_full_bleed_kenburns():
    out = render_scene(ctx(), {"type": "image", "src": SHOT, "kenburns": {"from": [1, 50, 50], "to": [1.3, 20, 30]}})
    assert re.search(r"\.med\{position:absolute;left:0px;top:0px;width:1080px;height:1920px", out.css)
    assert "scale:1.3," in out.js and 'ease:"sine.inOut"' in out.js
