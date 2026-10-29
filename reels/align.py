"""Озвучка Piper с пословными таймкодами без распознавания речи.

Piper (VITS) сам решает, сколько отсчётов звука займёт каждая фонема; piper-tts ≥ 1.8 отдаёт эти длительности
(`include_alignments`, нужен пакет onnx). Сумма длительностей равна длине звука до отсчёта, поэтому время слова
получается точным до кадра модели (256 отсчётов ≈ 12 мс), без Whisper и без скачивания моделей распознавания.

Каждый блок сценария синтезируется отдельно, между блоками вставляется пауза — по ней конвейер режет блоки.
Результат — wav и words.json [{"w", "s", "e"}] для `voice: {engine: file, file: …, words: …}`:

    python -m reels.align examples/my-reel --model ru-irinia-medium.onnx
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import wave
from pathlib import Path

PUNCT = set(",.;:!?…—–-\"'«»()")
EDGE = {"^", "$", "_"}


def words_from_phonemes(alignments: list[tuple[str, int]], sample_rate: int, offset: float = 0.0) -> list[dict]:
    """Слова из фонем Piper: [(фонема, отсчётов), …] → [{"s", "e"}] в секундах от offset.

    Слова разделяет фонема-пробел; знаки препинания (пауза после слова) и служебные ^ $ в слово не входят.
    Начало слова — начало его первой фонемы, конец — конец последней звучащей фонемы.
    """
    words: list[dict] = []
    t, cur = 0, None
    for ph, n in alignments:
        a, t = t, t + int(n)
        if ph == " ":
            if cur:
                words.append(cur)
            cur = None
        elif ph in EDGE or ph in PUNCT:
            continue
        elif cur is None:
            cur = {"s": a, "e": t}
        else:
            cur["e"] = t
    if cur:
        words.append(cur)
    return [{"s": round(offset + w["s"] / sample_rate, 3), "e": round(offset + w["e"] / sample_rate, 3)}
            for w in words]


def _tokens(text: str) -> list[str]:
    from .timing import script_tokens  # noqa: WPS433
    return script_tokens(text)


def synthesize_blocks(model: Path, blocks: list[str], pause: float = 0.35, sentence_pause: float = 0.15,
                      lead: float = 0.1, length_scale: float | None = None):
    """Синтез блоков по очереди с паузами. Возвращает (float32-звук, частота, слова с таймкодами)."""
    import numpy as np  # noqa: WPS433
    from piper import PiperVoice, SynthesisConfig  # noqa: WPS433

    voice = PiperVoice.load(str(model), include_alignments=True)
    sr = voice.config.sample_rate
    cfg = SynthesisConfig(length_scale=length_scale, normalize_audio=False)
    parts, words = [np.zeros(int(lead * sr), np.float32)], []
    t = lead
    for bi, text in enumerate(blocks):
        tokens = _tokens(text)
        got: list[dict] = []
        for ci, ch in enumerate(voice.synthesize(text, syn_config=cfg, include_alignments=True)):
            if ci:
                parts.append(np.zeros(int(sentence_pause * sr), np.float32))
                t += sentence_pause
            al = ch.phoneme_alignments
            if not al:
                raise RuntimeError("модель не отдаёт длительности фонем: нужен piper-tts ≥ 1.8 и pip install onnx")
            audio = ch.audio_float_array
            if sum(a.num_samples for a in al) != len(audio):
                raise RuntimeError(f"длительности фонем ({sum(a.num_samples for a in al)}) ≠ длине звука "
                                   f"({len(audio)})")
            got += words_from_phonemes([(a.phoneme, a.num_samples) for a in al], sr, t)
            parts.append(audio)
            t += len(audio) / sr
        if len(got) != len(tokens):
            raise RuntimeError(f"блок {bi + 1}: в сценарии {len(tokens)} слов, Piper произнёс {len(got)} — "
                               "уберите дефисы/цифры/сокращения: " + text)
        words += [{"w": w, **g} for w, g in zip(tokens, got)]
        gap = pause if bi < len(blocks) - 1 else 0.4
        parts.append(np.zeros(int(gap * sr), np.float32))
        t += gap
    return np.concatenate(parts), sr, words


def write_wav(path: Path, audio, sr: int) -> Path:
    import numpy as np  # noqa: WPS433
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return path


def loudnorm(src: Path, dst: Path, lufs: float = -19.0, sr: int = 24000) -> Path:
    """Громкость по EBU R128 в два прохода (линейная нормализация — таймкоды не сдвигаются)."""
    af = f"loudnorm=I={lufs}:TP=-1.5:LRA=11"
    p = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(src), "-af", af + ":print_format=json", "-f", "null",
                        "-"], capture_output=True, text=True, check=True)
    m = json.loads(re.findall(r"\{[^{}]*\}", p.stderr)[-1])
    af += (f":measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
           f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-af", af, "-ar", str(sr), "-ac", "1",
                    "-c:a", "pcm_s16le", str(dst)], check=True)
    return dst


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m reels.align", description=__doc__.split("\n")[0])
    ap.add_argument("project", type=Path, help="папка с reel.yaml")
    ap.add_argument("--model", type=Path, required=True, help="голос Piper .onnx (рядом .onnx.json)")
    ap.add_argument("--out", default="assets", help="куда положить narration.wav и words.json (в проекте)")
    ap.add_argument("--pause", type=float, default=0.35, help="пауза между блоками, с")
    ap.add_argument("--length-scale", type=float, default=None, help="темп Piper: < 1 быстрее")
    ap.add_argument("--lufs", type=float, default=-19.0,
                    help="громкость голоса; с музыкой и «вжухами» микс выйдет около −16 LUFS")
    a = ap.parse_args(argv)

    from .project import load  # noqa: WPS433
    project = load(a.project)
    audio, sr, words = synthesize_blocks(a.model, [b.text for b in project.blocks], a.pause,
                                         length_scale=a.length_scale)
    out = a.project / a.out
    out.mkdir(parents=True, exist_ok=True)
    raw = write_wav(out / "narration-raw.wav", audio, sr)
    loudnorm(raw, out / "narration.wav", a.lufs)
    raw.unlink()
    (out / "words.json").write_text(json.dumps(words, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    print(f"{out / 'narration.wav'}: {len(audio) / sr:.2f} с, {len(words)} слов → {out / 'words.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
