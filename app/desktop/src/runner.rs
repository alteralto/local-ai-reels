//! Очередь запусков: не больше одного запуска на проект, общий предел одновременных запусков,
//! живой вывод stdout/stderr Python, остановка всего дерева процессов.
use std::collections::{HashMap, VecDeque};
use std::process::Stdio;
use std::sync::{Arc, Mutex, MutexGuard};

use serde::Serialize;
use tokio::io::{AsyncBufReadExt, AsyncRead, BufReader};
use tokio::sync::watch;

use crate::error::{hint, AppError, Result};
use crate::python::{self, Outcome, PythonEnv, StageEvent};

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LogEvent {
    pub job_id: u64,
    pub project: String,
    pub stream: &'static str,
    pub line: String,
    pub stage: Option<StageEvent>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct FinishEvent {
    pub job_id: u64,
    pub project: String,
    pub label: String,
    pub outcome: Outcome,
    pub code: Option<i32>,
    pub message: String,
    pub hint: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct JobInfo {
    pub id: u64,
    pub project: String,
    pub label: String,
    pub state: &'static str,
}

/// Куда отдавать события (окно Tauri или тест).
pub trait Sink: Send + Sync + 'static {
    fn log(&self, ev: LogEvent);
    fn finished(&self, ev: FinishEvent);
    fn changed(&self, jobs: Vec<JobInfo>);
}

/// Задание: последовательность команд `python -m reels …`; следующая выполняется, только если предыдущая
/// закончилась кодом 0.
#[derive(Debug, Clone)]
pub struct Job {
    pub project: String,
    pub label: String,
    pub steps: Vec<Vec<String>>,
}

struct Queued {
    id: u64,
    job: Job,
}

struct Running {
    label: String,
    stop: watch::Sender<bool>,
}

struct Inner {
    concurrency: usize,
    next_id: u64,
    queue: VecDeque<Queued>,
    running: HashMap<String, (u64, Running)>,
}

pub struct Runner {
    inner: Mutex<Inner>,
    env: Mutex<Option<PythonEnv>>,
    sink: Arc<dyn Sink>,
}

impl Runner {
    pub fn new(sink: Arc<dyn Sink>, concurrency: usize) -> Arc<Self> {
        Arc::new(Runner {
            inner: Mutex::new(Inner {
                concurrency: concurrency.max(1),
                next_id: 1,
                queue: VecDeque::new(),
                running: HashMap::new(),
            }),
            env: Mutex::new(None),
            sink,
        })
    }

    fn lock(&self) -> MutexGuard<'_, Inner> {
        self.inner.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// Python для следующих запусков (устанавливается приложением перед enqueue).
    pub fn set_env(&self, env: PythonEnv) {
        *self.env.lock().unwrap_or_else(|e| e.into_inner()) = Some(env);
    }

    fn env(&self) -> Option<PythonEnv> {
        self.env.lock().unwrap_or_else(|e| e.into_inner()).clone()
    }

    pub fn set_concurrency(self: &Arc<Self>, n: usize) {
        self.lock().concurrency = n.clamp(1, 16);
        self.pump();
    }

    pub fn jobs(&self) -> Vec<JobInfo> {
        let g = self.lock();
        let mut v: Vec<JobInfo> = g
            .running
            .iter()
            .map(|(p, (id, r))| JobInfo { id: *id, project: p.clone(), label: r.label.clone(), state: "running" })
            .collect();
        v.sort_by_key(|j| j.id);
        v.extend(g.queue.iter().map(|q| JobInfo {
            id: q.id,
            project: q.job.project.clone(),
            label: q.job.label.clone(),
            state: "queued",
        }));
        v
    }

    pub fn is_busy(&self, project: &str) -> bool {
        let g = self.lock();
        g.running.contains_key(project) || g.queue.iter().any(|q| q.job.project == project)
    }

    /// Ставит задание в очередь. Второй запуск того же проекта — ошибка «уже выполняется».
    pub fn enqueue(self: &Arc<Self>, job: Job) -> Result<u64> {
        if job.steps.is_empty() {
            return Err(AppError::Invalid("пустое задание".into()));
        }
        let id = {
            let mut g = self.lock();
            if g.running.contains_key(&job.project) || g.queue.iter().any(|q| q.job.project == job.project) {
                return Err(AppError::Busy(job.project));
            }
            let id = g.next_id;
            g.next_id += 1;
            g.queue.push_back(Queued { id, job });
            id
        };
        self.pump();
        Ok(id)
    }

    /// Останавливает запуск проекта или убирает его из очереди. true — было что останавливать.
    pub fn stop(self: &Arc<Self>, project: &str) -> bool {
        let found = {
            let mut g = self.lock();
            let before = g.queue.len();
            g.queue.retain(|q| q.job.project != project);
            let dequeued = g.queue.len() != before;
            let running = g.running.get(project).map(|(_, r)| r.stop.send(true).is_ok()).unwrap_or(false);
            dequeued || running
        };
        self.sink.changed(self.jobs());
        found
    }

    fn pump(self: &Arc<Self>) {
        let mut started = Vec::new();
        {
            let mut g = self.lock();
            while g.running.len() < g.concurrency {
                let Some(q) = g.queue.pop_front() else { break };
                let (tx, rx) = watch::channel(false);
                g.running.insert(q.job.project.clone(), (q.id, Running { label: q.job.label.clone(), stop: tx }));
                started.push((q, rx));
            }
        }
        for (q, rx) in started {
            let me = Arc::clone(self);
            tauri::async_runtime::spawn(async move { me.exec(q.id, q.job, rx).await });
        }
        self.sink.changed(self.jobs());
    }

    async fn exec(self: Arc<Self>, id: u64, job: Job, mut stop: watch::Receiver<bool>) {
        let (outcome, code, tail) = match self.env() {
            None => (Outcome::Failed, None, AppError::PythonNotFound.to_string()),
            Some(env) => self.exec_steps(id, &job, &env, &mut stop).await,
        };
        let message = match outcome {
            Outcome::Ok | Outcome::Stopped | Outcome::Gate => outcome.text().to_string(),
            _ => {
                let last = python::last_line(&tail);
                if last.is_empty() {
                    outcome.text().to_string()
                } else {
                    last.trim_start_matches('✗').trim().to_string()
                }
            }
        };
        let h = match outcome {
            Outcome::Failed | Outcome::Config => hint(&tail).map(str::to_string),
            _ => None,
        };
        {
            let mut g = self.lock();
            g.running.remove(&job.project);
        }
        self.sink.finished(FinishEvent {
            job_id: id,
            project: job.project.clone(),
            label: job.label.clone(),
            outcome,
            code,
            message,
            hint: h,
        });
        self.pump();
    }

    async fn exec_steps(
        &self,
        id: u64,
        job: &Job,
        env: &PythonEnv,
        stop: &mut watch::Receiver<bool>,
    ) -> (Outcome, Option<i32>, String) {
        let tail = Arc::new(Mutex::new(VecDeque::<String>::new()));
        let tail_text = |t: &Arc<Mutex<VecDeque<String>>>| {
            t.lock().unwrap_or_else(|e| e.into_inner()).iter().cloned().collect::<Vec<_>>().join("\n")
        };
        for step in &job.steps {
            if *stop.borrow() {
                return (Outcome::Stopped, None, tail_text(&tail));
            }
            self.sink.log(LogEvent {
                job_id: id,
                project: job.project.clone(),
                stream: "cmd",
                line: format!("$ python -m reels {}", step.join(" ")),
                stage: None,
            });
            let mut cmd = python::command(env, step);
            cmd.stdout(Stdio::piped()).stderr(Stdio::piped());
            #[cfg(unix)]
            cmd.process_group(0);
            let mut child = match cmd.spawn() {
                Ok(c) => c,
                Err(e) => {
                    let msg = AppError::Spawn { exe: env.exe.display().to_string(), msg: crate::error::io_text(&e) };
                    return (Outcome::Failed, None, msg.to_string());
                }
            };
            let pid = child.id();
            let readers = [
                child.stdout.take().map(|s| self.pipe(id, job, "stdout", s, Arc::clone(&tail))),
                child.stderr.take().map(|s| self.pipe(id, job, "stderr", s, Arc::clone(&tail))),
            ];
            let status = tokio::select! {
                s = child.wait() => s.ok(),
                _ = stop.changed() => {
                    kill_tree(pid);
                    let _ = child.kill().await;
                    let _ = child.wait().await;
                    for r in readers.into_iter().flatten() { let _ = r.await; }
                    return (Outcome::Stopped, None, tail_text(&tail));
                }
            };
            for r in readers.into_iter().flatten() {
                let _ = r.await;
            }
            let code = status.and_then(|s| s.code());
            let outcome = if status.is_none() { Outcome::Failed } else { Outcome::from_code(code) };
            if outcome != Outcome::Ok {
                return (outcome, code, tail_text(&tail));
            }
        }
        (Outcome::Ok, Some(0), tail_text(&tail))
    }

    fn pipe<R: AsyncRead + Unpin + Send + 'static>(
        &self,
        id: u64,
        job: &Job,
        stream: &'static str,
        reader: R,
        tail: Arc<Mutex<VecDeque<String>>>,
    ) -> tauri::async_runtime::JoinHandle<()> {
        let sink = Arc::clone(&self.sink);
        let project = job.project.clone();
        tauri::async_runtime::spawn(async move {
            let mut r = BufReader::new(reader);
            let mut buf = Vec::new();
            loop {
                buf.clear();
                match r.read_until(b'\n', &mut buf).await {
                    Ok(0) | Err(_) => break,
                    Ok(_) => {}
                }
                let raw = String::from_utf8_lossy(&buf);
                // прогресс-бары (tqdm) переписывают строку через \r — берём последнее состояние
                let line = raw.trim_end_matches(['\n', '\r']).rsplit('\r').next().unwrap_or("").to_string();
                if line.trim().is_empty() {
                    continue;
                }
                {
                    let mut t = tail.lock().unwrap_or_else(|e| e.into_inner());
                    t.push_back(line.clone());
                    if t.len() > 40 {
                        t.pop_front();
                    }
                }
                let stage = python::parse_progress(&line);
                sink.log(LogEvent { job_id: id, project: project.clone(), stream, line, stage });
            }
        })
    }
}

/// Останавливает Python и всё, что он запустил (ffmpeg, node, браузер).
fn kill_tree(pid: Option<u32>) {
    let Some(pid) = pid else { return };
    #[cfg(unix)]
    {
        if let Ok(pgid) = i32::try_from(pid) {
            // SAFETY: kill(2) с отрицательным pid шлёт сигнал группе процессов, созданной process_group(0).
            unsafe {
                libc::kill(-pgid, libc::SIGTERM);
            }
        }
    }
    #[cfg(windows)]
    {
        let _ = std::process::Command::new("taskkill").args(["/T", "/F", "/PID", &pid.to_string()]).status();
    }
}

/// Папка проекта как ключ очереди.
pub fn key(dir: &std::path::Path) -> String {
    dir.display().to_string()
}
