"""관문의 1건 손실 2%(D-028)와 D-005 주문 크기·1주 예외(D-021/D-025/D-036) 검사. 15만 원 기준."""

from stockking.domain import AccountSnapshot, OrderRequest, OrderType, Position, Side
from stockking.risk import RiskGate, RiskLimits
from stockking.risk.stages import CapitalStage, limits_for

EQ = 150_000
TARGET = 30_000


def snap(price, sym="000100", over=frozenset(), positions=None):
    return AccountSnapshot(EQ, positions or {}, {sym: price}, EQ, over_exception_symbols=over)


def buy(price, qty=1, stop_pct=0.08, exc="", target=TARGET, sym="000100"):
    return OrderRequest(sym, Side.BUY, qty, OrderType.LIMIT, price, "S02",
                        stop_price=None if stop_pct is None else price * (1 - stop_pct),
                        order_target=target, sizing_exception=exc)


def gate():
    return RiskGate(limits_for(CapitalStage.T0))


def reasons(order, s):
    return gate().check(order, s).reasons


def test_defaults_are_t0_values():
    L = RiskLimits()
    assert (L.max_daily_loss_pct, L.max_weekly_loss_pct, L.max_monthly_loss_pct) == (0.05, 0.08, None)
    assert L.max_trade_loss_pct == 0.02 and L.final_stop_pct == 0.20


def test_integer_order_in_band_ok():
    assert gate().check(buy(28_500), snap(28_500))                  # 95%, 손실 2,280


def test_out_of_band_without_exception_rejected():
    assert any("D005_BAND" in r for r in reasons(buy(18_450), snap(18_450)))


def test_trade_loss_cap():
    r = reasons(buy(28_500, stop_pct=0.12), snap(28_500))           # 3,420 > 3,000
    assert any("TRADE_LOSS" in x for x in r)


def test_bad_or_missing_stop():
    assert any("BAD_STOP" in x for x in reasons(buy(28_500, stop_pct=-0.01), snap(28_500)))
    assert any("NO_STOP" in x for x in reasons(buy(18_450, stop_pct=None, exc="ONE_SHARE_UNDER"), snap(18_450)))
    strict = RiskGate(RiskLimits(require_stop_for_buy=True))
    assert not strict.check(buy(28_500, stop_pct=None), snap(28_500))


def test_under_exception_ok_even_with_over_held():
    o = buy(18_450, exc="ONE_SHARE_UNDER")
    assert gate().check(o, snap(18_450, over=frozenset({"000999"})))  # D-036: 동시 1개 한도 제외


def test_over_exception_limits():
    o = buy(42_000, stop_pct=0.06, exc="ONE_SHARE_OVER")
    assert gate().check(o, snap(42_000))
    r = reasons(buy(42_000, stop_pct=0.06, exc="ONE_SHARE_OVER"), snap(42_000, over=frozenset({"000999"})))
    assert any("ONE_SHARE_CONCURRENT" in x for x in r)
    r2 = reasons(buy(46_000, stop_pct=0.01, exc="ONE_SHARE_OVER"), snap(46_000))
    assert any("ONE_SHARE_PRICE" in x for x in r2)


def test_exception_misuse_rejected():
    assert any("ONE_SHARE_NOT_NEEDED" in x for x in reasons(buy(28_500, exc="ONE_SHARE_OVER"), snap(28_500)))
    assert any("EXCEPTION_KIND" in x for x in reasons(buy(18_450, exc="ONE_SHARE_OVER"), snap(18_450)))
    assert any("ONE_SHARE_QTY" in x for x in reasons(buy(18_450, qty=2, exc="ONE_SHARE_UNDER", stop_pct=0.01),
                                                       snap(18_450)))
    no_target = OrderRequest("000100", Side.BUY, 1, OrderType.LIMIT, 18_450, "S02", stop_price=17_000,
                             sizing_exception="ONE_SHARE_UNDER")
    assert any("EXCEPTION_WITHOUT_TARGET" in x for x in reasons(no_target, snap(18_450)))


def test_sell_unaffected():
    s = snap(18_450, positions={"000100": Position("000100", 1, 18_000)})
    assert gate().check(OrderRequest("000100", Side.SELL, 1, OrderType.LIMIT, 18_450, "S02"), s)


def test_weight_cap_30pct_only_for_new_one_share_exception():
    held = {"000100": Position("000100", 1, 42_000)}
    r = reasons(buy(42_000, stop_pct=0.06, exc="ONE_SHARE_OVER"), snap(42_000, positions=held))
    assert any("POSITION_WEIGHT" in x and "22%" in x for x in r)   # 추가 매수는 예외 비중 적용 안 함
