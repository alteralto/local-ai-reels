"""Библиотека шаблонов сцен. Каждый модуль пакета регистрирует свои шаблоны декоратором @scene (см. base.py)."""
import importlib
import pkgutil

from .base import REGISTRY, SceneCtx, SceneDoc, SceneError, SceneOut, Theme, esc, num, render_scene, rich, scene

for _m in pkgutil.iter_modules(__path__):
    if _m.name != "base":
        importlib.import_module(f"{__name__}.{_m.name}")

__all__ = ["REGISTRY", "SceneCtx", "SceneDoc", "SceneError", "SceneOut", "Theme", "esc", "num", "render_scene",
           "rich", "scene"]
