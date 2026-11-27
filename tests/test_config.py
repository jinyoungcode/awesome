import pytest

from stockking import config
from stockking.config import LiveTradingNotApproved, TradingMode, resolve_mode


def test_default_mode_is_paper(monkeypatch):
    monkeypatch.delenv("STOCKKING_MODE", raising=False)
    assert resolve_mode() is TradingMode.PAPER


def test_live_mode_is_blocked():
    assert config.LIVE_TRADING_APPROVED is False, "대표 승인 전 LIVE 스위치가 켜져 있으면 안 됨"
    with pytest.raises(LiveTradingNotApproved):
        resolve_mode("live")


def test_unknown_mode_rejected():
    with pytest.raises(ValueError):
        resolve_mode("real")


def test_secret_not_printed():
    s = config.Secret("super-secret-value")
    assert "super-secret-value" not in repr(s) and "super-secret-value" not in str(s)
