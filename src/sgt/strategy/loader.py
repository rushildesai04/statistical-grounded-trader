"""Load and hash strategy configs.

``load_strategy`` reads a YAML file (size-bounded, ``safe_load`` only — external
bytes are hostile), validates the grammar, and returns a ``StrategyConfig``.
``config_hash`` is the config's content identity (key-order independent) used as its
registry id and as the ``strategy_id`` component of deterministic order ids.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from sgt.core.ids import config_hash as _config_hash
from sgt.strategy.schema import StrategyConfig
from sgt.strategy.validate import StrategyValidationError, validate_strategy

_MAX_CONFIG_BYTES = 1_000_000  # 1 MB: a strategy config is tiny; refuse anything larger.


def load_strategy(path: str | Path) -> StrategyConfig:
    p = Path(path)
    size = p.stat().st_size
    if size > _MAX_CONFIG_BYTES:
        raise StrategyValidationError([f"config file {p} is {size} bytes (> {_MAX_CONFIG_BYTES} limit)"])
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise StrategyValidationError([f"config at {p} must be a mapping, got {type(raw).__name__}"])
    return validate_strategy(raw)


def config_hash(config: StrategyConfig) -> str:
    """Stable 16-char content hash over the canonical JSON model dump."""
    return _config_hash(config.model_dump(mode="json"))
