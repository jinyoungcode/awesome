import pytest

from stockking.broker.kis import KIS_PAPER_BASE_URL, KisPaperClient, KisPaperCredentials
from stockking.config import Secret
from stockking.domain import OrderRequest, OrderType, Side


def creds():
    return KisPaperCredentials(Secret("k"), Secret("s"), "12345678", "01")


def test_live_domain_refused():
    with pytest.raises(PermissionError):
        KisPaperClient(creds(), base_url="https://openapi.koreainvestment.com:9443")


def test_order_uses_paper_tr_id_and_domain():
    calls = []

    def transport(method, url, headers, body):
        calls.append((method, url, headers, body))
        if url.endswith("/oauth2/tokenP"):
            return {"access_token": "TOKEN", "expires_in": 86400}
        return {"rt_cd": "0", "msg1": "ok", "output": {"ODNO": "0001"}}

    c = KisPaperClient(creds(), transport=transport)
    c.min_interval_sec = 0
    r = c.place_order(OrderRequest("005930", Side.BUY, 3, OrderType.LIMIT, 70_000))
    assert r.accepted and r.broker_order_id == "0001"
    method, url, headers, body = calls[-1]
    assert url.startswith(KIS_PAPER_BASE_URL)
    assert headers["tr_id"].startswith("V")  # 모의투자 TR 은 V 로 시작
    assert body == {"CANO": "12345678", "ACNT_PRDT_CD": "01", "PDNO": "005930",
                    "ORD_DVSN": "00", "ORD_QTY": "3", "ORD_UNPR": "70000"}
    # 토큰은 캐시되어 1회만 발급
    assert sum(1 for c_ in calls if c_[1].endswith("tokenP")) == 1


def test_from_env_requires_keys(monkeypatch):
    monkeypatch.delenv("KIS_PAPER_APP_KEY", raising=False)
    with pytest.raises(RuntimeError):
        KisPaperCredentials.from_env()


# ---------- 토큰 파일 캐시 (1일차 실측: 1분 안 재실행 시 403) ----------
import json as _json
import os as _os
import time as _time


def _tclient(cache, key="k", responses=None):
    calls = []

    def transport(method, url, headers, body):
        calls.append(url)
        if url.endswith("/oauth2/tokenP"):
            return (responses or {}).get("token", {"access_token": "TOK-" + key, "expires_in": 86400})
        return {"rt_cd": "0", "output": {"stck_prpr": "1", "stck_sdpr": "1"}}

    c = KisPaperClient(KisPaperCredentials(Secret(key), Secret("s"), "12345678", "01"), transport=transport,
                       token_cache=cache)
    c.min_interval_sec = 0
    return c, calls


def test_token_reused_across_processes(tmp_path):
    cache = tmp_path / "tok.json"
    c1, calls1 = _tclient(cache)
    c1.inquire_price("005930")
    c2, calls2 = _tclient(cache)            # 새 프로세스(새 객체)
    c2.inquire_price("005930")
    assert sum(u.endswith("tokenP") for u in calls1) == 1 and not any(u.endswith("tokenP") for u in calls2)
    saved = cache.read_text(encoding="utf-8")
    assert "TOK-k" in saved and '"k"' not in saved and "appsecret" not in saved   # 앱키·시크릿은 저장 안 함
    if _os.name == "posix":
        assert (cache.stat().st_mode & 0o077) == 0


def test_token_cache_ignored_for_other_key_or_expired(tmp_path):
    cache = tmp_path / "tok.json"
    _tclient(cache)[0].inquire_price("005930")
    c, calls = _tclient(cache, key="other")
    c.inquire_price("005930")
    assert any(u.endswith("tokenP") for u in calls)        # 다른 앱키 → 재발급
    d = _json.loads(cache.read_text(encoding="utf-8"))
    d["expires_at"] = _time.time() + 30                     # 만료 60초 이내 → 재발급
    cache.write_text(_json.dumps(d), encoding="utf-8")
    c3, calls3 = _tclient(cache, key="other")
    c3.inquire_price("005930")
    assert any(u.endswith("tokenP") for u in calls3)


def test_token_rate_limit_message(tmp_path):
    c, _ = _tclient(tmp_path / "t.json", responses={"token": {"_http_status": 403, "error_code": "EGW00133",
                                                              "error_description": "1분당 1회"}})
    with pytest.raises(RuntimeError, match="1분 후 다시 실행"):
        c.inquire_price("005930")
    c2, _ = _tclient(tmp_path / "t2.json", responses={"token": {"_http_status": 401, "error_code": "EGW00103"}})
    with pytest.raises(RuntimeError, match="토큰 발급 실패"):
        c2.inquire_price("005930")


def test_http_error_body_preserved(monkeypatch):
    import io
    import urllib.error

    from stockking.broker import kis

    def boom(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, io.BytesIO(b'{"error_code":"EGW00133"}'))
    monkeypatch.setattr(kis.urllib.request, "urlopen", boom)
    assert kis._urllib_transport("POST", "https://x", {}, {}) == {"_http_status": 403, "error_code": "EGW00133"}


def test_default_token_cache_outside_repo(monkeypatch, tmp_path):
    from stockking.broker.kis import default_token_cache
    monkeypatch.delenv("STOCKKING_TOKEN_CACHE")
    p = default_token_cache()
    assert p.name == "kis_paper_token.json" and "awesome" not in str(p.parent)


# ---------- 1일차 실측: 주문내역 조회 0건 ----------
def _dclient(responder):
    seen = []

    def transport(method, url, headers, body):
        if url.endswith("/oauth2/tokenP"):
            return {"access_token": "T", "expires_in": 86400}
        seen.append((headers["tr_id"], url))
        return responder(headers["tr_id"], url)

    c = KisPaperClient(KisPaperCredentials(Secret("k"), Secret("s"), "12345678", "01"), transport=transport)
    c.min_interval_sec = 0
    return c, seen


ROW = {"odno": "0000025951", "pdno": "005930", "sll_buy_dvsn_cd": "02", "ord_qty": "1", "tot_ccld_qty": "0",
       "avg_prvs": "0", "cncl_yn": "N", "cncl_cfrm_qty": "1"}


def test_daily_orders_falls_back_across_variants_and_remembers():
    def responder(tr, url):
        if tr == "VTTC8001R" and "EXCG_ID_DVSN_CD" not in url:
            return {"rt_cd": "0", "output1": [ROW]}
        return {"rt_cd": "0", "output1": []}
    c, seen = _dclient(responder)
    rows = c.inquire_daily_orders("20261126")
    assert rows[0]["odno"] == "0000025951" and rows[0]["canceled"] is True   # 취소확인수량으로도 취소 판정
    n = len(seen)
    c.inquire_daily_orders("20261126")
    assert len(seen) == n + 1                       # 성공한 변형을 기억 → 다음엔 한 번에


def test_daily_orders_all_error_raises_and_empty_ok():
    c, _ = _dclient(lambda tr, url: {"rt_cd": "1", "msg_cd": "OPSQ0002", "msg1": "없는 서비스 코드"})
    with pytest.raises(RuntimeError, match="주문체결 조회 실패"):
        c.inquire_daily_orders("20261126")
    c2, _ = _dclient(lambda tr, url: {"rt_cd": "0", "output1": []})
    assert c2.inquire_daily_orders("20261126") == []


def test_debug_log_masks_account(monkeypatch, tmp_path):
    monkeypatch.setenv("STOCKKING_KIS_DEBUG", "1")
    c, _ = _dclient(lambda tr, url: {"rt_cd": "0", "output1": [ROW]})
    c.inquire_daily_orders("20261126")
    log = next((tmp_path / "var" / "logs").glob("kis_raw_*.jsonl")).read_text(encoding="utf-8")
    assert "0000025951" in log and "12345678" not in log and '"T"' not in log and "****" in log


def test_run_cli_clean_exit(capsys):
    from stockking.ops.common import run_cli

    def boom():
        raise RuntimeError("KIS 토큰 발급 제한(HTTP 403 EGW00133) — 1분 후 다시 실행하세요.")
    assert run_cli(boom) == 2
    err = capsys.readouterr().err
    assert "중단:" in err and "1분" in err and "Traceback" not in err
