//! `reels-app run` — параллельный прогон, живой прогресс, итоговая таблица.

use std::collections::HashMap;
use std::io::IsTerminal;
use std::path::{Path, PathBuf};
use std::time::Duration;

use anyhow::{bail, Context, Result};
use indicatif::{MultiProgress, ProgressBar, ProgressStyle};
use reels_core::python::Stream;
use reels_core::scheduler::progress_hint;
use reels_core::{Event, Outcome, ProjectStatus, Python, Scheduler, Shutdown, Summary};
use tokio::sync::{mpsc, watch};

use crate::util::{fmt_clock, fmt_dur, name_of, same, scan_mp4, short};
use crate::SchedArgs;

pub async fn run(py: Python, roots: &[PathBuf], args: &SchedArgs, precheck: bool) -> Result<i32> {
    let dirs = reels_core::discover(roots).context("поиск проектов")?;
    if dirs.is_empty() {
        bail!("не найдено ни одного проекта (папки с reel.yaml)");
    }
    let cfg = args.config();
    println!(
        "■ проектов: {}; слоты: видеокарта {}, процессор {}{}",
        dirs.len(),
        cfg.gpu_slots,
        cfg.cpu_slots,
        if cfg.yes {
            "; автопилот --yes"
        } else {
            ""
        }
    );
    let (tx, rx) = mpsc::unbounded_channel();
    let (stop_tx, stop_rx) = watch::channel(Shutdown::Run);
    let sched = Scheduler::new(py.clone(), cfg, tx, stop_rx);
    if precheck {
        match sched.precheck(&dirs).await {
            Ok(0) => {}
            Ok(n) => println!("  озвучка и музыка уже готовы у {n} — сразу в очередь на рендер"),
            Err(e) => println!("  (status --json недоступен: {e}; фаза ml пройдёт по кэшу)"),
        }
    }
    tokio::spawn(ctrl_c(stop_tx, Duration::from_secs(30)));
    let ui = tokio::spawn(progress(dirs.clone(), rx));
    let summary = sched.run_all(&dirs).await;
    drop(sched);
    let _ = ui.await;
    let statuses = py.status(&dirs).await.unwrap_or_default();
    print_summary(&summary, &statuses);
    Ok(summary.exit_code())
}

/// Первый Ctrl+C — дождаться текущих процессов (не дольше `grace`), второй — убить сразу.
pub async fn ctrl_c(stop: watch::Sender<Shutdown>, grace: Duration) {
    if tokio::signal::ctrl_c().await.is_err() {
        return;
    }
    eprintln!("\n⏸ остановка: новые фазы не начинаются, текущие дорабатывают (ещё раз Ctrl+C — прервать сразу)");
    let _ = stop.send(Shutdown::Drain);
    tokio::select! {
        _ = tokio::signal::ctrl_c() => eprintln!("⏹ прерываю процессы"),
        _ = tokio::time::sleep(grace) => eprintln!("⏹ прошло {}, прерываю процессы", fmt_dur(grace)),
    }
    let _ = stop.send(Shutdown::Kill);
}

/// Живой прогресс: строка на проект (indicatif) в терминале, иначе обычные строки.
pub async fn progress(dirs: Vec<PathBuf>, mut rx: mpsc::UnboundedReceiver<Event>) {
    let tty = std::io::stdout().is_terminal();
    let mp = MultiProgress::new();
    let style =
        ProgressStyle::with_template("{spinner:.cyan} {prefix:<24} {elapsed:>4} {wide_msg}")
            .unwrap_or_else(|_| ProgressStyle::default_spinner())
            .tick_chars("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏ ");
    let mut bars: HashMap<PathBuf, ProgressBar> = HashMap::new();
    let mut phase: HashMap<PathBuf, String> = HashMap::new();
    let bar_for = |dir: &Path, bars: &mut HashMap<PathBuf, ProgressBar>| -> ProgressBar {
        bars.entry(dir.to_path_buf())
            .or_insert_with(|| {
                let b = mp.add(ProgressBar::new_spinner());
                b.set_style(style.clone());
                b.set_prefix(name_of(dir));
                b.set_message("в очереди");
                b.enable_steady_tick(Duration::from_millis(120));
                b
            })
            .clone()
    };
    if tty {
        for d in &dirs {
            bar_for(d, &mut bars);
        }
    }
    while let Some(ev) = rx.recv().await {
        let dir = ev.dir().to_path_buf();
        let name = name_of(&dir);
        if !tty {
            if let Some(l) = plain_line(&ev) {
                println!("[{name}] {l}");
            }
            continue;
        }
        let bar = bar_for(&dir, &mut bars);
        match &ev {
            Event::ProjectStarted { .. } => bar.set_message("в очереди"),
            Event::Phase {
                phase: ph, attempt, ..
            } => {
                let p = if *attempt > 1 {
                    format!("{ph} (попытка {attempt})")
                } else {
                    ph.to_string()
                };
                bar.set_message(format!("{p} ▸ запуск"));
                phase.insert(dir.clone(), p);
            }
            Event::Line { line, stream, .. } => {
                if let Some(h) = progress_hint(line) {
                    let p = phase.get(&dir).cloned().unwrap_or_default();
                    bar.set_message(format!("{p} {h}"));
                    if *stream == Stream::Stderr && h.starts_with('✗') {
                        let _ = mp.println(format!("[{name}] {h}"));
                    }
                }
            }
            Event::Waiting { gate, .. } => {
                bar.finish_with_message(format!("… ждёт подтверждения «{gate}»"))
            }
            Event::Done { elapsed, .. } => {
                bar.finish_with_message(format!("✓ готово за {}", fmt_dur(*elapsed)))
            }
            Event::Failed {
                phase: ph, error, ..
            } => {
                let first = error.lines().next().unwrap_or("");
                bar.finish_with_message(format!("✗ {ph}: {first}"))
            }
        }
    }
    for b in bars.values() {
        if !b.is_finished() {
            b.finish();
        }
    }
}

fn plain_line(ev: &Event) -> Option<String> {
    Some(match ev {
        Event::ProjectStarted { .. } => "в очереди".into(),
        Event::Phase { phase, attempt, .. } if *attempt > 1 => {
            format!("▶ {phase} (попытка {attempt})")
        }
        Event::Phase { phase, .. } => format!("▶ {phase}"),
        Event::Line { line, .. } => progress_hint(line)?,
        Event::Waiting { gate, .. } => format!("… ждёт подтверждения «{gate}»"),
        Event::Done { elapsed, .. } => format!("✓ готово за {}", fmt_dur(*elapsed)),
        Event::Failed { phase, error, .. } => {
            format!("✗ {phase}: {}", error.lines().next().unwrap_or(""))
        }
    })
}

/// Итоговая таблица.
pub fn print_summary(s: &Summary, statuses: &[ProjectStatus]) {
    println!(
        "\n■ итог за {}: готово {}, ждут подтверждения {}, ошибки {}{}",
        fmt_dur(s.elapsed),
        s.done(),
        s.waiting(),
        s.failed(),
        if s.cancelled() > 0 {
            format!(", остановлено {}", s.cancelled())
        } else {
            String::new()
        }
    );
    println!(
        "  {:<2}{:<24} {:<22} {:>7} {:>7} {:>7} {:>7}  результат",
        "", "проект", "итог", "всего", "ml", "рендер", "очередь"
    );
    let opt = |d: Option<Duration>| d.map(fmt_clock).unwrap_or_else(|| "—".into());
    for r in &s.reports {
        let (mark, what) = match &r.outcome {
            Outcome::Done => ("✓", "готово".to_string()),
            Outcome::Waiting { gate } => ("…", format!("ждёт «{gate}»")),
            Outcome::Failed { phase, .. } => ("✗", format!("ошибка ({phase})")),
            Outcome::Cancelled => ("⏹", "остановлено".to_string()),
        };
        let outputs = outputs_of(&r.dir, statuses);
        let out = match &r.outcome {
            Outcome::Failed { error, .. } => error.lines().next().unwrap_or("").to_string(),
            _ if outputs.is_empty() => "—".into(),
            _ => outputs
                .iter()
                .map(|p| short(p))
                .collect::<Vec<_>>()
                .join(", "),
        };
        println!(
            "  {mark} {:<24} {:<22} {:>7} {:>7} {:>7} {:>7}  {out}",
            name_of(&r.dir),
            what,
            fmt_clock(r.total),
            opt(r.ml),
            opt(r.render),
            fmt_clock(r.queued),
        );
    }
    let waiting: Vec<_> = s
        .reports
        .iter()
        .filter_map(|r| match &r.outcome {
            Outcome::Waiting { gate } => Some((r, gate)),
            _ => None,
        })
        .collect();
    if !waiting.is_empty() {
        println!("\n  подтвердить и продолжить:");
        for (r, gate) in waiting {
            println!("    python -m reels approve {} {gate}", short(&r.dir));
        }
        println!("  (или reels-app tui …, клавиша a; или запуск с --yes)");
    }
    for r in &s.reports {
        if let Outcome::Failed { error, code, .. } = &r.outcome {
            if error.lines().count() > 1 {
                println!(
                    "\n  ✗ {} (код {}):",
                    name_of(&r.dir),
                    code.map(|c| c.to_string()).unwrap_or("—".into())
                );
                for l in error.lines() {
                    println!("    {l}");
                }
            }
        }
    }
}

fn outputs_of(dir: &Path, statuses: &[ProjectStatus]) -> Vec<PathBuf> {
    if let Some(st) = statuses.iter().find(|s| same(&s.dir, dir)) {
        return st.mp4s().into_iter().map(|(_, p)| p.clone()).collect();
    }
    scan_mp4(dir)
}
