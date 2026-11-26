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
    saved = cache.read_text()
    assert "TOK-k" in saved and '"k"' not in saved and "appsecret" not in saved   # 앱키·시크릿은 저장 안 함
    if _os.name == "posix":
        assert (cache.stat().st_mode & 0o077) == 0


def test_token_cache_ignored_for_other_key_or_expired(tmp_path):
    cache = tmp_path / "tok.json"
    _tclient(cache)[0].inquire_price("005930")
    c, calls = _tclient(cache, key="other")
    c.inquire_price("005930")
    assert any(u.endswith("tokenP") for u in calls)        # 다른 앱키 → 재발급
    d = _json.loads(cache.read_text())
    d["expires_at"] = _time.time() + 30                     # 만료 60초 이내 → 재발급
    cache.write_text(_json.dumps(d))
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
