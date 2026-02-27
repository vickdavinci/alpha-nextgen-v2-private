from data.data_validator import (
    is_option_symbol,
    normalize_symbol_key,
    validate_option_order_payload,
)
from data.indicator_manager import is_indicator_ready, rolling_window_ready, safe_indicator_value
from data.symbol_manager import build_spread_runtime_key, symbols_match


class _IndicatorMock:
    def __init__(self, value: float, ready: bool = True):
        self.IsReady = ready
        self.Current = type("C", (), {"Value": value})()


class _WindowMock:
    def __init__(self, count: int):
        self.Count = count


def test_symbol_key_normalization_collapses_whitespace():
    assert normalize_symbol_key("  qqq   260130c00500000 ") == "QQQ 260130C00500000"


def test_option_payload_validation():
    ok, code = validate_option_order_payload("QQQ 260130C00500000", 2.5, 3)
    assert ok is True
    assert code == "OK"

    bad, bad_code = validate_option_order_payload("QQQ", 2.5, 3)
    assert bad is False
    assert bad_code == "E_INVALID_OPTION_SYMBOL"


def test_symbol_manager_helpers():
    assert symbols_match("QQQ 260130C00500000", " qqq   260130c00500000 ")
    assert (
        build_spread_runtime_key("QQQ 260130C00500000", "QQQ 260130C00510000")
        == "QQQ 260130C00500000|QQQ 260130C00510000"
    )
    assert is_option_symbol("QQQ 260130C00500000") is True


def test_indicator_manager_helpers():
    ready = _IndicatorMock(42.0, ready=True)
    not_ready = _IndicatorMock(99.0, ready=False)
    assert is_indicator_ready(ready) is True
    assert safe_indicator_value(ready) == 42.0
    assert safe_indicator_value(not_ready, default=1.25) == 1.25
    assert rolling_window_ready(_WindowMock(5), min_size=3) is True
    assert rolling_window_ready(_WindowMock(1), min_size=3) is False
