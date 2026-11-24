"""주문 직전 필수 관문(Pre-Trade Risk Gate).

설계 원칙
1. 모든 주문은 OrderRouter -> RiskGate.check() 를 통과해야만 브로커로 간다.
   백테스트 엔진도 같은 RiskGate 를 쓴다(백테스트와 실전의 규칙이 달라지는 사고 방지).
2. Fail-closed: 체크 중 예외·데이터 누락(현재가 없음 등)은 '거부'로 처리한다.
3. 킬스위치는 한번 켜지면 사람이 수동으로 끌 때까지 유지된다(자동 해제 없음).
4. 한도 수치는 리스크관리실(RISK_POLICY.md)이 정한다. 아래 기본값은
   정책 확정 전까지 쓰는 '보수적 임시값'이며 실전 근거가 아니다.
"""

from __future__ import annotations

import os
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from ..domain import AccountSnapshot, OrderRequest, OrderType, Side


@dataclass(frozen=True)
class RiskLimits:
    """[임시값] 리스크관리실 정책 확정 시 교체. 비율은 0~1."""

    max_daily_loss_pct: float = 0.02        # 당일 시작 평가금 대비 -2% 도달 시 신규 매수 중단 + 킬스위치
    max_weekly_loss_pct: float = 0.04
    max_monthly_loss_pct: float | None = 0.08
    max_position_weight: float = 0.20       # 1종목 최대 비중(주문 후 기준)
    max_open_positions: int = 5             # 최대 동시 보유 종목 수
    max_order_value: int = 2_000_000        # 1회 주문 최대 금액(원)
    max_orders_per_minute: int = 10         # 폭주 방지(무한루프 주문 차단)
    price_band_pct: float = 0.30            # 기준가 대비 ±30%(가격제한폭) 밖 지정가 거부
    allow_market_orders: bool = False       # 시장가 기본 금지(모의투자 일부 미지원·슬리피지)
    kill_on_daily_loss: bool = True
    # D-016/D-028: 원금 대비 이 비율 손실 시 최종 손절(킬스위치). None = 검사 안 함
    final_stop_pct: float | None = 0.20


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reasons: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.approved


class KillSwitch:
    """프로세스 내 플래그 + 파일 플래그(다른 프로세스/사람이 즉시 발동 가능).

    발동: `touch var/KILL_SWITCH` 또는 KillSwitch.trip(reason)
    해제: 파일 삭제 + reset() — 사람이 원인 확인 후 수동으로만.
    """

    def __init__(self, flag_file: str | Path | None = None, memory_only: bool = False) -> None:
        # memory_only=True: 백테스트용. 실운영 킬스위치 파일을 읽지도 쓰지도 않는다(상호 간섭 방지).
        self._memory_only = memory_only
        self._flag_file = Path(flag_file or os.environ.get("STOCKKING_KILL_SWITCH_FILE", "var/KILL_SWITCH"))
        self._tripped_reason: str | None = None
        self._lock = threading.Lock()

    @property
    def flag_file(self) -> Path:
        return self._flag_file

    def trip(self, reason: str) -> None:
        with self._lock:
            if self._tripped_reason is None:
                self._tripped_reason = reason
            if self._memory_only:
                return
            try:
                self._flag_file.parent.mkdir(parents=True, exist_ok=True)
                self._flag_file.write_text(f"{datetime.now().isoformat()} {reason}\n", encoding="utf-8")
            except OSError:
                pass  # 파일 기록 실패해도 메모리 플래그는 이미 켜짐

    def is_tripped(self) -> bool:
        if self._memory_only:
            return self._tripped_reason is not None
        return self._tripped_reason is not None or self._flag_file.exists()

    def reason(self) -> str | None:
        if self._tripped_reason:
            return self._tripped_reason
        if not self._memory_only and self._flag_file.exists():
            return f"kill-switch file present: {self._flag_file}"
        return None

    def reset(self, operator: str) -> None:
        if not operator:
            raise ValueError("해제자는 반드시 기록해야 합니다")
        with self._lock:
            self._tripped_reason = None
            if not self._memory_only and self._flag_file.exists():
                self._flag_file.unlink()


@dataclass
class RiskGate:
    limits: RiskLimits = field(default_factory=RiskLimits)
    kill_switch: KillSwitch = field(default_factory=KillSwitch)
    _recent_orders: deque = field(default_factory=deque, init=False, repr=False)
    _seen_order_ids: set = field(default_factory=set, init=False, repr=False)

    def check(
        self,
        order: OrderRequest,
        account: AccountSnapshot,
        reference_price: int | None = None,
        now: datetime | None = None,
    ) -> RiskDecision:
        """주문 허용 여부 판단. 예외 발생 시 거부(fail-closed)."""
        try:
            reasons = self._evaluate(order, account, reference_price, now or datetime.now())
        except Exception as exc:  # noqa: BLE001 - 의도적 fail-closed
            return RiskDecision(False, (f"risk check error (fail-closed): {exc!r}",))
        if reasons:
            return RiskDecision(False, tuple(reasons))
        self._register(order, now or datetime.now())
        return RiskDecision(True)

    # ------------------------------------------------------------------
    def _evaluate(self, order, account, reference_price, now) -> list[str]:
        L = self.limits
        reasons: list[str] = []

        # 0. 킬스위치 — 발동 중 신규 매수 차단. 보유분을 줄이는 손절·청산 매도는 허용(D-026)
        #    매도도 아래 공매도 금지(보유 수량 이내)·가격·중복 검사는 그대로 받는다.
        if self.kill_switch.is_tripped() and order.side is Side.BUY:
            return [f"KILL_SWITCH: {self.kill_switch.reason()}"]

        # 1. 멱등성 — 같은 client_order_id 재전송 차단
        if order.client_order_id in self._seen_order_ids:
            reasons.append(f"DUPLICATE_ORDER: {order.client_order_id}")

        # 2. 주문 속도 제한
        window_start = now - timedelta(minutes=1)
        while self._recent_orders and self._recent_orders[0] < window_start:
            self._recent_orders.popleft()
        if len(self._recent_orders) >= L.max_orders_per_minute:
            reasons.append(f"ORDER_RATE: 분당 {L.max_orders_per_minute}건 초과")

        # 3. 주문 유형
        if order.order_type is OrderType.MARKET and not L.allow_market_orders:
            reasons.append("MARKET_ORDER_NOT_ALLOWED")

        # 4. 가격 — 기준가 없으면 거부(fail-closed)
        price = order.limit_price if order.order_type is OrderType.LIMIT else account.prices.get(order.symbol)
        ref = reference_price or account.prices.get(order.symbol)
        if price is None or ref is None or ref <= 0:
            reasons.append(f"NO_PRICE: {order.symbol} 기준가/주문가 없음")
            return reasons
        if abs(price - ref) / ref > L.price_band_pct:
            reasons.append(f"PRICE_BAND: 주문가 {price} 가 기준가 {ref} 의 ±{L.price_band_pct:.0%} 밖")

        order_value = price * order.quantity
        equity = account.equity
        if equity <= 0:
            reasons.append("NO_EQUITY")
            return reasons

        # 5. 손실 한도 (일/주/월). 도달 시 신규 '매수' 차단, 일 한도는 킬스위치까지
        loss_checks = [
            ("DAILY", account.start_of_day_equity, L.max_daily_loss_pct),
            ("WEEKLY", account.start_of_week_equity, L.max_weekly_loss_pct),
            ("MONTHLY", account.start_of_month_equity, L.max_monthly_loss_pct),
        ]
        # 5-0. 최종 손절(D-016/D-028): 원금 대비 -20% → 킬스위치(시스템 정지). 청산 매도는 허용(D-026)
        if account.principal and L.final_stop_pct is not None:
            loss = (account.principal - equity) / account.principal
            if loss >= L.final_stop_pct:
                self.kill_switch.trip(f"FINAL_STOP: 원금 대비 {loss:.2%} >= {L.final_stop_pct:.0%}")
                if order.side is Side.BUY:
                    return [f"FINAL_STOP: 원금 대비 {loss:.2%} 손실 → 전량 청산·시스템 정지"]
        for name, base, limit in loss_checks:
            if not base or limit is None:
                continue
            drawdown = (base - equity) / base
            if drawdown >= limit:
                if name == "DAILY" and L.kill_on_daily_loss:
                    self.kill_switch.trip(f"daily loss {drawdown:.2%} >= {limit:.2%}")
                    if order.side is Side.BUY:
                        return [f"{name}_LOSS_LIMIT: {drawdown:.2%} >= {limit:.2%} → KILL_SWITCH 발동"]
                    continue
                if order.side is Side.BUY:
                    reasons.append(f"{name}_LOSS_LIMIT: {drawdown:.2%} >= {limit:.2%}")

        if order.side is Side.BUY:
            # 6. 1회 주문 금액
            if order_value > L.max_order_value:
                reasons.append(f"ORDER_VALUE: {order_value:,}원 > {L.max_order_value:,}원")
            # 7. 현금 (미수 금지)
            if order_value > account.cash:
                reasons.append(f"INSUFFICIENT_CASH: {order_value:,}원 > 예수금 {account.cash:,}원")
            # 8. 종목 비중 (주문 후 기준)
            held = account.positions.get(order.symbol)
            held_value = held.quantity * ref if held else 0
            weight_after = (held_value + order_value) / equity
            if weight_after > L.max_position_weight:
                reasons.append(f"POSITION_WEIGHT: 주문 후 {weight_after:.1%} > {L.max_position_weight:.0%}")
            # 9. 동시 보유 종목 수
            open_symbols = {s for s, p in account.positions.items() if p.quantity > 0}
            if order.symbol not in open_symbols and len(open_symbols) >= L.max_open_positions:
                reasons.append(f"MAX_POSITIONS: 이미 {len(open_symbols)}종목 보유")
        else:
            # 10. 공매도 금지 — 보유 수량 초과 매도 거부
            held = account.positions.get(order.symbol)
            held_qty = held.quantity if held else 0
            if order.quantity > held_qty:
                reasons.append(f"NO_SHORT_SELLING: 매도 {order.quantity}주 > 보유 {held_qty}주")

        return reasons

    def _register(self, order: OrderRequest, now: datetime) -> None:
        self._recent_orders.append(now)
        self._seen_order_ids.add(order.client_order_id)

    def preload_order_ids(self, order_ids) -> None:
        """재시작 복구: 감사 로그에 남은 주문번호를 다시 등록해 같은 번호의 재전송을 막는다."""
        self._seen_order_ids.update(order_ids)
