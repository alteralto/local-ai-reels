//! Мелочи: форматирование времени, разбор длительностей, открытие файлов, поиск готовых mp4.

use std::path::{Path, PathBuf};
use std::time::Duration;

/// «12.3 с», «4 мин 05 с», «1 ч 02 мин».
pub fn fmt_dur(d: Duration) -> String {
    let s = d.as_secs_f64();
    if s < 60.0 {
        return format!("{s:.1} с");
    }
    let t = d.as_secs();
    if t < 3600 {
        format!("{} мин {:02} с", t / 60, t % 60)
    } else {
        format!("{} ч {:02} мин", t / 3600, (t % 3600) / 60)
    }
}

/// Короткий вид: «0:42», «12:05», «1:02:03».
pub fn fmt_clock(d: Duration) -> String {
    let t = d.as_secs();
    if t >= 3600 {
        format!("{}:{:02}:{:02}", t / 3600, (t % 3600) / 60, t % 60)
    } else {
        format!("{}:{:02}", t / 60, t % 60)
    }
}

/// «5s», «500ms», «2m», «1h», «7» (секунды).
pub fn parse_duration(s: &str) -> Result<Duration, String> {
    let s = s.trim();
    let split = s
        .find(|c: char| !(c.is_ascii_digit() || c == '.'))
        .unwrap_or(s.len());
    let (num, unit) = s.split_at(split);
    let n: f64 = num
        .parse()
        .map_err(|_| format!("«{s}»: ожидается длительность вида 5s, 500ms, 2m"))?;
    let secs = match unit.trim() {
        "" | "s" | "с" | "sec" => n,
        "ms" | "мс" => n / 1000.0,
        "m" | "min" | "мин" => n * 60.0,
        "h" | "ч" => n * 3600.0,
        u => return Err(format!("«{u}»: неизвестная единица (ms, s, m, h)")),
    };
    if !secs.is_finite() || secs < 0.0 {
        return Err(format!("«{s}»: длительность должна быть положительной"));
    }
    Ok(Duration::from_secs_f64(secs))
}

/// Имя проекта для таблиц — имя папки.
pub fn name_of(dir: &Path) -> String {
    dir.file_name()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_else(|| dir.display().to_string())
}

/// Путь относительно текущей папки, если он внутри неё.
pub fn short(p: &Path) -> String {
    std::env::current_dir()
        .ok()
        .and_then(|cwd| p.strip_prefix(cwd).ok().map(Path::to_path_buf))
        .unwrap_or_else(|| p.to_path_buf())
        .display()
        .to_string()
}

/// Готовые ролики в `<проект>/out/*.mp4` — запасной путь, если status --json недоступен.
pub fn scan_mp4(dir: &Path) -> Vec<PathBuf> {
    let Ok(rd) = std::fs::read_dir(dir.join("out")) else {
        return Vec::new();
    };
    let mut v: Vec<PathBuf> = rd
        .filter_map(Result::ok)
        .map(|e| e.path())
        .filter(|p| p.extension().map(|e| e == "mp4").unwrap_or(false))
        .collect();
    v.sort();
    v
}

/// Один и тот же путь (с учётом ссылок: Python отдаёт resolve()).
pub fn same(a: &Path, b: &Path) -> bool {
    a == b || matches!((a.canonicalize(), b.canonicalize()), (Ok(x), Ok(y)) if x == y)
}

/// Открыть файл программой по умолчанию (xdg-open / open / start).
pub fn open_path(p: &Path) -> std::io::Result<()> {
    use std::process::{Command, Stdio};
    let mut cmd = if cfg!(target_os = "macos") {
        let mut c = Command::new("open");
        c.arg(p);
        c
    } else if cfg!(windows) {
        let mut c = Command::new("cmd");
        c.args(["/C", "start", ""]).arg(p);
        c
    } else {
        let mut c = Command::new("xdg-open");
        c.arg(p);
        c
    };
    cmd.stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map(|_| ())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn durations() {
        assert_eq!(parse_duration("5s"), Ok(Duration::from_secs(5)));
        assert_eq!(parse_duration("500ms"), Ok(Duration::from_millis(500)));
        assert_eq!(parse_duration("2m"), Ok(Duration::from_secs(120)));
        assert_eq!(parse_duration("7"), Ok(Duration::from_secs(7)));
        assert!(parse_duration("abc").is_err());
        assert!(parse_duration("5 лет").is_err());
        assert_eq!(fmt_dur(Duration::from_millis(12_300)), "12.3 с");
        assert_eq!(fmt_dur(Duration::from_secs(245)), "4 мин 05 с");
        assert_eq!(fmt_clock(Duration::from_secs(3723)), "1:02:03");
    }
}
