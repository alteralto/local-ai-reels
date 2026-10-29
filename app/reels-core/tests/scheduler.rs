//! Планировщик с поддельным Python: сценарий на sh спит, печатает строки прогресса и выходит с кодом из fake.env.
#![cfg(unix)]

use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::time::Duration;

use reels_core::python::{Python, Shutdown};
use reels_core::scheduler::{Event, Outcome, Phase, SchedConfig, Scheduler};
use tokio::sync::{mpsc, watch};

const FAKE: &str = r#"#!/bin/sh
# поддельный python: -m reels <команда> …
shift 2
cmd=$1; shift
case "$cmd" in
run)
  dir=$1; shift
  phase=render; yes=0; prev=""
  for a in "$@"; do
    [ "$prev" = "--until" ] && [ "$a" = "music" ] && phase=ml
    [ "$a" = "--yes" ] && yes=1
    prev=$a
  done
  name=$(basename "$dir")
  ML_SLEEP=0.3; RENDER_SLEEP=0.3; ML_EXIT=0; RENDER_EXIT=0; ML_FAIL_TIMES=0
  [ -f "$dir/fake.env" ] && . "$dir/fake.env"
  echo "start $phase $name" >> "$FAKE_LOG"
  echo "■ $name  ($dir)"
  if [ "$phase" = ml ]; then
    echo "  ▸ voice"; sleep "$ML_SLEEP"; echo "  ✓ voice  $ML_SLEEP с"; code=$ML_EXIT
    cnt="$dir/.fake-ml-count"; n=$(cat "$cnt" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" > "$cnt"
    [ "$n" -le "$ML_FAIL_TIMES" ] && code=1
  else
    echo "  ▸ render [linear]"; sleep "$RENDER_SLEEP"; echo "  ✓ render [linear]  $RENDER_SLEEP с"; code=$RENDER_EXIT
  fi
  [ "$code" = 3 ] && [ "$yes" = 1 ] && code=0
  echo "end $phase $name" >> "$FAKE_LOG"
  if [ "$code" = 3 ]; then
    echo ""; echo "── ждёт подтверждения «take» ──"; echo "Подтвердить: python -m reels approve $dir take"
  elif [ "$code" = 1 ]; then
    echo "Traceback (most recent call last):" >&2; echo "✗ этап $phase: модель не загрузилась" >&2
  elif [ "$code" = 2 ]; then
    echo "✗ $dir/reel.yaml: нет blocks" >&2
  fi
  exit "$code"
  ;;
*)
  echo "unknown $cmd" >&2; exit 2;;
esac
"#;

struct Env {
    _tmp: tempfile::TempDir,
    root: PathBuf,
    log: PathBuf,
    py: Python,
}

fn env() -> Env {
    let tmp = tempfile::tempdir().expect("tempdir");
    let root = tmp.path().to_path_buf();
    let fake = root.join("fake-python");
    std::fs::write(&fake, FAKE).expect("write fake");
    std::fs::set_permissions(&fake, std::fs::Permissions::from_mode(0o755)).expect("chmod");
    let log = root.join("log.txt");
    std::fs::write(&log, "").expect("log");
    // FAKE_LOG наследуется дочерними процессами
    std::env::set_var("FAKE_LOG", &log);
    let py = Python {
        exe: fake.into_os_string(),
        repo: None,
    };
    Env {
        _tmp: tmp,
        root,
        log,
        py,
    }
}

fn project(root: &Path, name: &str, conf: &str) -> PathBuf {
    let d = root.join("projects").join(name);
    std::fs::create_dir_all(&d).expect("mkdir");
    std::fs::write(d.join("reel.yaml"), "slug: x\n").expect("yaml");
    std::fs::write(d.join("fake.env"), conf).expect("conf");
    d
}

/// Журнал «start|end фаза проект» в порядке событий.
fn read_log(log: &Path) -> Vec<(String, String, String)> {
    std::fs::read_to_string(log)
        .expect("log")
        .lines()
        .filter_map(|l| {
            let mut it = l.split_whitespace();
            Some((it.next()?.into(), it.next()?.into(), it.next()?.into()))
        })
        .collect()
}

fn max_concurrent(log: &[(String, String, String)], phase: &str) -> usize {
    let (mut cur, mut best) = (0usize, 0usize);
    for (ev, ph, _) in log {
        if ph != phase {
            continue;
        }
        if ev == "start" {
            cur += 1;
            best = best.max(cur);
        } else {
            cur = cur.saturating_sub(1);
        }
    }
    best
}

fn sched(
    e: &Env,
    cfg: SchedConfig,
) -> (
    Scheduler,
    mpsc::UnboundedReceiver<Event>,
    watch::Sender<Shutdown>,
) {
    let (tx, rx) = mpsc::unbounded_channel();
    let (stx, srx) = watch::channel(Shutdown::Run);
    (Scheduler::new(e.py.clone(), cfg, tx, srx), rx, stx)
}

fn drain(rx: &mut mpsc::UnboundedReceiver<Event>) -> Vec<Event> {
    let mut v = Vec::new();
    while let Ok(ev) = rx.try_recv() {
        v.push(ev);
    }
    v
}

// Тесты меняют общую переменную FAKE_LOG, поэтому идут по одному (в одном тесте).
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn scheduler_scenarios() {
    conveyor_overlap_and_limits().await;
    waiting_failed_and_retries().await;
    yes_passes_through().await;
    kill_on_shutdown().await;
}

async fn conveyor_overlap_and_limits() {
    let e = env();
    let dirs: Vec<PathBuf> = ["a", "b", "c", "d"]
        .iter()
        .map(|n| project(&e.root, n, "ML_SLEEP=0.4\nRENDER_SLEEP=0.6\n"))
        .collect();
    let cfg = SchedConfig {
        gpu_slots: 1,
        cpu_slots: 2,
        ..SchedConfig::default()
    };
    let (s, mut rx, _stop) = sched(&e, cfg);
    let summary = s.run_all(&dirs).await;
    assert_eq!(summary.done(), 4, "{:?}", summary.reports);
    assert_eq!(summary.exit_code(), 0);
    let log = read_log(&e.log);
    assert_eq!(log.len(), 16, "{log:?}");
    assert_eq!(
        max_concurrent(&log, "ml"),
        1,
        "gpu=1: две фазы ml одновременно: {log:?}"
    );
    assert!(
        max_concurrent(&log, "render") <= 2,
        "cpu=2 превышен: {log:?}"
    );
    // конвейер: ml одного проекта идёт, пока другой рендерится
    let mut render_active = 0usize;
    let mut overlapped = false;
    for (ev, ph, _) in &log {
        match (ev.as_str(), ph.as_str()) {
            ("start", "render") => render_active += 1,
            ("end", "render") => render_active -= 1,
            ("start", "ml") if render_active > 0 => overlapped = true,
            _ => {}
        }
    }
    assert!(overlapped, "ml не перекрывается с render: {log:?}");
    // у каждого проекта ml строго до render, и никогда два процесса сразу
    for n in ["a", "b", "c", "d"] {
        let own: Vec<_> = log
            .iter()
            .filter(|(_, _, p)| p == n)
            .map(|(e, p, _)| format!("{e} {p}"))
            .collect();
        assert_eq!(
            own,
            ["start ml", "end ml", "start render", "end render"],
            "{n}: {own:?}"
        );
    }
    // быстрее последовательного прогона (4×1.0 с)
    assert!(
        summary.elapsed < Duration::from_millis(3600),
        "{:?}",
        summary.elapsed
    );
    let events = drain(&mut rx);
    assert_eq!(
        events
            .iter()
            .filter(|e| matches!(e, Event::Done { .. }))
            .count(),
        4
    );
    assert!(events
        .iter()
        .any(|e| matches!(e, Event::Line { line, .. } if line.contains("▸ voice"))));
    assert!(events.iter().any(|e| matches!(
        e,
        Event::Phase {
            phase: Phase::Render,
            ..
        }
    )));
    for r in &summary.reports {
        assert!(r.ml.is_some() && r.render.is_some());
        assert_eq!(r.attempts, 2);
    }
}

async fn waiting_failed_and_retries() {
    let e = env();
    let ok = project(&e.root, "ok", "ML_SLEEP=0.1\nRENDER_SLEEP=0.1\n");
    let gate = project(&e.root, "gate", "ML_SLEEP=0.1\nML_EXIT=3\n");
    let bad = project(
        &e.root,
        "bad",
        "ML_SLEEP=0.1\nRENDER_SLEEP=0.1\nRENDER_EXIT=1\n",
    );
    let cfgerr = project(&e.root, "cfgerr", "ML_SLEEP=0.1\nML_EXIT=2\n");
    let flaky = project(
        &e.root,
        "flaky",
        "ML_SLEEP=0.1\nRENDER_SLEEP=0.1\nML_FAIL_TIMES=1\n",
    );
    let cfg = SchedConfig {
        gpu_slots: 2,
        cpu_slots: 2,
        retries: 1,
        ..SchedConfig::default()
    };
    let (s, mut rx, _stop) = sched(&e, cfg);
    let summary = s
        .run_all(&[
            ok.clone(),
            gate.clone(),
            bad.clone(),
            cfgerr.clone(),
            flaky.clone(),
        ])
        .await;
    let by = |d: &PathBuf| {
        summary
            .reports
            .iter()
            .find(|r| &r.dir == d)
            .expect("report")
    };
    assert_eq!(by(&ok).outcome, Outcome::Done);
    assert_eq!(
        by(&gate).outcome,
        Outcome::Waiting {
            gate: "take".into()
        }
    );
    assert!(
        by(&gate).render.is_none(),
        "после точки рендер не запускается"
    );
    match &by(&bad).outcome {
        Outcome::Failed { phase, code, error } => {
            assert_eq!(*phase, Phase::Render);
            assert_eq!(*code, Some(1));
            assert!(error.contains("модель не загрузилась"), "{error}");
        }
        o => panic!("bad: {o:?}"),
    }
    assert_eq!(by(&bad).attempts, 3, "ml + render + 1 повтор");
    match &by(&cfgerr).outcome {
        Outcome::Failed { code, .. } => assert_eq!(*code, Some(2)),
        o => panic!("cfgerr: {o:?}"),
    }
    assert_eq!(by(&cfgerr).attempts, 1, "код 2 не повторяется");
    assert_eq!(by(&flaky).outcome, Outcome::Done, "повтор после кода 1");
    assert_eq!(by(&flaky).attempts, 3);
    assert_eq!(summary.exit_code(), 1);
    assert_eq!(
        (summary.done(), summary.waiting(), summary.failed()),
        (2, 1, 2)
    );
    let events = drain(&mut rx);
    assert!(events
        .iter()
        .any(|e| matches!(e, Event::Waiting { gate: g, .. } if g == "take")));
    assert!(events
        .iter()
        .any(|e| matches!(e, Event::Line { line, .. } if line.starts_with("↻ повтор"))));

    // только ожидание → код 3
    let (s, _rx, _stop) = sched(&e, SchedConfig::default());
    assert_eq!(s.run_all(&[gate]).await.exit_code(), 3);
}

async fn yes_passes_through() {
    let e = env();
    let gate = project(
        &e.root,
        "gate",
        "ML_SLEEP=0.1\nRENDER_SLEEP=0.1\nML_EXIT=3\n",
    );
    let cfg = SchedConfig {
        yes: true,
        ..SchedConfig::default()
    };
    let (s, _rx, _stop) = sched(&e, cfg);
    let summary = s.run_all(&[gate]).await;
    assert_eq!(summary.exit_code(), 0, "{:?}", summary.reports);
}

async fn kill_on_shutdown() {
    let e = env();
    let slow = project(&e.root, "slow", "ML_SLEEP=30\n");
    let queued = project(&e.root, "queued", "ML_SLEEP=30\n");
    let (s, _rx, stop) = sched(&e, SchedConfig::default());
    let h1 = s.submit(slow.clone()).expect("submit");
    assert!(
        s.submit(slow.clone()).is_none(),
        "второй процесс на тот же проект"
    );
    let h2 = s.submit(queued).expect("submit");
    tokio::time::sleep(Duration::from_millis(300)).await;
    stop.send(Shutdown::Kill).expect("send");
    let r1 = tokio::time::timeout(Duration::from_secs(5), h1)
        .await
        .expect("убит вовремя")
        .expect("join");
    let r2 = tokio::time::timeout(Duration::from_secs(5), h2)
        .await
        .expect("снят с очереди")
        .expect("join");
    assert_eq!(r1.outcome, Outcome::Cancelled);
    assert_eq!(r2.outcome, Outcome::Cancelled);
    assert!(!s.is_busy(&slow));
}
