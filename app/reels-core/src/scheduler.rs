//! Планировщик: много проектов одновременно, как настоящий конвейер.
//!
//! Каждый проект проходит две фазы:
//! * «ml» — `run --until music` (озвучка, выравнивание, музыка): нужен слот видеокарты (`gpu_slots`);
//! * «render» — `run` до конца (сборка, кадры, рендер HyperFrames): нужен слот процессора (`cpu_slots`).
//!
//! Проект держит не больше одного слота и одного процесса Python за раз, поэтому фаза «ml» проекта B
//! идёт одновременно с «render» проекта A. Код 3 — проект ждёт подтверждения и уходит со сцены,
//! код 1 — повтор (`retries`), код 2 — ошибка настройки, без повторов.

use std::collections::HashSet;
use std::fmt;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use tokio::sync::{mpsc, watch, Semaphore};
use tokio::task::JoinHandle;

use crate::python::{
    Python, RunOpts, Shutdown, Stream, EXIT_CONFIG, EXIT_FAILED, EXIT_GATE, EXIT_OK,
};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Phase {
    Ml,
    Render,
}

impl Phase {
    pub fn as_str(self) -> &'static str {
        match self {
            Phase::Ml => "ml",
            Phase::Render => "render",
        }
    }
}

impl fmt::Display for Phase {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

#[derive(Debug, Clone)]
pub struct SchedConfig {
    pub gpu_slots: usize,
    pub cpu_slots: usize,
    /// Повторы фазы при коде 1.
    pub retries: u32,
    pub yes: bool,
    pub edits: Vec<String>,
}

impl Default for SchedConfig {
    fn default() -> Self {
        SchedConfig {
            gpu_slots: 1,
            cpu_slots: default_cpu_slots(),
            retries: 0,
            yes: false,
            edits: Vec::new(),
        }
    }
}

/// max(1, ядра/4): каждый рендер HyperFrames сам занимает несколько ядер.
pub fn default_cpu_slots() -> usize {
    let cores = std::thread::available_parallelism()
        .map(|n| n.get())
        .unwrap_or(1);
    (cores / 4).max(1)
}

/// События для интерфейсов.
#[derive(Debug, Clone)]
pub enum Event {
    /// Проект поставлен в очередь.
    ProjectStarted {
        dir: PathBuf,
    },
    /// Фаза началась (слот получен, процесс запущен).
    Phase {
        dir: PathBuf,
        phase: Phase,
        attempt: u32,
    },
    /// Строка вывода Python.
    Line {
        dir: PathBuf,
        phase: Phase,
        stream: Stream,
        line: String,
    },
    /// Остановился на контрольной точке.
    Waiting {
        dir: PathBuf,
        gate: String,
    },
    Done {
        dir: PathBuf,
        elapsed: Duration,
    },
    Failed {
        dir: PathBuf,
        phase: Phase,
        code: Option<i32>,
        error: String,
    },
}

impl Event {
    pub fn dir(&self) -> &Path {
        match self {
            Event::ProjectStarted { dir }
            | Event::Phase { dir, .. }
            | Event::Line { dir, .. }
            | Event::Waiting { dir, .. }
            | Event::Done { dir, .. }
            | Event::Failed { dir, .. } => dir,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Outcome {
    Done,
    Waiting {
        gate: String,
    },
    Failed {
        phase: Phase,
        code: Option<i32>,
        error: String,
    },
    /// Остановлен по Ctrl+C до начала или во время фазы.
    Cancelled,
}

#[derive(Debug, Clone)]
pub struct ProjectReport {
    pub dir: PathBuf,
    pub outcome: Outcome,
    pub total: Duration,
    /// Время работы фаз (без ожидания слота).
    pub ml: Option<Duration>,
    pub render: Option<Duration>,
    /// Ожидание слотов.
    pub queued: Duration,
    /// Всего запусков процессов Python.
    pub attempts: u32,
}

#[derive(Debug, Clone, Default)]
pub struct Summary {
    pub reports: Vec<ProjectReport>,
    pub elapsed: Duration,
}

impl Summary {
    pub fn done(&self) -> usize {
        self.count(|o| matches!(o, Outcome::Done))
    }
    pub fn waiting(&self) -> usize {
        self.count(|o| matches!(o, Outcome::Waiting { .. }))
    }
    pub fn failed(&self) -> usize {
        self.count(|o| matches!(o, Outcome::Failed { .. }))
    }
    pub fn cancelled(&self) -> usize {
        self.count(|o| matches!(o, Outcome::Cancelled))
    }
    fn count(&self, f: impl Fn(&Outcome) -> bool) -> usize {
        self.reports.iter().filter(|r| f(&r.outcome)).count()
    }

    /// 0 — всё готово, 3 — кто-то ждёт подтверждения, 1 — есть ошибки (или прервано).
    pub fn exit_code(&self) -> i32 {
        if self.failed() > 0 || self.cancelled() > 0 {
            1
        } else if self.waiting() > 0 {
            3
        } else {
            0
        }
    }
}

/// Планировщик; клонируется дёшево, проекты можно добавлять на ходу (`submit`).
#[derive(Clone)]
pub struct Scheduler {
    inner: Arc<Inner>,
}

struct Inner {
    py: Python,
    cfg: SchedConfig,
    gpu: Arc<Semaphore>,
    cpu: Arc<Semaphore>,
    tx: mpsc::UnboundedSender<Event>,
    shutdown: watch::Receiver<Shutdown>,
    /// Проекты, у которых сейчас есть задача: второй процесс на тот же проект не запускается.
    busy: Mutex<HashSet<PathBuf>>,
    /// Проекты, у которых фаза «ml» уже готова (из status --json) — сразу в очередь на рендер.
    skip_ml: Mutex<HashSet<PathBuf>>,
}

impl Scheduler {
    pub fn new(
        py: Python,
        cfg: SchedConfig,
        tx: mpsc::UnboundedSender<Event>,
        shutdown: watch::Receiver<Shutdown>,
    ) -> Scheduler {
        let gpu = Arc::new(Semaphore::new(cfg.gpu_slots.max(1)));
        let cpu = Arc::new(Semaphore::new(cfg.cpu_slots.max(1)));
        Scheduler {
            inner: Arc::new(Inner {
                py,
                cfg,
                gpu,
                cpu,
                tx,
                shutdown,
                busy: Mutex::new(HashSet::new()),
                skip_ml: Mutex::new(HashSet::new()),
            }),
        }
    }

    pub fn config(&self) -> &SchedConfig {
        &self.inner.cfg
    }

    pub fn python(&self) -> &Python {
        &self.inner.py
    }

    /// Пометить проекты, у которых фаза «ml» готова: им не нужно ждать видеокарту.
    pub fn mark_ml_ready<I: IntoIterator<Item = PathBuf>>(&self, dirs: I) {
        if let Ok(mut s) = self.inner.skip_ml.lock() {
            s.extend(dirs);
        }
    }

    /// Уточнить по `status --json`, кому не нужна фаза «ml». Ошибки игнорируются (тогда фаза просто пройдёт по кэшу).
    pub async fn precheck(&self, dirs: &[PathBuf]) -> Result<usize, crate::python::BridgeError> {
        let st = self.inner.py.status(dirs).await?;
        let ready: Vec<PathBuf> = st
            .iter()
            .filter(|s| s.ml_ready())
            .map(|s| s.dir.clone())
            .collect();
        let n = ready.len();
        self.mark_ml_ready(ready);
        Ok(n)
    }

    pub fn is_busy(&self, dir: &Path) -> bool {
        self.inner
            .busy
            .lock()
            .map(|b| b.contains(dir))
            .unwrap_or(false)
    }

    /// Поставить проект в очередь. None — у проекта уже есть задача.
    pub fn submit(&self, dir: PathBuf) -> Option<JoinHandle<ProjectReport>> {
        {
            let mut busy = self.inner.busy.lock().ok()?;
            if !busy.insert(dir.clone()) {
                return None;
            }
        }
        let _ = self
            .inner
            .tx
            .send(Event::ProjectStarted { dir: dir.clone() });
        let inner = Arc::clone(&self.inner);
        Some(tokio::spawn(async move {
            let report = inner.run_project(&dir).await;
            if let Ok(mut busy) = inner.busy.lock() {
                busy.remove(&dir);
            }
            report
        }))
    }

    /// Прогнать все проекты и дождаться итогов (в порядке `dirs`).
    pub async fn run_all(&self, dirs: &[PathBuf]) -> Summary {
        let t0 = Instant::now();
        let handles: Vec<(PathBuf, Option<JoinHandle<ProjectReport>>)> = dirs
            .iter()
            .map(|d| (d.clone(), self.submit(d.clone())))
            .collect();
        let mut reports = Vec::with_capacity(handles.len());
        for (dir, h) in handles {
            let report = match h {
                Some(h) => match h.await {
                    Ok(r) => r,
                    Err(e) => failed_report(dir, Phase::Ml, None, format!("задача упала: {e}")),
                },
                None => failed_report(dir, Phase::Ml, None, "проект уже выполняется".into()),
            };
            reports.push(report);
        }
        Summary {
            reports,
            elapsed: t0.elapsed(),
        }
    }
}

fn failed_report(dir: PathBuf, phase: Phase, code: Option<i32>, error: String) -> ProjectReport {
    ProjectReport {
        dir,
        outcome: Outcome::Failed { phase, code, error },
        total: Duration::ZERO,
        ml: None,
        render: None,
        queued: Duration::ZERO,
        attempts: 0,
    }
}

enum PhaseResult {
    Ok,
    Stop(Outcome),
}

impl Inner {
    async fn run_project(&self, dir: &Path) -> ProjectReport {
        let t0 = Instant::now();
        let mut report = ProjectReport {
            dir: dir.to_path_buf(),
            outcome: Outcome::Done,
            total: Duration::ZERO,
            ml: None,
            render: None,
            queued: Duration::ZERO,
            attempts: 0,
        };
        let skip_ml = self
            .skip_ml
            .lock()
            .map(|mut s| s.remove(dir))
            .unwrap_or(false);
        let phases: &[Phase] = if skip_ml {
            &[Phase::Render]
        } else {
            &[Phase::Ml, Phase::Render]
        };
        for &phase in phases {
            match self.run_phase(dir, phase, &mut report).await {
                PhaseResult::Ok => {}
                PhaseResult::Stop(o) => {
                    report.outcome = o;
                    break;
                }
            }
        }
        report.total = t0.elapsed();
        let ev = match &report.outcome {
            Outcome::Done => Event::Done {
                dir: dir.to_path_buf(),
                elapsed: report.total,
            },
            Outcome::Waiting { gate } => Event::Waiting {
                dir: dir.to_path_buf(),
                gate: gate.clone(),
            },
            Outcome::Failed { phase, code, error } => Event::Failed {
                dir: dir.to_path_buf(),
                phase: *phase,
                code: *code,
                error: error.clone(),
            },
            Outcome::Cancelled => Event::Failed {
                dir: dir.to_path_buf(),
                phase: Phase::Ml,
                code: None,
                error: "остановлено".into(),
            },
        };
        let _ = self.tx.send(ev);
        report
    }

    async fn run_phase(&self, dir: &Path, phase: Phase, report: &mut ProjectReport) -> PhaseResult {
        let sem = match phase {
            Phase::Ml => Arc::clone(&self.gpu),
            Phase::Render => Arc::clone(&self.cpu),
        };
        let opts = RunOpts {
            yes: self.cfg.yes,
            until: (phase == Phase::Ml).then(|| "music".to_string()),
            edits: self.cfg.edits.clone(),
        };
        let mut attempt = 0;
        loop {
            attempt += 1;
            let q0 = Instant::now();
            let mut shutdown = self.shutdown.clone();
            // ждём слот, но не дольше, чем до Ctrl+C
            let permit = tokio::select! {
                p = Arc::clone(&sem).acquire_owned() => match p {
                    Ok(p) => p,
                    Err(_) => return PhaseResult::Stop(Outcome::Cancelled),
                },
                _ = wait_stop(&mut shutdown) => return PhaseResult::Stop(Outcome::Cancelled),
            };
            if *self.shutdown.borrow() != Shutdown::Run {
                return PhaseResult::Stop(Outcome::Cancelled);
            }
            report.queued += q0.elapsed();
            report.attempts += 1;
            let _ = self.tx.send(Event::Phase {
                dir: dir.to_path_buf(),
                phase,
                attempt,
            });
            let t = Instant::now();
            let tx = self.tx.clone();
            let d = dir.to_path_buf();
            let exit = self
                .py
                .run_streaming(dir, &opts, self.shutdown.clone(), |stream, line| {
                    let _ = tx.send(Event::Line {
                        dir: d.clone(),
                        phase,
                        stream,
                        line: line.to_string(),
                    });
                })
                .await;
            drop(permit);
            let took = t.elapsed();
            match phase {
                Phase::Ml => report.ml = Some(report.ml.unwrap_or_default() + took),
                Phase::Render => report.render = Some(report.render.unwrap_or_default() + took),
            }
            if exit.killed {
                return PhaseResult::Stop(Outcome::Cancelled);
            }
            match exit.code {
                Some(EXIT_OK) => return PhaseResult::Ok,
                Some(EXIT_GATE) => {
                    return PhaseResult::Stop(Outcome::Waiting {
                        gate: exit.gate().unwrap_or_else(|| "?".into()),
                    })
                }
                Some(EXIT_FAILED)
                    if attempt <= self.cfg.retries && *self.shutdown.borrow() == Shutdown::Run =>
                {
                    let _ = self.tx.send(Event::Line {
                        dir: dir.to_path_buf(),
                        phase,
                        stream: Stream::Stderr,
                        line: format!(
                            "↻ повтор {attempt}/{}: {}",
                            self.cfg.retries,
                            exit.error_message()
                        ),
                    });
                    continue;
                }
                code => {
                    let mut error = exit.error_message();
                    if code == Some(EXIT_CONFIG) && !error.contains("настрой") {
                        error = format!("ошибка настройки: {error}");
                    }
                    return PhaseResult::Stop(Outcome::Failed { phase, code, error });
                }
            }
        }
    }
}

async fn wait_stop(rx: &mut watch::Receiver<Shutdown>) {
    loop {
        if *rx.borrow_and_update() != Shutdown::Run {
            return;
        }
        if rx.changed().await.is_err() {
            // отправитель закрыт — останова не будет
            std::future::pending::<()>().await;
        }
    }
}

/// Последняя содержательная строка прогресса для компактного вывода: «▸ voice», «✓ voice 12.3 с» и т. п.
pub fn progress_hint(line: &str) -> Option<String> {
    let t = line.trim();
    if t.is_empty() {
        return None;
    }
    let first = t.chars().next()?;
    if matches!(first, '▸' | '✓' | '·' | '✗' | '■' | '─' | '↻') {
        Some(t.split_whitespace().collect::<Vec<_>>().join(" "))
    } else {
        None
    }
}
