import pytest

from stockking.backtest.costs import CostModel


@pytest.fixture
def costs():
    # 테스트 전용 가정치 — 실제 수수료·세율 아님
    return CostModel(commission_rate=0.00015, sell_tax_rate=0.002, slippage_bps=5, source="TEST")


@pytest.fixture(autouse=True)
def isolate_kill_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKKING_KILL_SWITCH_FILE", str(tmp_path / "KILL_SWITCH"))
