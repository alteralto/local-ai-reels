"use strict";
/* Фикстура для превью интерфейса без Tauri (браузер, скриншоты). В приложении не используется. */
if (!window.__TAURI__) {
  const media = "../../../docs/media/";
  const W = "/home/me/Reels";
  const stages = (done, edits = ["linear", "from-end"]) => {
    const rows = [];
    ["check", "accent", "voice", "align", "music"].forEach((s, i) => rows.push({ key: s, stage: s, edit: null, status: i < done ? "done" : "todo", at: i < done ? "29.10 12:0" + i : null }));
    ["build", "snapshot", "render", "finish"].forEach((s, i) => edits.forEach((e) => rows.push({ key: `${s}@${e}`, stage: s, edit: e, status: i + 5 < done ? "done" : "todo", at: null })));
    return rows;
  };
  const gates = (st) => ["script", "accents", "take", "frames"].map((g, i) => ({ gate: g, stage: ["check", "accent", "voice", "snapshot"][i], status: st[i] }));
  const projects = [
    { dir: `${W}/povodok`, slug: "povodok", title: "Поводок", edits: { linear: "1234", "from-end": "4123" }, stages: stages(9), gates: gates(["approved", "approved", "approved", "approved"]),
      outputs: { linear: { mp4: `${W}/povodok/out/povodok-linear.mp4`, frames: media + "frames.png" }, "from-end": { mp4: `${W}/povodok/out/povodok-from-end.mp4`, frames: null } }, report: `${W}/povodok/out/report.md`, error: null },
    { dir: `${W}/venera`, slug: "venera", title: "Венера: сутки длиннее года", edits: { linear: "1234" }, stages: stages(3), gates: gates(["approved", "approved", "waiting", "todo"]), outputs: { linear: { mp4: null, frames: null } }, report: null, error: null },
    { dir: `${W}/opyonok`, slug: "opyonok", title: "Опёнок-гигант", edits: { linear: "1234" }, stages: stages(5), gates: gates(["approved", "approved", "approved", "todo"]), outputs: {}, report: null, error: null },
    { dir: `${W}/toilets`, slug: "toilets", title: "Куда пропали туалеты", edits: {}, stages: stages(0), gates: gates(["todo", "todo", "todo", "todo"]), outputs: {}, report: null, error: null },
  ];
  const blocks = [
    { index: 1, name: "Крючок", scene: "headline", text: "На Венере сутки длиннее года: планета поворачивается медленнее, чем обходит Солнце." },
    { index: 2, name: "Факт", scene: "stat", text: "Один оборот вокруг оси занимает двести сорок три земных дня, а год — двести двадцать пять." },
    { index: 3, name: "Поворот", scene: "compare", text: "И крутится она в обратную сторону: Солнце там встаёт на западе." },
    { index: 4, name: "Панчлайн", scene: "headline", text: "Если ждать рассвета на Венере, проще дождаться дня рождения." },
  ].map((b) => ({ ...b, words: b.text.split(/\s+/).filter((w) => /[\p{L}\p{N}]/u.test(w)).length }));
  const files = {
    dir: `${W}/venera`, title: "Венера: сутки длиннее года", theme: "night", limit: 60, blocks, totalWords: blocks.reduce((a, b) => a + b.words, 0), yamlError: null,
    accents: { master: "На Вен+ере с+утки длинн+ее г+ода: план+ета повор+ачивается медл+еннее, чем обх+одит С+олнце.", ref: "" },
    takes: [
      { file: "takes/denis-7.wav", voice: "denis", seed: 7, ratio: 1.0, heard: "на венере сутки длиннее года планета поворачивается медленнее", duration: 23.4, path: "", chosen: true },
      { file: "takes/dmitri-7.wav", voice: "dmitri", seed: 7, ratio: 0.98, heard: "на венере сутки длинее года планета поворачивается медленнее", duration: 24.1, path: "", chosen: false },
      { file: "takes/denis-11.wav", voice: "denis", seed: 11, ratio: 0.95, heard: "на венере сутки длиннее года планета поварачивается", duration: 22.8, path: "", chosen: false },
      { file: "takes/dmitri-31.wav", voice: "dmitri", seed: 31, ratio: 0.81, heard: "на вене сутки длинней года", duration: 25.6, path: "", chosen: false },
    ],
    choice: { file: "takes/denis-7.wav" }, master: "", qa: {}, outFiles: [], report: null, reportText: null,
  };
  const report = "# Поводок\n\n## Монтажи\n- linear — 24.6 с, −18.1 LUFS, сверка 100 %\n- from-end — 24.6 с, −18.0 LUFS, сверка 98 %\n\n## Голос\n- дубль denis/7, сверка 100 %";
  const listeners = {};
  const emit = (ev, p) => (listeners[ev] || []).forEach((cb) => cb(p));
  const scene = (location.hash || "#simple").slice(1);
  const settings = { workspace: W, concurrency: 2, mode: scene.startsWith("expert") ? "expert" : "simple" };
  const handlers = {
    app_info: () => ({ settings, defaultWorkspace: W, python: "/usr/bin/python3", repo: "/home/me/local-ai-reels", pythonError: null }),
    set_settings: (a) => a.settings,
    list_projects: () => projects,
    list_runs: () => [],
    list_examples: () => [{ name: "povodok", dir: "/x/povodok" }],
    open_project: (a) => {
      const st = projects.find((p) => p.dir === a.dir) || projects[0];
      if (a.dir.endsWith("povodok")) return { status: st, busy: false, files: { ...files, dir: a.dir, title: st.title, blocks: blocks.slice(0, 4), takes: [], accents: null, reportText: report } };
      return { status: st, files, busy: false };
    },
    simple_make: () => `${W}/venera`,
  };
  window.REELS_FIXTURE = {
    invoke: async (cmd, args) => (handlers[cmd] ? handlers[cmd](args || {}) : null),
    listen: async (ev, cb) => { (listeners[ev] = listeners[ev] || []).push(cb); },
    src: (p) => (p && p.startsWith(media) ? p : ""),
    afterInit: async (S, ui) => {
      const lines = ["$ python -m reels run /home/me/Reels/venera --no-input --yes", "■ venera  (/home/me/Reels/venera)", "  · check     без изменений", "  · accent    без изменений", "  ▸ voice", "  ✓ voice     41.2 с", "  ▸ align"];
      if (scene === "simple") {
        ui.setSource("topic");
        document.getElementById("topic").value = "Почему на Венере сутки длиннее года";
        const r = { dir: `${W}/venera`, title: "Почему на Венере сутки длиннее года", step: 0, stage: null, done: false, log: [], outputs: {} };
        S.simple.reels.push(r, { dir: "x", title: "Поводок", done: true, outputs: {}, log: [] });
        S.simple.current = r;
        ui.renderSimple();
        emit("runs-changed", [{ id: 1, project: r.dir, label: "ролик по теме", state: "running" }]);
        lines.forEach((line, i) => emit("run-log", { jobId: 1, project: r.dir, stream: i ? "stdout" : "cmd", line, stage: (line.match(/^\s+([▸✓·])\s+(\w+)/) || [])[2] ? { stage: line.match(/^\s+[▸✓·]\s+(\w+)/)[1], edit: null, state: "x" } : null }));
      } else if (scene === "simple-done") {
        const r = { dir: `${W}/povodok`, title: "Поводок", done: true, step: 4, log: [], outputs: { linear: { mp4: "a" }, "from-end": { mp4: "b" } }, poster: media + "hero.gif" };
        S.simple.reels.push(r);
        S.simple.current = r;
        ui.renderSimple();
      } else {
        emit("runs-changed", [{ id: 2, project: `${W}/opyonok`, label: "запуск", state: "running" }, { id: 3, project: `${W}/toilets`, label: "черновик", state: "queued" }]);
        S.runningStage.set(`${W}/opyonok`, "build");
        lines.forEach((line, i) => emit("run-log", { jobId: 2, project: `${W}/opyonok`, stream: i === 0 ? "cmd" : "stdout", line: line.replace(/venera/g, "opyonok"), stage: null }));
        emit("run-log", { jobId: 2, project: `${W}/opyonok`, stream: "stderr", line: "Lightning automatically upgraded your loaded checkpoint", stage: null });
        await ui.openProject(scene === "expert-done" ? `${W}/povodok` : `${W}/venera`);
      }
    },
  };
}
