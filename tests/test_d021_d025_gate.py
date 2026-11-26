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
    assert any("POSITION_WEIGHT" in x for x in r)   # 추가 매수는 예외 비중(30%) 적용 안 함 — 22%(+0.1%p)


def test_exception_position_over_30pct_blocks_add_but_no_forced_sell():
    # 예외로 산 종목이 올라 비중 35%: 추가 매수 거부, 매도는 허용(강제 매도 없음 — 관문은 매도를 만들지 않음)
    held = {"000100": Position("000100", 1, 42_000)}
    s = AccountSnapshot(97_500, held, {"000100": 52_500}, 150_000)
    assert not gate().check(buy(52_500, stop_pct=0.05, exc="ONE_SHARE_OVER"), s)
    assert gate().check(OrderRequest("000100", Side.SELL, 1, OrderType.LIMIT, 52_500, "S02"), s)


def test_weight_tolerance_221_and_301():
    """리스크관리실: 허용오차 0.1%p. 일반 22.1%까지 통과·22.2% 거부, 1주 예외 신규 30.1%까지."""
    eq = 1_000_000
    def chk(price, qty, exc="", target=None, stop=None):
        o = OrderRequest("000100", Side.BUY, qty, OrderType.LIMIT, price, "S02", stop_price=stop,
                         order_target=target, sizing_exception=exc)
        return RiskGate(limits_for(CapitalStage.T0)).check(o, AccountSnapshot(eq, {}, {"000100": price}, eq))
    assert chk(221_000, 1)                                            # 22.1% 통과
    assert any("POSITION_WEIGHT" in r for r in chk(222_000, 1).reasons)   # 22.2% 거부
    # 1주 예외(목표 20만 원의 110% 초과): 30.1% 통과, 30.2% 거부 (가격 상한 30%는 별도 검사라 비중만 확인)
    ok = chk(301_000, 1, "ONE_SHARE_OVER", 200_000, 301_000 * 0.95).reasons
    assert not any("POSITION_WEIGHT" in r for r in ok)
    bad = chk(302_000, 1, "ONE_SHARE_OVER", 200_000, 302_000 * 0.95).reasons
    assert any("POSITION_WEIGHT" in r for r in bad)


def test_33000_order_passes_after_fees_at_150k():
    """15만 원: 33,000원(22%) 주문 4건이 앞선 수수료 반영 후에도 통과 (수수료는 계산에서 빼지 않음)."""
    held = {f"00020{i}": Position(f"00020{i}", 1, 33_000) for i in range(3)}
    prices = {**{k: 33_000 for k in held}, "000100": 33_000}
    cash = 150_000 - 3 * 33_005
    s = AccountSnapshot(cash, held, prices, 150_000)
    assert s.equity == 149_985
    assert gate().check(buy(33_000, stop_pct=0.08), s)              # 33,000 / 149,985 = 22.002%
