//! Поиск проектов: папки с `reel.yaml` под заданными корнями.

use std::collections::BTreeSet;
use std::io;
use std::path::{Path, PathBuf};

pub const CONFIG_NAME: &str = "reel.yaml";

/// Папки, в которые не заходим: артефакты сборки, зависимости, служебное.
const SKIP_DIRS: [&str; 7] = [
    "build",
    "out",
    "node_modules",
    ".git",
    "target",
    "__pycache__",
    ".venv",
];
const MAX_DEPTH: usize = 12;

/// Проекты под корнями: сам корень (если в нём `reel.yaml`, или это путь к `reel.yaml`) и все вложенные папки.
/// Внутрь найденного проекта не спускаемся; пакеты Python (`__init__.py`) и скрытые папки пропускаем.
/// Результат — абсолютные пути без повторов, по алфавиту.
pub fn discover<P: AsRef<Path>>(roots: &[P]) -> io::Result<Vec<PathBuf>> {
    let mut found = BTreeSet::new();
    for root in roots {
        let root = root.as_ref();
        let root = if root.is_file() && root.file_name().map(|n| n == CONFIG_NAME).unwrap_or(false)
        {
            root.parent().unwrap_or(Path::new(".")).to_path_buf()
        } else {
            root.to_path_buf()
        };
        if !root.is_dir() {
            return Err(io::Error::new(
                io::ErrorKind::NotFound,
                format!("{}: нет такой папки", root.display()),
            ));
        }
        let abs = absolute(&root);
        walk(&abs, 0, &mut found);
    }
    Ok(found.into_iter().collect())
}

/// Абсолютный путь без разыменования ссылок (canonicalize на Windows даёт \\?\ — не годится для python).
pub fn absolute(p: &Path) -> PathBuf {
    std::path::absolute(p).unwrap_or_else(|_| p.to_path_buf())
}

pub fn is_project(dir: &Path) -> bool {
    dir.join(CONFIG_NAME).is_file()
}

fn walk(dir: &Path, depth: usize, found: &mut BTreeSet<PathBuf>) {
    if is_project(dir) {
        found.insert(dir.to_path_buf());
        return;
    }
    if depth >= MAX_DEPTH || dir.join("__init__.py").exists() {
        return;
    }
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    let mut subdirs: Vec<PathBuf> = entries
        .filter_map(Result::ok)
        .filter(|e| e.file_type().map(|t| t.is_dir()).unwrap_or(false)) // ссылки не разыменовываем
        .filter(|e| {
            let name = e.file_name();
            let name = name.to_string_lossy();
            !name.starts_with('.') && !SKIP_DIRS.contains(&name.as_ref())
        })
        .map(|e| e.path())
        .collect();
    subdirs.sort();
    for d in subdirs {
        walk(&d, depth + 1, found);
    }
}
