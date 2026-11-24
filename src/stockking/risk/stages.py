"""자금 단계별 리스크 한도 (D-014, D-016, D-025, D-028).

`RiskLimits()` 기본값은 초기 임시값이다. 운영 코드는 반드시 `limits_for(stage)`를 쓴다.
(PR #2 병합 후 RiskLimits 기본값도 T0 값으로 맞출 예정 — 지금 바꾸면 PR #2와 충돌)
"""

from __future__ import annotations

import os
from dataclasses import replace
from enum import Enum

from .gate import RiskLimits


class CapitalStage(str, Enum):
    PAPER = "paper"   # 3거래일 모의 (15만 원 기준으로 운용 → T0 한도 적용, D-032)
    T0 = "t0"         # 실전 15만 원 테스트 (D-031)
    T1 = "t1"         # 첫 증액 이후 (T2~ 도 이 값, 단계별 변경 시 여기 추가)


# D-031/D-032: 모의·T0 시작 자금 15만 원 전체를 운용 기준으로 사용(예비금 없음)
STARTING_CAPITAL = 150_000
SLOTS = 5   # 동시 보유 한도 → 주문 목표 = (시작 자금 + 실현 순손익) ÷ 5 = 30,000원


def operating_capital() -> int:
    """운용 기준 자금. 기본 15만 원, STOCKKING_OPERATING_CAPITAL 로 변경(증액은 대표 승인 후)."""
    value = int(os.environ.get("STOCKKING_OPERATING_CAPITAL", STARTING_CAPITAL))
    if value <= 0:
        raise ValueError("STOCKKING_OPERATING_CAPITAL 은 양수여야 합니다")
    return value


def capital_warning(broker_equity: int, capital: int | None = None) -> str | None:
    """증권사 계좌 평가금이 운용 기준 자금과 크게 다르면 경고.

    KIS 모의 계좌는 기본 예수금이 운용 기준(15만 원)보다 훨씬 클 수 있다. 그러면 비중·손실 한도가
    계좌 전체 기준으로 계산돼 실전(15만 원)과 달라진다 → 모의 계좌 투자금을 15만 원 근처로 맞추거나,
    S02 실행기에서 운용 기준 자금으로 장부를 따로 관리해야 한다.
    """
    capital = capital or operating_capital()
    if broker_equity > capital * 1.2 or broker_equity < capital * 0.8:
        return (f"증권사 평가금 {broker_equity:,}원 ≠ 운용 기준 {capital:,}원 — 한도가 실전과 다르게 계산됩니다 "
                "(PAPER_RUNBOOK §0 참고)")
    return None


# D-028 확정값
DAILY_LOSS = {CapitalStage.PAPER: 0.05, CapitalStage.T0: 0.05, CapitalStage.T1: 0.03}
WEEKLY_LOSS = 0.08
FINAL_STOP_FROM_PRINCIPAL = 0.20       # 원금 −20% → 전량 청산·시스템 정지 (D-016)
PEAK_DRAWDOWN_DERISK = 0.15            # 최고점 −15% → 매매 규모 50% 축소 (D-016)
DERISK_MULTIPLIER = 0.5
MAX_TRADE_LOSS_PCT = 0.02              # 매매 1건 최대 손실 (D-028) — 손절가 필드 추가 후 관문 검사(PR #2 병합 후)

# D-025 1주 예외 한도 — D-021 구현 PR(PR #2 병합 후)에서 사용. 15만 원 기준 1주 가격 상한 45,000원(D-032)
ONE_SHARE_EXCEPTION_MAX_PRICE_PCT = 0.30
ONE_SHARE_EXCEPTION_MAX_CONCURRENT = 1
ONE_SHARE_EXCEPTION_MAX_LOSS_PCT = 0.02


def resolve_stage(raw: str | None = None) -> CapitalStage:
    value = (raw if raw is not None else os.environ.get("STOCKKING_CAPITAL_STAGE", "paper")).strip().lower()
    try:
        return CapitalStage(value)
    except ValueError as exc:
        raise ValueError(f"알 수 없는 STOCKKING_CAPITAL_STAGE: {value!r} (paper|t0|t1)") from exc


def limits_for(stage: CapitalStage, base: RiskLimits | None = None) -> RiskLimits:
    return replace(
        base or RiskLimits(),
        max_daily_loss_pct=DAILY_LOSS[stage],
        max_weekly_loss_pct=WEEKLY_LOSS,
        max_monthly_loss_pct=None,       # D-028에 월간 한도 없음 → 검사 안 함
        final_stop_pct=FINAL_STOP_FROM_PRINCIPAL,
    )


def size_multiplier(equity: int, peak_equity: int | None) -> float:
    """최고점 대비 −15% 이하이면 0.5, 아니면 1.0 (D-016). 사이징 단계에서 주문 수량에 곱한다."""
    if not peak_equity or peak_equity <= 0:
        return 1.0
    return DERISK_MULTIPLIER if (peak_equity - equity) / peak_equity >= PEAK_DRAWDOWN_DERISK else 1.0
