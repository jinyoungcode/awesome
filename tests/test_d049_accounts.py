"""D-049 실전·모의 병행: 계좌 프로필 분리 (1단계, 실계좌 코드 잠금 유지)."""
import ast
import json
import os
from pathlib import Path

import pytest

from stockking import config
from stockking.audit import Journal
from stockking.broker import PaperBroker
from stockking.config import AccountProfileMismatch, TradingMode
from stockking.domain import AccountSnapshot, OrderRequest, OrderStatus, OrderType, Position, Side
from stockking.execution import OrderRouter
from stockking.execution import router as router_mod
from stockking.risk import KillSwitch, RiskGate

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def profile(monkeypatch):
    def _set(name):
        config._reset_account_profile_for_tests()
        if name:
            monkeypatch.setenv("STOCKKING_ACCOUNT", name)
        else:
            monkeypatch.delenv("STOCKKING_ACCOUNT", raising=False)
        # 경로 기본값을 보려면 conftest 의 파일 경로 고정을 푼다
        for k in ("STOCKKING_KILL_SWITCH_FILE", "STOCKKING_SECRETS_FILE", "STOCKKING_AUTO_CAPITAL_FILE",
                  "STOCKKING_TOKEN_CACHE"):
            monkeypatch.delenv(k, raising=False)
    return _set


def _acct():
    return AccountSnapshot(1_000_000, {"005930": Position("005930", 2, 70_000)}, {"005930": 70_000}, 1_140_000)


def _order(side=Side.BUY, qty=1, cid=None, sid="t"):
    kw = {"client_order_id": cid} if cid else {}
    return OrderRequest("005930", side, qty, OrderType.LIMIT, 70_000, sid, **kw)


# ---------- 호환 모드 (STOCKKING_ACCOUNT 없음) = D-049 이전과 같음 ----------
def test_compat_mode_keeps_legacy_paths(profile):
    from stockking.broker.kis import default_token_cache
    from stockking.ops.common import acquire_account_lock
    from stockking.paths import var_root
    from stockking.risk.stages import _auto_requested
    profile(None)
    assert var_root() == Path("var")
    assert KillSwitch().flag_file == Path("var/KILL_SWITCH")
    assert config.default_secrets_file().parent.name == "stockking"
    assert default_token_cache().name == "kis_paper_token.json"
    assert _order().client_order_id.startswith("SK-")
    assert acquire_account_lock() is None and not _auto_requested()


def test_profile_fixed_per_process(profile, monkeypatch):
    profile("paper")
    assert config.account_profile() == "paper"
    monkeypatch.setenv("STOCKKING_ACCOUNT", "live")
    with pytest.raises(AccountProfileMismatch):
        config.account_profile()
    monkeypatch.setenv("STOCKKING_ACCOUNT", "xx")
    config._reset_account_profile_for_tests()
    with pytest.raises(ValueError):
        config.account_profile()


def test_paper_profile_paths_prefix_and_auto_capital(profile):
    from stockking.broker.kis import default_token_cache
    from stockking.paths import var_root
    from stockking.risk.stages import _auto_capital_file, _auto_requested
    profile("paper")
    assert var_root() == Path("var/paper")
    assert KillSwitch().flag_file == Path("var/paper/KILL_SWITCH")
    assert _auto_capital_file() == str(Path("var/paper/operating_capital.json"))
    assert default_token_cache().parent.name == "paper" and default_token_cache().name == "kis_token.json"
    assert _order().client_order_id.startswith("PP-")
    assert _auto_requested()          # 병행 모의 운용 금액 기본 auto (D-049)


def test_live_profile_prefix_and_no_paper_client(profile):
    from stockking.broker.kis import KisPaperClient, KisPaperCredentials
    from stockking.paths import var_root
    profile("live")
    assert var_root() == Path("var/live") and _order().client_order_id.startswith("LV-")
    with pytest.raises(AccountProfileMismatch):
        KisPaperClient(KisPaperCredentials(config.Secret("k"), config.Secret("s"), "12345678", "01"))


# ---------- 키 파일 ----------
def _write_secret(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def test_paper_secrets_fallback_then_profile_file(profile):
    profile("paper")
    base = config._stockking_dir()
    legacy = _write_secret(base / "secrets.env", "KIS_PAPER_APP_KEY=a\n")
    assert config.default_secrets_file() == legacy               # 이동 전: 기존 파일 사용
    new = _write_secret(base / "paper" / "secrets.env", "KIS_PAPER_APP_KEY=b\n")
    assert config.default_secrets_file() == new
    assert config.load_secret_file()["KIS_PAPER_APP_KEY"] == "b"


def test_live_has_no_fallback_and_mixed_keys_rejected(profile):
    profile("live")
    _write_secret(config._stockking_dir() / "secrets.env", "KIS_PAPER_APP_KEY=a\n")
    assert config.default_secrets_file().parent.name == "live"   # 실전은 기존 모의 파일을 절대 안 씀
    _write_secret(config.default_secrets_file(), "KIS_LIVE_APP_KEY=x\nKIS_PAPER_APP_KEY=a\n")
    with pytest.raises(config.InsecureSecretFile, match="KIS_PAPER_APP_KEY"):
        config.load_secret_file()


def test_paper_file_with_live_key_rejected(profile):
    profile("paper")
    _write_secret(config._stockking_dir() / "paper" / "secrets.env", "KIS_PAPER_APP_KEY=a\nKIS_LIVE_APP_KEY=b\n")
    with pytest.raises(config.InsecureSecretFile, match="KIS_LIVE_APP_KEY"):
        config.load_secret_file()


# ---------- 한 프로세스 한 계좌 ----------
class FakeLiveBroker:
    is_paper = False

    def __init__(self):
        self.sent = []

    def place_order(self, o):
        self.sent.append(o)


def test_router_refuses_other_account_kind(profile, costs):
    profile("live")
    with pytest.raises(AccountProfileMismatch):       # live 프로세스 + 모의 브로커
        OrderRouter(PaperBroker(1, {}, costs), RiskGate(), Journal(), TradingMode.PAPER)
    profile("paper")
    with pytest.raises(PermissionError):              # paper 프로세스 + 실전 브로커
        OrderRouter(FakeLiveBroker(), RiskGate(), Journal(), TradingMode.PAPER)


def test_router_rejects_foreign_order_id_before_broker(profile, costs):
    profile("paper")
    broker = PaperBroker(10_000_000, {"005930": 70_000}, costs)
    j = Journal()
    r = OrderRouter(broker, RiskGate(), j, TradingMode.PAPER)
    res = r.submit(_order(cid="LV-20261203-000001"), _acct())
    assert res.status is OrderStatus.REJECTED_BY_RISK and "ORDER_ID_PREFIX_MISMATCH" in res.message
    assert not broker.get_positions().get("005930")
    assert r.submit(_order(), _acct()).status is OrderStatus.SUBMITTED   # PP- 는 통과


def test_paper_only_strategy_rejected_on_live(profile, monkeypatch):
    """2단계 대비: LIVE 가 승인돼도 S03·D 시리즈는 실계좌로 못 나감."""
    profile("live")
    monkeypatch.setattr(router_mod, "LIVE_TRADING_APPROVED", True)
    b = FakeLiveBroker()
    r = OrderRouter(b, RiskGate(), Journal(), TradingMode.LIVE)
    for sid in ("S03_ETF", "D01"):
        res = r.submit(_order(sid=sid), _acct())
        assert "STRATEGY_PAPER_ONLY" in res.message
    assert b.sent == []


def test_same_profile_second_process_blocked(profile, tmp_path):
    from stockking.ops.common import acquire_account_lock
    profile("paper")
    lock = Path("var/paper/.lock")
    lock.parent.mkdir(parents=True)
    lock.write_text(json.dumps({"pid": os.getppid()}), encoding="utf-8")   # 살아 있는 다른 프로세스
    with pytest.raises(RuntimeError, match="이미 실행 중"):
        acquire_account_lock()
    lock.write_text(json.dumps({"pid": 2 ** 22 + 12345}), encoding="utf-8")   # 죽은 프로세스가 남긴 잠금
    assert acquire_account_lock() == lock
    assert json.loads(lock.read_text(encoding="utf-8"))["pid"] == os.getpid()
    assert acquire_account_lock() == lock      # 같은 프로세스 재호출은 통과


# ---------- 계좌별 킬스위치 + KILL_ALL ----------
def test_kill_switch_is_per_account(profile):
    profile("paper")
    KillSwitch().trip("paper incident")
    assert Path("var/paper/KILL_SWITCH").exists()
    profile("live")
    assert not KillSwitch().is_tripped()        # 모의 킬스위치는 실전을 멈추지 않음


def test_kill_all_blocks_buys_on_both_accounts_allows_sells(profile, monkeypatch):
    from stockking.ops import kill_all
    monkeypatch.delenv("STOCKKING_KILL_ALL_FILE")
    with pytest.raises(ValueError):
        kill_all.trip("", "대표")
    for name in ("paper", "live"):
        profile(name)
        kill_all.trip("비상", "대표")
        ks = KillSwitch()
        assert ks.is_tripped() and "KILL_ALL" in ks.reason()
        g = RiskGate(kill_switch=ks)
        assert not g.check(_order(Side.BUY), _acct())
        assert g.check(_order(Side.SELL, 2), _acct())     # D-026 청산 매도 허용
        assert kill_all.clear("대표") and not KillSwitch().is_tripped()
    log = [json.loads(x) for x in Path("var/kill_all_log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [x["action"] for x in log] == ["trip", "clear", "trip", "clear"]


# ---------- 감사 로그 섞임 방지 ----------
def test_startup_blocks_foreign_prefix_records(profile, tmp_path):
    from stockking.ops.recovery import scan_journal, startup_blockers
    j = tmp_path / "j.jsonl"
    with j.open("a", encoding="utf-8") as fh:
        for cid in ("PP-1", "SK-2", "LV-3"):
            fh.write(json.dumps({"event": "order_request", "order": {"client_order_id": cid}}) + "\n")
            fh.write(json.dumps({"event": "risk_decision", "client_order_id": cid, "approved": False}) + "\n")
    profile("paper")
    rep = scan_journal(j)
    assert rep.foreign_order_ids == ["LV-3"]
    assert any("다른 계좌 접두어" in b for b in startup_blockers(rep, KillSwitch(tmp_path / "KS")))
    profile(None)
    assert scan_journal(j).foreign_order_ids == []      # 호환 모드는 검사 안 함(기존 동작)


# ---------- migrate_var ----------
def test_migrate_var_moves_and_guards(profile):
    from stockking.ops import migrate_var
    from stockking.paths import layout_error
    profile(None)
    base = config._stockking_dir()
    _write_secret(base / "secrets.env", "KIS_PAPER_APP_KEY=a\n")
    (base / "kis_paper_token.json").write_text("{}", encoding="utf-8")
    Path("var/journal").mkdir(parents=True)
    Path("var/journal/20261202.jsonl").write_text('{"event": "x", "who": "대표"}\n', encoding="utf-8")
    Path("var/operating_capital.json").write_text('{"capital": 10000000}', encoding="utf-8")
    p = migrate_var.plan()
    assert p["var_items"] == ["journal", "operating_capital.json"] and p["secrets"] and p["token"]
    assert Path("var/journal").exists()                 # 미리보기는 아무것도 안 바꿈
    with pytest.raises(ValueError):
        migrate_var.apply("")
    migrate_var.apply("대표")
    assert Path("var/paper/journal/20261202.jsonl").exists() and not Path("var/journal").exists()
    assert Path("var/_pre_d049_backup/journal/20261202.jsonl").exists()
    assert (base / "paper" / "secrets.env").exists() and not (base / "secrets.env").exists()
    assert not (base / "kis_paper_token.json").exists()
    assert layout_error() and "STOCKKING_ACCOUNT" in layout_error()   # 이동 뒤 호환 모드 실행은 거부
    with pytest.raises(RuntimeError, match="이미 이동"):
        migrate_var.apply("대표")
    profile("paper")
    assert layout_error() is None
    from stockking.risk.stages import operating_capital
    assert operating_capital() == 10_000_000 and config.default_secrets_file() == base / "paper" / "secrets.env"


def test_run_cli_refuses_compat_mode_after_migration(profile, capsys):
    from stockking.ops.common import run_cli
    profile(None)
    Path("var").mkdir()
    Path("var/MIGRATED_D049").write_text("{}", encoding="utf-8")
    assert run_cli(lambda: 0) == 2 and "STOCKKING_ACCOUNT" in capsys.readouterr().err


# ---------- 경로 하드코딩 금지 ----------
def test_var_paths_only_built_in_paths_module():
    bad = []
    for py in sorted((ROOT / "src").rglob("*.py")):
        if py.name == "paths.py":
            continue
        for n in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and \
                    " " not in n.value and (n.value == "var" or n.value.startswith(("var/", "var\\"))):
                bad.append(f"{py.relative_to(ROOT)}:{n.lineno} {n.value!r}")
    assert bad == []
