//! Файлы проекта, которые показываются на контрольных точках, выбор дубля, запись блоков сценария.
use std::collections::BTreeMap;
use std::path::{Component, Path, PathBuf};

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::error::{AppError, Result};

pub const CONFIG: &str = "reel.yaml";

fn read_text(path: &Path) -> Result<String> {
    std::fs::read_to_string(path).map_err(|e| AppError::io(path, &e))
}

fn read_json_opt<T: for<'de> Deserialize<'de>>(path: &Path) -> Option<T> {
    let text = std::fs::read_to_string(path).ok()?;
    serde_json::from_str(&text).ok()
}

fn write_atomic(path: &Path, data: &[u8]) -> Result<()> {
    let tmp = path.with_extension("tmp-desktop");
    std::fs::write(&tmp, data).map_err(|e| AppError::io(&tmp, &e))?;
    std::fs::rename(&tmp, path).map_err(|e| AppError::io(path, &e))
}

/// Слова для лимита: токены, в которых есть буква или цифра.
pub fn word_count(text: &str) -> usize {
    text.split_whitespace().filter(|w| w.chars().any(char::is_alphanumeric)).count()
}

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct Block {
    pub index: usize,
    pub name: String,
    pub text: String,
    pub words: usize,
    pub scene: String,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
#[serde(default)]
pub struct Accents {
    pub master: String,
    #[serde(rename = "ref")]
    pub reference: String,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
#[serde(default)]
pub struct Take {
    pub file: String,
    pub voice: Value,
    pub seed: Value,
    pub ratio: Option<f64>,
    pub heard: Option<String>,
    pub duration: Option<f64>,
    /// Абсолютный путь — заполняется приложением.
    pub path: Option<String>,
    /// Этот дубль сейчас в master.wav.
    pub chosen: bool,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
#[serde(default)]
pub struct QaIssue {
    pub t: Option<f64>,
    pub kind: String,
    pub severity: String,
    pub text: String,
    pub detail: Value,
}

#[derive(Debug, Clone, Default, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ProjectFiles {
    pub dir: String,
    pub title: String,
    pub theme: String,
    pub limit: Option<u64>,
    pub blocks: Vec<Block>,
    pub total_words: usize,
    pub yaml_error: Option<String>,
    pub accents: Option<Accents>,
    pub takes: Vec<Take>,
    pub choice: Option<Value>,
    pub master: Option<String>,
    pub qa: BTreeMap<String, Vec<QaIssue>>,
    pub out_files: Vec<String>,
    pub report: Option<String>,
    pub report_text: Option<String>,
}

pub fn is_project(dir: &Path) -> bool {
    dir.join(CONFIG).is_file()
}

fn yaml_str(v: &serde_yaml::Value, key: &str) -> String {
    match v.get(key) {
        Some(serde_yaml::Value::String(s)) => s.clone(),
        Some(serde_yaml::Value::Number(n)) => n.to_string(),
        _ => String::new(),
    }
}

/// Блоки сценария из reel.yaml.
pub fn blocks(cfg: &serde_yaml::Value) -> Vec<Block> {
    let Some(seq) = cfg.get("blocks").and_then(|b| b.as_sequence()) else { return vec![] };
    seq.iter()
        .enumerate()
        .map(|(i, b)| {
            let text = yaml_str(b, "text").split_whitespace().collect::<Vec<_>>().join(" ");
            let scene = b.get("scene").map(|s| yaml_str(s, "type")).unwrap_or_default();
            let name = yaml_str(b, "name");
            Block { index: i + 1, words: word_count(&text), name, text, scene }
        })
        .collect()
}

/// Собирает всё, что нужно экрану проекта. Отсутствующие файлы — не ошибка (этап ещё не выполнен).
pub fn load(dir: &Path) -> Result<ProjectFiles> {
    if !is_project(dir) {
        return Err(AppError::NoProject(dir.display().to_string()));
    }
    let mut f = ProjectFiles { dir: dir.display().to_string(), ..Default::default() };
    let yaml = read_text(&dir.join(CONFIG))?;
    match serde_yaml::from_str::<serde_yaml::Value>(&yaml) {
        Ok(cfg) => {
            f.title = yaml_str(&cfg, "title");
            f.theme = match cfg.get("theme") {
                Some(serde_yaml::Value::String(s)) => s.clone(),
                Some(v) => yaml_str(v, "base"),
                None => String::new(),
            };
            f.limit = cfg.get("limit").and_then(serde_yaml::Value::as_u64);
            f.blocks = blocks(&cfg);
            f.total_words = f.blocks.iter().map(|b| b.words).sum();
        }
        Err(e) => f.yaml_error = Some(format!("reel.yaml не читается: {e}")),
    }
    let voice = dir.join("build").join("voice");
    f.accents = read_json_opt(&voice.join("accents.json"));
    f.choice = read_json_opt(&voice.join("choice.json"));
    let chosen_file = f.choice.as_ref().and_then(|c| c.get("file")).and_then(Value::as_str).map(str::to_string);
    let mut takes: Vec<Take> = read_json_opt(&voice.join("takes.json")).unwrap_or_default();
    for t in &mut takes {
        t.path = Some(voice.join(&t.file).display().to_string());
        t.chosen = chosen_file.as_deref() == Some(t.file.as_str());
    }
    f.takes = takes;
    let master = voice.join("master.wav");
    f.master = master.is_file().then(|| master.display().to_string());
    if let Ok(rd) = std::fs::read_dir(dir.join("build").join("edits")) {
        for e in rd.flatten() {
            let qa = e.path().join("qa.json");
            if let Some(list) = read_json_opt::<Vec<QaIssue>>(&qa) {
                f.qa.insert(e.file_name().to_string_lossy().into_owned(), list);
            }
        }
    }
    let out = dir.join("out");
    if let Ok(rd) = std::fs::read_dir(&out) {
        let mut files: Vec<String> = rd
            .flatten()
            .map(|e| e.path())
            .filter(|p| matches!(p.extension().and_then(|x| x.to_str()), Some("mp4" | "jpg" | "png" | "md")))
            .map(|p| p.display().to_string())
            .collect();
        files.sort();
        f.out_files = files;
    }
    let report = out.join("report.md");
    if report.is_file() {
        f.report = Some(report.display().to_string());
        f.report_text = std::fs::read_to_string(&report).ok();
    }
    Ok(f)
}

/// Путь внутри папки без выхода наружу (`..`, абсолютные пути).
fn safe_join(base: &Path, rel: &str) -> Result<PathBuf> {
    let p = Path::new(rel);
    if p.components().any(|c| !matches!(c, Component::Normal(_) | Component::CurDir)) {
        return Err(AppError::Invalid(format!("недопустимый путь дубля: {rel}")));
    }
    Ok(base.join(p))
}

/// Ручной выбор дубля: копия в master.wav и обновлённый choice.json. Возвращает новый choice.
pub fn choose_take(dir: &Path, file: &str) -> Result<Value> {
    let voice = dir.join("build").join("voice");
    let takes: Vec<Take> = read_json_opt(&voice.join("takes.json"))
        .ok_or_else(|| AppError::Invalid("Дублей ещё нет: сначала выполните этап voice.".into()))?;
    let take = takes
        .iter()
        .find(|t| t.file == file)
        .ok_or_else(|| AppError::Invalid(format!("Дубль {file} не найден в takes.json")))?;
    let src = safe_join(&voice, file)?;
    if !src.is_file() {
        return Err(AppError::io(&src, &std::io::Error::from(std::io::ErrorKind::NotFound)));
    }
    let master = voice.join("master.wav");
    let tmp = voice.join("master.wav.tmp-desktop");
    std::fs::copy(&src, &tmp).map_err(|e| AppError::io(&tmp, &e))?;
    std::fs::rename(&tmp, &master).map_err(|e| AppError::io(&master, &e))?;
    let choice = serde_json::json!({
        "file": take.file, "voice": take.voice, "seed": take.seed, "ratio": take.ratio,
        "ok": true, "manual": true,
    });
    let text = serde_json::to_string_pretty(&choice).map_err(|e| AppError::Invalid(e.to_string()))?;
    write_atomic(&voice.join("choice.json"), text.as_bytes())?;
    Ok(choice)
}

/// Проекты в рабочей папке: сама папка, её подпапки и подпапки второго уровня с reel.yaml.
pub fn scan(workspace: &Path) -> Vec<PathBuf> {
    fn skip(name: &str) -> bool {
        name.starts_with('.') || matches!(name, "build" | "out" | "node_modules" | "target" | "assets" | "scenes")
    }
    let mut found = Vec::new();
    if is_project(workspace) {
        found.push(workspace.to_path_buf());
    }
    let subdirs = |d: &Path| -> Vec<PathBuf> {
        std::fs::read_dir(d)
            .map(|rd| {
                rd.flatten()
                    .filter(|e| e.file_type().map(|t| t.is_dir()).unwrap_or(false))
                    .filter(|e| !skip(&e.file_name().to_string_lossy()))
                    .map(|e| e.path())
                    .collect()
            })
            .unwrap_or_default()
    };
    for d in subdirs(workspace) {
        if is_project(&d) {
            found.push(d);
        } else {
            found.extend(subdirs(&d).into_iter().filter(|x| is_project(x)));
        }
    }
    found.sort();
    found
}

/// Имя папки из темы: латиница/кириллица/цифры через дефис, не длиннее 40 символов.
pub fn slugify(text: &str) -> String {
    let mut s = String::new();
    for c in text.to_lowercase().chars() {
        if c.is_alphanumeric() {
            s.push(c);
        } else if !s.ends_with('-') && !s.is_empty() {
            s.push('-');
        }
        if s.chars().count() >= 40 {
            break;
        }
    }
    let s = s.trim_matches('-').to_string();
    if s.is_empty() {
        "rolik".into()
    } else {
        s
    }
}

/// Свободная папка `base/name`, `base/name-2`, …
pub fn free_dir(base: &Path, name: &str) -> PathBuf {
    let first = base.join(name);
    if !first.exists() {
        return first;
    }
    (2..).map(|i| base.join(format!("{name}-{i}"))).find(|p| !p.exists()).unwrap_or(first)
}

/// Убирает якоря анимации (`at`, `*_at`) — они ссылались на слова текста шаблона.
fn strip_anchors(v: &mut serde_yaml::Value) {
    match v {
        serde_yaml::Value::Mapping(m) => {
            let keys: Vec<serde_yaml::Value> =
                m.keys().filter(|k| k.as_str().is_some_and(|s| s == "at" || s.ends_with("_at"))).cloned().collect();
            for k in keys {
                m.remove(&k);
            }
            for (_, x) in m.iter_mut() {
                strip_anchors(x);
            }
        }
        serde_yaml::Value::Sequence(s) => s.iter_mut().for_each(strip_anchors),
        _ => {}
    }
}

/// Режим «Свой текст»: блоки пользователя в reel.yaml шаблона (типы сцен сохраняются), тема и название.
pub fn write_blocks(dir: &Path, texts: &[String], theme: &str) -> Result<()> {
    let path = dir.join(CONFIG);
    let yaml = read_text(&path)?;
    let mut cfg: serde_yaml::Value =
        serde_yaml::from_str(&yaml).map_err(|e| AppError::Invalid(format!("reel.yaml шаблона не читается: {e}")))?;
    let texts: Vec<&str> = texts.iter().map(|t| t.trim()).filter(|t| !t.is_empty()).collect();
    if texts.is_empty() {
        return Err(AppError::Invalid("Текст пустой: вставьте 4 коротких абзаца.".into()));
    }
    let blocks = cfg
        .get_mut("blocks")
        .and_then(|b| b.as_sequence_mut())
        .ok_or_else(|| AppError::Invalid("В шаблоне нет блоков (blocks).".into()))?;
    if texts.len() != blocks.len() {
        return Err(AppError::Invalid(format!(
            "Нужно {} абзаца — по одному на блок (крючок, факт, поворот, панчлайн); сейчас {}.",
            blocks.len(),
            texts.len()
        )));
    }
    for (b, t) in blocks.iter_mut().zip(&texts) {
        if let Some(m) = b.as_mapping_mut() {
            m.insert("text".into(), (*t).into());
        }
        strip_anchors(b);
    }
    let title: String = texts[0].split_whitespace().take(4).collect::<Vec<_>>().join(" ");
    if let Some(m) = cfg.as_mapping_mut() {
        m.insert("title".into(), title.trim_end_matches(|c: char| !c.is_alphanumeric()).into());
        if !theme.is_empty() {
            m.insert("theme".into(), theme.into());
        }
    }
    let out = serde_yaml::to_string(&cfg).map_err(|e| AppError::Invalid(e.to_string()))?;
    write_atomic(&path, out.as_bytes())
}

/// Делит вставленный текст на абзацы (по пустым строкам; если их нет — по строкам).
pub fn split_paragraphs(text: &str) -> Vec<String> {
    let by_blank: Vec<String> = text
        .replace("\r\n", "\n")
        .split("\n\n")
        .map(|p| p.split_whitespace().collect::<Vec<_>>().join(" "))
        .filter(|p| !p.is_empty())
        .collect();
    if by_blank.len() > 1 {
        return by_blank;
    }
    text.lines().map(str::trim).filter(|l| !l.is_empty()).map(str::to_string).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp() -> tempfile::TempDir {
        tempfile::tempdir().unwrap_or_else(|e| panic!("{e}"))
    }

    const YAML: &str = "title: T\ntheme: blueprint\nlimit: 60\nblocks:\n  - name: A\n    text: Раз два — три.\n    scene: {type: headline, at: [\"раз\"]}\n  - name: B\n    text: Четыре 5 шесть\n    scene: {type: stat, value_at: x, items: [{text: a, at: b}]}\nedits: {linear: \"12\"}\n";

    #[test]
    fn words_and_blocks() {
        assert_eq!(word_count("Раз два — три."), 3);
        let d = tmp();
        std::fs::write(d.path().join(CONFIG), YAML).ok();
        let f = load(d.path()).unwrap_or_else(|e| panic!("{e}"));
        assert_eq!(f.blocks.len(), 2);
        assert_eq!(f.total_words, 6);
        assert_eq!(f.blocks[1].scene, "stat");
        assert_eq!(f.limit, Some(60));
    }

    #[test]
    fn choose_take_copies_and_updates_choice() {
        let d = tmp();
        let voice = d.path().join("build/voice");
        std::fs::create_dir_all(voice.join("takes")).ok();
        std::fs::write(d.path().join(CONFIG), YAML).ok();
        std::fs::write(voice.join("takes/a.wav"), b"AAA").ok();
        std::fs::write(voice.join("takes/b.wav"), b"BBB").ok();
        std::fs::write(voice.join("master.wav"), b"AAA").ok();
        std::fs::write(
            voice.join("takes.json"),
            r#"[{"file":"takes/a.wav","voice":"denis","seed":7,"ratio":1.0},{"file":"takes/b.wav","voice":"dmitri","seed":11,"ratio":0.9}]"#,
        )
        .ok();
        let c = choose_take(d.path(), "takes/b.wav").unwrap_or_else(|e| panic!("{e}"));
        assert_eq!(c["voice"], "dmitri");
        assert_eq!(std::fs::read(voice.join("master.wav")).unwrap_or_default(), b"BBB");
        let f = load(d.path()).unwrap_or_else(|e| panic!("{e}"));
        assert!(f.takes[1].chosen && !f.takes[0].chosen);
        assert!(choose_take(d.path(), "../../etc/passwd").is_err());
        assert!(choose_take(d.path(), "takes/zzz.wav").is_err());
    }

    #[test]
    fn write_blocks_keeps_scenes_strips_anchors() {
        let d = tmp();
        std::fs::write(d.path().join(CONFIG), YAML).ok();
        write_blocks(d.path(), &["Первый блок текста здесь.".into(), "Второй блок.".into()], "night")
            .unwrap_or_else(|e| panic!("{e}"));
        let text = std::fs::read_to_string(d.path().join(CONFIG)).unwrap_or_default();
        assert!(text.contains("Второй блок.") && text.contains("night") && text.contains("type: stat"));
        assert!(!text.contains("at:"), "{text}");
        assert!(write_blocks(d.path(), &["один".into()], "").is_err());
    }

    #[test]
    fn slugs_and_paragraphs() {
        assert_eq!(slugify("Почему исчезают туалеты?"), "почему-исчезают-туалеты");
        assert_eq!(split_paragraphs("a b\n\nc\n\n\nd").len(), 3);
        assert_eq!(split_paragraphs("a\nb\nc\nd").len(), 4);
        let d = tmp();
        std::fs::create_dir_all(d.path().join("x")).ok();
        assert_eq!(free_dir(d.path(), "x"), d.path().join("x-2"));
    }

    #[test]
    fn scan_finds_nested_projects() {
        let d = tmp();
        for p in ["a", "group/b", "build/c", ".hidden/d"] {
            std::fs::create_dir_all(d.path().join(p)).ok();
            std::fs::write(d.path().join(p).join(CONFIG), "title: x").ok();
        }
        let found = scan(d.path());
        assert_eq!(found, vec![d.path().join("a"), d.path().join("group/b")]);
    }
}
