//! Мост к Python-конвейеру: поиск интерпретатора и пакета reels, сборка команд, разбор `status --json`.
use std::collections::BTreeMap;
use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::process::Stdio;

use serde::{Deserialize, Serialize};
use tokio::process::Command;

use crate::error::{AppError, Result};

/// Интерпретатор и (необязательно) корень репозитория с пакетом `reels/` для PYTHONPATH.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PythonEnv {
    pub exe: PathBuf,
    pub repo: Option<PathBuf>,
}

/// Исполняемый файл в PATH (на Windows — с расширением .exe).
pub fn find_in_path(name: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    let exts: &[&str] = if cfg!(windows) { &[".exe", ""] } else { &[""] };
    std::env::split_paths(&path)
        .flat_map(|dir| exts.iter().map(move |ext| dir.join(format!("{name}{ext}"))))
        .find(|p| p.is_file())
}

/// Ближайший предок, в котором лежит пакет `reels/` (папка с `__init__.py` и `__main__.py`).
pub fn repo_root(start: &Path) -> Option<PathBuf> {
    start
        .ancestors()
        .find(|d| d.join("reels").join("__init__.py").is_file() && d.join("reels").join("__main__.py").is_file())
        .map(Path::to_path_buf)
}

/// Находит Python: REELS_PYTHON, иначе python3/python в PATH. Корень пакета ищется от папок `hints`
/// (папка проекта, рабочая папка, папка программы).
pub fn resolve(hints: &[PathBuf]) -> Result<PythonEnv> {
    resolve_with(std::env::var_os("REELS_PYTHON"), hints)
}

pub fn resolve_with(env_python: Option<OsString>, hints: &[PathBuf]) -> Result<PythonEnv> {
    let exe = match env_python.filter(|v| !v.is_empty()) {
        Some(v) => {
            let p = PathBuf::from(&v);
            if p.components().count() > 1 || p.is_file() {
                p
            } else {
                find_in_path(&v.to_string_lossy()).ok_or(AppError::PythonNotFound)?
            }
        }
        None => ["python3", "python"].iter().find_map(|n| find_in_path(n)).ok_or(AppError::PythonNotFound)?,
    };
    let mut starts: Vec<PathBuf> = hints.to_vec();
    if let Ok(cwd) = std::env::current_dir() {
        starts.push(cwd);
    }
    if let Some(dir) = std::env::current_exe().ok().and_then(|p| p.parent().map(Path::to_path_buf)) {
        starts.push(dir);
    }
    let repo = starts.iter().find_map(|s| repo_root(s));
    Ok(PythonEnv { exe, repo })
}

/// `python -m reels <args>` с PYTHONPATH на корень репозитория, UTF-8 и небуферизованным выводом.
pub fn command(env: &PythonEnv, args: &[String]) -> Command {
    let mut c = Command::new(&env.exe);
    c.arg("-m").arg("reels").args(args);
    if let Some(repo) = &env.repo {
        let mut paths = vec![repo.clone()];
        if let Some(old) = std::env::var_os("PYTHONPATH") {
            paths.extend(std::env::split_paths(&old));
        }
        if let Ok(joined) = std::env::join_paths(paths) {
            c.env("PYTHONPATH", joined);
        }
        c.current_dir(repo);
    }
    c.env("PYTHONIOENCODING", "utf-8").env("PYTHONUNBUFFERED", "1").env("PYTHONUTF8", "1");
    c.stdin(Stdio::null()).kill_on_drop(true);
    #[cfg(windows)]
    {
        // CREATE_NO_WINDOW: без мигающей консоли.
        c.creation_flags(0x0800_0000);
    }
    c
}

/// Последняя содержательная строка вывода — для сообщения об ошибке.
pub fn last_line(text: &str) -> String {
    text.lines().rev().map(str::trim).find(|l| !l.is_empty()).unwrap_or("").to_string()
}

/// Выполняет короткую команду reels и возвращает stdout; ненулевой код → понятная ошибка.
pub async fn output(env: &PythonEnv, args: &[String]) -> Result<String> {
    let out = command(env, args)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output()
        .await
        .map_err(|e| AppError::Spawn { exe: env.exe.display().to_string(), msg: crate::error::io_text(&e) })?;
    let stdout = String::from_utf8_lossy(&out.stdout).into_owned();
    let stderr = String::from_utf8_lossy(&out.stderr).into_owned();
    if out.status.success() {
        return Ok(stdout);
    }
    let code = out.status.code().unwrap_or(-1);
    let mut msg = last_line(&stderr);
    if msg.is_empty() {
        msg = last_line(&stdout);
    }
    if stderr.contains("No module named reels") || stderr.contains("No module named 'reels'") {
        return Err(AppError::PythonNotFound);
    }
    Err(AppError::Python { code, msg: msg.trim_start_matches('✗').trim().to_string() })
}

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
#[serde(default)]
pub struct StageRow {
    pub key: String,
    pub stage: String,
    pub edit: Option<String>,
    pub status: String,
    pub at: Option<String>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
#[serde(default)]
pub struct GateRow {
    pub gate: String,
    pub stage: String,
    pub status: String,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
#[serde(default)]
pub struct EditOutput {
    pub mp4: Option<String>,
    pub frames: Option<String>,
}

/// Строка `reels status --json` по одному проекту.
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
#[serde(default)]
pub struct ProjectStatus {
    pub dir: String,
    pub slug: String,
    pub title: String,
    pub edits: BTreeMap<String, serde_json::Value>,
    pub stages: Vec<StageRow>,
    pub gates: Vec<GateRow>,
    pub outputs: BTreeMap<String, EditOutput>,
    pub report: Option<String>,
    pub error: Option<String>,
}

impl ProjectStatus {
    /// Контрольная точка, на которой стоит проект (первая в статусе waiting).
    pub fn waiting_gate(&self) -> Option<&str> {
        self.gates.iter().find(|g| g.status == "waiting").map(|g| g.gate.as_str())
    }
}

pub fn parse_status(stdout: &str) -> Result<Vec<ProjectStatus>> {
    let start = stdout.find('[').ok_or_else(|| AppError::BadJson(snippet(stdout)))?;
    serde_json::from_str(&stdout[start..]).map_err(|e| AppError::BadJson(format!("{e}; {}", snippet(stdout))))
}

fn snippet(s: &str) -> String {
    let t: String = s.trim().chars().take(120).collect();
    if t.is_empty() {
        "пустой вывод".into()
    } else {
        t
    }
}

/// `reels status --json <dirs>`.
pub async fn status(env: &PythonEnv, dirs: &[PathBuf]) -> Result<Vec<ProjectStatus>> {
    if dirs.is_empty() {
        return Ok(vec![]);
    }
    let mut args = vec!["status".to_string(), "--json".to_string()];
    args.extend(dirs.iter().map(|d| d.display().to_string()));
    parse_status(&output(env, &args).await?)
}

/// Как закончился запуск `reels run`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Outcome {
    Ok,
    Gate,
    Failed,
    Config,
    Stopped,
}

impl Outcome {
    pub fn from_code(code: Option<i32>) -> Self {
        match code {
            Some(0) => Outcome::Ok,
            Some(3) => Outcome::Gate,
            Some(2) => Outcome::Config,
            None => Outcome::Stopped,
            Some(_) => Outcome::Failed,
        }
    }

    pub fn text(self) -> &'static str {
        match self {
            Outcome::Ok => "готово",
            Outcome::Gate => "ждёт подтверждения",
            Outcome::Failed => "ошибка этапа",
            Outcome::Config => "ошибка в reel.yaml",
            Outcome::Stopped => "остановлено",
        }
    }
}

/// Строка прогресса конвейера: «  ▸ voice», «  ✓ build    [linear]  3.1 с», «  · check  без изменений».
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct StageEvent {
    pub stage: String,
    pub edit: Option<String>,
    pub state: &'static str,
}

pub fn parse_progress(line: &str) -> Option<StageEvent> {
    let t = line.trim_start();
    let mut chars = t.chars();
    let state = match chars.next()? {
        '▸' => "started",
        '✓' => "done",
        '·' => "skipped",
        _ => return None,
    };
    let rest = chars.as_str().trim_start();
    let stage: String = rest.chars().take_while(|c| c.is_ascii_alphanumeric() || *c == '_').collect();
    const STAGES: [&str; 9] = ["check", "accent", "voice", "align", "music", "build", "snapshot", "render", "finish"];
    if !STAGES.contains(&stage.as_str()) {
        return None;
    }
    let after = rest[stage.len()..].trim_start();
    let edit = after.strip_prefix('[').and_then(|s| s.split_once(']')).map(|(e, _)| e.to_string());
    Some(StageEvent { stage, edit, state })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn progress_lines() {
        assert_eq!(
            parse_progress("  ▸ voice"),
            Some(StageEvent { stage: "voice".into(), edit: None, state: "started" })
        );
        let e = parse_progress("  ✓ build    [linear]  3.1 с").unwrap_or_else(|| panic!("нет события"));
        assert_eq!((e.stage.as_str(), e.edit.as_deref(), e.state), ("build", Some("linear"), "done"));
        assert_eq!(parse_progress("  · check     без изменений").map(|e| e.state), Some("skipped"));
        assert_eq!(parse_progress("■ povodok"), None);
        assert_eq!(parse_progress("✓ готово"), None);
    }

    #[test]
    fn outcome_codes() {
        assert_eq!(Outcome::from_code(Some(0)), Outcome::Ok);
        assert_eq!(Outcome::from_code(Some(3)), Outcome::Gate);
        assert_eq!(Outcome::from_code(Some(2)), Outcome::Config);
        assert_eq!(Outcome::from_code(Some(1)), Outcome::Failed);
        assert_eq!(Outcome::from_code(None), Outcome::Stopped);
    }

    #[test]
    fn status_json_lenient() {
        let s = r#"warning: something
[{"dir": "/p", "slug": "p", "title": "P", "edits": {"linear": "1234"},
  "gates": [{"gate": "take", "stage": "voice", "status": "waiting"}], "extra": 1}]"#;
        let v = parse_status(s).unwrap_or_default();
        assert_eq!(v.len(), 1);
        assert_eq!(v[0].waiting_gate(), Some("take"));
        assert!(matches!(parse_status("Traceback"), Err(AppError::BadJson(_))));
    }

    #[test]
    fn repo_root_found_from_nested_dir() {
        let tmp = tempfile::tempdir().unwrap_or_else(|e| panic!("{e}"));
        let pkg = tmp.path().join("reels");
        std::fs::create_dir_all(&pkg).ok();
        std::fs::write(pkg.join("__init__.py"), "").ok();
        std::fs::write(pkg.join("__main__.py"), "").ok();
        let nested = tmp.path().join("examples").join("x");
        std::fs::create_dir_all(&nested).ok();
        assert_eq!(repo_root(&nested).as_deref(), Some(tmp.path()));
    }

    #[test]
    fn missing_python_is_reported() {
        let r = resolve_with(Some(OsString::from("definitely-not-a-python-xyz")), &[]);
        assert!(matches!(r, Err(AppError::PythonNotFound)));
    }
}
