//! Мост к Python-пакету: запуск `python -m reels …`, построчный вывод, коды выхода.

use std::collections::VecDeque;
use std::ffi::{OsStr, OsString};
use std::fmt;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::time::Duration;

use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::Command;
use tokio::sync::watch;

use crate::status::ProjectStatus;

/// Коды выхода `python -m reels run`.
pub const EXIT_OK: i32 = 0;
pub const EXIT_FAILED: i32 = 1;
pub const EXIT_CONFIG: i32 = 2;
pub const EXIT_GATE: i32 = 3;

const TAIL_LINES: usize = 30;

/// Как запускать Python-пакет.
#[derive(Debug, Clone)]
pub struct Python {
    /// Интерпретатор.
    pub exe: OsString,
    /// Корень репозитория с папкой `reels/` (добавляется в PYTHONPATH), если пакет не установлен.
    pub repo: Option<PathBuf>,
}

/// Режим остановки для запущенных процессов.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Shutdown {
    /// Работаем.
    Run,
    /// Новые фазы не начинать, текущие процессы дорабатывают.
    Drain,
    /// Убить текущие процессы.
    Kill,
}

/// Откуда строка.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Stream {
    Stdout,
    Stderr,
}

/// Итог процесса.
#[derive(Debug, Clone)]
pub struct Exit {
    /// Код выхода; None — процесс убит сигналом или не запустился.
    pub code: Option<i32>,
    /// Хвост stderr.
    pub stderr_tail: Vec<String>,
    /// Хвост stdout.
    pub stdout_tail: Vec<String>,
    /// Процесс убит по Shutdown::Kill.
    pub killed: bool,
}

impl Exit {
    /// Контрольная точка, на которой остановился `run` (из строки «ждёт подтверждения «take»»).
    pub fn gate(&self) -> Option<String> {
        self.stdout_tail.iter().rev().find_map(|l| parse_gate(l))
    }

    /// Сообщение об ошибке: последняя строка «✗ …» из stderr, иначе хвост stderr.
    pub fn error_message(&self) -> String {
        if let Some(l) = self
            .stderr_tail
            .iter()
            .rev()
            .find(|l| l.trim_start().starts_with('✗'))
        {
            return l.trim().to_string();
        }
        let tail: Vec<&str> = self
            .stderr_tail
            .iter()
            .rev()
            .filter(|l| !l.trim().is_empty())
            .take(5)
            .map(String::as_str)
            .collect();
        if tail.is_empty() {
            match self.code {
                Some(c) => format!("процесс завершился с кодом {c}"),
                None => "процесс прерван".to_string(),
            }
        } else {
            tail.into_iter().rev().collect::<Vec<_>>().join("\n")
        }
    }
}

/// Ищет «ждёт подтверждения «gate»» в строке вывода.
pub fn parse_gate(line: &str) -> Option<String> {
    let i = line.find("ждёт подтверждения")?;
    let rest = &line[i..];
    let a = rest.find('«')? + '«'.len_utf8();
    let b = rest[a..].find('»')? + a;
    Some(rest[a..b].to_string())
}

#[derive(Debug)]
pub enum BridgeError {
    /// Не удалось запустить интерпретатор.
    Spawn(String),
    /// Команда завершилась с ошибкой.
    Failed { code: Option<i32>, message: String },
    /// Ответ не разобран.
    Parse(String),
}

impl fmt::Display for BridgeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            BridgeError::Spawn(m) => write!(f, "не удалось запустить Python: {m}"),
            BridgeError::Failed { code, message } => match code {
                Some(c) => write!(f, "python -m reels завершился с кодом {c}: {message}"),
                None => write!(f, "python -m reels прерван: {message}"),
            },
            BridgeError::Parse(m) => write!(f, "не удалось разобрать ответ python -m reels: {m}"),
        }
    }
}

impl std::error::Error for BridgeError {}

/// Параметры `run`.
#[derive(Debug, Clone, Default)]
pub struct RunOpts {
    pub yes: bool,
    pub until: Option<String>,
    pub edits: Vec<String>,
}

impl Python {
    /// Интерпретатор: явный → $REELS_PYTHON → python3 → python. Репозиторий: явный → предок cwd → предок бинарника.
    pub fn detect(python: Option<&OsStr>, repo: Option<&Path>) -> Python {
        let exe = python
            .map(OsStr::to_os_string)
            .or_else(|| std::env::var_os("REELS_PYTHON").filter(|v| !v.is_empty()))
            .unwrap_or_else(|| {
                if which("python3").is_some() {
                    "python3".into()
                } else {
                    "python".into()
                }
            });
        let repo = repo
            .map(crate::discover::absolute)
            .or_else(|| std::env::current_dir().ok().and_then(|d| find_repo(&d)))
            .or_else(|| {
                std::env::current_exe()
                    .ok()
                    .and_then(|e| e.parent().and_then(find_repo))
            });
        Python { exe, repo }
    }

    /// Команда `python -m reels <args…>` с PYTHONPATH на репозиторий и небуферизованным UTF-8 выводом.
    pub fn command<I, S>(&self, args: I) -> Command
    where
        I: IntoIterator<Item = S>,
        S: AsRef<OsStr>,
    {
        let mut cmd = Command::new(&self.exe);
        cmd.arg("-m").arg("reels").args(args);
        cmd.env("PYTHONUNBUFFERED", "1")
            .env("PYTHONIOENCODING", "utf-8")
            .env("PYTHONUTF8", "1");
        if let Some(repo) = &self.repo {
            let mut paths = vec![repo.clone()];
            if let Some(old) = std::env::var_os("PYTHONPATH") {
                paths.extend(std::env::split_paths(&old));
            }
            if let Ok(joined) = std::env::join_paths(paths) {
                cmd.env("PYTHONPATH", joined);
            }
        }
        cmd.stdin(Stdio::null());
        cmd
    }

    /// Короткая команда целиком: (код, stdout, stderr).
    pub async fn output<I, S>(&self, args: I) -> Result<(Option<i32>, String, String), BridgeError>
    where
        I: IntoIterator<Item = S>,
        S: AsRef<OsStr>,
    {
        let out = self
            .command(args)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true)
            .output()
            .await
            .map_err(|e| BridgeError::Spawn(format!("{}: {e}", self.exe.to_string_lossy())))?;
        Ok((
            out.status.code(),
            String::from_utf8_lossy(&out.stdout).into_owned(),
            String::from_utf8_lossy(&out.stderr).into_owned(),
        ))
    }

    /// `status --json` для проектов.
    pub async fn status(&self, dirs: &[PathBuf]) -> Result<Vec<ProjectStatus>, BridgeError> {
        if dirs.is_empty() {
            return Ok(Vec::new());
        }
        let mut args: Vec<OsString> = vec!["status".into(), "--json".into()];
        args.extend(dirs.iter().map(|d| d.as_os_str().to_os_string()));
        let (code, out, err) = self.output(&args).await?;
        if code != Some(0) {
            let message = if err.contains("--json") {
                "эта версия пакета reels не умеет status --json — обновите пакет".to_string()
            } else {
                last_lines(&err, 3)
            };
            return Err(BridgeError::Failed { code, message });
        }
        // JSON — первый символ «[» в выводе (на случай посторонних строк до него)
        let start = out.find('[').unwrap_or(0);
        ProjectStatus::parse_list(&out[start..]).map_err(|e| BridgeError::Parse(e.to_string()))
    }

    /// `approve <dir> <gate>` → сообщение пакета.
    pub async fn approve(&self, dir: &Path, gate: &str) -> Result<String, BridgeError> {
        let (code, out, err) = self
            .output([OsStr::new("approve"), dir.as_os_str(), OsStr::new(gate)])
            .await?;
        if code == Some(0) {
            Ok(out.trim().to_string())
        } else {
            Err(BridgeError::Failed {
                code,
                message: last_lines(&format!("{out}\n{err}"), 3),
            })
        }
    }

    /// `draft "<тема>" -o <dir>`.
    pub async fn draft(&self, topic: &str, out_dir: &Path) -> Result<String, BridgeError> {
        let (code, out, err) = self
            .output([
                OsStr::new("draft"),
                OsStr::new(topic),
                OsStr::new("-o"),
                out_dir.as_os_str(),
            ])
            .await?;
        if code == Some(0) {
            Ok(out.trim().to_string())
        } else {
            Err(BridgeError::Failed {
                code,
                message: last_lines(&format!("{out}\n{err}"), 3),
            })
        }
    }

    /// `run <dir> --no-input …` с построчным выводом в `on_line`.
    /// При `Shutdown::Kill` процесс (и его дочерние на Unix) убивается.
    pub async fn run_streaming<F>(
        &self,
        dir: &Path,
        opts: &RunOpts,
        mut shutdown: watch::Receiver<Shutdown>,
        mut on_line: F,
    ) -> Exit
    where
        F: FnMut(Stream, &str),
    {
        let mut args: Vec<OsString> =
            vec!["run".into(), dir.as_os_str().into(), "--no-input".into()];
        if opts.yes {
            args.push("--yes".into());
        }
        if let Some(u) = &opts.until {
            args.push("--until".into());
            args.push(u.into());
        }
        for e in &opts.edits {
            args.push("--edit".into());
            args.push(e.into());
        }
        let mut cmd = self.command(&args);
        cmd.stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        #[cfg(unix)]
        {
            // своя группа процессов: Ctrl+C терминала не роняет рендер, а Kill убивает и ffmpeg/node
            cmd.process_group(0);
        }
        let mut child = match cmd.spawn() {
            Ok(c) => c,
            Err(e) => {
                return Exit {
                    code: None,
                    stderr_tail: vec![format!(
                        "✗ не удалось запустить {}: {e}",
                        self.exe.to_string_lossy()
                    )],
                    stdout_tail: Vec::new(),
                    killed: false,
                }
            }
        };
        let pid = child.id();
        let mut out = child.stdout.take().map(|s| BufReader::new(s).lines());
        let mut err = child.stderr.take().map(|s| BufReader::new(s).lines());
        let mut out_tail: VecDeque<String> = VecDeque::new();
        let mut err_tail: VecDeque<String> = VecDeque::new();
        let mut killed = false;
        let mut watching = true;

        loop {
            if out.is_none() && err.is_none() {
                break;
            }
            tokio::select! {
                l = async { out.as_mut().expect("stdout").next_line().await }, if out.is_some() => match l {
                    Ok(Some(l)) => { on_line(Stream::Stdout, &l); push_tail(&mut out_tail, l); }
                    _ => out = None,
                },
                l = async { err.as_mut().expect("stderr").next_line().await }, if err.is_some() => match l {
                    Ok(Some(l)) => { on_line(Stream::Stderr, &l); push_tail(&mut err_tail, l); }
                    _ => err = None,
                },
                r = shutdown.changed(), if watching => {
                    match r {
                        Err(_) => watching = false, // отправитель закрыт — просто ждём конец
                        Ok(()) if *shutdown.borrow() == Shutdown::Kill => {
                            watching = false;
                            killed = true;
                            kill_tree(&mut child, pid);
                        }
                        Ok(()) => {}
                    }
                }
            }
        }
        let code = match tokio::time::timeout(Duration::from_secs(10), child.wait()).await {
            Ok(Ok(s)) => s.code(),
            _ => {
                kill_tree(&mut child, pid);
                let _ = child.wait().await;
                None
            }
        };
        Exit {
            code,
            stderr_tail: err_tail.into(),
            stdout_tail: out_tail.into(),
            killed,
        }
    }
}

fn push_tail(tail: &mut VecDeque<String>, l: String) {
    if tail.len() == TAIL_LINES {
        tail.pop_front();
    }
    tail.push_back(l);
}

fn kill_tree(child: &mut tokio::process::Child, pid: Option<u32>) {
    #[cfg(unix)]
    if let Some(pid) = pid.and_then(|p| i32::try_from(p).ok()) {
        // SAFETY: killpg только посылает сигнал группе процессов, созданной для этого запуска.
        unsafe {
            libc::killpg(pid, libc::SIGKILL);
        }
    }
    #[cfg(not(unix))]
    let _ = pid;
    let _ = child.start_kill();
}

fn last_lines(s: &str, n: usize) -> String {
    let lines: Vec<&str> = s.lines().filter(|l| !l.trim().is_empty()).collect();
    lines[lines.len().saturating_sub(n)..].join("\n")
}

/// Корень репозитория local-ai-reels среди предков `start` (папка с `reels/__init__.py` и `reels/cli.py`).
pub fn find_repo(start: &Path) -> Option<PathBuf> {
    start
        .ancestors()
        .find(|d| {
            d.join("reels").join("__init__.py").is_file()
                && d.join("reels").join("cli.py").is_file()
        })
        .map(Path::to_path_buf)
}

/// Поиск программы в PATH.
pub fn which(name: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    let exts: Vec<String> = if cfg!(windows) {
        vec![".exe".into(), ".bat".into(), ".cmd".into(), String::new()]
    } else {
        vec![String::new()]
    };
    std::env::split_paths(&path).find_map(|d| {
        exts.iter()
            .map(|e| d.join(format!("{name}{e}")))
            .find(|p| p.is_file())
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn gate_from_line() {
        assert_eq!(
            parse_gate("── ждёт подтверждения «take» ──").as_deref(),
            Some("take")
        );
        assert_eq!(parse_gate("  ✓ voice  12.3 с"), None);
    }

    #[test]
    fn error_message_prefers_cross() {
        let e = Exit {
            code: Some(1),
            stderr_tail: vec!["Traceback".into(), "✗ нет таймкодов".into(), "".into()],
            stdout_tail: vec![],
            killed: false,
        };
        assert_eq!(e.error_message(), "✗ нет таймкодов");
    }
}
