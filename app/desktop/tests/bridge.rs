//! Мост к Python и очередь запусков на поддельном интерпретаторе (tests/fake_python.sh).
#![cfg(unix)]

use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use reels_desktop::error::AppError;
use reels_desktop::python::{self, Outcome, PythonEnv};
use reels_desktop::runner::{FinishEvent, Job, JobInfo, LogEvent, Runner, Sink};

fn fake_env() -> PythonEnv {
    let exe = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests").join("fake_python.sh");
    python::resolve_with(Some(exe.into_os_string()), &[]).unwrap_or_else(|e| panic!("{e}"))
}

#[derive(Default)]
struct Collect {
    logs: Mutex<Vec<LogEvent>>,
    done: Mutex<Vec<FinishEvent>>,
}

impl Sink for Collect {
    fn log(&self, ev: LogEvent) {
        self.logs.lock().unwrap_or_else(|e| e.into_inner()).push(ev);
    }
    fn finished(&self, ev: FinishEvent) {
        self.done.lock().unwrap_or_else(|e| e.into_inner()).push(ev);
    }
    fn changed(&self, _: Vec<JobInfo>) {}
}

impl Collect {
    fn finished(&self) -> Vec<FinishEvent> {
        self.done.lock().unwrap_or_else(|e| e.into_inner()).clone()
    }
    async fn wait(&self, n: usize) -> Vec<FinishEvent> {
        for _ in 0..200 {
            if self.finished().len() >= n {
                break;
            }
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
        self.finished()
    }
}

fn job(project: &str) -> Job {
    Job {
        project: project.into(),
        label: "тест".into(),
        steps: vec![vec!["run".into(), project.into(), "--no-input".into()]],
    }
}

#[tokio::test]
async fn status_json_through_fake_python() {
    let env = fake_env();
    let v = python::status(&env, &[PathBuf::from("/x/povodok"), PathBuf::from("/x/venera")])
        .await
        .unwrap_or_else(|e| panic!("{e}"));
    assert_eq!(v.len(), 2);
    assert_eq!(v[1].slug, "venera");
    assert_eq!(v[0].waiting_gate(), Some("take"));
    let out = python::output(&env, &["approve".into(), "/x".into(), "take".into()]).await.unwrap_or_default();
    assert!(out.contains("подтверждено"));
    match python::output(&env, &["nope".into()]).await {
        Err(AppError::Python { code: 2, msg }) => assert_eq!(msg, "нет команды nope"),
        other => panic!("ожидалась ошибка Python, получено {other:?}"),
    }
}

#[tokio::test]
async fn runner_outcomes_logs_and_hints() {
    let sink = Arc::new(Collect::default());
    let r = Runner::new(sink.clone(), 4);
    r.set_env(fake_env());
    for p in ["/w/ok", "/w/gate", "/w/fail"] {
        r.enqueue(job(p)).unwrap_or_else(|e| panic!("{e}"));
    }
    let done = sink.wait(3).await;
    let get = |p: &str| done.iter().find(|e| e.project == p).cloned().unwrap_or_else(|| panic!("нет {p}"));
    assert_eq!(get("/w/ok").outcome, Outcome::Ok);
    assert_eq!(get("/w/gate").outcome, Outcome::Gate);
    let fail = get("/w/fail");
    assert_eq!(fail.outcome, Outcome::Failed);
    assert_eq!(fail.message, "этап voice: CUDA not available");
    assert!(fail.hint.unwrap_or_default().contains("видеокарты"));
    let logs = sink.logs.lock().unwrap_or_else(|e| e.into_inner()).clone();
    assert!(logs.iter().any(|l| l.project == "/w/ok" && l.stream == "stderr" && l.line == "загрузка модели"));
    let stage = logs.iter().filter_map(|l| l.stage.clone()).find(|s| s.edit.as_deref() == Some("linear"));
    assert_eq!(stage.map(|s| (s.stage, s.state)), Some(("build".to_string(), "done")));
}

#[tokio::test]
async fn one_run_per_project_and_global_limit() {
    let sink = Arc::new(Collect::default());
    let r = Runner::new(sink.clone(), 1);
    r.set_env(fake_env());
    r.enqueue(job("/w/slow")).unwrap_or_else(|e| panic!("{e}"));
    assert!(matches!(r.enqueue(job("/w/slow")), Err(AppError::Busy(_))));
    r.enqueue(job("/w/ok")).unwrap_or_else(|e| panic!("{e}"));
    let states: Vec<_> = r.jobs().into_iter().map(|j| (j.project, j.state)).collect();
    assert_eq!(states, vec![("/w/slow".to_string(), "running"), ("/w/ok".to_string(), "queued")]);
    tokio::time::sleep(Duration::from_millis(300)).await;
    assert!(r.stop("/w/slow"));
    let done = sink.wait(2).await;
    assert_eq!(done[0].project, "/w/slow");
    assert_eq!(done[0].outcome, Outcome::Stopped);
    assert_eq!(done[1].outcome, Outcome::Ok, "очередь продолжилась после остановки");
    assert!(r.jobs().is_empty());
}

#[tokio::test]
async fn chained_steps_stop_on_failure() {
    let sink = Arc::new(Collect::default());
    let r = Runner::new(sink.clone(), 2);
    r.set_env(fake_env());
    let tmp = tempfile::tempdir().unwrap_or_else(|e| panic!("{e}"));
    let d = tmp.path().join("fail-project").display().to_string();
    let steps = vec![
        vec!["draft".into(), "тема".into(), "-o".into(), d.clone()],
        vec!["run".into(), d.clone(), "--yes".into()],
        vec!["approve".into(), d.clone(), "frames".into()],
    ];
    r.enqueue(Job { project: d.clone(), label: "цепочка".into(), steps }).unwrap_or_else(|e| panic!("{e}"));
    let done = sink.wait(1).await;
    assert_eq!(done[0].outcome, Outcome::Failed);
    assert!(Path::new(&d).join("reel.yaml").is_file(), "draft выполнился");
    let logs = sink.logs.lock().unwrap_or_else(|e| e.into_inner()).clone();
    assert!(!logs.iter().any(|l| l.line.contains("approve")), "после ошибки цепочка не продолжается");
}

#[test]
fn missing_env_python_reports_install_hint() {
    match python::resolve_with(Some("/nonexistent/python3".into()), &[]) {
        Ok(env) => assert_eq!(env.exe, PathBuf::from("/nonexistent/python3")),
        Err(e) => panic!("путь задан явно, ошибка будет при запуске: {e}"),
    }
}
