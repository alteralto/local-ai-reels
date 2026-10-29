"""Конвейер: этапы, кэш по хэшам входов, контрольные точки (gates) с подтверждением человеком.

Этап пропускается, если хэш его входов совпадает с записанным в .reels-state.json и выходы на месте.
Хэши сцеплены: изменился сценарий → устарели озвучка, таймкоды, сборка и всё дальше.
Контрольная точка подтверждается для конкретного артефакта (хэша): поменялся текст — подтверждать заново.
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import __version__
from .project import Project
from .textcheck import check_script
from .util import read_json, say, sha, write_json

STAGES = ["check", "accent", "voice", "align", "music", "build", "snapshot", "render", "finish"]
EDIT_STAGES = {"build", "snapshot", "render", "finish"}       # выполняются для каждого монтажа
GATES = {"check": "script", "accent": "accents", "voice": "take", "snapshot": "frames"}
STATE_NAME = ".reels-state.json"
PKG = Path(__file__).resolve().parent


class StageError(RuntimeError):
    """Этап не прошёл: сообщение объясняет, что исправить."""


class GateWait(RuntimeError):
    """Конвейер остановился на контрольной точке и ждёт подтверждения."""

    def __init__(self, gate: str, summary: str, project: Project):
        self.gate, self.summary, self.project = gate, summary, project
        super().__init__(f"ждёт подтверждения «{gate}»")


# --- состояние --------------------------------------------------------------------------------------------------
@dataclass
class State:
    path: Path
    stages: dict = field(default_factory=dict)
    approved: dict = field(default_factory=dict)

    @classmethod
    def load(cls, project: Project) -> "State":
        p = project.dir / STATE_NAME
        d = read_json(p, {}) or {}
        return cls(p, d.get("stages", {}), d.get("approved", {}))

    def save(self) -> None:
        write_json(self.path, {"version": __version__, "stages": self.stages, "approved": self.approved})

    def done(self, key: str, h: str, info: dict | None = None) -> None:
        self.stages[key] = {"hash": h, "at": datetime.now().isoformat(timespec="seconds"), **(info or {})}
        self.save()


def _code_hash() -> str:
    """Хэш исходников пакета: обновили шаблоны сцен или сборку — монтаж пересоберётся."""
    files = sorted(p for p in PKG.rglob("*") if p.suffix in (".py", ".mjs", ".md") and "__pycache__" not in p.parts)
    return sha(*files)


def _tree_hash(*dirs: Path) -> str:
    files = sorted(p for d in dirs if d.exists() for p in d.rglob("*") if p.is_file() and p.suffix != ".wav")
    return sha(*[str(p) for p in files], *files)


# --- контекст запуска --------------------------------------------------------------------------------------------
@dataclass
class Run:
    project: Project
    state: State
    edits: dict[str, list[int]]
    yes: bool = False
    interactive: bool = False
    force: set = field(default_factory=set)
    qa: str = "error"             # error — ошибки вёрстки останавливают конвейер; warn — только предупреждение
    hashes: dict = field(default_factory=dict)
    results: dict = field(default_factory=dict)

    def timing(self) -> dict:
        t = read_json(self.project.timing_json)
        if not t:
            raise StageError("нет таймкодов — сначала этап align")
        return t


# --- этапы -------------------------------------------------------------------------------------------------------
# Каждый этап: inputs(run[, edit]) → хэш входов; outputs(run[, edit]) → файлы, которые должны существовать;
# do(run[, edit]) → словарь сведений для отчёта; gate(run) → (хэш артефакта, сводка для человека) или None.

def _check_inputs(r: Run):
    p = r.project
    return sha(p.cfg["blocks"], p.cfg["limit"], p.cfg["lang"], p.cfg["source"], p.cfg["edits"])


def _check_do(r: Run):
    issues = check_script(r.project)
    for i in issues:
        say("   ", i)
    errors = [i for i in issues if i.level == "error"]
    if errors:
        raise StageError(f"в сценарии {len(errors)} ошибок — исправьте reel.yaml")
    return {"words": r.project.total_words, "warnings": len(issues)}


def _check_gate(r: Run):
    p = r.project
    lines = [f"  {b.index}. {b.name} ({b.words} сл.): {b.text}" for b in p.blocks]
    lines.append(f"  всего {p.total_words} / {p.cfg['limit']} слов; монтажи: "
                 + ", ".join(f"{k}={''.join(map(str, v))}" for k, v in p.edits.items()))
    return sha(p.script), "Сценарий:\n" + "\n".join(lines)


def _accent_inputs(r: Run):
    v = r.project.cfg["voice"]
    return sha(r.hashes["check"], v["engine"], v["ref_text"], v["accents"])


def _accent_do(r: Run):
    from . import voice
    path = voice.accent(r.project)
    return {"file": str(path)}


def _accent_gate(r: Run):
    if r.project.cfg["voice"]["engine"] != "f5":
        return None
    path = r.project.voice_dir / "accents.json"
    acc = read_json(path, {})
    return sha(path), (f"Ударения ({path}) — поправить можно в voice.accents в reel.yaml:\n  {acc.get('master', '')}"
                       f"\n  эталон: {acc.get('ref', '')}")


def _voice_inputs(r: Run):
    p, v = r.project, r.project.cfg["voice"]
    refs = [p.path(v["ref"].format(voice=x)) for x in v["voices"]] if v["engine"] == "f5" else []
    return sha(r.hashes["accent"], p.voice_dir / "accents.json",
               {k: v[k] for k in ("engine", "voices", "seeds", "speed", "nfe_step", "min_ratio")},
               *(x for x in refs if x), p.path(v["file"]) if v["engine"] == "file" else "")


def _voice_do(r: Run):
    from . import voice
    choice = voice.synthesize(r.project)
    return {k: choice.get(k) for k in ("file", "voice", "seed", "ratio", "ok")}


def _voice_gate(r: Run):
    p = r.project
    if p.cfg["voice"]["engine"] == "file":
        return None
    choice = read_json(p.voice_dir / "choice.json", {})
    takes = read_json(p.voice_dir / "takes.json", []) or []
    lines = [f"  {t.get('ratio')}  {Path(str(t.get('file'))).name}" for t in takes[:6]]
    return sha(p.master_wav), (f"Выбран дубль {choice.get('file')} (сверка {choice.get('ratio')}). Послушайте "
                               f"{p.master_wav}.\n  Дубли:\n" + "\n".join(lines) +
                               "\n  Другой дубль: скопируйте его в master.wav и запустите заново.")


def _align_inputs(r: Run):
    p, v = r.project, r.project.cfg["voice"]
    words_file = p.path(v["words"]) if v["engine"] == "file" and v["words"] else None
    return sha(r.hashes["voice"], p.master_wav, words_file or "", p.script, p.cfg["cut_bias"],
               [b.text for b in p.blocks])


def _align_do(r: Run):
    from . import timing as T, voice
    p = r.project
    voice.timestamps(p)
    heard = read_json(p.words_json)
    try:
        words = T.aligned(heard, p.script)
    except ValueError as e:
        raise StageError(f"{e}. Распознано: {' '.join(w['w'] for w in heard)}") from e
    cuts = T.block_cuts(words, T.block_token_counts(p), p.cfg["cut_bias"])
    write_json(p.timing_json, {"cuts": cuts, "words": words})
    say(f"    границы блоков: {cuts}")
    return {"cuts": cuts}


def _max_total(r: Run) -> float:
    from . import timing as T
    cuts = r.timing()["cuts"]
    return max(T.place(o, cuts, r.project.cfg["gap"], r.project.cfg["tail"])[1] for o in r.project.edits.values())


def _music_inputs(r: Run):
    m = r.project.cfg["music"]
    return sha(m, r.project.path(m["file"]) if m["engine"] == "file" else "", round(_max_total(r)))


def _music_outputs(r: Run):
    return [r.project.music_wav] if r.project.cfg["music"]["engine"] != "none" else []


def _music_do(r: Run):
    from . import music
    path = music.make_music(r.project, _max_total(r) + 1.0)
    return {"file": str(path) if path else None}


def _build_inputs(r: Run, edit: str):
    p = r.project
    cfg = {k: v for k, v in p.cfg.items() if k not in ("voice", "asr", "render", "poster")}
    return sha(r.hashes["align"], r.hashes["music"], p.timing_json, cfg, r.edits[edit], _code_hash(),
               _tree_hash(p.dir / "scenes", p.dir / "assets"))


def _build_do(r: Run, edit: str):
    from . import compose
    d = compose.build_edit(r.project, edit, r.edits[edit], r.timing())
    return {"dir": str(d)}


def _snapshot_inputs(r: Run, edit: str):
    return sha(r.hashes[f"build@{edit}"], r.qa)


def _snapshot_do(r: Run, edit: str):
    from . import compose, qa, render
    p, d = r.project, r.project.edit_dir(edit)
    times = compose.snapshot_times(p, r.edits[edit], r.timing())
    lint = render.lint(d)
    if lint.get("errors"):
        raise StageError(f"монтаж {edit}: hyperframes lint — {lint['errors']} ошибок:\n{lint.get('output', '')[-1500:]}")
    pngs = render.snapshot(d, times)
    sheet = qa.contact_sheet(pngs, d / "frames.jpg")
    p.out.mkdir(parents=True, exist_ok=True)
    shutil.copy(sheet, p.out / f"{p.slug}-{edit}-frames.jpg")
    try:
        issues = qa.layout_check(d, times)
    except Exception as e:  # noqa: BLE001 — автопроверка не должна ронять конвейер, если нет браузера
        say(f"    ! автопроверка вёрстки пропущена: {e}")
        issues = None
    write_json(d / "qa.json", issues if issues is not None else {"skipped": True})
    errors = [i for i in (issues or []) if i.get("severity") == "error"]
    if issues:
        say(qa.format_issues(issues))
    if errors and r.qa == "error":
        raise StageError(f"монтаж {edit}: автопроверка нашла {len(errors)} ошибок вёрстки (кадры: {sheet}). "
                         "Исправьте сцены или запустите с --qa warn")
    return {"frames": str(sheet), "qa_errors": len(errors), "qa_warnings": len(issues or []) - len(errors),
            "lint_warnings": lint.get("warnings", 0)}


def _snapshot_gate(r: Run):
    p = r.project
    sheets = [p.out / f"{p.slug}-{e}-frames.jpg" for e in r.edits]
    return sha(*[p.edit_dir(e) / "index.html" for e in r.edits]), (
        "Посмотрите кадры до рендера (переносы строк, наложения, обрезанные штрихи):\n"
        + "\n".join(f"  {s}" for s in sheets))


def _render_inputs(r: Run, edit: str):
    return sha(r.hashes[f"build@{edit}"], r.project.cfg["render"])


def _render_mp4(r: Run, edit: str) -> Path:
    return r.project.edit_dir(edit) / "renders" / f"{edit}.mp4"


def _render_do(r: Run, edit: str):
    from . import render
    rc = r.project.cfg["render"]
    out = render.render(r.project.edit_dir(edit), _render_mp4(r, edit), fps=rc["fps"], quality=rc["quality"])
    return {"file": str(out)}


def _finish_inputs(r: Run, edit: str):
    p = r.project
    return sha(r.hashes[f"render@{edit}"], p.cfg["poster"], p.cfg["asr"], p.cfg["render"]["fps"])


def _finish_do(r: Run, edit: str):
    from . import compose, finish
    p = r.project
    order = r.edits[edit]
    at = compose.poster_time(p, order, r.timing())
    out = p.out_mp4(edit)
    out.parent.mkdir(parents=True, exist_ok=True)
    finish.poster(_render_mp4(r, edit), at, out)
    res = finish.check(out, {"width": 1080, "height": 1920, "fps": p.cfg["render"]["fps"]})
    script = " ".join(p.blocks[b - 1].text for b in order)
    res["verify"] = finish.verify(out, script, p.cfg["asr"])
    qa_json = read_json(p.edit_dir(edit) / "qa.json")
    res.update({"edit": edit, "order": "".join(map(str, order)), "file": str(out), "poster_at": at,
                "qa": qa_json if isinstance(qa_json, list) else None})
    write_json(p.edit_dir(edit) / "finish.json", res)
    return {"file": str(out), "ok": res.get("ok")}


@dataclass
class Stage:
    name: str
    inputs: Callable
    do: Callable
    outputs: Callable
    gate: Callable | None = None


STAGE_DEFS = {
    "check": Stage("check", _check_inputs, _check_do, lambda r: [], _check_gate),
    "accent": Stage("accent", _accent_inputs, _accent_do, lambda r: [r.project.voice_dir / "accents.json"],
                    _accent_gate),
    "voice": Stage("voice", _voice_inputs, _voice_do, lambda r: [r.project.master_wav], _voice_gate),
    "align": Stage("align", _align_inputs, _align_do, lambda r: [r.project.timing_json]),
    "music": Stage("music", _music_inputs, _music_do, _music_outputs),
    "build": Stage("build", _build_inputs, _build_do, lambda r, e: [r.project.edit_dir(e) / "index.html"]),
    "snapshot": Stage("snapshot", _snapshot_inputs, _snapshot_do,
                      lambda r, e: [r.project.edit_dir(e) / "frames.jpg", r.project.edit_dir(e) / "qa.json"],
                      _snapshot_gate),
    "render": Stage("render", _render_inputs, _render_do, lambda r, e: [_render_mp4(r, e)]),
    "finish": Stage("finish", _finish_inputs, _finish_do,
                    lambda r, e: [r.project.out_mp4(e), r.project.edit_dir(e) / "finish.json"]),
}


# --- запуск -------------------------------------------------------------------------------------------------------
def _gate(r: Run, stage: Stage) -> None:
    if not stage.gate:
        return
    g = stage.gate(r)
    if g is None:
        return
    h, summary = g
    name = GATES[stage.name]
    if r.state.approved.get(name) == h:
        return
    if r.yes:
        r.state.approved[name] = h
        r.state.save()
        return
    if r.interactive:
        say(f"\n── контрольная точка «{name}» ──\n{summary}")
        ans = input("Подтвердить и продолжить? [y/N] ").strip().lower()
        if ans in ("y", "yes", "д", "да"):
            r.state.approved[name] = h
            r.state.save()
            return
    raise GateWait(name, summary, r.project)


def _run_one(r: Run, stage: Stage, edit: str | None) -> None:
    key = f"{stage.name}@{edit}" if edit else stage.name
    args = (r, edit) if edit else (r,)
    h = stage.inputs(*args)
    r.hashes[key] = h
    prev = r.state.stages.get(key, {})
    fresh = prev.get("hash") == h and all(Path(o).exists() for o in stage.outputs(*args))
    label = f"{stage.name:<8}" + (f" [{edit}]" if edit else "")
    if fresh and stage.name not in r.force:
        say(f"  · {label}  без изменений")
        r.results[key] = {k: v for k, v in prev.items() if k not in ("hash",)}
        return
    t0 = time.time()
    say(f"  ▸ {label}")
    info = stage.do(*args) or {}
    dt = time.time() - t0
    r.state.done(key, h, {**info, "seconds": round(dt, 1)})
    r.results[key] = info
    say(f"  ✓ {label}  {dt:.1f} с")


def _edit_hash(r: Run, stage_name: str) -> None:
    """Сводный хэш этапа по всем монтажам — вход для следующих общих этапов (не используется дальше finish)."""
    r.hashes[stage_name] = sha(*[r.hashes.get(f"{stage_name}@{e}", "") for e in r.edits])


def run(project: Project, *, yes: bool = False, edits: list[str] | None = None, from_stage: str | None = None,
        only: str | None = None, force: bool = False, qa: str = "error", interactive: bool | None = None,
        until: str | None = None) -> dict:
    """Прогоняет конвейер для проекта. Возвращает {этап: сведения}. GateWait — остановка на контрольной точке."""
    all_edits = project.edits
    chosen = {e: all_edits[e] for e in (edits or all_edits)} if edits else all_edits
    unknown = set(edits or []) - set(all_edits)
    if unknown:
        raise StageError(f"нет монтажей {', '.join(sorted(unknown))}; есть: {', '.join(all_edits)}")
    for s in (from_stage, only, until):
        if s and s not in STAGES:
            raise StageError(f"нет этапа «{s}»; этапы: {' → '.join(STAGES)}")
    r = Run(project, State.load(project), chosen, yes=yes,
            interactive=sys.stdin.isatty() if interactive is None else interactive, qa=qa)
    if force:
        r.force = set(STAGES)
    if from_stage:
        r.force |= set(STAGES[STAGES.index(from_stage):])
    say(f"■ {project.slug}  ({project.dir})")
    for name in STAGES:
        stage = STAGE_DEFS[name]
        if only and name != only:
            # входы предыдущих этапов всё равно нужны для хэшей следующих
            _hash_only(r, stage)
            continue
        if name in EDIT_STAGES:
            for e in r.edits:
                _run_one(r, stage, e)
            _edit_hash(r, name)
        else:
            _run_one(r, stage, None)
        if not only:
            _gate(r, stage)
        if until and name == until:
            break
    if (only in (None, "finish")) and (not until or until == "finish"):
        _report(r)
    return r.results


def _hash_only(r: Run, stage: Stage) -> None:
    try:
        if stage.name in EDIT_STAGES:
            for e in r.edits:
                r.hashes[f"{stage.name}@{e}"] = stage.inputs(r, e)
            _edit_hash(r, stage.name)
        else:
            r.hashes[stage.name] = stage.inputs(r)
    except Exception:  # noqa: BLE001 — входы могут ещё не существовать
        r.hashes.setdefault(stage.name, "")


def _report(r: Run) -> None:
    from . import finish
    p = r.project
    results = [read_json(p.edit_dir(e) / "finish.json") for e in p.edits]
    results = [x for x in results if x]
    if not results:
        return
    js, md = finish.write_report(p, results)
    say(f"■ готово: {', '.join(str(p.out_mp4(e).relative_to(p.dir)) for e in r.edits)}\n  отчёт: {md}")


def status(project: Project) -> list[tuple[str, str, str]]:
    """Состояние этапов: [(этап, статус, подробности)] — done | stale | todo; для точек — approved | waiting | todo | n/a."""
    js = status_json(project)
    return [(x["key"], x["status"], x["at"]) for x in js["stages"]] + \
        [(f"gate:{g['gate']}", g["status"], "") for g in js["gates"]]


def status_json(project: Project) -> dict:
    """Состояние проекта для приложений (reels status --json): этапы, контрольные точки, готовые файлы."""
    st = State.load(project)
    r = Run(project, st, project.edits, interactive=False)
    stages, gates = [], []
    for name in STAGES:
        stage = STAGE_DEFS[name]
        keys = [(f"{name}@{e}", e, (r, e)) for e in r.edits] if name in EDIT_STAGES else [(name, None, (r,))]
        done_all = True
        for key, edit, args in keys:
            try:
                h = stage.inputs(*args)
            except Exception:  # noqa: BLE001 — входы ещё не готовы
                h = None
            r.hashes[key] = h or ""
            prev = st.stages.get(key)
            if not prev:
                state = "todo"
            elif prev.get("hash") == h and all(Path(o).exists() for o in stage.outputs(*args)):
                state = "done"
            else:
                state = "stale"
            done_all &= state == "done"
            stages.append({"key": key, "stage": name, "edit": edit, "status": state, "at": prev.get("at", "") if prev else ""})
        if name in EDIT_STAGES:
            _edit_hash(r, name)
        if name in GATES:
            gate = GATES[name]
            try:
                g = stage.gate(r) if done_all else False
            except Exception:  # noqa: BLE001
                g = False
            if g is None:
                gs = "n/a"
            elif g is False:
                gs = "todo"
            else:
                gs = "approved" if st.approved.get(gate) == g[0] else "waiting"
            gates.append({"gate": gate, "stage": name, "status": gs})
    outputs = {}
    for e in project.edits:
        mp4 = project.out_mp4(e)
        frames = project.out / f"{project.slug}-{e}-frames.jpg"
        outputs[e] = {"mp4": str(mp4) if mp4.exists() else None, "frames": str(frames) if frames.exists() else None}
    report = project.out / "report.md"
    return {"dir": str(project.dir), "slug": project.slug, "title": project.cfg.get("title") or project.slug,
            "edits": {k: "".join(map(str, v)) for k, v in project.edits.items()}, "stages": stages, "gates": gates,
            "outputs": outputs, "report": str(report) if report.exists() else None, "error": None}


def approve(project: Project, gate: str) -> str:
    """Подтверждает контрольную точку для текущего артефакта."""
    if gate not in GATES.values():
        raise StageError(f"нет контрольной точки «{gate}»; есть: {', '.join(GATES.values())}")
    stage_name = next(k for k, v in GATES.items() if v == gate)
    st = State.load(project)
    r = Run(project, st, project.edits, interactive=False)
    for name in STAGES[:STAGES.index(stage_name) + 1]:
        _hash_only(r, STAGE_DEFS[name])
    g = STAGE_DEFS[stage_name].gate(r)
    if g is None:
        return f"«{gate}» для этого проекта не требуется"
    st.approved[gate] = g[0]
    st.save()
    return f"«{gate}» подтверждено"


def clean(project: Project) -> None:
    """Удаляет build/ и состояние (out/ не трогает)."""
    shutil.rmtree(project.build, ignore_errors=True)
    (project.dir / STATE_NAME).unlink(missing_ok=True)


def dumps(results: dict) -> str:
    return json.dumps(results, ensure_ascii=False, indent=1, default=str)
