"""자금 단계별 리스크 한도 (D-014, D-016, D-025, D-028).

`RiskLimits()` 기본값 = T0 값(D-028). 단계가 바뀌면 `limits_for(stage)`가 일중 한도 등을 바꾼다.
"""

from __future__ import annotations

import os
from pathlib import Path
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


def _auto_capital_file() -> str:
    from ..paths import var_root
    return os.environ.get("STOCKKING_AUTO_CAPITAL_FILE") or str(var_root() / "operating_capital.json")


def _capital_setting() -> str:
    """STOCKKING_OPERATING_CAPITAL. 없으면: 병행 모의(STOCKKING_ACCOUNT=paper)는 auto(D-049), 그 외 15만 원."""
    from ..config import account_profile
    raw = os.environ.get("STOCKKING_OPERATING_CAPITAL", "").strip()
    return raw or ("auto" if account_profile() == "paper" else "")


def _auto_requested() -> bool:
    return _capital_setting().lower() == "auto"


def _pending_file(capital_file: Path) -> Path:
    return capital_file.parent / "archive" / "PENDING_REFIX.json"


def archive_capital_file(reason: str, capital_file: str | Path | None = None, today: str | None = None) -> dict | None:
    """운용 금액 고정 파일을 archive\\operating_capital_<고정일>.json 으로 옮기고(삭제 안 함), 다음 고정 때
    감사 로그에 남길 이전 값·사유를 PENDING_REFIX.json 에 둔다. 파일이 없으면 None.
    사용: migrate_var --apply(사유 'D-049 restart'), connect_check --refix-capital(대표 승인)."""
    import json
    from datetime import date
    from pathlib import Path
    f = Path(capital_file or _auto_capital_file())
    if not f.exists():
        return None
    rec = json.loads(f.read_text(encoding="utf-8"))
    arch = f.parent / "archive"
    arch.mkdir(parents=True, exist_ok=True)
    stem = f"operating_capital_{str(rec.get('fixed_on') or today or date.today().isoformat()).replace('-', '')}"
    dest, n = arch / f"{stem}.json", 1
    while dest.exists():
        n += 1
        dest = arch / f"{stem}_{n}.json"
    rec |= {"archived_on": today or date.today().isoformat(), "archive_reason": reason}
    dest.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    f.unlink()
    _pending_file(f).write_text(json.dumps({"previous_capital": rec.get("capital"), "previous_fixed_on": rec.get("fixed_on"),
                                            "reason": reason, "archived_to": dest.name}, ensure_ascii=False, indent=2),
                                encoding="utf-8")
    return rec


def fix_auto_capital_detail(broker_orderable_cash: int, path: str | None = None,
                            today: str | None = None) -> tuple[int, dict | None]:
    """fix_auto_capital 과 같고, 이번에 새로 고정했으면 (값, {이전 값·사유}) — 이미 고정돼 있었으면 (값, None).
    새로 고정할 때 이전 값이 보관돼 있지 않으면 사유는 '최초 고정'."""
    import json
    from datetime import date
    from pathlib import Path
    if resolve_stage() is not CapitalStage.PAPER:
        raise ValueError("운용 금액 auto 는 모의투자 전용 — 실전 T0는 15만 원 고정(D-031·D-032)")
    f = Path(path or _auto_capital_file())
    if f.exists():
        return int(json.loads(f.read_text(encoding="utf-8"))["capital"]), None
    if broker_orderable_cash <= 0:
        raise ValueError("모의 주문 가능 금액이 0 이하라 운용 금액을 정할 수 없습니다")
    pf = _pending_file(f)
    prev = json.loads(pf.read_text(encoding="utf-8")) if pf.exists() else {"previous_capital": None, "reason": "최초 고정"}
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"capital": int(broker_orderable_cash), "fixed_on": today or date.today().isoformat(),
                             "source": "KIS 모의 D+2 예수금(주문 가능 금액 근사)", "decision": "D-046",
                             "previous_capital": prev.get("previous_capital"), "fix_reason": prev.get("reason")},
                            ensure_ascii=False, indent=2), encoding="utf-8")
    pf.unlink(missing_ok=True)
    return int(broker_orderable_cash), prev


def fix_auto_capital(broker_orderable_cash: int, path: str | None = None, today: str | None = None) -> int:
    """D-046: 'auto' 이면 모의 1일차 시작 시점 주문 가능 금액 전액을 운용 금액으로 한 번 고정 저장.
    이미 고정돼 있으면 그 값을 그대로 쓴다(5거래일 내내 같은 값). D-049: 다시 고정은 archive_capital_file 뒤에만."""
    return fix_auto_capital_detail(broker_orderable_cash, path, today)[0]


def operating_capital() -> int:
    """운용 기준 자금. 기본 15만 원(D-032, T0 실전 고정).
    모의투자는 STOCKKING_OPERATING_CAPITAL 로 모의 계좌 예수금 이하 임의 금액(D-044), 또는 'auto'
    (1일차 시작 시점 주문 가능 금액 전액을 고정, D-046)를 쓴다. 비중·손실 한도·1주 예외는 이 금액 기준 비율."""
    raw = _capital_setting().replace(",", "").replace("_", "").strip()
    if raw.lower() == "auto":
        import json
        from pathlib import Path
        if resolve_stage() is not CapitalStage.PAPER:
            raise ValueError("운용 금액 auto 는 모의투자 전용 — 실전 T0는 15만 원 고정(D-031·D-032)")
        f = Path(_auto_capital_file())
        if not f.exists():
            raise RuntimeError("운용 금액 auto 가 아직 고정되지 않았습니다 — 먼저 connect_check 를 실행하세요(D-046)")
        return int(json.loads(f.read_text(encoding="utf-8"))["capital"])
    value = int(raw) if raw else STARTING_CAPITAL
    if value <= 0:
        raise ValueError("STOCKKING_OPERATING_CAPITAL 은 양수여야 합니다")
    if resolve_stage() is not CapitalStage.PAPER and value != STARTING_CAPITAL:
        raise ValueError("실전 T0 운용 금액은 15만 원 고정(D-031·D-032) — 증액은 대표 승인 후 코드로")
    return value


def capital_error(broker_cash: int, capital: int | None = None) -> str | None:
    """D-044: 운용 금액이 모의 계좌 예수금보다 크면 오류. 작거나 같으면 정상(경고 없음)."""
    capital = capital or operating_capital()
    if capital > broker_cash:
        return (f"운용 금액 {capital:,}원 > 모의 계좌 예수금 {broker_cash:,}원 — "
                "STOCKKING_OPERATING_CAPITAL 을 예수금 이하로 낮추세요(D-044)")
    return None


def capital_warning(broker_equity: int, capital: int | None = None) -> str | None:  # 하위 호환
    return capital_error(broker_equity, capital)


# D-028 확정값
DAILY_LOSS = {CapitalStage.PAPER: 0.05, CapitalStage.T0: 0.05, CapitalStage.T1: 0.03}
WEEKLY_LOSS = 0.08
FINAL_STOP_FROM_PRINCIPAL = 0.20       # 원금 −20% → 전량 청산·시스템 정지 (D-016)
PEAK_DRAWDOWN_DERISK = 0.15            # 최고점 −15% → 매매 규모 50% 축소 (D-016)
DERISK_MULTIPLIER = 0.5
MAX_TRADE_LOSS_PCT = 0.02              # 매매 1건 최대 손실 (D-028) — 관문 TRADE_LOSS 검사

# D-025 1주 예외 한도 — 관문 _check_d005 와 d005_sizer 에서 사용. 15만 원 기준 1주 가격 상한 45,000원(D-032)
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
        max_trade_loss_pct=MAX_TRADE_LOSS_PCT,
        one_share_max_price_pct=ONE_SHARE_EXCEPTION_MAX_PRICE_PCT,
        one_share_max_concurrent_over=ONE_SHARE_EXCEPTION_MAX_CONCURRENT,
    )


def size_multiplier(equity: int, peak_equity: int | None) -> float:
    """최고점 대비 −15% 이하이면 0.5, 아니면 1.0 (D-016). 사이징 단계에서 주문 수량에 곱한다."""
    if not peak_equity or peak_equity <= 0:
        return 1.0
    return DERISK_MULTIPLIER if (peak_equity - equity) / peak_equity >= PEAK_DRAWDOWN_DERISK else 1.0
