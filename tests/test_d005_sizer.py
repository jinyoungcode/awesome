"""S02 명세 v1.0 §10 검증 예제 (15만 원) + D-036."""

from stockking.strategy.d005_sizer import ONE_SHARE_OVER, ONE_SHARE_UNDER, d005_size


def size(price, stop_pct=0.08, exc=0):
    return d005_size(price, price * (1 - stop_pct), 150_000, 0, 150_000, exc)


def test_case1_integer_order():
    d = size(28_500)
    assert d.qty == 1 and d.exception == ""          # 95%, 위험 2,280 ≤ 3,000


def test_case2_expensive_exception_rejected_then_ok_with_6pct():
    assert size(42_000).qty is None                  # 손실 3,360 > 3,000 → 포기
    d = size(42_000, stop_pct=0.06)
    assert d.qty == 1 and d.exception == ONE_SHARE_OVER   # 2,520 → 통과
    assert size(42_000, stop_pct=0.06, exc=1).qty is None  # '목표 초과'는 동시 1개 유지


def test_case3_between_prices_exception_once():
    d = size(18_450)
    assert d.qty == 1 and d.exception == ONE_SHARE_UNDER  # 61.5%/123% → 예외, 손실 1,476
    # D-036: '목표 미달' 예외는 동시 1개 한도에서 제외 → '목표 초과' 예외를 이미 보유해도 매수 가능
    assert size(18_450, exc=1).qty == 1


def test_price_over_30pct_rejected():
    assert size(46_000, stop_pct=0.01).qty is None   # 46,000 > 45,000
