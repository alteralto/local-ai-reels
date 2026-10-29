//! Ошибки приложения: всё, что уходит в интерфейс, — по-русски и с подсказкой, что делать.
use std::io;
use std::path::Path;

#[derive(Debug, thiserror::Error)]
pub enum AppError {
    #[error("Не найден Python с пакетом reels. Установите Python 3.10+ и пакет: pip install local-ai-reels — или укажите интерпретатор в переменной окружения REELS_PYTHON.")]
    PythonNotFound,
    #[error("Не удалось запустить Python ({exe}): {msg}")]
    Spawn { exe: String, msg: String },
    #[error("Команда reels завершилась с ошибкой (код {code}): {msg}")]
    Python { code: i32, msg: String },
    #[error("Python ответил не в формате JSON ({0}). Обновите пакет reels: нужна команда «reels status --json».")]
    BadJson(String),
    #[error("{path}: {msg}")]
    Io { path: String, msg: String },
    #[error("Проект «{0}» уже выполняется или стоит в очереди.")]
    Busy(String),
    #[error("В папке {0} нет reel.yaml — это не проект ролика.")]
    NoProject(String),
    #[error("{0}")]
    Invalid(String),
}

pub type Result<T> = std::result::Result<T, AppError>;

impl serde::Serialize for AppError {
    fn serialize<S: serde::Serializer>(&self, s: S) -> std::result::Result<S::Ok, S::Error> {
        s.serialize_str(&self.to_string())
    }
}

/// Понятное описание ошибки ввода-вывода.
pub fn io_text(e: &io::Error) -> String {
    match e.kind() {
        io::ErrorKind::NotFound => "файл или папка не найдены".into(),
        io::ErrorKind::PermissionDenied => "нет прав доступа".into(),
        io::ErrorKind::AlreadyExists => "уже существует".into(),
        io::ErrorKind::InvalidData => "файл повреждён или не в UTF-8".into(),
        _ => format!("ошибка ввода-вывода: {e}"),
    }
}

impl AppError {
    pub fn io(path: &Path, e: &io::Error) -> Self {
        AppError::Io { path: path.display().to_string(), msg: io_text(e) }
    }
}

/// Подсказка простым языком по тексту ошибки или хвосту журнала Python.
pub fn hint(text: &str) -> Option<&'static str> {
    let t = text.to_lowercase();
    let rules: &[(&[&str], &str)] = &[
        (
            &["no module named reels", "no module named 'reels'", "не найден python"],
            "Не найден Python с пакетом reels — установите: pip install local-ai-reels",
        ),
        (
            &["no module named"],
            "Не хватает Python-библиотеки — установите зависимости: pip install local-ai-reels[all]",
        ),
        (
            &["cuda", "nvidia", "gpu", "видеокарт"],
            "Нет видеокарты — используйте голос Piper (voice.engine: piper) или готовую озвучку (voice.engine: file)",
        ),
        (&["ffmpeg"], "Не найден ffmpeg — установите его и добавьте в PATH"),
        (
            &["npx", "node", "hyperframes", "chrome", "браузер"],
            "Для рендера нужен Node.js 22+ и браузер Chrome/Chromium — установите их",
        ),
        (
            &["connection refused", "localhost:1234", "llm", "/v1", "urlopen"],
            "Не отвечает локальная языковая модель — запустите LM Studio или Ollama (адрес — REELS_LLM_URL)",
        ),
        (&["нет reel.yaml", "configerror"], "Проверьте reel.yaml: в нём ошибка в настройках"),
        (&["слов", "limit"], "Сценарий длиннее лимита — сократите текст блоков"),
    ];
    rules.iter().find(|(keys, _)| keys.iter().any(|k| t.contains(k))).map(|(_, h)| *h)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hints_are_plain_language() {
        assert!(hint("ModuleNotFoundError: No module named reels").unwrap_or("").contains("pip install"));
        assert!(hint("torch: CUDA not available").unwrap_or("").contains("видеокарты"));
        assert_eq!(hint("всё хорошо"), None);
    }

    #[test]
    fn error_serializes_as_text() {
        let e = AppError::Busy("povodok".into());
        let s = serde_json::to_string(&e).unwrap_or_default();
        assert!(s.contains("povodok"));
    }
}
