//! Разбор контракта `status --json` и поиск проектов.

use std::path::PathBuf;

use reels_core::status::{Badge, GateState, ProjectStatus, StageState};

const FIXTURE: &str = r#"[
 {"dir": "/work/reels/povodok", "slug": "povodok", "title": "Поводок",
  "edits": {"linear": "1234", "hook": "3124"},
  "stages": [
   {"key": "check", "stage": "check", "edit": null, "status": "done", "at": "2026-10-29T10:00:00"},
   {"key": "accent", "stage": "accent", "edit": null, "status": "done", "at": "2026-10-29T10:00:05"},
   {"key": "voice", "stage": "voice", "edit": null, "status": "done", "at": "2026-10-29T10:01:00"},
   {"key": "align", "stage": "align", "edit": null, "status": "done", "at": "2026-10-29T10:01:30"},
   {"key": "music", "stage": "music", "edit": null, "status": "done", "at": "2026-10-29T10:02:00"},
   {"key": "build@linear", "stage": "build", "edit": "linear", "status": "done", "at": "2026-10-29T10:02:10"},
   {"key": "build@hook", "stage": "build", "edit": "hook", "status": "stale", "at": "2026-10-28T10:02:10"},
   {"key": "snapshot@linear", "stage": "snapshot", "edit": "linear", "status": "done", "at": ""},
   {"key": "snapshot@hook", "stage": "snapshot", "edit": "hook", "status": "todo", "at": ""},
   {"key": "render@linear", "stage": "render", "edit": "linear", "status": "todo", "at": ""},
   {"key": "render@hook", "stage": "render", "edit": "hook", "status": "todo", "at": ""},
   {"key": "finish@linear", "stage": "finish", "edit": "linear", "status": "todo", "at": ""},
   {"key": "finish@hook", "stage": "finish", "edit": "hook", "status": "todo", "at": ""}
  ],
  "gates": [
   {"gate": "script", "stage": "check", "status": "approved"},
   {"gate": "accents", "stage": "accent", "status": "n/a"},
   {"gate": "take", "stage": "voice", "status": "approved"},
   {"gate": "frames", "stage": "snapshot", "status": "waiting"}
  ],
  "outputs": {"linear": {"mp4": "/work/reels/povodok/out/povodok-linear.mp4", "frames": null},
              "hook": {"mp4": null, "frames": "/work/reels/povodok/build/hook/frames.png"}},
  "report": null, "error": null},
 {"dir": "/work/reels/broken", "slug": "broken", "title": "",
  "edits": {}, "stages": [], "gates": [], "outputs": {},
  "report": null, "error": "reel.yaml: нет blocks"},
 {"dir": "/work/reels/fresh", "slug": "fresh", "title": "Новый",
  "edits": {"linear": [1, 2]},
  "stages": [{"key": "check", "stage": "check", "edit": null, "status": "todo", "at": ""},
             {"key": "future", "stage": "future", "edit": null, "status": "skipped", "at": ""}],
  "gates": [{"gate": "script", "stage": "check", "status": "todo"},
            {"gate": "take", "stage": "voice", "status": "maybe"}],
  "outputs": {"linear": {"mp4": null, "frames": null}}, "report": "/work/reels/fresh/out/report.md", "error": null}
]"#;

#[test]
fn parses_contract_fixture() {
    let list = ProjectStatus::parse_list(FIXTURE).expect("parse");
    assert_eq!(list.len(), 3);
    let p = &list[0];
    assert_eq!(p.name(), "povodok");
    assert_eq!(p.title, "Поводок");
    assert_eq!(
        p.edits.keys().collect::<Vec<_>>(),
        ["linear", "hook"],
        "порядок монтажей сохраняется"
    );
    assert_eq!(p.edit_order("hook"), "3124");
    assert_eq!(
        p.stage("build@hook").map(|s| s.status),
        Some(StageState::Stale)
    );
    assert_eq!(
        p.stage("render@linear").and_then(|s| s.edit.as_deref()),
        Some("linear")
    );
    assert_eq!(
        p.gate("accents").map(|g| g.status),
        Some(GateState::NotApplicable)
    );
    assert!(p.ml_ready(), "ml готова: этапы done, точки approved/n/a");
    assert_eq!(p.waiting_gate(), Some("frames"));
    assert_eq!(p.badge(), Badge::Waiting("frames".into()));
    assert_eq!(
        p.mp4s(),
        vec![(
            "linear",
            &PathBuf::from("/work/reels/povodok/out/povodok-linear.mp4")
        )]
    );

    let b = &list[1];
    assert_eq!(b.badge(), Badge::Error);
    assert!(!b.ml_ready());

    let f = &list[2];
    assert_eq!(f.edit_order("linear"), "12");
    assert_eq!(
        f.stage("future").map(|s| s.status),
        Some(StageState::Unknown),
        "неизвестный статус не ломает разбор"
    );
    assert_eq!(f.gate("take").map(|g| g.status), Some(GateState::Unknown));
    assert_eq!(f.badge(), Badge::Todo);
    assert!(!f.ml_ready());
    assert_eq!(
        f.report.as_deref(),
        Some(std::path::Path::new("/work/reels/fresh/out/report.md"))
    );
}

#[test]
fn tolerates_missing_optional_fields() {
    let list = ProjectStatus::parse_list(r#"[{"dir": "/x/y"}]"#).expect("parse");
    assert_eq!(list[0].name(), "y");
    assert!(list[0].stages.is_empty());
    assert!(ProjectStatus::parse_list("{не json").is_err());
}

#[test]
fn discovers_projects_in_tree() {
    let tmp = tempfile::tempdir().expect("tempdir");
    let r = tmp.path();
    let mk = |p: &str| {
        let d = r.join(p);
        std::fs::create_dir_all(&d).expect("mkdir");
        std::fs::write(d.join("reel.yaml"), "slug: x\n").expect("write");
    };
    mk("a");
    mk("group/b");
    mk("group/deep/er/c");
    mk("a/scenes/nested"); // внутрь проекта не спускаемся
    mk("x/build/ghost");
    mk("x/out/ghost");
    mk("x/node_modules/ghost");
    mk(".git/ghost");
    mk(".hidden/ghost");
    mk("pkg/templates"); // пакет Python — шаблон, не проект
    std::fs::write(r.join("pkg/__init__.py"), "").expect("init");
    std::fs::create_dir_all(r.join("empty")).expect("mkdir");

    let found = reels_core::discover(&[r]).expect("discover");
    let rel: Vec<String> = found
        .iter()
        .map(|p| {
            p.strip_prefix(r)
                .expect("prefix")
                .to_string_lossy()
                .replace('\\', "/")
        })
        .collect();
    assert_eq!(rel, ["a", "group/b", "group/deep/er/c"]);
    assert!(found.iter().all(|p| p.is_absolute()));

    // корень-проект, путь к reel.yaml и повторы
    let again = reels_core::discover(&[r.join("a"), r.join("a/reel.yaml"), r.join("group")])
        .expect("discover");
    assert_eq!(again.len(), 3);
    assert!(reels_core::discover(&[r.join("нет-такой")]).is_err());
}
