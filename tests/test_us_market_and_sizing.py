"""D-005(주문 허용 범위 ±10% 유지)·D-006(미국 주식 지원) 테스트."""

from datetime import date
from decimal import Decimal

import pytest

from stockking.backtest import BacktestConfig, BacktestEngine
from stockking.backtest.costs import CostModel
from stockking.broker.kis import KisPaperClient, KisPaperCredentials, TR_PAPER_US_BUY, TR_PAPER_US_SELL
from stockking.config import Secret
from stockking.data import Bar
from stockking.data.bars import load_bars_csv
from stockking.domain import AccountSnapshot, OrderRequest, OrderType, Side
from stockking.market import FxRates, Market, market_of, normalize_symbol
from stockking.risk import KillSwitch, RiskGate, RiskLimits
from stockking.sizing import order_target, shares_for_target, within_band
from stockking.strategy import TargetOrder

START = 100_000  # D-004 모의 시작 자금


# --- 주문 크기 (D-005) ---------------------------------------------------------

def test_order_target_is_start_plus_realized_over_five():
    assert order_target(START, 0) == 20_000
    assert order_target(START, 5_000) == 21_000
    assert order_target(START, -10_000) == 18_000


@pytest.mark.parametrize("value,ok", [(17_999, False), (18_000, True), (20_000, True), (22_000, True), (22_001, False)])
def test_band_is_plus_minus_ten_percent_inclusive(value, ok):
    assert within_band(value, 20_000) is ok


def test_shares_for_target_picks_closest_in_band_or_none():
    assert shares_for_target(7_000, 20_000) == 3      # 21,000원(105%)
    assert shares_for_target(9_500, 20_000) == 2      # 19,000원(95%)
    assert shares_for_target(30_000, 20_000) is None  # 1주가 150% → 주문 불가
    assert shares_for_target(13_000, 20_000) is None  # 1주 65% / 2주 130% → 주문 불가


@pytest.mark.parametrize("amount", [18_000, 20_000, 21_000, 22_000])
def test_risk_gate_accepts_whole_band_on_fresh_100k_account(amount):
    snap = AccountSnapshot(START, {}, {"005930": 1_000}, START)
    gate = RiskGate(kill_switch=KillSwitch(memory_only=True))
    d = gate.check(OrderRequest("005930", Side.BUY, amount // 1_000, OrderType.LIMIT, 1_000), snap)
    assert d, d.reasons


def test_risk_gate_still_blocks_above_band():
    snap = AccountSnapshot(START, {}, {"005930": 1_000}, START)
    d = RiskGate(kill_switch=KillSwitch(memory_only=True)).check(
        OrderRequest("005930", Side.BUY, 23, OrderType.LIMIT, 1_000), snap)
    assert not d and any("POSITION_WEIGHT" in r for r in d.reasons)


# --- 시장 판별·환율 (D-006) ----------------------------------------------------

@pytest.mark.parametrize("sym,mkt", [("005930", Market.KR), ("5930", Market.KR), ("0001A0", Market.KR),
                                     ("AAPL", Market.US), ("brk.b", Market.US), ("BF-B", Market.US)])
def test_market_of(sym, mkt):
    assert market_of(sym) is mkt


def test_market_of_rejects_garbage():
    with pytest.raises(ValueError):
        market_of("??")


def test_normalize_keeps_us_ticker_and_pads_kr_code():
    assert normalize_symbol("aapl") == "AAPL"
    assert normalize_symbol("5930") == "005930"


def test_fx_uses_previous_business_day_and_rounds_half_up():
    fx = FxRates({date(2025, 1, 2): 1450, date(2025, 1, 6): 1460})
    assert fx.rate(date(2025, 1, 3)) == Decimal(1450)          # 1/3 환율 없음 → 1/2 환율
    assert fx.to_krw("10.0005", date(2025, 1, 6)) == 14_601     # 14600.73 → 14601
    with pytest.raises(KeyError):
        fx.rate(date(2025, 1, 1))


def test_load_bars_converts_usd_to_krw(tmp_path):
    csv = tmp_path / "bars.csv"
    csv.write_text("date,symbol,open,high,low,close,volume,currency\n"
                   "2025-01-02,5930,70000,71000,69000,70500,100,KRW\n"
                   "2025-01-02,aapl,10.00,10.50,9.80,10.20,500,USD\n", encoding="utf-8")
    fx = FxRates({date(2024, 12, 31): 1450})  # 기본: bar 날짜보다 앞선 환율만 사용
    kr, us = load_bars_csv(csv, fx)
    assert (kr.symbol, kr.close) == ("005930", 70_500)
    assert (us.symbol, us.open, us.high, us.low, us.close) == ("AAPL", 14_500, 15_225, 14_210, 14_790)


def test_load_bars_usd_without_fx_fails(tmp_path):
    csv = tmp_path / "bars.csv"
    csv.write_text("date,symbol,open,high,low,close,volume,currency\n2025-01-02,AAPL,1,1,1,1,1,USD\n")
    with pytest.raises(ValueError):
        load_bars_csv(csv)


# --- 리스크 관문: 시장별 가격제한폭 --------------------------------------------

def _snap(sym, px):
    return AccountSnapshot(10_000_000, {}, {sym: px}, 10_000_000)


def test_kr_price_band_still_enforced():
    gate = RiskGate(RiskLimits(max_position_weight=1.0), KillSwitch(memory_only=True))
    d = gate.check(OrderRequest("005930", Side.BUY, 1, OrderType.LIMIT, 140_000), _snap("005930", 100_000),
                   reference_price=100_000)
    assert not d and any("PRICE_BAND" in r for r in d.reasons)


def test_us_has_no_price_band_by_default():
    gate = RiskGate(RiskLimits(max_position_weight=1.0), KillSwitch(memory_only=True))
    d = gate.check(OrderRequest("AAPL", Side.BUY, 1, OrderType.LIMIT, 140_000), _snap("AAPL", 100_000),
                   reference_price=100_000)
    assert d, d.reasons


def test_unknown_symbol_is_rejected_fail_closed():
    gate = RiskGate(kill_switch=KillSwitch(memory_only=True))
    d = gate.check(OrderRequest("??", Side.BUY, 1, OrderType.LIMIT, 1_000), _snap("??", 1_000))
    assert not d and "fail-closed" in d.reasons[0]


# --- 백테스트: 한·미 혼합, 시장별 비용 -----------------------------------------

class Script:
    strategy_id = "script"

    def __init__(self, plan):
        self.plan, self.i = plan, -1

    def on_bar_close(self, ctx):
        self.i += 1
        return self.plan.get(self.i, [])


def test_mixed_kr_us_backtest_applies_market_cost_models():
    days = [date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6)]
    bars = []
    for d in days:
        bars.append(Bar(d, "005930", 10_000, 10_000, 10_000, 10_000, 100))
        bars.append(Bar(d, "AAPL", 14_500, 14_500, 14_500, 14_500, 100))  # 원화 환산 가격
    kr_cost = CostModel(0, 0.002, 0, source="test-kr")   # 국내 매도세 0.2% 가정
    us_cost = CostModel(0, 0.0, 0, source="test-us")     # 미국: 국내 증권거래세 없음
    plan = {0: [TargetOrder("005930", Side.BUY, 2), TargetOrder("AAPL", Side.BUY, 1)],
            1: [TargetOrder("005930", Side.SELL, 2), TargetOrder("AAPL", Side.SELL, 1)]}
    cfg = BacktestConfig(START, kr_cost, market_cost_models={Market.US: us_cost})
    res = BacktestEngine(cfg).run(Script(plan), bars)
    assert len(res.fills) == 4 and not res.rejected
    taxes = {f.symbol: f.tax for f in res.fills if f.side is Side.SELL}
    assert taxes == {"005930": 40, "AAPL": 0}
    assert res.summary()["warnings"] == []


# --- KIS 모의 해외주문 --------------------------------------------------------

def _client(calls):
    def transport(method, url, headers, body):
        calls.append((url, headers, body))
        if url.endswith("/oauth2/tokenP"):
            return {"access_token": "T", "expires_in": 86400}
        return {"rt_cd": "0", "msg1": "ok", "output": {"ODNO": "9"}}

    c = KisPaperClient(KisPaperCredentials(Secret("k"), Secret("s"), "12345678", "01"), transport=transport)
    c.min_interval_sec = 0
    return c


@pytest.mark.parametrize("side,tr", [(Side.BUY, TR_PAPER_US_BUY), (Side.SELL, TR_PAPER_US_SELL)])
def test_us_order_goes_to_overseas_endpoint_with_usd_price(side, tr):
    calls = []
    order = OrderRequest("AAPL", side, 2, OrderType.LIMIT, 290_000,
                         native_limit_price=Decimal("199.5"), exchange="nasd")
    assert _client(calls).place_order(order).accepted
    url, headers, body = calls[-1]
    assert url.endswith("/uapi/overseas-stock/v1/trading/order")
    assert headers["tr_id"] == tr and tr.startswith("V")
    assert body["OVRS_EXCG_CD"] == "NASD" and body["PDNO"] == "AAPL"
    assert body["OVRS_ORD_UNPR"] == "199.50" and body["ORD_QTY"] == "2" and body["ORD_DVSN"] == "00"


@pytest.mark.parametrize("kwargs", [dict(native_limit_price=None, exchange="NASD"),
                                    dict(native_limit_price=Decimal("1"), exchange=None),
                                    dict(native_limit_price=Decimal("1"), exchange="KRX")])
def test_us_order_requires_usd_price_and_exchange(kwargs):
    with pytest.raises(ValueError):
        _client([]).place_order(OrderRequest("AAPL", Side.BUY, 1, OrderType.LIMIT, 1_450, **kwargs))


def test_us_market_order_refused():
    with pytest.raises(ValueError):
        _client([]).build_us_order_body(OrderRequest("AAPL", Side.BUY, 1, OrderType.MARKET))


# --- 환율 look-ahead 방지 (Codex 리뷰 요청 회귀 테스트) ------------------------

def _usd_csv(tmp_path):
    csv = tmp_path / "bars.csv"
    csv.write_text("date,symbol,open,high,low,close,volume,currency\n"
                   "2025-01-03,AAPL,10.00,10.00,10.00,10.00,500,USD\n", encoding="utf-8")
    return csv


def test_same_day_fx_fixed_after_fill_is_not_used_by_default(tmp_path):
    # 1/3 환율(1500)은 1/3 체결 이후에 확정된다고 가정 → 기본 로드는 1/2 환율(1400)만 써야 한다
    fx = FxRates({date(2025, 1, 2): 1400, date(2025, 1, 3): 1500})
    (bar,) = load_bars_csv(_usd_csv(tmp_path), fx)
    assert bar.open == 14_000


def test_same_day_fx_only_when_explicitly_allowed(tmp_path):
    fx = FxRates({date(2025, 1, 2): 1400, date(2025, 1, 3): 1500})
    (bar,) = load_bars_csv(_usd_csv(tmp_path), fx, fx_same_day=True)
    assert bar.open == 15_000


def test_no_prior_fx_fails_closed(tmp_path):
    fx = FxRates({date(2025, 1, 3): 1500})  # 같은 날 환율뿐 → 기본 정책에서는 쓸 수 있는 환율이 없음
    with pytest.raises(KeyError):
        load_bars_csv(_usd_csv(tmp_path), fx)


def test_fx_strictly_before():
    fx = FxRates({date(2025, 1, 2): 1400, date(2025, 1, 3): 1500})
    assert fx.rate(date(2025, 1, 3)) == Decimal(1500)
    assert fx.rate(date(2025, 1, 3), strictly_before=True) == Decimal(1400)


# --- D-005 사이징 → 실제 체결 경로 (Codex 리뷰 요청) ----------------------------

class SizedBuyer:
    """sizing.shares_for_target 으로 수량을 정해 주문하는 테스트 전략(엔진·리스크 관문 실경로 검증용)."""
    strategy_id = "sized"

    def __init__(self, symbols, starting_cash):
        self.symbols, self.start, self.done = symbols, starting_cash, False

    def on_bar_close(self, ctx):
        if self.done:
            return []
        self.done = True
        target = order_target(self.start, 0)
        out = []
        for s in self.symbols:
            qty = shares_for_target(ctx.history[s][-1].close, target)
            if qty:
                out.append(TargetOrder(s, Side.BUY, qty))
        return out


def test_sizing_orders_fill_through_engine_and_risk_gate_within_band():
    days = [date(2025, 1, 2), date(2025, 1, 3)]
    prices = {"005930": 7_000, "000660": 9_500, "035420": 30_000}  # 3주=21,000 / 2주=19,000 / 불가
    bars = [Bar(d, s, p, p, p, p, 100) for d in days for s, p in prices.items()]
    res = BacktestEngine(BacktestConfig(START, CostModel(0, 0, 0, source="test"))).run(
        SizedBuyer(list(prices), START), bars)
    assert not res.rejected, res.rejected
    filled = {f.symbol: f.quantity * f.price for f in res.fills}
    assert filled == {"005930": 21_000, "000660": 19_000}  # 30,000원 종목은 정수로 범위 불가 → 주문 안 함
    assert all(within_band(v, 20_000) for v in filled.values())
