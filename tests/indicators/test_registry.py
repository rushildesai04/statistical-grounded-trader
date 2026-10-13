"""Registry: validate_spec rejections, IndicatorBank snapshot keys, defaults."""
from __future__ import annotations

from datetime import date

import pytest

from sgt.core.interfaces import IndicatorSpec
from sgt.indicators import IndicatorBank, KINDS, build, validate_spec

from _helpers import mk_bar

D1 = date(2026, 1, 5)


def test_validate_fills_defaults():
    p = validate_spec(IndicatorSpec.of("ema"))
    assert p == {"n": 9}
    p2 = validate_spec(IndicatorSpec.of("macd"))
    assert p2 == {"fast": 12, "slow": 26, "signal": 9}


@pytest.mark.parametrize(
    "spec",
    [
        IndicatorSpec.of("nope"),                       # unknown kind
        IndicatorSpec.of("ema", bad=3),                 # unknown param
        IndicatorSpec.of("ema", n=0),                   # below range
        IndicatorSpec.of("ema", n=501),                 # above range
        IndicatorSpec.of("ema", n=9.5),                 # non-integer
        IndicatorSpec.of("psar", start=0.3, max=0.2),   # start > max
        IndicatorSpec.of("psar", start=0.0),            # start not > 0
        IndicatorSpec.of("psar", max=1.5),              # max > 1
        IndicatorSpec.of("macd", fast=26, slow=12),     # fast >= slow
        IndicatorSpec.of("vwap", anchor="weekly"),      # bad choice
        IndicatorSpec.of("pvt", signal_kind="wma"),     # bad choice
        IndicatorSpec.of("ewo", fast=35, slow=5),       # fast >= slow
    ],
)
def test_validate_rejects_bad_specs(spec):
    with pytest.raises(ValueError):
        validate_spec(spec)
    with pytest.raises(ValueError):
        build(spec)


def test_indicator_bank_snapshot_keys_and_reset():
    specs = [
        IndicatorSpec.of("ema", n=3),
        IndicatorSpec.of("macd", fast=2, slow=3, signal=2),
        IndicatorSpec.of("vwap", anchor="session"),
    ]
    bank = IndicatorBank(specs)
    assert len(bank) == 3
    bars = [mk_bar(10.0 + i, i=i, volume=100, minute=570 + i, session=D1) for i in range(8)]
    for b in bars:
        bank.update(b)
    snap = bank.snapshot()
    expected_keys = {
        "ema(n=3).value",
        "macd(fast=2,signal=2,slow=3).line",
        "macd(fast=2,signal=2,slow=3).signal",
        "macd(fast=2,signal=2,slow=3).hist",
        "vwap(anchor=session).value",
    }
    assert set(snap) == expected_keys
    assert snap["ema(n=3).value"] is not None
    # access + reset
    assert "ema(n=3)" in bank
    assert bank["ema(n=3)"].ready
    bank.reset()
    snap2 = bank.snapshot()
    assert all(v is None for v in snap2.values())


def test_bank_rejects_duplicate_spec():
    with pytest.raises(ValueError):
        IndicatorBank([IndicatorSpec.of("ema", n=3), IndicatorSpec.of("ema", n=3)])


def test_kinds_outputs_match_built_indicator():
    for kind, info in KINDS.items():
        ind = build(IndicatorSpec.of(kind))
        assert ind.outputs == info.outputs, kind
