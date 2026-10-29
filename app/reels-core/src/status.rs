//! Типы ответа `python -m reels status --json` (контракт с Python-пакетом).

use std::path::PathBuf;

use indexmap::IndexMap;
use serde::{Deserialize, Serialize};

/// Этапы конвейера в порядке выполнения (как `reels.pipeline.STAGES`).
pub const STAGES: [&str; 9] = [
    "check", "accent", "voice", "align", "music", "build", "snapshot", "render", "finish",
];
/// Этапы, которые выполняются для каждого монтажа.
pub const EDIT_STAGES: [&str; 4] = ["build", "snapshot", "render", "finish"];
/// Этапы фазы «ml» (`run --until music`): им нужна видеокарта.
pub const ML_STAGES: [&str; 5] = ["check", "accent", "voice", "align", "music"];
/// Контрольные точки, которые встречаются в фазе «ml».
pub const ML_GATES: [&str; 3] = ["script", "accents", "take"];

/// Состояние одного проекта.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ProjectStatus {
    pub dir: PathBuf,
    #[serde(default)]
    pub slug: String,
    #[serde(default)]
    pub title: String,
    /// Монтажи: имя → порядок блоков ("1234" или список чисел — принимаем оба вида).
    #[serde(default)]
    pub edits: IndexMap<String, serde_json::Value>,
    #[serde(default)]
    pub stages: Vec<StageStatus>,
    #[serde(default)]
    pub gates: Vec<GateStatus>,
    #[serde(default)]
    pub outputs: IndexMap<String, Outputs>,
    #[serde(default)]
    pub report: Option<PathBuf>,
    #[serde(default)]
    pub error: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct StageStatus {
    /// "voice" или "render@linear".
    pub key: String,
    pub stage: String,
    #[serde(default)]
    pub edit: Option<String>,
    pub status: StageState,
    #[serde(default)]
    pub at: String,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum StageState {
    Done,
    Stale,
    Todo,
    #[serde(other)]
    Unknown,
}

impl StageState {
    /// Значок для таблиц: ✓ готово, ~ устарело, · не выполнялось.
    pub fn mark(self) -> &'static str {
        match self {
            StageState::Done => "✓",
            StageState::Stale => "~",
            StageState::Todo => "·",
            StageState::Unknown => "?",
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct GateStatus {
    pub gate: String,
    #[serde(default)]
    pub stage: String,
    pub status: GateState,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
pub enum GateState {
    #[serde(rename = "approved")]
    Approved,
    #[serde(rename = "waiting")]
    Waiting,
    #[serde(rename = "todo")]
    Todo,
    #[serde(rename = "n/a")]
    NotApplicable,
    #[serde(other)]
    Unknown,
}

impl GateState {
    pub fn mark(self) -> &'static str {
        match self {
            GateState::Approved => "✓",
            GateState::Waiting => "…",
            GateState::Todo => "·",
            GateState::NotApplicable => "–",
            GateState::Unknown => "?",
        }
    }

    /// Точка не мешает идти дальше.
    pub fn passable(self) -> bool {
        matches!(self, GateState::Approved | GateState::NotApplicable)
    }
}

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
pub struct Outputs {
    #[serde(default)]
    pub mp4: Option<PathBuf>,
    #[serde(default)]
    pub frames: Option<PathBuf>,
}

/// Сводное состояние проекта для значка в списках.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Badge {
    Done,
    Waiting(String),
    Partial,
    Todo,
    Error,
}

impl ProjectStatus {
    /// Разбирает вывод `status --json`.
    pub fn parse_list(json: &str) -> Result<Vec<ProjectStatus>, serde_json::Error> {
        serde_json::from_str(json)
    }

    /// Имя для таблиц: slug, а если его нет — имя папки.
    pub fn name(&self) -> String {
        if !self.slug.is_empty() {
            return self.slug.clone();
        }
        self.dir
            .file_name()
            .map(|s| s.to_string_lossy().into_owned())
            .unwrap_or_else(|| self.dir.display().to_string())
    }

    pub fn stage(&self, key: &str) -> Option<&StageStatus> {
        self.stages.iter().find(|s| s.key == key)
    }

    pub fn gate(&self, gate: &str) -> Option<&GateStatus> {
        self.gates.iter().find(|g| g.gate == gate)
    }

    /// Первая контрольная точка, которая ждёт подтверждения.
    pub fn waiting_gate(&self) -> Option<&str> {
        self.gates
            .iter()
            .find(|g| g.status == GateState::Waiting)
            .map(|g| g.gate.as_str())
    }

    /// Фаза «ml» уже выполнена и подтверждена: её можно не ставить в очередь на видеокарту.
    pub fn ml_ready(&self) -> bool {
        let stages_ok = ML_STAGES.iter().all(|k| {
            self.stage(k)
                .map(|s| s.status == StageState::Done)
                .unwrap_or(false)
        });
        let gates_ok = ML_GATES
            .iter()
            .all(|g| self.gate(g).map(|g| g.status.passable()).unwrap_or(false));
        self.error.is_none() && stages_ok && gates_ok
    }

    /// Все этапы готовы.
    pub fn all_done(&self) -> bool {
        !self.stages.is_empty() && self.stages.iter().all(|s| s.status == StageState::Done)
    }

    pub fn badge(&self) -> Badge {
        if self.error.is_some() {
            return Badge::Error;
        }
        if self.all_done() {
            return Badge::Done;
        }
        if let Some(g) = self.waiting_gate() {
            return Badge::Waiting(g.to_string());
        }
        if self
            .stages
            .iter()
            .any(|s| matches!(s.status, StageState::Done | StageState::Stale))
        {
            Badge::Partial
        } else {
            Badge::Todo
        }
    }

    /// Готовые mp4 по монтажам.
    pub fn mp4s(&self) -> Vec<(&str, &PathBuf)> {
        self.outputs
            .iter()
            .filter_map(|(e, o)| o.mp4.as_ref().map(|p| (e.as_str(), p)))
            .collect()
    }

    /// Порядок блоков монтажа строкой.
    pub fn edit_order(&self, edit: &str) -> String {
        match self.edits.get(edit) {
            Some(serde_json::Value::String(s)) => s.clone(),
            Some(serde_json::Value::Array(a)) => a.iter().map(|v| v.to_string()).collect(),
            Some(v) => v.to_string(),
            None => String::new(),
        }
    }
}
