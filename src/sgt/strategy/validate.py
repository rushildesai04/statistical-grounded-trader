"""Grammar validator (plan §7.1 composition rules).

``validate_strategy`` returns a fully-parsed :class:`StrategyConfig` or raises
:class:`StrategyValidationError` carrying the COMPLETE list of violations (never
just the first), so a bad config surfaces every problem at once. It accepts either
a raw mapping (as parsed from YAML) or an already-built ``StrategyConfig``.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from sgt.indicators.registry import validate_spec
from sgt.strategy.base import Family, Role
from sgt.strategy.schema import StrategyConfig


class StrategyValidationError(ValueError):
    def __init__(self, violations: list[str]) -> None:
        self.violations = list(violations)
        super().__init__("strategy validation failed:\n  - " + "\n  - ".join(self.violations))


def _role_ok(cond: Any, role: Role) -> bool:
    return role in cond.ROLES


def validate_strategy(data: Mapping[str, Any] | StrategyConfig) -> StrategyConfig:
    violations: list[str] = []

    if isinstance(data, StrategyConfig):
        config: StrategyConfig | None = data
    else:
        raw = dict(data)
        trig = raw.get("trigger")
        if isinstance(trig, list):
            # "exactly one trigger": record the cardinality violation, then unwrap
            # (or drop) so pydantic can still parse the rest and we report everything.
            if len(trig) != 1:
                violations.append(f"exactly one trigger is required, got {len(trig)}")
            raw = {**raw, "trigger": trig[0]} if trig else {k: v for k, v in raw.items() if k != "trigger"}
        try:
            config = StrategyConfig.model_validate(raw)
        except ValidationError as e:
            violations.append(f"schema error: {e.error_count()} issue(s): {e.errors(include_url=False)}")
            raise StrategyValidationError(violations) from e

    assert config is not None
    _check_grammar(config, violations)
    if violations:
        raise StrategyValidationError(violations)
    return config


def _check_grammar(config: StrategyConfig, violations: list[str]) -> None:
    trig = config.trigger
    tfam = trig.FAMILY
    tgroup = trig.CORRELATION_GROUP

    # 1) trigger must be allowed as a trigger; context can never trigger.
    if not _role_ok(trig, Role.TRIGGER):
        violations.append(f"trigger '{trig.kind}' may not be used as a trigger (roles={_roles(trig)})")
    if tfam is Family.CONTEXT:
        violations.append(f"context condition '{trig.kind}' cannot be a trigger")

    # 2) role placement for every other slot.
    for c in config.regime:
        if not _role_ok(c, Role.REGIME):
            violations.append(f"regime condition '{c.kind}' is not allowed in regime (roles={_roles(c)})")
    if config.setup is not None:
        for c in config.setup.any_of:
            if not _role_ok(c, Role.SETUP):
                violations.append(f"setup condition '{c.kind}' is not allowed in setup (roles={_roles(c)})")
    for c in config.filters:
        if not _role_ok(c, Role.FILTER):
            violations.append(f"filter condition '{c.kind}' is not allowed in filters (roles={_roles(c)})")
    for c in config.exits.any_of:
        if not _role_ok(c, Role.EXIT):
            violations.append(f"exit condition '{c.kind}' is not allowed in exits (roles={_roles(c)})")

    # 3) confirmations: role, family != trigger family, correlation groups.
    seen_groups: dict[str, str] = {}
    for c in config.confirm.signals:
        if not _role_ok(c, Role.CONFIRM):
            violations.append(f"confirm signal '{c.kind}' is not allowed in confirm (roles={_roles(c)})")
        if c.FAMILY is tfam:
            violations.append(
                f"confirm signal '{c.kind}' shares the trigger family '{tfam.value}' "
                "(confirmations must come from a different family)")
        g = c.CORRELATION_GROUP
        if g is not None:
            if g == tgroup:
                violations.append(
                    f"confirm signal '{c.kind}' shares the trigger's correlation group '{g}'")
            if g in seen_groups:
                violations.append(
                    f"confirm signals '{seen_groups[g]}' and '{c.kind}' are both in correlation "
                    f"group '{g}' (correlated signals count once in k-of-m)")
            else:
                seen_groups[g] = c.kind

    # 4) k-of-m bound.
    if config.confirm.k_of_m > len(config.confirm.signals):
        violations.append(
            f"confirm.k_of_m ({config.confirm.k_of_m}) exceeds number of signals "
            f"({len(config.confirm.signals)})")

    # 5) mandatory exits.
    exit_kinds = {c.kind for c in config.exits.any_of}
    if "stop_loss_pct" not in exit_kinds:
        violations.append("exits must include a 'stop_loss_pct' (hard stop)")
    if "time_stop_minutes" not in exit_kinds:
        violations.append("exits must include a 'time_stop_minutes' (time stop)")

    # 5b) extended-hours consistency (F-5c): a strategy whose regime admits any
    # extended-hours window must declare extended_hours, and its session_end flatten
    # must target the matching close (post when extended-hours, else rth).
    regime_windows: set[str] = set()
    for c in config.regime:
        if c.kind == "session_window_in":
            regime_windows |= set(c.windows)
    ext_windows = regime_windows & {"pre", "post", "after_hours"}
    if ext_windows and not config.extended_hours:
        violations.append(
            f"regime trades extended-hours window(s) {sorted(ext_windows)} but extended_hours is false")
    want_window = "post" if config.extended_hours else "rth"
    for c in config.exits.any_of:
        if c.kind == "session_end" and c.window != want_window:
            violations.append(
                f"session_end.window must be '{want_window}' when extended_hours={config.extended_hours}")

    _check_fast_slow(config, violations)

    # 6) every required indicator spec must validate against the registry.
    for c in config.all_conditions():
        for spec in c.required_specs():
            try:
                validate_spec(spec)
            except ValueError as e:
                violations.append(f"condition '{c.kind}' has an invalid indicator spec {spec.key}: {e}")


def _roles(c: Any) -> str:
    return ",".join(sorted(r.value for r in c.ROLES))


def _check_fast_slow(config: StrategyConfig, violations: list[str]) -> None:
    """A two-line cross with fast >= slow is meaningless (2026-10-13, F-6): the EMA
    registry validates each EMA alone, so the pair rule must live in the grammar."""
    for c in config.all_conditions():
        fast, slow = getattr(c, "fast", None), getattr(c, "slow", None)
        if isinstance(fast, int) and isinstance(slow, int) and fast >= slow:
            violations.append(f"condition '{c.kind}' requires fast < slow, got fast={fast} slow={slow}")
