//! `reels-app watch` — демон над папкой-входящими.
//!
//! * новый проект (папка с reel.yaml) или изменённый reel.yaml → в конвейер;
//! * проект ждал подтверждения, и его .reels-state.json изменился (кто-то сделал approve) → снова в конвейер;
//! * новые строки в `<inbox>/topics.txt` → `python -m reels draft "<тема>" -o <inbox>/<slug>`;
//!   обработанные строки помнит `<inbox>/.reels-app-topics.done`.

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime};

use anyhow::{bail, Result};
use reels_core::{Event, Outcome, Python, Scheduler, Shutdown};
use tokio::sync::{mpsc, watch as wch};
use tokio::task::JoinSet;

use crate::util::{fmt_dur, name_of};
use crate::SchedArgs;

const TOPICS: &str = "topics.txt";
const TOPICS_DONE: &str = ".reels-app-topics.done";
const STATE: &str = ".reels-state.json";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct Stamp {
    yaml: Option<SystemTime>,
    state: Option<SystemTime>,
}

fn stamp(dir: &Path) -> Stamp {
    let m = |p: PathBuf| std::fs::metadata(p).and_then(|m| m.modified()).ok();
    Stamp {
        yaml: m(dir.join(reels_core::discover::CONFIG_NAME)),
        state: m(dir.join(STATE)),
    }
}

struct Seen {
    stamp: Stamp,
    outcome: Option<Outcome>,
}

/// Нужно ли снова ставить проект в очередь.
fn needs_run(prev: Option<&Seen>, now: Stamp) -> bool {
    match prev {
        None => true,
        Some(s) => {
            s.stamp.yaml != now.yaml
                || (matches!(s.outcome, Some(Outcome::Waiting { .. }))
                    && s.stamp.state != now.state)
        }
    }
}

pub async fn watch(
    py: Python,
    inbox: &Path,
    args: &SchedArgs,
    interval: Duration,
    grace: Duration,
) -> Result<i32> {
    if !inbox.is_dir() {
        bail!("{}: нет такой папки", inbox.display());
    }
    let inbox = reels_core::discover::absolute(inbox);
    let cfg = args.config();
    println!(
        "■ слежу за {} каждые {} (слоты: видеокарта {}, процессор {}). Ctrl+C — остановка.",
        inbox.display(),
        fmt_dur(interval),
        cfg.gpu_slots,
        cfg.cpu_slots
    );
    let (tx, rx) = mpsc::unbounded_channel();
    let (stop_tx, stop_rx) = wch::channel(Shutdown::Run);
    let sched = Scheduler::new(py.clone(), cfg, tx, stop_rx.clone());
    let ui = tokio::spawn(log_events(rx));
    tokio::spawn(crate::run::ctrl_c(stop_tx, grace));

    let mut seen: HashMap<PathBuf, Seen> = HashMap::new();
    let mut tasks: JoinSet<Option<reels_core::ProjectReport>> = JoinSet::new();
    let mut drafting: JoinSet<()> = JoinSet::new();
    let mut stop = stop_rx;
    let mut tick = tokio::time::interval(interval);
    loop {
        tokio::select! {
            _ = tick.tick() => {}
            Some(r) = tasks.join_next(), if !tasks.is_empty() => {
                if let Ok(Some(rep)) = r {
                    // reel.yaml — отметка с момента запуска (правку во время прогона заметим), состояние — свежее
                    let state = stamp(&rep.dir).state;
                    if let Some(s) = seen.get_mut(&rep.dir) {
                        s.stamp.state = state;
                        s.outcome = Some(rep.outcome);
                    }
                }
                continue;
            }
            _ = stop.changed() => {}
        }
        if *stop.borrow() != Shutdown::Run {
            break;
        }
        if drafting.is_empty() {
            match new_topics(&inbox) {
                Ok(topics) if !topics.is_empty() => {
                    let py = py.clone();
                    let inbox = inbox.clone();
                    drafting.spawn(async move { draft_all(&py, &inbox, topics).await });
                }
                Ok(_) => {}
                Err(e) => eprintln!("✗ {TOPICS}: {e}"),
            }
        } else {
            while drafting.try_join_next().is_some() {}
        }
        let dirs = match reels_core::discover(&[&inbox]) {
            Ok(d) => d,
            Err(e) => {
                eprintln!("✗ {e}");
                continue;
            }
        };
        for d in dirs {
            if sched.is_busy(&d) {
                continue;
            }
            let now = stamp(&d);
            if needs_run(seen.get(&d), now) {
                // отметка до запуска: изменения во время прогона заметим в следующий раз
                seen.insert(
                    d.clone(),
                    Seen {
                        stamp: now,
                        outcome: None,
                    },
                );
                if let Some(h) = sched.submit(d) {
                    tasks.spawn(async move { h.await.ok() });
                }
            }
        }
    }
    let running = tasks.len();
    if running > 0 {
        eprintln!("  жду {running} проект(ов)…");
    }
    while tasks.join_next().await.is_some() {}
    drafting.abort_all();
    drop(sched);
    let _ = ui.await;
    println!("■ остановлено");
    Ok(0)
}

async fn log_events(mut rx: mpsc::UnboundedReceiver<Event>) {
    while let Some(ev) = rx.recv().await {
        let name = name_of(ev.dir());
        let line = match &ev {
            Event::ProjectStarted { .. } => "в очереди".to_string(),
            Event::Phase { phase, attempt, .. } if *attempt > 1 => {
                format!("▶ {phase} (попытка {attempt})")
            }
            Event::Phase { phase, .. } => format!("▶ {phase}"),
            Event::Line { line, .. } => match reels_core::scheduler::progress_hint(line) {
                Some(h) => h,
                None => continue,
            },
            Event::Waiting { gate, .. } => format!("… ждёт подтверждения «{gate}»"),
            Event::Done { elapsed, .. } => format!("✓ готово за {}", fmt_dur(*elapsed)),
            Event::Failed { phase, error, .. } => {
                format!("✗ {phase}: {}", error.lines().next().unwrap_or(""))
            }
        };
        println!("{} [{name}] {line}", clock());
    }
}

fn clock() -> String {
    let t = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    // UTC — без зависимости от часовых поясов
    format!("{:02}:{:02}:{:02}", (t / 3600) % 24, (t / 60) % 60, t % 60)
}

/// Новые темы из topics.txt (пустые строки и # — пропускаются).
fn new_topics(inbox: &Path) -> std::io::Result<Vec<String>> {
    let path = inbox.join(TOPICS);
    if !path.is_file() {
        return Ok(Vec::new());
    }
    let done: HashSet<String> = std::fs::read_to_string(inbox.join(TOPICS_DONE))
        .unwrap_or_default()
        .lines()
        .map(|l| l.trim().to_string())
        .collect();
    let text = std::fs::read_to_string(path)?;
    let mut out = Vec::new();
    for l in text.lines().map(str::trim) {
        if l.is_empty()
            || l.starts_with('#')
            || done.contains(l)
            || out.iter().any(|x: &String| x == l)
        {
            continue;
        }
        out.push(l.to_string());
    }
    Ok(out)
}

async fn draft_all(py: &Python, inbox: &Path, topics: Vec<String>) {
    use std::io::Write;
    for topic in topics {
        let dir = unique_dir(inbox, &slugify(&topic));
        println!(
            "{} [{}] ✎ черновик по теме «{topic}»",
            clock(),
            name_of(&dir)
        );
        match py.draft(&topic, &dir).await {
            Ok(_) => println!("{} [{}] ✓ черновик готов", clock(), name_of(&dir)),
            Err(e) => eprintln!("{} [{}] ✗ черновик: {e}", clock(), name_of(&dir)),
        }
        // помечаем и при ошибке, чтобы не повторять бесконечно; повтор — удалить строку из .reels-app-topics.done
        if let Ok(mut f) = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(inbox.join(TOPICS_DONE))
        {
            let _ = writeln!(f, "{topic}");
        }
    }
}

fn unique_dir(inbox: &Path, slug: &str) -> PathBuf {
    let mut d = inbox.join(slug);
    let mut i = 2;
    while d.exists() {
        d = inbox.join(format!("{slug}-{i}"));
        i += 1;
    }
    d
}

/// Латиница из темы: «Почему кошки мурчат?» → pochemu-koshki-murchat.
pub fn slugify(s: &str) -> String {
    const MAP: [(&str, &str); 33] = [
        ("а", "a"),
        ("б", "b"),
        ("в", "v"),
        ("г", "g"),
        ("д", "d"),
        ("е", "e"),
        ("ё", "e"),
        ("ж", "zh"),
        ("з", "z"),
        ("и", "i"),
        ("й", "y"),
        ("к", "k"),
        ("л", "l"),
        ("м", "m"),
        ("н", "n"),
        ("о", "o"),
        ("п", "p"),
        ("р", "r"),
        ("с", "s"),
        ("т", "t"),
        ("у", "u"),
        ("ф", "f"),
        ("х", "kh"),
        ("ц", "ts"),
        ("ч", "ch"),
        ("ш", "sh"),
        ("щ", "sch"),
        ("ъ", ""),
        ("ы", "y"),
        ("ь", ""),
        ("э", "e"),
        ("ю", "yu"),
        ("я", "ya"),
    ];
    let mut out = String::new();
    for c in s.to_lowercase().chars() {
        let mut buf = [0u8; 4];
        let cs: &str = c.encode_utf8(&mut buf);
        if let Some((_, lat)) = MAP.iter().find(|(cyr, _)| *cyr == cs) {
            out.push_str(lat);
        } else if c.is_ascii_alphanumeric() {
            out.push(c);
        } else if !out.ends_with('-') {
            out.push('-');
        }
    }
    let mut slug: String = out.trim_matches('-').chars().take(40).collect();
    slug = slug.trim_end_matches('-').to_string();
    if slug.is_empty() {
        "reel".into()
    } else {
        slug
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn slugs() {
        assert_eq!(slugify("Почему кошки мурчат?"), "pochemu-koshki-murchat");
        assert_eq!(slugify("  GPU и TTS: 5 фактов "), "gpu-i-tts-5-faktov");
        assert_eq!(slugify("???"), "reel");
    }

    #[test]
    fn topics_and_restamp() {
        let tmp = tempfile::tempdir().expect("tempdir");
        let inbox = tmp.path();
        std::fs::write(
            inbox.join(TOPICS),
            "# комментарий\nПервая тема\n\nВторая тема\nПервая тема\n",
        )
        .expect("write");
        std::fs::write(inbox.join(TOPICS_DONE), "Вторая тема\n").expect("write");
        assert_eq!(new_topics(inbox).expect("topics"), ["Первая тема"]);
        assert_eq!(unique_dir(inbox, "x"), inbox.join("x"));
        std::fs::create_dir(inbox.join("x")).expect("mkdir");
        assert_eq!(unique_dir(inbox, "x"), inbox.join("x-2"));

        let t0 = SystemTime::UNIX_EPOCH;
        let t1 = t0 + Duration::from_secs(1);
        let st = Stamp {
            yaml: Some(t0),
            state: Some(t0),
        };
        assert!(needs_run(None, st));
        let done = Seen {
            stamp: st,
            outcome: Some(Outcome::Done),
        };
        assert!(
            !needs_run(
                Some(&done),
                Stamp {
                    yaml: Some(t0),
                    state: Some(t1)
                }
            ),
            "approve не трогает готовые"
        );
        assert!(
            needs_run(
                Some(&done),
                Stamp {
                    yaml: Some(t1),
                    state: Some(t0)
                }
            ),
            "правка reel.yaml"
        );
        let waiting = Seen {
            stamp: st,
            outcome: Some(Outcome::Waiting {
                gate: "take".into(),
            }),
        };
        assert!(
            needs_run(
                Some(&waiting),
                Stamp {
                    yaml: Some(t0),
                    state: Some(t1)
                }
            ),
            "approve → дальше"
        );
    }
}
