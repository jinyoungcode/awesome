import pytest

from stockking.backtest.costs import CostModel


def test_domestic_unchanged():
    c = CostModel(0.00015, 0.002, 5, source="T")
    assert c.us_reg_fees(1_000_000, 10) == 0 and c.sell_charges(1_000_000, 10) == 2_000


def test_us_reg_fees():
    c = CostModel.us_from_usd(0.0025, 1_400.0, 5, source="COST_MODEL v0.2")
    # 1,000만원 매도, 100주: SEC 0.0000206 → 206원, TAF 100 × 0.000195 × 1,400 = 27.3 → 28원
    assert c.us_reg_fees(10_000_000, 100) == 206 + 28
    # TAF 상한 $9.79 × 1,400 = 13,706원
    assert c.us_reg_fees(0, 10_000_000) == 13_706
    assert c.sell_tax(10_000_000) == 0


def test_negative_rejected():
    with pytest.raises(ValueError):
        CostModel(0, 0, 0, sec_fee_rate=-1)
