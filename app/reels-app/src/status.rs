//! `reels-app status` — таблица: проект × этапы, точки, готовые ролики.

use std::path::PathBuf;

use anyhow::{bail, Context, Result};
use reels_core::status::{ProjectStatus, EDIT_STAGES};
use reels_core::Python;

use crate::util::short;

pub async fn status(py: Python, roots: &[PathBuf], json: bool) -> Result<i32> {
    let dirs = reels_core::discover(roots).context("поиск проектов")?;
    if dirs.is_empty() {
        bail!("не найдено ни одного проекта (папки с reel.yaml)");
    }
    let list = py.status(&dirs).await?;
    if json {
        out(&serde_json::to_string_pretty(&list)?);
        return Ok(0);
    }
    let text: String = list.iter().map(render).collect();
    out(text.trim_end());
    Ok(if list.iter().any(|p| p.error.is_some()) {
        1
    } else {
        0
    })
}

/// Печать без паники на закрытом канале (`reels-app status --json | head`).
fn out(s: &str) {
    use std::io::Write;
    let mut o = std::io::stdout().lock();
    let _ = writeln!(o, "{s}").and_then(|_| o.flush());
}

/// Текстовый блок одного проекта.
pub fn render(p: &ProjectStatus) -> String {
    let mut s = String::new();
    let title = if p.title.is_empty() {
        String::new()
    } else {
        format!(" — {}", p.title)
    };
    s += &format!("■ {}{}   {}\n", p.name(), title, short(&p.dir));
    if let Some(e) = &p.error {
        s += &format!("  ✗ {e}\n");
        return s;
    }
    let common: Vec<String> = p
        .stages
        .iter()
        .filter(|st| st.edit.is_none())
        .map(|st| format!("{} {}", st.status.mark(), st.stage))
        .collect();
    s += &format!("  {:<10} {}\n", "этапы", common.join("  "));
    for edit in p.edits.keys() {
        let row: Vec<String> = EDIT_STAGES
            .iter()
            .filter_map(|stage| p.stage(&format!("{stage}@{edit}")))
            .map(|st| format!("{} {}", st.status.mark(), st.stage))
            .collect();
        let label = format!("[{edit}]");
        s += &format!("  {label:<10} {}\n", row.join("  "));
    }
    if !p.gates.is_empty() {
        let gates: Vec<String> = p
            .gates
            .iter()
            .map(|g| format!("{} {}", g.status.mark(), g.gate))
            .collect();
        s += &format!("  {:<10} {}\n", "точки", gates.join("  "));
    }
    for (edit, o) in &p.outputs {
        if let Some(mp4) = &o.mp4 {
            s += &format!("  {:<10} {}\n", format!("{edit}.mp4"), short(mp4));
        } else if let Some(fr) = &o.frames {
            s += &format!("  {:<10} {}\n", format!("{edit} кадры"), short(fr));
        }
    }
    if let Some(r) = &p.report {
        s += &format!("  {:<10} {}\n", "отчёт", short(r));
    }
    s
}
