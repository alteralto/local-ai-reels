//! Ядро Rust-диспетчера local-ai-reels.
//!
//! * [`status`] — типы ответа `python -m reels status --json`;
//! * [`python`] — запуск `python -m reels …` и разбор кодов выхода;
//! * [`discover`] — поиск проектов (папок с `reel.yaml`);
//! * [`scheduler`] — параллельный конвейер: фаза «ml» на слотах видеокарты, «render» на слотах процессора.

pub mod discover;
pub mod python;
pub mod scheduler;
pub mod status;

pub use discover::discover;
pub use python::{Python, Shutdown};
pub use scheduler::{Event, Outcome, Phase, ProjectReport, SchedConfig, Scheduler, Summary};
pub use status::ProjectStatus;
