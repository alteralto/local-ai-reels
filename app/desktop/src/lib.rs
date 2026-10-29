//! Настольное приложение local-ai-reels: команды Tauri поверх Python-конвейера `python -m reels`.
pub mod error;
pub mod project;
pub mod python;
pub mod runner;

use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter, Manager, State};
use tauri_plugin_dialog::DialogExt;

use error::{AppError, Result};
use python::ProjectStatus;
use runner::{FinishEvent, Job, JobInfo, LogEvent, Runner, Sink};

/// Настройки, которые приложение помнит между запусками (папка конфигурации приложения).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default, rename_all = "camelCase")]
pub struct Settings {
    pub workspace: Option<String>,
    pub concurrency: usize,
    pub mode: String,
}

impl Default for Settings {
    fn default() -> Self {
        Settings { workspace: None, concurrency: 1, mode: "simple".into() }
    }
}

pub struct AppState {
    runner: Arc<Runner>,
    settings: Mutex<Settings>,
    settings_path: Option<PathBuf>,
}

impl AppState {
    fn settings(&self) -> Settings {
        self.settings.lock().unwrap_or_else(|e| e.into_inner()).clone()
    }
}

struct TauriSink(AppHandle);

impl Sink for TauriSink {
    fn log(&self, ev: LogEvent) {
        let _ = self.0.emit("run-log", ev);
    }
    fn finished(&self, ev: FinishEvent) {
        let _ = self.0.emit("run-finished", ev);
    }
    fn changed(&self, jobs: Vec<JobInfo>) {
        let _ = self.0.emit("runs-changed", jobs);
    }
}

fn abs(dir: &str) -> Result<PathBuf> {
    let p = PathBuf::from(dir);
    if dir.trim().is_empty() {
        return Err(AppError::Invalid("Не указана папка.".into()));
    }
    // без canonicalize: на Windows он даёт пути \\?\C:\…, которые не любят Python и asset-протокол
    Ok(std::path::absolute(&p).unwrap_or(p))
}

fn project_dir(dir: &str) -> Result<PathBuf> {
    let p = abs(dir)?;
    if !project::is_project(&p) {
        return Err(AppError::NoProject(p.display().to_string()));
    }
    Ok(p)
}

/// Разрешает показ файлов папки (видео, звук, кадры) через asset-протокол.
fn allow_assets(app: &AppHandle, dir: &Path) {
    let _ = app.asset_protocol_scope().allow_directory(dir, true);
}

fn python_env(state: &AppState, near: &[PathBuf]) -> Result<python::PythonEnv> {
    let mut hints = near.to_vec();
    if let Some(ws) = state.settings().workspace {
        hints.push(PathBuf::from(ws));
    }
    let env = python::resolve(&hints)?;
    state.runner.set_env(env.clone());
    Ok(env)
}

fn default_workspace(app: &AppHandle) -> PathBuf {
    app.path()
        .video_dir()
        .or_else(|_| app.path().home_dir())
        .map(|d| d.join("Reels"))
        .unwrap_or_else(|_| PathBuf::from("Reels"))
}

fn save_settings(state: &AppState, s: &Settings) -> Result<()> {
    *state.settings.lock().unwrap_or_else(|e| e.into_inner()) = s.clone();
    let Some(path) = &state.settings_path else { return Ok(()) };
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| AppError::io(parent, &e))?;
    }
    let text = serde_json::to_string_pretty(s).map_err(|e| AppError::Invalid(e.to_string()))?;
    std::fs::write(path, text).map_err(|e| AppError::io(path, &e))
}

// --- команды --------------------------------------------------------------------------------------------------------

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct Info {
    settings: Settings,
    default_workspace: String,
    python: Option<String>,
    repo: Option<String>,
    python_error: Option<String>,
}

#[tauri::command]
async fn app_info(app: AppHandle, state: State<'_, AppState>) -> Result<Info> {
    let settings = state.settings();
    if let Some(ws) = &settings.workspace {
        allow_assets(&app, Path::new(ws));
    }
    let (python, repo, python_error) = match python_env(&state, &[]) {
        Ok(env) => (Some(env.exe.display().to_string()), env.repo.map(|r| r.display().to_string()), None),
        Err(e) => (None, None, Some(e.to_string())),
    };
    Ok(Info { settings, default_workspace: default_workspace(&app).display().to_string(), python, repo, python_error })
}

#[tauri::command]
async fn set_settings(app: AppHandle, state: State<'_, AppState>, settings: Settings) -> Result<Settings> {
    let mut s = settings;
    s.concurrency = s.concurrency.clamp(1, 16);
    if s.mode != "expert" {
        s.mode = "simple".into();
    }
    if let Some(ws) = &s.workspace {
        allow_assets(&app, Path::new(ws));
    }
    state.runner.set_concurrency(s.concurrency);
    save_settings(&state, &s)?;
    Ok(s)
}

#[tauri::command]
async fn pick_folder(app: AppHandle, title: Option<String>) -> Result<Option<String>> {
    let picked = tauri::async_runtime::spawn_blocking(move || {
        app.dialog().file().set_title(title.unwrap_or_else(|| "Выберите папку".into())).blocking_pick_folder()
    })
    .await
    .map_err(|e| AppError::Invalid(format!("окно выбора папки: {e}")))?;
    Ok(picked.and_then(|p| p.into_path().ok()).map(|p| p.display().to_string()))
}

/// Проекты рабочей папки со статусами. Если Python недоступен, список всё равно показывается — с ошибкой.
#[tauri::command]
async fn list_projects(app: AppHandle, state: State<'_, AppState>, workspace: String) -> Result<Vec<ProjectStatus>> {
    let ws = abs(&workspace)?;
    if !ws.is_dir() {
        return Err(AppError::io(&ws, &std::io::Error::from(std::io::ErrorKind::NotFound)));
    }
    allow_assets(&app, &ws);
    let dirs = project::scan(&ws);
    statuses(&state, &dirs).await
}

async fn statuses(state: &AppState, dirs: &[PathBuf]) -> Result<Vec<ProjectStatus>> {
    let fallback = |err: String| -> Vec<ProjectStatus> {
        dirs.iter()
            .map(|d| {
                let f = project::load(d).ok();
                ProjectStatus {
                    dir: d.display().to_string(),
                    slug: d.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default(),
                    title: f.map(|f| f.title).unwrap_or_default(),
                    error: Some(err.clone()),
                    ..Default::default()
                }
            })
            .collect()
    };
    let env = match python_env(state, dirs) {
        Ok(env) => env,
        Err(e) => return Ok(fallback(e.to_string())),
    };
    match python::status(&env, dirs).await {
        Ok(mut v) => {
            // ключ проекта в интерфейсе и очереди — путь, который передали мы (Python может разрешить ссылки)
            for (s, d) in v.iter_mut().zip(dirs) {
                s.dir = d.display().to_string();
            }
            Ok(v)
        }
        Err(e) => Ok(fallback(e.to_string())),
    }
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct ProjectView {
    status: ProjectStatus,
    files: project::ProjectFiles,
    busy: bool,
}

#[tauri::command]
async fn open_project(app: AppHandle, state: State<'_, AppState>, dir: String) -> Result<ProjectView> {
    let d = project_dir(&dir)?;
    allow_assets(&app, &d);
    let status = statuses(&state, std::slice::from_ref(&d)).await?.into_iter().next().unwrap_or_default();
    let files = project::load(&d)?;
    Ok(ProjectView { status, files, busy: state.runner.is_busy(&runner::key(&d)) })
}

#[derive(Deserialize, Default)]
#[serde(default, rename_all = "camelCase")]
struct RunOptions {
    yes: bool,
    until: Option<String>,
    edit: Option<String>,
    from: Option<String>,
}

fn run_args(d: &Path, o: &RunOptions) -> Vec<String> {
    let mut a = vec!["run".into(), d.display().to_string(), "--no-input".into()];
    if o.yes {
        a.push("--yes".into());
    }
    for (flag, v) in [("--until", &o.until), ("--edit", &o.edit), ("--from", &o.from)] {
        if let Some(v) = v.as_ref().filter(|v| !v.is_empty()) {
            a.push(flag.into());
            a.push(v.clone());
        }
    }
    a
}

#[tauri::command]
async fn start_run(state: State<'_, AppState>, dir: String, options: Option<RunOptions>) -> Result<u64> {
    let d = project_dir(&dir)?;
    python_env(&state, std::slice::from_ref(&d))?;
    let o = options.unwrap_or_default();
    let label = if o.yes { "автопилот" } else { "запуск" };
    state.runner.enqueue(Job { project: runner::key(&d), label: label.into(), steps: vec![run_args(&d, &o)] })
}

#[tauri::command]
async fn stop_run(state: State<'_, AppState>, dir: String) -> Result<bool> {
    let d = abs(&dir)?;
    Ok(state.runner.stop(&runner::key(&d)))
}

#[tauri::command]
async fn list_runs(state: State<'_, AppState>) -> Result<Vec<JobInfo>> {
    Ok(state.runner.jobs())
}

#[tauri::command]
async fn approve(state: State<'_, AppState>, dir: String, gate: String) -> Result<String> {
    let d = project_dir(&dir)?;
    if !["script", "accents", "take", "frames"].contains(&gate.as_str()) {
        return Err(AppError::Invalid(format!("нет контрольной точки «{gate}»")));
    }
    let env = python_env(&state, std::slice::from_ref(&d))?;
    let out = python::output(&env, &["approve".into(), d.display().to_string(), gate]).await?;
    Ok(python::last_line(&out))
}

#[tauri::command]
async fn choose_take(dir: String, file: String) -> Result<serde_json::Value> {
    let d = project_dir(&dir)?;
    tauri::async_runtime::spawn_blocking(move || project::choose_take(&d, &file))
        .await
        .map_err(|e| AppError::Invalid(e.to_string()))?
}

#[derive(Serialize)]
struct Example {
    name: String,
    dir: String,
}

#[tauri::command]
async fn list_examples(state: State<'_, AppState>) -> Result<Vec<Example>> {
    let Ok(env) = python_env(&state, &[]) else { return Ok(vec![]) };
    let Some(repo) = env.repo else { return Ok(vec![]) };
    let mut out: Vec<Example> = project::scan(&repo.join("examples"))
        .into_iter()
        .map(|d| Example {
            name: d.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default(),
            dir: d.display().to_string(),
        })
        .collect();
    out.sort_by(|a, b| a.name.cmp(&b.name));
    Ok(out)
}

#[tauri::command]
async fn new_project(app: AppHandle, state: State<'_, AppState>, dir: String, from: Option<String>) -> Result<String> {
    let d = abs(&dir)?;
    if project::is_project(&d) {
        return Err(AppError::Invalid(format!("{} уже существует", d.join(project::CONFIG).display())));
    }
    let env = python_env(&state, &[])?;
    let mut args = vec!["new".to_string(), d.display().to_string()];
    if let Some(src) = from.filter(|s| !s.is_empty()) {
        args.push("--from".into());
        args.push(src);
    }
    python::output(&env, &args).await?;
    allow_assets(&app, &d);
    Ok(d.display().to_string())
}

/// «Черновик по теме»: `reels draft` идёт в очереди как обычный запуск (локальная LLM может думать долго).
#[tauri::command]
async fn draft_project(state: State<'_, AppState>, topic: String, dir: String) -> Result<String> {
    let d = abs(&dir)?;
    if topic.trim().is_empty() {
        return Err(AppError::Invalid("Напишите тему ролика.".into()));
    }
    python_env(&state, &[])?;
    let steps = vec![vec!["draft".into(), topic.trim().to_string(), "-o".into(), d.display().to_string()]];
    state.runner.enqueue(Job { project: runner::key(&d), label: "черновик".into(), steps })?;
    Ok(d.display().to_string())
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct SimpleRequest {
    topic: Option<String>,
    text: Option<String>,
    theme: String,
}

/// Режим «Просто»: тема → draft → run --yes, или свой текст → new → 4 блока в reel.yaml → run --yes.
#[tauri::command]
async fn simple_make(app: AppHandle, state: State<'_, AppState>, request: SimpleRequest) -> Result<String> {
    let s = state.settings();
    let ws = s.workspace.map(PathBuf::from).unwrap_or_else(|| default_workspace(&app));
    std::fs::create_dir_all(&ws).map_err(|e| AppError::io(&ws, &e))?;
    allow_assets(&app, &ws);
    let env = python_env(&state, &[])?;
    let theme = match request.theme.as_str() {
        t @ ("blueprint" | "night" | "paper" | "mono") => t.to_string(),
        _ => "blueprint".into(),
    };
    let topic = request.topic.unwrap_or_default().trim().to_string();
    let text = request.text.unwrap_or_default();
    let run = |d: &Path| vec!["run".to_string(), d.display().to_string(), "--no-input".into(), "--yes".into()];
    if !topic.is_empty() {
        let d = project::free_dir(&ws, &project::slugify(&topic));
        let draft = vec!["draft".into(), topic, "-o".into(), d.display().to_string(), "--theme".into(), theme];
        state.runner.enqueue(Job {
            project: runner::key(&d),
            label: "ролик по теме".into(),
            steps: vec![draft, run(&d)],
        })?;
        return Ok(d.display().to_string());
    }
    let paragraphs = project::split_paragraphs(&text);
    if paragraphs.is_empty() {
        return Err(AppError::Invalid("Напишите, о чём ролик, или вставьте свой текст.".into()));
    }
    if paragraphs.len() != 4 {
        return Err(AppError::Invalid(format!(
            "Нужно 4 коротких абзаца (крючок, факт, поворот, вывод), разделённых пустой строкой; сейчас {}.",
            paragraphs.len()
        )));
    }
    let d = project::free_dir(&ws, &project::slugify(&paragraphs[0]));
    python::output(&env, &["new".into(), d.display().to_string()]).await?;
    project::write_blocks(&d, &paragraphs, &theme)?;
    state.runner.enqueue(Job {
        project: runner::key(&d), label: "ролик по тексту".into(), steps: vec![run(&d)]
    })?;
    Ok(d.display().to_string())
}

/// «Сохранить видео»: копия mp4 в выбранное место.
#[tauri::command]
async fn save_video(app: AppHandle, src: String) -> Result<Option<String>> {
    let src = PathBuf::from(src);
    if !src.is_file() {
        return Err(AppError::io(&src, &std::io::Error::from(std::io::ErrorKind::NotFound)));
    }
    let name = src.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_else(|| "reel.mp4".into());
    let dst = tauri::async_runtime::spawn_blocking(move || {
        app.dialog()
            .file()
            .set_title("Сохранить видео")
            .set_file_name(&name)
            .add_filter("Видео MP4", &["mp4"])
            .blocking_save_file()
    })
    .await
    .map_err(|e| AppError::Invalid(e.to_string()))?;
    let Some(dst) = dst.and_then(|p| p.into_path().ok()) else { return Ok(None) };
    tokio::fs::copy(&src, &dst).await.map_err(|e| AppError::io(&dst, &e))?;
    Ok(Some(dst.display().to_string()))
}

/// «Показать в папке».
#[tauri::command]
async fn reveal(path: String) -> Result<()> {
    let p = PathBuf::from(&path);
    if !p.exists() {
        return Err(AppError::io(&p, &std::io::Error::from(std::io::ErrorKind::NotFound)));
    }
    let spawn = |prog: &str, args: Vec<std::ffi::OsString>| {
        std::process::Command::new(prog).args(args).spawn().map(|_| ()).map_err(|e| AppError::io(&p, &e))
    };
    if cfg!(target_os = "macos") {
        spawn("open", vec!["-R".into(), p.clone().into()])
    } else if cfg!(windows) {
        spawn("explorer", vec![format!("/select,{}", p.display()).into()])
    } else {
        let dir = if p.is_dir() { p.clone() } else { p.parent().map(Path::to_path_buf).unwrap_or_else(|| p.clone()) };
        spawn("xdg-open", vec![dir.into()])
    }
}

pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let settings_path = app.path().app_config_dir().ok().map(|d| d.join("settings.json"));
            let settings: Settings = settings_path
                .as_ref()
                .and_then(|p| std::fs::read_to_string(p).ok())
                .and_then(|t| serde_json::from_str(&t).ok())
                .unwrap_or_default();
            let runner = Runner::new(Arc::new(TauriSink(app.handle().clone())), settings.concurrency);
            app.manage(AppState { runner, settings: Mutex::new(settings), settings_path });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            app_info,
            set_settings,
            pick_folder,
            list_projects,
            open_project,
            start_run,
            stop_run,
            list_runs,
            approve,
            choose_take,
            list_examples,
            new_project,
            draft_project,
            simple_make,
            save_video,
            reveal
        ])
        .build(tauri::generate_context!());
    match app {
        Ok(app) => app.run(|_, _| {}),
        Err(e) => eprintln!("Не удалось запустить приложение: {e}"),
    }
}
