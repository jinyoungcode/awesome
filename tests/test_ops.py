import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from stockking import config
from stockking.audit import Journal
from stockking.broker.kis import KisPaperClient, KisPaperCredentials
from stockking.config import InsecureSecretFile, Secret, load_secret_file, require_secret
from stockking.domain import AccountSnapshot
from stockking.krx import floor_to_tick, tick_size
from stockking.ops import killswitch_drill, restart_drill
from stockking.ops.connect_check import KST, krx_open
from stockking.ops.daily_reconcile import compare
from stockking.ops.recovery import record_resolution, scan_journal, startup_blockers
from stockking.risk import KillSwitch


# ---------- 재시작 복구 ----------
def _w(path, **rec):
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _order(cid):
    return {"client_order_id": cid, "symbol": "005930", "side": "buy", "quantity": 1}


def test_scan_journal_classifies_orders(tmp_path):
    j = tmp_path / "j.jsonl"
    _w(j, event="order_request", order=_order("A"))
    _w(j, event="risk_decision", client_order_id="A", approved=True, reasons=[])
    _w(j, event="broker_response", client_order_id="A", accepted=True, broker_order_id="0001")
    _w(j, event="order_request", order=_order("B"))
    _w(j, event="risk_decision", client_order_id="B", approved=False, reasons=["X"])
    _w(j, event="order_request", order=_order("C"))
    _w(j, event="risk_decision", client_order_id="C", approved=True, reasons=[])
    _w(j, event="broker_error", client_order_id="C", error="timeout")
    _w(j, event="order_request", order=_order("D"))  # 응답 전 다운
    j.open("a").write('{"event": "order_req')       # 잘린 마지막 줄
    rep = scan_journal(j)
    assert rep.seen_order_ids == {"A", "B", "C", "D"}
    assert rep.accepted == {"A": "0001"}
    assert set(rep.unresolved) == {"C", "D"}
    assert rep.corrupt_lines == 1


def test_startup_blockers_and_resolution(tmp_path):
    j = tmp_path / "j.jsonl"
    _w(j, event="order_request", order=_order("D"))
    ks = KillSwitch(tmp_path / "KS")
    assert len(startup_blockers(scan_journal(j), ks)) == 1
    with pytest.raises(ValueError):
        record_resolution(Journal(j), "D", "not_sent", operator="")
    with pytest.raises(ValueError):
        record_resolution(Journal(j), "D", "maybe", operator="x")
    record_resolution(Journal(j), "D", "not_sent", operator="x")
    assert startup_blockers(scan_journal(j), ks) == []


def test_restart_drill_passes(tmp_path):
    assert restart_drill.run(tmp_path).passed


def test_killswitch_drill_passes_and_sends_nothing(tmp_path):
    snap = AccountSnapshot(1_000_000, {}, {"005930": 70_000}, 1_000_000)
    cl = killswitch_drill.run(tmp_path / "KS", Journal(), snap, "005930", 70_000, "tester")
    assert cl.passed and not (tmp_path / "KS").exists()


def test_killswitch_drill_refuses_if_already_tripped(tmp_path):
    (tmp_path / "KS").write_text("real incident")
    snap = AccountSnapshot(1_000_000, {}, {"005930": 70_000}, 1_000_000)
    cl = killswitch_drill.run(tmp_path / "KS", Journal(), snap, "005930", 70_000, "tester")
    assert not cl.passed and (tmp_path / "KS").read_text() == "real incident"


# ---------- 대사 ----------
def test_reconcile_compare():
    d = compare({"A": "0001", "B": "0002", "C": "0002"},
                [{"odno": "0000000001"}, {"odno": "0002"}, {"odno": "0009"}])
    assert d["matched"] == ["1", "2"] and d["unknown_to_system"] == ["9"]
    assert d["duplicate_broker_ids"] == ["2"] and d["missing_at_broker"] == []


def test_reconcile_cancel_of_system_order_is_not_manual():
    # 1일차 실측(11/26): 25951 매수 → 취소 주문 25953(orgn_odno=25951) 이 별도 행으로 조회됨
    rows = [{"odno": "0000025953", "orgn_odno": "0000025951"},
            {"odno": "0000025951", "orgn_odno": "0000000000"},
            {"odno": "0000025960", "orgn_odno": "0000025953"},   # 정정의 정정(사슬)
            {"odno": "0000025970", "orgn_odno": "0000000000"},   # 진짜 수동 주문
            {"odno": "0000025971", "orgn_odno": "0000025970"}]   # 수동 주문의 취소도 수동
    d = compare({"SK-1": "0000025951"}, rows)
    assert d["matched"] == ["25951"]
    assert d["linked_cancel_modify"] == ["25953", "25960"]
    assert d["unknown_to_system"] == ["25970", "25971"]


# ---------- 호가단위·장시간 ----------
@pytest.mark.parametrize("price,tick", [(1_999, 1), (4_995, 5), (19_990, 10), (49_950, 50),
                                        (199_900, 100), (499_500, 500), (500_000, 1_000)])
def test_tick_size(price, tick):
    assert tick_size(price) == tick and floor_to_tick(price + 1) % tick_size(price + 1) == 0


def test_krx_open():
    assert krx_open(datetime(2026, 11, 25, 10, 0, tzinfo=KST))
    assert not krx_open(datetime(2026, 11, 25, 8, 59, tzinfo=KST))
    assert not krx_open(datetime(2026, 11, 28, 10, 0, tzinfo=KST))  # 토요일


# ---------- 비밀값 파일 ----------
def test_secret_file_loads_when_private(tmp_path, monkeypatch):
    f = tmp_path / "secrets.env"
    f.write_text("# c\nKIS_PAPER_APP_KEY='abc'\n")
    f.chmod(0o600)
    monkeypatch.delenv("KIS_PAPER_APP_KEY", raising=False)
    assert load_secret_file(f) == {"KIS_PAPER_APP_KEY": "abc"}
    assert require_secret("KIS_PAPER_APP_KEY", f).reveal() == "abc"


@pytest.mark.skipif(os.name != "posix", reason="POSIX 권한")
def test_secret_file_rejects_world_readable(tmp_path):
    f = tmp_path / "secrets.env"
    f.write_text("K=v\n")
    f.chmod(0o644)
    with pytest.raises(InsecureSecretFile):
        load_secret_file(f)


def test_secret_file_rejects_inside_repo():
    f = Path(config.__file__).resolve().parents[2] / "var" / "test_secrets.env"
    f.parent.mkdir(exist_ok=True)
    try:
        f.write_text("K=v\n")
        f.chmod(0o600)
        with pytest.raises(InsecureSecretFile):
            load_secret_file(f)
    finally:
        f.unlink(missing_ok=True)


def test_missing_secret_file_is_empty(tmp_path):
    assert load_secret_file(tmp_path / "nope.env") == {}


# ---------- KIS 조회·취소 ----------
def _client(responses):
    calls = []

    def transport(method, url, headers, body):
        calls.append((method, url, headers, body))
        if url.endswith("/oauth2/tokenP"):
            return {"access_token": "T", "expires_in": 86400}
        return responses.pop(0)

    c = KisPaperClient(KisPaperCredentials(Secret("k"), Secret("s"), "12345678", "01"), transport=transport)
    c.min_interval_sec = 0
    return c, calls


def test_kis_price_uses_query_string():
    c, calls = _client([{"rt_cd": "0", "output": {"stck_prpr": "70100", "stck_sdpr": "69900"}}])
    assert c.inquire_price("005930") == {"price": 70100, "base_price": 69900}
    method, url, _, body = calls[-1]
    assert method == "GET" and "FID_INPUT_ISCD=005930" in url and body == {}


def test_kis_balance_parsing():
    c, calls = _client([{"rt_cd": "0",
                         "output1": [{"pdno": "005930", "hldg_qty": "3", "pchs_avg_pric": "70000.0"},
                                     {"pdno": "000660", "hldg_qty": "0", "pchs_avg_pric": "0"}],
                         "output2": [{"dnca_tot_amt": "9790000", "tot_evlu_amt": "10000000"}]}])
    b = c.inquire_balance()
    assert (b["cash"], b["total_equity"], b["positions"]) == (9_790_000, 10_000_000, {"005930": (3, 70000.0)})
    assert b["details"]["005930"]["qty"] == 3 and b["cash_d2"] == 9_790_000
    assert calls[-1][2]["tr_id"].startswith("V")


def test_kis_error_raises():
    c, _ = _client([{"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수 초과"}])
    with pytest.raises(RuntimeError, match="EGW00201"):
        c.inquire_balance()


def test_kis_cancel_uses_org_no_from_last_order():
    c, calls = _client([{"rt_cd": "0", "output": {"KRX_FWDG_ORD_ORGNO": "06010", "ODNO": "0000123"}},
                        {"rt_cd": "0", "msg1": "취소 완료", "output": {"ODNO": "0000124"}}])
    from stockking.domain import OrderRequest, OrderType, Side
    r = c.place_order(OrderRequest("005930", Side.BUY, 1, OrderType.LIMIT, 63_000))
    cr = c.cancel_order(r.broker_order_id, "005930", 1)
    body = calls[-1][3]
    assert cr.accepted and body["KRX_FWDG_ORD_ORGNO"] == "06010" and body["ORGN_ODNO"] == "0000123"
    assert body["RVSE_CNCL_DVSN_CD"] == "02" and body["QTY_ALL_ORD_YN"] == "Y"
    assert calls[-1][2]["tr_id"].startswith("V")


def test_kis_daily_orders_parsing():
    c, _ = _client([{"rt_cd": "0", "output1": [{"odno": "0000123", "pdno": "005930", "sll_buy_dvsn_cd": "02",
                                                "ord_qty": "1", "tot_ccld_qty": "0", "avg_prvs": "0",
                                                "cncl_yn": "Y"}]}])
    r = c.inquire_daily_orders("20261125")[0]
    assert {k: r[k] for k in ("odno", "symbol", "side", "ord_qty", "filled_qty", "avg_price", "canceled")} == {
        "odno": "0000123", "symbol": "005930", "side": "buy", "ord_qty": 1, "filled_qty": 0, "avg_price": 0.0,
        "canceled": True}


def test_secret_file_rejects_onedrive(tmp_path, monkeypatch):
    od = tmp_path / "OneDrive"
    (od / "stockking").mkdir(parents=True)
    f = od / "stockking" / "secrets.env"
    f.write_text("K=v\n")
    f.chmod(0o600)
    monkeypatch.setenv("OneDrive", str(od))
    with pytest.raises(InsecureSecretFile, match="OneDrive"):
        load_secret_file(f)


def test_windows_default_secret_path(monkeypatch):
    monkeypatch.setenv("APPDATA", "/fake/AppData/Roaming")
    assert config.default_secrets_file(is_windows=True) == Path("/fake/AppData/Roaming/stockking/secrets.env")
    assert config.default_secrets_file(is_windows=False).parts[-3:] == (".config", "stockking", "secrets.env")


def test_reconcile_cancel_odno_fallback_and_chain():
    """orgn_odno 가 비어 온 취소 행은 감사 로그 cancel_odno 로 매칭, 정정의 정정(사슬)도 시스템 것."""
    from stockking.ops.daily_reconcile import cancel_order_ids
    rows = [{"odno": "0000025951", "orgn_odno": ""}, {"odno": "0000025953", "orgn_odno": ""},
            {"odno": "0000025960", "orgn_odno": "0000025953"}]
    d = compare({"SK-1": "0000025951"}, rows, cancel_order_ids([{"event": "cancel_response", "cancel_odno": "0000025953"}]))
    assert d["linked_cancel_modify"] == ["25953", "25960"] and d["unknown_to_system"] == []
    assert compare({"SK-1": "0000025951"}, rows)["unknown_to_system"] == ["25953", "25960"]   # 보조 정보 없으면 수동 취급


def test_open_check_order_blocks_until_resolved(tmp_path):
    """C5 실습: connect_check 접수 후 취소 전 Ctrl+C → startup_check 가 열린 점검 주문을 잡고, resolve 후 해제."""
    j = tmp_path / "j.jsonl"
    o = {"client_order_id": "C1", "symbol": "005930", "side": "buy", "quantity": 1, "strategy_id": "connect_check"}
    _w(j, event="order_request", order=o)
    _w(j, event="risk_decision", client_order_id="C1", approved=True, reasons=[])
    _w(j, event="broker_response", client_order_id="C1", accepted=True, broker_order_id="0000025951")
    ks = KillSwitch(tmp_path / "KS")
    rep = scan_journal(j)
    assert set(rep.open_check_orders) == {"C1"} and not rep.unresolved
    assert any("취소 기록 없는 점검 주문" in b for b in startup_blockers(rep, ks))
    record_resolution(Journal(j), "C1", "unfilled_canceled", operator="대표")
    assert startup_blockers(scan_journal(j), ks) == []
    # 정상 흐름(취소 응답 있음)은 막지 않음
    j2 = tmp_path / "j2.jsonl"
    for line in j.read_text().splitlines()[:3]:
        j2.open("a").write(line + "\n")
    _w(j2, event="cancel_response", client_order_id="C1", accepted=True)
    assert startup_blockers(scan_journal(j2), ks) == []


def test_connect_check_hold_option_parses():
    import argparse
    from stockking.ops import connect_check
    src = Path(connect_check.__file__).read_text(encoding="utf-8")
    assert "--hold-seconds" in src and "모의투자 전용" in src
