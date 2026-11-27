import pytest

from stockking.backtest.costs import CostModel

# 테스트가 대표 PC 의 실제 설정·파일을 읽지 않도록 격리할 환경변수 (1일차 실측: 5건 실패 원인)
_ISOLATE_UNSET = (
    "STOCKKING_MODE", "STOCKKING_ACCOUNT", "STOCKKING_CAPITAL_STAGE", "STOCKKING_OPERATING_CAPITAL", "STOCKKING_KR_SELL_MODE",
    "KIS_PAPER_APP_KEY", "KIS_PAPER_APP_SECRET", "KIS_PAPER_ACCOUNT_NO", "KIS_PAPER_ACCOUNT_PRODUCT",
    "OneDrive", "OneDriveConsumer", "OneDriveCommercial",
)


@pytest.fixture
def costs():
    # 테스트 전용 가정치 — 실제 수수료·세율 아님
    return CostModel(commission_rate=0.00015, sell_tax_rate=0.002, slippage_bps=5, source="TEST")


@pytest.fixture(autouse=True)
def isolate_environment(tmp_path, monkeypatch):
    """모든 테스트를 임시 폴더에서 실행: var/ 상대경로(운용 금액·감사 로그·장부·킬스위치),
    비밀값 파일, 토큰 캐시, %APPDATA% 를 전부 tmp 로 돌린다."""
    for k in _ISOLATE_UNSET:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("STOCKKING_KILL_SWITCH_FILE", str(tmp_path / "KILL_SWITCH"))
    monkeypatch.setenv("STOCKKING_SECRETS_FILE", str(tmp_path / "no_secrets.env"))
    monkeypatch.setenv("STOCKKING_AUTO_CAPITAL_FILE", str(tmp_path / "operating_capital.json"))
    monkeypatch.setenv("STOCKKING_TOKEN_CACHE", str(tmp_path / "kis_paper_token.json"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STOCKKING_KILL_ALL_FILE", str(tmp_path / "KILL_ALL"))
    monkeypatch.chdir(tmp_path)
    from stockking import config
    config._reset_account_profile_for_tests()   # D-049: 계좌 프로필은 프로세스당 1회 고정 → 테스트마다 초기화
    yield
    config._reset_account_profile_for_tests()
