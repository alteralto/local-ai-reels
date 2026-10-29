<h1 align="center">local-ai-reels</h1>

<p align="center"><b>Vertical 1080×1920 reels with voice-over, captions, motion graphics and music — on local models</b></p>

<p align="center">
  <a href="https://github.com/alteralto/local-ai-reels/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/alteralto/local-ai-reels/actions/workflows/ci.yml/badge.svg"></a>
  <a href="LICENSE"><img alt="License: Apache-2.0" src="https://img.shields.io/badge/license-Apache--2.0-blue.svg"></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-3776ab.svg">
  <img alt="Node 22+" src="https://img.shields.io/badge/node-22%2B-339933.svg">
</p>

<p align="center"><a href="README.md">Русская версия</a> · the narration, CLI messages and step-by-step guide are in Russian</p>

<p align="center"><img src="docs/media/hero.gif" width="320" alt="Sample reel: blueprint-style scenes, captions with the current word highlighted, transitions between blocks"></p>

**local-ai-reels** is a conveyor that turns a short script into a finished vertical video for Reels, Shorts and
TikTok. The voice is cloned from a sample, stress marks are placed in advance, every word gets a timestamp, and the
graphics are written as code (HTML + SVG + GSAP) and rendered to MP4 in headless Chrome. Everything runs on your own
machine with no paid APIs: a 25-second reel renders in about a minute on an RTX 3060-class GPU. One voice-over yields
several edits, and a human steps in only at approval gates — or not at all (`--yes`).

## Features

- **One file, one reel.** Script, scenes, voice, music and edits live in `reel.yaml`; the conveyor takes them all the
  way to `out/<slug>-<edit>.mp4` plus a `report.md`.
- **Voice cloning with correct stress.** F5-TTS reproduces the timbre and pace of a reference sample, RUAccent marks
  Russian stress, Whisper checks every take against the script — only a word-perfect take goes into the edit.
- **Word-level captions.** Phrases of 3–5 words with the current word highlighted, never broken after a preposition.
- **Graphics as code.** A library of scene templates (headline, stat, fraction, bars, compare, list, quote, counter,
  timeline, map) plus custom HTML/SVG scenes. Animation is anchored to the narrator's words: `at: "каждый пятый"`.
- **Themes:** `blueprint`, `night`, `paper`, `mono`, or your own accent color.
- **Many edits from one voice-over.** Blocks are reordered on the timeline, not re-synthesized.
- **Music and sound.** MusicGen background from a text prompt, synthesized whooshes on cuts, mix at −18 LUFS.
- **Caching and approval gates.** A stage is skipped when its inputs have not changed; at four gates the conveyor
  waits for a human.
- **Automated layout QA.** Before rendering, frames are checked through the DOM: overflow past the frame edge,
  overlaps, intrusion into the caption zone. After rendering: duration, loudness and a word-for-word ASR check of
  the narration over the music.
- **Script drafts from a local LLM** (LM Studio, Ollama — any OpenAI-compatible server) and **batch mode** for dozens
  of reels.

## How it works

<p align="center"><img src="docs/media/pipeline.svg" alt="Stages: reel.yaml → check → accent → voice → align → music → build → snapshot → render → finish → out/*.mp4; gates script, accents, take, frames"></p>

| Stage | What it does | Technology |
|---|---|---|
| `check` | word limit, blocks, scene parameters, source caption | — |
| `accent` | stress marks in the script, manual fixes from `voice.accents` | RUAccent `turbo3.1` |
| `voice` | reference voice, several takes, selection by ASR match | F5-TTS 1.1 + `Misha24-10/F5-TTS_RUSSIAN`, Piper, faster-whisper |
| `align` | per-word timestamps, block boundaries | faster-whisper `large-v3-turbo` |
| `music` | background from a prompt, loudness, dip check | MusicGen-small |
| `build` | one HyperFrames project per edit: scenes, captions, audio | HTML/SVG + GSAP 3.14 |
| `snapshot` | frames before rendering, layout QA, contact sheet | HyperFrames, Chrome |
| `render` | 1080×1920 MP4 | HyperFrames 0.8.75 |
| `finish` | poster in frame 0, measurements, ASR check of the final audio, report | ffmpeg, faster-whisper |

Gates: **script** (after `check`), **accents** (after `accent`), **take** (after `voice`), **frames** (after
`snapshot`). An approval is bound to the artifact's hash: change the script and you approve again. Details:
[docs/architecture.md](docs/architecture.md) (Russian).

## Quick start in 2 minutes

No GPU and no models needed: the examples use the bundled narration and music from `example/`
(`voice.engine: file`, `music.engine: file`). You need Python 3.10+, Node.js 22+ and ffmpeg on PATH.

```bash
git clone https://github.com/alteralto/local-ai-reels && cd local-ai-reels
pip install -e .
python -m reels run examples/povodok-templates --yes
```

The finished videos land in `examples/povodok-templates/out/` next to `report.md`. On first run `npx` downloads
HyperFrames. Without a GPU rendering is slower: about 2 minutes per edit on 4 CPU cores.

<p align="center"><img src="docs/media/terminal.gif" width="760" alt="Running python -m reels run examples/povodok --yes"></p>

Two examples share the same narration:

| Example | Shows |
|---|---|
| [`examples/povodok`](examples/povodok) | a faithful port of the original "Povodok" (invisible leash) reel with hand-written SVG scenes (`type: custom`) |
| [`examples/povodok-templates`](examples/povodok-templates) | the same text using library templates only — a reel without a single line of HTML |

## Your own reel

```bash
reels new my-reel --from examples/povodok-templates   # project skeleton
$EDITOR my-reel/reel.yaml                             # script, scenes, voice, edits
reels check my-reel                                   # check before synthesis
reels run my-reel                                     # stops at the "script" gate
reels approve my-reel script                          # approve and continue
reels run my-reel                                     # … accents → take → frames → done
```

A minimal `reel.yaml`:

```yaml
title: Невидимый поводок
theme: blueprint
source: "источник: RSPH, «Taking the P***», 2019"
blocks:
  - text: У каждого горожанина есть невидимый поводок, и длину ему отмерил город.
    scene: {type: headline, kicker: "город · радиус свободы", lines: ["Невидимый", "*поводок*."]}
  - text: Каждый пятый выходит из дома реже, чем хочет, — боится не найти туалет.
    scene: {type: fraction, total: 5, highlight: 1, value: "*1* из 5", at: "каждый пятый",
            label: "выходит из дома реже, чем хочет"}
  # …
edits: {linear: "1234", from-end: "4123", suspense: "1324"}
voice: {engine: f5, voices: [denis, dmitri]}
music: {engine: musicgen}
```

Useful `reels run` flags: `--yes` — autopilot, `--edit linear` — a single edit, `--from voice` — restart from a stage,
`--only snapshot` — one stage, `--until build` — stop after a stage, `--force` — ignore the cache.
`reels status my-reel` shows which stages are done and which gates are waiting.

## Script drafts from a local LLM

```bash
reels draft "why public toilets are disappearing from cities" -o drafts/toilets --facts facts.md
```

`reels draft` asks a local model (LM Studio, Ollama or any OpenAI-compatible server; `--url`, `--model` or
`REELS_LLM_URL`, `REELS_LLM_MODEL`) and writes a ready `reel.yaml`: four blocks "hook → fact → twist → punchline"
within the word limit, scenes from the template library only. Give facts as a file (`--facts`) and review the
draft — the `script` gate is waiting for it anyway. Why small models fill templates instead of drawing their own
scenes: see [local-model-test/](local-model-test/README.md).

## Scene templates

| Template | Use it for |
|---|---|
| `headline` | a large headline with an accent word |
| `stat` | one big number with a caption |
| `fraction` | "one in five", "2 out of 5" — a share shown with icons |
| `bars` | a bar chart |
| `compare` | before / after, us / them |
| `list` | items one by one |
| `quote` | a quote with its author |
| `counter` | a number counting up with the voice |
| `timeline` | a timeline |
| `map` | a city-block diagram with points and radii |
| `custom` | your own scene: HTML/SVG/JS files in the project |

Parameters and examples: [docs/scenes.md](docs/scenes.md); in the terminal: `reels scenes`.

## Many edits from one voice-over

<p align="center"><img src="docs/media/edits.gif" alt="The first seconds of three edits: linear, from-end, suspense"></p>

The voice is synthesized once. An edit is just the order of blocks: `linear: "1234"`, `from-end: "4123"`,
`suspense: "1324"`. Pieces of the master file are laid out on the timeline (`data-media-start` on `<audio>`); scenes,
captions and transitions follow automatically. Three versions for an A/B test cost one synthesis.

## Automated QA

<p align="center"><img src="docs/media/qa.png" width="720" alt="Real layout defects: a clipped headline and text over the map"></p>

These are real defective frames from [local-model-test/](local-model-test/README.md): none of the local models up to
27B that we tested noticed them. The conveyor finds such defects through the frame's DOM — no vision model needed —
and stops at the `frames` gate. For the human it builds a contact sheet, so you can review the reel before
rendering:

<p align="center"><img src="docs/media/frames.png" alt="Contact sheet: 8 frames of the reel before rendering"></p>

## Batch mode

```bash
reels batch projects/*/ --yes      # all projects in a row, summary at the end
reels status projects/*/           # what is done, what awaits approval
```

Without `--yes` every project runs up to its next gate and waits — approve them in bulk and run the batch again.

## Requirements and installation

| | Minimum | For voice and music |
|---|---|---|
| Python | 3.10+ | 3.10+ |
| Node.js | 22+ (HyperFrames via `npx`) | 22+ |
| ffmpeg | 6+ on PATH (or a folder in `FFMPEG_DIR`) | 6+ |
| GPU | not needed | NVIDIA with 8+ GB (tested on RTX 3060 12 GB) |

```bash
pip install -e .          # conveyor, templates, rendering, examples with bundled narration
pip install -e .[ml]      # + F5-TTS, RUAccent, faster-whisper, MusicGen (install CUDA torch first)
```

Models are downloaded from Hugging Face on first use; Piper voices (`ru_RU-denis-medium`, `ru_RU-dmitri-medium`) go to
`models/piper/`. HyperFrames 0.8.75 runs through `npx`, downloads itself on first use, telemetry is off.

If HyperFrames cannot download Chrome (HTTP 403 happens in some countries), point it to an installed Chromium-based
browser, e.g. Edge on Windows:

```bash
export HYPERFRAMES_BROWSER_PATH="C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
```

Paths for macOS and Linux are in the [troubleshooting section](docs/guide.md#частые-ошибки) of the guide.

## Manual mode

The conveyor grew out of a set of scripts, and they stay in the repository as a step-by-step reference:

- [`scripts/`](scripts) — `texts`, `accent`, `synth`, `words`, `captext`, `music_gen`, `sfx_lock`, `build`, `polish`,
  `hf.sh`, `finish.sh`: one command per stage;
- [docs/guide.md](docs/guide.md) — the detailed guide (Russian): installation, your own voice, stress marks, choosing
  takes, block boundaries, music, editing, poster, checks, troubleshooting; includes a section for an AI agent;
- [docs/guide.pdf](docs/guide.pdf) — the same guide as a single file with all script sources attached
  (`python tools/build_pdf.py` rebuilds it);
- [local-model-test/](local-model-test/README.md) — can small local models write scenes and review frames?
  (short answer: they fill templates fine, but cannot draw or review layout).

```bash
mkdir my-reel && cp scripts/* my-reel/ && cp -r example/voice example/music my-reel/
cd my-reel && python build.py 1234 reel-linear && cd reel-linear
bash ../hf.sh render -o renders/reel-linear.mp4 --fps 30 --quality looks
```

## Licenses

Code: Apache-2.0, see [LICENSE](LICENSE). Models are not part of the repository and have their own licenses: the
Russian F5-TTS weights (`Misha24-10/F5-TTS_RUSSIAN`) and MusicGen-small are **CC-BY-NC 4.0, non-commercial use
only**; Piper voices `denis` and `dmitri` are CC0. The narration and music in `example/` were made with these models,
so they are non-commercial too. For commercial work use Qwen3-TTS (Apache-2.0) and stock or your own music.
Details: [NOTICE.md](NOTICE.md).

Only clone someone's voice with their consent.

## Roadmap

- [ ] Qwen3-TTS as a voice engine for commercial projects
- [ ] More themes and templates: line chart, world map, before/after with photos
- [ ] English and other languages: alignment and captions not tied to Russian
- [ ] Horizontal and square formats from the same `reel.yaml`
- [ ] Web UI for gates: listen to takes and browse frames in the browser
- [ ] Docker image with models and Chrome

## Contributing

Bug reports, new scene templates and themes are very welcome. How to run the tests, add a template and open a PR:
[CONTRIBUTING.md](CONTRIBUTING.md). Change history: [CHANGELOG.md](CHANGELOG.md).
