//! `reels-app tui` — панель: проекты слева, этапы/точки/журнал выбранного справа.

use std::collections::VecDeque;
use std::path::PathBuf;
use std::time::Duration;

use anyhow::{bail, Context, Result};
use ratatui::crossterm::event::{self, Event as TermEvent, KeyCode, KeyEventKind, KeyModifiers};
use ratatui::layout::{Constraint, Direction, Layout};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, List, ListItem, ListState, Paragraph, Wrap};
use ratatui::Frame;
use reels_core::scheduler::progress_hint;
use reels_core::status::{Badge, GateState, StageState, EDIT_STAGES, ML_STAGES};
use reels_core::{Event, Phase, ProjectStatus, Python, Scheduler, Shutdown};
use tokio::sync::{mpsc, watch};

use crate::util::{fmt_dur, name_of, open_path, same, scan_mp4};
use crate::SchedArgs;

const LOG_LINES: usize = 300;
const REFRESH: Duration = Duration::from_secs(2);

enum Msg {
    Key(event::KeyEvent),
    Sched(Event),
    Status(Result<Vec<ProjectStatus>, String>),
    Info(String),
    Approved(PathBuf),
}

enum RunState {
    Idle,
    Queued,
    Running(Phase, u32),
    Waiting(String),
    Done(Duration),
    Failed(String),
}

struct Proj {
    dir: PathBuf,
    status: Option<ProjectStatus>,
    run: RunState,
    log: VecDeque<String>,
}

struct App {
    projects: Vec<Proj>,
    sel: ListState,
    info: String,
    status_err: Option<String>,
    quit_armed: bool,
}

pub async fn tui(py: Python, roots: &[PathBuf], args: &SchedArgs) -> Result<i32> {
    let dirs = reels_core::discover(roots).context("поиск проектов")?;
    if dirs.is_empty() {
        bail!("не найдено ни одного проекта (папки с reel.yaml)");
    }
    let (tx, mut rx) = mpsc::unbounded_channel::<Msg>();
    let (etx, mut erx) = mpsc::unbounded_channel::<Event>();
    let (stop_tx, stop_rx) = watch::channel(Shutdown::Run);
    let sched = Scheduler::new(py.clone(), args.config(), etx, stop_rx);

    // события планировщика → общий канал
    let t = tx.clone();
    tokio::spawn(async move {
        while let Some(ev) = erx.recv().await {
            if t.send(Msg::Sched(ev)).is_err() {
                break;
            }
        }
    });
    // клавиши: отдельный поток (crossterm читает блокирующе)
    let t = tx.clone();
    std::thread::spawn(move || loop {
        match event::poll(Duration::from_millis(200)) {
            Ok(true) => match event::read() {
                Ok(TermEvent::Key(k)) if k.kind == KeyEventKind::Press => {
                    if t.send(Msg::Key(k)).is_err() {
                        break;
                    }
                }
                Ok(_) => {}
                Err(_) => break,
            },
            Ok(false) => {
                if t.is_closed() {
                    break;
                }
            }
            Err(_) => break,
        }
    });
    // обновление status --json каждые 2 с (и сразу по запросу)
    let (refresh_tx, mut refresh_rx) = mpsc::unbounded_channel::<()>();
    {
        let t = tx.clone();
        let py = py.clone();
        let dirs = dirs.clone();
        tokio::spawn(async move {
            loop {
                let r = py.status(&dirs).await.map_err(|e| e.to_string());
                if t.send(Msg::Status(r)).is_err() {
                    break;
                }
                tokio::select! {
                    _ = tokio::time::sleep(REFRESH) => {}
                    r = refresh_rx.recv() => if r.is_none() { break },
                }
            }
        });
    }

    let mut app = App {
        projects: dirs
            .iter()
            .map(|d| Proj {
                dir: d.clone(),
                status: None,
                run: RunState::Idle,
                log: VecDeque::new(),
            })
            .collect(),
        sel: ListState::default().with_selected(Some(0)),
        info: "r — запустить, R — все, a — подтвердить, o — открыть, ↑↓ — выбор, q — выход".into(),
        status_err: None,
        quit_armed: false,
    };

    let mut term = ratatui::init();
    let res: Result<()> = async {
        loop {
            term.draw(|f| draw(f, &mut app))?;
            let Some(msg) = rx.recv().await else { break };
            match msg {
                Msg::Key(k) => {
                    let ctrl_c =
                        k.code == KeyCode::Char('c') && k.modifiers.contains(KeyModifiers::CONTROL);
                    match k.code {
                        _ if ctrl_c || matches!(k.code, KeyCode::Char('q') | KeyCode::Esc) => {
                            let running = app
                                .projects
                                .iter()
                                .any(|p| matches!(p.run, RunState::Running(..) | RunState::Queued));
                            if !running || app.quit_armed {
                                let _ = stop_tx.send(Shutdown::Kill);
                                break;
                            }
                            app.quit_armed = true;
                            app.info = "идут процессы — ещё раз q: прервать их и выйти".into();
                        }
                        KeyCode::Down | KeyCode::Char('j') => app.move_sel(1),
                        KeyCode::Up | KeyCode::Char('k') => app.move_sel(-1),
                        KeyCode::Char('r') => {
                            if let Some(i) = app.sel.selected() {
                                app.submit(&sched, i);
                            }
                        }
                        KeyCode::Char('R') => {
                            for i in 0..app.projects.len() {
                                app.submit(&sched, i);
                            }
                        }
                        KeyCode::Char('a') => app.approve(&py, &tx),
                        KeyCode::Char('o') => app.open(),
                        _ => {}
                    }
                    if !matches!(k.code, KeyCode::Char('q') | KeyCode::Esc) && !ctrl_c {
                        app.quit_armed = false;
                    }
                }
                Msg::Sched(ev) => {
                    let finished = matches!(
                        ev,
                        Event::Done { .. } | Event::Waiting { .. } | Event::Failed { .. }
                    );
                    app.on_event(ev);
                    if finished {
                        let _ = refresh_tx.send(());
                    }
                }
                Msg::Status(Ok(list)) => {
                    app.status_err = None;
                    for st in list {
                        if let Some(p) = app.projects.iter_mut().find(|p| same(&p.dir, &st.dir)) {
                            p.status = Some(st);
                        }
                    }
                }
                Msg::Status(Err(e)) => app.status_err = Some(e),
                Msg::Info(s) => app.info = s,
                Msg::Approved(dir) => {
                    if let Some(i) = app.projects.iter().position(|p| p.dir == dir) {
                        app.submit(&sched, i);
                    }
                    let _ = refresh_tx.send(());
                }
            }
        }
        Ok(())
    }
    .await;
    ratatui::restore();
    res?;
    Ok(0)
}

impl App {
    fn move_sel(&mut self, d: i32) {
        let n = self.projects.len() as i32;
        let i = self.sel.selected().unwrap_or(0) as i32;
        self.sel
            .select(Some(((i + d).rem_euclid(n.max(1))) as usize));
    }

    fn current(&self) -> Option<&Proj> {
        self.sel.selected().and_then(|i| self.projects.get(i))
    }

    fn submit(&mut self, sched: &Scheduler, i: usize) {
        let Some(p) = self.projects.get_mut(i) else {
            return;
        };
        if sched.submit(p.dir.clone()).is_some() {
            p.run = RunState::Queued;
            self.info = format!("{} — в очереди", name_of(&p.dir));
        } else {
            self.info = format!("{} уже выполняется", name_of(&p.dir));
        }
    }

    fn approve(&mut self, py: &Python, tx: &mpsc::UnboundedSender<Msg>) {
        let Some(p) = self.current() else { return };
        let gate = match &p.run {
            RunState::Waiting(g) if g != "?" => Some(g.clone()),
            _ => p
                .status
                .as_ref()
                .and_then(|s| s.waiting_gate().map(str::to_string)),
        };
        let Some(gate) = gate else {
            self.info = format!("{}: нечего подтверждать", name_of(&p.dir));
            return;
        };
        let (py, tx, dir) = (py.clone(), tx.clone(), p.dir.clone());
        self.info = format!("{}: подтверждаю «{gate}»…", name_of(&dir));
        tokio::spawn(async move {
            match py.approve(&dir, &gate).await {
                Ok(m) => {
                    let _ = tx.send(Msg::Info(format!("{}: {m}", name_of(&dir))));
                    let _ = tx.send(Msg::Approved(dir));
                }
                Err(e) => {
                    let _ = tx.send(Msg::Info(format!("✗ {}: {e}", name_of(&dir))));
                }
            }
        });
    }

    fn open(&mut self) {
        let Some(p) = self.current() else { return };
        let target = p
            .status
            .as_ref()
            .and_then(|s| {
                s.outputs
                    .values()
                    .find_map(|o| o.mp4.clone())
                    .or_else(|| s.outputs.values().find_map(|o| o.frames.clone()))
            })
            .or_else(|| scan_mp4(&p.dir).into_iter().next());
        self.info = match target {
            Some(t) => match open_path(&t) {
                Ok(()) => format!("открываю {}", t.display()),
                Err(e) => format!("✗ не открыть {}: {e}", t.display()),
            },
            None => format!("{}: готового ролика или кадров пока нет", name_of(&p.dir)),
        };
    }

    fn on_event(&mut self, ev: Event) {
        let Some(p) = self.projects.iter_mut().find(|p| p.dir == ev.dir()) else {
            return;
        };
        let line = match ev {
            Event::ProjectStarted { .. } => {
                p.run = RunState::Queued;
                "в очереди".to_string()
            }
            Event::Phase { phase, attempt, .. } => {
                p.run = RunState::Running(phase, attempt);
                format!(
                    "▶ {phase}{}",
                    if attempt > 1 {
                        format!(" (попытка {attempt})")
                    } else {
                        String::new()
                    }
                )
            }
            Event::Line { line, .. } => line,
            Event::Waiting { gate, .. } => {
                p.run = RunState::Waiting(gate.clone());
                format!("… ждёт подтверждения «{gate}» — клавиша a")
            }
            Event::Done { elapsed, .. } => {
                p.run = RunState::Done(elapsed);
                format!("✓ готово за {}", fmt_dur(elapsed))
            }
            Event::Failed { phase, error, .. } => {
                p.run = RunState::Failed(error.lines().next().unwrap_or("").to_string());
                format!("✗ {phase}: {error}")
            }
        };
        for l in line.lines() {
            if p.log.len() == LOG_LINES {
                p.log.pop_front();
            }
            p.log.push_back(l.to_string());
        }
    }
}

fn badge(p: &Proj) -> (String, Color) {
    match &p.run {
        RunState::Queued => ("⋯ очередь".into(), Color::Blue),
        RunState::Running(ph, 1) => (format!("▶ {ph}"), Color::Cyan),
        RunState::Running(ph, a) => (format!("▶ {ph} #{a}"), Color::Cyan),
        RunState::Waiting(g) => (format!("… {g}"), Color::Yellow),
        RunState::Failed(_) => ("✗ ошибка".into(), Color::Red),
        RunState::Done(d) => (format!("✓ {}", crate::util::fmt_clock(*d)), Color::Green),
        RunState::Idle => match p.status.as_ref().map(ProjectStatus::badge) {
            Some(Badge::Done) => ("✓ готово".into(), Color::Green),
            Some(Badge::Waiting(g)) => (format!("… {g}"), Color::Yellow),
            Some(Badge::Partial) => ("~ частично".into(), Color::Gray),
            Some(Badge::Todo) => ("· новый".into(), Color::DarkGray),
            Some(Badge::Error) => ("✗ reel.yaml".into(), Color::Red),
            None => ("? …".into(), Color::DarkGray),
        },
    }
}

fn stage_span(st: Option<StageState>, name: &str) -> Vec<Span<'static>> {
    let (mark, color) = match st {
        Some(StageState::Done) => ("✓", Color::Green),
        Some(StageState::Stale) => ("~", Color::Yellow),
        Some(StageState::Todo) => ("·", Color::DarkGray),
        _ => ("?", Color::DarkGray),
    };
    vec![
        Span::styled(mark.to_string(), Style::default().fg(color)),
        Span::raw(format!(" {name:<9}")),
    ]
}

fn draw(f: &mut Frame, app: &mut App) {
    let outer = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Min(5), Constraint::Length(1)])
        .split(f.area());
    let cols = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(32), Constraint::Percentage(68)])
        .split(outer[0]);

    let items: Vec<ListItem> = app
        .projects
        .iter()
        .map(|p| {
            let (b, c) = badge(p);
            ListItem::new(Line::from(vec![
                Span::styled(format!("{b:<12}"), Style::default().fg(c)),
                Span::raw(name_of(&p.dir)),
            ]))
        })
        .collect();
    let list = List::new(items)
        .block(Block::default().borders(Borders::ALL).title(" проекты "))
        .highlight_style(Style::default().add_modifier(Modifier::REVERSED))
        .highlight_symbol("› ");
    f.render_stateful_widget(list, cols[0], &mut app.sel);

    let Some(p) = app.sel.selected().and_then(|i| app.projects.get(i)) else {
        return;
    };
    let mut lines: Vec<Line> = Vec::new();
    match &p.status {
        Some(st) if st.error.is_some() => {
            lines.push(Line::styled(
                format!("✗ {}", st.error.as_deref().unwrap_or("")),
                Style::default().fg(Color::Red),
            ));
        }
        Some(st) => {
            if !st.title.is_empty() {
                lines.push(Line::styled(
                    st.title.clone(),
                    Style::default().add_modifier(Modifier::BOLD),
                ));
            }
            let mut common = vec![Span::raw(format!("{:<10}", "общие"))];
            for s in ML_STAGES {
                common.extend(stage_span(st.stage(s).map(|x| x.status), s));
            }
            lines.push(Line::from(common));
            for edit in st.edits.keys() {
                let mut row = vec![Span::raw(format!("{:<10}", format!("[{edit}]")))];
                for s in EDIT_STAGES {
                    row.extend(stage_span(
                        st.stage(&format!("{s}@{edit}")).map(|x| x.status),
                        s,
                    ));
                }
                lines.push(Line::from(row));
            }
            let mut gates = vec![Span::raw(format!("{:<10}", "точки"))];
            for g in &st.gates {
                let color = match g.status {
                    GateState::Approved => Color::Green,
                    GateState::Waiting => Color::Yellow,
                    _ => Color::DarkGray,
                };
                gates.push(Span::styled(
                    g.status.mark().to_string(),
                    Style::default().fg(color),
                ));
                gates.push(Span::raw(format!(" {:<9}", g.gate)));
            }
            lines.push(Line::from(gates));
            for (edit, o) in &st.outputs {
                if let Some(m) = &o.mp4 {
                    lines.push(Line::raw(format!(
                        "{:<10}{}",
                        format!("{edit}"),
                        m.display()
                    )));
                }
            }
        }
        None => lines.push(Line::raw("состояние загружается…")),
    }
    if let RunState::Failed(e) = &p.run {
        lines.push(Line::styled(
            format!("✗ {e}"),
            Style::default().fg(Color::Red),
        ));
    }
    if let Some(e) = &app.status_err {
        lines.push(Line::styled(
            format!("status --json: {e}"),
            Style::default().fg(Color::Red),
        ));
    }
    let right = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(lines.len() as u16 + 2),
            Constraint::Min(3),
        ])
        .split(cols[1]);
    f.render_widget(
        Paragraph::new(lines).wrap(Wrap { trim: false }).block(
            Block::default()
                .borders(Borders::ALL)
                .title(format!(" {} ", name_of(&p.dir))),
        ),
        right[0],
    );
    let h = right[1].height.saturating_sub(2) as usize;
    let log: Vec<Line> = p
        .log
        .iter()
        .skip(p.log.len().saturating_sub(h))
        .map(|l| {
            let color = if l.trim_start().starts_with('✗') {
                Color::Red
            } else if progress_hint(l).is_some() {
                Color::Reset
            } else {
                Color::DarkGray
            };
            Line::styled(l.clone(), Style::default().fg(color))
        })
        .collect();
    f.render_widget(
        Paragraph::new(log).block(Block::default().borders(Borders::ALL).title(" журнал ")),
        right[1],
    );
    f.render_widget(
        Paragraph::new(app.info.clone()).style(Style::default().fg(Color::Gray)),
        outer[1],
    );
}
