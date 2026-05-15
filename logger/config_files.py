"""Helpers for loading and saving logger configuration files."""

from __future__ import annotations

from pathlib import Path

from .models import LoggerConfig


def load_logger_config(path: Path) -> LoggerConfig:
    """Load a ``LoggerConfig`` from a JSON file."""

    return LoggerConfig.model_validate_json(path.read_text(encoding="utf-8"))


def save_logger_config(config: LoggerConfig, path: Path) -> Path:
    """Atomically write a ``LoggerConfig`` JSON file and return its path."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(
        config.model_dump_json(indent=2),
        encoding="utf-8",
    )
    temp_path.replace(path)
    return path
