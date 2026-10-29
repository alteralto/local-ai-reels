//! reels-app — параллельный конвейер вертикальных роликов поверх `python -m reels`.

mod run;
mod status;
mod tui;
mod util;
mod watch;

use std::path::PathBuf;
use std::process::ExitCode;
use std::time::Duration;

use clap::{Args, Parser, Subcommand};
use reels_core::{Python, SchedConfig};

#[derive(Parser)]
#[command(
    name = "reels-app",
    version,
    about = "Параллельный конвейер local-ai-reels: много проектов сразу, фаза ml на видеокарте, рендер на процессоре"
)]
struct Cli {
    /// Интерпретатор Python (по умолчанию $REELS_PYTHON, python3 или python)
    #[arg(long, global = true, value_name = "PATH")]
    python: Option<PathBuf>,
    /// Корень репозитория local-ai-reels (папка с reels/) — добавляется в PYTHONPATH
    #[arg(long, global = true, value_name = "DIR")]
    repo: Option<PathBuf>,
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Args, Clone, Debug)]
pub struct SchedArgs {
    /// Автопилот: подтверждать контрольные точки (передаётся в python -m reels run --yes)
    #[arg(long, short = 'y')]
    yes: bool,
    /// Сколько фаз ml (озвучка, музыка) одновременно — по числу видеокарт
    #[arg(long, default_value_t = 1, value_name = "N")]
    gpu_slots: usize,
    /// Сколько рендеров одновременно (по умолчанию ядра/4, не меньше 1)
    #[arg(long, value_name = "N")]
    cpu_slots: Option<usize>,
    /// Только этот монтаж (можно несколько раз)
    #[arg(long = "edit", short = 'e', value_name = "E")]
    edits: Vec<String>,
    /// Повторы фазы при сбое этапа (код 1)
    #[arg(long, default_value_t = 0, value_name = "N")]
    retries: u32,
}

impl SchedArgs {
    fn config(&self) -> SchedConfig {
        SchedConfig {
            gpu_slots: self.gpu_slots.max(1),
            cpu_slots: self
                .cpu_slots
                .unwrap_or_else(reels_core::scheduler::default_cpu_slots)
                .max(1),
            retries: self.retries,
            yes: self.yes,
            edits: self.edits.clone(),
        }
    }
}

#[derive(Subcommand)]
enum Cmd {
    /// Прогнать проекты параллельным конвейером, в конце — сводка
    Run {
        /// Папки проектов или папки, где их искать (рекурсивно)
        #[arg(required = true)]
        roots: Vec<PathBuf>,
        #[command(flatten)]
        sched: SchedArgs,
        /// Не спрашивать status --json перед запуском (тогда каждая фаза ml ждёт слот видеокарты)
        #[arg(long)]
        no_precheck: bool,
    },
    /// Состояние этапов, контрольных точек и готовых роликов
    Status {
        #[arg(required = true)]
        roots: Vec<PathBuf>,
        /// Вывести JSON (как python -m reels status --json)
        #[arg(long)]
        json: bool,
    },
    /// Следить за папкой-входящими: новые и изменённые проекты, темы из topics.txt
    Watch {
        inbox: PathBuf,
        #[command(flatten)]
        sched: SchedArgs,
        /// Как часто проверять папку
        #[arg(long, default_value = "5s", value_parser = util::parse_duration)]
        interval: Duration,
        /// Сколько ждать текущие процессы после Ctrl+C, потом убить
        #[arg(long, default_value = "120s", value_parser = util::parse_duration)]
        grace: Duration,
    },
    /// Панель в терминале: проекты, этапы, журнал; r/R — запуск, a — подтвердить, o — открыть, q — выход
    Tui {
        #[arg(required = true)]
        roots: Vec<PathBuf>,
        #[command(flatten)]
        sched: SchedArgs,
    },
}

#[tokio::main]
async fn main() -> ExitCode {
    let cli = Cli::parse();
    let py = Python::detect(
        cli.python.as_deref().map(|p| p.as_os_str()),
        cli.repo.as_deref(),
    );
    let res = match cli.cmd {
        Cmd::Run {
            roots,
            sched,
            no_precheck,
        } => run::run(py, &roots, &sched, !no_precheck).await,
        Cmd::Status { roots, json } => status::status(py, &roots, json).await,
        Cmd::Watch {
            inbox,
            sched,
            interval,
            grace,
        } => watch::watch(py, &inbox, &sched, interval, grace).await,
        Cmd::Tui { roots, sched } => tui::tui(py, &roots, &sched).await,
    };
    match res {
        Ok(code) => ExitCode::from(u8::try_from(code).unwrap_or(1)),
        Err(e) => {
            eprintln!("✗ {e:#}");
            ExitCode::from(2)
        }
    }
}
